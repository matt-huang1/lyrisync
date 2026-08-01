"""The window against a player that answers back.

A test that supplies its own positions cannot see the loop: every one of
these drives a fake Spotify under the real PlayerMonitor, wired to the
real window's slots, where the only things that move the position are
playback and this app's own seeks. The announcement tests are here for
the same reason — what rings the bell is the player.
"""

TIER = "qt"  # the announcement wiring; the loop tests below drive a player

import time

import pytest

from sottovoce import player_events
from sottovoce import player_monitor as pmon
from sottovoce import window as w
from sottovoce.lyrics_provider import TrackLyrics

from helpers import APP, load


# -- being told rather than asking ----------------------------------------


def test_the_window_listens_for_spotifys_own_announcement(make_window):
    """Unconditional, like the display watcher and for the same reason: it
    is not a layer with an "off", it is the app being told rather than
    guessing. With no door in the suite the subscription simply finds
    nothing to observe, which is also the case the monitor's fast rate
    exists for."""
    window = make_window()
    assert isinstance(window._announcer, player_events.PlaybackAnnouncer)
    assert window._announcer.listening is False


def test_the_monitor_is_told_whether_anything_is_listening(make_window):
    """And told the truth: with no door in the suite the observer does not
    register, so the monitor must keep asking at its old rate — which is
    the same branch a Mac without pyobjc takes."""
    window = make_window()
    assert window._announcer.listening is False
    assert w.observing() is False
    monitor = window._monitor_thread._monitor
    assert monitor.interval() == monitor.poll_interval


def test_the_announcement_only_ever_rings_the_monitors_bell(make_window):
    """It is delivered on the UI thread and must not touch the window from
    there: what it does is set a flag the monitor's own thread reads."""
    window = make_window()
    assert window._announcer._on_announcement is w.announce


def test_the_observer_is_given_back_before_anything_is_destroyed(make_window):
    """The third thing that can still call in, beside the hotkey and the
    two workspace observers."""
    window = make_window()
    stopped = []
    window._announcer.stop = lambda: stopped.append(True)
    window._shutdown()
    assert stopped == [True]


# -- the loop, driven by the monitor that actually drives it ---------------
#
# Every loop test before this one handed LineLoop a position and asked
# what it decided, and every window test here handed _on_position_update a
# snapshot it had built by hand. Both are the caller answering its own
# question, and the bug lives in the gap between them: the wrap seek is
# dispatched a seek lead BEFORE the end bound, and for the whole round
# trip that carries it the player is still where it was, so the scheduler
# arms the wrap again, and again on the next poll. Nothing that supplies
# its own positions can produce that gap. 1487 tests passed against a loop
# that seeked twice at every wrap.
#
# So this section has a player in it. The clock is fake and Spotify is
# fake; the monitor, the window, its slots, its timers and the loop are
# all the real ones, and the ONLY things that move the position are
# playback and this app's own seeks — which is exactly the situation the
# loop lives in, because a seek is measured to announce nothing at all.

LOOP_LINES = TrackLyrics(synced=[(1.0, "one"), (11.0, "two"), (21.0, "three")])
ROUND_TRIP = 0.133  # NSAppleScript, compiled once: 133ms wall, measured


