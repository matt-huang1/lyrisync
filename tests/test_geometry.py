import pytest

from lyrisync.geometry import clamped_position

# Menu-bar-style available geometry: x, y, w, h.
AVAIL = (0, 25, 1440, 875)
W, H = 460, 200


def visible_overlap(pos, size, avail):
    x, y = pos
    w, h = size
    ax, ay, aw, ah = avail
    ox = min(x + w, ax + aw) - max(x, ax)
    oy = min(y + h, ay + ah) - max(y, ay)
    return ox, oy


def test_fully_inside_is_untouched():
    frame = (300, 300, W, H)
    assert clamped_position(frame, AVAIL) == (300, 300)


@pytest.mark.parametrize(
    ("frame", "axis"),
    [
        ((-459, 300, W, H), "x"),   # 1px visible on the left
        ((1439, 300, W, H), "x"),   # 1px visible on the right
        ((300, 25 - H + 1, W, H), "y"),  # 1px visible at the top
        ((300, 899, W, H), "y"),    # 1px visible at the bottom
        ((-459, 899, W, H), "xy"),  # corner: 1px in both axes
        ((-2000, -2000, W, H), "xy"),  # fully off-screen
    ],
)
def test_offscreen_windows_keep_grab_margin(frame, axis):
    x, y = clamped_position(frame, AVAIL)
    ox, oy = visible_overlap((x, y), (W, H), AVAIL)
    assert ox >= 40
    assert oy >= 40


def test_partial_tucking_is_preserved():
    # 100px visible on the left: allowed, not snapped anywhere.
    frame = (-360, 300, W, H)
    assert clamped_position(frame, AVAIL) == (-360, 300)


def test_exactly_at_margin_is_untouched():
    frame = (0 + 40 - W, 300, W, H)  # exactly 40px visible on the left
    assert clamped_position(frame, AVAIL) == (frame[0], 300)


def test_window_smaller_than_margin_stays_fully_visible():
    x, y = clamped_position((-100, 300, 30, 30), AVAIL)
    ox, oy = visible_overlap((x, y), (30, 30), AVAIL)
    assert (ox, oy) == (30, 30)
