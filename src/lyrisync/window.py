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
from typing import Optional

from PySide6.QtCore import (
    QObject,
    QPoint,
    QRunnable,
    QSettings,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QVBoxLayout, QWidget

from lyrisync.lyrics_provider import LyricsError, LyricsProvider
from lyrisync.player_monitor import PlayerMonitor, PlayerSnapshot
from lyrisync.view_model import LyricsViewModel, Mode

logger = logging.getLogger(__name__)
_WINDOW_WIDTH = 460
_CORNER_RADIUS = 14
_MIN_OPACITY = 0.25
_MAX_OPACITY = 1.0
_OPACITY_STEP = 0.05
_DEFAULT_OPACITY = 0.92
_MAX_PLAIN_LINES = 12

_STYLE = """
QLabel { background: transparent; }
QLabel#header { color: rgba(255, 255, 255, 120); font-size: 11px; }
QLabel#dim { color: rgba(255, 255, 255, 115); font-size: 14px; }
QLabel#current { color: rgba(255, 255, 255, 235); font-size: 17px; font-weight: 600; }
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


class LyricsWindow(QWidget):
    def __init__(self, provider: Optional[LyricsProvider] = None) -> None:
        super().__init__()
        self._provider = provider or LyricsProvider()
        self._view_model = LyricsViewModel()
        self._pool = QThreadPool.globalInstance()
        self._drag_offset: Optional[QPoint] = None
        self._settings = QSettings("lyrisync", "lyrisync")

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(_WINDOW_WIDTH)
        self.setStyleSheet(_STYLE)

        self._header = self._make_label("header")
        self._previous = self._make_label("dim")
        self._current = self._make_label("current")
        self._upcoming = self._make_label("dim")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(6)
        for label in (self._header, self._previous, self._current, self._upcoming):
            layout.addWidget(label)

        self._restore_settings()
        QApplication.instance().aboutToQuit.connect(self._shutdown)

        self._monitor_thread = MonitorThread(self)
        self._monitor_thread.track_changed.connect(self._on_track_change)
        self._monitor_thread.position_updated.connect(self._on_position_update)
        self._monitor_thread.state_changed.connect(self._on_state_change)
        self._monitor_thread.start()

        self._render()

    def _make_label(self, object_name: str) -> QLabel:
        label = QLabel()
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        label.setWordWrap(True)
        return label

    # -- monitor slots (UI thread, queued from MonitorThread) --------------

    def _on_track_change(self, snapshot: PlayerSnapshot) -> None:
        if self._view_model.track_changed(snapshot):
            task = FetchTask(self._provider, snapshot)
            task.signals.finished.connect(self._on_fetch_finished)
            self._pool.start(task)
        self._render()

    def _on_fetch_finished(self, track_id: str, lyrics: object, ok: bool) -> None:
        # Stale results (track changed while the fetch was in flight) are
        # rejected by the view model; the provider already cached them.
        if self._view_model.fetch_completed(track_id, lyrics, ok):
            self._render()

    def _on_position_update(self, snapshot: PlayerSnapshot) -> None:
        if self._view_model.position_changed(snapshot.position_seconds):
            self._render()

    def _on_state_change(self, snapshot: PlayerSnapshot) -> None:
        if self._view_model.player_state_changed(snapshot.state):
            self._render()

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        display = self._view_model.display()
        current = display.current
        if display.mode is Mode.PLAIN:
            current = self._cap_plain(display.plain_text)
        self._header.setText(display.header)
        self._header.setVisible(bool(display.header))
        self._previous.setText(display.previous)
        self._current.setText(current)
        self._upcoming.setText(display.upcoming)
        self.adjustSize()

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

    # -- interaction -------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_settings()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            step = _OPACITY_STEP if delta > 0 else -_OPACITY_STEP
            self._set_opacity(self._opacity + step)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("Quit", QApplication.instance().quit)
        menu.exec(event.globalPos())

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
        position = self._settings.value("window/pos")
        if isinstance(position, QPoint):
            self.move(position)

    def _save_settings(self) -> None:
        self._settings.setValue("window/pos", self.pos())
        self._settings.setValue("window/opacity", self._opacity)

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
