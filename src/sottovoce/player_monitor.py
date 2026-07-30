"""Poll the Spotify desktop app via AppleScript and emit playback events.

This module knows nothing about lyrics or the UI. It exposes:

- ``PlayerSnapshot`` / ``PlaybackState``: what Spotify is doing right now
- ``read_snapshot()``: one osascript query
- ``PlayerMonitor``: polls on an interval and fires callbacks on changes

All fields are fetched in a single osascript call that returns
newline-separated values, so every poll is one subprocess and the fields
are read atomically — a track change can't produce a snapshot mixing old
and new metadata.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_OSASCRIPT_TIMEOUT = 2.0

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
_SNAPSHOT_SCRIPT = '''
if application "Spotify" is not running then return "not_running"
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
'''


class SpotifyQueryError(RuntimeError):
    """An osascript query failed or returned something unparseable."""


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


def _osascript(expression: str) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-e", expression],
            capture_output=True,
            text=True,
            timeout=_OSASCRIPT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SpotifyQueryError(f"osascript failed: {exc}") from exc
    if proc.returncode != 0:
        message = proc.stderr.strip() or f"osascript exited {proc.returncode}"
        raise SpotifyQueryError(message)
    return proc.stdout.strip()


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


def set_position(seconds: float) -> None:
    """Seek Spotify to ``seconds``. A subprocess call — never invoke on a
    UI thread. Raises SpotifyQueryError on failure."""
    _osascript(
        f'tell application "Spotify" to set player position to {seconds:.3f}'
    )


def pause_playback() -> None:
    """Pause Spotify. Subprocess call — worker threads only."""
    _osascript('tell application "Spotify" to pause')


def resume_playback() -> None:
    """Resume Spotify. Subprocess call — worker threads only."""
    _osascript('tell application "Spotify" to play')


def read_snapshot() -> PlayerSnapshot:
    """Query Spotify once. Raises SpotifyQueryError only if the state itself
    is unreadable; a missing track degrades to a track-less snapshot."""
    output = _osascript(_SNAPSHOT_SCRIPT)
    # Stamped the instant the query returns: this is how fresh the position
    # below is, and callers extrapolate from it.
    polled_at = time.monotonic()
    lines = output.splitlines()
    if not lines:
        raise SpotifyQueryError("empty osascript output")
    if lines[0] == "not_running":
        return PlayerSnapshot(state=PlaybackState.NOT_RUNNING, polled_at=polled_at)

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

# How often the player is asked where it is. Named rather than left as a
# default argument because the window reasons about it too: its predicted
# line swap is allowed to run ahead of the player by the choreography plus
# one of these, since the position that proves the prediction right cannot
# arrive any sooner than the next poll.
POLL_INTERVAL_SECONDS = 0.3


class PlayerMonitor:
    """Polls Spotify and fires callbacks when things change.

    Callbacks all receive the current ``PlayerSnapshot``:

    - ``on_state_change``: playing/paused/stopped/not_running transitions
    - ``on_track_change``: the current track changed (including to none)
    - ``on_position_update``: every poll while a track is loaded

    On the first poll, state/track callbacks fire once to report the
    initial situation.
    """

    def __init__(
        self,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        on_track_change: Optional[SnapshotCallback] = None,
        on_position_update: Optional[SnapshotCallback] = None,
        on_state_change: Optional[SnapshotCallback] = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.on_track_change = on_track_change
        self.on_position_update = on_position_update
        self.on_state_change = on_state_change
        self._last: Optional[PlayerSnapshot] = None
        self._track_loss_pending = False
        # Set by stop() and never cleared. A monitor is run once, and the
        # flag this replaces was raised at the top of run() — so a stop()
        # that landed in the gap between starting the thread and the thread
        # body actually beginning was simply erased, and the loop polled on
        # forever. That surfaced as "monitor thread did not stop in time"
        # at shutdown, which is one bounded wait away from destroying a
        # QThread that is still running.
        self._stop = threading.Event()

    def poll_once(self) -> Optional[PlayerSnapshot]:
        """One poll cycle. Returns the snapshot, or None if the query
        transiently failed (the previous state is kept)."""
        try:
            snapshot = read_snapshot()
        except SpotifyQueryError:
            return None

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

        if previous is None or snapshot.state != previous.state:
            self._fire(self.on_state_change, snapshot)
        previous_key = previous.track_key if previous is not None else None
        if previous is None or snapshot.track_key != previous_key:
            self._fire(self.on_track_change, snapshot)
        if snapshot.position_seconds is not None:
            self._fire(self.on_position_update, snapshot)
        return snapshot

    @staticmethod
    def _fire(callback: Optional[SnapshotCallback], snapshot: PlayerSnapshot) -> None:
        if callback is not None:
            callback(snapshot)

    def run(self) -> None:
        """Block and poll until ``stop()`` is called (or KeyboardInterrupt).

        Returns immediately if ``stop()`` already happened: the request is
        never overwritten from in here, which is the whole point of the
        event.
        """
        while not self._stop.is_set():
            started = time.monotonic()
            self.poll_once()
            remaining = self.poll_interval - (time.monotonic() - started)
            if remaining > 0:
                # Waited on rather than slept through, so stopping does not
                # first sit out the rest of a poll interval.
                self._stop.wait(remaining)

    def stop(self) -> None:
        self._stop.set()
