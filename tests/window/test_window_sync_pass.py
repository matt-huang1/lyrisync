"""A tap-to-sync pass, from the outside, for its whole life.

Every sync test before this one handed the session its stamps. That is the
caller answering its own question — the same shape as the loop tests that
supplied their own positions and passed for 1487 runs against a loop that
seeked twice at every wrap — and it is why neither of the two reports this
file exists for could have been caught: a pass whose stamps arrive by
``session.stamp(4.0)`` never meets the tap bar, never meets the player, and
never meets the things that end it.

So nothing here supplies a stamp. A real ``PlayerMonitor`` over a fake
Spotify tells the window where the song is, and every stamp comes from a
press delivered to ``windowHandle()`` at the tap bar's own live centre,
which is the one path that runs Qt's own hit testing. What ends a pass is
the player changing track, the player stopping, the user pressing ✕, and
the layout being swapped underneath it — each driven, never called.

What all four used to have in common was that they threw every stamp away
without a word. That is the behaviour under test.
"""

TIER = "integration"  # the monitor, the window, the presses and the disk

import json

import pytest

from sottovoce import menu as m
from sottovoce import window as w

from helpers import (
    APP,
    SONG,
    go_compact,
    play,
    pressing,
    shown,
    worked,
)

TRACK = "0Ab1Cd2Ef3"

# Three lines nobody wrote a tune for, so the fixture is words rather than
# somebody's lyrics. Three because a pass has to be interruptible in the
# middle of one, which needs a middle.
LINES = ["the first line", "the second line", "the third line"]
PLAIN_BODY = json.dumps(
    {"syncedLyrics": None, "plainLyrics": "\n".join(LINES)}
).encode()


@pytest.fixture
def seeks(monkeypatch):
    """Where this app told the player to go, in order.

    A subclass of the task rather than a patch on ``window._pool``: the
    pool is ``QThreadPool.globalInstance()`` and assigning to its
    ``start()`` leaks into every test that runs after this one.
    """
    sent: list = []

    class RecordingCommand(w.PlayerCommandTask):
        def __init__(self, seek_to=None, pause=False, resume=False):
            super().__init__(seek_to, pause, resume)
            sent.append(seek_to)

    monkeypatch.setattr(w, "PlayerCommandTask", RecordingCommand)
    return sent


@pytest.fixture
def playing(make_window, lrclib, fetching, spotify):
    """A song on screen with plain lyrics, and the monitor that found it.

    The lookup is real all the way down to the connection, so the cache,
    the priority order and the plain-lyrics decode are all this app's own
    code — the only fake is what a socket would have answered.
    """
    lrclib(("api/get", (200, PLAIN_BODY)))

    def start(compact=False):
        window = shown(make_window())
        monitor = play(window, spotify)
        if compact:
            go_compact(window)
            APP.processEvents()
        return window, monitor

    return start


def tap(window, monitor, spotify, position):
    """The song reaches a position, the monitor says so, and the user taps.

    The position arrives the way every position does — through a poll —
    so the interpolation, the reaction offset and the clamp are all the
    real ones. The press is routed by Qt at the bar's live centre.
    """
    spotify.position = position
    monitor.tick()
    APP.processEvents()
    return pressing(window, window._tap_button)


def journal(window):
    return window._provider.read_pass(TRACK)


def stamped(window):
    """How many lines the pass has timed, live session or written down."""
    return window._pass_in_hand()


# A stamp is the position interpolated forward from the poll that carried
# it, so it is the tapped position minus the reaction offset PLUS however
# long the test took to get from the tick to the press. Real, tiny, and
# never zero — the assertions below name the second rather than the
# millisecond, which is what a person tapping is accurate to anyway.
def about(*seconds):
    """The stamps a pass should be carrying, to within a hundredth."""
    return [pytest.approx(value, abs=0.01) for value in seconds]


# -- the pass that finishes -------------------------------------------------


def test_a_pass_driven_entirely_by_presses_saves_the_song(playing, spotify):
    """The baseline, and it is a baseline nothing here had: every stamp
    from a press, every position from the player."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()

    for index, position in enumerate((2.0, 5.0, 8.0)):
        record = tap(window, monitor, spotify, position)
        assert record.acted == 1, f"the tap bar took no press at line {index}"
        assert record.dragged == 0, "the press reached the window's drag handler"

    worked(window)
    APP.processEvents()
    assert window._provider.user_sync_text(TRACK) == (
        "[00:01.75] the first line\n"
        "[00:04.75] the second line\n"
        "[00:07.75] the third line\n"
    )
    # Complete, so it is not marked partial and the journal has gone.
    assert window._provider.sync_is_partial(
        TRACK, window._provider.user_sync_text(TRACK)
    ) is False
    assert journal(window) is None


def test_every_tap_is_written_down_as_it_lands(playing, spotify):
    """The promise the rest of this file rests on. Not "at the end", not
    "when something goes wrong": after each one."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    assert journal(window) is None, "a pass nobody has tapped in has nothing to keep"

    tap(window, monitor, spotify, 2.0)
    assert journal(window)["stamps"] == about(1.75)
    assert journal(window)["lines"] == LINES

    tap(window, monitor, spotify, 5.0)
    assert journal(window)["stamps"] == about(1.75, 4.75)

    # And down as well as up, or an undo would be given back by a resume.
    window._on_sync_undo()
    assert journal(window)["stamps"] == about(1.75)