class FakeClock:
    """Substituted for player_monitor's `time`, not for the process's.

    The module asks it for one thing, and containing it here keeps the
    window's own clock — the title card, the tap stamper — real.
    """

    def __init__(self, now):
        self.now = now

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeSpotify:
    """Position moves with the clock while playing, and when we seek it."""

    def __init__(self, clock, position=0.0):
        self.clock = clock
        self.commands = None      # set by the fixture; see answer()
        self.position = position
        self.stamped_at = clock.now
        self.playing = True
        self.seeks = []
        self.pauses = 0
        self.resumes = 0

    def now_position(self):
        if not self.playing:
            return self.position
        return self.position + (self.clock.now - self.stamped_at)

    def seek(self, seconds):
        self.seeks.append(seconds)
        self.position, self.stamped_at = seconds, self.clock.now

    def pause(self):
        self.position, self.stamped_at = self.now_position(), self.clock.now
        self.playing = False
        self.pauses += 1

    def resume(self):
        self.stamped_at = self.clock.now
        self.playing = True
        self.resumes += 1

    def answer(self, script):
        """The fake _ask. A COMMAND is charged the round trip it took: the
        clock goes back to when it was sent and forward to now, so what
        the app times around this call is what the harness is simulating.
        The effect lands after the clock is put back, so the player is
        moved at the moment the command returned and not at the moment it
        was sent."""
        if script != pmon._SNAPSHOT_SCRIPT and self.commands.landing_at is not None:
            # The command returns NOW. The caller set the clock back to
            # when it was sent, so player_monitor's own timing around this
            # call comes out as the round trip the harness is simulating.
            self.clock.now = self.commands.landing_at
        if script == pmon._SNAPSHOT_SCRIPT:
            return "\n".join([
                "playing" if self.playing else "paused",
                "spotify:track:t1", "Song", "Artist", "Album", "200000",
                f"{self.now_position():.3f}",
            ])
        if "set player position to" in script:
            self.seek(float(script.split("set player position to")[1]
                            .split("\n")[0]))
        elif " to pause" in script:
            self.pause()
        elif " to play" in script:
            self.resume()
        return ""


class Commands:
    """The player commands the window dispatches, and when they land.

    Recorded in __init__ rather than by patching the pool: _pool is
    QThreadPool.globalInstance() and assigning to its start() leaks into
    every test that runs afterwards. The task's run() is already a no-op
    here, so nothing reaches Spotify off this thread — the round trip is
    played out on the fake clock instead, which is the only way the window
    between dispatching a seek and it landing can be reproduced at all.
    """

    def __init__(self, clock, spotify, round_trip=ROUND_TRIP):
        self.clock, self.spotify = clock, spotify
        self.round_trip = round_trip
        self.pending = []
        # When the command now executing LANDS. player_monitor times
        # itself around _ask, so the harness puts the clock back to the
        # moment the command left and the fake _ask puts it forward again
        # to the moment it returned. Without that the app measures a round
        # trip of zero, and the loop's lead IS that round trip: it would
        # be measured against a harness that never charged for it.
        self.landing_at = None

    def dispatch(self, seek_to=None, pause=False, resume=False):
        self.pending.append((self.clock.now + self.round_trip,
                             seek_to, pause, resume))

    def land_due(self):
        due = [c for c in self.pending if c[0] <= self.clock.now]
        self.pending = [c for c in self.pending if c[0] > self.clock.now]
        for lands_at, seek_to, pause, resume in due:
            self.landing_at = lands_at
            self.clock.now = lands_at - self.round_trip   # when it was sent
            try:
                if seek_to is not None:
                    pmon.set_position(seek_to)
                if pause:
                    pmon.pause_playback()
                if resume:
                    pmon.resume_playback()
            finally:
                self.landing_at = None
                self.clock.now = lands_at


@pytest.fixture
def player(monkeypatch):
    """A real PlayerMonitor over a fake Spotify, ready to be wired to a
    window's own slots."""
    clock = FakeClock(time.monotonic())
    spotify = FakeSpotify(clock)
    commands = Commands(clock, spotify)
    spotify.commands = commands

    monkeypatch.setattr(pmon, "time", clock)
    monkeypatch.setattr(pmon, "_ask", spotify.answer)
    monkeypatch.setattr(pmon, "spotify_running", lambda: True)
    monkeypatch.setattr(pmon, "_moved", None, raising=False)
    # Module state, like _moved and _wake: the round trips one test leaves
    # behind are the lead a later one's first wrap is dispatched with, so
    # a fresh window would open already knowing how fast this machine is.
    pmon._round_trips.clear()

    class RecordingSeek(w.SeekTask):
        def __init__(self, seconds):
            super().__init__(seconds)
            commands.dispatch(seek_to=seconds)

    class RecordingCommand(w.PlayerCommandTask):
        def __init__(self, seek_to=None, pause=False, resume=False):
            super().__init__(seek_to, pause, resume)
            commands.dispatch(seek_to=seek_to, pause=pause, resume=resume)

    monkeypatch.setattr(w, "SeekTask", RecordingSeek)
    monkeypatch.setattr(w, "PlayerCommandTask", RecordingCommand)

    was = pmon.observing(True)
    yield clock, spotify, commands
    pmon.observing(was)
    pmon._wake.clear()
    pmon._moved = None
    pmon._round_trips.clear()


