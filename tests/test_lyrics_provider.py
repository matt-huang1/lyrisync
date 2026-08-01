TIER = "unit"  # Qt-free logic, called directly

import json
import threading
import time

import pytest

from sottovoce import lyrics_provider as lp
from sottovoce.player_monitor import PlaybackState, PlayerSnapshot


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

    An unrouted URL is a 404 rather than an error, because every attempt in
    the chain now goes out on every fetch: a test that routes only the
    exact match is describing a song LRCLIB has under that album and
    nowhere else, which is a 404 from the other two.

    Called from the attempt threads, so the record is locked — and it is a
    record of WHAT was asked, never of what order the answers came back
    in, which is now the network's business rather than the chain's.
    """

    def __init__(self, *routes):
        self.routes = list(routes)
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, url):
        with self._lock:
            self.calls.append(url)
        for substring, response in self.routes:
            if substring in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return None

    def asked(self, substring):
        """The URLs matching ``substring``, in no particular order."""
        with self._lock:
            return [url for url in self.calls if substring in url]

    def asked_once(self, substring):
        return len(self.asked(substring)) == 1

    @property
    def count(self):
        with self._lock:
            return len(self.calls)


@pytest.fixture
def provider(tmp_path):
    return lp.LyricsProvider(
        cache_dir=tmp_path / "cache", user_sync_dir=tmp_path / "user_syncs"
    )


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
    assert fake.asked_once("album_name=Album")
    url = fake.asked("album_name=Album")[0]
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
    # Exact, not a substring: the album URL begins with this one.
    album_less = lp.LRCLIB_GET_URL + "?track_name=Song&artist_name=Artist&duration=225"
    assert album_less in fake.calls


def test_double_404_falls_back_to_search(provider, monkeypatch):
    fake = use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", [search_record(syncedLyrics=SYNCED_LRC)]),
        ("api/get", None),
    )
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "synced"
    assert fake.asked_once("api/search")


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
    """A 200 with null lyrics from the exact match ends the chain — and now
    that every attempt goes out anyway, the claim has to be made about the
    ANSWER rather than about how many requests were spared: search has a
    perfectly good synced record here and it is not used."""
    use_fetcher(
        monkeypatch,
        ("album_name", INSTRUMENTAL_RESPONSE),
        ("api/search", [search_record(syncedLyrics=SYNCED_LRC)]),
    )
    assert provider.get_lyrics(snapshot()) is None


def test_no_album_snapshot_skips_album_query(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("api/get", SYNCED_RESPONSE))
    lyrics = provider.get_lyrics(snapshot(album=None))
    assert lyrics.kind == "synced"
    assert fake.asked("album_name") == []


# -- caching semantics ---------------------------------------------------


def test_cache_hit_skips_fetch(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    first = provider.get_lyrics(snapshot())
    asked = fake.count
    second = provider.get_lyrics(snapshot())
    assert fake.count == asked  # the second lookup never left the machine
    assert second.synced == first.synced


def test_genuine_not_found_is_cached_negatively(provider, monkeypatch):
    fake = use_fetcher(
        monkeypatch,
        ("album_name", None),
        ("api/search", []),
        ("api/get", None),
    )
    assert provider.get_lyrics(snapshot()) is None
    asked = fake.count
    assert provider.get_lyrics(snapshot()) is None
    assert fake.count == asked  # second call never hit the network
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
    assert fake.asked_once("album_name=Album")


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

    fresh = lp.LyricsProvider(
        cache_dir=provider.cache_dir, user_sync_dir=provider.user_sync_dir
    )
    use_fetcher(monkeypatch, ("album_name", lp.LyricsError("offline")))
    lyrics = fresh.get_lyrics(snapshot())  # cache hit: no fetch, no error
    assert lyrics.synced == [(12.0, "First line"), (17.5, "Second line")]


def test_corrupt_cache_entry_refetches(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    provider.get_lyrics(snapshot())
    asked = fake.count
    provider._cache_path("track123").write_text("{not json", encoding="utf-8")
    assert provider.get_lyrics(snapshot()) is not None
    assert fake.count == asked * 2  # an unreadable entry is a fresh lookup


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


def test_non_music_item_never_touches_network_or_cache(provider, monkeypatch):
    # DJ narration reuses the upcoming song's ID under spotify:media:.
    # It must not be fetched, must not be cached, and must not READ the
    # song's cache entry either.
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    song = snapshot(track_id="shared123")
    assert provider.get_lyrics(song) is not None  # song cached normally

    narration = PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id="shared123",
        track_kind="media",
        title="Up next",
        artist="DJ X",
        album="DJ",
        duration_ms=0,
        position_seconds=1.0,
    )
    asked = fake.count
    assert provider.get_lyrics(narration) is None  # not the song's lyrics
    assert fake.count == asked  # no second network call
    entry = json.loads(provider._cache_path("shared123").read_text(encoding="utf-8"))
    assert entry["found"] is True  # song's entry untouched


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


# -- user syncs (tap-to-sync) --------------------------------------------

USER_LRC = "[00:01.00] Mine first\n[00:04.25] Mine second\n"


def test_user_sync_round_trips(provider):
    provider.save_user_sync("track123", USER_LRC)
    lyrics = provider.read_user_sync("track123")
    assert lyrics.kind == "synced"
    assert lyrics.synced == [(1.0, "Mine first"), (4.25, "Mine second")]


def test_user_sync_beats_the_network(provider, monkeypatch):
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    provider.save_user_sync("track123", USER_LRC)
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.synced == [(1.0, "Mine first"), (4.25, "Mine second")]
    assert fake.calls == []  # never asked LRCLIB


def test_user_sync_beats_the_cache(provider, monkeypatch):
    """The usual reason a sync exists is that the cached answer was plain
    (or wrong), so a cache hit must not shadow it."""
    use_fetcher(monkeypatch, ("album_name", PLAIN_ONLY_RESPONSE))
    assert provider.get_lyrics(snapshot()).kind == "plain"  # populates cache

    provider.save_user_sync("track123", USER_LRC)
    lyrics = provider.get_lyrics(snapshot())
    assert lyrics.kind == "synced"
    assert lyrics.synced == [(1.0, "Mine first"), (4.25, "Mine second")]


def test_clearing_the_cache_leaves_user_syncs_intact(provider, monkeypatch):
    """The documented reset is "delete .lyrics_cache/" — it must not cost
    the user a sync they tapped out by hand."""
    use_fetcher(monkeypatch, ("album_name", PLAIN_ONLY_RESPONSE))
    provider.get_lyrics(snapshot())
    provider.save_user_sync("track123", USER_LRC)

    for entry in provider.cache_dir.iterdir():
        entry.unlink()
    assert provider.get_lyrics(snapshot()).kind == "synced"


def test_user_syncs_live_outside_the_cache_directory(provider):
    path = provider.save_user_sync("track123", USER_LRC)
    assert path.parent == provider.user_sync_dir
    assert provider.user_sync_dir != provider.cache_dir
    assert provider.cache_dir not in path.parents


def test_user_sync_survives_a_new_provider_instance(provider, monkeypatch):
    provider.save_user_sync("track123", USER_LRC)
    fresh = lp.LyricsProvider(
        cache_dir=provider.cache_dir, user_sync_dir=provider.user_sync_dir
    )
    use_fetcher(monkeypatch, ("album_name", lp.LyricsError("offline")))
    assert fresh.get_lyrics(snapshot()).kind == "synced"


def test_has_user_sync_reports_existence(provider):
    assert provider.has_user_sync("track123") is False
    assert provider.has_user_sync(None) is False
    provider.save_user_sync("track123", USER_LRC)
    assert provider.has_user_sync("track123") is True


def test_unparseable_user_sync_falls_back_to_the_normal_chain(provider, monkeypatch):
    """A hand-edit that destroys the timestamps shows the real lyrics
    rather than an empty song."""
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    provider.save_user_sync("track123", "no timestamps at all\n")
    assert provider.get_lyrics(snapshot()).synced == [
        (12.0, "First line"),
        (17.5, "Second line"),
    ]
    assert fake.asked_once("album_name=Album")


def test_non_music_item_never_reads_a_user_sync(provider, monkeypatch):
    # DJ narration reuses the upcoming song's ID, so the ID-keyed user sync
    # is the wrong answer for it just as the cache entry is.
    use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    provider.save_user_sync("shared123", USER_LRC)
    narration = PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id="shared123",
        track_kind="media",
        title="Up next",
        artist="DJ X",
    )
    assert provider.get_lyrics(narration) is None


def test_user_sync_track_id_sanitized_for_filename(provider):
    path = provider.save_user_sync("weird/../id", USER_LRC)
    assert path.parent == provider.user_sync_dir
    assert path.exists()
    assert provider.read_user_sync("weird/../id") is not None


def test_saving_a_user_sync_reports_failure(provider, monkeypatch):
    """Unlike the cache, a save is the user's work — a failure must raise
    so the caller can say so, not vanish."""
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(lp.Path, "write_text", boom)
    with pytest.raises(OSError):
        provider.save_user_sync("track123", USER_LRC)


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


# -- the chain runs its attempts at once ----------------------------------


def test_a_404_hands_the_floor_to_the_next_attempt_at_once(provider, monkeypatch):
    """The chain still falls back, and it does not wait out the hedge to do
    it: a definitive "not here" leaves nothing to overlap with."""
    fake = use_fetcher(monkeypatch, ("api/search", []))
    monkeypatch.setattr(lp, "_HEDGE_SECONDS", 30.0)  # never reached
    started = time.monotonic()
    provider.get_lyrics(snapshot())

    assert sorted(fake.calls) == sorted(lp.attempt_urls(snapshot()))
    assert len(fake.calls) == 3
    assert time.monotonic() - started < 1.0


def test_a_song_with_no_album_asks_two_questions_not_three(provider):
    """The album-less /get and the album-qualified one would be the same
    request, and asking a free service the same question twice at once is
    not a thing to do."""
    urls = lp.attempt_urls(snapshot(album=None))

    assert len(urls) == 2
    assert not any("album_name" in url for url in urls)


def test_the_most_precise_answer_wins_whatever_order_they_land_in(
    provider, monkeypatch
):
    """Priority order, not completion order. Search is the loose match and
    may well answer first; it must still lose to the exact one.

    The hedge is shortened so the fan-out definitely happens: with the
    exact match slow, this is the case the concurrency exists for, and it
    is the only one in which there is an order to get wrong."""
    monkeypatch.setattr(lp, "_HEDGE_SECONDS", 0.01)
    slow_but_exact = threading.Event()

    def fetcher(url):
        if "album_name" in url:
            slow_but_exact.wait(2.0)  # search lands long before this
            return SYNCED_RESPONSE
        if "api/search" in url:
            return [search_record(plainLyrics="the loose answer")]
        return None

    monkeypatch.setattr(lp, "_fetch_json", fetcher)
    threading.Timer(0.05, slow_but_exact.set).start()

    lyrics = provider.get_lyrics(snapshot())

    assert lyrics.kind == "synced"  # not "the loose answer"


def test_a_failure_that_outranks_an_answer_is_still_a_retry_state(
    provider, monkeypatch
):
    """Sequentially, an error on the exact match ended the chain and search
    was never asked. Once the hedge fires it IS asked, and its answer must
    still be refused: caching a loose match because the precise one
    happened to fail would be a wrong answer written down permanently."""
    monkeypatch.setattr(lp, "_HEDGE_SECONDS", 0.01)
    use_fetcher(
        monkeypatch,
        ("album_name", lp.LyricsError("LRCLIB returned HTTP 500")),
        ("api/search", [search_record(syncedLyrics=SYNCED_LRC)]),
    )

    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot())
    assert not provider._cache_path("track123").exists()


def test_a_failure_below_the_winner_is_never_looked_at(provider, monkeypatch):
    """The mirror of the rule above: the exact match answered, so what
    happened to the attempts under it is not the chain's business — it
    would not even have asked them before."""
    fake = use_fetcher(
        monkeypatch,
        ("album_name", SYNCED_RESPONSE),
        ("api/get", lp.LyricsError("timed out")),
        ("api/search", lp.LyricsError("timed out")),
    )

    assert provider.get_lyrics(snapshot()).kind == "synced"
    # And they are not asked either, which is the whole of the hedge.
    assert fake.count == 1


def test_an_attempt_that_never_answers_is_not_a_licence_to_use_a_worse_one(
    provider, monkeypatch
):
    """A wedged attempt has an UNKNOWN outcome, which is a retry state.
    Falling through to search would be treating "we could not ask" as "the
    answer was no"."""
    monkeypatch.setattr(lp, "_ATTEMPT_WAIT", 0.05)
    never = threading.Event()

    def fetcher(url):
        if "album_name" in url:
            never.wait(30.0)
            return None
        return [search_record(syncedLyrics=SYNCED_LRC)]

    monkeypatch.setattr(lp, "_fetch_json", fetcher)
    try:
        with pytest.raises(lp.LyricsError):
            provider.get_lyrics(snapshot())
    finally:
        never.set()


