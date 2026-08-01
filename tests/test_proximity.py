"""Yielding to the pointer, without a pointer.

Everything here is the pure module: the trigger region and its hysteresis,
the edge-triggered state machine, where a dodge goes, the gate that
suspends the whole thing, and the ghost's opacity. What none of it can
answer is whether the window FEELS like it got out of the way, which is a
question about a hand on a trackpad and is verified by driving the real
one.
"""

from __future__ import annotations

TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce import proximity as p
from sottovoce.geometry import GRAB_MARGIN

# A window and a screen to put it on. Both plain rectangles, because the
# module is.
SCREEN = (0, 0, 1710, 1107)
WINDOW = (600, 400, 460, 200)
STRIP = (400, 25, 900, 40)


def clear_gate():
    """Every gate open, so a test about the region is not also a test about
    the gate."""
    return dict(
        mode=p.DODGE,
        visible=True,
        syncing=False,
        attempting=False,
        explaining=False,
        dragging=False,
        flying=False,
    )


# -- the three modes ------------------------------------------------------


def test_the_mode_is_its_own_label():
    """One string is the setting, the label and the stored value. Two
    would be two things to keep in step, and the menu draws these
    directly."""
    assert p.MODES == (p.OFF, p.DODGE, p.GHOST)
    assert p.OFF == "Off"
    assert len(set(p.MODES)) == 3


@pytest.mark.parametrize("mode", p.MODES)
def test_a_known_mode_survives_the_round_trip(mode):
    assert p.mode_from(mode) == mode


@pytest.mark.parametrize("raw", ["dodge", "", None, 3, "Fade", True])
def test_anything_else_is_off(raw):
    """A preference file is a thing a person can edit and a thing an older
    version can leave behind. A value nobody recognises leaves the window
    doing what it does with no layer on, rather than something invented."""
    assert p.mode_from(raw) == p.OFF


def test_off_is_a_refusal_like_any_other():
    """Off is not a special case anywhere else in the app: it is the layer
    being off, which is the first reason the gate gives."""
    assert p.refusal(**{**clear_gate(), "mode": p.OFF}) == p.DISABLED
    assert p.may_act(**{**clear_gate(), "mode": p.OFF}) is False


# -- the gate -------------------------------------------------------------


def test_nothing_in_the_way_means_the_window_may_act():
    assert p.refusal(**clear_gate()) is None
    assert p.may_act(**clear_gate()) is True


@pytest.mark.parametrize(
    "state, reason",
    [
        ("syncing", p.SYNCING),
        ("attempting", p.ATTEMPTING),
        ("explaining", p.EXPLAINING),
        ("dragging", p.DRAGGING),
        ("flying", p.FLYING),
    ],
)
def test_each_thing_that_needs_the_window_refuses_by_name(state, reason):
    """A refusal names itself, and the boolean is derived from it. Three of
    these are the same sentence — the user needs to click this window —
    and each is worth telling apart in a log."""
    assert p.refusal(**{**clear_gate(), state: True}) == reason
    assert p.may_act(**{**clear_gate(), state: True}) is False


def test_a_hidden_window_is_in_nobodys_way():
    assert p.refusal(**{**clear_gate(), "visible": False}) == p.HIDDEN


def test_the_state_a_user_is_in_the_middle_of_is_named_first():
    """Ordered most specific first: a sync pass over a dragged window is
    reported as the sync pass, because that is the thing they would be
    surprised to have interrupted."""
    both = p.refusal(**{**clear_gate(), "syncing": True, "dragging": True})
    assert both == p.SYNCING


# -- the region, and the hysteresis ---------------------------------------


def test_arriving_is_the_home_rectangle_exactly():
    """No margin on the way in. The window is in the way when the pointer
    is on it, and a window that reacted before being reached would be
    reacting to somebody passing by."""
    x, y, width, height = WINDOW
    assert p.still_engaged(
        point=(x + 1, y + 1), home=WINDOW, current=WINDOW, engaged=False
    )
    assert not p.still_engaged(
        point=(x - 1, y + 1), home=WINDOW, current=WINDOW, engaged=False
    )
    assert not p.still_engaged(
        point=(x + width + 1, y + 1), home=WINDOW, current=WINDOW, engaged=False
    )


