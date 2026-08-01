"""What each layout was last left at.

Position and size together, because they are one fact: a strip is a
quarter the height of the full layout and usually a different width, so
the place that suits one does not suit the other. Coming back from the
strip used to give the full layout its old SIZE at wherever the strip
happened to be standing.
"""

TIER = "unit"  # Qt-free logic, called directly

from sottovoce.placement import EMPTY, LayoutShapes, Shape

FULL, COMPACT = False, True


def test_a_layout_never_worn_remembers_nothing():
    shapes = LayoutShapes()
    assert shapes.recall(FULL) == EMPTY
    assert shapes.recall(COMPACT) == EMPTY
    assert shapes.recall(FULL).has_position is False
    assert shapes.recall(FULL).has_size is False


def test_each_layout_keeps_its_own():
    shapes = LayoutShapes()
    shapes.remember(FULL, x=300, y=200, width=520, height=340)
    shapes.remember(COMPACT, x=120, y=60, width=260)

    assert shapes.recall(FULL) == Shape(300, 200, 520, 340)
    assert shapes.recall(COMPACT) == Shape(120, 60, 260, None)


def test_the_strip_has_no_remembered_height():
    """It follows the type size, so there is one right answer for it and
    remembering another would only be a way to disagree with the setting.
    Not passed rather than refused: which layout this is, is the caller's
    to know."""
    shapes = LayoutShapes()
    shapes.remember(COMPACT, x=0, y=0, width=260)
    assert shapes.recall(COMPACT).height is None


def test_a_width_the_song_is_choosing_is_not_written_down():
    """That slot holds the width the USER picked, which is what turning
    the fit off gives back. A fitted one would let it drift a song at a
    time towards whatever the longest line happened to be."""
    shapes = LayoutShapes()
    shapes.remember(COMPACT, x=10, y=10, width=260)
    shapes.remember(COMPACT, x=40, y=90, width=999, keep_width=True)

    assert shapes.recall(COMPACT).width == 260
    # And the position IS still recorded: the song has an opinion about
    # the width and none at all about where the window is.
    assert (shapes.recall(COMPACT).x, shapes.recall(COMPACT).y) == (40, 90)


def test_a_position_alone_leaves_the_size_alone():
    """They are learned at different moments — a layout can be moved
    without being resized — so writing one may not blank the other."""
    shapes = LayoutShapes()
    shapes.remember(FULL, width=520, height=340)
    shapes.remember(FULL, x=7, y=9)
    assert shapes.recall(FULL) == Shape(7, 9, 520, 340)


def test_a_size_alone_leaves_the_position_alone():
    shapes = LayoutShapes()
    shapes.remember(FULL, x=7, y=9)
    shapes.remember(FULL, width=400, height=300)
    assert shapes.recall(FULL) == Shape(7, 9, 400, 300)


def test_half_a_position_is_no_position():
    """A preferences file written before positions were remembered has
    sizes and no coordinates, and one coordinate is not a place."""
    shapes = LayoutShapes()
    shapes.remember(FULL, x=7, width=400)
    assert shapes.recall(FULL).has_position is False
    assert shapes.recall(FULL).width == 400


def test_a_zero_width_is_no_width():
    """Which is what the settings file holds for a layout never worn, and
    what _width_for reads as "keep whatever the window is at"."""
    assert Shape(width=0).has_size is False
    assert Shape(width=260).has_size is True


def test_a_position_at_the_origin_is_still_a_position():
    """QPoint(0, 0) is falsy in PySide and this rule has cost this project
    a bug once already: the one window in a thousand docked at the
    top-left of the primary screen."""
    shapes = LayoutShapes()
    shapes.remember(FULL, x=0, y=0, width=460)
    assert shapes.recall(FULL).has_position is True


def test_forgetting_positions_keeps_sizes():
    """A size clamps to whatever screen there is; a position off the side
    of every display is not a preference, it is a place that has gone."""
    shapes = LayoutShapes()
    shapes.remember(FULL, x=300, y=200, width=520, height=340)
    shapes.remember(COMPACT, x=120, y=60, width=260)

    shapes.forget_positions()

    assert shapes.recall(FULL) == Shape(None, None, 520, 340)
    assert shapes.recall(COMPACT) == Shape(None, None, 260, None)
