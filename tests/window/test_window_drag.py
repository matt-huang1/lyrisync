"""Dragging and resizing, with Qt deciding what each press means.

The other file that lets Qt route a press is test_window_press.py, and it
asks the opposite question: there, a press must reach a CONTROL and the
drag handler must never see it. Here every press is meant for the window
itself, and what has to be right is what the window then does with it.

Everything about this behaviour was asked by hand before: a
``mousePressEvent`` built in the test, a ``window.move()`` standing in for
the drag, a ``mouseReleaseEvent`` built the same way. Three things live in
the gap that opens up. The press has to be hit-tested to decide whether it
is a move or a resize, and only Qt does that. The move has to reach
``mouseMoveEvent`` with a button held, and a test that calls
``window.move()`` has moved the window without going through the handler
that would have. And the release has to find a geometry that differs from
the one the press recorded, which is the whole of the rule that a press
which moved nothing is not somebody placing a window: the drag-of-zero
that lit the learn glow was a press Qt routed here, and no test that
supplied its own geometry could produce one.
"""

TIER = "integration"  # Qt decides move, resize or neither

import logging

import pytest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from sottovoce import menu as m

from helpers import (
    APP,
    SYNCED,
    VSCODE,
    finish_fit,
    finish_move,
    go_compact,
    load,
    shown,
)


# -- a press, a move and a release, all routed by Qt -----------------------