# -- the hedge: the ones below are asked only when they are needed ---------


def test_a_first_attempt_that_answers_is_the_only_question_asked(
    provider, monkeypatch
):
    """MEASURED, and it is the whole reason this exists: over 30 lookups of
    15 real tracks the album match answered 30 times, in 61ms by median.
    Two of every three requests were being made so that an answer nobody
    would read could arrive alongside the one they would."""
    fake = use_fetcher(
        monkeypatch,
        ("album_name", SYNCED_RESPONSE),
        ("api/search", [search_record(syncedLyrics=SYNCED_LRC)]),
    )

    assert provider.get_lyrics(snapshot()).kind == "synced"
    assert fake.count == 1
    assert fake.asked_once("album_name")
    assert fake.asked("api/search") == []


def test_a_slow_first_attempt_lets_the_rest_go_out_beside_it(
    provider, monkeypatch
):
    """The case the concurrency was for. Past the hedge this is the chain
    it always was, one hedge later — so the attempts overlap rather than
    queueing behind a service having a bad minute."""
    monkeypatch.setattr(lp, "_HEDGE_SECONDS", 0.05)
    holding = threading.Event()
    asked_while_holding = threading.Event()

    def fetcher(url):
        if "album_name" in url:
            holding.wait(3.0)
            return SYNCED_RESPONSE
        asked_while_holding.set()
        return None

    monkeypatch.setattr(lp, "_fetch_json", fetcher)
    try:
        # The others go out while the first is still out, not after it.
        threading.Timer(0.5, holding.set).start()
        lyrics = provider.get_lyrics(snapshot())
    finally:
        holding.set()

    assert lyrics.kind == "synced"  # the precise answer still wins
    assert asked_while_holding.is_set()


