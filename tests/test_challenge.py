"""LRCLIB's proof of work: the rule, and the loop that answers it.

The rule is somebody else's and is verified on their machine, so the thing
worth testing is not "does our solver agree with itself" but "does it agree
with the SERVER". So the server's own loop is written out here, once, from
``verify_answer`` in lrclib/server/src/utils.rs, and the module's one-line
comparison is checked against it over cases chosen to break it: equal
bytes, a difference in the first byte, a difference in the last, and the
length mismatch the server refuses first.

Every target here is an easy one. The real one takes 16.8 million hashes by
median and that is measured in challenge.py rather than sat through in a
test; what these ask about is the loop's behaviour, which is the same at
one hash as at seventeen million.
"""

from __future__ import annotations

TIER = "unit"  # Qt-free logic, called directly

import hashlib

import pytest

from sottovoce import challenge as c


# The documented example, from LRCLIB's own API page.
DOCUMENTED_TARGET = "000000FF" + "00" * 28
# One hash in 256 clears this, so solving it is instant and everything
# about the loop is the same.
EASY = "00FF" + "00" * 30
# Every hash clears this, so a solve is over on the first attempt.
ANY = "FF" * 32


def at(target, prefix="prefix", asked_at=0.0):
    return c.Challenge(prefix=prefix, target=target, asked_at=asked_at)


# -- the rule, against the server's own loop -------------------------------


def as_the_server_does(digest: bytes, target: bytes) -> bool:
    """``verify_answer``, transcribed. Same length or nothing; then walk
    the bytes, fail on the first that is greater, pass on the first that is
    smaller, and pass when they run out."""
    if len(digest) != len(target):
        return False
    for mine, theirs in zip(digest, target):
        if mine > theirs:
            return False
        if mine < theirs:
            break
    return True


CASES = [
    (b"\x00" * 32, b"\x00" * 32),                      # equal all the way
    (b"\x00" * 32, b"\xff" * 32),                      # smaller at once
    (b"\xff" * 32, b"\x00" * 32),                      # greater at once
    (b"\x00" * 31 + b"\x01", b"\x00" * 32),            # greater in the LAST byte
    (b"\x00" * 31 + b"\x00", b"\x00" * 31 + b"\x01"),  # smaller in the last byte
    (b"\x00\xff" + b"\x00" * 30, b"\x01\x00" + b"\x00" * 30),  # first byte decides
    (b"\x01\x00" + b"\x00" * 30, b"\x00\xff" + b"\x00" * 30),
]


@pytest.mark.parametrize("digest,target", CASES, ids=range(len(CASES)))
def test_the_rule_is_the_servers_rule(digest, target):
    assert c.clears(digest, target) is as_the_server_does(digest, target)


def test_a_target_of_the_wrong_length_clears_nothing():
    """The server checks the lengths before it compares anything, and
    answers False when they differ. A hash and a target of different sizes
    have no order between them worth acting on."""
    assert c.clears(b"\x00" * 32, b"\x00" * 31) is False
    assert c.clears(b"\x00" * 31, b"\x00" * 32) is False


def test_the_last_byte_is_not_forgotten():
    """LRCGET's client-side loop stops one byte short of the end, which is
    the one place the documented example implementation and the server
    disagree. It is a case nobody will meet — the first thirty-one bytes
    have to be identical — and it is exactly the reason the rule here was
    taken off the server rather than off the example."""
    over = b"\x00" * 31 + b"\x01"
    assert c.clears(over, b"\x00" * 32) is False
    assert as_the_server_does(over, b"\x00" * 32) is False


# -- what a challenge says about itself ------------------------------------


def test_the_documented_target_is_about_seventeen_million_hashes():
    """The number the whole design of the progress line rests on: a hash
    clears ``000000FF...`` about once in 2**32/255. Measured against the
    real thing in challenge.py at 15.9 million by median over twelve
    solves, which is the same number arriving from the other direction."""
    assert round(at(DOCUMENTED_TARGET).expected_attempts()) == 16_843_009


def test_a_target_that_is_not_thirty_two_bytes_of_hex_is_not_a_challenge():
    for bad in ("nonsense", "00FF", "00" * 31, ""):
        with pytest.raises(c.ChallengeError):
            at(bad).target_bytes


def test_the_deadline_leaves_room_to_send_what_was_found():
    """A nonce found with two seconds of the challenge left is a nonce that
    will not arrive in time, so the solver stops before then."""
    asked = at(EASY, asked_at=1000.0)
    assert asked.deadline() == 1000.0 + c.LIFETIME_SECONDS - c.SEND_MARGIN_SECONDS
    assert c.LIFETIME_SECONDS == 300.0  # LRCLIB's own five minutes


# -- solving ---------------------------------------------------------------


def test_a_solved_nonce_really_clears_the_target():
    """What ties the loop to the rule. The solver compares inline for
    speed — a call per attempt measured about a sixth of the whole solve —
    so what keeps the two spellings honest is that every answer it gives is
    put back through ``clears`` here.
    """
    asked = at(EASY)
    found = c.solve(asked, now=fake_clock())

    assert found is not None
    digest = hashlib.sha256(f"{asked.prefix}{found.nonce}".encode()).digest()
    assert c.clears(digest, asked.target_bytes) is True
    assert found.attempts == found.nonce + 1


def test_the_token_is_the_prefix_and_the_nonce_joined_by_a_colon():
    """The server splits on the colon and reads the halves back, so this
    format is not ours to spell differently."""
    assert c.Solution(nonce=7, attempts=8, seconds=0.1).token("abc") == "abc:7"


def test_a_challenge_with_no_prefix_is_not_a_challenge():
    with pytest.raises(c.ChallengeError):
        c.solve(at(ANY, prefix=""))


def test_stopping_gives_up_and_says_nothing_was_found():
    """The cancel, read on the same schedule as the clock. Returning None
    rather than raising because the caller does not care which way it gave
    up: either way there is no token."""
    asked = at("00" * 32)  # nothing will ever clear this
    assert c.solve(asked, should_stop=lambda: True, chunk=1, now=fake_clock()) is None


def test_a_challenge_that_runs_out_of_time_is_abandoned():
    """The other way to give up, and the one the publish path recovers from
    by asking for another challenge. The clock is the caller's, so five
    minutes takes no time at all here."""
    ticking = fake_clock(step=100.0)
    assert c.solve(at("00" * 32, asked_at=0.0), chunk=1, now=ticking) is None


def test_progress_is_reported_as_it_goes():
    """What the window's count is drawn from. Once per chunk, with the
    attempts and the seconds so far, and never a percentage: there is no
    such number for a memoryless process."""
    seen = []
    ticking = fake_clock(step=1.0)
    c.solve(
        at("00" * 32, asked_at=0.0),
        chunk=1,
        on_progress=lambda tried, elapsed: seen.append((tried, elapsed)),
        now=ticking,
    )

    assert seen, "nothing was reported during a solve of several minutes"
    tried = [count for count, _ in seen]
    assert tried == sorted(tried) and tried[0] > 0
    assert all(elapsed > 0 for _, elapsed in seen)


def test_a_target_everything_clears_is_solved_on_the_first_attempt():
    found = c.solve(at(ANY), now=fake_clock())
    assert found is not None
    assert (found.nonce, found.attempts) == (0, 1)


def fake_clock(step=0.0, start=0.0):
    """A monotonic clock the test drives. Every call advances it by
    ``step``, which is how a five minute deadline is reached in a
    millisecond."""
    now = [start]

    def read():
        now[0] += step
        return now[0]

    return read