def test_leaving_needs_the_release_margin():
    """The whole of the hysteresis: a pointer just outside the window has
    arrived at nothing and left nothing."""
    x, y, _, height = WINDOW
    just_outside = (x - 1, y + height // 2)
    assert not p.still_engaged(
        point=just_outside, home=WINDOW, current=WINDOW, engaged=False
    )
    assert p.still_engaged(
        point=just_outside, home=WINDOW, current=WINDOW, engaged=True
    )


def test_the_band_is_exactly_the_release_margin_wide():
    x, y, _, height = WINDOW
    edge_y = y + height // 2
    inside_band = (x - p.RELEASE_MARGIN, edge_y)
    past_it = (x - p.RELEASE_MARGIN - 1, edge_y)
    assert p.still_engaged(
        point=inside_band, home=WINDOW, current=WINDOW, engaged=True
    )
    assert not p.still_engaged(
        point=past_it, home=WINDOW, current=WINDOW, engaged=True
    )


def test_a_pointer_on_the_boundary_cannot_flap():
    """The property the margin exists for, stated as one: there is no
    single position that reads as arrived while engaged and as left while
    not. That is what a zero-width band would give."""
    x, y, _, height = WINDOW
    edge_y = y + height // 2
    for offset in range(-p.RELEASE_MARGIN - 2, 3):
        point = (x + offset, edge_y)
        arriving = p.still_engaged(
            point=point, home=WINDOW, current=WINDOW, engaged=False
        )
        leaving = p.still_engaged(
            point=point, home=WINDOW, current=WINDOW, engaged=True
        )
        # Once engaged, the answer can only be the same or stickier.
        assert leaving or not arriving


def test_the_region_is_anchored_on_where_the_window_belongs():
    """The rule that stops Dodge oscillating. A window that has stepped
    aside is not under the pointer any more, and a region that followed it
    would report clear one poll after reporting covered, for ever."""
    dodged = (WINDOW[0], WINDOW[1] + 900, WINDOW[2], WINDOW[3])
    inside_home = (WINDOW[0] + 10, WINDOW[1] + 10)
    assert p.still_engaged(
        point=inside_home, home=WINDOW, current=dodged, engaged=True
    )


def test_following_a_dodged_window_is_not_leaving_it():
    """What makes a dodged window catchable. Without this the pointer
    chasing it would leave the home region, the window would come back,
    and it would arrive where the pointer no longer is."""
    dodged = (WINDOW[0], WINDOW[1] + 900, WINDOW[2], WINDOW[3])
    on_the_dodged_one = (dodged[0] + 10, dodged[1] + 10)
    assert not p.still_engaged(
        point=on_the_dodged_one, home=WINDOW, current=WINDOW, engaged=True
    )
    assert p.still_engaged(
        point=on_the_dodged_one, home=WINDOW, current=dodged, engaged=True
    )


def test_clear_of_both_is_leaving():
    dodged = (WINDOW[0], WINDOW[1] + 900, WINDOW[2], WINDOW[3])
    assert not p.still_engaged(
        point=(5, 5), home=WINDOW, current=dodged, engaged=True
    )


def test_the_boundary_of_a_window_is_on_the_window():
    """Inclusive, unlike geometry.intersects, and the two are answering
    different questions: that one asks whether two rectangles share any
    AREA, where a shared edge is none. A pointer on the last row of pixels
    a window drew is on that window."""
    x, y, width, height = WINDOW
    assert p.contains((x, y), WINDOW)
    assert p.contains((x + width, y + height), WINDOW)
    assert not p.contains((x + width + 1, y + height), WINDOW)


# -- the state machine ----------------------------------------------------


def test_nothing_happens_on_a_poll_with_the_pointer_elsewhere():
    approach = p.Approach()
    assert approach.observe(inside=False) == p.IDLE
    assert approach.engaged is False
    assert approach.active is False


def test_arriving_starts_it_and_leaving_ends_it():
    approach = p.Approach()
    assert approach.observe(inside=True) == p.ARRIVED
    assert approach.active is True
    assert approach.observe(inside=True) == p.HELD
    assert approach.active is True
    assert approach.observe(inside=False) == p.LEFT
    assert approach.active is False


def test_a_refusal_hands_the_window_back_without_pretending_it_is_clear():
    """Two booleans and not one. Whether the pointer is on the window is a
    fact about the pointer; whether the window may act on it is the gate.
    Conflating them is what a re-arming bug is made of."""
    approach = p.Approach()
    approach.observe(inside=True)
    assert approach.observe(inside=True, refusal=p.SYNCING) == p.SUSPENDED
    assert approach.active is False
    assert approach.engaged is True


def test_the_behaviour_does_not_restart_under_a_hand_that_never_left():
    """Edge triggered, and this is why it matters. A sync pass ending with
    the pointer still on the window must not step it aside under a hand
    that is using it: the pointer has to leave and come back, which is a
    thing the user does rather than a thing that happens to them."""
    approach = p.Approach()
    approach.observe(inside=True)
    approach.observe(inside=True, refusal=p.SYNCING)
    assert approach.observe(inside=True) == p.IDLE
    assert approach.active is False
    # Leaving and coming back is what arms it again.
    assert approach.observe(inside=False) == p.IDLE
    assert approach.observe(inside=True) == p.ARRIVED
    assert approach.active is True


def test_a_suspension_that_suspends_nothing_is_idle():
    approach = p.Approach()
    assert approach.observe(inside=True, refusal=p.DISABLED) == p.IDLE


def test_taking_the_window_by_hand_stops_it_without_disengaging():
    """Wherever the user puts it is where it belongs now, so there is
    nothing to hand back — and nothing steps aside again until the pointer
    has actually gone."""
    approach = p.Approach()
    approach.observe(inside=True)
    approach.stand_down()
    assert approach.active is False
    assert approach.engaged is True
    assert approach.observe(inside=True) == p.IDLE
    assert approach.active is False


def test_release_forgets_both():
    approach = p.Approach()
    approach.observe(inside=True)
    approach.release()
    assert approach.engaged is False
    assert approach.active is False
    assert approach.observe(inside=True) == p.ARRIVED


# -- where a dodge goes ---------------------------------------------------


def middle(rect):
    x, y, width, height = rect
    return (x + width // 2, y + height // 2)


def test_a_dodge_vacates_the_whole_footprint():
    """Not just the pixel under the pointer. What somebody reaches into a
    window for is the thing the window is on top of, which is a region."""
    target = p.dodge_destination(WINDOW, middle(WINDOW), SCREEN)
    assert target is not None
    x, y = target
    moved = (x, y, WINDOW[2], WINDOW[3])
    assert not _overlaps(moved, WINDOW)


def test_the_gap_between_the_two_positions_is_the_release_margin():
    """One number, twice, because it is the same question in the other
    direction: a window that stepped aside by exactly its own height would
    land its new edge on the pointer's old position."""
    assert p.CLEARANCE == p.RELEASE_MARGIN
    _, y = p.dodge_destination(WINDOW, middle(WINDOW), SCREEN)
    assert y == WINDOW[1] + WINDOW[3] + p.CLEARANCE


def test_the_destination_is_never_under_the_pointer():
    """The one property every candidate has to keep, whatever the screen
    did to it on the way."""
    for home in (WINDOW, STRIP):
        for point in (middle(home), (home[0], home[1]), (home[0] + home[2], home[1])):
            target = p.dodge_destination(home, point, SCREEN)
            assert target is not None
            assert not p.contains(point, (*target, home[2], home[3]))


def test_the_axis_falls_out_of_the_windows_own_shape():
    """Least travel, and nothing names an axis anywhere. A strip 900 wide
    and 40 tall steps vertically because that is 52 points against 912; a
    tall narrow window steps sideways for the same arithmetic."""
    x, y = p.dodge_destination(STRIP, middle(STRIP), SCREEN)
    assert x == STRIP[0]  # nothing sideways
    assert y != STRIP[1]

    tall = (800, 300, 200, 600)
    x, y = p.dodge_destination(tall, middle(tall), SCREEN)
    assert y == tall[1]
    assert x != tall[0]


def test_a_window_at_the_top_of_the_screen_steps_down():
    """Docked, which is the case the strip is usually in. Up is off the
    screen, so the candidate that needed no clamping wins."""
    docked = (405, 0, 900, 40)
    x, y = p.dodge_destination(docked, middle(docked), SCREEN)
    assert (x, y) == (405, 40 + p.CLEARANCE)


def test_a_window_at_the_bottom_of_the_screen_steps_up():
    low = (405, 1107 - 40, 900, 40)
    x, y = p.dodge_destination(low, middle(low), SCREEN)
    assert x == 405
    assert y < low[1]


def test_a_destination_is_clamped_like_any_other_placement():
    """A width change is still a placement and so is this: the rule that
    keeps a window reachable does not care what moved it."""
    for home in (WINDOW, STRIP, (405, 0, 900, 40), (0, 900, 460, 200)):
        x, y = p.dodge_destination(home, middle(home), SCREEN)
        assert x + home[2] >= SCREEN[0] + GRAB_MARGIN
        assert x <= SCREEN[0] + SCREEN[2] - GRAB_MARGIN
        assert y + home[3] >= SCREEN[1] + GRAB_MARGIN
        assert y <= SCREEN[1] + SCREEN[3] - GRAB_MARGIN


def test_a_screen_edge_costs_the_nearest_direction_not_the_dodge():
    """The fallback, and the reason there is one. The window's preferred
    step is off the screen, the clamp drags it back, and the next
    candidate that fits whole is taken instead of a placement half off the
    display."""
    docked = (405, 0, 900, 40)
    x, y = p.dodge_destination(docked, middle(docked), SCREEN)
    assert (x, y) == (405, 40 + p.CLEARANCE)  # down, unclamped
    target = (405, -40 - p.CLEARANCE, 900, 40)
    assert (x, y) != target[:2]


def test_nowhere_to_go_is_said_rather_than_faked():
    """A window shuffled to a position still under the pointer would
    uncover nothing and look like the feature working.

    It takes an absurd screen to reach, and that is the point of pinning
    it: clamping leaves a strip of the grab margin clear at each edge, so
    the answer is only None when the available area is under two margins
    across in BOTH axes. The branch exists, so it is exercised."""
    postage_stamp = (0, 0, 2 * GRAB_MARGIN - 20, 2 * GRAB_MARGIN - 20)
    home = (0, 0, 100, 100)
    assert p.dodge_destination(home, (30, 30), postage_stamp) is None


def test_the_same_home_always_dodges_the_same_way():
    """The two vertical candidates cost exactly the same, so the order is
    fixed rather than however the sort happened to land: a window that
    stepped up one song and down the next would read as random."""
    first = p.dodge_destination(WINDOW, middle(WINDOW), SCREEN)
    for _ in range(5):
        assert p.dodge_destination(WINDOW, middle(WINDOW), SCREEN) == first


def _overlaps(first, second):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# -- the ghost ------------------------------------------------------------


def test_a_ghost_at_rest_changes_nothing():
    assert p.ghost_opacity(1.0, 0.0) == 1.0
    assert p.ghost_opacity(0.4, 0.0) == 0.4


def test_a_full_ghost_lands_on_the_ceiling():
    assert p.ghost_opacity(1.0, 1.0) == pytest.approx(p.GHOST_CEILING)


@pytest.mark.parametrize("base", [0.0, 0.05, 0.12, 0.25, 0.6, 1.0])
@pytest.mark.parametrize("level", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_a_ghost_is_never_brighter_than_what_it_was_handed(base, level):
    """The property, and it holds for a base already below the ceiling —
    which is what makes the min a guarantee rather than a live branch if
    either constant ever moves. ``base`` is the user's own opacity with a
    notification yield already folded into it, so this is also what makes
    a banner over a ghosted window leave it at whichever is fainter."""
    assert p.ghost_opacity(base, level) <= base + 1e-9


def test_the_level_is_clamped():
    assert p.ghost_opacity(1.0, 5.0) == pytest.approx(p.GHOST_CEILING)
    assert p.ghost_opacity(1.0, -5.0) == 1.0


def test_the_ceiling_is_under_anything_the_user_can_choose():
    """The window's own opacity floor is 0.25 and this is beneath it, so
    the ghost is always a destination rather than sometimes a no-op."""
    from sottovoce.window import _MIN_OPACITY

    assert p.GHOST_CEILING < _MIN_OPACITY


def test_the_fade_is_proportional_to_what_is_left_of_it():
    """A pointer that skims the window comes back from wherever the fade
    got to, in the time that part of the journey is worth."""
    assert p.fade_ms(0.0, 1.0) == p.GHOST_MS
    assert p.fade_ms(0.5, 1.0) == p.GHOST_MS // 2
    assert p.fade_ms(1.0, 0.5) == p.fade_ms(0.5, 1.0)


def test_a_fade_that_travels_nothing_still_finishes():
    """A zero-length animation never reports finishing, and the level
    would be left part way."""
    assert p.fade_ms(0.5, 0.5) == 1
