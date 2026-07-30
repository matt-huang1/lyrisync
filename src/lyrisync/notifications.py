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

## The rectangle is the whole display, and nothing distinguishes the cases

The rectangle that window reports is the whole display: (0, 0 1710x1107) on
a 1710x1107 screen, both for a banner in the top-right corner and for the
panel. macOS exposes no rectangle for the banner itself.

Milestone 16 shipped the intersection against that rectangle, which meant
the window dimmed for a banner it was nowhere near. 16.1 went looking for
something better and **there is nothing**. Every field of the notification
window was dumped in three states — nothing showing, a banner up, the panel
open — and compared:

| field | banner | panel |
|---|---|---|
| `kCGWindowNumber` | 54338 | 54338 |
| `kCGWindowLayer` | 21 | 21 |
| `kCGWindowBounds` | 0,0 1710x1107 | 0,0 1710x1107 |
| `kCGWindowAlpha` | 1.0 | 1.0 |
| `kCGWindowSharingState` | 1 | 1 |
| `kCGWindowStoreType` | 1 | 1 |
| `kCGWindowMemoryUsage` | 2368 | 2368 |
| index in the on-screen list | 116 | 116 |
| the set of keys macOS returned | identical | identical |

**Identical in every field.** The window count does not change either — one
window before, one during, one for the panel. `kCGWindowMemoryUsage` is
2368 even with nothing on screen, so it is not a backing-store size and
tells us nothing about what is drawn. The only signal in the whole record is
`kCGWindowIsOnscreen` appearing, and it says *something* is showing, never
what or where.

So the choice was between the whole display and a heuristic, and this
module now uses a heuristic — stated as one, here and in the docs.

## The heuristic: the rightmost strip of the display

Notifications appear in one place, and where that is was **measured from
pixels** rather than assumed. Not at runtime — that would need the
permission this whole design avoids — but once, in a harness, by diffing
screen captures with the notification up against captures without it, over
five trials per case with the results intersected so that anything moving
for its own reasons drops out:

| case | rectangle, in points | right edge |
|---|---|---|
| one short banner | 1349, 54  346x62 | 1695 |
| a long wrapped banner | 1343, 44  360x120 | 1702 |
| three stacked banners | 1340, 38  368x96 | 1708 |
| the Notification Centre panel | 1294, 34  416x608 | 1710 |

Every case is right-anchored and inside the rightmost 416 points. The panel
is the widest, and its height depends on how much is in it — 608 to 713
points measured, and it scrolls beyond that, so there is no maximum height
to find.

Hence the region: **the reported rectangle, narrowed to its rightmost
``PLAUSIBLE_STRIP_WIDTH`` points, full height.** That fixes the reported
bug — a window on the left three quarters of the screen is left alone — and
keeps what milestone 16 had, because the rectangle being narrowed is still
the one macOS reported, so a notification on another display still cannot
reach this one. It also narrows *further* for free: ``min`` means that if
Apple ever reports a real banner rectangle, that rectangle is used as-is
rather than widened back out to a strip.

What it gets wrong, plainly: a window parked in the bottom-right corner
still fades for a banner in the top-right one, because the panel can reach
that far and nothing distinguishes the panel from a banner. Full height is
the honest over-approximation, and over-approximating is the right
direction — a layer whose whole job is to get out of the way should fail by
moving when it needn't, not by sitting there when it should.

## Why the fade cannot be proportional

The obvious refinement — fade in proportion to how much of the window is
actually covered, or to how far the panel has been pulled open — is **not
implementable without pixel capture**, and pixel capture is exactly the
permission this feature exists without.

It needs the real rectangle, and macOS reports the display. The only public
route to the real one is reading the notification window's pixels
(``CGWindowListCreateImage`` and friends), which is what the Screen
Recording prompt guards. So the choice is a proportional fade behind a
permission dialogue, or a fixed fade behind none. This picks none, and the
fixed ceiling is the price.

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

