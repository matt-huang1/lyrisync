"""Guards on the one directory the app must never touch destructively.

``.user_syncs/`` holds work the user did by hand, one tap per line. Unlike
``.lyrics_cache/`` it is not derived from anything and cannot be rebuilt by
re-fetching, so nothing in lyrisync may delete, clear, expire, or truncate
it — and no documented cleanup step may point at it.
"""

import re
from pathlib import Path

import pytest

from lyrisync import lyrics_provider as lp
from lyrisync.player_monitor import PlaybackState, PlayerSnapshot


PACKAGE_DIR = Path(lp.__file__).parent
REPO_ROOT = PACKAGE_DIR.parents[1]

# Every way Python deletes or truncates a file or directory.
_DELETION_CALLS = re.compile(
    r"\b(?:"
    r"shutil\.rmtree|rmtree"
    r"|os\.(?:remove|unlink|rmdir|removedirs)"
    r"|\.unlink\(|\.rmdir\("
    r"|open\([^)]*['\"]w"
    r")"
)


def source_files():
    return sorted(PACKAGE_DIR.glob("*.py"))


def test_the_package_contains_no_deletion_calls():
    """Broad by design: lyrisync only ever creates and reads files, so any
    deletion primitive appearing anywhere in it is a regression worth
    looking at — most of all one that could reach a user sync."""
    assert source_files(), "no package sources found"
    offenders = {
        path.name: _DELETION_CALLS.findall(path.read_text(encoding="utf-8"))
        for path in source_files()
    }
    assert {name: hits for name, hits in offenders.items() if hits} == {}


def test_no_source_file_mentions_clearing_the_user_sync_directory():
    name = lp.DEFAULT_USER_SYNC_DIR.name
    for path in source_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if name in line:
                assert not re.search(
                    r"\b(clear|delete|remove|wipe|reset|purge|prune)\b", line.lower()
                ), f"{path.name}: {line.strip()}"


def test_readme_cleanup_advice_never_points_at_user_syncs():
    """The README tells users to delete .lyrics_cache/ to reset. No
    sentence anywhere may say the same of .user_syncs/."""
    name = lp.DEFAULT_USER_SYNC_DIR.name
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    sentences = re.split(r"(?<=[.!?])\s+", readme)
    mentions = [s for s in sentences if name in s]
    assert mentions, "the README should say where hand-made syncs live"
    for sentence in mentions:
        assert not re.search(
            r"\b(delete|remove|clear|wipe|purge)\b", sentence.lower()
        ), sentence


def test_user_sync_directory_is_gitignored():
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert f"{lp.DEFAULT_USER_SYNC_DIR.name}/" in ignored


def test_user_syncs_are_not_stored_under_the_cache_directory():
    assert lp.DEFAULT_USER_SYNC_DIR != lp.DEFAULT_CACHE_DIR
    assert lp.DEFAULT_CACHE_DIR not in lp.DEFAULT_USER_SYNC_DIR.parents


@pytest.fixture
def provider(tmp_path):
    return lp.LyricsProvider(
        cache_dir=tmp_path / "cache", user_sync_dir=tmp_path / "user_syncs"
    )


def snapshot(track_id="track123", track_kind="track", title="Song"):
    return PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id=track_id,
        track_kind=track_kind,
        title=title,
        artist="Artist",
        album="Album",
        duration_ms=225000,
        position_seconds=1.0,
    )


def test_a_saved_sync_survives_every_provider_code_path(provider, monkeypatch):
    """Walk the provider through fetches, cache writes, negative caching,
    errors and non-music items, then check the sync file is byte-identical."""
    lrc = "[00:01.00] Mine first\n[00:04.25] Mine second\n"
    path = provider.save_user_sync("track123", lrc)

    def fake_fetch(url):
        if "track_name=Found" in url:
            return {"syncedLyrics": "[00:09.00] Theirs\n", "plainLyrics": "Theirs"}
        if "track_name=Broken" in url:
            raise lp.LyricsError("offline")
        return None  # 404 all the way down -> negative cache

    monkeypatch.setattr(lp, "_fetch_json", fake_fetch)

    provider.get_lyrics(snapshot(track_id="other", title="Found"))
    provider.get_lyrics(snapshot(track_id="another", title="Missing"))
    with pytest.raises(lp.LyricsError):
        provider.get_lyrics(snapshot(track_id="third", title="Broken"))
    provider.get_lyrics(snapshot(track_kind="media"))  # DJ narration
    provider.get_lyrics(snapshot())  # the synced track itself

    assert path.exists()
    assert path.read_text(encoding="utf-8") == lrc


