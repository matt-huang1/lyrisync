"""Floating always-on-top lyrics window — the main sottovoce app.

Run with ``sottovoce``. Spotify polling runs on a worker QThread that emits
snapshots to the UI through queued signals, and LRCLIB fetches run on the
global QThreadPool — the UI thread never runs a subprocess and never blocks
on the network. All display decisions live in ``view_model.LyricsViewModel``;
this module renders them, plus the anticipatory line fade: using the next
line's known timestamp, the current line fades out shortly before it and the
new line fades in so it is fully legible exactly on time.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QRunnable,
    QSettings,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtCore import Property  # noqa: E402  (grouped separately: it is a class helper)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsEffect,
    QGraphicsOpacityEffect,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sottovoce import accessibility
from sottovoce import appearance
from sottovoce.app_positions import (
    ActivationDebounce,
    AppPositions,
    GLOW_SECONDS,
    SETTLE_SECONDS,
    display_label,
    glow_intensity,
    glow_width,
    learn_refusal,
    may_acknowledge,
    move_refusal,
    status_summary,
)
from sottovoce.artwork import ArtworkProvider
from sottovoce.geometry import (
    BASE_WIDTH as _BASE_WIDTH,
    MIN_SCALE as _MIN_SCALE,
    MIN_WIDTH as _MIN_WIDTH,
    RESIZE_MARGIN,
    beside_centred_text,
    button_margin,
    button_side,
    clamped_position,
    cocoa_point_from_qt,
    compact_text_gutter,
    control_gap,
    docked_position,
    fitted_window_width,
    is_docked,
    min_window_height,
    qt_rect_from_cocoa,
    resized_position,
    scale_for as _scale_for,
    width_cap,
    sync_bar_bottom,
    sync_bar_gap,
    sync_bar_height,
    sync_bar_reserve,
    text_gutter,
)
from sottovoce.failure import UNKNOWN, FetchFailure
from sottovoce.paste_window import PasteWindow
from sottovoce.gestures import opacity_step, scroll_step, wheel_action
from sottovoce import flight
from sottovoce import frontmost
from sottovoce import hit_test
from sottovoce import hotkey
from sottovoce.loop import LineLoop, LoopPhase
from sottovoce.lyrics_provider import LyricsError, LyricsProvider, close_connections
from sottovoce.macspaces import (
    ACTIVATION_POLICY_ACCESSORY,
    STATUS_WINDOW_LEVEL,
    all_desktops_behavior,
)
from sottovoce import login_item
from sottovoce import menubar
from sottovoce import nsmenu
from sottovoce import placement
from sottovoce import settings as preferences
from sottovoce.menu import (
    ALBUM_COLOUR,
    ALL_DESKTOPS,
    COMPACT,
    COMPACT_SIZE,
    DOCK_TOP,
    ECHO,
    FIT_TO_SONG,
    FORGET_POSITIONS,
    Menu,
    OPEN_AT_LOGIN,
    PASTE_SYNC,
    POSITION_LIST,
    POSITION_STATUS,
    PROXIMITY,
    QUIT,
    REMEMBER_POSITION,
    ROMANISATION,
    Row,
    SHOW_LYRICS,
    SPEECH_RATE,
    SPOKEN,
    MENUBAR_ANIMATION,
    SYNC,
    YIELD_NOTIFICATIONS,
    visible_entries,
)
from sottovoce import notifications
from sottovoce import proximity
from sottovoce.player_events import PlaybackAnnouncer
from sottovoce.player_monitor import (
    POLL_INTERVAL_SECONDS,
    PlaybackState,
    PlayerMonitor,
    PlayerSnapshot,
    SpotifyQueryError,
    announce,
    command_round_trips,
    observing,
    pause_playback,
    resume_playback,
    set_position,
)
from sottovoce.speech import (
    SPEECH_RATE_PRESETS,
    SPEECH_RATE_WPM,
    SpeechSession,
    button_visible,
    detect_voice,
    speak_korean,
)
from sottovoce.sync_session import interpolated_position
from sottovoce import symbols
from sottovoce.symbols import (
    RETRY_FALLBACK_GLYPH,
    RETRY_SYMBOL,
    SPEAK_FALLBACK_GLYPH,
    SPEAK_SYMBOL,
    WHY_FALLBACK_GLYPH,
    WHY_SYMBOL,
    icon_size,
    symbol_icon,
)
from sottovoce.transition import LineTransition
from sottovoce.typography import (
    BOTTOM_MARGIN,
    COMPACT_TEXT_SIZES,
    CONTEXT,
    CURRENT,
    CURRENT_SPACING,
    DEFAULT_COMPACT_TEXT_SIZE,
    compact_scale,
    HEADER,
    LINE_TRAVEL,
    PLAIN,
    PRONUNCIATION,
    PRONUNCIATION_SPACING,
    PROGRESS,
    ROW_SPACING,
    TOP_MARGIN,
    font_stack,
    style_for,
)
from sottovoce.vibrancy import (
    AUTORESIZE_FILL,
    BLENDING_MODE_BEHIND_WINDOW,
    MATERIAL_HUD_WINDOW,
    masked_corners,
    STATE_ACTIVE,
    WINDOW_BELOW,
    appearance_name,
)
from sottovoce.view_model import LyricsViewModel, Mode, card_yields

logger = logging.getLogger(__name__)

_CORNER_RADIUS = 14
_RESIZE_MARGIN = RESIZE_MARGIN
# What a first run opens at, comfortably above the full layout's floor at
# scale 1.0 (183), so it opens at a shape it chose rather than at one it
# was clamped to.
_DEFAULT_HEIGHT = 200

# The smallest the window can ever be: the narrowest width, and the
# compact layout's floor at the scale that width produces. Derived rather
# than stated, because the per-layout floor is applied a few lines into
# the constructor and this one is only what holds until then — a literal
# here that was larger than the compact floor would clamp a restored strip
# up to a shape the user never chose.
_MIN_SIZE = QSize(_MIN_WIDTH, min_window_height(_MIN_SCALE, compact=True))

_MIN_OPACITY = 0.25
_MAX_OPACITY = 1.0
# Full opacity by default, because window alpha and the blur are exclusive:
# a window with alphaValue < 1 gets no behind-window blur at all, however
# slight the translucency (verified by screenshot — at 0.92 the text of the
# document underneath came through sharp, at 1.0 it dissolved completely).
# Defaulting to anything less traded the entire material for a translucency
# nobody asked for. Dimming still works and still means what it says: it
# gives up the frost to let the screen underneath show through.
_DEFAULT_OPACITY = 1.0

# Every colour the window draws now lives in appearance.py, as two
# palettes with the same shape — including the painted background, which
# is a scrim over the vibrancy material and the whole background without
# one. Both are sized so the sung line clears 4.5:1 with the material
# contributing nothing, against whichever backdrop is worst for that mode
# (a white page for dark, a black one for light). tests/test_scrim.py
# pins both floors.
#
# One palette still serves both ways a control can be drawn: the
# stylesheet paints it as text, symbols.py tints the SF Symbol from the
# same values, so the icon and the glyph it falls back to can never
# describe different states.


def _qcolor(colour: appearance.RGBA) -> QColor:
    return QColor(*colour)


def _panel_path(rect: QRectF, radius: float, square_top: bool) -> QPainterPath:
    """The panel's outline: rounded on all four corners, or square across
    the top and rounded underneath.

    The second shape is the docked one. A window flush under the menu bar
    with two rounded corners against it reads as a panel that happens to be
    up there, with a sliver of desktop showing through each corner; with
    the top squared off it reads as the menu bar's own band continuing
    downwards, which on a notched Mac is the notch and on an unnotched one
    is the bar. The difference is two corners and it is the whole effect.

    One builder for both, rather than the square-top case being a special
    route: the rounded case here is byte for byte what
    ``drawRoundedRect(rect, radius, radius)`` produces, at 1x and at 2x,
    and a test pins that — so there is no second answer to what an
    undocked panel looks like, only one function asked for a different
    corner.
    """
    top = 0.0 if square_top else radius
    path = QPainterPath()
    path.moveTo(rect.left(), rect.top() + top)
    if top:
        path.arcTo(rect.left(), rect.top(), 2 * top, 2 * top, 180, -90)
    path.lineTo(rect.right() - top, rect.top())
    if top:
        path.arcTo(rect.right() - 2 * top, rect.top(), 2 * top, 2 * top, 90, -90)
    path.lineTo(rect.right(), rect.bottom() - radius)
    path.arcTo(
        rect.right() - 2 * radius,
        rect.bottom() - 2 * radius,
        2 * radius,
        2 * radius,
        0,
        -90,
    )
    path.lineTo(rect.left() + radius, rect.bottom())
    path.arcTo(
        rect.left(),
        rect.bottom() - 2 * radius,
        2 * radius,
        2 * radius,
        270,
        -90,
    )
    path.closeSubpath()
    return path


# Anticipatory line fade: the old line fades out over [ts-200, ts-100],
# the new line swaps in at ts-100 and its fade-in completes AT ts, so it
# is fully legible at its timestamp and never late.
# One line change is two phases of equal length: the old line leaves over
# the first, the new one arrives over the second. Everything is expressed
# from that single number so the three cannot drift apart — the swap is
# one phase before the timestamp, the whole choreography two.
#
# The schedule was extended EARLIER rather than allowed to finish later.
# The incoming line still settles exactly on its timestamp; it simply
# starts moving 520ms before it instead of 200ms, which is what turns a
# flick into a movement.
_FADE_MS = 260
_SWAP_LEAD_MS = _FADE_MS
_FADE_OUT_LEAD_MS = 2 * _FADE_MS

# How far ahead of a line's timestamp the screen is allowed to be. The
# predicted swap puts the next line up a whole phase early on purpose, so
# the display and the view model disagreeing by one line is a state this
# window asked for — but only until the player is further away than the
# choreography can account for, and the position that would confirm it
# cannot arrive before the next poll.
_PREDICTION_LEAD_SECONDS = _FADE_OUT_LEAD_MS / 1000 + POLL_INTERVAL_SECONDS

# The album-colour cross-fade. Slower than the line fade on purpose: that
# one has to finish before a lyric is due, this one is scenery and a
# 100ms colour change reads as a flicker rather than as a transition.
_TINT_FADE_MS = 600

# How long the window takes to travel to a remembered position. The same
# duration as one phase of a line change, and sine easing for the same
# reason 13 chose it over cubic — cubic's ends are steep enough that even
# this reads as a snap. InOut rather than In-then-Out because this is one
# continuous movement rather than two phases handing over: it leaves and
# arrives in the same gesture, so both ends are eased.
_MOVE_MS = _FADE_MS
_MOVE_CURVE = QEasingCurve.Type.InOutSine

# The window leaving for the menu bar item and coming back. It accelerates
# away and decelerates onto its mark — In leaving, Out arriving, the same
# pairing the line change uses and for the same reason: the departure
# should read as a departure and the arrival as settling.
_FLIGHT_LEAVING = QEasingCurve.Type.InSine
_FLIGHT_ARRIVING = QEasingCurve.Type.OutSine

# Fading out of a notification's way, and back. The same curve as the
# travel to a remembered position, and the same argument for it: one
# continuous movement rather than two phases handing over, so both ends are
# eased — and a fade that reverses mid-way then does so symmetrically,
# which a paired In/Out would not.
_YIELD_CURVE = _MOVE_CURVE

# The compact layout's controls coming out from under the pointer, and
# going away again. The same phase length as everything else the window
# does, and the yield's curve for the yield's reason: it is one continuous
# fade that can reverse halfway — a pointer that skims the window and
# leaves — and a paired In/Out would not reverse symmetrically. Its
# duration is proportional to how far it has to travel, like the flight's,
# so a reversal comes back from where it got to rather than dawdling
# through a journey it has already made.
_REVEAL_MS = _FADE_MS
_REVEAL_CURVE = _MOVE_CURVE

# How often the window asks where the pointer is. There is no event to
# wait for (see _check_pointer), so this is the whole of the window's
# answer and the interval is the whole of its latency: at 100ms the
# controls are already moving before the hand has finished arriving.
# Slower would be felt; faster would buy nothing that can be seen.
#
# TWO layers ask, and they share the one poll rather than each keeping a
# timer: the compact layout's controls coming out from under the pointer,
# and yielding to the pointer. One number, one timer, one reading of where
# the pointer is per tick, so the two can never disagree about it.
#
# MEASURED, on this machine, at 200,000 iterations each: asking macOS
# where the pointer is costs 0.309us, testing it against the window's
# frame takes the poll to 0.714us, and the region test that the yield adds
# takes it to 0.973us. At 100ms that is 0.00097% of a core, and the added
# 0.258us is 0.00026% of one. Working out where a dodge goes costs 0.852us
# and happens once per arrival, not once per poll.
_POINTER_POLL_MS = 100

# How big an application icon is drawn for the menu. macOS puts 16-point
# icons beside menu items; this is asked for in points and comes back at
# the screen's own scale, so a Retina menu gets a 32-pixel icon rather than
# a 16-pixel one stretched.
_MENU_ICON_POINTS = 16

# How long shutdown waits for the monitor thread and then for the worker
# pool. Long enough for a tick to finish the round trip it is waiting on
# (measured at 133ms with Spotify running, and there is no timeout on it
# to fall back to) or a fetch to notice it is done, short enough that quit
# still feels like quit.
_SHUTDOWN_WAIT_MS = 3000

_TITLE_CARD_SECONDS = 2.0
# How long the sync exit control stays armed awaiting its second click.
# What the progress row says meanwhile is coloured from the palette, so
# the armed prompt reads in both modes (see _exit_confirm_text).
_EXIT_CONFIRM_MS = 4000
_EXIT_CONFIRM_TEXT = "discard this sync? tap ✕ again"
_DOTS_FRAMES = ["·", "· ·", "· · ·"]
_RETRY_TICK_MS = 1000
# How long after a song's own lookup lands before the rest of its album is
# fetched. SET BY EYE and nothing is measured against it, which is worth
# saying rather than dressing up: it exists to put the quiet requests
# clear of the ones somebody is waiting on, and everything it has to clear
# is already over by then — the title card is 2s, the artwork fetch is
# started at the same moment as the lyrics and is not waited on by
# anything either. Longer would only mean warming less of an album the
# user is halfway through.
_WARM_DELAY_MS = 5000


def _widget_name(widget) -> str:
    """How a widget is named in a press trace: its object name if it has
    one, its class otherwise, and "none" for nothing at all — which is
    what ``childAt`` answers when a press landed on bare chrome, and is
    the single most useful thing in the whole trace."""
    if widget is None:
        return "none"
    name = widget.objectName() if hasattr(widget, "objectName") else ""
    return name or type(widget).__name__


def _style_for(
    scale: float, family_stack: str, palette: appearance.Palette
) -> str:
    header = style_for(HEADER, scale)
    context = style_for(CONTEXT, scale)
    current = style_for(CURRENT, scale)
    pron = style_for(PRONUNCIATION, scale)
    plain = style_for(PLAIN, scale)
    progress = style_for(PROGRESS, scale)
    # Colour carries the hierarchy alongside weight: the sung line is the
    # only full-strength thing on screen, everything else recedes. Which
    # direction "full strength" runs in — near-white or near-black — is
    # the palette's business, not this function's.
    rgba = appearance.rgba
    return f"""
