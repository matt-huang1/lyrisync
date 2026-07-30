"""The journey to and from the menu bar item.

Pure arithmetic, so the path can be checked without a screen, a menu bar
or a compositor. What cannot be checked here — whether it LOOKS like the
window went to the menu bar — is a question about pixels and is verified
by hand; see docs/menu-and-system-integration.md.
"""

import pytest

from sottovoce import flight

SCREEN = (0, 0, 1710, 1107)
SECOND_SCREEN = (1710, -200, 1920, 1080)
WINDOW = (400, 500, 460, 200)
ITEM = (1159, 0, 38, 34)


# -- is there anywhere to fly to? -----------------------------------------


def test_an_item_on_the_screen_can_be_flown_to():
    assert flight.item_usable(ITEM, [SCREEN])


def test_no_item_at_all_is_a_plain_fade():
    """The menu bar item may not exist: no system tray, or it never
    appeared. Not an error — the window fades where it stands."""
    assert not flight.item_usable(None, [SCREEN])


def test_an_item_of_no_size_is_a_plain_fade():
    """What a hidden item reports. A rectangle with no area is not a place
    to fly to, and treating it as one would send the window to a corner
    for no reason anybody could see."""
    assert not flight.item_usable((1159, 0, 0, 0), [SCREEN])
    assert not flight.item_usable((1159, 0, 38, 0), [SCREEN])
    assert not flight.item_usable((1159, 0, 0, 34), [SCREEN])


def test_an_item_that_is_not_on_any_screen_is_a_plain_fade():
    """Behind the notch, in an overflow, or on a display that has just
    been unplugged. A flight to a rectangle nobody can see would throw the
    window off the edge of the world on the way to nowhere."""
    assert not flight.item_usable((-4000, -4000, 38, 34), [SCREEN])
    assert not flight.item_usable((5000, 0, 38, 34), [SCREEN])


def test_an_item_on_the_second_screen_is_still_a_place_to_fly_to():
    assert flight.item_usable((3000, 0, 38, 34), [SCREEN, SECOND_SCREEN])


def test_an_item_with_no_screens_at_all_is_a_plain_fade():
    assert not flight.item_usable(ITEM, [])


# -- where the window is on the way ---------------------------------------


def test_the_journey_starts_exactly_where_the_window_is():
    frame = flight.frame_at(0.0, WINDOW, ITEM)
    assert (frame.x, frame.y) == (WINDOW[0], WINDOW[1])
    assert frame.scale == 1.0
    assert frame.opacity == 1.0


def test_the_journey_ends_on_the_menu_bar_item():
    """Centred on it, small and gone. The window's own rectangle never
    changes size — the content is what scales — so its top-left is
    wherever puts that shrunken content on the item."""
    frame = flight.frame_at(1.0, WINDOW, ITEM)
    assert (frame.x + WINDOW[2] / 2, frame.y + WINDOW[3] / 2) == flight.centre(ITEM)
    assert frame.scale == pytest.approx(flight.END_SCALE)
    assert frame.opacity == 0.0


def test_the_content_stops_small_rather_than_at_nothing():
    """A thing that shrinks to zero has to be watched to the very end; one
    that stops at about the size of the item it is heading for has already
    said where it went."""
    assert 0.0 < flight.END_SCALE < 0.2


def test_every_part_of_the_journey_moves_in_one_direction():
    frames = [flight.frame_at(step / 20, WINDOW, ITEM) for step in range(21)]
    assert [f.scale for f in frames] == sorted((f.scale for f in frames), reverse=True)
    assert [f.opacity for f in frames] == sorted(
        (f.opacity for f in frames), reverse=True
    )
    # The item is up and to the right of the window here, so y falls and
    # x rises the whole way.
    assert [f.y for f in frames] == sorted((f.y for f in frames), reverse=True)
    assert [f.x for f in frames] == sorted(f.x for f in frames)


def test_halfway_is_halfway():
    """Within a pixel: a window position is a whole number of them, so the
    midpoint of an odd distance has to land on one side of itself."""
    frame = flight.frame_at(0.5, WINDOW, ITEM)
    home_x, home_y = flight.centre(WINDOW)
    item_x, item_y = flight.centre(ITEM)
    assert frame.x + WINDOW[2] / 2 == pytest.approx((home_x + item_x) / 2, abs=1)
    assert frame.y + WINDOW[3] / 2 == pytest.approx((home_y + item_y) / 2, abs=1)
    assert frame.opacity == pytest.approx(0.5)


def test_progress_outside_the_journey_is_clamped():
    """Qt easing curves overshoot on some types, and a caller that
    reversed a flight could hand back a value from the other side."""
    assert flight.frame_at(-1.0, WINDOW, ITEM) == flight.frame_at(0.0, WINDOW, ITEM)
    assert flight.frame_at(2.0, WINDOW, ITEM) == flight.frame_at(1.0, WINDOW, ITEM)


def test_the_window_flies_from_wherever_it_happens_to_be():
    """Any screen position, including one that is mostly off the edge."""
    for home in ((0, 0, 460, 200), (1600, 1000, 460, 200), (-100, 900, 460, 200)):
        landed = flight.frame_at(1.0, home, ITEM)
        assert (landed.x + home[2] / 2, landed.y + home[3] / 2) == flight.centre(ITEM)


# -- the fallback ---------------------------------------------------------


def test_with_no_target_the_window_fades_where_it_stands():
    """The same function, not a second path: a fallback nobody exercises
    is a fallback that does not work."""
    for progress in (0.0, 0.5, 1.0):
        frame = flight.frame_at(progress, WINDOW, None)
        assert (frame.x, frame.y) == (WINDOW[0], WINDOW[1])
        assert frame.scale == 1.0
        assert frame.opacity == pytest.approx(1.0 - progress)


# -- how long it takes ----------------------------------------------------


def test_a_whole_journey_takes_the_whole_time():
    assert flight.duration_ms(0.0, 1.0) == flight.FLIGHT_MS
    assert flight.duration_ms(1.0, 0.0) == flight.FLIGHT_MS


def test_a_reversal_takes_only_as_long_as_it_has_to():
    """A hide interrupted halfway comes back from halfway. Coming back
    through a journey already made would leave the window drifting after
    the key that asked for it."""
    assert flight.duration_ms(0.5, 0.0) == pytest.approx(flight.FLIGHT_MS / 2, abs=1)
    assert flight.duration_ms(0.9, 1.0) == pytest.approx(flight.FLIGHT_MS / 10, abs=1)


def test_no_journey_still_takes_a_moment():
    """A zero-length animation never reports finishing, and the window
    would be left mid-flight with nothing to land it."""
    assert flight.duration_ms(0.4, 0.4) >= 1


def test_the_flight_is_as_quick_as_the_rest_of_the_windows_movement():
    """One sense of how fast this window moves: the same 260ms as a line
    change's phase and as the travel to a remembered position."""
    assert 150 <= flight.FLIGHT_MS <= 400
