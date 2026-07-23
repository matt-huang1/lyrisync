"""Korean romanisation for lyric lines. Pure text transforms — imports
nothing from other lyrisync modules."""

from __future__ import annotations

from korean_romanizer.romanizer import Romanizer as _Romanizer

# Hangul unicode ranges: precomposed syllables, combining jamo, and the
# compatibility jamo block (ㅋㅋㅋ-style letters used alone).
_HANGUL_RANGES = (
    ("가", "힣"),  # syllables 가-힣
    ("ᄀ", "ᇿ"),  # combining jamo
    ("㄰", "㆏"),  # compatibility jamo (ㅋ etc.)
)


def contains_hangul(text: str) -> bool:
    return any(
        low <= char <= high for char in text for low, high in _HANGUL_RANGES
    )


def romanize_korean(text: str) -> str:
    """Revised-Romanization of the hangul in ``text``; anything else passes
    through unchanged (the romanizer only transforms hangul, so mixed
    Korean/English lines keep their English words). A line the library
    chokes on comes back untouched — never crash a lyrics display."""
    if not contains_hangul(text):
        return text
    try:
        return _Romanizer(text).romanize()
    except Exception:
        return text
