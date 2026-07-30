"""What the menu bar item is showing, and the glyph it is drawn from.

The item used to be one image whatever the app was doing, which made it a
launcher rather than an indicator. It is the only part of this app that is
always on screen, so it is the natural place to say — quietly, without being
read — whether anything is happening.

## Brightness and shape are independent

*Milestone 15.1.* Milestone 15 had three whole-glyph states (idle, active,
practice) and that conflated two different questions into one axis: a paused
song dimmed the icon exactly as hiding the window did, so the one thing the
dimming was for — confirming that ⇧⌘J landed — was indistinguishable from
Spotify being paused. Nothing playing no longer dims anything.

Three properties now, each answering one question:

- **brightness** — is the lyrics layer on? Dimmed means the window is
  hidden. That is the only thing it means, so the menu bar is a reliable
  confirmation for a keypress whose whole effect is that something
  disappears.
- **shape** — is a song playing? Three bars of equal length for no, and the
  short / long / short arrangement for yes, which is the window's own
  previous / current / next rows with the current one longest.
- **the dot** — is a practice mode running? A loop, an echo pass or a
  tap-to-sync.

They compose, so the eight combinations come from three booleans rather than
from eight drawings. That is only affordable because the glyph is drawn
rather than loaded: at 15 there were three SVG files, and this would have
needed twenty.

Practice still outranks the window being hidden — a pass keeps running while
the lyrics are away, and then the item is the only evidence it is going — so
practice keeps the icon at full brightness.

The glyph is a template image: solid black with the shape in the alpha
channel, so macOS tints it for a light or dark menu bar, and the dimmed
alpha comes through as a dimmer glyph rather than a grey one. That is also
why practice is a DOT and not a colour: a coloured menu bar icon stops
following the menu bar.

Pure and Qt-free: the state and the geometry are decided here, the pixels
are drawn by symbols.py.
"""

from __future__ import annotations

from typing import NamedTuple

# -- brightness -----------------------------------------------------------

# What "dimmed" is worth. Carried over from milestone 15's idle glyph, where
# it was measured to read as the same icon with less ink rather than as a
# different, greyer one.
DIM_ALPHA = 0.4
FULL_ALPHA = 1.0


# -- the glyph's geometry -------------------------------------------------
#
# A 22-unit square, which is also the point size it is drawn at, so one unit
# is one point and the numbers below can be read against the menu bar's own
# 22-point thickness. Inherited from the SVGs this replaced, so the shape did
# not change when the drawing moved into code.

GLYPH_UNITS = 22

# Three bars: previous, current, next. The middle one is thicker as well as
# longer, because it is the line being sung — the same hierarchy the window
# gives its own current row.
BAR_THICKNESSES = (2, 3, 2)
BAR_CENTRES_Y = (6, 11, 16)

# Horizontally centred, and shifted left when the dot needs the corner. The
# shift cannot be more than 2: the middle bar is 18 long, so a centre below 9
# would push it off the left edge of a 22-unit square.
BAR_CENTRE_X = 11
DOT_SHIFT = 2

# The practice mark, in the corner the bars leave empty.
#
# Moved in from (18.5, 18.5) r3 in milestone 15.1, and not for taste: the
# even shape's bottom bar is longer than the playing shape's, and at the old
# position and radius the dot OVERLAPPED it by half a unit. Rendered at 16
# points that is not a mark beside a bar, it is a bar with a blob on the end
# — found by looking at the sheet, after the pairwise pixel differences had
# said everything was fine. `test_the_dot_never_touches_a_bar` is what stops
# it coming back.
DOT_CENTRE = (19, 19)
DOT_RADIUS = 2.75

# How much clear space the dot keeps from the nearest bar. One unit is about
# 1.5 device pixels at 16 points on a Retina menu bar — enough that
# antialiasing cannot close the gap.
DOT_CLEARANCE = 1.0


# -- the shapes -----------------------------------------------------------

# Nothing playing: three bars of equal length. Not a second drawing — the
# same three bars at the same three weights, saying nothing about which line
# is current, because there is no current line.
#
# 12 rather than 14, which is the dot's doing: the bottom bar shares its row
# with the mark, so the longest any bar may be is what still leaves
# DOT_CLEARANCE beside it.
EVEN_LENGTHS = (12, 12, 12)

# Playing: short / long / short, the window's previous / current / next with
# the current one longest.
#
# The rest of the tuple is the optional animation (see `arrangement`). The
# middle bar is 18 in every one of them, so "the current line is the longest"
# is true of every frame the icon can ever show — the arrangement varies the
# lines around it, which is what a lyric advancing actually looks like.
# The bottom bar stays within the dot's clearance in every one of them; the
# top bar is free to be longer because it shares no row with the mark.
ARRANGEMENTS = (
    (10, 18, 10),
    (13, 18, 8),
    (7, 18, 12),
    (12, 18, 9),
)

PLAYING_LENGTHS = ARRANGEMENTS[0]


class IconSpec(NamedTuple):
    """Everything needed to draw the item, and nothing else.

    Hashable, which is what lets the window cache one drawing per
    combination and compare "what should be showing" against "what is
    showing" with ``==``. That comparison is load-bearing: the refresh runs
    on every monitor tick, and handing the same image back to an
    NSStatusItem three times a second is the menu bar item being rebuilt
    under the user.
    """

    lengths: tuple[int, int, int]
    dimmed: bool
    dot: bool


def arrangement(step: int) -> tuple[int, int, int]:
    """The ``step``-th set of bar lengths, cycling.

    Stepped on a real lyric change, never on a timer: the icon is a thing to
    notice, not a thing to watch, and a menu bar that moves on its own is
    something to look at. Negative and huge steps both wrap, because the
    caller's counter is not this module's business.
    """
    return ARRANGEMENTS[step % len(ARRANGEMENTS)]


def bar_centre_x(dot: bool) -> float:
    """Where the bars are centred, given whether the dot needs room."""
    return BAR_CENTRE_X - (DOT_SHIFT if dot else 0)


def icon_spec(
    *,
    playing: bool,
    lyrics_visible: bool,
    practising: bool,
    animated: bool = False,
    line_changes: int = 0,
) -> IconSpec:
    """What the menu bar item should be showing right now.

    Three independent answers, so no state can hide another:

    - ``dimmed`` asks only whether the lyrics are on screen. Practice
      overrides it, because a pass running behind a hidden window is the one
      case where this item is the only evidence anything is happening, and
      an icon that went quiet there would be reporting on the window rather
      than on the app.
    - ``lengths`` asks only whether a song is playing.
    - ``dot`` asks only whether a practice mode is engaged.

    ``animated`` is off by default and only reaches the shape while a song
    is playing: with nothing playing there are no line changes to step, and
    an arrangement frozen mid-cycle would be a shape that means nothing.
    """
    if playing:
        lengths = arrangement(line_changes) if animated else PLAYING_LENGTHS
    else:
        lengths = EVEN_LENGTHS
    return IconSpec(
        lengths=lengths,
        dimmed=not lyrics_visible and not practising,
        dot=practising,
    )
