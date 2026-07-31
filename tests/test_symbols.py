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

from sottovoce import symbols  # noqa: E402

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


# -- the menu bar glyph ----------------------------------------------------


def test_the_glyph_is_drawn_at_the_screens_scale_and_labelled_in_points():
    """THE CLIPPING BUG, pinned, in the form it takes now.

    Handing QSystemTrayIcon a 44-pixel pixmap at devicePixelRatio 2 —
    logically 22x22, which is what the SVG it replaced was — put a CLIPPED
    glyph on the menu bar: two of the three bars and no practice dot.
    ``QIcon.availableSizes()`` reported RAW PIXELS and did not fold the ratio
    in, so the status item took a 44-point image for a 22-point slot and drew
    its top two thirds. Four constructions were photographed on a real status
    item before an icon engine was chosen.

    The item is an NSStatusItem this app makes now, so there is no QIcon in
    the way: the glyph is PIXELS at the screen's own scale, and nsmenu.py
    labels the image with the point size the menu bar wants. This is the
    pixel half — that the drawing is at ``points * ratio`` and not at
    ``points`` — and it is the half the bug turned on.
    """
    from sottovoce import menubar

    spec = menubar.icon_spec(playing=True, lyrics_visible=True, practising=True)
    ratio = max(1, int(round(symbols.device_pixel_ratio())))
    png = symbols.menubar_png(spec, menubar.GLYPH_UNITS)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    pixmap = QPixmap()
    assert pixmap.loadFromData(png)
    assert pixmap.width() == pixmap.height() == menubar.GLYPH_UNITS * ratio


def test_the_glyph_is_drawn_whole_at_whatever_size_is_asked_for():
    """Rendered on demand at the size actually wanted, which is what the SVG
    engine had been doing all along. Whole, meaning the dot in the far corner
    is present: it is the first thing a clipped glyph loses."""
    from sottovoce import menubar

    spec = menubar.icon_spec(playing=True, lyrics_visible=True, practising=True)
    for side in (16, 18, 22, 44, 64):
        image = symbols.menubar_pixmap(spec, side).toImage()
        assert image.width() == image.height() == side
        corner = image.pixelColor(
            round(menubar.DOT_CENTRE[0] * side / menubar.GLYPH_UNITS),
            round(menubar.DOT_CENTRE[1] * side / menubar.GLYPH_UNITS),
        )
        assert corner.alpha() > 0, side


def test_the_glyph_is_a_template_image():
    """macOS owns the colour, which is why the practice mark is a DOT and not
    a hue: a coloured menu bar icon stops following the menu bar.

    Two halves. The pixels are black with the shape in the ALPHA channel,
    which is measured here; and the image is told it is a template, which is
    one call inside nsmenu.py's one door and is asserted where that door is
    tested.
    """
    from sottovoce import menubar

    spec = menubar.icon_spec(playing=False, lyrics_visible=True, practising=False)
    image = symbols.menubar_pixmap(spec, 44).toImage()
    colours = {
        image.pixelColor(x, y).getRgb()[:3]
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    }
    assert colours == {(0, 0, 0)}


def test_a_dimmed_glyph_carries_less_ink_than_a_bright_one():
    """Dimming has to be the same shape at lower alpha, not a grey — a grey
    would stop following the menu bar. Measured from the drawn alpha."""
    from sottovoce import menubar

    def ink(spec):
        pixmap = symbols.menubar_pixmap(spec, 44)
        image = pixmap.toImage()
        return sum(
            image.pixelColor(x, y).alpha()
            for y in range(image.height())
            for x in range(image.width())
        )

    bright = menubar.icon_spec(playing=True, lyrics_visible=True, practising=False)
    dim = menubar.icon_spec(playing=True, lyrics_visible=False, practising=False)
    assert bright.lengths == dim.lengths, "the same shape, by construction"
    assert 0 < ink(dim) < ink(bright)
    assert ink(dim) == pytest.approx(ink(bright) * menubar.DIM_ALPHA, rel=0.02)


def test_the_practice_dot_adds_ink_without_changing_the_bars():
    from sottovoce import menubar

    plain = menubar.icon_spec(playing=True, lyrics_visible=True, practising=False)
    dotted = menubar.icon_spec(playing=True, lyrics_visible=True, practising=True)
    assert plain.lengths == dotted.lengths
    assert not symbols.menubar_pixmap(plain, 44).isNull()
    assert (
        symbols.menubar_pixmap(dotted, 44).toImage()
        != symbols.menubar_pixmap(plain, 44).toImage()
    )


def test_a_checkable_button_gets_its_engaged_colour_as_a_state():
    """The distinction that made the first attempt do nothing visible.

    A checked QPushButton draws its icon in ``QIcon.State.On``, still in
    Normal or Active MODE. ``QIcon.Mode.Selected`` is what an item view
    asks for and a button never does — so an engaged colour baked in there
    is a colour nothing ever draws. Found by screenshot: the control
    stayed grey when checked.
    """
    import ast
    import inspect

    # Scanned as syntax, not as text: this test's own explanation names
    # the symbol it forbids, and so does the function's docstring. A
    # substring scan could only be satisfied by deleting the reasoning,
    # which is the trap test_notifications.py already documents.
    tree = ast.parse(inspect.getsource(symbols.symbol_icon))
    body = tree.body[0].body[1:]  # everything after the docstring
    used = {
        node.attr
        for statement in body
        for node in ast.walk(statement)
        if isinstance(node, ast.Attribute)
    }
    assert "Selected" not in used
    assert "On" in used
