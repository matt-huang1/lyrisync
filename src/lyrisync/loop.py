"""Line-loop practice controller: repeat the current lyric line by seeking
back to its start whenever playback reaches its end.

Pure logic, Qt-free like geometry.py. The controller owns the loop bounds
and every keep-or-cancel decision; the caller owns timers and performs the
actual seek (a subprocess call).
"""

from __future__ import annotations

from typing import Optional

# A position this far before the loop start means the user seeked away
# backwards; this far past the end means they seeked forward or our wrap
# seek failed. Either way the user has voted — cancel.
ENTRY_GRACE = 0.5
EXIT_GRACE = 1.0

# Dispatch the wrap seek this early: the osascript write takes ~150-200ms,
# so firing at the end bound exactly would bleed the next line through.
SEEK_LEAD_SECONDS = 0.15


class LineLoop:
    def __init__(self) -> None:
        self._start: Optional[float] = None
        self._end: Optional[float] = None

    @property
    def engaged(self) -> bool:
        return self._start is not None

    @property
    def start(self) -> Optional[float]:
        return self._start

    @property
    def end(self) -> Optional[float]:
        return self._end

    def engage(
        self,
        lines: list,
        index: int,
        duration_seconds: Optional[float],
    ) -> bool:
        """Capture the current line's bounds: [line start, next line's
        start), or the track duration as the end bound for the last line.
        Returns False (nothing captured) when there is no current line or
        no usable end bound."""
        if index < 0 or index >= len(lines):
            return False
        start = lines[index][0]
        if index + 1 < len(lines):
            end = lines[index + 1][0]
        else:
            end = duration_seconds
        if end is None or end <= start:
            return False
        self._start, self._end = start, end
        return True

    def release(self) -> None:
        self._start = self._end = None

    def still_valid(self, position_seconds: Optional[float]) -> bool:
        """Keep-engaged check for each poll. False means the position left
        the graced bounds — the user seeked elsewhere (or the wrap seek
        failed) and the loop must cancel."""
        if not self.engaged or position_seconds is None:
            return True
        return (
            self._start - ENTRY_GRACE
            <= position_seconds
            <= self._end + EXIT_GRACE
        )

    def wrap_eta(
        self, position_seconds: float, playing: bool
    ) -> Optional[float]:
        """Seconds from now until the wrap seek should be DISPATCHED (the
        seek lead is already subtracted). None when nothing should be
        scheduled: not engaged, or paused — the loop lies dormant and
        resumes with playback."""
        if not self.engaged or not playing:
            return None
        return max(0.0, self._end - position_seconds - SEEK_LEAD_SECONDS)
