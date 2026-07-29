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
    QRectF,
    QRunnable,
    QSettings,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

try:
    APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - platform plugin missing
    pytest.skip(f"Qt cannot start: {exc}", allow_module_level=True)

from lyrisync import appearance as ap  # noqa: E402
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


def snapshot(track_id="t1", title="Song", state=PlaybackState.PLAYING):
    return PlayerSnapshot(
        state=state,
        track_id=track_id,
        title=title,
        artist="Artist",
        album="Album",
        duration_ms=200000,
        position_seconds=0.0,
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


def test_a_cancelled_schedule_leaves_no_timers_armed(make_window):
    window = synced_window(make_window)
    window._on_position_update(snapshot())
    window._cancel_line_schedule()
    assert not window._fadeout_timer.isActive()
    assert not window._swap_timer.isActive()
    assert window._current_fx.progress == 0.0


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


def test_the_hairline_is_never_tinted_by_the_album(make_window):
    """A coloured edge reads as a border; this one is meant to read as an
    edge."""
    for palette, appearance in (
        (ap.DARK, ap.Appearance.DARK),
        (ap.LIGHT, ap.Appearance.LIGHT),
    ):
        tinted = ap.tinted(palette, (200, 40, 40), appearance)
        assert tinted.border == palette.border


def test_the_shadow_is_guarded_off_cocoa(make_window):
    """No NSWindow on the offscreen platform, so these must be no-ops
    rather than crashes — which is what keeps the suite headless."""
    window = make_window()
    window._apply_shadow()
    window._invalidate_shadow()


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
