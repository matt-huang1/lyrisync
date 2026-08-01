"""When this app may ask LRCLIB again, after an answer it could not use.

Two rules live here and they are not the same rule, which is why they are
in one module rather than two: one is ours and one is theirs.

## Ours: the schedule

"lyrics unavailable, will retry" used to mean every 30 seconds, for ever.
That is the right cadence for a blip and the wrong one for an outage: a
song left on screen through a three hour outage made 360 requests of a
free service that was already having a bad day, and every one of them
asked the same question and got the same answer.

So the interval GROWS with the number of consecutive failures and stops at
a ceiling.

- The FIRST retry is still 30 seconds, unchanged. Most failures are a blip
  — a wifi handover, a connection the server closed — and the promise on
  screen is answered at the speed it always was.
- Then it doubles: 30, 60, 120, 240, and stops at 300.
- Any success resets it to zero.

MEASURED, both halves. Over a three hour outage the schedule makes 38
requests where a flat 30 seconds made 360, and the saving holds at other
lengths: 14 against 120 over an hour, 98 against 960 over eight. So it is
a 9x reduction in what a stuck song costs LRCLIB — 8.6x, 9.5x and 9.8x at
those three lengths, rising, because past the ceiling both schedules are
linear and the growing part is a smaller share of a longer outage. It
costs the user nothing in the common case, because the common case is the
first retry.

The CEILING is 300 seconds and that is measured too, against the thing
that actually decides how long a user waits. A track change re-attempts
immediately, whatever the schedule says — so the ceiling only governs a
song left up. Of 69 tracks across 5 real albums the median is 232s and 88%
run under 300s, which means for 88% of songs the next track beats the
ceiling to it. A longer ceiling would be a number that almost never
applies; a shorter one would be spending requests to be ready for a moment
that has already passed.

## Theirs: the hold

LRCLIB's API documentation asks callers to honour ``Retry-After`` on a 429
and says that ignoring it may result in a temporary ban. That is not a
schedule, it is an instruction, and it outranks everything above: the hold
is a floor under the interval, and while it stands NOTHING goes out — not
a retry, not a track change's first lookup, not the album warm.

It is here rather than in the retry timer because the retry timer is only
one of the ways a request leaves this app. A hold that only quietened the
retries would be a hold that a user skipping tracks walked straight
through, which is the case the ban exists for.

Pure and Qt-free, like every policy module here. The clock is passed in.
"""

from __future__ import annotations

import threading
from typing import Optional

# The first retry, unchanged from when it was the only one. A blip is the
# common case and this is the cadence that answers it.
BASE_SECONDS = 30.0

# Where the doubling stops. See the module docstring: 88% of 69 real album
# tracks are shorter than this, so for most songs the next track change
# re-attempts before the ceiling is ever reached.
CEILING_SECONDS = 300.0

# How fast it grows. Doubling rather than anything cleverer: it is the one
# growth rate that needs no justification of its own, and the two numbers
# above are what the schedule is actually made of.
GROWTH = 2.0


def delay(failures: int, asked_to_wait: Optional[float] = None) -> float:
    """How long to wait before asking again.

    ``failures`` is how many consecutive lookups have failed; 0 or 1 both
    mean "the first retry", so a caller that has not counted yet gets the
    interval this app has always had.

    ``asked_to_wait`` is LRCLIB's own ``Retry-After``, in seconds, when the
    last failure carried one. It raises the interval and never lowers it:
    their number is an instruction about the minimum, and ours may be
    longer because we have been failing for a while. Taking the larger is
    the only reading under which both are honoured.
    """
    grown = BASE_SECONDS * (GROWTH ** max(0, failures - 1))
    scheduled = min(CEILING_SECONDS, grown)
    if asked_to_wait is None:
        return scheduled
    return max(scheduled, float(asked_to_wait))


class Hold:
    """A pause LRCLIB asked for, and the fact that it is still running.

    One instant, not a queue: a second 429 arriving during a hold replaces
    it rather than adding to it, because what the server is telling us each
    time is when it will next be willing to answer.

    Locked, because the requests that set this and the requests that
    consult it are on different threads — the fallback chain runs its
    attempts concurrently and the album warm runs on a worker of its own.
    """

    def __init__(self) -> None:
        self._until: Optional[float] = None
        self._lock = threading.Lock()

    def asked_to_wait(self, seconds: float, now: float) -> None:
        """LRCLIB said to come back in ``seconds``. Nothing goes out until
        then. A non-positive or unreadable number is no hold at all, which
        is the honest reading of a header that did not say anything."""
        if seconds is None or seconds <= 0:
            return
        with self._lock:
            self._until = now + float(seconds)

    def remaining(self, now: float) -> float:
        """Seconds left of the hold, or 0.0 when there is none.

        The number and not just the boolean, because a request refused
        here has to be able to say how long it is refused for: the window
        puts that on the retry schedule so it does not spend the whole
        pause asking and being turned away.
        """
        with self._lock:
            until = self._until
            if until is None:
                return 0.0
            left = until - now
            if left <= 0:
                self._until = None
                return 0.0
            return left

    def clear(self) -> None:
        """Forget the hold. For the suite, which must not carry one test's
        429 into the next test's lookup."""
        with self._lock:
            self._until = None
