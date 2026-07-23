import json

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


def snapshot(track_id="track123", title="Song", artist="Artist", album="Album"):
    return PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id=track_id,
        title=title,
        artist=artist,
        album=album,
        duration_ms=225000,
        position_seconds=10.0,
    )


def search_record(title="Song", artist="Artist", duration=225.0, **fields):
    return {
        "trackName": title,
        "artistName": artist,
        "albumName": "Whatever",
        "duration": duration,
        "syncedLyrics": None,
        "plainLyrics": None,
        **fields,
    }


class FakeFetcher:
    """Stands in for _fetch_json. Routes by URL substring, first match wins.
    A None response models a 404; an exception instance gets raised.

    Route keys distinguish the three request shapes: a /get URL with the
    album carries "album_name=", the album-less retry matches "api/get",
    and the search fallback matches "api/search".
    """

    def __init__(self, *routes):
        self.routes = list(routes)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for substring, response in self.routes:
            if substring in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected URL: {url}")


@pytest.fixture
def provider(tmp_path):
    return lp.LyricsProvider(cache_dir=tmp_path / "cache")


def use_fetcher(monkeypatch, *routes):
    fake = FakeFetcher(*routes)
    monkeypatch.setattr(lp, "_fetch_json", fake)
    return fake


# -- fetch and fallback chain --------------------------------------------


def test_synced_lyrics_fetched_and_parsed(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
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
    use_fetcher(monkeypatch, ("album_name", PLAIN_ONLY_RESPONSE))
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "plain"
    assert lyrics.plain.startswith("Just some plain lyrics")
    assert lyrics.synced is None


def test_404_with_album_retries_without_album(provider, monkeypatch):
    fake = use_fetcher(
        monkeypatch,
        ("album_name", None),        # exact match 404s (album mismatch)
        ("api/get", SYNCED_RESPONSE),  # album-less retry hits
    )
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "synced"
    assert len(fake.calls) == 2
    assert "album_name" not in fake.calls[1]


def test_double_404_falls_back_to_search(provider, monkeypatch):
    fake = use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", [search_record(syncedLyrics=SYNCED_LRC)]),
        ("api/get", None),
    )
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "synced"
    assert len(fake.calls) == 3
    assert "api/search" in fake.calls[2]


def test_search_prefers_synced_and_filters_bad_matches(provider, monkeypatch):
    use_fetcher(
        monkeypatch,
        ("album_name", None),
        (
            "api/search",
            [
                search_record(title="Other Song", syncedLyrics=SYNCED_LRC),
                search_record(duration=500.0, syncedLyrics=SYNCED_LRC),  # wrong version
                search_record(plainLyrics="plain words"),
                search_record(title="SONG", syncedLyrics=SYNCED_LRC),  # case-insensitive
            ],
        ),
        ("api/get", None),
    )
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "synced"


def test_search_falls_back_to_plain_result(provider, monkeypatch):
    use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", [search_record(plainLyrics="plain words")]),
        ("api/get", None),
    )
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "plain"


def test_nothing_found_anywhere_returns_none(provider, monkeypatch):
    use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", []),
        ("api/get", None),
    )
    assert provider.get_lyrics(snapshot()) is None


def test_instrumental_exact_match_is_definitive(provider, monkeypatch):
    # A 200 with null lyrics from the exact match ends the chain: no search.
    fake = use_fetcher(monkeypatch, ("album_name", INSTRUMENTAL_RESPONSE))
    assert provider.get_lyrics(snapshot()) is None
    assert len(fake.calls) == 1


def test_no_album_snapshot_skips_album_query(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("api/get", SYNCED_RESPONSE))
    lyrics = provider.get_lyrics(snapshot(album=None))
    assert lyrics.kind == "synced"
    assert len(fake.calls) == 1
    assert "album_name" not in fake.calls[0]


# -- caching semantics ---------------------------------------------------


def test_cache_hit_skips_fetch(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    first = provider.get_lyrics(snapshot())
    second = provider.get_lyrics(snapshot())
    assert len(fake.calls) == 1
    assert second.synced == first.synced


def test_genuine_not_found_is_cached_negatively(provider, monkeypatch):
    fake = use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", []),
        ("api/get", None),
    )
    assert provider.get_lyrics(snapshot()) is None
    assert provider.get_lyrics(snapshot()) is None
    assert len(fake.calls) == 3  # second call never hit the network
    entry = json.loads(provider._cache_path("track123").read_text(encoding="utf-8"))
    assert entry == {"found": False, "synced": None, "plain": None}


def test_http_error_raises_and_is_never_cached(provider, monkeypatch):
    use_fetcher(monkeypatch, ("album_name", lp.LyricsError("LRCLIB returned HTTP 429")))
    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot())
    assert not provider._cache_path("track123").exists()

    # Next attempt goes to the network again and succeeds.
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    assert provider.get_lyrics(snapshot()).kind == "synced"
    assert len(fake.calls) == 1


def test_error_midway_through_fallback_chain_not_cached(provider, monkeypatch):
    use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/get", lp.LyricsError("timed out")),
    )
    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot())
    assert not provider._cache_path("track123").exists()


def test_cache_survives_new_provider_instance(provider, monkeypatch):
    use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    provider.get_lyrics(snapshot())

    fresh = lp.LyricsProvider(cache_dir=provider.cache_dir)
    use_fetcher(monkeypatch, ("album_name", lp.LyricsError("offline")))
    lyrics = fresh.get_lyrics(snapshot())  # cache hit: no fetch, no error
    assert lyrics.synced == [(12.0, "First line"), (17.5, "Second line")]


def test_corrupt_cache_entry_refetches(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    provider.get_lyrics(snapshot())
    provider._cache_path("track123").write_text("{not json", encoding="utf-8")
    assert provider.get_lyrics(snapshot()) is not None
    assert len(fake.calls) == 2


def test_no_track_id_returns_none_without_fetch(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    no_track = PlayerSnapshot(state=PlaybackState.STOPPED)
    assert provider.get_lyrics(no_track) is None
    assert fake.calls == []


def test_missing_metadata_returns_none_without_fetch(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    partial = snapshot(title=None)
    assert provider.get_lyrics(partial) is None
    assert fake.calls == []


def test_negative_cache_file_shape(provider, monkeypatch):
    use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", []),
        ("api/get", None),
    )
    provider.get_lyrics(snapshot())
    entry = json.loads(provider._cache_path("track123").read_text(encoding="utf-8"))
    assert entry == {"found": False, "synced": None, "plain": None}


def test_track_id_sanitized_for_filename(provider, monkeypatch):
    use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", []),
        ("api/get", None),
    )
    provider.get_lyrics(snapshot(track_id="weird/../id"))
    path = provider._cache_path("weird/../id")
    assert path.parent == provider.cache_dir
    assert path.exists()


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
