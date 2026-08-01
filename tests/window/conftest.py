"""The two fixtures every file in this directory stands on.

``make_window`` is the seam: the settings object is injected rather than
redirected globally, because QSettings.setDefaultFormat/setPath are
process-wide and silently do nothing on macOS. ``no_real_world`` is the
other half — every worker a window can start is stubbed, and the four
native doors a window opens on construction are answered with None rather
than blocked, which is the branch a machine without pyobjc takes.

The doors themselves are shut for the whole session one directory up, in
tests/conftest.py, and stay armed for anything that reaches around these.
"""

import pytest

from PySide6.QtCore import QSettings

from sottovoce import lyrics_provider as lp
from sottovoce import nsmenu
from sottovoce import player_events
from sottovoce import player_monitor as pmon
from sottovoce import window as w
from sottovoce.artwork import ArtworkProvider
from sottovoce.http_client import ConnectionPool
from sottovoce.lyrics_provider import LyricsProvider

from helpers import APP, REAL_FETCH_RUN, FakeLrclib, FakeSpotify, fake_kit


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

    WarmTask is on the list for a sharper version of the same reason: it
    makes a request PER TRACK on the album, sleeping between them, so left
    alone one window test would leave a worker asking LRCLIB questions for
    the next several seconds of the run.

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
                 w.ArtworkTask, w.WarmTask):
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
    # which is the plain window every other test here describes.
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


@pytest.fixture
def lrclib(monkeypatch):
    """A pool that opens fakes instead of sockets.

    The POOL is the real one: ``http_client.ConnectionPool`` with its own
    connect factory, which is the seam that module was given so the suite
    could exercise reuse and the stale-connection retry without a socket.
    Installed as the module's pool, so ``_fetch_json`` and ``post_json``
    find it the way they find the real one and nothing above here knows the
    difference.

    Here rather than in one file because two need it: a lyrics lookup and a
    publication are the same door, and faking either of them anywhere
    higher would be faking this app's own parts.
    """

    def install(*routes):
        service = FakeLrclib(*routes)
        monkeypatch.setattr(
            lp, "_pool", ConnectionPool(lp.LRCLIB_HOST, connect=service.connect)
        )
        return service

    return install


@pytest.fixture
def spotify(monkeypatch):
    """A fake Spotify under the real monitor, and the module state it
    touches put back afterwards."""
    fake = FakeSpotify()
    monkeypatch.setattr(pmon, "_ask", fake.answer)
    monkeypatch.setattr(pmon, "spotify_running", lambda: True)
    monkeypatch.setattr(pmon, "_moved", None, raising=False)
    yield fake
    pmon._wake.clear()
    pmon._moved = None


@pytest.fixture
def fetching(monkeypatch):
    """Give ``FetchTask`` its body back for the length of one test."""
    monkeypatch.setattr(w.FetchTask, "run", REAL_FETCH_RUN)


@pytest.fixture
def drawn(monkeypatch):
    """Open the door onto the AppKit stand-in, for the length of one test.

    Applied after ``no_real_world`` has answered it with None, so this wins
    for every window built inside the test. The session guard one directory
    up stays armed for anything that reaches around both.

    ``_HELPER_CLASS`` is reset because nsmenu caches the one Objective-C
    class it defines (defining it twice raises) and the cached one would
    otherwise be built against whichever kit came first and outlive the
    test that made it.
    """
    monkeypatch.setattr(nsmenu, "_HELPER_CLASS", None)
    monkeypatch.setattr(w.nsmenu, "_appkit", fake_kit)
