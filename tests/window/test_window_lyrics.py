"""What the window says while it waits, and when it has nothing.

The title card is a floor on how long the gap LOOKS, not on how long it
lasts; a failed lookup says one plain line and keeps the reason one click
away. A track that simply has no lyrics offers nothing to click, and that
distinction is the point.
"""

TIER = "qt"  # a real window, driven by calling its own methods

from sottovoce.failure import FetchFailure
from sottovoce.view_model import Mode

from helpers import APP, PLAIN, SYNCED, load, snapshot


def test_the_title_card_gives_the_window_back_as_soon_as_lyrics_land(make_window):
    """The card is a floor on how long the gap LOOKS, not on how long it
    lasts. It used to run its full two seconds whatever happened
    underneath, so lyrics that arrived in 900ms sat behind the song's name
    for another 1.1 seconds — a delay the app was adding to every track.
    """
    window = make_window()
    window._on_track_change(snapshot())
    APP.processEvents()
    # The card is up: the song announces itself while the fetch is out.
    assert window._card_active() is True
    assert window._card_on_screen() is True
    assert window._current.text() == window._view_model.display().header

    window._on_fetch_finished("t1", SYNCED, True)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()

    # Still inside the two seconds, and already showing the song.
    assert window._card_active() is True
    assert window._card_on_screen() is False
    assert window._current.text() == "one"


def test_the_card_still_covers_a_song_joined_before_its_first_line(make_window):
    """Ending it here would trade two seconds of the song's name for ten
    seconds of an empty window."""
    window = make_window()
    window._on_track_change(snapshot())
    window._on_fetch_finished("t1", SYNCED, True)
    window._on_position_update(snapshot(position=0.2))  # first line is at 1.0
    APP.processEvents()

    assert window._card_on_screen() is True
    assert window._current.text() == window._view_model.display().header


# -- why the lyrics are not here -------------------------------------------
#
# The window's one line about a failed fetch was "lyrics unavailable, will
# retry", which is true of a 503, of the wifi being off, and of a request
# that timed out on the third attempt. The reason is now one click away and
# no closer: it is offered beside that message and nowhere else, so a song
# that simply has no lyrics still gets one plain line and nothing to dig at.


def fail(window, why, track_id="t1"):
    """A track whose lookup came back with a reason."""
    window._on_track_change(snapshot(track_id=track_id))
    window._on_fetch_finished(track_id, None, False, why)
    window._title_card_until = 0.0
    window._render()
    APP.processEvents()


HTTP_503 = FetchFailure(kind="http", status=503, attempt="album match")


def test_the_message_itself_is_unchanged(make_window):
    """The default is for the people who do not care why, and there are
    more of them. Nothing about the affordance may change what the window
    says on its own."""
    window = make_window()
    fail(window, HTTP_503)
    assert window._current.text() == "lyrics unavailable, will retry"
    assert window._upcoming.text() == ""


def test_the_affordance_is_offered_only_for_a_service_failure(make_window):
    """The distinction that has to stay obvious: a track with no lyrics is
    not a track the service failed on."""
    window = make_window()
    fail(window, HTTP_503)
    # isVisibleTo, not isVisible: this window was never shown.
    assert window._why_button.isVisibleTo(window) is True

    window._on_fetch_finished("t1", None, True)  # a genuine "no lyrics"
    window._render()
    assert window._view_model.display().mode is Mode.NO_LYRICS
    assert window._current.text() == "no lyrics found"
    assert window._why_button.isVisibleTo(window) is False


def test_the_affordance_is_offered_nowhere_else(make_window):
    """Every other mode: synced, plain, fetching, idle."""
    window = make_window()
    for lyrics in (SYNCED, PLAIN):
        load(window, lyrics)
        assert window._why_button.isVisibleTo(window) is False
    window._on_track_change(snapshot(track_id="t9"))
    window._title_card_until = 0.0
    window._render()
    assert window._view_model.display().mode is Mode.FETCHING
    assert window._why_button.isVisibleTo(window) is False


def test_clicking_reveals_the_specific_reason(make_window):
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    APP.processEvents()
    assert window._upcoming.text() == "LRCLIB answered HTTP 503 · album match"
    assert window._why_button.isChecked()


def test_clicking_again_puts_it_away(make_window):
    """A thing to glance at, not a state to get stuck in."""
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    window._why_button.click()
    APP.processEvents()
    assert window._upcoming.text() == ""
    assert not window._why_button.isChecked()


def test_each_kind_of_failure_says_which_it_was(make_window):
    """The four the provider can tell apart, end to end."""
    window = make_window()
    for why, expected in (
        (FetchFailure(kind="http", status=503, attempt="search"),
         "LRCLIB answered HTTP 503 · search"),
        # 429 is the one status with a line of its own, because it is the
        # one this app does something about rather than only reports.
        (FetchFailure(kind="http", status=429, attempt="search"),
         "LRCLIB asked this app to slow down · search"),
        (FetchFailure(kind="held", retry_after=12.0),
         "waiting, as LRCLIB asked"),
        (FetchFailure(kind="timeout", attempt="title and artist"),
         "LRCLIB did not answer in time · title and artist"),
        (FetchFailure(kind="connection", attempt="album match"),
         "could not reach lrclib.net · album match"),
        (FetchFailure(kind="payload", attempt="search"),
         "LRCLIB's answer could not be read · search"),
    ):
        fail(window, why)
        window._why_shown = True
        window._render()
        assert window._upcoming.text() == expected


