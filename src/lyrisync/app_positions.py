"""Where the window sits for each app you use.

The whole of per-app position memory that is neither Qt nor AppKit: the
map itself, the settling rule that stops a Cmd-Tab sweep dragging the
window across the screen, and the two gates that say when the app may
learn a position and when it may act on one.

Three things shaped this:

- **Learning is implicit.** There is no "save position here" command. The
  user drags the window where they want it while working in some app, and
  that is the whole gesture — the app was already told everything it needs
  by watching. An explicit save would be a second thing to remember to do,
  for a preference that is only ever expressed by moving a window.
- **Positions are cheap.** Every entry can be relearned by dragging the
  window once, so the map is capped and the least recently used entry is
  simply dropped. Nothing here is worth protecting the way a hand-made
  sync is.
- **Doing nothing is a valid answer**, and usually the right one. An app
  with no remembered position leaves the window exactly where it is,
  rather than guessing a default and moving it somewhere nobody asked for.

Everything a decision here refuses, it can also *name*. Implicit learning
with no feedback is indistinguishable from a broken feature, so each gate
returns its reason and the caller logs it — one rule, phrased once, doing
duty as both the answer and the explanation. A second copy of the rule
inside a log line would be a second copy of the rule.

Pure and Qt-free, so the rules can be tested without a display, a
notification centre, or a second application to switch to.
"""

from __future__ import annotations

import json
import logging
import math
from collections import OrderedDict
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

# How many apps to remember. Positions are cheap to relearn — one drag
# each — so this is a bound on the settings file rather than a resource
# anybody has to think about. Well past the number of apps a person
# switches between in a day.
MAX_ENTRIES = 50

# What an announced activation was taken to be. Returned by
# ActivationDebounce.observe so the caller can say which of the three
# happened without asking the debounce a second question about its own
# state — a diagnostic log line that reconstructs the decision is a log
# line that can disagree with it.
ARRIVAL = "arrival"  # a new app; its settling clock starts now
REPEAT = "repeat"  # the app already settling, announced again
UNKEYABLE = "unkeyable"  # nothing to key on, so nothing to settle

# How long an app must stay frontmost before the window follows it.
#
# The number that stops a Cmd-Tab sweep from being a series of commands:
# holding Cmd and stepping through six apps announces six activations, and
# without this the window would chase every one of them. Long enough to
# sit out that sweep, short enough that a deliberate switch feels like it
# was waiting for you.
SETTLE_SECONDS = 0.4


class Placement(NamedTuple):
    """Where the window goes for an app, and what that app is called.

    The name is carried because the map outlives the sessions that taught
    it: an app that is not running cannot be asked its name, and a list of
    bundle identifiers is a list nobody can read. It is the LAST SEEN
    name, deliberately — if macOS ever declines to say, the app keeps the
    name it had rather than reverting to its identifier.
    """

    x: int
    y: int
    name: Optional[str] = None


