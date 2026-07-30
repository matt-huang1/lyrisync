# The menu, and living in the system

## One menu, two ways in

The menu bar item's menu and the window's right-click menu are **literally
the same `QMenu` object**. Two menus could drift apart; one cannot.

Its structure is built once and never rebuilt. Refreshing only flips
visibility, check marks and labels — rebuilding would make the native menu
bar item flicker every time anything changed.

Checkable entries connect to `triggered`, not `toggled`, because refresh
calls `setChecked` on all of them and `toggled` would feed those
programmatic changes straight back into the setters as if the user had
clicked.

Which entries are visible is pure logic in `menu.py`, tested without Qt.

## Entries appear only where they apply

The learning layers hide when they cannot act — there is nothing to
romanise without hangul on screen, nothing to speak without the Korean
voice installed. With every layer dormant the menu is show/hide, the two
choices about how the window looks, and quit.

Album colour is the exception: it is *always* visible. The others hide
because they cannot act; this one can always be answered and is a standing
preference about the window, so appearing and vanishing with each track
would hide it exactly when someone goes looking — before the music starts.

**Quit is visible in every state.** The menu bar item is the way back from
a hidden window, so it must never be a dead end.

## Hiding hides, and nothing else

Hiding the lyrics leaves the monitor running, a loop engaged, and a
tap-to-sync pass stamping. Showing the window again picks the song up
wherever it now is. The setting is remembered across launches.

That is the whole distinction: hiding is a display choice, not a stop
button.

### The window goes somewhere, visibly

It used to blink out. That is right for a window you closed and wrong for
one that is still running: nothing said where it had gone, so the way back
was something to remember rather than something you saw.

Now it **shrinks and fades towards the menu bar item**, and grows back out
of it — 260 ms, the same as one phase of a line change and the same as the
travel to a remembered position, because the window should have one sense
of how fast it moves. It accelerates away and decelerates onto its mark
(`InSine` leaving, `OutSine` arriving), the pairing
[milestone 13](motion-and-typography.md) chose for the same reason.

**The content scales; the window does not.** A `CALayer` affine transform
on the view Qt draws into, so the compositor does the scaling. Animating
the window's *size* would re-lay the text out on every frame — the type
scale follows the window's width — and the window would read as rewriting
itself rather than leaving. Qt leaves that layer's anchor point at its
origin, so scaling about the centre is a translation of half the shrinkage
in each direction; measured, not assumed, after a bare scale pinned the
content to the bottom-left corner.

Two things cannot travel with it and are put away for the journey:

- **the material**, because it is a sibling view rather than a child and
  would sit there at full size while the panel shrank away from it. This
  costs nothing that was not already lost — an alpha below 1 switches the
  behind-window blur off anyway.
- **the shadow**, which the window server derives from the window's alpha
  channel and caches, so it would keep the silhouette of a full-size panel
  around a small one.

**Where the item is** comes from `QSystemTrayIcon.geometry()`, and that is
the status item's own button window: measured against
`NSStatusBarWindow.frame()` in the same process, the two agree exactly
once Cocoa's bottom-left origin is taken out — `(1159, 1073, 38×34)` in
Cocoa is `(1159, 0, 38×34)` here. A pyobjc route beside it would be a
second source of truth for one rectangle.

**When there is no item to fly to** — behind the notch, in an overflow, on
a display that has just been unplugged, or no menu bar at all — the window
fades in place. That is the same function with no target rather than a
second code path, because a fallback nobody exercises is a fallback that
does not work.

**Nothing is left behind.** The flight borrows the window's position, its
opacity and its content scale, and one method gives all three back — so an
interruption, a landing and a shutdown all leave the window in the same
state. Pressing the hotkey twice quickly reverses the journey from
wherever it had got to, in proportionally less time, rather than queueing
a second one. Quitting mid-flight lands the window *before* the settings
are saved, or the menu bar's corner would be persisted as where the user
left it.

One ordering was found rather than chosen: the position goes back
**before** the window is hidden. Moving a window that has just been hidden
is undone by the platform's own move event for the last position it
actually had, and with `_flight_home` already given up that is how a
window ends up somewhere nobody put it. Restoring while it is still on
screen has no such race and nothing is seen — at that point it is at zero
opacity and a sixteenth of its size.

The window never activates the app on the way: it is unfocusable by
design, and the flight only ever moves, fades and scales it. Startup does
not fly — `apply_saved_visibility` is the app arriving, not the user
asking for the window back.

