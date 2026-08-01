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


# -- where an answer came from --------------------------------------------


def test_a_lookup_says_which_of_the_four_answered(provider, monkeypatch, tmp_path):
    """Only one distinction is acted on — whether LRCLIB itself answered —
    but every source is named, because a boolean at this end would be a
    boolean somebody has to remember the meaning of at the other."""
    use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))

    first = provider.look_up(snapshot())
    assert first.source == lp.FROM_SERVICE
    assert first.from_service is True

    second = provider.look_up(snapshot())
    assert second.source == lp.FROM_CACHE
    assert second.from_service is False

    provider.save_user_sync("track123", "[00:01.00] theirs\n")
    third = provider.look_up(snapshot())
    assert third.source == lp.FROM_USER_SYNC
    assert third.from_service is False

    narration = provider.look_up(snapshot(track_id="x"))
    narration = provider.look_up(
        PlayerSnapshot(state=PlaybackState.PLAYING, track_id="y", track_kind="media")
    )
    assert narration.source == lp.FROM_NOWHERE


def test_get_lyrics_still_answers_with_lyrics_alone(provider, monkeypatch):
    """The terminal tools and every older caller ask this one, and it must
    keep meaning exactly what it meant."""
    use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    assert provider.get_lyrics(snapshot()).kind == "synced"


# -- the pause LRCLIB asks for ---------------------------------------------


@pytest.fixture(autouse=True)
def no_hold():
    """Every test here starts with nothing outstanding. The hold is module
    level for the same reason the pool is — it is a fact about the host —
    which means a 429 in one test would otherwise refuse the next one's
    first request."""
    lp._hold.clear()
    yield
    lp._hold.clear()


class Held:
    """A response, with headers. Enough of http.client's shape for the pool
    to hand it back, which is where Retry-After is read."""

    def __init__(self, status, body=b"{}", headers=()):
        self.status = status
        self._body = body
        self._headers = list(headers)
        self.will_close = False

    def read(self):
        return self._body

    def getheaders(self):
        return list(self._headers)


def answering(monkeypatch, response):
    """A real ConnectionPool over a fake connection, installed as the
    module's. Faked at the connection rather than at _fetch_json, because
    what is under test here is what _fetch_json does with a status and a
    header — stubbing it out would be stubbing out the answer."""
    class Connection:
        def request(self, method, path, body=None, headers=None):
            self.path = path
            self.body = body

        def getresponse(self):
            return response

        def close(self):
            pass

    from sottovoce.http_client import ConnectionPool

    monkeypatch.setattr(
        lp, "_pool", ConnectionPool(lp.LRCLIB_HOST, connect=Connection)
    )


def test_a_429_starts_a_pause_and_carries_the_number_they_gave(
    provider, monkeypatch
):
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    answering(monkeypatch, Held(429, headers=[("Retry-After", "90")]))

    with pytest.raises(lp.LyricsError) as raised:
        provider.get_lyrics(snapshot())

    assert raised.value.failure.status == 429
    assert raised.value.failure.retry_after == 90.0
    assert lp._hold.remaining(1000.0) == 90.0


def test_while_the_pause_runs_nothing_leaves_this_app(provider, monkeypatch):
    """The whole point of the hold being in front of _fetch_json rather
    than in front of the retry timer: a track change asks straight away,
    and LRCLIB's documentation says ignoring Retry-After may earn a ban."""
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    answering(monkeypatch, Held(429, headers=[("Retry-After", "90")]))
    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot())

    asked = []
    monkeypatch.setattr(lp, "_lrclib_pool", lambda: asked.append(1))

    with pytest.raises(lp.LyricsError) as raised:
        provider.get_lyrics(snapshot(track_id="another"))

    assert asked == [], "a request went out during the pause"
    assert raised.value.failure.kind == lp.HELD
    assert raised.value.failure.retry_after == 90.0


def test_the_pause_ends_and_asking_resumes(provider, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    answering(monkeypatch, Held(429, headers=[("Retry-After", "30")]))
    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot())

    clock[0] = 1031.0
    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    assert provider.get_lyrics(snapshot(track_id="later")).kind == "synced"
    assert fake.count == 1


