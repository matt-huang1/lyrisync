"""The guards in conftest.py, exercised.

A guard nobody runs is worth nothing — one of the four escapes this suite
has had was a tray test that silently never ran. So each door gets a test
that walks into it: through the app's own functions where possible, so
what is proven is that the real call path is blocked, not that a patch is
installed somewhere.

These are the only tests allowed to cause an escape on purpose, and they
drain the record afterwards so the autouse check does not fail them.
"""

import socket
import threading

import pytest

from sottovoce import hotkey
from sottovoce import lyrics_provider as lp
from sottovoce import player_monitor as pm
from sottovoce import speech


def test_the_lyrics_fetch_cannot_reach_lrclib(escapes):
    """The real escape, at the real call site: this is the function that
    was mid-ssl-handshake when CI aborted."""
    with pytest.raises(RuntimeError, match="test escape"):
        lp._fetch_json(lp.LRCLIB_GET_URL + "?track_name=x")
    assert any("lrclib.net" in e or "outbound network" in e for e in escapes.drain())


def test_a_raw_socket_cannot_leave_the_machine(escapes):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="test escape"):
        sock.connect(("lrclib.net", 443))
    sock.close()
    assert escapes.drain()


def test_create_connection_is_guarded_too(escapes):
    """urllib takes this path rather than socket.connect on some versions;
    both doors or neither."""
    with pytest.raises(RuntimeError, match="test escape"):
        socket.create_connection(("lrclib.net", 443), timeout=1)
    assert escapes.drain()


def test_loopback_still_works(escapes):
    """The guard must not be a blanket ban: Qt and pytest talk to
    themselves over local sockets, and a suite that cannot do that would
    be fixed by weakening the guard."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    accepted = threading.Thread(target=server.accept, daemon=True)
    accepted.start()

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(server.getsockname())
    client.close()
    accepted.join(timeout=2)
    server.close()

    assert escapes.drain() == []


def test_spotify_cannot_be_commanded(escapes):
    """osascript is how every player command reaches Spotify — a seek, a
    pause, a resume. Unstubbed, a test restarts the developer's music."""
    with pytest.raises(RuntimeError, match="test escape"):
        pm.set_position(0.0)
    assert escapes.drain()


def test_the_speakers_cannot_be_reached(escapes):
    with pytest.raises(RuntimeError, match="test escape"):
        speech.speak_korean("안녕하세요")
    assert escapes.drain()


def test_the_voice_check_is_a_subprocess_too(escapes):
    """detect_voice runs at window construction, so it escapes on every
    single Qt test unless it is stubbed."""
    with pytest.raises(RuntimeError, match="test escape"):
        speech.detect_voice()
    assert escapes.drain()


def test_a_bare_provider_refuses_to_touch_the_real_directories(escapes):
    """.user_syncs/ holds syncs the user tapped out by hand and nothing in
    this project may write over them, least of all a test."""
    with pytest.raises(RuntimeError, match="test escape"):
        lp.LyricsProvider()
    assert any(".user_syncs" in e for e in escapes.drain())


def test_a_provider_given_only_one_directory_refuses_as_well(escapes, tmp_path):
    with pytest.raises(RuntimeError, match="test escape"):
        lp.LyricsProvider(cache_dir=tmp_path / "cache")
    assert escapes.drain()


def test_an_injected_provider_is_fine(escapes, tmp_path):
    provider = lp.LyricsProvider(
        cache_dir=tmp_path / "cache", user_sync_dir=tmp_path / "syncs"
    )
    assert provider.cache_dir == tmp_path / "cache"
    assert escapes.drain() == []


def test_a_bare_artwork_provider_refuses_the_repo_directory(escapes):
    """Same hazard as the lyrics provider one directory along: a default
    ArtworkProvider writes derived cover colours into the repo, and every
    cache miss it takes is a request to Spotify's CDN."""
    from sottovoce import artwork

    with pytest.raises(RuntimeError, match="test escape"):
        artwork.ArtworkProvider()
    assert any(".artwork_cache" in e for e in escapes.drain())


