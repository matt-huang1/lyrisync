"""Window behaviour that needs a real Qt object tree: the shared settings
menu, the menu bar item, window visibility, and shutdown.

Everything testable without Qt lives in the pure modules (menu.py,
view_model.py, geometry.py) and is covered there. These fill the gap that
cannot be made pure: signal wiring, QSettings round-trips, the tray.

They run everywhere, on the offscreen platform — nothing here is
macOS-only, because everything native (Cocoa collection behaviour, the
activation policy) is guarded off-cocoa in the code under test and is
asserted structurally rather than by calling into AppKit. CI installs the
system libraries PySide6 needs; the import guard below only catches the
case where that has gone wrong, so a broken runner degrades to a visible
skip instead of a collection error.
"""

import logging
import os
import threading
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# exc_type=ImportError, not the default ModuleNotFoundError: PySide6 imports
# fine with its shared libraries missing and fails later on "libEGL.so.1:
# cannot open shared object file", which is an ImportError but not a
# ModuleNotFoundError.
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="PySide6 unusable (missing system Qt libraries?)",
    exc_type=ImportError,
)

from PySide6.QtCore import (  # noqa: E402
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QRunnable,
    QSettings,
    Qt,
    QTimer,
)
from PySide6.QtGui import QAction, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

try:
    APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - platform plugin missing
    pytest.skip(f"Qt cannot start: {exc}", allow_module_level=True)

from lyrisync import appearance as ap  # noqa: E402
from lyrisync import frontmost  # noqa: E402
from lyrisync import hotkey  # noqa: E402
from lyrisync import login_item  # noqa: E402
from lyrisync import menu as m  # noqa: E402
from lyrisync import vibrancy  # noqa: E402
from lyrisync import window as w  # noqa: E402
from lyrisync.artwork import ArtworkProvider  # noqa: E402
from lyrisync.lyrics_provider import LyricsProvider, TrackLyrics  # noqa: E402
from lyrisync.player_monitor import PlaybackState, PlayerSnapshot  # noqa: E402
from lyrisync.view_model import Mode  # noqa: E402


# Captured before any fixture stubs it, so the one test that needs the
# real worker body can still reach it.
REAL_ARTWORK_RUN = w.ArtworkTask.run

PLAIN = TrackLyrics(plain="first line\nsecond line\nthird line")
SYNCED = TrackLyrics(synced=[(1.0, "one"), (5.0, "two")])
KOREAN_SYNCED = TrackLyrics(synced=[(1.0, "안녕하세요"), (5.0, "잘 가")])


@pytest.fixture(autouse=True)
def no_real_world(monkeypatch):
    """Nothing here may reach Spotify, the speakers, or the network.

    The polling thread runs and joins like the real one so shutdown is
    exercised for real, but never shells out. The player commands matter
    just as much: entering a sync pass dispatches a seek-to-0 and a resume,
    and on a developer's Mac osascript would happily restart whatever they
    were listening to — or launch Spotify to do it.

    Every worker the window can start is stubbed here, FetchTask included:
    a track change fires a fetch, and left alone it goes to LRCLIB for
    real. That is what aborted CI — a request still in its ssl handshake
    when the test tore the window down. Tests that want lyrics call
    ``load()``, which hands them over as if the fetch had returned.

    detect_voice is stubbed for a second reason on top of the `say`
    subprocess: unstubbed it answers True on a Mac and False on the Linux
    runner, so the speech paths would be covered locally and skipped in CI
    while both were green.

    Carbon is answered rather than blocked. Every window built here
    registers the global hotkey, and the conftest guard would fail all of
    them for it; handing back None is the same branch a machine without
    Carbon takes, so the real GlobalHotkey runs its real code and simply
    finds nothing to claim. The tests that need a live registration fake
    the door themselves.
    """

    def fake_run(self):
        # Stops the way the real one does — by asking the monitor, never by
        # raising a flag of its own. The stub used to set _running itself on
        # entry, which meant a _shutdown() landing before the thread body
        # started had its stop erased and the teardown waited 3s for a
        # thread that would never come back. That race was the monitor's,
        # not the stub's; the stub only has to keep sharing it.
        while not self._monitor._stop.is_set():
            self.msleep(5)

    monkeypatch.setattr(w.MonitorThread, "run", fake_run)
    for task in (w.PlayerCommandTask, w.SeekTask, w.SpeakTask, w.FetchTask,
                 w.ArtworkTask):
        monkeypatch.setattr(task, "run", lambda self: None)
    monkeypatch.setattr(w, "detect_voice", lambda: True)
    monkeypatch.setattr(w.hotkey, "_carbon", lambda: None)
    # Answered rather than blocked, for the same reason as Carbon: every
    # window here can turn per-app position memory on, and handing back
    # None is the branch a machine without pyobjc takes — so the real
    # FrontmostWatcher runs its real code and finds nothing to observe.
    # The conftest guard stays armed for anything that reaches around it.
    monkeypatch.setattr(w.frontmost, "_workspace", lambda: None)
    monkeypatch.setattr(w.frontmost, "own_bundle_id", lambda: None)


@pytest.fixture
def make_window(tmp_path):
    """Windows wired to a settings file of their own.

    The settings object is injected rather than redirected globally:
    QSettings.setDefaultFormat/setPath are process-wide and silently do
    nothing on macOS, so a test that trusted them would write into the real
    ~/Library/Preferences entry and stamp on the user's saved window.
    """
    settings_path = tmp_path / "lyrisync-test.ini"
    windows = []

    def factory():
        provider = LyricsProvider(
            cache_dir=tmp_path / "cache", user_sync_dir=tmp_path / "syncs"
        )
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        window = w.LyricsWindow(
            provider=provider,
            settings=settings,
            artwork_provider=ArtworkProvider(cache_dir=tmp_path / "art"),
        )
        window.resize(460, 220)
        windows.append(window)
        return window

    yield factory
    unjoined = []
    for window in windows:
        window._shutdown()
        # Destroying a QWidget whose QThread is still running is a qFatal:
        # the process aborts with "QThread: Destroyed while thread is still
        # running", taking the whole run with it and naming whichever test
        # happened to be on screen — which is how a shutdown bug came to
        # look like a bug in the quit test. So the thread is forced down
        # before anything is destroyed, and the test fails afterwards with
        # a sentence instead of a signal.
        if window._monitor_thread.isRunning():
            unjoined.append(window)
            window._monitor_thread.terminate()
            window._monitor_thread.wait(1000)
        window.hide()
        window.deleteLater()
    APP.processEvents()
    assert not unjoined, (
        f"{len(unjoined)} monitor thread(s) outlived _shutdown — destroying "
        "those windows would have aborted the process"
    )


def test_the_window_writes_only_where_it_is_told(make_window, tmp_path):
    """Guard on the seam itself: lose the injection and every test below
    starts editing the real user's preferences."""
    window = make_window()
    assert window._settings.fileName() == str(tmp_path / "lyrisync-test.ini")
    window._set_lyrics_visible(False)
    window._settings.sync()
    assert (tmp_path / "lyrisync-test.ini").exists()


def snapshot(
    track_id="t1", title="Song", state=PlaybackState.PLAYING, position=0.0
):
    return PlayerSnapshot(
        state=state,
        track_id=track_id,
        title=title,
        artist="Artist",
        album="Album",
        duration_ms=200000,
        position_seconds=position,
    )


def load(window, lyrics, track_id="t1"):
    window._on_track_change(snapshot(track_id=track_id))
    window._on_fetch_finished(track_id, lyrics, True)
    window._title_card_until = 0.0  # skip the 2s "song announces itself" card
    window._render()
    APP.processEvents()


def visible_keys(window):
    """The menu's visible entries, in menu order, as menu.py keys."""
    by_action = {id(a): key for key, a in window._menu_actions.items()}
    return tuple(
        by_action[id(action)]
        for action in window._menu.actions()
        if action.isVisible()
    )


# -- one menu, two ways in ------------------------------------------------


@pytest.fixture
def with_tray(monkeypatch):
    """Force the menu bar item into existence.

    The offscreen platform reports no system tray, so the real one would
    never be built and the test would skip everywhere, testing nothing. A
    QSystemTrayIcon still constructs and holds its menu here; what is being
    checked is that _build_tray hands over the shared menu and a template
    icon, not whether this platform can draw a menu bar.
    """

    class AlwaysAvailable(QSystemTrayIcon):
        @staticmethod
        def isSystemTrayAvailable():
            return True

    monkeypatch.setattr(w, "QSystemTrayIcon", AlwaysAvailable)


def test_menu_bar_and_right_click_are_literally_the_same_menu(with_tray, make_window):
    window = make_window()
    assert window._tray is not None
    assert window._tray.contextMenu() is window._menu


def test_the_menu_bar_icon_is_a_template_image(with_tray, make_window):
    """A mask icon is what macOS tints for light and dark menu bars; ship a
    coloured one and it stays black on a dark menu bar."""
    window = make_window()
    icon = window._tray.icon()
    assert not icon.isNull()
    assert icon.isMask() is True


def test_no_system_tray_is_survivable(monkeypatch, make_window):
    """Nothing else may depend on the menu bar item existing. Forced rather
    than relying on the platform, so the path is exercised wherever the
    suite runs."""

    class NeverAvailable(QSystemTrayIcon):
        @staticmethod
        def isSystemTrayAvailable():
            return False

    monkeypatch.setattr(w, "QSystemTrayIcon", NeverAvailable)
    window = make_window()
    assert window._tray is None
    assert window._menu is not None
    window._refresh_menu()
    window._set_lyrics_visible(False)
    assert window.isVisible() is False


def test_the_menu_is_built_once_and_never_rebuilt(make_window):
    """Structure is fixed; only visibility, check marks and the sync label
    move. A rebuilt native menu bar item would flicker under the user."""
    window = make_window()
    before = list(window._menu.actions())
    load(window, SYNCED)
    load(window, PLAIN, track_id="t2")
    window._refresh_menu()
    assert list(window._menu.actions()) == before


