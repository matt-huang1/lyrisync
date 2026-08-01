"""Lyrics arriving, from the wire to the line on screen.

Every other window test hands ``_on_fetch_finished`` a ``TrackLyrics`` it
built itself: ``FetchTask.run`` is stubbed for the whole directory, because
left alone a track change goes to LRCLIB for real and that is what aborted
CI mid-handshake. So the app's central path was covered in halves that
never met — ``lyrics_provider`` against a fake ``_fetch_json`` on one side,
a window against a hand-made answer on the other — and nothing asked
whether the two fit together.

Here they do. The window, ``FetchTask``, ``LyricsProvider``, the fallback
chain, ``ConnectionPool`` and the cache are all the real ones; the fake is
one connection object, injected where the pool opens a socket, which is
the only thing in that list that is not this app. The track change is not
supplied either: a real ``PlayerMonitor`` over a fake Spotify decides there
was one, exactly as it does in test_window_player.py, because "a new song
started" is where this path is entered from.

Four answers a song can get, and they are four different states rather than
four shapes of the same one: a synced hit, a plain one, a track LRCLIB
genuinely does not have, and a service failure — which is the one that must
NOT be written down as "no lyrics", because its outcome is unknown.
"""

TIER = "integration"  # the window, the fetch, the provider and the pool

import json
import threading
import time

import pytest

from sottovoce import lyrics_provider as lp
from sottovoce import player_monitor as pmon
from sottovoce import window as w
from sottovoce.http_client import ConnectionPool
from sottovoce.view_model import Mode

from helpers import APP, pressing, shown

# Captured at import, before the directory's autouse fixture stubs it. The
# same trick helpers.py uses for the artwork worker, and it stays here
# rather than moving there because one file needs it.
REAL_FETCH_RUN = w.FetchTask.run
REAL_WARM_RUN = w.WarmTask.run


# -- the one thing here that is not this app -------------------------------


SONG = {
    "uri": "spotify:track:0Ab1Cd2Ef3",
    "title": "Blue Hour",
    "artist": "Someone",
    "album": "First Light",
    "duration_ms": 214000,
}

SYNCED_LRC = "[00:01.00] first line\n[00:05.50] second line\n"


def payload(synced=None, plain=None):
    return json.dumps({"syncedLyrics": synced, "plainLyrics": plain}).encode()


SYNCED_BODY = payload(synced=SYNCED_LRC, plain="first line\nsecond line")
PLAIN_BODY = payload(plain="first line\nsecond line\nthird line")


class FakeResponse:
    def __init__(self, status, body, headers=()):
        self.status = status
        self._body = body
        self._headers = list(headers)
        # The server keeps the connection, which is what LRCLIB measurably
        # does and what the pool exists for: the next attempt in the chain
        # reuses this one rather than opening another.
        self.will_close = False

    def read(self):
        return self._body

    def getheaders(self):
        return list(self._headers)


class FakeLrclib:
    """LRCLIB, one layer below the app: a connection factory, and what it
    answers to each path.

    Routed by substring in the order given, first match wins, exactly as
    test_lyrics_provider.py's fetcher is — the three request shapes are told
    apart by ``album_name=`` (the exact match), ``api/get`` (the same
    question without the album) and ``api/search`` (the loose one).

    An unrouted path is a 404 and not an error, because a 404 is what
    LRCLIB says about a question it has no answer to, and the chain is
    meant to walk past those.

    A route may carry headers as a third item, which is how a 429 says how
    long it wants to be left alone.
    """

    def __init__(self, *routes):
        self.routes = list(routes)
        self.asked = []
        self.headers = []
        self.connections = 0
        self._lock = threading.Lock()

    def connect(self):
        with self._lock:
            self.connections += 1
        return _FakeConnection(self)

    def answer(self, path, headers):
        with self._lock:
            self.asked.append(path)
            self.headers.append(headers)
        for substring, response in self.routes:
            if substring in path:
                return response
        return (404, b"")

    def asked_for(self, substring):
        with self._lock:
            return [path for path in self.asked if substring in path]


class _FakeConnection:
    def __init__(self, service):
        self._service = service
        self._pending = None
        self.closed = False

    def request(self, method, path, headers=None):
        self._pending = self._service.answer(path, dict(headers or {}))

    def getresponse(self):
        status, body, *headers = self._pending
        return FakeResponse(status, body, headers[0] if headers else ())

    def close(self):
        self.closed = True


