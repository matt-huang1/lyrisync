# Yielding to notifications

*Milestone 16.*

The lyrics window floats at the status window level. A notification banner
is drawn *below* it — level 21 against the window's 25, measured in the
window list — so when a banner arrives over the same part of the screen,
this app is the thing covering somebody's mail.

That framing decides the whole feature. Nothing is competing for attention;
the window is simply in front of something it has no business being in
front of. So it **yields**: it fades to a low opacity while something is
over it, and comes back when the way is clear.

Off by default, like every layer, under **Yield to notifications** in the
menu bar item's menu. With it off, nothing is polled at all — off means the
app is not looking, not that it looks and ignores the answer.

## Fading, never moving

Moving the window out of the way would fight [per-app position
memory](per-app-position.md) for ownership of where the window lives, and
would then have to answer two more questions with no good answers: when to
move back, and where to. Fading borrows nothing that has to be given back
except a number.

## No permission is needed, and that was measured

The mechanism is `CGWindowListCopyWindowInfo`. The worry about it is real —
it belongs to the same family of calls that puts a Screen Recording prompt
in front of the user — and it turns out not to apply here.

Verified on macOS 26.5.2 with a throwaway ad-hoc-signed app bundle carrying
a bundle identifier that had never been granted anything, rather than from
the developer's own terminal, which inherits its editor's grant and would
have answered the wrong question:

| | |
|---|---|
| `CGPreflightScreenCaptureAccess()` before | `0` |
| `CGPreflightScreenCaptureAccess()` after | `0` |
| windows returned | 166 |
| `kCGWindowOwnerName` / `OwnerPID` / `Layer` / `Bounds` | all present |
| `kCGWindowIsOnscreen` | present whenever true |
| `kCGWindowName` | **absent for 160 of 166** |
| prompt shown | none — no new on-screen window for 10s |
| TCC log naming the probe | none |

One field is withheld, and it is the one that would deserve a prompt:
`kCGWindowName`, the window's *title*. Reading the titles of other
people's windows is precisely the thing worth asking about, and this app
has no use for them — it needs to know that a notification is on screen,
not what it says. Two tests enforce that: the module may not mention
`kCGWindowName`, and it may not mention any screen-capture entry point
either, `CGWindowListCreateImage` included.

The probe bundle was deleted afterwards, and it left no TCC entry behind.

## Keyed on the bundle identifier, never the owner's name

`kCGWindowOwnerName` is **localised**. It reads "Notification Centre" on an
en-GB machine and "Notification Center" on a US one. A string match on it
would work for whoever wrote the code and quietly never fire for half the
people who ran it — the worst failure shape available, a layer that is
simply off, silently, for reasons nobody can see.

So the owner's process identifier is resolved to a bundle identifier
through `NSRunningApplication`, which is not localised and is what the rest
of the app already keys apps on. A test asserts the owner-name key never
appears in the module.

`com.apple.controlcenter` is deliberately **not** in the set, and it is the
trap this design walked into first: Control Centre owns *eleven*
permanently on-screen windows, one per menu bar item. An app that yielded
to it would fade on its first poll and never come back. There is a test
for that too.

## One window covers both cases

Banners and the Notification Centre panel are the same window — number
54338 in the run this was measured in, always present in the full window
list, flipping `kCGWindowIsOnscreen` from absent to true for the five
seconds a banner is up and for as long as the panel is open. Opening the
panel by clicking the clock and closing it again was measured the same way.

So there is one rule and not two.

## The rectangle is the whole display — and nothing tells the cases apart

*Milestone 16.1.*

The rectangle that window reports is **the whole display**: `(0, 0
1710x1107)` on a 1710x1107 screen, both for a banner in the top-right
corner and for the panel. macOS exposes no rectangle for the banner itself.

Milestone 16 shipped the intersection against that rectangle and it was
wrong in practice: **the window dimmed for a banner it was nowhere near.**

Before working around it, 16.1 went looking for a real signal. Every field
of the notification window, dumped in three states and compared:

