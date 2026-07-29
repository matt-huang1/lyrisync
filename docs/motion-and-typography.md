# Motion and typography

How a line changes, and how the window is set.

## The line change is anticipatory

The window knows the next line's timestamp, so it does not wait for the
poll that crosses it. The whole movement is scheduled **before** the line
is due, and the incoming line settles onto its mark exactly *on* the
timestamp.

That is the property everything else is arranged around: motion completes
on time rather than starting on time, so nothing is still moving while it
is being read.

One line change is two phases of 260 ms:

```
ts - 520ms   the outgoing line starts leaving   (eased In)
ts - 260ms   the lines swap; the new one rises  (eased Out)
ts           the new line is in place and fully legible
```

Every constant derives from that single phase length, so the swap point
and the total window cannot drift apart from it.

**Sine, not cubic.** Cubic's ends are steep enough that even a 260 ms
phase reads as a flick. The pair stays In-then-Out so the outgoing line is
fastest exactly where the incoming picks up: velocity is continuous across
the swap, which is what makes two phases read as one movement rather than
as two.

The schedule was extended *earlier* rather than allowed to finish later —
520 ms of movement before the line is due, where it used to be 200 ms.
Travel went 7 px → 10 px at the same time: distance and duration are a
pair, and a distance that read as a twitch at 100 ms reads as too little
at 260 ms.

**A gap shorter than the choreography gets a quicker version of the same
movement, not a truncated one.** Each phase takes at most half the gap, so
both phases still fit and the arrival still lands on the timestamp at any
tempo — a 400 ms gap gets 200 ms phases, a 120 ms gap gets 60 ms. The
animation reads that per-transition duration rather than the nominal
constant, or the clamp would be decorative and the movement would overrun
the line it belongs to.

## One signed property, not two animations

Vertical motion is a single value, `progress`, running -1 (gone, drifted
up) through 0 (in place) to +1 (not yet arrived, below):

```
opacity = 1 - |progress|
offset  = progress x travel
```

They are not independent — a line half faded is half travelled, by
definition — and one `QPropertyAnimation` beats a parallel group of two
that could drift.

It is a `QGraphicsEffect`, not a moved widget. The current line lives in a
`QVBoxLayout`, so anything that moved it would be undone by the next
layout pass and would ripple into the rows above and below. Drawing the
source pixmap at an offset touches no geometry, which is why the rest of
the window cannot feel it happening. `boundingRectFor` must grow by the
travel, or the moving block is clipped to its own box and reads as
dissolving at the edge instead of leaving.

## One line change plays once

The choreography (520 ms) outgrew the poll interval (300 ms), which means
a poll lands inside almost every line change. That poll used to do two
damaging things:

- re-arm both timers from what was *left* of the gap — a smaller eta, and
  therefore a hurried second run of the same movement;
- read the predicted swap's one-line lead over the view model as a missed
  prediction, and snap the display back to the line being left.

The result was a line that jumped, settled, and jumped again. Reproduced
deterministically before fixing: the indices reaching the screen were
`[1, 0]` where they should read `[1]`.

The fix is a dedupe **by target line index** — the same identity shape the
view model uses for tracks, where an announcement of what is already
showing is not news. It is two questions rather than one:

- **May the timers be re-armed for this line?** Yes until the movement
  begins, which is what keeps a seek being picked up within one poll.
- **May this trigger start the movement?** Only the first one. The
  fade-out claims the change; every later trigger for that line is
  refused.

Being ahead of the view model is **bounded** as well as owned. Suppressing
the snap whenever the screen leads by one line would leave the window
sitting on a line the song has left after a seek backwards into the middle
of the current one — waiting for a timestamp half a verse away. So the
lead only stands while the player is within the choreography plus one poll
interval of that timestamp; past that, the world moved, and it snaps.

Everything that means "the world moved" — a seek, a pause, a loop wrap,
entering sync mode, a track change, any render at all — cancels through
one place, so no animation outlives the situation it was describing or
leaves a line parked off its mark.

## What it costs

Measured per line change: 1.7 ms for the text swap, 57.8 ms for the fade,
92.7 ms with the rise — against 34.1 ms at the old 100 ms pacing. At a
line every four seconds that is **2.3% of one core**, and 0.16% between
lines.

## Verifying motion

Two things this project learned the hard way:

- **`QWidget::grab()`/`render()` does not apply a `QGraphicsEffect`.**
  Measured, after it produced nonsense readings. So an effect can never be
  verified through `grab()`; the reliable readback is tracing the effect's
  own `draw()`. A real line change calls it 8 times per phase with the
  eased progress sequence — a stronger check than any screenshot.
- **macOS's window server hands back a stale frame** on the first capture
  after a change, so frozen single frames of an animation are not
  trustworthy. Three attempts at a filmstrip disagreed with each other.
  Static states screenshot fine; motion has to be measured, not
  photographed.

`grab()` *is* the right readback for `paintEvent` output, which is how the
tinted hairline is checked from real pixels.

## The type scale

| role | size | weight |
|---|---|---|
| header | 11 px | 500 |
| context (previous/upcoming) | 13 px | 400 |
| **current line** | **20 px** | **700** |
| pronunciation | 12 px | 400 |
| plain lyrics | 14 px | 400 |

Sizes are base pixels at scale 1.0; the window multiplies by a
width-derived scale, so the file changes proportions and never the scaling
system.

The sung line is deliberately far from its neighbours on both axes — a
1.54x size ratio and three weight steps, where it used to be 1.29x and one
step. At the old separation the eye had to read the window to find the
current line.

Weights are stated for every role rather than left to inherit: the
contrast between the current line and its neighbours *is* the hierarchy,
and a default would put it at the mercy of whatever Qt picks per platform.

**Tracking is the one type setting that cannot live with the others**, and
that is a Qt limitation, not a design choice: Qt stylesheets have no
`letter-spacing` property. It goes on via `QFont`, with the font read back
*after* the stylesheet has been applied and polished — measured that size
and weight still come from the stylesheet afterwards, so `typography.py`
stays the single source and this only adds what the stylesheet cannot say.

`geometry.py` imports the same numbers to compute the minimum window
height, so the height floor can never describe a type scale the stylesheet
has moved on from. Raising the hierarchy grew that floor from 161 px to
183 px, so the default window height went 170 → 200: a first run should
open at a shape it chose, not at the floor it was clamped to.

## Icons

The speak control is an **SF Symbol** (`text.bubble`), rendered per scale
at the screen's device pixel ratio — an SF Symbol is drawn for the size it
is asked for, and a 1x glyph stretched to 2x is visibly soft. It is a
template image, so it carries its shape in the alpha channel and takes its
colour from the app.

The route there is worth recording. Colour emoji are out: 🔊 renders in
colour whatever is asked of it, and macOS has no monochrome speaker glyph
(U+1F56A and friends are tofu), so the button was ♬ for a while. The
native answer was SF Symbols rather than hunting for a Unicode character
that happens to render monochrome. ♬ stays as the fallback for when
symbols cannot be had — off macOS, no pyobjc, symbol missing — which is
also what lets the test suite stay headless.

One source for control colours: the window owns idle/hover/engaged, the
stylesheet paints text with them and the symbol module tints the icon with
them, so a glyph and the icon that replaced it cannot describe different
states.
