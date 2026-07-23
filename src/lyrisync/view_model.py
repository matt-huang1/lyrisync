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
    NON_MUSIC = "non_music"  # DJ narration, ads: header only, empty body
    ERROR = "error"        # fetch failed (network/server); not cached, will retry


@dataclass(frozen=True)
class Display:
    mode: Mode
    header: str = ""       # "Artist — Title"
    previous: str = ""
    current: str = ""
    upcoming: str = ""
    plain_text: str = ""   # full text, only in PLAIN mode


RETRY_INTERVAL_SECONDS = 30.0


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
        self._identity: Optional[tuple] = None
        self._header = ""
        self._lyrics: Optional[TrackLyrics] = None
        self._index = -1
        self._error_at = 0.0
        self._suspended_mode: Optional[Mode] = None

    def track_changed(self, snapshot: PlayerSnapshot) -> bool:
        """Returns True when the new track needs a lyrics fetch."""
        if not snapshot.has_track:
            self._reset()
            return False
        identity = (snapshot.track_kind, snapshot.track_id)
        if identity == self._identity:
            # Duplicate announcement of the item already shown (metadata
            # settling, transient monitor blips): keep the display — never
            # flash back to loading or dispatch a redundant fetch. ERROR is
            # the exception: fresh metadata is worth a new attempt.
            self._suspended_mode = None
            self._header = f"{snapshot.title} — {snapshot.artist}"
            if self._mode is Mode.ERROR and snapshot.is_music_track:
                self._mode = Mode.FETCHING
                return True
            return False
        self._identity = identity
        self._suspended_mode = None
        self._track_id = snapshot.track_id
        self._header = f"{snapshot.title} — {snapshot.artist}"
        self._lyrics = None
        self._index = -1
        if not snapshot.is_music_track:
            # DJ narration, ads: header only, nothing to look up.
            self._mode = Mode.NON_MUSIC
            return False
        self._mode = Mode.FETCHING
        return True

    def fetch_completed(
        self,
        track_id: str,
        lyrics: Optional[TrackLyrics],
        ok: bool = True,
        now: float = 0.0,
    ) -> bool:
        """Returns False for stale results, which must not be displayed.
        ``ok=False`` means the fetch errored: show the retryable
        "unavailable" state rather than claiming there are no lyrics;
        ``now`` timestamps the failure for the retry schedule."""
        if track_id != self._track_id:
            return False
        if not ok:
            resolved = Mode.ERROR
            self._lyrics = None
            self._error_at = now
        elif lyrics is None:
            resolved = Mode.NO_LYRICS
            self._lyrics = None
        else:
            resolved = Mode.SYNCED if lyrics.synced else Mode.PLAIN
            self._lyrics = lyrics
        if self._mode is Mode.IDLE and self._suspended_mode is not None:
            # Player is stopped right now; remember the outcome for the
            # resume-restore instead of showing lyrics over the idle state.
            self._suspended_mode = resolved
            return False
        self._mode = resolved
        return True

    def retry_due(self, now: float) -> bool:
        """True when a failed fetch should be re-attempted (every
        RETRY_INTERVAL_SECONDS while in ERROR). Flips the mode back to
        FETCHING, so a True return means: dispatch a fetch now."""
        if self._mode is not Mode.ERROR:
            return False
        if now - self._error_at < RETRY_INTERVAL_SECONDS:
            return False
        self._mode = Mode.FETCHING
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
            # Suspend rather than reset: a stop can be a one-poll blip mid
            # item-switch, and resuming the same track fires no track-change
            # event to rebuild from.
            self._suspended_mode = self._mode
            self._mode = Mode.IDLE
            return True
        if self._mode is Mode.IDLE and self._suspended_mode is not None:
            self._mode = self._suspended_mode
            self._suspended_mode = None
            return True
        return False

    def timeline(self) -> Optional[tuple[list, int]]:
        """(synced lines, current index) while in SYNCED mode — what the
        window's anticipatory line-fade scheduler needs."""
        if self._mode is Mode.SYNCED and self._lyrics is not None:
            return self._lyrics.synced, self._index
        return None

    def _reset(self) -> None:
        self._mode = Mode.IDLE
        self._track_id = None
        self._identity = None
        self._header = ""
        self._lyrics = None
        self._index = -1
        self._suspended_mode = None

    def display(self) -> Display:
        mode = self._mode
        if mode is Mode.IDLE:
            return Display(mode=mode, current="Spotify is not playing")
        if mode is Mode.FETCHING:
            # current stays empty: the window renders its animated
            # loading indicator for this mode.
            return Display(mode=mode, header=self._header)
        if mode is Mode.NO_LYRICS:
            return Display(mode=mode, header=self._header, current="no lyrics found")
        if mode is Mode.NON_MUSIC:
            return Display(mode=mode, header=self._header)
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