class AppPositions:
    """Bundle identifier → Placement, least recently used dropped first.

    Recency counts a *use*, not just a write: recalling a position for an
    app is evidence you still switch to it, so it refreshes the entry.
    Otherwise the app you use constantly but rarely re-place would be the
    first one evicted.
    """

    def __init__(self, limit: int = MAX_ENTRIES) -> None:
        self._limit = max(1, int(limit))
        self._entries: "OrderedDict[str, Placement]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, bundle_id: object) -> bool:
        return bundle_id in self._entries

    @property
    def bundle_ids(self) -> tuple[str, ...]:
        """Least recently used first — the order they would be evicted."""
        return tuple(self._entries)

    def remember(
        self, bundle_id: str, x: int, y: int, name: Optional[str] = None
    ) -> None:
        """Record where the window sits for this app, replacing whatever
        was there. The newest entry is the last to be evicted.

        A name of None does not erase the one already stored: the caller
        not knowing what an app is called this time is not evidence that
        the name it learned last time was wrong.
        """
        if not bundle_id:
            return
        previous = self._entries.pop(bundle_id, None)
        if name is None and previous is not None:
            name = previous.name
        self._entries[bundle_id] = Placement(int(x), int(y), name or None)
        while len(self._entries) > self._limit:
            evicted, _ = self._entries.popitem(last=False)
            logger.debug("forgot the position for %s (map full)", evicted)

    def recall(self, bundle_id: Optional[str]) -> Optional[tuple[int, int]]:
        """The remembered position, or None. None means "leave the window
        alone" — never a default, because a default would move the window
        somewhere the user never put it.

        The point only, not the name: every caller of this is about to
        move a window.
        """
        if not bundle_id:
            return None
        placement = self._entries.get(bundle_id)
        if placement is None:
            return None
        self._entries.move_to_end(bundle_id)
        return (placement.x, placement.y)

    def peek(self, bundle_id: Optional[str]) -> Optional[tuple[int, int]]:
        """The remembered position without counting a use.

        For showing what is known — the menu's readout, a log line — where
        ``recall`` would be wrong: opening a menu to look at an entry is
        not evidence you still switch to that app, and letting a glance
        refresh recency would make the eviction order describe where the
        user has been looking rather than where they have been working.
        """
        if not bundle_id:
            return None
        placement = self._entries.get(bundle_id)
        return None if placement is None else (placement.x, placement.y)

    def name_for(self, bundle_id: Optional[str]) -> Optional[str]:
        """The last name seen for this app, or None. Never counts a use,
        for the same reason ``peek`` does not."""
        if not bundle_id:
            return None
        placement = self._entries.get(bundle_id)
        return None if placement is None else placement.name

    def listed(self) -> tuple[tuple[str, Optional[str]], ...]:
        """Every remembered app as (identifier, name), most recently used
        first — the order a list should read in, and the reverse of the
        order things are evicted in."""
        return tuple(
            (bundle_id, placement.name)
            for bundle_id, placement in reversed(self._entries.items())
        )

    def forget(self, bundle_id: str) -> bool:
        return self._entries.pop(bundle_id, None) is not None

    def forget_all(self) -> None:
        """Throw the whole map away. Every entry costs one drag to
        relearn, which is why this needs no confirmation."""
        self._entries.clear()

    # -- persistence ------------------------------------------------------

    def to_json(self) -> str:
        """A list of rows rather than an object, so the recency order is
        part of the format rather than a property of whichever JSON
        implementation reads it back.

        Four fields now — the name came later — and a row is written with
        all four even when the name is null, so the shape on disk does not
        depend on what happened to be known.
        """
        return json.dumps(
            [
                [key, place.x, place.y, place.name]
                for key, place in self._entries.items()
            ]
        )

    @classmethod
    def from_json(cls, raw: object, limit: int = MAX_ENTRIES) -> "AppPositions":
        """Rebuild from stored text. Anything unreadable yields an empty
        map, and a single bad entry costs only itself: a settings file
        somebody edited by hand should degrade, not take the feature down
        with it."""
        positions = cls(limit)
        if not isinstance(raw, str) or not raw.strip():
            return positions
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            logger.info("stored app positions were unreadable — starting empty")
            return positions
        if not isinstance(decoded, list):
            return positions
        for entry in decoded:
            parsed = _parse_entry(entry)
            if parsed is None:
                logger.debug("skipped a malformed app position entry: %r", entry)
                continue
            bundle_id, x, y, name = parsed
            positions.remember(bundle_id, x, y, name)
        return positions


def _parse_entry(entry: object) -> Optional[tuple[str, int, int, Optional[str]]]:
    """One stored row, in either shape.

    Three fields is what milestone 14 wrote and is still read: a map saved
    before names existed keeps every position and simply has no names
    until each app is next seen. Upgrading a format by making the new
    field optional costs one branch; refusing the old shape would cost the
    user every position they had.
    """
    if not isinstance(entry, (list, tuple)) or len(entry) not in (3, 4):
        return None
    bundle_id, x, y = entry[0], entry[1], entry[2]
    name = entry[3] if len(entry) == 4 else None
    if not isinstance(bundle_id, str) or not bundle_id:
        return None
    if isinstance(x, bool) or isinstance(y, bool):
        return None  # JSON true/false are ints in Python; they are not points
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    if name is not None and not isinstance(name, str):
        return None
    return bundle_id, x, y, name or None


