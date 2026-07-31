"""Ask the Spotify desktop app what it is doing, and emit playback events.

This module knows nothing about lyrics or the UI. It exposes:

- ``PlayerSnapshot`` / ``PlaybackState``: what Spotify is doing right now
- ``read_snapshot()``: one AppleScript query
- ``PlayerMonitor``: runs a loop and fires callbacks on changes

All fields are fetched in a single AppleScript expression that returns
newline-separated values, so every query is one round trip and the fields
are read atomically — a track change can't produce a snapshot mixing old
and new metadata.

## The query is sent from THIS process, not by launching osascript

For thirteen milestones every query was ``subprocess.run(["osascript",
...])``, three times a second, forever. Measured on an M4, against the
identical script sent through ``NSAppleScript`` in-process:

| | CPU per query | wall |
|---|---|---|
| ``osascript`` subprocess | 58.8 ms | 200 ms |
| ``NSAppleScript``, compiled once | 5.5 ms | 133 ms |
| ``NSAppleScript``, compiled each time | 24.3 ms | 133 ms |

Almost none of that 58.8ms was the question. It was fork, exec,
LaunchServices, TCC, and the AppleScript framework being loaded and thrown
away, 3.3 times a second — and it did not land only on us: the daemons
that carry a process launch (``launchservicesd``, ``tccd``,
``runningboardd``, ``loginwindow``) woke on every poll and were idle
without one.

The script is compiled ONCE and kept, which is the difference between
5.5ms and 24.3ms, and every execution is serialised behind one lock. That
lock is not defensive: measured, three threads executing the same compiled
script concurrently took 6.8s per execution against 0.13s serialised, with
no errors and no wrong answers — fifty times slower for the privilege of
being unserialised.

## And it is asked far less often than it used to be

``player_events.py`` observes Spotify's own announcement that something
changed, so the loop no longer has to discover a track change by asking.
Between queries the position is interpolated from the monotonic clock,
which is exact rather than approximate: measured against Spotify's own
answer every five seconds for 92 seconds, the largest disagreement was
1.4ms.

What is left for the loop to catch is a SEEK, which is measured to produce
no announcement at all. See ``RECONCILE_SECONDS``.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Spotify's bundle identifier, which is how this module asks whether it is
# running. Not localised, not a path, and the same kind of identity the
# notification yield and the position layer key apps on.
SPOTIFY_BUNDLE_ID = "com.spotify.client"

# One call, newline-separated output: either "not_running", or the player
# state alone (no track loaded — the try block leaves output untouched when
# any track field errors), or state followed by the six track fields, or
# those plus the artwork URL.
#
# The artwork gets a try of its own INSIDE the first one, deliberately.
# Appended to the same statement, a Spotify build that does not answer
# `artwork url` would fail the whole expression and take the six fields
# with it — the app would show a running player and never find a song,
# for the sake of a colour. Nested, the cost of that is one missing line.
#
# ## Why nothing here may be asked of a Mac with no Spotify on it
#
# Everything below is Spotify's OWN terminology — `player state`, `spotify
# url`, even `current track` — and AppleScript resolves terminology at
# COMPILE time, out of the application bundle on disk. With no bundle to
# read, this script does not run and report "not running": it cannot be
# compiled at all.
#
# For thirteen milestones that was merely expensive, because the script was
# compiled inside a fresh `osascript` process, where it failed with a
# syntax error in 182ms and nobody saw anything: "141:146: syntax error:
# Expected “,” but found identifier. (-2741)". Compiled in THIS process it
# is not expensive, it is a modal dialogue: macOS puts up its "Where is
# Spotify?" application chooser, in front of the user, and blocks the
# thread that asked until somebody dismisses it. Measured, by compiling
# against an application name that is not installed: still blocked after
# five minutes, with a file panel on screen owned by this process.
#
# And it is not a property of the `tell` block. The dictionary-free probe
# this module used to keep for exactly this case — `if application "X" is
# not running` and nothing else, which compiles anywhere — puts up the
# SAME dialogue, because what cannot be resolved is the application, not
# its vocabulary. So there is no AppleScript that is safe to ask about an
# application that might not be there.
#
# Hence the rule this module now holds itself to: **AppleScript is never
# compiled or sent unless Spotify is already running**, and whether it is
# running is answered by AppKit rather than by AppleScript (see
# ``spotify_running``). An application that is running is an application
# whose bundle is on disk, so the chooser has nothing to ask about.

# How long any one question to Spotify may take before it is a failure.
#
# It has to be said in the script. The subprocess this module used to run
# was bounded by subprocess.run's own timeout, and an in-process
# NSAppleScript has no equivalent: it sends with the Apple Event Manager's
# default, which is about a minute. A minute is not a bound — it is one
# wedged Spotify away from a monitor thread that outlives shutdown's
# three-second wait, and a QThread destroyed while running is a qFatal.
#
# `with timeout of N seconds` is AppleScript's own, needs no application
# dictionary, and is the same two seconds subprocess.run was given.
_QUERY_TIMEOUT_SECONDS = 2

_SNAPSHOT_SCRIPT = f'''
if application "Spotify" is not running then return "not_running"
with timeout of {_QUERY_TIMEOUT_SECONDS} seconds
	tell application "Spotify"
		set output to (player state as string)
		try
			set output to output & linefeed & (spotify url of current track) \
& linefeed & (name of current track) & linefeed & (artist of current track) \
& linefeed & (album of current track) & linefeed & (duration of current track) \
& linefeed & (player position)
			try
				set output to output & linefeed & (artwork url of current track)
			end try
		end try
		return output
	end tell
end timeout
'''


def _command(body: str) -> str:
    """One instruction to Spotify, bounded like every other question.

    The commands were one-liners and are now three, so the timeout is
    written once here rather than three times beside them.
    """
    return (
        f"with timeout of {_QUERY_TIMEOUT_SECONDS} seconds\n"
        f'\ttell application "Spotify" to {body}\n'
        "end timeout"
    )


class SpotifyQueryError(RuntimeError):
    """A query to Spotify failed or returned something unparseable."""


class PlaybackState(Enum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    NOT_RUNNING = "not_running"


# Some osascript versions render the player-state enum as its raw four-char
# code instead of text. Case matters: kPSp is paused, kPSP is playing.
_RAW_STATE_CODES = {
    "kPSP": PlaybackState.PLAYING,
    "kPSp": PlaybackState.PAUSED,
    "kPSS": PlaybackState.STOPPED,
}


@dataclass(frozen=True)
class PlayerSnapshot:
    state: PlaybackState
    track_id: Optional[str] = None
    # URI scheme kind: "track" for music, "media" for DJ narration, "ad",
    # "episode", ... Spotify's DJ narration reuses the UPCOMING song's ID
    # under the spotify:media: scheme, so the kind is part of identity.
    track_kind: str = "track"
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    position_seconds: Optional[float] = None
    # The album cover, if this Spotify build reports one. Deliberately not
    # part of track_key: a cover appearing a poll later than the metadata
    # must not read as a different song.
    artwork_url: Optional[str] = None
    # Monotonic clock reading from the moment this poll's answer came back.
    # Consumers that need a fresher position than the poll interval (the
    # tap-to-sync stamper) interpolate forward from here instead of running
    # their own osascript query.
    polled_at: Optional[float] = None

    @property
    def has_track(self) -> bool:
        return self.track_id is not None

    @property
    def is_music_track(self) -> bool:
        """True for real songs — the only items worth a lyrics lookup."""
        return self.track_id is not None and self.track_kind == "track"

    @property
    def track_key(self) -> Optional[tuple]:
        """Identity used to detect track changes. Includes the kind so a
        DJ-narration item turning into the song it announced (same ID,
        different scheme) still registers as a change."""
        if self.track_id is not None:
            return (self.track_kind, self.track_id)
        if self.title is not None or self.artist is not None:
            return (self.title, self.artist)
        return None


def _cocoa():
    """``NSAppleScript`` and ``NSRunningApplication``, or None where there
    are not any.

    The single door, the same shape as ``hotkey._carbon()``,
    ``frontmost._workspace()`` and the rest. Returns None off macOS and
    without pyobjc, so every caller has one branch to handle and the suite
    has one seam to shut, and it needs shutting: without it a test would
    send Apple events to the developer's own Spotify, and would answer
    differently depending on whether they happened to have it open.

    Two classes and one door because they are one capability here: the
    question "is Spotify running" exists only to decide whether the other
    one may be used at all (see the snapshot script's comment on the
    application chooser), and a door that shut one without the other would
    shut neither.
    """
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSRunningApplication
        from Foundation import NSAppleScript
    except Exception:  # pragma: no cover - pyobjc missing
        logger.warning("Cocoa unavailable: Spotify cannot be asked anything")
        return None
    return NSAppleScript, NSRunningApplication


# Compiled scripts, by source. Compiling is 19ms of the 24ms an
# uncompiled execution costs, and the two scripts here are constants, so
# the whole of that is paid twice per process rather than 200,000 times a
# day.
_compiled: dict[str, object] = {}

# One execution at a time. Not defensive: measured, three threads sharing
# one compiled script took 6.8s per execution against 0.13s serialised.
# The monitor's thread and the worker pool's seek/pause/resume are the two
# callers, and they do collide.
_ask_lock = threading.Lock()


def _compile(script, source: str):
    compiled = script.alloc().initWithSource_(source)
    ok, error = compiled.compileAndReturnError_(None)
    if not ok:
        # The failure a Mac with no Spotify on it produces, every time,
        # for a reason that will not clear. read_snapshot tells that case
        # apart from a transient one; here it is just a failure.
        raise SpotifyQueryError(f"could not compile: {_message(error)}")
    return compiled


def _message(error) -> str:
    """What went wrong, out of NSAppleScript's error dictionary.

    Its own message, not a socket's or a subprocess's: the string is shown
    nowhere, but it is logged, and a log line that says "None" is a log
    line that cost somebody an evening.
    """
    if error is None:
        return "no reason given"
    try:
        return str(error.get("NSAppleScriptErrorMessage") or dict(error))
    except Exception:  # pragma: no cover - defensive
        return str(error)


def _ask(expression: str) -> str:
    """Send one AppleScript expression to Spotify and return its answer.

    A blocking round trip to another process — never call this on a UI
    thread. Raises SpotifyQueryError for every failure, which is the one
    thing every caller of this module has always been able to rely on.

    Nothing is sent, and nothing is even compiled, unless Spotify is
    running. The gate lives here rather than in each caller because
    forgetting it is not a wrong answer, it is a modal chooser on the
    user's screen and a thread blocked behind it (see the snapshot script
    above). One choke point, so a command added later cannot miss it.
    """
    door = _cocoa()
    if door is None:
        raise SpotifyQueryError("no AppleScript on this machine")
    if not spotify_running():
        raise SpotifyQueryError("Spotify is not running")
    script, _ = door
    with _ask_lock:
        compiled = _compiled.get(expression)
        if compiled is None:
            compiled = _compile(script, expression)
            _compiled[expression] = compiled
        result, error = compiled.executeAndReturnError_(None)
    if result is None:
        raise SpotifyQueryError(_message(error))
    return (result.stringValue() or "").strip()


def _parse_state(raw: str) -> PlaybackState:
    text = raw.strip().lower()
    for state in (PlaybackState.PLAYING, PlaybackState.PAUSED, PlaybackState.STOPPED):
        if state.value == text:
            return state
    for code, state in _RAW_STATE_CODES.items():
        if code in raw:
            return state
    raise SpotifyQueryError(f"unrecognized player state: {raw!r}")


def _parse_track_id(url: str) -> Optional[str]:
    """Extract the bare track ID from a Spotify URI or open.spotify.com URL."""
    url = url.strip()
    if not url:
        return None
    if url.startswith("http"):
        # e.g. https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=...
        return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or None
    # e.g. spotify:track:4uLU6hMCjMI75M1A2tKUQC
    return url.rsplit(":", 1)[-1] or None


def _parse_track_kind(url: str) -> str:
    """The URI scheme kind: "track", "media" (DJ narration), "ad", ..."""
    url = url.strip().split("?", 1)[0].rstrip("/")
    parts = url.split("/") if url.startswith("http") else url.split(":")
    return parts[-2] if len(parts) >= 2 and parts[-2] else "track"


# -- being told to ask again ------------------------------------------------
#
# The loop no longer asks Spotify on every tick, so something has to say
# when what it last said has stopped being true. Two things do, and both
# come through here:
#
# - Spotify's own announcement, via player_events.py, which covers every
#   track change, every play and pause, and Spotify quitting and starting.
# - this app changing what the player is doing, which is the one kind of
#   seek the loop must never wait to notice: the loop's wrap, tap-to-sync
#   and echo practice all move the position, several times a song.
#
# Module level rather than a method on the monitor, because the commands
# below are module functions and the alternative is threading a monitor
# reference through every QRunnable that sends one. That makes it
# impossible to add a command that forgets, which is the property worth
# having.
_wake = threading.Event()
_rings = 0
_observing = False
_rings_lock = threading.Lock()

# Where this app itself put the player, and when: ``(seconds, monotonic)``.
#
# ``disturb()`` says "go and ask". This says what the answer IS, and the
# case it exists for is the one where asking does not work: ``poll_once``
# clears the wake BEFORE the query and returns None when the query raises,
# so a transient failure has spent the disturb the seek rang and the next
# answer is a whole reconciliation interval away. Everything in between is
# carried forward from before the seek. Measured, with one failed poll
# after a line loop's wrap seek: the window was told a position 9.673s
# from where Spotify was. With this, 0.000s.
#
# It is worth being clear about what this did NOT fix, because it was
# written to. The loop's own bug — a wrap dispatched twice, audible at
# every wrap — is not staleness at all: the position the window is told
# agrees with Spotify to 0.000s across every round trip measured, because
# disturb() lands inside one and because a query cannot execute while a
# seek is (one lock). See ``loop.observe_position``.
#
# A position this app set is not an observation waiting to be made. It is
# an answer, and it is available the moment the command comes back.
_moved: Optional[tuple[float, float]] = None
_moved_lock = threading.Lock()


def announce() -> None:
    """Spotify says something changed. Ask it what, now.

    Counted as well as signalled, because the monitor also has to notice
    an announcement it did NOT get (see ``PlayerMonitor.interval``).
    """
    global _rings
    with _rings_lock:
        _rings += 1
    _wake.set()


def announcements() -> int:
    with _rings_lock:
        return _rings


def observing(listening: Optional[bool] = None) -> bool:
    """Whether anything is listening for Spotify's announcements.

    Set by whoever registered the observer, read by the monitor to decide
    how often it has to ask. It starts optimistic rather than earning the
    slower rate from the first announcement, because announcements only
    arrive when something CHANGES: a user who starts the app in the middle
    of a song and lets it play would have earned nothing for four minutes,
    which is most of the saving thrown away for a case that then corrects
    itself in one tick. The pessimistic direction is covered instead, by
    the monitor noticing a change nobody announced.
    """
    global _observing
    if listening is not None:
        _observing = bool(listening)
    return _observing


def disturb() -> None:
    """This app has just moved the player. What it last said is stale."""
    _wake.set()


def moved(seconds: float) -> None:
    """This app has just put the player AT ``seconds``. Stamped now.

    The companion to ``disturb()`` and not a replacement for it: one says
    the last answer is stale, this one says what replaces it in the
    meantime. Both, because a seek is a reason to go and look as well as a
    fact about where the player is.
    """
    global _moved
    with _moved_lock:
        _moved = (seconds, time.monotonic())


def last_move() -> Optional[tuple[float, float]]:
    """The last position this app set, and when — or None."""
    with _moved_lock:
        return _moved


def set_position(seconds: float) -> None:
    """Seek Spotify to ``seconds``. A blocking round trip to another
    process — never invoke on a UI thread. Raises SpotifyQueryError on
    failure."""
    try:
        _ask(_command(f"set player position to {seconds:.3f}"))
        # After the command has come back, and so NOT in the finally: a
        # seek that failed moved nothing, and saying it did would be a
        # wrong answer where the stale estimate is merely an old one. A
        # failed seek surfaces as the position drifting out of the loop's
        # bounds, which is what cancels the loop — the documented
        # behaviour, and the one thing that must not be papered over.
        moved(seconds)
    finally:
        # In the finally, because a command that failed is exactly as much
        # of a reason to go and look as one that worked: the failure might
        # have been the reply, not the seek.
        disturb()


def pause_playback() -> None:
    """Pause Spotify. Blocking round trip — worker threads only."""
    try:
        _ask(_command("pause"))
    finally:
        disturb()


def resume_playback() -> None:
    """Resume Spotify. Blocking round trip — worker threads only."""
    try:
        _ask(_command("play"))
    finally:
        disturb()


def spotify_running() -> bool:
    """Whether Spotify is running, asked without AppleScript.

    This is the gate, not a convenience. AppleScript cannot be asked about
    an application that might not be installed without risking a modal
    chooser (see the snapshot script above), so what decides whether to
    ask has to be something else, and AppKit answers from the list of
    running processes: no Apple event, no LaunchServices search, no
    permission, and measured at 0.017ms of CPU against 25.7ms for the
    dictionary-free AppleScript probe this replaces.

    False both for "installed and not running" and for "not installed at
    all", and the difference does not matter to any caller: from the
    window's point of view they are the same silence. It is also what
    notices Spotify starting later, since it is asked every time.
    """
    door = _cocoa()
    if door is None:
        return False
    _, running_application = door
    try:
        apps = running_application.runningApplicationsWithBundleIdentifier_(
            SPOTIFY_BUNDLE_ID
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("could not ask which applications are running", exc_info=True)
        return False
    return bool(apps)


def _not_running(polled_at: float) -> PlayerSnapshot:
    return PlayerSnapshot(state=PlaybackState.NOT_RUNNING, polled_at=polled_at)


def read_snapshot() -> PlayerSnapshot:
    """Query Spotify once. Raises SpotifyQueryError only if the state itself
    is unreadable; a missing track degrades to a track-less snapshot, and a
    Mac with no Spotify on it degrades to "not running" — which is the
    truth, and is what it was always meant to report.

    The running check comes FIRST now, and it is the whole of the "Mac
    with no Spotify" answer. It used to come second, asked only when the
    snapshot had already failed, because it was the expensive one; asked
    in-process it costs a fraction of a microsecond, and asking the
    snapshot first is no longer merely wasteful but unsafe.
    """
    if not spotify_running():
        return _not_running(time.monotonic())
    output = _ask(_SNAPSHOT_SCRIPT)
    # Stamped the instant the query returns: this is how fresh the position
    # below is, and callers extrapolate from it.
    polled_at = time.monotonic()
    lines = output.splitlines()
    if not lines:
        raise SpotifyQueryError("empty answer from Spotify")
    if lines[0] == "not_running":
        return _not_running(polled_at)

    state = _parse_state(lines[0])
    # 7 lines is a track whose artwork URL was not reported, 8 is one with
    # it. Anything else means no track loaded, or a track field itself
    # contained a newline (rare enough to degrade gracefully).
    if len(lines) not in (7, 8):
        logger.debug("snapshot (no track): state=%r lines=%r", lines[0], lines[1:])
        return PlayerSnapshot(state=state, polled_at=polled_at)
    url, title, artist, album, duration_raw, position_raw = lines[1:7]
    artwork_url = lines[7].strip() if len(lines) == 8 else ""
    logger.debug(
        "snapshot: state=%r url=%r title=%r artist=%r album=%r dur=%r pos=%r art=%r",
        lines[0], url, title, artist, album, duration_raw, position_raw, artwork_url,
    )
    try:
        # Locale-dependent decimal separator: some systems print "12,34".
        duration_ms = int(float(duration_raw.replace(",", ".")))
        position_seconds = float(position_raw.replace(",", "."))
    except ValueError:
        return PlayerSnapshot(state=state, polled_at=polled_at)

    return PlayerSnapshot(
        state=state,
        track_id=_parse_track_id(url),
        track_kind=_parse_track_kind(url),
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        position_seconds=position_seconds,
        artwork_url=artwork_url or None,
        polled_at=polled_at,
    )


SnapshotCallback = Callable[[PlayerSnapshot], None]

# How often the window hears where the player is. Named rather than left
# as a default argument because the window reasons about it too: its
# predicted line swap is allowed to run ahead of the player by the
# choreography plus one of these, since the position that proves the
# prediction right cannot arrive any sooner than the next update.
#
# This is the rate the window is TOLD at, and it has not changed. What
# changed is that most of those updates no longer cost an Apple event:
# between queries the position is worked out from the monotonic clock.
POLL_INTERVAL_SECONDS = 0.3

# How long the monitor will go without asking Spotify anything, once
# Spotify's announcements have proved they arrive.
#
# This is the seek window and nothing else. Everything else that can
# happen to a player rings the doorbell (player_events.py lists them, each
# one driven and timed), and the app's own seeks call disturb() the
# instant they land. What is left is the user dragging Spotify's own
# scrubber, which is measured to announce nothing at all, and which this
# is the whole latency of.
#
# The value is a trade and is stated as one. A query costs 4.3ms of CPU,
# so the cost of the loop is 4.3ms/T:
#
#   T = 0.3s (what it used to be, always)   1.43% of one core
#   T = 0.52s (one line change)             0.83%
#   T = 1.0s (this)                         0.43%
#   T = 2.0s                                0.21%
#
# against 19.6% for the subprocess this replaced. 1.0s buys most of what
# there is to buy and leaves a seek corrected inside about two line
# changes, which is the unit the window already moves in. Below 0.5s the
# curve is steep and the thing being bought is latency on the one action
# the user is already watching the player redraw.
RECONCILE_SECONDS = 1.0


class PlayerMonitor:
    """Follows Spotify and fires callbacks when things change.

    Callbacks all receive the current ``PlayerSnapshot``:

    - ``on_state_change``: playing/paused/stopped/not_running transitions
    - ``on_track_change``: the current track changed (including to none)
    - ``on_position_update``: every tick while a track is loaded

    On the first tick, state/track callbacks fire once to report the
    initial situation.

    A tick is not a query. ``tick()`` asks Spotify when there is a reason
    to — the doorbell rang, this app moved the player, or the last answer
    is older than ``interval()`` — and otherwise carries the last answer
    forward on the monotonic clock. Every callback fires from the same
    rules whichever it was, so nothing downstream can tell the difference
    and nothing downstream has to.
    """

    def __init__(
        self,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        on_track_change: Optional[SnapshotCallback] = None,
        on_position_update: Optional[SnapshotCallback] = None,
        on_state_change: Optional[SnapshotCallback] = None,
        reconcile_interval: float = RECONCILE_SECONDS,
    ) -> None:
        self.poll_interval = poll_interval
        self.reconcile_interval = reconcile_interval
        self.on_track_change = on_track_change
        self.on_position_update = on_position_update
        self.on_state_change = on_state_change
        self._last: Optional[PlayerSnapshot] = None
        self._track_loss_pending = False
        # The last time Spotify itself was asked, as opposed to the last
        # time the window was told something.
        self._asked_at: Optional[float] = None
        # Whether an announcement has actually arrived, and whether one
        # has ever been missed. The second is what there is no version of
        # Spotify to sniff for: a track or state change discovered by
        # asking, with nothing having rung for it, is the doorbell caught
        # missing something, and the monitor goes back to asking.
        self._rung = False
        self._missed = False
        self._rings_seen = announcements()
        # Set by stop() and never cleared. A monitor is run once, and the
        # flag this replaces was raised at the top of run() — so a stop()
        # that landed in the gap between starting the thread and the thread
        # body actually beginning was simply erased, and the loop polled on
        # forever. That surfaced as "monitor thread did not stop in time"
        # at shutdown, which is one bounded wait away from destroying a
        # QThread that is still running.
        self._stop = threading.Event()

    # -- how often to actually ask -----------------------------------------

    def interval(self) -> float:
        """How long the monitor may go without asking Spotify anything.

        The reconciliation interval while there is a doorbell to rely on,
        and the old poll interval otherwise — so a Mac where the observer
        would not install, or a Spotify that does not announce, behaves
        exactly as this app did before any of this existed. There is
        nothing to configure and nothing to detect: it is lost by a change
        arriving unannounced and given back by the next one that is
        announced properly.
        """
        if self._missed:
            return self.poll_interval
        if self._rung or observing():
            return self.reconcile_interval
        return self.poll_interval

    def _due(self, now: float) -> bool:
        """Whether this tick has to be a real query.

        Due when waiting for the NEXT tick would leave the answer older
        than ``interval()``, rather than when it already is. Two reasons,
        and the second is why it is written this way round:

        - the interval is a ceiling on how stale an answer may be, and
          "ask once it is too old" would mean it always is, by a tick.
        - the two are equal until the doorbell has earned the slower rate,
          and `elapsed >= interval` on floating-point ticks that are
          nominally exactly one interval apart is a coin toss. That would
          have halved the rate in the very case that exists to be
          identical to the old behaviour.
        """
        if _wake.is_set() or self._asked_at is None or self._last is None:
            return True
        return now - self._asked_at > self.interval() - self.poll_interval

    def _rebased(
        self, snapshot: Optional[PlayerSnapshot]
    ) -> Optional[PlayerSnapshot]:
        """``snapshot``, with a seek this app made applied to it.

        Applied to exactly the answers that cannot already include it —
        the ones taken before it. The comparison is between two stamps
        that mean the same thing, which is why it can be trusted: both are
        read the instant that call's own round trip came back, and every
        question and every command goes through one lock, so a query whose
        round trip ended before the seek's ran entirely before it.

        That is also why this is asked of the LAST answer and not of a
        fresh one. A query cannot run while a seek is executing, so an
        answer that has just come back is always stamped after any seek
        that has already finished, and rebasing it could never do
        anything. What goes stale is the answer being carried forward.

        Once a query comes back from after the seek, its stamp is the
        later one and this becomes a no-op for ever, which is why nothing
        has to clear the move: an answer that has seen it wins, including
        the user dragging Spotify's own scrubber afterwards.

        A snapshot with no position is left alone. That is the one-poll
        track dropout, whose whole point is that it reports no position;
        giving it one would fire a position event from a substitute
        snapshot that exists to avoid exactly that.
        """
        if snapshot is None or snapshot.position_seconds is None:
            return snapshot
        move = last_move()
        if move is None or snapshot.polled_at is None:
            return snapshot
        position, at = move
        if at < snapshot.polled_at:
            return snapshot
        return replace(snapshot, position_seconds=position, polled_at=at)

    def _carried_forward(self, now: float) -> Optional[PlayerSnapshot]:
        """Where the player must be, given where it was and how long ago.

        Exact rather than approximate, and that is measured: checked
        against Spotify's own answer every five seconds for 92 seconds of
        one track, the largest disagreement was 1.4ms and there was no
        trend. Spotify's player position and this machine's monotonic
        clock are the same clock.

        None when there is nothing to carry forward, which makes the tick
        a query instead: no answer yet, no position in the last one (a
        debounced blip), or a position that would now be past the end of
        the track — that last one is a song that has finished, and the
        announcement for the next one is either already on its way or
        never coming, and either way the answer is to go and ask.
        """
        last = self._last
        if last is None or last.polled_at is None or last.position_seconds is None:
            return None
        elapsed = now - last.polled_at
        if last.state is not PlaybackState.PLAYING:
            # Paused, stopped, gone: the position is not moving, but the
            # stamp is, and the stamp is what anything interpolating from
            # this snapshot extrapolates from.
            return replace(last, polled_at=now)
        position = last.position_seconds + elapsed
        if last.duration_ms is not None and position * 1000 >= last.duration_ms:
            return None
        return replace(last, position_seconds=position, polled_at=now)

    def tick(self) -> Optional[PlayerSnapshot]:
        """One turn of the loop. A query when there is a reason for one,
        and otherwise the last answer carried forward.

        Returns whatever the window was told, or None if a query was tried
        and transiently failed.
        """
        now = time.monotonic()
        # Before anything reads it. `_last` is only ever written by a
        # query, so rebasing it here is idempotent — the carried-forward
        # snapshot below is derived from it and never replaces it — and it
        # is what makes the answer between two queries as fresh as the
        # seek that caused it.
        self._last = self._rebased(self._last)
        if self._due(now):
            return self.poll_once()
        snapshot = self._carried_forward(now)
        if snapshot is None:
            return self.poll_once()
        if snapshot.position_seconds is not None:
            self._fire(self.on_position_update, snapshot)
        return snapshot

    def poll_once(self) -> Optional[PlayerSnapshot]:
        """One real query, and the callbacks it earns. Returns the
        snapshot, or None if the query transiently failed (the previous
        state is kept)."""
        # Cleared BEFORE the query: an announcement arriving while it is in
        # flight describes something this answer may not include, and
        # clearing afterwards would throw that away. At worst one redundant
        # query, which is the direction to be wrong in.
        _wake.clear()
        self._asked_at = time.monotonic()
        try:
            snapshot = read_snapshot()
        except SpotifyQueryError as exc:
            # Said out loud, at last. This used to be swallowed entirely,
            # which meant a Mac where every poll failed looked exactly like
            # a Mac where nothing was playing — with nothing anywhere to
            # say which. Debug rather than warning: a genuinely transient
            # failure happens, and one line three times a second is not a
            # diagnostic, it is a stream.
            logger.debug("poll failed: %s", exc)
            return None

        # Counted AFTER the query, and against the last poll's reading, so
        # a ring that landed mid-query still counts for the change this
        # answer is about to report. Counted before, a track change and
        # its announcement racing the same 133ms round trip would read as
        # a change nobody announced, and the doorbell would be blamed for
        # arriving on time.
        rings = announcements()
        rang = rings != self._rings_seen
        self._rings_seen = rings

        previous = self._last

        # Debounce one-poll track dropouts: mid item-switch (DJ hand-offs,
        # queue advances) AppleScript can briefly report no track. A single
        # such poll keeps the previous track's fields (state still updates);
        # only a second consecutive trackless poll is a real track loss.
        if snapshot.track_key is not None:
            self._track_loss_pending = False
        elif (
            previous is not None
            and previous.track_key is not None
            and not self._track_loss_pending
        ):
            self._track_loss_pending = True
            # Keep the track's identity/metadata; position is unknown this
            # poll, so no position event fires from the substitute.
            snapshot = replace(previous, state=snapshot.state, position_seconds=None)
        self._last = snapshot

        state_changed = previous is None or snapshot.state != previous.state
        previous_key = previous.track_key if previous is not None else None
        track_changed = previous is None or snapshot.track_key != previous_key
        # Who found out first. On the very first query there is no
        # previous to have missed anything, so it says nothing either way.
        if rang:
            self._rung = True
            self._missed = False
        elif previous is not None and (state_changed or track_changed):
            if not self._missed:
                logger.info(
                    "a change arrived without being announced: asking Spotify "
                    "on a timer again"
                )
            self._missed = True

        if state_changed:
            self._fire(self.on_state_change, snapshot)
        if track_changed:
            self._fire(self.on_track_change, snapshot)
        if snapshot.position_seconds is not None:
            self._fire(self.on_position_update, snapshot)
        return snapshot

    @staticmethod
    def _fire(callback: Optional[SnapshotCallback], snapshot: PlayerSnapshot) -> None:
        if callback is not None:
            callback(snapshot)

    def run(self) -> None:
        """Block and tick until ``stop()`` is called (or KeyboardInterrupt).

        Returns immediately if ``stop()`` already happened: the request is
        never overwritten from in here, which is the whole point of the
        event.

        The wait is on ``_wake`` rather than on ``_stop``, so a doorbell
        or one of this app's own seeks interrupts it and is acted on
        within the round trip rather than at the end of the tick it landed
        in. ``stop()`` sets both, so quitting still does not sit out a
        wait — and ``_stop`` is still the only thing this loop's condition
        reads, so a stop can no more be lost now than it could before.
        """
        while not self._stop.is_set():
            started = time.monotonic()
            self.tick()
            remaining = self.poll_interval - (time.monotonic() - started)
            if remaining > 0:
                _wake.wait(remaining)

    def stop(self) -> None:
        self._stop.set()
        # Second, and only ever as a nudge: the loop's condition is _stop
        # and nothing else, so this can only make the wait end sooner.
        _wake.set()