def test_menu_visibility_follows_the_pure_gating(make_window):
    window = make_window()
    window._speech_available = False
    load(window, SYNCED)
    assert visible_keys(window) == m.visible_entries(
        has_korean_lyrics=False,
        speech_available=False,
        synced=True,
        sync_offered=False,
    )

    window._speech_available = True
    load(window, KOREAN_SYNCED, track_id="t2")
    window._view_model.romanisation_enabled = True
    window._refresh_menu()
    assert visible_keys(window) == m.visible_entries(
        has_korean_lyrics=True,
        speech_available=True,
        synced=True,
        sync_offered=False,
    )


def test_bare_menu_when_every_layer_is_dormant(make_window):
    window = make_window()
    window._speech_available = False
    window._refresh_menu()  # idle: no lyrics, nothing to sync
    assert visible_keys(window) == (
        m.SHOW_LYRICS,
        m.SEPARATOR_AFTER_SHOW,
        m.ALBUM_COLOUR,
        m.ALL_DESKTOPS,
        m.REMEMBER_POSITION,
        m.SEPARATOR_BEFORE_QUIT,
        m.QUIT,
    )


def test_sync_entry_label_switches_once_a_user_sync_exists(make_window):
    window = make_window()
    load(window, PLAIN)
    assert window._menu_actions[m.SYNC].isVisible()
    assert window._menu_actions[m.SYNC].text() == "Sync this song"

    window._provider.save_user_sync("t1", "[00:01.00] first line\n")
    window._refresh_menu()
    assert window._menu_actions[m.SYNC].text() == "Re-sync this song"


def test_quit_is_visible_in_every_state(make_window):
    window = make_window()
    for lyrics in (None, PLAIN, SYNCED):
        if lyrics is not None:
            load(window, lyrics)
        window._refresh_menu()
        assert window._menu_actions[m.QUIT].isVisible()


# -- toggles drive the same state from the menu ---------------------------


def test_toggling_from_the_menu_updates_state_and_settings(make_window):
    window = make_window()
    load(window, KOREAN_SYNCED)

    window._menu_actions[m.ROMANISATION].trigger()
    assert window._view_model.romanisation_enabled is True
    assert window._settings.value("lyrics/romanisation", type=bool) is True

    window._menu_actions[m.ECHO].trigger()
    assert window._echo_enabled is True
    assert window._loop.echo is True

    window._rate_actions[160].trigger()
    assert window._speech_rate == 160
    assert window._settings.value("lyrics/speech_rate", type=int) == 160


def test_a_refresh_does_not_feed_check_marks_back_into_the_setters(make_window):
    """Check marks are set programmatically on every render; wiring them to
    toggled instead of triggered would invert settings behind the user."""
    window = make_window()
    window._view_model.romanisation_enabled = True
    window._spoken_enabled = False
    for _ in range(3):
        window._refresh_menu()
    assert window._view_model.romanisation_enabled is True
    assert window._spoken_enabled is False


# -- show lyrics ----------------------------------------------------------


def test_hiding_the_lyrics_leaves_everything_else_running(make_window):
    window = make_window()
    window.apply_saved_visibility()
    load(window, PLAIN)
    window._begin_sync()
    window._on_position_update(snapshot())
    window._on_tap()
    stamped = window._view_model.sync_session.index

    window._menu_actions[m.SHOW_LYRICS].trigger()  # unchecks -> hide
    APP.processEvents()
    assert window.isVisible() is False
    assert window._monitor_thread.isRunning() is True
    assert window._view_model.sync_session is not None
    assert window._view_model.sync_session.index == stamped
    assert window._view_model.display().mode is Mode.SYNCING


def test_a_sync_pass_keeps_stamping_while_hidden(make_window):
    window = make_window()
    load(window, PLAIN)
    window._begin_sync()
    window._set_lyrics_visible(False)

    window._last_state = PlaybackState.PLAYING
    window._on_position_update(snapshot())
    window._on_tap()
    assert window._view_model.sync_session.index == 1


def test_showing_again_restores_the_current_display(make_window):
    window = make_window()
    load(window, SYNCED)
    window._set_lyrics_visible(False)

    # The song moves on while the window is away.
    window._on_position_update(
        PlayerSnapshot(
            state=PlaybackState.PLAYING,
            track_id="t1",
            title="Song",
            artist="Artist",
            duration_ms=200000,
            position_seconds=6.0,
        )
    )
    window._set_lyrics_visible(True)
    APP.processEvents()
    assert window.isVisible() is True
    assert window._current.text() == "two"  # caught up, not the stale line


def test_show_lyrics_is_persisted_and_restored(make_window):
    window = make_window()
    window._set_lyrics_visible(False)
    window._settings.sync()

    reopened = make_window()
    assert reopened._lyrics_visible is False
    reopened.apply_saved_visibility()
    assert reopened.isVisible() is False
    assert reopened._menu_actions[m.SHOW_LYRICS].isChecked() is False

    reopened._set_lyrics_visible(True)
    reopened._settings.sync()
    assert make_window()._lyrics_visible is True


def test_visibility_survives_the_shutdown_save(make_window):
    window = make_window()
    window._set_lyrics_visible(False)
    window._shutdown()
    window._settings.sync()
    assert make_window()._lyrics_visible is False


# -- following the system appearance --------------------------------------


def set_scheme(scheme):
    """Publish an appearance change the way the platform does.

    The offscreen plugin will not change its own colour scheme —
    setColorScheme is ignored and it reports Unknown forever — so the
    signal is emitted directly. That still exercises the real connection
    the window makes in __init__ rather than calling its slot by hand,
    which is the half of this that could silently not be wired.
    """
    APP.styleHints().colorSchemeChanged.emit(scheme)
    APP.processEvents()


def test_the_window_starts_on_the_system_appearance(make_window):
    """Offscreen reports Unknown, which resolves to dark — so the suite
    runs against the palette the app has always had."""
    window = make_window()
    assert window._appearance is ap.Appearance.DARK
    assert window._palette is ap.DARK


def test_a_system_change_repaints_the_window(make_window):
    window = make_window()
    load(window, SYNCED)

    set_scheme(Qt.ColorScheme.Light)
    assert window._appearance is ap.Appearance.LIGHT
    assert window._palette is ap.LIGHT
    assert ap.rgba(ap.LIGHT.current) in window.styleSheet()

    set_scheme(Qt.ColorScheme.Dark)
    assert window._palette is ap.DARK
    assert ap.rgba(ap.DARK.current) in window.styleSheet()


def test_the_scrim_follows_the_palette(make_window, monkeypatch):
    """The background is painted, not styled, so it needs its own path out
    of the palette — and it is the one thing a stylesheet swap would
    silently leave behind."""
    window = make_window()
    # No material on the offscreen platform, so paintEvent reaches for the
    # solid background — the same code path, one field along.
    assert window._material is None
    painted = []
    real_qcolor = w._qcolor
    monkeypatch.setattr(
        w, "_qcolor", lambda colour: (painted.append(colour), real_qcolor(colour))[1]
    )

    # painted[0] is the fill; painted[1] is the hairline drawn over it.
    window.grab()  # a real paintEvent, into a pixmap
    assert painted[0] == ap.DARK.solid
    assert painted[1] == ap.DARK.border

    set_scheme(Qt.ColorScheme.Light)
    painted.clear()
    window.grab()
    assert painted[0] == ap.LIGHT.solid
    assert painted[1] == ap.LIGHT.border


def test_a_redundant_change_restyles_nothing(make_window, monkeypatch):
    """The signal fires for changes this window does not care about — an
    Unknown, or a re-announcement of what is already on screen. Rebuilding
    the stylesheet for those would repolish every widget for nothing."""
    window = make_window()
    # Spied on the instance, not the module: the signal reaches every
    # window alive in the process, and windows from earlier tests outlive
    # their deleteLater() until an event loop runs. Counting module-level
    # calls would be counting theirs.
    repaints = []
    real_apply = window._apply_appearance
    monkeypatch.setattr(
        window, "_apply_appearance", lambda: (repaints.append(1), real_apply())[1]
    )

    set_scheme(Qt.ColorScheme.Dark)      # already dark
    set_scheme(Qt.ColorScheme.Unknown)   # resolves to dark
    assert repaints == []
    assert window._palette is ap.DARK

    set_scheme(Qt.ColorScheme.Light)
    assert len(repaints) == 1


def test_a_resize_after_a_switch_keeps_the_new_palette(make_window):
    """_apply_scale rebuilds the stylesheet too. If it reached for a
    constant instead of the current palette, the window would snap back to
    dark the next time it was dragged wider."""
    window = make_window()
    set_scheme(Qt.ColorScheme.Light)

    window.resize(600, 260)
    APP.processEvents()
    assert ap.rgba(ap.LIGHT.current) in window.styleSheet()
    assert ap.rgba(ap.DARK.current) not in window.styleSheet()


def test_everything_that_is_not_a_colour_survives_a_switch(make_window):
    """The switch repaints; it must not disturb anything else. Geometry,
    opacity, an engaged loop and a sync pass in progress all carry on."""
    window = make_window()
    load(window, PLAIN)
    window._begin_sync()
    window._on_position_update(snapshot())
    window._on_tap()

    window.resize(520, 240)
    window._set_opacity(0.6)
    APP.processEvents()
    geometry, opacity = window.geometry(), window._opacity
    scale, stamped = window._scale, window._view_model.sync_session.index

    set_scheme(Qt.ColorScheme.Light)

    assert window.geometry() == geometry
    assert window._opacity == opacity
    assert window._scale == scale
    assert window._view_model.sync_session.index == stamped
    assert window._view_model.display().mode is Mode.SYNCING
    assert window._monitor_thread.isRunning() is True
    # isVisibleTo, not isVisible: this window was never shown, so every
    # child reports hidden regardless. What matters is that the switch did
    # not take the tap row out of the layout.
    assert window._tap_button.isVisibleTo(window) is True