@pytest.fixture
def lrclib(monkeypatch):
    """A pool that opens fakes instead of sockets.

    The POOL is the real one — this is ``http_client.ConnectionPool`` with
    its own connect factory, which is the seam that module was given so the
    suite could exercise reuse and the stale-connection retry without a
    socket. Installed as the module's pool, so ``_fetch_json`` finds it the
    way it finds the real one and nothing above here knows the difference.
    """
    def install(*routes):
        service = FakeLrclib(*routes)
        monkeypatch.setattr(
            lp, "_pool", ConnectionPool(lp.LRCLIB_HOST, connect=service.connect)
        )
        return service

    return install


# -- a real fetch, on a real worker thread ---------------------------------


@pytest.fixture
def fetching(monkeypatch):
    """Give ``FetchTask`` its body back for the length of one test."""
    monkeypatch.setattr(w.FetchTask, "run", REAL_FETCH_RUN)


class FakeSpotify:
    """Enough of a player to announce a song and hold a position."""

    def __init__(self, song=SONG):
        self.song = song
        self.position = 0.0
        self.state = "playing"

    def answer(self, script):
        if script != pmon._SNAPSHOT_SCRIPT:
            return ""
        return "\n".join([
            self.state,
            self.song["uri"],
            self.song["title"],
            self.song["artist"],
            self.song["album"],
            str(self.song["duration_ms"]),
            f"{self.position:.3f}",
        ])


@pytest.fixture
def spotify(monkeypatch):
    fake = FakeSpotify()
    monkeypatch.setattr(pmon, "_ask", fake.answer)
    monkeypatch.setattr(pmon, "spotify_running", lambda: True)
    monkeypatch.setattr(pmon, "_moved", None, raising=False)
    yield fake
    pmon._wake.clear()
    pmon._moved = None


def monitor_for(window):
    """A real monitor wired to the real window's slots."""
    return pmon.PlayerMonitor(
        on_track_change=window._on_track_change,
        on_position_update=window._on_position_update,
        on_state_change=window._on_state_change,
    )


