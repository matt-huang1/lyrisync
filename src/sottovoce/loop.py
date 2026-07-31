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

# -- how early to dispatch the wrap seek ------------------------------------
#
# The wrap is dispatched before the line's end bound so that the seek
# LANDS on it, which makes the lead one thing and one thing only: how long
# a command to Spotify takes. It was a constant, 0.46s, inherited from the
# osascript era when the write was measured at 150-200ms; in-process the
# same command was measured between 133ms and a full second in one
# session, because it queues on the one lock behind whatever the monitor
# happens to be asking. A constant against that fires early nearly always
# and late the rest of the time, and the error is the whole difference
# between the lead and the round trip: at a real 133ms a 0.46s lead cuts
# 327ms off the end of every line.
#
# So the lead is the recent round trips, and the app measures its own.

# The first wrap of a session has nothing to go on. 0.20s: the in-process
# round trip is measured at 133ms and a command can wait a whole query
# behind the lock, so a first guess of one and a half round trips errs
# early rather than late — early truncates the tail of the line, late
# bleeds the next line through, and the next wrap has a measurement.
SEEK_LEAD_START = 0.20

# A lead below this is not worth having: the dispatch itself has to reach
# a worker thread. Above the ceiling the lead is eating the line rather
# than protecting it, and a round trip that slow is one `still_valid`
# is about to cancel the loop over anyway (EXIT_GRACE).
SEEK_LEAD_FLOOR = 0.05
SEEK_LEAD_CEILING = EXIT_GRACE

# How many recent round trips the lead is taken over. The statistic is the
# MEDIAN, which is the whole of the outlier handling and needs no
# threshold to argue about: one command that queued behind a slow query
# moves the middle of eight samples by nothing at all, and it takes five
# of them agreeing before the lead follows. Eight is about three wraps of
# a ten second line, so the lead tracks a machine that has genuinely got
# slower within a verse and ignores a single hiccup entirely.
SEEK_LEAD_SAMPLES = 8


def seek_lead(round_trips: tuple = ()) -> float:
    """How early to dispatch the wrap seek, given what recent commands to
    the player actually cost.

    Pure, and given the numbers rather than going to look for them: the
    measuring belongs to whoever owns the channel (`player_monitor`), and
    the policy belongs here.
    """
    usable = [
        seconds for seconds in round_trips[-SEEK_LEAD_SAMPLES:] if seconds >= 0
    ]
    if not usable:
        return SEEK_LEAD_START
    ordered = sorted(usable)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return min(SEEK_LEAD_CEILING, max(SEEK_LEAD_FLOOR, median))


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
        # The last position seen while the line was simply playing. What
        # the wrap landing is measured against.
        self._seen: Optional[float] = None
        # What recent commands to the player cost, newest last. Handed in
        # rather than fetched, so this stays pure.
        self._round_trips: tuple = ()

    def observe_round_trips(self, round_trips) -> None:
        """Feed how long this app's recent player commands took."""
        self._round_trips = tuple(round_trips)

    @property
    def lead(self) -> float:
        """How early the wrap seek goes out, at the moment it is asked."""
        return seek_lead(self._round_trips)

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
        self._seen = None
        return True

    def release(self) -> None:
        self._start = self._end = None
        self._phase = LoopPhase.LISTEN
        self._pause_confirmed = False
        self._wrap_pending = False
        self._seen = None

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
        # The threshold a position has to fall below to count as the wrap
        # landing, and it is the last position actually SEEN rather than
        # `end - lead`, which is only where the scheduler meant to fire.
        # The two came apart the moment the lead stopped being a constant:
        # a lead measured shorter between arming the timer and it going
        # off puts `end - lead` LATER than the position the wrap really
        # went out at, so the very next poll reads ordinary playback as
        # the seek landing and dispatches a second one. Measured, at a
        # 0.10s round trip: two extra seeks in five wraps. Playback only
        # ever moves forward, so nothing playing on can fall below where
        # the line already was, and the seek to the start bound is far
        # below it.
        self._wrap_pending = True
        self._wrap_from = (
            self._seen if self._seen is not None else self._end - self.lead
        )
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
        if position_seconds is None:
            return
        if self._wrap_pending and position_seconds < self._wrap_from:
            self._wrap_pending = False
        if not self._wrap_pending:
            # Only while the line is playing on: a position that arrives
            # with a wrap still out is one the wrap has not answered yet,
            # and moving the threshold up to meet it would be the loop
            # agreeing with itself.
            self._seen = position_seconds

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
        return max(0.0, self._end - position_seconds - self.lead)
