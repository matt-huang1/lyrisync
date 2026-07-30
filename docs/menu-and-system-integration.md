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

Show on all desktops, [Remember position per app](per-app-position.md) and
[Yield to notifications](notification-yield.md) are always visible for the
same reason: each is a standing preference about the window rather than
about the song. Gating the last of those on a notification being up right
now would offer it for five seconds at a time, which is not a way to find a
setting.

[Compact and Dock to top](compact-and-docking.md) are the same argument a
fifth and sixth time, and they sit together for it: one is a standing
choice about the window's shape, the other a command about where it goes,
and both are answerable with nothing playing. Docking in particular is
what somebody reaches for while setting the window up, which is before
there is a song to gate it on. Dock to top is the one entry here that is
**not checkable** — it puts the window somewhere once, and nothing holds
it there, so there is no state for a tick to describe.

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

The item is the only part of this app that is always on screen, so it is the
natural place to say — quietly, without being read — whether anything is
going on. A menu bar icon is 16 points tall and shares a strip with a dozen
others; the eye takes it in without focusing, or not at all.

### Brightness and shape are independent

*Milestone 15.1.* Milestone 15 had three whole-glyph states — idle, active,
practice — and that conflated two different questions into one axis. A paused
song dimmed the icon exactly as hiding the window did, so the one thing the
dimming was **for** was indistinguishable from Spotify being paused.

Three properties now, each answering one question and nothing else:

| property | asks | says |
|---|---|---|
| **brightness** | is the lyrics layer on? | full, or 40% ink when the window is hidden |
| **shape** | is a song playing? | three bars of equal length for no, short / long / short for yes |
| **the dot** | is a practice mode running? | a mark in the corner for a loop, an echo pass or a sync pass |

**Nothing playing no longer dims anything.** Dimming means one thing — the
window is away — which makes the menu bar a reliable confirmation for a
keypress whose whole effect is that something disappears.

The playing shape is the window's own previous / current / next rows, with
the current one longest and thickest. The stopped shape is the same three
bars saying nothing about which line is current, because there is no current
line.

Practice still outranks the window being hidden: a pass keeps running with
the lyrics away, and there the item is the only evidence it is going, so
practice keeps the glyph at full brightness.

They **compose**, so eight combinations come from three booleans rather than
from eight drawings. That is only affordable because the glyph is now
**drawn** rather than loaded — at 15 there were three SVG files in
`sottovoce/assets/`, and this would have needed twenty. The bar thicknesses,
centres and the dot are the numbers those files carried; `menubar.py` owns
them and `symbols.py` paints them.

Still a **template image**: solid black with the shape in the alpha channel,
so macOS tints it for a light or dark menu bar and the dim glyph's lower
alpha comes through as a dimmer glyph rather than a grey one. That is also
why practice is a *dot* and not a colour — a coloured menu bar icon stops
following the menu bar.

### It updates live, from the monitor tick

Until 15.1 the only reliable caller was `aboutToShow`, so **the icon only
changed when the menu was opened.** The cause is worth writing down: the
refresh ran from `_render`, and a pause does not re-render, because
`player_state_changed` returns `False` for `PAUSED` — the display text is
unchanged, so there is nothing to draw. The item therefore claimed a song was
playing until somebody clicked it.

It is now refreshed from **every position update and every state change**.
`_refresh_menu` still calls it too, and that is not redundant: position
updates stop arriving the moment there is no track at all, so with Spotify
closed the tick is gone and hiding the window would have nothing to dim the
icon. The tick is the guarantee; the other callers are promptness.

The icon is set only when the spec *changes*. That now runs three times a
second, and handing the same image back to an `NSStatusItem` that often is
the menu bar item being rebuilt under the user — the flicker the shared menu
is built once to avoid. Each drawing is cached, so a change is a dictionary
lookup.

### An optional animation, tied to real line changes