def settled(window, seconds=5.0):
    """Pump the event loop until the lookup has answered.

    The fetch runs on the global QThreadPool and its result crosses back on
    a queued signal, so both halves need the loop turning. Waiting on the
    MODE rather than on the pool is what makes this honest about the thing
    under test: it is done when the window knows, not when the worker
    stopped.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if window._view_model.display().mode is not Mode.FETCHING:
            window._title_card_until = 0.0  # the card is not what is asked here
            window._render()
            APP.processEvents()
            return
        time.sleep(0.005)
    raise AssertionError("the lookup never came back")


def worked(window):
    """Let every worker the window started finish, and its answer come back.

    Only the non-music case needs this, and the reason is worth saying out
    loud: nothing about it ever enters FETCHING, so there is no state
    change for ``settled`` to wait on — a test that only pumped the loop
    would be asserting that no request had been made YET.
    """
    assert window._pool.waitForDone(5000), "a worker never finished"
    APP.processEvents()


def play(window, spotify, position=None):
    """A song starts, and the monitor is the one that notices."""
    monitor = monitor_for(window)
    monitor.tick()
    APP.processEvents()
    settled(window)
    if position is not None:
        spotify.position = position
        monitor.tick()
        APP.processEvents()
    return monitor


# -- the four answers ------------------------------------------------------


def test_a_synced_song_reaches_the_window_as_a_sung_line(
    make_window, lrclib, spotify, fetching
):
    """The path the app exists for, with nothing about it supplied: the
    monitor found a track, the window asked, the provider walked the chain,
    the pool made the request, and the line on screen is the one the
    timestamp names."""
    service = lrclib(("album_name=", (200, SYNCED_BODY)))
    window = make_window()

    play(window, spotify, position=2.0)

    assert window._view_model.display().mode is Mode.SYNCED
    assert window._current.text() == "first line"
    # The album match answered, so nothing below it was ever asked. That is
    # the hedge's whole point and it can only be seen from out here.
    assert len(service.asked_for("album_name=")) == 1
    assert service.asked_for("api/search") == []


def test_the_request_carries_this_apps_own_user_agent(
    make_window, lrclib, spotify, fetching
):
    """One definition of the User-Agent, asserted where it actually goes.
    Every other test of it reads the constant, which is the same string
    agreeing with itself."""
    service = lrclib(("album_name=", (200, SYNCED_BODY)))
    window = make_window()

    play(window, spotify)

    assert service.headers[0]["User-Agent"] == lp.USER_AGENT
    assert SONG["title"].replace(" ", "+") in service.asked[0]


def test_a_plain_answer_comes_through_as_plain_lyrics(
    make_window, lrclib, spotify, fetching
):
    """LRCLIB has the words and not the timings. The window has a whole
    other layout for that, and which one it wears is decided by what came
    back rather than by the test."""
    lrclib(("album_name=", (200, PLAIN_BODY)))
    window = make_window()

    play(window, spotify, position=2.0)

    assert window._view_model.display().mode is Mode.PLAIN
    assert "first line" in window._plain_label.text()
    assert window._why_button.isVisibleTo(window) is False


def test_the_chain_falls_through_a_404_to_the_attempt_below_it(
    make_window, lrclib, spotify, fetching
):
    """A 404 is a definitive answer to one question and says nothing about
    the next, so the attempt below gets the floor at once — no hedge to
    wait out, because there is nothing left to overlap with."""
    service = lrclib(("api/search", (200, json.dumps([{
        "trackName": SONG["title"],
        "artistName": SONG["artist"],
        "duration": SONG["duration_ms"] / 1000,
        "syncedLyrics": SYNCED_LRC,
        "plainLyrics": None,
    }]).encode())))
    window = make_window()

    play(window, spotify, position=2.0)

    assert window._view_model.display().mode is Mode.SYNCED
    assert window._current.text() == "first line"
    assert len(service.asked_for("album_name=")) == 1
    assert len(service.asked_for("api/search")) == 1


def test_a_track_lrclib_does_not_have_says_so_and_offers_nothing_to_click(
    make_window, lrclib, spotify, fetching, tmp_path
):
    """A genuine miss: every attempt 404s, which is an ANSWER. The window
    says there are none, there is no reason to ask for, and the result is
    written down so the same song is never looked up again."""
    service = lrclib()  # nothing routed: 404 all the way down
    window = make_window()

    play(window, spotify)

    assert window._view_model.display().mode is Mode.NO_LYRICS
    assert window._current.text() == "no lyrics found"
    assert window._why_button.isVisibleTo(window) is False
    assert len(service.asked) == 3, "the whole chain was not walked"

    cached = list(window._provider.cache_dir.glob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text())["found"] is False


def test_a_service_failure_becomes_the_retry_state_and_is_never_cached(
    make_window, lrclib, spotify, fetching
):
    """The distinction the whole failure path is built around. A 503 is not
    "this song has no lyrics": the outcome is unknown, so the window says
    it will retry, the reason is one click away, and nothing about it is
    written down — a cached miss here would be a wrong answer kept for
    ever."""
    service = lrclib(("album_name=", (503, b"upstream is unhappy")))
    window = make_window()

    play(window, spotify)

    assert window._view_model.display().mode is Mode.ERROR
    assert window._current.text() == "lyrics unavailable, will retry"
    assert window._why_button.isVisibleTo(window) is True

    window._why_button.click()
    APP.processEvents()
    assert window._upcoming.text() == "LRCLIB answered HTTP 503 · album match"

    assert list(window._provider.cache_dir.glob("*.json")) == []
    # And an error on the attempt that outranks the rest stops the chain:
    # using a looser answer would be writing a wrong one down.
    assert service.asked_for("api/search") == []


# -- what happens next -----------------------------------------------------


def test_the_retry_asks_again_and_the_second_answer_lands(
    make_window, lrclib, spotify, fetching
):
    """"Will retry" is a promise, and the only place it can be checked is
    here: the retry runs the same worker against the same provider, and
    what has to arrive is a second request on the wire and a window that
    changes its mind."""
    service = lrclib(("album_name=", (503, b"")))
    window = make_window()
    play(window, spotify)
    assert window._view_model.display().mode is Mode.ERROR
    failed = len(service.asked)

    service.routes = [("album_name=", (200, SYNCED_BODY))]
    window._view_model._error_at = -1000.0  # due now
    window._tick_retry()
    settled(window)

    assert len(service.asked) > failed, "the retry never went out"
    assert window._view_model.display().mode is Mode.SYNCED


def test_the_same_song_again_is_answered_from_the_cache(
    make_window, lrclib, spotify, fetching
):
    """The cache the first lookup wrote is the one the second reads, and
    the proof is a request that never happens."""
    service = lrclib(("album_name=", (200, SYNCED_BODY)))
    window = make_window()
    play(window, spotify)
    asked = len(service.asked)

    second = make_window()  # a fresh window on the same cache directory
    play(second, spotify, position=2.0)

    assert second._view_model.display().mode is Mode.SYNCED
    assert second._current.text() == "first line"
    assert len(service.asked) == asked, "the cache was not consulted"


def test_the_users_own_sync_outranks_the_network(
    make_window, lrclib, spotify, fetching
):
    """.user_syncs/ is the user's work, not a cache: they made it because
    the remote answer was wrong, so nothing may go out and overrule it."""
    service = lrclib(("album_name=", (200, PLAIN_BODY)))
    window = make_window()
    window._provider.save_user_sync(
        pmon._parse_track_id(SONG["uri"]), "[00:01.00] the line they tapped\n"
    )

    play(window, spotify, position=2.0)

    assert window._view_model.display().mode is Mode.SYNCED
    assert window._current.text() == "the line they tapped"
    assert service.asked == []


def test_narration_asks_nothing_and_leaves_the_song_it_announced_alone(
    make_window, lrclib, spotify, fetching
):
    """DJ narration reuses the UPCOMING song's ID, which is why a lookup
    for one is wrong in both directions: reading would put the song's
    lyrics on screen over the narration, and WRITING would answer the
    narration's three 404s under the song's own key and leave that answer
    there for ever.

    The song is played second on purpose. It is the half that can only be
    seen from out here — the narration and the song are two windows, two
    lookups and one cache, and the poisoning is invisible until something
    asks the second question.
    """
    service = lrclib(("album_name=", (200, SYNCED_BODY)))
    narration = make_window()
    spotify.song = {**SONG, "uri": SONG["uri"].replace(":track:", ":media:")}

    monitor_for(narration).tick()
    worked(narration)

    assert narration._view_model.display().mode is Mode.NON_MUSIC
    assert service.asked == [], "narration went to the network"
    assert list(narration._provider.cache_dir.glob("*.json")) == []

    # And now the song it was announcing, on the same cache directory.
    song = make_window()
    spotify.song = SONG
    play(song, spotify, position=2.0)

    assert song._view_model.display().mode is Mode.SYNCED
    assert song._current.text() == "first line"


# -- asking again, by hand -------------------------------------------------


def test_the_reason_and_the_retry_are_reached_by_a_press_qt_routes(
    make_window, lrclib, spotify, fetching
):
    """The two controls beside a failed lookup, pressed where they are.

    Every other test of these calls ``click()``, which names the receiver
    and so cannot fail the way a hit-testing bug fails. Here the press goes
    to the top-level QWindow and Qt picks what is under the point: the ⓘ
    reveals the reason, the retry appears beside it, and pressing that
    really does put a second request on the wire and change the window's
    mind. The whole path is real — the press, the model, the worker, the
    provider, the pool and the connection.
    """
    service = lrclib(("album_name=", (503, b"")))
    window = shown(make_window())
    play(window, spotify)
    assert window._view_model.display().mode is Mode.ERROR
    failed = len(service.asked)

    # Nothing to retry with until the reason is out: the retry belongs to
    # the explanation, not to the message.
    assert window._retry_button.isVisibleTo(window) is False
    why = pressing(window, window._why_button)
    assert why.acted == 1, "the reason control did not take its own press"
    assert why.dragged == 0, "the reason control's press reached the drag handler"
    assert window._upcoming.text() == "LRCLIB answered HTTP 503 · album match"
    assert window._retry_button.isVisibleTo(window) is True

    service.routes = [("album_name=", (200, SYNCED_BODY))]
    retry = pressing(window, window._retry_button)
    settled(window)

    assert retry.acted == 1, "the retry did not take its own press"
    assert retry.dragged == 0, "the retry's press reached the drag handler"
    assert len(service.asked) > failed, "no second request went out"
    assert window._view_model.display().mode is Mode.SYNCED


def test_pressing_retry_does_not_wait_out_the_backoff(
    make_window, lrclib, spotify, fetching
):
    """The point of the control. After four failures the next automatic
    retry is four minutes away, and somebody who knows the service is back
    should not have to sit through it — nor start the next failure at four
    minutes if they were wrong."""
    service = lrclib(("album_name=", (503, b"")))
    window = shown(make_window())
    play(window, spotify)
    for _ in range(3):
        window._view_model._error_at = -1e6  # due now, whatever the schedule
        window._tick_retry()
        settled(window)
    assert window._view_model.failures == 4
    assert window._view_model.retry_interval() == 240.0
    asked = len(service.asked)

    window._why_button.click()
    APP.processEvents()
    window._retry_button.click()
    settled(window)

    assert len(service.asked) > asked
    assert window._view_model.failures == 1, "the schedule did not go back"
    assert window._view_model.retry_interval() == 30.0


def test_a_429_stops_every_request_including_the_one_the_user_asked_for(
    make_window, lrclib, spotify, fetching
):
    """LRCLIB's documentation says ignoring Retry-After may earn a
    temporary ban, so the pause is not the user's to waive. Their press
    goes out, is refused at the provider's door before a socket is opened,
    and the reason says so rather than pretending it was asked.
    """
    service = lrclib(("album_name=", (429, b"", [("Retry-After", "600")])))
    window = shown(make_window())
    play(window, spotify)
    assert window._view_model.display().mode is Mode.ERROR
    asked = len(service.asked)

    window._why_button.click()
    APP.processEvents()
    assert window._upcoming.text() == "LRCLIB asked this app to slow down · album match"

    window._retry_button.click()
    settled(window)

    assert len(service.asked) == asked, "a request went out during the pause"
    assert window._upcoming.text() == "waiting, as LRCLIB asked"
    # And the refusal is not counted against the service: it never reached
    # it. What holds the retries off is the pause itself.
    assert window._view_model.failures == 0
    # What holds the retries off instead is the pause itself, carried back
    # as what is LEFT of it rather than what it started as: the refusal
    # happened a moment after the 429, and the window waits from now.
    assert 590.0 < window._view_model.retry_interval() <= 600.0


# -- the album, fetched before anyone asks for it --------------------------


@pytest.fixture
def warming(monkeypatch):
    """Give ``WarmTask`` its body back, and make its two waits short.

    The delay before it starts and the gap between its requests are both
    real time on real threads. That they are honoured at all is asserted in
    test_lyrics_provider.py against a recorded sleep; what is under test
    here is the wiring — the timer, the worker, the provider and the
    requests actually reaching the wire — so the waits are shortened rather
    than sat through.
    """
    monkeypatch.setattr(w.WarmTask, "run", REAL_WARM_RUN)
    monkeypatch.setattr(w, "_WARM_DELAY_MS", 1)
    monkeypatch.setattr(lp, "WARM_REQUEST_GAP_SECONDS", 0.01)


def album_search(*names, duration=214.0):
    """A search response naming tracks and carrying their lyrics, which is
    what LRCLIB's really does."""
    return (200, json.dumps([
        {
            "trackName": name,
            "artistName": SONG["artist"],
            "albumName": SONG["album"],
            "duration": duration,
            "syncedLyrics": SYNCED_LRC,
            "plainLyrics": None,
        }
        for name in names
    ]).encode())


