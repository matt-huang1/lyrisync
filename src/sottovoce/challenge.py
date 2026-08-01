"""LRCLIB's proof-of-work: the rule, the solver, and the token it makes.

Publishing to LRCLIB needs no account and no key. What it needs instead is
a few seconds of this machine's CPU, which is the whole of the spam
control: ``POST /api/request-challenge`` hands back a ``prefix`` and a
``target``, and a valid ``X-Publish-Token`` is ``prefix:nonce`` where the
nonce is a number whose SHA-256 clears the target.

## The rule, taken from the server rather than guessed at

The documentation points at LRCGET's solver for an example, and LRCGET's
loop is *nearly* the server's rule: it walks the bytes of the hash against
the bytes of the target, and stops one byte short of the end. That last
byte only ever matters when the first thirty-one are identical, which is
not a case anybody will meet, but it is a difference and this is a
verification somebody else performs.

So the rule here is the SERVER's, read off ``verify_answer`` in
lrclib/server/src/utils.rs: the hash and the target must be the same
length, and the hash is walked against the target most significant byte
first, failing on the first byte that is greater and passing on the first
that is smaller. That is exactly ``digest <= target`` for two byte strings
of equal length, because Python compares bytes the same way, and spelling
it as the comparison rather than as the loop is what makes it obviously
the same rule every time it is read.

## The cost of it, measured

The target the documentation shows is ``000000FF`` followed by 28 zero
bytes, so a hash clears it about once in 2**32/255 tries: 16.8 million
expected. Twelve solves at that target on this machine (Python 3.13,
Apple silicon):

| | |
|---|---|
| median | 4.67s |
| mean | 7.33s |
| slowest of the twelve | 24.29s |
| rate | 3.32M hashes/s |

The spread is the shape of the thing rather than noise: each attempt is
an independent coin, so the time to a hit is geometric and the tail is
long. It is why there is a progress line and a way to stop rather than a
spinner and a promise.

Two decisions come out of the measurement. **The hasher is primed with
the prefix once and copied per attempt** rather than rebuilt: 3.14M
hashes/s against 2.01M for rebuilding, a 56% difference, and the prefix is
32 bytes of every 40-odd hashed. And **the deadline and the stop flag are
read every 50,000 attempts** rather than every attempt: at this rate that
is a look every 15ms, and it costs nothing measurable (3.61M/s chunked
against 3.53M/s unchunked, five runs each, which is inside the noise).

The target is not fixed, which is the other reason the solve is bounded
and interruptible: the server divides it down as recent submissions rise,
so a busy hour is a harder challenge and there is no number here that can
promise a duration.

Pure and Qt-free. The clock is passed in, the stop flag is the caller's,
and nothing in here knows what a lyric is.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# How long a challenge is good for. LRCLIB's documentation says five
# minutes and the server agrees: the cache the prefix is kept in is built
# with a five minute time to live. Ours, and it is a fact about their
# service rather than a preference of this app's.
LIFETIME_SECONDS = 300.0

# Taken off the lifetime before the solver gives up on a challenge, so a
# nonce found at the very end still has time to be sent. Set by eye and
# said so: it is the round trip of one POST plus room for a slow one, and
# nothing here measures that. The only case it governs is a challenge hard
# enough to take five minutes, which at the documented target never
# happens (see the module docstring).
SEND_MARGIN_SECONDS = 15.0

# Attempts between two looks at the clock and the stop flag. Measured: the
# chunked loop runs at 3.61M hashes/s against 3.53M for the unchunked one
# over five runs each, so the check costs nothing readable, and at that
# rate 50,000 attempts is a look every 15ms.
CHUNK = 50_000

# The digest is 32 bytes, so a target that is not is not a target. Checked
# rather than assumed: a length mismatch is what the server answers False
# for, and a client that hashed against a short target would solve
# something nobody asked for.
DIGEST_BYTES = hashlib.sha256().digest_size


class ChallengeError(ValueError):
    """A challenge that cannot be worked on: a target that is not 32 bytes
    of hex, or a prefix that is empty. Its own type because the publish
    path reports it as a failure of the exchange rather than crashing on
    it, and because "this is not a challenge" is a different thing from
    "this challenge was not solved in time"."""


@dataclass(frozen=True)
class Challenge:
    """What ``POST /api/request-challenge`` answered with.

    Both halves are strings because both halves are strings on the wire,
    and the prefix goes back out verbatim in the token: re-encoding it
    would be this app deciding what somebody else's identifier means.

    ``asked_at`` is a reading of the monotonic clock taken when the answer
    landed, which is what the deadline is measured from. It is here rather
    than beside the solver because the thing that expires is the
    CHALLENGE, and a challenge that has been carried around for four
    minutes is four minutes closer to useless whoever is holding it. It has
    no default, deliberately: a challenge that did not say when it arrived
    would read as having arrived at the start of time and expire on the
    spot, which is the kind of default that passes every test written
    against it.
    """

    prefix: str
    target: str
    asked_at: float

    @property
    def target_bytes(self) -> bytes:
        """The target as the bytes a digest is compared against. Raises
        ChallengeError for anything that is not 32 bytes of hex."""
        try:
            raw = bytes.fromhex(self.target)
        except ValueError as exc:
            raise ChallengeError(f"target is not hex: {self.target!r}") from exc
        if len(raw) != DIGEST_BYTES:
            raise ChallengeError(
                f"target is {len(raw)} bytes, not {DIGEST_BYTES}"
            )
        return raw

    def deadline(self) -> float:
        """The monotonic instant after which a nonce is not worth having:
        the challenge's own lifetime, less the room a send needs."""
        return self.asked_at + LIFETIME_SECONDS - SEND_MARGIN_SECONDS

    def expected_attempts(self) -> float:
        """How many hashes this target should take, on average.

        A hash is uniform over 2**256 values and any of the first
        ``target + 1`` of them clears it, so the expected count is the
        reciprocal of that share. Shown to the user beside the count so far,
        because "13 million of about 17 million" is the only honest way to
        describe progress through a memoryless process: it is not a
        fraction of the work done, it is how big the coin is.
        """
        target = int.from_bytes(self.target_bytes, "big")
        if target <= 0:
            return float("inf")
        return 2 ** (DIGEST_BYTES * 8) / (target + 1)


