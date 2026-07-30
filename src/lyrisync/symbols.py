"""SF Symbols as button icons, with a text glyph to fall back to.

The overlay's controls were drawn with whatever characters rendered
monochrome — a beamed note for "speak this line", because macOS has no
monochrome speaker in Unicode and 🔊 comes out in colour whatever is asked
of it. That was a workaround for a font problem, and the native answer was
always SF Symbols: `text.bubble` is the system's own glyph for a line
being spoken, drawn at the weight and optical size Apple ships it at.

A symbol arrives as a template image — shape in the alpha channel, no
colour of its own — which is exactly what a button whose colour comes from
a stylesheet needs. The tint is applied here instead, once per state, so
the three states the stylesheet used to paint (idle, hover, speaking)
survive the move from text to icon.

Nothing here is required: off macOS, without pyobjc, or on a macOS too old
for a given symbol, ``symbol_icon`` returns None and the caller keeps its
text glyph. That is what lets the whole suite run headless on Linux.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from lyrisync import menubar

logger = logging.getLogger(__name__)

# The system's glyph for a spoken line. Available since macOS 11, which is
# also the floor for SF Symbols existing at all.
SPEAK_SYMBOL = "text.bubble"

# What the button says when the symbol cannot be had. Not 🔊: it renders in
# colour whatever is asked of it, and macOS has no monochrome speaker glyph
# (U+1F56A and friends fall back to a striped tofu box).
SPEAK_FALLBACK_GLYPH = "♬"

# NSFont weights, as used by NSImageSymbolConfiguration.
_WEIGHT_REGULAR = 5.0


def available() -> bool:
    """Whether this process can load SF Symbols at all."""
    if QApplication.platformName() != "cocoa":
        return False
    try:
        from AppKit import NSImage  # noqa: F401
    except ImportError:
        return False
    return True


def device_pixel_ratio() -> float:
    """The screen's scale factor, or 1 with no screen (offscreen, tests)."""
    screen = QApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


def _template_pixmap(name: str, point_size: float, ratio: float = 1.0) -> Optional[QPixmap]:
    """The raw symbol as a pixmap: black shape, alpha carrying the form.

    Asked for at ``point_size * ratio`` and then labelled with that ratio,
    so a Retina screen gets a symbol drawn at 2x and shown at 1x rather
    than a 1x symbol stretched — the difference between a crisp glyph and
    a soft one, and the reason this takes a point size at all instead of
    scaling one pixmap around.
    """
    try:
        from AppKit import NSImage, NSImageSymbolConfiguration
    except ImportError:
        logger.warning("pyobjc unavailable — no SF Symbols")
        return None
    try:
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
        if image is None:
            logger.warning("no SF Symbol named %r on this macOS", name)
            return None
        configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            point_size * ratio, _WEIGHT_REGULAR
        )
        configured = image.imageWithSymbolConfiguration_(configuration) or image
        data = configured.TIFFRepresentation()
        if data is None:
            logger.warning("SF Symbol %r produced no image data", name)
            return None
        pixmap = QPixmap()
        if not pixmap.loadFromData(bytes(data)):
            logger.warning("SF Symbol %r did not decode", name)
            return None
        pixmap.setDevicePixelRatio(ratio)
        return pixmap
    except Exception:
        logger.exception("failed to load SF Symbol %r", name)
        return None


