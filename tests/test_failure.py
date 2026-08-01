"""Why a lookup failed, and the one place it is put into words.

The window used to be able to say exactly one thing about a failed fetch:
"lyrics unavailable, will retry". True of a 503, of a laptop with the wifi
off, and of a request that timed out on the third attempt — and the app
knew which and threw it away at the door.

These check the two halves of not throwing it away: the provider attaching
what happened and where in the fallback chain, and ``describe`` turning
that into the one line both the window and the terminal tool show.
"""

from __future__ import annotations

TIER = "unit"  # Qt-free logic, called directly

import http.client

import pytest

from sottovoce import failure as f
from sottovoce import lyrics_provider as lp
from sottovoce.player_monitor import PlaybackState, PlayerSnapshot

SNAPSHOT = PlayerSnapshot(
    state=PlaybackState.PLAYING,
    track_id="track123",
    title="Song",
    artist="Artist",
    album="Album",
    duration_ms=210_000,
)


# -- the words -------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    (
        (
            f.FetchFailure(kind=f.HTTP, status=503, attempt=f.ATTEMPT_ALBUM),
            "LRCLIB answered HTTP 503 · album match",
        ),
        (
            f.FetchFailure(kind=f.TIMEOUT, attempt=f.ATTEMPT_SEARCH),
            "LRCLIB did not answer in time · search",
        ),
        (
            f.FetchFailure(kind=f.CONNECTION, attempt=f.ATTEMPT_EXACT),
            "could not reach lrclib.net · title and artist",
        ),
        (
            f.FetchFailure(kind=f.PAYLOAD, attempt=f.ATTEMPT_SEARCH),
            "LRCLIB's answer could not be read · search",
        ),
        (f.FetchFailure(kind=f.UNKNOWN, detail="boom"), "the lookup failed: boom"),
        (f.FetchFailure(), "the lookup failed"),
        (None, ""),
    ),
)
def test_describe(given, expected):
    assert f.describe(given) == expected


def test_the_socket_s_own_words_stay_off_the_window():
    """"[Errno 8] nodename nor servname provided, or not known" is the
    right thing for a log and the wrong thing for a 460-point HUD. The
    kind already says which of the four things happened; the detail is
    kept on the record for the log line that reports it."""
    said = f.describe(
        f.FetchFailure(
            kind=f.CONNECTION,
            attempt=f.ATTEMPT_ALBUM,
            detail="[Errno 8] nodename nor servname provided, or not known",
        )
    )
    assert "Errno" not in said
    assert said == "could not reach lrclib.net · album match"


def test_a_failure_with_nowhere_to_point_says_only_what_happened():
    assert f.describe(f.FetchFailure(kind=f.TIMEOUT)) == "LRCLIB did not answer in time"


def test_nothing_here_carries_an_em_dash():
    """The pairing is the middle dot, like the header's — see
    tests/test_user_facing_text.py."""
    for kind in (f.HTTP, f.TIMEOUT, f.CONNECTION, f.PAYLOAD, f.UNKNOWN):
        said = f.describe(f.FetchFailure(kind=kind, status=500, attempt="x"))
        assert "—" not in said
        assert " · x" in said


# -- what the provider attaches --------------------------------------------


class FakeResponse:
    def __init__(self, status, body=b"{}"):
        self.status = status
        self.body = body


def use_pool(monkeypatch, answer):
    """Point the provider's one door at a canned answer or an exception."""

    class Pool:
        def get(self, path, headers=None):
            if isinstance(answer, BaseException):
                raise answer
            return answer

    monkeypatch.setattr(lp, "_lrclib_pool", lambda: Pool())


def test_an_http_status_is_carried(monkeypatch):
    use_pool(monkeypatch, FakeResponse(503))
    with pytest.raises(lp.LyricsError) as caught:
        lp._fetch_json("https://lrclib.net/api/get?x=1")
    assert caught.value.failure.kind == f.HTTP
    assert caught.value.failure.status == 503


def test_a_connection_failure_is_carried_with_its_detail(monkeypatch):
    use_pool(monkeypatch, OSError("[Errno 61] Connection refused"))
    with pytest.raises(lp.LyricsError) as caught:
        lp._fetch_json("https://lrclib.net/api/get?x=1")
    assert caught.value.failure.kind == f.CONNECTION
    assert "Connection refused" in caught.value.failure.detail


def test_a_protocol_error_is_a_connection_failure_too(monkeypatch):
    """http.client raises its own family for a socket that answered with
    nonsense, and from here that is the same situation."""
    use_pool(monkeypatch, http.client.BadStatusLine("''"))
    with pytest.raises(lp.LyricsError) as caught:
        lp._fetch_json("https://lrclib.net/api/get?x=1")
    assert caught.value.failure.kind == f.CONNECTION


