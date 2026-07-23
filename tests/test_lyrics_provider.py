import json
import urllib.error

import pytest

from lyrisync import lyrics_provider as lp
from lyrisync.player_monitor import PlaybackState, PlayerSnapshot


SYNCED_LRC = "[00:12.00] First line\n[00:17.50] Second line\n"

SYNCED_RESPONSE = {
    "syncedLyrics": SYNCED_LRC,
    "plainLyrics": "First line\nSecond line",
}
PLAIN_ONLY_RESPONSE = {
    "syncedLyrics": None,
    "plainLyrics": "Just some plain lyrics\nSecond line",
}
INSTRUMENTAL_RESPONSE = {"syncedLyrics": None, "plainLyrics": None}


def snapshot(track_id="track123", title="Song", artist="Artist"):
    return PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id=track_id,
        title=title,
        artist=artist,
        album="Album",
        duration_ms=225000,
        position_seconds=10.0,
    )


class FakeFetcher:
    """Stands in for _fetch_json. A response of None means 404; an exception
    instance gets raised."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def provider(tmp_path):
    return lp.LyricsProvider(cache_dir=tmp_path / "cache")


def use_fetcher(monkeypatch, response):
    fake = FakeFetcher(response)
    monkeypatch.setattr(lp, "_fetch_json", fake)
    return fake


def test_synced_lyrics_fetched_and_parsed(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, SYNCED_RESPONSE)
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "synced"
    assert lyrics.synced == [(12.0, "First line"), (17.5, "Second line")]
    assert len(fake.calls) == 1
    url = fake.calls[0]
    assert "track_name=Song" in url
    assert "artist_name=Artist" in url
    assert "album_name=Album" in url
    assert "duration=225" in url


def test_plain_fallback_when_no_synced(provider, monkeypatch):
    use_fetcher(monkeypatch, PLAIN_ONLY_RESPONSE)
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "plain"
    assert lyrics.plain.startswith("Just some plain lyrics")
    assert lyrics.synced is None


def test_404_returns_none(provider, monkeypatch):
    use_fetcher(monkeypatch, None)
    assert provider.get_lyrics(snapshot()) is None


def test_instrumental_returns_none(provider, monkeypatch):
    use_fetcher(monkeypatch, INSTRUMENTAL_RESPONSE)
    assert provider.get_lyrics(snapshot()) is None


def test_cache_hit_skips_fetch(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, SYNCED_RESPONSE)
    first = provider.get_lyrics(snapshot())
    second = provider.get_lyrics(snapshot())
    assert len(fake.calls) == 1
    assert second.synced == first.synced


def test_negative_result_is_cached(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, None)
    assert provider.get_lyrics(snapshot()) is None
    assert provider.get_lyrics(snapshot()) is None
    assert len(fake.calls) == 1  # "no lyrics" is remembered


def test_network_error_not_cached(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, urllib.error.URLError("offline"))
    assert provider.get_lyrics(snapshot()) is None

    fake.response = SYNCED_RESPONSE
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics is not None  # retried after transient failure
    assert len(fake.calls) == 2


def test_cache_survives_new_provider_instance(provider, monkeypatch, tmp_path):
    use_fetcher(monkeypatch, SYNCED_RESPONSE)
    provider.get_lyrics(snapshot())

    fresh = lp.LyricsProvider(cache_dir=provider.cache_dir)
    use_fetcher(monkeypatch, urllib.error.URLError("offline"))
    lyrics = fresh.get_lyrics(snapshot())
    assert lyrics is not None
    assert lyrics.synced == [(12.0, "First line"), (17.5, "Second line")]


def test_corrupt_cache_entry_refetches(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, SYNCED_RESPONSE)
    provider.get_lyrics(snapshot())
    provider._cache_path("track123").write_text("{not json", encoding="utf-8")
    assert provider.get_lyrics(snapshot()) is not None
    assert len(fake.calls) == 2


def test_no_track_id_returns_none_without_fetch(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, SYNCED_RESPONSE)
    no_track = PlayerSnapshot(state=PlaybackState.STOPPED)
    assert provider.get_lyrics(no_track) is None
    assert fake.calls == []


def test_missing_metadata_returns_none_without_fetch(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, SYNCED_RESPONSE)
    partial = snapshot(title=None)
    assert provider.get_lyrics(partial) is None
    assert fake.calls == []


# -- LRC parsing ---------------------------------------------------------


def test_parse_lrc_basic():
    assert lp.parse_lrc(SYNCED_LRC) == [(12.0, "First line"), (17.5, "Second line")]


def test_parse_lrc_multiple_timestamps_per_line():
    parsed = lp.parse_lrc("[00:10.00][01:10.00] Chorus\n")
    assert parsed == [(10.0, "Chorus"), (70.0, "Chorus")]


def test_parse_lrc_ignores_metadata_tags():
    text = "[ar: Artist]\n[ti: Title]\n[length: 3:45]\n[00:05.00] Real line\n"
    assert lp.parse_lrc(text) == [(5.0, "Real line")]


def test_parse_lrc_sorts_out_of_order_lines():
    parsed = lp.parse_lrc("[00:30.00] Later\n[00:10.00] Earlier\n")
    assert parsed == [(10.0, "Earlier"), (30.0, "Later")]


def test_parse_lrc_keeps_empty_lines():
    parsed = lp.parse_lrc("[00:05.00] Words\n[00:10.00] \n[00:15.00] More\n")
    assert parsed[1] == (10.0, "")


def test_parse_lrc_minutes_over_59():
    assert lp.parse_lrc("[61:05.50] Long track\n") == [(3665.5, "Long track")]


def test_parse_lrc_no_fraction():
    assert lp.parse_lrc("[00:42] Whole seconds\n") == [(42.0, "Whole seconds")]


def test_parse_lrc_garbage_and_empty():
    assert lp.parse_lrc("") == []
    assert lp.parse_lrc("no timestamps here\n\n") == []


# -- cache file format ---------------------------------------------------


def test_negative_cache_file_shape(provider, monkeypatch):
    use_fetcher(monkeypatch, None)
    provider.get_lyrics(snapshot())
    entry = json.loads(provider._cache_path("track123").read_text(encoding="utf-8"))
    assert entry == {"found": False, "synced": None, "plain": None}


def test_track_id_sanitized_for_filename(provider, monkeypatch):
    use_fetcher(monkeypatch, None)
    provider.get_lyrics(snapshot(track_id="weird/../id"))
    path = provider._cache_path("weird/../id")
    assert path.parent == provider.cache_dir
    assert path.exists()
