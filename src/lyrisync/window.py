"""Floating always-on-top lyrics window — the main lyrisync app.

Run with ``lyrisync``. Spotify polling runs on a worker QThread that emits
snapshots to the UI through queued signals, and LRCLIB fetches run on the
global QThreadPool — the UI thread never runs a subprocess and never blocks
on the network. All display decisions live in ``view_model.LyricsViewModel``;
this module only renders them.
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
    QParallelAnimationGroup,
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
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from lyrisync.lyrics_provider import LyricsError, LyricsProvider
from lyrisync.player_monitor import PlayerMonitor, PlayerSnapshot
from lyrisync.view_model import LyricsViewModel, Mode

logger = logging.getLogger(__name__)

_BASE_WIDTH = 460
_MIN_SIZE = QSize(260, 120)
_MAX_SIZE = QSize(1400, 900)
_CORNER_RADIUS = 14
_RESIZE_MARGIN = 8
_GRAB_MARGIN = 40  # px of window that must stay on-screen after a drag

_MIN_OPACITY = 0.25
_MAX_OPACITY = 1.0
_DEFAULT_OPACITY = 0.92
# Full min→max travel: ~37 wheel notches or ~940 trackpad pixels.
_OPACITY_PER_WHEEL_NOTCH = 0.02
_OPACITY_PER_SCROLL_PIXEL = 0.0008

_FADE_MS = 180
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


class FadeLabel(QWidget):
    """Word-wrapping, centred label that cross-fades between texts: the old
    text fades out while the new fades in over ~180ms. The new text is set
    at animation start, so a synced line always appears on time."""

    def __init__(self, object_name: str) -> None:
        super().__init__()
        self._front = self._make(object_name)
        self._back = self._make(object_name)
        stack = QStackedLayout(self)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self._back)
        stack.addWidget(self._front)
        self._front_fx = QGraphicsOpacityEffect(self._front)
        self._back_fx = QGraphicsOpacityEffect(self._back)
        self._front.setGraphicsEffect(self._front_fx)
        self._back.setGraphicsEffect(self._back_fx)
        self._front_fx.setOpacity(1.0)
        self._back_fx.setOpacity(0.0)
        self._animation: Optional[QParallelAnimationGroup] = None
        self._text = ""

    @staticmethod
    def _make(object_name: str) -> QLabel:
        label = QLabel()
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        label.setWordWrap(True)
        return label

    def setText(self, text: str, animate: bool = True) -> None:
        if text == self._text:
            return
        self._text = text
        if self._animation is not None:
            self._animation.stop()
            self._finish_swap()
        if not animate or not self.isVisible():
            self._front.setText(text)
            return
        self._back.setText(text)
        group = QParallelAnimationGroup(self)
        for effect, end in ((self._front_fx, 0.0), (self._back_fx, 1.0)):
            fade = QPropertyAnimation(effect, b"opacity")
            fade.setDuration(_FADE_MS)
            fade.setEndValue(end)
            group.addAnimation(fade)
        group.finished.connect(self._on_fade_done)
        self._animation = group
        group.start()

    def _on_fade_done(self) -> None:
        self._animation = None
        self._finish_swap()

    def _finish_swap(self) -> None:
        # Back (new text) becomes front; the hidden label is cleared so the
        # layout's height tracks only the visible text.
        self._front, self._back = self._back, self._front
        self._front_fx, self._back_fx = self._back_fx, self._front_fx
        self._front.setText(self._text)
        self._front_fx.setOpacity(1.0)
        self._back.setText("")
        self._back_fx.setOpacity(0.0)


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
        self._title_card_until = 0.0
        self._dots_frame = 0
        self._scale = 0.0
        self._all_desktops = False
        self._native_applied = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(_MIN_SIZE)
        self.setMaximumSize(_MAX_SIZE)
        self.setMouseTracking(True)

        self._header = QLabel()
        self._header.setObjectName("header")
        self._header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._header.setWordWrap(True)
        self._previous = FadeLabel("dim")
        self._current = FadeLabel("current")
        self._upcoming = FadeLabel("dim")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(6)
        layout.addStretch(1)
        for widget in (self._header, self._previous, self._current, self._upcoming):
            layout.addWidget(widget)
        layout.addStretch(1)

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

    # -- monitor slots (UI thread, queued from MonitorThread) --------------

    def _on_track_change(self, snapshot: PlayerSnapshot) -> None:
        self._current_snapshot = snapshot if snapshot.is_music_track else None
        if snapshot.has_track:
            self._title_card_until = time.monotonic() + _TITLE_CARD_SECONDS
            QTimer.singleShot(int(_TITLE_CARD_SECONDS * 1000) + 100, self._render)
        if self._view_model.track_changed(snapshot):
            self._start_fetch(snapshot)
        self._render()

    def _on_fetch_finished(self, track_id: str, lyrics: object, ok: bool) -> None:
        # Stale results (track changed while the fetch was in flight) are
        # rejected by the view model; the provider already cached them.
        if self._view_model.fetch_completed(track_id, lyrics, ok, now=time.monotonic()):
            self._render()

    def _on_position_update(self, snapshot: PlayerSnapshot) -> None:
        if self._view_model.position_changed(snapshot.position_seconds):
            self._render()

    def _on_state_change(self, snapshot: PlayerSnapshot) -> None:
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
        if self._view_model.display().mode is Mode.FETCHING:
            self._dots_frame = (self._dots_frame + 1) % len(_DOTS_FRAMES)
            self._current.setText(_DOTS_FRAMES[self._dots_frame], animate=False)

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        display = self._view_model.display()

        if (
            display.header
            and display.mode is not Mode.IDLE
            and time.monotonic() < self._title_card_until
        ):
            # Title card: the song announces itself before lyrics start.
            self._header.setVisible(False)
            self._previous.setText("", animate=False)
            self._current.setText(display.header)
            self._upcoming.setText("", animate=False)
            return

        current = display.current
        animate_current = True
        if display.mode is Mode.PLAIN:
            current = self._cap_plain(display.plain_text)
        elif display.mode is Mode.FETCHING:
            current = _DOTS_FRAMES[self._dots_frame]
            animate_current = False
        self._header.setText(display.header)
        self._header.setVisible(bool(display.header))
        self._previous.setText(display.previous)
        self._current.setText(current, animate=animate_current)
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
        """Font sizes track window width."""
        scale = max(0.7, min(2.2, self.width() / _BASE_WIDTH))
        if abs(scale - self._scale) > 0.01:
            self._scale = scale
            self.setStyleSheet(_style_for(scale))

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

        width = max(_MIN_SIZE.width(), min(_MAX_SIZE.width(), rect.width()))
        height = max(_MIN_SIZE.height(), min(_MAX_SIZE.height(), rect.height()))
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
        if self._drag_offset is not None:
            self._nudge_onscreen()
        self._drag_offset = None
        self._resize_edges = Qt.Edges()
        self._save_settings()

    def _nudge_onscreen(self) -> None:
        """After a drag, keep at least a ~40px grab area visible. Free
        placement otherwise — no snapping or alignment."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        x, y = frame.x(), frame.y()
        if frame.right() < available.left() + _GRAB_MARGIN:
            x = available.left() + _GRAB_MARGIN - frame.width()
        elif frame.left() > available.right() - _GRAB_MARGIN:
            x = available.right() - _GRAB_MARGIN
        if frame.bottom() < available.top() + _GRAB_MARGIN:
            y = available.top() + _GRAB_MARGIN - frame.height()
        elif frame.top() > available.bottom() - _GRAB_MARGIN:
            y = available.bottom() - _GRAB_MARGIN
        if (x, y) != (frame.x(), frame.y()):
            self.move(x, y)

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

    def _apply_all_desktops(self, enabled: bool) -> None:
        """Set NSWindowCollectionBehaviorCanJoinAllSpaces on the native
        window. Qt has no cross-platform API for Spaces, hence pyobjc."""
        if QApplication.platformName() != "cocoa":
            # winId() is only an NSView under the cocoa platform plugin;
            # casting it blindly (e.g. offscreen in tests) would crash.
            return
        try:
            import objc
            from AppKit import NSWindowCollectionBehaviorCanJoinAllSpaces
        except ImportError:
            logger.warning("pyobjc unavailable — 'show on all desktops' disabled")
            return
        try:
            view = objc.objc_object(c_void_p=int(self.winId()))
            nswindow = view.window()
            if nswindow is None:
                return
            behavior = int(nswindow.collectionBehavior())
            if enabled:
                behavior |= int(NSWindowCollectionBehaviorCanJoinAllSpaces)
            else:
                behavior &= ~int(NSWindowCollectionBehaviorCanJoinAllSpaces)
            nswindow.setCollectionBehavior_(behavior)
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
        size = self._settings.value("window/size")
        if isinstance(size, QSize):
            self.resize(size.expandedTo(_MIN_SIZE).boundedTo(_MAX_SIZE))
        else:
            self.resize(_BASE_WIDTH, 170)
        position = self._settings.value("window/pos")
        if isinstance(position, QPoint):
            self.move(position)
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
