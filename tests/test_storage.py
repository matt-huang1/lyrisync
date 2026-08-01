"""Where the app's files go, which is not the same question as what they
are called.

Everything about ``.user_syncs/`` was tested except the one thing that
decided where it was: the name was a constant, the writes had a seam, the
deletions had an AST guard, and the directory itself was
``Path(".user_syncs")`` — resolved against whatever directory the process
happened to be started in. A checkout starts in the checkout. macOS
starts a bundle in ``/``, which is read-only, so in the shipped app every
tap in a sync pass raised Errno 30 and nothing was written down at all.

No test could have caught it, and that is structural rather than
unlucky: tests/conftest.py REFUSES a provider built on its defaults, so
the suite is forbidden from touching the one value that was wrong. What
is checkable without touching a real directory is the value itself, and
these are the two properties it needed and did not have: it is absolute,
and it is the same answer from any working directory.
"""

TIER = "unit"  # Qt-free logic, called directly

import os
from pathlib import Path

import pytest

from sottovoce import artwork
from sottovoce import lyrics_provider as lp
from sottovoce import storage


# Every directory this app makes files in, and the constant each one is
# reached through. Asked of the modules rather than written out again:
# a fourth directory added later should join this list by being wired up,
# not by somebody remembering.
DIRECTORIES = {
    "lyrics cache": lp.DEFAULT_CACHE_DIR,
    "user syncs": lp.DEFAULT_USER_SYNC_DIR,
    "artwork cache": artwork.DEFAULT_ARTWORK_CACHE_DIR,
}


# -- where they go ----------------------------------------------------------


def test_every_directory_the_app_writes_to_is_absolute():
    """The defect in one line. A relative path is not a location: it is a
    location plus whoever launched the process."""
    for what, path in DIRECTORIES.items():
        assert path.is_absolute(), f"the {what} directory moves with the launcher"


def test_where_the_files_go_does_not_change_with_the_working_directory(tmp_path):
    """The same claim driven rather than read.

    Two working directories, the same three answers. Before the fix this
    failed on all three: ``Path(".user_syncs").resolve()`` is a different
    directory in each, which is exactly what the bundle ran into.
    """
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    here = Path.cwd()
    try:
        os.chdir(first)
        from_first = {name: path.resolve() for name, path in DIRECTORIES.items()}
        os.chdir(second)
        from_second = {name: path.resolve() for name, path in DIRECTORIES.items()}
    finally:
        os.chdir(here)
    assert from_first == from_second


def test_the_root_is_where_macos_keeps_an_app_s_own_files(tmp_path):
    """Taken as an argument rather than read, so this is the same function
    the app runs."""
    assert storage.data_root(tmp_path) == (
        tmp_path / "Library" / "Application Support" / "SottoVoce"
    )


def test_the_names_inside_are_the_names_every_page_already_uses():
    """The root moved; the names did not, and that is deliberate. A rename
    would falsify every sentence written about these directories, and the
    documented reset is one of those sentences."""
    root = storage.DATA_ROOT
    assert lp.DEFAULT_CACHE_DIR == root / ".lyrics_cache"
    assert lp.DEFAULT_USER_SYNC_DIR == root / ".user_syncs"
    assert artwork.DEFAULT_ARTWORK_CACHE_DIR == root / ".artwork_cache"


def test_the_three_directories_are_three_directories():
    """Distinct, and the user's work is not under either cache: clearing a
    cache is documented as a safe reset and must stay one."""
    assert len(set(DIRECTORIES.values())) == 3
    for cache in (lp.DEFAULT_CACHE_DIR, artwork.DEFAULT_ARTWORK_CACHE_DIR):
        assert cache not in lp.DEFAULT_USER_SYNC_DIR.parents


# -- the carry from where they used to go -----------------------------------


@pytest.fixture
def dirs(tmp_path):
    legacy, current = tmp_path / "checkout" / ".user_syncs", tmp_path / "app"
    legacy.mkdir(parents=True)
    return legacy, current


