# The menu, and living in the system

## One menu, two ways in

*Milestone 21.* The menu bar item's menu and the window's right-click menu
were **literally the same `QMenu` object**, on the argument that two menus
could drift apart and one cannot. The object was one; the **appearance was
two**. Qt hands a system tray's menu to macOS, which converts it into a
real `NSMenu`, so the menu bar item got the system's own drawing: its font,
its check marks, its separators, its submenu timing. The same object popped
up under the pointer is drawn by Qt's widget style instead. Same entries,
same order, two different menus depending on how you opened it.

So the menu is now a **model**, and one native `NSMenu` drawn from it serves
both routes:

| | |
|---|---|
| `menu.py` | the model: the entry tree, the labels, the gating, the live state, and the one place a click lands. Pure — no Qt, no Cocoa. |
| `nsmenu.py` | the drawing: the only place in the app that says `NSMenu`, `NSMenuItem` or `NSStatusItem`. One door, `_appkit()`. |
| `window.py` | the wiring: which handler each entry has, and what the state is. |

`setMenu:` on the status item covers the first route.
`popUpMenuPositioningItem:atLocation:inView:` covers the second, and it
works **from an accessory app that never activates** — verified by
screenshot before any of it was written, because a menu that needed the app
to come forward would have been the end of the idea. The point makes one
trip across the coordinate line on the way (`geometry.cocoa_point_from_qt`):
Qt measures down from the top left of the primary screen and AppKit up from
its bottom left.

**The menu bar item is ours now.** Qt owns the `NSStatusItem` it makes and
there is no supported way to hand it a menu of your own, so the item that
carries one `NSMenu` has to be an item this app made. That also means it is
one this app has to **give back**: `_shutdown` removes it, where Qt used to
destroy it with the widget.

Its structure is built once and never rebuilt. Refreshing only flips
hidden, check marks, chosen presets and two labels — rebuilding would make
the native menu bar item flicker every time anything changed. The one
exception is the remembered-apps list, whose entries **are** data, and which
is assembled only while somebody is looking at it (`menuWillOpen:`).

**Nothing checks or unchecks an entry from a click.** The handler changes
the app's state and the refresh that follows says what the state now is, so
`Menu.trigger` hands a toggle the state it is moving *to*. A tick that
moved itself would be a second answer to what the setting is.

What holds that up is that the tick has exactly **one writer**.
`NativeMenu.apply` is the only thing in the app that calls `setState_`, and
it reads what to write off the model; the click path — `fire_` → `_fired` →
`Menu.trigger` → the handler — never touches `_checked` at all, it only
*reads* it to work out which way the toggle is going. A structural test
pins the writer down, because a second `setState_` somewhere else would
pass every behavioural test while quietly reintroducing the bug.

It used to be written here that the rule was "why the `QMenu` entries were
connected to `triggered` rather than `toggled`", and that was the wrong
reason twice over. It names a mechanism this app no longer has — the
`QMenu` is gone, and AppKit never moves an `NSMenuItem`'s state on its own
the way a checkable `QAction` does. And it was not what that connection
bought even at the time: a `QAction` flips its own tick on a click whichever
signal you listen to, so `triggered` was never what stopped the tick moving
itself. What it avoided was the *refresh* re-entering the handlers, because
`toggled` also fires when `setChecked` is called from our own code.

Which entries are visible is still pure logic in `menu.py`, tested without
Qt — and so, now, is everything else about the menu except its pixels.

## The shape of it

Seventeen entries in one flat column had become a list to read rather than
a menu to use. They are grouped by what they are *about*:

```
Show lyrics                     ✓
────────────────────────
Compact                         ✓      what is on screen
   Compact text size      ▸             (inside the strip only)
   Fit the width to the song    ✓       (inside the strip only)
Album colour                    ✓
────────────────────────
Romanisation                    ✓      this song
Spoken reference                ✓
   Speech rate           ▸
Echo practice                   ✓
Sync this song
────────────────────────
Position                 ▸             where the window goes
System                   ▸             how the app sits in the system
────────────────────────
Quit
```

With every layer dormant that is **four rows and two submenus**, against
eight entries and a separator before.