def test_a_retry_after_this_app_cannot_read_is_no_pause_at_all(
    provider, monkeypatch
):
    """The header may be an HTTP date, which would mean trusting this
    machine's clock to agree with theirs. A number that cannot be read is
    treated as no number rather than as a guess, and the caller still backs
    off on its own schedule."""
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    answering(monkeypatch, Held(429, headers=[("Retry-After", "Wed, 21 Oct 2026 07:28:00 GMT")]))

    with pytest.raises(lp.LyricsError) as raised:
        provider.get_lyrics(snapshot())

    assert raised.value.failure.retry_after is None
    assert lp._hold.remaining(1000.0) == 0.0


def test_the_header_is_read_however_it_is_spelled(provider, monkeypatch):
    """HTTP field names are case-insensitive, and a lookup that only knew
    one spelling would quietly read as "they did not ask us to wait"."""
    monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
    answering(monkeypatch, Held(429, headers=[("retry-after", "45")]))

    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot())

    assert lp._hold.remaining(1000.0) == 45.0


# -- the album, fetched before it is needed --------------------------------
#
# Two stages, and the tests are grouped by which one they are about,
# because the split IS the design: one search for any album a track was
# played from, and a request per track only for an album somebody is
# listening through.


ALBUM_SEARCH = [
    {"trackName": "Song", "duration": 225.0, "syncedLyrics": SYNCED_LRC},
    {"trackName": "Second Song", "duration": 240.0, "syncedLyrics": SYNCED_LRC},
    # The same name at a different length. LRCLIB really does answer like
    # this, and which of the two is the recording nobody can say until the
    # track plays.
    {"trackName": "Second Song", "duration": 225.0, "syncedLyrics": SYNCED_LRC},
    {"trackName": "Third Song", "duration": 300.0, "syncedLyrics": SYNCED_LRC},
    {"trackName": ""},
]


def warm_fetcher(monkeypatch, search=None, **per_track):
    """The two stages: one search that names tracks and carries their
    lyrics, then one /get per name. Each name may be routed to a record, to
    None (a 404) or to an exception."""
    routes = []
    for name, response in per_track.items():
        routes.append((f"track_name={name.replace('_', '+')}", response))
    routes.append(("api/search", ALBUM_SEARCH if search is None else search))
    return use_fetcher(monkeypatch, *routes)


def record(duration=225.0, **fields):
    """One /get answer for a track on the album. The default duration is
    the snapshot's, so a warmed track is one the duration check accepts;
    the tests that care hand over a different one."""
    return {
        "trackName": "Second Song",
        "artistName": "Artist",
        "albumName": "Album",
        "duration": duration,
        "syncedLyrics": SYNCED_LRC,
        "plainLyrics": None,
        **fields,
    }


def playing(title="Second Song", track_id="t2"):
    return snapshot(track_id=track_id, title=title)


# -- stage one: one search, whatever it happens to carry --------------------


def test_a_track_from_a_new_album_costs_exactly_one_request(provider, monkeypatch):
    """The stage that runs for every album, and the only one most albums
    ever cost. Nineteen requests for a song somebody skipped past is the
    thing the split exists to stop."""
    fake = warm_fetcher(monkeypatch)

    provider.warm_album(snapshot(), sleep=lambda _: None)

    assert len(fake.asked("api/search")) == 1
    assert fake.asked("api/get") == []
    assert provider.album_is_searched(snapshot()) is True
    assert provider.album_is_warm(snapshot()) is False


def test_the_search_records_are_kept_and_can_answer(provider, monkeypatch):
    """One request, and the album's second track already has lyrics."""
    warm_fetcher(monkeypatch)
    provider.warm_album(snapshot(), sleep=lambda _: None)

    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    answer = provider.look_up(playing())

    assert answer.source == lp.FROM_WARM
    assert fake.count == 0


def test_every_record_under_one_name_is_kept(provider, monkeypatch):
    """The search answers with the same title at several lengths, and which
    is this recording is a question nobody can answer at the time. So they
    are all kept and the duration decides when the track is in hand."""
    warm_fetcher(monkeypatch)
    provider.warm_album(snapshot(), sleep=lambda _: None)

    entry = provider._read_warm_entry("Artist", "Album", "Second Song")
    assert [r["duration"] for r in entry["records"]] == [240.0, 225.0]
    # 225s is the one playing, so that is the one served.
    assert provider.read_warm(playing()) is not None


def test_the_search_is_asked_once_however_many_tracks_play(provider, monkeypatch):
    fake = warm_fetcher(monkeypatch)
    provider.warm_album(snapshot(), sleep=lambda _: None)
    asked = fake.count

    provider.warm_album(snapshot(), sleep=lambda _: None)  # the same track

    assert fake.count == asked