| field | nothing showing | banner | panel |
|---|---|---|---|
| `kCGWindowNumber` | 54338 | 54338 | 54338 |
| `kCGWindowLayer` | 21 | 21 | 21 |
| `kCGWindowBounds` | 0,0 1710x1107 | 0,0 1710x1107 | 0,0 1710x1107 |
| `kCGWindowAlpha` | 1.0 | 1.0 | 1.0 |
| `kCGWindowSharingState` | 1 | 1 | 1 |
| `kCGWindowStoreType` | 1 | 1 | 1 |
| `kCGWindowMemoryUsage` | 2368 | 2368 | 2368 |
| index in the on-screen list | 116 | 116 | 116 |
| `kCGWindowIsOnscreen` | *absent* | true | true |
| windows owned, on screen | 0 | 1 | 1 |
| the set of keys returned | — | identical | identical |

**A banner and the open panel are identical in every single field.** The
window count does not change, the ordering does not change, and
`kCGWindowMemoryUsage` is 2368 even with nothing on screen — so it is not a
backing-store size and says nothing about what is drawn. The only signal in
the entire record is `kCGWindowIsOnscreen` appearing, and it says *something*
is showing, never what or where.

So there is no pairing of a case with its region to be had, and the only
remaining option is a heuristic.

## The heuristic: the rightmost strip

**This is a heuristic.** It is not read from anything; it is a constant, and
where notifications appear was measured rather than assumed.

Measured from pixels — legitimately, because this happened *once, in a
harness that already had Screen Recording*, and the app ships the answer as
a number instead of looking. Screen captures with the notification up were
diffed against captures without it, five trials per case with the masks
intersected so that anything moving for its own reasons drops out:

| case | rectangle, in points | width | right edge |
|---|---|---|---|
| one short banner | 1349, 54  346x62 | 346 | 1695 |
| a long wrapped banner | 1343, 44  360x120 | 360 | 1702 |
| three stacked banners | 1340, 38  368x96 | 368 | 1708 |
| the Notification Centre panel | 1294, 34  416x608 | 416 | 1710 |

Every case is right-anchored and inside the rightmost **416 points**. The
panel is the widest; its height depends on how much is in it — 608 to 713
points measured, and it scrolls beyond that, so there is no maximum height
to find.

The region is therefore **the reported rectangle, narrowed to its rightmost
`PLAUSIBLE_STRIP_WIDTH` points, full height**. That constant is **440**, not
the measured 416: banner and panel widths move with the system text size and
with localisation, and the two failure directions are not worth the same.
Too wide fades the window when it did not strictly need to; too narrow
leaves it sitting over somebody's mail, which is the whole thing this layer
exists to stop.

Narrowing *the reported rectangle*, rather than asking a screen where its
right edge is, keeps milestone 16's one real property intact: the rectangle
being narrowed is the display the notification is on, so a banner on another
display still cannot reach a window over here. And the narrowing uses `min`,
so a reported rectangle already narrower than the strip is handed back
untouched — the day macOS reports a real banner rectangle, this stops being
a heuristic without an edit.

The narrowing happens **inside `in_the_way`**, not at the call site, so there
is one path from a reported rectangle to an answer and no version of this
that forgets and compares against a whole display again.

**What it gets wrong, plainly:** a window parked in the bottom-right corner
still fades for a banner in the top-right one. The panel can reach that far,
nothing distinguishes the panel from a banner, and full height is the honest
over-approximation. Over-approximating is the right direction here — a layer
whose job is to get out of the way should fail by moving when it needn't,
not by sitting there when it should have moved.

Verified live: with the window at `(20, 400)`, well clear of the strip, a
real banner sat on screen for 6.4 seconds and the window's opacity stayed at
1.000 for every one of the traced frames. With the window at `(1280, 60)`,
inside the strip, it faded as before.

## Why the fade cannot be proportional

Fading in proportion to how much of the window is actually covered — or to
how far the Notification Centre panel has been pulled open — **is not
implementable without pixel capture**, and pixel capture is precisely the
permission this feature exists without.

A proportional fade needs the real rectangle. macOS reports the display, and
the investigation above establishes that nothing else in the window record
narrows it. The only public route to the real geometry is reading the
notification window's pixels — `CGWindowListCreateImage` and its successors
— which is what the Screen Recording prompt guards, and which a test in this
project forbids the module from even naming.