def test_the_armed_discard_prompt_is_coloured_per_mode(make_window):
    """It carries its colour inline rather than by object name, so it is
    the one piece of text a stylesheet swap cannot reach."""
    window = make_window()
    load(window, PLAIN)
    window._begin_sync()
    window._on_sync_exit()  # arms it
    assert ap.rgba(ap.DARK.confirm_text) in window._progress.text()

    set_scheme(Qt.ColorScheme.Light)
    assert ap.rgba(ap.LIGHT.confirm_text) in window._progress.text()


def test_the_speak_icon_is_redrawn_for_the_new_mode(make_window, monkeypatch):
    """An SF Symbol is a template image tinted by us, so a white glyph
    stays white on a pale panel unless it is rendered again."""
    window = make_window()
    tints = []
    monkeypatch.setattr(
        w, "symbol_icon", lambda name, size, normal, **kw: tints.append(normal) or None
    )

    set_scheme(Qt.ColorScheme.Light)
    assert tints, "the icon was never re-rendered"
    assert tints[-1].alpha() == ap.LIGHT.control_idle[3]
    assert tints[-1].blue() == ap.LIGHT.control_idle[2]


def test_the_material_appearance_is_asked_for_the_same_mode(make_window):
    """No material off cocoa, so this is the guard rather than the call —
    but the guard is what keeps the suite headless."""
    window = make_window()
    assert window._material is None
    window._apply_material_appearance()  # must be a no-op, not a crash


def test_the_material_and_the_scrim_cannot_disagree():
    """One answer drives both: whichever mode the palette came from is the
    NSAppearance the material is told to adopt."""
    assert vibrancy.appearance_name(True) == vibrancy.DARK_APPEARANCE
    assert vibrancy.appearance_name(False) == vibrancy.LIGHT_APPEARANCE
    assert "NSAppearanceName" in vibrancy.LIGHT_APPEARANCE


def test_there_is_no_appearance_setting(make_window):
    """Following the system is the whole feature. A toggle would be a
    second source of truth for something macOS already answers."""
    window = make_window()
    assert not any("appearance" in key for key in window._menu_actions)
    assert not any("theme" in key for key in window._menu_actions)
    window._save_settings()
    keys = window._settings.allKeys()
    assert not any("appearance" in k or "theme" in k for k in keys)


# -- the line change: fade and rise ---------------------------------------


def synced_window(make_window):
    window = make_window()
    load(window, SYNCED)
    window._last_state = PlaybackState.PLAYING
    APP.processEvents()
    return window


def test_a_line_at_rest_is_opaque_and_on_its_mark(make_window):
    window = synced_window(make_window)
    assert window._current_fx.progress == 0.0


def test_the_outgoing_line_leaves_upward(make_window):
    """Upward, in the direction the song is going — a line that sank as it
    left would read as the song going backwards."""
    window = synced_window(make_window)
    window._begin_fade_out()
    assert window._fade_anim is not None
    assert window._fade_anim.endValue() == -1.0

    window._fade_anim.setCurrentTime(window._fade_anim.duration())
    APP.processEvents()
    assert window._current_fx.progress == -1.0


def test_the_incoming_line_rises_from_below_into_place(make_window):
    window = synced_window(make_window)
    window._render()
    window._predicted_swap()
    # Starts below and transparent...
    assert window._fade_anim is not None
    assert window._fade_anim.endValue() == 0.0

    window._fade_anim.setCurrentTime(0)
    APP.processEvents()
    assert window._current_fx.progress == pytest.approx(1.0, abs=0.05)

    window._fade_anim.setCurrentTime(window._fade_anim.duration())
    APP.processEvents()
    assert window._current_fx.progress == 0.0  # ...and lands exactly on its mark


def test_the_motion_ends_on_the_timestamp_rather_than_starting_on_it(make_window):
    """The anticipatory schedule stays authoritative. The rise finishes as
    the line becomes current, so it is never still moving while being read."""
    window = synced_window(make_window)
    window._render()
    window._predicted_swap()
    assert window._fade_anim.duration() == w._FADE_MS
    # The swap is scheduled a full fade before the timestamp, so
    # fade-in-completes-at-ts holds by construction.
    assert w._SWAP_LEAD_MS >= w._FADE_MS


def test_the_line_is_eased_not_linear(make_window):
    window = synced_window(make_window)
    window._begin_fade_out()
    out_curve = window._fade_anim.easingCurve().type()
    window._render()
    window._predicted_swap()
    in_curve = window._fade_anim.easingCurve().type()
    assert out_curve != QEasingCurve.Type.Linear
    assert in_curve != QEasingCurve.Type.Linear
    assert in_curve != out_curve  # departure accelerates, arrival settles


def test_travel_is_scale_aware(make_window):
    """A few pixels at default width, proportionally more when the window
    is dragged wider — the same scale everything else in the window
    follows. Widths stay inside the offscreen screen (800px), or the
    resize is clamped and the test proves nothing."""
    window = make_window()
    # Shown, because Qt defers resize events for hidden widgets and
    # _apply_scale would never run — the test would pass on a stale value.
    window.show()
    window.resize(300, 240)
    APP.processEvents()
    narrow = window._current_fx.travel

    window.resize(460, 240)
    APP.processEvents()
    default = window._current_fx.travel

    window.resize(760, 260)
    APP.processEvents()
    wide = window._current_fx.travel

    assert narrow < default < wide
    assert 3 <= default <= 10  # a few pixels, at the width the app opens at


@pytest.mark.parametrize(
    "disturbance",
    (
        "render",
        "seek",
        "pause",
        "loop",
        "sync",
        "track_change",
    ),
)
def test_nothing_leaves_a_line_mid_flight(make_window, disturbance):
    """A line parked off its mark, or fading, after the world moved is the
    failure mode of animating this at all. Every path back to a known
    state has to snap."""
    window = synced_window(make_window)
    window._begin_fade_out()
    window._fade_anim.setCurrentTime(window._fade_anim.duration() // 2)
    APP.processEvents()
    assert window._current_fx.progress != 0.0  # genuinely mid-flight

    if disturbance == "render":
        window._render()
    elif disturbance == "seek":
        window._on_position_update(snapshot())
        window._render()
    elif disturbance == "pause":
        window._on_state_change(snapshot(state=PlaybackState.PAUSED))
        window._render()
    elif disturbance == "loop":
        window._do_loop_wrap()
        window._render()
    elif disturbance == "sync":
        load(window, PLAIN, track_id="t2")
        window._begin_sync()
    elif disturbance == "track_change":
        window._on_track_change(snapshot(track_id="t9"))
    APP.processEvents()

    assert window._current_fx.progress == 0.0
    assert window._fade_anim is None or not window._fade_anim.state()


def test_the_choreography_is_two_equal_phases_before_the_timestamp():
    """One number, three constants derived from it, so the swap point and
    the total window cannot drift apart from the phase length."""
    assert w._SWAP_LEAD_MS == w._FADE_MS
    assert w._FADE_OUT_LEAD_MS == 2 * w._FADE_MS


def test_the_transition_is_unhurried_but_still_lands_on_time(make_window):
    """Extended EARLIER rather than finishing later: the arrival still
    ends on the timestamp, it just starts moving well before it."""
    window = synced_window(make_window)
    lines = [(0.0, "a"), (10.0, "b")]
    window._schedule_line_advance(lines, 0, 0.0)

    assert window._transition_ms == w._FADE_MS
    # swap one phase before the line, fade-out two phases before it
    assert window._swap_timer.interval() == 10000 - w._FADE_MS
    assert window._fadeout_timer.interval() == 10000 - 2 * w._FADE_MS


@pytest.mark.parametrize(
    "gap_ms,expected_phase",
    (
        (10000, 260),   # ordinary spacing: the full choreography
        (1040, 260),    # exactly twice the phase: still full
        (600, 260),     # the phases fit with room to spare
        (400, 200),     # too tight for the full movement: scaled down
        (120, 60),      # a rapid-fire line
        (20, 10),       # absurdly fast
    ),
)
def test_a_short_gap_gets_a_quicker_movement_not_a_truncated_one(
    make_window, gap_ms, expected_phase
):
    """Lines can arrive faster than the animation window — ad-libs, rapid
    call-and-response. Both phases still fit and the arrival still ends
    exactly on the timestamp; the movement is simply quicker."""
    window = synced_window(make_window)
    lines = [(0.0, "a"), (gap_ms / 1000, "b")]
    window._schedule_line_advance(lines, 0, 0.0)

    assert window._transition_ms == expected_phase
    swap_at = window._swap_timer.interval()
    # The arrival begins one phase before the line and lasts one phase,
    # so it settles ON it — the property that must hold at any tempo.
    assert swap_at + window._transition_ms == pytest.approx(gap_ms, abs=1)
    assert swap_at >= 0
    assert window._fadeout_timer.interval() >= 0


def test_a_shortened_transition_is_what_the_animation_actually_uses(make_window):
    """The clamp is worthless if the animation still runs at the nominal
    duration — it would overrun the line it belongs to."""
    window = synced_window(make_window)
    window._schedule_line_advance([(0.0, "a"), (0.4, "b")], 0, 0.0)
    assert window._transition_ms == 200

    window._begin_fade_out()
    assert window._fade_anim.duration() == 200


def test_the_easing_is_gentle_at_both_ends(make_window):
    """Sine, not cubic: cubic's ends are steep enough that even a 260ms
    phase reads as a flick."""
    window = synced_window(make_window)
    window._begin_fade_out()
    assert window._fade_anim.easingCurve().type() == QEasingCurve.Type.InSine
    window._render()
    window._predicted_swap()
    assert window._fade_anim.easingCurve().type() == QEasingCurve.Type.OutSine


def test_a_cancelled_schedule_leaves_no_timers_armed(make_window):
    window = synced_window(make_window)
    window._on_position_update(snapshot())
    window._cancel_line_schedule()
    assert not window._fadeout_timer.isActive()
    assert not window._swap_timer.isActive()
    assert window._current_fx.progress == 0.0


# -- one line change plays once -------------------------------------------


def expire(timer, fire):
    """What Qt does when a single-shot timer runs out: it stops, and then
    the slot runs. Driven by hand because the choreography is measured in
    hundreds of milliseconds and a test that waited them out would be slow
    and racy, while what is being checked here is purely the order events
    arrive in."""
    timer.stop()
    fire()


def record_lines(window):
    """Every index the window puts on screen, in order. A line change that
    plays twice shows up as [1, 0, 1] where it should read [1]."""
    shown = []
    original = window._set_lines

    def spy(lines, index):
        shown.append(index)
        original(lines, index)

    window._set_lines = spy
    return shown


def test_a_repeated_trigger_for_the_same_line_does_not_restart_it(make_window):
    """The identity dedupe, on the path a re-armed timer takes. A second
    fade-out for a line already leaving must not start the movement over
    from wherever it had got to."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=1.5))
    window._begin_fade_out()
    animation = window._fade_anim
    assert animation is not None
    animation.setCurrentTime(animation.duration() // 2)
    half = animation.currentTime()

    window._begin_fade_out()
    window._begin_fade_out()

    assert window._fade_anim is animation, "the animation was replaced"
    assert window._fade_anim.currentTime() == half, "the movement restarted"


def test_a_poll_landing_mid_change_does_not_play_it_again(make_window):
    """The bug. One line change is 520ms and a poll arrives every 300ms,
    so a poll lands inside almost every change. It used to re-arm the
    timers from what was left of the gap AND snap the display back to the
    line being left — so the same change played a second time, faster,
    right on top of itself."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=1.5))
    shown = record_lines(window)

    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)
    assert shown == [1]

    # Polls between the swap and the line's own timestamp, which is where
    # the poll interval puts them nearly every time.
    for position in (4.8, 4.9, 4.95):
        window._on_position_update(snapshot(position=position))

    assert shown == [1], "the line change played again"
    assert window._current.text() == "two"
    assert not window._fadeout_timer.isActive()
    assert not window._swap_timer.isActive()