# How wide the strip of a display is where notifications actually appear.
#
# THE ONE HEURISTIC IN THIS MODULE, and named so it reads as one. macOS
# reports the whole display for both a banner and the panel, and 16.1
# established that nothing in the window record tells the two apart, so
# there is no way to derive this — only to measure where notifications go
# and take the answer as a constant.
#
# Measured from pixels (screen captures diffed with the notification up
# against captures without it, five trials per case, intersected): a short
# banner occupies 346pt of width, a long one 360, three stacked 368, and the
# Notification Centre panel 416 — every one of them anchored to the right
# edge, the widest reaching it exactly.
#
# 440 rather than the measured 416, and the margin is deliberate rather than
# nervous: banner and panel widths move with the system text size, with
# localisation, and with whatever Apple does next, and the two failure
# directions are not symmetric. Too wide fades the window when it did not
# strictly need to; too narrow leaves it sitting over somebody's mail, which
# is the entire thing this layer exists to stop.
PLAUSIBLE_STRIP_WIDTH = 440

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

# How often to look while nothing is over the window. A banner is on screen
# for about five seconds (measured: 5.1s, twice), so this starts the fade
# within a third of a second of one arriving and the window is out of the way
# well inside the first tenth of its life.
#
# Deliberately not derived from the monitor's own 300ms: they are the same
# number for different reasons, and a change to how often Spotify is asked
# about its position should not change how often the window server is asked
# about its windows.
POLL_SECONDS = 0.3

# How often to look while the window IS faded, which is a different
# question. Coming back late is worse than going away late: the window going
# faint a third of a second into a banner nobody has read yet costs nothing,
# while staying faint a third of a second after the screen is clear is the
# user waiting for their own lyrics.
#
# So the interval drops while yielded and goes back afterwards. Measured at
# 0.105ms of CPU per poll, this is 0.105% of one core — and only for the few
# seconds a notification is actually up, against 0.035% the rest of the time.
# Restore latency goes from one 300ms poll plus the 260ms fade to one 100ms
# poll plus the fade.
#
# Not lower than 100ms because the fade is 260ms and dominates: halving this
# again would buy 50ms off a ~360ms total and cost twice the polling.
YIELDED_POLL_SECONDS = 0.1

# Why the window is not yielding. Returned by yield_refusal so the caller
# can log the reason from the same rule that decided it — the shape
# app_positions.py settled on, for the reason it settled on it: a log line
# that restates the rule is a second copy of the rule.
DISABLED = "the layer is off"
HIDDEN = "the window is hidden"
SYNCING = "a sync pass is in progress"
FLYING = "the window is mid-flight"


def poll_interval_seconds(yielding: bool) -> float:
    """How long until the next look, given what the window is doing now.

    Two intervals rather than one, because the two directions are not worth
    the same: going away late costs nothing (the banner has only just
    arrived and nobody has read it), coming back late is the user waiting
    for their own lyrics. So the layer looks harder exactly while it has
    something to undo — which is also the only time the faster rate is
    being paid for.
    """
    return YIELDED_POLL_SECONDS if yielding else POLL_SECONDS


def plausible_region(reported: Rect) -> Rect:
    """Where a notification described by ``reported`` can actually be.

    THE HEURISTIC, and the name says so. macOS reports the whole display
    for both a banner and the panel and nothing in the record distinguishes
    them (see the module docstring), so this narrows the reported rectangle
    to the rightmost ``PLAUSIBLE_STRIP_WIDTH`` points of itself.

    Right-anchored, full height, and derived FROM the reported rectangle
    rather than from a screen this module went and asked about — which is
    what keeps milestone 16's one real property intact: the rectangle being
    narrowed is the display the notification is on, so a banner on another
    display still cannot reach a window over here.

    ``min`` rather than an unconditional strip, so a reported rectangle
    already narrower than the strip is handed back untouched. That is the
    forward-compatible case, not a defensive one: the day macOS reports a
    real banner rectangle, this stops being a heuristic by itself.
    """
    x, y, width, height = reported
    strip = min(width, PLAUSIBLE_STRIP_WIDTH)
    return (x + width - strip, y, strip, height)


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


def in_the_way(window: Rect, reported: Sequence[Rect]) -> bool:
    """Whether a notification described by ``reported`` covers ``window``.

    Takes what the system said and narrows it here, rather than expecting
    the caller to have done it: there is one path from a reported rectangle
    to an answer, so there is no version of this that forgets the
    heuristic and compares against a whole display again. That is what
    milestone 16 shipped, and it is the bug 16.1 exists to fix.

    Any overlap at all, not a fraction of the window. A threshold would be
    describing how much of a rectangle this code has never seen is over
    another, and the strip is already an over-approximation — thresholding
    an approximation is precision theatre.
    """
    return any(intersects(window, plausible_region(rect)) for rect in reported)


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
