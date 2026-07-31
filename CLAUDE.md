# SottoVoce — working in this repo

Synced lyrics for the Spotify desktop app on macOS: a floating, frameless
window that lives in the menu bar, with opt-in layers for language
learners.

This file is what still binds. The reasoning, the measurements and the
bugs behind each rule are in **[docs/decision-log.md](docs/decision-log.md)**,
which is the old CLAUDE.md kept whole. Every rule below is here because
something went wrong without it — treat "why is this like this?" as a
question the decision log has already answered, and look there before
changing it.

## Architecture in brief

Three parts that do not know about each other: `player_monitor.py` follows
Spotify over AppleScript, `lyrics_provider.py` answers with lyrics, and
`window.py` is the only thing that knows either exists. Everything that
can be logic rather than widget is a **Qt-free pure module** —
`view_model.py`, `sync.py`, `sync_session.py`, `loop.py`, `menu.py`,
`geometry.py`, `typography.py`, `appearance.py`, `transition.py`,
`flight.py`, `app_positions.py`, `gestures.py`, `romanize.py`,
`menubar.py`, `http_client.py`, `settings.py`, `notifications.py`,
`proximity.py`, `failure.py`, `player_events.py`. That is
why the contrast floor is a test rather than a judgement, and why the
whole suite runs headless on Linux.

Threads: the UI thread (Qt only — it never blocks on Spotify), one monitor
`QThread` ticking every ~300ms and querying Spotify about once a second,
and a `QThreadPool` for one-shot work (lyrics fetch, artwork fetch, `say`,
seek, pause/resume). Full map in
[docs/architecture.md](docs/architecture.md).

## Session rules

- **A test touches nothing real.** Not the network, not the developer's
  Spotify, not their saved settings, not the lyrics cache, and above all
  not `.user_syncs/`. Every dangerous thing has a seam; use it.
- **Measure, then decide.** Numbers in this project are measured and the
  measurement is recorded — no constant is set by eye without saying so,
  and none is justified by a measurement of something else.
- **Look at the artefact.** Render the sheet, screenshot the window, read
  the capture. Three separate bugs were invisible in healthy-looking
  numbers and obvious on sight.
- **A refusal names itself.** Gates return the reason (`learn_refusal`,
  `move_refusal`, `refusal`) and the boolean is derived from it, never the
  other way round: a reconstruction can disagree with what happened.
- **One definition.** The version, the User-Agent, the header format, the
  control colours, the type scale, the settings identity — each exists
  once. Two copies of a fact is the shape of most bugs in the log.
- **End every session by committing with a descriptive message and
  pushing.**
- **New work goes in both places.** The narrative, the measurements and
  the wrong turns go in [the decision log](docs/decision-log.md), in
  order. A rule that must not be undone comes back *here*, stated as a
  rule, in as few words as it needs.

## The testing guards

`tests/conftest.py` shuts thirteen doors for the whole session. They are the
alarm, not the fix — the seams are the fix.

| guard | catches |
|---|---|
| outbound sockets (at `socket`, not `urllib`) | any lyrics or artwork fetch |
| `subprocess.run` / `Popen` | `say` to the speakers |
| `player_monitor._cocoa()` | every question and every command to Spotify |
| `player_events._distributed_center()` | an observer on the developer's Spotify |
| `LyricsProvider()` on its defaults | the real `.lyrics_cache/` and `.user_syncs/` |
| `ArtworkProvider()` on its default | the real `.artwork_cache/`, and the CDN |
| `QSettings("sottovoce", "sottovoce")` | the real preferences plist |
| `login_item._main_app_service()` | a real login item on the developer's Mac |
| `nsmenu._appkit()` | an icon in the developer's menu bar, and a menu on their screen |
| `hotkey._carbon()` | claiming ⇧⌘J from whoever is running the suite |
| `frontmost._workspace()` | watching the developer switch apps |
| `notifications._quartz()` | reading every window open on the machine |
| `accessibility._workspace()` | how the developer's Mac is set up, and an observer on it |
| `settings._legacy_settings()` | the plist the old name left behind |

Rules that come with them:

- **A guard must record, not merely raise.** Every escape happens on a
  worker thread and the app catches broad exceptions there by design, so
  an exception alone is swallowed exactly where it matters. Each block is
  recorded and an autouse fixture fails the test that caused it.
