"""Pure window-layout geometry. No Qt: rects are (x, y, w, h) tuples."""

from __future__ import annotations

import math

from sottovoce.typography import (
    BOTTOM_MARGIN,
    CONTEXT,
    CURRENT,
    CURRENT_SPACING,
    HEADER,
    PRONUNCIATION,
    PRONUNCIATION_SPACING,
    ROW_SPACING,
    TOP_MARGIN,
    base_size,
)

GRAB_MARGIN = 40  # px of window that must stay on-screen after a drag

# The window's width, and what the type scale makes of it. Everything on
# the window is proportional to this scale, which is what makes dragging an
# edge a size control rather than a crop.
BASE_WIDTH = 460  # the width at which the type scale is exactly 1.0
MIN_WIDTH = 260
# The scale never goes below this, however narrow the window gets: past a
# point shrinking the text stops being proportional and starts being
# unreadable.
MIN_SCALE = 0.65


def scale_for(width: int) -> float:
    """The type scale at this window width.

    One definition, because three places ask: the window once it has been
    resized, the resize floor, which has to know the scale a drag is ABOUT
    to land on, and the fit, which has to know what it would be measuring
    against.
    """
    return max(MIN_SCALE, width / BASE_WIDTH)

# Overlay button metrics. The window sizes its buttons AND reserves its
# text gutters from these same numbers, so text and buttons can never
# disagree about who owns the edge zones.
_BUTTON_BASE_SIDE = 26
_BUTTON_MIN_SIDE = 22
_BUTTON_BASE_MARGIN = 8
_BUTTON_MIN_MARGIN = 6
_GUTTER_PAD = 6

# Label rows top to bottom: header, previous, current, pronunciation,
# upcoming. Taken from typography.py rather than copied, so a change to the
# type scale can never leave the height floor describing the old one.
_ROW_FONTS_PX = tuple(
    base_size(role) for role in (HEADER, CONTEXT, CURRENT, PRONUNCIATION, CONTEXT)
)
# What is left of that in the compact layout: the sung line and the
# pronunciation under it, and nothing else. The pronunciation is counted
# whether or not the romanisation layer is on, for the reason the full
# layout counts it too — which lines carry hangul changes song by song,
# and a floor that moved with them would resize the window mid-track.
_COMPACT_ROW_FONTS_PX = tuple(base_size(role) for role in (CURRENT, PRONUNCIATION))
_LINE_HEIGHT_FACTOR = 1.45
_ROW_SPACING = ROW_SPACING
_PRONUNCIATION_SPACING = PRONUNCIATION_SPACING
_CURRENT_SPACING = CURRENT_SPACING
_TOP_MARGIN = TOP_MARGIN
_BOTTOM_MARGIN = BOTTOM_MARGIN
_MIN_HEIGHT_FLOOR = 120

# Tap-to-sync bottom row. _RESIZE_MARGIN mirrors the window's edge grab
# zone: the row must stay clear of it or dragging the bottom edge would
# land on the tap bar instead.
_SYNC_BAR_BASE_HEIGHT = 34
_SYNC_BAR_MIN_HEIGHT = 28

# Gap between two overlay controls sitting side by side, and between the
# lyric text and the tap row above it. One number because it is one
# question — how far apart two things this size have to be before they
# read as two things.
_CONTROL_GAP = 6

# Width of the window's edge grab zone, in px. Lives here because the tap
# row's placement has to stay clear of it.
RESIZE_MARGIN = 8


