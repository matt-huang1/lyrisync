"""Pure flag arithmetic for the macOS all-desktops toggle. No pyobjc here —
values are Apple's ABI-stable constants, verified by runtime readback.

Qt gives its windows NSWindowCollectionBehaviorFullScreenPrimary by
default. Primary and FullScreenAuxiliary are mutually exclusive: with both
set, Primary wins and the window cannot join another app's full-screen
Space (it vanishes on swipe). Enabling must therefore clear Primary, not
just OR our flags in.
"""

from __future__ import annotations

CAN_JOIN_ALL_SPACES = 1 << 0   # NSWindowCollectionBehaviorCanJoinAllSpaces
FULL_SCREEN_PRIMARY = 1 << 7   # NSWindowCollectionBehaviorFullScreenPrimary
FULL_SCREEN_AUXILIARY = 1 << 8  # NSWindowCollectionBehaviorFullScreenAuxiliary

# Qt's stay-on-top maps to NSModalPanelWindowLevel (8); floating over
# full-screen Spaces is reliable at status level.
STATUS_WINDOW_LEVEL = 25       # NSStatusWindowLevel


def all_desktops_behavior(current: int) -> int:
    """Collection behavior with the all-desktops toggle on: joins every
    Space including full-screen ones. Unrelated bits are preserved."""
    return (current & ~FULL_SCREEN_PRIMARY) | CAN_JOIN_ALL_SPACES | FULL_SCREEN_AUXILIARY
