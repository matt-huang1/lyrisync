# Per-app window position

The window returns to wherever you last put it, for whichever app you
just switched to. Off by default, like every layer.

Put it bottom-right while you are in VSCode and top-left while you are in
Safari, and from then on it goes to the right place on its own.

## Learning is implicit

There is no "save position here" command. You drag the window where you
want it while working in some app, and that *is* the gesture — the app
was already watching which app is in front, so it has everything it
needs.

An explicit save would be a second thing to remember to do, for a
preference that is only ever expressed by moving a window. The moment a
drag or a resize ends is the only moment the user has said anything about
where the window belongs, so that is the only moment anything is learned.

This works because of a property the app already had: **the window is
unfocusable and the app is an accessory**, so dragging it does not change
which app is frontmost. Without that, every drag would record a position
against SottoVoce itself.

## Watching the frontmost app

`NSWorkspace` posts `NSWorkspaceDidActivateApplicationNotification` every
time the frontmost application changes, and subscribing needs **no
permission at all**. Nothing here reads a keystroke, a window's contents,
or anything about the other app beyond the identifier it advertises. The
alternatives that could answer the same question — an Accessibility
observer, polling the window list — all put a prompt in front of the user
for information macOS is already broadcasting.

A full-screen Space switch counts as an activation, and that is wanted
rather than tolerated: moving into a full-screen editor *is* arriving at
that app.