def deliver(window, kind, point, buttons):
    """One mouse event at a window-local point, hit-tested by Qt.

    Delivered to ``windowHandle()`` rather than to the widget, for the
    reason test_window_press.py gives at length: sending to a widget names
    the receiver, and which receiver a press resolves to is exactly what
    these tests are about. A window with no handle has nothing to route
    through, so every test here shows the window first.
    """
    handle = window.windowHandle()
    assert handle is not None, "the window must be shown to be pressed on"
    APP.sendEvent(
        handle,
        QMouseEvent(
            kind,
            QPointF(point),
            QPointF(window.mapToGlobal(point)),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    APP.processEvents()


def press(window, point):
    deliver(window, QEvent.Type.MouseButtonPress, point, Qt.MouseButton.LeftButton)


def move_to(window, point):
    deliver(window, QEvent.Type.MouseMove, point, Qt.MouseButton.LeftButton)


def release(window, point):
    deliver(window, QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.NoButton)
    # The release defers the nudge and the learn by one tick, so that what
    # is recorded is where the window actually ended up rather than where
    # the event said it was going.
    APP.processEvents()


def drag(window, frm, by):
    """Press at a window-local point, move by an offset, let go.

    The offset is applied to the same window-local point rather than to a
    fresh one, so the coordinates the handler sees move exactly as far as
    a hand would have moved them: the window travels under the pointer,
    and a second point read from the moved window would cancel the travel
    out.
    """
    press(window, frm)
    move_to(window, frm + by)
    release(window, frm + by)


def middle(window):
    """A window-local point that is lyrics and margin: no control, and no
    resize edge either, so a press there is a drag and nothing else."""
    return QPoint(window.width() // 2, window.height() // 2)


def right_edge(window):
    """A window-local point inside the resize margin on the right."""
    return QPoint(window.width() - 2, window.height() // 2)


@pytest.fixture
def learning(make_window):
    """A shown window with the position layer on, another app in front, and
    a record of every position it decides to remember."""
    window = shown(make_window())
    load(window, SYNCED)
    window._remember_position = True
    window._frontmost = VSCODE
    window._own_bundle_id = "com.sottovoce.sottovoce"
    window.move(300, 200)
    APP.processEvents()
    recorded = []
    real = window._positions.remember

    def recording(bundle_id, x, y, name=None):
        recorded.append((x, y))
        return real(bundle_id, x, y, name)

    window._positions.remember = recording
    return window, recorded


# -- moving ----------------------------------------------------------------


def test_a_drag_qt_routed_moves_the_window_and_learns_where_it_landed(learning):
    """The whole gesture, end to end, with nothing about it supplied.

    The assertion that the window moved is not a formality: it is what
    proves the move reached ``mouseMoveEvent`` at all. A harness whose
    press never resolved to the window would report a learn of nothing and
    look exactly like the bug this file exists for.
    """
    window, positions = learning
    before = window.frameGeometry().topLeft()

    drag(window, middle(window), QPoint(60, 40))

    assert window.frameGeometry().topLeft() == before + QPoint(60, 40)
    assert positions == [(before.x() + 60, before.y() + 40)]
    assert window._glow_anim is not None, "nothing said the position was learned"


def test_a_press_that_moved_nothing_learns_nothing_and_does_not_glow(
    learning, caplog
):
    """The learn glow bug, asked the way it happened. Every press that
    misses a control lands on the window, every one of those ends a drag of
    zero pixels, and the app used to answer each one by recording a
    position and lighting the hairline to say so.

    The refusal is read off the log rather than recomputed here. A gate
    that names its reason is only worth having if what is checked is the
    reason the app gave, and a reconstruction can disagree with what
    happened.
    """
    window, positions = learning
    before = window.frameGeometry()

    with caplog.at_level(logging.DEBUG, logger="sottovoce.window"):
        press(window, middle(window))
        release(window, middle(window))

    assert window.frameGeometry() == before
    assert positions == []
    assert window._glow_anim is None
    assert "learn: nothing recorded, the window was not moved" in caplog.text


def test_the_drag_offset_is_given_back_at_the_release(learning):
    """A window still holding an offset would follow the pointer around
    with no button down. Two presses in a row is how that shows up."""
    window, _ = learning
    drag(window, middle(window), QPoint(30, 20))
    assert window._drag_offset is None
    assert not window._resize_edges

    settled = window.frameGeometry().topLeft()
    deliver(window, QEvent.Type.MouseMove, middle(window), Qt.MouseButton.NoButton)
    assert window.frameGeometry().topLeft() == settled


# -- resizing --------------------------------------------------------------


def test_a_drag_on_an_edge_resizes_rather_than_moving(learning):
    """Which of the two a press means is Qt's answer to where it landed,
    and then the window's to which edge that is. Both are skipped by a test
    that sets ``_resize_edges`` itself."""
    window, positions = learning
    before = window.frameGeometry()

    drag(window, right_edge(window), QPoint(70, 0))

    assert window.frameGeometry().topLeft() == before.topLeft(), "it moved"
    assert window.width() == before.width() + 70
    # A resize is a placement too: the geometry changed, so the release has
    # something to learn. What it must never be is a learn of nothing.
    assert positions == [(before.x(), before.y())]


def test_a_press_on_an_edge_turns_the_fitting_off_before_anything_moves(make_window):
    """At the START of the drag, so the song does not re-fit the window out
    from under a gesture still in progress. Asserted between the press and
    the move, which is the one place the difference is visible."""
    window = shown(make_window())
    go_compact(window)
    load(window, SYNCED)
    finish_fit(window)
    assert window._fitting is True

    press(window, right_edge(window))

    assert window._fit_to_song is False
    assert window._settings.value("window/fit_to_song", type=bool) is False
    assert window._menu.is_checked(m.FIT_TO_SONG) is False

    release(window, right_edge(window))


def test_a_drag_in_the_middle_leaves_the_fitting_alone(make_window):
    """Moving is not resizing, and the two are told apart by where the
    press landed and nothing else."""
    window = shown(make_window())
    go_compact(window)
    load(window, SYNCED)
    finish_fit(window)

    drag(window, middle(window), QPoint(40, 30))

    assert window._fit_to_song is True
    assert window._fitting is True


# -- docking, which is a position and not a flag ---------------------------


def test_dragging_a_docked_window_undocks_it(make_window):
    """Whether the window is docked is asked of the POSITION on every move.
    A flag would have to be cleared by every drag, and this is the drag."""
    window = shown(make_window())
    load(window, SYNCED)
    window._dock_to_top()
    finish_move(window)
    assert window._docked is True

    drag(window, middle(window), QPoint(0, 120))

    assert window._docked is False


def test_a_drag_that_lands_on_the_docked_position_is_docked_again(make_window):
    """The other half, and the reason there is no flag: nothing about this
    drag is a dock command, and the window is drawn square across the top
    all the same."""
    window = shown(make_window())
    load(window, SYNCED)
    window._dock_to_top()
    finish_move(window)
    home = window.frameGeometry().topLeft()

    drag(window, middle(window), QPoint(0, 150))
    assert window._docked is False

    start = middle(window)
    press(window, start)
    move_to(window, start - QPoint(0, 150))
    release(window, start - QPoint(0, 150))

    assert window.frameGeometry().topLeft() == home
    assert window._docked is True
