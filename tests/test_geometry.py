import pytest

from sottovoce.geometry import (
    RESIZE_MARGIN,
    button_margin,
    button_side,
    clamped_position,
    min_window_height,
    sync_bar_bottom,
    sync_bar_gap,
    sync_bar_height,
    sync_bar_reserve,
    text_gutter,
)

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


# -- button metrics and text gutters -------------------------------------

SCALES = (0.65, 1.0, 1.4, 2.0, 3.2)


def test_button_metrics_floor_at_small_scale():
    assert button_side(0.65) == 22   # comfortable click target
    assert button_margin(0.65) == 6


def test_button_metrics_track_scale():
    assert button_side(1.0) == 26
    assert button_side(2.0) == 52
    assert button_margin(2.0) == 16


def test_text_gutter_always_clears_the_button_zone():
    for scale in SCALES:
        zone = button_margin(scale) + button_side(scale)
        assert text_gutter(scale) >= zone + 4, f"scale {scale}"


def test_text_gutter_grows_with_scale():
    gutters = [text_gutter(scale) for scale in SCALES]
    assert gutters == sorted(gutters)
    assert gutters[-1] > gutters[0]


# -- minimum window height -----------------------------------------------


def test_min_height_default_scale():
    # rows 16+19+29+17+19=100, spacing 10*4+3+5*2=53, margins 14+16=30
    assert min_window_height(1.0) == 183


def test_the_floor_counts_every_gap_the_window_actually_leaves():
    """Derived, not eyeballed: each of the three vertical rhythm constants
    has to appear in the floor, or a more generous layout would silently
    let the bottom row fall off a short window."""
    from sottovoce import geometry, typography

    base = min_window_height(1.0)
    for name in ("ROW_SPACING", "PRONUNCIATION_SPACING", "CURRENT_SPACING"):
        original = getattr(typography, name)
        monkey = original + 4
        setattr(geometry, f"_{name}", monkey)
        try:
            assert min_window_height(1.0) > base, f"{name} is not in the floor"
        finally:
            setattr(geometry, f"_{name}", original)


def test_min_height_is_derived_from_the_type_scale():
    """The floor exists to keep all five rows visible, so it has to be
    computed from the same sizes the stylesheet uses — not a copy that can
    drift when the type scale changes."""
    from sottovoce import geometry, typography

    assert geometry._ROW_FONTS_PX == (
        typography.base_size(typography.HEADER),
        typography.base_size(typography.CONTEXT),
        typography.base_size(typography.CURRENT),
        typography.base_size(typography.PRONUNCIATION),
        typography.base_size(typography.CONTEXT),
    )
    assert geometry._ROW_SPACING == typography.ROW_SPACING
    assert geometry._TOP_MARGIN == typography.TOP_MARGIN
    assert geometry._BOTTOM_MARGIN == typography.BOTTOM_MARGIN


def test_min_height_floor_at_small_scale():
    assert min_window_height(0.65) == 120  # content needs ~100; floor wins


def test_min_height_monotonic_with_scale():
    heights = [min_window_height(scale) for scale in SCALES]
    assert heights == sorted(heights)
    assert heights[-1] > heights[0]


def test_min_height_scales_roughly_linearly():
    # Doubling the scale should roughly double the content height.
    assert min_window_height(2.0) == pytest.approx(2 * min_window_height(1.0), rel=0.1)


# -- tap-to-sync bottom row ----------------------------------------------


def test_sync_bar_is_a_comfortable_target_at_every_scale():
    for scale in SCALES:
        # Taller than the small overlay buttons: it is the primary control
        # of sync mode and gets hit repeatedly, without looking.
        assert sync_bar_height(scale) > button_side(scale)


def test_sync_bar_metrics_grow_with_scale():
    heights = [sync_bar_height(scale) for scale in SCALES]
    assert heights == sorted(heights)
    assert heights[-1] > heights[0]


def test_sync_bar_clears_the_resize_grip():
    """The row sits above the bottom edge zone, so dragging the window's
    bottom edge never lands on the tap bar instead."""
    for scale in SCALES:
        assert sync_bar_bottom(scale) >= RESIZE_MARGIN


def test_sync_bar_reserve_covers_the_row_and_both_gaps():
    for scale in SCALES:
        assert sync_bar_reserve(scale) == (
            sync_bar_height(scale) + sync_bar_gap(scale) + sync_bar_bottom(scale)
        )


def test_min_height_makes_room_for_the_sync_bar():
    """No window shape may bury the tap row: the floor grows by exactly
    what the row claims."""
    for scale in SCALES:
        assert min_window_height(scale, sync_bar=True) == (
            min_window_height(scale) + sync_bar_reserve(scale)
        )