# -- stage two: a second track is the intent --------------------------------


def test_a_second_track_from_the_album_is_what_buys_the_per_track_pass(
    provider, monkeypatch
):
    """A second track is the difference between a song somebody heard and
    an album somebody is listening to, and it is the only signal there
    is."""
    slept = []
    fake = warm_fetcher(
        monkeypatch, Second_Song=record(), Third_Song=record(duration=300.0)
    )
    provider.warm_album(snapshot(), sleep=slept.append)
    assert fake.asked("api/get") == []

    provider.warm_album(playing(), sleep=slept.append)

    # One per name the search returned, the playing track's included: its
    # own lookup answered a different question (this recording), and the
    # name may cover other tracks the user has not reached.
    assert len(fake.asked("api/get")) == 3
    assert slept == [lp.REQUEST_GAP_SECONDS] * 3
    assert provider.album_is_warm(playing()) is True


def test_the_per_track_answer_is_added_rather_than_substituted(
    provider, monkeypatch
):
    """Stage two is better sourced and goes first, but it can also be a
    different recording than the search found — throwing the search's away
    would be losing a track to a request meant to gain one."""
    warm_fetcher(monkeypatch, Second_Song=record(duration=180.0))
    provider.warm_album(snapshot(), sleep=lambda _: None)
    provider.warm_album(playing(), sleep=lambda _: None)

    entry = provider._read_warm_entry("Artist", "Album", "Second Song")
    assert entry["records"][0]["duration"] == 180.0, "the /get answer is not first"
    assert 225.0 in [r["duration"] for r in entry["records"]]
    # And the track that actually plays is still served, from the record
    # the search found.
    assert provider.read_warm(playing()) is not None


def test_the_same_track_again_is_not_a_second_track(provider, monkeypatch):
    fake = warm_fetcher(monkeypatch, Second_Song=record())
    provider.warm_album(snapshot(), sleep=lambda _: None)
    asked = fake.count

    provider.warm_album(snapshot(track_id="track123"), sleep=lambda _: None)

    assert fake.count == asked
    assert provider.album_is_warm(snapshot()) is False


def test_a_name_already_asked_about_is_not_asked_again(provider, monkeypatch):
    """Stage two is once per album ever, and the note that says so is on
    each name rather than only on the album."""
    fake = warm_fetcher(monkeypatch, Second_Song=record())
    provider.warm_album(snapshot(), sleep=lambda _: None)
    provider.warm_album(playing(), sleep=lambda _: None)
    asked = len(fake.asked("api/get"))

    provider.warm_album(playing(track_id="t3", title="Third Song"), sleep=lambda _: None)

    assert len(fake.asked("api/get")) == asked, "the album was warmed twice"


def test_one_failure_ends_the_album(provider, monkeypatch):
    """Nothing is waiting on this, and a service that just refused one
    request is not one to ask eighteen more times."""
    fake = warm_fetcher(
        monkeypatch,
        Song=lp.LyricsError("nope"),
        Second_Song=record(),
    )
    provider.warm_album(snapshot(), sleep=lambda _: None)

    assert provider.warm_album(playing(), sleep=lambda _: None) == 0
    assert len(fake.asked("api/get")) == 1
    # Left unwarmed, so a later track from the album may try again.
    assert provider.album_is_warm(playing()) is False


def test_a_pass_can_be_stopped_partway(provider, monkeypatch):
    """Shutdown's half of it: this sleeps between requests by design, so
    without a way to end it the pool would wait out the album."""
    stop = []
    fake = warm_fetcher(monkeypatch, Second_Song=record())
    provider.warm_album(snapshot(), sleep=lambda _: None)

    provider.warm_album(
        playing(),
        sleep=lambda _: None,
        should_stop=lambda: bool(stop) or stop.append(1),
    )

    assert len(fake.asked("api/get")) <= 1
    assert provider.album_is_warm(playing()) is False


# -- what the store may and may not say -------------------------------------


def test_a_warmed_track_is_served_before_the_network_and_says_so(
    provider, monkeypatch
):
    warm_fetcher(monkeypatch, Second_Song=record())
    provider.warm_album(snapshot(), sleep=lambda _: None)
    provider.warm_album(playing(), sleep=lambda _: None)

    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    answer = provider.look_up(playing(track_id="t9"))

    assert answer.source == lp.FROM_WARM
    assert answer.from_service is False
    assert answer.lyrics.kind == "synced"
    assert fake.count == 0, "a warmed track went to the network anyway"


