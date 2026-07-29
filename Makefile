# LyriSync
#
# Development runs from the source tree (`.venv/bin/lyrisync`); the .app
# bundle is for using it like an app rather than a checkout. Bundle
# building is deliberately not in CI — it produces a macOS-only artefact
# that only a human can accept (menu bar icon, Dock absence, Automation
# prompt), so a green tick would be claiming more than it checked.

PYTHON ?= .venv/bin/python

.PHONY: app icon test clean help

help:
	@echo "make app    build dist/LyriSync.app (ad-hoc signed)"
	@echo "make icon   regenerate packaging/LyriSync.icns from appicon.svg"
	@echo "make test   run the test suite"
	@echo "make clean  remove build/ and the generated icon, empty dist/"

app:
	PYTHON=$(PYTHON) packaging/make_app.sh

icon:
	$(PYTHON) packaging/make_icon.py

test:
	$(PYTHON) -m pytest -q

# dist/ is emptied rather than removed, for the reason make_app.sh spells
# out: `rm -rf dist` races Finder recreating .DS_Store inside it and dies
# on the rmdir with "Directory not empty". A comment out here rather than
# in the recipe, or make echoes it at the shell.
clean:
	rm -rf build packaging/LyriSync.iconset packaging/LyriSync.icns
	mkdir -p dist
	find dist -mindepth 1 -delete
