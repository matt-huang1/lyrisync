# The compact layout, and docking to the top

Two things that are about the window's shape and where it sits, rather
than about the song: a layout that reduces the window to the line being
sung, and a command that puts it under the menu bar.

Both are in the menu. Compact is a switch, default off, like every other
layer; Dock to top is a command, and nothing holds the window there
afterwards.

## What compact is

The sung line, and the romanisation under it when that layer is on. No
header, no previous line, no next line, no plain-lyrics note. Off is the
window the app has always had, at the height it was last left at.

Everything else still holds: per-app position memory, the opacity gesture,
edge resizing, the flight to and from the menu bar item, and the
notification yield. None of them knows this layout exists, which is the
point — it changes which rows are on screen and how tall the window may
be, and nothing else.

## The height floor

`geometry.min_window_height(scale, compact=True)` is the same arithmetic
the full layout uses, asked for two rows instead of five:

| scale | full | compact |
|---|---|---|
| 0.65 (the narrowest window) | 120 | 51 |
| 1.0 (460pt wide) | 183 | 79 |
| 2.0 | 367 | 159 |

Derived, not chosen. Two rows, the tighter gap between them, and the
window's own top and bottom margins. Two things are deliberately dropped
with the other three rows:

- **The five-row floor of 120.** That number is where five rows stop
  fitting, which is not a fact about two.
- **The air above and below the sung line** (`CURRENT_SPACING`). It exists
  to stop three lyric rows reading as an evenly spaced list with one of
  them in bold. With no neighbours there is nothing to separate from, so
  the layout gives it up and the floor stops counting it.

The pronunciation row is counted whether or not the romanisation layer is
on, for the reason the full layout counts it too: which lines carry hangul
changes song by song, and a floor that moved with them would resize the
window under the user mid-track.

Each layout keeps the height it was last left at, in `window/full_height`
and `window/compact_height`. Switching gives that height back rather than
one derived from the other layout, so going compact and returning is not a
way to lose the window's shape.

## A strip is one row tall, so a long line ends in an ellipsis

**Found by screenshot.** The full layout wraps a long line onto a second
row and its floor leaves room for one. A strip has no such room by
construction, so the second row landed halfway on top of the romanisation,
cut through the middle. Nothing in the numbers said so: the window was at
its floor and the floor was right.

So in the compact layout the two lyric rows do not wrap, and what will not
fit is elided. The width is computed from the window and its gutters
rather than read off the label, because the elision runs before the layout
that would produce a label width; the unelided line is kept, so a resize
lays it out again from the line rather than from what was left of it.

## The controls, and why they are polled for

The compact layout hides the loop, speak and done buttons and brings them
back while the pointer is over the window, on a 260ms fade. It also brings
them back while echo practice is waiting for a turn, whatever the pointer
is doing: that phase pauses the song and the done button is the only way
out of it, and a prompt nobody can see is not a prompt.

The obvious implementation is `enterEvent` and `leaveEvent`. It works
perfectly, and only while the app is frontmost.

**Measured, and it corrected the first version outright.** Driving the
real pointer onto the real window with the app backgrounded — accessory
activation policy, `WindowDoesNotAcceptFocus`, Finder in front, which is
the only state this app is ever in — the window heard nothing. `hovered`
stayed false with the cursor provably inside its frame. Qt installs its
tracking area `NSTrackingActiveInActiveApp`, so an app that never
activates has no hover events to miss.

What still answers is the pointer's own position, which is a screen
coordinate and belongs to nobody. So the window asks, on a 100ms timer
that runs only while the compact layout is in force and the window is
showing. One poll costs **0.8us** measured, which is 0.0008% of a core at
that rate; the interval is the whole of the latency, and at 100ms the
controls are already moving before the hand has finished arriving.

Asking the pointer also fixes the bug the event version would have had
anyway: Qt sends the window a Leave the moment the pointer moves onto one
of the window's own controls, and `underMouse()` is false for the same
reason. The frame contains its children; the question answers itself.

