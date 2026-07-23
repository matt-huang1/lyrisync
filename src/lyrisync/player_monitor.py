"""Poll the Spotify desktop app via AppleScript and emit playback events.

This module knows nothing about lyrics or the UI. It exposes:

- ``PlayerSnapshot`` / ``PlaybackState``: what Spotify is doing right now
- ``read_snapshot()``: one round of osascript queries
- ``PlayerMonitor``: polls on an interval and fires callbacks on changes

Every field is fetched with its own osascript call so values never need to
be split out of a delimited string.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

_OSASCRIPT_TIMEOUT = 2.0

_SCRIPT_IS_RUNNING = 'application "Spotify" is running'
_SCRIPT_PLAYER_STATE = 'tell application "Spotify" to player state as string'
_SCRIPT_TRACK_URL = 'tell application "Spotify" to spotify url of current track'
_SCRIPT_TRACK_NAME = 'tell application "Spotify" to name of current track'
_SCRIPT_TRACK_ARTIST = 'tell application "Spotify" to artist of current track'
_SCRIPT_TRACK_ALBUM = 'tell application "Spotify" to album of current track'
_SCRIPT_TRACK_DURATION = 'tell application "Spotify" to duration of current track'
_SCRIPT_PLAYER_POSITION = 'tell application "Spotify" to player position'


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
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    position_seconds: Optional[float] = None

    @property
    def has_track(self) -> bool:
        return self.track_id is not None

    @property
    def track_key(self) -> Optional[tuple]:
        """Identity used to detect track changes."""
        if self.track_id is not None:
            return (self.track_id,)
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


def read_snapshot() -> PlayerSnapshot:
    """Query Spotify once. Raises SpotifyQueryError only if the state itself
    is unreadable; a missing track degrades to a track-less snapshot."""
    if _osascript(_SCRIPT_IS_RUNNING) != "true":
        return PlayerSnapshot(state=PlaybackState.NOT_RUNNING)

    state = _parse_state(_osascript(_SCRIPT_PLAYER_STATE))

    try:
        url = _osascript(_SCRIPT_TRACK_URL)
        title = _osascript(_SCRIPT_TRACK_NAME)
        artist = _osascript(_SCRIPT_TRACK_ARTIST)
        album = _osascript(_SCRIPT_TRACK_ALBUM)
        duration_ms = int(float(_osascript(_SCRIPT_TRACK_DURATION)))
        # Locale-dependent decimal separator: some systems print "12,34".
        position_seconds = float(_osascript(_SCRIPT_PLAYER_POSITION).replace(",", "."))
    except (SpotifyQueryError, ValueError):
        # No track loaded (fresh launch, or Spotify mid track-switch).
        return PlayerSnapshot(state=state)

    return PlayerSnapshot(
        state=state,
        track_id=_parse_track_id(url),
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        position_seconds=position_seconds,
    )


SnapshotCallback = Callable[[PlayerSnapshot], None]


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
        poll_interval: float = 0.3,
        on_track_change: Optional[SnapshotCallback] = None,
        on_position_update: Optional[SnapshotCallback] = None,
        on_state_change: Optional[SnapshotCallback] = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.on_track_change = on_track_change
        self.on_position_update = on_position_update
        self.on_state_change = on_state_change
        self._last: Optional[PlayerSnapshot] = None
        self._running = False

    def poll_once(self) -> Optional[PlayerSnapshot]:
        """One poll cycle. Returns the snapshot, or None if the query
        transiently failed (the previous state is kept)."""
        try:
            snapshot = read_snapshot()
        except SpotifyQueryError:
            return None

        previous = self._last
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
        """Block and poll until ``stop()`` is called (or KeyboardInterrupt)."""
        self._running = True
        try:
            while self._running:
                started = time.monotonic()
                self.poll_once()
                remaining = self.poll_interval - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
