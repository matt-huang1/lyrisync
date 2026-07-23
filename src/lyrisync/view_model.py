"""Pure display logic for the lyrics window. No Qt, no I/O.

The window feeds player/fetch events in and renders the resulting
``Display``. Keeping this separate from the widget makes the state
machine — including the stale-fetch guard — testable without a display
server.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lyrisync.lyrics_provider import TrackLyrics
from lyrisync.player_monitor import PlaybackState, PlayerSnapshot
from lyrisync.sync import current_line_index


class Mode(Enum):
    IDLE = "idle"          # Spotify closed/stopped or no track
    FETCHING = "fetching"  # lyrics lookup in flight
    SYNCED = "synced"      # timed lines advancing with playback
    PLAIN = "plain"        # lyrics exist but carry no timestamps
    NO_LYRICS = "no_lyrics"
    ERROR = "error"        # fetch failed (network/server); not cached, will retry


@dataclass(frozen=True)
class Display:
    mode: Mode
    header: str = ""       # "Artist — Title"
    previous: str = ""
    current: str = ""
    upcoming: str = ""
    plain_text: str = ""   # full text, only in PLAIN mode


class LyricsViewModel:
    """State machine behind the window.

    Each mutating method returns True when the visible display changed, so
    the caller re-renders only when needed. ``fetch_completed`` ignores
    results for tracks that are no longer current (the fetch raced a track
    change); the provider has already cached them by then.
    """

    def __init__(self) -> None:
        self._mode = Mode.IDLE
        self._track_id: Optional[str] = None
        self._header = ""
        self._lyrics: Optional[TrackLyrics] = None
        self._index = -1

    def track_changed(self, snapshot: PlayerSnapshot) -> bool:
        """Returns True when the new track needs a lyrics fetch."""
        if not snapshot.has_track:
            self._reset()
            return False
        self._track_id = snapshot.track_id
        self._header = f"{snapshot.artist} — {snapshot.title}"
        self._lyrics = None
        self._index = -1
        self._mode = Mode.FETCHING
        return True

    def fetch_completed(
        self, track_id: str, lyrics: Optional[TrackLyrics], ok: bool = True
    ) -> bool:
        """Returns False for stale results, which must not be displayed.
        ``ok=False`` means the fetch errored: show the retryable
        "unavailable" state rather than claiming there are no lyrics."""
        if track_id != self._track_id:
            return False
        if not ok:
            self._lyrics = None
            self._mode = Mode.ERROR
            return True
        self._lyrics = lyrics
        if lyrics is None:
            self._mode = Mode.NO_LYRICS
        elif lyrics.synced:
            self._mode = Mode.SYNCED
        else:
            self._mode = Mode.PLAIN
        return True

    def position_changed(self, position_seconds: Optional[float]) -> bool:
        if self._mode is not Mode.SYNCED or position_seconds is None:
            return False
        index = current_line_index(self._lyrics.synced, position_seconds)
        if index == self._index:
            return False
        self._index = index
        return True

    def player_state_changed(self, state: PlaybackState) -> bool:
        if state in (PlaybackState.NOT_RUNNING, PlaybackState.STOPPED):
            if self._mode is Mode.IDLE:
                return False
            self._reset()
            return True
        return False

    def _reset(self) -> None:
        self._mode = Mode.IDLE
        self._track_id = None
        self._header = ""
        self._lyrics = None
        self._index = -1

    def display(self) -> Display:
        mode = self._mode
        if mode is Mode.IDLE:
            return Display(mode=mode, current="Spotify is not playing")
        if mode is Mode.FETCHING:
            return Display(mode=mode, header=self._header, current="fetching…")
        if mode is Mode.NO_LYRICS:
            return Display(mode=mode, header=self._header, current="no lyrics found")
        if mode is Mode.ERROR:
            return Display(
                mode=mode,
                header=self._header,
                current="lyrics unavailable — will retry",
            )
        if mode is Mode.PLAIN:
            return Display(
                mode=mode,
                header=self._header,
                previous="plain lyrics — not synced",
                plain_text=self._lyrics.plain or "",
            )
        lines = self._lyrics.synced
        index = self._index
        return Display(
            mode=mode,
            header=self._header,
            previous=lines[index - 1][1] if index >= 1 else "",
            current=lines[index][1] if index >= 0 else "",
            upcoming=lines[index + 1][1] if index + 1 < len(lines) else "",
        )