QWidget {{ font-family: {family_stack}; }}
QLabel {{ background: transparent; }}
QLabel#header {{
    color: {rgba(palette.header)};
    font-size: {header.size_px}px; font-weight: {header.weight};
}}
QLabel#dim {{
    color: {rgba(palette.context)};
    font-size: {context.size_px}px; font-weight: {context.weight};
}}
QLabel#current {{
    color: {rgba(palette.current)};
    font-size: {current.size_px}px; font-weight: {current.weight};
}}
QLabel#pron {{
    color: {rgba(palette.pronunciation)};
    font-size: {pron.size_px}px; font-weight: {pron.weight};
}}
QLabel#plain {{
    color: {rgba(palette.plain)};
    font-size: {plain.size_px}px; font-weight: {plain.weight};
}}
QLabel#progress {{
    color: {rgba(palette.progress)};
    font-size: {progress.size_px}px; font-weight: {progress.weight};
}}
QScrollArea#plainScroll, QScrollArea#plainScroll QWidget {{ background: transparent; border: none; }}
QScrollArea#plainScroll QScrollBar:vertical {{
    background: transparent; width: {max(3, round(4 * scale))}px; margin: 0;
}}
QScrollArea#plainScroll QScrollBar::handle:vertical {{
    background: {rgba(palette.scrollbar)}; border-radius: {max(1, round(2 * scale))}px;
    min-height: 24px;
}}
QScrollArea#plainScroll QScrollBar::add-line:vertical,
QScrollArea#plainScroll QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollArea#plainScroll QScrollBar::add-page:vertical,
QScrollArea#plainScroll QScrollBar::sub-page:vertical {{ background: transparent; }}
QPushButton#loop, QPushButton#speak, QPushButton#why, QPushButton#retry {{
    color: {rgba(palette.control_idle)}; background: transparent; border: none;
    border-radius: {round(6 * scale)}px;
    font-size: {round(15 * scale)}px;
}}
QPushButton#loop:hover, QPushButton#speak:hover, QPushButton#why:hover,
QPushButton#retry:hover {{
    color: {rgba(palette.control_hover)}; background: {rgba(palette.control_wash)};
}}
QPushButton#why:checked {{ color: {rgba(palette.control_engaged)}; }}
QPushButton#loop:checked {{ color: {rgba(palette.control_engaged)}; }}
QPushButton#speak:disabled {{ color: {rgba(palette.control_engaged)}; }}
QPushButton#attempt {{
    color: {rgba(palette.attempt_text)}; border: none;
    background: {rgba(palette.attempt_fill)}; border-radius: {round(6 * scale)}px;
    font-size: {round(15 * scale)}px;
}}
QPushButton#attempt:hover {{ background: {rgba(palette.attempt_fill_hover)}; }}
QPushButton#tap {{
    color: {rgba(palette.tap_text)}; background: {rgba(palette.tap_fill)};
    border: none; border-radius: {round(8 * scale)}px;
    font-size: {round(13 * scale)}px; font-weight: 700;
}}
QPushButton#tap:hover {{ background: {rgba(palette.tap_fill_hover)}; }}
QPushButton#tap:pressed {{ background: {rgba(palette.tap_fill_pressed)}; }}
QPushButton#tap:disabled {{
    color: {rgba(palette.tap_text_off)}; background: {rgba(palette.tap_fill_off)};
}}
QPushButton#syncUndo, QPushButton#syncExit {{
    color: {rgba(palette.sync_text)}; border: none;
    background: {rgba(palette.sync_fill)}; border-radius: {round(8 * scale)}px;
    font-size: {round(14 * scale)}px;
}}
QPushButton#syncUndo:hover {{ color: {rgba(palette.sync_text_hover)}; }}
QPushButton#syncUndo:disabled {{ color: {rgba(palette.sync_text_off)}; }}
QPushButton#syncExit:hover {{
    color: {rgba(palette.exit_text_hover)}; background: {rgba(palette.exit_fill_hover)};
}}
"""


def _system_appearance() -> appearance.Appearance:
    """The palette the system is currently asking for.

    Qt's colour scheme rather than an AppKit call of our own: the cocoa
    plugin already watches NSApp.effectiveAppearance and republishes every
    change as a signal, so following the system costs no pyobjc, behaves
    the same on the offscreen platform the suite runs on, and cannot end
    up disagreeing with what Qt thinks the window's own palette is.
    """
    hints = QApplication.instance().styleHints()
    return appearance.from_color_scheme(int(hints.colorScheme().value))


def _clamped_point(frame: QRect, available: QRect) -> QPoint:
    x, y = clamped_position(
        (frame.x(), frame.y(), frame.width(), frame.height()),
        (available.x(), available.y(), available.width(), available.height()),
    )
    return QPoint(x, y)


class LineFade(QGraphicsEffect):
    """Fades the sung line and slides it, off one animatable number.

    ``progress`` runs -1 .. +1 and says where the line is in its own
    replacement:

    - ``0``  in place, fully opaque — the resting state
    - ``-1`` gone, drifted UP: where a line ends up when it is replaced
    - ``+1`` not yet arrived, sitting BELOW: where a line starts from

    One property rather than separate opacity and offset ones because
    they are not independent — a line half faded is half travelled, by
    definition — and because one QPropertyAnimation per line change is
    cheaper than a parallel group of two.

    A QGraphicsEffect rather than moving the widget: ``_current_box``
    lives in a QVBoxLayout, so anything that moved it would be undone by
    the next layout pass and would ripple into the rows above and below.
    Drawing the source pixmap at an offset touches no geometry at all,
    which is also why the rest of the window cannot feel this happening.

    ## The source is rasterised once per phase, not once per frame

    ``sourcePixmap`` re-renders the whole source widget — two labels, so
    two full text layouts and two runs of glyph rasterisation. Qt has no
    cache for it on a widget source; measured with the sampler, that
    render was the single largest thing in a line change, and inside it
    ``QPainter::drawText`` alone was a third of every frame's paint.

    Nothing about the source moves during a phase. The line's text is set
    once, at the swap, between the two phases; the only thing changing
    from frame to frame is ``progress``, which is a number this effect
    multiplies an offset and an opacity by. So the pixmap is kept and
    reused: measured, 37.5 renders per line change became 2.2.

    Two rules keep the cache honest, and they are layered on purpose:

    - anything that changes what the box shows calls ``invalidate()``.
      There are four such places and each is a funnel the window already
      had: ``_set_line_text`` (either row's words, re-elision included),
      ``_set_pronunciation`` (the row appearing or going), ``_restyle``
      (colour and type size) and ``_animate_line`` (the start of a phase).
    - a repaint that arrives without ``progress`` having moved since the
      last one is not a frame of an animation, so it re-renders anyway.
      That is the state the window spends almost all of its time in, so
      the ordinary case is exactly what it always was, and a fifth funnel
      that someone forgets is caught here rather than shown stale.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._travel = 0.0
        self._cached = None
        self._drawn_at: Optional[float] = None

    @property
    def travel(self) -> float:
        return self._travel

    @travel.setter
    def travel(self, value: float) -> None:
        self._travel = float(value)
        self.updateBoundingRect()

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = float(value)
        self.update()

    # Declared as a Qt property so QPropertyAnimation can drive it.
    progress = Property(float, _get_progress, _set_progress)

    def invalidate(self) -> None:
        """The source is about to look different, so what was rasterised
        of it is worth nothing. Cheap enough to call without checking: it
        drops a reference."""
        self._cached = None

    def boundingRectFor(self, rect):
        """Room to draw outside the source, or the travel would be
        clipped to the widget's own box and the line would appear to
        dissolve at the edge instead of leaving."""
        return QRectF(rect).adjusted(0, -self._travel, 0, self._travel)

    def draw(self, painter) -> None:
        moved = self._drawn_at is not None and self._drawn_at != self._progress
        self._drawn_at = self._progress
        if self._cached is None or not moved:
            # sourcePixmap returns the pixmap and writes where to put it
            # into the QPoint it is handed — an out-parameter, not a second
            # return value. Ignoring it would draw the block at the
            # widget's origin instead of its own.
            offset = QPoint()
            pixmap = self.sourcePixmap(
                Qt.CoordinateSystem.LogicalCoordinates,
                offset,
                QGraphicsEffect.PixmapPadMode.NoPad,
            )
            if pixmap.isNull():
                self._cached = None
                return
            self._cached = (pixmap, QPointF(offset))
        pixmap, offset = self._cached
        painter.setOpacity(max(0.0, 1.0 - abs(self._progress)))
        painter.drawPixmap(offset + QPointF(0.0, self._progress * self._travel), pixmap)


class MonitorThread(QThread):
    """Runs the player monitor's polling loop off the UI thread. Signals
    are emitted from this thread and delivered to UI slots via queued
    connections."""

    track_changed = Signal(object)     # PlayerSnapshot
    position_updated = Signal(object)  # PlayerSnapshot
    state_changed = Signal(object)     # PlayerSnapshot

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._monitor = PlayerMonitor(
            on_track_change=self.track_changed.emit,
            on_position_update=self.position_updated.emit,
            on_state_change=self.state_changed.emit,
        )

    def run(self) -> None:
        self._monitor.run()

    def stop(self) -> None:
        self._monitor.stop()


class SeekTask(QRunnable):
    """One position write to Spotify, off the UI thread. Fire-and-forget:
    a failed seek surfaces as the loop's position drifting out of bounds,
    which cancels the loop on its own."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self._seconds = seconds

    def run(self) -> None:
        try:
            set_position(self._seconds)
        except SpotifyQueryError as exc:
            logger.warning("seek to %.2fs failed: %s", self._seconds, exc)


class PlayerCommandTask(QRunnable):
    """One player command sequence off the UI thread: optionally seek,
    then pause or resume."""

    def __init__(
        self,
        seek_to: Optional[float] = None,
        pause: bool = False,
        resume: bool = False,
    ) -> None:
        super().__init__()
        self._seek_to = seek_to
        self._pause = pause
        self._resume = resume

    def run(self) -> None:
        try:
            if self._seek_to is not None:
                set_position(self._seek_to)
            if self._pause:
                pause_playback()
            if self._resume:
                resume_playback()
        except SpotifyQueryError as exc:
            logger.warning("player command failed: %s", exc)


class _SpeakSignals(QObject):
    finished = Signal()


class SpeakTask(QRunnable):
    """Pause (maybe) → speak the line → resume (maybe), all in one worker
    so the ordering is guaranteed and the UI thread never blocks."""

    def __init__(
        self, text: str, pause_first: bool, resume_after: bool, rate: int
    ) -> None:
        super().__init__()
        self.signals = _SpeakSignals()
        self._text = text
        self._pause_first = pause_first
        self._resume_after = resume_after
        self._rate = rate

    def run(self) -> None:
        try:
            if self._pause_first:
                pause_playback()
            speak_korean(self._text, self._rate)
            if self._resume_after:
                resume_playback()
        except Exception:
            logger.exception("spoken reference failed")
        try:
            self.signals.finished.emit()
        except RuntimeError:
            pass  # app tore down the signal object mid-speech


class _SaveSyncSignals(QObject):
    finished = Signal(str, bool)  # track_id, ok


class SaveSyncTask(QRunnable):
    """Writes a finished tap-to-sync to disk off the UI thread, then
    reports back so the reload happens strictly after the file exists —
    reloading first would read the old lyrics and show the song as plain
    again."""

    def __init__(self, provider: LyricsProvider, track_id: str, lrc_text: str) -> None:
        super().__init__()
        self.signals = _SaveSyncSignals()
        self._provider = provider
        self._track_id = track_id
        self._lrc_text = lrc_text

    def run(self) -> None:
        ok = False
        try:
            self._provider.save_user_sync(self._track_id, self._lrc_text)
            ok = True
        except OSError:
            logger.exception("failed to save user sync for %s", self._track_id)
        try:
            self.signals.finished.emit(self._track_id, ok)
        except RuntimeError:
            pass  # app tore down the signal object mid-save


class _ArtworkSignals(QObject):
    finished = Signal(str, object)  # track_id, (r, g, b) | None


class ArtworkTask(QRunnable):
    """One album-cover colour lookup, off the UI thread.

    Downloads and decodes an image, which is exactly the kind of work the
    UI thread must never do — and unlike the lyrics fetch, nothing waits
    on the answer. A cover that never arrives leaves the window the colour
    it already was, so there is no error state to report and no retry.
    """

    def __init__(self, provider: ArtworkProvider, track_id: str, url: Optional[str]) -> None:
        super().__init__()
        self.signals = _ArtworkSignals()
        self._provider = provider
        self._track_id = track_id
        self._url = url

    def run(self) -> None:
        colour = None
        try:
            colour = self._provider.colour_for(self._track_id, self._url)
        except Exception:
            logger.exception("album colour failed for %s", self._track_id)
        try:
            self.signals.finished.emit(self._track_id, colour)
        except RuntimeError:
            pass  # app tore down the signal object mid-fetch


class _FetchSignals(QObject):
    # track_id, TrackLyrics | None, ok, FetchFailure | None, from_service
    finished = Signal(str, object, bool, object, bool)


class FetchTask(QRunnable):
    """Runs one lyrics lookup off the UI thread. Failures are logged and
    reported as ok=False — never silently converted to "no lyrics" — and
    carry what went wrong, so the window can answer "why" if asked.

    A success also reports whether LRCLIB is what answered. The retry
    schedule is the only thing that reads it and view_model.fetch_completed
    says why it has to: a cache hit is not evidence that the service is
    back.
    """

    def __init__(self, provider: LyricsProvider, snapshot: PlayerSnapshot) -> None:
        super().__init__()
        self.signals = _FetchSignals()
        self._provider = provider
        self._snapshot = snapshot

    def run(self) -> None:
        track_id = self._snapshot.track_id
        lyrics, ok, why, from_service = None, False, None, False
        try:
            answer = self._provider.look_up(self._snapshot)
            lyrics, from_service = answer.lyrics, answer.from_service
            ok = True
        except LyricsError as exc:
            logger.exception("lyrics fetch failed for %s", track_id)
            why = exc.failure
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            logger.exception("unexpected error fetching lyrics for %s", track_id)
            # Still a reason, and still an honest one: something went wrong
            # that the provider did not expect. A window that said only
            # "lyrics unavailable" here would be hiding the more
            # interesting of the two failures.
            why = FetchFailure(kind=UNKNOWN, detail=str(exc))
        try:
            self.signals.finished.emit(track_id, lyrics, ok, why, from_service)
        except RuntimeError:
            pass  # app tore down the signal object while we were fetching


class WarmTask(QRunnable):
    """Fetches the album into the cache, off the UI thread and off
    everybody's critical path.

    Nothing waits on this and nothing is told when it finishes: a warmed
    track is only ever noticed by a lookup that finds it there later. That
    is what lets it sleep between requests, which the per-track stage must
    — LRCLIB asks that batch work go out sequentially with a gap, and a gap
    is real time on a real thread.

    ``stop`` is how shutdown gets its thread back. The per-track stage
    takes several seconds by design, and ``_shutdown`` waits three; without
    a way to end the sleep this would be the one worker that is always
    still running.
    """

    def __init__(
        self,
        provider: LyricsProvider,
        snapshot: PlayerSnapshot,
        stop: threading.Event,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._snapshot = snapshot
        self._stop = stop

    def run(self) -> None:
        try:
            self._provider.warm_album(
                self._snapshot,
                # Waited on rather than slept through, for the monitor's
                # reason: a stop set during the gap ends it now instead of
                # after it.
                sleep=self._stop.wait,
                should_stop=self._stop.is_set,
            )
        except Exception:  # noqa: BLE001 - nothing is waiting on this
            logger.exception("album warm failed")


class LyricsWindow(QWidget):
    def __init__(
        self,
        provider: Optional[LyricsProvider] = None,
        settings: Optional[QSettings] = None,
        artwork_provider: Optional[ArtworkProvider] = None,
    ) -> None:
        super().__init__()
        self._provider = provider or LyricsProvider()
        self._artwork = artwork_provider or ArtworkProvider()
        self._view_model = LyricsViewModel()
        self._pool = QThreadPool.globalInstance()
        # Injectable so tests and scratch runs write somewhere of their own:
        # QSettings' default location is global process state, and getting
        # it wrong means stamping on the real user's saved window.
        if settings is not None:
            self._settings = settings
        else:
            # The real preferences file, and the only path that consults the
            # one this app left behind when it stopped being LyriSync. An
            # injected settings object is the caller's and arrives complete;
            # copying somebody else's app's preferences into it is not this
            # constructor's business, and it keeps the suite off the legacy
            # file by construction rather than by remembering to stub.
            self._settings = QSettings(
                preferences.ORGANISATION, preferences.APPLICATION
            )
            logger.info(
                "settings: %s", preferences.migrate(self._settings).value
            )

        self._drag_offset: Optional[QPoint] = None
        self._resize_edges = Qt.Edges()
        self._press_geometry = QRect()
        self._press_global = QPoint()
        self._current_snapshot: Optional[PlayerSnapshot] = None
        self._last_state = PlaybackState.NOT_RUNNING
        # Freshest known position and the monotonic instant it was read, so
        # a tap can interpolate forward without querying Spotify.
        self._last_position: Optional[float] = None
        self._last_polled_at: Optional[float] = None
        self._title_card_until = 0.0
        self._card_key: Optional[tuple] = None
        self._dots_frame = 0
        self._scale = 0.0
        self._all_desktops = False
        # The compact layout. `_compact` is what the user asked for and
        # `_compact_applied` is what the window is currently wearing: a
        # sync pass borrows the full layout for as long as it runs, so the
        # two disagree for exactly that long. The full layout's height is
        # kept, so switching back gives it the shape it was last left at.
        # The strip's is not kept, because it is not a free number: it
        # follows the type size below, and there is one right answer.
        self._compact = False  # restored from settings below
        self._compact_applied = False
        self._compact_text_size = DEFAULT_COMPACT_TEXT_SIZE
        # Whether the window is exactly where docking put it, which is the
        # only thing that decides its shape. Derived from the position on
        # every move, never set by the dock command: see _update_docked.
        self._docked = False
        # What each layout was last left at: where it was AND how big.
        # One record, because they are one fact — a strip is a quarter the
        # height of the full layout and usually a different width, so the
        # place that suits one is not the place that suits the other, and
        # coming back from the strip used to hand the full layout its old
        # size at wherever the strip was standing. The strip's own width
        # is the width the USER chose, kept even while the song is
        # choosing the window's actual one, so turning the fit off has
        # something to give back; the strip has no remembered height at
        # all, because it follows the type size.
        self._shapes = placement.LayoutShapes()
        self._fit_to_song = True  # restored from settings below
        self._fit_anim: Optional[QPropertyAnimation] = None
        # How much of the overlay controls is showing, and where it is
        # heading. Full in the full layout and never touched there;
        # in compact it runs 0 (a strip with nothing on it but the line)
        # to 1. Kept apart from the target for the yield's reason: a
        # render that agrees with the current state must cost nothing.
        self._reveal = 1.0
        self._reveal_to = 1.0
        self._reveal_anim: Optional[QVariantAnimation] = None
        self._reveal_effects: dict = {}
        self._control_offered: dict = {}
        self._hovered = False
        # Yielding to the pointer. `_proximity` is the pure observation of
        # where the pointer is and whether the behaviour is running;
        # `_proximity_home` is the window's REAL position, held for as long
        # as a dodge is standing aside and given back at the end of it,
        # exactly the way the flight holds one. None means the window is
        # where it belongs, so there is no flag to clear.
        self._proximity_mode = proximity.OFF  # restored from settings below
        self._proximity = proximity.Approach()
        self._proximity_home: Optional[QPoint] = None
        # How far into a ghost the window is, and where it is heading. The
        # yield's pair, kept apart for the yield's reason: a poll that
        # agrees with the current state must cost nothing.
        self._ghost_level = 0.0
        self._ghosting = False
        self._ghost_anim: Optional[QVariantAnimation] = None
        # The lyric rows before the compact layout shortened them, so a
        # resize can lay them out again from the line rather than from what
        # was left of it.
        self._full_text: dict = {}
        # Per-app position memory. All off until _restore_settings says
        # otherwise; the watcher is only started when the layer is on, so
        # with it off nothing observes anything.
        self._remember_position = False
        self._positions = AppPositions()
        self._debounce = ActivationDebounce(SETTLE_SECONDS)
        self._frontmost: Optional[str] = None
        # What that app is called, taken from the same announcement as its
        # identifier. The map keeps its own copy for apps that are not
        # running; this is the one for the app in front right now.
        self._frontmost_name: Optional[str] = None
        # The acknowledgement: how much of the warm colour the hairline is
        # carrying, and when the last one started (one per gesture).
        self._glow = 0.0
        self._glow_anim: Optional[QVariantAnimation] = None
        self._glow_at: Optional[float] = None
        # Which app the readout is showing an icon for, so a menu refresh
        # on every render does not redraw one three times a second.
        self._readout_icon_for: Optional[str] = None
        self._own_bundle_id = frontmost.own_bundle_id()
        self._watcher = frontmost.FrontmostWatcher(self._on_app_activated)
        self._move_anim: Optional[QPropertyAnimation] = None
        # The journey to and from the menu bar item. `_flight_home` is the
        # window's real position, held for the length of the flight and
        # given back at the end of it however the flight ends.
        self._flight_anim: Optional[QVariantAnimation] = None
        self._flight_progress = 0.0
        self._flight_home: Optional[tuple] = None
        self._flight_to: Optional[tuple] = None
        # What the flight is currently doing to the window's opacity, held
        # here rather than applied directly, so there is one place that
        # multiplies the three things with an opinion about it: the user's
        # own setting, a flight, and a yield.
        self._flight_opacity = 1.0
        # Yielding to notifications. `_yield_level` runs 0 (the user's own
        # opacity) to 1 (as faint as yielding goes), and `_yielding` is what
        # the last poll saw — the target the level is heading for, kept
        # apart from the level itself so an ordinary poll that agrees with
        # the current state costs nothing.
        self._yield_to_notifications = False  # restored from settings below
        self._yield_level = 0.0
        self._yielding = False
        self._yield_anim: Optional[QVariantAnimation] = None
        # Which glyph the menu bar item is showing, so it is only ever set
        # when it changes: the refresh runs on every monitor tick.
        self._tray_state = None  # a menubar.IconSpec once the tray exists
        self._tray_pngs: dict = {}  # spec -> PNG bytes, drawn once each
        # The optional arrangement stepping, and how many line changes have
        # gone by. Counted whether or not the layer is on, so switching it on
        # mid-song picks up where the song is.
        self._menubar_animation = False  # restored from settings below
        self._menubar_step = 0
        # What macOS's accessibility display settings ask of this window:
        # less motion, no transparency, more contrast. Live for the same
        # reason the appearance is — somebody who switches Reduce Motion on
        # because a migraine has started should not have to relaunch the
        # app to be believed — so this is read now and re-read on every
        # change, and the watcher is started below.
        self._display_options = accessibility.current_options()
        logger.info(
            "accessibility display options: %s",
            accessibility.describe(self._display_options),
        )
        self._display_watcher = accessibility.DisplayOptionsWatcher(
            self._on_display_options_changed
        )
        # Which palette the window is painting with. Resolved from the
        # system now and re-resolved on every change for as long as the
        # app runs — reading it once at startup would be wrong by the
        # afternoon on a Mac set to Auto.
        self._appearance = _system_appearance()
        self._palette = self._palette_now()
        # Open at Login: only meaningful from a bundle, and the status is
        # the system's to report. Read once here so the menu bar item is
        # already right the first time it is opened, then re-read on every
        # opening after that.
        self._bundled = login_item.running_bundled()
        self._login_status = (
            login_item.status() if self._bundled else login_item.LoginItemStatus.UNSUPPORTED
        )
        logger.info(
            "open at login: bundled=%s status=%s",
            self._bundled,
            self._login_status.value,
        )
        self._native_applied = False
        # The NSVisualEffectView once installed; None means the painted
        # background is carrying the window on its own.
        self._material = None
        # Album colour. `_tint_rgb` is the cover colour being painted
        # towards; the background colours either side of the cross-fade are
        # cached because working one out costs a luminance bisection per
        # field and paintEvent runs on every frame of the fade. Set up
        # after _material, which decides scrim versus solid.
        self._album_colour = False  # restored from settings below
        self._tint_rgb: Optional[tuple] = None
        self._tint_mix = 1.0
        self._tint_from = self._background_for(None)
        self._tint_to = self._tint_from
        # The hairline rides the same mix: it is the same tint arriving,
        # and two fades of the same thing could only drift apart.
        self._border_from = self._border_for(None)
        self._border_to = self._border_from
        self._tint_anim: Optional[QVariantAnimation] = None
        self._family_stack = font_stack(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        )
        # NSWindow (behavior, level) as Qt configured it, captured before
        # the first enable so disabling restores Qt's exact defaults.
        self._saved_native: Optional[tuple[int, int]] = None
        self._displayed_index: Optional[int] = None
        self._fade_anim: Optional[QPropertyAnimation] = None
        # How long each half of the next line change may take. Recomputed
        # per transition from the gap to the next line; the nominal value
        # until a schedule says otherwise.
        self._transition_ms = _FADE_MS
        # Which line change is in flight. The choreography outlasts the
        # poll interval, so a poll almost always lands inside one: this is
        # what stops that poll starting the same change over again.
        self._transition = LineTransition(_PREDICTION_LEAD_SECONDS)

        # WindowDoesNotAcceptFocus + WA_ShowWithoutActivating: an overlay
        # must never activate the app or steal focus — all interaction here
        # (drag, resize, wheel, context menu) is mouse-only.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumSize(_MIN_SIZE)
        self.setMouseTracking(True)

        self._header = self._make_label("header")
        self._previous = self._make_label("dim")
        self._current = self._make_label("current")
        self._pron = self._make_label("pron")
        self._pron.setVisible(False)
        self._upcoming = self._make_label("dim")

        # Current line + its pronunciation share one container so the
        # anticipatory fade and the rise carry both as one block. Its
        # contents margins are the extra air around the sung line, set
        # per scale in _apply_scale.
        self._current_box = QWidget()
        self._current_layout = QVBoxLayout(self._current_box)
        self._current_layout.setContentsMargins(0, 0, 0, 0)
        self._current_layout.setSpacing(PRONUNCIATION_SPACING)
        self._current_layout.addWidget(self._current)
        self._current_layout.addWidget(self._pron)
        self._current_fx = LineFade(self._current_box)
        self._current_box.setGraphicsEffect(self._current_fx)

        # Scrollable full-lyrics view for PLAIN mode. The label lives inside
        # a transparent, frameless scroll area; the note stays fixed above.
        self._plain_label = self._make_label("plain")
        self._plain_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self._plain_scroll = QScrollArea()
        self._plain_scroll.setObjectName("plainScroll")
        self._plain_scroll.setWidgetResizable(True)
        self._plain_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._plain_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._plain_scroll.setWidget(self._plain_label)
        self._plain_scroll.viewport().setAutoFillBackground(False)
        self._plain_label.setAutoFillBackground(False)
        self._plain_scroll.setVisible(False)
        # Wheel routing (Option+scroll = opacity) needs first look at wheel
        # events the scroll area would otherwise consume.
        self._plain_scroll.viewport().installEventFilter(self)

        # Synced-mode rows live in one container so PLAIN mode can hide
        # them — stretches included — as a unit, leaving no ghost space.
        self._synced_box = QWidget()
        self._synced_layout = QVBoxLayout(self._synced_box)
        self._synced_layout.setContentsMargins(0, 0, 0, 0)
        self._synced_layout.addStretch(1)
        for widget in (self._previous, self._current_box, self._upcoming):
            self._synced_layout.addWidget(widget)
        self._synced_layout.addStretch(1)

        # Fixed note above the scrolling plain body.
        self._plain_note = self._make_label("dim")
        self._plain_note.setVisible(False)

        # Fixed status row for a sync pass: line count, or the discard
        # confirmation once the exit control is armed.
        self._progress = self._make_label("progress")
        self._progress.setVisible(False)

        self._layout = QVBoxLayout(self)
        self._layout.addWidget(self._header)
        self._layout.addWidget(self._plain_note)
        self._layout.addWidget(self._progress)
        self._layout.addWidget(self._plain_scroll, 1)
        self._layout.addWidget(self._synced_box, 1)

        self._fadeout_timer = QTimer(self)
        self._fadeout_timer.setSingleShot(True)
        self._fadeout_timer.timeout.connect(self._begin_fade_out)
        self._swap_timer = QTimer(self)
        self._swap_timer.setSingleShot(True)
        self._swap_timer.timeout.connect(self._predicted_swap)
        # Restarted by every activation, so it only fires once the user has
        # stopped switching apps. The debounce object still owns the rule —
        # this only decides when to ask it.
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._apply_settled_app)
        # Repeating, unlike every other timer here, and running only while
        # the layer is on and the window is showing. There is no signal to
        # subscribe to — macOS broadcasts app activations but says nothing
        # when a banner appears — so this is the one thing in the app that
        # genuinely has to ask. The monitor's own 300ms tick was the
        # alternative and it cannot carry this: position updates stop
        # arriving the moment nothing is playing, which is exactly a moment
        # when the window is still on screen and still in the way. A layer
        # that worked only during playback would be the fourteenth
        # milestone's lesson again — a feature indistinguishable from a
        # broken one.
        #
        # Its interval is not fixed: it drops while the window is faded, so
        # coming back does not wait out a full idle-rate poll. See
        # _apply_poll_interval.
        self._yield_timer = QTimer(self)
        self._yield_timer.timeout.connect(self._check_notifications)
        self._apply_poll_interval()

        # Where the pointer is, asked rather than waited for. Repeating,
        # and running only while something could act on the answer: the
        # second thing in this app that genuinely has to ask, for a harder
        # version of the notification yield's reason. macOS delivers enter,
        # leave and mouse-moved events to an app's windows only while that
        # app is ACTIVE, and this one is an accessory that never activates
        # and a window that never takes focus, so there are no hover events
        # to subscribe to at all. See _check_pointer, where the measurement
        # is.
        #
        # Two layers read this one poll — the compact layout's controls and
        # yielding to the pointer — so the answer they act on is the same
        # answer, read once.
        self._pointer_timer = QTimer(self)
        self._pointer_timer.setInterval(_POINTER_POLL_MS)
        self._pointer_timer.timeout.connect(self._check_pointer)

        self._loop = LineLoop()
        self._loop_timer = QTimer(self)
        self._loop_timer.setSingleShot(True)
        self._loop_timer.timeout.connect(self._do_loop_wrap)
        self._echo_enabled = False  # restored from settings below
        self._attempt_button = self._make_overlay_button(
            # U+FE0E asks for text presentation: the mic then draws as a
            # monochrome glyph instead of a colour emoji, which is the only
            # thing separating these controls from an iMessage sticker.
            "attempt", "🎤︎", "Done · play the line again"
        )
        self._attempt_button.clicked.connect(self._on_attempt_done_clicked)
        self._loop_button = self._make_overlay_button("loop", "↻", "Loop this line")
        self._loop_button.setCheckable(True)
        self._loop_button.clicked.connect(self._toggle_loop)

        self._speech = SpeechSession()
        self._speech_available = detect_voice()
        self._spoken_enabled = True  # restored from settings below
        self._speech_rate = SPEECH_RATE_WPM
        self._speak_button = self._make_overlay_button(
            # The system's own glyph for a spoken line, as a template
            # image (symbols.py). The text glyph behind it is what shows
            # when SF Symbols cannot be had — off macOS, or without
            # pyobjc — and is a beamed note rather than 🔊 because that
            # renders in colour whatever is asked of it.
            "speak", SPEAK_FALLBACK_GLYPH, "Speak this line aloud"
        )
        self._speak_button.clicked.connect(self._on_speak_clicked)

        # Why the lyrics could not be fetched. The only control here that
        # reveals rather than does, and the only one that is not offered
        # all the time: it exists beside the "lyrics unavailable" message
        # and nowhere else, so a song that simply has no lyrics is still
        # answered with one plain line and no invitation to dig.
        #
        # Mouse-only, like every other interaction on this window: it never
        # takes focus, so there is no keyboard to reach it with.
        self._why_button = self._make_overlay_button(
            "why", WHY_FALLBACK_GLYPH, "Why?"
        )
        # Checkable so the control says whether it is currently showing
        # anything, in the same engaged colour the loop button uses. Its
        # clicked signal rather than toggled, for the reason the menu's
        # checkable entries give: the render sets the check itself.
        self._why_button.setCheckable(True)
        self._why_button.clicked.connect(self._toggle_why)
        # Whether the reason is currently on screen. Kept across the 30s
        # retries on purpose: the mode goes ERROR -> FETCHING -> ERROR every
        # time one runs, and hiding the reason under someone who had just
        # asked for it would make the affordance feel broken. Cleared with
        # the song, in _render.
        self._why_shown = False
        self._why_track: Optional[str] = None

        # And the one thing to DO about the reason, which is the only
        # reason the reason is worth reading twice: ask again now. It lives
        # beside the failure rather than beside the message, so it appears
        # only for somebody who has already opened the explanation — the
        # message itself says "will retry" and means it, and a button next
        # to that sentence would be inviting people to do the thing the app
        # has just promised to do for them.
        self._retry_button = self._make_overlay_button(
            "retry", RETRY_FALLBACK_GLYPH, "Try again now"
        )
        self._retry_button.clicked.connect(self._on_retry_clicked)

        # Tap-to-sync. The track key is captured on entry: a same-track
        # re-announcement (metadata settling) must not cancel the pass,
        # only a genuinely different song.
        self._sync_track_key: Optional[tuple] = None
        # The one window this app has that takes a keyboard, and it exists
        # only while it is being used. None whenever there is not one.
        self._paste_window: Optional[PasteWindow] = None
        self._tap_button = self._make_overlay_button(
            "tap", "TAP", "Tap as each line begins"
        )
        self._tap_button.clicked.connect(self._on_tap)
        self._undo_button = self._make_overlay_button(
            "syncUndo", "↩", "Undo the last tap"
        )
        self._undo_button.clicked.connect(self._on_sync_undo)
        self._sync_exit_button = self._make_overlay_button(
            "syncExit", "✕", "Discard this sync"
        )
        self._sync_exit_button.clicked.connect(self._on_sync_exit)
        # Exit is confirmed in place rather than with a dialog: this window
        # never takes focus (and runs under the accessory activation
        # policy), so a modal would either be unreachable or drag the app
        # into the foreground. First click arms, second discards.
        self._exit_armed = False
        self._exit_disarm_timer = QTimer(self)
        self._exit_disarm_timer.setSingleShot(True)
        self._exit_disarm_timer.timeout.connect(self._disarm_sync_exit)

        self._restore_settings()
        self._build_menu()
        self._build_tray()
        self._build_hotkey()
        self._apply_scale()
        # The layout the settings asked for, applied to the rows, the
        # controls and the fade they ride on. Nothing is resized here:
        # _restore_settings has already put the window at the size that
        # layout was last left at, so there is no shape to swap.
        self._apply_compact()
        # Live, not read-once: on a Mac set to Auto the appearance changes
        # under a running app, and the app that only looked at startup is
        # the one that is wrong every evening.
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._on_color_scheme_changed
        )
        # Unconditional, unlike the activation watcher beside it: this is
        # not an opt-in layer whose "off" must remove the work, it is the
        # system being followed, and there is no setting that could ask the
        # app to stop following it.
        self._display_watcher.start()
        QApplication.instance().aboutToQuit.connect(self._shutdown)

        self._monitor_thread = MonitorThread(self)
        self._monitor_thread.track_changed.connect(self._on_track_change)
        self._monitor_thread.position_updated.connect(self._on_position_update)
        self._monitor_thread.state_changed.connect(self._on_state_change)
        self._monitor_thread.start()

        # Spotify's own announcement that something changed, which is what
        # lets the monitor stop asking three times a second. Unconditional
        # like the display watcher and for the same reason: it is not a
        # layer with an "off", it is the app being told rather than
        # guessing. Delivered on this thread, and it touches nothing here
        # — it sets a flag the monitor's own thread reads.
        self._announcer = PlaybackAnnouncer(announce)
        # The monitor is told whether anything is listening rather than
        # asked to work it out: an announcement only arrives when
        # something CHANGES, so a monitor that waited for one before
        # slowing down would ask three times a second through a whole
        # song that nobody interrupted.
        observing(self._announcer.start())

        self._dots_timer = QTimer(self)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._dots_timer.start(400)

        self._retry_timer = QTimer(self)
        self._retry_timer.timeout.connect(self._tick_retry)
        self._retry_timer.start(_RETRY_TICK_MS)

        # Fetching the album ahead of time, quietly, so a later outage has
        # less to be noticeable about. Which (album, track) pairs have had
        # their turn, the snapshot the next warm will run from, and the
        # flag that ends one early: the per-track stage sleeps between its
        # requests, and shutdown has to be able to interrupt that rather
        # than wait out an album.
        self._warm_considered: set = set()
        self._warm_pending: Optional[PlayerSnapshot] = None
        self._warm_stop = threading.Event()
        self._warm_timer = QTimer(self)
        self._warm_timer.setSingleShot(True)
        self._warm_timer.timeout.connect(self._start_warm)

        self._render()

    @staticmethod
    def _make_label(object_name: str) -> QLabel:
        label = QLabel()
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        label.setWordWrap(True)
        return label

    def _make_overlay_button(self, object_name: str, glyph: str, tip: str) -> QPushButton:
        button = QPushButton(glyph, self)
        button.setObjectName(object_name)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tip)
        button.setVisible(False)
        # So a press that DID land on a control is traced by the same code
        # that traces one that did not. Two records of the same event
        # written in two places is how they come to disagree about it.
        button.installEventFilter(self)
        return button

    # -- monitor slots (UI thread, queued from MonitorThread) --------------

    def _on_track_change(self, snapshot: PlayerSnapshot) -> None:
        self._last_state = snapshot.state
        self._current_snapshot = snapshot if snapshot.is_music_track else None
        self._release_loop()
        if self._syncing and snapshot.track_key != self._sync_track_key:
            # A different song: the pass can't be finished, so discard it.
            # No prompt — the user's action already said they moved on.
            self._cancel_sync()
        self._plain_scroll.verticalScrollBar().setValue(0)  # fresh track, top
        if snapshot.has_track and snapshot.track_key != self._card_key:
            self._card_key = snapshot.track_key
            self._title_card_until = time.monotonic() + _TITLE_CARD_SECONDS
            QTimer.singleShot(int(_TITLE_CARD_SECONDS * 1000) + 100, self._render)
        elif not snapshot.has_track:
            self._card_key = None
        if self._view_model.track_changed(snapshot):
            self._start_fetch(snapshot)
            self._request_artwork(snapshot)
        self._render()

    def _on_fetch_finished(
        self,
        track_id: str,
        lyrics: object,
        ok: bool,
        failure: object = None,
        from_service: bool = True,
    ) -> None:
        # Stale results (track changed while the fetch was in flight) are
        # rejected by the view model; the provider already cached them.
        if self._view_model.fetch_completed(
            track_id,
            lyrics,
            ok,
            now=time.monotonic(),
            failure=failure,
            from_service=from_service,
        ):
            self._release_loop()  # lyrics changed under the loop
            self._render()
            # The one moment the strip resizes: a song's lines are known,
            # so how wide it has to be is known. Every other path here is
            # a line change, which deliberately does not.
            self._fit_width()
        # Asked on every outcome, including a result the view model
        # rejected as stale: what to warm is decided from the song that is
        # playing NOW rather than from the answer that just landed, and a
        # stale answer means a track change has already put a new one
        # there. Every reason not to warm is inside.
        self._consider_warming()

    def _on_position_update(self, snapshot: PlayerSnapshot) -> None:
        self._last_state = snapshot.state
        self._last_position = snapshot.position_seconds
        self._last_polled_at = snapshot.polled_at
        # The monitor tick IS what keeps the menu bar item honest. Before
        # 15.1 the icon was refreshed from _render, and a pause does not
        # re-render — so it claimed "playing" until the menu was opened.
        # Here rather than lower down: every path below this line returns
        # early for some reason, and none of those reasons is about the icon.
        self._refresh_tray_icon()
        self._view_model.position_changed(snapshot.position_seconds)
        timeline = self._view_model.timeline()
        if timeline is None:
            return
        lines, index = timeline
        position = snapshot.position_seconds
        if self._displayed_index != index and not self._predicted_ahead(
            lines, index, position
        ):
            # Seek, pause-drift correction, or a missed prediction: snap.
            self._render()

        playing = snapshot.state is PlaybackState.PLAYING
        # What this app's own commands have been costing. The wrap seek is
        # dispatched a lead before the line's end so that it LANDS on it,
        # so the lead is that round trip and nothing else; fed on every
        # update rather than after each seek, because the loop asks for it
        # at the moment it arms and there is no cursor to keep.
        self._loop.observe_round_trips(command_round_trips())
        if self._loop.engaged:
            if not self._loop.still_valid(position):
                self._release_loop()  # user seeked outside the line
                self._render()
                return
            # Before the scheduler below: a wrap already on its way is a
            # wrap this poll must not arm a second one behind.
            self._loop.observe_position(position)
            # The looped line never advances: suppress the fade scheduler
            # so no swap fires at the end bound, and arm the wrap seek from
            # the known end timestamp instead (dormant while paused).
            self._fadeout_timer.stop()
            self._swap_timer.stop()
            eta = self._loop.wrap_eta(position, playing) if position is not None else None
            if eta is None:
                self._loop_timer.stop()
            else:
                self._loop_timer.start(int(eta * 1000))
            return
        if playing and position is not None:
            self._schedule_line_advance(lines, index, position)
        else:
            self._cancel_line_schedule()

    def _on_state_change(self, snapshot: PlayerSnapshot) -> None:
        self._last_state = snapshot.state
        # Before the early exits below, and before the render that may or may
        # not happen: a stop or a quit is exactly the transition after which
        # no more position updates arrive, so this is the last chance the
        # tick gets to put the shape back to three even bars.
        self._refresh_tray_icon()
        if snapshot.state is not PlaybackState.PLAYING:
            self._cancel_line_schedule()
            self._loop_timer.stop()  # loop (if any) lies dormant, not cancelled
        if self._loop.observe_state(snapshot.state is PlaybackState.PLAYING) == "external_play":
            # The user un-paused mid-ATTEMPT: they've taken over — cancel
            # (already playing, so nothing to resume).
            self._release_loop(resume_if_attempt=False)
            self._render()
        if snapshot.state in (PlaybackState.STOPPED, PlaybackState.NOT_RUNNING):
            self._release_loop(resume_if_attempt=False)
            # Spotify stopped or quit: the pass can't be completed. Cancel
            # before the view model suspends, so the resume restores the
            # plain lyrics rather than a dead session.
            self._cancel_sync()
        if self._view_model.player_state_changed(snapshot.state):
            self._render()
        elif self._syncing:
            # The tap bar is live only while playing; a pause/resume has to
            # show up on it even though the display text is unchanged.
            self._render()

    # -- line loop ---------------------------------------------------------

    def _awaiting_attempt(self) -> bool:
        """Whether the echo loop is waiting on the done button.

        The state where the song is paused and the turn is the user's, and
        it is asked by three things that would otherwise each spell it
        out: whether to offer the done button at all, whether the compact
        layout holds its controls out whatever the pointer is doing, and
        whether yielding to the pointer is suspended. They are one fact,
        and three copies of it is three places for it to drift.
        """
        return self._loop.engaged and self._loop.phase is LoopPhase.ATTEMPT

    def _toggle_loop(self, checked: bool) -> None:
        if not checked:
            self._release_loop()
            self._render()
            return
        timeline = self._view_model.timeline()
        snapshot = self._current_snapshot
        duration = (
            snapshot.duration_ms / 1000
            if snapshot is not None and snapshot.duration_ms is not None
            else None
        )
        if timeline is None or not self._loop.engage(*timeline, duration):
            self._loop_button.setChecked(False)  # no current line to loop
            return
        self._render()

    def _release_loop(self, resume_if_attempt: bool = True) -> None:
        if not self._loop.engaged and not self._loop_button.isChecked():
            return
        was_attempt = self._awaiting_attempt()
        self._loop.release()
        self._loop_timer.stop()
        self._loop_button.setChecked(False)
        self._offer_control(self._attempt_button, False)
        if was_attempt and resume_if_attempt:
            # Released during the silent attempt: let the song continue
            # naturally from where the pause left it.
            self._pool.start(PlayerCommandTask(resume=True))

    def _do_loop_wrap(self) -> None:
        if not self._loop.engaged or self._last_state is not PlaybackState.PLAYING:
            return
        action = self._loop.on_end_reached()
        if action == "seek":
            self._pool.start(SeekTask(self._loop.start))
        elif action == "attempt":
            self._pool.start(PlayerCommandTask(pause=True))
            self._render()  # show the your-turn done-button

    def _on_attempt_done_clicked(self) -> None:
        """User-paced: the 🎤 click ends the silent attempt — replay the
        line. No timeout backs this up; silence is a valid resting state."""
        if not self._loop.engaged or self._loop.phase is not LoopPhase.ATTEMPT:
            return
        self._loop.finish_attempt()
        self._pool.start(PlayerCommandTask(seek_to=self._loop.start, resume=True))
        self._render()

    # -- tap-to-sync ---------------------------------------------------------

    @property
    def _syncing(self) -> bool:
        return self._view_model.sync_session is not None

    def _begin_sync(self) -> None:
        """Start a sync pass over the lyrics in hand."""
        if self._view_model.begin_sync():
            self._enter_sync()

    def _open_paste_window(self) -> None:
        """Ask for lyrics the app could not find, in a window that can take
        a keyboard.

        One at a time: a second press while it is open raises the one that
        is already there rather than making another, because two boxes both
        offering to sync the same song is two answers to what the lyrics
        are. See paste_window.py for why it is a window at all.
        """
        # Before either branch, and it is not decoration: an accessory app
        # is not the active app, and a window shown by one that is not
        # takes no keyboard at all. See bring_to_front, which measured it.
        bring_to_front()
        if self._paste_window is not None:
            self._paste_window.present()
            return
        display = self._view_model.display()
        window = PasteWindow(song=display.header)
        window.lines_ready.connect(self._on_pasted_lines)
        window.destroyed.connect(self._on_paste_window_gone)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._paste_window = window
        window.present()

    def _on_paste_window_gone(self) -> None:
        self._paste_window = None

    def _close_paste_window(self) -> None:
        window, self._paste_window = self._paste_window, None
        if window is not None:
            window.close()

    def _on_pasted_lines(self, lines) -> None:
        """The user handed over the words. From here it is an ordinary
        pass: the same session, the same tap row, the same save into
        ``.user_syncs/``, and the same reload afterwards — which is what
        makes this work with no network at all."""
        if not self._view_model.begin_sync_from(list(lines)):
            return
        logger.info("sync pass starting from %d pasted lines", len(lines))
        self._enter_sync()

    def _enter_sync(self) -> None:
        """Everything a pass needs once its lines are settled: every pass
        is a complete run from line one, so the track goes back to 0 and
        playback is made sure to be running before the first line
        arrives."""
        self._release_loop()
        self._disarm_sync_exit()
        snapshot = self._current_snapshot
        self._sync_track_key = snapshot.track_key if snapshot is not None else None
        self._pool.start(PlayerCommandTask(seek_to=0.0, resume=True))
        # A sync pass takes the full layout back for as long as it runs;
        # with compact off this is only the tap row's bottom margin.
        self._apply_compact()
        self._render()

    def _cancel_sync(self) -> bool:
        """Discard the pass in progress. Returns True when there was one."""
        if not self._view_model.end_sync():
            return False
        self._sync_track_key = None
        self._disarm_sync_exit()
        self._apply_compact()  # and the strip comes back, if that is where it was
        return True

    def _on_tap(self) -> None:
        """One tap = "this line starts now". Timed from the last poll plus
        the wall-clock since it landed, minus the reaction offset that
        SyncSession applies."""
        session = self._view_model.sync_session
        if session is None:
            return
        if self._last_state is not PlaybackState.PLAYING:
            return  # paused: the session simply waits for playback
        position = interpolated_position(
            self._last_position, self._last_polled_at, time.monotonic()
        )
        if position is None or not session.stamp(position):
            return
        self._disarm_sync_exit()
        if session.is_complete:
            self._finish_sync(session)
        else:
            self._render()

    def _on_sync_undo(self) -> None:
        session = self._view_model.sync_session
        if session is None or not session.undo():
            return
        self._disarm_sync_exit()
        self._render()

    def _on_sync_exit(self) -> None:
        """Two-step discard: the first click arms and says so in the
        progress row, the second throws the pass away. Anything else the
        user does — a tap, an undo, a few seconds of hesitation — disarms."""
        if not self._exit_armed:
            self._exit_armed = True
            self._exit_disarm_timer.start(_EXIT_CONFIRM_MS)
            self._render()
            return
        self._cancel_sync()
        self._render()

    def _disarm_sync_exit(self) -> None:
        self._exit_disarm_timer.stop()
        if self._exit_armed:
            self._exit_armed = False
            if self._syncing:
                self._render()

    def _finish_sync(self, session) -> None:
        """Last line stamped: save, leave sync mode, and reload so the song
        comes straight back as synced and can be checked by ear."""
        track_id = self._view_model.track_id
        lrc_text = session.to_lrc()
        self._cancel_sync()  # the session's work now lives in lrc_text
        self._render()
        if not track_id:
            return
        task = SaveSyncTask(self._provider, track_id, lrc_text)
        task.signals.finished.connect(self._on_sync_saved)
        self._pool.start(task)

    def _on_sync_saved(self, track_id: str, ok: bool) -> None:
        if not ok:
            return  # the plain lyrics are still on screen; nothing was lost
        if self._view_model.begin_reload(track_id):
            if self._current_snapshot is not None:
                self._start_fetch(self._current_snapshot)
            self._render()

    def _place_sync_controls(self) -> None:
        """Lay the tap row across the window bottom: undo on the left, exit
        on the right, and everything between belongs to the tap bar. Sits
        above the bottom resize margin so edge-dragging still works, and
        being child widgets, none of them start a window drag."""
        scale = self._scale
        margin = button_margin(scale)
        side = button_side(scale)
        gap = sync_bar_gap(scale)
        bar_height = sync_bar_height(scale)
        top = self.height() - bar_height - sync_bar_bottom(scale)
        for button in (self._undo_button, self._sync_exit_button):
            button.setFixedSize(side, bar_height)
        self._undo_button.move(margin, top)
        self._sync_exit_button.move(self.width() - side - margin, top)
        tap_left = margin + side + gap
        self._tap_button.setFixedSize(
            max(1, self.width() - 2 * tap_left), bar_height
        )
        self._tap_button.move(tap_left, top)

    # -- spoken reference ----------------------------------------------------

    def _current_line_text(self) -> str:
        timeline = self._view_model.timeline()
        if timeline is None:
            return ""
        lines, index = timeline
        return lines[index][1] if 0 <= index < len(lines) else ""

    def _on_speak_clicked(self) -> None:
        line = self._current_line_text()
        if not line:
            return
        playing = self._last_state is PlaybackState.PLAYING
        if not self._speech.begin(playing):
            return  # already speaking: rapid clicks never stack
        self._speak_button.setEnabled(False)  # doubles as the busy indicator
        task = SpeakTask(
            line, pause_first=playing, resume_after=playing, rate=self._speech_rate
        )
        task.signals.finished.connect(self._on_speech_finished)
        self._pool.start(task)

    def _on_speech_finished(self) -> None:
        self._speech.finish()  # resume already handled inside the worker
        self._speak_button.setEnabled(True)
        # Speaking during ATTEMPT needs no special handling: we stayed
        # paused, and the attempt simply continues until the user clicks 🎤.

    def _update_speak_button(self, line_text: Optional[str] = None) -> None:
        if line_text is None:
            line_text = self._current_line_text()
        self._offer_control(
            self._speak_button,
            button_visible(
                synced=self._view_model.display().mode is Mode.SYNCED,
                line_text=line_text,
                feature_enabled=self._spoken_enabled,
                voice_ok=self._speech_available,
            ),
        )

    def _start_fetch(self, snapshot: PlayerSnapshot) -> None:
        task = FetchTask(self._provider, snapshot)
        task.signals.finished.connect(self._on_fetch_finished)
        self._pool.start(task)

    # -- the rest of the album, before it is needed -------------------------

    def _consider_warming(self) -> None:
        """Whether to look at this album, and every reason not to.

        Once per TRACK rather than once per album, and that is the whole of
        what the window contributes to the two stages: a second track from
        an album is what says somebody is listening to the album rather
        than to a song, and a guard that stopped at the first track would
        be the guard that hides the signal. Which stage is owed — a search,
        the per-track pass, or nothing at all — is decided in the provider,
        from what is already on disk.

        Arming a timer rather than starting the work: the point of warming
        is that nobody is waiting for it, and the moment a lookup lands is
        the moment somebody just was. See _WARM_DELAY_MS.
        """
        snapshot = self._current_snapshot
        if snapshot is None or not snapshot.album or not snapshot.artist:
            return
        considered = (snapshot.artist, snapshot.album, snapshot.track_id)
        if considered in self._warm_considered:
            return  # this track has already had its turn
        if self._view_model.failures:
            # The service is failing. Speculative requests are the last
            # thing to send at a service that cannot answer the ones
            # somebody is waiting for, and the first thing to stop sending
            # at one that has asked for a pause.
            return
        if self._view_model.display().mode in (Mode.ERROR, Mode.FETCHING):
            return
        self._warm_considered.add(considered)
        self._warm_pending = snapshot
        self._warm_timer.start(_WARM_DELAY_MS)

    def _start_warm(self) -> None:
        """Put whichever stage this album is owed on a worker. One at a
        time, for ever: the per-track stage sleeps between requests by
        design, and two of them would be two albums interleaving their gaps
        into a stream of requests neither of them agreed to."""
        snapshot, self._warm_pending = self._warm_pending, None
        if snapshot is None or self._warm_stop.is_set():
            return
        logger.info("warming the rest of %r", snapshot.album)
        self._pool.start(WarmTask(self._provider, snapshot, self._warm_stop))

    def _tick_retry(self) -> None:
        """Honour "will retry": while in ERROR, re-attempt the fetch for
        the current track every RETRY_INTERVAL_SECONDS."""
        if self._view_model.retry_due(time.monotonic()):
            if self._current_snapshot is not None:
                self._start_fetch(self._current_snapshot)
            self._render()

    def _tick_dots(self) -> None:
        if self._view_model.display().mode is Mode.FETCHING and not self._card_on_screen():
            self._dots_frame = (self._dots_frame + 1) % len(_DOTS_FRAMES)
            self._set_line_text(self._current, _DOTS_FRAMES[self._dots_frame])

    # -- anticipatory line fade --------------------------------------------

    def _predicted_ahead(
        self, lines: list, index: int, position_seconds: Optional[float]
    ) -> bool:
        """Whether the screen being one line ahead of the view model is this
        window's own doing.

        The predicted swap runs a phase before the line's timestamp, so for
        most of that phase — and the poll interval it takes the player to
        catch up — the two legitimately disagree. Read as a missed
        prediction instead, it snapped the display back to the previous
        line and let the next poll play the whole change again: the same
        line change, twice, which is what this bug looked like.

        Only ever one line, only the line this transition owns, and only
        while the player is still close enough to it for the choreography
        to explain the gap. Anything else is the world having moved.
        """
        target = index + 1
        if position_seconds is None or self._displayed_index != target:
            return False
        if target >= len(lines):
            return False
        return self._transition.leads(target, lines[target][0], position_seconds)

    def _schedule_line_advance(
        self, lines: list, index: int, position_seconds: float
    ) -> None:
        """(Re)arm the fade-out/swap timers from the next line's timestamp.
        Rescheduled on every poll, so seeks correct the timing within one
        poll interval — but only until the movement to that line has begun.
        Past that point the schedule is settled: re-arming it from a poll
        that landed mid-choreography is what made one line change play
        twice, the second time in a hurry, because the eta it re-derived
        was the remainder rather than the gap."""
        upcoming = index + 1
        if upcoming >= len(lines):
            self._fadeout_timer.stop()
            self._swap_timer.stop()
            return
        if not self._transition.may_arm(upcoming):
            return
        eta_ms = int((lines[upcoming][0] - position_seconds) * 1000)
        if eta_ms <= 0:
            return  # the poll loop snaps it on the next update
        # Each phase gets at most half the gap, so a line that arrives
        # sooner than the full choreography gets a shorter version of the
        # same movement rather than a truncated one. Both phases still fit
        # before the timestamp, and the arrival still lands ON it — which
        # is the property that must survive at any tempo.
        self._transition_ms = min(_FADE_MS, eta_ms // 2)
        self._fadeout_timer.start(max(0, eta_ms - 2 * self._transition_ms))
        self._swap_timer.start(max(0, eta_ms - self._transition_ms))

    def _begin_fade_out(self) -> None:
        """The outgoing line leaves upward, in the direction the song is
        going. Eased IN — it accelerates away, which reads as departure
        rather than as something being switched off.

        Sine rather than cubic: cubic's ends are steep enough that a
        260ms phase still reads as a flick. The pair stays In-then-Out so
        the outgoing is fastest exactly where the incoming picks up, and
        velocity is continuous across the swap — which is what makes two
        phases read as one movement.

        This is also where a line change is claimed: the movement starting
        is the moment the transition becomes one thing that is happening
        rather than one thing that is scheduled. A second trigger for the
        same line — a re-armed timer, a poll that arrived mid-flight — is
        refused here and changes nothing.
        """
        timeline = self._view_model.timeline()
        if timeline is None or self._card_on_screen():
            return
        if self._last_state is not PlaybackState.PLAYING:
            return
        lines, index = timeline
        if index + 1 >= len(lines) or not self._transition.begin(index + 1):
            return
        self._animate_line(-1.0, QEasingCurve.Type.InSine)

    def _predicted_swap(self) -> None:
        timeline = self._view_model.timeline()
        if timeline is None or self._card_on_screen():
            return
        if self._last_state is not PlaybackState.PLAYING:
            return
        lines, index = timeline
        target = index + 1
        # Only advance one step beyond what's on screen; anything else
        # means the world moved (seek/track change) and _render owns it.
        if self._displayed_index != index or target >= len(lines):
            return
        self._set_lines(lines, target)
        self._displayed_index = target
        # The fade-out claimed this line a phase ago; claiming it again is
        # a no-op. It matters for the one case where that phase never ran
        # (the title card was still up when its timer fired), so that the
        # display being a line ahead of the player is recognisably ours
        # either way.
        self._transition.begin(target)
        # The new line starts below and rises into place, eased OUT so it
        # decelerates onto its mark. The schedule is unchanged and still
        # authoritative: this animation ENDS at the line's timestamp, so
        # the motion completes on time rather than starting on time.
        self._current_fx.progress = 1.0
        self._animate_line(0.0, QEasingCurve.Type.OutSine)

    def _animate_line(self, end: float, curve: QEasingCurve.Type) -> None:
        if self._fade_anim is not None:
            self._fade_anim.stop()
        # A phase is where the cache pays for itself, and its first frame
        # is the one render it costs. Dropped here rather than trusted
        # from the swap, because the swap is only one of the two phases
        # and the other one starts from whatever was already on screen.
        self._current_fx.invalidate()
        animation = QPropertyAnimation(self._current_fx, b"progress", self)
        # The duration this transition was scheduled for, which is the
        # nominal one unless the next line arrives too soon for it.
        animation.setDuration(self._transition_ms)
        animation.setEasingCurve(curve)
        animation.setEndValue(end)
        animation.start()
        self._fade_anim = animation

    def _cancel_line_schedule(self) -> None:
        """Back to rest, instantly. Every path that means "the world moved"
        — a seek, a pause, a loop wrap, entering sync mode, a track change,
        any render at all — comes through here, so no animation can outlive
        the situation it was describing or leave a line parked off its
        mark. Nothing is in flight afterwards, which is what lets the next
        poll schedule a fresh transition to the very line this one was
        abandoning."""
        self._fadeout_timer.stop()
        self._swap_timer.stop()
        self._transition.clear()
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim = None
        self._current_fx.progress = 0.0

    def _set_lines(self, lines: list, index: int) -> None:
        # The one place a synced line lands on the window — both the
        # predicted swap and the snap in _render come through here — which
        # makes it the one place that can say "the lyric advanced". Compared
        # against _displayed_index, which is still the PREVIOUS index at this
        # point: both callers assign it afterwards.
        self._advance_menubar_step(index)
        current = lines[index][1] if index >= 0 else ""
        # The ↻ marker is display-only: pronunciation is looked up on the
        # unprefixed line text.
        # ↻ marks the engaged loop through both phases; the 🎤 done-button
        # (not a text marker) is the your-turn signal during ATTEMPT.
        shown = f"↻ {current}" if self._loop.engaged else current
        self._set_context(
            lines[index - 1][1] if index >= 1 else "",
            lines[index + 1][1] if index + 1 < len(lines) else "",
        )
        self._set_line_text(self._current, shown)
        self._set_pronunciation(self._view_model.pronunciation_for(current))
        self._update_speak_button(current)

    def _set_context(self, previous: str, upcoming: str) -> None:
        """The lines either side of the sung one.

        The compact layout has no rows for them and gives up filling them
        as well as showing them: "off removes the work" is the rule every
        layer here is held to, and a row nobody can see is exactly the
        output that outlives the work when it is not.
        """
        if self._compact_applied:
            return
        self._previous.setText(previous)
        self._upcoming.setText(upcoming)

    def _set_line_text(self, label, text: str) -> None:
        """Put a lyric on the sung row or the one under it.

        FOUND BY SCREENSHOT, and it is why this exists at all. The full
        layout wraps a long line onto a second row and its floor leaves
        room for one; a strip is one row tall by construction, so the
        second row had nowhere to go and landed halfway on top of the
        romanisation, cut through the middle. Numbers said nothing was
        wrong: the window was at its floor and the floor was correct.

        So in the compact layout a line that does not fit ends in an
        ellipsis instead. The width is computed from the window and the
        gutters rather than read off the label, because the label's own
        width is a layout result and this runs before the layout that
        would produce it.

        The unelided text is kept, because the elision has to be redone
        whenever the window changes width or the type scale moves, and
        re-eliding an already elided line would eat it a word at a time.
        """
        self._full_text[label] = text
        # Both rows of the sung block are behind the fade's cached
        # rasterisation, and this is the only thing that writes either of
        # them — including the re-elision a resize does mid-change.
        self._current_fx.invalidate()
        if not self._compact_applied:
            label.setText(text)
            return
        label.ensurePolished()
        room = self.width() - 2 * compact_text_gutter(self._scale)
        label.setText(
            QFontMetricsF(label.font()).elidedText(
                text, Qt.TextElideMode.ElideRight, max(1, room)
            )
        )

    def _reelide(self) -> None:
        """Lay the lyric rows out again for a width or a type scale that
        has changed. A no-op with the compact layout off, where the rows
        wrap and nothing was shortened."""
        for label in (self._current, self._pron):
            self._set_line_text(label, self._full_text.get(label, ""))

    def _set_pronunciation(self, text: str) -> None:
        self._set_line_text(self._pron, text)
        # A row appearing or going changes the block's shape, which
        # _set_line_text above has no way to know about.
        self._pron.setVisible(bool(text))
        self._current_fx.invalidate()

    # -- rendering ---------------------------------------------------------

    def _card_active(self) -> bool:
        """The card's two seconds have not run out yet."""
        return time.monotonic() < self._title_card_until

    def _card_on_screen(self) -> bool:
        """The card is actually up — which is the question every caller
        here is asking, and not quite the same as its time being unspent.

        It ends early when the lyrics land with something to put on
        screen, so this is also what stops the anticipatory schedule from
        sitting out the rest of a card that is no longer showing.
        """
        return self._card_active() and not card_yields(self._view_model.display())

    def _render(self) -> None:
        display = self._view_model.display()
        self._cancel_line_schedule()
        # Menu entries follow mode and lyrics, so keep the menu bar item in
        # step here rather than only when it is about to open.
        self._refresh_menu()

        # Loop button only where looping is possible (synced timestamps).
        # Offered rather than shown: in the compact layout having something
        # to do and being on screen are two questions, and _offer_control
        # answers the first while the reveal answers the second.
        self._offer_control(self._loop_button, display.mode is Mode.SYNCED)
        self._offer_control(
            self._attempt_button,
            display.mode is Mode.SYNCED and self._awaiting_attempt(),
        )
        if display.mode is not Mode.SYNCED:
            # Synced path updates it per line.
            self._offer_control(self._speak_button, False)
        # Hidden for every mode by default and offered back at the very
        # bottom of this method, which is the only path that can reach
        # ERROR: every branch between here and there returns. The retry
        # goes with it — it belongs to the reason, and the reason belongs
        # to the failure.
        self._why_button.setVisible(False)
        self._retry_button.setVisible(False)
        if self._view_model.track_id != self._why_track:
            # A new song asks its own question. The reveal survives the 30s
            # retries (which pass through FETCHING and back) but not a
            # track change, which is a different failure or none at all.
            self._why_track = self._view_model.track_id
            self._why_shown = False
        self._render_sync_controls(display)

        # Persistent header whenever a track is known, and never in the
        # compact layout: what the song is called is the first thing a
        # strip gives up.
        self._header.setText(display.header)
        self._header.setVisible(bool(display.header) and not self._compact_applied)

        if display.mode is Mode.SYNCING:
            self._displayed_index = None
            self._show_plain_view(False)
            self._set_context(display.previous, display.upcoming)
            self._set_line_text(self._current, display.current)
            self._set_pronunciation(display.pronunciation)
            return

        if display.header and display.mode is not Mode.IDLE and self._card_on_screen():
            # Title card: the song announces itself before lyrics start.
            self._displayed_index = None
            self._show_plain_view(False)
            self._set_context("", "")
            self._set_line_text(self._current, display.header)
            self._set_pronunciation("")
            return

        if display.mode is Mode.SYNCED:
            self._show_plain_view(False)
            timeline = self._view_model.timeline()
            if timeline is not None:
                lines, index = timeline
                self._set_lines(lines, index)
                self._displayed_index = index
            return

        self._displayed_index = None
        plain = display.mode is Mode.PLAIN
        self._show_plain_view(plain)
        if plain:
            self._plain_note.setText(display.previous)  # "plain lyrics — not synced"
            self._plain_label.setText(display.plain_text)  # full, uncapped
            return
        current = display.current
        if display.mode is Mode.FETCHING:
            current = _DOTS_FRAMES[self._dots_frame]
        self._set_context(display.previous, display.upcoming)
        self._set_line_text(self._current, current)
        self._set_pronunciation(display.pronunciation)
        self._render_why(display)

    def _render_why(self, display) -> None:
        """The quiet way to ask why the lyrics are not here, and what it
        answers with.

        Offered only in ERROR mode, which is the whole point: a song that
        genuinely has no lyrics says "no lyrics found" and offers nothing
        to click, so the difference between "this service is having a bad
        day" and "nobody has written this song's lyrics down" stays a
        difference you can see without reading either message twice.

        The reason lands in the row directly under the message it explains
        — already empty in this mode, already the dim context colour. A
        second widget for it would be a second thing to place, style and
        keep in the type scale, for a line that is on screen about as often
        as a track fails. Which row that is depends on the layout, and
        that is the whole of what compact changes here: the full layout's
        upcoming row is gone, and the pronunciation row underneath the
        message has taken its place.
        """
        offered = display.mode is Mode.ERROR and bool(display.detail)
        self._why_button.setVisible(offered)
        self._why_button.setChecked(self._why_shown)
        # The retry follows the REASON, not the failure: it is on screen
        # exactly while the explanation it belongs to is. Set here rather
        # than only in the branch below, so putting the reason away takes
        # it with it.
        self._retry_button.setVisible(offered and self._why_shown)
        if not offered:
            return
        if self._why_shown:
            if self._compact_applied:
                self._set_pronunciation(display.detail)
            else:
                self._upcoming.setText(display.detail)
        self._place_why_button()
        if self._why_shown:
            self._place_retry_button()

    def _toggle_why(self, shown: bool) -> None:
        """Show the reason, or put it away again. Reversible on purpose:
        this is a thing to glance at, not a state to get stuck in."""
        self._why_shown = shown
        logger.debug("lyrics failure reason %s", "revealed" if shown else "hidden")
        self._render()

    def _on_retry_clicked(self) -> None:
        """Ask again now, rather than waiting out the schedule.

        Somebody pressing this knows something the schedule does not — the
        wifi is back, the VPN is off — so it takes the interval back to the
        start as well as asking. What it cannot take back is a pause LRCLIB
        asked for: that request goes out, is refused at the provider's
        door, and comes back as the reason saying so. See
        LyricsViewModel.retry_now.
        """
        if not self._view_model.retry_now():
            return
        logger.info("retry asked for by hand")
        if self._current_snapshot is not None:
            self._start_fetch(self._current_snapshot)
        self._render()

    def _place_why_button(self) -> None:
        """Just after the end of the message, wherever that falls.

        Measured from the text rather than pinned to a corner: "lyrics
        unavailable, will retry" is centred and the window is resizable, so
        a fixed position would be beside the message at one width and
        stranded in white space at every other. geometry.py owns the rule,
        including what to do when the message wraps and its laid-out width
        IS the row.
        """
        side = button_side(self._scale)
        self._why_button.setFixedSize(side, side)
        top_left = self._current.mapTo(self, QPoint(0, 0))
        self._current.ensurePolished()
        advance = QFontMetricsF(self._current.font()).horizontalAdvance(
            self._current.text()
        )
        self._why_button.move(
            beside_centred_text(
                top_left.x(),
                self._current.width(),
                advance,
                side,
                self._scale,
                self.width(),
            ),
            top_left.y() + (self._current.height() - side) // 2,
        )
        self._why_button.raise_()

    def _place_retry_button(self) -> None:
        """Beside the reason, by the same rule the ⓘ is placed beside the
        message: measured from the text rather than pinned to a corner.

        The row it follows is whichever row the reason went into, which is
        the one thing the compact layout changes here — the same fork
        _render_why takes, for the same reason it takes it.
        """
        side = button_side(self._scale)
        self._retry_button.setFixedSize(side, side)
        row = self._pron if self._compact_applied else self._upcoming
        row.ensurePolished()
        top_left = row.mapTo(self, QPoint(0, 0))
        advance = QFontMetricsF(row.font()).horizontalAdvance(row.text())
        self._retry_button.move(
            beside_centred_text(
                top_left.x(),
                row.width(),
                advance,
                side,
                self._scale,
                self.width(),
            ),
            top_left.y() + (row.height() - side) // 2,
        )
        self._retry_button.raise_()

    def _render_sync_controls(self, display) -> None:
        """The tap row and its status line. The tap bar goes inert while
        playback is paused — the session waits rather than stamping a
        position that isn't moving."""
        syncing = display.mode is Mode.SYNCING
        session = self._view_model.sync_session
        for button in (self._tap_button, self._undo_button, self._sync_exit_button):
            button.setVisible(syncing)
        self._progress.setVisible(syncing)
        if not syncing:
            return
        playing = self._last_state is PlaybackState.PLAYING
        self._tap_button.setEnabled(playing)
        self._tap_button.setText("TAP" if playing else "PAUSED")
        self._undo_button.setEnabled(bool(session and session.index > 0))
        # The armed prompt is coloured inline rather than by object name:
        # a stylesheet swap would need a repolish on every render.
        self._progress.setText(
            self._exit_confirm_text() if self._exit_armed else display.progress
        )
        self._place_sync_controls()

    def _exit_confirm_text(self) -> str:
        """The armed discard prompt, in this appearance's warning colour —
        a pale red on a dark panel is invisible on a light one."""
        return (
            f'<span style="color: {appearance.rgba(self._palette.confirm_text)}">'
            f"{_EXIT_CONFIRM_TEXT}</span>"
        )

    def _show_plain_view(self, plain: bool) -> None:
        """Swap between the scrolling plain body and the synced rows; the
        hidden side gives up ALL its layout space, stretches included.

        The note above the body ("plain lyrics · not synced") goes with the
        header in the compact layout, and for the same reason: it says
        something about the song rather than showing a line of it.
        """
        self._plain_note.setVisible(plain and not self._compact_applied)
        self._plain_scroll.setVisible(plain)
        self._synced_box.setVisible(not plain)

    # -- album colour ------------------------------------------------------

    def _background_for(self, tint_rgb) -> appearance.RGBA:
        """The window's background for this cover colour: the scrim when
        there is a material to sit on, the solid fill when there is not.

        ``tinted`` hands back the palette unchanged for an unusable colour
        (and None is one), so "no cover", "a grey cover" and "the feature
        is off" all land on exactly the same pixels.
        """
        palette = appearance.tinted(self._palette, tint_rgb, self._appearance)
        return palette.scrim if self._material is not None else palette.solid

    def _border_for(self, tint_rgb) -> appearance.RGBA:
        """The hairline for this cover colour.

        Where the album is actually felt. The panel's own luminance is
        pinned by the contrast floor and has almost no room left for
        colour — least of all in light mode, where the pale panel is close
        enough to white that a saturated hue would have to darken it to
        show at all. Nothing is read against the hairline, so nothing
        stops it taking the hue properly.
        """
        return appearance.tinted(self._palette, tint_rgb, self._appearance).border

    def _current_background(self) -> appearance.RGBA:
        """What is on screen right now, mid-fade included."""
        return appearance.blend(self._tint_from, self._tint_to, self._tint_mix)

    def _current_border(self) -> appearance.RGBA:
        """The hairline the album has asked for — the same mix as the
        panel, so the edge and the panel arrive together.

        Deliberately without the acknowledgement glow. This is what the
        tint cross-fade starts and ends on, and a glow folded in here
        would be captured as ``_border_from`` by any fade that began mid-
        acknowledgement and stay in the edge for good. Borrowed is not the
        same as taken.
        """
        return appearance.blend(self._border_from, self._border_to, self._tint_mix)

    def _painted_border(self) -> appearance.RGBA:
        """What the hairline actually is this frame: the album's edge, warmed
        by however much of the acknowledgement is showing.

        The glow lives here and nowhere else — one mix applied at the last
        moment, over whatever the tint currently says. That is what lets a
        cover arriving mid-glow cross-fade underneath it without either
        animation knowing about the other, and what makes handing the edge
        back a matter of the mix reaching zero rather than of restoring a
        remembered value.
        """
        edge = self._current_border()
        if self._glow <= 0.0:
            return edge
        return appearance.blend(edge, self._palette.learned_glow, self._glow)

    def _set_tint(self, tint_rgb, animate: bool = True) -> None:
        """Cross-fade the panel and its edge to a new cover colour.

        The fade starts from whatever is on screen rather than from the
        previous target, so a track changed halfway through the last fade
        moves on from where it had got to instead of jumping back.
        """
        if tint_rgb == self._tint_rgb:
            return
        self._tint_rgb = tint_rgb
        start = self._current_background()
        end = self._background_for(tint_rgb)
        edge_start = self._current_border()
        edge_end = self._border_for(tint_rgb)
        if self._tint_anim is not None:
            self._tint_anim.stop()
            self._tint_anim = None
        if (start, edge_start) == (end, edge_end) or not animate:
            self._tint_from = self._tint_to = end
            self._border_from = self._border_to = edge_end
            self._tint_mix = 1.0
            self.update()
            return
        self._tint_from, self._tint_to, self._tint_mix = start, end, 0.0
        self._border_from, self._border_to = edge_start, edge_end
        animation = QVariantAnimation(self)
        animation.setDuration(_TINT_FADE_MS)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.valueChanged.connect(self._on_tint_step)
        animation.start()
        self._tint_anim = animation

    def _on_tint_step(self, value) -> None:
        self._tint_mix = float(value)
        self.update()

    def _resnap_tint(self) -> None:
        """Recompute the painted background and edge without a fade.

        For the two things that change what a tint LOOKS like rather than
        which tint it is: the system appearance flipping, and the material
        arriving. Fading either would be animating the wrong thing.
        """
        if self._tint_anim is not None:
            self._tint_anim.stop()
            self._tint_anim = None
        self._tint_from = self._tint_to = self._background_for(self._tint_rgb)
        self._border_from = self._border_to = self._border_for(self._tint_rgb)
        self._tint_mix = 1.0
        self.update()

    def _request_artwork(self, snapshot: Optional[PlayerSnapshot]) -> None:
        """Start a cover lookup, if there is any point.

        Nothing is fetched while the layer is off — a disabled feature
        does not get to make network requests — and nothing is fetched for
        DJ narration or ads, which reuse other tracks' identity.
        """
        if not self._album_colour:
            return
        if snapshot is None or not snapshot.is_music_track:
            return
        task = ArtworkTask(self._artwork, snapshot.track_id, snapshot.artwork_url)
        task.signals.finished.connect(self._on_artwork_ready)
        self._pool.start(task)

    def _on_artwork_ready(self, track_id: str, colour) -> None:
        # Stale answers are dropped: covers arrive out of order when tracks
        # are skipped through, and the last one to land is not the one on
        # screen. Also drops everything in flight when the layer is
        # switched off mid-fetch.
        if not self._album_colour or track_id != self._view_model.track_id:
            return
        self._set_tint(tuple(colour) if colour else None)

    def _set_album_colour(self, enabled: bool) -> None:
        self._album_colour = enabled
        self._settings.setValue("window/album_colour", enabled)
        if enabled:
            self._request_artwork(self._current_snapshot)
        else:
            # Back to the untinted palette, exactly. Faded rather than
            # snapped so switching it off looks like the same gesture as
            # switching it on.
            self._set_tint(None)
        self._refresh_menu()

    def paintEvent(self, event) -> None:
        """Rounded background at the same radius as the material behind it,
        so the two corners coincide rather than one clipping the other,
        then a hairline just inside that edge.

        The hairline is one DEVICE pixel, not one logical pixel: on a
        Retina screen those differ by a factor of two, and a two-pixel
        line is a border rather than an edge. It is inset by half its own
        width so it lands inside the fill instead of straddling the
        boundary — straddling would put half the stroke outside the
        material's mask and read as a second, softer edge beside the
        first.

        Both come from the tint state rather than from the palette
        directly, because both carry the album's hue and both have to
        arrive on the same cross-fade.

        ## A band away from the corners is drawn straight

        A line change repaints a strip across the middle of the window,
        37 times, and this runs for every one of them: measured with the
        sampler, it became the single largest thing in a line change once
        the sung line stopped being re-rasterised, and the two rounded
        rectangles were most of it.

        Between the corner radii the shape is not rounded — it is a
        rectangle with a vertical line down each side — so when the
        damaged rectangle lies inside that band the same pixels come out
        of three axis-aligned fills, with no path to build and no
        antialiasing to compute. That the pixels ARE the same is asserted
        rather than reasoned about: a test compares the two paths grab
        for grab, byte for byte, over every band the window has.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_panel(painter, QRectF(event.rect()))

    def _paint_panel(self, painter, damaged) -> None:
        """The panel and its edge, into whatever painter is handed over.

        Separated from paintEvent so the two routes below can be compared
        against each other on a plain image: a QPainter on a widget only
        exists during a real paint event, so a claim about identical
        pixels could not otherwise be checked.
        """
        rect = QRectF(self.rect())
        # One device pixel at rest, and up to three while a learned
        # position is being acknowledged: the colour alone was a change of
        # a few hundred pixels on a 460-point window, which the eye does
        # not catch unless it is already there. Both the width and the
        # colour ride the one glow value, so the edge cannot be left thick
        # and cool, and both return to exactly what they were.
        width = glow_width(1.0 / max(1.0, self.devicePixelRatioF()), self._glow)
        fill = _qcolor(self._current_background())
        edge = _qcolor(self._painted_border())

        if self._straight_band(damaged):
            # Three axis-aligned fills: the background across the damaged
            # rows, and the hairline down each side of them. The same two
            # colours and the same width as below, read from the same
            # place, so there is still one answer to what the panel looks
            # like. Antialiasing stays ON: the saving is the path, and
            # turning the rasteriser's rules off for one of two routes
            # that must agree would be trading the property for the cost.
            painter.fillRect(
                QRectF(rect.left(), damaged.top(), rect.width(), damaged.height()),
                fill,
            )
            painter.fillRect(
                QRectF(rect.left(), damaged.top(), width, damaged.height()), edge
            )
            painter.fillRect(
                QRectF(rect.right() - width, damaged.top(), width, damaged.height()),
                edge,
            )
            return

        square_top = self._docked
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(_panel_path(rect, _CORNER_RADIUS, square_top))

        inset = width / 2
        pen = QPen(edge)
        pen.setWidthF(width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(
            _panel_path(
                rect.adjusted(inset, inset, -inset, -inset),
                _CORNER_RADIUS - inset,
                square_top,
            )
        )

    def _straight_band(self, damaged) -> bool:
        """Whether ``damaged`` lies where the panel's outline is straight.

        Between the two corner radii, where the shape is a rectangle with
        a vertical line down each side. Nothing about the horizontal
        extent matters: the sides ARE the shape there, at every height in
        the band.
        """
        return (
            damaged.top() >= _CORNER_RADIUS
            and damaged.bottom() <= self.height() - _CORNER_RADIUS
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
        # macOS caches the shadow's silhouette; without this a resized
        # window keeps the shadow of the shape it used to be.
        self._invalidate_shadow()

    def moveEvent(self, event) -> None:
        """Every move: a drag, a clamp, a nudge, the travel to a remembered
        position, the flight, docking itself.

        One funnel rather than a call beside each of those, because the
        question is about where the window ended up and not about what put
        it there — and because the answer has to be right after a move
        somebody added later without knowing this existed.
        """
        super().moveEvent(event)
        self._update_docked()

    def _docked_anchor(self) -> tuple:
        """Where docking would put this window at its current width. The
        three things only a running window can answer, gathered in one
        place so the pure rule has nothing to look up for itself."""
        screen, available = self._screen_rects()
        return docked_position(self.width(), screen, available, self._top_inset())

    def _update_docked(self) -> None:
        """Notice the window arriving at, or leaving, the docked position.

        Recomputed rather than cached behind a flag, and asked in full on
        every move: MEASURED at 8.9us on this machine, of which 6.8us is
        the screen's safe area and 1.4us the two Qt rects, so a drag
        delivering 60 moves a second spends 0.05% of one core on it. A
        cache would
        have to be invalidated by a screen change, a Dock that moved, a menu
        bar set to hide itself and a resize, and the first one that forgot
        would leave the window drawn as docked from the middle of the
        screen. See geometry.is_docked for why there is no flag either.
        """
        screen, available = self._screen_rects()
        docked = is_docked(
            (self.x(), self.y(), self.width(), self.height()),
            screen,
            available,
            self._top_inset(),
        )
        if docked is self._docked:
            return
        self._docked = docked
        logger.debug("dock shape: %s", "square top" if docked else "rounded")
        # The material is a native view with its own corners, so the two
        # have to be told together or the blur keeps the shape the paint
        # has just left.
        self._apply_material_corners()
        self._invalidate_shadow()
        self.update()

    def _relayout(self) -> None:
        """Everything that follows the window's width.

        Called by the resize event, and again by anything that changes the
        window's shape itself: a HIDDEN widget defers its resize event
        until it is shown, so a strip resized while it was away would come
        back laid out for the width it used to have.
        """
        self._apply_scale()
        # After the scale, which is what decides both the type size the
        # elision measures with and the gutters it measures against.
        self._reelide()
        self._place_buttons()
        # A width change moves where docking would put the window, so a
        # window that WAS docked may not be any more and one that was not
        # may now be. Here rather than in resizeEvent, so it rides with
        # everything else that follows the window's shape.
        self._update_docked()

    def _type_scale(self) -> float:
        """The scale the type is set at.

        In the FULL layout, the window's own width, which is what makes
        dragging an edge a size control there: everything on the window is
        proportional to it.

        In the COMPACT layout, the size the user picked, and the width has
        nothing to do with it. MEASURED, and the measurement is why: with
        the type following the width, the room for a line and the line
        itself grow at exactly the same rate, so the ratio between them is
        a constant — (460 - 144) / T — and a line wider than 316pt at scale
        1.0 fits at NO width. 13 of 14 real songs are past that. A strip
        whose type followed its width could therefore be widened all
        afternoon without one more character of the line appearing, which
        is not a window that is hard to use so much as a control that does
        nothing.

        Naming the size directly is what gives the width a job. The type
        stands still, so a wider strip is more of the line and a narrower
        one is less; the song's fit measures against a size that is not
        about to move; and the strip's HEIGHT is still while its width
        moves, since the floor comes off the same scale.
        """
        return self._type_scale_at(self.width(), self._compact_applied)

    def _type_scale_at(self, width: int, compact: bool) -> float:
        """The scale a layout this wide would set its type at.

        Asked about a shape the window has not taken yet as well as about
        the one it is in, which is why it takes both rather than reading
        either. Everything that changes the window's shape has to know the
        scale it is about to land on BEFORE it lands: the height floor
        comes off the scale, Qt will not let a resize through its own
        minimum, and a floor computed from the scale the window is leaving
        is how a strip gets clamped up to the five-row height it is trying
        to stop being.
        """
        return compact_scale(self._compact_text_size) if compact else _scale_for(width)

    def _restyle(self) -> None:
        """The one place the window's stylesheet is written.

        One place because the sung line's effect caches what it rasterised
        of that line, and a colour or a type size arriving without the
        cache being dropped is a line drawn in the appearance it used to
        have. Two callers, a scale change and an appearance change, and
        neither has to remember: this does.
        """
        self.setStyleSheet(_style_for(self._scale, self._family_stack, self._palette))
        self._current_fx.invalidate()

    def _apply_scale(self) -> None:
        """Fonts, margins, spacing, and button boxes track window width
        near-linearly, so everything stays visually proportional from min
        size to max."""
        scale = self._type_scale()
        if abs(scale - self._scale) > 0.01:
            self._scale = scale
            self._restyle()
            self._apply_layout_margins()
            row_gap = max(1, round(ROW_SPACING * scale))
            self._layout.setSpacing(row_gap)
            self._synced_layout.setSpacing(row_gap)
            # Far tighter than the row gap: the pronunciation belongs to the
            # line above it, and a uniform gap would read as three separate
            # rows instead of one block.
            self._current_layout.setSpacing(
                max(1, round(PRONUNCIATION_SPACING * scale))
            )
            self._apply_motion()
            self._apply_tracking()
            side = button_side(scale)
            for button in (self._loop_button, self._speak_button, self._attempt_button):
                button.setFixedSize(side, side)
            self._apply_speak_icon(side)
            self._apply_why_icon(side)
            self._apply_retry_icon(side)
            self._place_buttons()

    def _apply_motion(self) -> None:
        """How far a line travels as it is replaced, at this scale: its
        full rise, or none of it.

        Reduce Motion takes the travel and leaves the fade, which is what
        that setting asks for rather than a compromise — the whole point
        of ``progress`` being ONE signed number is that the opacity and
        the offset are the same journey, so removing the offset is setting
        its length to zero and changing nothing else. The choreography, its
        timing and its arrival on the timestamp are all untouched: what was
        a rise becomes a cross-fade of exactly the same length.
        """
        self._current_fx.travel = (
            0.0
            if self._display_options.reduce_motion
            else max(1.0, LINE_TRAVEL * self._scale)
        )

    def _apply_tracking(self) -> None:
        """Tighten the sung line's letter-spacing.

        Qt stylesheets have no letter-spacing property, so this is the one
        type setting that cannot live in the stylesheet with the others.
        The font is read back AFTER the stylesheet has been applied and
        polished, so size, weight and family still come from there and
        typography.py stays the single source — this only adds the one
        thing the stylesheet cannot say.
        """
        style = style_for(CURRENT, self._scale)
        self._current.ensurePolished()
        font = self._current.font()
        font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, style.tracking
        )
        self._current.setFont(font)

    def _apply_speak_icon(self, side: int) -> None:
        """Draw the speak button as its SF Symbol, at this scale.

        Re-rendered per scale rather than scaled from one pixmap: SF
        Symbols are drawn for the point size they are asked for, so a
        resized window gets a glyph rendered at its size instead of a
        blurred one. Where symbols are unavailable this does nothing and
        the button keeps the text glyph it was built with.

        Re-rendered per appearance too, and for the same reason the tint
        exists at all: the symbol arrives as a template image carrying its
        shape in the alpha channel and takes its colour from us, so a
        white glyph would stay white on a pale panel. The three states are
        the palette's, which is what keeps the icon and the stylesheet
        that paints its fallback glyph describing the same thing.
        """
        icon = symbol_icon(
            SPEAK_SYMBOL,
            float(icon_size(side).width()),
            _qcolor(self._palette.control_idle),
            active=_qcolor(self._palette.control_hover),
            disabled=_qcolor(self._palette.control_engaged),
        )
        if icon is None:
            return
        self._speak_button.setText("")
        self._speak_button.setIcon(icon)
        self._speak_button.setIconSize(icon_size(side))

    def _apply_why_icon(self, side: int) -> None:
        """The same treatment for the reveal control: the system's own
        info glyph, tinted from the same three control colours, falling
        back to the text glyph it was built with.

        Its engaged colour is the icon's On state rather than a mode: this
        button is checkable and is never disabled, so what says "the reason
        is on screen" is the same thing the loop button's stylesheet says
        with ``:checked``.
        """
        icon = symbol_icon(
            WHY_SYMBOL,
            float(icon_size(side).width()),
            _qcolor(self._palette.control_idle),
            active=_qcolor(self._palette.control_hover),
            checked=_qcolor(self._palette.control_engaged),
        )
        if icon is None:
            return
        self._why_button.setText("")
        self._why_button.setIcon(icon)
        self._why_button.setIconSize(icon_size(side))

    def _apply_retry_icon(self, side: int) -> None:
        """And the same for the one control beside the reason that acts.

        Two states rather than the ⓘ's three: it is not checkable, because
        there is nothing for it to be in the middle of — a press dispatches
        a lookup and the window is already in FETCHING before the button
        could have drawn itself engaged.
        """
        icon = symbol_icon(
            RETRY_SYMBOL,
            float(icon_size(side).width()),
            _qcolor(self._palette.control_idle),
            active=_qcolor(self._palette.control_hover),
        )
        if icon is None:
            return
        self._retry_button.setText("")
        self._retry_button.setIcon(icon)
        self._retry_button.setIconSize(icon_size(side))

    def _apply_layout_margins(self) -> None:
        """Side margins reserve the button gutters (geometry.py owns the
        shared math) so wrapped text can never run under a button; during a
        sync pass the bottom margin also reserves the tap row, and the
        height floor grows with it so no window shape can bury the row.

        Compact reserves a wider gutter, because its two right-hand
        controls sit side by side rather than stacked, and gives up the air
        around the sung line, because there is nothing left either side of
        it to be separated from. Both are the same arithmetic in
        geometry.py either way; only which numbers it is asked for changes.
        """
        scale = self._scale
        compact = self._compact_applied
        gutter = compact_text_gutter(scale) if compact else text_gutter(scale)
        syncing = self._syncing
        bottom = round(BOTTOM_MARGIN * scale) + (
            sync_bar_reserve(scale) if syncing else 0
        )
        self._layout.setContentsMargins(
            gutter, round(TOP_MARGIN * scale), gutter, bottom
        )
        # Extra air above and below the sung line, on top of the row gap.
        # It is what stops the three lyric rows reading as an evenly spaced
        # list with one of them in bold, so compact has no use for it.
        current_air = 0 if compact else max(1, round(CURRENT_SPACING * scale))
        self._current_layout.setContentsMargins(0, current_air, 0, current_air)
        # No window shape may hide the lyrics: height floor follows scale.
        self.setMinimumHeight(
            min_window_height(scale, sync_bar=syncing, compact=compact)
        )

    def _place_buttons(self) -> None:
        margin = button_margin(self._scale)
        side = self._loop_button.width()
        middle = (self.height() - side) // 2
        if self._compact_applied:
            # A strip has no top-right corner to put anything in, so the
            # two right-hand controls come down onto the centre line and
            # sit side by side, in the order the full layout stacks them.
            self._loop_button.move(self.width() - side - margin, middle)
            self._speak_button.move(
                self.width() - 2 * side - control_gap(self._scale) - margin, middle
            )
        else:
            self._loop_button.move(self.width() - side - margin, margin)
            self._speak_button.move(self.width() - side - margin, middle)
        # The done-button mirrors the speaker on the left, beside the line.
        self._attempt_button.move(margin, middle)
        self._place_sync_controls()
        if self._why_button.isVisibleTo(self):
            # Placed from the message rather than from a corner, so it has
            # to be re-placed whenever the window's shape changes.
            self._place_why_button()
        if self._retry_button.isVisibleTo(self):
            self._place_retry_button()
        for button in (
            self._loop_button,
            self._speak_button,
            self._attempt_button,
            self._tap_button,
            self._undo_button,
            self._sync_exit_button,
            self._why_button,
            self._retry_button,
        ):
            button.raise_()

    def _available_geometry(self) -> QRect:
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1440, 900)

    # -- the compact layout -------------------------------------------------

    @property
    def _compact_active(self) -> bool:
        """Whether the compact layout should be in force right now.

        What the user asked for, minus the one thing that takes it back. A
        sync pass needs the line before, the line after, a status row and a
        tap bar across the bottom, and a strip has room for one of those:
        the bar alone would BE the window. So the full layout is borrowed
        for as long as the pass runs and given back when it ends, which is
        the honest version of "behaves sensibly in compact" — the layout
        that can carry the mode, rather than a strip that pretends to.
        """
        return self._compact and not self._syncing

    def _set_compact(self, enabled: bool) -> None:
        """Turn the compact layout on or off.

        Off is not "compact with the rows back": it is the layout the app
        has always had, at the height it was last left at, with no reveal,
        no hover watching and no opacity effect on anything. That is the
        layers rule, and this layer is held to it like the rest.
        """
        if enabled == self._compact:
            return
        self._compact = enabled
        self._apply_compact()
        self._render()
        self._save_settings()
        logger.info(
            "compact layout %s (%dx%d)",
            "on" if enabled else "off",
            self.width(),
            self.height(),
        )

    def _apply_compact(self) -> None:
        """Put the window in the shape the layout in force asks for.

        The one way in for both callers — the menu toggle and a sync pass
        borrowing the full layout — so there is no path that can leave the
        rows of one layout inside the margins of the other.

        The FULL layout's height is given back rather than recomputed: it
        keeps the height it was last left at, so going compact and coming
        back gives the window its old shape, and a sync pass — which
        borrows the full layout without being asked to — hands the strip
        back the way it found it.

        The STRIP'S height is computed, because there is nothing to
        remember. It is one row of type, and which row of type is the one
        setting this layout has.
        """
        wanted = self._compact_active
        changed = wanted is not self._compact_applied
        if changed:
            # The window is about to change shape, and a dodge is holding
            # its position: reshaping it where it stands would leave the
            # home describing a window that no longer exists. Landed
            # rather than travelled, and ``stand_down`` rather than a
            # release, so nothing steps aside again under a hand that
            # never left.
            self._set_dodged(False, animate=False)
            self._proximity.stand_down()
            self._remember_shape(self._compact_applied)
            self._compact_applied = wanted
            self._hovered = wanted and self._pointer_inside()
        for row in (self._previous, self._upcoming):
            row.setVisible(not wanted)
        # A strip has room for one row, so its rows do not wrap: what will
        # not fit is elided in _set_line_text instead.
        for row in (self._current, self._pron):
            row.setWordWrap(not wanted)
        if changed:
            self._stop_fit()
            width = self._width_for(wanted)
            # The floor BEFORE the resize, at the scale the new layout will
            # be at rather than the one being left: Qt will not let a
            # resize through its own minimum, so a strip asked to be 79
            # tall while the five-row floor of 183 was still in force would
            # simply stay 183 and the layout would look like it had failed.
            self.setMinimumHeight(
                min_window_height(
                    self._type_scale_at(width, wanted),
                    sync_bar=self._syncing,
                    compact=wanted,
                )
            )
            self.resize(width, self._height_for(wanted))
            if wanted and not self._shapes.recall(True).width:
                # The first strip. The width it opened at is the user's
                # own, and is what turning the fit off gives back, so it
                # has to be written down BEFORE anything fits.
                self._shapes.remember(True, width=self.width())
        # After the resize: in the full layout the scale is the width's,
        # and a hidden widget defers its resize event until it is shown, so
        # nothing else would recompute it. The margins follow unconditionally
        # because which layout is in force has changed even when the scale
        # has not.
        self._apply_scale()
        self._apply_layout_margins()
        if changed:
            # Instant: the layout has just changed shape anyway, and a
            # strip that arrived and then grew would read as two events.
            self._fit_width(animate=False)
            # And AFTER the fit, because fitting anchors a width change on
            # the window's centre: put the layout back where it was left
            # and the song's opinion about the width has already been
            # taken. Instant, for the same reason the fit is — one change
            # of shape, not a change and then a journey.
            self._restore_layout_position()
        self._reelide()
        self._apply_reveal_effects()
        self._update_pointer_watch()
        self._place_buttons()

    def _restore_layout_position(self) -> None:
        """Put the layout now in force back where it was last left.

        A layout that has never been worn has nowhere to go back to and
        the window stays where it is, which is the same answer
        ``_width_for`` gives about a width nobody has chosen.

        Clamped to the screen, because a remembered position is only a
        preference and the display it was expressed on may be gone.

        A plain move, with no branch for a window standing aside from the
        pointer, and that is not an omission: the only caller changes the
        layout, and changing the layout gives the dodge back first
        (``_set_dodged(False)`` and ``stand_down()``) precisely so the
        window is not reshaped where it is only standing. A branch here
        could never run, and a branch that cannot run is a claim nothing
        can check.
        """
        shape = self._shapes.recall(self._compact_applied)
        if not shape.has_position:
            return
        target = _clamped_point(
            QRect(QPoint(shape.x, shape.y), self.size()),
            self._available_geometry(),
        )
        self.move(target)
        # Whether the window is docked is asked of the position, never
        # held as a flag, so a move is the moment to ask again.
        self._update_docked()

    def _remember_shape(self, compact: bool) -> None:
        """Write down where this layout is being left, and how big.

        The POSITION is ``_home_pos()`` and never ``pos()``: a dodge, a
        flight and a notification yield all stand the window somewhere it
        does not live, and a temporary position written down as a
        permanent one is the bug this app has now met four times.

        The strip's HEIGHT is not written down at all: it follows the type
        size, so there is one right answer for it and remembering another
        would only be a way to disagree with the setting.

        Its WIDTH is not recorded while the song is choosing it. That slot
        holds the width the user picked for the strip, which is what
        turning the fit off gives back, and overwriting it with a fitted
        one would let it drift a song at a time towards whatever the
        longest line happened to be. The position is still recorded: the
        song has an opinion about the width and none at all about where
        the window is.
        """
        home = self._home_pos()
        self._shapes.remember(
            compact,
            x=home.x(),
            y=home.y(),
            width=self.width(),
            height=None if compact else self.height(),
            keep_width=compact and self._fitting,
        )

    def _strip_height(self) -> int:
        """The height of a strip at the type size in force. Not a floor
        that something taller may sit above: a strip is one row of type,
        and how tall that row is, is the whole of the question.

        From `_type_scale`, not from `_scale`: the second is what has been
        applied and lags a layout change by exactly the moment this is
        asked in.
        """
        return min_window_height(
            self._type_scale(), sync_bar=self._syncing, compact=True
        )

    def _height_for(self, compact: bool) -> int:
        """The height to give this layout back.

        The strip's is derived. The full layout's is what it was last left
        at, never below its own floor; a full layout that has never been
        worn opens at the height a first run opens at.

        The floor is taken at the scale the layout is about to be at, which
        for the full layout is the width it is about to take — never at the
        scale the window is leaving.
        """
        if compact:
            return self._strip_height()
        floor = min_window_height(
            self._type_scale_at(self._width_for(False), False), compact=False
        )
        remembered = self._shapes.recall(False).height
        if not remembered:
            return max(_DEFAULT_HEIGHT, floor)
        return max(floor, remembered)

    def _width_for(self, compact: bool) -> int:
        """The width to give this layout back.

        The user's own, in both layouts: the fitted width is not stored
        here and never becomes the other layout's problem, which is the
        whole of "the full layout is unaffected". A layout that has never
        been worn keeps whatever the window is at.
        """
        remembered = self._shapes.recall(compact).width
        return max(_MIN_WIDTH, remembered or self.width())

    def _set_compact_text_size(self, size_px: int) -> None:
        """Set how big the sung line is in the strip.

        The one control the strip's shape follows from. The type stops
        following the width here, so this is what says how big the text is;
        the width then says how much of a line is on screen, and the height
        is neither's to choose.

        Nothing happens outside the compact layout, and that is the layers
        rule rather than an optimisation: the full layout's type size is
        its width, and a setting that quietly re-shaped it would be this
        layer leaking into the plain window.
        """
        if size_px == self._compact_text_size:
            return
        self._compact_text_size = size_px
        self._settings.setValue("window/compact_text_size", size_px)
        if self._compact_applied:
            self._apply_compact_type()
        self._refresh_menu()
        logger.info("compact text size %dpt", size_px)

    def _apply_compact_type(self, animate: bool = True) -> None:
        """Everything that follows the strip's own type size.

        In this order, and the order is the whole of it: the scale first,
        because the gutters the elision measures against and the height the
        window is about to take both come off it; then the shape, which is
        the song's width at the new size and the height that size asks for;
        then the lines, laid out again for both.

        Not resizeEvent's job, because the resize may be a no-op — the same
        width at a larger size is a strip that has to re-elide and has not
        changed shape.

        ONE resize, whether or not the song has a width to ask for. The
        height follows the size in every case, including the ones the fit
        declines: no lyrics yet, the window away at the menu bar, or the
        feature simply off. A fit that may or may not have moved the window
        plus a correction afterwards would be two travels for one change.
        """
        self._apply_scale()
        target = self._fitted_width()
        self._resize_width_to(self.width() if target is None else target, animate)
        self._relayout()

    # -- sizing the strip to the song --------------------------------------

    @property
    def _fitting(self) -> bool:
        """Whether the window's width is the song's to decide right now.

        Only in the compact layout, ever. The full layout's width is the
        user's and stays theirs, which is why this can default ON without
        breaking the layers rule: it is reachable only from inside a layout
        that is itself opt-in and default off, so the plain synced-lyrics
        window is exactly what it always was.
        """
        return self._fit_to_song and self._compact_applied

    def _widest_line(self) -> float:
        """How wide this song's widest line is, at the current type scale.

        The whole song, once, rather than the line on screen: a window that
        re-sized itself line by line would be a window twitching its way
        through a verse, and the point is a strip that is as small as it
        can be WITHOUT MOVING while the song plays.

        The romanisation is measured too when that layer is on, because it
        is a row the strip shows and a long romanised line can be the
        widest thing in the song. Measured in its own type, which is
        smaller than the sung line's, so it usually is not.

        Not measured: the title card, which is the song's name in the sung
        row for two seconds. Sizing a whole song to two seconds of it is
        the outlier problem the cap exists for, one level up. Nor the loop
        marker, which prefixes one line at a time while a loop is engaged
        and can push that one line into eliding two characters early.
        """
        timeline = self._view_model.timeline()
        if timeline is None:
            return 0.0
        lines, _ = timeline
        self._current.ensurePolished()
        sung = QFontMetricsF(self._current.font())
        widest = max(
            (sung.horizontalAdvance(text) for _, text in lines), default=0.0
        )
        if self._view_model.romanisation_enabled:
            self._pron.ensurePolished()
            pron = QFontMetricsF(self._pron.font())
            widest = max(
                widest,
                max(
                    (
                        pron.horizontalAdvance(
                            self._view_model.pronunciation_for(text)
                        )
                        for _, text in lines
                    ),
                    default=0.0,
                ),
            )
        return widest

    def _fitted_width(self) -> Optional[int]:
        """The width this song asks for, or None when the width is not the
        song's to choose right now.

        Separated from acting on it because two callers want the answer and
        only one of them wants "then leave the window alone". A type-size
        change has to resize the window whether or not there is a song to
        measure, since the HEIGHT follows the size either way; asking this
        first is what lets it use one resize for both cases instead of a
        fit that may or may not have happened and a correction after it.
        """
        if not self._fitting:
            return None
        if not self._lyrics_visible or self._flight_home is not None:
            # Away at the menu bar, or on the way there. Nobody can see the
            # width, and mid-flight the journey is holding the window's real
            # geometry and would hand the old one back on landing. An album
            # played hidden costs nothing, and the show puts the width
            # right before it takes off.
            return None
        widest = self._widest_line()
        if widest <= 0:
            return None  # nothing to fit to: the width stays where it was
        _, available = self._screen_rects()
        target = fitted_window_width(
            widest, self._scale, _MIN_WIDTH, width_cap(available[2])
        )
        logger.debug(
            "fit: widest line %.0fpt at %dpt type, %d -> %d wide",
            widest,
            self._compact_text_size,
            self.width(),
            target,
        )
        return target

    def _fit_width(self, animate: bool = True) -> None:
        """Size the strip to the song, if that is this window's business.

        Called when a song's lyrics arrive and at the few other moments
        that change what would be measured — never on a line change.
        """
        target = self._fitted_width()
        if target is not None:
            self._resize_width_to(target, animate)

    def _resize_width_to(self, target: int, animate: bool = True) -> None:
        """Take the window to this width, keeping it where it looks like it
        already is. The one path there, so the menu switching the feature
        off travels the same way a new song does.

        The HEIGHT travels with it in the compact layout, because a strip's
        height is its type size's and a type size change is one of the
        things that comes through here. One rect, so the window arrives at
        its new shape once rather than in two steps that would each be a
        movement to watch.

        Measured from where the window BELONGS rather than from where it
        is, so the song and a window standing aside from the pointer are
        not two authorities on its position. Everything the anchoring rule
        cares about is a property of the home rectangle: whether it is
        docked, and where its centre is. Anchoring on a dodged one would
        centre the new width on a temporary position and then hand that
        back as the permanent one.
        """
        height = self._strip_height() if self._compact_applied else self.height()
        home = self._home_rect()
        if target == home.width() and height == home.height():
            return
        screen, available = self._screen_rects()
        x, y = resized_position(
            (home.x(), home.y(), home.width(), home.height()),
            target,
            screen,
            available,
            self._top_inset(),
        )
        if self._proximity_home is not None:
            # The home moved under a window that is standing aside, so
            # where it stands aside TO is worked out again from the new
            # one. Same rule as a remembered-position move: one owner of
            # the real position, one derivation of the temporary one.
            self._proximity_home = QPoint(x, y)
            moved = self._dodge_target(QRect(x, y, target, height))
            if moved is not None:
                x, y = moved.x(), moved.y()
        self._travel_to(QRect(x, y, target, height), animate)

    def _travel_to(self, rect: QRect, animate: bool = True) -> None:
        """Take the window to a new shape, gently.

        One animation over the whole geometry rather than a move and a
        resize handed to each other: the position is a function of the
        width here, and two animations would have to agree about it frame
        by frame. The same duration and curve as the travel to a remembered
        position, and the same argument for InOutSine: one continuous
        movement, eased at both ends.
        """
        self._stop_fit()
        # Both write the window's position, so the last one asked wins,
        # which is the rule _stop_move already states for two of these.
        self._stop_move()
        if not animate or self._display_options.reduce_motion:
            # Reduce Motion asks for the size without the travel. There is
            # nothing here but travel, so it arrives instantly and the
            # window is the same shape either way.
            self.setGeometry(rect)
            self._relayout()
            return
        animation = QPropertyAnimation(self, b"geometry", self)
        animation.setDuration(_MOVE_MS)
        animation.setEasingCurve(_MOVE_CURVE)
        animation.setStartValue(self.geometry())
        animation.setEndValue(rect)
        animation.finished.connect(self._end_fit)
        animation.start()
        self._fit_anim = animation

    def _end_fit(self) -> None:
        self._fit_anim = None
        self._relayout()

    def _stop_fit(self) -> None:
        """Abandon a resize in flight, leaving the window wherever it got
        to. A second song arriving mid-travel retargets from there rather
        than from where the last one was heading."""
        if self._fit_anim is not None:
            self._fit_anim.stop()
            self._fit_anim = None

    def _finish_fit(self) -> None:
        """Land a resize in flight at once, instead of abandoning it half
        way.

        For everything that is about to take the window's position over
        for its own reasons: the travel to a remembered position, the
        flight to the menu bar, the save at shutdown. The width it was
        heading for is still the right width, and a window left at a
        waypoint would stay there, because nothing would ask again.
        """
        if self._fit_anim is None:
            return
        end = self._fit_anim.endValue()
        self._stop_fit()
        self.setGeometry(end)
        self._relayout()

    def _set_fit_to_song(self, enabled: bool) -> None:
        """Turn the sizing on or off from the menu."""
        if enabled == self._fit_to_song:
            return
        self._fit_to_song = enabled
        self._settings.setValue("window/fit_to_song", enabled)
        if enabled:
            self._fit_width()
        else:
            # The user's own width back, travelling the same way it left.
            self._resize_width_to(self._width_for(True))
        self._refresh_menu()
        logger.info("fit to song %s", "on" if enabled else "off")

    def _release_fit(self) -> None:
        """A drag on an edge is the user taking the width back.

        Turned off at the START of the resize rather than at the end, so
        the drag behaves exactly as it does with the feature off. It has to
        be the start for a second reason now: the song would otherwise
        re-fit the window out from under a gesture still in progress.
        """
        if not self._fitting:
            return
        self._stop_fit()
        self._fit_to_song = False
        self._settings.setValue("window/fit_to_song", False)
        self._refresh_menu()
        logger.info("fit to song off: the window is being resized by hand")

    def _screen_rects(self) -> tuple:
        """This window's screen and its available area, as plain rects for
        the pure geometry."""
        screen = self.screen() or QApplication.primaryScreen()
        available = self._available_geometry()
        geometry = screen.geometry() if screen else available
        return (
            (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
            (available.x(), available.y(), available.width(), available.height()),
        )

    # -- the overlay controls, and coming out from under the pointer --------

    def _revealable(self) -> tuple:
        """The controls the compact layout puts away. Not the ⓘ, which is
        placed beside the message it explains rather than in a gutter, and
        not the tap row, which only exists in a mode compact steps aside
        for."""
        return (self._loop_button, self._speak_button, self._attempt_button)

    def _offer_control(self, button, offered: bool) -> None:
        """Whether this control has anything to do.

        Not the same question as whether it is on screen, and separating
        the two is what the compact layout needs: a control with nothing to
        do is gone in either layout, and a control with something to do is
        gone in compact until it is reached for.
        """
        self._control_offered[button] = offered
        self._update_reveal()
        self._show_control(button)

    def _show_control(self, button) -> None:
        """Put a control on screen, or take it off.

        Faded out is not enough on its own: a widget at zero opacity is
        still a widget under the pointer, and an invisible thing that can
        be clicked is worse than either state it is between. So the
        reveal reaching zero takes the control off the window entirely.
        """
        offered = self._control_offered.get(button, False)
        button.setVisible(
            offered and (self._reveal > 0.0 or not self._compact_applied)
        )

    def _reveal_target(self) -> float:
        """How much of the overlay controls should be showing.

        All of it, unless the compact layout is in force. There they stay
        out of a window with room for one line, and come back for two
        reasons: the pointer is over the window, or the window is waiting
        for an answer.

        The second is echo practice, and it is the reason the reveal is not
        simply hover. The attempt phase pauses the song and hands the turn
        over; the only way out of it is the done-button, and a prompt
        nobody can see is not a prompt. So the controls are held out for
        as long as the window is asking something, whatever the pointer is
        doing.
        """
        if not self._compact_applied:
            return 1.0
        if self._awaiting_attempt():
            return 1.0
        if self._ghosting:
            # A ghosted window passes clicks through, so its controls are
            # things that can be seen and not pressed. That is the mirror
            # of the rule below about a widget at zero opacity, and it is
            # the same answer: a control that cannot be used is worse on
            # screen than off it. The attempt above outranks this because
            # yielding is suspended for the whole of that phase anyway.
            return 0.0
        if self._hovered:
            return 1.0
        return 0.0

    def _update_reveal(self, animate: bool = True) -> None:
        """Head for wherever the reveal should now be, if that is not
        already where it is heading."""
        target = self._reveal_target()
        if target == self._reveal_to:
            return
        self._reveal_to = target
        self._stop_reveal()
        if not animate or target == self._reveal:
            self._reveal = target
            self._apply_reveal()
            return
        animation = QVariantAnimation(self)
        # Proportional, like the flight's: a reveal interrupted halfway
        # comes back from halfway rather than making the whole journey
        # again. At least a millisecond, or the animation never reports
        # finishing.
        animation.setDuration(max(1, round(_REVEAL_MS * abs(target - self._reveal))))
        animation.setEasingCurve(_REVEAL_CURVE)
        animation.setStartValue(self._reveal)
        animation.setEndValue(target)
        animation.valueChanged.connect(self._on_reveal_step)
        animation.finished.connect(self._end_reveal)
        animation.start()
        self._reveal_anim = animation

    def _on_reveal_step(self, value) -> None:
        self._reveal = float(value)
        self._apply_reveal()

    def _end_reveal(self) -> None:
        self._reveal_anim = None

    def _stop_reveal(self) -> None:
        if self._reveal_anim is not None:
            self._reveal_anim.stop()
            self._reveal_anim = None

    def _apply_reveal(self) -> None:
        """One value, two things: how solid the controls are, and whether
        they are on the window at all."""
        for effect in self._reveal_effects.values():
            effect.setOpacity(self._reveal)
        for button in self._revealable():
            self._show_control(button)

    def _apply_reveal_effects(self) -> None:
        """Install the fade the compact layout's controls ride on, or take
        it away again.

        Taken away rather than left at full opacity, for the reason Reduce
        Transparency gives about the material: an effect that is doing
        nothing is still an effect, on every repaint of every control, and
        "off removes the work" is what this layer promises. Setting a
        widget's effect to None destroys the old one, so the reference goes
        first.
        """
        for button in self._revealable():
            if self._compact_applied:
                if button not in self._reveal_effects:
                    effect = QGraphicsOpacityEffect(button)
                    button.setGraphicsEffect(effect)
                    self._reveal_effects[button] = effect
            elif self._reveal_effects.pop(button, None) is not None:
                button.setGraphicsEffect(None)
        self._stop_reveal()
        self._reveal_to = self._reveal_target()
        self._reveal = self._reveal_to
        self._apply_reveal()

    def _pointer_position(self) -> QPoint:
        """Where the pointer is, in screen coordinates.

        The one place that asks. Everything about the pointer in this
        window is downstream of this call, so there is one thing to
        measure, one thing to stub, and no chance of two layers acting on
        two readings taken a moment apart.
        """
        return QCursor.pos()

    def _pointer_inside(self, point: Optional[QPoint] = None) -> bool:
        """Whether the pointer is over this window, asked of the pointer.

        Not ``underMouse()`` and not an enter/leave event, for two separate
        reasons that both land here. ``underMouse()`` is false for the
        window the moment the pointer is over one of its own children,
        which is exactly when the answer has to be yes. And the events do
        not arrive at all: see _check_pointer.

        ``point`` is passed by the poll, which has already read it and
        must not read it twice.
        """
        return self.frameGeometry().contains(
            self._pointer_position() if point is None else point
        )

    def _set_hovered(self, hovered: bool) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self._update_reveal()

    def _check_pointer(self) -> None:
        """One poll: where is the pointer, and what does that mean here?

        MEASURED, and it corrected the first version of this outright. The
        obvious implementation is enterEvent and leaveEvent, and it works
        perfectly while the app is frontmost, which is how it was written
        and how it passed. Driving the real pointer onto the real window
        with the app backgrounded, which is the only state this app is ever
        in, the window heard nothing: hovered stayed False with the cursor
        provably inside its frame. Qt installs its tracking area
        NSTrackingActiveInActiveApp, and this app runs under the accessory
        activation policy and its window refuses focus, so it is never the
        active app and there are no hover events for it to miss.

        What still answers is the pointer's own position, which is a screen
        coordinate and belongs to nobody. So this asks, on a timer, and the
        timer runs only while something could act on the answer: see
        _update_pointer_watch. One poll costs 0.973us measured, of which
        0.309us is asking macOS where the pointer is.

        The position is read ONCE and handed to both layers. Two readings
        a tick would not only cost twice as much, they could differ — the
        pointer is entitled to move between them — and the compact
        layout's controls and the yield would then be acting on different
        answers to the same question.
        """
        point = self._pointer_position()
        self._set_hovered(self._pointer_inside(point))
        self._observe_pointer(point)

    def _update_pointer_watch(self) -> None:
        """Poll only while a layer that wants the pointer is on and the
        window is on screen.

        Called from each of the things it depends on, so none of them has
        to know about the others. The same shape as the notification
        yield's watch, and the same cheapest half of "poll only as often as
        needed": the difference between polling and not polling at all.

        Two layers want it and either is enough — the compact layout's
        controls, which a full-size window has out anyway, and yielding to
        the pointer, which works in both layouts. A hidden window rules
        out both: nothing is over it and it is in nobody's way.
        """
        wanted = self._lyrics_visible and (
            self._compact_applied or self._proximity_mode != proximity.OFF
        )
        if wanted == self._pointer_timer.isActive():
            return
        if wanted:
            self._pointer_timer.start()
            logger.debug("pointer: watching")
        else:
            self._pointer_timer.stop()
            self._set_hovered(False)
            # Nothing is coming to tell this window the pointer left, so
            # whatever the yield borrowed is handed back here rather than
            # waiting for a poll that will not arrive.
            #
            # Landed rather than travelled, which is the flight's rule
            # about a resize in flight seen once more: the reason the
            # watch stops is almost always that the window is about to
            # leave for the menu bar, and the flight captures the real
            # geometry the instant after this. A return still in the air
            # would be captured as home and handed back as one.
            self._release_proximity(animate=False)
            logger.debug("pointer: stopped watching")

    # -- yielding to the pointer -------------------------------------------

    def _home_pos(self) -> QPoint:
        """Where the window belongs, which is not always where it is.

        A dodge holds the real position for as long as it is standing
        aside, exactly as the flight holds it for as long as the window is
        away. Everything that reads the window's position as a FACT about
        it — what to save at shutdown, what to record for the app in front
        — asks this rather than ``pos()``, so a position the pointer
        caused can never be written down as one the user chose.
        """
        # ``is not None``, never a truth test: PySide gives QPoint a
        # __bool__ and QPoint(0, 0) is FALSE, so a window docked at the
        # top-left of the primary screen would hand back where it is
        # standing as where it belongs.
        if self._proximity_home is None:
            return self.pos()
        return QPoint(self._proximity_home)

    def _home_rect(self) -> QRect:
        """The same, as a rectangle. The size is the live one because a
        dodge never changes it: only the position is borrowed, so there is
        no second answer about how big the window is."""
        return QRect(self._home_pos(), self.size())

    def _proximity_refusal(self) -> Optional[str]:
        """Why the window may not get out of the pointer's way right now."""
        return proximity.refusal(
            mode=self._proximity_mode,
            visible=self._lyrics_visible,
            syncing=self._syncing,
            attempting=self._awaiting_attempt(),
            explaining=self._why_shown,
            dragging=self._drag_offset is not None or bool(self._resize_edges),
            flying=self._flight_anim is not None,
        )

    def _observe_pointer(self, point: QPoint) -> None:
        """What this poll's pointer position means for the yield.

        The region is asked of where the window BELONGS, and the release
        of that and of where it actually is: proximity.still_engaged holds
        the whole of that rule and this only supplies the two rectangles.
        """
        home = self._home_rect()
        frame = self.frameGeometry()
        inside = proximity.still_engaged(
            point=(point.x(), point.y()),
            home=(home.x(), home.y(), home.width(), home.height()),
            current=(frame.x(), frame.y(), frame.width(), frame.height()),
            engaged=self._proximity.engaged,
        )
        outcome = self._proximity.observe(
            inside=inside, refusal=self._proximity_refusal()
        )
        if outcome is proximity.IDLE or outcome is proximity.HELD:
            return
        logger.debug(
            "pointer: %s at %d, %d (%s)",
            outcome,
            point.x(),
            point.y(),
            self._proximity_mode,
        )
        self._apply_proximity()

    def _apply_proximity(self) -> None:
        """Put the window where the observation says it should be.

        Idempotent and asked of the state rather than told by the caller,
        which is the same reason ``is_docked`` is asked of the position:
        one function decides what "the pointer is here" looks like, so a
        mode change, a suspension and an ordinary arrival cannot mean three
        slightly different things.
        """
        active = self._proximity.active
        self._set_dodged(active and self._proximity_mode == proximity.DODGE)
        self._set_ghosting(active and self._proximity_mode == proximity.GHOST)

    def _dodge_target(self, home: QRect) -> Optional[QPoint]:
        """Where a window whose home is ``home`` would step to, now."""
        point = self._pointer_position()
        available = self._available_geometry()
        target = proximity.dodge_destination(
            (home.x(), home.y(), home.width(), home.height()),
            (point.x(), point.y()),
            (
                available.x(),
                available.y(),
                available.width(),
                available.height(),
            ),
        )
        return None if target is None else QPoint(*target)

    def _set_dodged(self, dodged: bool, animate: bool = True) -> None:
        """Step aside, or come back.

        Coming back goes to the position that was borrowed, exactly — not
        to a recomputed one — which is what makes the promise keepable: a
        docked window is docked again afterwards, down to the pixel of
        parity that ``is_docked`` is recognised by.
        """
        if dodged == (self._proximity_home is not None):
            return
        if not dodged:
            home, self._proximity_home = self._proximity_home, None
            logger.debug("dodge: back to %d, %d", home.x(), home.y())
            self._glide_to(home, animate)
            return
        target = self._dodge_target(self.geometry())
        if target is None:
            # Nowhere to go. Said rather than faked: a window shuffled to
            # a position still under the pointer would uncover nothing and
            # look like the feature working.
            logger.info("dodge: no free position to step to, staying put")
            return
        self._proximity_home = self.pos()
        self._glide_to(target, animate)

    def _adopt_dodged_position(self) -> None:
        """The user has taken hold of a window that had stepped aside.

        Wherever they put it is where it belongs now, so the borrowed
        position is simply dropped rather than handed back. Nothing steps
        aside again until the pointer has left and come back, because the
        behaviour is edge triggered and ``stand_down`` keeps the engagement
        it was already in.
        """
        if self._proximity_home is None:
            return
        self._stop_move()
        self._proximity_home = None
        self._proximity.stand_down()
        logger.debug("dodge: the window was taken by hand, adopting where it is")

    def _set_ghosting(self, ghosting: bool, animate: bool = True) -> None:
        """Fade out of the way and let the clicks through, or take it back.

        The click pass-through is switched at the START of the fade rather
        than at the end of it, in both directions. The pointer arriving is
        the request, and a window that stayed clickable for the length of a
        fade would swallow exactly the click the user came to make.

        Retargeted from the level the window actually reached, not from the
        one the last animation was heading for — the yield's rule and the
        flight's, so a pointer that skims the window turns around from
        halfway rather than completing a fade nobody is waiting for.
        """
        if ghosting == self._ghosting:
            return
        self._ghosting = ghosting
        self._apply_click_through(ghosting)
        # The strip's controls are part of what is being handed over: see
        # _reveal_target.
        self._update_reveal()
        end = 1.0 if ghosting else 0.0
        start = self._ghost_level
        self._stop_ghost_animation()
        if start == end or not animate:
            self._ghost_level = end
            self._apply_window_opacity()
            return
        animation = QVariantAnimation(self)
        animation.setDuration(proximity.fade_ms(start, end))
        animation.setEasingCurve(_YIELD_CURVE)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.valueChanged.connect(self._on_ghost_step)
        animation.finished.connect(self._end_ghost)
        animation.start()
        self._ghost_anim = animation
        logger.debug("ghost: level %.2f to %.2f in %dms", start, end, animation.duration())

    def _on_ghost_step(self, value) -> None:
        self._ghost_level = float(value)
        self._apply_window_opacity()

    def _end_ghost(self) -> None:
        """Land on the level exactly, rather than wherever the last frame
        happened to fall."""
        self._ghost_level = 1.0 if self._ghosting else 0.0
        self._ghost_anim = None
        self._apply_window_opacity()

    def _stop_ghost_animation(self) -> None:
        if self._ghost_anim is not None:
            self._ghost_anim.stop()
            self._ghost_anim = None

    def _apply_click_through(self, ignore: bool) -> None:
        """Let a click reach whatever is under this window, or take it
        back.

        Two switches for one state, because they answer to two different
        things. The Qt attribute is what stops this window's own widgets
        from taking a press, and it is the whole of the answer on any
        platform without Cocoa; ``ignoresMouseEvents`` is what makes the
        click land on somebody else's app, which nothing inside Qt can do.
        Setting only the first would give a window that ignores clicks
        without passing them on, which is the worst of both.

        Verified by readback, like every other native state this window
        sets: asking is not the same as it having happened.
        """
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, ignore)
        nswindow = self._nswindow()
        if nswindow is None:
            return
        try:
            nswindow.setIgnoresMouseEvents_(bool(ignore))
            landed = bool(nswindow.ignoresMouseEvents())
        except Exception:
            logger.debug("could not change how the window takes clicks", exc_info=True)
            return
        if landed != bool(ignore):
            logger.warning(
                "click pass-through did not land: asked %s, got %s", ignore, landed
            )

    def _release_proximity(self, animate: bool = True) -> None:
        """Give back everything either behaviour borrowed, at once.

        One method for the whole layer, for the reason the flight has one:
        switching the mode, the window being hidden and shutdown all mean
        the same thing here, and three paths back would be three chances
        to leave the window faint, click-through or standing aside.
        """
        self._proximity.release()
        self._set_dodged(False, animate)
        self._set_ghosting(False, animate)

    def _set_proximity_mode(self, mode: str) -> None:
        """Choose what the window does when the pointer arrives.

        The old mode gives everything back before the new one is written
        down, so switching from Dodge to Ghost cannot leave a window
        standing aside with nothing left that remembers where it came
        from.
        """
        mode = proximity.mode_from(mode)
        if mode == self._proximity_mode:
            return
        self._release_proximity()
        self._proximity_mode = mode
        self._settings.setValue("window/proximity", mode)
        self._update_pointer_watch()
        self._refresh_menu()
        logger.info("yield to the pointer: %s", mode)

    # -- interaction: drag, resize, scroll-opacity, menu -------------------

    def _hit_edges(self, pos: QPoint) -> Qt.Edges:
        """Which edges a press at this point would resize.

        The compact layout offers no vertical ones, and that is the honest
        version of a strip whose height follows its type size: there is
        nothing a top or bottom drag could do, and an edge that showed a
        resize cursor and then refused to resize would be worse than one
        that never claimed to. What those edges do instead is what the
        rest of the window does, which is move it.
        """
        edges = Qt.Edges()
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if self._compact_applied:
            return edges
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _control_rects(self) -> list:
        """Every overlay control, in the window's own coordinates.

        All of them and not only the visible ones: "the control was not on
        the window" is an answer to why a press missed it, and a list that
        had already dropped it could not give that answer.
        """
        named = (
            ("loop", self._loop_button),
            ("speak", self._speak_button),
            ("attempt", self._attempt_button),
            ("tap", self._tap_button),
            ("undo", self._undo_button),
            ("sync_exit", self._sync_exit_button),
            ("why", self._why_button),
            ("retry", self._retry_button),
        )
        rects = []
        for name, button in named:
            top_left = button.mapTo(self, QPoint(0, 0))
            rects.append(
                hit_test.Control(
                    name=name,
                    rect=(top_left.x(), top_left.y(),
                          button.width(), button.height()),
                    visible=button.isVisibleTo(self),
                    enabled=button.isEnabled(),
                )
            )
        return rects

    def _animations_in_flight(self) -> str:
        """Which of the window's animations are running, for the trace.

        Asked of the animation objects rather than of a flag, because a
        flag is a second answer to the question and this exists to settle
        disagreements rather than to add one.
        """
        running = [
            name
            for name, animation in (
                ("fit", self._fit_anim),
                ("move", self._move_anim),
                ("flight", self._flight_anim),
                ("reveal", self._reveal_anim),
                ("line", self._fade_anim),
            )
            if animation is not None
        ]
        return ",".join(running) or "none"

    def _trace_press(self, watched, event) -> None:
        """Everything known about one press, at DEBUG.

        A report that a control is dead is a report about hit testing, and
        hit testing is the one thing a test that calls a slot cannot check
        (Fix 22.1). What settles it is what the press knew at the moment
        it arrived: where it was, what Qt resolved it to, where every
        control actually was in the same coordinate space, and what the
        compositor was doing to the view in between.

        Guarded on the level rather than left to the logging call, because
        this walks seven widgets and reads back a CALayer, and a press is
        not a rare event.
        """
        if not logger.isEnabledFor(logging.DEBUG):
            return
        try:
            global_point = event.globalPosition().toPoint()
            local = self.mapFromGlobal(global_point)
            point = (local.x(), local.y())
            controls = self._control_rects()
            transform = self._layer_transform()
            verdict = hit_test.diagnose(point, controls, transform)
            child = self.childAt(local)
            handle = self.windowHandle()
            logger.debug(
                "press: to=%s global=(%d,%d) window=(%d,%d) childAt=%s "
                "resolved=%s aimed=%s offset=(%.1f,%.1f) %s",
                _widget_name(watched), global_point.x(), global_point.y(),
                point[0], point[1], _widget_name(child),
                verdict.hit, verdict.aimed,
                verdict.offset[0], verdict.offset[1],
                "landed" if verdict.landed else f"MISSED: {verdict.refusal}",
            )
            logger.debug(
                "press:   frame=(%d,%d %dx%d) geometry=(%d,%d %dx%d) dpr=%.2f "
                "layer=(a=%.4f d=%.4f tx=%.2f ty=%.2f) anims=%s",
                self.frameGeometry().x(), self.frameGeometry().y(),
                self.frameGeometry().width(), self.frameGeometry().height(),
                self.geometry().x(), self.geometry().y(),
                self.geometry().width(), self.geometry().height(),
                handle.devicePixelRatio() if handle is not None
                else self.devicePixelRatioF(),
                transform.a, transform.d, transform.tx, transform.ty,
                self._animations_in_flight(),
            )
            logger.debug(
                "press:   compact=%s reveal=%.2f->%.2f dodged=%s ghost=%s "
                "attempt=%s scale=%.3f",
                self._compact_applied, self._reveal, self._reveal_to,
                self._proximity_home is not None, self._ghosting,
                self._awaiting_attempt(), self._scale,
            )
            for control in controls:
                x, y, width, height = control.rect
                logger.debug(
                    "press:   control %-9s window=(%d,%d %dx%d) "
                    "screen=(%d,%d) visible=%s enabled=%s hit=%s",
                    control.name, x, y, width, height,
                    self.mapToGlobal(QPoint(x, y)).x(),
                    self.mapToGlobal(QPoint(x, y)).y(),
                    control.visible, control.enabled, control.contains(point),
                )
        except Exception:  # pragma: no cover - a trace may never break a press
            logger.debug("could not trace the press", exc_info=True)

    def mousePressEvent(self, event) -> None:
        self._trace_press(self, event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._flight_anim is not None:
            # Mid-journey the window is somewhere else and its content is
            # drawn at a fraction of its size, but Qt still hit-tests
            # against the full-size layout: a press here would grab
            # something the user cannot see, at a position about to be
            # given back.
            return
        # A press can only reach a window that stepped aside if the user
        # followed it there, which is the gesture for taking it back.
        self._adopt_dodged_position()
        # A press ON this window IS a pointer on this window, and that is
        # the whole of the test — no region, no second reading of where
        # the pointer is, because the event brought the answer with it.
        #
        # It is here because the strip's controls come out when the POLL
        # notices the pointer, up to _POINTER_POLL_MS after it arrived,
        # and until they do there is nothing under the hand to press.
        # MEASURED, by posting real clicks at the control's own centre
        # with the app backgrounded: a press 0ms or 30ms after the pointer
        # arrived reached the window, one 60ms after it reached the
        # control. A hand that knows where the button is beats the poll.
        #
        # It does not rescue THIS press and does not pretend to: Qt chose
        # the receiver before this ran, and a control that is not on the
        # window is not one a press may be re-aimed at. What it does is
        # start the reveal when the hand arrived rather than when the next
        # poll happens to run. Guarded on the timer because whoever stops
        # the poll also clears the hover, and a hover set with nothing
        # left to clear it would hold the controls out for ever.
        if self._pointer_timer.isActive():
            self._set_hovered(True)
        self._press_global = event.globalPosition().toPoint()
        self._press_geometry = self.geometry()
        self._resize_edges = self._hit_edges(event.position().toPoint())
        if self._resize_edges:
            # The user is taking the width back. Answered before the drag
            # rather than after it, so nothing has to be fought.
            self._release_fit()
        else:
            self._drag_offset = self._press_global - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_hover_cursor(event.position().toPoint())
            return
        if self._resize_edges:
            self._apply_resize(event.globalPosition().toPoint())
        elif self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def _apply_resize(self, global_pos: QPoint) -> None:
        delta = global_pos - self._press_global
        rect = QRect(self._press_geometry)
        if self._resize_edges & Qt.Edge.LeftEdge:
            rect.setLeft(rect.left() + delta.x())
        if self._resize_edges & Qt.Edge.RightEdge:
            rect.setRight(rect.right() + delta.x())
        if self._resize_edges & Qt.Edge.TopEdge:
            rect.setTop(rect.top() + delta.y())
        if self._resize_edges & Qt.Edge.BottomEdge:
            rect.setBottom(rect.bottom() + delta.y())

        maximum = self._available_geometry().size()
        width = max(_MIN_SIZE.width(), min(maximum.width(), rect.width()))
        if self._compact_applied:
            # The strip's height is its type size's, whatever the width
            # does. This is also the drag that used to do nothing visible:
            # with the type following the width, a wider strip had bigger
            # text and showed the same words. It shows more of them now.
            height = self._strip_height()
        else:
            # Height floor depends on the width the resize will land on
            # (fonts scale with width there), so it is computed from the
            # clamped width rather than from where the window is now.
            floor = min_window_height(
                _scale_for(width), sync_bar=self._syncing, compact=False
            )
            height = max(floor, min(maximum.height(), rect.height()))
        # Re-anchor so the edge being dragged is the one that gives.
        if self._resize_edges & Qt.Edge.LeftEdge:
            rect.setLeft(rect.right() - width + 1)
        else:
            rect.setWidth(width)
        if self._resize_edges & Qt.Edge.TopEdge:
            rect.setTop(rect.bottom() - height + 1)
        else:
            rect.setHeight(height)
        self.setGeometry(rect)

    def _update_hover_cursor(self, pos: QPoint) -> None:
        edges = self._hit_edges(pos)
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if horizontal and vertical:
            tl_br = bool(edges & Qt.Edge.LeftEdge) == bool(edges & Qt.Edge.TopEdge)
            self.setCursor(
                Qt.CursorShape.SizeFDiagCursor if tl_br else Qt.CursorShape.SizeBDiagCursor
            )
        elif horizontal:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif vertical:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event) -> None:
        was_interacting = self._drag_offset is not None or bool(self._resize_edges)
        # Whether the window is anywhere other than where it was picked up.
        # A press that missed a control lands here having moved nothing,
        # and that is not somebody placing a window.
        moved = self.geometry() != self._press_geometry
        self._drag_offset = None
        self._resize_edges = Qt.Edges()
        if was_interacting:
            self._nudge_onscreen()
            # The window system may settle the final geometry after this
            # event; re-check once the event loop has caught up.
            QTimer.singleShot(0, self._nudge_onscreen)
            self._save_settings()
            # Learning is implicit and this is the whole of it: the user
            # has just said where they want the window while working in
            # some app. Deferred by the same tick as the nudge, so what is
            # recorded is where the window actually ended up.
            QTimer.singleShot(0, lambda: self._learn_position(moved=moved))

    def _nudge_onscreen(self) -> None:
        target = _clamped_point(self.frameGeometry(), self._available_geometry())
        if target != self.frameGeometry().topLeft():
            self.move(target)

    # -- per-app position memory -------------------------------------------

    def _set_remember_position(self, enabled: bool) -> None:
        """Turn the layer on or off.

        Off stops the observing as well as the moving: the notification
        subscription is removed, so with the layer off this app is not
        watching what the user does at all. That is the layers principle
        taken literally — off has to equal the app before the feature
        existed, not the app quietly still listening.

        Nothing moves on being switched on. The window is already where
        the user last put it, and jumping the moment a menu item is ticked
        would be the feature's first impression being a surprise.
        """
        self._remember_position = enabled
        self._settings.setValue("window/remember_position", enabled)
        if enabled:
            self._read_frontmost()
            started = self._watcher.start()
            logger.info(
                "per-app positions on: frontmost=%s watching=%s remembered=%d own=%s",
                self._frontmost,
                started,
                len(self._positions),
                self._own_bundle_id,
            )
        else:
            logger.info("per-app positions off: no longer watching activations")
            self._watcher.stop()
            self._debounce.cancel()
            self._settle_timer.stop()
            self._stop_move()
        self._refresh_menu()

    def _read_frontmost(self) -> None:
        """Ask macOS which app is in front, refusing ourselves.

        The same rule as the activation handler, at the other door. Asking
        while SottoVoce happens to be frontmost — the layer switched on from
        a menu opened over our own window — would seed the frontmost app as
        us, and the first drag would be refused for a reason the user could
        not act on. Unknown is the honest answer: unknown until the next
        activation says otherwise, which the readout shows as much.
        """
        identity = frontmost.current_app()
        ours = self._own_bundle_id
        if identity is None or (ours is not None and identity.bundle_id == ours):
            if identity is not None:
                logger.debug("frontmost app is us, treating it as unknown for now")
            self._frontmost, self._frontmost_name = None, None
            return
        self._frontmost, self._frontmost_name = identity.bundle_id, identity.name

    def _forget_positions(self) -> None:
        """Throw the whole map away. No confirmation: every entry costs one
        drag to relearn, which is the difference between this and a
        hand-made sync."""
        self._positions.forget_all()
        self._settings.setValue("window/app_positions", self._positions.to_json())
        logger.info("forgot every remembered window position")
        self._refresh_menu()

    def _rebuild_positions_menu(self) -> None:
        """Fill the submenu with what is remembered, most recent first.

        Rebuilt on opening rather than kept in step, because the entries
        ARE the map and the map changes without the menu being involved.

        Every row is a READOUT — icon, name, and nothing to click. Clicking
        one used to forget it, and that control was removed rather than
        kept: re-dragging the window in an app overwrites its position, so
        forgetting a single app can only ever mean "stop moving the window
        for this one", which is not a thing anybody wants for one app while
        wanting it for the others. Forget-all stays, because "stop doing
        this" is a real wish and that is where it belongs.

        What a row IS is nsmenu.py's business: it is an NSMenuItem carrying
        a view, because a disabled item is drawn grey and four grey rows
        read as four things that are unavailable rather than as four facts.
        This end hands over a label and some icon bytes and knows nothing
        about either.
        """
        listed = self._positions.listed()
        self._menu.set_rows(
            POSITION_LIST,
            tuple(
                Row(display_label(bundle_id, name), self._app_icon(bundle_id))
                for bundle_id, name in listed
            ),
        )
        logger.debug("remembered-apps menu rebuilt with %d entries", len(listed))

    def _app_icon(self, bundle_id: Optional[str]) -> Optional[bytes]:
        """The app's icon as TIFF bytes, or None where there is not one.

        Two calls deep into AppKit, so it is never asked for on a path that
        runs often: the submenu asks on opening, and the readout asks only
        when the app in front changes. Bytes rather than an image, because
        nothing pyobjc-shaped crosses out of frontmost.py and nothing
        Qt-shaped goes into the menu.
        """
        if not bundle_id:
            return None
        return frontmost.app_icon_tiff(bundle_id, _MENU_ICON_POINTS) or None

    def _on_app_activated(self, identity) -> None:
        """An app came to the front. Runs on the main thread — NSWorkspace
        posts there and the block is delivered on the posting thread — so
        this is an ordinary UI call, same as the hotkey's.

        Nothing happens yet: the arrival has to settle first, or a Cmd-Tab
        sweep would be six separate instructions to move.

        Our own activation is noted and then dropped rather than becoming
        the frontmost app. Opening the menu bar item can bring an accessory
        app forward, and taking that at face value would replace the app
        the user is actually working in with ourselves — after which a drag
        would be refused by the self-filter in ``learn_refusal`` and the
        user would see nothing being learned for no visible reason. What
        the window follows is the last app that was not us.
        """
        bundle_id = identity.bundle_id
        if not self._remember_position:
            logger.debug("activation: %s ignored, the layer is off", bundle_id)
            return
        if self._own_bundle_id is not None and bundle_id == self._own_bundle_id:
            logger.debug(
                "activation: %s is us, keeping %s as the frontmost app",
                bundle_id,
                self._frontmost,
            )
            return
        self._frontmost, self._frontmost_name = bundle_id, identity.name
        outcome = self._debounce.observe(bundle_id, time.monotonic())
        logger.debug("activation: %s (%s) [%s]", bundle_id, identity.name, outcome)
        self._settle_timer.start(int(SETTLE_SECONDS * 1000))

    def _apply_settled_app(self) -> None:
        """The settling interval elapsed — move, if there is somewhere to
        move to and nothing in the way."""
        now = time.monotonic()
        bundle_id = self._debounce.settled(now)
        if bundle_id is None:
            # The timer and the debounce time the same interval off two
            # different clocks, and QTimer is entitled to fire a hair
            # early — measured at 390ms against a 400ms rule, live. Since
            # the timer is single-shot, treating that as "no arrival"
            # dropped the move for good rather than by a millisecond. The
            # rule stays authoritative; the timer just asks again.
            remaining = self._debounce.remaining(now)
            if remaining > 0:
                logger.debug(
                    "settling: %s has %.0fms left, asking again",
                    self._debounce.pending,
                    remaining * 1000,
                )
                self._settle_timer.start(max(1, int(remaining * 1000) + 1))
            return
        refusal = move_refusal(
            enabled=self._remember_position,
            visible=self._lyrics_visible,
            dragging=self._drag_offset is not None or bool(self._resize_edges),
            syncing=self._syncing,
            flying=self._flight_anim is not None,
        )
        if refusal is not None:
            logger.debug("settled: %s, not moving: %s", bundle_id, refusal)
            return
        remembered = self._positions.recall(bundle_id)
        if remembered is None:
            # Never learned here: leave the window exactly where it is.
            logger.debug(
                "settled: %s has no position remembered, leaving the window", bundle_id
            )
            return
        logger.debug("settled: %s, remembered at %d, %d", bundle_id, *remembered)
        self._move_to(QPoint(*remembered))

    def _move_to(self, target: QPoint) -> None:
        """Travel to a remembered position.

        A window that has stepped aside from the pointer is moved by
        changing where it will step BACK to, not by moving it — the same
        rule docking already follows for a window away at the menu bar,
        and for the same reason: the yield is holding the real position
        and would hand the old one straight back the moment the pointer
        left. Where it stands aside to is then derived again from the new
        home, or the two would disagree about where "back" is.
        """
        if self._proximity_home is not None:
            clamped = _clamped_point(
                QRect(target, self.size()), self._available_geometry()
            )
            self._proximity_home = clamped
            moved = self._dodge_target(QRect(clamped, self.size()))
            logger.debug(
                "move: %d, %d is the new home while standing aside", *clamped.toTuple()
            )
            self._glide_to(moved if moved is not None else clamped)
            return
        self._glide_to(target)

    def _glide_to(self, target: QPoint, animate: bool = True) -> None:
        """Take the window to a position, clamped and animated.

        Clamped on arrival rather than only when learned: the position may
        have been recorded on a second display that is no longer attached,
        or before the dock moved, and a remembered position is not a
        licence to put the window somewhere it cannot be reached. A dodge
        comes back through here too, and the clamp is a no-op for it — the
        position it is returning to was on screen when it was borrowed.
        """
        clamped = _clamped_point(
            QRect(target, self.size()), self._available_geometry()
        )
        if clamped != target:
            logger.debug(
                "move: %d, %d is off this screen, clamped to %d, %d",
                target.x(),
                target.y(),
                clamped.x(),
                clamped.y(),
            )
        if clamped == self.pos():
            logger.debug("move: already at %d, %d", clamped.x(), clamped.y())
            return
        # A resize in flight is writing this window's position too. Landed
        # rather than abandoned: the width it was heading for is right.
        self._finish_fit()
        logger.debug(
            "move: %d, %d → %d, %d",
            self.pos().x(),
            self.pos().y(),
            clamped.x(),
            clamped.y(),
        )
        self._stop_move()
        if not animate or self._display_options.reduce_motion:
            # The window still goes where it was asked to go — both layers
            # that come through here are about where it ends up rather than
            # about how it gets there, so Reduce Motion takes the travel
            # and leaves the answer. ``animate`` is the other case: a
            # shutdown or a mode change, where there is nobody left to
            # watch a journey.
            self.move(clamped)
            logger.debug("moved without travelling (animate=%s)", animate)
            return
        animation = QPropertyAnimation(self, b"pos", self)
        animation.setDuration(_MOVE_MS)
        animation.setEasingCurve(_MOVE_CURVE)
        animation.setStartValue(self.pos())
        animation.setEndValue(clamped)
        animation.start()
        self._move_anim = animation

    def _dock_to_top(self) -> None:
        """Put the window under the menu bar, centred on its own screen.

        An explicit command and nothing more: nothing snaps here, no edge
        is magnetic, and the window is as draggable the instant after this
        as the instant before. It exists because "centred under the menu
        bar" is a position that is tedious to hit by hand and obvious to
        want, especially with the compact layout on.

        Where exactly is ``geometry.docked_position``'s business; what this adds
        is the two things only a running window can answer — which screen
        it is on, and how far down that screen the notch reaches.

        A window that is away at the menu bar is moved by changing where
        the flight will put it back, not by moving it: the flight is
        holding the real position and would hand the old one straight back
        at the end of the journey.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            logger.debug("dock: no screen to dock to")
            return
        geometry = screen.geometry()
        available = screen.availableGeometry()
        inset = self._top_inset()
        x, y = docked_position(
            self.width(),
            (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
            (available.x(), available.y(), available.width(), available.height()),
            inset,
        )
        logger.info(
            "dock: %d, %d on %s (available top %d, safe area top %d)",
            x,
            y,
            screen.name(),
            available.y(),
            inset,
        )
        if self._flight_home is not None:
            self._flight_home = (x, y, self._flight_home[2], self._flight_home[3])
        else:
            self._move_to(QPoint(x, y))
        # Written from the target rather than from the window: the travel
        # takes a phase length and neither of these may record a waypoint.
        self._settings.setValue("window/pos", QPoint(x, y))
        self._learn_position(QPoint(x, y))

    def _top_inset(self) -> int:
        """How far down the screen the notch reaches, in points.

        Asked of the screen's own safe area rather than worked out from a
        menu bar height, because the two come apart exactly where it
        matters: a Mac set to hide its menu bar automatically gives the
        whole screen back as available space and leaves the notch where it
        was. Where the menu bar IS showing this is the smaller of the two
        answers and changes nothing, which is why it is a floor and not a
        replacement.

        Zero everywhere the question cannot be asked — off cocoa, without
        pyobjc, on a macOS with no safe areas, or with the window not on a
        screen at all — which is also every case where a Mac has no notch
        for it to describe.
        """
        nswindow = self._nswindow()
        if nswindow is None:
            return 0
        try:
            screen = nswindow.screen()
            if screen is None:
                return 0
            return int(screen.safeAreaInsets().top)
        except Exception:
            logger.debug("no safe area for this screen", exc_info=True)
            return 0

    def _stop_move(self) -> None:
        """Abandon a move in flight, leaving the window wherever it got to.

        A second activation mid-travel retargets from there rather than
        from where the last one was heading — the same rule the album
        tint's cross-fade follows, and for the same reason: the user is
        looking at where it is, not at where it was going.
        """
        if self._move_anim is not None:
            self._move_anim.stop()
            self._move_anim = None

    def _learn_position(
        self, position: Optional[QPoint] = None, moved: bool = True
    ) -> None:
        """Record where the window now sits for whichever app is in front.

        Called when a drag or resize ends, and when the window is docked to
        the top: those are the moments the user has expressed a preference
        about where the window goes. The app in front is not this one: the
        window never takes focus and the app is an accessory, so dragging
        it leaves the frontmost app exactly as it was.

        ``position`` is passed by the callers that already know where the
        window is going and would otherwise be asking mid-travel. A drag
        has already landed and passes nothing, and what it gets back is
        where the window BELONGS: a position the pointer caused is not a
        preference the user expressed, and recording one would teach the
        map that this app's own dodging is where the window lives.
        """
        refusal = learn_refusal(
            enabled=self._remember_position,
            frontmost=self._frontmost,
            own_bundle_id=self._own_bundle_id,
            moved=moved,
        )
        if refusal is not None:
            logger.debug("learn: nothing recorded, %s", refusal)
            return
        position = self._home_pos() if position is None else position
        self._positions.remember(
            self._frontmost, position.x(), position.y(), self._frontmost_name
        )
        self._settings.setValue("window/app_positions", self._positions.to_json())
        logger.debug(
            "learn: %d, %d recorded for %s (%s) (%d apps remembered)",
            position.x(),
            position.y(),
            self._frontmost,
            self._frontmost_name,
            len(self._positions),
        )
        self._acknowledge_learned()
        self._refresh_menu()  # the forget entry appears with the first entry

    # -- the acknowledgement ----------------------------------------------

    def _acknowledge_learned(self) -> None:
        """Say, on the window, that a position was just recorded.

        The gesture is a drag and it ends in silence, which is this
        feature's oldest problem restated: nothing on screen distinguishes
        a drag that was learned from a drag that was not. So the hairline
        warms for half a second and goes back.

        One per gesture. A second glow starting inside the first would
        read as a flicker rather than as two answers, and it is also what
        makes a release delivered twice harmless.
        """
        now = time.monotonic()
        if not may_acknowledge(now=now, last=self._glow_at):
            logger.debug("acknowledgement: still glowing, not restarting")
            return
        self._glow_at = now
        if self._glow_anim is not None:
            self._glow_anim.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(int(GLOW_SECONDS * 1000))
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.valueChanged.connect(self._on_glow_step)
        animation.finished.connect(self._end_glow)
        animation.start()
        self._glow_anim = animation
        logger.debug("acknowledgement: hairline glow for %.0fms", GLOW_SECONDS * 1000)

    def _on_glow_step(self, value) -> None:
        """The phase runs 0 to 1; the intensity rises and falls within it.

        One property with the whole shape in it, rather than two animations
        handing over — the same reasoning as the line change's signed
        ``progress``, and it is what guarantees the edge starts and ends at
        exactly the tint's own colour.
        """
        self._glow = glow_intensity(float(value))
        self.update()

    def _end_glow(self) -> None:
        """Hand the edge back, exactly. The glow is a mix applied at paint
        time and never written into the tint state, so returning it is
        setting one float to zero — there is nothing to restore because
        nothing was taken."""
        self._glow = 0.0
        self._glow_anim = None
        self.update()

    def wheelEvent(self, event) -> None:
        """Wheel over the window chrome (margins, header, synced lines).
        Modifiers ride on the event itself, so Option detection works even
        though the window never takes focus."""
        option = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        plain = self._view_model.display().mode is Mode.PLAIN
        if wheel_action(plain, option) == "opacity":
            step = opacity_step(event.pixelDelta().y(), event.angleDelta().y())
            if step:
                self._set_opacity(self._opacity + step)
        else:
            # Plain mode, wheel outside the scroll area: forward to it.
            bar = self._plain_scroll.verticalScrollBar()
            bar.setValue(
                bar.value() - scroll_step(event.pixelDelta().y(), event.angleDelta().y())
            )

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            # Every press, whoever it reached. The window's own handler
            # cannot answer "did this one land where the user aimed it",
            # because a press that reached the window is precisely the
            # case where it did not reach a control.
            self._trace_press(watched, event)
        if (
            watched is self._plain_scroll.viewport()
            and event.type() == QEvent.Type.Wheel
        ):
            option = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
            if wheel_action(True, option) == "opacity":
                step = opacity_step(event.pixelDelta().y(), event.angleDelta().y())
                if step:
                    self._set_opacity(self._opacity + step)
                return True  # don't let the scroll area also scroll
            return False  # native (kinetic) scrolling handles it
        return super().eventFilter(watched, event)

    # -- settings menu (shared by the menu bar item and right-click) -------

    def _build_menu(self) -> None:
        """Build the one settings menu, once.

        One MODEL, drawn once as a native NSMenu that both the menu bar
        item and the window's right-click use. Until milestone 21 this was
        one QMenu, which was one object and two appearances: Qt converts a
        tray menu into a real NSMenu and draws a popup itself, so the same
        entries looked like two different menus depending on which way you
        opened them. The model is in menu.py, the drawing is in nsmenu.py,
        and this is the wiring between them and the app.

        Its structure never changes afterwards — ``_refresh_menu`` only
        flips visibility, check marks, chosen presets and two labels —
        which keeps the native menu bar item from being rebuilt underneath
        the user.

        Nothing here checks or unchecks an entry, and that rule survived
        the move: the handler changes the app's state and the refresh that
        follows says what the state now is. What holds it up is that the
        tick has exactly ONE writer — ``NativeMenu.apply``, reading the
        model — and the click path is not it, so ``Menu.trigger`` calls the
        handler with the state the entry is moving to rather than trusting
        a tick that moved itself.
        """
        self._menu = Menu()
        self._menu.on(SHOW_LYRICS, self._set_lyrics_visible)
        self._menu.on(ROMANISATION, self._set_romanisation)
        self._menu.on(SPOKEN, self._set_spoken_reference)
        self._menu.on(SPEECH_RATE, self._set_speech_rate)
        self._menu.on(ECHO, self._set_echo_practice)
        self._menu.on(COMPACT, self._set_compact)
        self._menu.on(COMPACT_SIZE, self._set_compact_text_size)
        self._menu.on(FIT_TO_SONG, self._set_fit_to_song)
        self._menu.on(ALBUM_COLOUR, self._set_album_colour)
        self._menu.on(ALL_DESKTOPS, self._set_all_desktops)
        self._menu.on(MENUBAR_ANIMATION, self._set_menubar_animation)
        self._menu.on(YIELD_NOTIFICATIONS, self._set_yield_to_notifications)
        self._menu.on(PROXIMITY, self._set_proximity_mode)
        # A command rather than a switch: it puts the window somewhere
        # once. Nothing holds it there afterwards, so there is no state for
        # a tick to describe.
        self._menu.on(DOCK_TOP, self._dock_to_top)
        self._menu.on(REMEMBER_POSITION, self._set_remember_position)
        self._menu.on(FORGET_POSITIONS, self._forget_positions)
        self._menu.on(OPEN_AT_LOGIN, self._set_open_at_login)
        self._menu.on(SYNC, self._begin_sync)
        self._menu.on(PASTE_SYNC, self._open_paste_window)
        # Straight to the app's quit, so the existing aboutToQuit shutdown
        # (settings saved, monitor joined) runs however quit is reached.
        self._menu.on(QUIT, QApplication.instance().quit)

        # The two things a refresh on a timer cannot do honestly. Order
        # matters in the first: re-read the system's answer, so the refresh
        # that follows is drawing the state the system is actually in.
        self._menu.on_open(None, self._on_menu_opening)
        # And the one submenu whose CONTENTS are rebuilt rather than only
        # relabelled. Everything else is a fixed set of entries whose
        # visibility changes, because rebuilding a menu the status item
        # owns makes it flicker — but a list of what has been learned
        # cannot be a fixed set, and this one is assembled only while the
        # user is looking at it, never while the menu bar item is idle.
        self._menu.on_open(POSITION_LIST, self._rebuild_positions_menu)

        view = nsmenu.NativeMenu(self._menu)
        if view.build():
            self._menu.attach(view)
        else:
            logger.info("no native menu: AppKit unavailable")

    def _on_menu_opening(self) -> None:
        """Somebody is about to read the menu."""
        self._reread_login_item()
        self._refresh_menu()

    def _refresh_position_readout(self) -> None:
        """The line that says what the layer knows, and the icon beside it.

        The name comes from the activation that brought this app forward,
        falling back to the one the map learned when it was last placed —
        which is what lets an app that has since quit still be named — and
        to the identifier when neither exists.

        ``peek``, not ``recall``: a glance at the menu is not evidence the
        user still switches to that app, and letting it refresh recency
        would make the eviction order describe where they have been
        looking. The icon is fetched only when the app in front changes,
        because this runs on every render and drawing one is two calls into
        AppKit.
        """
        name = self._frontmost_name or self._positions.name_for(self._frontmost)
        self._menu.set_label(
            POSITION_STATUS,
            status_summary(
                count=len(self._positions),
                frontmost=self._frontmost,
                frontmost_name=name,
                placed=self._positions.peek(self._frontmost) is not None,
            ),
        )
        if self._frontmost != self._readout_icon_for:
            self._readout_icon_for = self._frontmost
            self._menu.set_icon(POSITION_STATUS, self._app_icon(self._frontmost))

    def _refresh_menu(self) -> None:
        """Bring the shared menu in line with the current state. Cheap
        enough to run on every render, so the menu bar item is already
        correct before it is opened rather than only on opening."""
        sync_label = self._view_model.sync_menu_entry(
            self._provider.has_user_sync(self._view_model.track_id)
        )
        self._menu.show_only(
            visible_entries(
                has_korean_lyrics=self._view_model.has_korean_lyrics,
                speech_available=self._speech_available,
                synced=self._view_model.display().mode is Mode.SYNCED,
                sync_offered=sync_label is not None,
                paste_sync_offered=self._view_model.paste_sync_offered,
                login_item_offered=login_item.offered(
                    bundled=self._bundled, status=self._login_status
                ),
                positions_remembered=len(self._positions) > 0,
                remembering_positions=self._remember_position,
                compact=self._compact_applied,
            )
        )
        if sync_label is not None:
            self._menu.set_label(SYNC, sync_label)
        self._refresh_position_readout()
        self._menu.set_checked(SHOW_LYRICS, self._lyrics_visible)
        self._menu.set_checked(ROMANISATION, self._view_model.romanisation_enabled)
        self._menu.set_checked(SPOKEN, self._spoken_enabled)
        self._menu.set_checked(ECHO, self._echo_enabled)
        # What the user asked for, not what a sync pass is borrowing: the
        # tick describes the setting, and the pass gives the layout back.
        self._menu.set_checked(COMPACT, self._compact)
        self._menu.set_checked(FIT_TO_SONG, self._fit_to_song)
        self._menu.set_checked(ALBUM_COLOUR, self._album_colour)
        self._menu.set_checked(ALL_DESKTOPS, self._all_desktops)
        self._menu.set_checked(MENUBAR_ANIMATION, self._menubar_animation)
        self._menu.set_checked(YIELD_NOTIFICATIONS, self._yield_to_notifications)
        self._menu.set_checked(REMEMBER_POSITION, self._remember_position)
        # The system's answer, not ours: the tick follows what macOS says,
        # so flipping it in System Settings shows up here rather than the
        # two quietly disagreeing.
        self._menu.set_checked(
            OPEN_AT_LOGIN, login_item.is_enabled(self._login_status)
        )
        self._menu.set_label(OPEN_AT_LOGIN, login_item.label_for(self._login_status))
        self._menu.set_chosen(SPEECH_RATE, self._speech_rate)
        self._menu.set_chosen(COMPACT_SIZE, self._compact_text_size)
        self._menu.set_chosen(PROXIMITY, self._proximity_mode)
        self._menu.sync()
        self._refresh_tray_icon()

    def _build_tray(self) -> None:
        """The menu bar item. Ours rather than Qt's, and that is what buys
        the native menu: Qt owns the NSStatusItem it makes, so the item
        that carries one NSMenu has to be an item this app made.

        Its glyph is drawn rather than loaded, and it is a template image,
        so macOS tints it for light and dark menu bars instead of us
        shipping two of each.
        """
        self._tray = nsmenu.StatusItem()
        if not self._tray.create("SottoVoce"):
            logger.info("no menu bar: the item is unavailable")
            self._tray = None
            return
        self._tray_state = self._tray_spec_now()
        self._tray.set_image(
            self._tray_png_for(self._tray_state), menubar.GLYPH_UNITS
        )
        if self._menu.view is not None:
            self._tray.set_menu(self._menu.view)

    def _tray_png_for(self, spec) -> bytes:
        """One drawing per combination, kept.

        Brightness, shape and dot compose into eight states, and the optional
        animation multiplies the shape by four — so drawing on demand and
        caching by spec is what keeps a line change to a dictionary lookup
        after the first time it is seen. Each glyph is painted once for the
        life of the process.
        """
        png = self._tray_pngs.get(spec)
        if png is None:
            png = symbols.menubar_png(spec, menubar.GLYPH_UNITS)
            self._tray_pngs[spec] = png
            logger.debug("menu bar glyph drawn: %r", spec)
        return png

    def _tray_spec_now(self):
        """What the item should be showing, from the app's current state."""
        return menubar.icon_spec(
            playing=self._last_state is PlaybackState.PLAYING,
            lyrics_visible=self._lyrics_visible,
            practising=self._loop.engaged or self._syncing,
            animated=self._menubar_animation,
            line_changes=self._menubar_step,
        )

    def _refresh_tray_icon(self) -> None:
        """Bring the menu bar glyph in line with what the app is doing.

        Driven by the MONITOR TICK, not by the menu opening. Until 15.1 the
        only reliable caller was ``aboutToShow``: the icon was refreshed from
        ``_render``, and a pause does not re-render, because
        ``player_state_changed`` returns False for PAUSED — the display text
        is unchanged, so there is nothing to draw. The icon therefore sat on
        "playing" until somebody opened the menu. It is now refreshed from
        every position update and every state change as well.

        ``_refresh_menu`` still calls it, and that is not redundant: position
        updates stop arriving the moment there is no track at all, so with
        Spotify closed the tick is gone and hiding the window would have
        nothing to dim the icon. The tick is the guarantee; the other callers
        are promptness.

        Set only when the spec CHANGES. This now runs three times a second,
        and handing the same image back to an NSStatusItem that often is the
        menu bar item being rebuilt under the user — the flicker the shared
        menu is built once to avoid.
        """
        if self._tray is None:
            return
        spec = self._tray_spec_now()
        if spec == self._tray_state:
            return
        self._tray_state = spec
        self._tray.set_image(self._tray_png_for(spec), menubar.GLYPH_UNITS)
        logger.debug("menu bar glyph: %r", spec)

    def _advance_menubar_step(self, index: int) -> None:
        """A different synced line is on screen: step the arrangement.

        Called from the one place a synced line lands on the window, and only
        when the index actually differs — ``_render`` re-runs ``_set_lines``
        for reasons that have nothing to do with the song (a menu refresh, a
        resize), and those are not line changes.

        The step is counted whether or not the animation is on, so switching
        the layer on mid-song picks up from wherever the song is rather than
        restarting a cycle. Refreshing the icon here is what makes the
        arrangement change ON the line change rather than up to a poll later.
        """
        if index == self._displayed_index:
            return
        self._menubar_step += 1
        if self._menubar_animation:
            self._refresh_tray_icon()

    def _set_menubar_animation(self, enabled: bool) -> None:
        """Turn the arrangement stepping on or off.

        Off is not merely "stop moving": the shape goes back to the plain
        short / long / short, so off equals the app before this existed.
        """
        self._menubar_animation = enabled
        self._settings.setValue("window/menubar_animation", enabled)
        logger.info("menu bar animation %s", "on" if enabled else "off")
        self._refresh_tray_icon()
        self._refresh_menu()

    def _build_hotkey(self) -> None:
        """Claim the global show/hide combination.

        Deliberately not shown on the menu entry it duplicates. A QAction
        with a shortcut is a second thing that can fire it — Qt would own
        one path and Carbon the other, and two mechanisms driving one
        action is the drift this app keeps designing away. Worse, the
        claim would not always be true: registration can be refused, and
        macOS may hand a press to another app holding the same keys, so a
        menu printing ⇧⌘J beside "Show lyrics" would be the login-item
        failure again — an entry promising something that does not
        happen. So the label stays as it was, the README says what the
        keys are, and the log says whether they landed.
        """
        self._hotkey = hotkey.GlobalHotkey(
            hotkey.TOGGLE_LYRICS, self._toggle_lyrics_visible
        )
        if not self._hotkey.register():
            # Already logged with the reason. Nothing here depends on it:
            # the menu bar item does the same job and always has.
            logger.info("continuing without the global hotkey")

    def contextMenuEvent(self, event) -> None:
        """The second way into the one menu, and the reason it is native.

        The point makes one trip across the coordinate line: Qt measures
        down from the top left of the primary screen and AppKit measures up
        from its bottom left. Modal while the menu is up, exactly as
        ``QMenu.exec`` was, and it does not activate the app.
        """
        self._on_menu_opening()
        primary = QApplication.primaryScreen()
        if primary is None:
            return
        point = event.globalPos()
        x, y = cocoa_point_from_qt(
            point.x(), point.y(), primary.geometry().height()
        )
        self._menu.popup(x, y)

    # -- settings -----------------------------------------------------------

    def _set_lyrics_visible(self, visible: bool) -> None:
        """Show or hide the lyrics window. Nothing else stops: the monitor
        thread keeps polling, an engaged loop stays engaged, and a sync pass
        in progress carries on — so showing it again picks the song up
        wherever it now is.

        The window travels to and from the menu bar item rather than
        blinking out: see ``_begin_flight``. The setting is written from
        the logical state, not from whether the window happens to be on
        screen mid-flight.
        """
        self._lyrics_visible = visible
        self._settings.setValue("window/visible", visible)
        if visible:
            self._render()  # catch up with whatever happened while hidden
            # Before the flight, which captures the window's real geometry
            # and hands it back on landing: a song may have changed while
            # the strip was away, and the width has to be right before the
            # journey rather than after it.
            self._fit_width(animate=False)
        self._refresh_menu()
        # Hiding gives the opacity back before the flight borrows it: a
        # window that goes away faded would come back faded, because the
        # yield is a level and not something the flight restores. Showing
        # starts looking again, and the first poll settles it.
        if not visible:
            self._stop_yield()
        self._update_yield_watch()
        # A window at the menu bar has no pointer over it, and the reveal
        # goes back with the watch so it does not come back revealed.
        self._update_pointer_watch()
        self._begin_flight(visible)

    # -- leaving for the menu bar, and coming back -------------------------

    def _menubar_item_rect(self) -> Optional[tuple]:
        """Where the menu bar item is, or None.

        The status item's own button window, flipped into Qt's
        coordinates. It used to be ``QSystemTrayIcon.geometry()``, and the
        two were measured against each other in the same process while both
        existed: they agree exactly once Cocoa's bottom-left origin is
        taken out (1159,1073 38x34 in Cocoa is 1159,0 38x34 here). With the
        item now ours, this IS the one source of truth for the rectangle
        rather than a second one beside Qt's.
        """
        if self._tray is None:
            return None
        frame = self._tray.frame()
        primary = QApplication.primaryScreen()
        if frame is None or primary is None:
            return None
        rect = qt_rect_from_cocoa(frame, primary.geometry().height())
        if rect[2] <= 0 or rect[3] <= 0:
            return None
        return rect

    def _flight_destination(self) -> Optional[tuple]:
        """The rectangle to fly to, or None for a plain fade in place.

        None is not a failure: the item can be behind the notch, in an
        overflow, or on a display that has just been unplugged, and a
        flight towards a rectangle that is not on any screen would throw
        the window off the edge of the world.
        """
        item = self._menubar_item_rect()
        screens = tuple(
            (
                screen.geometry().x(),
                screen.geometry().y(),
                screen.geometry().width(),
                screen.geometry().height(),
            )
            for screen in QApplication.screens()
        )
        if not flight.item_usable(item, screens):
            logger.debug("no usable menu bar item (%s): fading in place", item)
            return None
        return item

    def _begin_flight(self, showing: bool) -> None:
        """Send the window to the menu bar item, or bring it back.

        Hiding used to be instantaneous, which said nothing about where the
        window had gone; the way back was something to remember rather than
        something you saw. Now it shrinks and fades towards the item, and
        grows back out of it.

        A flight already in progress is not restarted from the beginning:
        the new one picks up the progress the old one had reached and takes
        proportionally less time, so a hotkey pressed twice quickly reverses
        the movement instead of queueing a second one.
        """
        if self._display_options.reduce_motion:
            # No journey at all, rather than a quick one. The flight is
            # movement in every dimension it has — position, scale, opacity
            # — so there is no part of it left to keep once the movement is
            # taken out, and a fade in place would be answering a question
            # nobody asked. Any flight already in the air gives back
            # everything it borrowed first.
            self._stop_flight()
            self.setVisible(showing)
            logger.debug(
                "reduce motion: %s without the flight",
                "showing" if showing else "hiding",
            )
            return
        running = self._flight_anim is not None
        start = self._flight_progress if running else (1.0 if showing else 0.0)
        end = 0.0 if showing else 1.0
        if running:
            self._flight_anim.stop()
            self._flight_anim = None
        if start == end and not running:
            # Already where it should be: setting the same state twice is
            # not a journey. Still make sure the window agrees.
            self.setVisible(showing)
            return

        if not running:
            # A resize in flight would make home a waypoint, and the
            # flight hands home back on landing.
            self._finish_fit()
            # Home is where the window logically lives, captured before
            # anything moves it. Everything the flight touches is restored
            # to this, whether it lands or is interrupted.
            self._flight_home = (
                self.pos().x(),
                self.pos().y(),
                self.width(),
                self.height(),
            )
            self._flight_to = self._flight_destination()
            self._begin_native_flight()
        self._apply_flight(start)
        if showing:
            self.setVisible(True)

        animation = QVariantAnimation(self)
        animation.setDuration(flight.duration_ms(start, end))
        animation.setEasingCurve(_FLIGHT_LEAVING if not showing else _FLIGHT_ARRIVING)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.valueChanged.connect(lambda value: self._apply_flight(float(value)))
        animation.finished.connect(lambda: self._land(showing))
        animation.start()
        self._flight_anim = animation
        logger.debug(
            "flight: %s %.2f → %.2f in %dms, to %s",
            "showing" if showing else "hiding",
            start,
            end,
            animation.duration(),
            self._flight_to,
        )

    def _apply_flight(self, progress: float) -> None:
        """Put the window where the journey says it is, this frame."""
        self._flight_progress = progress
        frame = flight.frame_at(progress, self._flight_home, self._flight_to)
        self.move(frame.x, frame.y)
        self._flight_opacity = frame.opacity
        self._apply_window_opacity()
        self._set_content_scale(frame.scale)

    def _land(self, showing: bool) -> None:
        """Arrive: put the window back where it lives, and hide it if that
        is where it was going.

        The position goes back BEFORE the hide, and that order was found
        rather than chosen: moving a window that has just been hidden is
        undone by the platform's own move event for the last position it
        actually had, so the window came back at the menu bar's corner —
        with `_flight_home` already given up, which is how a window ends up
        somewhere nobody put it. Restoring while it is still on screen has
        no such race, and nothing is seen: at this point in a hide it is at
        zero opacity and a sixteenth of its size.
        """
        self._flight_anim = None
        if self._flight_home is not None:
            self.move(self._flight_home[0], self._flight_home[1])
        if not showing:
            self.setVisible(False)
        self._end_flight()

    def _end_flight(self) -> None:
        """Give back position, opacity and scale, exactly.

        The one place that undoes a flight, so an interruption, a landing
        and a shutdown all leave the window in the same state — there is no
        path that can leave it small, faint, or parked under the menu bar.
        """
        self._flight_progress = 0.0
        if self._flight_home is not None:
            self.move(self._flight_home[0], self._flight_home[1])
            self._flight_home = None
        self._flight_to = None
        self._flight_opacity = 1.0
        self._apply_window_opacity()
        self._set_content_scale(1.0)
        self._end_native_flight()

    def _stop_flight(self) -> None:
        """Abandon a flight in progress and restore what it borrowed."""
        if self._flight_anim is not None:
            self._flight_anim.stop()
            self._flight_anim = None
        self._end_flight()

    # -- getting out of a notification's way -------------------------------

    def _set_yield_to_notifications(self, enabled: bool) -> None:
        """Turn the layer on or off.

        Off stops the looking as well as the fading — the timer is stopped,
        so with the layer off this app is not asking the window server
        anything. The same literal reading of the layers principle that
        removes the activation subscription with per-app positions off:
        off has to equal the app before the feature was written, not the
        app quietly still polling.

        Switching it on does not fade anything by itself. The first poll is
        a third of a second away and will fade if there is something there,
        which is the same answer arrived at honestly rather than a guess
        made at the moment a menu item was ticked.
        """
        self._yield_to_notifications = enabled
        self._settings.setValue("window/yield_notifications", enabled)
        if enabled:
            self._update_yield_watch()
            logger.info(
                "yield to notifications on: polling every %.0fms, ceiling %.2f",
                notifications.POLL_SECONDS * 1000,
                notifications.YIELD_CEILING,
            )
        else:
            logger.info("yield to notifications off: no longer polling")
            self._yield_timer.stop()
            self._stop_yield()
        self._refresh_menu()

    def _update_yield_watch(self) -> None:
        """Poll only while the layer is on and the window is on screen.

        A hidden window is not in anybody's way, so there is nothing to
        look for — and this is the cheapest half of "poll only as often as
        needed", because it is the difference between polling and not
        polling at all rather than between two intervals. Called from both
        of the things it depends on, so neither has to know about the
        other's state.
        """
        wanted = self._yield_to_notifications and self._lyrics_visible
        if wanted == self._yield_timer.isActive():
            return
        if wanted:
            self._yield_timer.start()
            logger.debug("yield: watching for notifications")
        else:
            self._yield_timer.stop()
            logger.debug("yield: stopped watching")

    def _apply_poll_interval(self) -> None:
        """Look harder while there is something to undo.

        The two directions are not worth the same. Going faint a third of a
        second into a banner costs nothing — it has only just arrived. Still
        being faint a third of a second after the screen is clear is the
        user waiting for their own lyrics, so while yielded the interval
        drops to 100ms and the restore costs one short poll plus the fade
        instead of one long poll plus the fade.

        "Yielded" here means the window is not back yet — the target OR the
        level. Reading only the target put the rate back to 300ms the moment
        a banner cleared, while the fade home was still running, so a second
        notification arriving inside that 260ms was met at the idle rate by a
        window that was still faint. Found in the trace, not by reasoning:
        the interval column showed 300 against a level of 1.0.

        Only written when it changes: setInterval on a running timer restarts
        the countdown, and doing that on every poll would mean a timer that
        never fires at the idle rate at all.
        """
        engaged = self._yielding or self._yield_level > 0.0
        wanted = int(notifications.poll_interval_seconds(engaged) * 1000)
        if self._yield_timer.interval() == wanted:
            return
        self._yield_timer.setInterval(wanted)
        logger.debug("yield: polling every %dms", wanted)

    def _check_notifications(self) -> None:
        """One poll: is the notification system over this window?

        The refusal is asked first and it is not only a gate — a pass that
        starts, or a window that is hidden, while the window is already
        faded has to hand the opacity back, or the window would be left
        faint for a banner that has long gone.
        """
        refusal = notifications.yield_refusal(
            enabled=self._yield_to_notifications,
            visible=self._lyrics_visible,
            syncing=self._syncing,
            flying=self._flight_anim is not None,
        )
        if refusal is not None:
            if self._yielding or self._yield_level:
                logger.debug("yield: giving the opacity back, %s", refusal)
                self._set_yielding(False)
            return
        rect = self.frameGeometry()
        window = (rect.x(), rect.y(), rect.width(), rect.height())
        occupied = notifications.occupied_rects()
        covered = notifications.in_the_way(window, occupied)
        if covered == self._yielding:
            return
        logger.debug(
            "yield: %s, window at %r, notification at %r",
            "fading" if covered else "clearing",
            window,
            occupied,
        )
        self._set_yielding(covered)

    def _set_yielding(self, yielding: bool) -> None:
        """Move the yield level towards where it now belongs.

        Retargeted from the level the window actually reached, not from the
        one the last animation was heading for — the tint cross-fade's rule
        and the flight's, so a banner that clears while the window is still
        fading turns around from halfway rather than completing a fade
        nobody is waiting for. ``duration_ms`` makes that turn cost what
        the remaining distance is worth.
        """
        self._yielding = yielding
        # Before the animation, not after it: the faster rate is wanted for
        # the poll that lands during the fade, not only once it has finished.
        self._apply_poll_interval()
        end = 1.0 if yielding else 0.0
        start = self._yield_level
        if self._yield_anim is not None:
            self._yield_anim.stop()
            self._yield_anim = None
        if start == end:
            # Already there — including the case where the layer is
            # switched off having never faded anything.
            self._yield_level = end
            self._apply_window_opacity()
            return
        animation = QVariantAnimation(self)
        animation.setDuration(notifications.duration_ms(start, end))
        animation.setEasingCurve(_YIELD_CURVE)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.valueChanged.connect(self._on_yield_step)
        animation.finished.connect(self._end_yield)
        animation.start()
        self._yield_anim = animation
        logger.debug(
            "yield: level %.2f → %.2f in %dms",
            start,
            end,
            animation.duration(),
        )

    def _on_yield_step(self, value) -> None:
        """One frame. The level is the only thing that moves; what it means
        for the window's opacity is decided in one place, together with the
        user's own setting and whatever a flight is doing."""
        self._yield_level = float(value)
        self._apply_window_opacity()

    def _end_yield(self) -> None:
        """Land on the level exactly, rather than wherever the last frame
        happened to fall."""
        self._yield_level = 1.0 if self._yielding else 0.0
        self._yield_anim = None
        # The level has landed, so the rate can follow it home: this is where
        # a fade back to nothing hands the idle interval back.
        self._apply_poll_interval()
        self._apply_window_opacity()

    def _stop_yield(self) -> None:
        """Give the opacity back at once, with no fade.

        For the paths where a fade would be wrong or pointless: the layer
        being switched off (the user asked for the window back, not for it
        to drift back), and shutdown. Goes through the same level and the
        same composition as everything else, so there is no second idea of
        what "not yielding" looks like.
        """
        if self._yield_anim is not None:
            self._yield_anim.stop()
            self._yield_anim = None
        self._yielding = False
        self._yield_level = 0.0
        self._apply_poll_interval()  # back to the idle rate with the opacity
        self._apply_window_opacity()

    def _toggle_lyrics_visible(self) -> None:
        """What the global hotkey does, and all it does.

        It flips the same flag the menu entry writes and goes through the
        same setter, so there is one piece of state and one place that
        changes it — the tick matches the window whichever of the two was
        used, without either having to know about the other. Runs on the
        main thread: Carbon delivers the event through the Qt event loop
        (see hotkey.py), so this is an ordinary UI call.
        """
        self._set_lyrics_visible(not self._lyrics_visible)

    def apply_saved_visibility(self) -> None:
        """Show the window at startup unless the user left it hidden. Used
        instead of an unconditional show(): the menu bar item is the way
        back, so starting hidden is a valid state."""
        self.setVisible(self._lyrics_visible)

    def _set_romanisation(self, enabled: bool) -> None:
        self._view_model.romanisation_enabled = enabled
        self._settings.setValue("lyrics/romanisation", enabled)
        # A row joining or leaving the strip changes what the widest thing
        # in the song is, which is one of the few things besides a new song
        # that does.
        self._fit_width()
        self._render()

    def _set_spoken_reference(self, enabled: bool) -> None:
        self._spoken_enabled = enabled
        self._settings.setValue("lyrics/spoken_reference", enabled)
        self._update_speak_button()
        self._refresh_menu()

    def _set_speech_rate(self, rate: int) -> None:
        self._speech_rate = rate
        self._settings.setValue("lyrics/speech_rate", rate)
        self._refresh_menu()

    def _set_echo_practice(self, enabled: bool) -> None:
        self._echo_enabled = enabled
        self._loop.echo = enabled
        self._settings.setValue("lyrics/echo_practice", enabled)
        self._refresh_menu()

    # -- native window (vibrancy material, all-desktops behaviour) ---------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._native_applied:
            self._native_applied = True
            self._apply_vibrancy()
            self._apply_shadow()
            self._apply_all_desktops(self._all_desktops)
        # A hidden widget defers its move and resize events until it is
        # shown, and plenty happens to a hidden window: the flight puts the
        # position back before hiding it, a remembered position moves it,
        # and the saved geometry is restored before anything is on screen.
        # Without this the first frame would be drawn in the shape the
        # window had before all of that.
        self._update_docked()

    def _palette_now(self) -> appearance.Palette:
        """The colours to paint with: this appearance, as the accessibility
        settings ask for it.

        One place that composes the two, so a change to either arrives the
        same way. With nothing switched on it hands back the shipped
        palette object itself, which is what makes "no accessibility
        setting on" and "this app before those settings were followed" the
        same pixels rather than nearly the same.
        """
        return appearance.palette_for(
            self._appearance,
            high_contrast=self._display_options.increase_contrast,
            opaque_background=self._display_options.solid_background,
        )

    def _on_color_scheme_changed(self, scheme) -> None:
        """The system changed appearance under us — follow it.

        Fires for a manual flip in System Settings and for the scheduled
        Auto transition alike: both are the same effectiveAppearance
        change as far as anything above AppKit can tell, which is why
        there is nothing here that knows about sunset.
        """
        resolved = appearance.from_color_scheme(int(scheme.value))
        if resolved is self._appearance:
            return
        logger.info(
            "system appearance -> %s, following", resolved.value
        )
        self._appearance = resolved
        self._palette = self._palette_now()
        self._apply_appearance()

    def _on_display_options_changed(self, options) -> None:
        """An accessibility display setting moved — follow it, live.

        The same shape as the appearance change above, and deliberately:
        both are the system telling a running app that what it should look
        like has changed, and an app that only looked at startup is the app
        that is wrong for the rest of the session.

        Three things can follow, and each is checked rather than assumed.
        The palette is re-resolved always, because Increase Contrast moves
        colours and dropping the material moves which background is
        painted. The material is installed or removed only when the answer
        to "is there a material" actually changed — reinstalling one that
        is already there would stack effect views. The line's travel is
        re-applied always, because ``_apply_scale`` early-outs when the
        width has not moved, which is exactly this case.
        """
        if options == self._display_options:
            return
        logger.info(
            "accessibility display options -> %s, following",
            accessibility.describe(options),
        )
        wanted_solid = options.solid_background
        had_solid = self._display_options.solid_background
        self._display_options = options
        self._palette = self._palette_now()
        if wanted_solid != had_solid:
            self._apply_material_presence()
        self._apply_motion()
        self._apply_appearance()

    def _apply_material_presence(self) -> None:
        """Install the vibrancy material, or take it away.

        Reduce Transparency is the one accessibility setting that changes
        something native rather than something painted: the frost is an
        NSVisualEffectView, so honouring the request means the view has to
        go, not merely be painted over. Removed from its superview rather
        than hidden — a hidden effect view is still an effect view, and
        ``_begin_native_flight`` hides and shows this one for its own
        reasons, which would put a suppressed material straight back.

        A no-op before the window has ever been shown: the first install
        happens in ``showEvent``, and it consults the same options.
        """
        if not self._native_applied:
            return
        if self._display_options.solid_background:
            material, self._material = self._material, None
            if material is not None:
                try:
                    material.removeFromSuperview()
                except Exception:
                    logger.exception("could not remove the vibrancy material")
                logger.info("reduce transparency: material removed, solid background")
            self._resnap_tint()  # a different background, so a re-derived tint
            return
        if self._material is None:
            self._apply_vibrancy()

    def _apply_appearance(self) -> None:
        """Repaint everything the palette owns.

        Deliberately not routed through _apply_scale: that one early-outs
        when the width has not moved, which is exactly the case here, and
        an appearance change that silently did nothing is the bug this
        whole feature is about. Everything else — geometry, fonts,
        margins, the fade timers, an engaged loop, a sync pass in
        progress — is untouched, because none of it is a colour.
        """
        self._restyle()
        self._apply_material_appearance()
        self._apply_speak_icon(button_side(self._scale))
        self._apply_why_icon(button_side(self._scale))
        self._apply_retry_icon(button_side(self._scale))
        # The cover colour survives the switch; what it derives from does
        # not. Re-derived against the new palette, without a fade.
        self._resnap_tint()
        self._render()  # the armed discard prompt carries its colour inline

    def _apply_shadow(self) -> None:
        """Ask macOS for the window's own shadow.

        The native one rather than a painted one: macOS derives the shape
        from the window's alpha channel, so it follows the rounded corners
        for free, sits OUTSIDE the window's bounds (a painted shadow would
        have to live inside them and eat into the panel), and costs the
        compositor nothing per frame.

        The catch is that it is cached, not recomputed: macOS keeps the
        shape it last derived, so a resize leaves the old silhouette
        behind until invalidateShadow tells it otherwise (see
        resizeEvent). Verified by screenshot rather than assumed — a
        translucent frameless window is exactly the case where a native
        shadow might have been refused.
        """
        nswindow = self._nswindow()
        if nswindow is None:
            return
        try:
            nswindow.setHasShadow_(True)
            nswindow.invalidateShadow()
            logger.debug("native shadow: hasShadow=%s", bool(nswindow.hasShadow()))
        except Exception:
            logger.exception("failed to enable the native window shadow")

    def _invalidate_shadow(self) -> None:
        """Tell macOS the silhouette moved. Cheap, and skipped entirely
        off cocoa."""
        nswindow = self._nswindow()
        if nswindow is None:
            return
        try:
            nswindow.invalidateShadow()
        except Exception:
            logger.debug("could not invalidate the window shadow", exc_info=True)

    def _apply_material_appearance(self) -> None:
        """Point the NSVisualEffectView at the same mode the scrim is
        painted for. No-op without a material, which is every non-cocoa
        run and any run where vibrancy did not install."""
        if self._material is None:
            return
        try:
            from AppKit import NSAppearance
        except ImportError:  # pragma: no cover - material implies pyobjc
            return
        try:
            name = appearance_name(self._appearance is appearance.Appearance.DARK)
            native = NSAppearance.appearanceNamed_(name)
            if native is None:
                logger.warning("no NSAppearance named %s", name)
                return
            self._material.setAppearance_(native)
            logger.debug("material appearance -> %s", name)
        except Exception:
            logger.exception("failed to set the material appearance")

    def _reread_login_item(self) -> None:
        """Ask macOS what it thinks, discarding whatever we thought.

        Never cached beyond one menu opening: the user can turn this off
        in System Settings while the app runs, and the entry that claims
        otherwise is the whole failure mode this guards against.
        """
        if not self._bundled:
            return
        self._login_status = login_item.status()

    def _set_open_at_login(self, enabled: bool) -> None:
        """Register or unregister, then show what actually happened.

        The stored value records what the user asked for; the tick comes
        from the status read back afterwards. When macOS holds the
        registration for approval the two deliberately disagree, and the
        entry stays unchecked with a label saying where to go — the app
        does not start at login yet, and saying otherwise would be a lie
        the user only discovers at their next login.
        """
        ok, self._login_status = login_item.set_enabled(enabled)
        self._settings.setValue("window/open_at_login", enabled)
        if not ok:
            logger.warning(
                "Open at Login stays %s (status %s)",
                "off" if enabled else "on",
                self._login_status.value,
            )
        self._refresh_menu()

    def _set_all_desktops(self, enabled: bool) -> None:
        self._all_desktops = enabled
        self._apply_all_desktops(enabled)
        self._save_settings()
        self._refresh_menu()

    def _nsview(self):
        """The native NSView backing this widget, or None off-cocoa /
        without pyobjc."""
        if QApplication.platformName() != "cocoa":
            # winId() is only an NSView under the cocoa platform plugin;
            # casting it blindly (e.g. offscreen in tests) would crash.
            return None
        try:
            import objc
        except ImportError:
            logger.warning("pyobjc unavailable: native window features disabled")
            return None
        try:
            return objc.objc_object(c_void_p=int(self.winId()))
        except Exception:
            logger.exception("failed to resolve NSView")
            return None

    def _nswindow(self):
        """The native NSWindow, or None off-cocoa / without pyobjc."""
        view = self._nsview()
        return view.window() if view is not None else None

    def _layer_transform(self) -> hit_test.Transform:
        """The affine transform the compositor is drawing this view
        through, as a fact rather than as a belief.

        Read back rather than reconstructed from ``_flight_progress``,
        which is the same rule the all-desktops toggle is held to: what
        the app last asked for and what the layer is actually carrying are
        two different things, and a press that goes to the wrong place is
        the case where they have come apart. The identity off cocoa,
        which is the truth there — no layer, no transform.
        """
        view = self._nsview()
        if view is None:
            return hit_test.IDENTITY
        try:
            layer = view.layer()
            if layer is None:
                return hit_test.IDENTITY
            a, _b, _c, d, tx, ty = layer.affineTransform()
            return hit_test.Transform(a=a, d=d, tx=tx, ty=ty)
        except Exception:
            logger.debug("could not read the window's layer transform", exc_info=True)
            return hit_test.IDENTITY

    def _set_content_scale(self, scale: float) -> None:
        """Scale what is already drawn, without Qt re-laying it out.

        A CALayer affine transform on the view Qt renders into, so the
        compositor does the scaling: the window keeps its size, the type
        scale is not recomputed, and no text reflows during the journey.
        Animating the window's size instead would run ``_apply_scale`` on
        every frame and the window would read as rewriting itself rather
        than leaving.

        Qt leaves the layer's anchor point at its origin, so scaling about
        the centre is a translation of half the shrinkage in each
        direction — measured, not assumed: a bare scale pinned the content
        to the bottom-left corner.

        A no-op off cocoa and without pyobjc, which is exactly the plain
        move-and-fade the fallback describes.
        """
        view = self._nsview()
        if view is None:
            return
        try:
            layer = view.layer()
            if layer is None:
                return
            bounds = layer.bounds()
            width, height = bounds.size.width, bounds.size.height
            shift_x = width / 2 * (1.0 - scale)
            shift_y = height / 2 * (1.0 - scale)
            layer.setAffineTransform_((scale, 0.0, 0.0, scale, shift_x, shift_y))
        except Exception:
            logger.debug("could not scale the window's content", exc_info=True)

    def _begin_native_flight(self) -> None:
        """Put away the two things that cannot travel with the content.

        The material is a SIBLING view, not a child, so it would sit there
        at full size while the panel shrank away from it. Hiding it costs
        nothing that was not already lost: an alpha below 1 switches the
        behind-window blur off anyway, and the flight is never at alpha 1
        except at its very ends.

        The shadow is drawn by the window server from the window's alpha
        channel and cached, so it would keep the silhouette of a full-size
        panel around a small one. Off for the journey, on at the end.
        """
        if self._material is not None:
            try:
                self._material.setHidden_(True)
            except Exception:
                logger.debug("could not hide the material for the flight", exc_info=True)
        nswindow = self._nswindow()
        if nswindow is None:
            return
        try:
            nswindow.setHasShadow_(False)
        except Exception:
            logger.debug("could not drop the shadow for the flight", exc_info=True)

    def _end_native_flight(self) -> None:
        """Give the material and the shadow back."""
        if self._material is not None:
            try:
                self._material.setHidden_(False)
            except Exception:
                logger.debug("could not restore the material", exc_info=True)
        self._apply_shadow()

    def _apply_vibrancy(self) -> bool:
        """Slide a real NSVisualEffectView underneath the Qt content.

        The window is already frameless and non-opaque for the translucent
        background, which is exactly what a behind-window blend needs. The
        opacity gesture rides on the NSWindow's alpha, which dims the
        material with everything else — but an alpha below 1 also switches
        the blur off entirely, so the frost is something full opacity buys
        and dimming spends. Dimming the two native views instead of the
        window does not save it; the blur wants an untouched alpha.

        Returns whether the material is in place; ``paintEvent`` falls back
        to a solid background when it is not, so a failure here costs the
        blur and nothing else. Verified by readback, not assumed.
        """
        if self._display_options.solid_background:
            # The setting is about this view and nothing else, so the
            # honest answer is not to build one. Checked here rather than
            # only at the call sites because showEvent installs the first
            # material before anything else has had a chance to ask.
            logger.info(
                "reduce transparency: no vibrancy material, solid background instead"
            )
            return False
        nsview = self._nsview()
        if nsview is None:
            return False
        try:
            from AppKit import NSAppearance, NSVisualEffectView
        except ImportError:
            logger.warning("pyobjc unavailable: no vibrancy material")
            return False
        try:
            container = nsview.superview()
            if container is None:
                logger.warning("no superview to host the material")
                return False
            effect = NSVisualEffectView.alloc().initWithFrame_(nsview.frame())
            effect.setAutoresizingMask_(AUTORESIZE_FILL)
            effect.setBlendingMode_(BLENDING_MODE_BEHIND_WINDOW)
            effect.setMaterial_(MATERIAL_HUD_WINDOW)
            effect.setState_(STATE_ACTIVE)
            # Set to the mode this window resolved, not left to inherit:
            # the scrim painted on top comes from that same answer, and a
            # material in the other mode would show through it.
            name = appearance_name(self._appearance is appearance.Appearance.DARK)
            native = NSAppearance.appearanceNamed_(name)
            if native is not None:
                effect.setAppearance_(native)
            # The material owns its own corners at the same radius the
            # scrim is painted with, so the two coincide exactly — and on
            # the same two or four of them, so a docked window's blur is
            # squared off where its paint is.
            effect.setWantsLayer_(True)
            effect.layer().setCornerRadius_(float(_CORNER_RADIUS))
            effect.layer().setMaskedCorners_(masked_corners(self._docked))
            effect.layer().setMasksToBounds_(True)
            container.addSubview_positioned_relativeTo_(effect, WINDOW_BELOW, nsview)
        except Exception:
            logger.exception("failed to install the vibrancy material")
            return False

        # Readback: it must be in the hierarchy AND behind the Qt view.
        siblings = list(container.subviews())
        if effect not in siblings or siblings.index(effect) > siblings.index(nsview):
            logger.warning("vibrancy material did not land behind the content")
            return False
        self._material = effect
        logger.debug(
            "vibrancy: material=%d state=%d radius=%.1f",
            int(effect.material()),
            int(effect.state()),
            float(effect.layer().cornerRadius()),
        )
        # Repaint with the scrim rather than the solid fill — which is a
        # different background, so any tint has to be re-derived for it.
        self._resnap_tint()
        return True

    def _apply_material_corners(self) -> None:
        """Tell the material which of its corners are rounded.

        Separate from installing it because the shape changes far more
        often than the material does: every time the window arrives at or
        leaves the docked position. Verified by readback, like every other
        native state this window sets.
        """
        if self._material is None:
            return
        wanted = masked_corners(self._docked)
        try:
            layer = self._material.layer()
            if layer is None:
                return
            layer.setMaskedCorners_(wanted)
            landed = int(layer.maskedCorners())
        except Exception:
            logger.debug("could not set the material's corners", exc_info=True)
            return
        if landed != wanted:
            logger.warning(
                "material corners did not land: asked 0x%x, got 0x%x", wanted, landed
            )

    def _apply_all_desktops(self, enabled: bool) -> None:
        """All-desktops toggle: native window flags only —
        CanJoinAllSpaces + FullScreenAuxiliary with Qt's conflicting
        FullScreenPrimary bit cleared (Primary wins over Auxiliary and
        blocks full-screen Spaces), at status window level so the overlay
        stays above full-screen content. Disabling restores Qt's saved
        defaults. Qt has no cross-platform API for Spaces, hence pyobjc.

        The accessory activation policy is NOT part of this: it is applied
        once at startup and never revoked (see apply_accessory_policy), so
        no toggle state can bring the Dock icon back.
        """
        nswindow = self._nswindow()
        if nswindow is None:
            return
        try:
            if enabled:
                if self._saved_native is None:
                    self._saved_native = (
                        int(nswindow.collectionBehavior()),
                        int(nswindow.level()),
                    )
                nswindow.setCollectionBehavior_(
                    all_desktops_behavior(int(nswindow.collectionBehavior()))
                )
                nswindow.setLevel_(STATUS_WINDOW_LEVEL)
            elif self._saved_native is not None:
                behavior, level = self._saved_native
                self._saved_native = None
                nswindow.setCollectionBehavior_(behavior)
                nswindow.setLevel_(level)
            logger.debug(
                "native state: behavior=0x%x level=%d",
                int(nswindow.collectionBehavior()),
                int(nswindow.level()),
            )
        except Exception:
            logger.exception("failed to set NSWindow collection behavior")

    # -- persistence -------------------------------------------------------

    def _set_opacity(self, value: float) -> None:
        self._opacity = max(_MIN_OPACITY, min(_MAX_OPACITY, value))
        self._apply_window_opacity()

    def _apply_window_opacity(self) -> None:
        """The one place that decides how solid the window is.

        Four things have an opinion and they compose rather than compete:
        the user's own setting is the baseline, a notification yield takes
        it down towards a ceiling, a ghost takes whatever is left down
        towards its own, and a flight scales the result as the window
        leaves for the menu bar. Every one of them used to call
        setWindowOpacity itself, which worked only because no two of them
        were ever true at once — and a fade to a notification that landed
        during a flight would have been the first pair.

        The two ceilings compose as a ``min`` each: a banner arriving over
        a ghosted window leaves the window at whichever of the two is
        fainter, which is the one that is more out of the way, and neither
        can ever make the window brighter than the user asked for.
        """
        base = notifications.yielded_opacity(self._opacity, self._yield_level)
        base = proximity.ghost_opacity(base, self._ghost_level)
        self.setWindowOpacity(base * self._flight_opacity)

    def _restore_settings(self) -> None:
        try:
            opacity = float(self._settings.value("window/opacity", _DEFAULT_OPACITY))
        except (TypeError, ValueError):
            opacity = _DEFAULT_OPACITY
        self._set_opacity(opacity)
        # Read before the size, and assigned rather than routed through the
        # setter like every other layer here: the saved size belongs to
        # whichever layout was in force when it was written, and a window
        # restored into the wrong one would be clamped to the wrong floor
        # before anything else had a chance to say so.
        self._compact = self._settings.value("window/compact", False, type=bool)
        self._compact_applied = self._compact
        # Read before the size for the same reason as the layout: in the
        # strip this IS the type scale, so a saved size restored ahead of
        # it would be measured against the wrong one. A value that is not
        # one of the presets is a hand-edited or an outgrown preference,
        # and it falls back rather than being honoured — the same rule the
        # speech rate is held to.
        self._compact_text_size = self._settings.value(
            "window/compact_text_size", DEFAULT_COMPACT_TEXT_SIZE, type=int
        )
        if self._compact_text_size not in COMPACT_TEXT_SIZES:
            self._compact_text_size = DEFAULT_COMPACT_TEXT_SIZE
        # Each layout's own shape, position included. Read per key rather
        # than as one blob: the size keys predate the position ones, so a
        # preferences file written before this session has half of each
        # record and has to keep the half it has.
        for compact, group in ((False, "full"), (True, "compact")):
            x = self._settings.value(f"window/{group}_x", None)
            y = self._settings.value(f"window/{group}_y", None)
            self._shapes.remember(
                compact,
                x=int(x) if x is not None else None,
                y=int(y) if y is not None else None,
                width=self._settings.value(
                    f"window/{group}_width", 0, type=int
                ) or None,
                height=(
                    self._settings.value("window/full_height", 0, type=int)
                    or None
                ) if not compact else None,
            )
        # The one setting in this app that defaults ON, and it can, because
        # it acts only inside a layout that is itself opt-in and default
        # off. With compact off there is nothing here to have an opinion
        # about; see the _fitting property.
        self._fit_to_song = self._settings.value(
            "window/fit_to_song", True, type=bool
        )
        available = self._available_geometry()
        size = self._settings.value("window/size")
        if isinstance(size, QSize):
            self.resize(size.expandedTo(_MIN_SIZE).boundedTo(available.size()))
        else:
            self.resize(_BASE_WIDTH, _DEFAULT_HEIGHT)
        position = self._settings.value("window/pos")
        if isinstance(position, QPoint):
            self.move(_clamped_point(QRect(position, self.size()), available))
        self._all_desktops = self._settings.value(
            "window/all_desktops", False, type=bool
        )
        self._view_model.romanisation_enabled = self._settings.value(
            "lyrics/romanisation", False, type=bool
        )
        self._spoken_enabled = self._settings.value(
            "lyrics/spoken_reference", True, type=bool
        )
        self._speech_rate = self._settings.value(
            "lyrics/speech_rate", SPEECH_RATE_WPM, type=int
        )
        if self._speech_rate not in SPEECH_RATE_PRESETS:
            self._speech_rate = SPEECH_RATE_WPM
        self._echo_enabled = self._settings.value(
            "lyrics/echo_practice", False, type=bool
        )
        self._loop.echo = self._echo_enabled
        # Off by default, like every other layer: the plain window is what
        # the app is, and the cover colour is something asked for.
        self._album_colour = self._settings.value(
            "window/album_colour", False, type=bool
        )
        # The map is restored whether or not the layer is on: switching it
        # off should not throw away what it learned, and the forget entry
        # has to be reachable to clear a bad map without turning the
        # feature back on first.
        self._positions = AppPositions.from_json(
            self._settings.value("window/app_positions", "")
        )
        # Assigned rather than routed through the setter, like every other
        # layer here: restore runs BEFORE the menu is built, and the setter
        # refreshes the menu. The one thing the setter does that matters at
        # startup is starting the watcher, so that is done explicitly.
        self._remember_position = self._settings.value(
            "window/remember_position", False, type=bool
        )
        if self._remember_position:
            self._read_frontmost()
            started = self._watcher.start()
            logger.info(
                "per-app positions restored on: frontmost=%s watching=%s "
                "remembered=%d own=%s",
                self._frontmost,
                started,
                len(self._positions),
                self._own_bundle_id,
            )
        else:
            logger.info(
                "per-app positions off (%d remembered): not watching activations",
                len(self._positions),
            )
        # Assigned rather than routed through the setter, for the same
        # reason as the layer above: this runs before the menu is built and
        # the setter refreshes the menu. Watching depends on both this and
        # the saved visibility, so it is started after the pair of them
        # rather than beside either one.
        # Assigned rather than routed through the setter, like every layer
        # here: restore runs before the menu is built and the setter
        # refreshes it. Nothing else to do — the tray is built afterwards
        # and asks for the spec itself.
        self._menubar_animation = self._settings.value(
            "window/menubar_animation", False, type=bool
        )
        self._yield_to_notifications = self._settings.value(
            "window/yield_notifications", False, type=bool
        )
        # Assigned rather than routed through the setter, like every layer
        # here. Off by default and validated back to one of the three
        # modes, the same rule the speech rate and the strip's type size
        # are held to: a value nobody recognises leaves the window doing
        # what it does with no layer on.
        self._proximity_mode = proximity.mode_from(
            self._settings.value("window/proximity", proximity.OFF)
        )
        self._lyrics_visible = self._settings.value("window/visible", True, type=bool)
        self._update_yield_watch()
        # The pointer watch is not started here: _apply_compact does it,
        # and it runs after the constructor has finished building the
        # controls the reveal is about. It reads the mode restored above,
        # so a window that starts in the full layout with the yield on is
        # still watching.
        # Open at Login is NOT restored from here: the stored value is what
        # the user last asked for, and the system is what is actually true.
        # Reading it back is the whole point, so the setting is only ever
        # compared against reality — loudly, when they disagree.
        wanted = self._settings.value("window/open_at_login", False, type=bool)
        if self._bundled and wanted != login_item.is_enabled(self._login_status):
            logger.info(
                "Open at Login was last set to %s here but macOS says %s, "
                "following macOS",
                wanted,
                self._login_status.value,
            )

    def _save_settings(self) -> None:
        # Where the window BELONGS, not where a dodge has it standing:
        # shutdown lands a resize in flight for exactly this reason, and a
        # temporary position persisted as the permanent one is the same
        # bug with a different cause.
        self._settings.setValue("window/pos", self._home_pos())
        self._settings.setValue("window/size", self.size())
        self._settings.setValue("window/opacity", self._opacity)
        self._settings.setValue("window/compact", self._compact)
        self._settings.setValue("window/compact_text_size", self._compact_text_size)
        self._settings.setValue("window/fit_to_song", self._fit_to_song)
        # The shape the OTHER layout was last left at, and the live one
        # too: what window/pos and window/size hold is where the window is
        # STANDING, which after a sync pass has borrowed the full layout is
        # not the same question. Refreshed here so a quit mid-layout keeps
        # both, and _remember_shape is what declines to overwrite a width
        # the song is currently choosing.
        self._remember_shape(self._compact_applied)
        # The strip's height is derived from its type size now, so the key
        # that used to hold it is removed rather than left behind: a stored
        # number nothing reads is a second answer waiting for somebody to
        # believe it.
        self._settings.remove("window/compact_height")
        for compact, group in ((False, "full"), (True, "compact")):
            shape = self._shapes.recall(compact)
            self._settings.setValue(f"window/{group}_width", shape.width or 0)
            if shape.has_position:
                self._settings.setValue(f"window/{group}_x", shape.x)
                self._settings.setValue(f"window/{group}_y", shape.y)
        self._settings.setValue(
            "window/full_height", self._shapes.recall(False).height or 0
        )
        self._settings.setValue("window/all_desktops", self._all_desktops)
        self._settings.setValue("window/album_colour", self._album_colour)
        self._settings.setValue("window/visible", self._lyrics_visible)
        self._settings.setValue("window/remember_position", self._remember_position)
        self._settings.setValue("window/menubar_animation", self._menubar_animation)
        self._settings.setValue(
            "window/yield_notifications", self._yield_to_notifications
        )
        self._settings.setValue("window/proximity", self._proximity_mode)
        self._settings.setValue("window/app_positions", self._positions.to_json())

    def _shutdown(self) -> None:
        """Leave nothing of ours running, before anything of ours is
        destroyed.

        The monitor is a QThread the window owns, and a QThread destroyed
        while it is still running is a qFatal — the process aborts with
        "QThread: Destroyed while thread is still running" rather than
        exiting. The pool workers are the same hazard one step removed:
        each holds a signals object and a provider, and a fetch blocked in
        a socket outlives ``exec()`` by as long as its timeout, so without
        this the app tore its window down and then let a worker report
        into the wreckage.

        Both waits are bounded. A worker that will not come back in time
        (``say`` can hold a line for a minute) is logged and left to the
        thread pool's own destructor, which is where it was always going
        to be dealt with; blocking quit on it would be worse.

        The global hotkey goes first, before anything is saved or joined.
        It is the one thing here that can still call *in*: Carbon holds a
        pointer to a callback that toggles this window, so releasing it
        last would leave a keypress able to land in the middle of a
        teardown.
        """
        self._hotkey.unregister()
        # The other things that can still call in: NSWorkspace holds two
        # blocks, one that moves a window being torn down and one that
        # repaints it. Removed beside the hotkey, and for the same reason.
        self._watcher.stop()
        self._display_watcher.stop()
        # The third thing that can call in, and the newest: Spotify's
        # announcement holds an observer that wakes the monitor. Beside
        # the other two rather than with the monitor below, because what
        # matters is that nothing outside can still reach in, not which of
        # ours it reaches.
        self._announcer.stop()
        # The fourth, and the only one that is ours rather than the
        # system's: the menu bar item holds a menu whose items point back
        # at a helper that calls into this window. Qt used to own that item
        # and destroy it with the widget; an item this app made is one this
        # app has to give back, or it outlives the window that answers it.
        if self._tray is not None:
            self._tray.release()
            self._tray = None
        # The fifth thing that can still call in, and the only one that is
        # a window: the paste window holds a connection into this one and
        # would outlive it, since it is a top-level of its own with no
        # parent to take it down. Closed here beside the rest.
        self._close_paste_window()
        self._settle_timer.stop()
        self._pointer_timer.stop()
        # Before the pool is drained below: the album warm sleeps between
        # its requests by design, so a worker that was not told to stop
        # would sit out the whole album inside the three second wait. The
        # timer goes too, or a warm armed a moment ago starts one during
        # the teardown.
        self._warm_timer.stop()
        self._warm_stop.set()
        self._stop_reveal()
        # Landed rather than stopped, before the save below: a resize
        # abandoned mid-travel would persist a waypoint as the window's
        # size. The same argument the flight makes three lines down.
        self._finish_fit()
        # And before the save for the flight's other reason: a dodge holds
        # the window's real position while it stands aside. Without the
        # travel, because there is nobody left to watch it. _save_settings
        # asks for the home position anyway, so this is what stops the
        # window being torn down faint and click-through rather than what
        # stops the wrong position being written.
        self._release_proximity(animate=False)
        self._stop_move()
        # Stopped before the save, like the flight below and for a milder
        # version of the same reason: a poll landing mid-teardown would ask
        # the window server about a window being destroyed. The level goes
        # back too — it is not persisted, but leaving the window faint while
        # it is torn down would be visible.
        self._yield_timer.stop()
        self._stop_yield()
        # Before the save: a flight holds the window's real position while
        # it is away, and saving mid-journey would persist the menu bar's
        # corner as where the user left it.
        self._stop_flight()
        self._save_settings()
        self._monitor_thread.stop()
        # A tick may be mid-query, waiting on Spotify to answer.
        if not self._monitor_thread.wait(_SHUTDOWN_WAIT_MS):
            logger.warning("monitor thread did not stop in time")
        # Queued work that has not started yet is simply dropped: a fetch
        # or a save that has not begun has nothing to finish.
        self._pool.clear()
        if not self._pool.waitForDone(_SHUTDOWN_WAIT_MS):
            logger.warning("worker still running at shutdown (%d active)",
                           self._pool.activeThreadCount())
        # Last, and after the workers, because a fetch still in flight owns
        # a connection: LRCLIB holds an idle one for minutes, and leaving
        # sockets open on somebody else's server after quitting is untidy.
        # Cannot block — an idle connection has nothing in flight on it.
        close_connections()