## The menu bar glyph says what is happening

The item is the only part of this app that is always on screen, so it is
the natural place to say — quietly, without being read — whether anything
is going on. **Three states, and the number is the point.** A menu bar
icon is 16 points tall and shares a strip with a dozen others; the eye
takes it in without focusing, or not at all, so every state past the third
is a distinction nobody can make at that size.

| | |
|---|---|
| **idle** | the glyph at 40% ink. Nothing playing, or the lyrics hidden. |
| **active** | the glyph at full strength: lyrics up, following a song. |
| **practice** | the glyph with a dot: a loop, an echo pass or a sync pass. |

They differ in the two ways that survive being small: **how much ink there
is**, and **whether there is a mark that is not usually there**. Idle is
the active glyph with less of it rather than a different drawing — one
shape at two strengths is one icon doing more or less; two shapes would be
two icons.

Practice outranks everything, including the window being hidden: a sync
pass or an engaged loop keeps running with the lyrics hidden, and there
the menu bar item is the only evidence it is still going. Hiding the
window otherwise dims the glyph, which quietly makes the menu bar the
confirmation that ⇧⌘J landed — useful for a keypress whose whole effect is
that something disappears.

All three are **template images**: solid black with the shape in the alpha
channel, so macOS tints them for a light or dark menu bar and the idle
one's lower alpha comes through as a dimmer glyph rather than a grey one.
That is also why the practice state is a *dot* and not a colour — a
coloured menu bar icon stops following the menu bar. **Nothing animates**:
a moving menu bar icon is a thing to look at, and this is a thing to
notice.

The icon is set only when the state *changes*. The refresh runs on every
render, three times a second, and handing the same icon back to an
`NSStatusItem` that often is the menu bar item being rebuilt under the
user — the flicker the shared menu is built once to avoid.

## Open at Login

Uses `SMAppService.mainApp`, not a `LaunchAgent` plist, because the menu
entry has to show what the **system** thinks. A plist stays exactly as
written after the user disables the item in System Settings, so a menu
built on it would keep claiming the app starts at login when it no longer
does.

The cost is macOS 13 (the app's floor is 11); below that the entry is not
offered at all, which beats a fallback that cannot describe itself
honestly on the very systems it would serve.

**The tick never comes from the stored preference.** The setting records
what the user asked for; `status()` is re-read at startup and on every
menu opening, and the tick follows macOS. The two are only ever compared
in order to log the disagreement.

`REQUIRES_APPROVAL` is not enabled — registration can return no error and
still not start the app at login. So enabling re-reads the status instead
of trusting its own return, reports failure, and leaves the entry unticked
with a label naming System Settings. An unticked box that explains itself
beats a ticked one that is wrong until next login.

Measured, not assumed: a freshly built bundle reports `NOT_FOUND` (3)
before it has ever been registered, registers cleanly under an ad-hoc
signature with no approval step, and reports `NOT_REGISTERED` (0) after
being switched off. So `NOT_FOUND` is an ordinary "off", and the entry is
still offered from it.

Every native call goes through one accessor, so the test suite has one
door to shut. A stray call would leave a real login item on whoever ran
the suite.

## Settings

`QSettings` is **injected** into the window, not configured globally.
`QSettings.setDefaultFormat` / `setPath` are process-wide and silently do
nothing on macOS — trusting them meant tests writing into the real user's
preferences.

The bundle identifier *is* the settings contract: `com.lyrisync.lyrisync`
is what `QSettings("lyrisync", "lyrisync")` already resolves to, so the
bundled app and a terminal run share one plist. Verified by launching both
at once and reading the window geometry back — same position, same size,
same file.

There is **no appearance setting**. macOS already answers that question,
and a toggle would be a second source of truth for it — the same argument
as the login item's tick following the system.

## Shutdown

A `QThread` destroyed while still running is a `qFatal`: the process
aborts with "QThread: Destroyed while thread is still running" and names
whichever test was on screen, which is how a shutdown bug once came to
look like a bug in the quit test.

So shutdown drains everything the window owns — the hotkey first, then the
monitor thread, then the worker pool — before anything of the window's is
destroyed.

Its waits are **bounded and honest**. A worker that will not return in 3
seconds (`say` can hold a line for a minute) is logged and left to the
thread pool's own destructor, which is where it was always going to be
dealt with. Blocking quit on it would be worse than the warning.
