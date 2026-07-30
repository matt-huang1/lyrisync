# Design philosophy

These principles were not written first. They are what the decision log
turned out to be made of — each one is stated here with the decisions that
produced it, so it can be checked against the code rather than admired.

When two of them conflict, the order below is roughly the order they win
in.

---

## 1. Readability is never traded for aesthetics

The window floats over someone else's screen and cannot control its own
background, so the sung line clears 4.5:1 **with the vibrancy material
contributing nothing**. Everything the blur adds is headroom.

That ordering decides real arguments:

- The scrim's alpha is measured, not chosen by eye — dark ships at 150
  where 4.5:1 is crossed at 147; light ships at 134 where it is crossed at
  131. A material that fails to attach costs depth and never readability.
- The album tint takes the cover's **hue only**, and the tinted background
  is bisected onto the untinted one's exact relative luminance, so a neon
  sleeve cannot move the floor.
- When the panel ran out of gamut, the colour moved to the hairline rather
  than the floor moving to accommodate it. Relaxing the pinned luminance
  by 5% was measured — it bought the worst hue 3 units of chroma and cost
  0.17 of the floor — and rejected.

The counterweight is that **contrast headroom is not aesthetic headroom**.
The floor says what is allowed, never what is wanted; a near-fully-
saturated wash cleared the floor comfortably and still looked wrong, and
was cut by two thirds.

## 2. Native before custom

If macOS already draws it, use the thing macOS draws.

- The blur is a real `NSVisualEffectView`, not a painted approximation.
- The shadow is the native `NSWindow` shadow: it follows the rounded
  corners for free because macOS derives it from the alpha channel, it
  sits outside the window bounds, and it costs the compositor nothing per
  frame.
- The speak button is an SF Symbol, after a detour through Unicode glyphs
  that could not be made monochrome. The right answer was the system's
  icon set, not a better emoji.
- Open at Login is `SMAppService`, not a hand-written LaunchAgent plist.
- The global hotkey is the OS's own hotkey matcher.

The cost of this principle is accepted explicitly: the native shadow
cannot be tinted, because `NSWindow` exposes no colour API for it. That
ends the discussion rather than starting a painted-shadow one.

## 3. Prefer no setting where the system already answers the question

Light and dark **follow macOS**, live, and there is no appearance toggle.
A toggle would be a second source of truth for something the system
already decides, and the two would disagree the first time someone
switched one of them.

The same reasoning gives Open at Login its shape: the tick is re-read from
`SMAppService` at startup and on every menu opening, and the stored
preference is only ever compared against it in order to *log* the
disagreement. An entry that claims the app starts at login when it no
longer does is worse than no entry.

A setting is worth having when it records a preference the system has no
opinion about — window opacity, which layers are on, show on all desktops.

## 4. One source of truth

Two things that describe the same fact will eventually describe it
differently.

- The menu bar menu and the window's right-click menu are literally the
  same `QMenu` object.
- The hotkey drives the same setter the menu entry does, so the tick
  matches the window whichever was used.
- `typography.py` owns the type scale and `geometry.py` imports it, so the
  height floor cannot describe a scale the stylesheet has moved on from.
- One module owns the control colours; the stylesheet paints text with
  them and the symbol renderer tints icons with them, so a glyph and the
  icon that replaced it cannot describe different states.
- The material's appearance is set from the same resolved answer the scrim
  is painted from, because inheriting one and computing the other means
  they disagree mid-transition.
- The album tint's panel and hairline ride one cross-fade mix, not two.

## 5. Learning features are layers; the default is a simple lyrics window

Romanisation, spoken reference, looping, echo practice, album colour,
tap-to-sync: every one is a toggle, off by default or hidden until it can
act. **With every layer off, the app must equal what it would have been if
none of them had been written** — and where that is checkable it is
checked byte for byte.

Layers hide when they cannot act (nothing to romanise without hangul), but
never hide when they *can* be answered: album colour stays visible even
before a song starts, because a preference about the window that vanishes
when you go looking for it is worse than one that is simply off.

## 6. Never let a decorative feature break an essential one

Ordering the two is most of the work.

- The artwork query gets its own nested `try` inside the AppleScript, so a
  Spotify build that will not answer `artwork url` loses a colour rather
  than losing the six track fields — the app showing a running player and
  never finding a song, for the sake of a tint.
- A fetch landing under a tap-to-sync pass is held, not applied: the pass
  is what the user is doing, and a background refresh does not get to tear
  it down.
- The loop suppresses the line-change scheduler rather than fighting it.
- Hiding the window stops nothing: the monitor runs, a loop stays engaged,
  a sync pass keeps stamping.
- Dimming the window is understood as spending the blur — a real trade,
  made deliberately, rather than a feature quietly disabling another.

## 7. Verify at the layer the user experiences

A healthy return value is not evidence that anything appeared on screen.

- `_material is not None` only proves a view attached. Whether it *blurs*
  is a question about pixels, so the check is a screenshot over a
  text-heavy backdrop: sharp glyphs inside the window mean no blur,
  however healthy the handle looks.
- The contrast numbers that get quoted come from real captures with the
  material rendering, not only from the analytic floor.