def track(title, duration=214.0):
    return (200, json.dumps({
        "trackName": title,
        "artistName": SONG["artist"],
        "albumName": SONG["album"],
        "duration": duration,
        "syncedLyrics": SYNCED_LRC,
        "plainLyrics": None,
    }).encode())


def searched(window, seconds=5.0):
    """Pump the loop until stage one has been and gone."""
    settle(window, lambda: window._provider.album_is_searched(window._current_snapshot),
           seconds, "the album was never searched")


def warmed(window, seconds=5.0):
    """Pump the loop until the per-track stage has finished its work."""
    settle(window, lambda: window._provider.album_is_warm(window._current_snapshot),
           seconds, "the album was never warmed")


def settle(window, done, seconds, complaint):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if done():
            APP.processEvents()
            return
        time.sleep(0.005)
    raise AssertionError(complaint)


def second_track(window, spotify, title="Second Song", uri="spotify:track:9Zz"):
    """The same album, a different song. This is the whole signal the
    per-track stage waits for."""
    spotify.song = {**SONG, "uri": uri, "title": title}
    monitor_for(window).tick()
    APP.processEvents()
    settled(window)


def test_a_song_from_a_new_album_costs_one_request(
    make_window, lrclib, spotify, fetching, warming
):
    """Stage one, driven from the only place it can start: a song began,
    its own lookup landed, and some seconds later the app asks LRCLIB what
    else is on the album — once.

    Nineteen requests for a song somebody skipped past is exactly what the
    two stages exist to stop, so the assertion that nothing per-track went
    out is the point of the test rather than a detail of it.
    """
    service = lrclib(
        ("api/search", album_search("Blue Hour", "Second Song", "Third Song")),
        ("album_name=", (200, SYNCED_BODY)),
    )
    window = make_window()
    play(window, spotify)
    assert window._view_model.display().mode is Mode.SYNCED
    own = len(service.asked)

    searched(window)

    assert len(service.asked_for("api/search")) == 1
    assert len(service.asked) == own + 1, "the album cost more than one request"
    assert window._provider.album_is_warm(window._current_snapshot) is False