- **The guards have tests of their own.** An unrun guard is not a guard.
- **Each native capability has exactly ONE door**, and a structural test
  asserts the native import appears in exactly one place — inside it. A
  second import site passes every behavioural test while reopening the
  door.
- **No test is macOS-only.** Everything native is guarded off-Cocoa and
  asserted structurally. CI fails if any test is skipped.
- **A test may not patch `window._pool`** — it is
  `QThreadPool.globalInstance()`, process-wide, and assigning to its
  `start()` leaks into every test that runs afterwards. Record with a
  subclass of the task instead.
- **No test may pin a pixel that is really a font measurement.** Anything
  downstream of a fitted width or an elided line differs by platform: the
  same fixture line measures 237pt to 351pt across real families, which
  straddles the 316pt where a strip stops fitting inside 460 and is how a
  green suite on macOS became a red one on Linux. Assert the property —
  it fits, it is the narrowest that does, neither bound decided it,
  nothing is elided — and re-measure with the font in force rather than
  naming a number.
- **Reading a QImage's bytes requires holding the image**, and
  `pixels_of()` is the one place that does it. `img.copy(r).constBits()`
  is a memoryview onto a temporary PySide does not keep alive, so
  `.tobytes()` can read freed memory: measured at 9 failures in 20 runs
  against 0 in 40 once the copy is bound. It presents as a flaky pixel
  comparison and reads as a bug in the drawing.
- **`QSettings` is injected, never configured globally.**
  `setDefaultFormat`/`setPath` are process-wide and silently do nothing on
  macOS.
- **A test that supplies its own positions cannot see the loop.** Every
  loop test handed `LineLoop` a position and asked what it decided, and
  every window test handed `_on_position_update` a snapshot built by
  hand: both are the caller answering its own question, and 1487 of them
  passed against a loop that seeked twice at every wrap. What the wrap
  disagrees with is the PLAYER, so the suite has one — a fake Spotify and
  a fake clock under the real `PlayerMonitor`, wired to the real window's
  slots, where the only things that move the position are playback and
  this app's own seeks. Its driver advances to whichever event is next
  (tick, wrap timer, command landing) and never by a fixed step: a poll
  interval at a time puts every landing after the tick that follows it,
  so the window between a seek being sent and it landing never contains a
  poll, and the first version of the harness passed against the unfixed
  loop.
- **A control is proved by a press, and the press goes to the WINDOW.**
  Calling a slot, calling `click()`, and asking `isVisibleTo` all name the
  receiver, which is the one thing a hit-testing bug gets wrong: 1483
  tests passed against a report that every control on the window was
  dead, and none of them could have failed. A press delivered to
  `windowHandle()` lets Qt pick the receiver, at the control's centre
  taken from its live geometry, asserting both halves — the control
  acted, AND the drag handler did not. Both, because neither implies the
  other. A guard test presses bare chrome and asserts the opposite, or
  the second half passes in a suite where nothing is pressable at all.

## Constraints that must not be reintroduced

### Lyrics and the user's work

- `.user_syncs/` is **the user's work, not cache**. It is consulted before
  cache and network, never invalidated, and nothing in the app or its docs
  may delete it — clearing `.lyrics_cache/` must stay a safe reset. Only
  `lyrics_provider.py` and `artwork.py` may write in the package, and
  `artwork.py` may not so much as mention the user-sync directory.
- **Only genuine 404s are cached negatively.** An error's outcome is
  unknown; it surfaces as a retry state and re-attempts every 30s.
- **Non-music items never touch the cache or the network** — DJ narration
  reuses the next song's ID, so even a cache *read* is wrong.
- **Prefer no lyrics to mismatched-duration lyrics.**
- The fallback attempts **resolve in priority order**, and an error on an
  attempt that outranks an answer is still a retry state — using the looser
  answer would write a wrong one down forever.
- **An attempt below the one in hand is asked only when it is needed.**
  Measured: the album match answered 30 of 30 lookups, in 61ms by median.
  So the rest go out on a **hedge** (250ms, clear of the 170ms slowest
  observed) when an attempt is slow, at once when it 404s, and never when
  it answers or errors. LRCLIB is free and under load; the concurrency is
  kept for the day it is slow and not spent on the day it is not.
- Connections to LRCLIB are pooled. A request on a **reused** connection
  that fails is retried once, because a server may close an idle
  connection at any time; a brand new one that fails is **not**, or an
  unreachable network is tried twice and every real failure takes twice as
  long to report.