def test_an_injected_artwork_provider_is_fine(escapes, tmp_path):
    from sottovoce import artwork

    provider = artwork.ArtworkProvider(cache_dir=tmp_path / "art")
    assert provider.cache_dir == tmp_path / "art"
    assert escapes.drain() == []


def test_album_artwork_cannot_be_downloaded(escapes):
    """The cover fetch is a real HTTP request to a CDN. Blocked at the
    socket like every other, and recorded so the test that reached for it
    fails rather than quietly getting no tint."""
    from sottovoce import artwork

    # The guard's own RuntimeError, not an ArtworkError: it is raised
    # below the layer that translates network failures, exactly as the
    # lyrics fetch guard above behaves. colour_for() then swallows it
    # broadly — which is why recording the escape, not raising it, is
    # what makes the offending test fail.
    with pytest.raises(RuntimeError, match="test escape"):
        artwork._download("https://i.scdn.co/image/whatever")
    assert any("outbound network" in e for e in escapes.drain())


def test_a_test_cannot_claim_a_system_wide_hotkey(escapes):
    """RegisterEventHotKey takes the combination from every other app for
    as long as the process lives — including the developer's, mid-run. The
    escape is recorded rather than raised out, because register() catches
    broad exceptions by design: a hotkey it cannot have is a logged fact,
    not a crash, so an exception alone would be swallowed here too."""
    hk = hotkey.GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)
    assert hk.register() is False
    assert hk.registered is False
    assert any("hotkey" in e for e in escapes.drain())


def test_the_real_preferences_file_cannot_be_opened(escapes):
    """The first escape this project had: QSettings("sottovoce", "sottovoce")
    is the user's own window position, opacity and toggles."""
    w = pytest.importorskip(
        "sottovoce.window",
        reason="PySide6 unusable (missing system Qt libraries?)",
        exc_type=ImportError,
    )
    with pytest.raises(RuntimeError, match="test escape"):
        w.QSettings("sottovoce", "sottovoce")
    assert escapes.drain()


def test_the_preferences_left_behind_by_the_old_name_cannot_be_opened(escapes):
    """The rename left ~/Library/Preferences/com.lyrisync.lyrisync.plist
    where it was, and the migration reads it once on a first launch. That
    is the developer's own file too — and a READ, which leaves nothing
    behind to notice afterwards, so it needs the alarm more than a write
    does. Every test of the migration passes its own factory instead."""
    from sottovoce import settings as preferences

    with pytest.raises(RuntimeError, match="test escape"):
        preferences._legacy_settings()
    assert any("lyrisync" in e for e in escapes.drain())


def test_a_settings_file_of_its_own_is_fine(escapes, tmp_path):
    w = pytest.importorskip(
        "sottovoce.window",
        reason="PySide6 unusable (missing system Qt libraries?)",
        exc_type=ImportError,
    )
    settings = w.QSettings(str(tmp_path / "own.ini"), w.QSettings.Format.IniFormat)
    settings.setValue("window/opacity", 1.0)
    settings.sync()
    assert (tmp_path / "own.ini").exists()
    assert escapes.drain() == []


def test_an_escape_off_the_main_thread_is_still_recorded(escapes):
    """The property the whole design rests on. Worker threads catch broad
    exceptions on purpose — a failed fetch is a retry state, not a crash —
    so a guard that only raised would be swallowed exactly where these
    escapes happen."""

    def swallow():
        try:
            socket.create_connection(("lrclib.net", 443), timeout=1)
        except Exception:
            pass  # precisely what FetchTask does

    worker = threading.Thread(target=swallow)
    worker.start()
    worker.join(timeout=5)
    assert escapes.drain(), "an escape on a worker thread went unrecorded"