Off by default, under **Animate the menu bar icon**. With it on, each time
the lyric line advances the three bar lengths step to the next of four
arrangements. Not a timer and not a loop: a moving menu bar icon is a thing
to look at, and this is a thing to notice — it moves when the song does, and
otherwise sits still.

The middle bar is the same length in every arrangement, so *the current line
is the longest* stays true of every frame the icon can ever show; only the
lines around it vary, which is what a lyric advancing actually looks like.
The first arrangement is the plain playing shape, so switching the layer on
changes whether the icon moves and not what it says.

Stepped from the one place a synced line lands on the window, and only when
the index actually differs — `_render` re-runs that for reasons which have
nothing to do with the song. The step is counted whether or not the layer is
on, so switching it on mid-song picks up where the song is.

Measured on a real menu bar: **0.020 ms of CPU per line change**, against the
92.7 ms one line change of the window already costs — 0.022% of it, and
0.0005% of one core at a line every four seconds. Drawing one glyph is
0.012 ms and happens once per combination; a refresh that changes nothing is
0.0005 ms.

### What was measured, and what is hard to tell apart

Rendered at 16 points on a light and a dark bar, and photographed on the real
menu bar beside the system's own icons. Six glyphs are reachable (practice
forces full brightness, so hidden-vs-shown collapses there), and the pairwise
fraction of the square that differs runs from 9.8% to 37%.

The **closest pair is stopped-and-hidden against playing-and-hidden** — 9.8%,
both at 40% ink with only the shape between them. It is the hardest call on
the strip and the one to be honest about. Every other pair has either a
brightness step or the dot to separate it, and stopped against playing at
full brightness (10.2%) reads clearly: a stack of equal lines against a
centred taper.

One defect was found by looking at the rendered sheet after the numbers had
all come back healthy: the dot **overlapped** the even shape's bottom bar by
half a unit, which at 16 points is not a mark beside a bar but a bar with a
blob on the end. The pairwise differences could not see it. The dot moved in
and shrank slightly, the even bars came down from 14 units to 12, and
`test_the_dot_never_touches_a_bar` does real rectangle-against-circle
arithmetic over every shape so it cannot come back.

### The glyph is drawn by an icon engine, and that was measured too

Handing `QSystemTrayIcon` a 44-pixel pixmap at `devicePixelRatio` 2 —
logically 22x22, exactly what the SVG it replaced was — put a **clipped**
glyph on the menu bar: two of the three bars and no dot.
`QIcon.availableSizes()` reports raw pixels and does not fold the ratio in,
so the status item took a 44-point image for a 22-point slot and drew its top
two thirds.

Four constructions were put on a real status item and photographed:

| construction | result |
|---|---|
| 44px at ratio 2 | clipped |
| 36px at ratio 2 | clipped |
| both 22px and 44px in one icon | clipped — Qt takes the larger |
| 22px at ratio 1 | whole, but upscaled by the compositor and soft |
| a `QIconEngine` drawing on demand | whole, and the crispest of the five |

Which is what the SVG engine had been doing all along: rendering at the size
actually wanted. `test_the_glyph_reports_the_size_the_menu_bar_wants` pins the
number the bug turned on.

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

The bundle identifier *is* the settings contract: `com.sottovoce.sottovoce`
is what `QSettings("sottovoce", "sottovoce")` already resolves to, so the
bundled app and a terminal run share one plist. Verified by launching both
at once and reading the window geometry back — same position, same size,
same file.

Which is also why renaming the app cost something. The identifier was
`com.lyrisync.lyrisync`, and macOS keys the file on it — changing it
orphans the old plist rather than moving it. `settings.py` copies it
across once, on a launch that finds nothing of its own, and leaves the
original where it is. What it cannot copy is in
[packaging](packaging.md#the-bundle-identifier-is-the-settings-contract):
the Automation grant and the login item are keyed on the identifier *and*
the signature, so both have to be granted again.

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
