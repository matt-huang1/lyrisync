"""Yielding to the pointer: dodge, ghost, and the suspensions.

The pure rules — the region, the hysteresis, the destination, the gate —
are in test_proximity.py. What is here is what only a real window can
answer: that a temporary position is never mistaken for a permanent one,
that the song and the pointer do not both own the width, and that the
window comes back exactly.
"""

TIER = "qt"  # a real window, driven by calling its own methods

from unittest.mock import patch

import pytest

from PySide6.QtCore import QPoint, QRect, Qt

from sottovoce import accessibility
from sottovoce import menu as m
from sottovoce import notifications as n
from sottovoce import proximity
from sottovoce import window as w
from sottovoce.player_monitor import PlaybackState

from helpers import (
    APP,
    OPACITY_STEP,
    PLAIN,
    SYNCED,
    arrive,
    away_from,
    finish_fit,
    finish_move,
    go_compact,
    land,
    load,
    over,
    pointer_at,
    poll,
    press_at,
    release,
    settle,
    settle_yield,
    snapshot,
    with_mode,
)


def leave(window):
    """The pointer goes somewhere else entirely."""
    poll(window, away_from(window))


def test_the_layer_is_off_until_it_is_asked_for(make_window):
    """Default-off like every layer here, and off removes the work: with
    the compact layout off too, nothing is watching the pointer at all."""
    window = make_window()
    window.apply_saved_visibility()
    land(window)
    assert window._proximity_mode == proximity.OFF
    assert window._menu.chosen(m.PROXIMITY) == proximity.OFF
    assert window._pointer_timer.isActive() is False


def test_the_mode_is_persisted_and_restored(make_window):
    window = with_mode(make_window(), proximity.GHOST)
    window._save_settings()
    window._settings.sync()
    second = make_window()
    assert second._proximity_mode == proximity.GHOST
    assert second._menu.chosen(m.PROXIMITY) == proximity.GHOST


def test_a_mode_nobody_recognises_is_off(make_window):
    """A hand-edited or outgrown preference. The same rule the speech rate
    and the strip's type size are held to."""
    window = make_window()
    window._settings.setValue("window/proximity", "Vanish")
    window._settings.sync()
    second = make_window()
    assert second._proximity_mode == proximity.OFF


def test_the_layer_watches_the_pointer_in_the_full_layout_too(make_window):
    """The compact layout is not a condition of this one: a full-size
    window is as much in somebody's way as a strip."""
    window = with_mode(make_window(), proximity.DODGE)
    assert window._compact_applied is False
    assert window._pointer_timer.isActive() is True

    with pointer_at(window, away_from(window)):
        window._set_proximity_mode(proximity.OFF)
    assert window._pointer_timer.isActive() is False


# -- dodge ----------------------------------------------------------------


def test_the_window_steps_aside_and_comes_back_exactly(make_window):
    """The promise, and it is a promise about a pixel: a docked window is
    recognised by BEING exactly where docking put it, so anything less
    than exact would leave it drawn as a floating panel afterwards."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()

    arrive(window)
    assert window.pos() != home
    assert window._proximity_home == home

    leave(window)
    assert window.pos() == home
    assert window._proximity_home is None


def test_a_dodged_window_is_not_where_it_belongs(make_window):
    """The two are separate questions and the whole layer rests on it."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    assert window._home_pos() == home
    assert window._home_pos() != window.pos()
    assert window._home_rect().size() == window.size()


def test_a_temporary_position_is_never_learned(make_window):
    """The explicit requirement, and the reason for it: a position the
    pointer caused is not a preference the user expressed, and recording
    one would teach the map that this app's own dodging is where the
    window lives."""
    window = with_mode(make_window(), proximity.DODGE)
    window._set_remember_position(True)
    settle(window, "com.apple.Safari")
    home = window.pos()

    arrive(window)
    assert window.pos() != home
    window._learn_position()
    assert window._positions.peek("com.apple.Safari") == (home.x(), home.y())


def test_a_temporary_position_is_never_saved(make_window):
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    window._save_settings()
    assert window._settings.value("window/pos") == home


