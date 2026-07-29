"""Light and dark palettes, and which one the system is asking for.

The window used to be dark, full stop — the material pinned to the dark
appearance and every colour in the stylesheet written as white at some
alpha. That was the right call while legibility over an arbitrary
backdrop was the only thing being weighed, but it makes the app the one
thing on a light-mode Mac that never changes, and on a Mac set to Auto it
is wrong for half the day.

So the colours move here, as two palettes with the same shape, and the
window renders whichever one the system is currently asking for. Qt-free
on purpose: these are plain RGBA tuples, so the contrast maths in the
tests can weigh them without starting a Qt application, and there is
exactly one place to look for what colour anything is.

The promise the dark palette was built around is unchanged and now
applies to both: the sung line clears 4.5:1 against its own background
with the vibrancy material contributing NOTHING. What changes is which
backdrop is the hard one. White text on a dark scrim is worst over a
white page — the scrim is the palest it can get there. Dark text on a
light scrim is the mirror image: worst over a BLACK page, where the
scrim is the darkest it can get. Each palette is therefore measured
against the backdrop that is worst for it, and tests/test_scrim.py holds
both to the floor over both extremes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

# Straight-alpha RGBA, 0-255 per channel. Tuples rather than QColor so
# nothing here needs Qt; window.py converts at the point of use.
RGBA = tuple[int, int, int, int]


class Appearance(Enum):
    LIGHT = "light"
    DARK = "dark"


# Qt.ColorScheme, as published by QStyleHints. Named here so the mapping
# is testable without importing Qt.
COLOR_SCHEME_UNKNOWN = 0
COLOR_SCHEME_LIGHT = 1
COLOR_SCHEME_DARK = 2


def from_color_scheme(raw: int) -> Appearance:
    """What Qt's colour scheme means for this window.

    Only an explicit Light is light. Unknown is what a platform that will
    not answer reports — the offscreen plugin, an X server without a
    portal — and it resolves to dark, which is what this window has always
    been and the safer guess for something floating over somebody else's
    screen.
    """
    return Appearance.LIGHT if int(raw) == COLOR_SCHEME_LIGHT else Appearance.DARK


@dataclass(frozen=True)
class Palette:
    """Every colour the window draws, for one appearance.

    Flat and explicit rather than derived: a palette that computed its own
    light variant would be a second set of rules to keep honest, and the
    contrast floor is checked against the values that actually ship.
    """

    # The window itself. `scrim` is painted over the vibrancy material;
    # `solid` is the whole background when no material could be installed.
    scrim: RGBA
    solid: RGBA
    # The hairline around the panel, drawn one device pixel wide just
    # inside the fill. What separates a pane of glass from a rectangle:
    # light at low alpha over the dark panel, dark at low alpha over the
    # pale one, the way macOS edges its own HUD surfaces. Deliberately
    # NOT tinted with the album colour — a coloured hairline reads as a
    # border, and this one is meant to read as an edge.
    border: RGBA

    # Text, in the order it appears down the window.
    header: RGBA
    context: RGBA        # the previous and upcoming lines
    current: RGBA        # the sung line — the one with the 4.5:1 promise
    pronunciation: RGBA
    plain: RGBA
    progress: RGBA       # the sync pass's counter row

    # The overlay controls (loop, speak, and the wash behind them on hover).
    control_idle: RGBA
    control_hover: RGBA
    control_engaged: RGBA
    control_wash: RGBA
    scrollbar: RGBA

    # The echo-practice "your turn is over" button.
    attempt_text: RGBA
    attempt_fill: RGBA
    attempt_fill_hover: RGBA

    # The tap bar. Deliberately inverted against the window in both modes:
    # it is the one control the eye has to find without looking for it.
    tap_text: RGBA
    tap_fill: RGBA
    tap_fill_hover: RGBA
    tap_fill_pressed: RGBA
    tap_text_off: RGBA
    tap_fill_off: RGBA

    # Undo and exit, either side of the tap bar.
    sync_text: RGBA
    sync_fill: RGBA
    sync_text_hover: RGBA
    sync_text_off: RGBA
    exit_text_hover: RGBA
    exit_fill_hover: RGBA
    confirm_text: RGBA   # the armed "discard this sync?" prompt


# The palette this app has always had. Every value is what shipped before
# appearance following existed, so dark mode is unchanged by this feature
# rather than re-derived and slightly different.
#
# The scrim's alpha is measured, not chosen: 4.5:1 over a white page is
# crossed at 147 and this is 150 — enough that rounding cannot land under
# the threshold, little enough that it is still the lowest alpha the
# promise allows. More scrim would only cost blur.
DARK = Palette(
    scrim=(14, 15, 20, 150),
    solid=(18, 18, 24, 232),
    border=(255, 255, 255, 30),
    header=(255, 255, 255, 130),
    context=(255, 255, 255, 148),
    current=(255, 255, 255, 250),
    pronunciation=(255, 255, 255, 170),
    plain=(255, 255, 255, 205),
    progress=(130, 200, 255, 190),
    control_idle=(255, 255, 255, 105),
    control_hover=(255, 255, 255, 225),
    control_engaged=(130, 200, 255, 235),
    control_wash=(255, 255, 255, 26),
    scrollbar=(255, 255, 255, 70),
    attempt_text=(255, 214, 120, 240),
    attempt_fill=(255, 214, 120, 28),
    attempt_fill_hover=(255, 214, 120, 60),
    tap_text=(16, 18, 26, 245),
    tap_fill=(235, 242, 255, 225),
    tap_fill_hover=(255, 255, 255, 245),
    tap_fill_pressed=(130, 200, 255, 245),
    tap_text_off=(255, 255, 255, 110),
    tap_fill_off=(255, 255, 255, 30),
    sync_text=(255, 255, 255, 150),
    sync_fill=(255, 255, 255, 22),
    sync_text_hover=(255, 255, 255, 230),
    sync_text_off=(255, 255, 255, 60),
    exit_text_hover=(255, 160, 160, 240),
    exit_fill_hover=(255, 120, 120, 45),
    confirm_text=(255, 170, 170, 235),
)


# The mirror image, and measured the same way. Dark text on a light scrim
# is worst over a BLACK page, where 4.5:1 is crossed at alpha 131; this is
# 134, the same hair of rounding headroom the dark scrim keeps.
#
# The blues, the amber and the warning red are darkened rather than
# reused: (130, 200, 255) is a light-on-dark accent and washes out to
# nothing on a pale panel. How far to darken them was not an eye
# judgement either — each was swept until it matched or beat what the
# dark palette manages in ITS worst case, so no role is legible in one
# mode and marginal in the other. test_scrim.py pins that parity role by
# role, which is what stops a later tweak quietly regressing one side.
LIGHT = Palette(
    scrim=(246, 247, 250, 134),
    solid=(248, 249, 252, 236),
    border=(0, 0, 0, 38),
    header=(18, 19, 26, 140),
    context=(18, 19, 26, 165),
    current=(18, 19, 26, 250),
    pronunciation=(18, 19, 26, 180),
    plain=(18, 19, 26, 205),
    progress=(0, 70, 140, 235),
    control_idle=(18, 19, 26, 120),
    control_hover=(18, 19, 26, 225),
    control_engaged=(0, 60, 120, 240),
    control_wash=(0, 0, 0, 20),
    scrollbar=(18, 19, 26, 70),
    attempt_text=(140, 88, 0, 245),
    attempt_fill=(255, 190, 60, 70),
    attempt_fill_hover=(255, 190, 60, 120),
    tap_text=(245, 247, 255, 245),
    tap_fill=(32, 36, 50, 232),
    tap_fill_hover=(18, 20, 30, 245),
    tap_fill_pressed=(0, 105, 200, 245),
    tap_text_off=(18, 19, 26, 110),
    tap_fill_off=(0, 0, 0, 26),
    sync_text=(18, 19, 26, 158),
    sync_fill=(0, 0, 0, 20),
    sync_text_hover=(18, 19, 26, 235),
    sync_text_off=(18, 19, 26, 60),
    exit_text_hover=(175, 30, 30, 240),
    exit_fill_hover=(215, 60, 60, 50),
    confirm_text=(140, 10, 10, 250),
)


_PALETTES = {Appearance.LIGHT: LIGHT, Appearance.DARK: DARK}


def palette_for(appearance: Appearance) -> Palette:
    return _PALETTES[appearance]


def rgba(colour: RGBA) -> str:
    """One colour as a Qt stylesheet function call."""
    red, green, blue, alpha = colour
    return f"rgba({red}, {green}, {blue}, {alpha})"


# -- tinting the panel with the album's colour ---------------------------

# THE GOVERNING RULE: the artwork supplies a HUE and nothing else. Its
# luminance and its saturation are discarded and replaced with ours, per
# mode, so a near-white cover cannot produce a pale window and a hot pink
# one cannot produce a hot pink window. The failure this prevents is the
# obvious way to do it — sampling a colour and painting with it — which
# works beautifully for three albums and then meets a neon cover.
#
# How much colour a tint carries, per mode.
#
# These were 0.85 and 0.95, picked because measurement showed saturation
# was nearly free: pinning the luminance means sweeping 0.18 to 1.00 moves
# the worst-case contrast by about 0.01. That reasoning was sound and the
# conclusion was wrong — CONTRAST HEADROOM IS NOT AESTHETIC HEADROOM. What
# the floor permits and what looks like a pane of glass are different
# questions, and a near-fully-saturated wash answers only the first: it
# reads as a coloured panel rather than as the window quietly taking on
# the record's colour.
#
# So they are now set by eye at the point where the colour is FELT rather
# than noticed, and the floor is simply re-checked afterwards rather than
# used to justify the value. Light carries slightly more because a pale
# panel compresses saturation harder than a near-black one does.
TINT_SATURATION_DARK = 0.18
TINT_SATURATION_LIGHT = 0.22

TINT_SATURATION = {
    Appearance.DARK: TINT_SATURATION_DARK,
    Appearance.LIGHT: TINT_SATURATION_LIGHT,
}

# An artwork colour flatter than this has no hue worth taking — a black
# and white cover would otherwise be assigned whatever hue its noise
# happened to lean towards.
MIN_ARTWORK_SATURATION = 0.10


def relative_luminance(rgb) -> float:
    """WCAG relative luminance. The quantity the contrast floor is written
    in, and therefore the one a tint must not move."""
    def channel(value: float) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(c) for c in rgb[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def rgb_to_hsl(rgb) -> tuple[float, float, float]:
    """(hue 0-360, saturation 0-1, lightness 0-1)."""
    red, green, blue = (c / 255 for c in rgb[:3])
    high, low = max(red, green, blue), min(red, green, blue)
    lightness = (high + low) / 2
    if high == low:
        return 0.0, 0.0, lightness  # achromatic: no hue exists
    span = high - low
    saturation = (
        span / (high + low) if lightness <= 0.5 else span / (2.0 - high - low)
    )
    if high == red:
        hue = ((green - blue) / span) % 6
    elif high == green:
        hue = (blue - red) / span + 2
    else:
        hue = (red - green) / span + 4
    return hue * 60, saturation, lightness


def hsl_to_rgb(hue: float, saturation: float, lightness: float) -> tuple[int, int, int]:
    if saturation <= 0:
        value = round(lightness * 255)
        return value, value, value
    high = (
        lightness * (1 + saturation)
        if lightness < 0.5
        else lightness + saturation - lightness * saturation
    )
    low = 2 * lightness - high

    def component(offset: float) -> int:
        t = ((hue / 360) + offset) % 1.0
        if t < 1 / 6:
            value = low + (high - low) * 6 * t
        elif t < 1 / 2:
            value = high
        elif t < 2 / 3:
            value = low + (high - low) * (2 / 3 - t) * 6
        else:
            value = low
        return max(0, min(255, round(value * 255)))

    return component(1 / 3), component(0), component(-1 / 3)


def _at_luminance(hue: float, saturation: float, target: float) -> tuple[int, int, int]:
    """The colour of this hue and saturation whose relative luminance
    matches ``target``.

    Bisection rather than a formula: relative luminance is not HSL's
    lightness and the two disagree badly across hues — pure yellow and
    pure blue sit at the same HSL lightness and nowhere near the same
    luminance. Holding HSL lightness constant would therefore have moved
    the contrast floor by hue, which is exactly the bug a hue-only tint
    is supposed to avoid. Luminance rises monotonically with lightness at
    fixed hue and saturation, so this always converges.
    """
    low, high = 0.0, 1.0
    for _ in range(24):
        middle = (low + high) / 2
        if relative_luminance(hsl_to_rgb(hue, saturation, middle)) < target:
            low = middle
        else:
            high = middle
    return hsl_to_rgb(hue, saturation, (low + high) / 2)


def usable_hue(artwork_rgb) -> Optional[float]:
    """The hue to tint with, or None when the artwork has none worth
    taking. Callers treat None as "leave the palette alone"."""
    if artwork_rgb is None:
        return None
    hue, saturation, _ = rgb_to_hsl(artwork_rgb)
    if saturation < MIN_ARTWORK_SATURATION:
        return None
    return hue


def tinted(palette: Palette, artwork_rgb, appearance: Appearance) -> Palette:
    """``palette`` recoloured towards the artwork's hue.

    Only the two backgrounds move. Text keeps every value 12a measured,
    which is what lets the contrast floor be re-checked rather than
    re-derived: the thing behind the words changes hue at exactly the same
    luminance, and the words do not change at all.

    An unusable artwork colour returns the palette unchanged — the same
    object, so "no tint" and "tinting off" are indistinguishable
    downstream.
    """
    hue = usable_hue(artwork_rgb)
    if hue is None:
        return palette
    saturation = TINT_SATURATION[appearance]

    def recolour(colour: RGBA) -> RGBA:
        red, green, blue = _at_luminance(
            hue, saturation, relative_luminance(colour)
        )
        return (red, green, blue, colour[3])

    return replace(palette, scrim=recolour(palette.scrim), solid=recolour(palette.solid))


def blend(first: RGBA, second: RGBA, mix: float) -> RGBA:
    """``first`` towards ``second``, for the cross-fade between two tints."""
    mix = max(0.0, min(1.0, mix))
    return tuple(  # type: ignore[return-value]
        round(a + (b - a) * mix) for a, b in zip(first, second)
    )
