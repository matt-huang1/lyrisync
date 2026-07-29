# Album colour

An optional layer that colours the window from the current album cover.
Off by default, like every layer.

## The governing rule

> **The artwork supplies a HUE and nothing else.** Its own luminance and
> saturation are discarded and replaced with ours, per mode.

The failure this prevents is the obvious implementation — sample a colour,
paint with it — which works beautifully for three albums and then meets a
neon cover, or a near-white one, and produces a window nobody can read
lyrics against.

Because the hue is all that is taken, a near-white sleeve, a near-black
one, a neon one and a muted one *of the same hue* all produce exactly the
same window. That is a test, not an aspiration.

## Getting a hue out of a cover

Extraction is a **dominant-hue vote**, not an average. Averaging a cover
gives mud: a red sleeve on black averages to dark maroon, and a photograph
averages to grey.

The image is sampled down to 48 px a side, and pixels are discarded before
voting if they are near-black (lightness < 0.12), near-white (> 0.92) or
barely coloured (saturation < 0.15). Nearly every sleeve is mostly dark,
and letting those pixels count makes every album the same colour.

The survivors vote into 24 hue bins — 15° each, fine enough to separate
red from orange and coarse enough that a gradient still lands together —
weighted by saturation, and the winning bin has to carry at least 2% of
that weight per sampled pixel before it counts as a colour rather than as
noise in a monochrome sleeve. The answer is that bin's saturation-weighted
mean colour, which cannot produce mud: every member already agrees about
the hue, and the weighting stops the washed-out members dragging it.

**"No usable hue" is a real answer**, not a failure. It can be reached
twice over: no bin wins, or the winning colour is itself flatter than 0.10
saturation, at which point the window declines to tint rather than invent
a hue from noise. Confirmed live: the cover for *I'll Show You* is a
black-and-white photograph with median saturation 0.005, and correctly
produced no tint at all.

## The cache holds the colour, never the image

Three integers of JSON per track, against 100 KB+ per cover. Nothing ever
needs to look at an image twice.

"No usable hue" is cached like a definitive 404: it is an answer *about
the cover*, and asking again would get the same one. A fetch that failed
and an image that will not decode are answers about nothing, and are never
cached.

The code keeps the two apart rather than collapsing both into `None` —
decoding **raises** when an image will not decode at all, and **returns
`None`** only for "there is no hue here". Same distinction as the lyrics
cache's, for the same reason.

## Tinting the panel: bisection on luminance

The tinted background is solved by **bisecting onto the untinted colour's
relative luminance** — not by holding HSL lightness. The two are not the
same quantity and disagree badly across hues: pure yellow and pure blue
sit at the same HSL lightness and nowhere near the same luminance.

Pinning the wrong one would move the contrast floor *by hue*, which is
exactly the bug a hue-only tint exists to avoid. Measured: sweeping all
360 hues at full saturation moves the scrim's luminance by under 8% (that
residue is 8-bit quantisation) and costs 0.03 of the floor.

Only the two backgrounds are tinted. Every text colour keeps the value it
was measured at, which is what lets the floor be re-*checked* under tint
rather than re-derived.

## How much colour: chroma, not saturation

The strength of a tint is specified as the **chroma of the finished
panel** — the spread between its strongest and weakest channel, 0–255,
after the background's alpha has diluted it.

That is a fix for a real bug, diagnosed rather than guessed. Saturation
collapses at both ends of the lightness range: the light scrim sits at
L = 0.973 where even S = 1.0 can only produce 14/255 of chroma, while the
dark scrim at L = 0.067 gets 34/255 from the same number. One saturation
therefore meant 2.4x more colour in dark than in light — and at 0.22 the
light tint moved the scrim by 3/255, *less* than the palette's own
built-in blue cast. Album colour looked switched off in light mode however
strong the cover was.