class ActivationDebounce:
    """Which app has been frontmost long enough to be worth moving for.

    Activations arrive as fast as the user can press Tab. Acting on each
    one would drag the window across the screen once per app in a Cmd-Tab
    sweep, which is the failure this exists to prevent — the feature is
    meant to be noticed when you arrive somewhere, not while you are
    passing through.

    Repeat announcements of the app already pending do NOT restart the
    clock. macOS can report the same activation more than once, and a rule
    that reset on every announcement could keep an app settling forever
    while it sat there being frontmost.
    """

    def __init__(self, interval: float = SETTLE_SECONDS) -> None:
        self._interval = max(0.0, float(interval))
        self._pending: Optional[str] = None
        self._since = 0.0

    @property
    def pending(self) -> Optional[str]:
        """The app currently settling, if any."""
        return self._pending

    def observe(self, bundle_id: Optional[str], now: float) -> str:
        """A new frontmost app. Starts (or continues) its settling clock.

        Answers which of ARRIVAL, REPEAT and UNKEYABLE this was, so a
        caller can log what became of an announcement it passed in. The
        return value is advisory — nothing here behaves differently for
        being asked.
        """
        if not bundle_id:
            return UNKEYABLE
        if bundle_id == self._pending:
            return REPEAT  # already settling; a repeat is not a new arrival
        self._pending = bundle_id
        self._since = now
        return ARRIVAL

    def remaining(self, now: float) -> float:
        """How much longer the pending app must stay in front, in seconds.

        Exists because whatever wakes the caller up is a second clock, and
        two clocks measuring one interval will disagree. A timer that
        fires a hair early would otherwise drop the arrival for good — it
        asks once, is told "not yet", and nothing asks again. With this
        the rule stays authoritative and the timer is only a prompt.
        """
        if self._pending is None:
            return 0.0
        return max(0.0, self._interval - (now - self._since))

    def settled(self, now: float) -> Optional[str]:
        """The app that has now been frontmost for the full interval, once.

        Returns None while it is still settling, and None again once it has
        been handed over — so the window moves once per arrival rather than
        on every tick of whatever is doing the asking.
        """
        if self._pending is None:
            return None
        if now - self._since < self._interval:
            return None
        settled, self._pending = self._pending, None
        return settled

    def cancel(self) -> None:
        """Forget what was settling. For switching the layer off, and for
        anything else that means the pending arrival no longer applies."""
        self._pending = None


# -- the two gates --------------------------------------------------------


def learn_refusal(
    *, enabled: bool, frontmost: Optional[str], own_bundle_id: Optional[str]
) -> Optional[str]:
    """Why finishing a drag should NOT record a position, or None if it
    should.

    The window is unfocusable and the app is an accessory, so dragging it
    does not change which app is frontmost — that is exactly what makes
    implicit learning work, and it is why the answer is almost always
    None.

    Our own identifier is refused anyway. If anything ever does make
    LyriSync frontmost (a future menu, a debugger attaching), a position
    recorded against ourselves would be an entry that can never be
    recalled, quietly evicting a real one. ``own_bundle_id`` is None for a
    source run, which has no bundle and therefore nothing to collide with.
    """
    if not enabled:
        return "the layer is off"
    if not frontmost:
        return "no frontmost app is known"
    if own_bundle_id is not None and frontmost == own_bundle_id:
        return "LyriSync itself is frontmost"
    return None


def may_learn(
    *, enabled: bool, frontmost: Optional[str], own_bundle_id: Optional[str]
) -> bool:
    """Whether finishing a drag should record a position. The refusal
    without its reason, for callers that only need the answer."""
    return (
        learn_refusal(
            enabled=enabled, frontmost=frontmost, own_bundle_id=own_bundle_id
        )
        is None
    )


def move_refusal(
    *, enabled: bool, visible: bool, dragging: bool, syncing: bool, flying: bool
) -> Optional[str]:
    """Why the window may NOT be moved to a remembered position, or None
    if it may.

    Each refusal is a different kind of "something else is already
    happening to this window":

    - **dragging** — the user has hold of it. Moving it under the cursor
      would be the app fighting the hand.
    - **syncing** — a tap-to-sync pass is a rhythm game against a moving
      target; the tap bar moving mid-pass would cost stamps.
    - **flying** — it is on its way to or from the menu bar item, and that
      journey owns its position until it lands. Two animations of one
      window's position could only fight.
    - **hidden** — there is nothing to move, and moving it anyway would
      mean it reappears somewhere it was never seen to go.

    Ordered most specific first, because the reason a user is most likely
    to be surprised by is the one they are in the middle of doing.
    """
    if not enabled:
        return "the layer is off"
    if dragging:
        return "the window is being dragged"
    if syncing:
        return "a sync pass is running"
    if flying:
        return "the window is on its way to or from the menu bar"
    if not visible:
        return "the window is hidden"
    return None


def may_move(
    *, enabled: bool, visible: bool, dragging: bool, syncing: bool, flying: bool = False
) -> bool:
    """Whether the window may be moved to a remembered position. The
    refusal without its reason."""
    return (
        move_refusal(
            enabled=enabled,
            visible=visible,
            dragging=dragging,
            syncing=syncing,
            flying=flying,
        )
        is None
    )


# -- saying what is known -------------------------------------------------