A control faded to zero is also **taken off the window**, not left
invisible. A widget at zero opacity is still a widget under the pointer.

With compact off the opacity effects are destroyed rather than left at
full: an effect that is doing nothing is still an effect on every repaint,
and this is the same rule Reduce Transparency is held to.

The fade was verified by screen capture at five levels, because
`grab()`/`render()` does not apply a `QGraphicsEffect` and could only have
agreed with itself. The darkest pixel of the loop button, over the light
panel: **132, 157, 182, 207, 232** at 1.0, 0.75, 0.5, 0.25 and 0.0 — a
clean ramp to the panel's own colour, and gone at the end.

## A sync pass takes the full layout back

Compact steps aside for a sync pass, for as long as the pass runs.

A pass needs the line before, the line after, a status row and a tap bar
across the bottom. A strip has room for one of those, and the bar alone
would *be* the window. The alternative was a strip that pretended, which
is the fourteenth milestone's lesson again: a feature indistinguishable
from a broken one.

Looping and echo practice do **not** fall back. They are the compact
layout's best case — one line, repeated — and the loop is engaged and
released many times a song, so a layout that changed with the phase would
be a window resizing itself every few seconds.

The setting is untouched while the pass borrows the layout: `_compact` is
what the user asked for, `_compact_applied` is what the window is wearing,
and the menu's tick follows the first.

## Docking to the top

`geometry.docked_position` is pure arithmetic and takes three things: the
window's width, the screen, and the screen's available area.

**Centred on the screen, not on the available area.** A Dock on the left
or right shrinks the available area but not the menu bar and not the
notch, and it is those the window is lining up with.

**Flush, with no gap of its own.** "Just below the menu bar" is a position;
a gap would be a number set by eye. The window is freely draggable
afterwards, so nudging it down is a nudge.

**The top edge clears whichever obstacle reaches further down.** The
available area is the menu bar's answer and is usually the whole of it: on
a notched Mac macOS reserves the entire band the notch sits in, so the
available area already starts below it. Measured live on a 14" MacBook
Pro: screen 1710x1107, available top **34**, safe-area top **33** — the
menu bar is one point taller than the notch, and docking lands at 34.

The safe area is asked for anyway, because the two come apart in exactly
one case and it is a case people use: **"Automatically hide and show the
menu bar" gives the whole screen back as available space and leaves the
notch where it was.** Without the inset the window would be docked
underneath it. It is a floor on the available area, never a replacement.

It is read from the window's own `NSScreen`, through the `_nswindow()`
door that already exists, so there is no screen matching to get wrong and
no second native door to guard. Zero everywhere the question cannot be
asked — off cocoa, without pyobjc, on a macOS with no safe areas, or with
the window not on a screen — which is also every case where a Mac has no
notch to describe.

**A dock is learned like the end of a drag.** With per-app position memory
on, a dock that was not recorded would be undone by the next app switch,
and docking is the user saying where the window goes, which is the same
thing a drag says. The position is written from the target rather than
from the window, because the travel takes a phase length and neither the
save nor the learn may record a waypoint.

A window that is away at the menu bar is docked by moving where the flight
will put it back, not by moving the window: the flight is holding the real
position and would hand the old one straight back on landing.

## Out of scope

Splitting the layout around the notch, Dynamic Island style flanking
icons, and automatic snapping to any edge. Docking is a command, and it is
the only thing here that places the window without being dragged.

## Where the code is

| | |
|---|---|
| `geometry.py` | `min_window_height(compact=)`, `compact_text_gutter`, `control_gap`, `docked_position` |
| `menu.py` | `COMPACT`, `DOCK_TOP` |
| `window.py` | `_apply_compact`, `_set_line_text`, `_reveal_target`, `_check_pointer`, `_dock_to_top`, `_top_inset` |
| `tests/test_geometry.py` | the floors, the gutters and the dock position, notched screen included |
| `tests/test_window_qt.py` | the rows, the height swap, the reveal, the sync fallback, the dock |