- The tinted hairline is checked from the pixels `paintEvent` produced.
- Where the compositor cannot be trusted — macOS returns a stale frame on
  the first capture after a change, and `grab()` does not apply a
  `QGraphicsEffect` — the readback moves to something that can be
  trusted (tracing the effect's own `draw()`), rather than the claim
  quietly weakening to match the tool.

The same instinct applies to the OS: `RegisterEventHotKey` was tested
against two live processes to find out that it is not exclusive, and the
log message was corrected to stop claiming that a refusal means another
app owns the combination.

## 8. Measured, not eyeballed

Every number in this project that could have been picked by eye has a
measurement behind it, and the measurements are kept as tests so a later
tweak has to argue with them.

Scrim alphas, the light-mode palette's parity with dark role by role, the
tint's chroma per hue across all 360 of them, the type hierarchy's effect
on the height floor, the CPU cost of the line change (93 ms per change,
2.3% of one core at a line every four seconds).

The corollary is that measurement is also allowed to *overturn* a
conclusion. Album colour was set near full saturation because measurement
proved it was nearly free — and then cut by two thirds because what the
floor permits is not what looks right. The measurement was correct; the
inference from it was not.

The sharper version of the same lesson: a measurement can be of the wrong
*quantity*, and a number carries no warning that its label is wrong.
Milestone 16 measured a notification banner's "text contrast" through the
window and found it collapsing to 1.50:1 — a clean, plausible, repeatable
result, and not what it claimed to be. The sample covered the whole window
rectangle, where most of the pixels are the app behind the banner, so
"text" was the banner's pale body and "background" was a dark editor.
Cropped to the banner, its own text contrast turns out to *rise* under the
window and never to approach 4.5:1 at all.

**Looking at the artefact is what caught it** — the capture, not the number
derived from it. So a pixel measurement is not finished until someone has
seen the pixels, which is principle 7 turned back on the verification
itself.

## 9. The user's own work is not cache

`.lyrics_cache/` and `.artwork_cache/` are derived data: everything in
them can be fetched again, so throwing them away is a safe reset.
`.user_syncs/` is different. It holds work the user did by hand, one tap
per line, and nothing in the app or in its documentation may point a
cleanup step at it.

That line is enforced rather than intended — a test asserts exactly one
module writes there, and that the album-colour module may not so much as
mention the directory. A module that learns to write has to argue for
itself in that test first.

Downstream of the same principle: a sync is saved only when a pass is
complete, so there is never a half-finished file; a re-sync draws its
lines from the sync it replaces, so it works offline and after a cache
reset; and LRCLIB's timings are never offered for overwrite, because
someone else's work is not yours to replace either.

## 10. Only definitive answers are remembered

A genuine 404 is a fact about the song and is cached. A timeout is a fact
about the network and is never cached — it becomes a visible retry state
that re-attempts every 30 seconds.

Conflating the two is how an app becomes permanently certain that a song
has no lyrics because of one bad minute of Wi-Fi. The album-colour path
keeps the same distinction, which is why a failed download and an image
that will not decode are different outcomes in the code rather than one
`None`.

## 11. The best features are invisible when they work

Per-app window position memory is the clearest example this project has.
It removed a friction that was being paid many times a day — drag the
lyrics out of the way of the editor, drag them back for the browser — and
the measure of its success is that **it is never noticed while working**.
The window is simply where it should be. Nobody thinks about it, which is
the point.

That is worth stating because it cuts against the instinct to make work
visible. A feature that announced itself here would be worse, not better:
this window floats over somebody else's screen while they are doing
something else.

The corollary is the harder half, and it was learned the expensive way. A
feature nobody notices is a feature nobody can tell from a broken one.
Per-app positions shipped, silently did nothing for a whole milestone
because a drag was quietly refused, and the report that came back was two
things at once: *it does not work*, and *I cannot tell whether I am using
it right*. The second was the real defect. So invisible-while-working
buys an obligation:

- **it must be possible to ask.** The menu says how many apps are
  remembered and whether the app in front is one of them, by name and
  icon, whenever it is opened.
- **the moment of learning gets a brief answer** — half a second of warm
  on the hairline, and then gone.
- **every refusal names itself** in the log, from one rule that also
  decides the refusal, so the explanation cannot disagree with what
  happened.

None of that makes the feature visible in use. It makes it *answerable*
when asked, which is a different thing.

## 12. Transient feedback may borrow a surface, then give it back

Milestone 13.2 gave the hairline a single owner: the album tint, on one
cross-fade shared with the panel, because two animations of the same
colour can only drift apart. Milestone 14.1 then declined to acknowledge a
learned position on that edge, citing exactly that rule.

That was too strict, and the distinction that resolves it is between
*owning* a surface and *borrowing* one. Persistent decoration owns the
hairline; an acknowledgement may take it for half a second and hand it
back — provided the borrowing is built so that giving it back is
guaranteed rather than remembered to be done:

- the glow is a mix applied **at paint time**, over whatever the tint
  currently says. It is never written into the tint state, so a cover
  arriving mid-glow cross-fades underneath it and cannot capture a warmed
  edge as its starting colour.
- the shape starts and ends at zero — a half sine, one property — so
  there is no step at either boundary and no value to restore.
- returning the surface is the animation reaching its end, not a piece of
  cleanup that could be skipped.

Stated generally: **a transient may borrow what a persistent thing owns,
if the loan is structural.** If handing it back depends on remembering to
hand it back, the answer is still no.

---

## What this is not

Not a style guide, and not a promise about the future. Several of these
principles were arrived at by getting the opposite wrong first — the light
palette that shipped broken because the worst backdrop flips with the
mode, the tint that looked switched off in light mode, the line change
that played twice. They are written down so that the next decision starts
from what was learned rather than from what seemed obvious.
