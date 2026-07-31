import pytest

from sottovoce.loop import (
    ENTRY_GRACE,
    EXIT_GRACE,
    SEEK_LEAD_SECONDS,
    LineLoop,
    LoopPhase,
)

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


# -- echo practice phase machine -----------------------------------------


def echo_loop(index=1):
    loop = engaged_loop(index)
    loop.echo = True
    return loop


def test_echo_off_end_reached_seeks_as_today():
    loop = engaged_loop(1)
    assert loop.on_end_reached() == "seek"
    assert loop.phase is LoopPhase.LISTEN


def test_echo_on_end_reached_enters_attempt():
    loop = echo_loop(1)
    assert loop.phase is LoopPhase.LISTEN  # engage starts listening
    assert loop.on_end_reached() == "attempt"
    assert loop.phase is LoopPhase.ATTEMPT


def test_attempt_persists_until_user_finishes():
    loop = echo_loop(1)
    loop.on_end_reached()
    # No timeout: any number of state observations later, still ATTEMPT.
    for _ in range(10):
        loop.observe_state(playing=False)
    assert loop.phase is LoopPhase.ATTEMPT
    loop.finish_attempt()  # only the user's click ends it
    assert loop.phase is LoopPhase.LISTEN


def test_listen_attempt_listen_cycles():
    loop = echo_loop(1)
    for _ in range(3):
        assert loop.on_end_reached() == "attempt"
        assert loop.phase is LoopPhase.ATTEMPT
        loop.finish_attempt()
        assert loop.phase is LoopPhase.LISTEN


def test_wrap_eta_suspended_during_attempt():
    loop = echo_loop(1)
    loop.on_end_reached()
    # Even if a stray PLAYING poll arrives before our pause lands, the
    # wrap scheduler must stay quiet — the user's click owns this phase.
    assert loop.wrap_eta(29.9, playing=True) is None
    loop.finish_attempt()
    assert loop.wrap_eta(20.0, playing=True) is not None


def test_release_during_attempt_resets_phase():
    loop = echo_loop(1)
    loop.on_end_reached()
    loop.release()
    assert not loop.engaged
    assert loop.phase is LoopPhase.LISTEN
    # Fresh engage starts a clean LISTEN with no stale pause bookkeeping.
    assert loop.engage(LINES, 1, DURATION)
    assert loop.phase is LoopPhase.LISTEN
    assert loop.observe_state(playing=True) == "ok"


def test_release_during_listen_is_plain_release():
    loop = echo_loop(1)
    loop.release()
    assert not loop.engaged


def test_requested_pause_is_not_external():
    loop = echo_loop(1)
    loop.on_end_reached()
    # Poll lag: PLAYING may still be observed before our pause lands.
    assert loop.observe_state(playing=True) == "ok"
    # Our requested pause arrives: confirmed, still fine.
    assert loop.observe_state(playing=False) == "ok"
    assert loop.observe_state(playing=False) == "ok"


def test_external_play_mid_attempt_cancels():
    loop = echo_loop(1)
    loop.on_end_reached()
    loop.observe_state(playing=False)  # our pause confirmed
    assert loop.observe_state(playing=True) == "external_play"


def test_observe_state_quiet_outside_attempt():
    loop = echo_loop(1)  # LISTEN
    assert loop.observe_state(playing=False) == "ok"  # user pause: dormancy
    assert loop.observe_state(playing=True) == "ok"
    assert LineLoop().observe_state(playing=True) == "ok"  # not engaged


def test_attempt_position_stays_within_bounds():
    loop = echo_loop(1)
    loop.on_end_reached()
    # Paused position freezes around the end bound (pause lands with some
    # latency): must not read as a user seek-away.
    assert loop.still_valid(30.2)


def test_auto_cancel_rules_unchanged_in_echo_mode():
    loop = echo_loop(1)
    assert loop.still_valid(30.0 + EXIT_GRACE + 0.5) is False
    assert loop.still_valid(20.0 - ENTRY_GRACE - 0.5) is False


# -- one wrap at a time --------------------------------------------------
#
# The wrap is dispatched a seek lead BEFORE the end bound, and the round
# trip that carries it takes most of a poll interval. Every position that
# arrives in between is still inside the lead, so wrap_eta clamps to zero
# and the wrap goes out again — and again on the next poll. Measured
# against a 10 second line looped for 45 seconds: 7 to 8 seeks where 4
# were wanted. What that sounds like is the line restarting, playing for a
# round trip, and restarting again.


def dispatched(loop):
    """One turn of the scheduler at the end bound: what the caller does."""
    return loop.on_end_reached()


def test_a_wrap_already_on_its_way_is_not_dispatched_again():
    loop = engaged_loop(1)  # [20, 30), so the wrap goes out at 29.54
    assert dispatched(loop) == "seek"
    # The seek is in flight: the positions that arrive are the ones that
    # armed it in the first place, and they must arm nothing.
    for position in (29.54, 29.7, 29.9, 30.1):
        loop.observe_position(position)
        assert loop.wrap_eta(position, playing=True) is None
        assert dispatched(loop) == "none"


def test_the_wrap_landing_frees_the_next_one():
    loop = engaged_loop(1)
    assert dispatched(loop) == "seek"
    loop.observe_position(20.0)  # it landed
    assert loop.wrap_eta(20.0, playing=True) == pytest.approx(
        10.0 - SEEK_LEAD_SECONDS
    )
    assert dispatched(loop) == "seek"


def test_only_a_position_before_the_dispatch_point_counts_as_landing():
    """A seek is the only thing that moves a position backwards, so that
    is what the wrap landing looks like. The line simply playing on does
    not, however many polls it takes."""
    loop = engaged_loop(1)
    dispatched(loop)
    for position in (29.6, 29.8, 30.0, 30.4):  # still running forward
        loop.observe_position(position)
    assert dispatched(loop) == "none"
    loop.observe_position(29.53)  # one hundredth before the dispatch point
    assert dispatched(loop) == "seek"


def test_a_wrap_that_never_lands_dispatches_nothing_more():
    """A failed seek has always surfaced as the position drifting out of
    the bounds, which cancels the loop. It must not surface as a seek a
    poll, for ever."""
    loop = engaged_loop(1)
    assert dispatched(loop) == "seek"
    for position in (29.8, 30.3, 30.8, 31.1):
        loop.observe_position(position)
        assert dispatched(loop) == "none"
    assert loop.still_valid(31.1) is False  # and this is what ends it


def test_observe_position_ignores_an_unknown_position():
    loop = engaged_loop(1)
    dispatched(loop)
    loop.observe_position(None)  # a debounced blip poll
    assert dispatched(loop) == "none"


def test_a_pending_wrap_does_not_survive_release_or_a_fresh_engage():
    loop = engaged_loop(1)
    dispatched(loop)
    loop.release()
    assert loop.engage(LINES, 1, DURATION)
    assert loop.wrap_eta(20.0, playing=True) is not None
    assert dispatched(loop) == "seek"


def test_echo_never_has_a_wrap_in_flight():
    """The attempt phase already suppresses the scheduler from the first
    wrap onward, and the caller pauses rather than seeking, so echo was
    never exposed to this. Proved rather than assumed: the pending flag
    must not leak into a phase that has its own rule."""
    loop = echo_loop(1)
    assert loop.on_end_reached() == "attempt"
    assert loop.wrap_eta(29.9, playing=True) is None  # the ATTEMPT rule
    loop.finish_attempt()
    assert loop.wrap_eta(20.0, playing=True) is not None
    assert loop.on_end_reached() == "attempt"
