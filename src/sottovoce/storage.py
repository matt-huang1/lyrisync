"""Where this app keeps the files it makes.

A directory NAME is not a location, and until this module existed the
location was whichever directory the app happened to be launched from:
``Path(".user_syncs")`` resolves against the process's working directory,
which is the checkout when the app is run from source and ``/`` when
macOS launches the bundle. ``/`` is read-only.

MEASURED, in the shipped 1.2.0 bundle, driving a real sync pass through
the menu bar item and pressing the tap bar with real events: every press
raised ``OSError: [Errno 30] Read-only file system: '.user_syncs'`` from
the journal write, four times out of four. The stamps were landing (the
menu read "Save the 4 lines timed so far") and none of them was being
written down, which is the one promise a pass makes. The lyrics cache
could not be written either, so every song was fetched from LRCLIB again
from scratch, and a finished pass would have had nowhere to save its
`.lrc`. From the user's chair it read as a tap bar that did nothing.

So the answer is absolute and it is the same answer whoever asks it. It
hangs off the home directory rather than off ``sys.executable`` or
``sys.frozen``, because a bundle and a checkout belong to the same person
and a build that kept files of its own would be a second copy of their
work: the same reasoning that makes the bundle identifier and
``QSettings("sottovoce", "sottovoce")`` one string rather than two.

The names inside are the names they always were, so every sentence
written about them stays true and clearing the cache is still the reset
it was documented to be. What changed is the one thing that was never
stated anywhere: which directory those names are under.
"""

from __future__ import annotations

TIER = "unit"  # Qt-free logic, called directly

from enum import Enum
from pathlib import Path

# The folder a person would look in, named the way macOS names them. Not
# the bundle identifier: this one is for opening in Finder, and the
# identifier already has a job (the preferences file, which macOS keys on
# it and nobody browses).
APP_DIR_NAME = "SottoVoce"

# Unchanged, and deliberately: a rename here would falsify every page that
# tells somebody what these directories are.
CACHE_DIR_NAME = ".lyrics_cache"
ARTWORK_CACHE_DIR_NAME = ".artwork_cache"
USER_SYNC_DIR_NAME = ".user_syncs"


def data_root(home: Path) -> Path:
    """The one directory everything this app writes lives under.

    Takes the home directory rather than reading it, so what the app does
    on a real Mac and what a test drives are the same function.
    """
    return Path(home) / "Library" / "Application Support" / APP_DIR_NAME


DATA_ROOT = data_root(Path.home())
CACHE_DIR = DATA_ROOT / CACHE_DIR_NAME
ARTWORK_CACHE_DIR = DATA_ROOT / ARTWORK_CACHE_DIR_NAME
USER_SYNC_DIR = DATA_ROOT / USER_SYNC_DIR_NAME

# Where a run from a checkout used to put them: beside whatever directory
# it was started in. Relative, which is the whole of the bug, and kept
# here because it is also where somebody's existing syncs are sitting.
LEGACY_USER_SYNC_DIR = Path(USER_SYNC_DIR_NAME)

# The carry from that old place into this one lives in lyrics_provider,
# which is the module that owns the directory: a second module learning to
# put files in there would be a second module that could take one out, and
# tests/test_user_sync_safety.py is where that has to be argued. What
# comes back from it is named here, beside the paths it is about.


class Carry(Enum):
    """What the one-time carry did, in words a log line can use.

    Named rather than reconstructed by looking at the directory
    afterwards, like the settings migration and every other gate in this
    app: a directory can only ever say what is in it now, never what this
    launch did.
    """

    COPIED = "carried hand-made syncs in from the working directory"
    NOTHING_TO_CARRY = "no syncs beside the working directory"
    ALREADY_CARRIED = "the syncs beside the working directory are already in"
    SAME_DIRECTORY = "the working directory is the app's own"