def test_the_player_catching_up_is_not_a_second_change(make_window):
    """The position finally crosses the timestamp and the view model
    agrees with the screen. Nothing should move: the change already
    happened."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=1.5))
    shown = record_lines(window)
    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)
    window._fade_anim.setCurrentTime(window._fade_anim.duration())  # it lands
    APP.processEvents()

    window._on_position_update(snapshot(position=5.1))

    assert shown == [1]
    assert window._displayed_index == 1
    assert window._current_fx.progress == 0.0  # still on its mark, not moving


def test_the_line_after_it_is_still_scheduled_normally(make_window):
    """Dedupe by target, not a latch: the change to the next line is a
    different change and arms as usual."""
    window = make_window()
    load(window, TrackLyrics(synced=[(1.0, "one"), (5.0, "two"), (9.0, "three")]))
    window._last_state = PlaybackState.PLAYING
    window._on_position_update(snapshot(position=1.5))
    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)

    window._on_position_update(snapshot(position=5.1))

    assert window._swap_timer.isActive()
    assert window._transition.may_arm(2)
    window._begin_fade_out()
    assert window._transition.target == 2


def test_a_seek_back_into_the_current_line_still_snaps(make_window):
    """The bound on being ahead. Once the player is further from the line
    than the choreography can explain, the screen showing it is not a
    prediction any more — it is wrong, and snapping is the whole point of
    that check."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=4.5))
    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)
    assert window._current.text() == "two"

    window._on_position_update(snapshot(position=1.2))  # seek, backwards

    assert window._displayed_index == 0
    assert window._current.text() == "one"
    assert window._swap_timer.isActive()  # and the change is scheduled afresh


def test_the_effect_reserves_room_for_the_travel(make_window):
    """Without this the moving block is clipped to its own box and reads
    as dissolving at the edge instead of leaving."""
    window = synced_window(make_window)
    fx = window._current_fx
    source = QRectF(0, 0, 100, 40)
    grown = fx.boundingRectFor(source)
    assert grown.top() < source.top()
    assert grown.bottom() > source.bottom()


# -- depth ----------------------------------------------------------------


def test_both_palettes_carry_a_hairline(make_window):
    """Light over the dark panel, dark over the pale one — the way macOS
    edges its own HUD surfaces."""
    assert ap.DARK.border[:3] == (255, 255, 255)
    assert ap.LIGHT.border[:3] == (0, 0, 0)
    for palette in (ap.DARK, ap.LIGHT):
        assert 0 < palette.border[3] < 64, "a hairline, not a border"


def test_the_hairline_is_where_the_album_colour_goes(make_window):
    """SUPERSEDES "a coloured hairline reads as a border". The panel's
    luminance is pinned by the contrast floor and has no gamut left to
    spend, least of all in light mode; the edge has no text on it and can
    take the hue properly. What is checked here is the wiring — that the
    window paints the tinted edge rather than the palette's own — with
    the derivation itself measured in test_scrim.py."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    assert window._current_border() == ap.DARK.border  # untinted until a cover

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    expected = ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).border
    assert window._current_border() == expected
    assert window._current_border() != ap.DARK.border


def test_the_painted_edge_is_the_tinted_one(make_window):
    """From the pixels paintEvent produced, not from the colour it was
    asked for: the top row of the grab is the hairline over the fill, and
    it has to be the album's hue rather than the palette's neutral edge.
    grab() does not apply a QGraphicsEffect, but paintEvent is exactly
    what it does run."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    middle = window.width() // 2
    neutral = window.grab().toImage().pixelColor(middle, 0)
    assert neutral.red() == neutral.green()  # a grey edge over a grey fill

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    edge = window.grab().toImage().pixelColor(middle, 0)
    fill = window.grab().toImage().pixelColor(middle, 4)
    assert edge.red() > edge.green() and edge.red() > edge.blue(), "not red at all"
    assert edge.red() - min(edge.green(), edge.blue()) > 3 * (
        fill.red() - min(fill.green(), fill.blue())
    ), "the edge is carrying no more colour than the panel"


def test_the_edge_and_the_panel_arrive_together(make_window):
    """One cross-fade drives both. Two fades of the same tint could only
    drift apart, and an edge that changed colour before its panel would
    read as a flicker at the rim."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)

    assert window._current_border() == ap.DARK.border  # still where it began
    window._tint_anim.setCurrentTime(w._TINT_FADE_MS // 2)
    APP.processEvents()
    assert window._current_border() not in (
        ap.DARK.border,
        ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).border,
    )

    settle_tint(window)
    assert window._current_border() == ap.tinted(
        ap.DARK, RED_COVER, ap.Appearance.DARK
    ).border


def test_switching_the_layer_off_restores_the_plain_edge(make_window):
    """The layers principle reaches the rim too: off is the app before
    this existed, to the byte."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert window._current_border() != ap.DARK.border

    window._set_album_colour(False)
    settle_tint(window)
    assert window._current_border() == ap.DARK.border


def test_the_edge_is_re_derived_for_the_new_appearance(make_window):
    """The two modes want different lightnesses for the same hue — the
    edge has to stay lighter than a dark panel and darker than a pale
    one — so a switch cannot carry the old colour across."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    set_scheme(Qt.ColorScheme.Light)
    assert window._current_border() == ap.tinted(
        ap.LIGHT, RED_COVER, ap.Appearance.LIGHT
    ).border


def test_the_shadow_is_guarded_off_cocoa(make_window):
    """No NSWindow on the offscreen platform, so these must be no-ops
    rather than crashes — which is what keeps the suite headless."""
    window = make_window()
    window._apply_shadow()
    window._invalidate_shadow()


# -- per-app position memory ----------------------------------------------


VSCODE = "com.microsoft.VSCode"
SAFARI = "com.apple.Safari"


NAMES = {
    VSCODE: "Code",
    SAFARI: "Safari",
    "com.apple.Notes": "Notes",
    "com.lyrisync.lyrisync": "LyriSync",
}


_UNSET = object()


def activate(window, bundle_id, name=_UNSET):
    """What NSWorkspace hands the window: an identifier AND a name, taken
    from the same announcement."""
    if name is _UNSET:
        name = NAMES.get(bundle_id)
    window._on_app_activated(frontmost.AppIdentity(bundle_id, name))


def remembering(make_window, frontmost_app=VSCODE):
    """A window with the layer on and an app in front of it."""
    window = make_window()
    window.show()
    window._set_remember_position(True)
    window._frontmost = frontmost_app
    window._frontmost_name = NAMES.get(frontmost_app)
    APP.processEvents()
    return window


def end_a_drag(window, x, y):
    """What the user finishing a drag looks like from here: the window is
    somewhere new, and the release handler runs."""
    window.move(x, y)
    window._drag_offset = QPoint(0, 0)  # as mousePressEvent would have left it
    window.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(1, 1),
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    APP.processEvents()  # the learn is deferred by one tick, like the nudge


def test_the_layer_is_off_by_default(make_window):
    """Every layer off must equal the app before this existed — including
    not observing anything."""
    window = make_window()
    assert window._remember_position is False
    assert window._menu_actions[m.REMEMBER_POSITION].isChecked() is False
    assert not window._watcher.active


def test_nothing_is_learned_while_the_layer_is_off(make_window):
    window = make_window()
    window.show()
    window._frontmost = VSCODE
    end_a_drag(window, 300, 200)
    assert len(window._positions) == 0


def test_finishing_a_drag_records_the_position_for_the_frontmost_app(make_window):
    """Learning is implicit: there is no save action, only the drag the
    user was going to do anyway."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    assert window._positions.recall(VSCODE) == (300, 200)


def test_finishing_a_resize_records_it_too(make_window):
    window = remembering(make_window)
    window.move(120, 90)
    window._resize_edges = Qt.Edge.RightEdge
    window.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(1, 1),
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    APP.processEvents()
    assert window._positions.recall(VSCODE) == (120, 90)


