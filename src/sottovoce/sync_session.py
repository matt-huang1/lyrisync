"""Tap-to-sync: turn plain lyrics into timed ones, one tap per line.

Pure logic, Qt-free like loop.py and geometry.py. The session owns the
line list, the cursor, and the stamps recorded so far; the caller owns the
clock, the player, and the widgets.

Where the lines come from is not this module's business: plain lyrics from
LRCLIB, the sync a re-sync replaces, or a block somebody pasted in. All
three arrive as tap targets, and ``targets_from_paste`` is the only one
with anything to take off first.

Two corrections stand between "when the click landed" and "when the line
actually started":

- *Interpolation*. Playback position is only known as of the last poll
  (~300ms apart), and the UI thread must never run a subprocess to ask for
  a fresher one. ``interpolated_position`` advances the last known position
  by the wall-clock time since that poll landed.
- *Reaction offset*. A human taps after hearing the line begin, so every
  stamp is late by roughly a reaction time. ``SYNC_REACTION_OFFSET_SECONDS``
  is subtracted back off, clamped so timestamps never go negative and never
  run backwards past the previous line.

A pass also has to survive the world. It costs minutes of somebody's
attention and the things that ended it used to throw all of that away in
silence: a track change (a song ENDING is a track change), a one-poll stop
the monitor debounces everywhere else, a mis-aimed press on the discard.
So a pass is written down as it goes — ``encode``/``decode`` here are the
shape of that record, ``resume_targets`` is the one rule about whether an
old record may be stamped onto the lines in hand, and the file it lives in
is ``lyrics_provider``'s. Nothing here touches a disk.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Subtracted from every stamp to cancel the tap's reaction lag. Tuning
# knob: raise it if your syncs consistently land late, lower it if lines
# appear before they are sung.
SYNC_REACTION_OFFSET_SECONDS = 0.25

# Ceiling on how far a stale poll may be extrapolated. Polls are ~300ms
# apart, so anything beyond this means the poll loop stalled (a slow
# osascript, a wedged Spotify) and guessing further would invent a
# position rather than refine one.
MAX_EXTRAPOLATION_SECONDS = 2.0


def sync_targets_from_lines(lines: Iterable[str]) -> list[str]:
    """The lines a sync pass will stamp: every non-blank one, in order.
    Blank lines are structure (instrumental gaps, verse breaks), not tap
    targets."""
    return [line.strip() for line in lines if line.strip()]


def sync_targets(plain_text: str) -> list[str]:
    """Tap targets from a block of plain lyrics."""
    return sync_targets_from_lines(plain_text.splitlines())


# What a bracketed thing at the start of a line is: an LRC timestamp
# ([00:12.34]) or an LRC metadata tag ([ar:Someone]). Both are structure
# rather than words, and both arrive when somebody pastes a .lrc file into
# a box that asked for lyrics.
_LRC_STAMP_RE = re.compile(r"^\s*(?:\[\d+:\d{1,2}(?:[.:]\d+)?\]\s*)+")
_LRC_TAG_RE = re.compile(r"^\s*\[[a-zA-Z#]+:[^\]]*\]\s*$")


def targets_from_paste(text: str) -> list[str]:
    """Tap targets from lyrics a person pasted in.

    The same targets as any other pass — every non-blank line, in order —
    with one thing taken off first: LRC syntax. What people have to hand is
    frequently a ``.lrc`` file from somewhere, and pasting one into a box
    that asked for words would otherwise produce a song whose every line
    begins "[00:12.34]" and then be saved back out with a second timestamp
    in front of the first.

    Metadata tags go entirely: ``[ar:Someone]`` is not a line anybody
    sings, and a pass that made the user tap through four of them before
    the first lyric would be one they abandoned. A stamp at the START of a
    line is stripped and the words after it kept, because those words are
    exactly the line.

    Nothing else is cleaned up. What is left is what the user pasted, and
    guessing further about somebody's own lyrics is how a sync ends up
    missing the line they were waiting for.
    """
    lines = []
    for raw in text.splitlines():
        if _LRC_TAG_RE.match(raw):
            continue
        lines.append(_LRC_STAMP_RE.sub("", raw))
    return sync_targets_from_lines(lines)


def interpolated_position(
    position_seconds: Optional[float],
    polled_at: Optional[float],
    now: float,
    playing: bool = True,
) -> Optional[float]:
    """Playback position right now, estimated from the last poll.

    ``polled_at`` and ``now`` are readings of the same monotonic clock.
    Returns None when there is nothing to interpolate from. While paused
    the position does not advance, so the last reading stands.
    """
    if position_seconds is None:
        return None
    if not playing or polled_at is None:
        return position_seconds
    elapsed = min(MAX_EXTRAPOLATION_SECONDS, max(0.0, now - polled_at))
    return position_seconds + elapsed


def format_timestamp(seconds: float) -> str:
    """One LRC stamp: ``[mm:ss.xx]``, centiseconds. Rounding happens once,
    in centiseconds, so 59.999s carries into the next minute instead of
    rendering as ``[00:60.00]``."""
    centis = max(0, round(seconds * 100))
    minutes, within_minute = divmod(centis, 6000)
    return f"[{minutes:02d}:{within_minute // 100:02d}.{within_minute % 100:02d}]"


class SyncSession:
    """One tap-to-sync pass over a song's lines.

    The cursor is implied by the stamps: line *n* is next to stamp exactly
    when *n* stamps have been recorded. ``undo`` drops the last stamp,
    which steps the cursor back with it.
    """

    def __init__(
        self,
        lines: list[str],
        offset: float = SYNC_REACTION_OFFSET_SECONDS,
        stamps: Optional[Iterable[float]] = None,
    ) -> None:
        self._lines = list(lines)
        self._offset = offset
        # A resumed pass starts with the stamps it already had, trimmed to
        # the lines it has: a record longer than its lines could only come
        # from a file somebody edited, and stamping line 30 of a 20 line
        # song is not a state this can be in.
        self._stamps: list[float] = list(stamps or [])[: len(self._lines)]

    @property
    def lines(self) -> list[str]:
        return list(self._lines)

    @property
    def stamps(self) -> list[float]:
        return list(self._stamps)

    @property
    def total(self) -> int:
        return len(self._lines)

    @property
    def index(self) -> int:
        """Index of the line waiting to be stamped; ``total`` when done."""
        return len(self._stamps)

    @property
    def is_complete(self) -> bool:
        return bool(self._lines) and len(self._stamps) == len(self._lines)

    @property
    def previous(self) -> str:
        """The line just stamped, or "" before the first tap.

        Kept on screen because it is the timing cue for the next tap: the
        singer is partway through it, and watching it run out is how you
        know when the current line begins.
        """
        index = self.index
        return self._lines[index - 1] if index >= 1 else ""

    @property
    def current(self) -> str:
        """The line to stamp next, or "" once every line is stamped."""
        index = self.index
        return self._lines[index] if index < len(self._lines) else ""

    def upcoming(self, count: int = 2) -> list[str]:
        """The next ``count`` lines after the current one, fewer near the
        end of the song."""
        start = self.index + 1
        return self._lines[start : start + max(0, count)]

    def stamp(self, position_seconds: float) -> bool:
        """Record ``position_seconds`` for the current line and advance.

        Returns False when there is nothing left to stamp. The reaction
        offset is applied here, clamped to [0, ...] and to at least the
        previous stamp so the timeline never runs backwards.
        """
        if self.is_complete or not self._lines:
            return False
        value = max(0.0, position_seconds - self._offset)
        if self._stamps:
            value = max(value, self._stamps[-1])
        self._stamps.append(value)
        return True

    def undo(self) -> bool:
        """Remove the last stamp and step back one line. Returns False when
        nothing has been stamped yet."""
        if not self._stamps:
            return False
        self._stamps.pop()
        return True

    def to_lrc(self) -> str:
        """The stamped lines as LRC text, in order.

        Lines not yet stamped are simply absent, which is what makes this
        the same function for a complete pass and for a partial one kept
        on purpose: the file says what was timed and claims nothing about
        what was not. Whether a pass that is short of its last lines may
        be saved at all is the window's question, not this one's.
        """
        return "".join(
            f"{format_timestamp(stamp)} {line}\n"
            for stamp, line in zip(self._stamps, self._lines)
        )

    def encode(self) -> dict:
        """The pass, as something that can be written down.

        The LINES go in it as well as the stamps, and that is the whole
        reason a resumed pass can be trusted: stamps alone are numbers
        against positions in a list, and the list can change under them
        between one playing of a song and the next.
        """
        return {
            "version": PASS_VERSION,
            "lines": list(self._lines),
            "stamps": list(self._stamps),
        }


# -- a pass written down ---------------------------------------------------
#
# A pass in progress is the user's work in exactly the sense a finished
# sync is, which is why the file lives with the syncs rather than in the
# cache: clearing ``.lyrics_cache/`` is a documented reset, and minutes of
# somebody tapping through a song is not something a reset may take.
#
# The shape is here, next to the session it is a picture of, so that what
# is written and what is read back are one definition. ``lyrics_provider``
# owns the file; this owns what is in it.

PASS_VERSION = 1


def decode(record: object) -> Optional[tuple[list[str], list[float]]]:
    """``(lines, stamps)`` from a written-down pass, or None.

    None for anything that is not a pass this app wrote: a version it does
    not know, a file half-written when the power went, a hand-edit that
    left a string where a number belongs. A record that cannot be read is
    not an error to report — it is simply not a pass to resume, and the
    song falls back to offering a fresh one.

    Stamps never run backwards, so a record whose do is rejected rather
    than repaired: the repair would be a guess about which of two numbers
    the user meant, and a fresh pass is the honest answer.
    """
    if not isinstance(record, dict) or record.get("version") != PASS_VERSION:
        return None
    lines, stamps = record.get("lines"), record.get("stamps")
    if not isinstance(lines, list) or not isinstance(stamps, list):
        return None
    if not lines or not all(isinstance(line, str) for line in lines):
        return None
    if len(stamps) > len(lines):
        return None
    values: list[float] = []
    for stamp in stamps:
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            return None
        value = float(stamp)
        if value < 0 or (values and value < values[-1]):
            return None
        values.append(value)
    return [str(line) for line in lines], values


def resume_targets(
    record: object, lines_in_hand: Optional[list[str]] = None
) -> Optional[tuple[list[str], list[float]]]:
    """The pass to resume for this song, or None to start a fresh one.

    Two things have to be true, and the second is the one that matters.

    The record has to be readable, which ``decode`` answers. And its lines
    have to be the lines this song is offering NOW: a sync is stamps
    against WORDS, so resuming a record made from different words would
    put somebody's timings against lines they never tapped. That is the
    same rule publishing already holds itself to, one layer down.

    ``lines_in_hand`` is None when the song has no lines of its own to
    disagree with — a pass over pasted lyrics, whose words only ever
    existed in the record. There the record stands alone, which is not a
    weaker check but the only one there is: nothing else knows those words.

    A record with no stamps in it resumes onto nothing, so it is not a
    resume at all and says so by answering None.
    """
    decoded = decode(record)
    if decoded is None:
        return None
    lines, stamps = decoded
    if not stamps:
        return None
    if lines_in_hand and list(lines_in_hand) != lines:
        return None
    return lines, stamps


def pass_progress(record: object) -> Optional[tuple[int, int]]:
    """``(stamped, total)`` for a written-down pass, or None.

    What the menu and the window need to SAY about an interrupted pass,
    and they need it without building a session: "14 / 22 lines" is a
    sentence about a file.
    """
    decoded = decode(record)
    if decoded is None:
        return None
    lines, stamps = decoded
    return len(stamps), len(lines)
