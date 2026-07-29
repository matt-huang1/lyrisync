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
	@echo "make clean  remove build/, dist/ and the generated icon"

app:
	PYTHON=$(PYTHON) packaging/make_app.sh

icon:
	$(PYTHON) packaging/make_icon.py

test:
	$(PYTHON) -m pytest -q

clean:
	rm -rf build packaging/LyriSync.icns
	mkdir -p dist && find dist -mindepth 1 -delete