So the choice is a proportional fade behind a permission dialogue, or a
fixed fade behind none. This picks none. The fixed ceiling is the price, and
it is a smaller one than the prompt would be.

## One coordinate space

Qt's geometry and the window list's bounds are the same coordinate space,
which is not something to assume — milestone 12a found Qt's geometry
disagreeing with the screen by the menu bar height. Checked in one process,
for one window: Qt's `frameGeometry()` said `(1240, 40, 460, 200)` and
`CGWindowListCopyWindowInfo` said `(1240, 40, 460, 200)`. `dx=0, dy=0`, so
there is no conversion in the code.

## How faint, and for how long

The ceiling is **0.15**, and it is an absolute ceiling on opacity rather
than a factor to multiply the user's setting by. A factor would send a
window already dimmed to the floor down to 0.04 — five per cent of a
window, which reads as having closed. A ceiling means the same destination
whatever the user set, and the fade simply has less to travel.

The rule is `min(theirs, this)`, so yielding is never *brighter* than what
was asked for. With the window's own opacity floor at 0.25 the ceiling sits
below anything the user can choose, so the `min` never picks their side
today — which is exactly why the property is pinned in a test rather than
left to the floor staying where it is.

The fade is **260ms** each way, `InOutSine` — one phase of a line change,
the same duration as the travel to a remembered position and the flight to
the menu bar, because this window should only have one sense of how fast it
moves. `InOut` rather than a paired In/Out because this is one continuous
movement, which also means a fade that reverses mid-way reverses
symmetrically. An interruption retargets from the level the window actually
reached and pays for the distance left.

## What the ceiling was measured against

The value is set **by eye**, at the point where the window has plainly
receded without appearing to have gone. The measurement came afterwards to
check it rather than to justify it — the order milestone 12b settled on
when contrast headroom turned out not to be aesthetic headroom.

From real pixels, a real banner, the window over it in the top-right corner
where banners land:

| | full opacity | yielded (0.149) |
|---|---|---|
| interference from the bare banner, mean \|channel diff\| | 132.7 / 255 | **20.2 / 255** |
| the banner's separation from the app behind it | 1.50:1 | **7.63:1** |

The banner alone measures 9.91:1 against what is behind it, so yielding
recovers most of what makes it read as a floating object at all.

**One correction worth keeping**, because the obvious assumption is wrong.
The banner's *own* text contrast is never the casualty. Cropped to the
banner's interior — ink against its pale body — it measures 8.32:1 alone,
8.98:1 yielded and **12.90:1 under a fully opaque window**. It *rises*,
because this app's pale panel brightens the banner's pale body while its
dark ink barely moves. Swept across every ceiling from 0 to 1 it never
approaches 4.5:1.

The first version of that measurement claimed the opposite, and reported
the banner going illegible at 1.50:1 under a fully opaque window. It was
sampling the whole of the window's rectangle, where the majority of the
pixels are the dark app behind rather than anything the banner drew, so
"text" was the banner's pale body and "background" was the editor. **Looking
at the capture is what found it.** The number was right about something;
the label on it was wrong. No legibility threshold picks this ceiling, and
claiming one did would be inventing a measurement that says something else.

What full opacity actually costs, visible in the capture rather than in any
number: the window's own lyrics are drawn across the same space as the
banner's text, and the banner loses its edge against everything around it.

## Polling, and what it costs

There is no signal to subscribe to. macOS broadcasts application
activations, which is why [per-app positions](per-app-position.md) needs no
polling at all, but it says nothing when a banner appears. So this is the
one thing in the app that genuinely has to ask.

The monitor's own 300ms tick was the alternative and it cannot carry this:
position updates stop arriving the moment nothing is playing, which is
exactly a moment when the window is still on screen and still in the way. A
layer that worked only during playback would be milestone 14's lesson
again — a feature indistinguishable from a broken one.

So it is a repeating `QTimer`, running only while the layer is on *and* the
window is showing. Measured in the real app process: **0.105–0.126 ms of CPU
per poll**, against 2.3% of a core for the line change at a line every four
seconds. `kCGWindowListOptionOnScreenOnly` rather than the whole list is most
of that — 0.123 ms against 2.227 ms for every window on the machine.