def _nsapp():
    """The one door onto NSApplication, or None where there is none.

    Two things in this app ask it something — the activation policy at
    startup, and bringing the app forward for the paste window — and they
    are the same capability, so they go through one function. Off cocoa or
    without pyobjc it answers None and both callers do nothing, which is
    also what keeps the suite from ever activating the developer's app.
    """
    if QApplication.platformName() != "cocoa":
        return None
    try:
        from AppKit import NSApplication
    except ImportError:
        logger.warning("pyobjc unavailable: NSApplication cannot be reached")
        return None
    return NSApplication.sharedApplication()


def apply_accessory_policy() -> None:
    """Run as a menu bar accessory: no Dock icon, no Cmd-Tab entry.

    Unconditional and permanent. A Regular-policy app owns a Space, so
    activating it from inside another app's full-screen Space makes macOS
    switch there instead of overlaying — which no collection-behavior flag
    can undo. Must be in force before the window is first shown, or macOS
    may still treat that show as a regular-app activation. With no Dock
    icon, Quit lives in the menu bar item (and SIGINT).
    """
    shared = _nsapp()
    if shared is None:
        return
    try:
        shared.setActivationPolicy_(ACTIVATION_POLICY_ACCESSORY)
        logger.debug(
            "activation policy -> accessory (readback=%d)",
            int(shared.activationPolicy()),
        )
    except Exception:
        logger.exception("failed to set activation policy")


