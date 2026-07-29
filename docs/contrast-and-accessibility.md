# Contrast and accessibility

The window floats over whatever else is on screen — a white document, a
dark editor, a bright video, someone's slide deck. It has no control over
its own background. So the promise it makes is explicit:

> **The sung line clears 4.5:1 against its own background, with the
> vibrancy material contributing nothing.**

WCAG AA for normal text, and the floor is checked in a test rather than
judged by eye.

## Why "with the material contributing nothing"

The `NSVisualEffectView` behind the lyrics only ever *helps* — it renders
as a tint in the direction of its own mode, which pulls the backdrop
towards the panel. But it can fail to attach (no pyobjc, a future macOS),
and it disappears entirely the moment the window is dimmed, because macOS
draws behind-window blur only at full alpha.

A promise that depends on the blur is therefore a promise that breaks
exactly when someone dims the window to read through it. The floor is
computed as if the material were fully transparent, and everything the
material actually contributes is headroom.

## Which backdrop is the hard one flips with the mode

Missing this is how a light mode ships broken.

- White text on a **dark** scrim is worst over a **white** page — that is
  where a translucent dark scrim is at its palest.
- Dark text on a **light** scrim is the mirror image: worst over a
  **black** page, where the scrim is at its darkest.

Each palette is measured against the backdrop that suits it least, and the
tests run both extremes for both palettes.

The scrim alphas fall straight out of that: dark crosses 4.5:1 at alpha
147 and ships **150**; light crosses at 131 and ships **134**. Just enough
that rounding cannot land under the threshold, and no more — every extra
unit of scrim is blur spent.

## Parity, role by role

Only the sung line carries the 4.5:1 promise. Everything else is meant to
recede — the header, the context lines, the pronunciation, the idle
controls.

"Meant to recede" is not a licence to disappear in one mode only. Every
role that recedes in dark mode is pinned to recede *no further* in light
mode, each measured in its own worst case. Four light colours (both accent
blues, the idle control, the warning red) were swept until they satisfied
that, rather than picked by eye: `(130, 200, 255)` is a light-on-dark
accent and washes out to nothing on a pale panel.

That parity test is what stops a later tweak quietly regressing one side.

## The measured numbers

Analytic floors, no material at all:

| | dark | light |
|---|---|---|
| untinted | 4.70:1 | 4.72:1 |
| under album tint, worst of all 360 hues | 4.68:1 | 4.69:1 |

Measured from real screenshots with the material rendering:

| backdrop | dark | light |
|---|---|---|
| white document | 9.3:1 | 15.2:1 |
| dark editor | 16.0:1 | 10.3:1 |
| bright video | 8.9:1 | 15.5:1 |

With a deliberately hostile fully saturated cover tinting the panel: 9.3:1
over a white page and 17.2:1 over a black one in dark mode; 15.2:1 and
9.7:1 in light.

Re-measured from real pixels after the album colour moved to the hairline,
over each mode's worst backdrop: **11.4–11.6:1** dark and **11.9–12.0:1**
light, with the tint moving it by under 0.3 in the safe direction.

## Where the colours live

All of them are plain RGBA tuples in a Qt-free module, `appearance.py`.
Two palettes with the same shape, flat and explicit rather than derived —
a palette that computed its own light variant would be a second set of
rules to keep honest, and the floor is checked against the values that
actually ship.

Being Qt-free is what lets the contrast maths run in tests without a
display server, on every push, on a Linux runner.

## Type is part of legibility

The type hierarchy is 20 px / weight 700 for the sung line against 13 px /
400 for its neighbours: a 1.54x size ratio and three weight steps, where
it used to be 18/600 against 14/400 (1.29x and one step). At the old
separation the eye had to *read* the window to find the current line; at
this one it lands on it.

Only the sung line is tracked (-0.35 px at base scale). Tightening
body-size type costs legibility and buys nothing.

See [motion and typography](motion-and-typography.md) for the rest.
