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
from pathlib import Path
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
    QRect,
    QRectF,
    QRunnable,
    QSettings,
    Qt,
    QTimer,
)
from PySide6.QtGui import (  # noqa: E402
    QCursor,
    QFontMetricsF,
    QMouseEvent,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - platform plugin missing
    pytest.skip(f"Qt cannot start: {exc}", allow_module_level=True)

from sottovoce import accessibility  # noqa: E402
from sottovoce import appearance as ap  # noqa: E402
from sottovoce import frontmost  # noqa: E402
from sottovoce import hotkey  # noqa: E402
from sottovoce import login_item  # noqa: E402
from sottovoce import menu as m  # noqa: E402
from sottovoce import menubar as mb  # noqa: E402
from sottovoce import notifications as n  # noqa: E402
from sottovoce import player_events  # noqa: E402
from sottovoce import proximity  # noqa: E402
from sottovoce import settings as preferences  # noqa: E402
from sottovoce import vibrancy  # noqa: E402
from sottovoce import window as w  # noqa: E402
from sottovoce.artwork import ArtworkProvider  # noqa: E402
from sottovoce.failure import FetchFailure  # noqa: E402
from sottovoce.lyrics_provider import LyricsProvider, TrackLyrics  # noqa: E402
from sottovoce.player_monitor import PlaybackState, PlayerSnapshot  # noqa: E402
from sottovoce.view_model import Mode  # noqa: E402


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
    # Answered rather than blocked for the third time, and for the third
    # time because every window built here reads it: with no workspace the
    # accessibility door returns DisplayOptions() — nothing switched on,
    # which is the plain window every other test in this file describes.
    # Tests that want a setting on assign window._display_options.
    monkeypatch.setattr(w.accessibility, "_workspace", lambda: None)
    # Answered rather than blocked for the fourth time, and for the fourth
    # reason that is the same reason: every window built here starts the
    # announcer, and handing back None is the branch a machine without
    # pyobjc takes — so the real PlaybackAnnouncer runs its real code and
    # finds nothing to observe, which is also the case the monitor's fast
    # rate exists for. The conftest guard stays armed for anything that
    # reaches around it.
    monkeypatch.setattr(player_events, "_distributed_center", lambda: None)
    # Answered rather than blocked for the fifth time, and the fifth reason
    # is the same one: every window built here builds a menu and asks the
    # menu bar for an item, and handing back None is the branch a machine
    # without AppKit takes — so the real NativeMenu and StatusItem run
    # their real code, find nothing to draw with, and the window carries on
    # with a menu that is pure state. The conftest guard stays armed for
    # anything that reaches around it, and it is the loudest of the lot:
    # unblocked, a suite run leaves a glyph in the developer's menu bar per
    # window and a modal menu tracking loop in the middle of the run.
    monkeypatch.setattr(w.nsmenu, "_appkit", lambda: None)


@pytest.fixture
def make_window(tmp_path):
    """Windows wired to a settings file of their own.

    The settings object is injected rather than redirected globally:
    QSettings.setDefaultFormat/setPath are process-wide and silently do
    nothing on macOS, so a test that trusted them would write into the real
    ~/Library/Preferences entry and stamp on the user's saved window.
    """
    settings_path = tmp_path / "sottovoce-test.ini"
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
    assert window._settings.fileName() == str(tmp_path / "sottovoce-test.ini")
    window._set_lyrics_visible(False)
    window._settings.sync()
    assert (tmp_path / "sottovoce-test.ini").exists()


def test_an_injected_settings_file_is_never_migrated_into(make_window):
    """The carry from the LyriSync name runs on the file the window opens
    for itself and on no other. An injected settings object is the
    caller's and arrives complete; copying an older app's preferences into
    it is not the constructor's business — and it is what keeps the whole
    suite off ~/Library/Preferences/com.lyrisync.lyrisync.plist by
    construction rather than by remembering to stub something.

    The legacy door is guarded in conftest, so a regression here would
    also fail as an escape. This says it in the window's own terms.
    """
    window = make_window()
    assert window._settings.value(preferences.MIGRATION_KEY) is None


def test_the_title_card_gives_the_window_back_as_soon_as_lyrics_land(make_window):
    """The card is a floor on how long the gap LOOKS, not on how long it
    lasts. It used to run its full two seconds whatever happened
    underneath, so lyrics that arrived in 900ms sat behind the song's name
    for another 1.1 seconds — a delay the app was adding to every track.
    """
    window = make_window()
    window._on_track_change(snapshot())
    APP.processEvents()
    # The card is up: the song announces itself while the fetch is out.
    assert window._card_active() is True
    assert window._card_on_screen() is True
    assert window._current.text() == window._view_model.display().header

    window._on_fetch_finished("t1", SYNCED, True)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()

    # Still inside the two seconds, and already showing the song.
    assert window._card_active() is True
    assert window._card_on_screen() is False
    assert window._current.text() == "one"


def test_the_card_still_covers_a_song_joined_before_its_first_line(make_window):
    """Ending it here would trade two seconds of the song's name for ten
    seconds of an empty window."""
    window = make_window()
    window._on_track_change(snapshot())
    window._on_fetch_finished("t1", SYNCED, True)
    window._on_position_update(snapshot(position=0.2))  # first line is at 1.0
    APP.processEvents()

    assert window._card_on_screen() is True
    assert window._current.text() == window._view_model.display().header


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


def land(window):
    """Run the hide/show flight to its end without waiting it out.

    The window now travels to and from the menu bar item, so "hidden" is
    where the journey lands rather than what the click does — the same
    shape as finish_move for the travel to a remembered position.
    """
    if window._flight_anim is not None:
        window._flight_anim.setCurrentTime(window._flight_anim.duration())
    APP.processEvents()


def visible_keys(window):
    """The menu's visible entries, in menu order, as menu.py keys."""
    return tuple(key for key in m.MENU_ORDER if window._menu.is_visible(key))


# -- one menu, two ways in ------------------------------------------------


class FakeStatusItem:
    """The far side of nsmenu.StatusItem, so the menu bar item's whole life
    can be watched without putting a glyph on the developer's menu bar.

    A fake rather than the real thing under a stubbed door, because what
    every test here is about is what the WINDOW does with the item: which
    menu it hands over, when it redraws the glyph, and that it gives the
    item back at shutdown.
    """

    frame_rect = (1159.0, 1073.0, 38.0, 34.0)

    def __init__(self):
        self.tooltip = None
        self.images = []
        self.menu = None
        self.released = 0

    def create(self, tooltip=""):
        self.tooltip = tooltip
        return True

    def set_menu(self, menu):
        self.menu = menu

    def set_image(self, png, points):
        self.images.append((png, points))

    def frame(self):
        return self.frame_rect

    def release(self):
        self.released += 1


@pytest.fixture
def with_tray(monkeypatch):
    """Force the menu bar item into existence, as a fake.

    Nothing native can be built here: the door is answered with None for
    every window in this file, and the conftest guard would fail any test
    that reached around it. So the item the window builds is this one, and
    what is being checked is the window's half of the arrangement.
    """
    items = []

    def make_item():
        item = FakeStatusItem()
        items.append(item)
        return item

    monkeypatch.setattr(w.nsmenu, "StatusItem", make_item)
    return items


def right_click_at(window, x=10, y=10):
    """A context-menu event, as Qt would deliver one. Only the global
    position is read, which is the point that crosses into Cocoa."""

    class Event:
        def globalPos(self):
            return QPoint(x, y)

    return Event()


class FakeMenuView:
    """The far side of nsmenu.NativeMenu: what a drawn menu would be told."""

    def __init__(self):
        self.applied = 0
        self.rows = {}
        self.popups = []

    def apply(self, menu):
        self.applied += 1

    def set_rows(self, key, rows):
        self.rows[key] = rows

    def popup(self, x, y):
        self.popups.append((x, y))
        return True


def test_the_menu_bar_item_and_the_right_click_share_one_menu(with_tray, make_window):
    """The whole point of the milestone: one model, one drawing of it, two
    ways in. The item is handed the same view object the window pops up."""
    window = make_window()
    view = FakeMenuView()
    window._menu.attach(view)
    window._tray.set_menu(window._menu.view)

    assert window._tray.menu is view
    window.contextMenuEvent(right_click_at(window))
    assert view.popups, "the right-click did not open the shared menu"


def test_the_menu_bar_item_is_given_the_menu_and_a_glyph(with_tray, make_window):
    window = make_window()
    assert window._tray.tooltip == "SottoVoce"
    assert window._tray.images, "no glyph was ever drawn"
    png, points = window._tray.images[0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert points == mb.GLYPH_UNITS


def test_no_menu_bar_is_survivable(monkeypatch, make_window):
    """Nothing else may depend on the menu bar item existing. Forced rather
    than relying on the platform, so the path is exercised wherever the
    suite runs."""

    class NoMenuBar(FakeStatusItem):
        def create(self, tooltip=""):
            return False

    monkeypatch.setattr(w.nsmenu, "StatusItem", NoMenuBar)
    window = make_window()
    assert window._tray is None
    assert window._menu is not None
    window._refresh_menu()
    window._set_lyrics_visible(False)
    land(window)
    assert window.isVisible() is False


def test_the_item_is_given_back_at_shutdown(with_tray, make_window):
    """Qt used to own the item and destroy it with the widget. An item this
    app made is one this app has to remove, or it outlives the window that
    answers its menu."""
    window = make_window()
    item = window._tray
    window._shutdown()
    assert item.released == 1
    assert window._tray is None


def test_where_the_item_is_comes_back_in_qt_coordinates(with_tray, make_window):
    """The flight aims at the item, and the flight thinks in Qt rectangles.
    Cocoa measures up from the bottom of the primary screen; this is the one
    place that subtraction happens."""
    window = make_window()
    height = APP.primaryScreen().geometry().height()
    assert window._menubar_item_rect() == (1159, height - 1073 - 34, 38, 34)


def test_the_menu_is_built_once_and_never_rebuilt(make_window):
    """Structure is fixed; only visibility, check marks, chosen presets and
    two labels move. A rebuilt native menu bar item would flicker under the
    user while they were reading it."""
    window = make_window()
    view = FakeMenuView()
    window._menu.attach(view)
    load(window, SYNCED)
    load(window, PLAIN, track_id="t2")
    window._refresh_menu()
    assert window._menu.entries is m.MENU
    assert view.applied > 1, "the state was never pushed at the drawing"
    assert view.rows == {}, "something rebuilt a submenu without being asked"


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
        m.COMPACT,
        m.ALBUM_COLOUR,
        m.SEPARATOR_AFTER_WINDOW,
        m.POSITION_MENU,
        m.DOCK_TOP,
        m.SEPARATOR_AFTER_DOCK,
        m.REMEMBER_POSITION,
        m.SYSTEM_MENU,
        m.ALL_DESKTOPS,
        m.YIELD_NOTIFICATIONS,
        m.PROXIMITY,
        m.MENUBAR_ANIMATION,
        m.SEPARATOR_BEFORE_QUIT,
        m.QUIT,
    )


def test_sync_entry_label_switches_once_a_user_sync_exists(make_window):
    window = make_window()
    load(window, PLAIN)
    assert window._menu.is_visible(m.SYNC)
    assert window._menu.label(m.SYNC) == "Sync this song"

    window._provider.save_user_sync("t1", "[00:01.00] first line\n")
    window._refresh_menu()
    assert window._menu.label(m.SYNC) == "Re-sync this song"


def test_quit_is_visible_in_every_state(make_window):
    window = make_window()
    for lyrics in (None, PLAIN, SYNCED):
        if lyrics is not None:
            load(window, lyrics)
        window._refresh_menu()
        assert window._menu.is_visible(m.QUIT)


# -- toggles drive the same state from the menu ---------------------------


def test_toggling_from_the_menu_updates_state_and_settings(make_window):
    window = make_window()
    load(window, KOREAN_SYNCED)

    window._menu.trigger(m.ROMANISATION)
    assert window._view_model.romanisation_enabled is True
    assert window._settings.value("lyrics/romanisation", type=bool) is True

    window._menu.trigger(m.ECHO)
    assert window._echo_enabled is True
    assert window._loop.echo is True

    window._menu.trigger(m.SPEECH_RATE, 160)
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

    window._menu.trigger(m.SHOW_LYRICS)  # unchecks -> hide
    land(window)
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
    land(window)
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
    assert reopened._menu.is_checked(m.SHOW_LYRICS) is False

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
    assert not any("appearance" in key for key in m.MENU_ORDER)
    assert not any("theme" in key for key in m.MENU_ORDER)
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


# -- the line change: what it costs to draw --------------------------------
#
# `sourcePixmap` re-renders the whole source widget — two labels, so two
# text layouts and two runs of glyph rasterisation — and Qt has no cache
# for it on a widget source. Measured with the sampler on a real window,
# that render was the single largest thing in a line change, and inside it
# QPainter::drawText alone was a third of every frame's paint. Nothing
# about the source moves during a phase, so it is rasterised once per
# phase and not once per frame.


class CountingFade(w.LineFade):
    """The real effect, with the one expensive call counted.

    A subclass rather than a patch, for the reason the pool has: assigning
    to something process-wide leaks into every test that runs afterwards.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renders = 0

    def sourcePixmap(self, *args, **kwargs):
        self.renders += 1
        return super().sourcePixmap(*args, **kwargs)


def counting_window(make_window):
    """A window whose sung line is behind a counting effect.

    Shown, because a hidden widget does not paint and an effect that is
    never drawn counts nothing: the first version of this measured zero
    renders and would have passed for any implementation at all.
    """
    window = synced_window(make_window)
    effect = CountingFade(window._current_box)
    window._current_fx = effect
    window._current_box.setGraphicsEffect(effect)
    window._apply_motion()
    window.show()
    APP.processEvents()
    window._current_box.repaint()
    assert effect.renders, "the effect is not being drawn at all"
    return window, effect


def repaint(window, times=1):
    """Force the effect to draw, the way an animation frame does."""
    for _ in range(times):
        window._current_box.repaint()


def test_a_phase_rasterises_the_line_once_however_many_frames_it_has(make_window):
    window, effect = counting_window(make_window)
    window._animate_line(-1.0, QEasingCurve.Type.InSine)
    effect.renders = 0
    for step in range(1, 9):
        effect.progress = -step / 8.0
        repaint(window)
    assert effect.renders == 1, "re-rendered the same words once a frame"


def test_a_repaint_that_is_not_a_frame_rasterises_again(make_window):
    """The state the window spends almost all of its time in. A repaint
    arriving without progress having moved is not a frame of an animation,
    so the ordinary case is exactly what it always was — and a funnel
    somebody forgets to invalidate is caught here rather than shown
    stale."""
    window, effect = counting_window(make_window)
    repaint(window)
    effect.renders = 0
    repaint(window, times=3)
    assert effect.renders == 3


@pytest.mark.parametrize(
    ("what", "change"),
    [
        ("the words", lambda win: win._set_line_text(win._current, "a new line")),
        ("the romanisation", lambda win: win._set_pronunciation("saeroun")),
        ("the romanisation going", lambda win: win._set_pronunciation("")),
        ("the type and colour", lambda win: win._restyle()),
    ],
)
def test_anything_that_changes_the_line_drops_what_was_drawn_of_it(
    make_window, what, change
):
    """Mid-phase, which is the only time it can matter and the only time
    it is hard: a resize re-elides the line while it is moving, and an
    appearance change repaints it. Either one drawn from the cache would
    be the line as it used to be."""
    window, effect = counting_window(make_window)
    window._animate_line(-1.0, QEasingCurve.Type.InSine)
    effect.progress = -0.25
    repaint(window)
    effect.renders = 0

    change(window)
    effect.progress = -0.5  # the phase carries on
    repaint(window)
    assert effect.renders == 1, f"{what} changed and the old drawing was reused"


def test_a_resize_mid_change_re_elides_and_is_not_drawn_from_the_cache(make_window):
    """The route the parametrised case above stands in for, driven for
    real: the strip elides against the window's width, so a drag while a
    line is moving changes the words themselves."""
    window, effect = counting_window(make_window)
    window._compact_applied = True
    window._animate_line(-1.0, QEasingCurve.Type.InSine)
    effect.progress = -0.25
    repaint(window)
    effect.renders = 0

    window._relayout()
    effect.progress = -0.5
    repaint(window)
    assert effect.renders == 1


def pixels_of(image, rect=None):
    """The raw bytes of an image, or of one rectangle of it.

    The copy is BOUND before it is read, and that is the whole of this
    function. ``image.copy(rect).constBits()`` hands back a memoryview onto
    a temporary QImage that PySide does not keep alive for it, so the copy
    can be released before ``tobytes()`` reads the buffer and the read
    returns whatever the allocator has since put there.

    That is not a theory. Written the short way, the straight-band test
    failed 9 runs in 20 of its own node, with the panel's colour reading as
    zeroes in a band that had been drawn correctly; written this way, 0 in
    40. It never failed in a full-suite run, which is how it survived a
    whole milestone being read as an intermittent bug in the painting.
    """
    held = image if rect is None else image.copy(rect)
    return held.constBits().tobytes()


def panel_pixels(window, damaged, straight, ratio):
    """What _paint_panel puts on an image for ``damaged``.

    ``straight`` picks the route by lying to the band test, which is what
    makes the two comparable: the same call, the same painter settings,
    the same window, one branch apart.

    ``ratio`` is the screen's device pixel ratio, because that is what the
    hairline's width is derived from and 1 is the one value a Mac never
    has. The image is allocated in DEVICE pixels and told its ratio, so
    the rasteriser works where the difference could actually show.
    """
    from PySide6.QtGui import QImage, QPainter

    image = QImage(
        int(window.width() * ratio),
        int(window.height() * ratio),
        QImage.Format.Format_ARGB32,
    )
    image.setDevicePixelRatio(ratio)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    with patch.object(w.LyricsWindow, "_straight_band", lambda self, r: straight), \
            patch.object(w.LyricsWindow, "devicePixelRatioF", lambda self: ratio):
        window._paint_panel(painter, QRectF(damaged))
    painter.end()
    return image


@pytest.mark.parametrize("ratio", [1.0, 2.0])
@pytest.mark.parametrize("glow", [0.0, 0.5, 1.0])
def test_a_band_is_drawn_straight_and_it_is_the_same_pixels(make_window, glow, ratio):
    """The claim, checked rather than reasoned about. Between the corner
    radii the panel is a rectangle with a line down each side, so three
    axis-aligned fills produce exactly what the two rounded rectangles do
    — including at 2x, where the hairline is half a logical pixel wide and
    a rounding difference between the two routes would be visible, and
    while the edge is thickened by an acknowledged position."""
    window = make_window()
    window._glow = glow
    for top in range(w._CORNER_RADIUS, window.height() - w._CORNER_RADIUS - 8, 7):
        damaged = QRect(0, top, window.width(), 8)
        fast = panel_pixels(window, damaged, True, ratio)
        slow = panel_pixels(window, damaged, False, ratio)
        device = QRect(
            int(damaged.x() * ratio), int(damaged.y() * ratio),
            int(damaged.width() * ratio), int(damaged.height() * ratio),
        )
        assert pixels_of(fast, device) == pixels_of(slow, device), (
            f"the band at y={top} differs at {ratio}x"
        )


def test_the_corners_are_not_a_band(make_window):
    """The rounded part has to go through the path, or the window would
    have square corners while a line changed near the top of it."""
    window = make_window()
    assert not window._straight_band(QRectF(0, 0, window.width(), 20))
    assert not window._straight_band(
        QRectF(0, window.height() - 20, window.width(), 20)
    )
    assert not window._straight_band(QRectF(0, 0, window.width(), window.height()))
    middle = QRectF(0, w._CORNER_RADIUS, window.width(), 10)
    assert window._straight_band(middle)


def test_the_line_change_repaints_inside_the_band(make_window):
    """The whole reason the branch exists: the sung line sits between the
    corners, so a line change takes the cheap route 37 times."""
    window = synced_window(make_window)
    window.show()
    APP.processEvents()
    box = window._current_box
    damaged = window._current_fx.boundingRectFor(QRectF(box.rect())).translated(
        box.mapTo(window, box.rect().topLeft())
    )
    assert window._straight_band(damaged)


# -- being told rather than asking ----------------------------------------


def test_the_window_listens_for_spotifys_own_announcement(make_window):
    """Unconditional, like the display watcher and for the same reason: it
    is not a layer with an "off", it is the app being told rather than
    guessing. With no door in the suite the subscription simply finds
    nothing to observe, which is also the case the monitor's fast rate
    exists for."""
    window = make_window()
    assert isinstance(window._announcer, player_events.PlaybackAnnouncer)
    assert window._announcer.listening is False


def test_the_monitor_is_told_whether_anything_is_listening(make_window):
    """And told the truth: with no door in the suite the observer does not
    register, so the monitor must keep asking at its old rate — which is
    the same branch a Mac without pyobjc takes."""
    window = make_window()
    assert window._announcer.listening is False
    assert w.observing() is False
    monitor = window._monitor_thread._monitor
    assert monitor.interval() == monitor.poll_interval


def test_the_announcement_only_ever_rings_the_monitors_bell(make_window):
    """It is delivered on the UI thread and must not touch the window from
    there: what it does is set a flag the monitor's own thread reads."""
    window = make_window()
    assert window._announcer._on_announcement is w.announce


def test_the_observer_is_given_back_before_anything_is_destroyed(make_window):
    """The third thing that can still call in, beside the hotkey and the
    two workspace observers."""
    window = make_window()
    stopped = []
    window._announcer.stop = lambda: stopped.append(True)
    window._shutdown()
    assert stopped == [True]


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
    "com.sottovoce.sottovoce": "SottoVoce",
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
    assert window._menu.is_checked(m.REMEMBER_POSITION) is False
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

    window._menu.trigger(m.FORGET_POSITIONS)

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
    assert reopened._menu.is_checked(m.REMEMBER_POSITION) is True


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
    window._own_bundle_id = "com.sottovoce.sottovoce"
    window._frontmost = "com.sottovoce.sottovoce"

    end_a_drag(window, 300, 200)

    assert len(window._positions) == 0


def test_our_own_activation_does_not_become_the_frontmost_app(make_window):
    """Opening the menu bar item can bring an accessory app forward. Taken
    at face value that would replace the app the user is working in with
    ourselves, after which every drag would be refused by the self-filter
    and nothing would be learned for no visible reason. The window follows
    the last app that was not us."""
    window = remembering(make_window, frontmost_app=VSCODE)
    window._own_bundle_id = "com.sottovoce.sottovoce"

    activate(window, "com.sottovoce.sottovoce")

    assert window._frontmost == VSCODE
    end_a_drag(window, 300, 200)
    assert window._positions.recall(VSCODE) == (300, 200)


def test_our_own_activation_does_not_disturb_an_app_that_is_settling(make_window):
    """It is dropped before the debounce, not through it: an arrival that
    was almost due must not be restarted, or reaching for the menu bar
    would cost the move the user was waiting for."""
    window = remembering(make_window)
    window._own_bundle_id = "com.sottovoce.sottovoce"
    window._positions.remember(SAFARI, 400, 300)
    window.move(10, 10)

    activate(window, SAFARI)
    activate(window, "com.sottovoce.sottovoce")

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
    window._own_bundle_id = "com.sottovoce.sottovoce"
    ourselves = frontmost.AppIdentity("com.sottovoce.sottovoce", "SottoVoce")
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
    assert "No positions remembered" in window._menu.label(m.POSITION_STATUS)
    assert "Code not placed yet" in window._menu.label(m.POSITION_STATUS)

    end_a_drag(window, 300, 200)

    assert "1 app remembered" in window._menu.label(m.POSITION_STATUS)
    assert "Code is placed" in window._menu.label(m.POSITION_STATUS)


def test_the_readout_names_an_app_it_only_knows_from_the_map(make_window):
    """The name is stored beside the position precisely so an app that is
    not running — and cannot be asked what it is called — is still
    readable in the menu."""
    window = remembering(make_window)
    window._positions.remember(SAFARI, 10, 20, "Safari")
    window._frontmost, window._frontmost_name = SAFARI, None

    window._refresh_menu()

    assert "Safari is placed" in window._menu.label(m.POSITION_STATUS)


def test_the_readout_falls_back_to_the_identifier_with_no_name(make_window):
    """An app never seen running and never placed has no name anywhere.
    Its identifier beats a blank."""
    window = remembering(make_window)
    window._frontmost, window._frontmost_name = "com.unknown.app", None

    window._refresh_menu()

    assert "com.unknown.app" in window._menu.label(m.POSITION_STATUS)


def test_the_readout_is_a_readout_and_not_a_control(make_window):
    """It is the one entry in the model that is neither a switch nor a
    command, and the kind is what nsmenu.py disables it from. Asserted on
    the model rather than on a menu item, because the claim is about what
    the entry IS."""
    window = remembering(make_window)
    assert m.ENTRIES[m.POSITION_STATUS].kind == m.READOUT
    assert window._menu.has_handler(m.POSITION_STATUS) is False
    window._menu.trigger(m.POSITION_STATUS)  # lands nowhere, and does not raise


def test_the_readout_carries_another_apps_name_and_stays_where_it_is(make_window):
    """The only entry whose text the app does not write: it carries another
    app's name — "System Settings" now that names are shown, and the
    identifier when there is no name. It used to need QAction.MenuRole.NoRole
    to survive that, because Qt's text heuristic matches substrings and
    would have moved "com.apple.systempreferences" into the application
    menu: a diagnostic that disappears when you go to read it. A native
    NSMenuItem has no such heuristic, so the hazard went with the QMenu."""
    window = remembering(make_window)

    for bundle_id, name in (
        ("com.apple.systempreferences", None),
        ("com.apple.systempreferences", "System Settings"),
    ):
        window._frontmost, window._frontmost_name = bundle_id, name
        window._refresh_menu()
        assert (name or bundle_id) in window._menu.label(m.POSITION_STATUS)


def test_the_readout_follows_the_frontmost_app(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    activate(window, SAFARI)
    window._refresh_menu()

    status = window._menu.label(m.POSITION_STATUS)
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


def readout_rows(window):
    """The app rows, which are all of them: the list is a readout."""
    return list(window._menu.rows(m.POSITION_LIST))


def row_text(row):
    """The name a row shows."""
    labels = [row.label]
    return labels[0] if labels else ""


def listed_apps(window):
    return [row_text(action) for action in readout_rows(window)]


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


def test_the_list_is_a_readout_with_nothing_to_click(make_window):
    """Per-app forget was removed rather than kept. Re-dragging the window
    in an app overwrites its position, so forgetting one app can only mean
    "stop moving the window for this one" — which is not a thing anybody
    wants for one app while wanting it for the others. Forget-all covers
    the wish that is real."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    window._rebuild_positions_menu()

    rows = readout_rows(window)
    assert rows
    for row in rows:
        assert isinstance(row, m.Row)
    # A row is DATA and carries no handler, which is what per-app forget
    # being removed MEANS: there is nothing for a click to reach. The map is
    # untouched by anything the list can do.
    before = window._positions.listed()
    assert window._menu.has_handler(m.POSITION_LIST) is False
    assert window._positions.listed() == before
    assert m.FORGET_POSITIONS in visible_keys(window)


def test_the_rows_are_not_disabled_so_macos_draws_them_normally(make_window):
    """THE 15.1 FIX, carried into the native menu. They were disabled
    QActions when per-app forget was removed, and macOS greys a disabled
    item — so four remembered apps read as four things that were unavailable
    rather than as four facts.

    The answer was a QWidgetAction and is now an NSMenuItem with a view, for
    exactly the same reason: an attributed title with an explicit labelColor
    does NOT help, because AppKit dims a disabled item when it draws it
    whatever the string asked for (measured, on a real menu). The kind is
    what carries the claim, so it is what is asserted here; the brightness
    itself is a question about pixels and is verified by hand.
    """
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._rebuild_positions_menu()

    assert readout_rows(window)
    assert m.ENTRIES[m.POSITION_LIST].kind == m.ROWS
    # The one entry that is neither: a readout the app disables on purpose,
    # because one grey line among ticked entries reads as a note.
    assert m.ENTRIES[m.POSITION_STATUS].kind == m.READOUT


def test_forgetting_everything_takes_the_list_away_with_it(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    assert m.POSITION_LIST in visible_keys(window)

    window._menu.trigger(m.FORGET_POSITIONS)

    assert m.POSITION_LIST not in visible_keys(window)
    assert m.FORGET_POSITIONS not in visible_keys(window)


def test_an_app_with_no_icon_is_still_listed(make_window):
    """Off macOS there are no icons at all, and on it an app can have been
    uninstalled since. A name with no face still reads."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    window._rebuild_positions_menu()

    entry = next(row for row in readout_rows(window) if row.label == "Code")
    # No icon at all: the offscreen platform has no AppKit to ask for one.
    assert entry.icon is None


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
    assert window._menu.is_checked(m.ALBUM_COLOUR) is False
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

    window._menu.trigger(m.ALBUM_COLOUR)
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

    window._menu.trigger(m.ALBUM_COLOUR)  # off
    settle_tint(window)
    assert window._album_colour is False
    assert painted_background(window) == ap.DARK.solid
    assert window._menu.is_checked(m.ALBUM_COLOUR) is False


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
    assert reopened._menu.is_checked(m.ALBUM_COLOUR) is True


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


# -- leaving for the menu bar, and coming back ----------------------------


def half_way(window):
    """Stop a flight in the middle of itself, where everything it borrowed
    is borrowed."""
    window._flight_anim.setCurrentTime(window._flight_anim.duration() // 2)
    APP.processEvents()


def at_menu_bar(window, rect=...):
    """Put the menu bar item somewhere, as macOS would report it.

    The offscreen platform has no menu bar at all, so the real geometry is
    empty everywhere the suite runs — which is the FALLBACK path, and
    testing only that would leave the flight itself unexercised. The
    default sits at the top right of whatever screen this platform claims,
    where a menu bar item actually is.
    """
    if rect is ...:
        screen = APP.primaryScreen().geometry()
        rect = (screen.right() - 200, screen.top(), 38, 34)
    window._menubar_item_rect = lambda: rect


def test_hiding_flies_the_window_to_the_menu_bar(make_window):
    """It used to blink out, which said nothing about where it had gone —
    the way back was something to remember rather than something seen."""
    window = make_window()
    window.apply_saved_visibility()
    window.move(400, 500)
    at_menu_bar(window)

    window._set_lyrics_visible(False)

    assert window._flight_anim is not None
    assert window.isVisible() is True  # still on its way
    half_way(window)
    assert window.pos() != QPoint(400, 500)  # heading for the menu bar
    assert window.windowOpacity() < 1.0
    land(window)
    assert window.isVisible() is False


def test_the_window_is_put_back_exactly_where_it_was(make_window):
    """The flight borrows the window's position for the length of the
    journey. A window that came back an inch from where the user left it
    would be the feature undoing the one it sits next to."""
    window = make_window()
    window.apply_saved_visibility()
    window.move(400, 500)
    at_menu_bar(window)

    window._set_lyrics_visible(False)
    land(window)
    assert window.pos() == QPoint(400, 500)
    assert window.windowOpacity() == window._opacity

    window._set_lyrics_visible(True)
    land(window)
    assert window.pos() == QPoint(400, 500)
    assert window.windowOpacity() == window._opacity


def test_showing_grows_the_window_out_of_the_menu_bar(make_window):
    window = make_window()
    window.apply_saved_visibility()
    window.move(400, 500)
    at_menu_bar(window)
    window._set_lyrics_visible(False)
    land(window)

    window._set_lyrics_visible(True)

    assert window.isVisible() is True  # visible for the whole arrival
    half_way(window)
    assert window.pos() != QPoint(400, 500)
    assert 0.0 < window.windowOpacity() < 1.0
    land(window)
    assert window.pos() == QPoint(400, 500)


def test_an_interrupted_flight_leaves_no_ghost(make_window):
    """The hotkey pressed twice quickly. Whatever happens, the window ends
    up at its own position, at its own opacity, at full size."""
    window = make_window()
    window.apply_saved_visibility()
    window.move(400, 500)
    at_menu_bar(window)

    window._set_lyrics_visible(False)
    half_way(window)
    window._set_lyrics_visible(True)  # changed their mind
    land(window)

    assert window.isVisible() is True
    assert window.pos() == QPoint(400, 500)
    assert window.windowOpacity() == window._opacity
    assert window._flight_anim is None


def test_a_reversal_picks_up_where_the_journey_had_got_to(make_window):
    """Rather than starting again from the beginning, which would be the
    window ignoring the first press."""
    window = make_window()
    window.apply_saved_visibility()
    at_menu_bar(window)

    window._set_lyrics_visible(False)
    half_way(window)
    midway = window.pos()

    window._set_lyrics_visible(True)
    assert window.pos() == midway  # continues from here
    assert window._flight_anim.duration() < w.flight.FLIGHT_MS


def test_shutdown_lands_the_window_before_saving_where_it_is(make_window):
    """Quitting mid-flight must not persist the menu bar's corner as where
    the user left the window."""
    window = make_window()
    window.apply_saved_visibility()
    window.move(400, 500)
    at_menu_bar(window)

    window._set_lyrics_visible(False)
    half_way(window)
    window._shutdown()

    assert window._flight_anim is None
    assert window._settings.value("window/pos") == QPoint(400, 500)
    assert window._settings.value("window/visible", type=bool) is False


def test_a_window_with_no_menu_bar_item_fades_where_it_stands(make_window):
    """Behind the notch, in an overflow, or no menu bar item at all. It
    says less than a flight and it cannot be wrong."""
    window = make_window()
    window.apply_saved_visibility()
    window.move(400, 500)
    at_menu_bar(window, None)  # what a hidden item comes back as

    window._set_lyrics_visible(False)
    half_way(window)

    assert window.pos() == QPoint(400, 500)  # no travel
    assert window.windowOpacity() < 1.0  # but it does fade
    land(window)
    assert window.isVisible() is False
    assert window.windowOpacity() == window._opacity


def test_an_item_off_every_screen_is_not_flown_to(make_window):
    """A stale rectangle after a display change would otherwise throw the
    window off the edge of the world."""
    window = make_window()
    window.apply_saved_visibility()
    window.move(400, 500)
    at_menu_bar(window, (-9000, -9000, 38, 34))

    window._set_lyrics_visible(False)
    half_way(window)

    assert window.pos() == QPoint(400, 500)


def test_the_window_is_not_dragged_while_it_is_flying(make_window):
    """Qt hit-tests the full-size layout even while the content is drawn at
    a fraction of it, so a press would grab something invisible at a
    position about to be given back."""
    window = make_window()
    window.apply_saved_visibility()
    at_menu_bar(window)
    window._set_lyrics_visible(False)
    half_way(window)

    window.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(30, 30),
            QPointF(30, 30),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    assert window._drag_offset is None
    assert not window._resize_edges


def test_the_flight_never_activates_the_app(make_window):
    """The window is unfocusable by design, and hiding it must not be the
    one thing that brings SottoVoce forward. Recorded rather than asserted
    on isActiveWindow, which the offscreen platform answers however it
    likes: what matters is that the flight never ASKS."""
    window = make_window()
    window.apply_saved_visibility()
    at_menu_bar(window)
    asked = []
    window.activateWindow = lambda: asked.append("activate")
    window.raise_ = lambda: asked.append("raise")

    window._set_lyrics_visible(False)
    land(window)
    window._set_lyrics_visible(True)
    land(window)

    assert asked == []
    assert bool(window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_starting_up_does_not_fly(make_window):
    """apply_saved_visibility is the app arriving, not the user asking for
    the window back. A flight there would animate something nobody did."""
    window = make_window()
    window.apply_saved_visibility()
    assert window._flight_anim is None
    assert window.isVisible() is True


# -- the menu bar glyph ---------------------------------------------------


def test_nothing_playing_shows_three_even_bars_at_full_brightness(
    with_tray, make_window
):
    """15.1: nothing playing no longer dims. The shape says there is no
    current line; the brightness says the lyrics are on screen."""
    window = make_window()
    assert window._tray_state.lengths == mb.EVEN_LENGTHS
    assert window._tray_state.dimmed is False
    assert window._tray_state.dot is False


def test_playing_changes_the_shape_and_not_the_brightness(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    before = window._tray_state
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS
    assert window._tray_state.dimmed == before.dimmed is False


def test_hiding_the_lyrics_dims_it_and_leaves_the_shape_alone(
    with_tray, make_window
):
    """The two axes, shown not to interfere: the shape still says a song is
    playing while the brightness says the window is away."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state == mb.IconSpec(mb.PLAYING_LENGTHS, False, False)

    window._set_lyrics_visible(False)
    land(window)
    assert window._tray_state == mb.IconSpec(mb.PLAYING_LENGTHS, True, False)

    window._set_lyrics_visible(True)
    land(window)
    assert window._tray_state == mb.IconSpec(mb.PLAYING_LENGTHS, False, False)


def test_a_practice_mode_adds_the_dot(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))  # a line to loop
    window._last_state = PlaybackState.PLAYING
    window._toggle_loop(True)
    assert window._loop.engaged
    window._refresh_tray_icon()
    assert window._tray_state.dot is True

    window._toggle_loop(False)
    window._refresh_tray_icon()
    assert window._tray_state.dot is False


def test_a_sync_pass_adds_the_dot_too(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    load(window, PLAIN, track_id="t7")
    window._begin_sync()
    window._refresh_tray_icon()
    assert window._tray_state.dot is True


def test_practice_keeps_it_bright_behind_a_hidden_window(with_tray, make_window):
    """A pass keeps running while the lyrics are away, and then the item is
    the only evidence it is going."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, PLAIN, track_id="t7")
    window._begin_sync()
    window._set_lyrics_visible(False)
    land(window)
    assert window._tray_state.dot is True
    assert window._tray_state.dimmed is False


def test_the_glyph_follows_a_pause_without_the_menu_being_opened(
    with_tray, make_window
):
    """THE 15.1 BUG. The icon was refreshed from _render, and a pause does not
    re-render — player_state_changed returns False for PAUSED because the
    display text is unchanged. So the item claimed a song was playing until
    somebody opened the menu. The monitor tick is what fixes it."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS

    window._on_position_update(snapshot(state=PlaybackState.PAUSED))

    assert window._tray_state.lengths == mb.EVEN_LENGTHS


def test_a_state_change_refreshes_it_before_anything_can_return_early(
    with_tray, make_window
):
    """Spotify quitting is the transition after which no more position
    updates arrive, so it is the tick's last chance to put the shape back."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS

    window._on_state_change(snapshot(state=PlaybackState.NOT_RUNNING))

    assert window._tray_state.lengths == mb.EVEN_LENGTHS


def test_the_glyph_is_set_only_when_it_changes(with_tray, make_window):
    """The refresh now runs on every monitor tick — three times a second —
    and handing the same icon back to an NSStatusItem that often is the menu
    bar item being rebuilt under the user."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))

    before = len(window._tray.images)
    for _ in range(5):
        window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert len(window._tray.images) == before

    window._on_position_update(snapshot(state=PlaybackState.PAUSED))
    assert len(window._tray.images) == before + 1


def test_each_glyph_is_drawn_once_and_kept(with_tray, make_window):
    """Eight combinations, times four arrangements with the animation on. A
    line change has to be a dictionary lookup, not a repaint."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    playing = window._tray_state
    assert playing in window._tray_pngs
    first = window._tray_pngs[playing]

    window._on_position_update(snapshot(state=PlaybackState.PAUSED))
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_pngs[playing] is first


def test_every_glyph_is_a_template_so_macos_owns_the_colour(with_tray, make_window):
    """A coloured menu bar icon stops following the menu bar, which is why
    practice is a DOT and not a hue.

    Two halves, and both are here because either alone would pass while the
    icon came out black on a dark bar: the pixels have to be black with the
    shape in the ALPHA channel, and the image has to be told it is a
    template. The first is a property of the drawing and is measured; the
    second is one call inside the one door and is asserted structurally,
    the way everything native in this suite is.
    """
    window = make_window()
    window.apply_saved_visibility()
    for playing in (True, False):
        for visible in (True, False):
            for practising in (True, False):
                spec = mb.icon_spec(
                    playing=playing,
                    lyrics_visible=visible,
                    practising=practising,
                )
                image = w.symbols.menubar_pixmap(spec, mb.GLYPH_UNITS).toImage()
                colours = {
                    image.pixelColor(x, y).getRgb()[:3]
                    for x in range(image.width())
                    for y in range(image.height())
                    if image.pixelColor(x, y).alpha() > 0
                }
                assert colours == {(0, 0, 0)}, spec
    source = (
        Path(w.nsmenu.__file__).read_text(encoding="utf-8")
    )
    assert "setTemplate_(True)" in source


def test_the_drawn_glyphs_are_not_all_the_same_pixels(with_tray, make_window):
    """Eight specs that happened to render identically would pass every test
    above and say nothing on the menu bar."""
    make_window()
    seen = set()
    for playing in (True, False):
        for visible in (True, False):
            for practising in (True, False):
                spec = mb.icon_spec(
                    playing=playing, lyrics_visible=visible, practising=practising
                )
                seen.add(w.symbols.menubar_png(spec, mb.GLYPH_UNITS))
    # practice forces bright and a dot, so hidden-vs-shown collapses there
    assert len(seen) == 6


# -- the optional arrangement stepping ------------------------------------


def test_the_animation_is_off_by_default(with_tray, make_window):
    window = make_window()
    assert window._menubar_animation is False
    assert window._menu.is_checked(m.MENUBAR_ANIMATION) is False


def test_off_means_the_shape_never_moves(with_tray, make_window):
    """The layers principle: off must equal the app before this existed."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))
    shapes = {window._tray_state.lengths}
    for position in (1.0, 5.0, 1.0, 5.0):
        window._on_position_update(
            snapshot(state=PlaybackState.PLAYING, track_id="t5", position=position)
        )
        shapes.add(window._tray_state.lengths)
    assert shapes == {mb.PLAYING_LENGTHS}


def test_a_line_change_steps_the_arrangement(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))

    seen = [window._tray_state.lengths]
    for position in (5.0, 1.0, 5.0):
        window._on_position_update(
            snapshot(state=PlaybackState.PLAYING, track_id="t5", position=position)
        )
        seen.append(window._tray_state.lengths)

    assert len(set(seen)) > 1, "the shape has to actually move"
    assert all(shape in mb.ARRANGEMENTS for shape in seen)


def test_the_step_counts_only_real_line_changes(with_tray, make_window):
    """_render re-runs _set_lines for reasons that have nothing to do with
    the song — a menu refresh, a resize — and those are not line changes."""
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))
    before = window._menubar_step

    for _ in range(4):
        window._render()
        window._refresh_menu()
    assert window._menubar_step == before


def test_the_step_is_counted_even_with_the_layer_off(with_tray, make_window):
    """So switching it on mid-song picks up where the song is rather than
    restarting a cycle."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))
    before = window._menubar_step
    window._on_position_update(
        snapshot(state=PlaybackState.PLAYING, track_id="t5", position=5.0)
    )
    assert window._menubar_step > before


def test_nothing_moves_the_shape_while_nothing_is_playing(with_tray, make_window):
    """There are no line changes with nothing playing, and an arrangement
    frozen mid-cycle would be a shape that means nothing."""
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    window._menubar_step = 2
    window._on_position_update(snapshot(state=PlaybackState.PAUSED))
    assert window._tray_state.lengths == mb.EVEN_LENGTHS


def test_switching_the_animation_off_puts_the_shape_back(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    window._menubar_step = 2
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths != mb.PLAYING_LENGTHS

    window._set_menubar_animation(False)
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS


def test_the_animation_setting_survives_a_restart(with_tray, make_window):
    first = make_window()
    first._set_menubar_animation(True)
    first._save_settings()
    first._settings.sync()

    second = make_window()
    assert second._menubar_animation is True
    assert second._menu.is_checked(m.MENUBAR_ANIMATION) is True


def test_no_menu_bar_item_is_not_a_crash(monkeypatch, make_window):
    """Everything about the glyph has to survive there being nowhere to
    put it — the same rule the rest of the menu bar code follows."""

    class NoMenuBar(FakeStatusItem):
        def create(self, tooltip=""):
            return False

    monkeypatch.setattr(w.nsmenu, "StatusItem", NoMenuBar)
    window = make_window()
    window._last_state = PlaybackState.PLAYING
    window._refresh_menu()  # must not raise
    assert window._tray is None


# -- the global hotkey ----------------------------------------------------


def test_the_hotkey_toggles_the_window(make_window):
    window = make_window()
    window.apply_saved_visibility()
    assert window.isVisible() is True

    window._toggle_lyrics_visible()
    land(window)
    assert window.isVisible() is False

    window._toggle_lyrics_visible()
    land(window)
    assert window.isVisible() is True


def test_the_tick_matches_whichever_of_the_two_was_used(make_window):
    """The requirement: one piece of state, two ways to reach it. A press
    after a menu click, and a menu click after a press, both have to leave
    the tick describing the window."""
    window = make_window()
    window.apply_saved_visibility()
    from_menu = lambda: window._menu.trigger(m.SHOW_LYRICS)

    for act in (
        window._toggle_lyrics_visible,        # hotkey hides
        from_menu,                             # menu shows
        window._toggle_lyrics_visible,        # hotkey hides again
        window._toggle_lyrics_visible,        # hotkey shows
        from_menu,                             # menu hides
    ):
        act()
        land(window)
        assert window._menu.is_checked(m.SHOW_LYRICS) is window.isVisible()
        assert window._menu.is_checked(m.SHOW_LYRICS) is window._lyrics_visible


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
    land(window)
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
    land(window)
    assert window.isVisible() is True
    assert window._current.text() == "two"


def test_the_window_asks_for_the_one_documented_combination(make_window):
    window = make_window()
    assert window._hotkey.combination is hotkey.TOGGLE_LYRICS


def test_a_refused_hotkey_leaves_the_app_fully_working(make_window, caplog):
    """Registration fails here for real — the fixture hands back no Carbon
    — so this is the "another app owns it" path. Everything the hotkey
    would have done is still reachable from the menu."""
    with caplog.at_level(logging.INFO, logger="sottovoce.window"):
        window = make_window()
    assert window._hotkey.registered is False
    assert "continuing without the global hotkey" in caplog.text

    window._menu.trigger(m.SHOW_LYRICS)
    land(window)
    assert window.isVisible() is False


def test_the_menu_entry_never_advertises_the_combination(make_window):
    """Two mechanisms firing one action is the drift this app designs
    away, and a label printing ⇧⌘J while another app holds it would be a
    menu claiming something untrue. No entry in the model carries a key
    equivalent and nsmenu.py never asks for one, so there is nowhere for a
    combination to be printed."""
    window = make_window()
    assert "⌘" not in window._menu.label(m.SHOW_LYRICS)
    assert not any("⌘" in m.ENTRIES[key].label for key in m.MENU_ORDER)


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
    land(window)
    assert window.isVisible() is False
    assert window._menu.is_checked(m.SHOW_LYRICS) is False


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
    QTimer.singleShot(0, lambda: window._menu.trigger(m.QUIT))
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
        with caplog.at_level(logging.WARNING, logger="sottovoce.window"):
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
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False

    window._login_status = login_item.LoginItemStatus.ENABLED
    window._refresh_menu()
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is True


def test_awaiting_approval_stays_unchecked_and_says_why(make_window):
    """Registered but not yet approved is not enabled. The entry must not
    claim a launch that will not happen, and the label is the only place
    that can point at System Settings."""
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.REQUIRES_APPROVAL
    window._refresh_menu()

    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False
    assert "System Settings" in window._menu.label(m.OPEN_AT_LOGIN)


def test_the_label_returns_to_normal_once_approved(make_window):
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.REQUIRES_APPROVAL
    window._refresh_menu()
    window._login_status = login_item.LoginItemStatus.ENABLED
    window._refresh_menu()
    assert window._menu.label(m.OPEN_AT_LOGIN) == login_item.MENU_LABEL


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
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is True

    window._set_open_at_login(False)
    assert asked == [True, False]
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False


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
    with caplog.at_level(logging.WARNING, logger="sottovoce.window"):
        window._set_open_at_login(True)

    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False
    assert "System Settings" in window._menu.label(m.OPEN_AT_LOGIN)
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

    window._menu.opening()
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is True
    window._menu.opening()
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False


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
    window._menu.opening()
    assert asked == []


def test_the_all_desktops_toggle_cannot_touch_the_activation_policy():
    """Accessory is applied once at startup and never revoked, so no toggle
    state can bring the Dock icon (or the Space switch) back."""
    assert not hasattr(w.LyricsWindow, "_apply_activation_policy")
    assert callable(w.apply_accessory_policy)
    source = w.LyricsWindow._apply_all_desktops.__doc__ or ""
    assert "activation policy is NOT part of this" in source


# -- getting out of a notification's way ----------------------------------
#
# The pure rules — what overlaps, how faint, how long — live in
# test_notifications.py. These cover what only a real window can answer:
# that the timer follows the layer, that the three things with an opinion
# about opacity compose, and that every path out of a fade gives it back.
#
# occupied_rects is stubbed rather than blocked. The conftest guard shuts
# the door underneath it and stays armed for anything reaching around;
# handing back rectangles here is what lets the poll be driven on a machine
# with no notification centre at all, which is every CI runner.


DISPLAY_RECT = (0, 0, 1710, 1107)


def put_in_the_way(window):
    """Move the window into the strip where notifications actually appear.

    Needed since 16.1: the window's position is now part of the answer, so a
    test that wants a fade has to say where the window is. Derived from the
    region rather than written as a number, so the constant moving cannot
    leave these tests quietly asserting nothing.
    """
    x, y, width, _ = n.plausible_region(DISPLAY_RECT)
    window.move(x + 10, y + 10)
    APP.processEvents()
    assert n.in_the_way(
        (window.frameGeometry().x(), window.frameGeometry().y(),
         window.frameGeometry().width(), window.frameGeometry().height()),
        [DISPLAY_RECT],
    ), "the test's own premise: this window should be in the way"


def put_out_of_the_way(window):
    """Move the window well clear of where notifications appear."""
    window.move(20, 400)
    APP.processEvents()

# windowOpacity() does not hand back what it was given: Qt stores it as an
# 8-bit alpha, so 0.15 reads back as 38/255 = 0.14902. Measured, not
# guessed at — the first version of these tests compared exactly and failed
# on the third decimal. The same 8-bit residue the album tint's luminance
# sweep runs into, and the tolerance for reading a real window's opacity.
OPACITY_STEP = 1 / 255


def notifications_at(monkeypatch, *rects):
    """What the next poll will see. Returns the call log, so a test can
    assert the layer is not looking at all."""
    calls = []

    def occupied():
        calls.append(True)
        return tuple(rects)

    monkeypatch.setattr(w.notifications, "occupied_rects", occupied)
    return calls


def settle_yield(window):
    """Run the fade to its end without waiting it out — the same shape as
    land() for the flight."""
    if window._yield_anim is not None:
        window._yield_anim.setCurrentTime(window._yield_anim.duration())
    APP.processEvents()


def test_yielding_to_notifications_is_off_by_default(make_window):
    """Default off, like every layer. And off means not looking: the timer
    is the whole of the watching, so an inactive timer is the layers
    principle taken literally."""
    window = make_window()
    assert window._yield_to_notifications is False
    assert window._yield_timer.isActive() is False
    assert window._menu.is_checked(m.YIELD_NOTIFICATIONS) is False


def test_the_layer_being_off_asks_nothing(make_window, monkeypatch):
    """Not merely ignored — never asked. A poll that arrived anyway must
    still not read the window list."""
    window = make_window()
    calls = notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    assert calls == []
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_switching_it_on_starts_watching_and_off_stops(make_window):
    window = make_window()
    window._set_yield_to_notifications(True)
    assert window._yield_timer.isActive() is True
    assert window._menu.is_checked(m.YIELD_NOTIFICATIONS) is True

    window._set_yield_to_notifications(False)
    assert window._yield_timer.isActive() is False
    assert window._menu.is_checked(m.YIELD_NOTIFICATIONS) is False


def test_switching_it_on_fades_nothing_by_itself(make_window, monkeypatch):
    """The first poll is a third of a second away and will answer honestly.
    Fading at the moment a menu item is ticked would be a guess."""
    window = make_window()
    calls = notifications_at(monkeypatch, DISPLAY_RECT)
    window._set_yield_to_notifications(True)
    assert calls == []
    assert window._yield_level == 0.0


def test_a_notification_over_the_window_fades_it(make_window, monkeypatch):
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    assert window._yielding is True
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_the_users_opacity_comes_back_when_it_clears(make_window, monkeypatch):
    window = make_window()
    put_in_the_way(window)
    window._set_opacity(0.8)
    window._set_yield_to_notifications(True)

    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)

    notifications_at(monkeypatch)  # the banner has gone
    window._check_notifications()
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(0.8, abs=OPACITY_STEP)
    assert window._yield_level == 0.0


def test_a_notification_that_does_not_reach_the_window_is_left_alone(
    make_window, monkeypatch
):
    """A banner on another display. The intersection is real arithmetic, so
    this is the same code path as the overlapping case rather than a
    branch."""
    window = make_window()
    window.move(200, 200)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, (5000, 0, 1710, 1107))

    window._check_notifications()
    assert window._yielding is False
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_an_already_dimmed_window_is_never_brightened(make_window, monkeypatch):
    """The user has scrolled the window down to the floor. Yielding takes it
    further, never back up — measured against their own setting, not against
    full opacity."""
    window = make_window()
    put_in_the_way(window)
    window._set_opacity(w._MIN_OPACITY)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    seen = []
    for step in range(0, 11):
        window._yield_level = step / 10
        window._apply_window_opacity()
        seen.append(window.windowOpacity())
    assert max(seen) <= w._MIN_OPACITY + 1e-6
    assert seen == sorted(seen, reverse=True)


def test_a_repeat_poll_while_faded_starts_no_second_fade(make_window, monkeypatch):
    """Three polls a second land inside every banner. An announcement of
    what is already true is not news — the same dedupe shape as the line
    change's target index."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    settle_yield(window)
    assert window._yield_anim is None

    window._check_notifications()
    window._check_notifications()
    assert window._yield_anim is None
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_a_banner_clearing_mid_fade_turns_around_from_where_it_got_to(
    make_window, monkeypatch
):
    """The interruption case. It retargets from the level the window
    actually reached and pays for the distance left, rather than finishing a
    fade nobody is waiting for."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()

    halfway = window._yield_anim
    halfway.setCurrentTime(halfway.duration() // 2)
    APP.processEvents()
    reached = window._yield_level
    assert 0.0 < reached < 1.0

    notifications_at(monkeypatch)
    window._check_notifications()
    assert window._yield_anim is not None
    assert window._yield_anim.startValue() == pytest.approx(reached)
    assert window._yield_anim.duration() < n.YIELD_MS

    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_a_sync_pass_is_never_faded_under_the_user(make_window, monkeypatch):
    """Principle 6: the pass is the user tapping this window once per line,
    and a decorative feature does not get to fade an essential one. A pass
    beginning while the window is already faint hands the opacity back."""
    window = make_window()
    put_in_the_way(window)
    load(window, PLAIN)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is True

    window._begin_sync()
    assert window._syncing is True
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is False
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_hiding_the_window_hands_the_opacity_back_and_stops_watching(
    make_window, monkeypatch
):
    """A hidden window is in nobody's way, so there is nothing to look for.
    The level goes back BEFORE the flight borrows the opacity — a window
    that went away faded would come back faded, because the flight restores
    its own factor and not this one."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._set_lyrics_visible(False)
    assert window._yield_level == 0.0
    assert window._yield_timer.isActive() is False
    land(window)

    window._set_lyrics_visible(True)
    land(window)
    assert window._yield_timer.isActive() is True
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_a_flight_and_a_yield_compose_rather_than_overwrite(make_window, monkeypatch):
    """The pair that could not happen before this milestone and now can.
    Both scale the same window, so they multiply — and neither may reset the
    other's contribution on its way out."""
    window = make_window()
    window._set_opacity(0.9)
    window._yield_level = 1.0
    window._flight_opacity = 0.5
    window._apply_window_opacity()
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING * 0.5, abs=OPACITY_STEP)

    window._end_flight()
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_switching_the_layer_off_gives_the_window_back_at_once(
    make_window, monkeypatch
):
    """No fade on the way out of the layer: the user asked for the window
    back, not for it to drift back."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._set_yield_to_notifications(False)
    assert window._yield_anim is None
    assert window._yield_level == 0.0
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_the_setting_survives_a_restart(make_window):
    """And restoring it on must not need the menu to exist yet: the setter
    refreshes the menu, and _restore_settings runs before it is built —
    which is the bug the previous layer shipped and this one inherits the
    fix for."""
    first = make_window()
    first._set_yield_to_notifications(True)
    first._save_settings()
    first._settings.sync()

    second = make_window()
    assert second._yield_to_notifications is True
    assert second._yield_timer.isActive() is True
    assert second._menu.is_checked(m.YIELD_NOTIFICATIONS) is True


def test_a_restored_hidden_window_does_not_start_watching(make_window):
    """Both halves are read at startup, and watching depends on the pair."""
    first = make_window()
    first._set_yield_to_notifications(True)
    first._set_lyrics_visible(False)
    first._save_settings()
    first._settings.sync()

    second = make_window()
    assert second._yield_to_notifications is True
    assert second._lyrics_visible is False
    assert second._yield_timer.isActive() is False


def test_shutdown_stops_watching_and_hands_the_opacity_back(
    make_window, monkeypatch
):
    """A poll landing mid-teardown would ask the window server about a
    window being destroyed."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._shutdown()
    assert window._yield_timer.isActive() is False
    assert window._yield_anim is None
    assert window._yield_level == 0.0
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_the_yield_is_never_written_into_the_saved_opacity(make_window, monkeypatch):
    """What gets persisted is what the user chose, not what a banner
    happened to be doing when the app quit."""
    window = make_window()
    put_in_the_way(window)
    window._set_opacity(0.7)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._save_settings()
    assert window._settings.value("window/opacity", type=float) == pytest.approx(0.7)


def test_only_one_place_writes_the_windows_opacity():
    """Three things scale the window now — the user's setting, a yield and a
    flight — and each of them used to call setWindowOpacity directly. That
    worked only because no two were ever true at once. Enforced as a source
    scan rather than trusted, because a fourth caller would pass every
    behavioural test above while quietly dropping the other two."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(w))
    callers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "setWindowOpacity"
            for inner in ast.walk(node)
        )
    }
    assert callers == {"_apply_window_opacity"}


def test_a_banner_leaves_a_window_nowhere_near_it_alone(make_window, monkeypatch):
    """THE 16.1 BUG, at the level the user met it. macOS reports the whole
    display for a banner in one corner, so before the region was narrowed
    this window faded for something it was nothing like."""
    window = make_window()
    put_out_of_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    assert window._yielding is False
    assert window._yield_anim is None
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_dragging_into_the_way_starts_the_fade_at_the_next_poll(
    make_window, monkeypatch
):
    """The position is read on every poll, not cached, so moving the window
    under a banner that is already up is picked up without anything having to
    tell the layer that the window moved."""
    window = make_window()
    put_out_of_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    assert window._yielding is False

    put_in_the_way(window)
    window._check_notifications()
    assert window._yielding is True
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_dragging_out_of_the_way_ends_the_fade(make_window, monkeypatch):
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is True

    put_out_of_the_way(window)
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is False
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


# -- the polling rate follows what the window is doing ---------------------


def test_the_idle_rate_is_what_a_fresh_window_polls_at(make_window):
    window = make_window()
    assert window._yield_timer.interval() == int(n.POLL_SECONDS * 1000)


def test_the_rate_goes_up_the_moment_the_fade_starts(make_window, monkeypatch):
    """Before the animation, not after it: the short interval is wanted for
    the poll that lands DURING the fade, which is where a banner dismissed
    early would otherwise be missed for a full idle interval."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    assert window._yield_anim is not None, "still fading"
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)


def test_the_rate_goes_back_down_once_the_window_is_restored(
    make_window, monkeypatch
):
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)

    notifications_at(monkeypatch)
    window._check_notifications()
    # Still fast: the window is not back yet. Reading only the target put the
    # rate back here, while the window was still faint — so a second banner
    # inside that 260ms met the idle rate.
    assert window._yield_level > 0
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)

    settle_yield(window)
    assert window._yield_level == 0
    assert window._yield_timer.interval() == int(n.POLL_SECONDS * 1000)


def test_giving_the_opacity_back_also_gives_the_rate_back(make_window, monkeypatch):
    """Every path out of a fade returns both, so there is no way to be left
    polling three times as often as the layer needs for the rest of the
    session."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)

    window._stop_yield()
    assert window._yield_timer.interval() == int(n.POLL_SECONDS * 1000)


def test_the_timer_stays_running_across_a_rate_change(make_window, monkeypatch):
    """setInterval on a running QTimer restarts its countdown, which is fine
    once per change and would be a timer that never fires if it happened on
    every poll — hence the only-when-it-changes guard."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    assert window._yield_timer.isActive()

    window._check_notifications()
    assert window._yield_timer.isActive()
    settle_yield(window)
    assert window._yield_timer.isActive()


def test_a_repeat_poll_does_not_rewrite_the_interval(make_window, monkeypatch):
    """The guard itself. Three polls a second land inside every banner; each
    one calling setInterval would restart the countdown every time and the
    timer would never actually reach it."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    writes = []
    real = window._yield_timer.setInterval
    monkeypatch.setattr(
        window._yield_timer,
        "setInterval",
        lambda ms: writes.append(ms) or real(ms),
    )
    for _ in range(5):
        window._check_notifications()
    assert writes == []


# -- why the lyrics are not here -------------------------------------------
#
# The window's one line about a failed fetch was "lyrics unavailable, will
# retry", which is true of a 503, of the wifi being off, and of a request
# that timed out on the third attempt. The reason is now one click away and
# no closer: it is offered beside that message and nowhere else, so a song
# that simply has no lyrics still gets one plain line and nothing to dig at.


def fail(window, why, track_id="t1"):
    """A track whose lookup came back with a reason."""
    window._on_track_change(snapshot(track_id=track_id))
    window._on_fetch_finished(track_id, None, False, why)
    window._title_card_until = 0.0
    window._render()
    APP.processEvents()


HTTP_503 = FetchFailure(kind="http", status=503, attempt="album match")


def test_the_message_itself_is_unchanged(make_window):
    """The default is for the people who do not care why, and there are
    more of them. Nothing about the affordance may change what the window
    says on its own."""
    window = make_window()
    fail(window, HTTP_503)
    assert window._current.text() == "lyrics unavailable, will retry"
    assert window._upcoming.text() == ""


def test_the_affordance_is_offered_only_for_a_service_failure(make_window):
    """The distinction that has to stay obvious: a track with no lyrics is
    not a track the service failed on."""
    window = make_window()
    fail(window, HTTP_503)
    # isVisibleTo, not isVisible: this window was never shown.
    assert window._why_button.isVisibleTo(window) is True

    window._on_fetch_finished("t1", None, True)  # a genuine "no lyrics"
    window._render()
    assert window._view_model.display().mode is Mode.NO_LYRICS
    assert window._current.text() == "no lyrics found"
    assert window._why_button.isVisibleTo(window) is False


def test_the_affordance_is_offered_nowhere_else(make_window):
    """Every other mode: synced, plain, fetching, idle."""
    window = make_window()
    for lyrics in (SYNCED, PLAIN):
        load(window, lyrics)
        assert window._why_button.isVisibleTo(window) is False
    window._on_track_change(snapshot(track_id="t9"))
    window._title_card_until = 0.0
    window._render()
    assert window._view_model.display().mode is Mode.FETCHING
    assert window._why_button.isVisibleTo(window) is False


def test_clicking_reveals_the_specific_reason(make_window):
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    APP.processEvents()
    assert window._upcoming.text() == "LRCLIB answered HTTP 503 · album match"
    assert window._why_button.isChecked()


def test_clicking_again_puts_it_away(make_window):
    """A thing to glance at, not a state to get stuck in."""
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    window._why_button.click()
    APP.processEvents()
    assert window._upcoming.text() == ""
    assert not window._why_button.isChecked()


def test_each_kind_of_failure_says_which_it_was(make_window):
    """The four the provider can tell apart, end to end."""
    window = make_window()
    for why, expected in (
        (FetchFailure(kind="http", status=429, attempt="search"),
         "LRCLIB answered HTTP 429 · search"),
        (FetchFailure(kind="timeout", attempt="title and artist"),
         "LRCLIB did not answer in time · title and artist"),
        (FetchFailure(kind="connection", attempt="album match"),
         "could not reach lrclib.net · album match"),
        (FetchFailure(kind="payload", attempt="search"),
         "LRCLIB's answer could not be read · search"),
    ):
        fail(window, why)
        window._why_shown = True
        window._render()
        assert window._upcoming.text() == expected


def test_the_reveal_survives_a_retry(make_window):
    """The retry runs every 30s and takes the mode ERROR -> FETCHING ->
    ERROR. Hiding the reason under somebody who had just asked for it
    would make the control feel broken."""
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    assert window._why_shown

    window._view_model._error_at = -1000.0  # due now
    window._tick_retry()
    APP.processEvents()
    assert window._view_model.display().mode is Mode.FETCHING
    assert window._why_shown  # remembered, though nothing is on screen

    window._on_fetch_finished("t1", None, False, HTTP_503)
    APP.processEvents()
    assert window._upcoming.text() == "LRCLIB answered HTTP 503 · album match"


def test_a_new_song_asks_its_own_question(make_window):
    """The reveal belongs to the failure that prompted it."""
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    assert window._why_shown

    fail(window, FetchFailure(kind="timeout", attempt="search"), track_id="t2")
    assert not window._why_shown
    assert window._upcoming.text() == ""


def test_a_failure_with_nothing_to_say_offers_nothing(make_window):
    """A fetch that failed before any reason existed. The message is
    unchanged and there is simply nothing to click."""
    window = make_window()
    fail(window, None)
    assert window._current.text() == "lyrics unavailable, will retry"
    assert window._why_button.isVisibleTo(window) is False


def test_the_control_sits_beside_the_message(make_window):
    """Placed from the text rather than pinned to a corner: the message is
    centred and the window is resizable, so a fixed position would be
    beside it at one width and stranded at every other."""
    window = make_window()
    window.resize(460, 220)
    fail(window, HTTP_503)
    narrow = window._why_button.pos().x()

    window.resize(700, 220)
    APP.processEvents()
    window._render()
    wide = window._why_button.pos().x()
    assert wide > narrow, "the control did not follow the message"
    # And never off the edge: the gutter is where a wrapped message puts it.
    assert window._why_button.pos().x() + window._why_button.width() <= window.width()


def test_the_control_never_leaves_the_window_at_its_narrowest(make_window):
    """The wrapping case, where the message's laid-out width IS the row."""
    window = make_window()
    window.resize(260, 200)
    fail(window, HTTP_503)
    APP.processEvents()
    right = window._why_button.pos().x() + window._why_button.width()
    assert 0 < window._why_button.pos().x()
    assert right <= window.width()


# -- macOS accessibility display settings ----------------------------------
#
# Read live, like the appearance: somebody who switches Reduce Motion on
# because a migraine has started should not have to relaunch the app to be
# believed. The settings themselves cannot be toggled from a test — the
# domain is TCC-protected — so what is checked here is what the window does
# when it is told.


def tell(window, **options):
    """Hand the window a set of display options, the way the watcher
    would."""
    window._on_display_options_changed(accessibility.DisplayOptions(**options))
    APP.processEvents()


def test_a_window_starts_with_nothing_switched_on(make_window):
    window = make_window()
    assert window._display_options == accessibility.NONE
    assert window._palette is ap.palette_for(window._appearance)


def test_the_window_watches_for_changes(make_window):
    """The wiring, not the effect: an app that only looked at startup is
    the app that is wrong for the rest of the session."""
    window = make_window()
    assert isinstance(window._display_watcher, accessibility.DisplayOptionsWatcher)
    # No workspace in the suite, so the subscription simply finds nothing
    # to observe — the same branch a machine without pyobjc takes.
    assert window._display_watcher.active is False


def test_the_observer_is_released_before_anything_is_destroyed(make_window):
    """NSWorkspace holds a block that repaints a window being torn down,
    the same hazard the activation watcher has."""
    window = make_window()
    stopped = []
    window._display_watcher.stop = lambda: stopped.append(True)
    window._shutdown()
    assert stopped == [True]


def test_the_same_options_twice_change_nothing(make_window):
    window = make_window()
    palette = window._palette
    tell(window)
    assert window._palette is palette


# Reduce Motion.


def test_reduce_motion_takes_the_travel_out_of_a_line_change(make_window):
    """The fade stays and the rise goes. ``progress`` is one signed number
    carrying both, so the travel is a length and this sets it to zero."""
    window = make_window()
    load(window, SYNCED)
    assert window._current_fx.travel > 0

    tell(window, reduce_motion=True)
    assert window._current_fx.travel == 0.0
    # And the choreography itself is untouched: the same timers, the same
    # phase length, the arrival still on the timestamp.
    window._on_position_update(snapshot(position=0.2))
    assert window._swap_timer.isActive()


def test_the_travel_comes_back(make_window):
    window = make_window()
    tell(window, reduce_motion=True)
    tell(window)
    assert window._current_fx.travel > 0


def test_a_resize_under_reduce_motion_does_not_restore_the_travel(make_window):
    """_apply_scale recomputes it, so it has to go through the same
    place."""
    window = make_window()
    tell(window, reduce_motion=True)
    window.resize(640, 300)
    APP.processEvents()
    assert window._current_fx.travel == 0.0


def test_reduce_motion_hides_the_window_without_the_flight(make_window):
    window = make_window()
    tell(window, reduce_motion=True)
    window._set_lyrics_visible(False)
    APP.processEvents()
    assert window._flight_anim is None
    assert not window.isVisible()
    window._set_lyrics_visible(True)
    APP.processEvents()
    assert window._flight_anim is None
    assert window.isVisible()


def test_reduce_motion_gives_back_everything_the_flight_borrowed(make_window):
    """Switched on mid-journey: the flight in the air must not be left
    holding the window's position, opacity or scale."""
    window = make_window()
    window.move(400, 300)
    window._set_lyrics_visible(False)  # a flight is now running
    assert window._flight_anim is not None

    tell(window, reduce_motion=True)
    window._set_lyrics_visible(True)
    APP.processEvents()
    assert window._flight_anim is None
    assert window._flight_home is None
    assert window._flight_opacity == 1.0
    assert window.pos() == QPoint(400, 300)


def test_reduce_motion_moves_the_window_without_travelling(make_window):
    """Per-app position memory is about where the window lives, not about
    how it gets there: it still arrives, it simply does not travel."""
    window = make_window()
    window.move(100, 100)
    tell(window, reduce_motion=True)
    window._move_to(QPoint(300, 240))
    APP.processEvents()
    assert window._move_anim is None
    assert window.pos() == QPoint(300, 240)


# Reduce Transparency.


def test_reduce_transparency_paints_the_solid_background(make_window):
    window = make_window()
    tell(window, reduce_transparency=True)
    assert window._palette.solid[3] == 255
    assert window._material is None
    assert window._current_background() == window._palette.solid


def test_reduce_transparency_refuses_to_install_a_material(make_window):
    """The setting is about that view and nothing else, so the honest
    answer is not to build one."""
    window = make_window()
    tell(window, reduce_transparency=True)
    assert window._apply_vibrancy() is False


def test_the_material_is_removed_rather_than_hidden(make_window):
    """A hidden effect view is still an effect view, and the flight hides
    and shows this one for its own reasons — which would put a suppressed
    material straight back."""
    window = make_window()

    class FakeMaterial:
        def __init__(self):
            self.removed = False
            self.hidden = None

        def removeFromSuperview(self):
            self.removed = True

        def setHidden_(self, value):
            self.hidden = value

    material = FakeMaterial()
    window._native_applied = True
    window._material = material
    tell(window, reduce_transparency=True)
    assert material.removed
    assert material.hidden is None
    assert window._material is None


def test_switching_it_off_asks_for_the_material_back(make_window, monkeypatch):
    window = make_window()
    window._native_applied = True
    tell(window, reduce_transparency=True)
    asked = []
    monkeypatch.setattr(
        window, "_apply_vibrancy", lambda: asked.append(True) or False
    )
    tell(window)
    assert asked == [True]


def test_the_background_before_the_window_is_shown_is_not_touched(make_window):
    """The first install happens in showEvent and consults the same
    options; asking for one before that would be asking about a window
    that has no native view yet."""
    window = make_window()
    window._native_applied = False
    window._material = None
    tell(window, reduce_transparency=True)  # must not raise
    assert window._material is None


# Increase Contrast.


def test_increase_contrast_lifts_the_palette_and_drops_the_material(make_window):
    """macOS turns Reduce Transparency on with it, and the app derives the
    same thing rather than trusting the pair to arrive together."""
    window = make_window()
    tell(window, increase_contrast=True)
    assert window._palette.solid[3] == 255
    assert window._palette is not ap.palette_for(window._appearance)
    for role, value in ap.HIGH_CONTRAST_OVERRIDES[window._appearance].items():
        assert getattr(window._palette, role) == value


def test_the_lifted_palette_reaches_the_stylesheet(make_window):
    """The colours are painted from a stylesheet, so a palette nobody
    applied is a setting that did nothing."""
    window = make_window()
    before = window.styleSheet()
    tell(window, increase_contrast=True)
    assert window.styleSheet() != before
    assert ap.rgba(window._palette.control_idle) in window.styleSheet()


def test_an_appearance_change_keeps_the_accessibility_settings(make_window):
    """Two systems the window follows, one palette. Whichever moves, both
    are asked again."""
    window = make_window()
    tell(window, increase_contrast=True)
    other = (
        ap.Appearance.LIGHT
        if window._appearance is ap.Appearance.DARK
        else ap.Appearance.DARK
    )
    window._appearance = other
    window._palette = window._palette_now()
    assert window._palette.solid[3] == 255
    assert (
        window._palette.border == ap.HIGH_CONTRAST_OVERRIDES[other]["border"]
    )


# -- the compact layout ---------------------------------------------------
#
# What is checked here is what needs a real object tree: which rows are on
# screen, what the window's shape does when the layout changes, and the
# reveal the controls ride on. The arithmetic underneath — the height floor
# and the dock position, notch included — is pure and lives in
# tests/test_geometry.py.


def compact_rows(window):
    """Which of the five label rows are on screen, as a tuple of bools.

    isVisibleTo, not isVisible: these windows are never shown, so every
    child would answer False and the tuple would say nothing.
    """
    return tuple(
        row.isVisibleTo(window)
        for row in (
            window._header,
            window._previous,
            window._current,
            window._pron,
            window._upcoming,
        )
    )


def go_compact(window):
    """Switch to the compact layout with the pointer definitely elsewhere.

    The offscreen platform parks its cursor at (10, 10) and these windows
    are placed over it, so a plain _set_compact would find itself hovered
    and the controls would come out at once — which is right, and is not
    what any of these tests is about.
    """
    with pointer_at(window, away_from(window)):
        window._set_compact(True)


def over(window):
    """A screen coordinate inside the window's frame."""
    frame = window.frameGeometry()
    return QPoint(frame.center())


def away_from(window):
    """A screen coordinate well clear of the window, release margin and
    all. Derived from the frame rather than named, so it stays outside a
    window that has been resized or moved."""
    frame = window.frameGeometry()
    return QPoint(
        frame.right() + proximity.RELEASE_MARGIN + 100,
        frame.bottom() + proximity.RELEASE_MARGIN + 100,
    )


def pointer_at(window, point):
    """Put the pointer at a screen coordinate for the length of a block.

    QCursor.pos() is whatever the offscreen platform last saw and cannot
    be driven from here, so the ONE thing that asks it is replaced.
    Everything above that is the real code — the frame test, the region
    test, the hysteresis and the gate all run for real on a real
    coordinate, which is what lets one helper drive both layers that read
    this poll.
    """
    return patch.object(w.LyricsWindow, "_pointer_position", lambda self: point)


def hover(window, inside):
    """One turn of the pointer poll, with the pointer inside the window or
    well clear of it."""
    with pointer_at(window, over(window) if inside else away_from(window)):
        window._check_pointer()
    finish_reveal(window)


def finish_reveal(window):
    """Run the controls' fade to its end without waiting it out."""
    if window._reveal_anim is not None:
        window._reveal_anim.setCurrentTime(window._reveal_anim.duration())
    APP.processEvents()


def test_compact_is_off_until_it_is_asked_for(make_window):
    """Default-off like every layer here: a fresh window is the app as it
    has always been."""
    window = make_window()
    assert window._compact is False
    assert window._compact_applied is False
    assert window._menu.is_checked(m.COMPACT) is False


def test_compact_shows_the_sung_line_and_nothing_else(make_window):
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()
    assert compact_rows(window)[0] is True   # the header is there to lose
    assert window._previous.isVisibleTo(window) is True

    go_compact(window)
    header, previous, current, _, upcoming = compact_rows(window)
    assert (header, previous, upcoming) == (False, False, False)
    assert current is True
    assert window._current.text() == "one"


def test_compact_keeps_the_romanisation_line_under_the_sung_one(make_window):
    """The one row that survives beside the line: it belongs to it."""
    window = make_window()
    window._view_model.romanisation_enabled = True
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    assert window._pron.isVisibleTo(window) is True
    assert window._pron.text()


def test_compact_off_restores_the_layout_exactly(make_window):
    """Off must equal the plain synced-lyrics window, which is more than
    the rows coming back: the gutters, the air around the sung line, the
    height floor and the controls' placement all have to be what they
    were."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()
    # A hidden widget defers its resize event until it is shown, so these
    # windows never run one and the controls are still placed for the size
    # the constructor gave them. Placed once here so both sides of the
    # comparison are answering about the same window.
    window._place_buttons()
    before = (
        compact_rows(window),
        window._layout.contentsMargins().left(),
        window._current_layout.contentsMargins().top(),
        window.minimumHeight(),
        window.height(),
        window._loop_button.pos(),
        window._speak_button.pos(),
    )

    go_compact(window)
    window._set_compact(False)
    APP.processEvents()

    assert (
        compact_rows(window),
        window._layout.contentsMargins().left(),
        window._current_layout.contentsMargins().top(),
        window.minimumHeight(),
        window.height(),
        window._loop_button.pos(),
        window._speak_button.pos(),
    ) == before


def test_compact_takes_the_window_down_to_a_strip(make_window):
    window = make_window()
    window.resize(460, 220)
    APP.processEvents()
    go_compact(window)
    assert window.minimumHeight() == w.min_window_height(
        window._scale, compact=True
    )
    assert window.height() == window.minimumHeight()
    assert window.height() < 220
    # A WIDE thin strip: the width is untouched. Safe as a literal because
    # no song has been loaded, so there is nothing for the fit to measure
    # and no font can reach this number.
    assert window.width() == 460


def test_the_full_layout_keeps_the_height_it_was_left_at(make_window):
    """Going compact and coming back gives the window its old shape, not a
    shape derived from the strip."""
    window = make_window()
    window.resize(460, 260)
    APP.processEvents()

    go_compact(window)
    APP.processEvents()

    window._set_compact(False)
    APP.processEvents()
    assert window.height() == 260


def test_the_strips_height_is_its_type_sizes_and_is_not_remembered(make_window):
    """The full layout's height is a free number and is kept. The strip's
    is not: it is one row of type, so the type size answers it, and a
    remembered height would only be a way to disagree with the setting."""
    window = make_window()
    go_compact(window)
    APP.processEvents()
    assert window.height() == w.min_window_height(window._scale, compact=True)

    # Nothing can leave it at another height, and coming back does not
    # restore one.
    window.resize(window.width(), window.height() + 40)
    APP.processEvents()
    window._set_compact(False)
    APP.processEvents()
    go_compact(window)
    APP.processEvents()
    assert window.height() == w.min_window_height(window._scale, compact=True)


def test_compact_and_the_full_height_survive_a_restart(make_window):
    window = make_window()
    window.resize(460, 240)
    APP.processEvents()
    go_compact(window)
    strip = window.height()
    window._save_settings()
    window._settings.sync()

    # The strip is what was written down, and the full layout's height with
    # it. Read off the settings rather than off the reopened window,
    # because make_window resizes every window it builds and would land on
    # top of the restored size.
    assert window._settings.value("window/size").height() == strip
    assert window._settings.value("window/full_height", type=int) == 240
    # The strip's height is derived now, so the key that used to hold it is
    # removed rather than left behind for somebody to believe.
    assert window._settings.value("window/compact_height") is None

    reopened = make_window()
    APP.processEvents()
    assert reopened._compact is True
    assert reopened._compact_applied is True
    # The compact layout is in force from the first frame, not applied
    # after a first render: the floor is the strip's, and the rows either
    # side of the sung line are already gone.
    assert reopened.minimumHeight() == w.min_window_height(
        reopened._scale, compact=True
    )
    assert compact_rows(reopened)[1] is False

    reopened._set_compact(False)
    APP.processEvents()
    assert reopened.height() == 240


def test_a_sync_pass_borrows_the_full_layout_and_gives_it_back(make_window):
    """A pass needs the line before, the line after, a status row and a tap
    bar across the bottom. A strip has room for one of those, and the bar
    alone would BE the window, so the layout steps aside for the pass
    rather than pretending."""
    window = make_window()
    load(window, PLAIN, track_id="t7")
    go_compact(window)
    strip = window.height()

    window._begin_sync()
    APP.processEvents()
    assert window._compact_applied is False
    assert window._previous.isVisibleTo(window) is True
    assert window._progress.isVisibleTo(window) is True
    assert window.height() > strip
    # The setting is untouched: the tick describes what was asked for.
    assert window._compact is True
    assert window._menu.is_checked(m.COMPACT) is True

    window._cancel_sync()
    window._render()
    APP.processEvents()
    assert window._compact_applied is True
    assert window.height() == strip
    assert window._previous.isVisibleTo(window) is False


def test_compact_gives_up_filling_the_rows_it_does_not_have(make_window):
    """Off removes the work, not just the output. The rows either side of
    the sung line are not written to at all."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._previous.setText("a row nobody filled")
    window._on_position_update(snapshot(position=6.0))
    APP.processEvents()
    assert window._previous.text() == "a row nobody filled"


# -- the overlay controls, and coming out from under the pointer ----------


def test_compact_puts_the_controls_away_until_the_pointer_arrives(make_window):
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()
    assert window._loop_button.isVisibleTo(window) is True

    go_compact(window)
    assert window._loop_button.isVisibleTo(window) is False
    assert window._speak_button.isVisibleTo(window) is False

    hover(window, True)
    assert window._loop_button.isVisibleTo(window) is True
    assert window._reveal == 1.0

    hover(window, False)
    assert window._loop_button.isVisibleTo(window) is False
    assert window._reveal == 0.0


def test_a_faded_control_is_off_the_window_rather_than_invisible(make_window):
    """A widget at zero opacity is still a widget under the pointer, and an
    invisible thing that can be clicked is worse than either state it is
    between."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    assert window._reveal == 0.0
    for button in window._revealable():
        assert button.isVisibleTo(window) is False


def test_the_pointer_over_a_control_is_still_over_the_window(make_window):
    """The one thing underMouse() gets wrong: it is false for the window
    the moment the pointer is over one of its own children, which is
    exactly when the controls have to stay. Asking the frame answers for
    everything inside it.

    The real pointer, such as it is: the offscreen platform parks its
    cursor and will not move it, so the window is moved under the cursor
    instead of the other way round.
    """
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._place_buttons()

    pointer = QCursor.pos()
    button = window._loop_button
    window.move(
        pointer
        - button.pos()
        - QPoint(button.width() // 2, button.height() // 2)
    )
    APP.processEvents()
    assert button.geometry().translated(window.pos()).contains(pointer)

    assert window._pointer_inside() is True
    window._check_pointer()
    finish_reveal(window)
    assert window._hovered is True
    assert button.isVisibleTo(window) is True


def test_the_pointer_is_only_watched_where_it_can_matter(make_window):
    """No events to subscribe to, so this is a poll, and a poll that is not
    needed does not run. Off removes the work."""
    window = make_window()
    window.apply_saved_visibility()
    land(window)
    assert window._pointer_timer.isActive() is False  # compact is off

    go_compact(window)
    assert window._pointer_timer.isActive() is True

    window._set_lyrics_visible(False)  # away at the menu bar: no pointer on it
    land(window)
    assert window._pointer_timer.isActive() is False
    window._set_lyrics_visible(True)
    land(window)
    assert window._pointer_timer.isActive() is True

    window._set_compact(False)
    assert window._pointer_timer.isActive() is False


def test_a_sync_pass_stops_the_pointer_watch_with_the_layout(make_window):
    """The full layout has its controls out anyway. Nothing is watching for
    a pointer that could not change anything."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, PLAIN, track_id="t7")
    go_compact(window)
    assert window._pointer_timer.isActive() is True

    window._begin_sync()
    assert window._pointer_timer.isActive() is False
    window._cancel_sync()
    assert window._pointer_timer.isActive() is True


def test_the_attempt_prompt_is_held_out_without_a_pointer(make_window):
    """Echo practice pauses the song and hands the turn over, and the done
    button is the only way out of it. A prompt nobody can see is not a
    prompt, so the reveal is held open for as long as the window is asking
    something."""
    window = make_window()
    window._echo_enabled = True
    window._loop.echo = True
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    window._last_state = PlaybackState.PLAYING
    window._toggle_loop(True)
    go_compact(window)
    assert window._attempt_button.isVisibleTo(window) is False  # still playing the line

    window._do_loop_wrap()  # the line ends: your turn
    finish_reveal(window)
    assert window._loop.phase is w.LoopPhase.ATTEMPT
    assert window._reveal == 1.0
    assert window._attempt_button.isVisibleTo(window) is True

    window._on_attempt_done_clicked()
    finish_reveal(window)
    assert window._reveal == 0.0


def test_compact_off_takes_the_fade_away_rather_than_leaving_it_idle(make_window):
    """An effect that is doing nothing is still an effect, on every repaint
    of every control."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    assert window._reveal_effects == {}
    for button in window._revealable():
        assert button.graphicsEffect() is None

    go_compact(window)
    assert len(window._reveal_effects) == len(window._revealable())
    for button in window._revealable():
        assert button.graphicsEffect() is not None

    window._set_compact(False)
    APP.processEvents()
    assert window._reveal_effects == {}
    for button in window._revealable():
        assert button.graphicsEffect() is None
    assert window._reveal == 1.0


def test_a_hidden_window_does_not_come_back_revealed(make_window):
    """The reveal goes back with the watch: coming out of the menu bar with
    the controls already showing would be the window answering a hover
    nobody performed."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    hover(window, True)
    assert window._reveal == 1.0

    window._set_lyrics_visible(False)
    land(window)
    finish_reveal(window)
    assert window._hovered is False
    assert window._reveal == 0.0


# -- a press at a control, routed the way a real one is -------------------
#
# Everything above this line reaches a control by calling its slot, its
# click(), or by asking whether it is visibleTo the window. None of that
# asks the question a user asks with a finger, which is "what is at this
# point, and what did it do with the press?" — and a control can be
# visible, wired and correct while the answer is "the window, and it
# started a drag". That failure looks like every control on the window
# being dead, and nothing above would have gone red for it.
#
# So these send the press to the top-level QWindow rather than to a
# widget. That is the one path that runs Qt's own hit testing: the widget
# under the point is found there, and a press that finds nothing lands on
# the window and starts a drag. Sent at each control's ACTUAL position,
# taken from its geometry rather than named, so a control that moves
# without its test moving is still being pressed where it is.


def press_through(window, point):
    """Press and release at a window-local point, hit-tested by Qt.

    Delivered to ``windowHandle()``, never to a widget: sending to the
    widget names the receiver, which is the whole of what needs proving
    wrong. This is also why the window is shown first, since a widget with
    no window handle has nothing to route through.
    """
    handle = window.windowHandle()
    assert handle is not None, "the window must be shown to be pressed on"
    globally = QPointF(window.mapToGlobal(point))
    for kind, buttons in (
        (QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton),
    ):
        APP.sendEvent(
            handle,
            QMouseEvent(
                kind,
                QPointF(point),
                globally,
                Qt.MouseButton.LeftButton,
                buttons,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
    APP.processEvents()


class PressRecord:
    """What a press at a point did: whether the control acted, and whether
    the window took it for a drag instead.

    Both halves matter and neither implies the other. A control that acted
    is not proof the window kept its hands off, and a window that started
    no drag is not proof anything was pressed.
    """

    def __init__(self, window, button):
        self._window = window
        self._button = button
        self.acted = 0
        self.dragged = 0
        self._original = type(window).mousePressEvent

    def __enter__(self):
        record = self

        def spy(window, event):
            record.dragged += 1
            return record._original(window, event)

        # On the type, not the instance: a QWidget's event handlers are
        # looked up on the class, so an instance attribute would never be
        # called and the drag would go unrecorded.
        type(self._window).mousePressEvent = spy
        self._connection = self._button.clicked.connect(self._acted)
        return self

    def _acted(self, *_):
        self.acted += 1

    def __exit__(self, *_):
        type(self._window).mousePressEvent = self._original
        self._button.clicked.disconnect(self._connection)
        return False


def shown(window):
    """On screen, so it has a window handle to route a press through."""
    window.show()
    APP.processEvents()
    return window


def pressing(window, button):
    """Press at the centre of a control and say what happened."""
    with PressRecord(window, button) as record:
        press_through(window, button.geometry().center())
    return record


def test_the_harness_can_tell_a_control_from_the_window(make_window):
    """The guard on the two tests below. A press that lands on the window's
    own chrome must record a drag and no control, or a suite where every
    control is dead would still be green: an assertion that the drag
    handler did not fire proves nothing if nothing ever makes it fire.
    """
    window = shown(make_window())
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()

    with PressRecord(window, window._loop_button) as record:
        # The middle of the window, which is lyrics and margin: nothing to
        # press, so the press is a drag.
        press_through(window, QPoint(window.width() // 2, window.height() // 2))
    assert record.acted == 0
    assert record.dragged == 1


def test_every_control_takes_its_own_press_in_the_full_layout(make_window):
    """A real press at each control's real position acts, and the window's
    drag handler never sees it.

    Korean lyrics because the spoken reference is offered for a hangul line
    and no other, and the point here is the press rather than the gate: a
    control that is not on the window cannot be pressed, and a test that
    quietly asserted that would be asserting nothing.
    """
    window = shown(make_window())
    window._echo_enabled = True
    window._loop.echo = True
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    window._last_state = PlaybackState.PLAYING
    APP.processEvents()

    for name, button in (
        ("loop", window._loop_button),
        ("spoken reference", window._speak_button),
    ):
        assert button.isVisibleTo(window) is True, name
        record = pressing(window, button)
        assert record.acted == 1, f"{name} did not act on a press at its own position"
        assert record.dragged == 0, f"{name}'s press reached the drag handler"

    # The echo done button exists only while the attempt is waiting, which
    # is also the only state its press means anything in.
    window._toggle_loop(True)
    window._do_loop_wrap()
    APP.processEvents()
    assert window._awaiting_attempt() is True
    record = pressing(window, window._attempt_button)
    assert record.acted == 1
    assert record.dragged == 0
    assert window._awaiting_attempt() is False  # the press ended the attempt

    # And the tap row, which a sync pass takes the full layout back for.
    window._begin_sync()
    APP.processEvents()
    for name, button in (
        ("tap", window._tap_button),
        ("undo", window._undo_button),
        ("discard", window._sync_exit_button),
    ):
        assert button.isVisibleTo(window) is True, name
        record = pressing(window, button)
        assert record.acted == 1, f"{name} did not act on a press at its own position"
        assert record.dragged == 0, f"{name}'s press reached the drag handler"
    window._cancel_sync()


def test_every_revealed_control_takes_its_own_press_in_the_strip(make_window):
    """The same again in compact, where the controls are somewhere else and
    are only there at all once the pointer has arrived.

    Two layouts rather than one because they place these controls by
    different arithmetic, and a press is answered by where a control IS.
    """
    window = shown(make_window())
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    hover(window, True)
    APP.processEvents()

    for name, button in (
        ("loop", window._loop_button),
        ("spoken reference", window._speak_button),
    ):
        assert button.isVisibleTo(window) is True, name
        record = pressing(window, button)
        assert record.acted == 1, f"{name} did not act on a press at its own position"
        assert record.dragged == 0, f"{name}'s press reached the drag handler"


def test_with_the_yield_off_nothing_ever_asks_for_click_through(make_window):
    """Click-through belongs to Ghost and to nothing else.

    The ghost tests below say it goes on and comes off with the fade. This
    says the switch is never touched at all in the mode the app ships in,
    which is the state a window whose every control is dead would be in if
    the switch had been initialised or left set: off is not "asked for and
    given back", it is never asked for.
    """
    asked = []
    original = w.LyricsWindow._apply_click_through

    def record(self, ignore):
        asked.append(ignore)
        return original(self, ignore)

    with patch.object(w.LyricsWindow, "_apply_click_through", record):
        window = shown(make_window())
        load(window, KOREAN_SYNCED)
        window._on_position_update(snapshot(position=2.0))
        go_compact(window)
        hover(window, True)
        hover(window, False)

    assert window._proximity_mode == proximity.OFF
    assert asked == []
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    )


def test_the_failure_reason_lands_under_the_message_in_compact(make_window):
    """The upcoming row it normally uses is gone, and the pronunciation row
    directly under the message has taken its place: same argument, one row
    up."""
    window = make_window()
    window._on_track_change(snapshot())
    window._title_card_until = 0.0
    window._on_fetch_finished(
        "t1", None, False, FetchFailure(kind="unreachable", attempt="album match")
    )
    go_compact(window)
    window._toggle_why(True)
    APP.processEvents()
    detail = window._view_model.display().detail
    assert detail
    assert window._pron.text() == detail
    assert window._pron.isVisibleTo(window) is True
    assert window._why_button.isVisibleTo(window) is True

    window._toggle_why(False)
    APP.processEvents()
    assert window._pron.text() == ""


def test_the_resize_floor_follows_the_layout(make_window):
    """Dragging the bottom edge up must reach the strip in compact and stop
    at five rows out of it."""
    window = make_window()
    window.setGeometry(100, 100, 460, 300)
    APP.processEvents()

    def drag_the_bottom_edge_up():
        window._press_global = QPoint(560, 400)
        window._press_geometry = window.geometry()
        window._resize_edges = Qt.Edge.BottomEdge
        window._apply_resize(QPoint(560, 100))
        APP.processEvents()
        window._resize_edges = Qt.Edges()
        return window.height()

    assert drag_the_bottom_edge_up() == w.min_window_height(window._scale)
    go_compact(window)
    assert drag_the_bottom_edge_up() == w.min_window_height(
        window._scale, compact=True
    )


# -- docking to the top ---------------------------------------------------


def test_dock_to_top_centres_the_window_under_the_menu_bar(make_window):
    window = make_window()
    screen = window.screen() or APP.primaryScreen()
    geometry = screen.geometry()
    available = screen.availableGeometry()
    window.move(geometry.x() + 40, geometry.y() + 400)
    APP.processEvents()

    window._dock_to_top()
    finish_move(window)

    expected = w.docked_position(
        window.width(),
        (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
        (available.x(), available.y(), available.width(), available.height()),
        window._top_inset(),
    )
    assert (window.pos().x(), window.pos().y()) == expected


def test_dock_leaves_the_window_as_draggable_as_it_found_it(make_window):
    """An explicit command, not a snap: nothing holds the window there."""
    window = make_window()
    window._dock_to_top()
    finish_move(window)
    docked = window.pos()
    window.move(docked.x() + 120, docked.y() + 200)
    APP.processEvents()
    assert window.pos() != docked


def test_dock_records_where_it_put_the_window(make_window):
    """Written from the target rather than from the window: the travel
    takes a phase length, and a save mid-journey would record a waypoint."""
    window = make_window()
    window._dock_to_top()
    saved = window._settings.value("window/pos")
    finish_move(window)
    assert (saved.x(), saved.y()) == (window.pos().x(), window.pos().y())


def test_dock_is_learned_like_the_end_of_a_drag(make_window):
    """The per-app layer would otherwise undo it on the next app switch:
    docking is the user saying where the window goes, which is the same
    thing a drag says."""
    window = make_window()
    window._remember_position = True
    window._frontmost = "com.example.editor"
    window._dock_to_top()
    finish_move(window)
    assert window._positions.peek("com.example.editor") == (
        window.pos().x(),
        window.pos().y(),
    )


def test_dock_moves_where_the_flight_will_put_the_window_back(make_window):
    """Away at the menu bar, the flight is holding the real position and
    hands it back at the end of the journey. Moving the window instead
    would be undone by the landing."""
    window = make_window()
    window.apply_saved_visibility()
    window._set_lyrics_visible(False)
    land(window)
    window._flight_home = (10, 10, window.width(), window.height())

    window._dock_to_top()
    screen = window.screen() or APP.primaryScreen()
    geometry = screen.geometry()
    available = screen.availableGeometry()
    expected = w.docked_position(
        window.width(),
        (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
        (available.x(), available.y(), available.width(), available.height()),
        window._top_inset(),
    )
    assert window._flight_home[:2] == expected


def test_the_safe_area_is_zero_where_it_cannot_be_asked(make_window):
    """Off cocoa there is no NSWindow to ask, and the available area is
    already the whole answer."""
    window = make_window()
    assert window._nswindow() is None
    assert window._top_inset() == 0


# -- sizing the strip to the song -----------------------------------------
#
# The arithmetic is pure and lives in tests/test_geometry.py, including the
# cap and the docked case. What is checked here is the wiring: what gets
# measured, when it is allowed to move the window, and what stops it.

# Lines chosen to land between the narrowest window and the offscreen
# platform's cap (800pt screen, so 400), which is what makes a fitted width
# something other than one of the two clamps.
FITTED = TrackLyrics(
    synced=[
        (1.0, "a middling line"),
        (5.0, "a rather longer line than that"),
        (9.0, "short"),
    ]
)
# Far past any cap: one outlier that must not widen the whole song.
OUTLIER = TrackLyrics(
    synced=[(1.0, "short"), (5.0, "an extravagantly long line " * 8)]
)
# Hangul that romanises to something much longer than it is. Syllables
# picked for the ratio rather than the meaning: 쌍 is five Latin letters,
# and five letters of the pronunciation type are wider than one hangul
# glyph of the sung type by enough that no font substitution can reverse
# it. The obvious fixture ("안녕하세요 반갑습니다") does not: 174 against 170.
ROMANISED = TrackLyrics(synced=[(1.0, "쌍쌍쌍쌍쌍쌍"), (5.0, "잘 가")])


def press(window, point):
    """A left-button press at a point in the window, as Qt delivers it."""
    window.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(point),
            QPointF(window.mapToGlobal(point)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def widest_sung(window, lyrics):
    """The widest line, measured here rather than asked of the window."""
    window._current.ensurePolished()
    metrics = QFontMetricsF(window._current.font())
    return max(metrics.horizontalAdvance(text) for _, text in lyrics.synced)


def expected_width(window, text_width):
    screen = window.screen() or APP.primaryScreen()
    return w.fitted_window_width(
        text_width,
        window._scale,
        w._MIN_WIDTH,
        w.width_cap(screen.availableGeometry().width()),
    )


def room_for_text(window):
    """How much of the window's width is the line's, at the scale in force.

    The gutters are asked for rather than assumed, because they follow the
    type size and the whole point of these tests is that neither the size
    nor the font is a constant.
    """
    return window.width() - 2 * w.compact_text_gutter(window._scale)


def screen_cap(window):
    screen = window.screen() or APP.primaryScreen()
    return w.width_cap(screen.availableGeometry().width())


def finish_fit(window):
    """Run the width animation to its end without waiting it out."""
    if window._fit_anim is not None:
        window._fit_anim.setCurrentTime(window._fit_anim.duration())
    APP.processEvents()


def test_fitting_is_on_by_default_and_only_acts_in_compact(make_window):
    """The one default-on setting in the app, and it can be: it is reached
    only from inside a layout that is itself opt-in and default off, so the
    plain synced-lyrics window is untouched either way."""
    window = make_window()
    assert window._fit_to_song is True
    assert window._compact_applied is False
    assert window._fitting is False

    go_compact(window)
    assert window._fitting is True


def test_the_strip_sizes_itself_when_a_song_arrives(make_window):
    """The properties of the fit, none of which is a pixel count.

    This used to assert the width landed under 460, which passed on macOS
    and failed on the Linux runner at 465. Nothing was wrong: the fixture's
    longest line measures 246pt in the font macOS resolves and about 321pt
    in the one fontconfig picks, and 465 is exactly ceil(321) plus the two
    gutters. The bound was a font measurement in disguise, so the test was
    really asserting which machine it ran on.

    What it should protect is what the feature promises, and all of it
    survives the font changing underneath.
    """
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    widest = widest_sung(window, FITTED)

    # It is the width the pure arithmetic asks for, given what was measured.
    assert window.width() == expected_width(window, widest)

    # Neither bound is what decided it, so the fit below is the real answer
    # rather than a clamp. Checked first, because the tightness only means
    # anything when nothing else got in the way.
    assert w._MIN_WIDTH < window.width() < screen_cap(window)

    # The widest line fits, and by less than a pixel: as small as the strip
    # can be while still showing the whole of it.
    assert 0 <= room_for_text(window) - widest < 1

    # Which is the same thing said where the user would see it: nothing on
    # screen is elided.
    assert window._current.text() == window._full_text[window._current]
    assert "…" not in window._current.text()


def test_a_song_too_wide_for_the_screen_is_capped_and_elides(make_window):
    """The other side of it. Past the cap the line does not fit and says so,
    and the window stops at the cap rather than growing past the screen."""
    window = make_window()
    go_compact(window)
    load(window, OUTLIER)
    finish_fit(window)

    assert window.width() == screen_cap(window)
    assert room_for_text(window) < widest_sung(window, OUTLIER)
    window._on_position_update(snapshot(position=6.0))
    APP.processEvents()
    assert window._current.text().endswith("…")


def test_the_fit_is_right_whatever_the_font_is(make_window):
    """The guard the stale bound should have been.

    A fitted width is a font measurement plus two gutters, so any test that
    names a pixel is really naming a font. Measured across the families this
    machine has, the fixture's longest line runs from 237pt (Times New
    Roman) to 351pt (Menlo) — a 48% spread that straddles the 316pt where a
    strip stops fitting inside 460, which is exactly how a green suite on
    one platform became a red one on another.

    So the property is asserted against whatever the font turns out to be,
    over several real families rather than the one the platform happens to
    default to. Nothing here is skipped when a platform is short of fonts:
    one family is still a family.
    """
    from PySide6.QtGui import QFontDatabase

    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)

    # Real families, taken from the platform rather than named: a hard-coded
    # family list would be the same portability bug one level up. The
    # leading-dot names are the system's own internal faces and are skipped
    # over as candidates, not skipped as tests.
    available = [f for f in QFontDatabase.families() if not f.startswith(".")]
    original = window._family_stack
    seen = {}
    try:
        for family in [original, *available]:
            stack = family if family is original else f'"{family}"'
            window._family_stack = stack
            window._restyle()
            widest = widest_sung(window, FITTED)
            window._fit_width(animate=False)
            finish_fit(window)
            assert 0 <= room_for_text(window) - widest < 1, family
            assert w._MIN_WIDTH <= window.width() <= screen_cap(window), family
            seen[round(widest)] = window.width()
            if len(seen) >= 4:
                break
    finally:
        window._family_stack = original
        window._restyle()

    # And the families really did disagree, or the loop above proved
    # nothing: a test that only ever saw one measurement is the old one.
    assert len(seen) > 1, f"only one text width seen across {len(available)} families"


def test_the_fitted_width_follows_the_chosen_type_size(make_window):
    """The same song at a larger size needs a wider window, and the room it
    leaves for the line still matches what that line now measures. Stated as
    a relation between the sizes rather than as five numbers, because the
    numbers are the font's and the relation is the app's."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)

    widths = []
    for size in w.COMPACT_TEXT_SIZES:
        window._set_compact_text_size(size)
        finish_fit(window)
        APP.processEvents()
        assert 0 <= room_for_text(window) - widest_sung(window, FITTED) < 1
        widths.append(window.width())

    assert widths == sorted(widths)
    assert widths[-1] > widths[0]


def test_the_strip_does_not_resize_on_a_line_change(make_window):
    """The point of measuring the whole song: as small as it can be
    WITHOUT MOVING while the song plays. A window that re-sized itself line
    by line would twitch its way through a verse."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    fitted = window.width()

    for position in (2.0, 6.0, 10.0):
        window._on_position_update(snapshot(position=position))
        APP.processEvents()
        assert window._fit_anim is None
        assert window.width() == fitted


def test_a_new_song_resizes_again(make_window):
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    fitted = window.width()

    load(window, SYNCED, track_id="t2")  # much shorter lines
    finish_fit(window)
    assert window.width() < fitted
    assert window.width() == expected_width(window, widest_sung(window, SYNCED))


def test_the_romanisation_line_is_measured_too(make_window):
    """It is a row the strip shows, and a long romanised line can be the
    widest thing in a song even in its smaller type."""
    window = make_window()
    go_compact(window)
    window._view_model.romanisation_enabled = False
    load(window, ROMANISED)
    finish_fit(window)
    without = window.width()

    window._set_romanisation(True)
    finish_fit(window)
    with_romanisation = window.width()

    window._pron.ensurePolished()
    pron = QFontMetricsF(window._pron.font())
    romanised = max(
        pron.horizontalAdvance(window._view_model.pronunciation_for(text))
        for _, text in ROMANISED.synced
    )
    sung = widest_sung(window, ROMANISED)
    assert romanised > sung, "the fixture no longer exercises this"
    assert with_romanisation > without
    assert with_romanisation == expected_width(window, romanised)


def test_one_outlier_line_cannot_widen_the_whole_song(make_window):
    window = make_window()
    go_compact(window)
    load(window, OUTLIER)
    finish_fit(window)
    # From the AVAILABLE width, which is what the window fits against.
    assert window.width() == screen_cap(window)


def test_the_song_takes_the_width_and_leaves_the_type_alone(make_window):
    """The song chooses the width; the type size is the user's and does not
    move with it. Without that there would be no width to find at all — the
    type would grow exactly as fast as the window, so a line that does not
    fit at one width would not fit at any.

    "The song moved it" is said as "the width is the fitted one", not as
    "the width is no longer 460": whether the fit happens to land on the
    width it started from is a fact about the font, and on some platform it
    will.
    """
    window = make_window()
    go_compact(window)
    users_width = window._compact_width
    scale = window._scale

    load(window, FITTED)
    finish_fit(window)
    assert window.width() == expected_width(window, widest_sung(window, FITTED))
    assert window._scale == scale
    assert window._compact_width == users_width  # the user's own, untouched


def test_the_strips_height_does_not_move_when_its_width_does(make_window):
    """A consequence of holding the scale, and the reason height adaptation
    needs no code: the floor comes off the same scale."""
    window = make_window()
    go_compact(window)
    height = window.height()
    floor = window.minimumHeight()

    load(window, FITTED)
    finish_fit(window)
    assert window.height() == height
    assert window.minimumHeight() == floor


def test_the_full_layout_is_unaffected(make_window):
    window = make_window()
    window.resize(460, 220)
    APP.processEvents()
    load(window, FITTED)
    APP.processEvents()
    assert window._fit_anim is None
    assert window.width() == 460


def test_the_fitted_width_never_becomes_the_full_layouts(make_window):
    """Each layout keeps the width the USER gave it. A fitted width is the
    song's and does not follow the window out of the strip."""
    window = make_window()
    window.resize(500, 220)
    APP.processEvents()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    # The song's width, whatever it works out to be in this font. Asserting
    # it is "not 500" would be asserting that the font does not happen to
    # produce 500, which is not a claim about the app.
    assert window.width() == expected_width(window, widest_sung(window, FITTED))

    window._set_compact(False)
    APP.processEvents()
    assert window.width() == 500


def test_a_manual_resize_turns_the_fitting_off(make_window):
    """Rather than fighting the user. Answered at the START of the drag, so
    the type scale follows the edge live instead of being pinned for the
    length of the gesture and jumping when it is let go."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    assert window._fit_to_song is True

    press(window, QPoint(2, 40))  # the left edge: inside the resize margin
    assert window._fit_to_song is False
    assert window._settings.value("window/fit_to_song", type=bool) is False
    assert window._menu.is_checked(m.FIT_TO_SONG) is False


def test_dragging_the_window_does_not_turn_the_fitting_off(make_window):
    """Moving is not resizing. Only an edge is the user taking the width
    back."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)

    press(window, QPoint(200, 40))  # the middle: a drag
    assert window._fit_to_song is True


def test_turning_it_off_gives_the_users_width_back(make_window):
    window = make_window()
    window.resize(500, 220)
    APP.processEvents()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    assert window.width() != 500

    window._set_fit_to_song(False)
    finish_fit(window)
    assert window.width() == 500
    # And the type is where it was throughout: the strip's size is the
    # user's to name, and the width was never what set it.
    assert window._scale == w.compact_scale(window._compact_text_size)


def test_the_setting_survives_a_restart(make_window):
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    window._save_settings()
    window._settings.sync()

    reopened = make_window()
    assert reopened._fit_to_song is False
    assert reopened._menu.is_checked(m.FIT_TO_SONG) is False


def test_reduce_motion_changes_the_size_without_travelling(make_window):
    """There is nothing here but travel, so it arrives instantly and the
    window is the same shape either way."""
    window = make_window()
    go_compact(window)
    tell(window, reduce_motion=True)
    load(window, FITTED)
    assert window._fit_anim is None
    assert window.width() == expected_width(window, widest_sung(window, FITTED))


def test_the_resize_travels_by_default(make_window):
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    assert window._fit_anim is not None
    assert window._fit_anim.duration() == w._MOVE_MS
    assert window._fit_anim.easingCurve().type() == w._MOVE_CURVE
    finish_fit(window)
    assert window._fit_anim is None


def test_the_entry_is_offered_only_where_it_can_act(make_window):
    window = make_window()
    window._refresh_menu()
    assert m.FIT_TO_SONG not in visible_keys(window)

    go_compact(window)
    assert m.FIT_TO_SONG in visible_keys(window)

    window._set_compact(False)
    assert m.FIT_TO_SONG not in visible_keys(window)


def test_nothing_to_fit_to_leaves_the_width_alone(make_window):
    """A song with no lyrics yet, or none at all: the width stays where it
    was rather than snapping to the floor."""
    window = make_window()
    window.resize(500, 220)
    APP.processEvents()
    go_compact(window)
    window._on_track_change(snapshot())
    APP.processEvents()
    assert window.width() == 500


def test_a_hidden_strip_is_fitted_before_it_flies_home(make_window):
    """The flight holds the window's real geometry and hands it back on
    landing, so a song that changed while the strip was away has to be
    answered before the journey rather than after it."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    window._set_lyrics_visible(False)
    land(window)

    load(window, FITTED)  # a new song, with the window away
    assert window._fit_anim is None  # refused: the flight owns the geometry

    window._set_lyrics_visible(True)
    land(window)
    assert window.width() == expected_width(window, widest_sung(window, FITTED))


def test_a_move_lands_a_resize_rather_than_abandoning_it(make_window):
    """The travel to a remembered position writes this window's position
    too. A window left at a waypoint would stay there, because nothing
    would ask again."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    load(window, FITTED)
    target = window._fit_anim.endValue()
    assert window._fit_anim is not None

    window._move_to(QPoint(80, 90))
    finish_move(window)
    assert window._fit_anim is None
    assert window.width() == target.width()


def test_the_flight_does_not_take_a_waypoint_home(make_window):
    """It captures the window's real geometry and hands it back on
    landing, so a resize in flight has to be landed first."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    load(window, FITTED)
    target = window._fit_anim.endValue()

    window._set_lyrics_visible(False)
    land(window)
    window._set_lyrics_visible(True)
    land(window)
    assert window.width() == target.width()


def test_shutdown_saves_the_width_it_was_heading_for(make_window):
    """Not the one it happened to be passing through."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    load(window, FITTED)
    target = window._fit_anim.endValue()

    window._shutdown()
    assert window._settings.value("window/size").width() == target.width()


# -- the strip's own type size --------------------------------------------


def elided_now(window):
    """What the sung row is actually showing, which in a strip is the line
    cut to fit rather than the line."""
    return window._current.text()


def resize_and_lay_out(window, width, height=None):
    """Resize a window nobody has shown, the way a shown one behaves.

    A hidden widget defers its resize event until it is shown, so these
    windows never run one; _relayout is the app's own answer to that and
    is what every path that changes the shape itself calls.
    """
    window.resize(width, window.height() if height is None else height)
    window._relayout()
    APP.processEvents()


def move_and_notice(window, point):
    """Move a window nobody has shown, and let it notice where it landed.
    moveEvent is deferred for the same reason resizeEvent is."""
    window.move(point)
    window._update_docked()
    APP.processEvents()


def test_the_strips_type_is_the_setting_and_not_the_width(make_window):
    """The bug this replaced: the type scale followed the window's width,
    so the room for a line and the line grew together and widening the
    strip could not show one more character of it."""
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    APP.processEvents()
    scale = window._scale

    for width in (300, 460, 900, 1400):
        resize_and_lay_out(window, width)
        assert window._scale == scale, width
        assert window._scale == w.compact_scale(window._compact_text_size)


def test_the_full_layout_still_takes_its_type_from_its_width(make_window):
    """Untouched, which is the layers rule: the setting above is reachable
    only from inside the strip and may not change the plain window."""
    window = make_window()
    for width in (300, 460, 900):
        resize_and_lay_out(window, width, 260)
        assert window._scale == w._scale_for(width)


def test_widening_the_strip_shows_more_of_the_line(make_window):
    """The whole point of decoupling them. Measured on the elided text
    rather than on a width: what the user is looking at is how much of the
    line is on screen."""
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    load(window, TrackLyrics(synced=[(1.0, "a line long enough that a narrow strip has to cut it short")]))
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()

    seen = []
    for width in (300, 500, 800, 1200):
        resize_and_lay_out(window, width)
        seen.append(len(elided_now(window)))
    assert seen == sorted(seen)
    assert seen[-1] > seen[0]


def test_the_strips_height_follows_its_type_size(make_window):
    window = make_window()
    go_compact(window)
    APP.processEvents()

    heights = []
    for size in w.COMPACT_TEXT_SIZES:
        window._set_compact_text_size(size)
        finish_fit(window)
        APP.processEvents()
        assert window._scale == w.compact_scale(size), size
        assert window.height() == w.min_window_height(
            window._scale, compact=True
        ), size
        heights.append(window.height())
    assert heights == sorted(heights)
    assert heights[-1] > heights[0]


def test_the_size_is_the_only_thing_that_moves_the_strips_height(make_window):
    """A width change must leave it alone, or the strip would be a window
    changing shape in two directions every time a song arrived."""
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    APP.processEvents()
    height = window.height()
    for width in (300, 700, 1100):
        resize_and_lay_out(window, width)
        assert window.height() == height, width


def test_the_strip_offers_no_vertical_resize(make_window):
    """Its height has one right answer, so an edge that showed a resize
    cursor and then refused would be worse than one that never claimed to.
    A press there moves the window instead."""
    window = make_window()
    go_compact(window)
    APP.processEvents()
    assert not window._hit_edges(QPoint(window.width() // 2, 1))
    assert not window._hit_edges(QPoint(window.width() // 2, window.height() - 1))
    # The sides still resize, and they are what the width is dragged by.
    assert window._hit_edges(QPoint(1, window.height() // 2)) & Qt.Edge.LeftEdge
    assert (
        window._hit_edges(QPoint(window.width() - 1, window.height() // 2))
        & Qt.Edge.RightEdge
    )


def test_the_full_layout_still_resizes_vertically(make_window):
    window = make_window()
    APP.processEvents()
    assert window._hit_edges(QPoint(window.width() // 2, 1)) & Qt.Edge.TopEdge
    assert (
        window._hit_edges(QPoint(window.width() // 2, window.height() - 1))
        & Qt.Edge.BottomEdge
    )


def test_dragging_a_side_of_the_strip_keeps_its_derived_height(make_window):
    """The drag path has its own height arithmetic, and it used to compute
    the floor from the width it was landing on. In a strip the width has
    nothing to say about it."""
    window = make_window()
    go_compact(window)
    APP.processEvents()
    height = window.height()
    window._press_global = QPoint(0, 0)
    window._press_geometry = window.geometry()
    window._resize_edges = Qt.Edge.RightEdge
    window._apply_resize(QPoint(120, 0))
    APP.processEvents()
    assert window.height() == height
    assert window.width() == window._press_geometry.width() + 120


def test_the_size_survives_a_restart(make_window):
    window = make_window()
    go_compact(window)
    window._set_compact_text_size(28)
    window._save_settings()
    window._settings.sync()

    reopened = make_window()
    assert reopened._compact_text_size == 28
    reopened._refresh_menu()
    assert reopened._menu.chosen(m.COMPACT_SIZE) == 28


def test_a_stored_size_that_is_not_a_preset_falls_back(make_window):
    """The same rule the speech rate is held to: a hand-edited or outgrown
    preference is not honoured, because the menu could not show it and the
    user would have no way back."""
    window = make_window()
    window._settings.setValue("window/compact_text_size", 9)
    window._settings.sync()

    reopened = make_window()
    assert reopened._compact_text_size == w.DEFAULT_COMPACT_TEXT_SIZE


def test_the_size_menu_appears_only_inside_the_strip(make_window):
    window = make_window()
    window._refresh_menu()
    assert window._menu.is_visible(m.COMPACT_SIZE) is False

    go_compact(window)
    window._refresh_menu()
    assert window._menu.is_visible(m.COMPACT_SIZE) is True


def test_changing_the_size_refits_the_window_to_the_song(make_window):
    """The widest line is a different width at a different size, so the
    fit has to be asked again — and it is the same travel a new song
    takes."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    small = window.width()

    window._set_compact_text_size(28)
    finish_fit(window)
    APP.processEvents()
    assert window.width() > small
    assert window.width() == expected_width(window, widest_sung(window, FITTED))


def test_changing_the_size_outside_the_strip_moves_nothing(make_window):
    """Off is off: the full layout's shape is not this setting's business,
    and the value is still recorded for the next time the strip is worn."""
    window = make_window()
    window.resize(460, 260)
    APP.processEvents()
    before = (window.size(), window._scale)

    window._set_compact_text_size(28)
    APP.processEvents()
    assert (window.size(), window._scale) == before
    assert window._compact_text_size == 28


# -- the docked shape ------------------------------------------------------


def dock_the_window(window):
    """Put the window exactly where docking would, without the travel."""
    x, y = window._docked_anchor()
    move_and_notice(window, QPoint(x, y))
    return x, y


def test_the_window_knows_when_it_is_docked(make_window):
    window = make_window()
    move_and_notice(window, QPoint(80, 300))
    assert window._docked is False

    dock_the_window(window)
    assert window._docked is True


def test_dragging_it_away_gives_the_rounded_shape_straight_back(make_window):
    window = make_window()
    dock_the_window(window)
    assert window._docked is True

    move_and_notice(window, window.pos() + QPoint(1, 0))
    assert window._docked is False


def test_a_width_change_under_a_docked_window_keeps_it_docked(make_window):
    """Docking centres on the screen, so the docked position moves with the
    width. The window is re-docked rather than centre-anchored, and it has
    to still recognise itself afterwards."""
    window = make_window()
    go_compact(window)
    dock_the_window(window)
    assert window._docked is True

    window._resize_width_to(window.width() + 180, animate=False)
    APP.processEvents()
    assert window._docked is True
    assert window.pos().toTuple() == window._docked_anchor()


def test_a_resize_alone_can_undock_a_window(make_window):
    """A window that stays put while its width changes is no longer
    centred, and says so."""
    window = make_window()
    dock_the_window(window)
    resize_and_lay_out(window, window.width() + 60)
    assert window._docked is False


def test_the_docked_panel_has_square_top_corners(make_window):
    """The whole visual claim, read off the pixels. The very corner pixel
    is the panel when docked and the backdrop when not."""
    window = make_window()
    resize_and_lay_out(window, 400, 90)

    def corner_pixels(docked):
        window._docked = docked
        image = panel_pixels(window, QRect(0, 0, 400, 90), False, 2.0)
        return (
            image.pixel(0, 0),
            image.pixel(image.width() - 1, 0),
            image.pixel(0, image.height() - 1),
            image.pixel(image.width() - 1, image.height() - 1),
        )

    rounded = corner_pixels(False)
    squared = corner_pixels(True)
    # Nothing is painted into a rounded corner: all four are the empty fill.
    assert len(set(rounded)) == 1
    # Docked, the two at the top are painted and the two underneath are not.
    assert squared[0] != rounded[0]
    assert squared[1] != rounded[1]
    assert squared[2] == rounded[2]
    assert squared[3] == rounded[3]


def test_the_rounded_path_is_what_drawroundedrect_always_drew(make_window):
    """One builder serves both shapes, so the undocked window must come out
    byte for byte as it did — otherwise "square top corners when docked"
    would have quietly restyled every other window in the app."""
    from PySide6.QtGui import QImage, QPainter

    for ratio in (1.0, 2.0):
        size, radius = 200, float(w._CORNER_RADIUS)
        rect = QRectF(0, 0, size, size)

        def render(use_path):
            image = QImage(int(size * ratio), int(size * ratio),
                           QImage.Format.Format_ARGB32)
            image.setDevicePixelRatio(ratio)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.white)
            if use_path:
                painter.drawPath(w._panel_path(rect, radius, False))
            else:
                painter.drawRoundedRect(rect, radius, radius)
            painter.end()
            return pixels_of(image)

        assert render(True) == render(False), f"the outline moved at {ratio}x"


def test_a_docked_band_is_still_drawn_straight_and_is_the_same_pixels(make_window):
    """The fast path is about the sides, which are straight in both shapes.
    Asserted for the docked one rather than assumed, because the whole
    reason the fast path is allowed to exist is that somebody checked."""
    window = make_window()
    window._docked = True
    for top in range(w._CORNER_RADIUS, window.height() - w._CORNER_RADIUS - 8, 7):
        damaged = QRect(0, top, window.width(), 8)
        fast = panel_pixels(window, damaged, True, 2.0)
        slow = panel_pixels(window, damaged, False, 2.0)
        device = QRect(0, int(top * 2), int(window.width() * 2), 16)
        assert pixels_of(fast, device) == pixels_of(slow, device), (
            f"the docked band at y={top} differs"
        )


def test_the_material_is_told_the_same_two_corners(make_window):
    """The blur is a native view under the painted scrim. Rounded at the
    top while the scrim is square would show the desktop through two
    notches at exactly the corners the shape exists to remove."""
    assert vibrancy.masked_corners(False) == vibrancy.ALL_CORNERS
    assert vibrancy.masked_corners(True) == vibrancy.BOTTOM_CORNERS
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_TOP_LEFT == 0
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_TOP_RIGHT == 0
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_BOTTOM_LEFT
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_BOTTOM_RIGHT


def test_asking_for_the_material_corners_without_one_is_harmless(make_window):
    """Off cocoa there is no material, and this runs on every move."""
    window = make_window()
    assert window._material is None
    window._apply_material_corners()  # must not raise


# -- yielding to the pointer ----------------------------------------------
#
# The pure rules — the region, the hysteresis, the destination, the gate —
# are in test_proximity.py. What is here is what only a real window can
# answer: that a temporary position is never mistaken for a permanent one,
# that the song and the pointer do not both own the width, and that the
# window comes back exactly.


def poll(window, point):
    """One turn of the pointer poll with the pointer at a screen point,
    then everything it started run to its end."""
    with pointer_at(window, point):
        window._check_pointer()
    finish_move(window)
    finish_ghost(window)
    finish_reveal(window)


def press_at(window, point):
    """A left press landing on the window at a screen point."""
    local = QPointF(point - window.frameGeometry().topLeft())
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        local,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def release():
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(1, 1),
        QPointF(1, 1),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def finish_ghost(window):
    """Run the ghost fade to its end without waiting it out."""
    if window._ghost_anim is not None:
        window._ghost_anim.setCurrentTime(window._ghost_anim.duration())
    APP.processEvents()


def arrive(window):
    """The pointer arrives over the middle of the window."""
    poll(window, over(window))


def leave(window):
    """The pointer goes somewhere else entirely."""
    poll(window, away_from(window))


def with_mode(window, mode):
    """Turn the layer on with the pointer definitely elsewhere, and give
    the window a position with room around it in every direction."""
    window.apply_saved_visibility()
    land(window)
    window.move(600, 400)
    APP.processEvents()
    with pointer_at(window, away_from(window)):
        window._set_proximity_mode(mode)
    return window


def test_the_layer_is_off_until_it_is_asked_for(make_window):
    """Default-off like every layer here, and off removes the work: with
    the compact layout off too, nothing is watching the pointer at all."""
    window = make_window()
    window.apply_saved_visibility()
    land(window)
    assert window._proximity_mode == proximity.OFF
    assert window._menu.chosen(m.PROXIMITY) == proximity.OFF
    assert window._pointer_timer.isActive() is False


def test_the_mode_is_persisted_and_restored(make_window):
    window = with_mode(make_window(), proximity.GHOST)
    window._save_settings()
    window._settings.sync()
    second = make_window()
    assert second._proximity_mode == proximity.GHOST
    assert second._menu.chosen(m.PROXIMITY) == proximity.GHOST


def test_a_mode_nobody_recognises_is_off(make_window):
    """A hand-edited or outgrown preference. The same rule the speech rate
    and the strip's type size are held to."""
    window = make_window()
    window._settings.setValue("window/proximity", "Vanish")
    window._settings.sync()
    second = make_window()
    assert second._proximity_mode == proximity.OFF


def test_the_layer_watches_the_pointer_in_the_full_layout_too(make_window):
    """The compact layout is not a condition of this one: a full-size
    window is as much in somebody's way as a strip."""
    window = with_mode(make_window(), proximity.DODGE)
    assert window._compact_applied is False
    assert window._pointer_timer.isActive() is True

    with pointer_at(window, away_from(window)):
        window._set_proximity_mode(proximity.OFF)
    assert window._pointer_timer.isActive() is False


# -- dodge ----------------------------------------------------------------


def test_the_window_steps_aside_and_comes_back_exactly(make_window):
    """The promise, and it is a promise about a pixel: a docked window is
    recognised by BEING exactly where docking put it, so anything less
    than exact would leave it drawn as a floating panel afterwards."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()

    arrive(window)
    assert window.pos() != home
    assert window._proximity_home == home

    leave(window)
    assert window.pos() == home
    assert window._proximity_home is None


def test_a_dodged_window_is_not_where_it_belongs(make_window):
    """The two are separate questions and the whole layer rests on it."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    assert window._home_pos() == home
    assert window._home_pos() != window.pos()
    assert window._home_rect().size() == window.size()


def test_a_temporary_position_is_never_learned(make_window):
    """The explicit requirement, and the reason for it: a position the
    pointer caused is not a preference the user expressed, and recording
    one would teach the map that this app's own dodging is where the
    window lives."""
    window = with_mode(make_window(), proximity.DODGE)
    window._set_remember_position(True)
    settle(window, "com.apple.Safari")
    home = window.pos()

    arrive(window)
    assert window.pos() != home
    window._learn_position()
    assert window._positions.peek("com.apple.Safari") == (home.x(), home.y())


def test_a_temporary_position_is_never_saved(make_window):
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    window._save_settings()
    assert window._settings.value("window/pos") == home


def test_shutdown_brings_the_window_home_before_it_saves(make_window):
    """The flight's rule seen once more: something is holding the window's
    real position, and the save must not persist the loan."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    window._shutdown()
    assert window.pos() == home
    assert window._settings.value("window/pos") == home


def test_a_remembered_position_moves_the_home_rather_than_the_window(
    make_window,
):
    """A window standing aside is moved by changing where it will step
    BACK to. Moving it would be pointless: the yield is holding the real
    position and would hand the old one straight back."""
    window = with_mode(make_window(), proximity.DODGE)
    window._set_remember_position(True)
    settle(window, "com.apple.Safari")
    window.move(500, 500)
    window._learn_position()
    settle(window, "com.apple.Terminal")
    window.move(900, 700)
    window._learn_position()

    arrive(window)
    dodged = window.pos()
    settle(window, "com.apple.Safari")
    finish_move(window)
    assert window._home_pos() == QPoint(500, 500)
    assert window.pos() != dodged

    leave(window)
    assert window.pos() == QPoint(500, 500)


def test_the_song_and_the_pointer_do_not_both_own_the_width(make_window):
    """Fitting measures from where the window BELONGS. Anchoring a width
    change on a dodged position would centre the new width on a temporary
    one and then hand that back as the permanent one."""
    window = with_mode(make_window(), proximity.DODGE)
    go_compact(window)
    window._proximity.release()
    home = window.pos()
    width = window.width()

    arrive(window)
    dodged = window.pos()
    window._resize_width_to(width + 200)
    finish_fit(window)
    assert window.width() == width + 200
    assert window._home_pos() != dodged
    # Anchored on the home's centre, which is what it would have been with
    # no pointer anywhere near it.
    assert window._home_pos().x() == home.x() - 100

    leave(window)
    assert window.pos() == window._home_pos()
    assert window.width() == width + 200


def test_taking_the_window_by_hand_adopts_where_it_is(make_window):
    """A press can only reach a window that stepped aside if the user
    followed it there, which is the gesture for taking it back. Nothing
    steps aside again until the pointer has left and come back."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    dodged = window.pos()

    window.mousePressEvent(press_at(window, dodged))
    assert window._proximity_home is None
    assert window.pos() == dodged
    assert window._proximity.engaged is True
    assert window._proximity.active is False
    window.mouseReleaseEvent(release())

    arrive(window)
    assert window.pos() == dodged  # not re-armed by a pointer that never left
    assert home != dodged


def test_a_dodged_window_can_be_followed_without_it_running_away(make_window):
    """Following it is not leaving it. Without that half the pointer
    chasing it would leave the home region, the window would come back,
    and it would arrive where the pointer no longer is."""
    window = with_mode(make_window(), proximity.DODGE)
    arrive(window)
    dodged = window.pos()
    poll(window, over(window))
    assert window.pos() == dodged


def test_the_window_stays_put_when_there_is_nowhere_to_go(make_window):
    """Said rather than faked. A window shuffled to a position still under
    the pointer would uncover nothing and look like the feature working."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    with patch.object(w.LyricsWindow, "_dodge_target", lambda self, home: None):
        arrive(window)
    assert window.pos() == home
    assert window._proximity_home is None


def test_reduce_motion_takes_the_travel_and_leaves_the_answer(make_window):
    """The layer is about where the window ends up, not about how it gets
    there — the same reading the travel to a remembered position gets."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    window._display_options = accessibility.DisplayOptions(reduce_motion=True)

    with pointer_at(window, over(window)):
        window._check_pointer()
    assert window._move_anim is None
    assert window.pos() != home

    with pointer_at(window, away_from(window)):
        window._check_pointer()
    assert window._move_anim is None
    assert window.pos() == home


# -- ghost ----------------------------------------------------------------


def test_ghosting_fades_the_window_and_lets_the_clicks_through(make_window):
    window = with_mode(make_window(), proximity.GHOST)
    home = window.pos()
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)

    arrive(window)
    assert window.pos() == home  # nothing moves, so nothing has to move back
    assert window.windowOpacity() == pytest.approx(proximity.GHOST_CEILING, abs=OPACITY_STEP)
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    )

    leave(window)
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    )


def test_the_clicks_go_through_at_the_start_of_the_fade_not_the_end(
    make_window,
):
    """The pointer arriving is the request. A window that stayed clickable
    for the length of a fade would swallow exactly the click the user came
    to make."""
    window = with_mode(make_window(), proximity.GHOST)
    with pointer_at(window, over(window)):
        window._check_pointer()
    assert window._ghost_anim is not None  # still fading
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    )


def test_a_ghosted_strip_does_not_offer_controls_it_cannot_take(make_window):
    """The mirror of the rule about a widget at zero opacity: a control
    that can be seen and not pressed is worse on screen than off it."""
    window = with_mode(make_window(), proximity.GHOST)
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._proximity.release()

    arrive(window)
    assert window._reveal == 0.0
    assert window._loop_button.isVisibleTo(window) is False

    leave(window)
    assert window._reveal == 0.0  # the pointer is elsewhere now


def test_the_two_ceilings_compose_rather_than_argue(make_window):
    """A banner over a ghosted window leaves it at whichever of the two is
    fainter, which is the one that is more out of the way. Everything that
    scales this window's opacity still composes in one place.

    Asserted as the property rather than as the arithmetic, because the
    two ceilings happen to be the same number today and an equality would
    pass without saying anything the day one of them moves."""
    window = with_mode(make_window(), proximity.GHOST)
    arrive(window)
    window._set_yielding(True)
    settle_yield(window)
    assert window.windowOpacity() <= min(
        proximity.GHOST_CEILING, n.YIELD_CEILING
    ) + OPACITY_STEP
    leave(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_a_ghost_is_never_brighter_than_the_user_asked_for(make_window):
    window = with_mode(make_window(), proximity.GHOST)
    for opacity in (0.25, 0.5, 1.0):
        window._set_opacity(opacity)
        arrive(window)
        assert window.windowOpacity() <= window._opacity + OPACITY_STEP
        leave(window)
        assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_hiding_gives_the_ghost_back_before_the_flight_borrows_it(make_window):
    """The yield's rule: a window that goes away faded would come back
    faded, because the level is not something the flight restores."""
    window = with_mode(make_window(), proximity.GHOST)
    arrive(window)
    assert window._ghost_level == 1.0

    window._set_lyrics_visible(False)
    land(window)
    assert window._ghost_level == 0.0
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    )


# -- the suspensions ------------------------------------------------------


def test_switching_mode_hands_back_what_the_old_one_borrowed(make_window):
    """Dodge to Ghost cannot leave a window standing aside with nothing
    left that remembers where it came from."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    assert window.pos() != home

    with pointer_at(window, over(window)):
        window._set_proximity_mode(proximity.GHOST)
    finish_move(window)
    assert window.pos() == home
    assert window._proximity_home is None
    assert window._ghost_level == 0.0  # edge triggered: it did not re-arm


@pytest.mark.parametrize("mode", [proximity.DODGE, proximity.GHOST])
def test_a_sync_pass_suspends_the_whole_layer(make_window, mode):
    """A rhythm game played on a button on this window, once per line.
    Ghosting it would send the taps to whatever is behind it and Dodging
    it would move the target mid-song."""
    window = with_mode(make_window(), mode)
    load(window, PLAIN, track_id="t7")
    home = window.pos()

    arrive(window)
    window._begin_sync()
    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0
    assert window._proximity.engaged is True  # the pointer did not go anywhere

    # And it does not re-arm under a hand that never left.
    window._cancel_sync()
    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0


@pytest.mark.parametrize("mode", [proximity.DODGE, proximity.GHOST])
def test_an_echo_attempt_suspends_the_whole_layer(make_window, mode):
    """The song is paused, the turn is the user's, and the done button is
    the only way out of it."""
    window = with_mode(make_window(), mode)
    window._echo_enabled = True
    window._loop.echo = True
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    window._last_state = PlaybackState.PLAYING
    window._toggle_loop(True)
    home = window.pos()

    arrive(window)
    window._do_loop_wrap()
    assert window._awaiting_attempt() is True
    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0


@pytest.mark.parametrize("mode", [proximity.DODGE, proximity.GHOST])
def test_an_open_failure_register_suspends_the_whole_layer(make_window, mode):
    """The click that closes it is the same click, on the same control."""
    window = with_mode(make_window(), mode)
    home = window.pos()
    window._toggle_why(True)

    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0
    assert window._proximity_refusal() == proximity.EXPLAINING


def test_a_drag_suspends_it(make_window):
    window = with_mode(make_window(), proximity.DODGE)
    window.mousePressEvent(press_at(window, over(window)))
    assert window._proximity_refusal() == proximity.DRAGGING
    window.mouseReleaseEvent(release())
    assert window._proximity_refusal() is None


def test_a_flight_suspends_it(make_window):
    """Two animations of one window's position could only fight, and the
    flight owns the opacity too until it lands."""
    window = with_mode(make_window(), proximity.DODGE)
    window._set_lyrics_visible(False)
    if window._flight_anim is not None:
        assert window._proximity_refusal() == proximity.FLYING
    land(window)
    assert window._proximity_refusal() == proximity.HIDDEN


def test_a_docked_window_steps_aside_and_docks_again(make_window):
    """Docked is recognised by BEING exactly where docking put it, so a
    window that came back a pixel off would be drawn square across the top
    while sitting in the middle of a screen."""
    window = with_mode(make_window(), proximity.DODGE)
    window._dock_to_top()
    finish_move(window)
    docked = window.pos()
    assert window._docked is True

    arrive(window)
    assert window.pos() != docked
    assert window._docked is False  # it is not under the menu bar any more

    leave(window)
    assert window.pos() == docked
    assert window._docked is True


def test_a_strip_steps_aside_too(make_window):
    """Both layouts. A strip is the shape most likely to be parked over
    somebody's work, and it steps vertically because that is the shorter
    way out of its own footprint."""
    window = with_mode(make_window(), proximity.DODGE)
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._proximity.release()
    finish_fit(window)
    home = window.pos()

    arrive(window)
    assert window.pos().x() == home.x()
    assert window.pos().y() != home.y()
    assert not window.frameGeometry().intersects(
        QRect(home, window.size())
    )

    leave(window)
    assert window.pos() == home


def test_a_window_at_the_origin_still_knows_where_it_belongs(make_window):
    """PySide gives QPoint a __bool__ and QPoint(0, 0) is FALSE, so a
    truth test on the held position would hand back where the window is
    standing as where it belongs — for exactly one window in a thousand,
    the one docked at the top-left of the primary screen."""
    window = with_mode(make_window(), proximity.DODGE)
    window.move(0, 0)
    APP.processEvents()

    arrive(window)
    assert window.pos() != QPoint(0, 0)
    assert window._home_pos() == QPoint(0, 0)

    leave(window)
    assert window.pos() == QPoint(0, 0)
