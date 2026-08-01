"""The one-time carry of the user's preferences across the rename.

The app used to be LyriSync, and macOS keys a preferences file on the
bundle identifier. Renaming does not move that file, it orphans it: the
window position, the size, the opacity and every toggle would still be
sitting in com.lyrisync.lyrisync.plist, intact and never read again.

Every test here hands ``migrate`` a legacy factory of its own. The real
door is shut by the guard in conftest, which has a test of its own — an
unrun guard is the tray test again.
"""

TIER = "qt"  # QSettings round trips, on ini files of their own

import pytest

# Two ini files stand in for the two plists. QSettings is the same class
# either way; only the location differs, which is the whole reason the
# location is injected.
pytest.importorskip(
    "PySide6.QtCore",
    reason="PySide6 unusable (missing system Qt libraries?)",
    exc_type=ImportError,
)

from PySide6.QtCore import QPoint, QSettings, QSize  # noqa: E402

from sottovoce.settings import (  # noqa: E402
    MIGRATION_KEY,
    Migration,
    migrate,
    refusal,
)


def ini(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


@pytest.fixture
def old(tmp_path):
    """A settings file with a used app's worth of state in it."""
    settings = ini(tmp_path / "com.lyrisync.lyrisync.ini")
    settings.setValue("window/pos", QPoint(1240, 40))
    settings.setValue("window/size", QSize(460, 200))
    settings.setValue("window/opacity", 0.85)
    settings.setValue("window/album_colour", True)
    settings.setValue("window/app_positions", '[["com.apple.Safari",10,20,"Safari"]]')
    settings.sync()
    return settings


@pytest.fixture
def new(tmp_path):
    return ini(tmp_path / "com.sottovoce.sottovoce.ini")


def test_a_first_launch_carries_every_value_across(new, old):
    assert migrate(new, lambda: old) is Migration.COPIED

    assert new.value("window/pos") == QPoint(1240, 40)
    assert new.value("window/size") == QSize(460, 200)
    assert float(new.value("window/opacity")) == pytest.approx(0.85)
    assert new.value("window/album_colour", type=bool) is True
    assert new.value("window/app_positions") == (
        '[["com.apple.Safari",10,20,"Safari"]]'
    )


def test_the_types_survive_the_crossing(new, old):
    """The position and size are QPoint and QSize, not strings. Restoring
    checks the type before using either — a settings file that answered
    with text would leave the window at its default shape and read as the
    migration having done nothing."""
    migrate(new, lambda: old)

    assert isinstance(new.value("window/pos"), QPoint)
    assert isinstance(new.value("window/size"), QSize)


def test_the_old_file_is_left_exactly_where_it_was(new, old, tmp_path):
    """Copied, not moved. Deleting a user's settings to tidy up after a
    rename is the tidying this project does not do — the same instinct
    that keeps .user_syncs/ out of every clean-up path — and it is what
    makes a bad copy recoverable."""
    migrate(new, lambda: old)

    assert old.value("window/pos") == QPoint(1240, 40)
    assert (tmp_path / "com.lyrisync.lyrisync.ini").exists()


def test_it_happens_once_and_records_that_it_did(new, old):
    assert migrate(new, lambda: old) is Migration.COPIED
    assert new.value(MIGRATION_KEY, False, type=bool) is True

    # Whatever the user has done since is theirs. A second carry would put
    # the old values back over the top of it.
    new.setValue("window/opacity", 0.4)
    assert migrate(new, lambda: old) is Migration.ALREADY_RUN
    assert float(new.value("window/opacity")) == pytest.approx(0.4)


def test_a_refusal_never_opens_the_old_file(new, old):
    """Both refusals are answered from our own file, before the legacy one
    is touched. The door onto it is the only real user state this module
    can reach, so a run with nothing to do there does not knock."""

    def refuse():
        raise AssertionError("the legacy preferences were opened anyway")

    migrate(new, lambda: old)
    assert migrate(new, refuse) is Migration.ALREADY_RUN

    fresh_but_used = ini(new.fileName() + ".other")
    fresh_but_used.setValue("window/opacity", 1.0)
    assert migrate(fresh_but_used, refuse) is Migration.SETTINGS_OF_OUR_OWN


def test_settings_of_our_own_are_not_overwritten(new, old):
    """A launch that already has settings is not a first launch, whatever
    the marker says. This is the shape of a user who ran the renamed app
    before the migration existed: the sensible answer is to leave what
    they have alone rather than to reinstate an older app's."""
    new.setValue("window/opacity", 0.4)

    assert migrate(new, lambda: old) is Migration.SETTINGS_OF_OUR_OWN
    assert float(new.value("window/opacity")) == pytest.approx(0.4)
    assert new.value("window/pos") is None


def test_a_user_who_never_ran_lyrisync_is_answered_too(new, tmp_path):
    """"Asked and found nothing" is an answer, and writing it down is what
    stops the old file being consulted on every launch for the rest of the
    app's life."""
    empty = ini(tmp_path / "nothing-here.ini")

    assert migrate(new, lambda: empty) is Migration.NOTHING_TO_COPY
    assert new.value(MIGRATION_KEY, False, type=bool) is True
    assert migrate(new, lambda: empty) is Migration.ALREADY_RUN


def test_only_this_app_s_own_keys_cross(new, old):
    """The bug a real run found, in both directions.

    On macOS ``allKeys()`` does not answer for one app: NSUserDefaults
    resolves through a search list, so a plist that has never been written
    still reports ~70 keys from NSGlobalDomain. Measured on this machine —
    a brand new com.sottovoce.sottovoce answered 70 — which meant "there
    are settings here already" was true on every real Mac and the carry
    would never have run at all. Had it run, the same fall-through would
    have copied AppleLocale and the trackpad gestures into the app's own
    plist.

    An ini file has no search list, so the fall-through is standing in for
    here as a key that is simply not ours.
    """
    old.setValue("AppleLocale", "en_GB")
    old.sync()

    assert migrate(new, lambda: old) is Migration.COPIED
    assert new.value("window/pos") == QPoint(1240, 40)
    assert new.value("AppleLocale") is None


def test_a_plist_full_of_somebody_else_s_keys_is_still_a_first_launch(new, old):
    """The other half of the same bug: a settings object answering with
    keys that are not ours must not read as an app that has already been
    used, or the carry refuses itself on every Mac."""
    new.setValue("AppleLocale", "en_GB")

    assert refusal(new) is None
    assert migrate(new, lambda: old) is Migration.COPIED


def test_every_key_the_window_saves_is_one_the_migration_would_carry():
    """OWNED_GROUPS names the groups this app writes under, and a key
    written outside them would be carried by nothing. Read out of
    window.py rather than listed here, so adding a group and forgetting
    the migration is a red test rather than a setting that quietly does
    not survive the rename.
    """
    import ast
    from pathlib import Path

    from sottovoce.settings import OWNED_GROUPS

    source = Path(__file__).resolve().parent.parent / "src" / "sottovoce" / "window.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    saved = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setValue"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_settings"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert saved, "no settings keys found in window.py — has the seam moved?"

    orphans = sorted(k for k in saved if k.split("/")[0] not in OWNED_GROUPS)
    assert not orphans, (
        f"window.py saves {orphans}, which no group in OWNED_GROUPS covers — "
        "the migration would leave those behind"
    )


def test_the_refusal_names_itself(new, old):
    """The reason is returned rather than reconstructed afterwards by
    asking the settings object what state it is in — app_positions' rule,
    for its reason: a reconstruction can disagree with what happened."""
    assert refusal(new) is None

    migrate(new, lambda: old)
    assert refusal(new) is Migration.ALREADY_RUN
    assert Migration.ALREADY_RUN.value == "the carry from LyriSync has already run"