# -- the three things that end a pass ---------------------------------------


def test_a_track_change_mid_pass_keeps_every_stamp_and_says_so(playing, spotify):
    """The report this file was opened for.

    A song ENDING is a track change, so tapping the last verse and having
    the next song start was the commonest way to reach this line — and it
    used to take the whole pass with it, silently.
    """
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)
    tap(window, monitor, spotify, 5.0)
    assert stamped(window) == (2, 3)

    spotify.song = dict(SONG, uri="spotify:track:NEXTSONG", title="Whatever Is Next")
    spotify.position = 0.0
    monitor.tick()
    APP.processEvents()

    assert window._syncing is False, "the pass stayed on screen for another song"
    # The stamps are on disk, under the song they belong to, whatever is
    # playing now.
    assert window._provider.read_pass(TRACK)["stamps"] == about(1.75, 4.75)
    # And nothing was promoted behind anybody's back: two of three lines
    # is not a sync until somebody says it is.
    assert window._provider.user_sync_text(TRACK) is None


def test_the_song_coming_back_offers_the_pass_back(playing, spotify, seeks):
    """What makes the line above a pause rather than a loss."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)

    spotify.song = dict(SONG, uri="spotify:track:NEXTSONG", title="Whatever Is Next")
    monitor.tick()
    APP.processEvents()
    spotify.song = SONG
    monitor.tick()
    APP.processEvents()
    assert window._view_model.track_id == TRACK

    window._refresh_menu()
    assert window._menu.label(m.SYNC) == "Resume the sync (1 / 3 lines)"
    # The window says it too, where the count was being kept, because the
    # menu is not somewhere anybody is looking until they open it.
    assert "1 of 3 lines timed" in window._progress.text()
    assert window._progress.isVisibleTo(window) is True

    window._begin_sync()
    APP.processEvents()
    session = window._view_model.sync_session
    assert session.stamps == about(1.75)
    assert session.current == "the second line"
    # And it goes back to where the tapping stopped rather than to 0: the
    # lines before it are timed, and sitting through them is why nobody
    # would resume.
    assert seeks[-1] == about(1.75)[0]


def test_spotify_stopping_mid_pass_keeps_every_stamp(playing, spotify):
    """The monitor debounces a one-poll track loss and the view model
    suspends rather than resets, both because a stop can be a blip. A pass
    was the one thing here that took it as final."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)

    spotify.state = "stopped"
    monitor.tick()
    APP.processEvents()

    assert window._syncing is False
    assert window._provider.read_pass(TRACK)["stamps"] == about(1.75)


def test_pressing_the_exit_stops_the_pass_and_keeps_it(playing, spotify):
    """✕ used to be the discard. It is the stop, and it says which.

    Two presses still, because leaving is still a thing to be sure about
    — what changed is that the sentence in between promises the taps are
    kept rather than warning they are about to go.
    """
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)

    first = pressing(window, window._sync_exit_button)
    assert first.acted == 1
    assert window._syncing is True, "one press must not end a pass"
    assert "your taps are kept" in window._progress.text()

    second = pressing(window, window._sync_exit_button)
    assert second.acted == 1
    assert window._syncing is False
    assert window._provider.read_pass(TRACK)["stamps"] == about(1.75)