def test_only_save_user_sync_ever_writes_to_the_directory():
    """One writer, one entry point. A second place writing user-sync files
    would be a second place that could truncate one.

    artwork.py writes too — derived cover colours — and is allowed here
    only because it is held to something stronger below: it must not so
    much as name the user-sync directory. Any module beyond these two
    that learns to write is a new way to lose a sync, and has to argue
    for itself here first.
    """
    source = (PACKAGE_DIR / "lyrics_provider.py").read_text(encoding="utf-8")
    body = source.split("def save_user_sync", 1)
    assert len(body) == 2, "save_user_sync is gone or renamed"
    writes = {
        path.name
        for path in source_files()
        if "write_text" in path.read_text(encoding="utf-8")
    }
    assert writes == {"lyrics_provider.py", "artwork.py"}
    assert source.count("write_text") == 2  # the cache entry, and this one


def test_the_artwork_cache_cannot_reach_the_user_sync_directory():
    """The cover-colour cache is cache: its own directory, its own name,
    and no knowledge that .user_syncs/ exists. Clearing either of the two
    caches must stay a safe reset."""
    from lyrisync import artwork

    source = (PACKAGE_DIR / "artwork.py").read_text(encoding="utf-8")
    assert "user_sync" not in source
    assert ".lrc" not in source
    assert artwork.DEFAULT_ARTWORK_CACHE_DIR != lp.DEFAULT_USER_SYNC_DIR
    assert artwork.DEFAULT_ARTWORK_CACHE_DIR != lp.DEFAULT_CACHE_DIR


def test_completing_a_resync_overwrites_the_previous_one(provider):
    """The one sanctioned way a stored sync ever changes."""
    provider.save_user_sync("track123", "[00:01.00] alpha\n[00:04.00] beta\n")
    path = provider.user_sync_path("track123")

    redone = "[00:02.50] alpha\n[00:06.75] beta\n"
    assert provider.save_user_sync("track123", redone) == path
    assert path.read_text(encoding="utf-8") == redone  # replaced, not appended
    assert provider.read_user_sync("track123").synced == [
        (2.5, "alpha"),
        (6.75, "beta"),
    ]
    assert len(list(provider.user_sync_dir.iterdir())) == 1  # no stray copies


def test_an_abandoned_resync_leaves_the_stored_sync_untouched(provider):
    """Only a completed pass reaches save_user_sync, so abandoning one
    cannot cost the user the sync they already had."""
    original = "[00:01.00] alpha\n[00:04.00] beta\n"
    provider.save_user_sync("track123", original)

    from lyrisync.sync_session import SyncSession
    from lyrisync.view_model import LyricsViewModel

    vm = LyricsViewModel()
    vm.track_changed(
        PlayerSnapshot(
            state=PlaybackState.PLAYING,
            track_id="track123",
            title="Song",
            artist="Artist",
        )
    )
    vm.fetch_completed("track123", provider.read_user_sync("track123"))
    assert vm.begin_sync() is True
    vm.sync_session.stamp(30.0)  # a partial, wrong pass
    assert isinstance(vm.sync_session, SyncSession)
    vm.end_sync()

    assert provider.user_sync_path("track123").read_text(encoding="utf-8") == original
    assert provider.read_user_sync("track123").synced == [(1.0, "alpha"), (4.0, "beta")]


def test_a_saved_sync_is_never_overwritten_by_a_fetch(provider, monkeypatch):
    lrc = "[00:01.00] Mine\n"
    provider.save_user_sync("track123", lrc)
    monkeypatch.setattr(
        lp,
        "_fetch_json",
        lambda url: {"syncedLyrics": "[00:09.00] Theirs\n", "plainLyrics": "Theirs"},
    )
    assert provider.get_lyrics(snapshot()).synced == [(1.0, "Mine")]
    assert provider.user_sync_path("track123").read_text(encoding="utf-8") == lrc
