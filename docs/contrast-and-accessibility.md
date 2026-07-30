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

## The three macOS accessibility display settings

System Settings → Accessibility → Display has three switches that say
things about this window, and until session D the window had never asked.
All three are read live — one NSWorkspace observer on
`NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification` — for the
same reason the appearance is: somebody who turns Reduce Motion on because
a migraine has started should not have to relaunch the app to be believed.
Qt publishes none of them; `QStyleHints` has `colorScheme` and no more.

They live in `accessibility.py`, behind one door onto NSWorkspace.
Deliberately not `frontmost.py`'s door, even though both stand on the same
class: per-app position memory is an opt-in layer that unsubscribes when
it is switched off, and this is a system setting followed for as long as
the app runs, so sharing a door would mean the suite could not block one
without blocking the other.

### Reduce Motion

The travel goes and the fade stays. The line's rise is one signed
`progress` carrying opacity and offset together, so removing the rise is
setting its length to zero and changing nothing else — same phase length,
same easing, same arrival exactly on the timestamp. What was a rise
becomes a cross-fade of the same duration.

The flight to the menu bar item and the travel to a remembered position go
entirely rather than becoming quick versions of themselves: movement is
all they are, and a fade in place would be answering a question nobody
asked. The window still hides, shows and arrives where it was going.

The album-colour cross-fade, the notification yield and the
learned-position glow are untouched. They are fades, which is what Reduce
Motion asks to be given *instead* of movement.

### Reduce Transparency

The NSVisualEffectView is removed from its superview, not hidden — a
hidden effect view is still an effect view, and the flight hides and shows
this one for its own reasons, which would put a suppressed material
straight back. The background becomes `palette.solid` at **alpha 255**.
The shipped 232/236 stay exactly as they are; they belong to a different
case (vibrancy that could not be installed at all) and are measured for
it.

This costs the frost and buys a great deal of contrast, because the floor
has always been measured with the material contributing nothing:

| role | over the scrim | over the opaque panel |
|---|---|---|
| sung line, dark | 4.70:1 | **17.93:1** |
| sung line, light | 4.72:1 | **16.89:1** |
| plain body, dark | 3.76:1 | 12.17:1 |
| context lines, light | 2.94:1 | 5.59:1 |

### Increase Contrast

macOS turns Reduce Transparency on and locks it there while this is on,
because a blurred backdrop and a contrast guarantee cannot both be
honoured. The app derives the same thing rather than trusting the pair to
arrive together.

**What was found before anything was changed.** Over the scrim with the
material contributing nothing, the sung line clears 4.5:1 and *nothing
else does*: plain 3.76/3.78, pronunciation 3.12/3.24, context 2.76/2.94,
header 2.48/2.48, progress 2.13/2.28, the idle control 2.12/2.17
(dark/light). That is the hierarchy working as designed, and the wrong
answer to somebody who has asked the system for more.

Dropping the material does most of the work, so what is left is a short
list of overrides rather than a third pair of palettes — `appearance.py`'s
`HIGH_CONTRAST_OVERRIDES`. Each number is the alpha that crosses a floor
over the opaque panel, plus the few steps of rounding headroom the scrim's
own alpha keeps:

| role | dark | light |
|---|---|---|
| `header` | unchanged (5.48:1) | 140 → 155 |
| `control_idle` | 105 → 120 | 120 → 155 |
| `scrollbar` | 70 → 90 | 70 → 124 |
| `sync_text_off` | 60 → 90 | 60 → 124 |
| `tap_text_off` | unchanged (3.83:1) | 110 → 124 |
| `border` | 30 → 90 | 38 → 112 |
| `attempt_text` | unchanged | alpha 245 → 255 |

Two floors, because two kinds of thing are being lifted. **4.5:1** for
anything read to follow a song. **3:1** for the marks — the scrollbar, the
hairline — and for the labels on switched-off controls, which WCAG exempts
outright and which this holds anyway: "PAUSED" on the tap bar is a
disabled control and is still the one word explaining why tapping does
nothing.

`attempt_text` is the one role lifted by opacity rather than by alpha
arithmetic. At 245 it manages 4.25:1 over its own hovered amber fill, and
the colour is already as dark as that pairing allows; 255 gets it to
4.56:1.

**The album tint's hairline is not lifted, and that is measured rather
than forgotten.** Over the opaque panel it bottoms out at 2.02:1 (dark)
and 1.62:1 (light) across the hues, and reaching 3:1 would need alpha 163
and 218 — a nearly opaque line, which ends the hue-only design
`BORDER_CHROMA` exists to hold. Nothing is read against the hairline, and
the panel and every word on it clear their floors with the tint on, swept
over all 72 hues.

### What is manual

The switches themselves cannot be toggled from a test:
`com.apple.universalaccess` is TCC-protected and `defaults write` answers
*"Could not write domain com.apple.universalaccess; exiting"* without Full
Disk Access. So the app's response to each option is tested by handing the
window the option, and the reading of the real switch is verified by hand.
The observation path was verified live by posting the notification onto
NSWorkspace's own notification centre and watching the observer receive
it.
