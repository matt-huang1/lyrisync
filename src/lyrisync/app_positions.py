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

Pure and Qt-free, so the rules can be tested without a display, a
notification centre, or a second application to switch to.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# How many apps to remember. Positions are cheap to relearn — one drag
# each — so this is a bound on the settings file rather than a resource
# anybody has to think about. Well past the number of apps a person
# switches between in a day.
MAX_ENTRIES = 50

# How long an app must stay frontmost before the window follows it.
#
# The number that stops a Cmd-Tab sweep from being a series of commands:
# holding Cmd and stepping through six apps announces six activations, and
# without this the window would chase every one of them. Long enough to
# sit out that sweep, short enough that a deliberate switch feels like it
# was waiting for you.
SETTLE_SECONDS = 0.4


class AppPositions:
    """Bundle identifier → (x, y), least recently used dropped first.

    Recency counts a *use*, not just a write: recalling a position for an
    app is evidence you still switch to it, so it refreshes the entry.
    Otherwise the app you use constantly but rarely re-place would be the
    first one evicted.
    """

    def __init__(self, limit: int = MAX_ENTRIES) -> None:
        self._limit = max(1, int(limit))
        self._entries: "OrderedDict[str, tuple[int, int]]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, bundle_id: object) -> bool:
        return bundle_id in self._entries

    @property
    def bundle_ids(self) -> tuple[str, ...]:
        """Least recently used first — the order they would be evicted."""
        return tuple(self._entries)

    def remember(self, bundle_id: str, x: int, y: int) -> None:
        """Record where the window sits for this app, replacing whatever
        was there. The newest entry is the last to be evicted."""
        if not bundle_id:
            return
        self._entries.pop(bundle_id, None)
        self._entries[bundle_id] = (int(x), int(y))
        while len(self._entries) > self._limit:
            evicted, _ = self._entries.popitem(last=False)
            logger.debug("forgot the position for %s (map full)", evicted)

    def recall(self, bundle_id: Optional[str]) -> Optional[tuple[int, int]]:
        """The remembered position, or None. None means "leave the window
        alone" — never a default, because a default would move the window
        somewhere the user never put it."""
        if not bundle_id:
            return None
        position = self._entries.get(bundle_id)
        if position is None:
            return None
        self._entries.move_to_end(bundle_id)
        return position

    def forget(self, bundle_id: str) -> bool:
        return self._entries.pop(bundle_id, None) is not None

    def forget_all(self) -> None:
        """Throw the whole map away. Every entry costs one drag to
        relearn, which is why this needs no confirmation."""
        self._entries.clear()

    # -- persistence ------------------------------------------------------

    def to_json(self) -> str:
        """A list of triples rather than an object, so the recency order is
        part of the format rather than a property of whichever JSON
        implementation reads it back."""
        return json.dumps([[key, x, y] for key, (x, y) in self._entries.items()])

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
            bundle_id, x, y = parsed
            positions.remember(bundle_id, x, y)
        return positions


def _parse_entry(entry: object) -> Optional[tuple[str, int, int]]:
    if not isinstance(entry, (list, tuple)) or len(entry) != 3:
        return None
    bundle_id, x, y = entry
    if not isinstance(bundle_id, str) or not bundle_id:
        return None
    if isinstance(x, bool) or isinstance(y, bool):
        return None  # JSON true/false are ints in Python; they are not points
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    return bundle_id, x, y


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

    def observe(self, bundle_id: Optional[str], now: float) -> None:
        """A new frontmost app. Starts (or continues) its settling clock."""
        if not bundle_id:
            return
        if bundle_id == self._pending:
            return  # already settling; a repeat is not a new arrival
        self._pending = bundle_id
        self._since = now

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


def may_learn(
    *, enabled: bool, frontmost: Optional[str], own_bundle_id: Optional[str]
) -> bool:
    """Whether finishing a drag should record a position.

    The window is unfocusable and the app is an accessory, so dragging it
    does not change which app is frontmost — that is exactly what makes
    implicit learning work, and it is why the answer is almost always yes.

    Our own identifier is refused anyway. If anything ever does make
    LyriSync frontmost (a future menu, a debugger attaching), a position
    recorded against ourselves would be an entry that can never be
    recalled, quietly evicting a real one. ``own_bundle_id`` is None for a
    source run, which has no bundle and therefore nothing to collide with.
    """
    if not enabled or not frontmost:
        return False
    return own_bundle_id is None or frontmost != own_bundle_id


def may_move(*, enabled: bool, visible: bool, dragging: bool, syncing: bool) -> bool:
    """Whether the window may be moved to a remembered position.

    Each refusal is a different kind of "the user is in the middle of
    something":

    - **dragging** — they have hold of the window. Moving it under the
      cursor would be the app fighting the hand.
    - **syncing** — a tap-to-sync pass is a rhythm game against a moving
      target; the tap bar moving mid-pass would cost stamps.
    - **hidden** — there is nothing to move, and moving it anyway would
      mean it reappears somewhere it was never seen to go.
    """
    return enabled and visible and not dragging and not syncing