def test_a_warm_hit_becomes_an_ordinary_cache_entry(provider, monkeypatch):
    """Promoted on the way past, so the second play takes the fast path and
    the duration is never checked twice."""
    warm_fetcher(monkeypatch)
    provider.warm_album(snapshot(), sleep=lambda _: None)
    assert provider.look_up(playing()).source == lp.FROM_WARM

    assert provider.look_up(playing()).source == lp.FROM_CACHE


def test_a_different_recording_of_the_same_name_is_refused(provider, monkeypatch):
    """The check that makes a warm answer trustworthy. It is the /get
    tolerance rather than the looser search one, because this answer stands
    in for the album match: MEASURED, 3 of 20 warmed tracks across 4 real
    albums were a different recording, and those three land here.
    """
    warm_fetcher(
        monkeypatch,
        search=[{"trackName": "Second Song", "duration": 180.0,
                 "syncedLyrics": SYNCED_LRC}],
    )
    provider.warm_album(snapshot(), sleep=lambda _: None)

    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    answer = provider.look_up(playing())  # 225s

    assert answer.source == lp.FROM_SERVICE, "a wrong recording was served"
    assert fake.count >= 1


def test_a_track_the_warm_never_reached_falls_straight_through(
    provider, monkeypatch
):
    """The warm store can answer yes and it can answer nothing. It can
    never answer "this track has no lyrics" — it is a guess made without
    the track in hand, and a guess may not stop the real question being
    asked."""
    warm_fetcher(monkeypatch)
    provider.warm_album(snapshot(), sleep=lambda _: None)

    fake = use_fetcher(monkeypatch, ("album_name", SYNCED_RESPONSE))
    answer = provider.look_up(playing(track_id="t9", title="Never Warmed"))

    assert answer.source == lp.FROM_SERVICE
    assert answer.lyrics.kind == "synced"
    assert list(provider.warm_dir.glob("*.json"))  # the store is still there


def test_a_record_with_no_duration_is_not_worth_keeping(provider, monkeypatch):
    """There would be no way to recognise the recording later, and an
    unverifiable warm entry is exactly what "prefer no lyrics to
    mismatched-duration lyrics" is about."""
    warm_fetcher(
        monkeypatch,
        search=[{"trackName": "Second Song", "syncedLyrics": SYNCED_LRC}],
    )

    assert provider.warm_album(snapshot(), sleep=lambda _: None) == 0
    assert provider.read_warm(playing()) is None


def test_an_instrumental_record_is_not_worth_keeping(provider, monkeypatch):
    warm_fetcher(
        monkeypatch,
        search=[{"trackName": "Second Song", "duration": 225.0}],
    )

    assert provider.warm_album(snapshot(), sleep=lambda _: None) == 0
    assert provider.read_warm(playing()) is None


def test_a_search_that_answered_nothing_is_still_an_answer(provider, monkeypatch):
    """"Asked and got nothing" is what stops this being asked again on the
    next track of the same album."""
    fake = warm_fetcher(monkeypatch, search=[])

    assert provider.warm_album(snapshot(), sleep=lambda _: None) == 0
    assert provider.album_is_searched(snapshot()) is True
    provider.warm_album(snapshot(), sleep=lambda _: None)
    assert len(fake.asked("api/search")) == 1


def test_nothing_is_warmed_for_an_item_with_no_album(provider, monkeypatch):
    fake = use_fetcher(monkeypatch)
    assert provider.warm_album(snapshot(album=""), sleep=lambda _: None) == 0
    assert fake.count == 0


def test_narration_never_warms_anything(provider, monkeypatch):
    """Non-music items reuse the upcoming song's ID and must not touch the
    cache or the network at all, and that includes speculatively."""
    fake = use_fetcher(monkeypatch)
    narration = PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id="track123",
        track_kind="media",
        title="Song",
        artist="Artist",
        album="Album",
        duration_ms=225000,
    )

    assert provider.warm_album(narration, sleep=lambda _: None) == 0
    assert fake.count == 0


def test_the_warm_store_lives_inside_the_cache_directory(provider):
    """Clearing .lyrics_cache/ is a documented reset and must stay one.
    This is the only thing here nobody made, so it has to go with it."""
    assert provider.warm_dir.parent == provider.cache_dir
    assert provider.user_sync_dir not in provider.warm_dir.parents