def test_the_search_alone_answers_a_later_track(
    make_window, lrclib, spotify, fetching, warming
):
    """What that one request buys. The second track's lyrics came back
    inside the search, so the window fills with nothing on the wire."""
    service = lrclib(
        ("api/search", album_search("Blue Hour", "Second Song")),
        ("album_name=", (200, SYNCED_BODY)),
    )
    window = make_window()
    play(window, spotify)
    searched(window)
    asked = len(service.asked)

    second = make_window()
    spotify.song = {**SONG, "uri": "spotify:track:9Zz", "title": "Second Song"}
    play(second, spotify, position=2.0)

    assert second._view_model.display().mode is Mode.SYNCED
    assert second._current.text() == "first line"
    assert len(service.asked) == asked, "a warmed track went to the network"


def test_a_second_track_from_the_album_buys_the_per_track_pass(
    make_window, lrclib, spotify, fetching, warming
):
    """The intent the expensive stage waits for. A second track is the
    difference between a song somebody heard and an album somebody is
    listening to, and it is the only signal there is."""
    service = lrclib(
        ("track_name=Third+Song", track("Third Song")),
        ("api/search", album_search("Blue Hour", "Second Song", "Third Song")),
        ("album_name=", (200, SYNCED_BODY)),
    )
    window = make_window()
    play(window, spotify)
    searched(window)
    assert service.asked_for("track_name=Third+Song") == []

    second_track(window, spotify)
    warmed(window)

    # One per name the search returned, and only now.
    assert len(service.asked_for("track_name=Third+Song")) == 1
    assert len(service.asked_for("api/search")) == 1


