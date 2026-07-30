# Testing, and the guards that make it safe

1143 tests, run on every push. The interesting part is not the count — it
is that the suite is allowed nowhere near anything real.

## The rule

> **A test touches nothing real.** Not the network, not the developer's
> Spotify, not their saved settings, not the lyrics cache, not the syncs
> they tapped out by hand.

This is not politeness. Running the suite used to restart whatever the
developer was listening to, because entering a tap-to-sync pass dispatches
a seek-to-0 and a resume, and `osascript` will happily launch Spotify to
do it.

## Seams first, guards second

Every dangerous thing has a **seam**: an injected settings object, an
injected cache directory, a stubbed task. The seams are the fix.

What lives in `tests/conftest.py` is the **alarm** that goes off when a
seam is missed, because four escapes were found by their symptoms rather
than by the suite: real `QSettings` writes, real player commands, a tray
test that never actually ran, and a live LRCLIB fetch that aborted CI
mid-handshake.

Ten doors are shut for the whole session:

| guard | catches |
|---|---|
| outbound sockets (at `socket`, not `urllib`) | any lyrics or artwork fetch, however it is made |
| `subprocess.run` / `Popen` | `osascript` to Spotify, `say` to the speakers |
| a `LyricsProvider` on its default directories | anything that would read or write the real caches — `.user_syncs/` included |
| an `ArtworkProvider` on its default directory | the real `.artwork_cache/`, and the CDN a miss would reach for |
| `QSettings("sottovoce", "sottovoce")` | the real `~/Library/Preferences` plist |
| `login_item._main_app_service()` | leaving a real login item on the developer's Mac |
| `hotkey._carbon()` | claiming ⇧⌘J from whoever is running the suite |
| `frontmost._workspace()` | observing the developer's own app switching |
| `notifications._quartz()` | reading every window open on the machine |
| `accessibility._workspace()` | how the developer's Mac is set up, and an observer left sitting on it |
| `settings._legacy_settings()` | the plist the LyriSync name left behind |

The last six are the same shape and each module has exactly **one** native
door for that reason. Three of them would outlive the test that started
them and keep calling into a window that has since been destroyed; the
window list reads which apps are running and where their windows are,
which is both none of the suite's business and a result that would depend
on what the developer happens to have open; the accessibility door is the
same hazard twice over — a test that read Reduce Motion would pass or fail
depending on how the developer has System Settings configured, and the
observer it registers would sit on the workspace repainting a destroyed
window. The last is a **read** of the user's old preferences, which is the
kind of escape that leaves nothing behind to notice afterwards — the
migration takes a factory so every test of it supplies its own file.

`frontmost` and `accessibility` both stand on NSWorkspace and have
separate doors anyway: two capabilities with two lifetimes, and one door
could not be blocked without blocking the other.

Three of those doors have structural tests as well as behavioural ones —
the native imports must appear in exactly one place, inside the door —
because a second import site would pass every behavioural test while
quietly reopening it.

Loopback sockets are allowed — Qt and pytest use them internally.

## A guard must record, not merely raise

This is the subtle half. Every escape happens on a worker thread, and the
app catches broad exceptions there *by design* — a failed fetch is a retry
state, not a crash. So an exception alone is swallowed exactly where it
matters most.

Each block is therefore **recorded** as well as raised, and an autouse
fixture fails the test that caused it — whether the escape happened on the
test's own thread, inside a `QRunnable`, or in a `QThread` that outlived
the call.

The guards have tests of their own. An unrun guard is the tray test all
over again.

## Pure modules carry the logic

Display state, loop and echo state, tap-to-sync sessions, menu gating,
gesture routing, the type scale, geometry, the colour palettes and the
contrast maths, the line-change dedupe — all of it lives in Qt-free
modules and is tested without a display.

What cannot be made pure — signal wiring, `QSettings` round-trips, the
tray, shutdown — is tested against a real Qt object tree on the
`offscreen` platform.

## No test is macOS-only

Everything native is guarded off-Cocoa in the code under test and asserted
structurally, so the whole suite runs headless on Linux. That is enforced:
CI fails if **any** test is skipped, because a skip means a module failed
to import and quietly opted out rather than that the runner is different.

CI has to `apt-get` PySide6's system libraries (`libegl1`, `libgl1`,
`libxkbcommon-x11-0`, …). The wheel imports fine without them and only
fails at *load* with "libEGL.so.1: cannot open shared object file" — which
is an `ImportError` but **not** a `ModuleNotFoundError`, so
`pytest.importorskip` does not catch it by default. The workflow also
asserts Qt actually starts headless before running anything, so a broken
runner fails loudly instead of skipping quietly.

## Things the suite learned the hard way

- **A test may not patch `window._pool`.** It is
  `QThreadPool.globalInstance()`, a process-wide singleton, so assigning
  to its `start()` leaks into every test that runs afterwards — it broke
  three shutdown tests two files later. Record what a window starts with a
  recording subclass of the task instead.
- **A `QThread` destroyed while running is a `qFatal`.** The process
  aborts and names whichever test was on screen, which is how a shutdown
  bug came to look like a bug in the quit test. The Qt fixture forces any
  surviving thread down, so a regression fails with a sentence instead of
  a signal.
- **`grab()` does not apply a `QGraphicsEffect`.** Anything animated by an
  effect has to be verified by tracing the effect's own `draw()`.
- **Screenshots are the readback for anything the compositor draws.** A
  healthy pyobjc handle does not prove a blur happened; only pixels do.

## Bundle building stays out of CI

The artefact is macOS-only, and the things worth checking about it — menu
bar icon, no Dock icon, the Automation prompt — can only be accepted by a
person. A green tick would be claiming more than it checked.

## No em dash reaches a person

Every string the package builds that is not a docstring is scanned, and
none of them may contain an em dash. The rule and its three replacements
are in [CLAUDE.md](../CLAUDE.md#the-window); what matters here is *how* it
is checked.

It is scanned as **syntax**, not as text. The file explaining the rule
contains em dashes, and so do dozens of module docstrings, which are
documentation and are allowed to read like prose. A substring scan over
the files could only ever be satisfied by deleting the explanations —
which is the same trap `test_notifications.py` documents for
`kCGWindowName` and `test_packaging.py` for the version literal.

Docstrings are identified **by position** — the first statement of a
module, class or function — rather than by content, so the same text
appearing somewhere it can reach a person is still caught. f-strings need
no special handling: their literal parts are `Constant` nodes inside a
`JoinedStr`.

The guard has tests of its own, as every guard here does.
