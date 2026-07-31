import pytest

from sottovoce import typography as t


ROLES = (t.HEADER, t.CONTEXT, t.CURRENT, t.PRONUNCIATION, t.PLAIN, t.PROGRESS)
SCALES = (0.65, 1.0, 1.4, 2.0, 3.2)


# -- hierarchy ------------------------------------------------------------


def test_the_current_line_is_the_heaviest_thing_on_screen():
    current = t.style_for(t.CURRENT)
    for role in ROLES:
        if role is t.CURRENT:
            continue
        assert t.style_for(role).weight < current.weight, role


def test_the_current_line_is_larger_than_its_neighbours():
    current = t.style_for(t.CURRENT).size_px
    assert t.style_for(t.CONTEXT).size_px < current
    assert t.style_for(t.PRONUNCIATION).size_px < current


def test_pronunciation_is_lighter_and_smaller_than_the_line_it_sits_under():
    current = t.style_for(t.CURRENT)
    pron = t.style_for(t.PRONUNCIATION)
    assert pron.size_px < current.size_px
    assert pron.weight < current.weight


def test_the_header_is_the_smallest_row():
    header = t.style_for(t.HEADER).size_px
    for role in (t.CONTEXT, t.CURRENT, t.PRONUNCIATION, t.PLAIN):
        assert header <= t.style_for(role).size_px, role
    assert header < t.style_for(t.CURRENT).size_px


def test_every_role_states_its_weight_rather_than_inheriting():
    for role in ROLES:
        assert t.style_for(role).weight > 0, role


def test_the_pronunciation_hugs_its_line_more_closely_than_rows_separate():
    """The pair has to read as one block, not three stacked rows."""
    assert t.PRONUNCIATION_SPACING < t.ROW_SPACING


# -- scaling --------------------------------------------------------------


def test_sizes_scale_with_the_window_and_weights_do_not():
    for role in ROLES:
        base = t.style_for(role, 1.0)
        big = t.style_for(role, 2.0)
        assert big.size_px == pytest.approx(2 * base.size_px, abs=1)
        assert big.weight == base.weight


def test_hierarchy_survives_every_scale():
    for scale in SCALES:
        current = t.style_for(t.CURRENT, scale)
        assert t.style_for(t.CONTEXT, scale).size_px < current.size_px, scale
        assert t.style_for(t.HEADER, scale).size_px < current.size_px, scale


def test_sizes_are_monotonic_in_scale():
    for role in ROLES:
        sizes = [t.style_for(role, scale).size_px for scale in SCALES]
        assert sizes == sorted(sizes), role


def test_a_tiny_scale_still_produces_a_usable_font_size():
    """Rounding must never reach 0px — Qt treats that as unset."""
    for role in ROLES:
        assert t.style_for(role, 0.01).size_px >= 1, role


def test_unknown_role_is_a_loud_error():
    with pytest.raises(KeyError):
        t.style_for("nonsense")


# -- family stack ---------------------------------------------------------


def test_the_platform_family_comes_first():
    stack = t.font_stack(".AppleSystemUIFont")
    assert stack.startswith('".AppleSystemUIFont"')


def test_every_family_is_quoted():
    """The macOS system family is reported as ".AppleSystemUIFont"; an
    unquoted leading dot is not a valid stylesheet identifier."""
    stack = t.font_stack(".AppleSystemUIFont")
    for family in stack.split(", "):
        assert family.startswith('"') and family.endswith('"'), family


def test_a_korean_face_is_named_explicitly():
    """The UI font carries no hangul of its own; naming the face CoreText
    would fall back to makes the fallback explicit rather than implicit."""
    assert "Apple SD Gothic Neo" in t.font_stack(".AppleSystemUIFont")


def test_fallbacks_follow_the_platform_family_in_order():
    stack = t.font_stack("SomeUIFont")
    assert stack == '"SomeUIFont", "Apple SD Gothic Neo", "Helvetica Neue"'


def test_a_platform_family_that_is_already_a_fallback_is_not_repeated():
    stack = t.font_stack("Helvetica Neue")
    assert stack.count("Helvetica Neue") == 1
    assert stack.startswith('"Helvetica Neue"')


def test_a_missing_platform_family_still_leaves_a_usable_stack():
    assert t.font_stack("") == '"Apple SD Gothic Neo", "Helvetica Neue"'


