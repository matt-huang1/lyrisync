"""Who may publish, what exactly goes, and what happens when it does not.

Everything here is the policy rather than the plumbing: the gate the menu
asks, the gate LRCLIB's own answer decides, the body of the request, and
the sequence of three requests with the proof of work in the middle. The
network is faked at ``post``, which is this module's own seam and is the
whole of what it does not own; the connection-level version of the same
path is tests/window/test_window_publish.py.

Two rules run through all of it. A refusal names itself and the boolean is
derived from the name, so a gate cannot answer "no" for a reason it has
forgotten. And nothing is sent by anything except ``send``, which is only
ever called after a person has read the submission these tests assert the
shape of.
"""

from __future__ import annotations

TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce import challenge as c
from sottovoce import lyrics_provider as lp
from sottovoce import publish as p
from sottovoce.failure import CONNECTION, HTTP, FetchFailure


PLAIN = "first line\nsecond line\nthird line"
LRC = "[00:01.00] first line\n[00:05.00] second line\n[00:09.00] third line\n"

RECORD = {
    "trackName": "Blue Hour",
    "artistName": "Someone",
    "albumName": "First Light",
    "duration": 213.0,
    "instrumental": False,
    "plainLyrics": PLAIN,
    "syncedLyrics": None,
}

OFFERABLE = dict(
    has_sync=True,
    already_published=False,
    remote_was_plain_only=True,
    title="Blue Hour",
    artist="Someone",
    album="First Light",
    duration_ms=214000,
)


# -- the gate the menu asks ------------------------------------------------


def test_a_song_with_a_sync_lrclib_has_only_words_for_is_offered():
    assert p.standing_refusal(**OFFERABLE) == ""
    assert p.may_offer(**OFFERABLE) is True


REFUSALS = [
    ({"has_sync": False}, p.NO_SYNC),
    ({"already_published": True}, p.ALREADY_SENT),
    ({"remote_was_plain_only": False}, p.NOT_PLAIN_ONLY),
    ({"title": ""}, p.NO_SONG),
    ({"artist": ""}, p.NO_SONG),
    ({"album": ""}, p.INCOMPLETE),
    ({"duration_ms": None}, p.INCOMPLETE),
]


@pytest.mark.parametrize("change,expected", REFUSALS, ids=[r for _, r in REFUSALS])
def test_each_refusal_names_itself(change, expected):
    """And the boolean follows the name rather than being kept beside it."""
    state = {**OFFERABLE, **change}
    assert p.standing_refusal(**state) == expected
    assert p.may_offer(**state) is False


def test_the_same_sync_is_never_offered_twice():
    """The record of a publication is what makes this a one-way door: the
    entry goes as soon as the sync has gone, and a re-sync brings it back
    because a re-sync is a different thing to send."""
    assert p.standing_refusal(**{**OFFERABLE, "already_published": True}) == (
        p.ALREADY_SENT
    )


# -- exactly what will be sent ---------------------------------------------


def submission(**overrides):
    return p.Submission(
        **{
            "track_name": "Blue Hour",
            "artist_name": "Someone",
            "album_name": "First Light",
            "duration": 213,
            "plain_lyrics": PLAIN,
            "synced_lyrics": LRC,
            **overrides,
        }
    )


def test_the_body_carries_the_six_fields_lrclib_documents_and_no_others():
    """The names are theirs. A seventh field, or one of these spelled our
    way, is a request they would refuse or misread."""
    assert submission().payload() == {
        "trackName": "Blue Hour",
        "artistName": "Someone",
        "albumName": "First Light",
        "duration": 213,
        "plainLyrics": PLAIN,
        "syncedLyrics": LRC,
    }


def test_the_preview_holds_everything_the_body_does():
    """"Show exactly what will be sent" is the rule, and this is where it
    is checkable: every value in the payload appears in the text the window
    puts on screen."""
    text = submission().preview()
    for value in submission().payload().values():
        assert str(value) in text


def test_the_metadata_rows_are_the_payload_in_the_order_it_is_sent():
    assert submission().rows() == (
        ("Track", "Blue Hour"),
        ("Artist", "Someone"),
        ("Album", "First Light"),
        ("Duration", "213 seconds"),
    )


# -- the gate LRCLIB decides -----------------------------------------------


def verify(record=RECORD, lrc=LRC, **overrides):
    def fetch(*_args, **_kwargs):
        if isinstance(record, Exception):
            raise record
        return record

    return p.verify(
        **{
            "title": "Blue Hour",
            "artist": "Someone",
            "album": "First Light",
            "duration_ms": 214000,
            "lrc_text": lrc,
            "fetch": fetch,
            **overrides,
        }
    )


def test_the_submission_is_built_from_lrclibs_own_record():
    """Their spelling and their duration, not Spotify's, and it matters:
    a submission is matched to an existing track by its normalised names
    and a duration within two seconds, so sending our own album name back
    would create a second record rather than adding to theirs."""
    answer = verify()

    assert answer.ready is True
    assert answer.submission.duration == 213, "Spotify's 214 was sent back"
    assert answer.submission.album_name == "First Light"
    assert answer.submission.plain_lyrics == PLAIN
    assert answer.submission.synced_lyrics == LRC


