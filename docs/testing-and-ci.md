# Testing, and the guards that make it safe

1622 tests, run on every push. The interesting part is not the count — it
is that the suite is allowed nowhere near anything real.

## The rule

> **A test touches nothing real.** Not the network, not the developer's
> Spotify, not their saved settings, not the lyrics cache, not the syncs
> they tapped out by hand.

This is not politeness. Running the suite used to restart whatever the
developer was listening to, because entering a tap-to-sync pass dispatches
a seek-to-0 and a resume, and an Apple event will happily start Spotify to
carry one out.

## Seams first, guards second

Every dangerous thing has a **seam**: an injected settings object, an
injected cache directory, a stubbed task. The seams are the fix.

What lives in `tests/conftest.py` is the **alarm** that goes off when a
seam is missed, because four escapes were found by their symptoms rather
than by the suite: real `QSettings` writes, real player commands, a tray
test that never actually ran, and a live LRCLIB fetch that aborted CI
mid-handshake.

Fourteen doors are shut for the whole session:

| guard | catches |
|---|---|
| outbound sockets (at `socket`, not `urllib`) | any lyrics or artwork fetch, however it is made |
| `subprocess.run` / `Popen` | `say` to the speakers |
| `player_monitor._cocoa()` | every question and every command to the developer's Spotify, and whether they have it open |
| `player_events._distributed_center()` | an observer left sitting on the distributed notification centre, waking on every track they play |
| a `LyricsProvider` on its default directories | anything that would read or write the real caches — `.user_syncs/` included |
| an `ArtworkProvider` on its default directory | the real `.artwork_cache/`, and the CDN a miss would reach for |
| `QSettings("sottovoce", "sottovoce")` | the real `~/Library/Preferences` plist |
| `login_item._main_app_service()` | leaving a real login item on the developer's Mac |
| `nsmenu._appkit()` | an icon in the developer's menu bar per window built, and a modal menu tracking loop in the middle of the run |
| `hotkey._carbon()` | claiming ⇧⌘J from whoever is running the suite |
| `frontmost._workspace()` | observing the developer's own app switching |
| `notifications._quartz()` | reading every window open on the machine |
| `accessibility._workspace()` | how the developer's Mac is set up, and an observer left sitting on it |
| `settings._legacy_settings()` | the plist the LyriSync name left behind |

The subprocess guard used to cover Spotify too, and that is worth writing
down rather than editing out: the app asked by launching `osascript`, so
one guard caught the network's neighbour and the player's commands
together. Milestone 19 moved the capability into this process, and the
alarm had to move with it — a guard that goes on passing after the thing
it guards has moved is worse than no guard, because it reads as coverage.

The last seven are the same shape and each module has exactly **one**
native door for that reason. Three of them would outlive the test that started
them and keep calling into a window that has since been destroyed; the menu
bar item is the loudest of the lot, since every window built here makes one
and a menu popped up from a test would block the run on a modal tracking
loop; the window list reads which apps are running and where their windows
are, which is both none of the suite's business and a result that would
depend on what the developer happens to have open; the accessibility door is the
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

## Answered, rather than blocked

A guard is a refusal, and a refusal is not always the branch worth taking.
Five of these doors are **answered with None** for every window
`tests/window/` builds — Carbon, both NSWorkspace doors, the distributed
notification centre, AppKit's menu — because handing back None is the
branch a machine without pyobjc takes, so the real `GlobalHotkey`, the
real `FrontmostWatcher`, the real `PlaybackAnnouncer` and the real
`NativeMenu` all run their own code and simply find nothing to claim,
observe or draw. Blocking them instead would fail every test in the
directory for constructing a window.

`test_window_menu_clicks.py` takes that one step further and answers
`nsmenu._appkit()` with a **stand-in for AppKit**: objects with the same
selectors that record what they were told instead of drawing it. Everything
above the door is then the real thing — the menu really is built, each item
really is armed with a tag, and a click really does arrive through the one
selector — which is what lets "a click on a native item" and "the state it
produced" be one test rather than two. The session guard stays armed for
anything that reaches around either.

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

