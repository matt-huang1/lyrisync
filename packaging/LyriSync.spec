# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for LyriSync.app.

Built with PyInstaller rather than py2app: PySide6 and pyobjc are both
covered by hooks PyInstaller maintains upstream (Qt plugins, the platform
plugin, the pyobjc framework tangle), where py2app's PySide6 story still
needs hand-written recipes to get the same bundle. One tool, one command,
and ad-hoc signing built into the same run.

The version is never written here. A bundle that claims a different
version from the package inside it is a bug nobody notices until someone
reports one, so both Info.plist keys come from the one place that knows
what is actually being frozen.
"""

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version as installed_version
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

PROJECT = Path(SPECPATH).parent
PACKAGING = PROJECT / "packaging"

# The INSTALLED distribution's version, not pyproject.toml's — because the
# installed package is what PyInstaller freezes into the bundle. Reading
# the file instead would describe the source tree, which is the same thing
# right up until it is not.
try:
    VERSION = installed_version("lyrisync")
except PackageNotFoundError:  # pragma: no cover - build-time only
    raise SystemExit(
        "lyrisync is not installed in this environment, so there is no "
        "version to stamp the bundle with. Run: pip install -e '.[build]'"
    )

# And the one way that can be wrong: an editable install's metadata is a
# snapshot taken at install time, so editing pyproject.toml without
# reinstalling leaves it stale. That failure is invisible in the finished
# app — the bundle simply claims the old version — so the build refuses
# rather than shipping a bundle that misstates itself.
with open(PROJECT / "pyproject.toml", "rb") as handle:
    DECLARED = tomllib.load(handle)["project"]["version"]

if VERSION != DECLARED:
    raise SystemExit(
        f"version drift: pyproject.toml declares {DECLARED}, the installed "
        f"lyrisync is {VERSION}. The bundle would claim {VERSION}. "
        "Reinstall first: pip install -e '.[build]'"
    )

# The plist key that decides whether the AppleScript calls work at all.
# macOS shows this sentence in the Automation prompt on first poll, and an
# app with no such string is refused silently — the app comes up, the menu
# bar icon appears, and every poll fails with no visible reason.
APPLE_EVENTS_REASON = (
    "LyriSync reads the Spotify app's current track and playback position "
    "so it can show the lyrics in time with the music. It does not control "
    "playback unless you use its loop, spoken-reference, or tap-to-sync "
    "features."
)

analysis = Analysis(
    [str(PACKAGING / "lyrisync_launcher.py")],
    pathex=[str(PROJECT / "src")],
    binaries=[],
    # No data files. The menu bar glyphs used to be three SVGs in
    # lyrisync/assets/ and are drawn by symbols.py from menubar.py's geometry
    # since milestone 15.1 — brightness, shape and dot compose into eight
    # combinations and twenty with the optional animation, which is more
    # images than anybody should ship.
    #
    # copy_metadata is load-bearing, not housekeeping: lyrisync/__init__.py
    # asks importlib.metadata for its own version, and PyInstaller freezes
    # the package WITHOUT its .dist-info unless told otherwise (verified —
    # there was none in the bundle). Without this the app still runs, but
    # falls back to "unknown" and introduces itself to LRCLIB as a version
    # it is not.
    datas=[
        *copy_metadata("lyrisync"),
    ],
    # AppKit and objc are imported inside functions (every native feature is
    # guarded), and korean_romanizer is reached through a lazy import too.
    hiddenimports=[
        "AppKit",
        "Foundation",
        "objc",
        "ServiceManagement",  # Open at Login, imported inside a guard
        "korean_romanizer",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt ships far more than a lyrics window needs, and each of these pulls
    # in tens of megabytes. Excluded deliberately, with the bundle launched
    # and exercised afterwards to prove nothing here was load-bearing.
    excludes=[
        "tkinter",
        "unittest",
        "pydoc_data",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuick3D",
        "PySide6.QtQuickWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth",
        "PySide6.QtPositioning",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio",
        "PySide6.QtTextToSpeech",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtScxml",
        "PySide6.QtStateMachine",
        "PySide6.QtNfc",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
        "PySide6.QtHttpServer",
        "PySide6.QtUiTools",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="LyriSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no terminal window, ever
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # ad-hoc signing happens in make_app.sh
    entitlements_file=None,
    icon=str(PACKAGING / "LyriSync.icns"),
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LyriSync",
)

app = BUNDLE(
    collection,
    name="LyriSync.app",
    icon=str(PACKAGING / "LyriSync.icns"),
    # The identifier QSettings("lyrisync", "lyrisync") already resolves to:
    # ~/Library/Preferences/com.lyrisync.lyrisync.plist. Matching it is what
    # carries every existing setting — window position, size, opacity, the
    # learning toggles — into the bundled app instead of starting it fresh.
    bundle_identifier="com.lyrisync.lyrisync",
    version=VERSION,
    info_plist={
        "CFBundleName": "LyriSync",
        "CFBundleDisplayName": "LyriSync",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        # An accessory app, declared rather than only applied at runtime:
        # apply_accessory_policy() still runs, but LSUIElement is what stops
        # macOS putting an icon in the Dock for the instant before it does.
        "LSUIElement": True,
        "NSAppleEventsUsageDescription": APPLE_EVENTS_REASON,
        "NSHighResolutionCapable": True,
        # SF Symbols (the speak button) arrived in Big Sur; the vibrancy
        # material and the Spaces behaviour are older than that.
        "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.music",
    },
)