def test_a_record_that_names_nothing_falls_back_to_what_was_asked():
    """A record is still the record this question found, whatever it
    chose to repeat back."""
    answer = verify(record={"plainLyrics": PLAIN})

    assert answer.submission.track_name == "Blue Hour"
    assert answer.submission.artist_name == "Someone"
    assert answer.submission.album_name == "First Light"
    assert answer.submission.duration == 214


FRESH_REFUSALS = [
    (None, p.NO_RECORD),
    ({**RECORD, "syncedLyrics": "[00:01.00] first line\n"}, p.ALREADY_SYNCED),
    ({**RECORD, "instrumental": True}, p.INSTRUMENTAL),
    ({**RECORD, "plainLyrics": "   "}, p.NOT_PLAIN_ONLY),
]


@pytest.mark.parametrize(
    "record,expected", FRESH_REFUSALS, ids=[r for _, r in FRESH_REFUSALS]
)
def test_the_fresh_check_refuses_and_says_why(record, expected):
    answer = verify(record=record)
    assert answer.ready is False
    assert answer.refusal == expected
    assert answer.submission is None


def test_a_sync_of_other_words_is_refused():
    """The scope, enforced rather than assumed. This version adds timings
    to text LRCLIB already holds, so the timings have to be for that text:
    a sync built from lyrics pasted in during an outage is a set of stamps
    against words nobody else has."""
    other = "[00:01.00] words from somewhere else\n"
    answer = verify(lrc=other)

    assert answer.refusal == p.DIFFERENT_WORDS


def test_a_lookup_that_could_not_be_made_is_neither_a_yes_nor_a_no():
    """A third answer, and it has to be: "LRCLIB could not be asked" is not
    "this may not be published", and treating it as one would turn a
    dropped wifi connection into a permanent refusal."""
    failure = FetchFailure(kind=CONNECTION, detail="no route to host")
    answer = verify(record=lp.LyricsError("offline", failure))

    assert answer.ready is False
    assert answer.refusal == ""
    assert answer.failure is failure


# -- the exchange ----------------------------------------------------------


class FakeLrclib:
    """The two POSTs, and what they answer.

    ``post`` is publish.py's own seam onto the network: everything above it
    here is real, including the token the header carries and the order the
    two requests go out in.
    """

    def __init__(self, *, publish_raises=None, challenge_raises=None):
        self.sent = []
        self.slept = []
        self._publish_raises = list(publish_raises or [])
        self._challenge_raises = list(challenge_raises or [])
        self._prefixes = 0

    def post(self, url, payload, headers=None):
        self.sent.append((url, payload, headers or {}))
        if url == lp.LRCLIB_CHALLENGE_URL:
            if self._challenge_raises:
                raise self._challenge_raises.pop(0)
            self._prefixes += 1
            return {"prefix": f"prefix{self._prefixes}", "target": "FF" * 32}
        if self._publish_raises:
            raise self._publish_raises.pop(0)
        return None  # a 201 with no body, which is what publishing answers

    def urls(self):
        return [url for url, _, _ in self.sent]


def send(service=None, **overrides):
    service = service or FakeLrclib()
    result = p.send(
        submission(),
        post=service.post,
        sleep=service.slept.append,
        now=lambda: 0.0,
        **overrides,
    )
    return service, result


def test_a_publish_asks_for_a_challenge_solves_it_and_sends_the_token():
    """The whole sequence, in order, with the token the solve produced."""
    service, result = send()

    assert service.urls() == [lp.LRCLIB_CHALLENGE_URL, lp.LRCLIB_PUBLISH_URL]
    _, payload, headers = service.sent[1]
    assert payload == submission().payload()
    assert headers[p.TOKEN_HEADER] == "prefix1:0"  # every hash clears FF..FF
    assert result.published is True
    assert result.outcome == p.PUBLISHED
    assert "sent to LRCLIB" in result.reason


def test_the_gap_lrclib_asks_for_is_taken_before_every_request():
    """Their instruction is sequential requests with a short delay between
    them, and it is one rule with no exception: the same number the album
    warm was measured at."""
    service, _ = send()
    assert service.slept == [lp.REQUEST_GAP_SECONDS] * 2


def test_a_refused_token_is_a_failure_that_can_be_tried_again():
    """400 is what LRCLIB answers for a token that is wrong, expired or
    already spent, and all three are recovered from the same way: a fresh
    challenge, which is what pressing again does."""
    refused = lp.LyricsError(
        "HTTP 400", FetchFailure(kind=HTTP, status=p.TOKEN_REFUSED_STATUS)
    )
    _, result = send(FakeLrclib(publish_raises=[refused]))

    assert result.outcome == p.FAILED
    assert result.may_try_again is True
    assert result.reason.startswith("the publish token was refused")