def test_an_unreadable_body_is_its_own_kind(monkeypatch):
    use_pool(monkeypatch, FakeResponse(200, b"not json at all"))
    with pytest.raises(lp.LyricsError) as caught:
        lp._fetch_json("https://lrclib.net/api/get?x=1")
    assert caught.value.failure.kind == f.PAYLOAD


def test_a_404_is_not_a_failure_at_all(monkeypatch):
    """The distinction the whole feature rests on: a definitive "not
    found" is an answer, and the chain moves on to the next attempt."""
    use_pool(monkeypatch, FakeResponse(404))
    assert lp._fetch_json("https://lrclib.net/api/get?x=1") is None


# -- and where in the chain it happened ------------------------------------


def test_the_chain_names_its_attempts():
    labels = [label for label, _ in lp.attempts(SNAPSHOT)]
    assert labels == [f.ATTEMPT_ALBUM, f.ATTEMPT_EXACT, f.ATTEMPT_SEARCH]
    # No album reported: two attempts, because the first two would
    # otherwise be the same request.
    from dataclasses import replace

    labels = [label for label, _ in lp.attempts(replace(SNAPSHOT, album=None))]
    assert labels == [f.ATTEMPT_EXACT, f.ATTEMPT_SEARCH]


def test_the_labels_and_the_urls_cannot_come_apart():
    """One list of pairs rather than two lists of the same length: the
    chain is two attempts long or three depending on the track, and that
    is exactly how a failure comes to name the wrong attempt."""
    from dataclasses import replace

    for snapshot in (SNAPSHOT, replace(SNAPSHOT, album=None)):
        chain = lp.attempts(snapshot)
        assert [url for _, url in chain] == lp.attempt_urls(snapshot)
        for label, url in chain:
            if label is f.ATTEMPT_SEARCH:
                assert url.startswith(lp.LRCLIB_SEARCH_URL)
            else:
                assert url.startswith(lp.LRCLIB_GET_URL)
            assert ("album_name" in url) == (label == f.ATTEMPT_ALBUM)


def test_a_failure_is_stamped_with_the_attempt_it_came_from(monkeypatch, tmp_path):
    """The first attempt errors, so the chain's outcome is unknown — and
    the reason says which link it was, not merely that there was one."""
    provider = lp.LyricsProvider(
        cache_dir=tmp_path / "cache", user_sync_dir=tmp_path / "syncs"
    )

    def fetcher(url):
        if "album_name" in url:
            raise lp.LyricsError(
                "boom", f.FetchFailure(kind=f.HTTP, status=500)
            )
        return None

    monkeypatch.setattr(lp, "_fetch_json", fetcher)
    with pytest.raises(lp.LyricsError) as caught:
        provider.get_lyrics(SNAPSHOT)
    assert caught.value.failure.attempt == f.ATTEMPT_ALBUM
    assert caught.value.failure.status == 500
    assert (
        f.describe(caught.value.failure)
        == "LRCLIB answered HTTP 500 · album match"
    )


def test_a_later_attempt_names_itself(monkeypatch, tmp_path):
    """A 404 on the exact match hands the floor along; the reason follows
    the failure to wherever it actually happened."""
    provider = lp.LyricsProvider(
        cache_dir=tmp_path / "cache", user_sync_dir=tmp_path / "syncs"
    )

    def fetcher(url):
        if url.startswith(lp.LRCLIB_SEARCH_URL):
            raise lp.LyricsError("boom", f.FetchFailure(kind=f.CONNECTION))
        return None  # both /get attempts 404

    monkeypatch.setattr(lp, "_fetch_json", fetcher)
    with pytest.raises(lp.LyricsError) as caught:
        provider.get_lyrics(SNAPSHOT)
    assert caught.value.failure.attempt == f.ATTEMPT_SEARCH
    assert caught.value.failure.kind == f.CONNECTION


def test_a_bare_lyrics_error_still_carries_something(monkeypatch, tmp_path):
    """Every existing ``raise LyricsError("...")`` stays valid, and a
    reason built from the message is better than no reason."""
    error = lp.LyricsError("offline")
    assert error.failure.kind == f.UNKNOWN
    assert error.failure.detail == "offline"
    assert str(error) == "offline"


def test_stamping_an_attempt_does_not_rewrite_the_exception():
    """A new exception rather than a mutated one: an exception that
    rewrites itself as it propagates is a poor thing to read a traceback
    from."""
    original = lp.LyricsError("boom", f.FetchFailure(kind=f.HTTP, status=500))
    stamped = original.at(f.ATTEMPT_SEARCH)
    assert stamped is not original
    assert original.failure.attempt == ""
    assert stamped.failure.attempt == f.ATTEMPT_SEARCH
    assert str(stamped) == "boom"
