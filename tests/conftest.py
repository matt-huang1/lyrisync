"""Guards that hold the whole suite to one rule: a test touches nothing
real.

Not the network, not the developer's Spotify, not their saved settings,
not the lyrics cache or the syncs they tapped out by hand. Each of those
has a seam — an injected settings object, an injected cache directory, a
stubbed task — and the seams are the fix. What lives here is the alarm
that goes off when one of them is missed, because four escapes have now
been found by their symptoms rather than by the suite: real QSettings
writes, real player commands, a tray test that never ran, and a live
LRCLIB fetch that aborted CI mid-handshake.

Two properties matter, and the ordering of this file follows them:

- The call is stopped before it reaches anything real. Raising is not
  enough on its own; app code catches broad exceptions on its worker
  threads, exactly where these escapes happen.
- The test fails because of it. So every block is also recorded, and an
  autouse fixture fails the test that caused it whether the escape
  happened on the test's own thread, inside a QRunnable, or in a QThread
  that outlived the call.
"""

from __future__ import annotations

import socket
import subprocess
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_lock = threading.Lock()
_violations: list[str] = []


def _violation(message: str) -> RuntimeError:
    """Record an escape and build the exception to raise for it."""
    with _lock:
        _violations.append(message)
    return RuntimeError(f"test escape: {message}")


def _loopback(address) -> bool:
    """Local sockets are fine — Qt and pytest use them internally."""
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    return host in ("127.0.0.1", "::1", "localhost", "", None)


@pytest.fixture(scope="session", autouse=True)
def _no_real_world():
    """Close every door once, for the whole session."""
    patch = pytest.MonkeyPatch()

    # -- the network ------------------------------------------------------
    # At the socket, not at urllib: this catches the lyrics fetch however
    # it is spelled, including the ssl handshake that hung CI.
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def guard_connect(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
            raise _violation(f"outbound network connection to {address!r}")
        return real_connect(self, address, *args, **kwargs)

    def guard_connect_ex(self, address, *args, **kwargs):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address):
            raise _violation(f"outbound network connection to {address!r}")
        return real_connect_ex(self, address, *args, **kwargs)

    def guard_create_connection(address, *args, **kwargs):
        if not _loopback(address):
            raise _violation(f"outbound network connection to {address!r}")
        return real_create_connection(address, *args, **kwargs)

    patch.setattr(socket.socket, "connect", guard_connect)
    patch.setattr(socket.socket, "connect_ex", guard_connect_ex)
    patch.setattr(socket, "create_connection", guard_create_connection)

    # -- subprocesses (osascript to Spotify, `say` to the speakers) -------
    # The monitor, the player commands and the spoken reference all reach
    # the outside world through subprocess and nothing else, so one guard
    # covers all three. Tests that exercise those paths stub the function
    # above this line (`_osascript`, `speak_korean`, the QRunnables).
    real_run = subprocess.run
    real_popen = subprocess.Popen

    def guard_run(args, *rest, **kwargs):
        raise _violation(f"subprocess {args!r}")

    def guard_popen(args, *rest, **kwargs):
        raise _violation(f"subprocess {args!r}")

    patch.setattr(subprocess, "run", guard_run)
    patch.setattr(subprocess, "Popen", guard_popen)

    # -- the developer's own files ----------------------------------------
    # LyricsProvider's defaults are relative paths, so a bare provider in a
    # test reads and writes the repo's real .lyrics_cache/ and, worse,
    # .user_syncs/ — syncs the user tapped out by hand, which the app is
    # never allowed to lose. Both directories are constructor arguments
    # precisely so tests can point them somewhere temporary; refusing the
    # defaults is what makes skipping that a failure instead of a habit.
    from lyrisync.lyrics_provider import LyricsProvider

    real_init = LyricsProvider.__init__
    missing = object()

    def guard_init(self, cache_dir=missing, user_sync_dir=missing):
        if cache_dir is missing or user_sync_dir is missing:
            raise _violation(
                "LyricsProvider built on its default directories "
                "(.lyrics_cache/, .user_syncs/) — inject a tmp_path for both"
            )
        real_init(self, cache_dir, user_sync_dir)

    patch.setattr(LyricsProvider, "__init__", guard_init)

    # -- the developer's own login items ----------------------------------
    # SMAppService registers the app to launch at login for the real user,
    # and nothing about that is scoped to a test run: a stray call here
    # would leave a login item behind on the developer's Mac. Every native
    # call goes through _main_app_service, so blocking it is enough.
    from lyrisync import login_item

    def guard_service():
        raise _violation("SMAppService — a test may not register a login item")

    patch.setattr(login_item, "_main_app_service", guard_service)

    # -- the developer's own keyboard -------------------------------------
    # RegisterEventHotKey claims a combination system-wide for as long as
    # the process lives, so a stray registration here takes ⇧⌘J away from
    # whoever is running the suite — every window construction would do
    # it, and nothing about it is scoped to a test. Every native call goes
    # through _carbon, so blocking it is enough.
    from lyrisync import hotkey

    def guard_carbon():
        raise _violation("Carbon — a test may not claim a system-wide hotkey")

    patch.setattr(hotkey, "_carbon", guard_carbon)

    # -- the developer's own settings -------------------------------------
    # QSettings("lyrisync", "lyrisync") is the real ~/Library/Preferences
    # entry: the user's window position, size, opacity and every toggle.
    # The window takes a settings object precisely so tests can hand it a
    # file of their own, and this refuses the form that would not.
    # Imported here rather than at module scope so a broken PySide6 still
    # leaves the Qt-free tests runnable.
    try:
        from lyrisync import window as window_module
    except ImportError:  # pragma: no cover - PySide6 unusable
        window_module = None

    if window_module is not None:
        real_qsettings = window_module.QSettings

        def guard_qsettings(*args, **kwargs):
            organisation_and_application = (
                len(args) >= 2
                and isinstance(args[0], str)
                and isinstance(args[1], str)
            )
            if organisation_and_application or not args:
                raise _violation(
                    f"QSettings{args!r} — the real preferences file; "
                    "inject a QSettings(path, IniFormat) instead"
                )
            return real_qsettings(*args, **kwargs)

        # Still a drop-in: QSettings.Format is part of how it is called.
        guard_qsettings.Format = real_qsettings.Format
        patch.setattr(window_module, "QSettings", guard_qsettings)

    yield

    patch.undo()


@pytest.fixture
def escapes():
    """The recorded escapes, for the tests that check the guards.

    Draining is what lets those tests pass: they cause an escape on
    purpose, so they have to take it off the record before the autouse
    fixture below sees it. Nothing else may use this.
    """

    class Recorder:
        def drain(self) -> list[str]:
            with _lock:
                found = list(_violations)
                _violations.clear()
            return found

    return Recorder()


@pytest.fixture(autouse=True)
def _fail_on_escape():
    """Fail the test that caused an escape, wherever the call came from.

    Worker threads swallow exceptions by design (a failed fetch is a retry
    state, not a crash), so raising inside them is invisible to pytest.
    Checking the record after the test is what makes it loud.
    """
    with _lock:
        _violations.clear()
    yield
    with _lock:
        escaped = list(_violations)
        _violations.clear()
    assert not escaped, "this test reached something real:\n  " + "\n  ".join(escaped)
