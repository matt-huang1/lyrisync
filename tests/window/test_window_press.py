"""A press at a control, routed the way a real one is.

The one file here that lets Qt choose the receiver rather than naming it.
"""

TIER = "integration"  # Qt picks the receiver, which is the whole question here

from unittest.mock import patch

import pytest

from PySide6.QtCore import QPoint, Qt

from sottovoce import proximity
from sottovoce import window as w
from sottovoce.player_monitor import PlaybackState

from helpers import (
    APP,
    KOREAN_SYNCED,
    PressRecord,
    go_compact,
    hover,
    load,
    press_through,
    pressing,
    shown,
    snapshot,
)


# -- a press at a control, routed the way a real one is -------------------
#
# Every other file here reaches a control by calling its slot, its
# click(), or by asking whether it is visibleTo the window. None of that
# asks the question a user asks with a finger, which is "what is at this
# point, and what did it do with the press?" — and a control can be
# visible, wired and correct while the answer is "the window, and it
# started a drag". That failure looks like every control on the window
# being dead, and none of the rest would have gone red for it.
#
# ``press_through``, ``PressRecord`` and ``pressing`` live in helpers.py
# because test_window_fetch.py needs them too, for the two controls that
# only exist beside a failed lookup. They send the press to the top-level
# QWindow rather than to a widget, which is the one path that runs Qt's
# own hit testing: the widget under the point is found there, and a press
# that finds nothing lands on the window and starts a drag. Sent at each
# control's ACTUAL position, taken from its geometry rather than named, so
# a control that moves without its test moving is still being pressed
# where it is.


def test_the_harness_can_tell_a_control_from_the_window(make_window):
    """The guard on the two tests below. A press that lands on the window's
    own chrome must record a drag and no control, or a suite where every
    control is dead would still be green: an assertion that the drag
    handler did not fire proves nothing if nothing ever makes it fire.
    """
    window = shown(make_window())
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()

    with PressRecord(window, window._loop_button) as record:
        # The middle of the window, which is lyrics and margin: nothing to
        # press, so the press is a drag.
        press_through(window, QPoint(window.width() // 2, window.height() // 2))
    assert record.acted == 0
    assert record.dragged == 1


def test_every_control_takes_its_own_press_in_the_full_layout(make_window):
    """A real press at each control's real position acts, and the window's
    drag handler never sees it.

    Korean lyrics because the spoken reference is offered for a hangul line
    and no other, and the point here is the press rather than the gate: a
    control that is not on the window cannot be pressed, and a test that
    quietly asserted that would be asserting nothing.
    """
    window = shown(make_window())
    window._echo_enabled = True
    window._loop.echo = True
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    window._last_state = PlaybackState.PLAYING
    APP.processEvents()

    for name, button in (
        ("loop", window._loop_button),
        ("spoken reference", window._speak_button),
    ):
        assert button.isVisibleTo(window) is True, name
        record = pressing(window, button)
        assert record.acted == 1, f"{name} did not act on a press at its own position"
        assert record.dragged == 0, f"{name}'s press reached the drag handler"

    # The echo done button exists only while the attempt is waiting, which
    # is also the only state its press means anything in.
    window._toggle_loop(True)
    window._do_loop_wrap()
    APP.processEvents()
    assert window._awaiting_attempt() is True
    record = pressing(window, window._attempt_button)
    assert record.acted == 1
    assert record.dragged == 0
    assert window._awaiting_attempt() is False  # the press ended the attempt

    # And the tap row, which a sync pass takes the full layout back for.
    window._begin_sync()
    APP.processEvents()
    for name, button in (
        ("tap", window._tap_button),
        ("undo", window._undo_button),
        ("discard", window._sync_exit_button),
    ):
        assert button.isVisibleTo(window) is True, name
        record = pressing(window, button)
        assert record.acted == 1, f"{name} did not act on a press at its own position"
        assert record.dragged == 0, f"{name}'s press reached the drag handler"
    window._cancel_sync()


def test_every_revealed_control_takes_its_own_press_in_the_strip(make_window):
    """The same again in compact, where the controls are somewhere else and
    are only there at all once the pointer has arrived.

    Two layouts rather than one because they place these controls by
    different arithmetic, and a press is answered by where a control IS.
    """
    window = shown(make_window())
    load(window, KOREAN_SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    hover(window, True)
    APP.processEvents()

    for name, button in (
        ("loop", window._loop_button),
        ("spoken reference", window._speak_button),
    ):
        assert button.isVisibleTo(window) is True, name
        record = pressing(window, button)
        assert record.acted == 1, f"{name} did not act on a press at its own position"
        assert record.dragged == 0, f"{name}'s press reached the drag handler"


@pytest.mark.qt
def test_with_the_yield_off_nothing_ever_asks_for_click_through(make_window):
    """Click-through belongs to Ghost and to nothing else.

    The ghost tests in test_window_pointer_yield.py say it goes on and
    comes off with the fade. This says the switch is never touched at all
    in the mode the app ships in,
    which is the state a window whose every control is dead would be in if
    the switch had been initialised or left set: off is not "asked for and
    given back", it is never asked for.
    """
    asked = []
    original = w.LyricsWindow._apply_click_through

    def record(self, ignore):
        asked.append(ignore)
        return original(self, ignore)

    with patch.object(w.LyricsWindow, "_apply_click_through", record):
        window = shown(make_window())
        load(window, KOREAN_SYNCED)
        window._on_position_update(snapshot(position=2.0))
        go_compact(window)
        hover(window, True)
        hover(window, False)

    assert window._proximity_mode == proximity.OFF
    assert asked == []
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    )