The saturation that yields a wanted chroma is then found by **bisecting on
saturation against the chroma actually achieved**, not by inverting HSL's
closed form. The closed form answers a different question (chroma at some
lightness), and pinning the luminance moves the lightness by wildly
different amounts per hue. Feeding that back as a correction does not
converge, it *oscillates*: yellow alternated between chroma 204 and chroma
2 on successive rounds, and stopping after two landed on whichever the
parity chose — which is how a yellow cover came out less coloured than no
cover at all. Chroma rises monotonically with saturation at fixed
luminance, so bisection converges, and a hue that cannot reach the target
settles for its most.

**Every hue must beat the untinted panel's own chroma, in both modes.**
That is the test that would have caught the original bug, and it is the
one that caught the oscillation too.

## Contrast headroom is not aesthetic headroom

An earlier version set the tint near full saturation, because measurement
showed saturation was nearly free against the contrast floor. The
reasoning was sound and the conclusion was wrong.

What the floor *permits* and what looks like a pane of glass are different
questions, and a near-fully-saturated wash answers only the first. The
panel's tint is now set by eye at the point where the colour is felt
rather than noticed, with the floor re-checked afterwards rather than used
to justify the value.

## Most of the colour is on the hairline

The panel ran out of room, and not through bad tuning: its luminance is
pinned by the 4.5:1 promise, and at the pale panel's luminance a blue is
nearly white. Buying its colour costs brightness the floor will not give
up. Relaxing the pin was measured rather than assumed — a 5% drop buys the
worst hue only 4.7 → 7.9 chroma and leaves the floor at 4.52, with none of
the rounding headroom the rest of the palette keeps. So the luminance
stays pinned, and the panel keeps a restrained wash.

The colour moved instead to the **hairline** — the one surface in the
window with no contrast obligation, because no text is read against it.
This supersedes an earlier decision that the hairline would never be
tinted (the objection was that a coloured hairline reads as a border);
measured against real covers in both modes it does not, because it is
still one device pixel and still inset.

Freed of the luminance pin, the hairline pins **HSL lightness** instead,
and that buys something the panel can never have: at a fixed lightness an
HSL colour's chroma is exactly `saturation x (1 - |2L - 1|)`, with **no
hue term at all**. So the edge is solved by arithmetic rather than
bisection, and delivers the same amount of colour for every hue:

| | dark | light |
|---|---|---|
| edge chroma, all 360 hues | 46.2 | 45.7 |
| panel chroma, range over hues | 10.0–14.1 | 4.7–12.1 |

Identical across hues, and the same in both modes — which is precisely
what the panel's pinned luminance denies it in light mode.

What fixes the two lightnesses (0.72 dark, 0.30 light) is that a coloured
line is not automatically an edge: it must stay lighter than the dark
panel and darker than the pale one, for every hue, over the backdrop that
leaves the panel closest to it. The binding cases are blue in dark mode
and yellow in light — the hues furthest from their own panel in
luminance. Alpha (110 / 105) is a free knob against saturation for the
same delivered chroma; it is what lets a strongly coloured edge avoid
being a strongly saturated one.

Floors under tint after all of that: **4.68:1 dark, 4.69:1 light** —
exactly what they were, because the change touches `border` and nothing
else.

## Behaviour

- The tint **cross-fades** over 600 ms between tracks, because a colour
  that changed in one frame reads as a glitch rather than as the song
  changing. A fade starts from whatever is on screen, so a track skipped
  mid-fade moves on from where it got to instead of jumping back.
- The panel and the edge ride **one** mix. Two fades of the same tint
  could only drift apart, and an edge that changed colour before its panel
  would read as a flicker at the rim.
- The layer is **always offered** in the menu, unlike the learning layers
  that hide when they cannot act. This one can always be answered and is a
  standing preference about the window; appearing and vanishing per track
  would hide it exactly when someone goes looking for it — before the
  music starts.
- Switching it off restores the plain window **exactly**, to the byte.

## Parked

Album art as an actual background; multiple colours or gradients from one
cover; per-song colour overrides.
