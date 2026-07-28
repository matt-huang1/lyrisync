"""Pure window-layout geometry. No Qt: rects are (x, y, w, h) tuples."""

from __future__ import annotations

from lyrisync.typography import (
    BOTTOM_MARGIN,
    CONTEXT,
    CURRENT,
    HEADER,
    PRONUNCIATION,
    ROW_SPACING,
    TOP_MARGIN,
    base_size,
)

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
# upcoming. Taken from typography.py rather than copied, so a change to the
# type scale can never leave the height floor describing the old one.
_ROW_FONTS_PX = tuple(
    base_size(role) for role in (HEADER, CONTEXT, CURRENT, PRONUNCIATION, CONTEXT)
)
_LINE_HEIGHT_FACTOR = 1.45
_ROW_SPACING = ROW_SPACING
_TOP_MARGIN = TOP_MARGIN
_BOTTOM_MARGIN = BOTTOM_MARGIN
_MIN_HEIGHT_FLOOR = 120

# Tap-to-sync bottom row. _RESIZE_MARGIN mirrors the window's edge grab
# zone: the row must stay clear of it or dragging the bottom edge would
# land on the tap bar instead.
_SYNC_BAR_BASE_HEIGHT = 34
_SYNC_BAR_MIN_HEIGHT = 28
_SYNC_BAR_GAP = 6

# Width of the window's edge grab zone, in px. Lives here because the tap
# row's placement has to stay clear of it.
RESIZE_MARGIN = 8


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


def sync_bar_height(scale: float) -> int:
    """Height of the tap-to-sync bottom row. Taller than a normal overlay
    button: it is the primary control of that mode and gets hit repeatedly,
    in a hurry, without looking."""
    return max(_SYNC_BAR_MIN_HEIGHT, round(_SYNC_BAR_BASE_HEIGHT * scale))


def sync_bar_gap(scale: float) -> int:
    """Breathing room between the lyric text and the tap row, and between
    the row's own controls."""
    return max(4, round(_SYNC_BAR_GAP * scale))


def sync_bar_bottom(scale: float) -> int:
    """Gap under the tap row. Never smaller than the window's resize
    margin, so the row cannot swallow the bottom-edge resize grip."""
    return max(button_margin(scale), RESIZE_MARGIN)


def sync_bar_reserve(scale: float) -> int:
    """Vertical space the tap row claims at the window bottom, gaps
    included. The layout's bottom margin grows by this during a sync pass
    so lyric text can never slide under the bar."""
    return sync_bar_height(scale) + sync_bar_gap(scale) + sync_bar_bottom(scale)


def min_window_height(scale: float, sync_bar: bool = False) -> int:
    """Smallest window height where all five label rows fit single-line at
    this scale — no window shape may hide the lyrics entirely. During a
    sync pass the tap row needs its space on top of that."""
    rows = sum(
        round(font * scale * _LINE_HEIGHT_FACTOR) for font in _ROW_FONTS_PX
    )
    spacing = round(_ROW_SPACING * scale) * 4 + 2  # 4 row gaps + pron gap
    margins = round(_TOP_MARGIN * scale) + round(_BOTTOM_MARGIN * scale)
    height = max(_MIN_HEIGHT_FLOOR, rows + spacing + margins)
    return height + sync_bar_reserve(scale) if sync_bar else height


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