def test_each_app_keeps_its_own_position(make_window):
    """The acceptance test, in miniature: place it one way here, another
    way there."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    window._frontmost = SAFARI
    end_a_drag(window, 60, 480)

    assert window._positions.recall(VSCODE) == (300, 200)
    assert window._positions.recall(SAFARI) == (60, 480)


def test_the_window_moves_to_a_remembered_position_on_activation(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window.move(10, 10)

    activate(window, VSCODE)
    window._settle_timer.stop()  # fire the settle by hand, deterministically
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()
    finish_move(window)

    assert window.pos() == QPoint(300, 200)


def finish_move(window):
    """Run the travel animation to its end without waiting it out."""
    if window._move_anim is not None:
        window._move_anim.setCurrentTime(window._move_anim.duration())
    APP.processEvents()


def settle(window, bundle_id):
    """An activation that has been frontmost long enough to act on."""
    activate(window, bundle_id)
    window._settle_timer.stop()
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()


def test_a_timer_that_fires_early_asks_again_rather_than_dropping_the_move(
    make_window,
):
    """Found live, not here: QTimer fired at 390ms against the 400ms rule,
    the debounce said "not yet", and because the timer is single-shot the
    arrival was dropped for good. The rule stays authoritative and the
    timer re-arms for what is left."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    activate(window, VSCODE)
    window._settle_timer.stop()
    window._apply_settled_app()  # asked immediately: far too early

    assert window._move_anim is None, "moved before the app had settled"
    assert window._settle_timer.isActive(), "the arrival was dropped"
    assert window._debounce.pending == VSCODE

    # And when it is genuinely due, the same arrival still lands.
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()
    finish_move(window)
    assert window.pos() == QPoint(400, 300)


def test_an_app_with_no_remembered_position_leaves_the_window_alone(make_window):
    """Doing nothing is the right answer: a default would move the window
    somewhere the user never put it."""
    window = remembering(make_window)
    window.move(123, 456)

    settle(window, "com.apple.Notes")
    finish_move(window)

    assert window.pos() == QPoint(123, 456)