def bring_to_front() -> None:
    """Make this app the active one, so a window of ours can take the
    keyboard.

    MEASURED, and it is the reason this function exists at all: under the
    accessory activation policy, ``show()`` + ``raise_()`` +
    ``activateWindow()`` leaves the window inactive and the focus widget
    None. A window nobody can type into is the one thing the paste window
    may not be, and no headless test can catch it — the offscreen platform
    reports whatever it likes about focus. Verified by running the real
    thing under the real policy: False and None before this call, True and
    the text box after it.

    ``activateIgnoringOtherApps_`` rather than anything gentler because an
    accessory app has no Dock icon to be activated THROUGH; it is the same
    thing macOS does for an app whose icon you click, and it is asked for
    only by an explicit menu command, never on its own.
    """
    shared = _nsapp()
    if shared is None:
        return
    try:
        shared.activateIgnoringOtherApps_(True)
    except Exception:
        logger.exception("could not bring the app forward")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("SOTTOVOCE_LOG", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("sottovoce")
    app.setOrganizationName("sottovoce")
    # A menu bar app outlives its window: hiding the lyrics must not be
    # mistaken for the user closing the last window and quitting.
    app.setQuitOnLastWindowClosed(False)
    apply_accessory_policy()  # before any window exists, let alone shows

    # Ctrl-C: Python signal handlers only run while the interpreter is
    # executing bytecode, so an idle Qt event loop would never deliver
    # SIGINT — the timer wakes the interpreter periodically to let it in.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    interrupt_timer = QTimer()
    interrupt_timer.timeout.connect(lambda: None)
    interrupt_timer.start(200)

    window = LyricsWindow()
    window.apply_saved_visibility()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
