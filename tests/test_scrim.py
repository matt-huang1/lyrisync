"""The scrim's contrast floor, computed rather than eyeballed.

The window paints ``_SCRIM_OVER_MATERIAL`` on top of the vibrancy material
and ``_PAINTED_BACKGROUND`` when there is no material at all. Both carry
the same promise: the sung line stays readable over a pure white document.
The material only ever helps — over a white page it renders as a dark
tint, measured at 8.2:1 against a real screenshot — so the floor is the
case where it contributes nothing, and that is what is checked here.

The compositing and the WCAG maths live in the test rather than in the
app: the app has no reason to compute a contrast ratio at runtime, but a
constant nudged by eye should fail the suite.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip(
    "PySide6.QtGui",
    reason="PySide6 unusable (missing system Qt libraries?)",
    exc_type=ImportError,
)

from lyrisync import window as w  # noqa: E402

WHITE = (255.0, 255.0, 255.0)
# The sung line's colour, from QLabel#current in the stylesheet.
SUNG_LINE = (255.0, 255.0, 255.0)
SUNG_LINE_ALPHA = 250 / 255


def _linear(channel: float) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(first, second) -> float:
    a, b = luminance(first), luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def over(top, top_alpha, bottom, bottom_alpha=1.0):
    """Source-over of two straight-alpha colours -> (colour, alpha)."""
    alpha = top_alpha + bottom_alpha * (1 - top_alpha)
    if alpha == 0:
        return (0.0, 0.0, 0.0), 0.0
    colour = tuple(
        (t * top_alpha + b * bottom_alpha * (1 - top_alpha)) / alpha
        for t, b in zip(top, bottom)
    )
    return colour, alpha


def sung_line_contrast(background, window_opacity=1.0):
    """Sung line vs its own background, both drawn over a white page.

    ``background`` is the window's painted background; the material below
    it is taken as fully transparent, which is the worst it can do.
    """
    bg = (
        float(background.red()),
        float(background.green()),
        float(background.blue()),
    )
    bg_alpha = background.alpha() / 255
    fg, fg_alpha = over(SUNG_LINE, SUNG_LINE_ALPHA, bg, bg_alpha)
    # The whole window is composited onto the page at its own opacity.
    on_page_bg, _ = over(bg, bg_alpha * window_opacity, WHITE)
    on_page_fg, _ = over(fg, fg_alpha * window_opacity, WHITE)
    return contrast(on_page_fg, on_page_bg)


def test_the_scrim_clears_4_5_to_1_over_a_white_page_with_no_material():
    assert sung_line_contrast(w._SCRIM_OVER_MATERIAL) >= 4.5


def test_the_solid_background_clears_it_too():
    """The fallback when vibrancy never installs carries the same promise
    and, being nearly opaque, has an easier time of it."""
    assert sung_line_contrast(w._PAINTED_BACKGROUND) >= 4.5


def test_the_scrim_keeps_no_more_than_a_hair_of_headroom():
    """Guard the tuning from the other side too. 4.5:1 is crossed at alpha
    147 and the constant is 150 — enough that rounding cannot land under
    the threshold, little enough that it is still the lowest alpha the
    promise allows, and not a value drifted upwards by eye."""
    from PySide6.QtGui import QColor

    scrim = w._SCRIM_OVER_MATERIAL
    lighter = QColor(scrim.red(), scrim.green(), scrim.blue(), scrim.alpha() - 6)
    assert sung_line_contrast(lighter) < 4.5


def test_the_window_starts_at_full_opacity():
    """Below 1.0 macOS renders the material without its blur, so the
    default has to be exactly 1.0 for the frost to exist at all."""
    assert w._DEFAULT_OPACITY == 1.0
    assert w._MAX_OPACITY == 1.0


def test_dimming_is_the_users_own_trade():
    """No promise is made below full opacity — dimming is a deliberate
    request to see through the window — but it is worth knowing where it
    lands, and that it degrades smoothly rather than falling off a cliff."""
    ratios = [
        sung_line_contrast(w._SCRIM_OVER_MATERIAL, window_opacity=opacity)
        for opacity in (1.0, 0.75, 0.5, w._MIN_OPACITY)
    ]
    assert ratios == sorted(ratios, reverse=True)
    assert ratios[-1] > 1.0
