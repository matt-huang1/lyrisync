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
    # pale one, the way macOS edges its own HUD surfaces. This is the
    # untinted one; with a cover in hand it carries the album's hue, and
    # carries it far harder than the panel can (see BORDER_CHROMA).
    border: RGBA
    # The warm colour the hairline is briefly mixed towards when a window
    # position has just been learned. Deliberately NOT tinted by the album:
    # an acknowledgement that changed colour with the cover would be read
    # as part of the artwork rather than as an answer, and the answer is
    # the same every time. Warm because every other accent in this app is
    # cool — nothing else on the window can be mistaken for it.
    learned_glow: RGBA

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
    # The amber this app already uses for "your turn" — one warm accent,
    # not two.
    learned_glow=(255, 214, 120, 165),
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
    # Darkened like every other accent here: (255, 214, 120) on a pale
    # panel is a hairline that has gone missing rather than warm.
    learned_glow=(150, 96, 0, 170),
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
# How much colour a tint carries, as the CHROMA of the finished panel —
# the spread between its strongest and weakest channel, 0-255, after the
# background's own alpha has diluted it.
#
# Chroma, not HSL saturation, and that change is the fix for a real bug.
# Saturation collapses at both ends of the lightness range: the light
# scrim sits at L=0.973 where even S=1.0 can only produce 14/255 of
# chroma, while the dark scrim at L=0.067 gets 34/255 from the same
# number. So one saturation meant 2.4x more colour in dark than in light,
# and at 0.22 the light tint moved the scrim by 3/255 — less than the
# palette's own built-in blue cast, which is why album colour looked
# switched off in light mode however strong the cover was.
#
# Stated this way the number means the same thing in both modes and can
# be reasoned about: this is how much colour ends up on screen.
#
# The two values still differ, because the eye is not linear either. At
# low luminance a given chroma reads as less colourful (the Hunt effect),
# so the dark panel needs more of it to feel equally tinted. Both are set
# by eye against real covers, and the contrast floor is re-checked
# afterwards rather than used to justify them.
TINT_CHROMA_DARK = 14.0
TINT_CHROMA_LIGHT = 12.0

TINT_CHROMA = {
    Appearance.DARK: TINT_CHROMA_DARK,
    Appearance.LIGHT: TINT_CHROMA_LIGHT,
}


# -- the hairline, where contrast is not at stake -------------------------

# The panel has no room left for colour and that is not a tuning failure:
# its luminance is pinned by the 4.5:1 promise, and at the light panel's
# 0.93 the hues that carry little luminance of their own are nearly white
# already. Buying their colour costs brightness the floor will not give
# up. So the colour moves to the one part of the window where the floor
# has no opinion at all — nothing is read against the hairline, and
# nothing is read against the shadow either.
#
# Freed of the luminance pin, the hairline can pin HSL LIGHTNESS instead,
# and that is worth more than it sounds: at a fixed lightness the chroma
# of a colour is exactly saturation x (1 - |2L - 1|) and does not depend
# on the hue at all. The panel's chroma has to be bisected for, hue by
# hue, and still lands anywhere from 4.7 to 14. The hairline's is the
# same number for all 360 of them, by construction — the uniformity the
# panel could never have.
#
# Stated the same way as the panel's, as the chroma the hairline colour
# contributes to the finished edge once its own alpha has diluted it, so
# the two numbers can be compared: this is roughly 3.5x what the panel
# carries in dark mode and 4x in light.
BORDER_CHROMA = 46.0

# Lightness and alpha per mode, and these are the numbers that keep the
# hairline an EDGE rather than just a coloured line: the tinted edge has
# to stay lighter than the dark panel and darker than the pale one, for
# every hue, over the backdrop that suits each mode least. That is what
# fixes the two lightnesses — the constraint binds on blue in dark mode
# and on yellow in light mode, the hues furthest from their panel in
# luminance, and it is measured in tests/test_scrim.py rather than
# assumed.
BORDER_LIGHTNESS = {Appearance.DARK: 0.72, Appearance.LIGHT: 0.30}
BORDER_ALPHA = {Appearance.DARK: 110, Appearance.LIGHT: 105}


