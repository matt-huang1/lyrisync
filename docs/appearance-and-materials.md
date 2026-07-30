# Appearance, materials and window behaviour

What the window is made of, and how it sits on the system.

## The material is real

The blur behind the lyrics is an `NSVisualEffectView` slid under the Qt
content — `hudWindow` material, behind-window blending — not a painted
approximation of one. Qt paints its background as a **scrim** over it: a
translucent fill rather than an opaque one, so the material shows through.

The single most important thing about that arrangement is the order of
priorities. **Legibility over an arbitrary background outranks the
material.** The scrim's alpha is measured so the sung line clears 4.5:1
with no blur behind it at all, so a material that fails to attach, or
renders pale on some future macOS, costs depth and never readability. See
[contrast and accessibility](contrast-and-accessibility.md).

## Blur and translucency are mutually exclusive

macOS renders behind-window blur only while the window's `alphaValue` is
exactly 1. At 0.92 the document underneath came through *sharp*; at 1.0 it
dissolved completely. Dimming either native view instead of the window
does not save it.

Verified by screenshot, not by API documentation, which says none of this.

So the window starts fully opaque, and the scroll-to-dim gesture is
understood as a deliberate trade: you spend the frost to see through. It
also means dimming needs no plumbing of its own — the opacity rides on the
`NSWindow`'s alpha, which dims the material too.

## Following light and dark

The window follows the system appearance in both directions, live, with no
relaunch and **no setting**. macOS already answers this question; a toggle
would be a second source of truth for it.

Following is done through Qt's `colorScheme` / `colorSchemeChanged`, not a
pyobjc KVO observer or a distributed notification. The Cocoa plugin
already watches `NSApp.effectiveAppearance` and republishes it, so this
costs no pyobjc and cannot disagree with Qt's own palette.

The appearance is still set *explicitly* on the material rather than left
to inherit — because the scrim painted on top comes from the palette this
app resolved, and one answer must drive both or they disagree mid-
transition.

macOS's Auto (sunset/sunrise) transition is not a separate path. Flipping
dark mode via System Events leaves `AppleInterfaceStyleSwitchesAutomatically`
at 1 — measured — so a scheduled change and a manual one are the same
`effectiveAppearance` change, and nothing in the code knows about sunset.

## Accessory activation policy

The app runs under `NSApplicationActivationPolicyAccessory` from the
moment it starts, applied before any window exists. `LSUIElement` is also
set in `Info.plist`: the runtime call is what the code guarantees, the
plist key is what stops macOS showing a Dock icon for the instant before
it runs.

This is what keeps SottoVoce out of the Dock and out of the ⌘-Tab
switcher — and, less obviously, it is what makes the full-screen overlay
work at all. A Regular-policy app triggers a Space switch when activated,
so the overlay would drag you out of the full-screen app you were watching.
Collection-behaviour flags alone do not fix that.

The policy is permanent and unconditional. It is no longer coupled to the
"show on all desktops" toggle, which now owns only collection behaviour
and window level. There is no regular policy to fall back to, so nothing
can bring the Dock icon back.

The window is **unfocusable by design** (`WindowDoesNotAcceptFocus` +
`WA_ShowWithoutActivating`). Everything you do to it — drag, resize,
scroll, right-click — is mouse-only, and none of it takes focus from what
you were typing in.

## Spaces and full screen

Qt defaults its windows to `FullScreenPrimary`. `Primary` and `Auxiliary`
are mutually exclusive in AppKit, so the all-desktops toggle must *clear*
Primary before setting Auxiliary — setting the one you want is not enough.
The resulting native state is verified by reading it back, not assumed.

## Depth: hairline and shadow

The panel has a hairline edge and a shadow, and both are the system's own
answer rather than a drawn imitation.

**The hairline is one _device_ pixel** (`1.0 / devicePixelRatioF`), not one
logical pixel: at 2x those differ by a factor of two, and a two-pixel line
is a border rather than an edge. It is inset by half its own width so it
lands inside the fill — straddling the boundary would put half the stroke
outside the material's mask and read as a second, softer edge beside the
first.

**The shadow is the native `NSWindow` shadow** (`setHasShadow_`), not a
painted one. macOS derives its shape from the alpha channel, so it follows
the rounded corners for free; it sits *outside* the window bounds, where a
painted shadow would have to eat into the panel; and it costs the
compositor nothing per frame. It is cached, so a resize must call
`invalidateShadow` or the old silhouette stays. It survives the opacity
gesture — at `alphaValue` 0.5, `hasShadow` is still true.

The shadow cannot be tinted, and that is a fact rather than a preference:
`NSWindow` exposes `hasShadow`, `invalidateShadow` and `setShadowStyle_`
and nothing about colour. Colouring it would mean abandoning the native
shadow for a painted one, which is the trade this section exists to
refuse.

## Screenshots are the readback

`_material is not None` only proves the view attached. Whether it *blurs*
is a question about pixels, so the check is a capture over a text-heavy
backdrop: sharp glyphs inside the window mean no blur, however healthy the
readback looks.

Two things about that harness were wrong the first time and looked fine:

- An accessory-policy app cannot own a Space, so a `showFullScreen`
  backdrop never comes forward and every shot silently catches the desktop
  instead. (All three captures came back byte-identical.)
- The capture rect must come from the `NSWindow` frame converted out of
  Cocoa's bottom-left origin, because Qt's geometry disagrees with the
  screen by the height of the menu bar.

And a third, found later: the harness must **pump** the Qt event loop
rather than sleeping in it. A `time.sleep` inside the slot doing the
capturing blocks repainting, so every capture comes back identical and the
run looks like a stale-frame problem. macOS *also* hands back a stale
frame on the first capture after a change, so the first one is thrown
away.