def test_the_move_is_animated_rather_than_a_teleport(make_window):
    """Consistent with the motion work in 13: eased, and sine rather than
    cubic."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    settle(window, VSCODE)

    assert window._move_anim is not None
    assert window._move_anim.duration() == w._MOVE_MS
    assert window._move_anim.easingCurve().type() == w._MOVE_CURVE
    assert window.pos() != QPoint(400, 300)  # not there yet
    finish_move(window)
    assert window.pos() == QPoint(400, 300)


def test_a_second_activation_mid_travel_retargets_from_where_it_got_to(make_window):
    """The same rule the tint cross-fade follows: the user is looking at
    where it is, not at where it was going."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window._frontmost = SAFARI
    end_a_drag(window, 80, 500)
    window.move(10, 10)

    settle(window, VSCODE)
    first = window._move_anim
    first.setCurrentTime(first.duration() // 2)
    APP.processEvents()
    midway = window.pos()

    settle(window, SAFARI)
    assert window._move_anim is not first
    assert window._move_anim.startValue() == midway
    finish_move(window)
    assert window.pos() == QPoint(80, 500)


def test_cmd_tabbing_through_apps_does_not_move_the_window(make_window):
    """The debounce, on the path a real Cmd-Tab sweep takes: several
    activations in quick succession, none of them settled."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    for app in (VSCODE, SAFARI, "com.apple.Notes", VSCODE):
        activate(window, app)

    assert window._move_anim is None
    assert window.pos() == QPoint(10, 10)
    assert window._settle_timer.isActive()  # still waiting for things to stop


@pytest.mark.parametrize("obstacle", ("dragging", "syncing", "hidden"))
def test_the_window_is_not_moved_while_the_user_is_busy(make_window, obstacle):
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    if obstacle == "dragging":
        window._drag_offset = QPoint(5, 5)
    elif obstacle == "syncing":
        load(window, PLAIN, track_id="t2")
        window._frontmost = VSCODE
        window._begin_sync()
        window.move(10, 10)
    else:
        window._set_lyrics_visible(False)

    settle(window, VSCODE)
    finish_move(window)

    assert window.pos() == QPoint(10, 10)


def test_a_remembered_position_is_still_clamped_on_arrival(make_window):
    """The screen it was learned on may be gone. A remembered position is
    not a licence to put the window somewhere unreachable."""
    window = remembering(make_window)
    window._positions.remember(VSCODE, 100_000, 100_000)

    settle(window, VSCODE)
    finish_move(window)

    available = window._available_geometry()
    assert window.pos().x() < available.right()
    assert window.pos().y() < available.bottom()


def test_switching_the_layer_off_stops_observing_and_moving(make_window):
    """Off means off: the subscription goes, not just the acting on it."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    window._set_remember_position(False)

    assert not window._watcher.active
    assert not window._settle_timer.isActive()
    settle(window, VSCODE)
    finish_move(window)
    assert window.pos() == QPoint(10, 10)


def test_switching_it_off_keeps_what_it_learned(make_window):
    """So that turning it back on does not start from nothing — and so the
    forget entry still has something to clear."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window._set_remember_position(False)
    assert window._positions.recall(VSCODE) == (400, 300)


def test_forgetting_clears_the_map_and_the_setting(make_window):
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    assert m.FORGET_POSITIONS in visible_keys(window)

    window._menu_actions[m.FORGET_POSITIONS].trigger()

    assert len(window._positions) == 0
    assert m.FORGET_POSITIONS not in visible_keys(window)
    assert window._settings.value("window/app_positions") == "[]"


def test_the_layer_and_the_map_are_persisted_and_restored(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._save_settings()
    window._settings.sync()

    reopened = make_window()
    assert reopened._remember_position is True
    assert reopened._positions.recall(VSCODE) == (300, 200)
    assert reopened._menu_actions[m.REMEMBER_POSITION].isChecked() is True


def test_a_corrupt_stored_map_does_not_stop_the_app_starting(make_window):
    window = make_window()
    window._settings.setValue("window/app_positions", "{not json")
    window._settings.setValue("window/remember_position", True)
    window._settings.sync()

    reopened = make_window()
    assert len(reopened._positions) == 0
    assert reopened._remember_position is True


def test_the_window_never_learns_a_position_against_itself(make_window):
    """It should never be frontmost — the window is unfocusable and the
    app is an accessory — but an entry keyed on us could never be recalled
    and would evict a real one."""
    window = remembering(make_window)
    window._own_bundle_id = "com.lyrisync.lyrisync"
    window._frontmost = "com.lyrisync.lyrisync"

    end_a_drag(window, 300, 200)

    assert len(window._positions) == 0


def test_our_own_activation_does_not_become_the_frontmost_app(make_window):
    """Opening the menu bar item can bring an accessory app forward. Taken
    at face value that would replace the app the user is working in with
    ourselves, after which every drag would be refused by the self-filter
    and nothing would be learned for no visible reason. The window follows
    the last app that was not us."""
    window = remembering(make_window, frontmost_app=VSCODE)
    window._own_bundle_id = "com.lyrisync.lyrisync"

    activate(window, "com.lyrisync.lyrisync")

    assert window._frontmost == VSCODE
    end_a_drag(window, 300, 200)
    assert window._positions.recall(VSCODE) == (300, 200)


def test_our_own_activation_does_not_disturb_an_app_that_is_settling(make_window):
    """It is dropped before the debounce, not through it: an arrival that
    was almost due must not be restarted, or reaching for the menu bar
    would cost the move the user was waiting for."""
    window = remembering(make_window)
    window._own_bundle_id = "com.lyrisync.lyrisync"
    window._positions.remember(SAFARI, 400, 300)
    window.move(10, 10)

    activate(window, SAFARI)
    activate(window, "com.lyrisync.lyrisync")

    assert window._debounce.pending == SAFARI
    window._settle_timer.stop()
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()
    finish_move(window)
    assert window.pos() == QPoint(400, 300)


def test_asking_who_is_in_front_refuses_ourselves(make_window):
    """The same rule at the other door: switching the layer on from a menu
    opened over our own window must not seed the frontmost app as us, or the
    first drag would be refused for a reason the user cannot act on."""
    window = make_window()
    window._own_bundle_id = "com.lyrisync.lyrisync"
    ourselves = frontmost.AppIdentity("com.lyrisync.lyrisync", "LyriSync")
    with patch.object(w.frontmost, "current_app", return_value=ourselves):
        window._set_remember_position(True)
    assert window._frontmost is None

    with patch.object(
        w.frontmost, "current_app", return_value=frontmost.AppIdentity(VSCODE, "Code")
    ):
        window._set_remember_position(False)
        window._set_remember_position(True)
    assert (window._frontmost, window._frontmost_name) == (VSCODE, "Code")


def test_the_menu_says_what_has_been_learned_and_where_we_are(make_window):
    """The feedback half of the milestone: learning is implicit, so without
    this the only evidence it works is the window happening to move."""
    window = remembering(make_window)
    window._refresh_menu()  # what opening the menu does
    assert m.POSITION_STATUS in visible_keys(window)
    status = window._menu_actions[m.POSITION_STATUS]
    assert "No positions remembered" in status.text()
    assert "Code not placed yet" in status.text()

    end_a_drag(window, 300, 200)

    assert "1 app remembered" in status.text()
    assert "Code is placed" in status.text()


def test_the_readout_names_an_app_it_only_knows_from_the_map(make_window):
    """The name is stored beside the position precisely so an app that is
    not running — and cannot be asked what it is called — is still
    readable in the menu."""
    window = remembering(make_window)
    window._positions.remember(SAFARI, 10, 20, "Safari")
    window._frontmost, window._frontmost_name = SAFARI, None

    window._refresh_menu()

    assert "Safari is placed" in window._menu_actions[m.POSITION_STATUS].text()


def test_the_readout_falls_back_to_the_identifier_with_no_name(make_window):
    """An app never seen running and never placed has no name anywhere.
    Its identifier beats a blank."""
    window = remembering(make_window)
    window._frontmost, window._frontmost_name = "com.unknown.app", None

    window._refresh_menu()

    assert "com.unknown.app" in window._menu_actions[m.POSITION_STATUS].text()


def test_the_readout_is_a_readout_and_not_a_control(make_window):
    window = remembering(make_window)
    assert not window._menu_actions[m.POSITION_STATUS].isEnabled()


def test_the_readout_cannot_be_relocated_by_its_own_text(make_window):
    """The only entry whose text the app does not write: it carries another
    app's name — "System Settings" now that names are shown, and the
    identifier when there is no name. Qt's default text heuristic matches
    substrings either way, and would move this item into the application
    menu: a diagnostic that disappears when you go to read it."""
    window = remembering(make_window)
    status = window._menu_actions[m.POSITION_STATUS]
    assert status.menuRole() == QAction.MenuRole.NoRole

    for bundle_id, name in (
        ("com.apple.systempreferences", None),
        ("com.apple.systempreferences", "System Settings"),
    ):
        window._frontmost, window._frontmost_name = bundle_id, name
        window._refresh_menu()
        assert status.menuRole() == QAction.MenuRole.NoRole
        assert (name or bundle_id) in status.text()


def test_the_readout_follows_the_frontmost_app(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    activate(window, SAFARI)
    window._refresh_menu()

    status = window._menu_actions[m.POSITION_STATUS].text()
    assert "Safari not placed yet" in status
    assert "1 app remembered" in status  # what is known has not changed


def test_reading_the_menu_is_not_using_a_position(make_window):
    """peek, not recall. A glance must not refresh recency, or the eviction
    order would describe where the user has been looking."""
    window = remembering(make_window)
    window._positions = w.AppPositions(limit=2)
    window._positions.remember(VSCODE, 1, 1)
    window._positions.remember(SAFARI, 2, 2)
    window._frontmost = VSCODE

    window._refresh_menu()
    window._positions.remember("com.apple.Notes", 3, 3)

    assert window._positions.peek(VSCODE) is None  # still the oldest


def test_the_learned_name_reaches_the_map(make_window):
    """Learned from the activation that brought the app forward, so the map
    can name it long after it has quit."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    assert window._positions.name_for(VSCODE) == "Code"
    assert '"Code"' in window._settings.value("window/app_positions")


# -- the list of remembered apps ------------------------------------------


def listed_apps(window):
    """The app rows, without the hint that explains what clicking does."""
    return [
        action.text()
        for action in window._positions_menu.actions()
        if action.isEnabled() and not action.isSeparator()
    ]


def test_the_remembered_apps_menu_lists_them_by_name(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._frontmost, window._frontmost_name = SAFARI, "Safari"
    end_a_drag(window, 40, 60)

    window._rebuild_positions_menu()

    assert listed_apps(window) == ["Safari", "Code"]  # most recently used first


def test_the_remembered_apps_menu_is_rebuilt_from_the_map(make_window):
    """The entries ARE the map, and the map changes without the menu being
    involved — so it is assembled on opening rather than kept in step. That
    is also why it is the one menu here whose contents are rebuilt at all."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._rebuild_positions_menu()
    assert listed_apps(window) == ["Code"]

    window._positions.forget_all()
    window._rebuild_positions_menu()

    assert listed_apps(window) == []


def test_clicking_a_remembered_app_forgets_only_that_one(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._frontmost, window._frontmost_name = SAFARI, "Safari"
    end_a_drag(window, 40, 60)
    window._rebuild_positions_menu()

    safari = next(
        a for a in window._positions_menu.actions() if a.text() == "Safari"
    )
    safari.trigger()

    assert window._positions.peek(SAFARI) is None
    assert window._positions.peek(VSCODE) == (300, 200)
    assert window._settings.value("window/app_positions") == window._positions.to_json()


def test_forgetting_the_last_app_takes_the_list_away_with_it(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    assert m.POSITION_LIST in visible_keys(window)

    window._rebuild_positions_menu()
    next(a for a in window._positions_menu.actions() if a.text() == "Code").trigger()

    assert m.POSITION_LIST not in visible_keys(window)
    assert m.FORGET_POSITIONS not in visible_keys(window)


def test_an_app_with_no_icon_is_still_listed(make_window):
    """Off macOS there are no icons at all, and on it an app can have been
    uninstalled since. A name with no face still reads."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    window._rebuild_positions_menu()

    entry = next(a for a in window._positions_menu.actions() if a.text() == "Code")
    assert entry.icon().isNull()  # the offscreen platform has no AppKit


def test_the_readout_asks_for_an_icon_only_when_the_app_changes(make_window):
    """_refresh_menu runs on every render — three times a second — and
    drawing an icon is two calls into AppKit."""
    window = remembering(make_window)
    asked = []

    with patch.object(w, "_MENU_ICON_POINTS", 16), patch.object(
        window, "_app_icon", side_effect=lambda key: asked.append(key)
    ):
        window._refresh_menu()
        window._refresh_menu()
        window._refresh_menu()
        assert asked == [VSCODE]

        activate(window, SAFARI)
        window._refresh_menu()
        window._refresh_menu()
        assert asked == [VSCODE, SAFARI]


# -- the acknowledgement ---------------------------------------------------


def finish_glow(window):
    """Run the acknowledgement to its end without waiting it out."""
    if window._glow_anim is not None:
        window._glow_anim.setCurrentTime(window._glow_anim.duration())
    APP.processEvents()


def test_a_learned_position_is_acknowledged_on_the_window(make_window):
    """The gesture ends in silence otherwise, which is this feature's
    oldest problem restated: nothing on screen distinguishes a drag that
    was learned from a drag that was not."""
    window = remembering(make_window)
    assert window._glow == 0.0

    end_a_drag(window, 300, 200)

    assert window._glow_anim is not None
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()
    assert window._glow > 0.0


def test_nothing_is_acknowledged_when_nothing_was_learned(make_window):
    """A refused drag must look like a refused drag. The glow says "that
    counted", so it may only appear where something was recorded."""
    window = make_window()  # the layer is off
    window.show()
    window._frontmost = VSCODE
    end_a_drag(window, 300, 200)
    assert window._glow_anim is None
    assert window._glow == 0.0


def test_the_edge_is_handed_back_exactly(make_window):
    """Borrowed, not taken. The album's own edge before and after, to the
    channel, with a cover in hand so there is something to give back."""
    window = remembering(make_window)
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    before = window._painted_border()
    assert before == ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).border

    end_a_drag(window, 300, 200)
    # Halfway, because the shape starts and ends at nothing: at the moment
    # the drag lands there is deliberately no glow yet to see.
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()
    assert window._painted_border() != before  # borrowed
    finish_glow(window)

    assert window._painted_border() == before  # and returned
    assert window._glow == 0.0
    assert window._current_border() == before  # never written into the tint


def test_a_cover_arriving_mid_glow_is_not_captured_with_the_glow_in_it(make_window):
    """The reason the glow is a paint-time mix and not part of the tint
    state: a cross-fade beginning mid-acknowledgement would otherwise take
    a warmed edge as its start and keep some of it for good."""
    window = remembering(make_window)
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    end_a_drag(window, 300, 200)
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()
    window._on_artwork_ready("t1", RED_COVER)  # a cover lands mid-glow
    settle_tint(window)
    finish_glow(window)

    assert window._painted_border() == ap.tinted(
        ap.DARK, RED_COVER, ap.Appearance.DARK
    ).border


def test_the_glow_does_not_fire_twice_for_one_gesture(make_window):
    """One drag is one thing the user did. A second glow starting inside
    the first would read as a flicker rather than as two answers — and a
    release delivered twice is the same case."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    first = window._glow_anim

    window._learn_position()  # as a second release would
    window._learn_position()

    assert window._glow_anim is first


def test_a_later_drag_is_acknowledged_again(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    finish_glow(window)
    window._glow_at -= w.GLOW_SECONDS  # as the clock would have moved on

    end_a_drag(window, 120, 90)

    assert window._glow_anim is not None


def test_the_acknowledgement_leaves_the_line_transition_alone(make_window):
    """Different surfaces, different animations: the glow is a colour on
    the hairline, the line change is an effect on the text. Neither may
    stop the other."""
    window = remembering(make_window)
    load(window, SYNCED, track_id="t9")
    window._on_position_update(snapshot(position=0.0, track_id="t9"))
    fx_before = window._current_fx.progress

    end_a_drag(window, 300, 200)
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()

    assert window._current_fx.progress == fx_before
    assert window._glow > 0.0


def test_the_readout_goes_with_the_layer(make_window):
    """It names the frontmost app, and with the layer off nothing is
    watching which app that is."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._set_remember_position(False)
    assert m.POSITION_STATUS not in visible_keys(window)
    assert m.FORGET_POSITIONS in visible_keys(window)  # the map is still clearable


def test_shutdown_stops_observing(make_window):
    """NSWorkspace holds a block that moves a window being torn down —
    the same hazard as the hotkey, released beside it."""
    window = remembering(make_window)
    window._shutdown()
    assert not window._watcher.active
    assert not window._settle_timer.isActive()


# -- album colour ---------------------------------------------------------


RED_COVER = (200, 40, 40)


def art_snapshot(track_id="t1", url="http://cover", kind="track"):
    return PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id=track_id,
        track_kind=kind,
        title="Song",
        artist="Artist",
        album="Album",
        duration_ms=200000,
        position_seconds=0.0,
        artwork_url=url,
    )


@pytest.fixture
def artwork_tasks(monkeypatch):
    """Record the cover lookups the window starts.

    A recording subclass rather than a patched thread pool: _pool is
    QThreadPool.globalInstance(), a process-wide singleton, so assigning
    to its start() leaks into every test that runs afterwards.
    """
    started = []
    real = w.ArtworkTask

    class Recording(real):
        def __init__(self, provider, track_id, url):
            super().__init__(provider, track_id, url)
            started.append((track_id, url))

    monkeypatch.setattr(w, "ArtworkTask", Recording)
    return started


def painted_background(window):
    """The colour paintEvent actually reaches for, mid-fade included."""
    return window._current_background()


def settle_tint(window):
    """Run the cross-fade to its end without waiting out the animation."""
    if window._tint_anim is not None:
        window._tint_anim.setCurrentTime(w._TINT_FADE_MS)
    APP.processEvents()


