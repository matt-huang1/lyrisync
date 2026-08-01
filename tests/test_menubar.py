"""What the menu bar item shows.

Three independent properties since 15.1 — brightness, shape, the dot — and
the point of them being independent is that no state can hide another. That
is what these check: each property against the one question it answers, and
each against the questions it must NOT answer.

What cannot be checked here — whether the pairs are actually told apart at 16
points — is a question about pixels and is verified by hand; see
docs/menu-and-system-integration.md.
"""

TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce import menubar


def spec(**overrides):
    settings = dict(playing=False, lyrics_visible=True, practising=False)
    settings.update(overrides)
    return menubar.icon_spec(**settings)


# -- brightness answers one question: is the lyrics layer on? --------------


def test_hiding_the_lyrics_dims_the_glyph():
    """Which makes the menu bar the confirmation that ⇧⌘J landed, on a
    keypress whose whole effect is that something disappears."""
    assert spec(lyrics_visible=False).dimmed is True
    assert spec(lyrics_visible=True).dimmed is False


def test_nothing_playing_no_longer_dims_the_glyph():
    """THE 15.1 FIX. Milestone 15 dimmed for a paused song exactly as it did
    for a hidden window, so the one thing dimming was for — confirming the
    hotkey — was indistinguishable from Spotify being paused."""
    assert spec(playing=False).dimmed is False
    assert spec(playing=True).dimmed is False


def test_brightness_ignores_the_song_entirely():
    for playing in (True, False):
        assert spec(playing=playing, lyrics_visible=True).dimmed is False
        assert spec(playing=playing, lyrics_visible=False).dimmed is True


def test_practice_keeps_the_glyph_bright_even_when_hidden():
    """A loop or a sync pass keeps running while the lyrics are hidden, and
    then the menu bar item is the ONLY evidence it is still going. An icon
    that went quiet there would be reporting on the window rather than on the
    app."""
    assert spec(lyrics_visible=False, practising=True).dimmed is False
    assert spec(playing=True, lyrics_visible=False, practising=True).dimmed is False


# -- shape answers one question: is a song playing? -----------------------


def test_nothing_playing_is_three_lines_of_equal_length():
    assert spec(playing=False).lengths == menubar.EVEN_LENGTHS
    assert len(set(menubar.EVEN_LENGTHS)) == 1


def test_playing_is_short_long_short():
    """The window's own previous / current / next, with the current one
    longest."""
    first, middle, last = spec(playing=True).lengths
    assert middle > first and middle > last


def test_the_shape_ignores_the_window_and_the_practice_mode():
    playing = spec(playing=True).lengths
    for hidden in (True, False):
        for practising in (True, False):
            got = spec(
                playing=True, lyrics_visible=not hidden, practising=practising
            ).lengths
            assert got == playing


def test_the_two_shapes_are_different():
    """Equal-length and short/long/short are the whole distinction between
    "nothing playing" and "playing" now that brightness no longer carries
    it."""
    assert menubar.EVEN_LENGTHS != menubar.PLAYING_LENGTHS


def test_every_shape_has_three_bars():
    for lengths in (menubar.EVEN_LENGTHS, *menubar.ARRANGEMENTS):
        assert len(lengths) == 3
        assert all(length > 0 for length in lengths)


# -- the dot answers one question: is a practice mode running? -----------


def test_a_practice_mode_adds_the_dot():
    assert spec(practising=True).dot is True
    assert spec(practising=False).dot is False


def test_the_dot_ignores_the_song_and_the_window():
    for playing in (True, False):
        for visible in (True, False):
            got = spec(playing=playing, lyrics_visible=visible, practising=True)
            assert got.dot is True


def test_the_dot_moves_the_bars_over_to_make_room():
    """Otherwise it would crowd them, and a mark that collides with the shape
    it is beside is a smudge at 16 points."""
    assert menubar.bar_centre_x(True) < menubar.bar_centre_x(False)
    assert menubar.bar_centre_x(False) == menubar.BAR_CENTRE_X


def test_the_dot_never_touches_a_bar():
    """THE ONE THE PIXEL DIFFERENCES MISSED.

    Every pair of glyphs differed by a healthy fraction of the square, and
    the even shape's bottom bar was still running half a unit UNDER the dot —
    which at 16 points is not a mark beside a bar, it is a bar with a blob on
    the end. Found by rendering the sheet and looking at it.

    Real rectangle-against-circle arithmetic, over every shape the icon can
    show, because the near miss is between the dot and whichever bar happens
    to share its row.
    """
    dot_x, dot_y = menubar.DOT_CENTRE
    centre = menubar.bar_centre_x(True)
    for lengths in (menubar.EVEN_LENGTHS, *menubar.ARRANGEMENTS):
        for length, thickness, bar_y in zip(
            lengths, menubar.BAR_THICKNESSES, menubar.BAR_CENTRES_Y
        ):
            left, right = centre - length / 2, centre + length / 2
            top, bottom = bar_y - thickness / 2, bar_y + thickness / 2
            # nearest point of the bar's rectangle to the dot's centre
            near_x = min(max(dot_x, left), right)
            near_y = min(max(dot_y, top), bottom)
            gap = ((dot_x - near_x) ** 2 + (dot_y - near_y) ** 2) ** 0.5
            assert gap >= menubar.DOT_RADIUS + menubar.DOT_CLEARANCE, (
                f"{lengths}: bar at y={bar_y} comes within {gap:.2f} of the "
                f"dot's centre, closer than r={menubar.DOT_RADIUS} plus "
                f"{menubar.DOT_CLEARANCE} of clearance"
            )