def tinted(pixmap: QPixmap, colour: QColor) -> QPixmap:
    """Recolour a template pixmap, keeping its alpha.

    SourceIn paints the colour only where the glyph already is, which is
    what makes a template image a template image: the shape is the alpha
    channel and the colour is ours to choose.
    """
    painted = QPixmap(pixmap.size())
    painted.setDevicePixelRatio(pixmap.devicePixelRatio())
    painted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(painted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(painted.rect(), colour)
    painter.end()
    return painted


def symbol_icon(
    name: str,
    point_size: float,
    normal: QColor,
    active: Optional[QColor] = None,
    disabled: Optional[QColor] = None,
) -> Optional[QIcon]:
    """An SF Symbol as a multi-state QIcon, or None if unavailable.

    The three states are baked in as separate pixmaps because a stylesheet
    colours text, not icons: Qt picks Active while the cursor is over the
    button and Disabled while the line is being spoken, which is the same
    hover/speaking pair the stylesheet used to draw.
    """
    if not available():
        return None
    template = _template_pixmap(name, point_size, device_pixel_ratio())
    if template is None:
        return None
    icon = QIcon()
    icon.addPixmap(tinted(template, normal), QIcon.Mode.Normal)
    icon.addPixmap(tinted(template, active or normal), QIcon.Mode.Active)
    icon.addPixmap(tinted(template, disabled or normal), QIcon.Mode.Disabled)
    return icon


def icon_from_tiff(data: bytes, points: int) -> Optional[QIcon]:
    """An icon decoded from TIFF bytes, labelled with the scale it came at.

    For application icons, which arrive from ``frontmost.app_icon_tiff``
    as bytes rather than as an NSImage — nothing pyobjc-shaped crosses out
    of that module, so this end needs no AppKit and the test suite can
    hand over a file's worth of bytes.

    Not a template image, unlike everything else here: an app's icon is
    its own artwork and colouring it would be defacing somebody's brand.
    The device pixel ratio is derived from what actually decoded rather
    than from the screen — the drawing was done by macOS at whatever scale
    it chose, and dividing pixels by points is what that scale IS.
    """
    if not data or points <= 0:
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(data):
        logger.debug("an application icon did not decode")
        return None
    ratio = max(1.0, pixmap.width() / points)
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


def draw_menubar_glyph(painter: QPainter, side: float, spec: menubar.IconSpec) -> None:
    """Paint the glyph into a ``side``x``side`` square, from menubar's geometry.

    Solid black with the form in the alpha channel — a template image, as the
    three SVGs this replaced were, so macOS tints it for a light or dark menu
    bar. Dimming is the brush's alpha rather than a grey, which is what makes
    the dim glyph read as the same icon with less ink.
    """
    scale = side / menubar.GLYPH_UNITS
    ink = QColor(0, 0, 0)
    ink.setAlphaF(menubar.DIM_ALPHA if spec.dimmed else menubar.FULL_ALPHA)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    centre_x = menubar.bar_centre_x(spec.dot)
    for length, thickness, centre_y in zip(
        spec.lengths, menubar.BAR_THICKNESSES, menubar.BAR_CENTRES_Y
    ):
        bar = QRectF(
            (centre_x - length / 2) * scale,
            (centre_y - thickness / 2) * scale,
            length * scale,
            thickness * scale,
        )
        radius = thickness / 2 * scale
        painter.drawRoundedRect(bar, radius, radius)
    if spec.dot:
        dot_x, dot_y = menubar.DOT_CENTRE
        radius = menubar.DOT_RADIUS * scale
        painter.drawEllipse(QPointF(dot_x * scale, dot_y * scale), radius, radius)


def menubar_pixmap(spec: menubar.IconSpec, side: int) -> QPixmap:
    """The glyph as a ``side``x``side`` pixmap of actual pixels."""
    side = max(1, int(side))
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_menubar_glyph(painter, side, spec)
    painter.end()
    return pixmap


class _MenubarIconEngine(QIconEngine):
    """Renders the glyph at whatever size it is asked for.

    An engine rather than a pixmap, and that was MEASURED rather than
    preferred. Handing QSystemTrayIcon a 44-pixel pixmap at
    devicePixelRatio 2 — logically 22x22, exactly what the SVG it replaced
    was — put a CLIPPED glyph on the menu bar: two of the three bars and no
    practice dot. ``QIcon.availableSizes()`` reports raw pixels and does not
    fold the ratio in, so the status item took a 44-point image for a
    22-point slot and showed its top two thirds.

    Four constructions were put on a real status item and photographed:

    - 44px at ratio 2 — clipped
    - 36px at ratio 2 — clipped
    - both 22px and 44px in one icon — clipped, Qt takes the larger
    - 22px at ratio 1 — whole, but upscaled by the compositor and soft
    - this engine — whole, and the crispest of the five

    Which is what the SVG engine had been doing all along: rendering on
    demand at the size actually wanted. Nothing else here needs an engine
    because nothing else is handed to a status item.
    """

    def __init__(self, spec: menubar.IconSpec) -> None:
        super().__init__()
        self._spec = spec

    def paint(self, painter, rect, mode, state) -> None:
        side = max(1, min(rect.width(), rect.height()))
        painter.drawPixmap(rect, menubar_pixmap(self._spec, side))

    def pixmap(self, size, mode, state) -> QPixmap:
        return menubar_pixmap(self._spec, min(size.width(), size.height()))

    def clone(self) -> QIconEngine:
        return _MenubarIconEngine(self._spec)

    def availableSizes(self, mode=None, state=None) -> list:
        # The size the menu bar actually wants. Reported honestly, because
        # this is the number the clipping bug turned on.
        return [QSize(menubar.GLYPH_UNITS, menubar.GLYPH_UNITS)]


# QIcon takes ownership of an engine on the C++ side, but the Python object
# has to outlive it or the icon is left painting through a collected wrapper.
# Bounded by the number of specs that exist — eight, or eighteen with the
# optional animation — because the window asks for each one once.
_ENGINES: list = []


def menubar_icon(spec: menubar.IconSpec) -> QIcon:
    """The menu bar glyph for one spec, drawn rather than loaded.

    Three SVG files became eight combinations of brightness, shape and dot in
    milestone 15.1, and eighteen once the optional animation is counted — so
    the glyph is painted from ``menubar``'s geometry instead of shipped as
    images. Nothing about the shape changed in the move: the bar thicknesses,
    centres and the dot are the numbers the SVGs carried.
    """
    engine = _MenubarIconEngine(spec)
    _ENGINES.append(engine)
    icon = QIcon(engine)
    icon.setIsMask(True)  # a template image: macOS owns the colour
    return icon


def icon_size(button_side: int) -> QSize:
    """Glyph box inside a button box, leaving the padding the hover
    highlight needs to read as a rounded square around it."""
    edge = max(10, round(button_side * 0.62))
    return QSize(edge, edge)