# -- writing to LRCLIB, and the record of having done it ---------------------
#
# The provider's half of publishing: the one door a POST goes through, the
# question the publish path asks fresh, and the sidecar that remembers what
# was sent. The exchange itself is test_publish.py's and the whole path is
# tests/window/test_window_publish.py's.


def posting(monkeypatch, response):
    """The same seam ``answering`` uses, one verb along: a real pool over a
    fake connection, so what is under test is what post_json does with a
    status rather than what a stub was told to return."""
    sent = {}

    class Connection:
        def request(self, method, path, body=None, headers=None):
            sent.update(method=method, path=path, body=body, headers=headers)

        def getresponse(self):
            return response

        def close(self):
            pass

    from sottovoce.http_client import ConnectionPool

    monkeypatch.setattr(
        lp, "_pool", ConnectionPool(lp.LRCLIB_HOST, connect=Connection)
    )
    return sent


def test_a_post_carries_the_apps_user_agent_and_its_json(monkeypatch):
    """One definition of the User-Agent, and the body is the payload rather
    than a rendering of it."""
    sent = posting(monkeypatch, Held(201, body=b""))

    assert lp.post_json(lp.LRCLIB_PUBLISH_URL, {"trackName": "Blue Hour"}) is None

    assert sent["method"] == "POST"
    assert sent["path"] == "/api/publish"
    assert json.loads(sent["body"]) == {"trackName": "Blue Hour"}
    assert sent["headers"]["User-Agent"] == lp.USER_AGENT
    assert sent["headers"]["Content-Type"] == "application/json"


def test_a_created_with_no_body_is_a_success_rather_than_a_broken_answer(
    monkeypatch,
):
    """201 and nothing else is what publishing answers with, and reading an
    empty body as unparseable would turn every publish into a failure."""
    posting(monkeypatch, Held(201, body=b""))
    assert lp.post_json(lp.LRCLIB_PUBLISH_URL, {}) is None


def test_a_post_may_carry_a_header_of_its_own(monkeypatch):
    sent = posting(monkeypatch, Held(200))

    lp.post_json(lp.LRCLIB_PUBLISH_URL, {}, {"X-Publish-Token": "prefix:42"})

    assert sent["headers"]["X-Publish-Token"] == "prefix:42"


def test_a_post_that_is_refused_carries_the_status_back(monkeypatch):
    """The status is the whole of what the publish path needs to tell its
    failures apart: 400 is the token, and it is the recoverable one."""
    posting(monkeypatch, Held(400, body=b'{"name": "IncorrectPublishTokenError"}'))

    with pytest.raises(lp.LyricsError) as raised:
        lp.post_json(lp.LRCLIB_PUBLISH_URL, {})

    assert raised.value.failure.status == 400


def test_a_429_on_a_post_starts_the_same_pause_a_lookup_would(monkeypatch):
    """The hold is a fact about the host rather than about an errand, so
    the verb it arrived on makes no difference to it."""
    monkeypatch.setattr(time, "monotonic", lambda: 5000.0)
    posting(monkeypatch, Held(429, headers=[("Retry-After", "120")]))

    with pytest.raises(lp.LyricsError):
        lp.post_json(lp.LRCLIB_PUBLISH_URL, {})

    assert lp._hold.remaining(5000.0) == 120.0


def test_a_post_is_refused_while_the_pause_runs(monkeypatch):
    """And in the other direction: a pause a lookup earned stops a publish
    before a socket is opened."""
    monkeypatch.setattr(time, "monotonic", lambda: 5000.0)
    lp._hold.asked_to_wait(60.0, 5000.0)
    posting(monkeypatch, Held(201, body=b""))

    with pytest.raises(lp.LyricsError) as raised:
        lp.post_json(lp.LRCLIB_PUBLISH_URL, {})

    assert raised.value.failure.kind == lp.HELD


