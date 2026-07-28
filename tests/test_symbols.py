"""SF Symbol loading and its fallback.

Nothing here is macOS-only. The symbol path is guarded off-cocoa in the
code, so on the Linux runner these assert the fallback contract — the part
that matters everywhere: no symbol must never mean no button.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip(
    "PySide6.QtGui",
    reason="PySide6 unusable (missing system Qt libraries?)",
    exc_type=ImportError,
)

from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from lyrisync import symbols  # noqa: E402

WHITE = QColor(255, 255, 255, 255)
BLUE = QColor(130, 200, 255, 235)


def test_symbols_are_unavailable_off_cocoa(monkeypatch):
    """The offscreen platform is not macOS, so nothing may try to import
    AppKit — that is what keeps the suite running on the runner."""
    monkeypatch.setattr(symbols.QApplication, "platformName", staticmethod(lambda: "offscreen"))
    assert symbols.available() is False
    assert symbols.symbol_icon(symbols.SPEAK_SYMBOL, 14.0, WHITE) is None


def test_an_unavailable_symbol_never_leaves_the_button_blank():
    """The contract the window relies on: symbol_icon returning None means
    'keep what you had', so the speak button keeps its text glyph rather
    than becoming an empty square."""
    assert symbols.SPEAK_FALLBACK_GLYPH
    assert symbols.SPEAK_FALLBACK_GLYPH != "🔊"  # renders in colour, always


def test_a_missing_symbol_name_is_survivable(monkeypatch):
    """A symbol that does not exist on this macOS answers None rather than
    raising: the name is a string Apple owns, not a promise."""
    monkeypatch.setattr(symbols, "available", lambda: True)
    monkeypatch.setattr(symbols, "_template_pixmap", lambda *a: None)
    assert symbols.symbol_icon("no.such.symbol", 14.0, WHITE) is None


def test_tinting_keeps_the_shape_and_replaces_the_colour():
    """What makes a template image usable: the glyph is the alpha channel,
    so the colour is ours. Painted here rather than mocked, since this is
    plain Qt compositing and runs anywhere."""
    template = QPixmap(4, 4)
    template.fill(QColor(0, 0, 0, 255))  # a solid 'glyph'
    painted = symbols.tinted(template, BLUE)

    image = painted.toImage()
    assert painted.size() == template.size()
    pixel = image.pixelColor(2, 2)
    assert (pixel.red(), pixel.green(), pixel.blue()) == (130, 200, 255)


def test_tinting_leaves_transparent_pixels_alone():
    template = QPixmap(4, 4)
    template.fill(QColor(0, 0, 0, 0))  # all hole, no glyph
    painted = symbols.tinted(template, BLUE)
    assert painted.toImage().pixelColor(2, 2).alpha() == 0


def test_the_icon_carries_all_three_states(monkeypatch):
    """Idle, hover and speaking were three stylesheet colours; an icon has
    to bake them in, because a stylesheet colours text and not pixmaps."""
    template = QPixmap(8, 8)
    template.fill(QColor(0, 0, 0, 255))
    monkeypatch.setattr(symbols, "available", lambda: True)
    monkeypatch.setattr(symbols, "_template_pixmap", lambda *a: template)

    icon = symbols.symbol_icon(
        symbols.SPEAK_SYMBOL,
        14.0,
        QColor(255, 255, 255, 105),
        active=QColor(255, 255, 255, 225),
        disabled=BLUE,
    )
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    modes = (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Disabled)
    colours = set()
    for mode in modes:
        pixel = icon.pixmap(QSize(8, 8), mode).toImage().pixelColor(4, 4)
        # Alpha included: idle and hover are the same white at different
        # strengths, which is the whole difference between them.
        colours.add((pixel.red(), pixel.green(), pixel.blue(), pixel.alpha()))
    assert len(colours) == 3, f"states must be distinguishable, got {colours}"


def test_the_glyph_box_leaves_room_inside_the_button():
    """The hover highlight is a rounded square around the glyph; a symbol
    filling the whole box would leave no square to see."""
    for side in (22, 28, 40):
        assert symbols.icon_size(side).width() < side
    assert symbols.icon_size(4).width() >= 10  # never vanishes at min scale
