"""One sync going back to LRCLIB, from the menu entry to the bytes on the
wire.

Everything in this file is the real thing except one object: the
connection. The window is real, the menu model is real, the click arrives
through ``Menu.trigger`` the way a native click does, the publish window is
real and runs its real workers on the real thread pool, ``publish.py``
walks the real exchange, ``challenge.py`` really solves the proof of work,
``lyrics_provider`` really builds and reads the requests, and
``ConnectionPool`` really pools them. What is faked is LRCLIB, at the
socket, which is the only thing in that list that is not this app.

The song's lyrics arrive the same way: a real ``PlayerMonitor`` over a fake
Spotify notices a track change, and the real ``FetchTask`` writes the real
cache entry. That matters more here than anywhere else, because half the
gate on publishing IS that cache entry, and a test that wrote it by hand
would be a test that had answered its own question.

The target the fake challenge hands back is an easy one. What it costs in
the world is measured in challenge.py (16.8 million hashes, 4.67s by
median); the loop is the same loop at one hash as at seventeen million, and
a suite that sat through the real thing would be a suite nobody runs.
"""

TIER = "integration"  # the menu, the window, the exchange, and the pool

import hashlib
import json
import time

import pytest

from sottovoce import challenge
from sottovoce import lyrics_provider as lp
from sottovoce import menu as m
from sottovoce import publish
from sottovoce import publish_window as pw
from sottovoce.view_model import Mode

from helpers import APP, SONG, play

# The words LRCLIB is holding for this song, and the sync somebody tapped
# out over them. The second is the first with stamps in front of it, which
# is exactly the case this version of publishing is for.
PLAIN = "first line\nsecond line\nthird line"
LRC = "[00:01.00] first line\n[00:05.00] second line\n[00:09.00] third line\n"

TRACK_ID = "0Ab1Cd2Ef3"

# Nothing clears this, so a solve runs until it is stopped or times out.
IMPOSSIBLE = "00" * 32
# Everything clears this, so the first attempt answers.
TRIVIAL = "FF" * 32


def record(**overrides):
    """What ``/api/get`` answers with. LRCLIB's own spelling of the album
    and its own duration, both one step away from Spotify's on purpose."""
    return json.dumps({
        "trackName": SONG["title"],
        "artistName": SONG["artist"],
        "albumName": "First Light (Deluxe)",
        "duration": 213.0,
        "instrumental": False,
        "plainLyrics": PLAIN,
        "syncedLyrics": None,
        **overrides,
    }).encode()


def challenge_body(target=TRIVIAL, prefix="VXMwW2qPfW2gkCNSl1i708NJkDghtAyU"):
    return json.dumps({"prefix": prefix, "target": target}).encode()


def service_for(lrclib, target=TRIVIAL):
    """LRCLIB answering every request this path can make.

    Ordered most specific first, like every other route table here. The
    publish check asks the same ``/api/get`` the song's own lookup asked,
    which is not an accident to be worked around: it is the same question,
    and the whole point of asking it twice is that the answer may have
    changed in between. A test that wants a different answer the second
    time says so with ``later``, after the song has played.
    """
    return lrclib(
        ("request-challenge", (200, challenge_body(target))),
        ("api/publish", (201, b"")),
        ("api/get", (200, record())),
    )


def later(service, *routes):
    """Change what LRCLIB says from here on.

    Which is the situation the fresh check exists for: the cache holds
    what was true when the song played, and this is the world moving on
    between then and somebody choosing to publish.
    """
    service.routes = list(routes) + service.routes


@pytest.fixture
def ready(make_window, lrclib, spotify, fetching):
    """A song played, its plain lyrics cached for real, and the user's own
    sync of it on disk.

    This is the state the menu entry is gated on, reached the way the app
    reaches it rather than assembled: the lookup that wrote the cache entry
    is the real one, over the fake connection.
    """

    def arrive(target=TRIVIAL, sync=LRC):
        service = service_for(lrclib, target=target)
        window = make_window()
        play(window, spotify)
        assert window._view_model.display().mode is Mode.PLAIN
        window._provider.save_user_sync(TRACK_ID, sync)
        window._reconsider_publishing()
        window._refresh_menu()
        return window, service

    return arrive