def test_the_fresh_check_asks_the_exact_signature_and_hands_back_the_record(
    monkeypatch,
):
    """The whole record rather than the TrackLyrics the app displays: what
    publishing turns on is whether LRCLIB has words and no timings, and two
    of those three facts are thrown away on the way to a TrackLyrics."""
    asked = []

    def fake_fetch(url):
        asked.append(url)
        return {"plainLyrics": "a line", "syncedLyrics": None, "instrumental": False}

    monkeypatch.setattr(lp, "_fetch_json", fake_fetch)

    record = lp.track_record("Blue Hour", "Someone", "First Light", 213.6)

    assert record["plainLyrics"] == "a line"
    assert asked[0].startswith(lp.LRCLIB_GET_URL)
    assert "track_name=Blue+Hour" in asked[0]
    assert "album_name=First+Light" in asked[0]
    assert "duration=214" in asked[0], "the duration was not rounded the way /get wants"


def test_the_fresh_check_reads_and_writes_no_cache(provider, monkeypatch, tmp_path):
    """The reason it exists. A cached answer is what LRCLIB said the first
    time this song played, and a publication is permanent."""
    monkeypatch.setattr(lp, "_fetch_json", lambda url: {"plainLyrics": "a line"})

    lp.track_record("Blue Hour", "Someone", "First Light", 213.0)

    assert list(provider.cache_dir.glob("*.json")) == []


# -- what has been published ------------------------------------------------


LRC = "[00:01.00] first line\n[00:05.00] second line\n"


def test_nothing_is_published_until_it_is_recorded(provider):
    provider.save_user_sync("track123", LRC)
    assert provider.is_published("track123", LRC) is False
    assert provider.published_record("track123") is None


def test_a_recorded_publication_is_remembered_for_that_text_alone(provider):
    """The record is of the TEXT, not of the track: a re-sync is a
    different thing to send, and the entry has to come back for it."""
    provider.save_user_sync("track123", LRC)
    provider.record_published("track123", LRC)

    assert provider.is_published("track123", LRC) is True
    assert provider.is_published("track123", LRC.replace("00:01", "00:02")) is False


def test_the_record_sits_beside_the_sync_rather_than_in_the_cache(provider):
    """Clearing .lyrics_cache/ is a documented reset. Forgetting what has
    been published is not a reset: it is an app offering to send somebody's
    work a second time."""
    provider.save_user_sync("track123", LRC)
    path = provider.record_published("track123", LRC)

    assert path.parent == provider.user_sync_dir
    assert provider.cache_dir not in path.parents
    assert path != provider.user_sync_path("track123")


def test_recording_a_publication_leaves_the_sync_untouched(provider):
    """.user_syncs/ is the user's work. Publishing copies it outward."""
    path = provider.save_user_sync("track123", LRC)
    before = path.read_bytes()

    provider.record_published("track123", LRC)

    assert path.read_bytes() == before
    assert sorted(p.suffix for p in provider.user_sync_dir.iterdir()) == [
        ".lrc",
        ".partial",
        ".published",
    ]


def test_an_unreadable_record_is_the_same_as_no_record(provider):
    """Best-effort on the way IN, like every other file this app reads: a
    truncated sidecar means the entry comes back, which is the safe way for
    this one to fail."""
    provider.save_user_sync("track123", LRC)
    provider.published_path("track123").write_text("{ not json", encoding="utf-8")

    assert provider.is_published("track123", LRC) is False


def test_the_sync_text_is_handed_back_exactly_as_it_sits_on_disk(provider):
    """The file is what would be sent and what is remembered as sent, so
    anything that took a round trip through parse_lrc would be a second
    version of it."""
    provider.save_user_sync("track123", LRC)
    assert provider.user_sync_text("track123") == LRC
    assert provider.user_sync_text("nothing here") is None
    assert provider.user_sync_text(None) is None


# -- what the cache says about LRCLIB's answer ------------------------------


def test_a_cached_plain_answer_is_what_makes_the_entry_worth_offering(provider):
    provider._write_cache("track123", lp.TrackLyrics(plain="a line"))
    assert provider.remote_was_plain_only("track123") is True


def test_a_cached_synced_answer_is_not(provider):
    provider._write_cache("track123", lp.TrackLyrics(synced=[(1.0, "a line")]))
    assert provider.remote_was_plain_only("track123") is False


def test_a_track_lrclib_has_nothing_for_is_not(provider):
    """Publishing lyrics for a track LRCLIB has no record of is a different
    thing and a later step."""
    provider._write_cache("track123", None)
    assert provider.remote_was_plain_only("track123") is False


def test_a_track_nobody_has_asked_about_is_not(provider):
    assert provider.remote_was_plain_only("track123") is False
    assert provider.remote_was_plain_only(None) is False
