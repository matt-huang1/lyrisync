"""Getting out of the way of a notification.

The lyrics window floats at the status level and a notification banner is
drawn *below* it — measured, in the window list: the window sits at level
25 and Notification Centre's at 21. So when a banner arrives over the same
part of the screen, this app is the thing covering somebody's mail. That is
the whole reason this layer exists, and it is why the verb is *yield*:
nothing is competing for attention, the window is simply in front of
something it has no business being in front of.

**Fading, never moving.** Moving would fight per-app position memory for
ownership of where the window lives, and would then have to answer when
and where to move back — two questions with no good answer, against one
that has an obvious one. Fading borrows nothing that has to be given back
except a number.

## No permission is needed, and that was measured rather than assumed

``CGWindowListCopyWindowInfo`` is the mechanism, and the worry about it is
real: it is the same family of calls that puts a Screen Recording prompt in
front of the user. It does not, for this. Verified on macOS 26.5.2 with a
throwaway ad-hoc-signed bundle carrying an identifier that had never been
granted anything:

- ``CGPreflightScreenCaptureAccess()`` answered 0 before the call and 0
  after it, so nothing was silently acquired. (This module does not call
  it — that is the probe's reading, and a test forbids the symbol here.)
- The call returned the full list — 166 windows — with ``kCGWindowOwnerName``,
  ``kCGWindowOwnerPID``, ``kCGWindowLayer``, ``kCGWindowBounds`` and
  ``kCGWindowIsOnscreen`` all present and correct.
- No prompt appeared: nothing new arrived on screen for ten seconds
  afterwards, and the TCC subsystem's log never named the probe.

One field *is* withheld, and it is the one that would need permission to
deserve: ``kCGWindowName``, the window's title, came back absent for
160 of those 166 windows. **Nothing here asks for it.** Reading the titles
of other people's windows is exactly the thing worth a prompt, and this
app has no use for them — it needs to know that a notification is on
screen, not what it says.

## Keyed on the bundle identifier, never the owner's name

``kCGWindowOwnerName`` is LOCALISED — it reads "Notification Centre" on
this machine and "Notification Center" on a US system, and something else
again in French. A string match on it would work for the developer and
quietly never fire for half the people who ran it. So the owner's process
identifier is resolved to a bundle identifier, which is not localised and
is the same thing the rest of this app keys apps on.

``com.apple.controlcenter`` is deliberately NOT in the set, and it is the
trap this design walked into first: Control Centre owns *eleven*
permanently on-screen windows — one per menu bar item, measured — so an
app that yielded to it would fade once, on the first poll, and never come
back.

## One window covers both cases

Banners and the Notification Centre panel are the same window: number
54338 in the run this was measured in, always present in the full window
list, flipping ``kCGWindowIsOnscreen`` from absent to true for the five
seconds a banner is up and for as long as the panel is open. So there is
one rule and not two, and ``kCGWindowIsOnscreen`` — which the docs describe
as optional and which is in fact present only while it is true — is the
signal.

## What "overlaps" can honestly mean

The rectangle that window reports is the whole display: (0, 0 1710x1107)
on a 1710x1107 screen, both for a banner in the top-right corner and for
the full-height panel. macOS exposes no rectangle for the banner itself —
the banner is drawn inside that host window and the only public way to
find out where would be to capture its pixels, which is precisely the
thing that needs the permission this design avoids.

So the intersection here is computed against the rectangle the system
actually reports, and what that buys today is the display test: a
notification on the built-in screen does not fade a window that is over on
an external one. On a single display it means any notification while the
window is showing. That is stated rather than dressed up, and the
alternative was worse — a banner rectangle guessed from where banners
usually appear would be a number picked by eye, in a project where the
scrim alpha and the tint chroma are not, and it would be wrong the first
time Apple moved them.

Pure and Qt-free apart from one door: the rules can be checked without a
display, a notification, or pyobjc.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional, Sequence

from lyrisync.geometry import intersects

logger = logging.getLogger(__name__)

# A rectangle as (x, y, width, height), top-left origin — the same
# convention as flight.py, and the same one CGWindowListCopyWindowInfo
# reports bounds in, which is what lets the two rectangles be compared
# without a conversion between them.
Rect = tuple[int, int, int, int]

# Who counts as the notification system. One identifier, not a family:
# see the module docstring on why Control Centre is not here.
NOTIFICATION_BUNDLE_ID = "com.apple.notificationcenterui"

# How faint the window goes while something is over it.
#
# An absolute ceiling on opacity rather than a factor to multiply by. A
# factor would mean a user who had already dimmed the window to the floor
# yielding to 0.04 — five per cent of a window, which is the "reads as
# having closed" case below — while a ceiling means the same destination
# whatever they set, and the fade simply has less to travel.
#
# Below _MIN_OPACITY (0.25) on purpose, so as things stand this ceiling is
# beneath anything the user can choose and `min` never picks their side.
# That makes the min a guarantee rather than a live branch — the property
# it pins is that yielding is never *brighter* than what was asked for,
# which is what keeps the rule honest if either constant ever moves.
#
# The floor exists so the user cannot lose the window by scrolling; this is
# transient, reverses itself, and is the app admitting it is in the way, so
# it is allowed to go further than the user can. Not zero: a window that
# vanishes completely reads as having closed, and the one that comes back
# reads as a new one arriving.
#
# The VALUE is set by eye, at the point where the window has plainly
# receded without appearing to have gone, and the measurement was taken
# afterwards to check it rather than to justify it — the same order 12b
# settled on when contrast headroom turned out not to be aesthetic
# headroom. From real pixels, a real banner, the window over it:
#
# - interference, as mean |channel difference| from the banner as macOS
#   drew it, over the window's own rectangle: 132.7/255 at full opacity,
#   20.2/255 here. 84.8% of it gone.
# - the banner's separation from the app behind it, which is what makes it
#   read as a floating object at all: 9.91:1 alone, 1.50:1 with the window
#   over it at full opacity, 7.63:1 here.
#
# And one correction worth keeping, because the obvious assumption is
# wrong: the banner's OWN text contrast is never the casualty. Cropped to
# the banner's interior it measures 8.32:1 alone, 8.98:1 here and 12.90:1
# under a fully opaque window — it RISES, because this app's pale panel
# brightens the banner's pale body while its dark ink barely moves. Swept
# across every ceiling from 0 to 1 it never approaches 4.5:1. So no
# legibility threshold picks this number, and claiming one did would be
# inventing a measurement that says something else.
YIELD_CEILING = 0.15

# How long the fade takes, each way. The same 260ms as one phase of a line
# change, as the travel to a remembered position and as the flight to the
# menu bar — this window should only have one sense of how fast it moves.
YIELD_MS = 260

# How often to look. A banner is on screen for about five seconds
# (measured: 5.1s, twice), so this starts the fade within a third of a
# second of one arriving and the window is out of the way well inside the
# first fifth of its life. Measured at 0.652ms per poll, which is 0.34% of
# one core at this interval — against 2.3% for the line change at a line
# every four seconds.
#
# Deliberately not derived from the monitor's own 300ms: they are the same
# number for different reasons, and a change to how often Spotify is asked
# about its position should not change how often the window server is asked
# about its windows.
POLL_SECONDS = 0.3

# Why the window is not yielding. Returned by yield_refusal so the caller
# can log the reason from the same rule that decided it — the shape
# app_positions.py settled on, for the reason it settled on it: a log line
# that restates the rule is a second copy of the rule.
DISABLED = "the layer is off"
HIDDEN = "the window is hidden"
SYNCING = "a sync pass is in progress"
FLYING = "the window is mid-flight"


def yield_refusal(
    *,
    enabled: bool,
    visible: bool,
    syncing: bool,
    flying: bool,
) -> Optional[str]:
    """Why the window may not yield right now, or None if it may.

    ``syncing`` is here for principle 6 rather than for tidiness: a sync
    pass is the user tapping a button on this window, once per line, and
    fading it to 0.15 underneath them would be a decorative feature
    breaking an essential one. A banner during a pass is simply covered.

    ``flying`` because a hide or show is already animating the same
    opacity toward the menu bar, and a window on its way out has nothing
    to get out of the way of.
    """
    if not enabled:
        return DISABLED
    if not visible:
        return HIDDEN
    if syncing:
        return SYNCING
    if flying:
        return FLYING
    return None


def may_yield(**kwargs) -> bool:
    """Derived from the refusal, never decided beside it."""
    return yield_refusal(**kwargs) is None


def in_the_way(window: Rect, occupied: Sequence[Rect]) -> bool:
    """Whether any of ``occupied`` covers part of ``window``.

    Any overlap at all, not a fraction of the window: with the system
    reporting a whole display there is no fraction to threshold, and
    inventing one would be describing a rectangle this code has never
    seen. If macOS ever reports a tighter rectangle this narrows with it
    for free, which is the other reason the intersection is real
    arithmetic rather than a presence check.
    """
    return any(intersects(window, rect) for rect in occupied)


def yielded_opacity(user_opacity: float, level: float) -> float:
    """The window's opacity at ``level`` of the way into a yield.

    ``level`` runs 0 (the user's own setting, untouched) to 1 (as faint as
    yielding goes). One signed-ish property with the whole shape in it,
    the same as the line change's ``progress`` and the glow's phase,
    because a fade out and the fade back are one quantity moving rather
    than two states to keep in step.

    Never brighter than the user asked for, at any level, for any input.
    That is the property, and it holds even for an opacity below the
    ceiling — which the window's own floor makes unreachable today, and
    which is exactly why it is worth pinning here rather than relying on
    the floor to keep being where it is.
    """
    level = max(0.0, min(1.0, float(level)))
    target = min(user_opacity, YIELD_CEILING)
    return user_opacity + (target - user_opacity) * level


def duration_ms(start: float, end: float) -> int:
    """How long to spend moving between two yield levels.

    Proportional, so a banner that clears while the window is still fading
    comes back from wherever it got to in the time that part of the
    journey is worth — the flight's rule, and the tint cross-fade's, and
    for the same reason: the user is looking at where it is, not at where
    it was going. At least one millisecond, because a zero-length
    animation never reports finishing and the level would be left stuck
    part-way.
    """
    return max(1, round(YIELD_MS * abs(float(end) - float(start))))


def _quartz():
    """CoreGraphics and NSRunningApplication, or None where there are not
    any.

    The single door, the same shape as hotkey._carbon(),
    login_item._main_app_service() and frontmost._workspace(). Returns None
    off macOS and without pyobjc, so every caller has one branch to handle
    and the suite has one seam to shut — and it needs shutting: without it
    a test would read the list of every window open on the developer's
    machine.
    """
    if sys.platform != "darwin":
        return None
    try:
        import Quartz
        from AppKit import NSRunningApplication
    except Exception:  # pragma: no cover - pyobjc missing
        logger.info("Quartz unavailable — not yielding to notifications")
        return None
    return Quartz, NSRunningApplication


def occupied_rects() -> tuple[Rect, ...]:
    """Where the notification system is on screen, right now.

    Empty when nothing of it is showing, which is the ordinary answer and
    the cheap one. Empty too when the window list cannot be read at all —
    a layer that cannot see is a layer that does not fade, rather than one
    that fades and stays faded.

    The process identifiers are re-resolved on every poll rather than
    cached. It costs one AppKit call (0.129ms of the 0.652ms measured) and
    it is what makes the layer survive Notification Centre being
    restarted, which happens on its own and would otherwise leave a cached
    identifier matching nothing — or, worse, matching whatever process
    later inherited the number.
    """
    door = _quartz()
    if door is None:
        return ()
    quartz, running_application = door
    try:
        apps = running_application.runningApplicationsWithBundleIdentifier_(
            NOTIFICATION_BUNDLE_ID
        )
        pids = {app.processIdentifier() for app in (apps or ())}
        if not pids:
            return ()
        # OnScreenOnly rather than the whole list: it is the signal wanted
        # (a window that is not on screen is not in the way) and it is
        # eight times cheaper — 0.123ms against 2.227ms for every window,
        # measured. ExcludeDesktopElements drops the desktop picture and
        # its icons, which are never over this window.
        options = (
            quartz.kCGWindowListOptionOnScreenOnly
            | quartz.kCGWindowListExcludeDesktopElements
        )
        listing = quartz.CGWindowListCopyWindowInfo(options, quartz.kCGNullWindowID)
        owner_key = quartz.kCGWindowOwnerPID
        bounds_key = quartz.kCGWindowBounds
        rects = []
        for window in listing or ():
            if window.get(owner_key) not in pids:
                continue
            bounds = window.get(bounds_key) or {}
            rects.append(
                (
                    int(bounds.get("X", 0)),
                    int(bounds.get("Y", 0)),
                    int(bounds.get("Width", 0)),
                    int(bounds.get("Height", 0)),
                )
            )
    except Exception:
        logger.debug("could not read the window list", exc_info=True)
        return ()
    return tuple(rects)
