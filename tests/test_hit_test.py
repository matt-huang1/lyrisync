"""Where a press lands, and where the user thought it would.

The two are the same question until something draws the window somewhere
other than where Qt lays it out, which the flight does: a CALayer affine
transform on the view, invisible to Qt's own hit testing. These are the
rules that name the gap when there is one, so a press trace can say
"MISSED: layer_transform" rather than printing numbers and leaving it to
whoever reads the log.
"""

TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce.hit_test import (
    IDENTITY,
    Control,
    Transform,
    aimed_at,
    control_at,
    diagnose,
    drawn_at,
)

LOOP = Control("loop", (400, 10, 30, 30))
MIC = Control("attempt", (10, 24, 30, 30))
CONTROLS = [LOOP, MIC]


# -- the transform -------------------------------------------------------


def test_no_transform_is_the_identity():
    assert IDENTITY.is_identity is True


def test_a_transform_that_only_rounds_is_still_the_identity():
    """A float comparison against exactly 1.0 would report the rounding of
    an identity as a transform, and every press would come back suspect."""
    assert Transform(a=1.0000001, d=0.9999999, tx=0.0001).is_identity is True


@pytest.mark.parametrize(
    "transform",
    [
        Transform(a=0.5, d=0.5),          # mid-flight
        Transform(tx=40.0),               # translated
        Transform(a=1.0, d=1.0, ty=-12.0),
    ],
)
def test_a_transform_that_moves_a_press_is_not_the_identity(transform):
    assert transform.is_identity is False


def test_drawn_and_aimed_are_inverses():
    transform = Transform(a=0.5, d=0.5, tx=115.0, ty=55.0)
    assert aimed_at(drawn_at((400, 24), transform), transform) == pytest.approx(
        (400, 24)
    )


def test_a_transform_scaled_to_nothing_answers_the_point_itself():
    """Where the flight ends. There is no inverse and nothing visible to
    have aimed at, so the honest answer is the point rather than a
    division by zero."""
    assert aimed_at((10, 10), Transform(a=0.0, d=0.0)) == (10, 10)


# -- which control ------------------------------------------------------


def test_the_control_under_the_point():
    assert control_at((410, 20), CONTROLS) == "loop"
    assert control_at((20, 30), CONTROLS) == "attempt"


def test_nothing_under_the_point_is_none():
    assert control_at((200, 200), CONTROLS) is None


def test_a_control_that_is_not_on_the_window_is_not_under_anything():
    """The whole of what the compact layout's reveal does at zero, and the
    single most common reason a press reaches the window instead."""
    hidden = Control("loop", (400, 10, 30, 30), visible=False)
    assert control_at((410, 20), [hidden]) is None


def test_a_disabled_control_takes_a_press_and_drops_it():
    off = Control("loop", (400, 10, 30, 30), enabled=False)
    assert control_at((410, 20), [off]) is None


def test_the_topmost_control_wins():
    """Last wins, because that is the order the window raises them in."""
    under = Control("under", (0, 0, 100, 100))
    over = Control("over", (0, 0, 100, 100))
    assert control_at((50, 50), [under, over]) == "over"


# -- the verdict ---------------------------------------------------------


def test_a_press_with_nothing_in_the_way_landed():
    verdict = diagnose((410, 20), CONTROLS)
    assert verdict.hit == "loop"
    assert verdict.aimed == "loop"
    assert verdict.landed is True
    assert verdict.refusal is None
    assert verdict.offset == (0.0, 0.0)


def test_a_press_on_bare_chrome_landed_on_nothing_and_that_is_not_a_miss():
    """Both answers are None and they agree, which is what a press on the
    window between the controls IS. Reading that as a failure would make
    every drag look like a bug."""
    verdict = diagnose((200, 200), CONTROLS)
    assert (verdict.hit, verdict.aimed) == (None, None)
    assert verdict.landed is True


def test_a_layer_transform_names_itself():
    """The window is drawn at half size about its centre, so the pixels of
    the loop button are nowhere near where Qt will hit-test for it."""
    transform = Transform(a=0.5, d=0.5, tx=115.0, ty=27.5)
    # Where the loop button is actually drawn, and so where a hand aims.
    point = drawn_at((415, 25), transform)
    verdict = diagnose(point, CONTROLS, transform)
    assert verdict.aimed == "loop"
    assert verdict.hit != "loop"
    assert verdict.landed is False
    assert verdict.refusal == "layer_transform"


def test_the_offset_is_how_far_paint_is_from_hit_testing():
    transform = Transform(tx=40.0, ty=10.0)
    verdict = diagnose((100, 100), CONTROLS, transform)
    assert verdict.offset == pytest.approx((-40.0, -10.0))


def test_landed_is_derived_from_the_refusal_and_not_the_other_way_round():
    """The project's rule about gates, applied to the one place a press
    trace could otherwise say two different things at once."""
    transform = Transform(a=0.5, d=0.5, tx=115.0, ty=27.5)
    verdict = diagnose(drawn_at((415, 25), transform), CONTROLS, transform)
    assert verdict.landed is (verdict.refusal is None)
