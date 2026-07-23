import pytest

from lyrisync.loop import ENTRY_GRACE, EXIT_GRACE, SEEK_LEAD_SECONDS, LineLoop

LINES = [(10.0, "one"), (20.0, "two"), (30.0, "three")]
DURATION = 200.0


def engaged_loop(index=1):
    loop = LineLoop()
    assert loop.engage(LINES, index, DURATION)
    return loop


# -- bounds capture ------------------------------------------------------


def test_engage_captures_line_bounds():
    loop = engaged_loop(1)
    assert (loop.start, loop.end) == (20.0, 30.0)


def test_engage_first_line():
    loop = engaged_loop(0)
    assert (loop.start, loop.end) == (10.0, 20.0)


def test_engage_last_line_uses_track_duration():
    loop = engaged_loop(2)
    assert (loop.start, loop.end) == (30.0, DURATION)


@pytest.mark.parametrize(
    ("lines", "index", "duration"),
    [
        (LINES, -1, DURATION),      # before the first line: nothing to loop
        (LINES, 3, DURATION),       # out of range
        ([], 0, DURATION),          # no lyrics
        (LINES, 2, None),           # last line but unknown duration
        (LINES, 2, 30.0),           # degenerate: end == start
        ([(10.0, "a"), (10.0, "b")], 0, DURATION),  # duplicate timestamps
    ],
)
def test_engage_rejects_unloopable(lines, index, duration):
    loop = LineLoop()
    assert loop.engage(lines, index, duration) is False
    assert not loop.engaged


def test_release_disengages():
    loop = engaged_loop()
    loop.release()
    assert not loop.engaged
    assert loop.wrap_eta(25.0, playing=True) is None


# -- anticipatory wrap scheduling ----------------------------------------


def test_wrap_eta_counts_down_to_end_bound():
    loop = engaged_loop(1)  # [20, 30)
    assert loop.wrap_eta(27.0, playing=True) == pytest.approx(
        3.0 - SEEK_LEAD_SECONDS
    )


def test_wrap_eta_never_negative():
    loop = engaged_loop(1)
    assert loop.wrap_eta(29.99, playing=True) == 0.0


def test_wrap_eta_dormant_while_paused():
    loop = engaged_loop(1)
    assert loop.wrap_eta(25.0, playing=False) is None
    assert loop.engaged  # pause does NOT cancel
    # resumes with playback
    assert loop.wrap_eta(25.0, playing=True) is not None


def test_wrap_eta_none_when_not_engaged():
    assert LineLoop().wrap_eta(25.0, playing=True) is None


# -- auto-cancel: seeks outside the bounds -------------------------------


def test_positions_inside_line_stay_valid():
    loop = engaged_loop(1)  # [20, 30)
    for position in (20.0, 25.0, 29.9):
        assert loop.still_valid(position)


def test_wrap_landing_at_start_stays_valid():
    loop = engaged_loop(1)
    assert loop.still_valid(20.0)


def test_overshoot_within_grace_stays_valid():
    loop = engaged_loop(1)
    assert loop.still_valid(30.0 + EXIT_GRACE - 0.1)  # seek latency overshoot


def test_seek_forward_out_of_line_cancels():
    loop = engaged_loop(1)
    assert loop.still_valid(30.0 + EXIT_GRACE + 0.5) is False


def test_seek_backward_out_of_line_cancels():
    loop = engaged_loop(1)
    assert loop.still_valid(20.0 - ENTRY_GRACE - 0.5) is False


def test_unknown_position_does_not_cancel():
    loop = engaged_loop(1)
    assert loop.still_valid(None)  # e.g. debounced blip poll
