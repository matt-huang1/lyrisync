"""How the window leaves for the menu bar, and comes back.

Hiding used to be instantaneous: the window was there, and then it was
not. That is fine for a window you closed and wrong for one that is still
running — nothing said where it went, so the way back was something to
remember rather than something you saw. Now it shrinks and fades towards
the menu bar item, and grows back out of it, which makes the item the
answer to "where did the lyrics go" without a word of explanation.

Three things are decided here, all of them arithmetic:

- **whether there is anywhere to fly to.** The menu bar item can be
  behind the notch, in an overflow, or simply not there. A flight to a
  rectangle that is not on any screen would send the window off the edge
  of the world, so the fallback is a plain fade in place — the same
  function with no target.
- **where the window is at each moment of the journey**, as a position, a
  scale for its content and an opacity.
- **how long the journey takes**, including a journey that reverses
  halfway.

Pure and Qt-free: plain numbers in, plain numbers out, so the path can be
checked without a screen, a menu bar, or a compositor. What this module
cannot answer — whether it *looks* like the window went to the menu bar —
is a question about pixels and is verified by hand.
"""

from __future__ import annotations

from typing import NamedTuple, Optional, Sequence

# A rectangle as (x, y, width, height), top-left origin — screen
# coordinates the way Qt reports them, not Cocoa's.
Rect = tuple[int, int, int, int]

# How long a full journey takes. The same 260ms as one phase of a line
# change and as the window's travel to a remembered position, because
# this is the same kind of movement and the window should only have one
# sense of how fast it moves.
FLIGHT_MS = 260

# How small the content gets before it is let go. Not zero: a thing that
# shrinks to nothing has to be watched to the very end, where one that
# stops small has already said where it went. Roughly the size of the
# menu bar item it is heading for.
END_SCALE = 0.06


class Frame(NamedTuple):
    """The window at one moment of the journey.

    ``scale`` is for the CONTENT, not the window: the window keeps its
    size all the way and the compositor scales what is drawn inside it.
    Animating the size instead would re-lay the text out on every frame —
    the type scale follows the window's width — and the window would read
    as rewriting itself rather than leaving.
    """

    x: int
    y: int
    scale: float
    opacity: float


def item_usable(item: Optional[Rect], screens: Sequence[Rect]) -> bool:
    """Whether the menu bar item is somewhere the window can fly to.

    False for the cases that all look the same from here and all mean the
    same thing — no item at all, an item of no size, and an item whose
    rectangle is not on any screen (behind the notch, in an overflow,
    reported stale after a display change). The caller falls back to a
    plain fade, which says less but cannot be wrong.
    """
    if item is None:
        return False
    x, y, width, height = item
    if width <= 0 or height <= 0:
        return False
    return any(_intersects(item, screen) for screen in screens)


def _intersects(first: Rect, second: Rect) -> bool:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def centre(rect: Rect) -> tuple[float, float]:
    x, y, width, height = rect
    return x + width / 2, y + height / 2


def frame_at(
    progress: float, home: Rect, target: Optional[Rect] = None
) -> Frame:
    """Where the window is, how big its content is and how solid it is,
    ``progress`` of the way from home (0) to gone (1).

    With no target this is a plain fade: the window stays exactly where it
    is at full size and only its opacity moves. That is the fallback when
    the menu bar item cannot be found, and it is the same code path rather
    than a second one — a fallback nobody exercises is a fallback that
    does not work.
    """
    progress = max(0.0, min(1.0, float(progress)))
    home_x, home_y, width, height = home
    if target is None:
        return Frame(home_x, home_y, 1.0, 1.0 - progress)

    scale = 1.0 + progress * (END_SCALE - 1.0)
    from_x, from_y = centre(home)
    to_x, to_y = centre(target)
    at_x = from_x + (to_x - from_x) * progress
    at_y = from_y + (to_y - from_y) * progress
    # The window's own rectangle never changes size, so its top-left is
    # simply wherever puts the (scaled, centred) content on that point.
    return Frame(
        round(at_x - width / 2),
        round(at_y - height / 2),
        scale,
        1.0 - progress,
    )


def duration_ms(start: float, end: float) -> int:
    """How long to spend going from one progress to another.

    Proportional, so a hide interrupted halfway comes back from halfway in
    half the time rather than dawdling through a journey it has already
    made. At least one millisecond, because a zero-length animation never
    reports finishing and the window would be left mid-flight.
    """
    return max(1, round(FLIGHT_MS * abs(end - start)))
