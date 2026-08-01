# Architecture

## Three components that do not know about each other

```
player_monitor.py   follows Spotify, emits events       — knows nothing about the UI
player_events.py    Spotify's own "something changed"  — knows nothing about the UI
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

`paste_window.py` is the one other widget, and it is deliberately nothing
like the lyrics window: an ordinary system-drawn top-level with a text
box, opened only to bring lyrics in for a tap-to-sync pass and closed
again the moment it hands them over. It exists precisely because
`window.py` refuses focus and cannot hold a text field. See
[tap-to-sync](tap-to-sync.md).

## Pure modules behind a thin window

Everything that can be logic rather than widget is:

| module | what it owns |
|---|---|
| `view_model.py` | display state machine, stale-fetch guard, mode transitions |
| `sync.py`, `sync_session.py` | which line is current; a tap-to-sync pass |
| `transition.py` | which line change is in flight (the dedupe) |
| `loop.py` | line looping and echo-practice phases |
| `menu.py` | the whole settings menu: the entry tree, the labels, which are visible, and where a click lands |
| `geometry.py` | the type scale, minimum height (full and compact), button boxes, drag clamping, rect intersection, the dock position, the fitted width |
| `flight.py` | the journey to and from the menu bar item |
| `app_positions.py` | the per-app position map, the settling rule, the gates |
| `notifications.py` | the overlap and opacity rules for yielding to a banner — plus one native door |
| `proximity.py` | yielding to the pointer: the trigger region, the hysteresis, where a dodge goes, the ghost's opacity |
| `player_events.py` | observing Spotify's own announcement that something changed — plus one native door |
| `typography.py` | the type scale — imported by `geometry.py` |
| `appearance.py` | both palettes, the album tint maths, the high-contrast overrides |
| `gestures.py` | scroll and wheel routing |
| `romanize.py` | hangul detection and romanisation |
| `settings.py` | where the preferences live, and the one-time carry from the LyriSync name — plus one native door |
| `http_client.py` | connections to one host, kept alive between requests |
| `failure.py` | why a lookup could not be answered, in words |
| `backoff.py` | how long before this app asks LRCLIB again: the schedule that grows, and the pause LRCLIB itself asks for |
| `accessibility.py` | the three macOS display settings the window follows — plus one native door |
| `nsmenu.py` | *not* pure, and the only module here that is all door: the one native NSMenu drawn from `menu.py`, and the menu bar item that carries it |

None of them imports Qt. That is why the contrast floor, the type scale
and the state machine can all be tested on a Linux runner with no display,
and why `window.py` is wiring rather than logic.

`notifications.py`, `settings.py` and `accessibility.py` are the three
entries above that are not purely pure: their rules are, and one function
each — `_quartz()`, `_legacy_settings()` and `_workspace()` — is the
single door to the window list, to the preferences the old name left
behind, and to how the developer's Mac is configured. Same shape as
`frontmost._workspace()`, `hotkey._carbon()`,
`login_item._main_app_service()` and `nsmenu._appkit()`. Each of those
seven doors is shut by `tests/conftest.py`, so a test can never reach the
developer's keyboard, workspace, login items, windows, saved settings,
accessibility preferences or menu bar.

`nsmenu.py` is the odd one out and is listed with the pure modules on
purpose: it holds no rules at all. Everything the menu *is* lives in
`menu.py` and is asserted there; this only draws it. Its door opens on the
`NSStatusItem` as well as the `NSMenu`, because the item exists to carry
the menu, the two are created and released together, and a test that may
not put a menu on screen may certainly not leave an icon in somebody's
menu bar.

`frontmost` and `accessibility` both stand on NSWorkspace and still have
separate doors: one is an opt-in layer that unsubscribes when it is
switched off, the other is a system setting followed for as long as the
app runs, and one door could not be blocked without blocking the other.

## Threads

- **UI thread** — Qt, and only Qt. It never blocks on Spotify. Spotify's
  announcement is delivered here, and all it does is set a flag the
  monitor's thread reads.
- **Monitor thread** (`QThread`) — ticks every ~300 ms, emitting a
  position each time by queued connection, and actually asks Spotify
  about once a second or whenever it is told to.
- **Worker pool** (`QThreadPool`) — one-shot tasks: lyrics fetch, artwork
  fetch, `say`, seek, pause/resume. The last three send Apple events,
  serialised against the monitor's behind one lock. The album warm runs
  here too and is the one worker that lasts: it makes a request per track
  with a deliberate gap between them, so it waits on a stop flag rather
  than sleeping, and shutdown sets that flag before it drains the pool.

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
