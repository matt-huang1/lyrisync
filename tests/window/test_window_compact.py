"""The strip: which rows are on it, and the controls that ride the
reveal.
"""

TIER = "qt"  # a real window, driven by calling its own methods

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor

from sottovoce import menu as m
from sottovoce import window as w
from sottovoce.failure import FetchFailure
from sottovoce.player_monitor import PlaybackState

from helpers import (
    APP,
    KOREAN_SYNCED,
    PLAIN,
    SYNCED,
    finish_reveal,
    go_compact,
    hover,
    land,
    load,
    snapshot,
)


# -- the compact layout ---------------------------------------------------
#
# What is checked here is what needs a real object tree: which rows are on
# screen, what the window's shape does when the layout changes, and the
# reveal the controls ride on. The arithmetic underneath — the height floor
# and the dock position, notch included — is pure and lives in
# tests/test_geometry.py.


def compact_rows(window):
    """Which of the five label rows are on screen, as a tuple of bools.

    isVisibleTo, not isVisible: these windows are never shown, so every
    child would answer False and the tuple would say nothing.
    """
    return tuple(
        row.isVisibleTo(window)
        for row in (
            window._header,
            window._previous,
            window._current,
            window._pron,
            window._upcoming,
        )
    )


def test_compact_is_off_until_it_is_asked_for(make_window):
    """Default-off like every layer here: a fresh window is the app as it
    has always been."""
    window = make_window()
    assert window._compact is False
    assert window._compact_applied is False
    assert window._menu.is_checked(m.COMPACT) is False


def test_compact_shows_the_sung_line_and_nothing_else(make_window):
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()
    assert compact_rows(window)[0] is True   # the header is there to lose
    assert window._previous.isVisibleTo(window) is True

    go_compact(window)
    header, previous, current, _, upcoming = compact_rows(window)
    assert (header, previous, upcoming) == (False, False, False)
    assert current is True
    assert window._current.text() == "one"


def test_compact_keeps_the_romanisation_line_under_the_sung_one(make_window):
    """The one row that survives beside the line: it belongs to it."""
    window = make_window()
    window._view_model.romanisation_enabled = True
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    assert window._pron.isVisibleTo(window) is True
    assert window._pron.text()