def test_shutdown_brings_the_window_home_before_it_saves(make_window):
    """The flight's rule seen once more: something is holding the window's
    real position, and the save must not persist the loan."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    window._shutdown()
    assert window.pos() == home
    assert window._settings.value("window/pos") == home


def test_a_remembered_position_moves_the_home_rather_than_the_window(
    make_window,
):
    """A window standing aside is moved by changing where it will step
    BACK to. Moving it would be pointless: the yield is holding the real
    position and would hand the old one straight back."""
    window = with_mode(make_window(), proximity.DODGE)
    window._set_remember_position(True)
    settle(window, "com.apple.Safari")
    window.move(500, 500)
    window._learn_position()
    settle(window, "com.apple.Terminal")
    window.move(900, 700)
    window._learn_position()

    arrive(window)
    dodged = window.pos()
    settle(window, "com.apple.Safari")
    finish_move(window)
    assert window._home_pos() == QPoint(500, 500)
    assert window.pos() != dodged

    leave(window)
    assert window.pos() == QPoint(500, 500)


def test_the_song_and_the_pointer_do_not_both_own_the_width(make_window):
    """Fitting measures from where the window BELONGS. Anchoring a width
    change on a dodged position would centre the new width on a temporary
    one and then hand that back as the permanent one."""
    window = with_mode(make_window(), proximity.DODGE)
    go_compact(window)
    window._proximity.release()
    home = window.pos()
    width = window.width()

    arrive(window)
    dodged = window.pos()
    window._resize_width_to(width + 200)
    finish_fit(window)
    assert window.width() == width + 200
    assert window._home_pos() != dodged
    # Anchored on the home's centre, which is what it would have been with
    # no pointer anywhere near it.
    assert window._home_pos().x() == home.x() - 100

    leave(window)
    assert window.pos() == window._home_pos()
    assert window.width() == width + 200


def test_taking_the_window_by_hand_adopts_where_it_is(make_window):
    """A press can only reach a window that stepped aside if the user
    followed it there, which is the gesture for taking it back. Nothing
    steps aside again until the pointer has left and come back."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    dodged = window.pos()

    window.mousePressEvent(press_at(window, dodged))
    assert window._proximity_home is None
    assert window.pos() == dodged
    assert window._proximity.engaged is True
    assert window._proximity.active is False
    window.mouseReleaseEvent(release())

    arrive(window)
    assert window.pos() == dodged  # not re-armed by a pointer that never left
    assert home != dodged


def test_a_dodged_window_can_be_followed_without_it_running_away(make_window):
    """Following it is not leaving it. Without that half the pointer
    chasing it would leave the home region, the window would come back,
    and it would arrive where the pointer no longer is."""
    window = with_mode(make_window(), proximity.DODGE)
    arrive(window)
    dodged = window.pos()
    poll(window, over(window))
    assert window.pos() == dodged


def test_the_window_stays_put_when_there_is_nowhere_to_go(make_window):
    """Said rather than faked. A window shuffled to a position still under
    the pointer would uncover nothing and look like the feature working."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    with patch.object(w.LyricsWindow, "_dodge_target", lambda self, home: None):
        arrive(window)
    assert window.pos() == home
    assert window._proximity_home is None


def test_reduce_motion_takes_the_travel_and_leaves_the_answer(make_window):
    """The layer is about where the window ends up, not about how it gets
    there — the same reading the travel to a remembered position gets."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    window._display_options = accessibility.DisplayOptions(reduce_motion=True)

    with pointer_at(window, over(window)):
        window._check_pointer()
    assert window._move_anim is None
    assert window.pos() != home

    with pointer_at(window, away_from(window)):
        window._check_pointer()
    assert window._move_anim is None
    assert window.pos() == home


# -- ghost ----------------------------------------------------------------


def test_ghosting_fades_the_window_and_lets_the_clicks_through(make_window):
    window = with_mode(make_window(), proximity.GHOST)
    home = window.pos()
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)

    arrive(window)
    assert window.pos() == home  # nothing moves, so nothing has to move back
    assert window.windowOpacity() == pytest.approx(proximity.GHOST_CEILING, abs=OPACITY_STEP)
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    )

    leave(window)
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    )


def test_the_clicks_go_through_at_the_start_of_the_fade_not_the_end(
    make_window,
):
    """The pointer arriving is the request. A window that stayed clickable
    for the length of a fade would swallow exactly the click the user came
    to make."""
    window = with_mode(make_window(), proximity.GHOST)
    with pointer_at(window, over(window)):
        window._check_pointer()
    assert window._ghost_anim is not None  # still fading
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    )


def test_a_ghosted_strip_does_not_offer_controls_it_cannot_take(make_window):
    """The mirror of the rule about a widget at zero opacity: a control
    that can be seen and not pressed is worse on screen than off it."""
    window = with_mode(make_window(), proximity.GHOST)
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._proximity.release()

    arrive(window)
    assert window._reveal == 0.0
    assert window._loop_button.isVisibleTo(window) is False

    leave(window)
    assert window._reveal == 0.0  # the pointer is elsewhere now


