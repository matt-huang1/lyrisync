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

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

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


def icon_size(button_side: int) -> QSize:
    """Glyph box inside a button box, leaving the padding the hover
    highlight needs to read as a rounded square around it."""
    edge = max(10, round(button_side * 0.62))
    return QSize(edge, edge)
