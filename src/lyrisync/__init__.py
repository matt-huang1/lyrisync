"""LyriSync.

The version lives here and nowhere else. Every copy of a version number
is a copy that stays right for one release: the bundle's Info.plist takes
it from the installed distribution's metadata, and so does this, so the
app, the bundle and the User-Agent LRCLIB sees cannot disagree.
"""

from importlib.metadata import PackageNotFoundError, version as _installed_version

from lyrisync.player_monitor import (
    PlaybackState,
    PlayerMonitor,
    PlayerSnapshot,
    SpotifyQueryError,
)

try:
    __version__ = _installed_version("lyrisync")
except PackageNotFoundError:
    # Running from a source tree that was never installed. Deliberately
    # not a number: a fallback version literal is exactly the drift this
    # module exists to remove, and a wrong version is worse than an
    # absent one. The frozen bundle does not take this path — the spec
    # copies the distribution metadata in so it can answer for real.
    __version__ = "unknown"

# One outbound identity, built from that one version. LRCLIB asks callers
# to identify themselves; the same string goes to whoever is hosting the
# album art. The URL is carried over unchanged from the two constants this
# replaces — only the version was in question here.
USER_AGENT = f"lyrisync/{__version__} (https://github.com/matthewhuang/lyrisync)"

__all__ = [
    "PlaybackState",
    "PlayerMonitor",
    "PlayerSnapshot",
    "SpotifyQueryError",
    "USER_AGENT",
    "__version__",
]
