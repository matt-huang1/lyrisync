#!/bin/bash
# Build SottoVoce.app. One command, from a clean checkout:
#
#   .venv/bin/pip install -e ".[build]"
#   make app
#
# Everything below is deterministic given that: the icon is rendered from
# packaging/appicon.svg, the bundle from packaging/SottoVoce.spec, and the
# signature is ad-hoc, so no certificate or keychain is involved.
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$PROJECT/.venv/bin/python}"
APP="$PROJECT/dist/SottoVoce.app"

if [ ! -x "$PYTHON" ]; then
  echo "no interpreter at $PYTHON — create .venv and pip install -e '.[build]'" >&2
  exit 1
fi
if ! "$PYTHON" -c "import PyInstaller" 2>/dev/null; then
  echo "PyInstaller missing — run: $PYTHON -m pip install -e '.[build]'" >&2
  exit 1
fi

echo "==> icon"
"$PYTHON" "$PROJECT/packaging/make_icon.py"

echo "==> bundle"
# Clean every time: a stale build/ directory is how a bundle ends up
# shipping a file that is no longer in the source tree.
rm -rf "$PROJECT/build"

# dist/ is EMPTIED, never removed. `rm -rf dist` unlinks the contents and
# then the directory, and Finder recreates .DS_Store inside it in that
# gap whenever the folder is open in a window — so the rmdir fails with
# "Directory not empty" and the whole build stops. Hit twice, both times
# with dist open on screen. Emptying has no such gap: a .DS_Store written
# a microsecond later lands in a directory we are keeping anyway, and
# PyInstaller overwrites what it needs to.
mkdir -p "$PROJECT/dist"
find "$PROJECT/dist" -mindepth 1 -delete
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$PROJECT/dist" \
  --workpath "$PROJECT/build" \
  "$PROJECT/packaging/SottoVoce.spec"

echo "==> ad-hoc signature"
# Unsigned code is refused outright on Apple silicon, and a bundle whose
# nested frameworks are signed piecemeal by PyInstaller can still fail its
# own seal. One --deep --force pass over the finished bundle settles it.
# This is NOT a developer-ID signature: there is no certificate here, so
# Gatekeeper still quarantines a downloaded copy and first launch needs
# right-click → Open. See the README.
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> built $APP ($(du -sh "$APP" | cut -f1))"
echo "    move it: mv \"$APP\" /Applications/"
# A bundle built here is not quarantined, so it just opens. The old line
# said "right-click → Open", which recent macOS no longer honours as a
# Gatekeeper override anyway — and which the README stopped saying.
echo "    it opens normally: a bundle you built is not marked as downloaded"