## Three tiers, and every test is in exactly one

| tier | what it is | what a failure means | count |
|---|---|---|---|
| `unit` | one Qt-free module's own logic, its collaborators stubbed | that module is wrong | 1147 |
| `integration` | this app's real parts wired to each other, only the outside world faked, entered where a user's actions arrive | the parts disagree | 53 |
| `qt` | needs a Qt object to answer: the widget tree, a `QSettings` round trip, a painted image | the window is wrong | 422 |

```
pytest -m unit           # 6.8s, no display needed
pytest -m integration    # 1.2s
pytest -m qt             # 5.0s
pytest                   # all three, and they are the whole suite
```

**Exactly one**, so the three add up to the suite and each can be run
alone. A module says which tier it is in with a `TIER` line under its
docstring; a test that differs from the rest of its file carries a marker
of its own, and the marker wins. `tests/conftest.py` resolves the two and
**refuses a test in no tier** — a new file with no `TIER` line fails
collection rather than quietly running untiered.

The alternative was a module-level `pytestmark` plus per-test overrides,
and it does not work: a mark on the module cannot be taken back off one
item, so a test carrying both would be selected by `-m unit` *and* by
`-m qt`, and the arithmetic that says the tiers are the suite would stop
holding. `tests/test_suite_shape.py` asserts the rest — every module declares a
tier, no test claims two, and the markers registered in `pyproject.toml`
are exactly the ones conftest resolves.

The line between `unit` and `integration` is **how many of the app's own
parts are in the room**, not how much is faked. `tests/test_player_monitor.py`
drives the real `PlayerMonitor` against a fake Spotify and a fake clock
and is still `unit`: one module, its door stubbed. The same fake Spotify
under the real monitor *wired to a real window* is `integration`, because
what it can catch is the two of them disagreeing.

## Where the window tests live

`tests/window/` is one file per behaviour, `qt` unless the row says
otherwise:

| | |
|---|---|
| `conftest.py` | `make_window` (the settings seam) and `no_real_world` |
| `helpers.py` | Qt brought up once, and the helpers a second file needs |
| `test_window_seam.py` | that the injected settings file is the one written |
| `test_window_lyrics.py` | the title card, and why the lyrics are not here |
| `test_window_menu.py` | the menu model, the menu bar item, the glyph, open at login |
| `test_window_visibility.py` | show/hide, the flight, the hotkey, quit |
| `test_window_appearance.py` | palette, hairline, album tint, the three display settings |
| `test_window_motion.py` | the line change, and what it costs to draw |
| `test_window_player.py` | the announcement, and the loop against a real player |
| `test_window_positions.py` | per-app memory, the acknowledgement, each layout's shape |
| `test_window_notifications.py` | the yield, and the poll rate |
| `test_window_compact.py` | the strip's rows and its reveal |
| `test_window_press.py` | presses Qt routes, rather than presses aimed by hand — `integration` |
| `test_window_drag.py` | dragging and resizing, with Qt deciding which — `integration` |
| `test_window_fetch.py` | lyrics from the wire to the line on screen — `integration` |
| `test_window_menu_clicks.py` | a click on a native item, and the state it produces — `integration` |
| `test_window_docking.py` | docking, and the square-topped shape |
| `test_window_fit.py` | fitting the strip to the song, and its type size |
| `test_window_pointer_yield.py` | dodge, ghost, the suspensions |

It was one 7447-line file, which was a fifth of the suite. A helper lives
in `helpers.py` **only** when a second file needs it — anything one file
uses stays in that file, next to what it serves — and the fixtures are in
`conftest.py`, which is why nothing imports a conftest by name.

Splitting it found a test that had never run: two different tests were
called `test_the_setting_survives_a_restart`, one about the notification
yield and one about fitting, and the second definition had shadowed the
first for as long as they shared a module. Both run now, and both pass.
`tests/test_suite_shape.py` is where a duplicate would be caught next time.

## What is driven through the real entry point, and what is not

