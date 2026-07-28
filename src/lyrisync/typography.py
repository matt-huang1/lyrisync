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


# (base px at scale 1.0, weight). Weights are stated for every role rather
# than left to inherit: the contrast between the current line and its
# neighbours is the whole hierarchy, and a default would put it at the
# mercy of whatever Qt picks per platform.
_ROLES = {
    HEADER: Style(11, 500),
    CONTEXT: Style(14, 400),
    CURRENT: Style(18, 600),
    PRONUNCIATION: Style(12, 400),
    PLAIN: Style(14, 400),
    PROGRESS: Style(11, 500),
}

# Vertical rhythm, base px at scale 1.0. The gap between the current line
# and its pronunciation is deliberately far tighter than the gap between
# rows, so the pair reads as one block instead of three separate lines.
ROW_SPACING = 8
PRONUNCIATION_SPACING = 2
TOP_MARGIN = 13
BOTTOM_MARGIN = 15

# Korean first, because the UI family carries no hangul of its own and this
# is the face CoreText falls back to anyway — naming it makes the fallback
# explicit instead of implicit. Helvetica Neue is the last resort before
# Qt's own default.
_FALLBACK_FAMILIES = ("Apple SD Gothic Neo", "Helvetica Neue")


def style_for(role: str, scale: float = 1.0) -> Style:
    """Pixel size and weight for a row at this window scale. Sizes floor at
    1px so an absurd scale can never produce an invisible or invalid font."""
    base = _ROLES[role]
    return Style(size_px=max(1, round(base.size_px * scale)), weight=base.weight)


def base_size(role: str) -> int:
    """Unscaled size, for callers doing their own layout arithmetic."""
    return _ROLES[role].size_px


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