class FakeTimer:
    """window._loop_timer, on the same clock as everything else here.

    The window arms it for `end - position - lead` seconds, which almost
    never lands on a poll. Left as a real QTimer it can only fire when the
    window asks for 0ms, so the wrap would always be dispatched at the
    instant of a tick and the gap it opens would never contain one. That
    is not tidiness either: at a 133ms round trip it is the difference
    between this test seeing the bug and not. The window's own scheduling
    code and its own _do_loop_wrap are untouched — only the clock is ours,
    exactly as the monitor's is.
    """

    def __init__(self, clock, fire):
        self.clock, self.fire = clock, fire
        self.due = None

    def start(self, milliseconds):
        self.due = self.clock.now + milliseconds / 1000.0

    def stop(self):
        self.due = None

    def isActive(self):
        return self.due is not None

    def expire(self):
        if self.due is not None and self.due <= self.clock.now:
            self.due = None
            self.fire()


def looping_window(make_window, player, index=0, echo=False):
    """A window on LOOP_LINES with the loop engaged on ``index``, and a
    real monitor feeding it."""
    clock, spotify, _ = player
    window = make_window()
    window._echo_enabled = window._loop.echo = echo
    load(window, LOOP_LINES)
    spotify.seek(LOOP_LINES.synced[index][0] + 0.5)
    monitor = pmon.PlayerMonitor(
        on_position_update=window._on_position_update,
        on_state_change=window._on_state_change,
        on_track_change=lambda snapshot: None,
    )
    monitor.tick()
    APP.processEvents()
    assert window._toggle_loop(True) is None
    assert window._loop.engaged, "nothing to loop"
    spotify.seeks.clear()  # putting the player on the line is not a wrap
    window._loop_timer = FakeTimer(clock, window._do_loop_wrap)
    return window, monitor


def play(window, monitor, player, seconds):
    """Run the player for ``seconds``.

    Three things happen on this clock: the monitor ticks every poll
    interval, the wrap timer the window armed comes due, and a command the
    window dispatched lands a round trip after it was sent. The clock
    advances to whichever of them is next rather than in fixed steps, so
    that a poll can fall between a seek being SENT and it LANDING — which
    is the whole of this bug, and which a fixed 0.3s step at a 0.133s
    round trip never produces. Written that way, the test passed against
    the unfixed loop.

    A command landing rings the monitor's bell (every one of them calls
    disturb) and the real run loop waits on that bell rather than on the
    clock, so a tick follows a landing immediately here too.
    """
    clock, _, commands = player
    timer = window._loop_timer
    end = clock.now + seconds
    next_tick = clock.now
    while True:
        # Only what is still to come: a test that stops commands landing
        # at all leaves their due times in the past for ever, and a
        # minimum taken over those would walk the clock backwards.
        upcoming = [due for due, *_ in commands.pending if due > clock.now]
        if timer.due is not None and timer.due > clock.now:
            upcoming.append(timer.due)
        when = min([next_tick] + upcoming)
        if when > end:
            clock.now = end
            return
        clock.now = max(clock.now, when)
        commands.land_due()
        timer.expire()
        if when >= next_tick or pmon._wake.is_set():
            monitor.tick()
            next_tick = clock.now + pmon.POLL_INTERVAL_SECONDS
        APP.processEvents()


@pytest.mark.integration
def test_the_loop_seeks_once_per_wrap(make_window, player):
    """The bug, stated as the number it got wrong. A 10 second line looped
    for 45 seconds wraps four times; before the pending-wrap gate it
    seeked seven to eight times, the extra one landing a round trip after
    the first and restarting a line that had already restarted. That is
    what the wrap sounded like."""
    _, spotify, _ = player
    window, monitor = looping_window(make_window, player)

    play(window, monitor, player, 45.0)

    assert spotify.seeks == [1.0] * 4
    assert window._loop.engaged, "the loop cancelled itself"