Two rules hold it together:

- **Nothing that comes and goes with the song is buried in a submenu.** An
  entry that has just appeared because it can now act is one somebody is
  looking for, and a submenu is one more click and one more place to look.
  So the learning layers stay at the top level and hide themselves, exactly
  as they did.
- **A setting's detail sits directly under its own switch.** Compact text
  size and the fit follow Compact; Speech rate follows Spoken reference.
  Grouping them elsewhere would separate a switch from the thing it
  switches.

`Position` is *Dock to top*, then per-app memory: the toggle, the readout,
the remembered apps and forget-all. One submenu, one question — where does
this window sit. `System` is Spaces, the notification yield, the menu bar
animation, then Open at Login: how the app behaves in somebody's machine
rather than in their song.

A submenu with nothing visible inside it is **hidden**, like any other entry
that cannot act, and separators collapse inside a submenu exactly as they
do outside one.

## A row can state a fact without being a control

The remembered-apps list is a list of things that *have* been learned. When
per-app forget was removed the rows became disabled `QAction`s, and macOS
greys a disabled item — so four remembered apps read as four things that
were *unavailable* rather than as four facts. A `QWidgetAction` fixed it.

The native menu needed the same fix again, and the obvious route does not
work: an **attributed title with an explicit `labelColor` is still drawn
grey**, because AppKit dims a disabled item when it draws it whatever the
string asked for. Measured, on a real menu. So the rows are `NSMenuItem`s
with a **view** — an icon well and a label — which AppKit draws at the
ordinary text colour, does not highlight, and does not treat as a control.
The same answer in the native idiom.

The position readout above them stays a plain disabled item, and that is not
an inconsistency: one grey line among ticked entries reads as a note, and
four of them read as a broken feature.

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
fifth and sixth time: one is a standing choice about the window's shape,
the other a command about where it goes, and both are answerable with
nothing playing. Docking in particular is what somebody reaches for while
setting the window up, which is before there is a song to gate it on. They
no longer sit next to each other — milestone 21 put docking with the rest
of "where does the window go" — and that argument is untouched by the
move: it was about visibility, and both are still always visible. Dock to
top is the one entry that is **not checkable** — it puts the window
somewhere once, and nothing holds it there, so there is no state for a
tick to describe.

**Fit the width to the song** follows the compact layout, because that is
the only place it means anything: the full layout's width is the user's
and stays theirs. It is the one entry in the app whose own default is ON,
and it can be, because it is reachable only from inside a layout that is
itself opt-in and default off. A default-on setting has to be unreachable
from the plain window, not merely quiet in it.

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

**Where the item is** is the status item's own button window, flipped into
Qt's coordinates by `geometry.qt_rect_from_cocoa`. It used to come from
`QSystemTrayIcon.geometry()`, and the two were measured against each other
in the same process while both existed: they agree exactly once Cocoa's
bottom-left origin is taken out — `(1159, 1073, 38×34)` in Cocoa is
`(1159, 0, 38×34)` here. With the item now ours this *is* the one source of
truth for the rectangle rather than a second one beside Qt's.

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

*Milestone 21 removed the `QIcon` from the path and kept the lesson.* The
glyph is PNG bytes at `points × ratio` pixels, and `nsmenu.py` labels the
`NSImage` with the point size the menu bar wants. Read back off a real
status item: `image.size()` is `22.0 × 22.0` and its representation is
`44 × 44` pixels, in a button of `38 × 22` points. Photographed from the
button's own drawing — three bars, short / long / short, nothing clipped,
tinted white by macOS because it is still a template image.

Four constructions were put on a real status item and photographed:

| construction | result |
|---|---|
| 44px at ratio 2 | clipped |
| 36px at ratio 2 | clipped |
| both 22px and 44px in one icon | clipped — Qt takes the larger |
| 22px at ratio 1 | whole, but upscaled by the compositor and soft |
| a `QIconEngine` drawing on demand | whole, and the crispest of the five |

Which is what the SVG engine had been doing all along: rendering at the size
actually wanted.
`test_the_glyph_is_drawn_at_the_screens_scale_and_labelled_in_points` pins
the number the bug turned on.

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
