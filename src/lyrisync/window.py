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
from typing import Optional

from PySide6.QtCore import (
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRunnable,
    QSettings,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from lyrisync.geometry import clamped_position
from lyrisync.lyrics_provider import LyricsError, LyricsProvider
from lyrisync.macspaces import STATUS_WINDOW_LEVEL, all_desktops_behavior
from lyrisync.player_monitor import PlaybackState, PlayerMonitor, PlayerSnapshot
from lyrisync.view_model import LyricsViewModel, Mode

logger = logging.getLogger(__name__)

_BASE_WIDTH = 460
_MIN_SIZE = QSize(260, 120)
_CORNER_RADIUS = 14
_RESIZE_MARGIN = 8

_MIN_OPACITY = 0.25
_MAX_OPACITY = 1.0
_DEFAULT_OPACITY = 0.92
# Full min→max travel: ~37 wheel notches or ~940 trackpad pixels.
_OPACITY_PER_WHEEL_NOTCH = 0.02
_OPACITY_PER_SCROLL_PIXEL = 0.0008

# Anticipatory line fade: the old line fades out over [ts-200, ts-100],
# the new line swaps in at ts-100 and its fade-in completes AT ts, so it
# is fully legible at its timestamp and never late.
_FADE_OUT_LEAD_MS = 200
_SWAP_LEAD_MS = 100
_FADE_MS = 100

_TITLE_CARD_SECONDS = 2.0
_DOTS_FRAMES = ["·", "· ·", "· · ·"]
_MAX_PLAIN_LINES = 12
_RETRY_TICK_MS = 1000


def _style_for(scale: float) -> str:
    return f"""
QLabel {{ background: transparent; }}
QLabel#header {{ color: rgba(255, 255, 255, 120); font-size: {round(11 * scale)}px; }}
QLabel#dim {{ color: rgba(255, 255, 255, 115); font-size: {round(14 * scale)}px; }}
QLabel#current {{ color: rgba(255, 255, 255, 235); font-size: {round(17 * scale)}px; font-weight: 600; }}
"""


def _clamped_point(frame: QRect, available: QRect) -> QPoint:
    x, y = clamped_position(
        (frame.x(), frame.y(), frame.width(), frame.height()),
        (available.x(), available.y(), available.width(), available.height()),
    )
    return QPoint(x, y)


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
        try:
            lyrics = self._provider.get_lyrics(self._snapshot)
        except LyricsError:
            logger.exception("lyrics fetch failed for %s", track_id)
            self.signals.finished.emit(track_id, None, False)
            return
        except Exception:
            logger.exception("unexpected error fetching lyrics for %s", track_id)
            self.signals.finished.emit(track_id, None, False)
            return
        self.signals.finished.emit(track_id, lyrics, True)


class LyricsWindow(QWidget):
    def __init__(self, provider: Optional[LyricsProvider] = None) -> None:
        super().__init__()
        self._provider = provider or LyricsProvider()
        self._view_model = LyricsViewModel()
        self._pool = QThreadPool.globalInstance()
        self._settings = QSettings("lyrisync", "lyrisync")

        self._drag_offset: Optional[QPoint] = None
        self._resize_edges = Qt.Edges()
        self._press_geometry = QRect()
        self._press_global = QPoint()
        self._current_snapshot: Optional[PlayerSnapshot] = None
        self._last_state = PlaybackState.NOT_RUNNING
        self._title_card_until = 0.0
        self._card_key: Optional[tuple] = None
        self._dots_frame = 0
        self._scale = 0.0
        self._all_desktops = False
        self._native_applied = False
        # NSWindow (behavior, level) as Qt configured it, captured before
        # the first enable so disabling restores Qt's exact defaults.
        self._saved_native: Optional[tuple[int, int]] = None
        self._displayed_index: Optional[int] = None
        self._fade_anim: Optional[QPropertyAnimation] = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(_MIN_SIZE)
        self.setMouseTracking(True)

        self._header = self._make_label("header")
        self._previous = self._make_label("dim")
        self._current = self._make_label("current")
        self._upcoming = self._make_label("dim")
        self._current_fx = QGraphicsOpacityEffect(self._current)
        self._current_fx.setOpacity(1.0)
        self._current.setGraphicsEffect(self._current_fx)

        self._layout = QVBoxLayout(self)
        self._layout.addWidget(self._header)
        self._layout.addStretch(1)
        for widget in (self._previous, self._current, self._upcoming):
            self._layout.addWidget(widget)
        self._layout.addStretch(1)

        self._fadeout_timer = QTimer(self)
        self._fadeout_timer.setSingleShot(True)
        self._fadeout_timer.timeout.connect(self._begin_fade_out)
        self._swap_timer = QTimer(self)
        self._swap_timer.setSingleShot(True)
        self._swap_timer.timeout.connect(self._predicted_swap)

        self._restore_settings()
        self._apply_scale()
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

    # -- monitor slots (UI thread, queued from MonitorThread) --------------

    def _on_track_change(self, snapshot: PlayerSnapshot) -> None:
        self._last_state = snapshot.state
        self._current_snapshot = snapshot if snapshot.is_music_track else None
        if snapshot.has_track and snapshot.track_key != self._card_key:
            self._card_key = snapshot.track_key
            self._title_card_until = time.monotonic() + _TITLE_CARD_SECONDS
            QTimer.singleShot(int(_TITLE_CARD_SECONDS * 1000) + 100, self._render)
        elif not snapshot.has_track:
            self._card_key = None
        if self._view_model.track_changed(snapshot):
            self._start_fetch(snapshot)
        self._render()

    def _on_fetch_finished(self, track_id: str, lyrics: object, ok: bool) -> None:
        # Stale results (track changed while the fetch was in flight) are
        # rejected by the view model; the provider already cached them.
        if self._view_model.fetch_completed(track_id, lyrics, ok, now=time.monotonic()):
            self._render()

    def _on_position_update(self, snapshot: PlayerSnapshot) -> None:
        self._last_state = snapshot.state
        self._view_model.position_changed(snapshot.position_seconds)
        timeline = self._view_model.timeline()
        if timeline is None:
            return
        lines, index = timeline
        if self._displayed_index != index:
            # Seek, pause-drift correction, or a missed prediction: snap.
            self._render()
        if (
            snapshot.state is PlaybackState.PLAYING
            and snapshot.position_seconds is not None
        ):
            self._schedule_line_advance(lines, index, snapshot.position_seconds)
        else:
            self._cancel_line_schedule()

    def _on_state_change(self, snapshot: PlayerSnapshot) -> None:
        self._last_state = snapshot.state
        if snapshot.state is not PlaybackState.PLAYING:
            self._cancel_line_schedule()
        if self._view_model.player_state_changed(snapshot.state):
            self._render()

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
        self._fadeout_timer.start(max(0, eta_ms - _FADE_OUT_LEAD_MS))
        self._swap_timer.start(max(0, eta_ms - _SWAP_LEAD_MS))

    def _begin_fade_out(self) -> None:
        if self._view_model.timeline() is None or self._card_active():
            return
        if self._last_state is PlaybackState.PLAYING:
            self._animate_current_opacity(0.0)

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
        self._current_fx.setOpacity(0.0)
        self._animate_current_opacity(1.0)

    def _animate_current_opacity(self, end: float) -> None:
        if self._fade_anim is not None:
            self._fade_anim.stop()
        animation = QPropertyAnimation(self._current_fx, b"opacity", self)
        animation.setDuration(_FADE_MS)
        animation.setEndValue(end)
        animation.start()
        self._fade_anim = animation

    def _cancel_line_schedule(self) -> None:
        self._fadeout_timer.stop()
        self._swap_timer.stop()
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim = None
        self._current_fx.setOpacity(1.0)

    def _set_lines(self, lines: list, index: int) -> None:
        self._previous.setText(lines[index - 1][1] if index >= 1 else "")
        self._current.setText(lines[index][1] if index >= 0 else "")
        self._upcoming.setText(lines[index + 1][1] if index + 1 < len(lines) else "")

    # -- rendering ---------------------------------------------------------

    def _card_active(self) -> bool:
        return time.monotonic() < self._title_card_until

    def _render(self) -> None:
        display = self._view_model.display()
        self._cancel_line_schedule()

        # Persistent compact header whenever a track is known.
        self._header.setText(display.header)
        self._header.setVisible(bool(display.header))

        if display.header and display.mode is not Mode.IDLE and self._card_active():
            # Title card: the song announces itself before lyrics start.
            self._displayed_index = None
            self._previous.setText("")
            self._current.setText(display.header)
            self._upcoming.setText("")
            return

        if display.mode is Mode.SYNCED:
            timeline = self._view_model.timeline()
            if timeline is not None:
                lines, index = timeline
                self._set_lines(lines, index)
                self._displayed_index = index
            return

        self._displayed_index = None
        current = display.current
        if display.mode is Mode.PLAIN:
            current = self._cap_plain(display.plain_text)
        elif display.mode is Mode.FETCHING:
            current = _DOTS_FRAMES[self._dots_frame]
        self._previous.setText(display.previous)
        self._current.setText(current)
        self._upcoming.setText(display.upcoming)

    @staticmethod
    def _cap_plain(text: str) -> str:
        lines = text.splitlines()
        if len(lines) > _MAX_PLAIN_LINES:
            return "\n".join(lines[:_MAX_PLAIN_LINES]) + "\n…"
        return text

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 18, 24, 232))
        painter.drawRoundedRect(self.rect(), _CORNER_RADIUS, _CORNER_RADIUS)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_scale()

    def _apply_scale(self) -> None:
        """Fonts, margins, and spacing track window width near-linearly, so
        text mass stays visually proportional from min size to max."""
        scale = max(0.65, self.width() / _BASE_WIDTH)
        if abs(scale - self._scale) > 0.01:
            self._scale = scale
            self.setStyleSheet(_style_for(scale))
            self._layout.setContentsMargins(
                round(20 * scale), round(14 * scale), round(20 * scale), round(16 * scale)
            )
            self._layout.setSpacing(round(6 * scale))

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
        height = max(_MIN_SIZE.height(), min(maximum.height(), rect.height()))
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
        pixel_delta = event.pixelDelta().y()
        if pixel_delta:  # trackpad: fine-grained pixel scrolling
            step = pixel_delta * _OPACITY_PER_SCROLL_PIXEL
        else:  # mouse wheel: 120 units per notch
            step = (event.angleDelta().y() / 120.0) * _OPACITY_PER_WHEEL_NOTCH
        if step:
            self._set_opacity(self._opacity + step)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        all_desktops = menu.addAction("Show on all desktops")
        all_desktops.setCheckable(True)
        all_desktops.setChecked(self._all_desktops)
        all_desktops.toggled.connect(self._set_all_desktops)
        menu.addSeparator()
        menu.addAction("Quit", QApplication.instance().quit)
        menu.exec(event.globalPos())

    # -- all-desktops (native NSWindow collection behaviour) ---------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._native_applied:
            self._native_applied = True
            self._apply_all_desktops(self._all_desktops)

    def _set_all_desktops(self, enabled: bool) -> None:
        self._all_desktops = enabled
        self._apply_all_desktops(enabled)
        self._save_settings()

    def _nswindow(self):
        """The native NSWindow, or None off-cocoa / without pyobjc."""
        if QApplication.platformName() != "cocoa":
            # winId() is only an NSView under the cocoa platform plugin;
            # casting it blindly (e.g. offscreen in tests) would crash.
            return None
        try:
            import objc
        except ImportError:
            logger.warning("pyobjc unavailable — 'show on all desktops' disabled")
            return None
        try:
            view = objc.objc_object(c_void_p=int(self.winId()))
            return view.window()
        except Exception:
            logger.exception("failed to resolve NSWindow")
            return None

    def _apply_all_desktops(self, enabled: bool) -> None:
        """All-desktops toggle on the native window: CanJoinAllSpaces +
        FullScreenAuxiliary with Qt's conflicting FullScreenPrimary bit
        cleared (Primary wins over Auxiliary and blocks full-screen
        Spaces), at status window level so the overlay stays above
        full-screen content. Disabling restores Qt's saved defaults. Qt
        has no cross-platform API for Spaces, hence pyobjc."""
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
            self.resize(_BASE_WIDTH, 170)
        position = self._settings.value("window/pos")
        if isinstance(position, QPoint):
            self.move(_clamped_point(QRect(position, self.size()), available))
        self._all_desktops = self._settings.value(
            "window/all_desktops", False, type=bool
        )

    def _save_settings(self) -> None:
        self._settings.setValue("window/pos", self.pos())
        self._settings.setValue("window/size", self.size())
        self._settings.setValue("window/opacity", self._opacity)
        self._settings.setValue("window/all_desktops", self._all_desktops)

    def _shutdown(self) -> None:
        self._save_settings()
        self._monitor_thread.stop()
        # Poll may be mid-osascript (up to its 2s timeout).
        self._monitor_thread.wait(3000)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LYRISYNC_LOG", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("lyrisync")
    app.setOrganizationName("lyrisync")

    # Ctrl-C: Python signal handlers only run while the interpreter is
    # executing bytecode, so an idle Qt event loop would never deliver
    # SIGINT — the timer wakes the interpreter periodically to let it in.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    interrupt_timer = QTimer()
    interrupt_timer.timeout.connect(lambda: None)
    interrupt_timer.start(200)

    window = LyricsWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
