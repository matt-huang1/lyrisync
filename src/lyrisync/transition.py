"""Which line change is happening, so that each one happens once.

The anticipatory schedule is armed from the player's position, which
arrives on a poll — and the choreography (two phases of 260ms) is now
longer than the poll interval, so a poll almost always lands in the
middle of a line change. Left alone, that poll re-arms the timers from a
smaller eta and the same line change plays a second time, on top of
itself, in a hurry: the visible bug was a line that jumped, settled, and
then jumped again.

Two things had to be answered to stop it, and both are questions about
identity rather than about time:

- may a trigger for this line index (re)arm the timers? Yes until the
  movement for it has begun — that is what lets an ordinary poll keep
  correcting the timing of a change that has not started, which is how a
  seek gets picked up within one poll. No afterwards: a transition to a
  line runs once.
- is the screen disagreeing with the view model on purpose? The
  predicted swap deliberately puts the next line up before the player
  reaches it, so a mismatch of exactly one line, for the line this
  transition owns, is the state we asked for and not a missed
  prediction. Everything else is the world having moved, and snaps.

Dedupe by target index is the same shape as the view model's track
identity check: an announcement of what is already showing is not news.

Pure and Qt-free, so the rule can be tested without timers or a display.
"""

from __future__ import annotations

from typing import Optional


class LineTransition:
    """The line change in flight, identified by the line it is heading
    for.

    ``lead_seconds`` is how far ahead of a line's timestamp the screen is
    allowed to be: the whole choreography, plus a poll interval of slack,
    because the position that proves the prediction right only arrives on
    the next poll.
    """

    def __init__(self, lead_seconds: float) -> None:
        self._lead = lead_seconds
        self._target: Optional[int] = None

    @property
    def target(self) -> Optional[int]:
        """The line index being moved to, or None when nothing is in
        flight."""
        return self._target

    def may_arm(self, target: int) -> bool:
        """Whether the timers may still be (re)armed for ``target``.

        True until the movement begins, so every poll re-derives the
        timing from a fresh position. False once it has, so a poll landing
        mid-choreography cannot start the same change again.
        """
        return self._target != target

    def begin(self, target: int) -> bool:
        """Claim the movement to ``target``. False when it is already in
        flight or already applied — the trigger is a repeat, and the
        caller must do nothing at all.
        """
        if self._target == target:
            return False
        self._target = target
        return True

    def leads(
        self, target: int, target_seconds: float, position_seconds: float
    ) -> bool:
        """True when ``target`` being on screen ahead of the player is this
        transition's doing and still stands.

        It stops standing when the player is further from the line than
        the choreography can explain — a seek back into the middle of the
        line before it. Without that bound the screen could sit on a line
        the song has left, waiting for a timestamp that is now half a
        verse away.
        """
        if self._target != target:
            return False
        return target_seconds - position_seconds <= self._lead

    def clear(self) -> None:
        """Nothing is in flight. Every path that means "the world moved"
        comes through here, so the next poll is free to arm a fresh
        transition to the very same line."""
        self._target = None
