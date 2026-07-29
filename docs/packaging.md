# Packaging the app bundle

```sh
make app     # dist/LyriSync.app, ad-hoc signed
make icon    # regenerate the icon from packaging/appicon.svg
make clean   # remove build/, dist/ and the generated icon
```

`make app` is the whole build: render the icon, freeze with PyInstaller
(`packaging/LyriSync.spec`), ad-hoc sign the finished bundle. It needs
`pip install -e ".[build]"` and nothing else — no certificate, no
keychain, no Xcode.

## PyInstaller, not py2app

PySide6 and pyobjc are both covered by hooks PyInstaller maintains
upstream. py2app needs hand-written recipes for the same bundle.

## The bundle identifier is the settings contract

`com.lyrisync.lyrisync` is exactly what `QSettings("lyrisync",
"lyrisync")` already resolves to, so the bundled app and a terminal run
share one plist and one set of remembered window geometry.

Verified by launching both at once and reading the geometry back — same
position, same size, same file.

## Two `Info.plist` keys that are load-bearing

**`LSUIElement`.** The runtime `apply_accessory_policy()` call is what the
code guarantees; this key is what stops macOS showing a Dock icon for the
instant before it runs.

**`NSAppleEventsUsageDescription`.** Not paperwork. Without it macOS
refuses the AppleScript calls *silently*, and the app looks broken in the
most confusing way available — window up, menu bar item present, never
finds a song.

## Ad-hoc signing, and Gatekeeper

Ad-hoc signing is what lets the bundle run at all on Apple silicon, where
unsigned code is refused outright. It is **not** a Developer ID signature
and says nothing about who built it.

So a fresh copy needs **right-click → Open** once. There is no Apple
Developer certificate behind this app and it has not been notarised, so
macOS has nobody to check it against; right-click → Open is how you say
you vouch for it yourself. Every launch after that is a normal
double-click.

The first time it polls, macOS asks for **Automation** permission
("LyriSync wants to control Spotify"). Moving the app afterwards can make
macOS ask again, so put it where you want it before granting.

## Running from a checkout instead

```sh
.venv/bin/pip install -e .
.venv/bin/lyrisync
```

Which is what development uses. Both read and write the same settings, so
toggles and window geometry carry over between the two.

Two auxiliary terminal tools exist for debugging: `lyrisync-monitor` (raw
player events) and `lyrisync-lyrics` (synced lyrics in the terminal).

## Bundle building is deliberately not in CI

See [testing and CI](testing-and-ci.md): the artefact is macOS-only and
what matters about it can only be accepted by a person.
