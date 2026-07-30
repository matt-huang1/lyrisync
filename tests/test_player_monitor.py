import time

import pytest

from sottovoce import player_monitor as pm


def batched_output(
    state="playing",
    track_id="4uLU6hMCjMI75M1A2tKUQC",
    title="Song",
    artist="Artist",
    album="Album",
    duration="225000",
    position="42.5",
    uri=None,
):
    """What the single snapshot query answers for a loaded track."""
    url = uri if uri is not None else f"spotify:track:{track_id}"
    return "\n".join([state, url, title, artist, album, duration, position])


class FakeSpotify:
    """Stands in for _ask, the one way anything reaches Spotify.

    Answers the batched snapshot script with ``.output``, raising it if it
    is an exception. Anything else is a script this module did not mean to
    run — the player commands have a fake of their own, because what makes
    them interesting is that they are commands and not questions.
    """

    def __init__(self, output):
        self.output = output
        self.calls = 0

    def __call__(self, script):
        assert script == pm._SNAPSHOT_SCRIPT
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class FakeRunning:
    """Stands in for spotify_running, counting how often it is asked.

    It is asked before every single query now, so "how often" is a real
    question: it is the gate that keeps AppleScript away from an
    application that might not be installed.
    """

    def __init__(self, running=True):
        self.running = running
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.running


def use_output(monkeypatch, output, running=True):
    fake = FakeSpotify(output)
    monkeypatch.setattr(pm, "_ask", fake)
    fake.probe = FakeRunning(running)
    monkeypatch.setattr(pm, "spotify_running", fake.probe)
    return fake


@pytest.fixture(autouse=True)
def _no_signals_left_lying_around():
    """The doorbell, this app's own seeks, and whether anything is
    listening at all reach the monitor through module state, because the
    alternative is threading a monitor reference through every QRunnable
    that sends a command — and that is exactly how one gets added that
    forgets. Module state is state one test can leave lying around for the
    next, so it is cleared around each of them."""
    pm._wake.clear()
    pm.observing(False)
    yield
    pm._wake.clear()
    pm.observing(False)


# -- snapshot parsing ----------------------------------------------------


def test_snapshot_not_running(monkeypatch):
    use_output(monkeypatch, "not_running")
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert not snapshot.has_track


def test_snapshot_playing_full_fields(monkeypatch):
    use_output(monkeypatch, batched_output())
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert snapshot.track_id == "4uLU6hMCjMI75M1A2tKUQC"
    assert snapshot.title == "Song"
    assert snapshot.artist == "Artist"
    assert snapshot.album == "Album"
    assert snapshot.duration_ms == 225000
    assert snapshot.position_seconds == pytest.approx(42.5)


def test_snapshot_single_call_per_poll(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    pm.read_snapshot()
    assert fake.calls == 1


def test_snapshot_no_track_loaded(monkeypatch):
    # The AppleScript try block leaves only the state line when track
    # fields error (fresh launch, nothing loaded).
    use_output(monkeypatch, "stopped")
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.STOPPED
    assert not snapshot.has_track
    assert snapshot.title is None
    assert snapshot.position_seconds is None


def test_snapshot_unexpected_line_count_degrades_to_stateless(monkeypatch):
    use_output(monkeypatch, "playing\nspotify:track:abc\nTitle")
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert not snapshot.has_track


def test_snapshot_bad_number_degrades(monkeypatch):
    use_output(monkeypatch, batched_output(duration="garbage"))
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert not snapshot.has_track


def test_locale_comma_numbers(monkeypatch):
    use_output(monkeypatch, batched_output(position="42,5", duration="225000"))
    snapshot = pm.read_snapshot()
    assert snapshot.position_seconds == pytest.approx(42.5)
    assert snapshot.duration_ms == 225000


def test_empty_output_raises(monkeypatch):
    use_output(monkeypatch, "")
    with pytest.raises(pm.SpotifyQueryError):
        pm.read_snapshot()


# -- poll freshness (tap-to-sync interpolates from this) -----------------


def test_snapshot_records_when_the_query_answered(monkeypatch):
    import time

    use_output(monkeypatch, batched_output())
    before = time.monotonic()
    snapshot = pm.read_snapshot()
    after = time.monotonic()
    assert before <= snapshot.polled_at <= after


def test_poll_time_is_stamped_after_the_query_not_before(monkeypatch):
    """The position is only as fresh as the moment the answer came back.
    Stamping before a slow query would claim it was newer than it is, and
    every interpolated tap and every carried-forward position would land
    early."""
    monkeypatch.setattr(pm, "spotify_running", FakeRunning(running=True))
    ticks = iter([100.0, 101.0, 102.0])
    monkeypatch.setattr(pm.time, "monotonic", lambda: next(ticks))

    def slow(script):
        next(ticks)  # a second of wall-clock burned inside the query
        return batched_output()

    monkeypatch.setattr(pm, "_ask", slow)
    assert pm.read_snapshot().polled_at == 101.0  # not 100.0


@pytest.mark.parametrize(
    "output",
    ["not_running", "playing", batched_output(position="nonsense")],
)
def test_every_snapshot_shape_carries_a_poll_time(monkeypatch, output):
    use_output(monkeypatch, output)
    assert pm.read_snapshot().polled_at is not None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("spotify:track:abc123", "abc123"),
        ("https://open.spotify.com/track/abc123?si=xyz", "abc123"),
        ("https://open.spotify.com/track/abc123", "abc123"),
        ("", None),
    ],
)
def test_parse_track_id(url, expected):
    assert pm._parse_track_id(url) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("playing", pm.PlaybackState.PLAYING),
        ("Paused", pm.PlaybackState.PAUSED),
        ("«constant ****kPSp»", pm.PlaybackState.PAUSED),
        ("«constant ****kPSP»", pm.PlaybackState.PLAYING),
        ("«constant ****kPSS»", pm.PlaybackState.STOPPED),
    ],
)
def test_parse_state(raw, expected):
    assert pm._parse_state(raw) is expected


