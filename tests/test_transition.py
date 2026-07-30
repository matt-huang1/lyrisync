"""The line-change dedupe, on its own.

No Qt and no timers here: the bug this rule fixes is about identity, not
about timing, so the rule is testable as a rule. The window tests then
check that it is actually consulted on the paths a poll takes.
"""

from sottovoce.transition import LineTransition

LEAD = 0.82  # the choreography (520ms) plus one poll interval


def transition():
    return LineTransition(LEAD)


# -- claiming a change ----------------------------------------------------


def test_nothing_is_in_flight_to_begin_with():
    assert transition().target is None


def test_the_first_trigger_for_a_line_claims_it():
    t = transition()
    assert t.begin(4) is True
    assert t.target == 4


def test_a_repeat_trigger_for_the_same_line_is_refused():
    """The whole point: a transition to a line runs once. A second
    trigger — a re-armed timer, a poll landing mid-flight — must be able
    to tell that it is a repeat and do nothing."""
    t = transition()
    t.begin(4)
    assert t.begin(4) is False
    assert t.begin(4) is False
    assert t.target == 4


def test_the_next_line_is_a_different_change():
    t = transition()
    t.begin(4)
    assert t.begin(5) is True
    assert t.target == 5


def test_clearing_lets_the_very_same_line_be_claimed_again():
    """A cancelled change is not a change that happened. Every path that
    means "the world moved" clears, and the same line must then be free to
    be scheduled from scratch — otherwise a seek back over a line would
    leave it unable to animate ever again."""
    t = transition()
    t.begin(4)
    t.clear()
    assert t.target is None
    assert t.begin(4) is True


# -- arming the timers ----------------------------------------------------


def test_the_timers_may_be_rearmed_until_the_movement_begins():
    """Every poll re-derives the schedule from a fresh position, which is
    how a seek is picked up within one poll interval."""
    t = transition()
    assert t.may_arm(4) is True
    assert t.may_arm(4) is True


def test_the_timers_are_settled_once_the_movement_begins():
    t = transition()
    t.begin(4)
    assert t.may_arm(4) is False


def test_a_change_in_flight_does_not_block_the_line_after_it():
    t = transition()
    t.begin(4)
    assert t.may_arm(5) is True


# -- the screen legitimately ahead of the player --------------------------


def test_the_line_this_change_owns_may_be_shown_early():
    """The predicted swap puts the next line up before the player reaches
    it, on purpose. Reading that as a missed prediction is what snapped
    the display back and played the change a second time."""
    t = transition()
    t.begin(4)
    assert t.leads(4, target_seconds=10.0, position_seconds=9.8) is True


def test_a_line_nobody_claimed_is_never_ahead_on_purpose():
    assert transition().leads(4, 10.0, 9.8) is False


def test_a_different_line_is_never_this_change():
    t = transition()
    t.begin(4)
    assert t.leads(7, 10.0, 9.8) is False


def test_being_ahead_stops_standing_once_the_player_is_far_from_the_line():
    """A seek back into the middle of the line before it. Without this
    bound the screen would sit on a line the song has not reached, waiting
    for a timestamp half a verse away."""
    t = transition()
    t.begin(4)
    assert t.leads(4, 10.0, 10.0 - LEAD + 0.01) is True
    assert t.leads(4, 10.0, 10.0 - LEAD - 0.01) is False


def test_the_player_arriving_at_the_line_is_still_this_change():
    """Position past the timestamp: the prediction was simply right, and
    the view model is a poll away from agreeing."""
    t = transition()
    t.begin(4)
    assert t.leads(4, 10.0, 10.2) is True