### Two intervals, because the two directions are not worth the same

*Milestone 16.1.* Going away late costs nothing: the banner has only just
arrived and nobody has read it yet. Coming back late is the user waiting for
their own lyrics. So the interval is **300 ms while nothing is over the
window and 100 ms while the window is faded**:

| | interval | CPU |
|---|---|---|
| idle | 300 ms | 0.042% of one core |
| yielded | 100 ms | 0.126–0.151% of one core |

The faster rate is only paid for during the few seconds a notification is
actually up. Not lower than 100 ms because the 260 ms fade dominates the
restore — halving again would buy 50 ms off a ~360 ms total for twice the
polling.

"Yielded" means **the target or the level**: the rate stays fast until the
window is actually back, not just until the banner has gone. That was found
in the trace rather than reasoned about — the interval column read 300 next
to a level of 1.0, which meant a second notification arriving inside the
260 ms fade home met a still-faint window at the idle rate.

### Restore latency, measured

Traced by an independent 20 ms timer, so the figure does not depend on the
app's own polling. Six trials with real notifications:

```
t=1.10s  notification appears
t=1.26s  fade begins            (159 ms after it appeared)
t=1.50s  fully yielded          (241 ms of travel)
t=6.41s  notification vanishes
t=6.46s  fade home begins       ( 44-156 ms after it vanished)
t=6.71s  fully restored         (289-395 ms after it vanished)
```

- **restore begins 44–156 ms** after the notification vanishes — one 100 ms
  poll, with QTimer's usual jitter either side
- **fully restored in 289–395 ms**, mean 342 ms

Milestone 16, at a flat 300 ms, measured **430 ms** for the same thing, with
the fade home starting 190 ms after the notification cleared. The theoretical
worst case went from 560 ms (300 + 260) to 360 ms (100 + 260); the measured
395 ms high water mark is that plus timer jitter.

## The cases that are refused, and why

Every refusal names itself, from the same rule that decides it, the shape
[per-app positions](per-app-position.md) settled on:

- **the layer is off** — nothing is polled
- **the window is hidden** — a hidden window is in nobody's way, and the
  poll stops rather than answering into the void
- **a sync pass is in progress** — the user is tapping a button on this
  window once per line, and a decorative feature does not get to fade an
  essential one under them. A banner during a pass is simply covered
- **the window is mid-flight** — a hide or show is already animating the
  same opacity towards the menu bar, and a window on its way out has
  nothing to get out of the way of

The refusal is asked before every poll and it is not only a gate: a pass
that starts, or a window that is hidden, while the window is already faded
has to hand the opacity back, or the window would be left faint for a
banner that has long gone.

## One place decides how solid the window is

Three things now have an opinion about the window's opacity and they
compose rather than compete: the user's own setting is the baseline, a
yield takes it down towards the ceiling, and a flight scales whatever is
left as the window leaves for the menu bar.

Every one of them used to call `setWindowOpacity` directly, which worked
only because no two of them were ever true at once — and a fade to a
notification landing during a flight would have been the first pair. They
now all go through one multiply.

Hiding gives the yield back **before** the flight borrows the opacity. The
flight restores its own factor and not this one, so a window that went away
faded would have come back faded.

One consequence, and it is the same trade the opacity gesture already
makes: the behind-window blur only renders while the window's alpha is
exactly 1, so a yielded window has no frost. That is correct here —
spending the material is the *point* while something else needs the space.

## What is not here

- **Moving the window.** See the top of this page.
- **Yielding to anything else.** Full-screen video, other apps' floating
  panels, screen sharing — all plausible, none measured, and each would
  need its own answer to "whose window is that". The set is one bundle
  identifier for now.
- **A different ceiling per appearance.** The interference depends on the
  panel and the backdrop, and light mode over a dark banner is already the
  worst pairing of the two.
- **A fade proportional to how much is covered.** Not a judgement call —
  [not implementable without pixel capture](#why-the-fade-cannot-be-proportional),
  and pixel capture is the permission this feature exists without.
- **Telling a banner from the panel.** Measured impossible: identical in
  every field of the window record. That is what forces one region for both,
  and the bottom-right corner case with it.