def test_the_hedge_is_not_waited_out_when_the_answer_is_already_there(
    provider, monkeypatch
):
    """A fast first attempt must not cost the hedge. The delay is a
    deadline for asking the others, never a pause before reading the one
    that answered."""
    monkeypatch.setattr(lp, "_HEDGE_SECONDS", 5.0)
    use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))

    started = time.monotonic()
    assert provider.get_lyrics(snapshot()).kind == "synced"
    assert time.monotonic() - started < 1.0


def test_an_error_on_the_first_attempt_asks_nothing_else(provider, monkeypatch):
    """The chain raises on it, so the attempts below were never going to be
    read. Asking them anyway would be spending a free service's time on
    answers with nowhere to go."""
    fake = use_fetcher(
        monkeypatch, ("album_name", lp.LyricsError("LRCLIB returned HTTP 503"))
    )

    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot())
    assert fake.count == 1


def test_no_question_is_ever_asked_twice(provider, monkeypatch):
    """The hedge asks for everything below the attempt it is waiting on,
    and the loop asks for the next one when it reaches it. Both routes run,
    and a request sent twice is exactly the cost this is here to avoid."""
    monkeypatch.setattr(lp, "_HEDGE_SECONDS", 0.01)
    fake = use_fetcher(monkeypatch, ("api/search", []))
    provider.get_lyrics(snapshot())

    assert sorted(fake.calls) == sorted(set(fake.calls))


def test_the_hedge_is_the_measurement_it_came_from(provider):
    """Not a number set by eye. The first attempt was measured at 61ms by
    median, 103ms at the 95th percentile and 170ms at its slowest, so the
    delay clears the slowest observed response with room and no lookup in
    the sample would have fanned out at all."""
    assert lp._HEDGE_SECONDS >= 0.170
    # And it stays well under the per-attempt bound, or it would be the
    # timeout rather than a hedge before it.
    assert lp._HEDGE_SECONDS < lp._ATTEMPT_WAIT / 10
