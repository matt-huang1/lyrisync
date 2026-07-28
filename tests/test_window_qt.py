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

import os

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

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

try:
    APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - platform plugin missing
    pytest.skip(f"Qt cannot start: {exc}", allow_module_level=True)

from lyrisync import menu as m  # noqa: E402
from lyrisync import window as w  # noqa: E402
from lyrisync.lyrics_provider import LyricsProvider, TrackLyrics  # noqa: E402
from lyrisync.player_monitor import PlaybackState, PlayerSnapshot  # noqa: E402
from lyrisync.view_model import Mode  # noqa: E402


PLAIN = TrackLyrics(plain="first line\nsecond line\nthird line")
SYNCED = TrackLyrics(synced=[(1.0, "one"), (5.0, "two")])
KOREAN_SYNCED = TrackLyrics(synced=[(1.0, "안녕하세요"), (5.0, "잘 가")])


@pytest.fixture(autouse=True)
def no_spotify(monkeypatch):
    """Nothing here may reach the real Spotify.

    The polling thread runs and joins like the real one so shutdown is
    exercised for real, but never shells out. The player commands matter
    just as much: entering a sync pass dispatches a seek-to-0 and a resume,
    and on a developer's Mac osascript would happily restart whatever they
    were listening to — or launch Spotify to do it.
    """

    def fake_run(self):
        self._monitor._running = True
        while self._monitor._running:
            self.msleep(5)

    monkeypatch.setattr(w.MonitorThread, "run", fake_run)
    for task in (w.PlayerCommandTask, w.SeekTask, w.SpeakTask):
        monkeypatch.setattr(task, "run", lambda self: None)


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
        window = w.LyricsWindow(provider=provider, settings=settings)
        window.resize(460, 220)
        windows.append(window)
        return window

    yield factory
    for window in windows:
        window._shutdown()
        window.hide()
        window.deleteLater()
    APP.processEvents()


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


# -- quit -----------------------------------------------------------------


def test_quit_from_the_menu_bar_runs_the_clean_shutdown(make_window):
    window = make_window()
    window._set_lyrics_visible(False)  # unreachable except from the menu bar
    assert window._monitor_thread.isRunning() is True

    timed_out = []
    QTimer.singleShot(0, window._menu_actions[m.QUIT].trigger)
    QTimer.singleShot(5000, lambda: (timed_out.append(True), APP.quit()))
    APP.exec()

    assert not timed_out, "quit did not come from the menu bar action"
    assert window._monitor_thread.isRunning() is False  # joined
    window._settings.sync()
    assert make_window()._lyrics_visible is False  # settings saved on the way out


# -- activation policy ----------------------------------------------------


def test_the_all_desktops_toggle_cannot_touch_the_activation_policy():
    """Accessory is applied once at startup and never revoked, so no toggle
    state can bring the Dock icon (or the Space switch) back."""
    assert not hasattr(w.LyricsWindow, "_apply_activation_policy")
    assert callable(w.apply_accessory_policy)
    source = w.LyricsWindow._apply_all_desktops.__doc__ or ""
    assert "activation policy is NOT part of this" in source
