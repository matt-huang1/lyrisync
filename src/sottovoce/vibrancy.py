"""Constants for the NSVisualEffectView behind the window's content.

No pyobjc here — same approach as macspaces.py: these are Apple's
ABI-stable enum values, and the window verifies the result by readback
rather than assuming the call landed.

The overlay wants a real macOS blur rather than a painted rectangle, but it
is a HUD floating over a stranger's screen, not a document window. Two
choices follow from that:

- ``MATERIAL_HUD_WINDOW`` is the semantic material for exactly this, so the
  blur tracks whatever Apple decides a HUD should look like — and it is
  the material in BOTH appearances, because "HUD window" is a role, not a
  colour: Aqua renders it as a pale panel and Dark Aqua as a smoked one,
  which is exactly the per-mode material this wants.
- The effect view used to be pinned to the dark appearance, because a
  material that went bright over a white document would have taken the
  white lyric text with it. That was true while the text was always
  white. Now the palette turns dark in light mode (appearance.py), so the
  pale panel is the right one and the reason to pin dark is gone.

The appearance is still set explicitly rather than left to inherit. Not
for the old reason — for a new one: the scrim painted on top comes from
the palette this app resolved, and the material underneath must be the
same mode or the two disagree for as long as the mismatch lasts. Setting
both from one answer is what makes that impossible.

The window still paints a thin scrim on top. That is what guarantees
contrast when the material underneath happens to be pale, and it is the
whole background when vibrancy is unavailable.

One thing the API does not say: the behind-window blur only renders while
the window's alphaValue is exactly 1. Any translucency at all — the
window's or either native view's — leaves the material as a flat tint with
the backdrop showing through sharp. The window therefore starts at full
opacity, and the scrim is sized to carry the sung line on its own for
every value below it.
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

# NSAppearance names. Set on the effect view to match the palette the
# window is painting with (see the module docstring).
LIGHT_APPEARANCE = "NSAppearanceNameAqua"
DARK_APPEARANCE = "NSAppearanceNameDarkAqua"


def appearance_name(dark: bool) -> str:
    """The NSAppearance the material should adopt for this mode."""
    return DARK_APPEARANCE if dark else LIGHT_APPEARANCE


# CACornerMask. The layer belongs to an NSVisualEffectView, which is not
# flipped, so its origin is the BOTTOM left: MinY is the pair of corners
# under the window and MaxY the pair against the menu bar. Stated here with
# the rest of Apple's enum values, and — like every one of them — verified
# by readback rather than believed.
CORNER_BOTTOM_LEFT = 1 << 0
CORNER_BOTTOM_RIGHT = 1 << 1
CORNER_TOP_LEFT = 1 << 2
CORNER_TOP_RIGHT = 1 << 3
ALL_CORNERS = (
    CORNER_BOTTOM_LEFT | CORNER_BOTTOM_RIGHT | CORNER_TOP_LEFT | CORNER_TOP_RIGHT
)
BOTTOM_CORNERS = CORNER_BOTTOM_LEFT | CORNER_BOTTOM_RIGHT


def masked_corners(docked: bool) -> int:
    """Which of the material's corners are rounded.

    The docked window squares off the two against the menu bar so the
    panel reads as the bar's own band continuing downwards. The material
    is a separate native view under the painted scrim, and it has to be
    told the same thing: a blur still rounded at the top under a scrim
    that is not would show the desktop through two small notches at the
    very corners the shape exists to remove.
    """
    return BOTTOM_CORNERS if docked else ALL_CORNERS
