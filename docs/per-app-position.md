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
against LyriSync itself.

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

Bundle identifier → (x, y), capped at 50 entries, least recently used
dropped first. Recency counts a *use* rather than just a write: recalling
a position refreshes the entry, or the app you switch to constantly but
rarely re-place would be the first evicted.

Positions are cheap — one drag each to relearn — which is why the cap can
be crude and why **Forget remembered positions** needs no confirmation.
That is the whole difference between this and a hand-made sync, which is
[never treated as cache](lyrics-and-caching.md).

Stored as JSON in the settings file, as a list of triples rather than an
object so the eviction order is part of the format rather than a property
of whichever JSON reader loads it back. A file somebody edited by hand
degrades rather than taking the feature down: unreadable text yields an
empty map, and one bad entry costs only itself.

## The toggle

**Remember position per app** is always offered — a standing preference
about where the window lives, answerable whether or not anything is
playing and whether or not any position has been learned yet. The same
argument as album colour.

**Forget remembered positions** appears only once something has been
learned, and disappears again when the map is cleared. It stays reachable
with the layer switched off, so a bad map can be cleared without turning
the feature back on to reach the control that clears it.

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
  app** — which the self-filter in `may_learn` correctly refused, so the
  symptom was positions silently not being learned rather than anything
  looking broken.
- It must pump the Qt event loop rather than sleeping in it, or no
  notification is ever delivered.