def display_label(bundle_id: Optional[str], name: Optional[str]) -> str:
    """What to call an app on screen: its name, or its identifier when
    that is all there is.

    The identifier was the readout's first answer and it was the wrong
    one. It is precise, and precision is what the log is for — a menu is
    read by a person deciding whether a feature works, and
    `com.microsoft.VSCode` makes them translate before they can answer.
    The fallback keeps the old behaviour exactly, for an app that has
    never been seen running.
    """
    if name:
        return name
    return bundle_id or ""


def status_summary(
    *,
    count: int,
    frontmost: Optional[str],
    frontmost_name: Optional[str] = None,
    placed: bool = False,
) -> str:
    """One line naming what the layer knows, for the menu to show.

    Two facts, because there are two ways to doubt an implicit feature:
    how much has been learned at all, and whether *this* app — the one in
    front, the one a drag would record against — is one of the ones that
    has been. A count alone would leave "is it working here?" unanswered,
    and the position alone would hide an empty map behind one app that
    happens to have no entry.

    No coordinates. They answered a question nobody asked of a menu: a
    number pair cannot be checked against anything by eye, and the window
    is already sitting at it in plain view. They stay in the DEBUG log,
    where a reader is comparing them with something.
    """
    if count == 0:
        learned = "No positions remembered"
    elif count == 1:
        learned = "1 app remembered"
    else:
        learned = f"{count} apps remembered"
    if not frontmost:
        return f"{learned} · frontmost app unknown"
    label = display_label(frontmost, frontmost_name)
    return f"{learned} · {label} {'is placed' if placed else 'not placed yet'}"


# -- acknowledging that a position was learned ----------------------------
#
# The drag is the whole gesture and it ends in silence, which is the
# feature's oldest problem restated: the window looks identical whether it
# recorded anything or not. A short warm glow on the hairline is the
# answer — the edge is borrowed for half a second and handed straight
# back, which is why the tint is not touched and nothing is captured.


# How long the whole acknowledgement lasts, rise and fall together.
#
# Was two line-change phases (520ms) and that was too quick to catch: the
# edge is one device pixel, so the eye has to already be on it. Three
# phases, still derived from the one constant the window's motion is built
# out of, and still short enough that it is over before it becomes a thing
# being watched.
GLOW_SECONDS = 0.78

# The most of the warm colour the edge ever carries, as a mix towards it.
# Now the whole way: at 0.85 the amber was still being averaged with a
# hairline that is nearly transparent at rest, and what arrived was a
# slightly warmer grey. The peak is what makes this perceptible at all, so
# it is the one that went to the limit.
GLOW_PEAK = 1.0

# How much wider the hairline gets at the peak, as a multiple of its
# resting width.
#
# The second half of being noticeable, and the half that does the work: a
# single device pixel changing colour is a change of a few hundred pixels
# on a 460-point window, which is nothing at the edge of attention. Three
# device pixels of warm edge is a shape change, and the eye is far better
# at those. It is still an EDGE — it grows inward from the same line, and
# the growth is on the same paint-time mix as the colour, so it returns
# with it.
GLOW_WIDTH_GAIN = 2.0


def glow_intensity(phase: float) -> float:
    """How much of the warm colour the hairline carries, 0 to GLOW_PEAK.

    ONE property with a rise and a fall in it, rather than two animations
    handing over — the same reasoning as the line change's signed
    ``progress``. A half sine is the shape: nothing at either end, so the
    edge leaves and returns to exactly the tint's own colour with no step
    at either boundary, and no easing curve to choose.
    """
    if phase <= 0.0 or phase >= 1.0:
        return 0.0
    return GLOW_PEAK * math.sin(math.pi * phase)


def glow_width(base_width: float, intensity: float) -> float:
    """How wide the hairline is drawn at this much glow.

    Grows from its resting width to ``1 + GLOW_WIDTH_GAIN`` times it and
    back, on the same intensity as the colour — one number driving both,
    so the edge cannot be left thick and cool or thin and warm. Returns
    the resting width exactly at zero, which is what makes the loan
    return itself.
    """
    return base_width * (1.0 + GLOW_WIDTH_GAIN * max(0.0, intensity))


def may_acknowledge(*, now: float, last: Optional[float]) -> bool:
    """Whether a learned position should be acknowledged on the window.

    One glow per gesture. A drag that ends is one thing the user did, and
    a second acknowledgement landing inside the first would read as a
    flicker rather than as two answers — the window would be talking about
    itself. Anything within one glow of the last one is therefore refused
    rather than restarted, which also makes this the guard against a
    release that arrives twice.
    """
    if last is None:
        return True
    return (now - last) >= GLOW_SECONDS