def test_playing_the_same_song_twice_is_not_a_second_track(
    make_window, lrclib, spotify, fetching, warming
):
    """Otherwise a song on repeat would buy the whole album."""
    service = lrclib(
        ("api/search", album_search("Blue Hour", "Second Song")),
        ("album_name=", (200, SYNCED_BODY)),
    )
    window = make_window()
    play(window, spotify)
    searched(window)
    asked = len(service.asked)

    second = make_window()  # the same song again, on the same cache
    play(second, spotify)
    for _ in range(40):
        APP.processEvents()
        time.sleep(0.005)

    assert len(service.asked) == asked
    assert window._provider.album_is_warm(window._current_snapshot) is False


def test_nothing_is_warmed_while_the_service_is_failing(
    make_window, lrclib, spotify, fetching, warming
):
    """Speculative requests are the last thing to send at a service that
    cannot answer the ones somebody is waiting for."""
    service = lrclib(("album_name=", (503, b"")))
    window = make_window()
    play(window, spotify)
    assert window._view_model.display().mode is Mode.ERROR
    failed = len(service.asked)

    for _ in range(40):  # well past the warm delay
        APP.processEvents()
        time.sleep(0.005)

    assert len(service.asked) == failed, "the app warmed during an outage"
    assert service.asked_for("api/search") == []
