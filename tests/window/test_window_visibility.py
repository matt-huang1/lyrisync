"""Coming and going: the flight to the menu bar, the hotkey, and quit.

Hiding is a journey to the menu bar item and back, so "hidden" is where
it lands rather than what the click does. Hiding leaves everything else
running, and shutdown drains what the window owns before anything is
destroyed.
"""

TIER = "qt"  # a real window, driven by calling its own methods

import logging
import threading
import time

import pytest

from PySide6.QtCore import QEvent, QPoint, QPointF, QRunnable, Qt, QTimer
from PySide6.QtGui import QMouseEvent

from sottovoce import hotkey
from sottovoce import menu as m
from sottovoce import window as w
from sottovoce.player_monitor import PlaybackState, PlayerSnapshot
from sottovoce.view_model import Mode

from helpers import APP, PLAIN, SYNCED, land, load, release, snapshot


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


@pytest.mark.integration
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
