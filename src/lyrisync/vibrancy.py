"""Constants for the NSVisualEffectView behind the window's content.

No pyobjc here — same approach as macspaces.py: these are Apple's
ABI-stable enum values, and the window verifies the result by readback
rather than assuming the call landed.

The overlay wants a real macOS blur rather than a painted rectangle, but it
is a HUD floating over a stranger's screen, not a document window. Two
choices follow from that:

- ``MATERIAL_HUD_WINDOW`` is the semantic material for exactly this, so the
  blur tracks whatever Apple decides a HUD should look like.
- The effect view is pinned to the dark appearance. A material that
  followed the system appearance would go bright over a white document in
  light mode and take the white lyric text with it; legibility over an
  arbitrary background outranks matching the user's theme.

The window still paints a thin scrim on top. That is what guarantees
contrast when the material underneath happens to be pale, and it is the
whole background when vibrancy is unavailable.
"""

from __future__ import annotations

# NSVisualEffectBlendingMode: blur what is BEHIND the window, not siblings
# within it. Requires a non-opaque window, which the translucent frameless
# overlay already is.
BLENDING_MODE_BEHIND_WINDOW = 0

# NSVisualEffectState: keep blurring even when the app is not active. The
# window never takes focus by design, so FollowsWindowActiveState would
# leave it inert forever.
STATE_ACTIVE = 1

# NSVisualEffectMaterial.hudWindow
MATERIAL_HUD_WINDOW = 13

# NSAutoresizingMaskOptions: track the content view through every resize.
AUTORESIZE_WIDTH_SIZABLE = 1 << 1
AUTORESIZE_HEIGHT_SIZABLE = 1 << 4
AUTORESIZE_FILL = AUTORESIZE_WIDTH_SIZABLE | AUTORESIZE_HEIGHT_SIZABLE

# NSWindowOrderingMode.below — the effect view goes UNDER the Qt view, or
# it would blur the lyrics into illegibility.
WINDOW_BELOW = -1

# Pinned appearance for the material (see the module docstring).
DARK_APPEARANCE = "NSAppearanceNameDarkAqua"


def autoresize_mask() -> int:
    """Width- and height-sizable, so the material fills the window at every
    size without us tracking resizes by hand."""
    return AUTORESIZE_FILL