def intersects(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> bool:
    """Whether two rectangles share any area at all.

    Touching edges do not count: a window whose right edge is exactly a
    screen's left edge is not on that screen, and a notification whose
    rectangle stops where the window starts is not over it.

    Lives here rather than in either caller because two of them now ask the
    same question — flight.py, about whether the menu bar item is on any
    screen, and notifications.py, about whether something is covering the
    window. Three lines of arithmetic in two places is still two places for
    the boundary case to be decided differently.
    """
    ax, ay, awidth, aheight = first
    bx, by, bwidth, bheight = second
    return (
        ax < bx + bwidth
        and bx < ax + awidth
        and ay < by + bheight
        and by < ay + aheight
    )


def button_side(scale: float) -> int:
    """Overlay button box edge at this scale, floored at a comfortable
    click target."""
    return max(_BUTTON_MIN_SIDE, round(_BUTTON_BASE_SIDE * scale))


def button_margin(scale: float) -> int:
    """Gap between an overlay button and the window edge."""
    return max(_BUTTON_MIN_MARGIN, round(_BUTTON_BASE_MARGIN * scale))


def control_gap(scale: float) -> int:
    """Gap between two overlay controls sitting side by side."""
    return max(4, round(_CONTROL_GAP * scale))


def text_gutter(scale: float) -> int:
    """Horizontal layout margin reserving the full button zone plus
    padding: wrapped text can never run under a button."""
    return button_margin(scale) + button_side(scale) + max(4, round(_GUTTER_PAD * scale))


def compact_text_gutter(scale: float) -> int:
    """The same margin for the compact layout, which has to reserve TWO
    controls a side rather than one.

    A strip has no top-right corner to put anything in, so the loop and
    the speak button sit side by side on the centre line where the full
    layout stacks them one above the other. The margin is the same on both
    sides even though only the right one carries two controls: the sung
    line is centred, and an asymmetric gutter would centre it in what is
    left of the window rather than in the window.
    """
    return text_gutter(scale) + button_side(scale) + control_gap(scale)


def sync_bar_height(scale: float) -> int:
    """Height of the tap-to-sync bottom row. Taller than a normal overlay
    button: it is the primary control of that mode and gets hit repeatedly,
    in a hurry, without looking."""
    return max(_SYNC_BAR_MIN_HEIGHT, round(_SYNC_BAR_BASE_HEIGHT * scale))


def sync_bar_gap(scale: float) -> int:
    """Breathing room between the lyric text and the tap row, and between
    the row's own controls. The same gap as anywhere else two controls sit
    beside each other, and it is that function rather than a second copy
    of the number."""
    return control_gap(scale)


def sync_bar_bottom(scale: float) -> int:
    """Gap under the tap row. Never smaller than the window's resize
    margin, so the row cannot swallow the bottom-edge resize grip."""
    return max(button_margin(scale), RESIZE_MARGIN)


def sync_bar_reserve(scale: float) -> int:
    """Vertical space the tap row claims at the window bottom, gaps
    included. The layout's bottom margin grows by this during a sync pass
    so lyric text can never slide under the bar."""
    return sync_bar_height(scale) + sync_bar_gap(scale) + sync_bar_bottom(scale)


def min_window_height(
    scale: float, sync_bar: bool = False, compact: bool = False
) -> int:
    """Smallest window height where every label row the layout has fits
    single-line at this scale — no window shape may hide the lyrics
    entirely. During a sync pass the tap row needs its space on top of
    that.

    The compact layout has two rows instead of five, and drops the air
    reserved above and below the sung line with them: that spacing exists
    to stop the three lyric rows reading as an evenly spaced list with one
    of them in bold, and with no neighbours there is nothing to separate
    from. Nor does it take the five-row floor, which is what "much
    smaller" means: 79px against 183px at scale 1.0, 51px against 120px at
    the smallest scale the window has.

    A sync pass leaves the compact layout for as long as it runs, so the
    two never combine in practice; the reserve is added either way rather
    than made an exception, because a reserve that depends on which layout
    asked for it is a second rule.
    """
    if compact:
        rows = sum(
            round(font * scale * _LINE_HEIGHT_FACTOR)
            for font in _COMPACT_ROW_FONTS_PX
        )
        spacing = round(_PRONUNCIATION_SPACING * scale)
        floor = 0
    else:
        rows = sum(
            round(font * scale * _LINE_HEIGHT_FACTOR) for font in _ROW_FONTS_PX
        )
        # 4 row gaps, the tighter pronunciation gap inside the current
        # block, and the extra air reserved above and below that block.
        spacing = (
            round(_ROW_SPACING * scale) * 4
            + round(_PRONUNCIATION_SPACING * scale)
            + round(_CURRENT_SPACING * scale) * 2
        )
        floor = _MIN_HEIGHT_FLOOR
    margins = round(_TOP_MARGIN * scale) + round(_BOTTOM_MARGIN * scale)
    height = max(floor, rows + spacing + margins)
    return height + sync_bar_reserve(scale) if sync_bar else height


def docked_position(
    window_width: int,
    screen: tuple[int, int, int, int],
    available: tuple[int, int, int, int],
    top_inset: int = 0,
) -> tuple[int, int]:
    """Where a window this wide sits when docked to the top of a screen.

    Centred on the SCREEN rather than on the available area, which are not
    the same thing once the Dock is on the left or the right. The menu bar
    it is docking under spans the screen and the notch is centred on the
    screen, so centring on anything else would put the window off-centre
    from the very thing it is lining up with.

    The top edge goes under whichever of the two obstacles reaches further
    down. ``available`` is the menu bar's answer and is usually the whole
    of it: on a notched Mac macOS reserves the entire band the notch sits
    in, so the available area already starts below it. ``top_inset`` is
    the screen's own safe area and is what survives the case that answer
    does not cover — a menu bar set to hide automatically gives the whole
    screen back while leaving the notch exactly where it was.

    Flush, with no gap of its own: "just below the menu bar" is a position
    and not an aesthetic, and a gap would be a number set by eye. The
    window is freely draggable afterwards, so nudging it down is a nudge.
    """
    sx, sy, swidth, _ = screen
    _, ay, _, _ = available
    return (sx + (swidth - window_width) // 2, max(ay, sy + top_inset))


# How much of a screen the compact layout may take up when it is sizing
# itself to a song. Half, because past that a floating strip stops reading
# as an overlay on somebody's work and starts reading as a window over it.
#
# Checked against 776 lines of real lyrics from 14 songs, measured in the
# app's own type at scale 1.0. The widest line in the corpus is 695pt and
# needs an 839pt window; on the 1710pt screen this was measured on, the cap
# is 855pt, so nothing in the corpus is clipped by it. On a 1440pt screen
# the cap is 720pt and 4 of the 14 are, which is the cap doing its job: a
# smaller screen gets a proportionally smaller strip rather than the same
# strip taking more of it.
_WIDTH_CAP_FRACTION = 0.5


def width_cap(screen_width: int) -> int:
    """The widest a strip sizing itself to a song may become on this
    screen. A bound on the feature, not on the window: a drag can still
    make it any width the screen allows."""
    return round(screen_width * _WIDTH_CAP_FRACTION)


def fitted_window_width(
    text_width: float, scale: float, minimum: int, maximum: int
) -> int:
    """The narrowest window that shows a line this wide whole.

    ``text_width`` is measured at ``scale`` and the gutters are asked for
    at the same one, which is the whole of the arithmetic. What makes it
    correct is a decision made outside this function: while the window is
    sizing itself to a song the type scale is HELD, rather than following
    the width as it does the rest of the time. Otherwise there is no
    answer to find — the type grows exactly as fast as the window, so the
    ratio of the room to the line is a constant and a line that does not
    fit at one width does not fit at any width. Measured: 13 of 14 real
    songs, at every width from 260 to 3000.

    Rounded UP, because half a pixel short is a line that elides.

    The cap wins over the fit and the floor wins over the cap: a screen too
    narrow for the minimum still gets a window it can use.
    """
    fitted = math.ceil(text_width) + 2 * compact_text_gutter(scale)
    return max(minimum, min(maximum, fitted))


def resized_position(
    frame: tuple[int, int, int, int],
    new_width: int,
    screen: tuple[int, int, int, int],
    available: tuple[int, int, int, int],
    top_inset: int = 0,
    margin: int = GRAB_MARGIN,
) -> tuple[int, int]:
    """Where a window goes when its width changes under it.

    Anchored on its own centre, so growing and shrinking are the same
    gesture in opposite directions and the window reads as staying put
    while the room either side of the line changes.

    A DOCKED window is re-docked instead, and it is recognised by being
    exactly where docking put it rather than by a flag somebody has to
    remember to clear. The two rules almost agree already — docking centres
    on the screen and centre-anchoring keeps the centre — but "almost" is
    a pixel of drift per resize when the two widths differ in parity, and a
    window that wandered a pixel off centre per song would be off centre by
    the end of an album.

    Clamped either way: a width change is still a placement, and the rule
    that keeps a window reachable does not care what moved it.
    """
    x, y, width, height = frame
    if (x, y) == docked_position(width, screen, available, top_inset):
        target = docked_position(new_width, screen, available, top_inset)
    else:
        target = (x + (width - new_width) // 2, y)
    return clamped_position(
        (target[0], target[1], new_width, height), available, margin
    )


def beside_centred_text(
    row_left: int,
    row_width: int,
    text_width: float,
    side: int,
    scale: float,
    window_width: int,
) -> int:
    """Left edge for a small control that sits just after a centred line.

    The window's message rows are centred and word-wrapped, so "beside the
    message" is not a fixed corner: it moves with how long the message is.
    This puts the control one gap past the text's right edge and no further
    right than the button gutter, which means a message that wraps — and
    whose laid-out width is therefore the whole row — gets the control at
    the gutter instead of off the edge of the window. Both are beside it;
    only one of them is on screen.
    """
    gap = max(3, round(4 * scale))
    text_right = row_left + (row_width + min(float(text_width), float(row_width))) / 2
    limit = window_width - side - button_margin(scale)
    return int(max(row_left, min(limit, round(text_right + gap))))


def clamped_position(
    frame: tuple[int, int, int, int],
    available: tuple[int, int, int, int],
    margin: int = GRAB_MARGIN,
) -> tuple[int, int]:
    """Nearest position to ``frame`` keeping at least ``margin`` px of it
    visible inside ``available`` in both axes. Free placement otherwise —
    tucking a window partially off-screen stays allowed."""
    x, y, width, height = frame
    ax, ay, awidth, aheight = available
    margin_w = min(margin, width)
    margin_h = min(margin, height)
    min_x = ax + margin_w - width
    max_x = ax + awidth - margin_w
    min_y = ay + margin_h - height
    max_y = ay + aheight - margin_h
    return (
        max(min_x, min(max_x, x)),
        max(min_y, min(max_y, y)),
    )
