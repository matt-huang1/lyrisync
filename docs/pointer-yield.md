# Getting out of the pointer's way

*Off · Dodge · Ghost. What the window does when your hand arrives.*

The opacity gesture — Option and a scroll over the window — has been the
answer to "you are in front of the thing I am doing" since milestone 4.
It works. It is also almost never reached for, and the reason is not that
it is hard: it has to be remembered, aimed and then undone, which is three
deliberate acts for a problem that lasts about two seconds.

This layer is the same wish answered without being asked. The pointer
arriving over the window **is** the request; the setting says what the
window does about it.

| | |
|---|---|
| **Off** | the default, and off means the pointer is not watched at all |
| **Dodge** | the window vacates its rectangle and comes back when you leave |
| **Ghost** | the window fades to 0.15 and lets the clicks through |

Neither behaviour is a better version of the other. Dodge is for a window
parked over something you **look at**; Ghost is for one parked over
something you **click**. Ghost borrows nothing that has to be handed back
except a number, which is the argument the [notification
yield](notification-yield.md) makes for fading rather than moving; Dodge
is the case that argument does not cover, and it pays for the move by
holding on to where the window belongs.

The rules are all in `proximity.py`, which is pure and Qt-free like every
other rules module in the app. `window.py` hands it two rectangles and a
pointer and does what it is told.

## Why this is a poll, and why it is the same poll

macOS delivers enter, leave and mouse-moved events only to the **active**
application. This one runs under the accessory activation policy with a
window that refuses focus, so it is never active — measured in milestone
18 by driving the real pointer onto the real window and watching `hovered`
stay stubbornly False. What still answers is the pointer's own position,
which is a screen coordinate and belongs to nobody.

Ghost makes that load-bearing twice over. A window that ignores mouse
events would not hear an enter event even if one were coming, so the thing
that notices the pointer **leaving** is the one thing the click
pass-through cannot switch off.

Two layers want the pointer now — the compact layout's controls coming out
from under it, and this — and they share one timer and **one reading per
tick**. Two readings could differ, because the pointer is entitled to move
between them, and the two layers would then be acting on different answers
to one question.

### What it costs

Measured on this machine, 200,000 iterations each:

| | |
|---|---|
| `QCursor.pos()` | 0.309us |
| the poll before this layer (position + frame test) | 0.714us |
| the poll with the region test | **0.973us** |
| working out where a dodge goes | 0.852us, once per arrival |

At 100ms that is **0.00097% of one core**, of which this layer added
0.00026%. The timer runs only while the window is showing and one of the
two layers wants it, which is the cheapest half of "poll only as often as
needed": the difference between polling and not polling at all.

## The region, and the hysteresis

The trigger region is anchored on **where the window belongs**, not on
where it currently is. That single decision is what makes Dodge possible:
a region that followed the window would report "clear" one poll after
reporting "covered", every time, for ever. The window flees the pointer,
the pointer is therefore no longer on it, the window comes home, the
pointer is on it again.

Leaving is a wider question than arriving and takes **both** rectangles:
the pointer must be clear of where the window belongs and of where it
actually is, each by the release margin.

```
arriving   ->  the home rectangle, exactly
leaving    ->  home OR current, each grown by RELEASE_MARGIN
```

The second half is what makes a dodged window catchable. Without it,
following the window would take the pointer out of the home region, the
window would come home, and it would arrive where the pointer no longer
is: a window you can see and can never click. With it, following it is not
leaving it, and a press on the window you caught adopts wherever it stands
as the new home.

### The 12 points, and what was measured

The band is **set by eye**, and this page says so rather than dressing it
up. What was measured is what it is *not* for: sampled at the poll's own
100ms for 30 seconds with nothing touching the trackpad, the pointer
reported the identical coordinate **300 times out of 300**. There is no
jitter to absorb, and a band of zero would not flap on a resting hand.

It exists for the deliberate small movement — a pointer parked on the
window's edge while its owner reads something — and for that there is no
threshold to find, only a distance that reads as "you have left" rather
than as "you twitched". The cost is one poll: a pointer crossing 12 points
at 120 points a second spends 100ms inside the band, which is the interval
the window is asking at anyway.