def test_compact_off_restores_the_layout_exactly(make_window):
    """Off must equal the plain synced-lyrics window, which is more than
    the rows coming back: the gutters, the air around the sung line, the
    height floor and the controls' placement all have to be what they
    were."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()
    # A hidden widget defers its resize event until it is shown, so these
    # windows never run one and the controls are still placed for the size
    # the constructor gave them. Placed once here so both sides of the
    # comparison are answering about the same window.
    window._place_buttons()
    before = (
        compact_rows(window),
        window._layout.contentsMargins().left(),
        window._current_layout.contentsMargins().top(),
        window.minimumHeight(),
        window.height(),
        window._loop_button.pos(),
        window._speak_button.pos(),
    )

    go_compact(window)
    window._set_compact(False)
    APP.processEvents()

    assert (
        compact_rows(window),
        window._layout.contentsMargins().left(),
        window._current_layout.contentsMargins().top(),
        window.minimumHeight(),
        window.height(),
        window._loop_button.pos(),
        window._speak_button.pos(),
    ) == before


def test_compact_takes_the_window_down_to_a_strip(make_window):
    window = make_window()
    window.resize(460, 220)
    APP.processEvents()
    go_compact(window)
    assert window.minimumHeight() == w.min_window_height(
        window._scale, compact=True
    )
    assert window.height() == window.minimumHeight()
    assert window.height() < 220
    # A WIDE thin strip: the width is untouched. Safe as a literal because
    # no song has been loaded, so there is nothing for the fit to measure
    # and no font can reach this number.
    assert window.width() == 460


def test_the_full_layout_keeps_the_height_it_was_left_at(make_window):
    """Going compact and coming back gives the window its old shape, not a
    shape derived from the strip."""
    window = make_window()
    window.resize(460, 260)
    APP.processEvents()

    go_compact(window)
    APP.processEvents()

    window._set_compact(False)
    APP.processEvents()
    assert window.height() == 260


def test_the_strips_height_is_its_type_sizes_and_is_not_remembered(make_window):
    """The full layout's height is a free number and is kept. The strip's
    is not: it is one row of type, so the type size answers it, and a
    remembered height would only be a way to disagree with the setting."""
    window = make_window()
    go_compact(window)
    APP.processEvents()
    assert window.height() == w.min_window_height(window._scale, compact=True)

    # Nothing can leave it at another height, and coming back does not
    # restore one.
    window.resize(window.width(), window.height() + 40)
    APP.processEvents()
    window._set_compact(False)
    APP.processEvents()
    go_compact(window)
    APP.processEvents()
    assert window.height() == w.min_window_height(window._scale, compact=True)


def test_compact_and_the_full_height_survive_a_restart(make_window):
    window = make_window()
    window.resize(460, 240)
    APP.processEvents()
    go_compact(window)
    strip = window.height()
    window._save_settings()
    window._settings.sync()

    # The strip is what was written down, and the full layout's height with
    # it. Read off the settings rather than off the reopened window,
    # because make_window resizes every window it builds and would land on
    # top of the restored size.
    assert window._settings.value("window/size").height() == strip
    assert window._settings.value("window/full_height", type=int) == 240
    # The strip's height is derived now, so the key that used to hold it is
    # removed rather than left behind for somebody to believe.
    assert window._settings.value("window/compact_height") is None

    reopened = make_window()
    APP.processEvents()
    assert reopened._compact is True
    assert reopened._compact_applied is True
    # The compact layout is in force from the first frame, not applied
    # after a first render: the floor is the strip's, and the rows either
    # side of the sung line are already gone.
    assert reopened.minimumHeight() == w.min_window_height(
        reopened._scale, compact=True
    )
    assert compact_rows(reopened)[1] is False

    reopened._set_compact(False)
    APP.processEvents()
    assert reopened.height() == 240


def test_a_sync_pass_borrows_the_full_layout_and_gives_it_back(make_window):
    """A pass needs the line before, the line after, a status row and a tap
    bar across the bottom. A strip has room for one of those, and the bar
    alone would BE the window, so the layout steps aside for the pass
    rather than pretending."""
    window = make_window()
    load(window, PLAIN, track_id="t7")
    go_compact(window)
    strip = window.height()

    window._begin_sync()
    APP.processEvents()
    assert window._compact_applied is False
    assert window._previous.isVisibleTo(window) is True
    assert window._progress.isVisibleTo(window) is True
    assert window.height() > strip
    # The setting is untouched: the tick describes what was asked for.
    assert window._compact is True
    assert window._menu.is_checked(m.COMPACT) is True

    window._cancel_sync()
    window._render()
    APP.processEvents()
    assert window._compact_applied is True
    assert window.height() == strip
    assert window._previous.isVisibleTo(window) is False


def test_compact_gives_up_filling_the_rows_it_does_not_have(make_window):
    """Off removes the work, not just the output. The rows either side of
    the sung line are not written to at all."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._previous.setText("a row nobody filled")
    window._on_position_update(snapshot(position=6.0))
    APP.processEvents()
    assert window._previous.text() == "a row nobody filled"


# -- the overlay controls, and coming out from under the pointer ----------


def test_compact_puts_the_controls_away_until_the_pointer_arrives(make_window):
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()
    assert window._loop_button.isVisibleTo(window) is True

    go_compact(window)
    assert window._loop_button.isVisibleTo(window) is False
    assert window._speak_button.isVisibleTo(window) is False

    hover(window, True)
    assert window._loop_button.isVisibleTo(window) is True
    assert window._reveal == 1.0

    hover(window, False)
    assert window._loop_button.isVisibleTo(window) is False
    assert window._reveal == 0.0


def test_a_faded_control_is_off_the_window_rather_than_invisible(make_window):
    """A widget at zero opacity is still a widget under the pointer, and an
    invisible thing that can be clicked is worse than either state it is
    between."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    assert window._reveal == 0.0
    for button in window._revealable():
        assert button.isVisibleTo(window) is False


def test_the_pointer_over_a_control_is_still_over_the_window(make_window):
    """The one thing underMouse() gets wrong: it is false for the window
    the moment the pointer is over one of its own children, which is
    exactly when the controls have to stay. Asking the frame answers for
    everything inside it.

    The real pointer, such as it is: the offscreen platform parks its
    cursor and will not move it, so the window is moved under the cursor
    instead of the other way round.
    """
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._place_buttons()

    pointer = QCursor.pos()
    button = window._loop_button
    window.move(
        pointer
        - button.pos()
        - QPoint(button.width() // 2, button.height() // 2)
    )
    APP.processEvents()
    assert button.geometry().translated(window.pos()).contains(pointer)

    assert window._pointer_inside() is True
    window._check_pointer()
    finish_reveal(window)
    assert window._hovered is True
    assert button.isVisibleTo(window) is True


def test_the_pointer_is_only_watched_where_it_can_matter(make_window):
    """No events to subscribe to, so this is a poll, and a poll that is not
    needed does not run. Off removes the work."""
    window = make_window()
    window.apply_saved_visibility()
    land(window)
    assert window._pointer_timer.isActive() is False  # compact is off

    go_compact(window)
    assert window._pointer_timer.isActive() is True

    window._set_lyrics_visible(False)  # away at the menu bar: no pointer on it
    land(window)
    assert window._pointer_timer.isActive() is False
    window._set_lyrics_visible(True)
    land(window)
    assert window._pointer_timer.isActive() is True

    window._set_compact(False)
    assert window._pointer_timer.isActive() is False


def test_a_sync_pass_stops_the_pointer_watch_with_the_layout(make_window):
    """The full layout has its controls out anyway. Nothing is watching for
    a pointer that could not change anything."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, PLAIN, track_id="t7")
    go_compact(window)
    assert window._pointer_timer.isActive() is True

    window._begin_sync()
    assert window._pointer_timer.isActive() is False
    window._cancel_sync()
    assert window._pointer_timer.isActive() is True


