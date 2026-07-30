"""Where the preferences live, and the one-time carry from the old name.

macOS keys a QSettings file on the organisation and application it was
opened with — ``QSettings("sottovoce", "sottovoce")`` is
``~/Library/Preferences/com.sottovoce.sottovoce.plist``, and the bundle
identifier in the spec is the same string so a terminal run and the built
app share one file rather than each keeping its own.

This app used to be called LyriSync, and every one of those names changed
with it. Renaming does not MOVE a preferences file — it orphans it. The
user's window position, size, opacity and every toggle would still be
sitting in ``com.lyrisync.lyrisync.plist``, intact and never read again,
and the app would open at its first-run defaults as though it had never
been used.

So the old file is copied across, once, on a launch that finds nothing of
its own. Copied and not moved: the old plist is left exactly where it is,
because deleting a user's settings to save a rename is the kind of tidying
this project does not do — the same instinct that keeps ``.user_syncs/``
out of every clean-up path. It costs a few kilobytes and it means a bad
copy is recoverable.

Two things this cannot carry, and no code could: macOS keys the Automation
grant and the login item registration on the identifier AND the code
signature, so both belong to the old app. macOS asks for Automation again
on the first poll, and Open at Login has to be switched on again.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# The pair every QSettings in this app is opened with. It resolves to
# com.sottovoce.sottovoce, which is what the bundle declares as its
# identifier — a test pins the two together, because the whole point of
# matching them is that the built app and a checkout read one file.
ORGANISATION = "sottovoce"
APPLICATION = "sottovoce"

# What it was called until the rename.
LEGACY_ORGANISATION = "lyrisync"
LEGACY_APPLICATION = "lyrisync"

# Written once, so the copy cannot happen twice. Its absence is not "this
# is a fresh install" — it is "nobody has asked the old file yet".
MIGRATION_KEY = "migration/from_lyrisync"

# Every group this app has ever written a key under. It needs them named
# because on macOS ``QSettings.allKeys()`` does NOT answer for this app
# alone: NSUserDefaults resolves through a search list, so a plist that
# has never been written still reports ~70 keys from NSGlobalDomain
# (AppleLocale, the trackpad gestures, half of System Settings).
#
# Measured, and it corrected the code twice over: a brand new
# com.sottovoce.sottovoce answers 70 keys, so "are there settings here
# already" was true on every real Mac and the carry would never have run
# on the machine it was written for. The same fall-through would then
# have copied all 70 of NSGlobalDomain's keys INTO the app's own plist.
# Asking per group answers 11 and 3 on the old file and nothing at all on
# a new one, which is the question actually being asked.
OWNED_GROUPS = ("window", "lyrics", "migration")


class Migration(Enum):
    """What the one-time carry did, in words a log line can use.

    A refusal names itself here rather than being reconstructed by asking
    the settings object afterwards, for app_positions' reason: a
    reconstruction can disagree with what actually happened.
    """

    COPIED = "copied the settings left behind under the LyriSync name"
    NOTHING_TO_COPY = "no LyriSync settings to carry over"
    ALREADY_RUN = "the carry from LyriSync has already run"
    SETTINGS_OF_OUR_OWN = "there are settings under this name already"


def own_keys(settings: Any) -> list[str]:
    """The keys in ``settings`` that belong to this app.

    Not ``allKeys()``: see OWNED_GROUPS. Both halves of the migration ask
    this instead — the one that decides whether to run, and the one that
    decides what to carry — because both would otherwise be answered by
    the whole of NSGlobalDomain.
    """
    found: list[str] = []
    for group in OWNED_GROUPS:
        settings.beginGroup(group)
        found.extend(f"{group}/{key}" for key in settings.allKeys())
        settings.endGroup()
    return found


def refusal(settings: Any) -> Optional[Migration]:
    """Why the old file should NOT be copied in, or None if it should.

    Both refusals are answered from OUR file alone, before the old one is
    opened at all. That ordering is deliberate: the door onto the legacy
    preferences is the one piece of real user state this module can reach,
    and a run that has nothing to do there should not touch it.
    """
    if settings.value(MIGRATION_KEY, False, type=bool):
        return Migration.ALREADY_RUN
    if own_keys(settings):
        # Settings of our own, from a launch that already ran. Overwriting
        # them with an older app's would be undoing whatever the user has
        # done since, which is worse than leaving a rename half-carried.
        return Migration.SETTINGS_OF_OUR_OWN
    return None


def migrate(
    settings: Any, legacy_settings: Optional[Callable[[], Any]] = None
) -> Migration:
    """Carry the LyriSync preferences into ``settings``, at most once.

    ``legacy_settings`` opens the old file; it is a factory rather than an
    object so that a refusal never opens one, and so tests have a seam
    that does not involve the developer's own preferences.
    """
    refused = refusal(settings)
    if refused is not None:
        return refused

    source = (legacy_settings or _legacy_settings)()
    keys = own_keys(source)
    for key in keys:
        settings.setValue(key, source.value(key))
    # Recorded either way. "Asked and found nothing" is an answer, and
    # writing it down is what stops the old file being consulted on every
    # launch for the rest of the app's life.
    settings.setValue(MIGRATION_KEY, True)
    settings.sync()

    if not keys:
        return Migration.NOTHING_TO_COPY
    logger.info(
        "carried %d settings across from %s.%s (the old file is left alone)",
        len(keys),
        LEGACY_ORGANISATION,
        LEGACY_APPLICATION,
    )
    return Migration.COPIED


def _legacy_settings() -> Any:  # pragma: no cover - the real preferences file
    """The one door onto the old preferences file.

    Same shape as ``login_item._main_app_service`` and
    ``frontmost._workspace``: everything that can reach real user state
    goes through a single function, so the suite has one thing to block.
    Imported here rather than at module scope so this module stays
    importable without Qt, like every other pure module in the app.
    """
    from PySide6.QtCore import QSettings

    return QSettings(LEGACY_ORGANISATION, LEGACY_APPLICATION)
