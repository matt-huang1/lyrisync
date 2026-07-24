"""Pure wheel-gesture routing and step math. No Qt.

Option(⌥)+scroll adjusts opacity everywhere. A plain scroll scrolls the
lyrics in PLAIN mode and adjusts opacity in every other mode, so synced
tracks behave exactly as before the scrollable plain view existed.
"""

from __future__ import annotations

# Full opacity travel (0.25→1.0) ≈ 37 wheel notches or ~940 trackpad px.
OPACITY_PER_WHEEL_NOTCH = 0.02
OPACITY_PER_SCROLL_PIXEL = 0.0008

# One wheel notch moves the plain-lyrics text this many pixels; trackpads
# provide exact pixel deltas and pass through unchanged.
SCROLL_PX_PER_WHEEL_NOTCH = 60


def wheel_action(plain_mode: bool, option_held: bool) -> str:
    """"opacity" or "scroll"."""
    if option_held or not plain_mode:
        return "opacity"
    return "scroll"


def opacity_step(pixel_delta_y: float, angle_delta_y: float) -> float:
    """Opacity change for one wheel event; pixel deltas (trackpad) win."""
    if pixel_delta_y:
        return pixel_delta_y * OPACITY_PER_SCROLL_PIXEL
    return (angle_delta_y / 120.0) * OPACITY_PER_WHEEL_NOTCH


def scroll_step(pixel_delta_y: float, angle_delta_y: float) -> int:
    """Content pixels to move for one wheel event; sign follows the
    platform's natural delta."""
    if pixel_delta_y:
        return int(pixel_delta_y)
    return round((angle_delta_y / 120.0) * SCROLL_PX_PER_WHEEL_NOTCH)