def test_hand_made_syncs_beside_a_checkout_are_carried_in(dirs):
    legacy, current = dirs
    (legacy / "track1.lrc").write_text("[00:01.00] mine\n", encoding="utf-8")
    (legacy / "track1.published").write_text("{}", encoding="utf-8")
    (legacy / "track2.partial").write_text("{}", encoding="utf-8")

    assert lp.carry_user_syncs(legacy, current) is storage.Carry.COPIED

    # The sidecars too: a sync that was published and a sync that covers
    # part of a song are facts about work somebody did, and a carry that
    # took the .lrc alone would offer to publish it all over again.
    assert {path.name for path in current.iterdir()} == {
        "track1.lrc",
        "track1.published",
        "track2.partial",
    }
    assert (current / "track1.lrc").read_text(encoding="utf-8") == "[00:01.00] mine\n"


def test_the_old_directory_is_left_exactly_as_it_was(dirs):
    """Copied, never moved. Two apps' worth of the same person's work is
    recoverable; one that was tidied away is not."""
    legacy, current = dirs
    (legacy / "track1.lrc").write_text("[00:01.00] mine\n", encoding="utf-8")

    lp.carry_user_syncs(legacy, current)

    assert (legacy / "track1.lrc").read_text(encoding="utf-8") == "[00:01.00] mine\n"


def test_a_sync_already_in_the_new_place_is_never_overwritten(dirs):
    """Both directories hold work somebody tapped out by hand. Choosing
    between two versions of one song is not a launch's decision."""
    legacy, current = dirs
    current.mkdir(parents=True)
    (legacy / "track1.lrc").write_text("[00:01.00] the old one\n", encoding="utf-8")
    (current / "track1.lrc").write_text("[00:02.00] the newer one\n", encoding="utf-8")
    (legacy / "track2.lrc").write_text("[00:03.00] only over there\n", encoding="utf-8")

    assert lp.carry_user_syncs(legacy, current) is storage.Carry.COPIED

    assert (current / "track1.lrc").read_text(encoding="utf-8") == (
        "[00:02.00] the newer one\n"
    )
    assert (current / "track2.lrc").exists()


def test_a_second_launch_carries_nothing_and_says_so(dirs):
    legacy, current = dirs
    (legacy / "track1.lrc").write_text("[00:01.00] mine\n", encoding="utf-8")

    assert lp.carry_user_syncs(legacy, current) is storage.Carry.COPIED
    assert lp.carry_user_syncs(legacy, current) is storage.Carry.ALREADY_CARRIED


def test_nothing_beside_the_working_directory_is_the_ordinary_case(tmp_path):
    """What the bundle finds, every time: its working directory is ``/``,
    and there was never a ``.user_syncs`` there to carry."""
    legacy, current = tmp_path / "nothing here", tmp_path / "app"
    assert lp.carry_user_syncs(legacy, current) is storage.Carry.NOTHING_TO_CARRY
    assert not current.exists(), "a carry with nothing to carry made a directory"


def test_running_the_app_from_its_own_directory_is_not_a_carry(tmp_path):
    """The one way the source and the destination can be the same
    directory, which would otherwise copy every file onto itself."""
    same = tmp_path / ".user_syncs"
    same.mkdir()
    assert lp.carry_user_syncs(same, same) is storage.Carry.SAME_DIRECTORY


def test_the_carry_says_which_of_the_four_things_it_did():
    """A refusal names itself, here as everywhere: the log line is the
    enum's own words rather than a sentence rebuilt from the directory
    afterwards, which could only ever describe what is there now."""
    assert {carry.name for carry in storage.Carry} == {
        "COPIED",
        "NOTHING_TO_CARRY",
        "ALREADY_CARRIED",
        "SAME_DIRECTORY",
    }
    for carry in storage.Carry:
        assert carry.value and carry.value[0].islower()
