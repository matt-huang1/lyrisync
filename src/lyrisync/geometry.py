"""Pure window-layout geometry. No Qt: rects are (x, y, w, h) tuples."""

from __future__ import annotations

GRAB_MARGIN = 40  # px of window that must stay on-screen after a drag

# Overlay button metrics. The window sizes its buttons AND reserves its
# text gutters from these same numbers, so text and buttons can never
# disagree about who owns the edge zones.
_BUTTON_BASE_SIDE = 26
_BUTTON_MIN_SIDE = 22
_BUTTON_BASE_MARGIN = 8
_BUTTON_MIN_MARGIN = 6
_GUTTER_PAD = 6

# Label rows top to bottom: header, previous, current, pronunciation,
# upcoming — base font px at scale 1.0, mirrored from the stylesheet.
_ROW_FONTS_PX = (11, 14, 17, 12, 14)
_LINE_HEIGHT_FACTOR = 1.45
_ROW_SPACING = 6
_TOP_MARGIN = 14
_BOTTOM_MARGIN = 16
_MIN_HEIGHT_FLOOR = 120


def button_side(scale: float) -> int:
    """Overlay button box edge at this scale, floored at a comfortable
    click target."""
    return max(_BUTTON_MIN_SIDE, round(_BUTTON_BASE_SIDE * scale))


def button_margin(scale: float) -> int:
    """Gap between an overlay button and the window edge."""
    return max(_BUTTON_MIN_MARGIN, round(_BUTTON_BASE_MARGIN * scale))


def text_gutter(scale: float) -> int:
    """Horizontal layout margin reserving the full button zone plus
    padding: wrapped text can never run under a button."""
    return button_margin(scale) + button_side(scale) + max(4, round(_GUTTER_PAD * scale))


def min_window_height(scale: float) -> int:
    """Smallest window height where all five label rows fit single-line at
    this scale — no window shape may hide the lyrics entirely."""
    rows = sum(
        round(font * scale * _LINE_HEIGHT_FACTOR) for font in _ROW_FONTS_PX
    )
    spacing = round(_ROW_SPACING * scale) * 4 + 2  # 4 row gaps + pron gap
    margins = round(_TOP_MARGIN * scale) + round(_BOTTOM_MARGIN * scale)
    return max(_MIN_HEIGHT_FLOOR, rows + spacing + margins)


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