def test_the_two_ceilings_compose_rather_than_argue(make_window):
    """A banner over a ghosted window leaves it at whichever of the two is
    fainter, which is the one that is more out of the way. Everything that
    scales this window's opacity still composes in one place.

    Asserted as the property rather than as the arithmetic, because the
    two ceilings happen to be the same number today and an equality would
    pass without saying anything the day one of them moves."""
    window = with_mode(make_window(), proximity.GHOST)
    arrive(window)
    window._set_yielding(True)
    settle_yield(window)
    assert window.windowOpacity() <= min(
        proximity.GHOST_CEILING, n.YIELD_CEILING
    ) + OPACITY_STEP
    leave(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_a_ghost_is_never_brighter_than_the_user_asked_for(make_window):
    window = with_mode(make_window(), proximity.GHOST)
    for opacity in (0.25, 0.5, 1.0):
        window._set_opacity(opacity)
        arrive(window)
        assert window.windowOpacity() <= window._opacity + OPACITY_STEP
        leave(window)
        assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_hiding_gives_the_ghost_back_before_the_flight_borrows_it(make_window):
    """The yield's rule: a window that goes away faded would come back
    faded, because the level is not something the flight restores."""
    window = with_mode(make_window(), proximity.GHOST)
    arrive(window)
    assert window._ghost_level == 1.0

    window._set_lyrics_visible(False)
    land(window)
    assert window._ghost_level == 0.0
    assert (
        window.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False
    )


# -- the suspensions ------------------------------------------------------


def test_switching_mode_hands_back_what_the_old_one_borrowed(make_window):
    """Dodge to Ghost cannot leave a window standing aside with nothing
    left that remembers where it came from."""
    window = with_mode(make_window(), proximity.DODGE)
    home = window.pos()
    arrive(window)
    assert window.pos() != home

    with pointer_at(window, over(window)):
        window._set_proximity_mode(proximity.GHOST)
    finish_move(window)
    assert window.pos() == home
    assert window._proximity_home is None
    assert window._ghost_level == 0.0  # edge triggered: it did not re-arm


@pytest.mark.parametrize("mode", [proximity.DODGE, proximity.GHOST])
def test_a_sync_pass_suspends_the_whole_layer(make_window, mode):
    """A rhythm game played on a button on this window, once per line.
    Ghosting it would send the taps to whatever is behind it and Dodging
    it would move the target mid-song."""
    window = with_mode(make_window(), mode)
    load(window, PLAIN, track_id="t7")
    home = window.pos()

    arrive(window)
    window._begin_sync()
    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0
    assert window._proximity.engaged is True  # the pointer did not go anywhere

    # And it does not re-arm under a hand that never left.
    window._leave_sync()
    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0


@pytest.mark.parametrize("mode", [proximity.DODGE, proximity.GHOST])
def test_an_echo_attempt_suspends_the_whole_layer(make_window, mode):
    """The song is paused, the turn is the user's, and the done button is
    the only way out of it."""
    window = with_mode(make_window(), mode)
    window._echo_enabled = True
    window._loop.echo = True
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    window._last_state = PlaybackState.PLAYING
    window._toggle_loop(True)
    home = window.pos()

    arrive(window)
    window._do_loop_wrap()
    assert window._awaiting_attempt() is True
    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0


@pytest.mark.parametrize("mode", [proximity.DODGE, proximity.GHOST])
def test_an_open_failure_register_suspends_the_whole_layer(make_window, mode):
    """The click that closes it is the same click, on the same control."""
    window = with_mode(make_window(), mode)
    home = window.pos()
    window._toggle_why(True)

    poll(window, over(window))
    assert window.pos() == home
    assert window._ghost_level == 0.0
    assert window._proximity_refusal() == proximity.EXPLAINING


def test_a_drag_suspends_it(make_window):
    window = with_mode(make_window(), proximity.DODGE)
    window.mousePressEvent(press_at(window, over(window)))
    assert window._proximity_refusal() == proximity.DRAGGING
    window.mouseReleaseEvent(release())
    assert window._proximity_refusal() is None


def test_a_flight_suspends_it(make_window):
    """Two animations of one window's position could only fight, and the
    flight owns the opacity too until it lands."""
    window = with_mode(make_window(), proximity.DODGE)
    window._set_lyrics_visible(False)
    if window._flight_anim is not None:
        assert window._proximity_refusal() == proximity.FLYING
    land(window)
    assert window._proximity_refusal() == proximity.HIDDEN


def test_a_docked_window_steps_aside_and_docks_again(make_window):
    """Docked is recognised by BEING exactly where docking put it, so a
    window that came back a pixel off would be drawn square across the top
    while sitting in the middle of a screen."""
    window = with_mode(make_window(), proximity.DODGE)
    window._dock_to_top()
    finish_move(window)
    docked = window.pos()
    assert window._docked is True

    arrive(window)
    assert window.pos() != docked
    assert window._docked is False  # it is not under the menu bar any more

    leave(window)
    assert window.pos() == docked
    assert window._docked is True


def test_a_strip_steps_aside_too(make_window):
    """Both layouts. A strip is the shape most likely to be parked over
    somebody's work, and it steps vertically because that is the shorter
    way out of its own footprint."""
    window = with_mode(make_window(), proximity.DODGE)
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))
    go_compact(window)
    window._proximity.release()
    finish_fit(window)
    home = window.pos()

    arrive(window)
    assert window.pos().x() == home.x()
    assert window.pos().y() != home.y()
    assert not window.frameGeometry().intersects(
        QRect(home, window.size())
    )

    leave(window)
    assert window.pos() == home


def test_a_window_at_the_origin_still_knows_where_it_belongs(make_window):
    """PySide gives QPoint a __bool__ and QPoint(0, 0) is FALSE, so a
    truth test on the held position would hand back where the window is
    standing as where it belongs — for exactly one window in a thousand,
    the one docked at the top-left of the primary screen."""
    window = with_mode(make_window(), proximity.DODGE)
    window.move(0, 0)
    APP.processEvents()

    arrive(window)
    assert window.pos() != QPoint(0, 0)
    assert window._home_pos() == QPoint(0, 0)

    leave(window)
    assert window.pos() == QPoint(0, 0)