def test_album_colour_is_off_by_default(make_window):
    """The layers principle: the plain window is what the app is."""
    window = make_window()
    assert window._album_colour is False
    assert window._menu_actions[m.ALBUM_COLOUR].isChecked() is False
    assert painted_background(window) == ap.DARK.solid


def test_nothing_is_fetched_while_the_layer_is_off(make_window, artwork_tasks):
    """A disabled feature does not get to make network requests."""
    window = make_window()
    window._on_track_change(art_snapshot())
    assert artwork_tasks == []


def test_enabling_it_asks_for_the_current_track(make_window, artwork_tasks):
    """Switched on mid-song, it must not wait for the next track."""
    window = make_window()
    window._on_track_change(art_snapshot())
    assert artwork_tasks == []

    window._menu_actions[m.ALBUM_COLOUR].trigger()
    assert window._album_colour is True
    assert artwork_tasks == [("t1", "http://cover")]


def test_a_cover_colour_tints_the_background(make_window):
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    expected = ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).solid
    assert painted_background(window) == expected
    assert painted_background(window) != ap.DARK.solid


def test_switching_it_off_restores_the_previous_look_exactly(make_window):
    """The acceptance criterion, and the layers principle: off must equal
    the app before this feature existed, to the byte."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert painted_background(window) != ap.DARK.solid

    window._menu_actions[m.ALBUM_COLOUR].trigger()  # off
    settle_tint(window)
    assert window._album_colour is False
    assert painted_background(window) == ap.DARK.solid
    assert window._menu_actions[m.ALBUM_COLOUR].isChecked() is False


def test_a_cover_landing_after_the_layer_is_off_changes_nothing(make_window):
    """Covers are in flight when the toggle is clicked."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._set_album_colour(False)

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert painted_background(window) == ap.DARK.solid


