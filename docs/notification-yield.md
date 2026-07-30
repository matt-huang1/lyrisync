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

## What "overlaps" can honestly mean

The rectangle that window reports is **the whole display**: `(0, 0
1710x1107)` on a 1710x1107 screen, both for a banner in the top-right
corner and for the full-height panel. macOS exposes no rectangle for the
banner itself. The banner is drawn inside that host window, and the only
public way to find out where would be to capture its pixels — which is
exactly the thing that needs the permission this design avoids.

So the intersection is computed against the rectangle the system actually
reports, and what that buys today is the display test: a notification on
the built-in screen does not fade a window parked on an external one. On a
single display it means any notification while the window is showing.

That is stated rather than dressed up. The alternative was worse: a banner
rectangle guessed from where banners usually appear would be a number
picked by eye, in a project where the scrim alpha and the tint chroma are
not — and it would be wrong the first time Apple moved them. The
intersection is real arithmetic, so if macOS ever reports a tighter
rectangle this narrows with it for free.

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

So it is a repeating `QTimer` at **300ms**, running only while the layer is
on *and* the window is showing. Measured in the real app process:

- **0.105 ms of CPU** per poll (0.309 ms wall)
- **0.035% of one core** at 300ms

Against 2.3% of a core for the line change at a line every four seconds.
`kCGWindowListOptionOnScreenOnly` rather than the whole list is most of
that: 0.123 ms against 2.227 ms for every window on the machine.

A banner is on screen for about five seconds, so 300ms starts the fade well
inside the first tenth of its life. Traced live, one real notification:

```
t=1.12s  notification appears
t=1.21s  fade begins            (level 0.009, opacity 0.988)
t=1.45s  fully yielded          (level 1.000, opacity 0.149)
t=6.44s  notification clears
t=6.63s  fade back begins
t=6.87s  fully restored         (level 0.000, opacity 1.000)
```

90ms and 190ms from the change to the fade starting — inside one poll
interval both ways — and 240ms of travel against the 260ms nominal.

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