# -- the three properties are genuinely independent -----------------------


def test_the_eight_combinations_are_all_distinct():
    """Which is the whole claim of splitting one axis into three: every
    combination of the three questions has its own answer."""
    seen = {
        menubar.icon_spec(
            playing=playing, lyrics_visible=visible, practising=practising
        )
        for playing in (True, False)
        for visible in (True, False)
        for practising in (True, False)
    }
    # practice forces bright and a dot, so hidden-vs-shown collapses there:
    # 2 shapes x (2 brightness + 1 practice) = 6 reachable specs
    assert len(seen) == 6
    assert all(isinstance(item, menubar.IconSpec) for item in seen)


def test_a_spec_is_hashable_so_it_can_be_cached_and_compared():
    """The window keeps one drawing per spec and only touches the
    NSStatusItem when the spec changes — both need this."""
    assert spec() == spec()
    assert len({spec(), spec()}) == 1


# -- the optional animation ----------------------------------------------


def test_the_animation_is_off_by_default():
    assert spec(playing=True).lengths == menubar.PLAYING_LENGTHS
    assert spec(playing=True, line_changes=7).lengths == menubar.PLAYING_LENGTHS


def test_the_arrangement_steps_with_the_line_changes():
    got = [
        menubar.icon_spec(
            playing=True, lyrics_visible=True, practising=False,
            animated=True, line_changes=step,
        ).lengths
        for step in range(len(menubar.ARRANGEMENTS))
    ]
    assert got == list(menubar.ARRANGEMENTS)
    assert len(set(got)) == len(menubar.ARRANGEMENTS), "each frame is distinct"


def test_the_arrangement_cycles():
    for step in range(-8, 20):
        assert menubar.arrangement(step) in menubar.ARRANGEMENTS
    period = len(menubar.ARRANGEMENTS)
    assert menubar.arrangement(0) == menubar.arrangement(period)
    assert menubar.arrangement(3) == menubar.arrangement(3 + period * 5)


def test_the_first_arrangement_is_the_static_playing_shape():
    """So switching the animation on does not change what the icon says, only
    whether it moves."""
    assert menubar.arrangement(0) == menubar.PLAYING_LENGTHS


def test_the_current_line_is_the_longest_in_every_arrangement():
    """The identity of the playing shape, and the reason only the outer bars
    vary: every frame the icon can ever show still reads as previous /
    current / next."""
    for lengths in menubar.ARRANGEMENTS:
        first, middle, last = lengths
        assert middle > first, lengths
        assert middle > last, lengths


def test_the_animation_never_reaches_a_stopped_song():
    """There are no line changes with nothing playing, so an arrangement
    frozen mid-cycle would be a shape that means nothing."""
    for step in range(len(menubar.ARRANGEMENTS) * 2):
        got = menubar.icon_spec(
            playing=False, lyrics_visible=True, practising=False,
            animated=True, line_changes=step,
        )
        assert got.lengths == menubar.EVEN_LENGTHS


def test_the_animation_does_not_touch_brightness_or_the_dot():
    plain = menubar.icon_spec(
        playing=True, lyrics_visible=False, practising=True,
        animated=False, line_changes=0,
    )
    moved = menubar.icon_spec(
        playing=True, lyrics_visible=False, practising=True,
        animated=True, line_changes=2,
    )
    assert (plain.dimmed, plain.dot) == (moved.dimmed, moved.dot)
    assert plain.lengths != moved.lengths


# -- the glyph's geometry -------------------------------------------------


def test_the_bars_fit_inside_the_glyph():
    for lengths in (menubar.EVEN_LENGTHS, *menubar.ARRANGEMENTS):
        for dot in (True, False):
            centre = menubar.bar_centre_x(dot)
            for length in lengths:
                assert centre - length / 2 >= 0
                assert centre + length / 2 <= menubar.GLYPH_UNITS


def test_the_dot_fits_inside_the_glyph():
    x, y = menubar.DOT_CENTRE
    assert x + menubar.DOT_RADIUS <= menubar.GLYPH_UNITS
    assert y + menubar.DOT_RADIUS <= menubar.GLYPH_UNITS


def test_the_middle_bar_is_the_heaviest():
    """Longer AND thicker, the same hierarchy the window gives its current
    row."""
    first, middle, last = menubar.BAR_THICKNESSES
    assert middle > first and middle > last


def test_dimming_is_alpha_and_not_a_grey():
    """A template image carries its shape in the alpha channel and macOS owns
    the colour, so the dim glyph has to be the same ink at lower alpha — a
    grey would stop following the menu bar."""
    assert 0 < menubar.DIM_ALPHA < menubar.FULL_ALPHA == 1.0


def test_there_are_three_bars_worth_of_geometry():
    assert len(menubar.BAR_THICKNESSES) == len(menubar.BAR_CENTRES_Y) == 3
