import pytest

from lyrisync import typography as t


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