def chroma_of(rgb) -> int:
    """The spread between a colour's strongest and weakest channel — how
    much colour it carries, in the unit the tint is specified in."""
    return max(rgb[:3]) - min(rgb[:3])


def tinted_border(hue: float, appearance: Appearance) -> RGBA:
    """The hairline in the album's hue.

    No bisection here, unlike the panel: with the lightness pinned the
    chroma of an HSL colour is exactly ``saturation x (1 - |2L - 1|)``, so
    the saturation that delivers what was asked for is arithmetic. The
    closed form is only untrustworthy when something else is being held
    (the panel holds luminance, which moves the lightness per hue and
    makes the same formula answer a different question).
    """
    lightness = BORDER_LIGHTNESS[appearance]
    alpha = BORDER_ALPHA[appearance]
    # The chroma is asked for on the finished edge, so the colour itself
    # has to carry more of it the more transparent it is — the same
    # convention the panel's tint uses.
    wanted = BORDER_CHROMA / (alpha / 255) / 255
    saturation = min(1.0, wanted / (1 - abs(2 * lightness - 1)))
    return (*hsl_to_rgb(hue, saturation, lightness), alpha)


def _at_chroma(hue: float, target: float, luminance: float):
    """The colour of this hue with this chroma, at this exact luminance.

    Bisects on SATURATION against the chroma actually achieved, rather
    than computing a saturation from HSL's closed form. The closed form
    answers a different question: it gives the chroma at some lightness,
    but pinning the luminance then MOVES the lightness, by wildly
    different amounts per hue — matching luminance 0.93 costs a yellow
    almost nothing (blue carries 7% of luminance, so it can drop to 51
    and stay bright) and costs a blue everything.

    Feeding that back as a correction does not converge, it oscillates:
    yellow alternated between chroma 204 and chroma 2 on successive
    rounds, and stopping after two landed on whichever the parity chose.
    That is what made a yellow cover come out LESS coloured than the
    untinted palette. Chroma rises monotonically with saturation at fixed
    luminance, so bisecting on it converges instead of arguing with
    itself, and a hue that cannot reach the target simply converges to
    the most it can carry.
    """
    low, high = 0.0, 1.0
    for _ in range(20):
        middle = (low + high) / 2
        if chroma_of(hsl_to_rgb(hue, middle, 0.5)) == 0:
            low = middle
            continue
        if chroma_of(_at_luminance(hue, middle, luminance)) < target:
            low = middle
        else:
            high = middle
    return _at_luminance(hue, (low + high) / 2, luminance)

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

    The two backgrounds move, at their own luminance, by a few units of
    chroma. The hairline moves properly: it is the one surface with no
    contrast obligation, so it takes the hue at four times the strength
    and is where the album is actually felt. Text keeps every value 12a
    measured, which is what lets the contrast floor be re-checked rather
    than re-derived: the thing behind the words changes hue at exactly the
    same luminance, and the words do not change at all.

    An unusable artwork colour returns the palette unchanged — the same
    object, so "no tint" and "tinting off" are indistinguishable
    downstream.
    """
    hue = usable_hue(artwork_rgb)
    if hue is None:
        return palette
    target = TINT_CHROMA[appearance]

    def recolour(colour: RGBA) -> RGBA:
        # The chroma is asked for on the FINISHED panel, so the colour
        # itself has to carry more of it the more transparent it is —
        # otherwise the scrim (alpha 134) and the solid fallback (alpha
        # 236) would be tinted to the same value and look different.
        alpha = max(1, colour[3]) / 255
        red, green, blue = _at_chroma(
            hue, target / alpha, relative_luminance(colour)
        )
        return (red, green, blue, colour[3])

    return replace(
        palette,
        scrim=recolour(palette.scrim),
        solid=recolour(palette.solid),
        border=tinted_border(hue, appearance),
    )


def blend(first: RGBA, second: RGBA, mix: float) -> RGBA:
    """``first`` towards ``second``, for the cross-fade between two tints."""
    mix = max(0.0, min(1.0, mix))
    return tuple(  # type: ignore[return-value]
        round(a + (b - a) * mix) for a, b in zip(first, second)
    )
