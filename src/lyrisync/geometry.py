"""Pure window-placement geometry. No Qt: rects are (x, y, w, h) tuples."""

from __future__ import annotations

GRAB_MARGIN = 40  # px of window that must stay on-screen after a drag


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
