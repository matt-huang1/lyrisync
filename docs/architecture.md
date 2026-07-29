# Architecture

## Three components that do not know about each other

```
player_monitor.py   polls Spotify, emits events        — knows nothing about the UI
lyrics_provider.py  user syncs, cache, LRCLIB          — knows nothing about the UI
window.py           PySide6 widget, wiring, natives    — knows about both
```

The monitor and the provider are importable, runnable and testable with no
Qt at all — which is what the two terminal tools (`lyrisync-monitor`,
`lyrisync-lyrics`) are, and how the whole lyrics path was built before
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
| `geometry.py` | minimum height, button boxes, drag clamping |
| `typography.py` | the type scale — imported by `geometry.py` |
| `appearance.py` | both palettes, the album tint maths |
| `gestures.py` | scroll and wheel routing |
| `romanize.py` | hangul detection and romanisation |

None of them imports Qt. That is why the contrast floor, the type scale
and the state machine can all be tested on a Linux runner with no display,
and why `window.py` is wiring rather than logic.

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
