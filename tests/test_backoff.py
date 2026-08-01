"""How long before this app asks LRCLIB again.

Two rules, and the tests are in two halves because the rules are: the
schedule is ours and the hold is theirs. What matters most here is the
arithmetic that justified the numbers — the request counts and the
ceiling — because those are the claims the module docstring makes and the
only place they can be checked is against the schedule itself.
"""

TIER = "unit"  # Qt-free logic, called directly

from sottovoce import backoff


# -- the schedule ----------------------------------------------------------


def test_the_first_retry_is_the_one_this_app_has_always_had():
    """Most failures are a blip and the promise on screen says "will
    retry". Growing the interval must not make the common case slower."""
    assert backoff.delay(0) == 30.0
    assert backoff.delay(1) == 30.0


def test_it_doubles_and_then_stops():
    assert [backoff.delay(n) for n in range(1, 6)] == [30.0, 60.0, 120.0, 240.0, 300.0]
    assert backoff.delay(9) == backoff.CEILING_SECONDS
    assert backoff.delay(500) == backoff.CEILING_SECONDS


def test_the_schedule_never_runs_backwards():
    """Every step is at least as long as the one before it. The property
    rather than the list, so a different growth rate would still have to
    be a backoff."""
    intervals = [backoff.delay(n) for n in range(0, 40)]
    assert intervals == sorted(intervals)


def test_what_a_three_hour_outage_costs_lrclib():
    """The measurement the module is built on, checked rather than quoted.

    A song left on screen through an outage used to make one request every
    30 seconds for as long as it lasted. 38 against 360 is the whole
    argument for the schedule existing.
    """
    assert retries_over(3 * 3600) == 38
    assert int(3 * 3600 // 30) == 360


def test_the_saving_holds_at_other_lengths():
    """A ratio that only appeared at one outage length would be a number
    chosen to look good. It is 8.6x over an hour, 9.5x over three and 9.8x
    over eight — rising, because past the ceiling both schedules are
    linear and the growing part is a smaller share of a longer outage.
    """
    savings = {}
    for hours in (1, 3, 8):
        seconds = hours * 3600
        savings[hours] = (seconds // 30) / retries_over(seconds)
    assert round(savings[1], 1) == 8.6
    assert round(savings[3], 1) == 9.5
    assert round(savings[8], 1) == 9.8


def test_the_ceiling_is_shorter_than_a_song():
    """Why 300 seconds and not more. A track change re-attempts at once,
    whatever the schedule says, so the ceiling only ever decides how long a
    song LEFT UP waits — and of 69 tracks across 5 real albums the median
    is 232s and 88% are under 300s. A longer ceiling would be a number that
    almost never applies.
    """
    assert backoff.CEILING_SECONDS == 300.0
    assert backoff.CEILING_SECONDS > 232.0  # the measured median


def retries_over(seconds: float) -> int:
    """How many retries the schedule makes in an outage of this length."""
    made = 0
    elapsed = 0.0
    while True:
        elapsed += backoff.delay(made + 1)
        if elapsed > seconds:
            return made
        made += 1


# -- what LRCLIB asked for -------------------------------------------------


def test_retry_after_raises_the_interval_and_never_lowers_it():
    """Their number is an instruction about the minimum and ours may be
    longer, so both are honoured only by taking the larger."""
    assert backoff.delay(1, asked_to_wait=120.0) == 120.0
    assert backoff.delay(1, asked_to_wait=5.0) == 30.0, "we may be more polite"
    assert backoff.delay(9, asked_to_wait=60.0) == 300.0
    assert backoff.delay(9, asked_to_wait=9000.0) == 9000.0, "a ban is worse"


def test_a_hold_refuses_until_it_is_over():
    hold = backoff.Hold()
    assert hold.remaining(now=100.0) == 0.0

    hold.asked_to_wait(30.0, now=100.0)

    assert hold.remaining(now=100.0) == 30.0
    assert hold.remaining(now=120.0) == 10.0
    assert hold.remaining(now=130.0) == 0.0
    assert hold.remaining(now=1e9) == 0.0


def test_a_second_429_replaces_the_hold_rather_than_extending_it():
    """What the server sends each time is when it will next be willing to
    answer, not another sentence to serve after the first."""
    hold = backoff.Hold()
    hold.asked_to_wait(300.0, now=0.0)
    hold.asked_to_wait(10.0, now=0.0)
    assert hold.remaining(now=0.0) == 10.0


def test_a_header_that_said_nothing_is_no_hold():
    """Retry-After is optional and may be unreadable. Refusing every
    request on the strength of a header nobody sent would be an app that
    stopped working because of a number it invented."""
    hold = backoff.Hold()
    hold.asked_to_wait(0.0, now=0.0)
    assert hold.remaining(now=0.0) == 0.0
    hold.asked_to_wait(-5.0, now=0.0)
    assert hold.remaining(now=0.0) == 0.0


def test_a_hold_can_be_dropped():
    hold = backoff.Hold()
    hold.asked_to_wait(600.0, now=0.0)
    hold.clear()
    assert hold.remaining(now=0.0) == 0.0