- A **re-sync takes its lines from the sync it replaces**, not a re-fetch,
  so it works offline. A sync is saved only when complete, and its
  confirmation is inline — never a modal, because the window must never
  take focus.
- The title card gives the window back when there is **something to
  show**, which is not the same as "the fetch finished": a synced song
  joined before its first line has lyrics and nothing to put on screen,
  and ending the card there trades two seconds of the song's name for ten
  of an empty window.

### The window

- **Legibility outranks the material.** The sung line clears 4.5:1 with no
  blur behind it at all, in both appearances, over each one's worst
  backdrop. Roles that recede in dark mode may recede no further in light.
- **The album tint supplies a HUE and nothing else** — its luminance and
  saturation are replaced with ours. The tinted background is solved by
  bisection onto the untinted colour's relative luminance, and tint
  strength is stated as **chroma of the finished panel**, not HSL
  saturation, which means different things at the two ends of the
  lightness range. Every hue must beat the untinted panel's own chroma.
- **Contrast headroom is not aesthetic headroom.** That the floor permits
  a value is not a reason to use it.
- The hairline is **one device pixel**, inset by half its width, and is
  where the album colour lives. The shadow is the **native NSWindow one**.
- **Blur and translucency are mutually exclusive**: macOS renders the
  behind-window blur only while `alphaValue` is exactly 1.
- The window **follows the system appearance** via Qt's `colorScheme`.
  There is no appearance setting — macOS already answers that.
- **No colour emoji.** SF Symbols with a text fallback, tinted from
  `window.py`'s control colours, which are the one source for both.
- **Everything that scales the window's opacity composes in one
  multiply** — the user's setting, the notification yield, the flight.
- **No em dash in anything a person can see.** A middle dot where two
  things are named side by side, a colon where the second half follows
  from the first, a comma where the two halves are one sentence.
  Docstrings and the history files are exempt; a test scans the syntax of
  every module, because a text scan could only be satisfied by deleting
  the explanations.
- The compact layout is **one setting and one applied state**
  (`_compact`, `_compact_applied`), because a sync pass borrows the full
  layout back and that is not the user changing their mind. The FULL
  layout **keeps the height it was last left at**; the strip's height is
  **derived from its type size** and is not remembered, so it offers no
  vertical resize either.
- **A strip is one row tall, so its rows do not wrap.** What will not fit
  is elided, measured against the window and its gutters rather than a
  label width that does not exist yet, and the unelided line is kept so a
  resize does not eat it a word at a time.
- Compact's gutter reserves **two controls a side, symmetrically**. Only
  the right side carries two, but the sung line is centred and an
  asymmetric gutter would centre it in what is left of the window.
- **In the full layout the type scale follows the width; in the strip it
  is a setting.** The proof is why: the room and the line grow at the same
  rate and the width cancels, leaving `(460 - 144) / T`, so a line past
  316pt at scale 1.0 fits at no width and 13 of 14 real songs are past it.
  A strip whose type followed its width could be widened all afternoon
  without showing one more character. Naming the sung line's size directly
  (five presets, default `base_size(CURRENT)`) is what gives the width a
  job, and the strip's height comes off the same size.
- **Anything that changes the window's shape takes the scale it is about
  to land on, never the one it is leaving** (`_type_scale_at`). `_scale`
  is what has been applied and lags a layout change by exactly the moment
  it is asked in; Qt will not let a resize through its own minimum, so a
  floor at the old scale clamps a strip up to the height it is trying to
  stop being.
- The fitted width is measured **once per song, never per line** — a
  window that re-sized itself line by line would twitch through a verse —
  and it is **capped at the screen**, not half of it: half was the only
  control over width while the type size was the width's, and the control
  now is the size. Measured: the old cap bound nothing at 20pt and under,
  and would have elided 4 of 14 songs at 24pt. The user's own width is
  kept separately and is never overwritten by a fitted one. A drag on an
  edge turns fitting off at the **start** of the drag.
- A width change is **anchored on the window's centre**, and a window that
  is exactly where docking put it is **re-docked** instead. Recognised by
  position, not by a flag: the two rules differ by a pixel of parity, and
  a pixel per song is an album.
- Anything that takes the window's position over — a remembered-position
  move, the flight, the save at shutdown — **lands** a resize in flight
  rather than abandoning it. Nothing asks again, so a waypoint is forever.
- **A hidden widget defers its resize event until it is shown.** Anything
  that changes the window's shape itself must re-lay it out (`_relayout`)
  rather than waiting for `resizeEvent`.