def open_publisher(window):
    """Click the menu entry, and wait for the check to answer.

    Through ``Menu.trigger`` because that is the one way in for a click,
    whether it came from the menu bar item or the window's own menu.
    """
    window._menu.trigger(m.PUBLISH)
    publisher = window._publish_window
    assert publisher is not None, "the entry opened nothing"
    wait_until(lambda: publisher.state != pw.CHECKING, "the check never answered")
    return publisher


def wait_until(done, complaint, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if done():
            APP.processEvents()
            return
        time.sleep(0.005)
    raise AssertionError(complaint)


def sent(service):
    """The publish requests that actually left, whole."""
    return service.sent_to("api/publish")


# -- nothing happens by itself ---------------------------------------------


def test_a_sync_saved_to_disk_is_never_sent_anywhere(ready):
    """The first rule, and the one worth asserting before any of the
    others: a completed sync stays local. The entry appears, and that is
    all that appears."""
    window, service = ready()

    assert window._menu.is_visible(m.PUBLISH) is True
    assert sent(service) == []
    assert service.sent_to("request-challenge") == []


def test_opening_the_window_asks_what_lrclib_has_and_sends_nothing(ready):
    """The click is not the consent. It asks a question, shows the answer,
    and waits."""
    window, service = ready()

    publisher = open_publisher(window)

    assert publisher.state == pw.REVIEW
    assert sent(service) == []
    assert service.sent_to("request-challenge") == []


def test_what_is_shown_is_exactly_what_would_be_sent(ready):
    """The whole of the consent: the window's own preview and the body of
    the request are one object, so there is no way for the second to differ
    from the first."""
    window, _ = ready()
    publisher = open_publisher(window)

    shown = publisher.submission
    assert shown.synced_lyrics == LRC
    assert shown.plain_lyrics == PLAIN
    for value in shown.payload().values():
        assert str(value) in shown.preview()


# -- the publish itself ----------------------------------------------------


def publish_it(window, publisher):
    publisher._primary.click()
    wait_until(
        lambda: publisher.state not in (pw.WORKING,), "the publish never came back"
    )
    return publisher


def test_pressing_publish_sends_the_sync_and_says_what_it_cost(ready):
    """The path the milestone exists for, end to end. A challenge is asked
    for, the proof of work is really solved, and the submission goes with
    the token that solve produced."""
    window, service = ready()
    publisher = publish_it(window, open_publisher(window))

    assert publisher.state == pw.DONE
    assert len(sent(service)) == 1
    assert len(service.sent_to("request-challenge")) == 1
    assert "hashes in" in publisher._status.text()


def test_the_body_on_the_wire_is_lrclibs_own_metadata_and_both_sets_of_words(
    ready,
):
    """Sent back with their spelling of the album and their duration, not
    Spotify's: a submission finds an existing track by its normalised names
    and a duration within two seconds, so our own album name would make a
    second record instead of adding to theirs."""
    window, service = ready()
    publish_it(window, open_publisher(window))

    body = json.loads(sent(service)[0].body)
    assert body == {
        "trackName": SONG["title"],
        "artistName": SONG["artist"],
        "albumName": "First Light (Deluxe)",
        "duration": 213,
        "plainLyrics": PLAIN,
        "syncedLyrics": LRC,
    }


def test_the_token_is_a_nonce_that_really_clears_the_challenge(ready):
    """The proof of work, checked the way the server checks it. The prefix
    on the wire is the one LRCLIB handed out and the nonce beside it is one
    this machine actually found."""
    window, service = ready()
    publish_it(window, open_publisher(window))

    token = sent(service)[0].headers[publish.TOKEN_HEADER]
    prefix, _, nonce = token.partition(":")
    assert prefix == "VXMwW2qPfW2gkCNSl1i708NJkDghtAyU"
    digest = hashlib.sha256(f"{prefix}{nonce}".encode()).digest()
    assert challenge.clears(digest, bytes.fromhex(TRIVIAL)) is True


def test_the_request_carries_this_apps_own_user_agent_and_content_type(ready):
    """One definition of the User-Agent, asserted where it goes rather than
    read off the constant. LRCLIB asks every client to identify itself, and
    a POST that did not would be the one request that did not."""
    window, service = ready()
    publish_it(window, open_publisher(window))

    headers = sent(service)[0].headers
    assert headers["User-Agent"] == lp.USER_AGENT
    assert headers["Content-Type"] == "application/json"


def test_the_requests_go_out_one_at_a_time_with_a_gap_between_them(ready):
    """LRCLIB asks for sequential requests with a short delay, and this
    path makes three of them in a row."""
    window, service = ready()
    started = time.monotonic()
    publish_it(window, open_publisher(window))

    paths = [request.path for request in service.requests]
    assert paths.index("/api/request-challenge") < paths.index("/api/publish")
    assert time.monotonic() - started >= 2 * lp.REQUEST_GAP_SECONDS


# -- and it is remembered --------------------------------------------------


def test_the_menu_stops_offering_it_and_says_it_has_gone(ready):
    """The record of a publication, and the whole reason there is one: the
    same unchanged sync is never offered twice."""
    window, _ = ready()
    publish_it(window, open_publisher(window))

    assert window._menu.is_visible(m.PUBLISH) is False
    assert window._menu.is_visible(m.PUBLISH_STATUS) is True
    assert window._menu.label(m.PUBLISH_STATUS) == "Published to LRCLIB"


def test_a_re_sync_may_be_published_again(ready):
    """The record is of the TEXT rather than of the track, so different
    stamps are a different thing to send. Which is right: the timings are
    what a re-sync changes, and they are what publishing is for."""
    window, _ = ready()
    publish_it(window, open_publisher(window))
    assert window._menu.is_visible(m.PUBLISH) is False

    window._provider.save_user_sync(TRACK_ID, LRC.replace("00:01.00", "00:02.50"))
    window._reconsider_publishing()
    window._refresh_menu()

    assert window._menu.is_visible(m.PUBLISH) is True


def test_the_users_own_sync_is_untouched_by_all_of_it(ready):
    """``.user_syncs/`` is their work. Publishing copies it outward and is
    not allowed to change or lose a byte of it on the way."""
    window, _ = ready()
    path = window._provider.user_sync_path(TRACK_ID)
    before = path.read_bytes()

    publish_it(window, open_publisher(window))

    assert path.read_bytes() == before
    # The record of the publication sits beside it rather than inside it,
    # so clearing the lyrics cache cannot forget what was sent.
    assert path.exists()
    assert window._provider.published_path(TRACK_ID).exists()
    assert sorted(p.suffix for p in window._provider.user_sync_dir.iterdir()) == [
        ".lrc",
        ".published",
    ]


# -- the fresh check overrules the cache -----------------------------------


def test_a_song_lrclib_has_since_synced_is_refused_and_nothing_is_sent(ready):
    """The reason the condition is checked again at publish time. The cache
    says LRCLIB had words and no timings, and that was true the day this
    song played; somebody else may have contributed since, and publishing
    is permanent."""
    window, service = ready()
    later(service, ("api/get", (200, record(syncedLyrics="[00:01.00] first line\n"))))

    publisher = open_publisher(window)

    assert publisher.state == pw.IMPOSSIBLE
    assert publisher._status.text() == publish.ALREADY_SYNCED
    assert sent(service) == []
    # And there is nothing to press: a refusal is a fact about the song, so
    # trying again would only produce it a second time.
    assert publisher._primary.isVisibleTo(publisher) is False


def test_a_track_lrclib_has_no_record_of_is_out_of_scope_and_says_so(ready):
    """Publishing lyrics for a track LRCLIB has never heard of is a
    different thing and a later step. Refused here, in words."""
    window, service = ready()
    later(service, ("api/get", (404, b"")))

    publisher = open_publisher(window)

    assert publisher.state == pw.IMPOSSIBLE
    assert publisher._status.text() == publish.NO_RECORD
    assert sent(service) == []


# -- the failures, each of them recoverable and legible --------------------


def test_a_check_that_could_not_be_made_offers_to_ask_again(ready):
    """Not a refusal: "LRCLIB could not be asked" is not "this may not be
    published", and the difference is a button."""
    window, service = ready()
    later(service, ("api/get", (503, b"")))

    publisher = open_publisher(window)

    assert publisher.state == pw.BROKEN
    assert publisher._status.text() == (
        "LRCLIB could not be asked · LRCLIB answered HTTP 503"
    )
    assert publisher._primary.isVisibleTo(publisher) is True
    assert sent(service) == []

    # And the button really does ask again, against a service that is back.
    service.routes = service.routes[1:]
    publisher._primary.click()
    wait_until(lambda: publisher.state != pw.CHECKING, "the retry never answered")
    assert publisher.state == pw.REVIEW


def test_a_refused_token_is_reported_and_a_fresh_challenge_is_made(ready):
    """400 is what LRCLIB answers for a token that is wrong, expired or
    already spent. Pressing again starts from a new challenge, because a
    prefix the server has already seen is a prefix it will refuse."""
    window, service = ready()
    later(service, ("api/publish", (400, json.dumps({
        "code": 400,
        "name": "IncorrectPublishTokenError",
        "message": "The provided publish token is incorrect",
    }).encode())))
    publisher = publish_it(window, open_publisher(window))

    assert publisher.state == pw.BROKEN
    assert publisher._status.text().startswith("the publish token was refused")
    assert len(service.sent_to("request-challenge")) == 1

    service.routes = service.routes[1:]
    publish_it(window, publisher)

    assert publisher.state == pw.DONE
    assert len(service.sent_to("request-challenge")) == 2


def test_a_429_is_obeyed_and_stops_everything_that_would_go_next(ready):
    """LRCLIB's documentation says ignoring Retry-After may earn a
    temporary ban, so the pause is not the publish path's to walk through
    either: it is set where the answer arrives, and the next request this
    app would make is refused before a socket is opened."""
    window, service = ready()
    later(service, (
        "api/publish",
        (429, json.dumps({"code": 429, "name": "TooManyRequests"}).encode(),
         [("Retry-After", "600")]),
    ))
    publisher = publish_it(window, open_publisher(window))

    assert publisher.state == pw.BROKEN
    assert "LRCLIB asked this app to slow down" in publisher._status.text()
    assert lp._hold.remaining(time.monotonic()) > 500

    # The pause outranks the retry, and the refusal names itself rather
    # than pretending a request was made.
    before = len(service.requests)
    publish_it(window, publisher)
    assert len(service.requests) == before, "a request went out during the pause"
    assert "waiting, as LRCLIB asked" in publisher._status.text()


def test_the_network_going_away_mid_publish_is_a_failure_and_not_a_send(ready):
    window, service = ready()
    later(service, ("api/publish", (500, b"upstream is unhappy")))
    publisher = publish_it(window, open_publisher(window))

    assert publisher.state == pw.BROKEN
    assert publisher._status.text() == (
        "the sync could not be sent · LRCLIB answered HTTP 500"
    )
    assert window._menu.is_visible(m.PUBLISH_STATUS) is False
    assert window._provider.published_path(TRACK_ID).exists() is False


def test_closing_the_window_stops_a_solve_in_flight_and_sends_nothing(ready):
    """The cancel, driven against a challenge nothing will ever clear. The
    solver reads the flag every 50,000 hashes, so closing the window ends
    the work rather than waiting it out — and what it was working towards
    had not left this Mac."""
    window, service = ready(target=IMPOSSIBLE)
    publisher = open_publisher(window)
    publisher._primary.click()
    wait_until(
        lambda: len(service.sent_to("request-challenge")) == 1,
        "the challenge was never requested",
    )

    publisher.close()

    assert window._pool.waitForDone(5000), "the solve outlived the window"
    APP.processEvents()
    assert sent(service) == []
    assert window._provider.published_path(TRACK_ID).exists() is False


def test_the_window_is_closed_by_shutdown_so_a_solve_cannot_outlive_it(ready):
    """A publish window is a top-level of its own with a worker attached,
    so it is drained beside the paste window rather than left to be
    destroyed with a solve still running."""
    window, service = ready(target=IMPOSSIBLE)
    publisher = open_publisher(window)
    publisher._primary.click()
    wait_until(
        lambda: len(service.sent_to("request-challenge")) == 1,
        "the challenge was never requested",
    )

    window._shutdown()

    assert window._publish_window is None
    assert window._pool.waitForDone(5000), "the solve outlived the shutdown"
    assert sent(service) == []
