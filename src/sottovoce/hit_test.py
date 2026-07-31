"""Where a press lands, and where the user thought it would.

Pure logic, Qt-free like geometry.py, and it exists because those two are
not the same question. Qt delivers a mouse press by walking its own widget
tree with its own geometry; what the user aimed at is what the COMPOSITOR
drew, and the flight scales the whole view through a CALayer transform
that Qt knows nothing about. While such a transform is anything but the
identity, paint and hit testing disagree by a knowable amount, and this is
what knows it.

Nothing here reads a widget. The window hands over rectangles and a
transform, so the verdict can be tested without a screen, and so the same
verdict is written down once rather than reconstructed twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Below this, a scale or a translation is the identity as far as a press is
# concerned: half a point of disagreement cannot move a press from one
# control to another, and a float comparison against exactly 1.0 would
# report a transform that is only the rounding of one.
TRANSFORM_EPSILON = 0.5


@dataclass(frozen=True)
class Transform:
    """An affine transform the compositor is drawing the view through.

    ``(a, d)`` scale, ``(tx, ty)`` translate, in the view's own points.
    CoreGraphics' full matrix has b and c as well; a rotation or a skew
    would need them and nothing in this app applies one, so they are not
    carried and their absence is the claim that they are zero.
    """

    a: float = 1.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    @property
    def is_identity(self) -> bool:
        """Whether paint and hit testing agree.

        The scales are compared against 1 in POINTS at a nominal 500 point
        window rather than as a ratio, so "1% smaller" is read as the five
        points it actually moves a press by.
        """
        return (
            abs(self.a - 1.0) * 500 <= TRANSFORM_EPSILON
            and abs(self.d - 1.0) * 500 <= TRANSFORM_EPSILON
            and abs(self.tx) <= TRANSFORM_EPSILON
            and abs(self.ty) <= TRANSFORM_EPSILON
        )


IDENTITY = Transform()


@dataclass(frozen=True)
class Control:
    """One overlay control, as the window sees it.

    ``rect`` is (x, y, width, height) in the window's own coordinates.
    ``pressable`` is the whole of whether a press on it would do anything:
    a hidden control is not under the pointer at all, and a disabled one
    takes the press and drops it.
    """

    name: str
    rect: tuple
    visible: bool = True
    enabled: bool = True

    @property
    def pressable(self) -> bool:
        return self.visible and self.enabled

    def contains(self, point: tuple) -> bool:
        x, y, width, height = self.rect
        return x <= point[0] < x + width and y <= point[1] < y + height


def drawn_at(point: tuple, transform: Transform) -> tuple:
    """Where a point of the window's layout appears on screen."""
    return (
        point[0] * transform.a + transform.tx,
        point[1] * transform.d + transform.ty,
    )


def aimed_at(point: tuple, transform: Transform) -> tuple:
    """Which point of the window's layout the user was pointing at.

    The inverse of ``drawn_at``: they touched a pixel, the compositor put
    that pixel there, and this is the layout coordinate it came from. Qt
    will hit-test the untransformed point instead, and the gap between the
    two is the bug this module exists to name.

    A degenerate transform (scaled to nothing, which is where the flight
    ends) has no inverse, and the honest answer is the point itself rather
    than a division by zero: nothing is visible to aim at anyway.
    """
    if not transform.a or not transform.d:
        return point
    return (
        (point[0] - transform.tx) / transform.a,
        (point[1] - transform.ty) / transform.d,
    )


def control_at(point: tuple, controls: list) -> Optional[str]:
    """The topmost pressable control under a point, or None.

    Last wins, because that is the order the window raises them in and so
    the order they are stacked on screen.
    """
    found = None
    for control in controls:
        if control.pressable and control.contains(point):
            found = control.name
    return found


@dataclass(frozen=True)
class Diagnosis:
    """What happened to one press, and why if it went wrong."""

    hit: Optional[str]       # what Qt will deliver it to
    aimed: Optional[str]     # what the user was pointing at
    offset: tuple            # how far paint is from hit testing, in points
    refusal: Optional[str]   # why they differ, or None if they do not

    @property
    def landed(self) -> bool:
        """Derived from the refusal and never the other way round: a
        reconstruction can disagree with what happened."""
        return self.refusal is None


def diagnose(
    point: tuple, controls: list, transform: Transform = IDENTITY
) -> Diagnosis:
    """Read one press.

    ``point`` is where Qt says the press is, in the window's coordinates,
    which is what Qt will hit-test. Whether that is what the user aimed at
    depends on the transform.
    """
    hit = control_at(point, controls)
    seen_point = aimed_at(point, transform)
    aimed = control_at(seen_point, controls)
    offset = (seen_point[0] - point[0], seen_point[1] - point[1])
    if hit == aimed:
        return Diagnosis(hit, aimed, offset, None)
    if not transform.is_identity:
        refusal = "layer_transform"
    else:  # pragma: no cover - unreachable while the transform is the only lens
        refusal = "unknown"
    return Diagnosis(hit, aimed, offset, refusal)
