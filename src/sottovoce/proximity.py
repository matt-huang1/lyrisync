"""Getting out of the way of the pointer.

The opacity gesture — Option and a scroll over the window — is the answer
this app has always had to "you are in front of the thing I am doing". It
works, and it is almost never used: it has to be remembered, aimed and
undone, which is three deliberate acts to solve a problem that lasts about
two seconds. This layer is the same wish answered without being asked. The
pointer arriving over the window IS the request, and the window either
steps aside or stops being in the way where it stands.

Two behaviours, because they solve the same problem for two different
things underneath, and neither is a better version of the other:

- **Dodge** moves. It vacates the rectangle it was sitting in, so what was
  under the window is visible AND clickable, and it comes back the moment
  the pointer leaves. For a window parked over something you look at.
- **Ghost** stays put, fades to ``GHOST_CEILING`` and stops accepting
  mouse events, so clicks land on whatever is behind it. Nothing moves, so
  nothing has to be moved back. For a window parked over something you
  click.

Off is the third value and the default, and off means the pointer is not
being watched at all — the window's poll runs for the compact layout's
controls or for this, and with the compact layout off and this off it does
not run.

## Why any of this is a poll rather than an event

The same reason the compact layout's controls are, and it is not a
preference: macOS delivers enter, leave and mouse-moved events only to the
ACTIVE application. This one runs under the accessory activation policy
with a window that refuses focus, so it is never active and there are no
hover events for it to miss. What still answers is the pointer's own
position, which is a screen coordinate and belongs to nobody. So the
window asks, on a timer, and only while something could act on the answer.

Ghost makes that mechanism load-bearing twice over: a window that ignores
mouse events would not hear an enter event even if one were coming, so the
thing that has to notice the pointer LEAVING is the one thing the click
pass-through cannot switch off.

## Hysteresis, and what it is anchored on

The trigger region is anchored on where the window BELONGS, not on where
it currently is. That is what stops Dodge oscillating: a window that fled
the pointer is no longer under it, and a region that followed the window
would report "clear" one poll after reporting "covered", every time, for
ever. So the region is the home rectangle, and the window stepping aside
cannot change the answer.

Leaving is a wider question than arriving, and it takes both rectangles:
the pointer has to be clear of where the window belongs AND of where it
actually is, each by ``RELEASE_MARGIN``. Without the second half a dodged
window could never be caught — the pointer chasing it would leave the home
region, the window would come home, and it would be under the pointer's
old position rather than its new one. With it, following the window means
the window stays where it went and can be grabbed like any other.

## Edge triggered, not level triggered

The behaviour BEGINS on the pointer arriving and ends on it leaving, and
nothing restarts it in between. That one rule is what makes every
suspension below tolerable: a sync pass starting over a dodged window
brings the window home and leaves it there, and when the pass ends with
the pointer still on the window nothing steps aside under the hand that is
still using it. The pointer has to leave and come back, which is a thing
the user does rather than a thing that happens to them.

Pure and Qt-free, so the region, the hysteresis, the destination and the
gate can all be checked without a screen or a pointer. What this module
cannot answer — whether it FEELS like the window got out of the way — is
verified by hand.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from sottovoce.geometry import GRAB_MARGIN, clamped_position

logger = logging.getLogger(__name__)

# A rectangle as (x, y, width, height), top-left origin, and a point as
# (x, y). The same convention as geometry.py, flight.py and
# notifications.py, which is what lets these rectangles be handed straight
# to ``clamped_position`` without a conversion in between.
Rect = tuple[int, int, int, int]
Point = tuple[int, int]

# The three values of the setting. The constant IS the label: it is what
# the menu draws, what the settings file stores and what the code compares
# against, and one string cannot disagree with itself. A stored value that
# is none of these is a hand-edited or an outgrown preference and falls
# back to OFF, the same rule the speech rate and the strip's type size are
# held to.
OFF = "Off"
DODGE = "Dodge"
GHOST = "Ghost"
MODES = (OFF, DODGE, GHOST)

# How far past the window the pointer must go before the window comes back,
# and — the same number for the same reason — how much room the dodge
# leaves between where the window was and where it went.
#
# SET BY EYE, and this says so. What was measured is what it is NOT for:
# sampled at the poll's own 100ms for 30 seconds with nothing touching the
# trackpad, the pointer reported the identical coordinate 300 times out of
# 300, so there is no jitter here to absorb and a margin of zero would not
# flap on a resting hand. The band exists for the deliberate small
# movement — a pointer parked on the window's edge while its owner reads
# something — and for that there is no threshold to find, only a distance
# that reads as "you have left" rather than as "you twitched".
#
# 12 points, and the cost of it is one poll: a pointer crossing the band at
# 120 points per second spends 100ms inside it, which is exactly the
# interval the window is asking at anyway, so below about that speed the
# hysteresis is what delays the return and above it the poll is.
#
# It doubles as the dodge's clearance because it is the same question
# asked in the other direction. A window that stepped aside by exactly its
# own height would put its new edge on the pointer's old position, which
# is inside the region it just left; leaving RELEASE_MARGIN between the
# two rectangles means the window lands exactly where it would count as
# clear.
RELEASE_MARGIN = 12
CLEARANCE = RELEASE_MARGIN

# How faint the window goes while the pointer is over it in Ghost.
#
# Bounded above and below by two arguments, and set by eye between them —
# which is what this comment exists to say, because the first attempt at
# it was set by arithmetic and looking at the result corrected it.
#
# ABOVE: strictly under the window's own opacity floor of 0.25. A ghost
# that stopped anywhere the user could already have dragged the window to
# by hand would be a mode that adds a mechanism and no destination.
#
# BELOW: not gone. A window that vanishes completely reads as having
# closed, and the one that comes back reads as a new one arriving. This
# ghost lasts as long as a hand is on the window rather than the five
# seconds a banner does, so there is more time for that reading to set in,
# not less.
#
# BETWEEN them, RENDERED AND LOOKED AT — the real window over an editor at
# 0.10, 0.15, 0.20, 0.25 and 0.35, in both appearances. 0.10 is not there
# at all: no panel, no edge, no line. 0.25 is still plainly a window over
# somebody's code. The eye puts "has stepped back but can still be found"
# at 0.15, where a trace of the sung line survives and nothing else does.
#
# The first version of this constant was 0.12, arrived at from the
# contrast arithmetic below and never looked at. On the sheet it is
# indistinguishable from 0.10 — which is to say from nothing. That is
# principle 3 collecting its fee for the fourth time in this project:
# healthy numbers, invisible bug, obvious on sight.
#
# The measurements were then taken to CHECK it rather than to justify it,
# the order 12b settled on when contrast headroom turned out not to be
# aesthetic headroom. Both, against the palettes that ship:
#
# - what survives of the screen underneath. The window covers with the
#   product of this and its own background alpha, so the panel contributes
#   about 14% of each pixel and 86% of what was there is untouched (86.4%
#   dark, 86.1% light).
# - whether the work underneath is still READABLE, which is the only
#   question here with a floor in it: black text on a white page keeps
#   15.6:1 under the dark panel, white on black keeps 15.6:1 under the
#   light one, against the 4.5:1 this app holds anything read for. That
#   floor did not pick the number and could not have — it permits
#   opacities up to 0.51, which is a window nobody would call ghosted.
#   Headroom is not a reason to use it.
#
# One ceiling for both appearances, and that is measured rather than
# assumed: the two agree within 0.3 percentage points of coverage at every
# opacity from 0.05 to 0.25, because the dark panel over a white page and
# the light panel over a black one are very nearly the same subtraction.
#
# It lands on the same value as notifications.YIELD_CEILING and it is
# deliberately not shared with it. Two facts that happen to agree are
# still two facts: that one is where a five-second banner needs the window
# to be, this one is where a hand does, and either is free to move without
# dragging the other with it. One definition is a rule about one fact, not
# about one number.
#
# An absolute ceiling rather than a factor, for the yield's reason: a
# factor would take a user who had already dimmed the window to its floor
# down to 0.04, while a ceiling means the same destination whatever they
# set and the fade simply has less to travel.
GHOST_CEILING = 0.15

# How long the ghost fade takes, each way. The same 260ms as one phase of a
# line change, as the travel to a remembered position, as the flight and as
# the notification yield: this window should only have one sense of how
# fast it moves. Dodge has no constant of its own for the same reason — it
# travels the way every other movement of this window travels.
GHOST_MS = 260

# Why the window is not getting out of the way. Returned by ``refusal`` so
# the caller logs the reason from the same rule that decided it, which is
# the shape app_positions.py settled on and for its reason: a log line that
# restates a rule is a second copy of the rule.
DISABLED = "the layer is off"
HIDDEN = "the window is hidden"
SYNCING = "a sync pass is in progress"
ATTEMPTING = "an echo attempt is waiting on the done button"
EXPLAINING = "the failure register is open"
DRAGGING = "the window is being dragged"
FLYING = "the window is mid-flight"

# What one poll did, for the log. Named rather than reconstructed, for the
# reason every other gate in this app names its answer.
ARRIVED = "arrived"  # the pointer came, and the behaviour began
HELD = "held"  # the pointer is still there and the behaviour is on
LEFT = "left"  # the pointer went, and everything was handed back
SUSPENDED = "suspended"  # something needs the window, so it was handed back
IDLE = "idle"  # nothing to do, which is almost every poll


def mode_from(raw: object) -> str:
    """A stored or clicked value, as one of the three modes.

    Anything else is OFF. A preference file is a thing a person can edit
    and a thing an older version can leave behind, and a value nobody
    recognises should leave the window doing what it does with no layer on
    rather than something invented.
    """
    return raw if raw in MODES else OFF


def refusal(
    *,
    mode: str,
    visible: bool,
    syncing: bool,
    attempting: bool,
    explaining: bool,
    dragging: bool,
    flying: bool,
) -> Optional[str]:
    """Why the window may not get out of the way right now, or None.

    Three of these are the same sentence: the user needs to click this
    window, and a layer that moved it or made it click through would be a
    convenience breaking a function.

    - **syncing** — a tap-to-sync pass is a rhythm game played on a button
      on this window, once per line. Ghosting it would send the taps to
      whatever is behind it and Dodging it would move the target mid-song.
    - **attempting** — the echo loop's attempt phase pauses the song and
      hands the turn over, and the done button is the only way out of it.
      It is also the one state where the compact layout holds its controls
      out whatever the pointer is doing, and this is that same rule seen
      from the other side.
    - **explaining** — the failure register is open because somebody
      clicked the ⓘ to open it. The click that closes it is the same click,
      on the same control.

    The other three are "something else already owns this window": a hand
    on it, a flight to or from the menu bar which owns both the position
    and the opacity until it lands, and a hidden window, which is in
    nobody's way and has no pointer over it.

    Ordered so that the reason a user would be most surprised by is the
    one they are in the middle of doing.
    """
    if mode == OFF:
        return DISABLED
    if syncing:
        return SYNCING
    if attempting:
        return ATTEMPTING
    if explaining:
        return EXPLAINING
    if dragging:
        return DRAGGING
    if flying:
        return FLYING
    if not visible:
        return HIDDEN
    return None


def may_act(**kwargs) -> bool:
    """Derived from the refusal, never decided beside it."""
    return refusal(**kwargs) is None


def contains(point: Point, rect: Rect, margin: int = 0) -> bool:
    """Whether ``point`` is inside ``rect`` grown by ``margin`` on every
    side.

    Inclusive of the boundary, unlike ``geometry.intersects``, and the two
    are answering different questions: that one asks whether two
    rectangles share any AREA, where a shared edge is no area at all. This
    one asks whether a pointer is on a window, and a pointer on the last
    row of pixels a window drew is on that window.
    """
    x, y = point
    rx, ry, width, height = rect
    return (
        rx - margin <= x <= rx + width + margin
        and ry - margin <= y <= ry + height + margin
    )


def still_engaged(
    *, point: Point, home: Rect, current: Rect, engaged: bool
) -> bool:
    """Whether the pointer counts as being at the window.

    Two different questions depending on which way it is going, which is
    the whole of the hysteresis:

    - **arriving**, the region is ``home`` exactly. Where the window
      belongs, so that a window which has stepped aside cannot change the
      answer to whether it should have.
    - **leaving**, the region is ``home`` OR ``current``, each grown by
      ``RELEASE_MARGIN``. The margin is what stops a pointer resting on
      the boundary flapping the window; ``current`` is what makes a dodged
      window catchable, because following it is not leaving.

    ``current`` equals ``home`` whenever the window has not moved, which
    is always in Ghost and between dodges in Dodge, and the second half
    costs nothing there.
    """
    if not engaged:
        return contains(point, home)
    return contains(point, home, RELEASE_MARGIN) or contains(
        point, current, RELEASE_MARGIN
    )


class Approach:
    """Whether the pointer is at the window, and whether the behaviour is
    on because of it.

    Two booleans rather than one, and they are not the same fact.
    ``engaged`` is an observation about the pointer and is true whether or
    not anything may be done about it; ``active`` is what the window is
    currently doing. Keeping them apart is what lets a suspension hand the
    window back without pretending the pointer went away, and it is the
    same separation ``notifications`` keeps between the level and the
    target.

    The transition from not-engaged to engaged is what starts the
    behaviour, and nothing else does. See the module docstring on why that
    is edge triggered.
    """

    def __init__(self) -> None:
        self._engaged = False
        self._active = False

    @property
    def engaged(self) -> bool:
        """Whether the pointer is at the window, refusal or no refusal."""
        return self._engaged

    @property
    def active(self) -> bool:
        """Whether the window is currently standing aside or ghosted."""
        return self._active

    def observe(self, *, inside: bool, refusal: Optional[str] = None) -> str:
        """One poll. Returns what it did, for the caller to log.

        A refusal does not un-engage: whether the pointer is on the window
        is a fact about the pointer, and the gate is about whether to act
        on it. Conflating the two would restart the behaviour the instant a
        sync pass ended, under a hand that is still where it was.
        """
        if refusal is not None:
            was_active, self._active = self._active, False
            self._engaged = inside
            return SUSPENDED if was_active else IDLE
        was, self._engaged = self._engaged, inside
        if not inside:
            was_active, self._active = self._active, False
            return LEFT if was_active else IDLE
        if not was:
            self._active = True
            return ARRIVED
        return HELD if self._active else IDLE

    def stand_down(self) -> None:
        """Stop acting without pretending the pointer left.

        For the user taking hold of the window: wherever they put it is
        where it belongs now, so there is nothing to hand back, and the
        edge rule means nothing steps aside again until the pointer has
        actually gone and come back.
        """
        self._active = False

    def release(self) -> None:
        """Forget both. For switching the mode and for shutdown, where
        the next poll is not coming."""
        self._engaged = False
        self._active = False


def ghost_opacity(base: float, level: float) -> float:
    """The window's opacity at ``level`` of the way into a ghost.

    ``level`` runs 0 (whatever the window was going to be anyway) to 1 (as
    faint as ghosting goes). One signed-ish property with the whole shape
    in it, the same as the line change's ``progress``, the glow's phase and
    the yield's level, because a fade out and the fade back are one
    quantity moving rather than two states to keep in step.

    Never brighter than ``base``, at any level, for any input. ``base`` is
    already the user's own setting with a notification yield folded into
    it, so this composes with the yield rather than arguing with it: a
    banner over a ghosted window leaves the window at whichever of the two
    is fainter, which is the one that is more out of the way.
    """
    level = max(0.0, min(1.0, float(level)))
    target = min(base, GHOST_CEILING)
    return base + (target - base) * level


def fade_ms(start: float, end: float) -> int:
    """How long to spend moving between two ghost levels.

    Proportional, so a pointer that skims the window and leaves brings it
    back from wherever the fade got to in the time that part of the
    journey is worth — the flight's rule, the yield's and the reveal's, and
    for the same reason: the user is looking at where it is, not at where
    it was going. At least a millisecond, because a zero-length animation
    never reports finishing and the level would be left part way.
    """
    return max(1, round(GHOST_MS * abs(float(end) - float(start))))


# -- where a dodge goes ---------------------------------------------------
#
# The window vacates its own footprint rather than sliding just far enough
# to uncover the pointer. Sliding by the smaller amount does answer the
# literal question — the pixel under the pointer is uncovered — and it
# answers nothing else: what somebody reaches into a window for is the
# thing the window is on top of, which is a region and not a point, and a
# strip that dropped fifteen points would still be over most of it. Ten
# points of travel and the thing you wanted still hidden is worse than not
# moving at all, because it looks like the feature worked.
#
# So each candidate clears the whole rectangle, and the choice between the
# four is the one that travels least. That makes the axis fall out of the
# window's own shape without being named anywhere: a strip 40 points tall
# and 900 wide steps up or down because that is 52 points against 912, and
# the same window in the full layout at 460x200 also steps vertically, and
# a tall narrow one would step sideways. Nothing had to decide that.


_ORDER = {"down": 0, "up": 1, "right": 2, "left": 3}


def _candidates(home: Rect) -> Sequence[tuple[int, str, Point]]:
    """The four places a window at ``home`` could step to, nearest first.

    Each one clears the whole footprint with ``CLEARANCE`` of daylight
    between the two rectangles. Ordered by travel, then by a fixed
    direction order so that the two vertical candidates — which always
    cost exactly the same — resolve the same way every time rather than
    however the sort happened to land.
    """
    x, y, width, height = home
    across = width + CLEARANCE
    down = height + CLEARANCE
    options = (
        (down, "down", (x, y + down)),
        (down, "up", (x, y - down)),
        (across, "right", (x + across, y)),
        (across, "left", (x - across, y)),
    )
    return sorted(options, key=lambda option: (option[0], _ORDER[option[1]]))


def dodge_destination(
    home: Rect,
    point: Point,
    available: Rect,
    margin: int = GRAB_MARGIN,
) -> Optional[Point]:
    """Where a window at ``home`` goes to get out from under ``point``.

    None when there is nowhere to go. A refusal rather than a guess: a
    window shuffled to a position still under the pointer would dodge on
    every arrival and uncover nothing, and standing still is at least
    honest about it.

    That is a rare answer and it is worth saying why rather than leaving
    it as a branch nobody can picture. Clamping only asks that ``margin``
    points of the window stay reachable, so the extreme placements leave a
    strip of ``margin`` at each edge of the available area with nothing on
    it — and a pointer is outside at least one of them unless the area is
    narrower than two margins in BOTH axes. It takes a screen about 80
    points across to reach.

    Every candidate is clamped the way any other placement of this window
    is, and then checked again afterwards — clamping is what turns a
    destination off the bottom of the screen back into one on it, and a
    destination that has been dragged back over the pointer is not a
    dodge. Preferring a candidate that needed no clamping at all is what
    keeps a window near a screen edge from stepping half off it when the
    other direction was free.

    The clearance is built into the candidates rather than asked for
    again here: an unclamped destination is already ``CLEARANCE`` clear of
    everything the pointer could have been on, and a clamped one has been
    dragged back by the screen, so what is left to ask of it is only
    whether the pointer is on it at all.
    """
    fallback: Optional[Point] = None
    _, _, width, height = home
    for _, direction, target in _candidates(home):
        x, y = clamped_position(
            (target[0], target[1], width, height), available, margin
        )
        if contains(point, (x, y, width, height)):
            continue
        if (x, y) == target:
            logger.debug("dodge: %s to %d, %d", direction, x, y)
            return (x, y)
        if fallback is None:
            fallback = (x, y)
    if fallback is None:
        logger.debug("dodge: nowhere to go from %r with the pointer at %r", home, point)
    return fallback