- **Echo practice does not fall back to the full layout** and a sync pass
  does. One line repeated is compact at its best, and the loop engages
  many times a song; the pass needs four things a strip has room for one
  of. The reveal is **held open while the attempt waits**, because that
  phase pauses the song and the done button is the only way out of it.
- A failed lookup says **"lyrics unavailable, will retry" and no more**
  unless asked. The reason (kind, HTTP status, the attempt it came from)
  lives one click away, in the HUD's own register, and never carries the
  socket's own message. **A track with no lyrics offers nothing to
  click** — that distinction is the point.

### Motion

- Vertical motion is **one signed property** (`progress`), not separate
  opacity and offset animations. It is a `QGraphicsEffect` drawing the
  source pixmap at an offset, never a moved widget, and `boundingRectFor`
  must grow by the travel or the block clips.
- Every duration derives from **one phase length** (260ms). Sine easing,
  In leaving and Out arriving. A gap shorter than the choreography gets a
  quicker version of it, never a truncated one, and **the arrival lands on
  the timestamp**.
- Line changes are **deduped by target index**: `may_arm(target)` until the
  movement begins, `begin(target)` once it does. Being ahead of the view
  model is bounded — past that, the world moved and the display snaps.
- **`grab()`/`render()` does not apply a `QGraphicsEffect`.** Verify by
  tracing `draw()`.
- **The sung line is rasterised once per phase, not once per frame.** Qt
  has no cache for `sourcePixmap` on a widget source and never calls
  `sourceChanged` for one either, so the invalidation is the window's
  own: four funnels, plus a repaint that arrives without `progress`
  having moved re-rendering anyway, which is the net under a fifth.
- **A repaint between the corner radii is filled straight.** The panel is
  a rectangle with a line down each side there, so three axis-aligned
  fills replace two antialiased rounded rectangles — and that they are
  the same pixels is asserted byte for byte, at 1x and 2x, not reasoned
  about.
- **A harness that agrees with itself is not a measurement.** A
  `processEvents` spin loop reads 100% of a core in every condition; an
  instrumented copy of `draw()` measures the copy.
- **macOS hands back a stale frame** on the first capture after a change:
  motion is measured, not photographed. A screenshot harness must pump the
  event loop, never `sleep` in it.

### Accessibility

- The three display settings are **observed live, like the appearance** —
  one NSWorkspace observer, its own door, never frontmost.py's. Qt
  publishes none of them.
- **Reduce Motion takes the travel and leaves the fade.** The line's rise
  is `travel = 0` (one signed `progress` is what makes that possible; the
  choreography, its timing and its arrival on the timestamp are
  untouched); the flight and the travel to a remembered position go
  entirely, because movement is all they are. The tint cross-fade, the
  yield and the glow stay: they are fades already.
- **Reduce Transparency removes the material, it does not hide it.** A
  hidden effect view is still one, and the flight hides and shows this one
  for its own reasons. The background becomes the solid palette at alpha
  255; the shipped 232/236 belong to a different case (vibrancy that would
  not install) and stay.
- **Increase Contrast implies Reduce Transparency** and the app derives
  that rather than trusting the pair to arrive together.
- Increase Contrast is a **short list of overrides, not a third palette**,
  and each one is the alpha that clears a measured floor over the opaque
  panel: 4.5:1 for anything read to follow a song, 3:1 for marks and
  switched-off labels. Dropping the material does most of the work.
- **The album tint's hairline is not lifted** to 3:1 and that is measured,
  not forgotten: reaching it would cost the hue-only design, and nothing
  is read against the hairline.

### Following Spotify

- **AppleScript is never compiled or sent unless Spotify is already
  running.** A `tell` block needs the app's dictionary at COMPILE time,
  and so, it turns out, does naming an application at all: compiled in
  this process, a script about an application that is not installed puts
  macOS's "Where is Spotify?" chooser in front of the user and blocks the
  thread. The dictionary-free probe does the same — what cannot be
  resolved is the application, not its vocabulary. Whether Spotify is
  running is therefore an **AppKit** question (`NSRunningApplication`,
  0.017ms), asked before anything is compiled, and the gate lives inside
  `_ask` so a command added later cannot miss it.