def test_nothing_but_a_deliberate_discard_ever_loses_a_stamp(playing, spotify):
    """The claim the three tests above add up to, asserted as one thing:
    of everything that can end a pass, exactly one takes the work."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)

    window._discard_pass()
    APP.processEvents()

    assert window._syncing is False
    assert window._provider.read_pass(TRACK) is None
    assert window._provider.user_sync_text(TRACK) is None
    # Nothing else in .user_syncs/ was touched on the way past.
    assert list(window._provider.user_sync_dir.glob("*.lrc")) == []


# -- the layout swapped underneath it ---------------------------------------


def test_a_layout_swap_mid_pass_changes_nothing_about_the_pass(playing, spotify):
    """The compact setting toggled while a pass is running.

    A pass borrows the full layout, so this is the one moment the setting
    and the applied state disagree on purpose — and the pass has to be
    indifferent to it. Both directions, because the borrow and the return
    are different code paths.
    """
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)

    window._set_compact(True)  # the user asks for the strip mid-pass
    APP.processEvents()
    assert window._compact is True
    assert window._compact_applied is False, "a pass keeps the full layout"
    assert window._syncing is True
    assert stamped(window) == (1, 3)

    # And the bar is still where a press can reach it, which is the half a
    # geometry assertion cannot answer.
    record = tap(window, monitor, spotify, 5.0)
    assert record.acted == 1
    assert record.dragged == 0
    assert stamped(window) == (2, 3)

    window._set_compact(False)
    APP.processEvents()
    assert window._syncing is True
    record = tap(window, monitor, spotify, 8.0)
    assert record.acted == 1
    worked(window)
    APP.processEvents()
    assert window._provider.user_sync_text(TRACK) is not None
    # The setting the user asked for is what the layout goes back to, and
    # the pass gave it back rather than deciding it.
    assert window._compact_applied is False


def test_a_pass_started_in_the_strip_takes_its_presses_and_gives_it_back(
    playing, spotify
):
    """The other entry path, driven the same way. The two reached the same
    geometry when this was measured; what this pins is that they reach the
    same PRESS, and that the strip comes back afterwards."""
    window, monitor = playing(compact=True)
    assert window._compact_applied is True
    window._begin_sync()
    APP.processEvents()
    assert window._compact_applied is False, "a pass borrows the full layout"

    for position in (2.0, 5.0, 8.0):
        record = tap(window, monitor, spotify, position)
        assert record.acted == 1
        assert record.dragged == 0

    worked(window)
    APP.processEvents()
    assert window._provider.user_sync_text(TRACK) is not None
    assert window._compact_applied is True, "the strip was not given back"


# -- a tap that does nothing says why ---------------------------------------


def test_a_tap_while_paused_is_refused_by_name(playing, spotify):
    """A control that takes a press and does nothing is indistinguishable
    from one that is not wired up, which is exactly the report this
    arrived as. Measured: a disabled QPushButton swallows the press."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()

    spotify.state = "paused"
    monitor.tick()
    APP.processEvents()

    assert window._tap_button.isEnabled() is False
    assert window._tap_button.text() == "PAUSED"
    record = pressing(window, window._tap_button)
    assert record.acted == 0, "a disabled bar cannot act"
    # And the row that was counting lines says which of the two it is.
    window._on_tap()
    assert "playback is paused" in window._progress.text()
    assert stamped(window) is None


def test_a_tap_with_no_position_yet_is_refused_by_name(playing, spotify):
    """The sharper one: the bar is enabled, the press lands, the handler
    runs, and the stamp used to be dropped in silence."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    window._last_position = None

    record = pressing(window, window._tap_button)

    assert record.acted == 1, "the press reached the control"
    assert stamped(window) is None, "and nothing was stamped"
    assert "waiting for Spotify" in window._progress.text()


def test_a_journal_that_cannot_be_written_is_said_out_loud(playing, spotify, monkeypatch):
    """The one case the whole mechanism cannot cover. A promise that
    fails quietly is worse than no promise."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()

    def refuse(track_id, record):
        raise OSError("read-only")

    monkeypatch.setattr(window._provider, "save_pass", refuse)
    tap(window, monitor, spotify, 2.0)

    assert window._pass_unsaved is True
    assert "not being saved" in window._progress.text()
    # The stamp itself still happened — the pass carries on, it is only
    # the keeping of it that failed.
    assert window._view_model.sync_session.stamps == about(1.75)


# -- what a kept pass is, and is not ----------------------------------------


def test_a_kept_pass_is_a_real_sync_that_may_not_be_published(playing, spotify):
    """Completion still means every line for the purpose it was ever for.
    What changed is that falling short is no longer the same as losing."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)
    tap(window, monitor, spotify, 5.0)

    window._keep_pass()
    worked(window)
    APP.processEvents()

    lrc = window._provider.user_sync_text(TRACK)
    assert lrc == "[00:01.75] the first line\n[00:04.75] the second line\n"
    assert window._provider.sync_is_partial(TRACK, lrc) is True
    assert window._provider.read_pass(TRACK) is None
    # Locally it is a sync like any other. Outwards it is refused, by
    # name, before anything is asked of LRCLIB.
    assert window._publish_refusal == w.publish.PARTIAL_SYNC


def test_a_resync_that_finishes_the_job_clears_the_partial_mark(playing, spotify):
    """A marker that outlived the file it was about would refuse a
    complete sync on the strength of the pass it replaced."""
    window, monitor = playing()
    window._begin_sync()
    APP.processEvents()
    tap(window, monitor, spotify, 2.0)
    window._keep_pass()
    worked(window)
    APP.processEvents()
    assert window._provider.sync_is_partial(
        TRACK, window._provider.user_sync_text(TRACK)
    ) is True

    window._provider.save_user_sync(TRACK, "[00:01.00] the first line\n")

    assert window._provider.sync_is_partial(
        TRACK, window._provider.user_sync_text(TRACK)
    ) is False


# -- a journal that no longer describes this song ---------------------------


def test_a_pass_made_from_different_words_is_not_resumed(playing, spotify):
    """Stamps are timings against WORDS. A record made from other words is
    not a pass to take back up, it is somebody else's timings put against
    lines they never tapped."""
    window, monitor = playing()
    window._provider.save_pass(
        TRACK,
        {"version": 1, "lines": ["words from somewhere else"], "stamps": [3.0]},
    )
    window._refresh_menu()

    assert window._resumable_pass() is None
    assert window._menu.label(m.SYNC) == "Sync this song"

    window._begin_sync()
    APP.processEvents()
    assert window._view_model.sync_session.lines == LINES
    assert window._view_model.sync_session.stamps == []
