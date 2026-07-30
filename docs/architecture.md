# Architecture

## Three components that do not know about each other

```
player_monitor.py   polls Spotify, emits events        — knows nothing about the UI
lyrics_provider.py  user syncs, cache, LRCLIB          — knows nothing about the UI
window.py           PySide6 widget, wiring, natives    — knows about both
```

The monitor and the provider are importable, runnable and testable with no
Qt at all — which is what the two terminal tools (`sottovoce-monitor`,
`sottovoce-lyrics`) are, and how the whole lyrics path was built before
there was a window.

(`artwork.py` sits beside the provider and is the one exception: it
decodes covers with `QImage` rather than adding an image library, because
Qt already ships with the app. Its colour maths is still pure.)

## Pure modules behind a thin window

Everything that can be logic rather than widget is:

| module | what it owns |
|---|---|
| `view_model.py` | display state machine, stale-fetch guard, mode transitions |
| `sync.py`, `sync_session.py` | which line is current; a tap-to-sync pass |
| `transition.py` | which line change is in flight (the dedupe) |
| `loop.py` | line looping and echo-practice phases |
| `menu.py` | which entries are visible, and their labels |
| `geometry.py` | minimum height (full and compact), button boxes, drag clamping, rect intersection, the dock position |
| `flight.py` | the journey to and from the menu bar item |
| `app_positions.py` | the per-app position map, the settling rule, the gates |
| `notifications.py` | the overlap and opacity rules for yielding — plus one native door |
| `typography.py` | the type scale — imported by `geometry.py` |
| `appearance.py` | both palettes, the album tint maths, the high-contrast overrides |
| `gestures.py` | scroll and wheel routing |
| `romanize.py` | hangul detection and romanisation |
| `settings.py` | where the preferences live, and the one-time carry from the LyriSync name — plus one native door |
| `http_client.py` | connections to one host, kept alive between requests |
| `failure.py` | why a lookup could not be answered, in words |
| `accessibility.py` | the three macOS display settings the window follows — plus one native door |

None of them imports Qt. That is why the contrast floor, the type scale
and the state machine can all be tested on a Linux runner with no display,
and why `window.py` is wiring rather than logic.

`notifications.py`, `settings.py` and `accessibility.py` are the three
entries above that are not purely pure: their rules are, and one function
each — `_quartz()`, `_legacy_settings()` and `_workspace()` — is the
single door to the window list, to the preferences the old name left
behind, and to how the developer's Mac is configured. Same shape as
`frontmost._workspace()`, `hotkey._carbon()` and
`login_item._main_app_service()`. Each of those six doors is shut by
`tests/conftest.py`, so a test can never reach the developer's keyboard,
workspace, login items, windows, saved settings or accessibility
preferences.

`frontmost` and `accessibility` both stand on NSWorkspace and still have
separate doors: one is an opt-in layer that unsubscribes when it is
switched off, the other is a system setting followed for as long as the
app runs, and one door could not be blocked without blocking the other.

## Threads

- **UI thread** — Qt, and only Qt. It never runs a subprocess, and never
  blocks on one.
- **Monitor thread** (`QThread`) — polls Spotify every ~300 ms and emits
  signals delivered by queued connection.
- **Worker pool** (`QThreadPool`) — one-shot tasks: lyrics fetch, artwork
  fetch, `say`, seek, pause/resume.

Shutdown drains them in a fixed order — hotkey, then monitor thread, then
pool — with bounded waits. See
[the menu and system integration](menu-and-system-integration.md#shutdown).

## Where the rest is written down

- [Spotify integration and polling](spotify-integration.md)
- [Lyrics sources and caching](lyrics-and-caching.md)
- [Tap-to-sync](tap-to-sync.md)
- [Contrast and accessibility](contrast-and-accessibility.md)
- [Appearance, materials and window behaviour](appearance-and-materials.md)
- [Album colour](album-colour.md)
- [Motion and typography](motion-and-typography.md)
- [The global hotkey, and why it is Carbon](hotkey-and-carbon.md)
- [The learning layers](learning-features.md)
- [Testing, and the guards that make it safe](testing-and-ci.md)
- [Packaging](packaging.md)