def test_parse_state_unrecognized():
    with pytest.raises(pm.SpotifyQueryError):
        pm._parse_state("garbage")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("spotify:track:abc123", "track"),
        ("spotify:media:abc123", "media"),
        ("spotify:ad:abc123", "ad"),
        ("https://open.spotify.com/track/abc123?si=x", "track"),
        ("", "track"),
    ],
)
def test_parse_track_kind(url, expected):
    assert pm._parse_track_kind(url) == expected


def test_dj_narration_snapshot_is_not_music(monkeypatch):
    # Spotify's DJ reports the upcoming song's ID under spotify:media:.
    use_output(
        monkeypatch,
        batched_output(
            uri="spotify:media:61uyGDPJ06MkxJtHgPmuyO",
            title="Up next", artist="DJ X", album="DJ", duration="0", position="1.0",
        ),
    )
    snapshot = pm.read_snapshot()
    assert snapshot.track_id == "61uyGDPJ06MkxJtHgPmuyO"
    assert snapshot.track_kind == "media"
    assert snapshot.has_track
    assert not snapshot.is_music_track


# -- monitor callbacks ---------------------------------------------------


class Recorder:
    def __init__(self):
        self.events = []

    def hook(self, name):
        return lambda snapshot: self.events.append((name, snapshot))

    def names(self):
        return [name for name, _ in self.events]


def make_monitor(recorder):
    return pm.PlayerMonitor(
        on_state_change=recorder.hook("state"),
        on_track_change=recorder.hook("track"),
        on_position_update=recorder.hook("position"),
    )


def test_first_poll_fires_initial_events(monkeypatch):
    use_output(monkeypatch, batched_output())
    recorder = Recorder()
    make_monitor(recorder).poll_once()
    assert recorder.names() == ["state", "track", "position"]


