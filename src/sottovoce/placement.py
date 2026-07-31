"""What each layout was last left at.

Pure logic, Qt-free like geometry.py. The window has two shapes it moves
between — the full layout and the strip — and until now only half of one
fact was remembered about them: the SIZE. Coming back from the strip gave
the full layout its old size at wherever the strip happened to be
standing, which after a song had fitted the strip's width was not where
the full layout had ever been.

Position and size are one fact here for one reason: a strip is a quarter
the height of the full layout and often a different width, so the place
that suits one is not the place that suits the other. A user who keeps
the strip under the menu bar and the full window in the corner is
describing two shapes, not one shape and a wish.

What is NOT remembered is as much of the design as what is:

- the STRIP'S HEIGHT, because it follows the type size. There is one right
  answer for it and remembering another would only be a way to disagree
  with the setting.
- the strip's WIDTH while the song is choosing it. That slot holds the
  width the user picked, which is what turning the fit off gives back.
- any position the window is only standing at: a dodge, a flight, a
  notification yield. The window has one word for that — where it BELONGS
  — and this is given it rather than asked to work it out.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class Shape:
    """Where a layout was and how big, as far as anything is known.

    Every field is optional and independently so, because they are learned
    at different moments: a layout that has been worn but never moved has
    a size and no position, and one restored from an old preferences file
    has a size and no position for the same reason.
    """

    x: Optional[int] = None
    y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def has_position(self) -> bool:
        return self.x is not None and self.y is not None

    @property
    def has_size(self) -> bool:
        return bool(self.width)


EMPTY = Shape()


class LayoutShapes:
    """The two shapes, and the rules about which parts may be written.

    Keyed on the one boolean the whole window is keyed on — whether the
    compact layout is in force — rather than on a name, so there is no
    third state to get wrong.
    """

    def __init__(self) -> None:
        self._shapes = {False: EMPTY, True: EMPTY}

    def recall(self, compact: bool) -> Shape:
        return self._shapes[compact]

    def remember(
        self,
        compact: bool,
        *,
        x: Optional[int] = None,
        y: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        keep_width: bool = False,
    ) -> None:
        """Write down what this layout is being left at.

        ``keep_width`` declines the width and nothing else, which is the
        strip while the song is fitting it: the position it is being left
        at is still the user's, and losing that because a song happened to
        be choosing the width would be answering a question nobody asked.

        ``height`` is simply not passed for the strip. Not refused here
        with a flag of its own, because "the strip has no remembered
        height" is not a rule about this moment — it is a rule about the
        strip, and the caller that knows which layout this is, is the one
        that knows it.
        """
        current = self._shapes[compact]
        changes = {}
        if x is not None and y is not None:
            changes["x"], changes["y"] = int(x), int(y)
        if width is not None and not keep_width:
            changes["width"] = int(width)
        if height is not None:
            changes["height"] = int(height)
        self._shapes[compact] = replace(current, **changes)

    def forget_positions(self) -> None:
        """Drop where both layouts were, keeping how big they were.

        The one thing that invalidates a remembered position and not a
        remembered size: the screen the window was on is no longer there,
        or is a different shape. A size clamps; a position off the side of
        every display is not a preference, it is a place that no longer
        exists.
        """
        for compact, shape in self._shapes.items():
            self._shapes[compact] = replace(shape, x=None, y=None)