@pytest.mark.integration
def test_the_loop_survives_a_seek_that_takes_a_whole_poll_interval(
    make_window, player, monkeypatch
):
    """The extra seeks were not only audible. Each one occupies the single
    AppleScript lock that every question and every command shares, so the
    reconciliation query that would have told the loop it had already
    wrapped queued behind them — and the player really did run past the
    end bound, and still_valid really did read it as the user seeking
    away. Measured before the gate: cancelled in 10 runs out of 10 at a
    0.7s round trip, after two or three wraps."""
    _, spotify, commands = player
    commands.round_trip = 0.7
    window, monitor = looping_window(make_window, player)

    play(window, monitor, player, 45.0)

    assert window._loop.engaged, "the loop cancelled itself"
    assert spotify.seeks == [1.0] * 4


@pytest.mark.integration
def test_a_seek_the_app_never_made_still_cancels_the_loop(make_window, player):
    """The gate must not become a way of ignoring the user. They drag
    Spotify's own scrubber into the next verse: nothing announces it, the
    reconciliation poll finds it, and the loop lets go."""
    clock, spotify, _ = player
    window, monitor = looping_window(make_window, player)

    play(window, monitor, player, 3.0)
    spotify.seek(21.5)  # the third line, by hand, with no announcement
    play(window, monitor, player, 3.0)

    assert not window._loop.engaged
    assert window._loop_button.isChecked() is False


@pytest.mark.integration
def test_a_wrap_seek_that_never_lands_still_cancels_the_loop(
    make_window, player, monkeypatch
):
    """A failed seek has always surfaced as the position drifting out of
    the bounds. It must not surface as a loop that quietly stops wrapping
    and plays the rest of the song."""
    _, spotify, commands = player
    window, monitor = looping_window(make_window, player)
    monkeypatch.setattr(commands, "land_due", lambda: None)  # every seek fails

    play(window, monitor, player, 45.0)

    assert spotify.seeks == []
    assert not window._loop.engaged


@pytest.mark.integration
def test_echo_practice_pauses_once_per_attempt(make_window, player):
    """Echo dispatches a pause rather than a seek and the ATTEMPT phase
    suppresses the scheduler from the first wrap onward, so it was never
    exposed to this — proved rather than assumed, because 'never exposed'
    was true of the plain loop's own tests too."""
    clock, spotify, _ = player
    window, monitor = looping_window(make_window, player, echo=True)

    play(window, monitor, player, 15.0)

    assert spotify.pauses == 1
    assert spotify.seeks == []
    assert window._loop.phase is w.LoopPhase.ATTEMPT
    assert spotify.playing is False

    window._on_attempt_done_clicked()  # the user's turn ends
    play(window, monitor, player, 3.0)

    assert spotify.seeks == [1.0]
    assert spotify.resumes == 1
    assert window._loop.phase is w.LoopPhase.LISTEN
    assert window._loop.engaged


@pytest.mark.integration
def test_echo_replays_the_line_again_and_again(make_window, player):
    """Listen, attempt, replay, listen: the cycle, over a real position
    stream, with the done button as the only way out of each attempt."""
    _, spotify, _ = player
    window, monitor = looping_window(make_window, player, echo=True)

    for _ in range(3):
        play(window, monitor, player, 15.0)
        assert window._loop.phase is w.LoopPhase.ATTEMPT
        window._on_attempt_done_clicked()
        play(window, monitor, player, 1.0)

    assert spotify.pauses == 3
    assert spotify.seeks == [1.0] * 3
    assert window._loop.engaged


@pytest.mark.integration
def test_the_window_is_told_where_this_apps_own_seek_put_the_player(
    make_window, player
):
    """The monitor's half. A seek announces nothing, so between
    reconciliations the position is carried forward — and it must be
    carried forward from where the seek left the player, not from where
    it was heading before it."""
    clock, spotify, commands = player
    window, monitor = looping_window(make_window, player)

    commands.dispatch(seek_to=15.0)
    clock.advance(commands.round_trip)
    commands.land_due()
    monitor.tick()          # the poll the seek's disturb() woke
    clock.advance(0.3)
    monitor.tick()          # and one carried forward from it

    assert window._last_position == pytest.approx(15.3, abs=0.01)
