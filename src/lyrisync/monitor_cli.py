"""Terminal runner: prints player-monitor events so they can be verified by
playing, pausing, and switching songs in Spotify.

Run with ``lyrisync-monitor`` or ``python -m lyrisync.monitor_cli``.
"""

from __future__ import annotations

import sys
import time

from lyrisync.player_monitor import PlaybackState, PlayerMonitor, PlayerSnapshot


def _fmt_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class EventPrinter:
    """Prints events on their own lines and the position as an in-place
    updating line at the bottom."""

    def __init__(self) -> None:
        self._position_line_active = False

    def _event(self, message: str) -> None:
        if self._position_line_active:
            sys.stdout.write("\n")
            self._position_line_active = False
        timestamp = time.strftime("%H:%M:%S")
        sys.stdout.write(f"[{timestamp}] {message}\n")
        sys.stdout.flush()

    def on_state_change(self, snapshot: PlayerSnapshot) -> None:
        self._event(f"state: {snapshot.state.value}")

    def on_track_change(self, snapshot: PlayerSnapshot) -> None:
        if not snapshot.has_track:
            self._event("track: (none)")
            return
        duration = (
            _fmt_clock(snapshot.duration_ms / 1000)
            if snapshot.duration_ms is not None
            else "?:??"
        )
        self._event(
            f"track: {snapshot.artist} — {snapshot.title} "
            f"[{snapshot.album}] ({duration})  id={snapshot.track_id}"
        )

    def on_position_update(self, snapshot: PlayerSnapshot) -> None:
        duration = (
            _fmt_clock(snapshot.duration_ms / 1000)
            if snapshot.duration_ms is not None
            else "?:??"
        )
        line = (
            f"  {_fmt_clock(snapshot.position_seconds)} / {duration}"
            f"  ({snapshot.state.value})"
        )
        sys.stdout.write("\r\x1b[K" + line)
        sys.stdout.flush()
        self._position_line_active = True


def main() -> int:
    printer = EventPrinter()
    monitor = PlayerMonitor(
        poll_interval=0.3,
        on_state_change=printer.on_state_change,
        on_track_change=printer.on_track_change,
        on_position_update=printer.on_position_update,
    )
    print("lyrisync player monitor — polling Spotify every 300ms, Ctrl-C to quit")
    print("(first run may trigger a macOS Automation permission prompt)")
    try:
        monitor.run()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
