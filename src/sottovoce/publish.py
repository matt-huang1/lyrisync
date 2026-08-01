"""Sending one hand-made sync back to LRCLIB, once somebody says so.

LRCLIB gives this app its lyrics for nothing and asks for nothing back. A
sync tapped out here is exactly the thing it is missing for that song, so
there ought to be a way to offer it — and every rule in this module exists
because "there ought to be a way" is one short step from an app that
uploads somebody's work without being asked.

## What may be published, and it is deliberately narrow

One case: **LRCLIB has the words for this track and no timings, and the
user has made timings for it.** Adding stamps to text they already host is
the clean case — the lines came from them, so what goes back is the same
lines with times against them, attached to the track record they already
have. A track LRCLIB has nothing at all for is a different thing (a whole
set of lyrics, from somewhere, with no record to attach it to) and is out
of scope here.

The gate is asked twice and the two askings are not the same question:

- ``standing_refusal`` is what the MENU asks. It reads the cache and the
  sync on disk, costs nothing, works offline, and answers "is this worth
  offering".
- ``verify`` is what the PUBLISH asks, and it goes to LRCLIB. What it
  gets back is what LRCLIB is holding right now, which is the only version
  of that fact worth acting on: the cached answer may be weeks old, and
  somebody else may have contributed timings in the meantime. Publishing
  is permanent and is done to somebody else's database, so the condition
  is checked against the database.

## Consent

Nothing here is ever reached without a person choosing it, one song at a
time, and what they confirm is the CONTENT rather than the idea:
``verify`` builds the exact ``Submission`` that will be sent, the window
shows all of it, and ``send`` is a separate call that happens afterwards.
There is no bulk path, no queue, and no automatic publication of a
completed sync. A sync that is never published is a sync that works
perfectly well, which is what it was before this module existed.

## The exchange

Three requests, sequential, with LRCLIB's own gap between them:

1. ``GET /api/get`` with the exact signature, which is the fresh check
   above and also where the plain lyrics that go back out come from.
2. ``POST /api/request-challenge``, which answers with a prefix and a
   target.
3. ``POST /api/publish``, carrying ``X-Publish-Token: prefix:nonce``.

Between the second and the third is the proof of work, which is seconds of
CPU and is ``challenge.py``'s. Its cost, and why it is interruptible, are
measured there.

Qt-free. The clock, the sleep and the stop flag are the caller's, because
all three of them are real time on a worker thread and shutdown has to be
able to end it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from sottovoce import challenge
from sottovoce import lyrics_provider as lrclib
from sottovoce.failure import PAYLOAD, FetchFailure, describe
from sottovoce.sync_session import sync_targets

logger = logging.getLogger(__name__)

# How many challenges one publish attempt will spend before giving up.
# A challenge that expires while it is being solved costs nothing but the
# CPU already spent, and asking for another is the obvious answer — but
# only so many times, because a target hard enough to outlast its own five
# minute lifetime twice running is a target this machine is not going to
# beat by trying a third time. See challenge.py for what the documented
# target actually costs (4.67s by median), which is why this is a ceiling
# on a case rather than a loop anybody expects to go round.
MAX_CHALLENGES = 3

# What a publish token is called on the wire.
TOKEN_HEADER = "X-Publish-Token"

# What LRCLIB answers when the token is wrong, expired or already spent.
# Documented as the one failure /api/publish names, and the server has one
# error for all three of those cases, which is why this is recovered from
# by starting again with a fresh challenge rather than by reading further.
TOKEN_REFUSED_STATUS = 400


# -- why it was not offered, in words --------------------------------------
#
# Each of these is the reason, and the boolean is derived from it rather
# than recorded beside it: a gate that answered False and separately
# remembered why can disagree with itself, and the reason is the half
# somebody actually needs.

NO_SONG = "there is no song in hand"
NO_SYNC = "there is no sync of this song to publish"
INCOMPLETE = "this song is missing details LRCLIB needs"
ALREADY_SENT = "this sync has been published already"
NOT_PLAIN_ONLY = "LRCLIB is not holding plain lyrics for this song"
# The three the fresh lookup can answer with, which the cached signal
# above cannot tell apart on its own.
NO_RECORD = "LRCLIB has no record of this song"
ALREADY_SYNCED = "LRCLIB already has synced lyrics for this song"
INSTRUMENTAL = "LRCLIB has this song down as instrumental"
DIFFERENT_WORDS = "these timings are not for the words LRCLIB is holding"


def standing_refusal(
    *,
    has_sync: bool,
    already_published: bool,
    remote_was_plain_only: bool,
    title: str = "",
    artist: str = "",
    album: str = "",
    duration_ms: Optional[int] = None,
) -> str:
    """Why this song may not be offered for publication, or "" when it may.

    Everything here is known without asking anybody: the sync is on disk,
    the record of a previous publication is beside it, the last answer
    LRCLIB gave is in the cache, and the metadata came from Spotify. That
    is what lets the menu ask it on every refresh.

    The four things LRCLIB requires of a submission are checked because
    they are required, not defensively: ``albumName`` and ``duration`` are
    both mandatory and both are how the submission finds the track record
    it is meant to be attached to. Spotify supplies all four for an
    ordinary song and supplies neither album nor duration for some of the
    things it also plays.
    """
    if not (title and artist):
        return NO_SONG
    if not has_sync:
        return NO_SYNC
    if not album or not duration_ms:
        return INCOMPLETE
    if already_published:
        return ALREADY_SENT
    if not remote_was_plain_only:
        return NOT_PLAIN_ONLY
    return ""


def may_offer(**known) -> bool:
    """Whether the menu offers this song. Derived from the reason, always
    and only, so the two cannot come apart."""
    return not standing_refusal(**known)


# -- exactly what will be sent ---------------------------------------------


@dataclass(frozen=True)
class Submission:
    """The body of one ``POST /api/publish``, and nothing else.

    Every field here is a field on the wire, and the names are LRCLIB's
    rather than this app's for the same reason the prefix goes back
    verbatim: this is somebody else's shape and renaming it locally would
    put a translation between what is shown and what is sent.

    The metadata is taken from LRCLIB's OWN record rather than from
    Spotify, which matters more than it looks. A submission is matched to
    an existing track by its normalised names and a duration within two
    seconds, so sending Spotify's 214 seconds against LRCLIB's 213 would
    still find the track — but sending Spotify's spelling of an album
    against LRCLIB's different one would not, and would quietly create a
    second track record with the timings on it and leave the plain lyrics
    where they were. Sending back what they gave us is what makes this an
    addition to their record rather than a near-duplicate of it.
    """

    track_name: str
    artist_name: str
    album_name: str
    duration: int
    plain_lyrics: str
    synced_lyrics: str

    def payload(self) -> dict:
        """The JSON body, exactly as it goes out."""
        return {
            "trackName": self.track_name,
            "artistName": self.artist_name,
            "albumName": self.album_name,
            "duration": self.duration,
            "plainLyrics": self.plain_lyrics,
            "syncedLyrics": self.synced_lyrics,
        }

    def rows(self) -> tuple[tuple[str, str], ...]:
        """The metadata as label and value pairs, in the order it is sent.

        Here rather than in the window because it is the same list the
        payload is, and two lists that have to stay in step are how a
        confirmation comes to describe something other than what is sent.
        """
        return (
            ("Track", self.track_name),
            ("Artist", self.artist_name),
            ("Album", self.album_name),
            ("Duration", f"{self.duration} seconds"),
        )

    def preview(self) -> str:
        """The whole submission as text: the metadata, then both bodies.

        What the confirmation shows, in one string, so that "what will be
        sent" can be asserted as a value rather than read off widgets.
        """
        rows = "\n".join(f"{label}: {value}" for label, value in self.rows())
        return (
            f"{rows}\n\n"
            f"Synced lyrics:\n{self.synced_lyrics}\n"
            f"Plain lyrics:\n{self.plain_lyrics}\n"
        )


@dataclass(frozen=True)
class Verification:
    """What the fresh lookup said: a submission to confirm, or a reason
    there is nothing to confirm.

    ``failure`` is set when the lookup itself could not be made, which is
    a third thing: not "this may not be published" but "nobody knows yet".
    The window says so and offers to ask again, exactly as it does for a
    lyrics lookup that failed.
    """

    submission: Optional[Submission] = None
    refusal: str = ""
    failure: Optional[FetchFailure] = None

    @property
    def ready(self) -> bool:
        return self.submission is not None


def _text(record: dict, key: str) -> str:
    value = record.get(key)
    return value.strip() if isinstance(value, str) else ""


def verify(
    *,
    title: str,
    artist: str,
    album: str,
    duration_ms: int,
    lrc_text: str,
    fetch: Callable[..., Optional[dict]] = lrclib.track_record,
) -> Verification:
    """Ask LRCLIB what it is holding, and build what would be sent.

    The condition is checked here and nowhere else that matters: the menu's
    answer is about what is worth offering, and this is about what is true.
    Between the two of them a song can be offered and then refused, and
    that is the correct outcome rather than a gap — somebody else may have
    contributed timings in the week since this song last played.

    The words are checked as well as the timings, and that is what keeps
    the scope honest. The lines of a sync made here came from LRCLIB's own
    plain lyrics, so they should be those lines exactly; a sync whose lines
    are something else (lyrics pasted in during an outage, say) is a set of
    timings for a different text, and publishing it would attach them to
    words they do not belong to.
    """
    try:
        record = fetch(title, artist, album, duration_ms / 1000)
    except lrclib.LyricsError as exc:
        logger.info("the publish check could not be made: %s", exc)
        return Verification(failure=exc.failure)
    if record is None:
        return Verification(refusal=NO_RECORD)
    if record.get("instrumental"):
        return Verification(refusal=INSTRUMENTAL)
    if _text(record, "syncedLyrics"):
        return Verification(refusal=ALREADY_SYNCED)
    plain = _text(record, "plainLyrics")
    if not plain:
        return Verification(refusal=NOT_PLAIN_ONLY)
    if sync_targets(plain) != [line for _, line in lrclib.parse_lrc(lrc_text)]:
        return Verification(refusal=DIFFERENT_WORDS)

    duration = record.get("duration")
    return Verification(
        submission=Submission(
            # LRCLIB's own values where it gave them, and what was asked
            # where it did not: a record that answers without naming itself
            # is still the record this question found.
            track_name=_text(record, "trackName") or title,
            artist_name=_text(record, "artistName") or artist,
            album_name=_text(record, "albumName") or album,
            duration=round(float(duration if duration else duration_ms / 1000)),
            plain_lyrics=plain,
            synced_lyrics=lrc_text,
        )
    )


# -- the exchange ----------------------------------------------------------

# Where a publish attempt has got to. Reported as it happens, because the
# middle of it is seconds long and a window with nothing to say for five
# seconds is a window that looks stuck.
CHECKING = "checking"
ASKING = "asking"      # requesting a challenge
SOLVING = "solving"    # the proof of work
EXPIRED = "expired"    # a challenge ran out mid-solve; asking for another
SENDING = "sending"    # the publish itself


@dataclass(frozen=True)
class Progress:
    """One thing worth saying while a publish is happening."""

    stage: str
    attempts: int = 0
    seconds: float = 0.0
    expected: float = 0.0


def progress_text(progress: Progress) -> str:
    """What the window says while it waits, in the app's own register.

    Here rather than in the window for the reason every sentence in this
    app is: it is a string somebody reads, it has to be assertable without
    a widget, and there has to be exactly one of it.

    The solve reports a count against an EXPECTED count rather than a
    percentage, and that is the honest shape of it. Each hash is an
    independent coin, so being a third of the way through the expected
    count says nothing at all about being a third of the way through the
    work: the odds of the next one landing are what they were at the
    start. What the two numbers do say is how big the problem is, which is
    the thing worth knowing while deciding whether to wait.
    """
    if progress.stage == CHECKING:
        return "asking LRCLIB what it has for this song"
    if progress.stage == ASKING:
        return "asking LRCLIB for a challenge"
    if progress.stage == EXPIRED:
        return "the challenge expired, asking for another"
    if progress.stage == SENDING:
        return "sending the sync"
    if progress.stage != SOLVING:
        return progress.stage
    if not progress.attempts:
        return "solving the proof of work"
    return (
        f"solving the proof of work · {progress.attempts:,} of about "
        f"{round(progress.expected):,} hashes · {progress.seconds:.0f}s"
    )


# How it ended. Four, because four are different to the person waiting:
# it worked, it was never allowed, it broke, or they stopped it.
PUBLISHED = "published"
REFUSED = "refused"
FAILED = "failed"
STOPPED = "stopped"


@dataclass(frozen=True)
class Result:
    """What became of one publish attempt.

    ``reason`` is the sentence, and every outcome has one — including the
    one that worked, because "sent, and the proof of work took 4.7s" is
    the thing somebody wants to read at the end of it. ``failure`` is kept
    beside it for the log and for the detail the HUD register can show; it
    is never what the sentence is built from at the point of display, so
    the two cannot disagree.
    """

    outcome: str
    reason: str = ""
    failure: Optional[FetchFailure] = None
    attempts: int = 0
    seconds: float = 0.0
    challenges: int = 0

    @property
    def published(self) -> bool:
        return self.outcome == PUBLISHED

    @property
    def may_try_again(self) -> bool:
        """Whether pressing again could plausibly do something different.

        A refusal is a fact about the song and pressing again would only
        produce it a second time. Everything else is a fact about a moment.
        """
        return self.outcome == FAILED


def solved_in(seconds: float, attempts: int) -> str:
    """How long the proof of work took, in the window's register.

    Its own sentence because it is the one thing about this that is worth
    reporting when everything went right, and because a duration and a
    count read badly built inline at each of the two places that say it.
    """
    return f"{attempts:,} hashes in {seconds:.1f}s"


def send(
    submission: Submission,
    *,
    on_progress: Optional[Callable[[Progress], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    post: Optional[Callable[..., object]] = None,
    solve: Callable[..., Optional[challenge.Solution]] = challenge.solve,
) -> Result:
    """Challenge, proof of work, publish. In that order, one at a time.

    Called only after somebody has read this exact submission and said yes.
    Nothing in here decides whether it may be sent: that was ``verify``'s
    and the person's, and by the time this runs the only questions left are
    mechanical ones.

    The gap between requests is LRCLIB's, taken before each of them rather
    than after, so the first is also spaced from whatever the check was
    doing a moment ago. It is one rule with no exception, which costs
    350ms in front of a publish that has just spent seconds solving — the
    alternative is a rule with a case in it, and a case is a thing to get
    wrong later.
    """
    post = post or lrclib.post_json
    spent = 0
    attempts = 0
    seconds = 0.0

    def say(stage: str, **rest) -> None:
        if on_progress is not None:
            on_progress(Progress(stage=stage, **rest))

    while spent < MAX_CHALLENGES:
        if should_stop is not None and should_stop():
            return Result(STOPPED, reason="stopped", challenges=spent)
        say(ASKING)
        spent += 1
        try:
            sleep(lrclib.REQUEST_GAP_SECONDS)
            answer = post(lrclib.LRCLIB_CHALLENGE_URL, {})
        except lrclib.LyricsError as exc:
            return _broke(exc, "the challenge could not be requested", spent)
        try:
            asked = challenge.Challenge(
                prefix=str((answer or {}).get("prefix", "")),
                target=str((answer or {}).get("target", "")),
                asked_at=now(),
            )
            expected = asked.expected_attempts()
            # Asked here rather than left to the solver: a challenge that
            # cannot be worked on is a failure of the exchange, and it
            # belongs beside the target that is not hex rather than
            # arriving as an exception out of the middle of a solve.
            if not asked.prefix:
                raise challenge.ChallengeError("challenge has no prefix")
        except (AttributeError, challenge.ChallengeError) as exc:
            logger.warning("LRCLIB's challenge could not be read: %s", exc)
            return Result(
                FAILED,
                reason="LRCLIB's challenge could not be read",
                failure=FetchFailure(kind=PAYLOAD, detail=str(exc)),
                challenges=spent,
            )

        say(SOLVING, expected=expected)
        solution = solve(
            asked,
            should_stop=should_stop,
            on_progress=lambda tried, elapsed: say(
                SOLVING, attempts=tried, seconds=elapsed, expected=expected
            ),
            now=now,
        )
        if solution is None:
            if should_stop is not None and should_stop():
                return Result(STOPPED, reason="stopped", challenges=spent)
            # Not stopped, so the clock ran out. Another challenge is the
            # whole of the recovery, and it is reported rather than done
            # quietly: the seconds already spent were real.
            logger.info("the challenge expired before it was solved")
            say(EXPIRED)
            continue
        attempts, seconds = solution.attempts, solution.seconds

        if should_stop is not None and should_stop():
            return Result(STOPPED, reason="stopped", challenges=spent)
        say(SENDING, attempts=attempts, seconds=seconds, expected=expected)
        try:
            sleep(lrclib.REQUEST_GAP_SECONDS)
            post(
                lrclib.LRCLIB_PUBLISH_URL,
                submission.payload(),
                {TOKEN_HEADER: solution.token(asked.prefix)},
            )
        except lrclib.LyricsError as exc:
            return _broke(
                exc,
                "the publish token was refused"
                if exc.failure.status == TOKEN_REFUSED_STATUS
                else "the sync could not be sent",
                spent,
                attempts=attempts,
                seconds=seconds,
            )
        logger.info(
            "published %r by %r: %s",
            submission.track_name,
            submission.artist_name,
            solved_in(seconds, attempts),
        )
        return Result(
            PUBLISHED,
            reason=f"sent to LRCLIB · {solved_in(seconds, attempts)}",
            attempts=attempts,
            seconds=seconds,
            challenges=spent,
        )

    return Result(
        FAILED,
        reason=f"the challenge expired {MAX_CHALLENGES} times running",
        challenges=spent,
    )


def _broke(
    exc: lrclib.LyricsError,
    said: str,
    challenges: int,
    *,
    attempts: int = 0,
    seconds: float = 0.0,
) -> Result:
    """One failed request, turned into an outcome.

    The sentence is this module's and the reason under it is
    ``failure.describe``'s, joined by the same middle dot the rest of the
    app joins two named things with. Both halves, because "the sync could
    not be sent" does not say whether the network is down or LRCLIB
    answered 503, and "could not reach lrclib.net" does not say which of
    the three requests it was.
    """
    logger.warning("%s: %s", said, exc)
    told = describe(exc.failure)
    return Result(
        FAILED,
        reason=f"{said} · {told}" if told else said,
        failure=exc.failure,
        attempts=attempts,
        seconds=seconds,
        challenges=challenges,
    )
