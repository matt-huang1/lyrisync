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
from PySide6.QtGui import QActionGroup, QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lyrisync.geometry import (
    button_margin,
    button_side,
    clamped_position,
    min_window_height,
    text_gutter,
)
from lyrisync.loop import LineLoop, LoopPhase
from lyrisync.lyrics_provider import LyricsError, LyricsProvider
from lyrisync.macspaces import (
    STATUS_WINDOW_LEVEL,
    activation_policy_for,
    all_desktops_behavior,
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
QLabel#pron {{ color: rgba(255, 255, 255, 165); font-size: {round(12 * scale)}px; }}
QPushButton#loop, QPushButton#speak {{
    color: rgba(255, 255, 255, 90); background: transparent; border: none;
    font-size: {round(15 * scale)}px;
}}
QPushButton#loop:hover, QPushButton#speak:hover {{ color: rgba(255, 255, 255, 190); }}
QPushButton#loop:checked {{ color: rgba(130, 200, 255, 235); }}
QPushButton#speak:disabled {{ color: rgba(130, 200, 255, 235); }}
QPushButton#attempt {{
    color: rgba(255, 214, 120, 240); border: none;
    background: rgba(255, 214, 120, 28); border-radius: {round(6 * scale)}px;
    font-size: {round(15 * scale)}px;
}}
QPushButton#attempt:hover {{ background: rgba(255, 214, 120, 60); }}
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
        # anticipatory fade covers both with a single opacity effect.
        self._current_box = QWidget()
        current_layout = QVBoxLayout(self._current_box)
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_layout.setSpacing(2)
        current_layout.addWidget(self._current)
        current_layout.addWidget(self._pron)
        self._current_fx = QGraphicsOpacityEffect(self._current_box)
        self._current_fx.setOpacity(1.0)
        self._current_box.setGraphicsEffect(self._current_fx)

        self._layout = QVBoxLayout(self)
        self._layout.addWidget(self._header)
        self._layout.addStretch(1)
        for widget in (self._previous, self._current_box, self._upcoming):
            self._layout.addWidget(widget)
        self._layout.addStretch(1)

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
            "attempt", "🎤", "Done — play the line again"
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
            "speak", "🔊", "Speak this line"
        )
        self._speak_button.clicked.connect(self._on_speak_clicked)

        self._restore_settings()
        self._apply_scale()
        if self._all_desktops:
            # Persisted-on startup: accessory policy must be in force
            # before the window first shows, or macOS may still treat the
            # first show as a regular-app activation.
            self._apply_activation_policy(True)
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
            self._release_loop()  # lyrics changed under the loop
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
        if self._view_model.player_state_changed(snapshot.state):
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

        # Loop button only where looping is possible (synced timestamps).
        self._loop_button.setVisible(display.mode is Mode.SYNCED)
        self._attempt_button.setVisible(
            display.mode is Mode.SYNCED
            and self._loop.engaged
            and self._loop.phase is LoopPhase.ATTEMPT
        )
        if display.mode is not Mode.SYNCED:
            self._speak_button.setVisible(False)  # synced path updates it per line

        # Persistent compact header whenever a track is known.
        self._header.setText(display.header)
        self._header.setVisible(bool(display.header))

        if display.header and display.mode is not Mode.IDLE and self._card_active():
            # Title card: the song announces itself before lyrics start.
            self._displayed_index = None
            self._previous.setText("")
            self._current.setText(display.header)
            self._set_pronunciation("")
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
        self._set_pronunciation(display.pronunciation)
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
        self._place_buttons()

    def _apply_scale(self) -> None:
        """Fonts, margins, spacing, and button boxes track window width
        near-linearly, so everything stays visually proportional from min
        size to max."""
        scale = max(0.65, self.width() / _BASE_WIDTH)
        if abs(scale - self._scale) > 0.01:
            self._scale = scale
            self.setStyleSheet(_style_for(scale))
            # Side margins reserve the button gutters (geometry.py owns the
            # shared math), so wrapped text can never run under a button.
            gutter = text_gutter(scale)
            self._layout.setContentsMargins(
                gutter, round(14 * scale), gutter, round(16 * scale)
            )
            self._layout.setSpacing(round(6 * scale))
            side = button_side(scale)
            for button in (self._loop_button, self._speak_button, self._attempt_button):
                button.setFixedSize(side, side)
            # No window shape may hide the lyrics: height floor follows scale.
            self.setMinimumHeight(min_window_height(scale))
            self._place_buttons()

    def _place_buttons(self) -> None:
        margin = button_margin(self._scale)
        side = self._loop_button.width()
        self._loop_button.move(self.width() - side - margin, margin)
        self._speak_button.move(
            self.width() - side - margin, (self.height() - side) // 2
        )
        # The done-button mirrors the speaker on the left, beside the line.
        self._attempt_button.move(margin, (self.height() - side) // 2)
        for button in (self._loop_button, self._speak_button, self._attempt_button):
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
        height = max(min_window_height(scale), min(maximum.height(), rect.height()))
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
        if self._view_model.has_korean_lyrics:
            romanisation = menu.addAction("Romanisation")
            romanisation.setCheckable(True)
            romanisation.setChecked(self._view_model.romanisation_enabled)
            romanisation.toggled.connect(self._set_romanisation)
        if self._view_model.display().mode is Mode.SYNCED:
            echo = menu.addAction("Echo practice")
            echo.setCheckable(True)
            echo.setChecked(self._echo_enabled)
            echo.toggled.connect(self._set_echo_practice)
        if self._speech_available:
            spoken = menu.addAction("Spoken reference")
            spoken.setCheckable(True)
            spoken.setChecked(self._spoken_enabled)
            spoken.toggled.connect(self._set_spoken_reference)
            rate_menu = menu.addMenu("Speech rate")
            rate_group = QActionGroup(rate_menu)
            rate_group.setExclusive(True)
            for wpm in SPEECH_RATE_PRESETS:
                preset = rate_menu.addAction(f"{wpm} wpm")
                preset.setCheckable(True)
                preset.setChecked(wpm == self._speech_rate)
                rate_group.addAction(preset)
                preset.triggered.connect(
                    lambda checked=False, rate=wpm: self._set_speech_rate(rate)
                )
        menu.addSeparator()
        menu.addAction("Quit", QApplication.instance().quit)
        menu.exec(event.globalPos())

    def _set_romanisation(self, enabled: bool) -> None:
        self._view_model.romanisation_enabled = enabled
        self._settings.setValue("lyrics/romanisation", enabled)
        self._render()

    def _set_spoken_reference(self, enabled: bool) -> None:
        self._spoken_enabled = enabled
        self._settings.setValue("lyrics/spoken_reference", enabled)
        self._update_speak_button()

    def _set_speech_rate(self, rate: int) -> None:
        self._speech_rate = rate
        self._settings.setValue("lyrics/speech_rate", rate)

    def _set_echo_practice(self, enabled: bool) -> None:
        self._echo_enabled = enabled
        self._loop.echo = enabled
        self._settings.setValue("lyrics/echo_practice", enabled)

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

    def _apply_activation_policy(self, enabled: bool) -> None:
        """Accessory policy while the toggle is on: a regular app owns a
        Space, so any activation from inside a full-screen Space makes
        macOS switch there instead of overlaying. Accessory removes the
        Dock icon and Cmd-Tab entry; Quit stays in the context menu and
        SIGINT. Needs no native window, so it can run before first show."""
        if QApplication.platformName() != "cocoa":
            return
        try:
            from AppKit import NSApplication
        except ImportError:
            logger.warning("pyobjc unavailable — activation policy unchanged")
            return
        try:
            shared = NSApplication.sharedApplication()
            shared.setActivationPolicy_(activation_policy_for(enabled))
            logger.debug(
                "activation policy -> %s (readback=%d)",
                "accessory" if enabled else "regular",
                int(shared.activationPolicy()),
            )
        except Exception:
            logger.exception("failed to set activation policy")

    def _apply_all_desktops(self, enabled: bool) -> None:
        """All-desktops toggle: accessory activation policy plus native
        window flags — CanJoinAllSpaces + FullScreenAuxiliary with Qt's
        conflicting FullScreenPrimary bit cleared (Primary wins over
        Auxiliary and blocks full-screen Spaces), at status window level
        so the overlay stays above full-screen content. Disabling restores
        Qt's saved defaults. Qt has no cross-platform API for Spaces,
        hence pyobjc."""
        self._apply_activation_policy(enabled)
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
