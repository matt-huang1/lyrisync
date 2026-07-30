import pytest

from sottovoce.geometry import (
    RESIZE_MARGIN,
    button_margin,
    button_side,
    clamped_position,
    compact_text_gutter,
    control_gap,
    docked_position,
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


# -- the compact layout ---------------------------------------------------

# A notched MacBook Pro 14", a Mac with no notch, and a second display
# hanging off to the right at a different vertical offset. Screens and
# available areas as Qt reports them: top-left origin, y down.
NOTCHED = (0, 0, 1512, 982)
NOTCHED_AVAILABLE = (0, 37, 1512, 945)   # the menu bar band, notch included
NOTCH_INSET = 32
PLAIN_SCREEN = (0, 0, 1440, 900)
PLAIN_AVAILABLE = (0, 25, 1440, 875)
SECOND_SCREEN = (1512, -120, 1920, 1080)
SECOND_AVAILABLE = (1512, -95, 1920, 1055)


def test_compact_floor_is_much_smaller_than_the_full_one():
    """The whole point of the layout. Two rows instead of five, and the
    air around the sung line goes with the neighbours it was separating
    it from."""
    for scale in SCALES:
        compact = min_window_height(scale, compact=True)
        assert compact < min_window_height(scale)
    # Stated rather than only compared, because "much smaller" is the
    # requirement and a ratio is what says whether it was met.
    assert min_window_height(1.0, compact=True) == 79
    assert min_window_height(1.0) == 183
    assert min_window_height(0.65, compact=True) == 51


def test_compact_floor_is_the_two_rows_it_shows():
    """Derived from the type scale like the full one, not picked: the
    sung line, the pronunciation under it, the tighter gap between the
    two, and the window's own margins."""
    from sottovoce import typography

    for scale in SCALES:
        rows = round(
            typography.base_size(typography.CURRENT) * scale * 1.45
        ) + round(typography.base_size(typography.PRONUNCIATION) * scale * 1.45)
        spacing = round(typography.PRONUNCIATION_SPACING * scale)
        margins = round(typography.TOP_MARGIN * scale) + round(
            typography.BOTTOM_MARGIN * scale
        )
        assert min_window_height(scale, compact=True) == rows + spacing + margins


def test_compact_floor_keeps_room_for_a_romanisation_that_is_not_there_yet():
    """The pronunciation row is counted whether or not the layer is on.
    Which lines carry hangul changes song by song, and a floor that moved
    with them would resize the window under the user mid-track."""
    from sottovoce import typography

    without_pron = min_window_height(1.0, compact=True) - round(
        typography.base_size(typography.PRONUNCIATION) * 1.45
    )
    assert min_window_height(1.0, compact=True) > without_pron


def test_compact_floor_takes_no_five_row_floor():
    """120 is where five rows stop fitting, which is not a fact about
    two."""
    assert min_window_height(0.65, compact=True) < 120


def test_compact_floor_grows_with_scale():
    heights = [min_window_height(scale, compact=True) for scale in SCALES]
    assert heights == sorted(heights)
    assert heights[-1] > heights[0]


def test_compact_floor_still_makes_room_for_the_sync_bar_if_asked():
    """A sync pass leaves the compact layout, so the two never combine in
    the app. The arithmetic composes anyway rather than making the reserve
    an exception, because a reserve that depended on who asked would be a
    second rule."""
    for scale in SCALES:
        assert min_window_height(scale, sync_bar=True, compact=True) == (
            min_window_height(scale, compact=True) + sync_bar_reserve(scale)
        )


def test_compact_gutter_reserves_one_more_control_than_the_full_one():
    """Compact brings the loop and the speak button down beside each other
    where the full layout stacks them, so the right-hand edge carries two
    controls and the margin has to say so."""
    for scale in SCALES:
        assert compact_text_gutter(scale) == (
            text_gutter(scale) + button_side(scale) + control_gap(scale)
        )
        assert compact_text_gutter(scale) > text_gutter(scale)


def test_compact_gutter_leaves_text_room_in_the_narrowest_window():
    """260px is the narrowest the window goes. Two gutters out of it must
    still leave a usable line, or the layout would be reserving the whole
    window for controls that are not even showing."""
    text_width = 260 - 2 * compact_text_gutter(0.65)
    assert text_width > 100


def test_control_gap_is_the_one_definition_of_the_gap():
    """The tap row's gap and the gap between two compact controls are the
    same question asked twice, and are one function."""
    for scale in SCALES:
        assert sync_bar_gap(scale) == control_gap(scale)


# -- docking to the top ---------------------------------------------------


def test_dock_centres_on_the_screen():
    x, y = docked_position(460, PLAIN_SCREEN, PLAIN_AVAILABLE)
    assert x + 460 // 2 == PLAIN_SCREEN[0] + PLAIN_SCREEN[2] // 2


def test_dock_centres_on_the_screen_not_on_what_the_dock_left():
    """A Dock on the left shrinks the available area but not the menu bar
    and not the notch, and it is those the window is lining up with."""
    dock_on_the_left = (80, 25, 1360, 875)
    centred = docked_position(460, PLAIN_SCREEN, PLAIN_AVAILABLE)
    assert docked_position(460, PLAIN_SCREEN, dock_on_the_left)[0] == centred[0]


def test_dock_sits_under_the_menu_bar_on_an_unnotched_mac():
    _, y = docked_position(460, PLAIN_SCREEN, PLAIN_AVAILABLE)
    assert y == PLAIN_AVAILABLE[1]


def test_dock_clears_the_notch():
    """With the menu bar showing, macOS has already reserved the whole
    band the notch sits in, so the available area is the answer and the
    safe area changes nothing."""
    _, y = docked_position(460, NOTCHED, NOTCHED_AVAILABLE, NOTCH_INSET)
    assert y == NOTCHED_AVAILABLE[1]
    assert y >= NOTCHED[1] + NOTCH_INSET


def test_dock_clears_the_notch_with_the_menu_bar_hidden():
    """The case the safe area exists for: "Automatically hide and show the
    menu bar" gives the whole screen back as available space and leaves
    the notch exactly where it was. Without the inset the window would be
    docked underneath it."""
    hidden_menu_bar = (0, 0, 1512, 982)
    _, y = docked_position(460, NOTCHED, hidden_menu_bar, NOTCH_INSET)
    assert y == NOTCH_INSET
    assert docked_position(460, NOTCHED, hidden_menu_bar, 0)[1] == 0


def test_dock_never_overlaps_the_notch_at_any_inset():
    for inset in range(0, 60, 4):
        for available_top in (0, 25, 37, 50):
            available = (0, available_top, 1512, 982 - available_top)
            _, y = docked_position(460, NOTCHED, available, inset)
            assert y >= NOTCHED[1] + inset
            assert y >= available_top


def test_dock_uses_the_screen_it_was_given():
    """Multiple displays: a second screen has its own origin in both axes,
    and docking there must not centre on the primary or borrow its top."""
    x, y = docked_position(460, SECOND_SCREEN, SECOND_AVAILABLE)
    assert x == 1512 + (1920 - 460) // 2
    assert y == -95


def test_dock_is_flush_with_nothing_of_its_own_added():
    """No gap invented here: "just below the menu bar" is a position, and
    a gap would be a number set by eye. Nudging it down is a drag."""
    for screen, available, inset in (
        (PLAIN_SCREEN, PLAIN_AVAILABLE, 0),
        (NOTCHED, NOTCHED_AVAILABLE, NOTCH_INSET),
        (SECOND_SCREEN, SECOND_AVAILABLE, 0),
    ):
        _, y = docked_position(460, screen, available, inset)
        assert y == max(available[1], screen[1] + inset)
