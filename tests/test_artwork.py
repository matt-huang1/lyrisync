"""Album-cover colour: what gets extracted, and what gets remembered.

Reaching a real cover is manual-verify — it is a CDN request, and the
suite is not allowed one (conftest blocks the socket, and a bare
ArtworkProvider refuses its default directory). What is tested here is
everything on this side of the download: the vote that turns an image
into a hue, the cases where the honest answer is "no colour", and a
cache that must remember an answer without ever remembering an error.

The extraction only has to get the HUE right. appearance.py throws away
the luminance and saturation of whatever comes out of here, so a test
asserting an exact RGB triple would be pinning something the app does
not use — these assert the hue, which is the part that matters.
"""

import json

import pytest

from lyrisync import artwork
from lyrisync.appearance import rgb_to_hsl


def hue_of(rgb):
    return rgb_to_hsl(rgb)[0]


def block(colour, count):
    return [colour] * count


# -- the vote -------------------------------------------------------------


def test_a_solid_cover_reads_as_its_own_hue():
    colour = artwork.dominant_colour(block((200, 40, 40), 400))
    assert colour is not None
    assert hue_of(colour) == pytest.approx(0, abs=8) or hue_of(colour) >= 352


def test_the_dominant_hue_wins_over_the_minority():
    samples = block((40, 60, 200), 300) + block((200, 60, 40), 100)
    colour = artwork.dominant_colour(samples)
    assert hue_of(colour) == pytest.approx(225, abs=15)  # blue, not red


def test_black_backgrounds_do_not_get_a_vote():
    """The failure this prevents: nearly every sleeve is mostly dark, and
    letting those pixels count makes every album come out the same."""
    samples = block((4, 4, 6), 3000) + block((220, 60, 40), 200)
    colour = artwork.dominant_colour(samples)
    assert colour is not None
    assert hue_of(colour) == pytest.approx(8, abs=12)  # the red detail


def test_white_borders_do_not_get_a_vote_either():
    samples = block((252, 252, 250), 3000) + block((40, 90, 210), 200)
    colour = artwork.dominant_colour(samples)
    assert colour is not None
    assert hue_of(colour) == pytest.approx(220, abs=15)


def test_a_monochrome_cover_has_no_colour():
    """A real answer, not a failure: a black-and-white sleeve gets no
    tint rather than whichever hue its noise leaned towards."""
    greys = [(v, v, v) for v in range(20, 240, 2)] * 20
    assert artwork.dominant_colour(greys) is None


def test_an_all_black_cover_has_no_colour():
    assert artwork.dominant_colour(block((0, 0, 0), 500)) is None


def test_an_all_white_cover_has_no_colour():
    assert artwork.dominant_colour(block((255, 255, 255), 500)) is None


def test_no_samples_at_all_is_no_colour():
    assert artwork.dominant_colour([]) is None


def test_a_speck_of_colour_is_not_a_dominant_colour():
    """One vivid pixel in a sleeve of near-black is noise — a JPEG
    artefact, a logo's anti-aliasing — and must not decide the hue of the
    whole window."""
    samples = block((10, 10, 12), 5000) + block((255, 0, 0), 4)
    assert artwork.dominant_colour(samples) is None


def test_a_washed_out_cover_still_reads_if_it_is_consistent():
    """Faded and pastel sleeves are common and do have a hue."""
    colour = artwork.dominant_colour(block((150, 190, 120), 800))
    assert colour is not None
    assert hue_of(colour) == pytest.approx(94, abs=15)


def test_the_extracted_colour_survives_the_hue_gate():
    """dominant_colour and appearance.usable_hue have separate saturation
    thresholds; a colour that passed the first and failed the second would
    be extraction work thrown away silently."""
    from lyrisync.appearance import usable_hue

    for sample in ((200, 40, 40), (150, 190, 120), (40, 60, 200)):
        colour = artwork.dominant_colour(block(sample, 500))
        assert usable_hue(colour) is not None


# -- decoding a real image ------------------------------------------------


def encoded_image(fill, width=64, height=64):
    """A real PNG, through Qt, so decode_colour is exercised for real."""
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtGui = pytest.importorskip("PySide6.QtGui")
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor(*fill))
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_a_real_image_decodes_to_its_hue():
    colour = artwork.decode_colour(encoded_image((30, 120, 200)))
    assert colour is not None
    assert hue_of(colour) == pytest.approx(rgb_to_hsl((30, 120, 200))[0], abs=10)


def test_a_real_monochrome_image_decodes_to_no_colour():
    assert artwork.decode_colour(encoded_image((128, 128, 128))) is None


def test_bytes_that_are_not_an_image_are_an_error_not_an_answer():
    """The distinction the cache depends on: a truncated download must
    not be remembered as "this cover has no colour" forever."""
    with pytest.raises(artwork.ArtworkError):
        artwork.decode_colour(b"this is not a png")