def test_a_cover_for_a_track_that_has_moved_on_is_dropped(make_window):
    """Skipping through tracks puts several lookups in flight at once, and
    the last to land is not the one on screen."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot(track_id="t2"))

    window._on_artwork_ready("t1", RED_COVER)  # the previous track's cover
    settle_tint(window)
    assert painted_background(window) == ap.DARK.solid


def test_a_cover_with_no_usable_colour_leaves_the_window_alone(make_window):
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    window._on_artwork_ready("t1", None)
    settle_tint(window)
    assert painted_background(window) == ap.DARK.solid


def test_the_setting_is_persisted_and_restored(make_window):
    window = make_window()
    window._set_album_colour(True)
    window._settings.sync()

    reopened = make_window()
    assert reopened._album_colour is True
    assert reopened._menu_actions[m.ALBUM_COLOUR].isChecked() is True


def test_the_tint_cross_fades_rather_than_snapping(make_window):
    """A colour that changed in one frame reads as a glitch, not as the
    song changing."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    window._on_artwork_ready("t1", RED_COVER)
    assert window._tint_anim is not None
    assert window._tint_anim.duration() == w._TINT_FADE_MS
    assert painted_background(window) == ap.DARK.solid  # still where it began

    window._tint_anim.setCurrentTime(w._TINT_FADE_MS // 2)
    APP.processEvents()
    midway = painted_background(window)
    assert midway not in (ap.DARK.solid,)

    settle_tint(window)
    assert painted_background(window) == ap.tinted(
        ap.DARK, RED_COVER, ap.Appearance.DARK
    ).solid


def test_a_second_cover_fades_on_from_where_the_first_had_got_to(make_window):
    """Tracks skipped quickly interrupt a fade in progress; restarting
    from the old target would jump backwards first."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    window._tint_anim.setCurrentTime(w._TINT_FADE_MS // 2)
    APP.processEvents()
    midway = painted_background(window)

    window._on_track_change(art_snapshot(track_id="t2"))
    window._on_artwork_ready("t2", (40, 60, 200))
    assert window._tint_from == midway


def test_the_tint_survives_an_appearance_switch(make_window):
    """The cover colour is kept; what it derives from is not. It must come
    out re-derived against the new palette, not carried across."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    set_scheme(Qt.ColorScheme.Light)
    assert window._tint_rgb == RED_COVER
    assert painted_background(window) == ap.tinted(
        ap.LIGHT, RED_COVER, ap.Appearance.LIGHT
    ).solid


def test_the_tint_never_touches_the_text(make_window):
    """Contrast is a promise about the sung line, and the stylesheet is
    where the sung line's colour lives."""
    window = make_window()
    window._set_album_colour(True)
    load(window, SYNCED)
    before = window.styleSheet()

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert window.styleSheet() == before
    assert ap.rgba(ap.DARK.current) in window.styleSheet()


def test_non_music_items_are_never_looked_up(make_window, artwork_tasks):
    """DJ narration and ads reuse other tracks' identity, so a cover
    fetched for one would be cached against the wrong song."""
    window = make_window()
    window._set_album_colour(True)
    artwork_tasks.clear()

    window._on_track_change(art_snapshot(track_id="t9", kind="media"))
    assert artwork_tasks == []


def test_a_track_without_a_cover_is_still_looked_up_from_cache(
    make_window, artwork_tasks
):
    """No URL is not the same as no answer: the colour may already be
    known from a previous play."""
    window = make_window()
    window._set_album_colour(True)
    artwork_tasks.clear()

    window._on_track_change(art_snapshot(url=None))
    assert artwork_tasks == [("t1", None)]


def test_the_artwork_task_never_raises_into_the_pool(make_window):
    """It runs on a pool thread where an exception would be swallowed
    somewhere unhelpful, so it has to catch its own."""

    class Exploding:
        def colour_for(self, track_id, url):
            raise RuntimeError("boom")

    reported = []
    task = w.ArtworkTask(Exploding(), "t1", "http://cover")
    task.signals.finished.connect(lambda tid, colour: reported.append((tid, colour)))
    REAL_ARTWORK_RUN(task)  # the fixture stubs run(); this test is about it
    APP.processEvents()
    assert reported == [("t1", None)]


# -- the global hotkey ----------------------------------------------------


def test_the_hotkey_toggles_the_window(make_window):
    window = make_window()
    window.apply_saved_visibility()
    assert window.isVisible() is True

    window._toggle_lyrics_visible()
    APP.processEvents()
    assert window.isVisible() is False

    window._toggle_lyrics_visible()
    APP.processEvents()
    assert window.isVisible() is True


def test_the_tick_matches_whichever_of_the_two_was_used(make_window):
    """The requirement: one piece of state, two ways to reach it. A press
    after a menu click, and a menu click after a press, both have to leave
    the tick describing the window."""
    window = make_window()
    window.apply_saved_visibility()
    show = window._menu_actions[m.SHOW_LYRICS]

    for act in (
        window._toggle_lyrics_visible,        # hotkey hides
        show.trigger,                          # menu shows
        window._toggle_lyrics_visible,        # hotkey hides again
        window._toggle_lyrics_visible,        # hotkey shows
        show.trigger,                          # menu hides
    ):
        act()
        APP.processEvents()
        assert show.isChecked() is window.isVisible()
        assert show.isChecked() is window._lyrics_visible


def test_the_hotkey_persists_the_same_setting_the_menu_does(make_window):
    window = make_window()
    window._toggle_lyrics_visible()
    assert window._settings.value("window/visible", type=bool) is False
    window._settings.sync()

    assert make_window()._lyrics_visible is False


def test_hiding_by_hotkey_leaves_everything_else_running(make_window):
    """Same contract as the menu entry: the window goes, nothing else
    does."""
    window = make_window()
    load(window, PLAIN)
    window._begin_sync()
    window._on_position_update(snapshot())
    window._on_tap()
    stamped = window._view_model.sync_session.index

    window._toggle_lyrics_visible()
    APP.processEvents()
    assert window.isVisible() is False
    assert window._monitor_thread.isRunning() is True
    assert window._view_model.sync_session.index == stamped


def test_showing_by_hotkey_catches_up_with_the_song(make_window):
    window = make_window()
    load(window, SYNCED)
    window._toggle_lyrics_visible()  # hidden

    window._on_position_update(
        PlayerSnapshot(
            state=PlaybackState.PLAYING,
            track_id="t1",
            title="Song",
            artist="Artist",
            duration_ms=200000,
            position_seconds=6.0,
        )
    )
    window._toggle_lyrics_visible()  # back
    APP.processEvents()
    assert window.isVisible() is True
    assert window._current.text() == "two"


def test_the_window_asks_for_the_one_documented_combination(make_window):
    window = make_window()
    assert window._hotkey.combination is hotkey.TOGGLE_LYRICS


def test_a_refused_hotkey_leaves_the_app_fully_working(make_window, caplog):
    """Registration fails here for real — the fixture hands back no Carbon
    — so this is the "another app owns it" path. Everything the hotkey
    would have done is still reachable from the menu."""
    with caplog.at_level(logging.INFO, logger="lyrisync.window"):
        window = make_window()
    assert window._hotkey.registered is False
    assert "continuing without the global hotkey" in caplog.text

    window._menu_actions[m.SHOW_LYRICS].trigger()
    APP.processEvents()
    assert window.isVisible() is False


def test_the_menu_entry_never_advertises_the_combination(make_window):
    """Two mechanisms firing one action is the drift this app designs
    away, and a label printing ⇧⌘J while another app holds it would be a
    menu claiming something untrue. Qt's shortcut is deliberately unset."""
    window = make_window()
    show = window._menu_actions[m.SHOW_LYRICS]
    assert show.shortcut().isEmpty()
    assert "⌘" not in show.text()


class RecordingCarbon:
    """The far side of hotkey._carbon, so the window's registration and
    release can be watched without claiming anything real."""

    def __init__(self):
        self.registered = 0
        self.released = 0
        self.handler = None

    def GetApplicationEventTarget(self):
        return 0xEE

    def InstallEventHandler(self, target, callback, count, types, user_data, out):
        self.handler = callback
        out.contents.value = 0xA1
        return 0

    def RemoveEventHandler(self, ref):
        return 0

    def RegisterEventHotKey(self, key_code, modifiers, hotkey_id, target, options, out):
        self.registered += 1
        out.contents.value = 0xB2
        return 0

    def UnregisterEventHotKey(self, ref):
        self.released += 1
        return 0

    def press(self):
        self.handler(None, None, None)


@pytest.fixture
def carbon(monkeypatch):
    lib = RecordingCarbon()
    monkeypatch.setattr(w.hotkey, "_carbon", lambda: lib)
    return lib


def test_the_window_registers_on_startup(carbon, make_window):
    window = make_window()
    assert carbon.registered == 1
    assert window._hotkey.registered is True


def test_a_real_press_toggles_the_window(carbon, make_window):
    """End to end on this side of the framework: the callback Carbon was
    handed is the one that hides the lyrics."""
    window = make_window()
    window.apply_saved_visibility()
    assert window.isVisible() is True

    carbon.press()
    APP.processEvents()
    assert window.isVisible() is False
    assert window._menu_actions[m.SHOW_LYRICS].isChecked() is False


def test_shutdown_leaves_no_registration_behind(carbon, make_window):
    """A stale registration after quit is a bug: it would keep swallowing
    the combination from every other app until the process died."""
    window = make_window()
    assert carbon.released == 0

    window._shutdown()
    assert carbon.released == 1
    assert window._hotkey.registered is False


def test_reaching_shutdown_twice_releases_it_once(carbon, make_window):
    """Quit from the menu bar runs _shutdown, and so does aboutToQuit
    behind it; the fixture then runs it a third time on teardown."""
    window = make_window()
    window._shutdown()
    window._shutdown()
    assert carbon.released == 1


# -- quit -----------------------------------------------------------------


def test_quit_from_the_menu_bar_runs_the_clean_shutdown(make_window):
    window = make_window()
    window._set_lyrics_visible(False)  # unreachable except from the menu bar
    assert window._monitor_thread.isRunning() is True

    timed_out = []
    # The rescue timer is owned and stopped rather than fired-and-forgotten:
    # a singleShot that outlives this test is still armed when a later one
    # calls exec(), and quits somebody else's event loop.
    rescue = QTimer()
    rescue.setSingleShot(True)
    rescue.timeout.connect(lambda: (timed_out.append(True), APP.quit()))
    rescue.start(5000)
    QTimer.singleShot(0, window._menu_actions[m.QUIT].trigger)
    APP.exec()
    rescue.stop()

    assert not timed_out, "quit did not come from the menu bar action"
    assert window._monitor_thread.isRunning() is False  # joined
    assert window._pool.activeThreadCount() == 0  # and drained
    window._settings.sync()
    assert make_window()._lyrics_visible is False  # settings saved on the way out


def test_shutdown_waits_for_a_worker_still_running(make_window):
    """The other half of a clean quit.

    A fetch blocked in a socket, or `say` reading a line out, outlives
    exec() by as long as it takes — and used to be left to report into a
    window that teardown was already destroying. Shutdown now waits for
    it, so by the time anything is torn down there is nobody left holding
    a reference to it.
    """
    window = make_window()
    finished = threading.Event()

    class SlowWorker(QRunnable):
        def run(self):
            time.sleep(0.2)
            finished.set()

    window._pool.start(SlowWorker())
    assert not finished.is_set()  # genuinely still running

    window._shutdown()

    assert finished.is_set(), "shutdown returned with a worker still going"
    assert window._pool.activeThreadCount() == 0


def test_shutdown_drops_work_that_never_started(make_window):
    """Queued-but-unstarted tasks are cleared rather than waited for: a
    fetch that has not begun has nothing to finish, and quitting should
    not first work through a backlog."""
    window = make_window()
    started = []
    release = threading.Event()

    class Blocker(QRunnable):
        def run(self):
            started.append(True)
            release.wait(timeout=5)

    class NeverRuns(QRunnable):
        def run(self):
            started.append("queued task ran")

    for _ in range(window._pool.maxThreadCount()):
        window._pool.start(Blocker())
    window._pool.start(NeverRuns())  # no thread free: queued, not started

    # Freed only once shutdown is already under way, so the queued task is
    # cleared before any thread could pick it up.
    threading.Timer(0.15, release.set).start()
    window._shutdown()

    assert "queued task ran" not in started
    assert started.count(True) == window._pool.maxThreadCount()  # those ran


def test_shutdown_survives_a_worker_that_will_not_come_back(make_window, caplog):
    """The wait is bounded. `say` can hold a line for a minute and quit
    cannot hang on it, so an undrainable worker is logged and left to the
    pool's own destructor rather than blocking the exit forever."""
    release = threading.Event()

    class Stuck(QRunnable):
        def run(self):
            release.wait(timeout=30)

    window = make_window()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(w, "_SHUTDOWN_WAIT_MS", 50)
    window._pool.start(Stuck())
    try:
        with caplog.at_level(logging.WARNING, logger="lyrisync.window"):
            window._shutdown()
        assert "worker still running at shutdown" in caplog.text
    finally:
        monkeypatch.undo()
        release.set()
        window._pool.waitForDone(5000)


# -- activation policy ----------------------------------------------------


# -- open at login --------------------------------------------------------


def test_open_at_login_is_hidden_when_running_from_source(make_window):
    """The suite runs from a checkout, which is the case every developer
    sees: no bundle for macOS to launch, so no switch."""
    window = make_window()
    assert window._bundled is False
    load(window, SYNCED)
    assert m.OPEN_AT_LOGIN not in visible_keys(window)


def test_open_at_login_appears_for_a_bundle(make_window):
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.NOT_REGISTERED
    load(window, SYNCED)
    assert m.OPEN_AT_LOGIN in visible_keys(window)


def test_the_entry_follows_the_system_not_the_stored_preference(make_window):
    """The requirement this feature turns on: the tick is macOS's answer.
    A user who switches this off in System Settings must see it switched
    off here, whatever this app last wrote down."""
    window = make_window()
    window._bundled = True
    window._settings.setValue("window/open_at_login", True)  # what we asked for

    window._login_status = login_item.LoginItemStatus.NOT_REGISTERED  # what is true
    window._refresh_menu()
    assert window._menu_actions[m.OPEN_AT_LOGIN].isChecked() is False

    window._login_status = login_item.LoginItemStatus.ENABLED
    window._refresh_menu()
    assert window._menu_actions[m.OPEN_AT_LOGIN].isChecked() is True


def test_awaiting_approval_stays_unchecked_and_says_why(make_window):
    """Registered but not yet approved is not enabled. The entry must not
    claim a launch that will not happen, and the label is the only place
    that can point at System Settings."""
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.REQUIRES_APPROVAL
    window._refresh_menu()

    action = window._menu_actions[m.OPEN_AT_LOGIN]
    assert action.isChecked() is False
    assert "System Settings" in action.text()


def test_the_label_returns_to_normal_once_approved(make_window):
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.REQUIRES_APPROVAL
    window._refresh_menu()
    window._login_status = login_item.LoginItemStatus.ENABLED
    window._refresh_menu()
    assert window._menu_actions[m.OPEN_AT_LOGIN].text() == login_item.MENU_LABEL


def test_toggling_registers_and_records_what_was_asked(make_window, monkeypatch):
    window = make_window()
    window._bundled = True
    asked = []

    def fake_set(enabled):
        asked.append(enabled)
        return True, (
            login_item.LoginItemStatus.ENABLED
            if enabled
            else login_item.LoginItemStatus.NOT_REGISTERED
        )

    monkeypatch.setattr(login_item, "set_enabled", fake_set)

    window._set_open_at_login(True)
    assert asked == [True]
    assert window._settings.value("window/open_at_login", type=bool) is True
    assert window._menu_actions[m.OPEN_AT_LOGIN].isChecked() is True

    window._set_open_at_login(False)
    assert asked == [True, False]
    assert window._menu_actions[m.OPEN_AT_LOGIN].isChecked() is False


def test_a_failed_registration_leaves_the_entry_unchecked(make_window, monkeypatch, caplog):
    """Rather than lying about it. The user clicked, macOS refused, and
    the menu has to show the refusal."""
    window = make_window()
    window._bundled = True
    monkeypatch.setattr(
        login_item,
        "set_enabled",
        lambda enabled: (False, login_item.LoginItemStatus.REQUIRES_APPROVAL),
    )
    with caplog.at_level(logging.WARNING, logger="lyrisync.window"):
        window._set_open_at_login(True)

    action = window._menu_actions[m.OPEN_AT_LOGIN]
    assert action.isChecked() is False
    assert "System Settings" in action.text()
    assert "Open at Login stays off" in caplog.text


def test_opening_the_menu_rereads_the_system(make_window, monkeypatch):
    """Not cached: the user can change this in System Settings while the
    app runs, so every opening asks again."""
    window = make_window()
    window._bundled = True
    answers = iter(
        [login_item.LoginItemStatus.ENABLED, login_item.LoginItemStatus.NOT_REGISTERED]
    )
    monkeypatch.setattr(login_item, "status", lambda: next(answers))

    window._menu.aboutToShow.emit()
    assert window._menu_actions[m.OPEN_AT_LOGIN].isChecked() is True
    window._menu.aboutToShow.emit()
    assert window._menu_actions[m.OPEN_AT_LOGIN].isChecked() is False


def test_a_source_run_never_asks_the_system(make_window, monkeypatch):
    """No bundle, no question: the entry is hidden, and asking would be
    asking about an app that does not exist as far as macOS is
    concerned."""
    window = make_window()
    window._bundled = False
    asked = []
    monkeypatch.setattr(
        login_item,
        "status",
        lambda: asked.append(True) or login_item.LoginItemStatus.ENABLED,
    )
    window._menu.aboutToShow.emit()
    assert asked == []


def test_the_all_desktops_toggle_cannot_touch_the_activation_policy():
    """Accessory is applied once at startup and never revoked, so no toggle
    state can bring the Dock icon (or the Space switch) back."""
    assert not hasattr(w.LyricsWindow, "_apply_activation_policy")
    assert callable(w.apply_accessory_policy)
    source = w.LyricsWindow._apply_all_desktops.__doc__ or ""
    assert "activation policy is NOT part of this" in source