Everything native goes through one door, `frontmost._workspace()`, the
same shape as [the hotkey's](hotkey-and-carbon.md) `_carbon()`. A stray
observer in the test suite would sit on the developer's own workspace for
the life of the process, so there is exactly one thing for the suite to
shut — and a test asserts that `NSWorkspace` is imported in exactly one
place, inside that door, because a second import added later would pass
every behavioural test while quietly reopening it.

## Settling, so a Cmd-Tab sweep is not six instructions

Holding Cmd and stepping through six apps announces six activations.
Acting on each would drag the window across the screen six times, which
is the failure this feature has to avoid to be worth having.

So an app must stay frontmost for **400 ms** before the window follows
it. Long enough to sit out a sweep, short enough that a deliberate switch
feels like it was waiting for you.

Repeat announcements of the app already pending do *not* restart the
clock. macOS can report the same activation more than once, and a rule
that reset on every announcement could keep an app settling forever while
it sat there being frontmost.

### The bug that only a real run could find

The `QTimer` that wakes the app up and the rule that decides whether an
app has settled are two clocks measuring one interval, and QTimer is
entitled to fire a hair early. Measured live: it fired at **390 ms**
against the 400 ms rule, the rule said "not yet", and because the timer is
single-shot **the arrival was dropped for good** — the window simply never
followed, once or twice out of every four switches.

The fix is not a fudge factor on either side. The rule stays
authoritative and the timer is only a prompt: asked too early, it
re-arms for however long is actually left. Same shape as the monitor
waiting out the remainder of a poll interval rather than sleeping through
one.

## When the window is not moved

Three refusals, each a different kind of "the user is in the middle of
something":

- **Dragging** — they have hold of the window; moving it under the cursor
  would be the app fighting the hand.
- **A tap-to-sync pass** — a rhythm game against a moving target. The tap
  bar sliding mid-pass would cost stamps.
- **Hidden** — nothing to move, and moving it anyway would mean it
  reappears somewhere it was never seen to go.

And the quiet one: **an app with no remembered position leaves the window
exactly where it is.** Never a default. A default would move the window
somewhere the user never put it, which is worse than doing nothing.

Every destination is still clamped by `geometry.py`, on arrival rather
than only when learned — the position may have been recorded on a display
that is no longer attached, and a remembered position is not a licence to
put the window somewhere unreachable.

## The move itself

Animated, not teleported: 260 ms — one phase of a line change — with
sine easing, for the same reason [milestone 13](motion-and-typography.md)
chose sine over cubic. `InOutSine` rather than In-then-Out, because this
is one continuous movement rather than two phases handing over.

A second activation mid-travel retargets from wherever the window got to,
not from where the last one was heading. The same rule the album tint's
cross-fade follows, and for the same reason: the user is looking at where
it is, not at where it was going.

## The map

Bundle identifier → (x, y, name), capped at 50 entries, least recently
used dropped first. The name is the last one seen, kept so the list can
read; a save that does not know it does not erase the one already there,
because not knowing what an app is called this time is not evidence that
last time was wrong. Recency counts a *use* rather than just a write: recalling
a position refreshes the entry, or the app you switch to constantly but
rarely re-place would be the first evicted.

Positions are cheap — one drag each to relearn — which is why the cap can
be crude and why **Forget remembered positions** needs no confirmation.
That is the whole difference between this and a hand-made sync, which is
[never treated as cache](lyrics-and-caching.md).

Stored as JSON in the settings file, as a list of rows rather than an
object so the eviction order is part of the format rather than a property
of whichever JSON reader loads it back. A file somebody edited by hand
degrades rather than taking the feature down: unreadable text yields an
empty map, and one bad entry costs only itself.

Rows have four fields since names arrived, and the three-field shape
milestone 14 wrote is still read — a map saved before names existed keeps
every position and simply has no labels until each app is next seen.
Refusing the old shape would have cost the user everything they had
taught it, to gain a label.

## The bug that made all of this look broken

Shipped, and reported as not working — with the second half of the report
being that the user could not tell whether they were using it right. That
second half is the more important one: **implicit learning with no
feedback is indistinguishable from a broken feature**, so it has to be
possible to tell the two apart before either can be fixed.

The evidence was in the settings file: the layer was on, the window had
plainly been dragged (its saved position had moved), and
`window.app_positions` was `[]`. So drags were happening and nothing was
being recorded, which narrows it to the three refusals in
`learn_refusal` — and only one of them could hold with the layer on and a
window being dragged.

**Dragging the window activates SottoVoce.** The feature's founding
assumption was that it does not: the window is unfocusable and the app is
an accessory, so a drag was thought to leave the frontmost app alone.
Unfocusable is about *key focus*; app activation is a separate thing, and
an accessory app can be frontmost. Measured, in both a source run and the
built bundle:

```
… activation notification: com.sottovoce.sottovoce     ← the drag
… learn: nothing recorded, SottoVoce itself is frontmost
```

So every drag replaced the app the user was working in with ourselves,
after which the self-filter — which exists for exactly this and was
right to fire — refused to learn. The map could never gain an entry.

The fix is one branch, and it belongs where the assumption failed rather
than where the symptom appeared: **our own activation is dropped instead
of becoming the frontmost app**, so what a drag records against is the
last app that was not us. The self-filter in `learn_refusal` stays as
well; it is now a second line rather than the only one, and it is what
would catch this class of thing again.

Reproduced before it was fixed, not inferred. The verification harness
takes a `--without-filter` flag that puts the old handler back: with it,
two real drags in two real applications leave `map=[]` and the window
never follows — the reported symptom, on demand.

### And the thing that hid it for a whole milestone

Milestone 14's harness learned positions by calling `_learn_position`
directly, which is the one step that cannot see this: no click, no
activation, no bug. It is also why the earlier note in this file reads as
though the accessory policy had settled the question — with the policy
applied our own activation stopped arriving *at show()*, and nothing was
ever clicked afterwards to make it arrive again.

The harness now posts real `CGEvent` mouse presses, drags and releases at
the window. That is the difference between testing the gesture and
testing the function the gesture calls.

## Seeing that it works

Two answers, for two different questions.

**"Is it recording anything, and does it know about the app I am in?"** —
the menu says so, in words, whenever it is opened:

```
Remember position per app            ✓
🧭 4 apps remembered · Safari is placed
Remembered apps                      ▸
Forget remembered positions
```

A count and the app in front, because there are two ways to doubt an
implicit feature: whether anything has been learned at all, and whether
*this* app — the one a drag would record against — is one of the ones
that has. A count alone leaves "is it working here?" unanswered; the
position alone hides an empty map behind one app that happens to have no
entry.

**Names, not identifiers**, with the app's own icon beside them. The
first version of this readout printed `com.apple.Safari`, on the argument
that it is what the map is keyed on and what the log says, so the two
could be compared. That is a real argument and it belongs to the log: a
menu is read by a person deciding whether a feature works, and an
identifier makes them translate before they can answer. The name comes
from the activation that brought the app forward, so it costs nothing —
both halves arrive in the same notification — and it is **stored beside
the position**, because the map outlives the sessions that taught it and
an app that is not running cannot be asked what it is called. With no
name anywhere, the identifier is still shown; it beats a blank.

**No coordinates.** They answered a question nobody asks of a menu: a
number pair cannot be checked against anything by eye, and the window is
sitting at it in plain view. They stay in the DEBUG log, where a reader is
comparing them with something. A detail toggle to put them back was
considered and rejected — an extra entry for a fact already on screen, in
a menu whose problem was never too little information.

The readout is disabled, because it is a readout and not a control, and
its `menuRole` is `NoRole` rather than Qt's default
`TextHeuristicRole`: it is the one entry whose text the app does not
write, and the heuristic that relocates "Preferences…" into the
application menu matches substrings — "System Settings", or
`com.apple.systempreferences` before it has a name, would trip it. A
diagnostic that moves itself depending on which app you switched to would
vanish exactly when read.

**Remembered apps** lists what has been learned, most recently used
first, each with its icon and name. It is a **readout**: the rows are
disabled and there is nothing to click. Clicking one used to forget it,
and that control was removed rather than kept — re-dragging the window in
an app overwrites its position, so per-app forget can only ever express
*stop moving the window for this one app*, which is not a thing anybody
wants for one app while wanting it for the others. **Forget remembered
positions** stays, because "stop doing this" is a real wish and that is
where it belongs.

The list is the only menu in this app whose *contents* are rebuilt rather
than only relabelled — everything else is a fixed set of entries whose visibility
changes, because rebuilding the menu bar item's structure makes it
flicker. A list of what has been learned cannot be a fixed set, and this
one is assembled on its own `aboutToShow`: only while the user is looking
at that submenu, never while the menu bar item is idle.

Icons come from the workspace by bundle identifier rather than from a
running process, so an app that is remembered but not running still has a
face; one that has been uninstalled since simply has none, and its name
still reads. They arrive as TIFF bytes so that nothing pyobjc-shaped
crosses out of `frontmost.py`, and they are **redrawn at the size asked
for**: `iconForFile_` hands back every representation from 16 to 1024,
which is 74 MB of TIFF decoding to a 1024x1024 pixmap. Drawn once at 16
points it is 12 KB, and comes back at the screen's own scale.

The readout follows the *toggle*, not the map, and unlike the forget
entry that is not about whether it could act. It names the frontmost app,
and with the layer off nothing is watching which app that is: a stale
line would be worse than no line, and going to look would be the
watching that "off" promises to end.

**"What exactly happened just now?"** — `SOTTOVOCE_LOG=DEBUG` prints the
whole chain, one line per decision, from the notification to the pixels:

```
per-app positions restored on: frontmost=com.apple.Safari watching=True remembered=2 own=com.sottovoce.sottovoce
activation notification: com.microsoft.VSCode
activation: com.microsoft.VSCode (arrival)
settling: com.microsoft.VSCode has 12ms left — asking again
settled: com.microsoft.VSCode — remembered at 900, 120
move: 150, 600 → 900, 120
learn: 396, 495 recorded for com.apple.Safari (1 apps remembered)
```

Every refusal names itself, and the reasons are not written twice:
`learn_refusal` and `move_refusal` return the reason, `may_learn` and
`may_move` are derived from them. A log line assembled separately from
the rule it describes is a log line that can disagree with what the code
did — which, in a feature whose whole problem was not being able to tell
working from broken, would be worse than no line at all.

`ActivationDebounce.observe` returns which of `ARRIVAL`, `REPEAT` and
`UNKEYABLE` an announcement was, for the same reason: the alternative is
asking the debounce about its own state afterwards and reconstructing the
answer.

### The acknowledgement on the window

**This supersedes the refusal recorded here in 14.1.** That entry
declined an on-window acknowledgement on three counts: a transient line
would have to borrow the sung-line row, it would have to print a bundle
identifier mid-lyric to say anything useful, and the wordless version —
pulsing the hairline — would be a second animation of an edge that
[13.2](appearance-and-materials.md) gave a single owner on purpose.

The first two still stand, and no text was added to the window. The third
was too strict, and the rule that replaces it is now
[principle 12](../DESIGN_PHILOSOPHY.md): **transient feedback may briefly
borrow a surface that persistent decoration owns, then return it.**
Owning a surface and borrowing one are different things, provided the
return is structural rather than remembered.

So when a position is recorded, the hairline warms for 780 ms and goes
back:

- **A half sine, one property.** `progress` runs 0 to 1 and the intensity
  is `sin(π · progress)` — nothing at either end, all the way to the warm
  colour in the middle. One property with the whole rise and fall in it,
  the same reasoning as the line change's signed `progress`, and it is why
  there is no easing curve to choose and no step at either boundary.
- **And the edge thickens with it**, from one device pixel to three. This
  is the half that does the work. The first version peaked at 0.85 of the
  way to the amber and lasted 520 ms, and it was too subtle to catch
  without staring: a single device pixel changing colour is a few hundred
  pixels on a 460-point window, which is nothing at the edge of attention.
  Three device pixels of warm edge is a *shape* change, and the eye is far
  better at those. The width rides the same intensity as the colour, so
  the edge cannot be left thick and cool, and it returns with it.
- **A mix at paint time, never in the tint state.** `_current_border()`
  stays the album's own answer and is what a cross-fade starts and ends
  on; `_painted_border()` applies the glow over it, once, in the frame
  being drawn. A cover landing mid-glow therefore cross-fades underneath
  it and cannot capture a warmed edge — which is exactly what would have
  happened had the glow been folded into the tint, and it is what the
  13.2 objection was really about.
- **Giving it back is the animation ending**, not a piece of cleanup: the
  mix reaches zero and the edge is the album's again, to the channel.
- **One per gesture.** A second acknowledgement inside the first is
  refused rather than restarted — it would read as a flicker rather than
  as two answers — and that is also what makes a release delivered twice
  harmless.
- **Warm, because everything else here is cool.** The amber is the one
  this app already uses for "your turn" in echo practice, darkened for
  light mode like every other accent. It is deliberately *not* tinted by
  the album: an acknowledgement that changed colour with the cover would
  read as part of the artwork rather than as an answer.

Measured, in both appearances, from the pixels `paintEvent` produced —
one device pixel of hairline at rest, three at the peak:

| | at rest | at the peak |
|---|---|---|
| dark | `(255,255,255,30)` | `(255,214,120,165)` |
| dark, red cover | `(237,130,130,110)` | `(255,214,120,165)` |
| light | `(0,0,0,38)` | `(150,96,0,170)` |
| light, red cover | `(132,21,21,105)` | `(150,96,0,170)` |

Over a real backdrop, with the material rendering, the edge goes from
`(58,60,65)` to `(197,165,96)` in dark and from `(185,186,186)` to
`(170,127,52)` in light. At the peak the album's own hue is gone from the
edge for a moment — the acknowledgement is the same answer every time, so
it arrives at the same colour whatever is playing — and it comes back
exactly.

The animation itself is traced rather than photographed, because macOS
returns a stale frame on the first capture after a change: 49 frames over
787 ms, peaking at 1.0, ending at 0.0, with the edge back to
`(132,21,21,105)` to the channel.

The menu is still refreshed at the moment of learning as well as on every
opening, so the fuller answer is one click away and always current.

## The toggle

**Remember position per app** is always offered — a standing preference
about where the window lives, answerable whether or not anything is
playing and whether or not any position has been learned yet. The same
argument as album colour.

**Forget remembered positions** and **Remembered apps** both appear only
once something has been learned, and disappear again when the map is
cleared. They stay reachable with the layer switched off, so a bad map
can be cleared without turning the feature back on to reach the control
that clears it — and a single app can be forgotten from the list rather
than everything at once. Neither asks for confirmation, for the reason
the map is capped at all: a position costs one drag to relearn.

Off stops the observing as well as the moving: the notification
subscription is removed, so with the layer off this app is not watching
what you do at all. Switching it off keeps what was learned, so turning
it back on does not start from nothing.

Nothing moves at the moment the layer is switched on. The window is
already where you last put it, and jumping the instant a menu item is
ticked would make the feature's first impression a surprise.

## Verifying it

The observer cannot be unit-tested — no test can make macOS change the
frontmost application — so it is checked by hand, and the check is
scripted rather than described. Two scratch harnesses under a real Qt
event loop, driving real app switches with `osascript`:

1. **The observer alone.** Three switches, three activations, correct
   bundle identifiers, no prompt, and `stop()` genuinely stops it.
2. **The whole chain.** A real window learns a position for two real
   applications the way a drag does, then switches between them: four out
   of four arrivals correct, no movement during a rapid sweep, correct
   position after the sweep settles, and no movement at all once the
   layer is off.

Two things that harness got wrong first, both worth knowing:

- It must call `apply_accessory_policy()` before creating the window, as
  `main()` does. Without it the process is a Regular app, `show()`
  activates it, and **our own bundle identifier arrives as the frontmost
  app** — which the self-filter in `learn_refusal` correctly refused, so
  the symptom was positions silently not being learned rather than
  anything looking broken.
- It must pump the Qt event loop rather than sleeping in it, or no
  notification is ever delivered.

### Re-verified with real gestures, and in the artefact

The harness now drives the gesture rather than the function under it:
real app switches with `osascript`, real mouse presses, drags and
releases posted as `CGEvent`s, and a second independent watcher recording
every activation macOS announces — including ours — so the raw stream can
be compared with what the window did with it.

Run against a source checkout (`org.python.python`) and then against the
built bundle (`com.sottovoce.sottovoce`), because a source tree that is
right and a bundle that is stale look identical from the outside. In the
bundle: two real drags in two real applications recorded
`[["com.microsoft.VSCode", 672, 260], ["com.apple.Safari", 396, 495]]`,
and three switches between them moved the window to the right place three
times out of three.

### Full-screen Spaces are the same path, measured

The most likely place for this to fail silently, and the case the author
actually lives in — so it is asked of the artefact rather than inferred
from the windowed case. It is not a separate path:

- Entering full screen announces **nothing**, and that is correct: Safari
  was already frontmost, so no activation happened.
- Leaving that Space for a windowed app announces an ordinary activation.
- Coming *back* into the full-screen Space announces an ordinary
  activation too, 400 ms later the window follows, and it arrives at the
  remembered position.

Confirmed from pixels as well as from the log: a screen capture taken
inside Safari's full-screen Space has the window sitting at the position
learned for Safari. (With **Show on all desktops** off it would not be
visible there at all — a Space switch would still be learned from and
still be followed, but the result would be waiting on the desktop.)

The scripted click on the menu bar item announced no activation at all.
Whether that is because a status-item click does not activate an
accessory app, or because the synthetic click never landed on it, cannot
be told from the log — so the self-activation branch is justified by the
drag, which *is* measured, and not by the menu.