def test_steady_state_only_fires_position(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(position="43.1")
    monitor.poll_once()
    assert recorder.names() == ["position"]


def test_track_change_fires_callback(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(track_id="other999", title="Next Song")
    monitor.poll_once()
    assert recorder.names() == ["track", "position"]
    assert recorder.events[0][1].track_id == "other999"


def test_dj_to_song_transition_fires_track_change(monkeypatch):
    # Same ID, different URI scheme: must register as a track change,
    # or the app stays stuck on the DJ state for the whole song.
    fake = use_output(
        monkeypatch,
        batched_output(
            uri="spotify:media:61uyGDPJ06MkxJtHgPmuyO",
            title="Up next", artist="DJ X", album="DJ", duration="0",
        ),
    )
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(
        uri="spotify:track:61uyGDPJ06MkxJtHgPmuyO",
        title="Company", artist="Justin Bieber", album="Purpose (Deluxe)",
        duration="198195",
    )
    monitor.poll_once()
    assert "track" in recorder.names()
    changed = dict(recorder.events)["track"]
    assert changed.title == "Company"
    assert changed.is_music_track


def test_pause_fires_state_change(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(state="paused")
    monitor.poll_once()
    assert recorder.names() == ["state", "position"]
    assert recorder.events[0][1].state is pm.PlaybackState.PAUSED


def test_quit_spotify_fires_state_then_track_change(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "not_running"
    # First trackless poll is debounced (could be a one-poll blip): only
    # the state change fires, track metadata is retained.
    snapshot = monitor.poll_once()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert snapshot.has_track
    assert recorder.names() == ["state"]

    # Second consecutive trackless poll confirms the loss.
    snapshot = monitor.poll_once()
    assert not snapshot.has_track
    assert recorder.names() == ["state", "track"]


def test_single_trackless_blip_is_debounced(monkeypatch):
    # Mid item-switch AppleScript can report no track for one poll; that
    # must not fire a track change to nothing and back.
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "playing"  # state-only: no track fields this poll
    monitor.poll_once()
    fake.output = batched_output()  # track is back, unchanged
    monitor.poll_once()
    assert "track" not in recorder.names()


def test_state_change_during_blip_still_fires(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "paused"  # trackless blip AND a state change
    snapshot = monitor.poll_once()
    assert recorder.names() == ["state"]
    assert snapshot.state is pm.PlaybackState.PAUSED
    assert snapshot.has_track  # metadata retained through the blip


def test_blip_then_new_track_fires_change(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "playing"
    monitor.poll_once()
    fake.output = batched_output(track_id="next999", title="Next")
    monitor.poll_once()
    assert "track" in recorder.names()
    assert dict(recorder.events)["track"].track_id == "next999"


def test_transient_query_failure_keeps_state(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = pm.SpotifyQueryError("osascript timed out")
    assert monitor.poll_once() is None
    assert recorder.names() == []

    # Recovery: same track again, only position fires (no spurious changes).
    fake.output = batched_output()
    monitor.poll_once()
    assert recorder.names() == ["position"]


# -- starting and stopping the poll loop ---------------------------------


def test_a_stop_before_run_is_not_erased(monkeypatch):
    """The race that made shutdown flaky.

    ``stop()`` used to clear a flag that ``run()`` raised again on entry,
    so a stop arriving in the gap between starting the thread and the
    thread body beginning was lost and the loop polled on forever. In the
    app that is "monitor thread did not stop in time", and one bounded
    wait later, a QThread destroyed while still running — a qFatal that
    takes the process with it.
    """
    fake = use_output(monkeypatch, batched_output())
    monitor = pm.PlayerMonitor(poll_interval=0.01)

    monitor.stop()
    monitor.run()  # must return, not poll

    assert fake.calls == 0


def test_run_stops_when_asked(monkeypatch):
    """And stops promptly: the remaining poll interval is waited on rather
    than slept through, so quit does not first sit out a poll."""
    use_output(monkeypatch, batched_output())
    monitor = pm.PlayerMonitor(poll_interval=30.0)

    def stop_after_first_poll(snapshot):
        monitor.stop()

    monitor.on_position_update = stop_after_first_poll

    started = time.monotonic()
    monitor.run()
    assert time.monotonic() - started < 5.0  # not the 30s interval


# -- album artwork --------------------------------------------------------


def test_the_artwork_url_comes_back_with_the_track(monkeypatch):
    use_output(monkeypatch, batched_output() + "\nhttps://i.scdn.co/image/abc")
    snapshot = pm.read_snapshot()
    assert snapshot.artwork_url == "https://i.scdn.co/image/abc"
    assert snapshot.title == "Song"  # the rest still parsed


def test_a_track_without_artwork_still_parses(monkeypatch):
    """The artwork line sits in a try of its own inside the script, so a
    Spotify build that will not answer `artwork url` costs one field
    rather than the whole track."""
    use_output(monkeypatch, batched_output())
    snapshot = pm.read_snapshot()
    assert snapshot.artwork_url is None
    assert snapshot.title == "Song"
    assert snapshot.track_id == "4uLU6hMCjMI75M1A2tKUQC"
    assert snapshot.position_seconds == 42.5


def test_an_empty_artwork_line_is_no_artwork(monkeypatch):
    use_output(monkeypatch, batched_output() + "\n   ")
    assert pm.read_snapshot().artwork_url is None


def test_the_script_keeps_artwork_in_a_try_of_its_own():
    """Structural, because the failure it prevents is invisible in a
    passing suite: appended to the same statement, a build that does not
    answer `artwork url` would fail the whole expression and the app would
    show a running player that never finds a song."""
    body = pm._SNAPSHOT_SCRIPT

    def open_trys(text):
        # "end try" contains "try", so opens are the difference.
        return text.count("try") - 2 * text.count("end try")

    assert open_trys(body) == 0, "the script's trys are unbalanced"
    before_artwork = body[: body.index("artwork url")]
    assert open_trys(before_artwork) == 2, "artwork is not nested inside both trys"


def test_artwork_is_not_part_of_track_identity():
    """A cover arriving a poll after the metadata must not read as a
    different song and restart the lyrics lookup."""
    without = pm.PlayerSnapshot(state=pm.PlaybackState.PLAYING, track_id="t1")
    with_art = pm.PlayerSnapshot(
        state=pm.PlaybackState.PLAYING, track_id="t1", artwork_url="http://cover"
    )
    assert without.track_key == with_art.track_key


# -- a Mac with no Spotify on it -------------------------------------------
#
# Never tested for thirteen milestones, and it turned out not to work the
# way the script implies. `if application "Spotify" is not running then
# return "not_running"` is the snapshot script's first line and reads like
# the answer, but everything below it is Spotify's OWN terminology, and
# AppleScript resolves terminology at COMPILE time out of the application
# bundle. With no bundle there is nothing to resolve, so the script never
# runs at all.
#
# What that costs depends entirely on WHERE it is compiled, which is the
# thing this milestone changed:
#
# - inside a fresh `osascript` process it failed with a syntax error in
#   182ms, silently, three times a second, forever:
#     141:146: syntax error: Expected “,” but found identifier. (-2741)
# - inside THIS process it does not fail. macOS puts up its "Where is
#   Spotify?" chooser, in front of the user, and blocks the thread that
#   asked until somebody dismisses it. Measured: still blocked after five
#   minutes, with a file panel on screen owned by the app.
#
# And the dictionary-free probe that used to tell the two cases apart does
# exactly the same thing, because what cannot be resolved is the
# application and not its vocabulary. So there is no AppleScript that is
# safe to ask here, and the answer is not to ask one: whether Spotify is
# running is now an AppKit question, and it is asked BEFORE anything is
# compiled or sent rather than afterwards.


def test_no_spotify_reports_not_running(monkeypatch):
    """The whole point. "Not running" is the truth on a Mac with no
    Spotify, and it is what the monitor is supposed to report."""
    use_output(monkeypatch, batched_output(), running=False)
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert not snapshot.has_track
    assert snapshot.polled_at is not None


def test_nothing_is_asked_of_a_spotify_that_is_not_there(monkeypatch):
    """THE rule, and the one that stops a modal chooser appearing on a
    machine that never had Spotify: not one query is compiled or sent
    while it is not running, however many times the monitor ticks."""
    fake = use_output(monkeypatch, batched_output(), running=False)
    for _ in range(5):
        assert pm.read_snapshot().state is pm.PlaybackState.NOT_RUNNING
    assert fake.calls == 0, "AppleScript was sent to an application that is not there"
    assert fake.probe.calls == 5


def test_the_gate_is_inside_the_one_way_out(monkeypatch):
    """Every question and every command goes through `_ask`, and the gate
    is in there rather than in each caller: a command added later cannot
    forget it, and forgetting it is a dialogue on somebody's screen."""
    monkeypatch.setattr(pm, "spotify_running", FakeRunning(running=False))
    door = []
    monkeypatch.setattr(pm, "_cocoa", lambda: (door.append(1), (object(), object()))[1])
    for command in (
        lambda: pm.set_position(12.0),
        pm.pause_playback,
        pm.resume_playback,
    ):
        with pytest.raises(pm.SpotifyQueryError, match="not running"):
            command()


def test_the_window_sits_in_its_idle_state(monkeypatch):
    """What the display does with that, end to end through the pure view
    model: the idle line, no header, no fetch, no error."""
    from sottovoce.view_model import LyricsViewModel, Mode

    use_output(monkeypatch, batched_output(), running=False)
    model = LyricsViewModel()
    snapshot = pm.read_snapshot()
    assert model.track_changed(snapshot) is False  # nothing to look up
    model.player_state_changed(snapshot.state)
    display = model.display()
    assert display.mode is Mode.IDLE
    assert display.current == "Spotify is not playing"
    assert display.header == ""
    assert display.detail == ""


def test_the_monitor_fires_the_state_callback(monkeypatch):
    """It used to fire nothing at all: poll_once returned None before any
    callback, so a Mac with no Spotify was indistinguishable from a Mac
    where the app had not started polling yet."""
    use_output(monkeypatch, batched_output(), running=False)
    states, tracks = [], []
    monitor = pm.PlayerMonitor(
        on_state_change=lambda s: states.append(s.state),
        on_track_change=lambda s: tracks.append(s.track_key),
    )
    assert monitor.poll_once() is not None
    assert states == [pm.PlaybackState.NOT_RUNNING]
    assert tracks == [None]


def test_spotify_arriving_later_is_noticed(monkeypatch):
    """The gate is asked every time, so it is also what watches for
    Spotify starting. This never needs a restart."""
    fake = use_output(monkeypatch, batched_output(), running=False)
    assert pm.read_snapshot().state is pm.PlaybackState.NOT_RUNNING

    fake.probe.running = True
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert snapshot.title == "Song"


def test_a_transient_failure_on_a_running_spotify_is_still_a_failure(monkeypatch):
    """A query that failed while Spotify is right there is not "Spotify is
    not installed" — it is the transient failure the loop has always kept
    state across, and it is still raised."""
    fake = use_output(monkeypatch, pm.SpotifyQueryError("timed out"), running=True)
    with pytest.raises(pm.SpotifyQueryError):
        pm.read_snapshot()
    # And nothing was remembered: the next query asks properly again.
    fake.output = batched_output()
    assert pm.read_snapshot().state is pm.PlaybackState.PLAYING


def test_the_running_question_needs_no_applescript():
    """The property the whole fix rests on, asserted structurally: the
    gate may not be a script, because a script is the thing it exists to
    keep away from an application that might not be there."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pm.spotify_running))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_ask" not in called
    source = inspect.getsource(pm.spotify_running)
    for spotify_term in ("player state", "current track", "spotify url"):
        assert spotify_term not in source


def test_every_query_is_bounded_in_the_script(monkeypatch):
    """subprocess.run's timeout used to bound a query. NSAppleScript sends
    with the Apple Event Manager's default, which is about a minute, and a
    minute is one wedged Spotify away from a monitor thread outliving
    shutdown's three-second wait — which is a QThread destroyed while
    running, which aborts the process."""
    bound = f"with timeout of {pm._QUERY_TIMEOUT_SECONDS} seconds"
    assert bound in pm._SNAPSHOT_SCRIPT
    assert bound in pm._command("pause")
    assert pm._QUERY_TIMEOUT_SECONDS * 1000 < 3000  # shutdown's own wait


def test_the_commands_are_the_same_shape_as_the_snapshot(monkeypatch):
    """One place writes the timeout, so a command cannot be added without
    it."""
    sent = []
    monkeypatch.setattr(pm, "_ask", sent.append)
    pm.set_position(61.5)
    pm.pause_playback()
    pm.resume_playback()
    assert [s.splitlines()[1].strip() for s in sent] == [
        'tell application "Spotify" to set player position to 61.500',
        'tell application "Spotify" to pause',
        'tell application "Spotify" to play',
    ]
    assert all(s.startswith("with timeout of") for s in sent)


# -- being told instead of asking ------------------------------------------
#
# For thirteen milestones the loop discovered everything by asking, three
# times a second, forever. Spotify announces most of it (player_events.py
# lists what, each case driven and timed), so the loop's own job shrank to
# the one thing that is never announced — a seek — and to carrying the
# position forward in between.
#
# What must NOT shrink is any guarantee the window depends on, so the
# section after this one re-proves each of them through `tick`, which is
# what actually drives the monitor now.


def ticks(monitor, n):
    """N turns of the loop, without the loop."""
    for _ in range(n):
        monitor.tick()


class Clock:
    """A monotonic clock a test can move by hand.

    The whole point of carrying a position forward is that it is arithmetic
    on this clock, so a test that used the real one would be measuring how
    long it took to run.
    """

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(pm.time, "monotonic", fake)
    return fake


def announced_monitor(recorder, **kwargs):
    """A monitor with a doorbell to rely on, which is a monitor built
    while something is listening for Spotify's announcements."""
    pm.observing(True)
    return pm.PlayerMonitor(**{**dict(reconcile_interval=1.0), **kwargs},
                            on_track_change=recorder.hook("track"),
                            on_position_update=recorder.hook("position"),
                            on_state_change=recorder.hook("state"))


def test_the_position_is_carried_forward_rather_than_asked_for(monkeypatch, clock):
    """The saving, stated as behaviour: the window still hears a position
    at its own rate, and Spotify is asked a fraction as often."""
    fake = use_output(monkeypatch, batched_output(position="10.0"))
    recorder = Recorder()
    monitor = announced_monitor(recorder)

    monitor.tick()  # the first one has to ask
    assert fake.calls == 1
    for _ in range(2):
        clock.advance(0.3)
        monitor.tick()
    assert fake.calls == 1, "asked Spotify for a position it could work out"
    assert recorder.names() == ["state", "track", "position",
                                "position", "position"]
    positions = [s.position_seconds for name, s in recorder.events
                 if name == "position"]
    assert positions == pytest.approx([10.0, 10.3, 10.6])


def test_the_answer_is_never_older_than_the_interval(monkeypatch, clock):
    """The interval is a ceiling on staleness, so the query happens on the
    last tick before it would be breached rather than on the first tick
    after."""
    fake = use_output(monkeypatch, batched_output(position="10.0"))
    monitor = announced_monitor(Recorder())  # 1.0s ceiling, 0.3s ticks
    monitor.tick()
    ages = []
    for _ in range(9):
        clock.advance(0.3)
        before = fake.calls
        monitor.tick()
        if fake.calls == before:
            ages.append(clock.now - monitor._asked_at)
    assert max(ages) <= 1.0, "let an answer go stale past the ceiling"
    assert fake.calls > 1, "never went back to Spotify at all"


def test_carrying_forward_is_arithmetic_on_the_clock_and_nothing_else(
    monkeypatch, clock
):
    """Measured against Spotify's own answer every five seconds for 92
    seconds of one track: the largest disagreement was 1.4ms and there was
    no trend. So this is exact, and the reconciliation poll exists for
    seeks rather than for drift."""
    use_output(monkeypatch, batched_output(position="10.0"))
    monitor = announced_monitor(Recorder(), reconcile_interval=1_000_000)
    monitor.tick()
    clock.advance(47.5)
    assert monitor.tick().position_seconds == pytest.approx(57.5)


def test_a_paused_player_does_not_advance_but_its_stamp_does(monkeypatch, clock):
    """The stamp is what anything interpolating from a snapshot
    extrapolates from — the tap-to-sync stamper does — so a stale one
    while paused would put every tap a minute into the future."""
    use_output(monkeypatch, batched_output(state="paused", position="10.0"))
    monitor = announced_monitor(Recorder(), reconcile_interval=1_000_000)
    first = monitor.tick()
    clock.advance(30.0)
    later = monitor.tick()
    assert later.position_seconds == 10.0
    assert later.polled_at == first.polled_at + 30.0


def test_a_position_past_the_end_of_the_track_is_asked_about(monkeypatch, clock):
    """A song that has finished: whatever is playing now is not what the
    arithmetic says, and the announcement for it is either already on its
    way or never coming. Either way the answer is to go and ask."""
    fake = use_output(monkeypatch, batched_output(position="220.0"))  # of 225s
    monitor = announced_monitor(Recorder(), reconcile_interval=1_000_000)
    monitor.tick()
    assert fake.calls == 1
    clock.advance(6.0)
    monitor.tick()
    assert fake.calls == 2


def test_an_announcement_is_acted_on_within_a_tick(monkeypatch, clock):
    fake = use_output(monkeypatch, batched_output())
    monitor = announced_monitor(Recorder())
    monitor.tick()
    clock.advance(0.1)
    monitor.tick()
    assert fake.calls == 1
    pm.announce()
    monitor.tick()
    assert fake.calls == 2


def test_this_apps_own_seek_is_never_waited_for(monkeypatch, clock):
    """The loop's wrap, tap-to-sync and echo practice all move the
    position, several times a song. A seek this app made is the one seek
    it does not have to discover."""
    fake = use_output(monkeypatch, batched_output())
    monitor = announced_monitor(Recorder())
    monitor.tick()
    clock.advance(0.1)
    monitor.tick()
    assert fake.calls == 1

    monkeypatch.setattr(pm, "_ask", lambda script: "")  # the command itself
    pm.set_position(61.0)
    monkeypatch.setattr(pm, "_ask", fake)
    monitor.tick()
    assert fake.calls == 2


@pytest.mark.parametrize("command", ["set_position", "pause_playback",
                                     "resume_playback"])
def test_every_command_says_so(monkeypatch, command):
    """In a finally, because a command that failed is exactly as much of a
    reason to go and look as one that worked: the failure might have been
    the reply rather than the seek."""
    monkeypatch.setattr(pm, "_ask", lambda script: (_ for _ in ()).throw(
        pm.SpotifyQueryError("nope")))
    pm._wake.clear()
    with pytest.raises(pm.SpotifyQueryError):
        getattr(pm, command)(0.0) if command == "set_position" else getattr(
            pm, command)()
    assert pm._wake.is_set()


# -- the slower rate is earned, and can be lost ----------------------------


def test_with_nothing_listening_it_asks_at_its_old_rate(monkeypatch, clock):
    """A Mac where the observer would not install behaves exactly as this
    app did before the announcement existed. There is no version to sniff
    for and nothing to configure."""
    fake = use_output(monkeypatch, batched_output())
    pm.observing(False)
    monitor = pm.PlayerMonitor(poll_interval=0.3, reconcile_interval=1.0)
    assert monitor.interval() == 0.3
    monitor.tick()
    clock.advance(0.3)
    monitor.tick()
    assert fake.calls == 2


def test_a_doorbell_to_rely_on_is_enough_and_it_does_not_have_to_ring_first(
    monkeypatch, clock
):
    """Announcements only arrive when something CHANGES, so a monitor that
    waited for one before slowing down would ask three times a second
    through a whole song that nobody interrupted — most of the saving
    thrown away for a case that corrects itself in one tick anyway."""
    use_output(monkeypatch, batched_output())
    pm.observing(True)
    assert pm.PlayerMonitor(poll_interval=0.3, reconcile_interval=1.0).interval() == 1.0


def test_a_change_that_arrives_unannounced_loses_it(monkeypatch, clock):
    """The doorbell caught missing something. Not a seek — a seek is never
    announced and is the whole reason the loop still asks — but a track or
    a state change, which every observed Spotify announces."""
    fake = use_output(monkeypatch, batched_output())
    monitor = announced_monitor(Recorder())
    monitor.tick()
    assert monitor.interval() == 1.0

    fake.output = batched_output(state="paused")  # nobody rang for this
    clock.advance(0.8)
    monitor.tick()
    assert monitor.interval() == 0.3


def test_the_next_properly_announced_change_gives_it_back(monkeypatch, clock):
    """One missed announcement can be a race rather than a Spotify that
    does not announce, so the demotion is not permanent — it is undone by
    the doorbell working."""
    fake = use_output(monkeypatch, batched_output())
    monitor = announced_monitor(Recorder())
    monitor.tick()
    fake.output = batched_output(state="paused")
    clock.advance(0.8)
    monitor.tick()
    assert monitor.interval() == 0.3

    pm.announce()
    fake.output = batched_output(state="playing")
    monitor.tick()
    assert monitor.interval() == 1.0


def test_a_ring_that_lands_mid_query_still_counts(monkeypatch, clock):
    """A track change and its announcement race the same 133ms round trip.
    Counted before the query, the change would read as one nobody
    announced and the doorbell would be blamed for arriving on time."""
    fake = use_output(monkeypatch, batched_output())
    monitor = announced_monitor(Recorder())
    monitor.tick()

    def rings_while_answering(script):
        pm.announce()
        return batched_output(track_id="next999")

    monkeypatch.setattr(pm, "_ask", rings_while_answering)
    clock.advance(0.8)
    monitor.tick()
    assert monitor.interval() == 1.0


def test_a_seek_does_not_lose_it(monkeypatch, clock):
    """A position that jumped is exactly what the loop is still asking
    for, so finding one is the doorbell working as measured rather than
    failing."""
    fake = use_output(monkeypatch, batched_output(position="10.0"))
    monitor = announced_monitor(Recorder())
    monitor.tick()
    fake.output = batched_output(position="180.0")
    clock.advance(0.8)
    monitor.tick()
    assert monitor.interval() == 1.0


def test_the_first_answer_of_all_cannot_have_missed_anything(monkeypatch, clock):
    """It fires a state change and a track change to report the initial
    situation, and there was no previous answer for anything to have been
    missed against."""
    use_output(monkeypatch, batched_output())
    monitor = announced_monitor(Recorder())
    monitor.tick()
    assert monitor.interval() == 1.0


# -- every guarantee, re-proved against what drives the monitor now --------
#
# `tick` is the loop body. Each of these was proved against `poll_once`
# above, when poll_once WAS the loop body; a guarantee that holds only for
# the entry point nothing calls is not a guarantee.


def tick_monitor(recorder):
    return announced_monitor(recorder)


def test_ticking_debounces_a_single_trackless_blip(monkeypatch, clock):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = tick_monitor(recorder)
    monitor.tick()
    recorder.events.clear()

    fake.output = "playing"  # state-only: no track fields this answer
    pm.announce()
    monitor.tick()
    fake.output = batched_output()
    pm.announce()
    monitor.tick()
    assert "track" not in recorder.names()


def test_ticking_reports_a_real_track_loss(monkeypatch, clock):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = tick_monitor(recorder)
    monitor.tick()
    recorder.events.clear()

    fake.output = "not_running"
    pm.announce()
    monitor.tick()
    assert recorder.names() == ["state"]
    pm.announce()
    monitor.tick()
    assert recorder.names() == ["state", "track"]


def test_ticking_keeps_the_uri_kind_in_track_identity(monkeypatch, clock):
    """Same ID, different URI scheme: DJ narration turning into the song
    it announced. Identity that ignored the kind would call that the same
    track and never look the song up."""
    fake = use_output(
        monkeypatch,
        batched_output(uri="spotify:media:61uyGDPJ06MkxJtHgPmuyO", duration="0"),
    )
    recorder = Recorder()
    monitor = tick_monitor(recorder)
    monitor.tick()
    recorder.events.clear()

    fake.output = batched_output(uri="spotify:track:61uyGDPJ06MkxJtHgPmuyO")
    pm.announce()
    monitor.tick()
    assert "track" in recorder.names()
    assert dict(recorder.events)["track"].is_music_track


def test_ticking_keeps_state_across_a_transient_failure(monkeypatch, clock):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = tick_monitor(recorder)
    monitor.tick()
    recorder.events.clear()

    fake.output = pm.SpotifyQueryError("timed out")
    pm.announce()
    assert monitor.tick() is None
    assert recorder.names() == []

    fake.output = batched_output()
    pm.announce()
    monitor.tick()
    assert recorder.names() == ["position"]


def test_ticking_reports_a_mac_with_no_spotify(monkeypatch, clock):
    use_output(monkeypatch, batched_output(), running=False)
    recorder = Recorder()
    monitor = tick_monitor(recorder)
    assert monitor.tick() is not None
    assert recorder.names() == ["state", "track"]
    assert recorder.events[0][1].state is pm.PlaybackState.NOT_RUNNING


def test_a_carried_forward_answer_never_invents_a_change(monkeypatch, clock):
    """It fires a position and nothing else, whatever else is true of the
    snapshot it came from: state and track changes are things Spotify
    said, and nothing here has heard from Spotify."""
    use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = tick_monitor(recorder)
    monitor.tick()
    recorder.events.clear()
    for _ in range(3):
        clock.advance(0.3)
        monitor.tick()
    assert set(recorder.names()) == {"position"}


def test_a_stop_before_run_is_not_erased_however_it_is_woken(monkeypatch):
    """The race that made shutdown flaky, re-proved now that the loop
    waits on the wake signal rather than on the stop. `_stop` is still the
    only thing the loop's condition reads, so a stop can no more be lost
    now than it could before."""
    fake = use_output(monkeypatch, batched_output())
    monitor = pm.PlayerMonitor(poll_interval=0.01)
    monitor.stop()
    pm.announce()  # a doorbell arriving in the same gap
    monitor.run()
    assert fake.calls == 0


def test_run_stops_promptly_when_asked(monkeypatch):
    use_output(monkeypatch, batched_output())
    monitor = pm.PlayerMonitor(poll_interval=30.0)
    monitor.on_position_update = lambda snapshot: monitor.stop()
    started = time.monotonic()
    monitor.run()
    assert time.monotonic() - started < 5.0


# -- one door -------------------------------------------------------------

import ast as _ast  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

TREE = _ast.parse(_Path(pm.__file__).read_text(encoding="utf-8"))


def test_the_native_imports_live_inside_the_door():
    """NSAppleScript and NSRunningApplication are imported in exactly one
    place, and it is the function the suite shuts. A second import site
    would pass every behavioural test here while quietly reopening the
    door — the same claim notifications.py makes about Quartz, for the
    same reason, and it matters more here: what is behind this door can
    pause the developer's music."""
    native = ("NSAppleScript", "NSRunningApplication")

    def import_sites(tree):
        return sorted(
            alias.name
            for node in _ast.walk(tree)
            if isinstance(node, (_ast.Import, _ast.ImportFrom))
            for alias in node.names
            if alias.name in native
        )

    inside = next(
        node
        for node in _ast.walk(TREE)
        if isinstance(node, _ast.FunctionDef) and node.name == "_cocoa"
    )
    assert import_sites(inside) == sorted(native)
    assert import_sites(TREE) == sorted(native)


def test_only_the_two_it_is_for_walk_through_it():
    """Sending a script, and deciding whether one may be sent at all."""
    callers = {
        node.name
        for node in _ast.walk(TREE)
        if isinstance(node, _ast.FunctionDef)
        and any(
            isinstance(inner, _ast.Call)
            and isinstance(inner.func, _ast.Name)
            and inner.func.id == "_cocoa"
            for inner in _ast.walk(node)
        )
    }
    assert callers == {"_ask", "spotify_running"}


def test_the_script_is_compiled_once_and_kept(monkeypatch):
    """19ms of the 24ms an uncompiled execution costs is the compile, and
    the two scripts here are constants."""
    monkeypatch.setattr(pm, "spotify_running", FakeRunning(running=True))
    compiles = []

    class FakeCompiled:
        def executeAndReturnError_(self, _):
            return (FakeResult(), None)

    class FakeResult:
        def stringValue(self):
            return "running"

    class FakeScript:
        @staticmethod
        def alloc():
            return FakeScript()

        def initWithSource_(self, source):
            compiles.append(source)
            return self

        def compileAndReturnError_(self, _):
            return (True, None)

        def executeAndReturnError_(self, _):
            return (FakeResult(), None)

    monkeypatch.setattr(pm, "_cocoa", lambda: (FakeScript, object()))
    monkeypatch.setattr(pm, "_compiled", {})
    for _ in range(5):
        pm._ask(pm._SNAPSHOT_SCRIPT)
    assert len(compiles) == 1


def test_one_execution_at_a_time(monkeypatch):
    """Not defensive: measured, three threads executing one compiled
    script concurrently took 6.8s per execution against 0.13s serialised,
    with no errors and no wrong answers. The monitor's thread and the
    worker pool's seek/pause/resume are the two callers, and they do
    collide."""
    import threading

    monkeypatch.setattr(pm, "spotify_running", FakeRunning(running=True))
    inside = []
    overlaps = []

    class FakeResult:
        def stringValue(self):
            return "running"

    class FakeScript:
        @staticmethod
        def alloc():
            return FakeScript()

        def initWithSource_(self, source):
            return self

        def compileAndReturnError_(self, _):
            return (True, None)

        def executeAndReturnError_(self, _):
            inside.append(1)
            if len(inside) > 1:
                overlaps.append(1)
            time.sleep(0.01)
            inside.pop()
            return (FakeResult(), None)

    monkeypatch.setattr(pm, "_cocoa", lambda: (FakeScript, object()))
    monkeypatch.setattr(pm, "_compiled", {})
    threads = [
        threading.Thread(target=lambda: pm._ask(pm._SNAPSHOT_SCRIPT))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not overlaps
