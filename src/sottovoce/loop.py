"""Line-loop practice controller: repeat the current lyric line by seeking
back to its start whenever playback reaches its end.

Pure logic, Qt-free like geometry.py. The controller owns the loop bounds
and every keep-or-cancel decision; the caller owns timers and performs the
actual seek (a subprocess call).
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class LoopPhase(Enum):
    LISTEN = "listen"    # the line is playing
    ATTEMPT = "attempt"  # echo mode: playback paused, the user's turn

# A position this far before the loop start means the user seeked away
# backwards; this far past the end means they seeked forward or our wrap
# seek failed. Either way the user has voted — cancel.
ENTRY_GRACE = 0.5
EXIT_GRACE = 1.0

# Dispatch the wrap seek this early: the osascript write takes ~150-200ms,
# so firing at the end bound exactly would bleed the next line through.
SEEK_LEAD_SECONDS = 0.46


class LineLoop:
    def __init__(self) -> None:
        self._start: Optional[float] = None
        self._end: Optional[float] = None
        # Echo practice: when True, reaching the end bound enters a silent
        # ATTEMPT phase instead of seeking straight back.
        self.echo = False
        self._phase = LoopPhase.LISTEN
        self._pause_confirmed = False
        # A wrap seek that has been dispatched and has not been seen to
        # land, and the position it was dispatched from. See
        # observe_position.
        self._wrap_pending = False
        self._wrap_from = 0.0

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
        self._phase = LoopPhase.LISTEN
        self._pause_confirmed = False
        self._wrap_pending = False
        return True

    def release(self) -> None:
        self._start = self._end = None
        self._phase = LoopPhase.LISTEN
        self._pause_confirmed = False
        self._wrap_pending = False

    @property
    def phase(self) -> LoopPhase:
        return self._phase

    def on_end_reached(self) -> str:
        """Decision when playback reaches the end bound. Returns "seek"
        (plain loop: jump back to the start), "attempt" (echo mode: the
        caller pauses playback and waits for the user), or "none" —
        including when the wrap this one would dispatch is already on its
        way (see ``observe_position``)."""
        if not self.engaged or self._wrap_pending:
            return "none"
        if self.echo:
            self._phase = LoopPhase.ATTEMPT
            self._pause_confirmed = False
            return "attempt"
        # The scheduler runs this timer for exactly `end - position - lead`
        # seconds, so the position it fires at is the end bound less the
        # lead, whatever the position that armed it was.
        self._wrap_pending = True
        self._wrap_from = self._end - SEEK_LEAD_SECONDS
        return "seek"

    def observe_position(self, position_seconds: Optional[float]) -> None:
        """Feed each poll's position, so the controller can tell the wrap
        seek IT dispatched from the line simply playing on.

        The counterpart to ``observe_state``, and it exists because the
        wrap is dispatched a seek lead BEFORE the end bound while the
        round trip that carries it takes most of a poll interval. Every
        position that arrives in between is still inside the lead, so
        ``wrap_eta`` clamps to zero and the wrap is dispatched again at
        once — and again on the next poll. Measured against a 10 second
        line looped for 45 seconds: 7 to 8 seeks where 4 were wanted, the
        extra one landing a round trip after the first and restarting a
        line that had already restarted. That is what the wrap sounded
        like.

        A seek is the only thing that moves a position backwards, so a
        position earlier than the one the wrap was dispatched from IS the
        wrap landing. A wrap that never lands is never replaced either:
        the position runs on past the end bound and ``still_valid``
        cancels the loop, which is what a failed seek has always done.
        """
        if not self._wrap_pending or position_seconds is None:
            return
        if position_seconds < self._wrap_from:
            self._wrap_pending = False

    def finish_attempt(self) -> None:
        """User-paced: the attempt ends only when the user says so (the 🎤
        click). Back to LISTEN; the caller seeks to the start bound and
        resumes playback. Until then, silence is a valid resting state —
        there is no timeout."""
        self._phase = LoopPhase.LISTEN
        self._pause_confirmed = False

    def observe_state(self, playing: bool) -> str:
        """Feed observed play-state transitions so the controller can tell
        the pause IT requested from external fiddling. Returns "ok", or
        "external_play" when playback came back mid-ATTEMPT from outside —
        the caller should cancel (documented: no heroic reconciliation)."""
        if not self.engaged or self._phase is not LoopPhase.ATTEMPT:
            return "ok"
        if not playing:
            self._pause_confirmed = True  # our requested pause landed
            return "ok"
        if self._pause_confirmed:
            return "external_play"
        return "ok"  # our pause is still in flight; not external

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
        scheduled: not engaged, paused (dormant — resumes with playback),
        mid-ATTEMPT (the attempt timer owns the phase), or with a wrap
        already on its way (``observe_position``)."""
        if not self.engaged or not playing:
            return None
        if self._phase is LoopPhase.ATTEMPT or self._wrap_pending:
            return None
        return max(0.0, self._end - position_seconds - SEEK_LEAD_SECONDS)