- **Every question and command is sent from this process**, through one
  compiled-once `NSAppleScript` per script, **serialised behind one
  lock** — three threads sharing one script measured fifty times slower
  than one at a time. Launching `osascript` cost 58.8ms of CPU against
  4.3ms, and woke four daemons on every poll.
- **A query's timeout lives in the script.** `NSAppleScript` sends with
  the Apple Event Manager's default of about a minute, and a minute is
  one wedged Spotify away from a monitor thread outliving shutdown's
  bounded wait. `with timeout of N seconds` needs no dictionary.
- **The announcement is a doorbell, not an answer.** Spotify's
  `PlaybackStateChanged` says "ask again"; the payload is thrown away,
  because it has no artwork URL and because track identity may have
  exactly one definition. It must be observed **by name** — registering
  for everything receives nothing.
- **A seek is announced to nobody**, and that is the only reason a poll
  loop survives. Everything else rings: track changes, play, pause, a
  song ending, Spotify quitting and starting, each driven and timed.
- **Between queries the position is arithmetic on the monotonic clock**,
  and it is exact rather than approximate (1.4ms over 92 seconds). The
  window still hears a position every `POLL_INTERVAL_SECONDS`; what
  changed is what that costs.
- **A seek this app made is never waited for**: the player commands call
  `disturb()` in a `finally`, so a failed command counts too. Module
  level, so a command cannot be added that forgets. A seek is also an
  **answer** and not only a question — `moved()` records where this app
  put the player and the monitor carries the position forward from
  there, because `poll_once` clears the wake BEFORE the query and returns
  None when it raises, so a transient failure spends the disturb and the
  next answer is a reconciliation interval away (measured: the window
  told a position 9.673s from the player's). Recorded on **success
  only**, while `disturb()` stays in the `finally`: a seek that failed
  moved nothing, and its whole signal is the position drifting out of
  bounds. Asked of the **last** answer and never of a fresh one — one
  lock means a query cannot run while a seek is executing, so a fresh
  answer is always stamped after any seek that has finished.
- **A command in flight is not a command that has happened.** The loop's
  wrap seek is dispatched a seek lead before the end bound and the round
  trip takes most of a poll interval, so every position that arrives in
  between is still inside the lead: `wrap_eta` clamped to zero and the
  wrap went out again, and again on the next poll. Measured, on a 10
  second line looped for 45 seconds: 7 to 8 seeks where 4 were wanted,
  the second landing a round trip after the first and restarting a line
  that had already restarted. The extra seeks queue on the one
  `_ask_lock` ahead of the reconciliation query, so at a 0.7s round trip
  the player really did run past the end bound and the loop cancelled
  itself in 10 runs out of 10. **One wrap is outstanding at a time**, and
  a position EARLIER than the one it was dispatched from is what says it
  landed — a seek is the only thing that moves a position backwards. A
  wrap that never lands dispatches nothing more and `still_valid`
  cancels, which is what a failed seek has always done. The anticipatory
  line change already had this rule (`may_arm`/`begin`); the loop did
  not.
- **The slower rate is lost, not earned.** It starts the moment the
  observer registers, because announcements only arrive when something
  changes and waiting for one means asking three times a second through
  an uninterrupted song. A track or state change discovered with nothing
  having rung takes it away, and the next properly announced one gives it
  back. Nothing sniffs a Spotify version.

### System integration
- **Accessory activation policy is unconditional** and applied before any
  window exists, with `LSUIElement` in the plist for the instant before it
  runs. There is no Regular policy to fall back to.
- Qt defaults windows to `FullScreenPrimary`; Primary and Auxiliary are
  mutually exclusive, so the all-desktops toggle must clear Primary.
  **Native state is verified by readback.**
- **One MODEL serves the menu bar item and the window's right-click
  menu, and one native NSMenu draws it.** "One menu" used to mean one
  `QMenu`, which was one object and two appearances: Qt converts a tray
  menu into a real NSMenu and draws a popup itself. The model is
  `menu.py` and is pure; `nsmenu.py` is the only place that says NSMenu,
  NSMenuItem or NSStatusItem. Its structure is built once; refresh only
  flips visibility, checks, chosen presets and labels, or the native item
  flickers.
- **Nothing checks or unchecks an entry from a click.** The handler
  changes the app's state and the refresh that follows says what the
  state is: `Menu.trigger` hands a toggle the state it is moving TO. It
  is why the QMenu entries were connected to `triggered` rather than
  `toggled`, and a tick that moved itself would be a second answer to
  what the setting is.
- **The menu bar item is this app's own NSStatusItem**, not
  `QSystemTrayIcon`'s: Qt owns the item it makes and there is no
  supported way to hand it a menu, so the item that carries one NSMenu
  has to be an item this app made. It is removed in `_shutdown` rather
  than destroyed with the widget, and the glyph is PNG bytes drawn by
  `symbols.py` and labelled with the point size the menu bar wants.
- **The long tail of standing preferences lives in two submenus**
  (Position, System) and nothing that comes and goes with the song may
  be buried in one: an entry that appears because it can now act is one
  somebody is looking for, and a submenu is one more click and one more
  place to look. A submenu with nothing visible inside it is hidden, like
  any other entry that cannot act.
- **A row that states a fact is not a control and may not be drawn
  grey.** macOS dims a disabled item when it draws it, whatever an
  attributed title's `labelColor` asked for (measured), so the
  remembered-apps rows are NSMenuItems with a VIEW. That was a
  `QWidgetAction` before and is the same answer in the native idiom.
- **Quit is visible in every state**, and hiding leaves the monitor, the
  loop and any sync pass running.
- The login item's tick **follows macOS**, never the stored preference,
  and `set_enabled` re-reads the status rather than trusting its return.
- The notification yield may **never ask for `kCGWindowName`** or touch
  any screen-capture entry point — two tests forbid it by scanning the
  module's syntax. It keys on the **bundle identifier**, never the
  localised owner name, and `com.apple.controlcenter` must stay out of the
  set (it owns eleven permanently on-screen windows).
- **macOS delivers enter, leave and mouse-moved events only to the ACTIVE
  app**, and this one never activates: Qt's tracking area is
  `NSTrackingActiveInActiveApp`. Anything that needs to know where the
  pointer is asks for its position, on a timer, and only while something
  could act on the answer. The offscreen platform has no pointer, so no
  test can catch this: it is verified by driving the real one with the
  app backgrounded. **Two layers read that one poll and they read it
  once between them** — the pointer may move between two calls, and two
  readings would be two answers to one question.
- Per-app position memory: **our own activation is dropped** rather than
  becoming the frontmost app — dragging the window activates us. The
  debounce rule is authoritative and the timer is only a prompt, because
  `QTimer` may fire early.
- **Docking is a command, not a snap**: nothing is magnetic and the window
  stays freely draggable. It is centred on the SCREEN (the menu bar spans
  it, the notch is centred on it), flush with no gap of its own, and the
  screen's **safe area is a floor on the available area, never a
  replacement** — the case it exists for is a menu bar set to hide
  automatically, which gives the whole screen back and leaves the notch.
  A dock is **learned like the end of a drag**, or the position layer
  undoes it at the next app switch, and is written from the target
  because the travel would otherwise record a waypoint.
- A docked window is drawn **square across the top and rounded
  underneath**, so it reads as the menu bar's band continuing rather than
  a panel with desktop showing through two corners. Whether it is docked
  is `geometry.is_docked`, asked of the POSITION on every move (8.9us) and
  never held as a flag: a flag has to be cleared by every drag, clamp,
  nudge and screen change. Asked from `_relayout` and `showEvent` too,
  because a hidden widget defers both its move and its resize events.
  **One path builds both shapes** and the rounded one is byte for byte
  what `drawRoundedRect` drew. The material is a separate native view and
  is told the same corners (`CACornerMask`), or the blur keeps the shape
  the paint has left.
- The flight puts the window's **position back before it is hidden**, and
  one method gives back everything it borrowed. Startup does not fly.
- Yielding to the pointer: the trigger region is anchored on **where the
  window belongs**, never on where it is, or a dodged window reports
  clear one poll after reporting covered, for ever. Leaving takes BOTH
  rectangles, each with the release margin, or a dodged window can never
  be caught. The behaviour is **edge triggered**: it begins on the
  pointer arriving and nothing restarts it, so a suspension that ends
  with the hand still there does not step the window out from under it.
  `Approach` keeps engagement and action as two booleans, because
  whether the pointer is on the window is not the same fact as whether
  the window may act on it.
- **A dodge is a loan, like the flight.** `_proximity_home` holds the
  real position and `_home_pos()` is what the save at shutdown, the
  per-app learn and the fitted width ask instead of `pos()`. Anything
  that moves the real position while it is borrowed writes the HOME and
  derives the temporary one again. A docked window must come back to the
  pixel: `is_docked` is recognised by position.
- **Click-through is two switches**: `WA_TransparentForMouseEvents` for
  this window's own widgets, `setIgnoresMouseEvents_` for the click to
  reach another app. Only the first is a window that swallows clicks
  without passing them on. Verified by readback. It is switched at the
  START of the fade, both ways, or the window eats the click the user
  came to make. A ghosted strip offers **no controls**: a control that
  can be seen and not pressed is the mirror of the widget-at-zero-opacity
  rule.
- Every native login-item, hotkey, workspace and window-list call goes
  through its module's single door. Releasing the hotkey is the **first**
  thing `_shutdown` does — it is the only thing that can still call in.

### Shutdown

- **A `QThread` destroyed while running is a `qFatal`.** `_shutdown`
  drains everything the window owns — hotkey, watcher, timers, flight,
  save, monitor thread, then the worker pool — before anything is
  destroyed.
- **Waits are bounded and honest.** A worker that will not return in 3s is
  logged and left to the pool's destructor; blocking quit on it would be
  worse.
- The monitor's stop is a `threading.Event` set once, and the remaining
  poll interval is **waited on rather than slept through**.

### Packaging and identity

- **The version has exactly one definition**: `importlib.metadata`, via
  `sottovoce/__init__.py`. No version literal anywhere else — the spec
  reads the installed distribution, refuses to build on drift, and
  `copy_metadata` is load-bearing so the frozen app can still answer.
- **The bundle identifier is the settings contract.**
  `com.sottovoce.sottovoce` is what `QSettings("sottovoce", "sottovoce")`
  resolves to, and a test pins the two together.
- `settings.py` carries the preferences the LyriSync name left behind,
  once. **`QSettings.allKeys()` on macOS falls through to NSGlobalDomain**
  — ask per group (`OWNED_GROUPS`), never for everything.
- `REPOSITORY_URL` is pinned against the README's `git clone` line, and
  the User-Agent has one definition.
- **`dist/` is emptied, never removed** (Finder recreates `.DS_Store` in
  the gap), and archives are made with `ditto`, never `zip`.
- **Bundle building stays out of CI**: the artefact is macOS-only and only
  a person can accept what matters about it.
- **A recording of the app is a recording of a real machine.** The demo
  GIF is shot against a *throwaway* editor profile (its own
  `--user-data-dir`), with Do Not Disturb on and the pointer parked
  off-frame — everything in the frame is real, so nothing in it may be
  personal, and the developer's own windows are not the backdrop.
  Nothing is configured for the camera. Everything borrowed is given
  back, and the preferences plist is restored **after** the app quits,
  because quitting writes to it.

### Layers

Every learning feature is an **opt-in, default-off layer**, and every
layer off must equal the plain synced-lyrics window. "Off" removes the
work, not just the output — the position layer unsubscribes from
activations rather than ignoring them.

Fitting the width to the song is the **one setting that defaults on**, and
it may only because it is reachable solely from inside the compact layout,
which is itself opt-in and default off. A default-on setting has to be
unreachable from the plain window, not merely quiet in it.

The strip's text size is offered on the same terms and for a sharper
reason: in the full layout the type size **is** the width, so a control for
it there would be a second answer to one question.

## Where the rest is

| | |
|---|---|
| [docs/compact-and-docking.md](docs/compact-and-docking.md) | the strip, the pointer poll, and the notch |
| [docs/pointer-yield.md](docs/pointer-yield.md) | Dodge, Ghost, and the hysteresis |
| [docs/decision-log.md](docs/decision-log.md) | every decision, in order, with its measurement |
| [DESIGN_PHILOSOPHY.md](DESIGN_PHILOSOPHY.md) | the twelve principles the rest is downstream of |
| [docs/](docs/) | one page per topic: architecture, contrast, album colour, motion, packaging, testing, … |
| [CHANGELOG.md](CHANGELOG.md) | the milestones in order |

## Parked

Splitting the compact layout around the notch; Dynamic Island style
flanking icons; automatic snapping to any edge; per line sizing; height
adaptation;
album-art background; multiple colours or gradients from one cover;
per-song colour overrides; karaoke word-by-word; side panels; Japanese
romanisation; configurable hotkeys or any hotkey beyond show/hide; focus
fade; yielding to anything beyond the notification system; a different
yield ceiling per appearance; publishing user syncs to LRCLIB; starting a
sync mid-song, partial saves, per-line editing of an existing sync.
