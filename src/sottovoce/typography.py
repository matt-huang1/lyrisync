"""The window's type scale: how big and how heavy every row is.

Pure and Qt-free. The platform's UI family is resolved by the window and
passed in, so the sizes, weights and fallback chain can be reasoned about —
and tested — without a display.

Hierarchy, loudest to quietest. The current line carries the weight and is
the only semibold thing on screen. Its pronunciation sits lighter and
smaller directly beneath it, close enough to read as one block. The context
lines above and below are regular weight and dimmer: present, but never
competing with the line being sung. The header is smallest of all — it is
metadata, not lyrics.

Sizes are base pixels at scale 1.0; the window multiplies by its width-
derived scale, so this file changes proportions, never the scaling system.
geometry.py imports the same numbers to compute the minimum window height,
so the two can never disagree about how tall a row is.
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = "header"
CONTEXT = "dim"  # the previous and upcoming lines
CURRENT = "current"
PRONUNCIATION = "pron"
PLAIN = "plain"
PROGRESS = "progress"


@dataclass(frozen=True)
class Style:
    size_px: int
    weight: int
    # Letter-spacing in base px at scale 1.0, negative to tighten. Only
    # the large text carries any: type set at display size looks loose at
    # the tracking that suits body text, which is why every type foundry
    # ships optical sizes and why the system font tightens as it grows.
    # The small roles stay at 0 — tightening them costs legibility and
    # buys nothing.
    tracking: float = 0.0


# (base px at scale 1.0, weight, tracking). Weights are stated for every
# role rather than left to inherit: the contrast between the current line
# and its neighbours is the whole hierarchy, and a default would put it at
# the mercy of whatever Qt picks per platform.
#
# The sung line is deliberately far away from its neighbours on both axes
# — 20/700 against 13/400 is a 1.54x size ratio and three weight steps,
# where it used to be 18/600 against 14/400 (1.29x and one step). At the
# old separation the eye had to read the window to find the current line;
# at this one it lands on it.
_ROLES = {
    HEADER: Style(11, 500),
    CONTEXT: Style(13, 400),
    CURRENT: Style(20, 700, tracking=-0.35),
    PRONUNCIATION: Style(12, 400),
    PLAIN: Style(14, 400),
    PROGRESS: Style(11, 500),
}

# Vertical rhythm, base px at scale 1.0.
#
# The gap between the current line and its pronunciation is deliberately
# far tighter than the gap between rows, so the pair reads as one block
# instead of three separate lines. CURRENT_SPACING is extra room ABOVE
# AND BELOW that block on top of the row gap: the sung line is what the
# window is for, and it needs air around it that the context lines do
# not. geometry.py's height floor accounts for all of these.
ROW_SPACING = 10
PRONUNCIATION_SPACING = 3
CURRENT_SPACING = 5
TOP_MARGIN = 14
BOTTOM_MARGIN = 16

# How far a line travels vertically as it is replaced, base px at scale
# 1.0. Still a hint of motion in the direction the song is going rather
# than a transition — but paced over 260ms rather than 100ms, and a
# distance that read as a twitch at the old speed reads as too little at
# this one. Distance and duration are a pair.
LINE_TRAVEL = 10

# Korean first, because the UI family carries no hangul of its own and this
# is the face CoreText falls back to anyway — naming it makes the fallback
# explicit instead of implicit. Helvetica Neue is the last resort before
# Qt's own default.
_FALLBACK_FAMILIES = ("Apple SD Gothic Neo", "Helvetica Neue")


def style_for(role: str, scale: float = 1.0) -> Style:
    """Pixel size, weight and tracking for a row at this window scale.
    Sizes floor at 1px so an absurd scale can never produce an invisible
    or invalid font. Tracking scales with the type it belongs to, and is
    not rounded — Qt takes a fractional letter-spacing, and rounding a
    third of a pixel to zero would delete the whole effect at small
    scales."""
    base = _ROLES[role]
    return Style(
        size_px=max(1, round(base.size_px * scale)),
        weight=base.weight,
        tracking=round(base.tracking * scale, 3),
    )


def base_size(role: str) -> int:
    """Unscaled size, for callers doing their own layout arithmetic."""
    return _ROLES[role].size_px


# The sung line's size, in points, when the COMPACT layout is choosing it
# instead of the window's width.
#
# The full layout derives its scale from the width, and that is what makes
# dragging an edge a size control there. In a strip it is what makes
# widening the window useless: the room for a line and the line itself grow
# at the same rate, so the ratio between them is a constant and a line that
# elides at one width elides at every width. Naming the size directly is
# what gives the width something to do — with the type held still, a wider
# window is more of the line.
#
# Five steps of about 20%, which is an ordinary type-scale interval, with
# the app's own sung-line size in the middle. It is in the middle rather
# than beside a default set here, because a second statement of "20" is a
# second thing to keep right.
COMPACT_TEXT_SIZES = (14, 17, 20, 24, 28)
DEFAULT_COMPACT_TEXT_SIZE = _ROLES[CURRENT].size_px


def compact_scale(size_px: int) -> float:
    """The type scale that puts the sung line at ``size_px``.

    A scale rather than a size, because everything else on the strip — the
    gutters, the buttons, the margins, the height floor — is already
    proportional to one, and a strip whose text changed size while its
    controls did not would be the type scale with a hole in it.
    """
    return size_px / _ROLES[CURRENT].size_px


def font_stack(system_family: str) -> str:
    """A Qt stylesheet font-family list: the platform UI font first, then
    the fallbacks, deduplicated. Every family is quoted — the macOS system
    family is reported as ``.AppleSystemUIFont``, and an unquoted leading
    dot is not a valid stylesheet identifier."""
    families = []
    for family in (system_family, *_FALLBACK_FAMILIES):
        if family and family not in families:
            families.append(family)
    return ", ".join(f'"{family}"' for family in families)