def test_a_rate_limit_is_reported_in_the_apps_own_words():
    """The one status that carries an instruction, and the sentence for it
    is the one the rest of the app already uses."""
    limited = lp.LyricsError(
        "HTTP 429", FetchFailure(kind=HTTP, status=429, retry_after=90.0)
    )
    _, result = send(FakeLrclib(publish_raises=[limited]))

    assert result.outcome == p.FAILED
    assert result.reason == (
        "the sync could not be sent · LRCLIB asked this app to slow down"
    )


def test_a_challenge_that_cannot_be_requested_never_reaches_the_publish():
    broken = lp.LyricsError("offline", FetchFailure(kind=CONNECTION))
    service, result = send(FakeLrclib(challenge_raises=[broken]))

    assert service.urls() == [lp.LRCLIB_CHALLENGE_URL]
    assert result.outcome == p.FAILED
    assert result.reason == (
        "the challenge could not be requested · could not reach lrclib.net"
    )


def test_a_challenge_that_expires_mid_solve_is_replaced_and_said_so():
    """The recovery that costs nothing but the CPU already spent, and it is
    reported rather than done quietly: the seconds were real."""
    solves = [None, c.Solution(nonce=4, attempts=5, seconds=0.2)]
    seen = []
    service, result = send(
        solve=lambda *_args, **_kwargs: solves.pop(0),
        on_progress=seen.append,
    )

    assert [progress.stage for progress in seen].count(p.EXPIRED) == 1
    assert service.urls().count(lp.LRCLIB_CHALLENGE_URL) == 2
    assert result.published is True
    # And the second challenge is a different one: a prefix reused would be
    # a token the server has already spent.
    _, _, headers = service.sent[-1]
    assert headers[p.TOKEN_HEADER] == "prefix2:4"


def test_a_challenge_that_keeps_expiring_gives_up_and_names_it():
    service, result = send(solve=lambda *_args, **_kwargs: None)

    assert service.urls().count(lp.LRCLIB_CHALLENGE_URL) == p.MAX_CHALLENGES
    assert service.urls().count(lp.LRCLIB_PUBLISH_URL) == 0
    assert result.outcome == p.FAILED
    assert str(p.MAX_CHALLENGES) in result.reason


def test_stopping_before_anything_starts_asks_nothing_at_all():
    service, result = send(should_stop=lambda: True)

    assert service.urls() == []
    assert result.outcome == p.STOPPED
    assert result.may_try_again is False


def test_stopping_during_the_solve_sends_nothing():
    """The cancel, and the property that makes it worth having: a solve
    given up on costs only the CPU already spent, because the thing it was
    working towards had not left this Mac.

    Stopped after the challenge went out on purpose, which is the case
    worth asserting: the CPU has been spent, a prefix is in hand, and the
    submission still does not go.
    """
    asked = []

    def stopping():
        # False while the challenge is requested, True by the time the
        # solver looks: the same shape as a cancel arriving mid-solve.
        asked.append(True)
        return len(asked) > 2

    service, result = send(
        solve=lambda *_args, **_kwargs: None, should_stop=stopping
    )

    assert service.urls() == [lp.LRCLIB_CHALLENGE_URL]
    assert result.outcome == p.STOPPED


def test_a_refusal_is_not_something_to_try_again():
    """Derived from the outcome rather than recorded: a fact about the song
    does not change because somebody pressed a button."""
    assert p.Result(p.REFUSED, reason=p.ALREADY_SYNCED).may_try_again is False
    assert p.Result(p.PUBLISHED).may_try_again is False
    assert p.Result(p.FAILED).may_try_again is True


def test_an_unreadable_challenge_is_a_failure_rather_than_a_crash():
    class Nonsense(FakeLrclib):
        def post(self, url, payload, headers=None):
            self.sent.append((url, payload, headers or {}))
            return {"prefix": "p", "target": "not hex at all"}

    _, result = send(Nonsense())

    assert result.outcome == p.FAILED
    assert "challenge could not be read" in result.reason


# -- what it says while it waits -------------------------------------------


def test_the_solve_is_reported_as_a_count_against_an_expected_count():
    """Never a percentage. Each hash is an independent coin, so a third of
    the expected count is not a third of the way through anything — what
    the two numbers say is how big the problem is."""
    said = p.progress_text(
        p.Progress(p.SOLVING, attempts=3_150_000, seconds=1.0, expected=16_843_009.0)
    )
    assert said == (
        "solving the proof of work · 3,150,000 of about 16,843,009 hashes · 1s"
    )


STAGES = [
    (p.CHECKING, "asking LRCLIB what it has for this song"),
    (p.ASKING, "asking LRCLIB for a challenge"),
    (p.EXPIRED, "the challenge expired, asking for another"),
    (p.SENDING, "sending the sync"),
    (p.SOLVING, "solving the proof of work"),
]


@pytest.mark.parametrize("stage,expected", STAGES, ids=[s for s, _ in STAGES])
def test_every_stage_has_something_to_say(stage, expected):
    assert p.progress_text(p.Progress(stage)) == expected


def test_the_time_it_took_is_reported_when_it_worked():
    """The one thing worth saying at the end of a publish that went right,
    because the middle of it was seconds of somebody's CPU."""
    assert p.solved_in(4.67, 15_947_532) == "15,947,532 hashes in 4.7s"