The same number is the dodge's **clearance**, because it is the same
question asked in the other direction. A window that stepped aside by
exactly its own height would put its new edge on the pointer's old
position, which is inside the region it just left.

## Where a dodge goes

It **vacates the whole footprint**, rather than sliding just far enough to
uncover the pointer. Sliding the smaller amount answers the literal
question and nothing else: what somebody reaches into a window for is the
thing the window is on top of, which is a region and not a point, and a
strip that dropped fifteen points would still be over most of it. Ten
points of travel with the thing you wanted still hidden is worse than not
moving at all, because it looks like the feature worked.

Four candidates, each clearing the footprint with the clearance gap,
ordered by travel. The **axis falls out of the window's own shape** and is
named nowhere:

| window | up/down costs | left/right costs | steps |
|---|---|---|---|
| a strip, 900 x 40 | 52 | 912 | vertically |
| the full layout, 460 x 200 | 212 | 472 | vertically |
| a tall narrow one, 200 x 600 | 612 | 212 | sideways |

Ties inside an axis resolve by a fixed order, because a window that
stepped up one song and down the next would read as random. Every
candidate is clamped like any other placement of this window, and one that
needed no clamping is preferred over one the screen dragged back, so a
window near an edge does not step half off it when the other direction was
free.

**Nowhere to go is an answer.** A window shuffled to a position still
under the pointer would dodge on every arrival and uncover nothing, so the
destination is `None` and the window stays put. It takes an absurd screen
to reach: clamping leaves a strip of the grab margin clear at each edge,
so `None` only happens when the available area is under two margins across
in *both* axes, about 80 points.

## A temporary position is never a permanent one

This is the [flight](menu-and-system-integration.md)'s rule arriving for
the third time. `_proximity_home` holds the window's real position for as
long as it stands aside, exactly as `_flight_home` does for as long as the
window is away at the menu bar, and `_home_pos()` is what asks:

- the save at shutdown, so `window/pos` is never a dodge;
- the per-app position learn, so the map never records where the app's own
  dodging put the window;
- `_resize_width_to`, because everything the width-anchoring rule cares
  about is a property of the home rectangle — whether it is docked, and
  where its centre is.

And the three things that move the real position while it is borrowed —
a remembered-position move, docking, the song changing the strip's width —
write the **home** and derive the standing-aside position again from it.
That is the same sentence `_dock_to_top` already contained for the flight.

A docked window therefore docks again exactly. It has to be exact:
`geometry.is_docked` recognises docking by the window **being** at the
docked position, so a pixel of drift would leave it drawn as a floating
panel from under the menu bar.

## Edge triggered, not level triggered

The behaviour **begins** on the pointer arriving and ends on it leaving,
and nothing restarts it in between.

That one rule is what makes every suspension below tolerable. A sync pass
starting over a dodged window brings the window home and leaves it there,
and when the pass ends with the pointer still on the window nothing steps
aside under a hand that is still using it. The pointer has to leave and
come back, which is a thing the user does rather than a thing that happens
to them.

`Approach` keeps two booleans and not one for exactly this reason: whether
the pointer is on the window is a fact about the pointer, and whether the
window may act on it is the gate. A suspension takes the second and leaves
the first alone.

## The suspensions

Three of them are one sentence — **the user needs to click this window**:

- **a tap-to-sync pass**, which is a rhythm game played on a button on
  this window, once per line. Ghosting it would send the taps through to
  whatever is behind it; dodging it would move the target mid-song.
- **an echo attempt**, which pauses the song and hands the turn over with
  the done button as the only way out. It is also the one state where the
  compact layout already holds its controls out whatever the pointer is
  doing, and this is that rule seen from the other side.
- **the failure register**, which is open because somebody clicked the ⓘ.
  The click that closes it is the same click, on the same control.

The other four are "something else owns this window": a hand on it (a drag
or a resize), a flight to or from the menu bar which owns both the
position and the opacity until it lands, a hidden window which is in
nobody's way, and the layer being off.

### What is not suspended, and why

**The window's own right-click menu is unreachable in Ghost.** A
right-click passes through to the app underneath. That is inherent to
click-through rather than an oversight, and the way in is the menu bar
item — the same answer the app already gives for a window that is hidden.