def test_the_attempt_prompt_is_held_out_without_a_pointer(make_window):
    """Echo practice pauses the song and hands the turn over, and the done
    button is the only way out of it. A prompt nobody can see is not a
    prompt, so the reveal is held open for as long as the window is asking
    something."""
    window = make_window()
    window._echo_enabled = True
    window._loop.echo = True
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    window._last_state = PlaybackState.PLAYING
    window._toggle_loop(True)
    go_compact(window)
    assert window._attempt_button.isVisibleTo(window) is False  # still playing the line

    window._do_loop_wrap()  # the line ends: your turn
    finish_reveal(window)
    assert window._loop.phase is w.LoopPhase.ATTEMPT
    assert window._reveal == 1.0
    assert window._attempt_button.isVisibleTo(window) is True

    window._on_attempt_done_clicked()
    finish_reveal(window)
    assert window._reveal == 0.0


def test_compact_off_takes_the_fade_away_rather_than_leaving_it_idle(make_window):
    """An effect that is doing nothing is still an effect, on every repaint
    of every control."""
    window = make_window()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    assert window._reveal_effects == {}
    for button in window._revealable():
        assert button.graphicsEffect() is None

    go_compact(window)
    assert len(window._reveal_effects) == len(window._revealable())
    for button in window._revealable():
        assert button.graphicsEffect() is not None

    window._set_compact(False)
    APP.processEvents()
    assert window._reveal_effects == {}
    for button in window._revealable():
        assert button.graphicsEffect() is None
    assert window._reveal == 1.0


def test_a_hidden_window_does_not_come_back_revealed(make_window):
    """The reveal goes back with the watch: coming out of the menu bar with
    the controls already showing would be the window answering a hover
    nobody performed."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    hover(window, True)
    assert window._reveal == 1.0

    window._set_lyrics_visible(False)
    land(window)
    finish_reveal(window)
    assert window._hovered is False
    assert window._reveal == 0.0


def test_the_failure_reason_lands_under_the_message_in_compact(make_window):
    """The upcoming row it normally uses is gone, and the pronunciation row
    directly under the message has taken its place: same argument, one row
    up."""
    window = make_window()
    window._on_track_change(snapshot())
    window._title_card_until = 0.0
    window._on_fetch_finished(
        "t1", None, False, FetchFailure(kind="unreachable", attempt="album match")
    )
    go_compact(window)
    window._toggle_why(True)
    APP.processEvents()
    detail = window._view_model.display().detail
    assert detail
    assert window._pron.text() == detail
    assert window._pron.isVisibleTo(window) is True
    assert window._why_button.isVisibleTo(window) is True

    window._toggle_why(False)
    APP.processEvents()
    assert window._pron.text() == ""


def test_the_resize_floor_follows_the_layout(make_window):
    """Dragging the bottom edge up must reach the strip in compact and stop
    at five rows out of it."""
    window = make_window()
    window.setGeometry(100, 100, 460, 300)
    APP.processEvents()

    def drag_the_bottom_edge_up():
        window._press_global = QPoint(560, 400)
        window._press_geometry = window.geometry()
        window._resize_edges = Qt.Edge.BottomEdge
        window._apply_resize(QPoint(560, 100))
        APP.processEvents()
        window._resize_edges = Qt.Edges()
        return window.height()

    assert drag_the_bottom_edge_up() == w.min_window_height(window._scale)
    go_compact(window)
    assert drag_the_bottom_edge_up() == w.min_window_height(
        window._scale, compact=True
    )
