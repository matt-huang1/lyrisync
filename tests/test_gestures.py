import pytest

from lyrisync import gestures as g


# -- routing -------------------------------------------------------------


def test_plain_scroll_scrolls_in_plain_mode():
    assert g.wheel_action(plain_mode=True, option_held=False) == "scroll"


def test_option_scroll_is_opacity_everywhere():
    assert g.wheel_action(plain_mode=True, option_held=True) == "opacity"
    assert g.wheel_action(plain_mode=False, option_held=True) == "opacity"


def test_plain_scroll_is_opacity_outside_plain_mode():
    # Synced and all other modes keep today's behavior.
    assert g.wheel_action(plain_mode=False, option_held=False) == "opacity"


# -- opacity step --------------------------------------------------------


def test_opacity_step_pixel_delta_wins():
    assert g.opacity_step(100, 120) == pytest.approx(100 * g.OPACITY_PER_SCROLL_PIXEL)


def test_opacity_step_wheel_notches():
    assert g.opacity_step(0, 120) == pytest.approx(g.OPACITY_PER_WHEEL_NOTCH)
    assert g.opacity_step(0, -240) == pytest.approx(-2 * g.OPACITY_PER_WHEEL_NOTCH)


def test_opacity_step_zero_deltas():
    assert g.opacity_step(0, 0) == 0.0


# -- scroll step ---------------------------------------------------------


def test_scroll_step_trackpad_pixels_pass_through():
    assert g.scroll_step(7, 0) == 7
    assert g.scroll_step(-13, 120) == -13  # pixel wins over angle


def test_scroll_step_wheel_notches():
    assert g.scroll_step(0, 120) == g.SCROLL_PX_PER_WHEEL_NOTCH
    assert g.scroll_step(0, -60) == -g.SCROLL_PX_PER_WHEEL_NOTCH // 2
