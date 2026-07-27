"""Tap-to-sync: turn plain lyrics into timed ones, one tap per line.

Pure logic, Qt-free like loop.py and geometry.py. The session owns the
line list, the cursor, and the stamps recorded so far; the caller owns the
clock, the player, and the widgets.

Two corrections stand between "when the click landed" and "when the line
actually started":

- *Interpolation*. Playback position is only known as of the last poll
  (~300ms apart), and the UI thread must never run a subprocess to ask for
  a fresher one. ``interpolated_position`` advances the last known position
  by the wall-clock time since that poll landed.
- *Reaction offset*. A human taps after hearing the line begin, so every
  stamp is late by roughly a reaction time. ``SYNC_REACTION_OFFSET_SECONDS``
  is subtracted back off, clamped so timestamps never go negative and never
  run backwards past the previous line.
"""

from __future__ import annotations

from typing import Optional

# Subtracted from every stamp to cancel the tap's reaction lag. Tuning
# knob: raise it if your syncs consistently land late, lower it if lines
# appear before they are sung.
SYNC_REACTION_OFFSET_SECONDS = 0.25

# Ceiling on how far a stale poll may be extrapolated. Polls are ~300ms
# apart, so anything beyond this means the poll loop stalled (a slow
# osascript, a wedged Spotify) and guessing further would invent a
# position rather than refine one.
MAX_EXTRAPOLATION_SECONDS = 2.0


def sync_targets(plain_text: str) -> list[str]:
    """The lines a sync pass will stamp: every non-blank line of the plain
    lyrics, in order. Blank lines are structure, not tap targets."""
    return [line.strip() for line in plain_text.splitlines() if line.strip()]


def interpolated_position(
    position_seconds: Optional[float],
    polled_at: Optional[float],
    now: float,
    playing: bool = True,
) -> Optional[float]:
    """Playback position right now, estimated from the last poll.

    ``polled_at`` and ``now`` are readings of the same monotonic clock.
    Returns None when there is nothing to interpolate from. While paused
    the position does not advance, so the last reading stands.
    """
    if position_seconds is None:
        return None
    if not playing or polled_at is None:
        return position_seconds
    elapsed = min(MAX_EXTRAPOLATION_SECONDS, max(0.0, now - polled_at))
    return position_seconds + elapsed


def format_timestamp(seconds: float) -> str:
    """One LRC stamp: ``[mm:ss.xx]``, centiseconds. Rounding happens once,
    in centiseconds, so 59.999s carries into the next minute instead of
    rendering as ``[00:60.00]``."""
    centis = max(0, round(seconds * 100))
    minutes, within_minute = divmod(centis, 6000)
    return f"[{minutes:02d}:{within_minute // 100:02d}.{within_minute % 100:02d}]"


class SyncSession:
    """One tap-to-sync pass over a song's lines.

    The cursor is implied by the stamps: line *n* is next to stamp exactly
    when *n* stamps have been recorded. ``undo`` drops the last stamp,
    which steps the cursor back with it.
    """

    def __init__(
        self,
        lines: list[str],
        offset: float = SYNC_REACTION_OFFSET_SECONDS,
    ) -> None:
        self._lines = list(lines)
        self._offset = offset
        self._stamps: list[float] = []

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    @property
    def stamps(self) -> list[float]:
        return list(self._stamps)

    @property
    def total(self) -> int:
        return len(self._lines)

    @property
    def index(self) -> int:
        """Index of the line waiting to be stamped; ``total`` when done."""
        return len(self._stamps)

    @property
    def is_complete(self) -> bool:
        return bool(self._lines) and len(self._stamps) == len(self._lines)

    @property
    def current(self) -> str:
        """The line to stamp next, or "" once every line is stamped."""
        index = self.index
        return self._lines[index] if index < len(self._lines) else ""

    def upcoming(self, count: int = 2) -> list[str]:
        """The next ``count`` lines after the current one, fewer near the
        end of the song."""
        start = self.index + 1
        return self._lines[start : start + max(0, count)]

    def stamp(self, position_seconds: float) -> bool:
        """Record ``position_seconds`` for the current line and advance.

        Returns False when there is nothing left to stamp. The reaction
        offset is applied here, clamped to [0, ...] and to at least the
        previous stamp so the timeline never runs backwards.
        """
        if self.is_complete or not self._lines:
            return False
        value = max(0.0, position_seconds - self._offset)
        if self._stamps:
            value = max(value, self._stamps[-1])
        self._stamps.append(value)
        return True

    def undo(self) -> bool:
        """Remove the last stamp and step back one line. Returns False when
        nothing has been stamped yet."""
        if not self._stamps:
            return False
        self._stamps.pop()
        return True

    def to_lrc(self) -> str:
        """The stamped lines as LRC text, in order. Lines not yet stamped
        are simply absent — v1 only ever saves a complete pass."""
        return "".join(
            f"{format_timestamp(stamp)} {line}\n"
            for stamp, line in zip(self._stamps, self._lines)
        )
