"""Terminal runner: synced lyrics for whatever Spotify is playing.

Run with ``lyrisync-lyrics`` or ``python -m lyrisync.lyrics_cli``.

Wires the player monitor to the lyrics provider. Synced lyrics render as a
previous / CURRENT / next block whose current line advances with playback;
plain lyrics print once; missing lyrics report cleanly. Pausing freezes the
line and seeking recovers automatically because the line index is recomputed
from the player position on every poll.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

from lyrisync.lyrics_provider import LyricsError, LyricsProvider, TrackLyrics
from lyrisync.player_monitor import PlaybackState, PlayerMonitor, PlayerSnapshot
from lyrisync.sync import current_line_index

_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_CLEAR_LINE = "\x1b[2K"
_UP_THREE = "\x1b[3F"


class ThreeLineView:
    """Renders previous / CURRENT / next lines, redrawing in place."""

    def __init__(self) -> None:
        self._drawn = False
        self._index: int | None = None

    def invalidate(self) -> None:
        """Forget the on-screen block (call before printing normal lines)."""
        self._drawn = False
        self._index = None

    def draw(self, lines: list[tuple[float, str]], index: int) -> None:
        if self._drawn and index == self._index:
            return
        width = shutil.get_terminal_size().columns
        previous = lines[index - 1][1] if index >= 1 else ""
        current = lines[index][1] if index >= 0 else ""
        upcoming = lines[index + 1][1] if index + 1 < len(lines) else ""

        if self._drawn:
            sys.stdout.write(_UP_THREE)
        for text, is_current in ((previous, False), (current, True), (upcoming, False)):
            text = text[: width - 4]
            if is_current:
                rendered = f"{_BOLD}▶ {text}{_RESET}"
            else:
                rendered = f"{_DIM}  {text}{_RESET}"
            sys.stdout.write(f"{_CLEAR_LINE}{rendered}\n")
        sys.stdout.flush()
        self._drawn = True
        self._index = index


class LyricsApp:
    def __init__(self, provider: LyricsProvider) -> None:
        self.provider = provider
        self.view = ThreeLineView()
        self.synced: list[tuple[float, str]] | None = None

    def _println(self, message: str) -> None:
        self.view.invalidate()
        sys.stdout.write(message + "\n")
        sys.stdout.flush()

    def on_track_change(self, snapshot: PlayerSnapshot) -> None:
        self.synced = None
        if not snapshot.has_track:
            self._println("\n(no track loaded)")
            return
        self._println(f"\n♪ {snapshot.title} — {snapshot.artist}")
        try:
            lyrics = self.provider.get_lyrics(snapshot)
        except LyricsError as exc:
            self._println(f"  lyrics unavailable ({exc}) — will retry next track")
            return
        if lyrics is None:
            self._println("  no lyrics found")
        elif lyrics.kind == "synced":
            self._println("  synced lyrics\n")
            self.synced = lyrics.synced
        else:
            self._println("  plain lyrics (not synced):\n")
            for line in lyrics.plain.splitlines():
                self._println(f"    {line}")

    def on_position_update(self, snapshot: PlayerSnapshot) -> None:
        if self.synced is None or snapshot.position_seconds is None:
            return
        index = current_line_index(self.synced, snapshot.position_seconds)
        self.view.draw(self.synced, index)

    def on_state_change(self, snapshot: PlayerSnapshot) -> None:
        if snapshot.state in (PlaybackState.NOT_RUNNING, PlaybackState.STOPPED):
            self.synced = None
            self._println(f"\n[{snapshot.state.value}]")


def main() -> int:
    # WARNING by default so log lines don't garble the in-place display;
    # LYRISYNC_LOG=INFO shows each LRCLIB request and status.
    logging.basicConfig(level=os.environ.get("LYRISYNC_LOG", "WARNING"))
    app = LyricsApp(LyricsProvider())
    monitor = PlayerMonitor(
        poll_interval=0.3,
        on_track_change=app.on_track_change,
        on_position_update=app.on_position_update,
        on_state_change=app.on_state_change,
    )
    print("lyrisync — synced lyrics from LRCLIB, Ctrl-C to quit")
    try:
        monitor.run()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