Worth knowing rather than worth fixing all at once. A `qt` test that calls
`window._on_position_update(snapshot(...))` is asking the window a
question the test wrote down; only an `integration` test asks the question
the app is actually asked.

**Driven all the way in** (the fifty-three):

- **The loop's wrap seek, echo practice, and the position a seek lands
  at** — a fake Spotify and a fake clock under the real `PlayerMonitor`,
  wired to the real window's slots. This is the harness that caught a loop
  seeking twice at every wrap while 1487 tests passed.
- **Lyrics arriving, from the wire to the line on screen** — the window,
  `FetchTask`, `LyricsProvider`, the fallback chain, `ConnectionPool` and
  the cache, all the real ones, with a fake **connection** underneath them
  and a real monitor over a fake Spotify announcing the song. A synced
  hit, a plain one, a chain that falls through a 404, a genuine miss
  written down as one, and a 503 that becomes the retry state and is
  written down as nothing. The user's own sync outranking the network, the
  retry going back out, the second play served from the cache, and
  narration leaving the song it announced alone.
- **Every menu entry a click can reach** — all nineteen, delivered to the
  selector Cocoa delivers to, with a tag off the table `_arm` built, and
  the tick read back off the item afterwards. Quit included, through a
  real event loop.
- **Dragging and resizing** — a press, a move and a release routed by Qt,
  so which of the two a press means is Qt's answer and not the test's:
  learning at the end of a drag, learning nothing after a press that moved
  nothing, fitting turned off at the start of an edge drag, and a window
  that is docked, dragged away, and dragged back onto the pixel.
- **Six of the seven controls on the window** — loop, spoken reference,
  echo done, tap, undo, discard — pressed at their live centres with the
  press delivered to the top-level `QWindow`, in both layouts, so Qt picks
  the receiver.
- **The global hotkey** — the callback Carbon was handed is the one that
  hides the lyrics.

**Handler only**, with the entry point that is missing:

| feature | how it is driven today | what is not exercised |
|---|---|---|
| the title card, the line change, the sung line, the romanisation and spoken rows, the glyph following play/pause | `_on_track_change` / `_on_position_update` with a hand-built snapshot | the monitor deciding there was a change at all — which the lyrics tests now do, but only for the fetch |
| the `why` button | `click()`, which names the receiver | the only one of the seven controls never pressed through Qt |
| AppKit itself, under the menu | a stand-in with the same selectors | that a real `NSMenuItem` accepts a tag and a target, which only a Mac can answer and is asserted structurally instead |
| the pointer yield and the strip's reveal | `_check_pointer()` with only `_pointer_position` faked | the `QTimer` that calls it. One call short of driven |
| the notification yield | `_check_notifications()` with only `occupied_rects` faked | the same |
| per-app position memory | `_on_app_activated(...)`, and the settle debounce rewound by hand | `FrontmostWatcher` calling back, and the timer |
| the three accessibility settings | `_on_display_options_changed(...)` | the watcher calling back |
| the spoken reference actually speaking | `SpeakTask` stubbed | `speech.py` is covered alone |

The system appearance is the exception that shows the shape of the fix:
`set_scheme` emits Qt's own `colorSchemeChanged`, so the connection the
window makes in `__init__` runs for real. It is still `qt` — one component
— but it enters where the system does, which is what the rows above do
not.

Two things the rows that have since closed are worth remembering for.

**A fake goes as far down as the seam allows.** The lyrics path is faked at
the **connection** — `http_client`'s injectable connect factory — and not
at `_fetch_json`, so the pool, the hedge, the priority order and the cache
are the real ones and the test can assert what only they together can be
wrong about: that a 503 is never written down, and that narration's three
404s never land under the upcoming song's key. Faked one layer higher it
would have been the two halves again, wearing an `integration` marker.

**The list of what is covered is asked of the app.** The menu file drives
nineteen entries and then asks `Menu.has_handler` which entries there are,
so an entry wired up later fails that test rather than quietly going
uncovered. A written-down list is a list that was true once.

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