# -- the cache ------------------------------------------------------------


@pytest.fixture
def provider(tmp_path):
    return artwork.ArtworkProvider(cache_dir=tmp_path / "art")


@pytest.fixture
def no_downloads(monkeypatch):
    """Fail loudly if anything reaches for the network."""

    def refuse(url):
        raise AssertionError(f"downloaded {url!r} when it should not have")

    monkeypatch.setattr(artwork, "_download", refuse)


def serve(monkeypatch, data, calls=None):
    def fake_download(url):
        if calls is not None:
            calls.append(url)
        if isinstance(data, Exception):
            raise data
        return data

    monkeypatch.setattr(artwork, "_download", fake_download)


def test_a_colour_is_worked_out_once_and_remembered(provider, monkeypatch):
    calls = []
    serve(monkeypatch, encoded_image((200, 50, 40)), calls)

    first = provider.colour_for("t1", "http://cover")
    assert first is not None
    assert calls == ["http://cover"]

    second = provider.colour_for("t1", "http://cover")
    assert second == first
    assert calls == ["http://cover"], "the cover was fetched twice"


def test_the_cache_holds_colours_not_images(provider, monkeypatch):
    """The whole reason the cache exists in this shape: covers are
    hundreds of kilobytes and are already on a CDN."""
    serve(monkeypatch, encoded_image((200, 50, 40)))
    provider.colour_for("t1", "http://cover")

    written = list(provider.cache_dir.iterdir())
    assert len(written) == 1
    assert written[0].suffix == ".json"
    assert written[0].stat().st_size < 200
    entry = json.loads(written[0].read_text())
    assert len(entry["colour"]) == 3


def test_no_usable_hue_is_remembered_as_an_answer(provider, monkeypatch):
    """A monochrome sleeve is settled. Re-deciding it on every play would
    be a download per track for a result that cannot change."""
    calls = []
    serve(monkeypatch, encoded_image((128, 128, 128)), calls)
    assert provider.colour_for("t1", "http://cover") is None
    assert calls == ["http://cover"]

    assert provider.colour_for("t1", "http://cover") is None
    assert calls == ["http://cover"], "a settled answer was fetched again"


def test_a_failed_fetch_is_never_remembered(provider, monkeypatch):
    """Errors are not answers — the same rule the lyrics cache follows.
    Offline once must not mean colourless forever."""
    serve(monkeypatch, artwork.ArtworkError("offline"))
    assert provider.colour_for("t1", "http://cover") is None
    assert not list(provider.cache_dir.iterdir()) if provider.cache_dir.exists() else True

    calls = []
    serve(monkeypatch, encoded_image((40, 80, 220)), calls)
    assert provider.colour_for("t1", "http://cover") is not None
    assert calls == ["http://cover"], "the retry never happened"


def test_an_image_that_will_not_decode_is_never_remembered(provider, monkeypatch):
    serve(monkeypatch, b"truncated")
    assert provider.colour_for("t1", "http://cover") is None

    calls = []
    serve(monkeypatch, encoded_image((40, 80, 220)), calls)
    assert provider.colour_for("t1", "http://cover") is not None
    assert calls == ["http://cover"]


def test_no_track_id_fetches_nothing(provider, no_downloads):
    assert provider.colour_for(None, "http://cover") is None
    assert provider.colour_for("", "http://cover") is None


def test_no_artwork_url_fetches_nothing(provider, no_downloads):
    """Spotify builds that do not report a cover, and every track before
    one is known. Silent, and the window keeps the colour it had."""
    assert provider.colour_for("t1", None) is None
    assert provider.colour_for("t1", "") is None


def test_a_corrupt_cache_entry_is_ignored_not_trusted(provider, monkeypatch):
    provider.cache_dir.mkdir(parents=True, exist_ok=True)
    provider._cache_path("t1").write_text("{not json", encoding="utf-8")
    calls = []
    serve(monkeypatch, encoded_image((200, 50, 40)), calls)
    assert provider.colour_for("t1", "http://cover") is not None
    assert calls == ["http://cover"]


def test_an_out_of_range_cache_entry_is_ignored(provider, monkeypatch):
    provider.cache_dir.mkdir(parents=True, exist_ok=True)
    provider._cache_path("t1").write_text('{"colour": [999, -4, 0]}', encoding="utf-8")
    calls = []
    serve(monkeypatch, encoded_image((200, 50, 40)), calls)
    assert provider.colour_for("t1", "http://cover") is not None
    assert calls == ["http://cover"]


def test_track_ids_never_escape_the_cache_directory(provider):
    """Track ids come from Spotify and land in a filename."""
    path = provider._cache_path("../../etc/passwd")
    assert provider.cache_dir in path.parents
    assert "/" not in path.name