def test_the_reveal_survives_a_retry(make_window):
    """The retry runs every 30s and takes the mode ERROR -> FETCHING ->
    ERROR. Hiding the reason under somebody who had just asked for it
    would make the control feel broken."""
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    assert window._why_shown

    window._view_model._error_at = -1000.0  # due now
    window._tick_retry()
    APP.processEvents()
    assert window._view_model.display().mode is Mode.FETCHING
    assert window._why_shown  # remembered, though nothing is on screen

    window._on_fetch_finished("t1", None, False, HTTP_503)
    APP.processEvents()
    assert window._upcoming.text() == "LRCLIB answered HTTP 503 · album match"


def test_a_new_song_asks_its_own_question(make_window):
    """The reveal belongs to the failure that prompted it."""
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    assert window._why_shown

    fail(window, FetchFailure(kind="timeout", attempt="search"), track_id="t2")
    assert not window._why_shown
    assert window._upcoming.text() == ""


def test_a_failure_with_nothing_to_say_offers_nothing(make_window):
    """A fetch that failed before any reason existed. The message is
    unchanged and there is simply nothing to click."""
    window = make_window()
    fail(window, None)
    assert window._current.text() == "lyrics unavailable, will retry"
    assert window._why_button.isVisibleTo(window) is False


def test_the_control_sits_beside_the_message(make_window):
    """Placed from the text rather than pinned to a corner: the message is
    centred and the window is resizable, so a fixed position would be
    beside it at one width and stranded at every other."""
    window = make_window()
    window.resize(460, 220)
    fail(window, HTTP_503)
    narrow = window._why_button.pos().x()

    window.resize(700, 220)
    APP.processEvents()
    window._render()
    wide = window._why_button.pos().x()
    assert wide > narrow, "the control did not follow the message"
    # And never off the edge: the gutter is where a wrapped message puts it.
    assert window._why_button.pos().x() + window._why_button.width() <= window.width()


def test_the_control_never_leaves_the_window_at_its_narrowest(make_window):
    """The wrapping case, where the message's laid-out width IS the row."""
    window = make_window()
    window.resize(260, 200)
    fail(window, HTTP_503)
    APP.processEvents()
    right = window._why_button.pos().x() + window._why_button.width()
    assert 0 < window._why_button.pos().x()
    assert right <= window.width()


# -- and the one thing to DO about the reason ------------------------------


def test_the_retry_belongs_to_the_reason_rather_than_to_the_message(make_window):
    """The message already says "will retry" and means it. A button beside
    that sentence would be inviting people to do the thing the app has just
    promised to do for them, so this one only exists for somebody who has
    opened the explanation."""
    window = make_window()
    fail(window, HTTP_503)
    assert window._why_button.isVisibleTo(window) is True
    assert window._retry_button.isVisibleTo(window) is False

    window._why_button.click()
    APP.processEvents()
    assert window._retry_button.isVisibleTo(window) is True

    window._why_button.click()  # put the reason away
    APP.processEvents()
    assert window._retry_button.isVisibleTo(window) is False


def test_the_retry_goes_with_the_song(make_window):
    """A new song is a different failure or none at all, and the reveal is
    cleared with it. Its control may not outlive it."""
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    APP.processEvents()
    assert window._retry_button.isVisibleTo(window) is True

    load(window, SYNCED, track_id="t2")

    assert window._retry_button.isVisibleTo(window) is False


def on_the_row_of(window, button, row):
    """Whether the control is vertically centred on that row, and to the
    right of where its text ends. Asserted as a relationship rather than as
    a position: what the row's coordinates are is a font measurement, and
    those differ by platform."""
    centre = button.mapTo(window, button.rect().center())
    top_left = row.mapTo(window, row.rect().topLeft())
    return top_left.y() <= centre.y() <= top_left.y() + row.height()


def test_the_retry_sits_beside_the_reason_in_both_layouts(make_window):
    """The reason lands in the upcoming row in the full layout and in the
    pronunciation row in the strip, because the strip has no upcoming row.
    The control follows the reason to whichever one it is, which is the
    same fork _render_why takes and the whole of what compact changes here.
    """
    window = make_window()
    fail(window, HTTP_503)
    window._why_button.click()
    APP.processEvents()

    reason = "LRCLIB answered HTTP 503 · album match"
    assert window._upcoming.text() == reason
    assert on_the_row_of(window, window._retry_button, window._upcoming)

    window._set_compact(True)
    APP.processEvents()
    window._render()

    assert window._compact_applied is True
    assert window._pron.text() == reason
    assert on_the_row_of(window, window._retry_button, window._pron)
    assert window._retry_button.isVisibleTo(window) is True


def test_pressing_the_retry_asks_again_at_once(make_window):
    """The window's half of it: a fetch is dispatched and the mode leaves
    ERROR without waiting out the schedule. That a request really goes out
    on the wire is asserted through the real provider in
    test_window_fetch.py."""
    window = make_window()
    fail(window, HTTP_503)
    for _ in range(3):
        window._view_model.fetch_completed(
            "t1", None, ok=False, now=window._view_model._error_at
        )
    assert window._view_model.retry_interval() > 30.0

    window._why_button.click()
    window._retry_button.click()
    APP.processEvents()

    assert window._view_model.display().mode is Mode.FETCHING
    assert window._view_model.failures == 0
