"""Floating always-on-top lyrics window — the main lyrisync app.

Run with ``lyrisync``. Spotify polling runs on a worker QThread that emits
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
    QActionGroup,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsEffect,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from lyrisync import appearance
from lyrisync.artwork import ArtworkProvider
from lyrisync.geometry import (
    RESIZE_MARGIN,
    button_margin,
    button_side,
    clamped_position,
    min_window_height,
    sync_bar_bottom,
    sync_bar_gap,
    sync_bar_height,
    sync_bar_reserve,
    text_gutter,
)
from lyrisync.gestures import opacity_step, scroll_step, wheel_action
from lyrisync import hotkey
from lyrisync.loop import LineLoop, LoopPhase
from lyrisync.lyrics_provider import LyricsError, LyricsProvider
from lyrisync.macspaces import (
    ACTIVATION_POLICY_ACCESSORY,
    STATUS_WINDOW_LEVEL,
    all_desktops_behavior,
)
from lyrisync import login_item
from lyrisync.menu import (
    ALBUM_COLOUR,
    ALL_DESKTOPS,
    ECHO,
    OPEN_AT_LOGIN,
    QUIT,
    ROMANISATION,
    SEPARATOR_AFTER_SHOW,
    SEPARATOR_BEFORE_QUIT,
    SHOW_LYRICS,
    SPEECH_RATE,
    SPOKEN,
    SYNC,
    visible_entries,
)
from lyrisync.player_monitor import (
    PlaybackState,
    PlayerMonitor,
    PlayerSnapshot,
    SpotifyQueryError,
    pause_playback,
    resume_playback,
    set_position,
)
from lyrisync.speech import (
    SPEECH_RATE_PRESETS,
    SPEECH_RATE_WPM,
    SpeechSession,
    button_visible,
    detect_voice,
    speak_korean,
)
from lyrisync.sync_session import interpolated_position
from lyrisync.symbols import (
    SPEAK_FALLBACK_GLYPH,
    SPEAK_SYMBOL,
    icon_size,
    symbol_icon,
)
from lyrisync.typography import (
    BOTTOM_MARGIN,
    CONTEXT,
    CURRENT,
    CURRENT_SPACING,
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
from lyrisync.vibrancy import (
    AUTORESIZE_FILL,
    BLENDING_MODE_BEHIND_WINDOW,
    MATERIAL_HUD_WINDOW,
    STATE_ACTIVE,
    WINDOW_BELOW,
    appearance_name,
)
from lyrisync.view_model import LyricsViewModel, Mode

logger = logging.getLogger(__name__)

_BASE_WIDTH = 460
_MIN_SIZE = QSize(260, 120)
_CORNER_RADIUS = 14
_RESIZE_MARGIN = RESIZE_MARGIN

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

# The album-colour cross-fade. Slower than the line fade on purpose: that
# one has to finish before a lyric is due, this one is scenery and a
# 100ms colour change reads as a flicker rather than as a transition.
_TINT_FADE_MS = 600

# How long shutdown waits for the monitor thread and then for the worker
# pool. Long enough for a poll to finish its osascript (2s timeout) or a
# fetch to notice it is done, short enough that quit still feels like quit.
_SHUTDOWN_WAIT_MS = 3000

_TITLE_CARD_SECONDS = 2.0
# How long the sync exit control stays armed awaiting its second click.
# What the progress row says meanwhile is coloured from the palette, so
# the armed prompt reads in both modes (see _exit_confirm_text).
_EXIT_CONFIRM_MS = 4000
_EXIT_CONFIRM_TEXT = "discard this sync? tap ✕ again"
_DOTS_FRAMES = ["·", "· ·", "· · ·"]
_RETRY_TICK_MS = 1000

MENUBAR_ICON = Path(__file__).parent / "assets" / "menubar.svg"


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
QPushButton#loop, QPushButton#speak {{
    color: {rgba(palette.control_idle)}; background: transparent; border: none;
    border-radius: {round(6 * scale)}px;
    font-size: {round(15 * scale)}px;
}}
QPushButton#loop:hover, QPushButton#speak:hover {{
    color: {rgba(palette.control_hover)}; background: {rgba(palette.control_wash)};
}}
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
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._travel = 0.0

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

    def boundingRectFor(self, rect):
        """Room to draw outside the source, or the travel would be
        clipped to the widget's own box and the line would appear to
        dissolve at the edge instead of leaving."""
        return QRectF(rect).adjusted(0, -self._travel, 0, self._travel)

    def draw(self, painter) -> None:
        # sourcePixmap returns the pixmap and writes where to put it into
        # the QPoint it is handed — an out-parameter, not a second return
        # value. Ignoring it would draw the block at the widget's origin
        # instead of its own.
        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates,
            offset,
            QGraphicsEffect.PixmapPadMode.NoPad,
        )
        if pixmap.isNull():
            return
        painter.setOpacity(max(0.0, 1.0 - abs(self._progress)))
        painter.drawPixmap(
            QPointF(offset) + QPointF(0.0, self._progress * self._travel), pixmap
        )


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
    finished = Signal(str, object, bool)  # track_id, TrackLyrics | None, ok


class FetchTask(QRunnable):
    """Runs one lyrics lookup off the UI thread. Failures are logged and
    reported as ok=False — never silently converted to "no lyrics"."""

    def __init__(self, provider: LyricsProvider, snapshot: PlayerSnapshot) -> None:
        super().__init__()
        self.signals = _FetchSignals()
        self._provider = provider
        self._snapshot = snapshot

    def run(self) -> None:
        track_id = self._snapshot.track_id
        lyrics, ok = None, False
        try:
            lyrics = self._provider.get_lyrics(self._snapshot)
            ok = True
        except LyricsError:
            logger.exception("lyrics fetch failed for %s", track_id)
        except Exception:
            logger.exception("unexpected error fetching lyrics for %s", track_id)
        try:
            self.signals.finished.emit(track_id, lyrics, ok)
        except RuntimeError:
            pass  # app tore down the signal object while we were fetching


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
        self._settings = settings or QSettings("lyrisync", "lyrisync")

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
        # Which palette the window is painting with. Resolved from the
        # system now and re-resolved on every change for as long as the
        # app runs — reading it once at startup would be wrong by the
        # afternoon on a Mac set to Auto.
        self._appearance = _system_appearance()
        self._palette = appearance.palette_for(self._appearance)
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

        self._loop = LineLoop()
        self._loop_timer = QTimer(self)
        self._loop_timer.setSingleShot(True)
        self._loop_timer.timeout.connect(self._do_loop_wrap)
        self._echo_enabled = False  # restored from settings below
        self._attempt_button = self._make_overlay_button(
            # U+FE0E asks for text presentation: the mic then draws as a
            # monochrome glyph instead of a colour emoji, which is the only
            # thing separating these controls from an iMessage sticker.
            "attempt", "🎤︎", "Done — play the line again"
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

        # Tap-to-sync. The track key is captured on entry: a same-track
        # re-announcement (metadata settling) must not cancel the pass,
        # only a genuinely different song.
        self._sync_track_key: Optional[tuple] = None
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
        # Live, not read-once: on a Mac set to Auto the appearance changes
        # under a running app, and the app that only looked at startup is
        # the one that is wrong every evening.
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._on_color_scheme_changed
        )
        QApplication.instance().aboutToQuit.connect(self._shutdown)

        self._monitor_thread = MonitorThread(self)
        self._monitor_thread.track_changed.connect(self._on_track_change)
        self._monitor_thread.position_updated.connect(self._on_position_update)
        self._monitor_thread.state_changed.connect(self._on_state_change)
        self._monitor_thread.start()

        self._dots_timer = QTimer(self)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._dots_timer.start(400)

        self._retry_timer = QTimer(self)
        self._retry_timer.timeout.connect(self._tick_retry)
        self._retry_timer.start(_RETRY_TICK_MS)

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

    def _on_fetch_finished(self, track_id: str, lyrics: object, ok: bool) -> None:
        # Stale results (track changed while the fetch was in flight) are
        # rejected by the view model; the provider already cached them.
        if self._view_model.fetch_completed(track_id, lyrics, ok, now=time.monotonic()):
            self._release_loop()  # lyrics changed under the loop
            self._render()

    def _on_position_update(self, snapshot: PlayerSnapshot) -> None:
        self._last_state = snapshot.state
        self._last_position = snapshot.position_seconds
        self._last_polled_at = snapshot.polled_at
        self._view_model.position_changed(snapshot.position_seconds)
        timeline = self._view_model.timeline()
        if timeline is None:
            return
        lines, index = timeline
        if self._displayed_index != index:
            # Seek, pause-drift correction, or a missed prediction: snap.
            self._render()

        position = snapshot.position_seconds
        playing = snapshot.state is PlaybackState.PLAYING
        if self._loop.engaged:
            if not self._loop.still_valid(position):
                self._release_loop()  # user seeked outside the line
                self._render()
                return
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
        was_attempt = self._loop.engaged and self._loop.phase is LoopPhase.ATTEMPT
        self._loop.release()
        self._loop_timer.stop()
        self._loop_button.setChecked(False)
        self._attempt_button.setVisible(False)
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
        """Start a sync pass: every pass is a complete run from line one,
        so the track goes back to 0 and playback is made sure to be
        running before the first line arrives."""
        if not self._view_model.begin_sync():
            return
        self._release_loop()
        self._disarm_sync_exit()
        snapshot = self._current_snapshot
        self._sync_track_key = snapshot.track_key if snapshot is not None else None
        self._pool.start(PlayerCommandTask(seek_to=0.0, resume=True))
        self._apply_layout_margins()
        self._render()

    def _cancel_sync(self) -> bool:
        """Discard the pass in progress. Returns True when there was one."""
        if not self._view_model.end_sync():
            return False
        self._sync_track_key = None
        self._disarm_sync_exit()
        self._apply_layout_margins()
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
        self._speak_button.setVisible(
            button_visible(
                synced=self._view_model.display().mode is Mode.SYNCED,
                line_text=line_text,
                feature_enabled=self._spoken_enabled,
                voice_ok=self._speech_available,
            )
        )

    def _start_fetch(self, snapshot: PlayerSnapshot) -> None:
        task = FetchTask(self._provider, snapshot)
        task.signals.finished.connect(self._on_fetch_finished)
        self._pool.start(task)

    def _tick_retry(self) -> None:
        """Honour "will retry": while in ERROR, re-attempt the fetch for
        the current track every RETRY_INTERVAL_SECONDS."""
        if self._view_model.retry_due(time.monotonic()):
            if self._current_snapshot is not None:
                self._start_fetch(self._current_snapshot)
            self._render()

    def _tick_dots(self) -> None:
        if self._view_model.display().mode is Mode.FETCHING and not self._card_active():
            self._dots_frame = (self._dots_frame + 1) % len(_DOTS_FRAMES)
            self._current.setText(_DOTS_FRAMES[self._dots_frame])

    # -- anticipatory line fade --------------------------------------------

    def _schedule_line_advance(
        self, lines: list, index: int, position_seconds: float
    ) -> None:
        """(Re)arm the fade-out/swap timers from the next line's timestamp.
        Rescheduled on every poll, so seeks correct the timing within one
        poll interval."""
        upcoming = index + 1
        if upcoming >= len(lines):
            self._fadeout_timer.stop()
            self._swap_timer.stop()
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
        """
        if self._view_model.timeline() is None or self._card_active():
            return
        if self._last_state is PlaybackState.PLAYING:
            self._animate_line(-1.0, QEasingCurve.Type.InSine)

    def _predicted_swap(self) -> None:
        timeline = self._view_model.timeline()
        if timeline is None or self._card_active():
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
        # The new line starts below and rises into place, eased OUT so it
        # decelerates onto its mark. The schedule is unchanged and still
        # authoritative: this animation ENDS at the line's timestamp, so
        # the motion completes on time rather than starting on time.
        self._current_fx.progress = 1.0
        self._animate_line(0.0, QEasingCurve.Type.OutSine)

    def _animate_line(self, end: float, curve: QEasingCurve.Type) -> None:
        if self._fade_anim is not None:
            self._fade_anim.stop()
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
        mark."""
        self._fadeout_timer.stop()
        self._swap_timer.stop()
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim = None
        self._current_fx.progress = 0.0

    def _set_lines(self, lines: list, index: int) -> None:
        current = lines[index][1] if index >= 0 else ""
        # The ↻ marker is display-only: pronunciation is looked up on the
        # unprefixed line text.
        # ↻ marks the engaged loop through both phases; the 🎤 done-button
        # (not a text marker) is the your-turn signal during ATTEMPT.
        shown = f"↻ {current}" if self._loop.engaged else current
        self._previous.setText(lines[index - 1][1] if index >= 1 else "")
        self._current.setText(shown)
        self._set_pronunciation(self._view_model.pronunciation_for(current))
        self._upcoming.setText(lines[index + 1][1] if index + 1 < len(lines) else "")
        self._update_speak_button(current)

    def _set_pronunciation(self, text: str) -> None:
        self._pron.setText(text)
        self._pron.setVisible(bool(text))

    # -- rendering ---------------------------------------------------------

    def _card_active(self) -> bool:
        return time.monotonic() < self._title_card_until

    def _render(self) -> None:
        display = self._view_model.display()
        self._cancel_line_schedule()
        # Menu entries follow mode and lyrics, so keep the menu bar item in
        # step here rather than only when it is about to open.
        self._refresh_menu()

        # Loop button only where looping is possible (synced timestamps).
        self._loop_button.setVisible(display.mode is Mode.SYNCED)
        self._attempt_button.setVisible(
            display.mode is Mode.SYNCED
            and self._loop.engaged
            and self._loop.phase is LoopPhase.ATTEMPT
        )
        if display.mode is not Mode.SYNCED:
            self._speak_button.setVisible(False)  # synced path updates it per line
        self._render_sync_controls(display)

        # Persistent compact header whenever a track is known.
        self._header.setText(display.header)
        self._header.setVisible(bool(display.header))

        if display.mode is Mode.SYNCING:
            self._displayed_index = None
            self._show_plain_view(False)
            self._previous.setText(display.previous)
            self._current.setText(display.current)
            self._set_pronunciation(display.pronunciation)
            self._upcoming.setText(display.upcoming)
            return

        if display.header and display.mode is not Mode.IDLE and self._card_active():
            # Title card: the song announces itself before lyrics start.
            self._displayed_index = None
            self._show_plain_view(False)
            self._previous.setText("")
            self._current.setText(display.header)
            self._set_pronunciation("")
            self._upcoming.setText("")
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
        self._previous.setText(display.previous)
        self._current.setText(current)
        self._set_pronunciation(display.pronunciation)
        self._upcoming.setText(display.upcoming)

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
        hidden side gives up ALL its layout space, stretches included."""
        self._plain_note.setVisible(plain)
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

    def _current_background(self) -> appearance.RGBA:
        """What is on screen right now, mid-fade included."""
        return appearance.blend(self._tint_from, self._tint_to, self._tint_mix)

    def _set_tint(self, tint_rgb, animate: bool = True) -> None:
        """Cross-fade the panel to a new cover colour.

        The fade starts from whatever is on screen rather than from the
        previous target, so a track changed halfway through the last fade
        moves on from where it had got to instead of jumping back.
        """
        if tint_rgb == self._tint_rgb:
            return
        self._tint_rgb = tint_rgb
        start = self._current_background()
        end = self._background_for(tint_rgb)
        if self._tint_anim is not None:
            self._tint_anim.stop()
            self._tint_anim = None
        if start == end or not animate:
            self._tint_from = self._tint_to = end
            self._tint_mix = 1.0
            self.update()
            return
        self._tint_from, self._tint_to, self._tint_mix = start, end, 0.0
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
        """Recompute the painted background without a fade.

        For the two things that change what a tint LOOKS like rather than
        which tint it is: the system appearance flipping, and the material
        arriving. Fading either would be animating the wrong thing.
        """
        if self._tint_anim is not None:
            self._tint_anim.stop()
            self._tint_anim = None
        self._tint_from = self._tint_to = self._background_for(self._tint_rgb)
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
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_qcolor(self._current_background()))
        painter.drawRoundedRect(rect, _CORNER_RADIUS, _CORNER_RADIUS)

        width = 1.0 / max(1.0, self.devicePixelRatioF())
        inset = width / 2
        pen = QPen(_qcolor(self._palette.border))
        pen.setWidthF(width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            rect.adjusted(inset, inset, -inset, -inset),
            _CORNER_RADIUS - inset,
            _CORNER_RADIUS - inset,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_scale()
        self._place_buttons()
        # macOS caches the shadow's silhouette; without this a resized
        # window keeps the shadow of the shape it used to be.
        self._invalidate_shadow()

    def _apply_scale(self) -> None:
        """Fonts, margins, spacing, and button boxes track window width
        near-linearly, so everything stays visually proportional from min
        size to max."""
        scale = max(0.65, self.width() / _BASE_WIDTH)
        if abs(scale - self._scale) > 0.01:
            self._scale = scale
            self.setStyleSheet(
                _style_for(scale, self._family_stack, self._palette)
            )
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
            # Extra air above and below the sung line, on top of the row
            # gap. It is what stops the three lyric rows reading as an
            # evenly spaced list with one of them in bold.
            current_air = max(1, round(CURRENT_SPACING * scale))
            self._current_layout.setContentsMargins(0, current_air, 0, current_air)
            # How far a line travels as it is replaced, at this scale.
            self._current_fx.travel = max(1.0, LINE_TRAVEL * scale)
            self._apply_tracking()
            side = button_side(scale)
            for button in (self._loop_button, self._speak_button, self._attempt_button):
                button.setFixedSize(side, side)
            self._apply_speak_icon(side)
            self._place_buttons()

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

    def _apply_layout_margins(self) -> None:
        """Side margins reserve the button gutters (geometry.py owns the
        shared math) so wrapped text can never run under a button; during a
        sync pass the bottom margin also reserves the tap row, and the
        height floor grows with it so no window shape can bury the row."""
        scale = self._scale
        gutter = text_gutter(scale)
        syncing = self._syncing
        bottom = round(BOTTOM_MARGIN * scale) + (
            sync_bar_reserve(scale) if syncing else 0
        )
        self._layout.setContentsMargins(
            gutter, round(TOP_MARGIN * scale), gutter, bottom
        )
        # No window shape may hide the lyrics: height floor follows scale.
        self.setMinimumHeight(min_window_height(scale, sync_bar=syncing))

    def _place_buttons(self) -> None:
        margin = button_margin(self._scale)
        side = self._loop_button.width()
        self._loop_button.move(self.width() - side - margin, margin)
        self._speak_button.move(
            self.width() - side - margin, (self.height() - side) // 2
        )
        # The done-button mirrors the speaker on the left, beside the line.
        self._attempt_button.move(margin, (self.height() - side) // 2)
        self._place_sync_controls()
        for button in (
            self._loop_button,
            self._speak_button,
            self._attempt_button,
            self._tap_button,
            self._undo_button,
            self._sync_exit_button,
        ):
            button.raise_()

    def _available_geometry(self) -> QRect:
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1440, 900)

    # -- interaction: drag, resize, scroll-opacity, menu -------------------

    def _hit_edges(self, pos: QPoint) -> Qt.Edges:
        edges = Qt.Edges()
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press_global = event.globalPosition().toPoint()
        self._press_geometry = self.geometry()
        self._resize_edges = self._hit_edges(event.position().toPoint())
        if not self._resize_edges:
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
        # Height floor depends on the width the resize will land on (fonts
        # scale with width), so compute it from the clamped width.
        scale = max(0.65, width / _BASE_WIDTH)
        floor = min_window_height(scale, sync_bar=self._syncing)
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
        self._drag_offset = None
        self._resize_edges = Qt.Edges()
        if was_interacting:
            self._nudge_onscreen()
            # The window system may settle the final geometry after this
            # event; re-check once the event loop has caught up.
            QTimer.singleShot(0, self._nudge_onscreen)
            self._save_settings()

    def _nudge_onscreen(self) -> None:
        target = _clamped_point(self.frameGeometry(), self._available_geometry())
        if target != self.frameGeometry().topLeft():
            self.move(target)

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

        The same QMenu backs the menu bar item and the window's right-click
        menu, so the two cannot drift apart. Its structure never changes
        afterwards — ``_refresh_menu`` only flips visibility, check marks
        and the sync label — which keeps the native menu bar item from
        being rebuilt underneath the user.

        Checkable entries connect to ``triggered``, not ``toggled``: a
        refresh calls setChecked() on all of them, and toggled would feed
        that straight back into the setters.
        """
        self._menu = QMenu(self)
        actions = {}

        show = self._menu.addAction("Show lyrics")
        show.setCheckable(True)
        show.triggered.connect(self._set_lyrics_visible)
        actions[SHOW_LYRICS] = show

        actions[SEPARATOR_AFTER_SHOW] = self._menu.addSeparator()

        romanisation = self._menu.addAction("Romanisation")
        romanisation.setCheckable(True)
        romanisation.triggered.connect(self._set_romanisation)
        actions[ROMANISATION] = romanisation

        spoken = self._menu.addAction("Spoken reference")
        spoken.setCheckable(True)
        spoken.triggered.connect(self._set_spoken_reference)
        actions[SPOKEN] = spoken

        rate_menu = self._menu.addMenu("Speech rate")
        rate_group = QActionGroup(rate_menu)
        rate_group.setExclusive(True)
        self._rate_actions = {}
        for wpm in SPEECH_RATE_PRESETS:
            preset = rate_menu.addAction(f"{wpm} wpm")
            preset.setCheckable(True)
            rate_group.addAction(preset)
            preset.triggered.connect(
                lambda checked=False, rate=wpm: self._set_speech_rate(rate)
            )
            self._rate_actions[wpm] = preset
        actions[SPEECH_RATE] = rate_menu.menuAction()

        echo = self._menu.addAction("Echo practice")
        echo.setCheckable(True)
        echo.triggered.connect(self._set_echo_practice)
        actions[ECHO] = echo

        album_colour = self._menu.addAction("Album colour")
        album_colour.setCheckable(True)
        album_colour.triggered.connect(self._set_album_colour)
        actions[ALBUM_COLOUR] = album_colour

        all_desktops = self._menu.addAction("Show on all desktops")
        all_desktops.setCheckable(True)
        all_desktops.triggered.connect(self._set_all_desktops)
        actions[ALL_DESKTOPS] = all_desktops

        # Label swaps to name the approval case in _refresh_menu.
        open_at_login = self._menu.addAction(login_item.MENU_LABEL)
        open_at_login.setCheckable(True)
        open_at_login.triggered.connect(self._set_open_at_login)
        actions[OPEN_AT_LOGIN] = open_at_login

        # Label swaps between "Sync" and "Re-sync" in _refresh_menu.
        sync = self._menu.addAction("Sync this song")
        sync.triggered.connect(self._begin_sync)
        actions[SYNC] = sync

        actions[SEPARATOR_BEFORE_QUIT] = self._menu.addSeparator()
        # Straight to the app's quit, so the existing aboutToQuit shutdown
        # (settings saved, monitor joined) runs however quit is reached.
        actions[QUIT] = self._menu.addAction("Quit", QApplication.instance().quit)

        self._menu_actions = actions
        # Order matters: re-read the system's answer first, so the refresh
        # that follows is drawing the state the system is actually in.
        self._menu.aboutToShow.connect(self._reread_login_item)
        self._menu.aboutToShow.connect(self._refresh_menu)

    def _refresh_menu(self) -> None:
        """Bring the shared menu in line with the current state. Cheap
        enough to run on every render, so the menu bar item is already
        correct before it is opened rather than only on aboutToShow."""
        sync_label = self._view_model.sync_menu_entry(
            self._provider.has_user_sync(self._view_model.track_id)
        )
        visible = set(
            visible_entries(
                has_korean_lyrics=self._view_model.has_korean_lyrics,
                speech_available=self._speech_available,
                synced=self._view_model.display().mode is Mode.SYNCED,
                sync_offered=sync_label is not None,
                login_item_offered=login_item.offered(
                    bundled=self._bundled, status=self._login_status
                ),
            )
        )
        for key, action in self._menu_actions.items():
            action.setVisible(key in visible)
        if sync_label is not None:
            self._menu_actions[SYNC].setText(sync_label)
        self._menu_actions[SHOW_LYRICS].setChecked(self._lyrics_visible)
        self._menu_actions[ROMANISATION].setChecked(
            self._view_model.romanisation_enabled
        )
        self._menu_actions[SPOKEN].setChecked(self._spoken_enabled)
        self._menu_actions[ECHO].setChecked(self._echo_enabled)
        self._menu_actions[ALBUM_COLOUR].setChecked(self._album_colour)
        self._menu_actions[ALL_DESKTOPS].setChecked(self._all_desktops)
        # The system's answer, not ours: the tick follows what macOS says,
        # so flipping it in System Settings shows up here rather than the
        # two quietly disagreeing.
        login_action = self._menu_actions[OPEN_AT_LOGIN]
        login_action.setChecked(login_item.is_enabled(self._login_status))
        login_action.setText(login_item.label_for(self._login_status))
        for wpm, action in self._rate_actions.items():
            action.setChecked(wpm == self._speech_rate)

    def _build_tray(self) -> None:
        """The menu bar item. Its icon is a template image, so macOS tints
        it for light and dark menu bars instead of us shipping two."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.info("no system tray — menu bar item unavailable")
            self._tray = None
            return
        icon = QIcon(str(MENUBAR_ICON))
        icon.setIsMask(True)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("LyriSync")
        self._tray.setContextMenu(self._menu)
        self._tray.show()

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
        self._refresh_menu()
        self._menu.exec(event.globalPos())

    # -- settings -----------------------------------------------------------

    def _set_lyrics_visible(self, visible: bool) -> None:
        """Show or hide the lyrics window. Nothing else stops: the monitor
        thread keeps polling, an engaged loop stays engaged, and a sync pass
        in progress carries on — so showing it again picks the song up
        wherever it now is."""
        self._lyrics_visible = visible
        self.setVisible(visible)
        if visible:
            self._render()  # catch up with whatever happened while hidden
        self._settings.setValue("window/visible", visible)
        self._refresh_menu()

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
        self._palette = appearance.palette_for(resolved)
        self._apply_appearance()

    def _apply_appearance(self) -> None:
        """Repaint everything the palette owns.

        Deliberately not routed through _apply_scale: that one early-outs
        when the width has not moved, which is exactly the case here, and
        an appearance change that silently did nothing is the bug this
        whole feature is about. Everything else — geometry, fonts,
        margins, the fade timers, an engaged loop, a sync pass in
        progress — is untouched, because none of it is a colour.
        """
        self.setStyleSheet(_style_for(self._scale, self._family_stack, self._palette))
        self._apply_material_appearance()
        self._apply_speak_icon(button_side(self._scale))
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
            logger.warning("pyobjc unavailable — native window features disabled")
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
        nsview = self._nsview()
        if nsview is None:
            return False
        try:
            from AppKit import NSAppearance, NSVisualEffectView
        except ImportError:
            logger.warning("pyobjc unavailable — no vibrancy material")
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
            # scrim is painted with, so the two coincide exactly.
            effect.setWantsLayer_(True)
            effect.layer().setCornerRadius_(float(_CORNER_RADIUS))
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
        self.setWindowOpacity(self._opacity)

    def _restore_settings(self) -> None:
        try:
            opacity = float(self._settings.value("window/opacity", _DEFAULT_OPACITY))
        except (TypeError, ValueError):
            opacity = _DEFAULT_OPACITY
        self._set_opacity(opacity)
        available = self._available_geometry()
        size = self._settings.value("window/size")
        if isinstance(size, QSize):
            self.resize(size.expandedTo(_MIN_SIZE).boundedTo(available.size()))
        else:
            # Comfortably above min_window_height at scale 1.0 (183),
            # so a first run opens at a shape it chose rather than at
            # the floor it was clamped to.
            self.resize(_BASE_WIDTH, 200)
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
        self._lyrics_visible = self._settings.value("window/visible", True, type=bool)
        # Open at Login is NOT restored from here: the stored value is what
        # the user last asked for, and the system is what is actually true.
        # Reading it back is the whole point, so the setting is only ever
        # compared against reality — loudly, when they disagree.
        wanted = self._settings.value("window/open_at_login", False, type=bool)
        if self._bundled and wanted != login_item.is_enabled(self._login_status):
            logger.info(
                "Open at Login was last set to %s here but macOS says %s — "
                "following macOS",
                wanted,
                self._login_status.value,
            )

    def _save_settings(self) -> None:
        self._settings.setValue("window/pos", self.pos())
        self._settings.setValue("window/size", self.size())
        self._settings.setValue("window/opacity", self._opacity)
        self._settings.setValue("window/all_desktops", self._all_desktops)
        self._settings.setValue("window/album_colour", self._album_colour)
        self._settings.setValue("window/visible", self._lyrics_visible)

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
        self._save_settings()
        self._monitor_thread.stop()
        # Poll may be mid-osascript (up to its 2s timeout).
        if not self._monitor_thread.wait(_SHUTDOWN_WAIT_MS):
            logger.warning("monitor thread did not stop in time")
        # Queued work that has not started yet is simply dropped: a fetch
        # or a save that has not begun has nothing to finish.
        self._pool.clear()
        if not self._pool.waitForDone(_SHUTDOWN_WAIT_MS):
            logger.warning("worker still running at shutdown (%d active)",
                           self._pool.activeThreadCount())


def apply_accessory_policy() -> None:
    """Run as a menu bar accessory: no Dock icon, no Cmd-Tab entry.

    Unconditional and permanent. A Regular-policy app owns a Space, so
    activating it from inside another app's full-screen Space makes macOS
    switch there instead of overlaying — which no collection-behavior flag
    can undo. Must be in force before the window is first shown, or macOS
    may still treat that show as a regular-app activation. With no Dock
    icon, Quit lives in the menu bar item (and SIGINT).
    """
    if QApplication.platformName() != "cocoa":
        return
    try:
        from AppKit import NSApplication
    except ImportError:
        logger.warning("pyobjc unavailable — activation policy unchanged")
        return
    try:
        shared = NSApplication.sharedApplication()
        shared.setActivationPolicy_(ACTIVATION_POLICY_ACCESSORY)
        logger.debug(
            "activation policy -> accessory (readback=%d)",
            int(shared.activationPolicy()),
        )
    except Exception:
        logger.exception("failed to set activation policy")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LYRISYNC_LOG", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("lyrisync")
    app.setOrganizationName("lyrisync")
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