**A native menu on screen** is not suspended either. AppKit runs it on a
nested modal loop with no close notification to observe, and while it is
up the window is under the menu rather than under the pointer.

## The ghost's ceiling, and the number that had to be corrected

**0.15**, and it took two goes.

The first was 0.12, derived from the contrast arithmetic and never looked
at. Rendered over an editor it is indistinguishable from 0.10 — which is
to say, from nothing: no panel, no edge, no line. Principle 3 collecting
its fee for the fourth time in this project.

So the window was rendered at 0.10, 0.15, 0.20, 0.25 and 0.35 in both
appearances and the sheet was read. 0.10 is not there. 0.25 — which is the
window's own opacity floor, and so something the user can already drag to
by hand — is still plainly a window over somebody's code. **0.15 is where
a trace of the sung line survives and nothing else does**, which is "has
stepped back but can still be found".

The measurements were then taken to *check* it rather than to justify it,
the order milestone 12b settled on:

| | dark | light |
|---|---|---|
| of each pixel underneath, what the panel contributes | 13.6% | 13.9% |
| what survives untouched | 86.4% | 86.1% |
| what the work underneath keeps, worst backdrop | 15.56:1 | 15.63:1 |

The 4.5:1 floor this app holds anything read for could not have picked the
number: it permits opacities up to **0.51**, which is a window nobody
would call ghosted. Contrast headroom is not aesthetic headroom, again.

One ceiling serves both appearances, and that is measured rather than
assumed: the two agree within 0.3 percentage points of coverage at every
opacity from 0.05 to 0.25, because the dark panel over a white page and
the light panel over a black one are very nearly the same subtraction.

It lands on the same value as `notifications.YIELD_CEILING` and is
**deliberately not shared with it**. Two facts that happen to agree are
still two facts: that one is where a five-second banner needs the window
to be, this one is where a hand does, and either is free to move without
dragging the other along. One definition is a rule about one fact, not
about one number.

## Two more consequences worth naming

**A ghosted strip offers no controls.** Milestone 18 took a faded control
off the window entirely, because a widget at zero opacity is still a
widget under the pointer and an invisible thing that can be clicked is
worse than either state it is between. Its mirror is a control that can be
seen and not pressed, which is what every overlay button becomes the
moment clicks start passing through — so the reveal goes to zero for the
length of a ghost.

**Click-through is two switches for one state**, and they answer to two
different things. `WA_TransparentForMouseEvents` stops this window's own
widgets taking a press, and is the whole of the answer off Cocoa;
`setIgnoresMouseEvents_` is what makes the click land on somebody else's
app, which nothing inside Qt can do. Setting only the first gives a window
that ignores clicks without passing them on, which is the worst of both.
Verified by readback on a real `NSWindow`: False at rest, True while
ghosted, False handed back.

## Reduce Motion

Takes the travel and leaves the answer, which is the reading the [travel
to a remembered position](per-app-position.md) already gets. Dodge is
movement, but the layer is about where the window ends up rather than
about how it gets there, so it steps aside instantly and comes back
instantly. Ghost is a fade, and fades stay.

## How it was verified

The rules are tested headless — the region, the hysteresis, the edge
trigger, the destination, the gate, and the four things only a real window
can answer (a temporary position is never learned, never saved and never
survives shutdown; the song and the pointer do not both own the width; a
docked window docks again to the pixel; a strip steps aside too).

What a test cannot answer is whether it *feels* right, so both modes were
driven on the real platform with the real pointer, with the app
backgrounded, which is the only state it is ever in:

```
                home        stepped aside     back        exact
full layout     600, 400    600, 612          600, 400    yes    200 of height + 12 clearance
compact strip   528, 400    528, 491          528, 400    yes     79 of height + 12 clearance
docked strip    553,  34    553, 125          553,  34    yes     docked -> floating -> docked

Ghost           position unchanged throughout, opacity 1.000 -> 0.149 -> 1.000
                                                  (Qt quantises opacity to 1/255)
```

The docked row is the one worth reading twice: the window reports
`docked` while it is under the menu bar, `not docked` while it stands
aside — so it is correctly redrawn with four rounded corners rather than
squared off in the middle of a screen — and `docked` again the moment it
is back, which only holds because it came back to the exact pixel.