# -- hierarchy and tracking (the feel pass) -------------------------------


def test_the_sung_line_stands_well_clear_of_its_neighbours():
    """The whole hierarchy. At 18/600 against 14/400 the eye had to read
    the window to find the current line; the separation is deliberately
    wider than that on both axes now."""
    current = t.style_for(t.CURRENT)
    context = t.style_for(t.CONTEXT)
    assert current.size_px / context.size_px >= 1.4
    assert current.weight - context.weight >= 200


def test_only_the_large_text_is_tracked():
    """Tightening body-size type costs legibility and buys nothing; it is
    display sizes that look loose at default tracking."""
    assert t.style_for(t.CURRENT).tracking < 0
    for role in (t.HEADER, t.CONTEXT, t.PRONUNCIATION, t.PLAIN, t.PROGRESS):
        assert t.style_for(role).tracking == 0


def test_tracking_scales_with_its_type():
    """It is a proportion of the type, not a fixed nudge — a line at half
    scale needs half the correction."""
    assert t.style_for(t.CURRENT, 2.0).tracking == pytest.approx(
        t.style_for(t.CURRENT, 1.0).tracking * 2
    )


def test_tracking_survives_a_small_scale_without_rounding_to_nothing():
    """Rounded to whole pixels it would be zero everywhere below scale 2,
    which is every real window."""
    assert t.style_for(t.CURRENT, 0.65).tracking != 0


def test_the_current_line_gets_more_air_than_the_rows_around_it():
    """The pronunciation stays welded to the line above it; the block as a
    whole gets room the context lines do not."""
    assert t.PRONUNCIATION_SPACING < t.ROW_SPACING
    assert t.CURRENT_SPACING > 0
    assert t.CURRENT_SPACING < t.ROW_SPACING


def test_line_travel_is_a_hint_not_a_transition():
    """Small enough that nobody watching the lyrics notices it happening,
    and never zero — zero would be the old in-place fade."""
    assert 0 < t.LINE_TRAVEL <= 12


# -- the strip's own type size --------------------------------------------


def test_the_default_compact_size_is_the_apps_own_sung_line():
    """Stated once. A second literal here would be a second answer to how
    big the sung line is, and the two would part company the first time
    the type scale was touched."""
    assert t.DEFAULT_COMPACT_TEXT_SIZE == t.base_size(t.CURRENT)
    assert t.compact_scale(t.DEFAULT_COMPACT_TEXT_SIZE) == 1.0


def test_the_default_is_one_of_the_presets_and_is_in_the_middle():
    """A default the menu cannot show is a default nobody can get back to,
    and a ladder with the default at one end is a ladder that only goes one
    way."""
    assert t.DEFAULT_COMPACT_TEXT_SIZE in t.COMPACT_TEXT_SIZES
    index = t.COMPACT_TEXT_SIZES.index(t.DEFAULT_COMPACT_TEXT_SIZE)
    assert 0 < index < len(t.COMPACT_TEXT_SIZES) - 1


def test_the_presets_climb():
    assert list(t.COMPACT_TEXT_SIZES) == sorted(set(t.COMPACT_TEXT_SIZES))


def test_every_step_is_about_a_fifth(): 
    """An ordinary type-scale interval. Small enough that the next size up
    is a change and not a different window, large enough to be worth
    having a menu entry for."""
    steps = [
        b / a for a, b in zip(t.COMPACT_TEXT_SIZES, t.COMPACT_TEXT_SIZES[1:])
    ]
    for step in steps:
        assert 1.15 <= step <= 1.25, steps


def test_the_scale_is_the_size_over_the_base():
    for size in t.COMPACT_TEXT_SIZES:
        scale = t.compact_scale(size)
        assert t.style_for(t.CURRENT, scale).size_px == size


def test_every_preset_keeps_the_hierarchy():
    """The strip shows two of the roles, and the sung line has to stay
    ahead of the one under it at every size the menu offers."""
    for size in t.COMPACT_TEXT_SIZES:
        scale = t.compact_scale(size)
        current = t.style_for(t.CURRENT, scale)
        pron = t.style_for(t.PRONUNCIATION, scale)
        assert pron.size_px < current.size_px, size
        assert pron.weight < current.weight, size


def test_the_smallest_preset_is_still_a_readable_size():
    assert min(t.COMPACT_TEXT_SIZES) >= 12