@dataclass(frozen=True)
class Solution:
    """A nonce that clears the target, and what it cost to find."""

    nonce: int
    attempts: int
    seconds: float

    def token(self, prefix: str) -> str:
        """The ``X-Publish-Token``: the challenge's prefix and this nonce,
        joined by a colon. The server splits on exactly one colon and reads
        the halves back, so the format has one definition and this is it."""
        return f"{prefix}:{self.nonce}"


def clears(digest: bytes, target: bytes) -> bool:
    """Whether this digest answers this target.

    The server's rule, and it is one comparison: two byte strings of the
    same length compare in Python the way the server walks them, most
    significant byte first, so ``<=`` IS "fails on a greater byte, passes
    on a smaller one, passes when every byte is equal". A length that does
    not match is False, because a hash and a target of different sizes have
    no meaningful order between them and the server says so first.
    """
    if len(digest) != len(target):
        return False
    return digest <= target


def solve(
    challenge: Challenge,
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[int, float], None]] = None,
    now: Callable[[], float] = time.monotonic,
    chunk: int = CHUNK,
) -> Optional[Solution]:
    """Find a nonce for this challenge, or give up and say so.

    Returns None for both ways of giving up, because they are the same
    thing to the caller: there is no token. Which one it was is on the
    record at INFO, and the publish path tells the two apart by asking the
    clock itself, since a challenge past its deadline needs a new challenge
    and a cancelled one needs nothing at all.

    ``should_stop`` is how the window's cancel reaches in, and it is asked
    on the same schedule as the clock. ``on_progress`` is handed the
    attempts so far and the seconds so far, and is called once per chunk:
    it is what the count on screen is drawn from, and it is deliberately
    not given a percentage, because there is no such number here.

    Raises ChallengeError for a target that is not a target. That is not a
    failure of the solve, it is a challenge that was never one.
    """
    target = challenge.target_bytes
    if not challenge.prefix:
        raise ChallengeError("challenge has no prefix")
    deadline = challenge.deadline()

    # Primed once and copied per attempt: the prefix is 32 bytes of every
    # hash and hashing it again per nonce measured 36% slower. See the
    # module docstring.
    primed = hashlib.sha256(challenge.prefix.encode("utf-8"))
    started = now()
    nonce = 0
    while True:
        end_of_chunk = nonce + max(1, chunk)
        while nonce < end_of_chunk:
            attempt = primed.copy()
            attempt.update(str(nonce).encode("ascii"))
            # ``clears`` is the rule and this is the rule with its one
            # precondition already met: the length was checked above, once,
            # rather than 17 million times. A call per attempt measured
            # about a sixth of the whole solve, and what the two spellings
            # agree about is asserted rather than assumed -- every nonce
            # this returns is put back through ``clears`` in the suite.
            if attempt.digest() <= target:
                seconds = now() - started
                logger.info(
                    "solved the challenge in %d attempts, %.2fs", nonce + 1, seconds
                )
                return Solution(nonce=nonce, attempts=nonce + 1, seconds=seconds)
            nonce += 1
        elapsed = now() - started
        if should_stop is not None and should_stop():
            logger.info("challenge solve stopped after %d attempts", nonce)
            return None
        if now() >= deadline:
            logger.info(
                "challenge expired after %d attempts, %.1fs", nonce, elapsed
            )
            return None
        if on_progress is not None:
            on_progress(nonce, elapsed)
