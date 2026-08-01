"""Getting out of a notification's way, and the poll that notices one.
"""

TIER = "qt"  # a real window, driven by calling its own methods

import pytest

from sottovoce import menu as m
from sottovoce import notifications as n
from sottovoce import window as w

from helpers import APP, OPACITY_STEP, PLAIN, land, load, settle_yield


# -- getting out of a notification's way ----------------------------------
#
# The pure rules — what overlaps, how faint, how long — live in
# test_notifications.py. These cover what only a real window can answer:
# that the timer follows the layer, that the three things with an opinion
# about opacity compose, and that every path out of a fade gives it back.
#
# occupied_rects is stubbed rather than blocked. The conftest guard shuts
# the door underneath it and stays armed for anything reaching around;
# handing back rectangles here is what lets the poll be driven on a machine
# with no notification centre at all, which is every CI runner.


DISPLAY_RECT = (0, 0, 1710, 1107)


def put_in_the_way(window):
    """Move the window into the strip where notifications actually appear.

    Needed since 16.1: the window's position is now part of the answer, so a
    test that wants a fade has to say where the window is. Derived from the
    region rather than written as a number, so the constant moving cannot
    leave these tests quietly asserting nothing.
    """
    x, y, width, _ = n.plausible_region(DISPLAY_RECT)
    window.move(x + 10, y + 10)
    APP.processEvents()
    assert n.in_the_way(
        (window.frameGeometry().x(), window.frameGeometry().y(),
         window.frameGeometry().width(), window.frameGeometry().height()),
        [DISPLAY_RECT],
    ), "the test's own premise: this window should be in the way"


def put_out_of_the_way(window):
    """Move the window well clear of where notifications appear."""
    window.move(20, 400)
    APP.processEvents()


def notifications_at(monkeypatch, *rects):
    """What the next poll will see. Returns the call log, so a test can
    assert the layer is not looking at all."""
    calls = []

    def occupied():
        calls.append(True)
        return tuple(rects)

    monkeypatch.setattr(w.notifications, "occupied_rects", occupied)
    return calls


def test_yielding_to_notifications_is_off_by_default(make_window):
    """Default off, like every layer. And off means not looking: the timer
    is the whole of the watching, so an inactive timer is the layers
    principle taken literally."""
    window = make_window()
    assert window._yield_to_notifications is False
    assert window._yield_timer.isActive() is False
    assert window._menu.is_checked(m.YIELD_NOTIFICATIONS) is False


def test_the_layer_being_off_asks_nothing(make_window, monkeypatch):
    """Not merely ignored — never asked. A poll that arrived anyway must
    still not read the window list."""
    window = make_window()
    calls = notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    assert calls == []
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_switching_it_on_starts_watching_and_off_stops(make_window):
    window = make_window()
    window._set_yield_to_notifications(True)
    assert window._yield_timer.isActive() is True
    assert window._menu.is_checked(m.YIELD_NOTIFICATIONS) is True

    window._set_yield_to_notifications(False)
    assert window._yield_timer.isActive() is False
    assert window._menu.is_checked(m.YIELD_NOTIFICATIONS) is False


def test_switching_it_on_fades_nothing_by_itself(make_window, monkeypatch):
    """The first poll is a third of a second away and will answer honestly.
    Fading at the moment a menu item is ticked would be a guess."""
    window = make_window()
    calls = notifications_at(monkeypatch, DISPLAY_RECT)
    window._set_yield_to_notifications(True)
    assert calls == []
    assert window._yield_level == 0.0


def test_a_notification_over_the_window_fades_it(make_window, monkeypatch):
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    assert window._yielding is True
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_the_users_opacity_comes_back_when_it_clears(make_window, monkeypatch):
    window = make_window()
    put_in_the_way(window)
    window._set_opacity(0.8)
    window._set_yield_to_notifications(True)

    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)

    notifications_at(monkeypatch)  # the banner has gone
    window._check_notifications()
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(0.8, abs=OPACITY_STEP)
    assert window._yield_level == 0.0


def test_a_notification_that_does_not_reach_the_window_is_left_alone(
    make_window, monkeypatch
):
    """A banner on another display. The intersection is real arithmetic, so
    this is the same code path as the overlapping case rather than a
    branch."""
    window = make_window()
    window.move(200, 200)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, (5000, 0, 1710, 1107))

    window._check_notifications()
    assert window._yielding is False
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_an_already_dimmed_window_is_never_brightened(make_window, monkeypatch):
    """The user has scrolled the window down to the floor. Yielding takes it
    further, never back up — measured against their own setting, not against
    full opacity."""
    window = make_window()
    put_in_the_way(window)
    window._set_opacity(w._MIN_OPACITY)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    seen = []
    for step in range(0, 11):
        window._yield_level = step / 10
        window._apply_window_opacity()
        seen.append(window.windowOpacity())
    assert max(seen) <= w._MIN_OPACITY + 1e-6
    assert seen == sorted(seen, reverse=True)


def test_a_repeat_poll_while_faded_starts_no_second_fade(make_window, monkeypatch):
    """Three polls a second land inside every banner. An announcement of
    what is already true is not news — the same dedupe shape as the line
    change's target index."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    settle_yield(window)
    assert window._yield_anim is None

    window._check_notifications()
    window._check_notifications()
    assert window._yield_anim is None
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_a_banner_clearing_mid_fade_turns_around_from_where_it_got_to(
    make_window, monkeypatch
):
    """The interruption case. It retargets from the level the window
    actually reached and pays for the distance left, rather than finishing a
    fade nobody is waiting for."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()

    halfway = window._yield_anim
    halfway.setCurrentTime(halfway.duration() // 2)
    APP.processEvents()
    reached = window._yield_level
    assert 0.0 < reached < 1.0

    notifications_at(monkeypatch)
    window._check_notifications()
    assert window._yield_anim is not None
    assert window._yield_anim.startValue() == pytest.approx(reached)
    assert window._yield_anim.duration() < n.YIELD_MS

    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_a_sync_pass_is_never_faded_under_the_user(make_window, monkeypatch):
    """Principle 6: the pass is the user tapping this window once per line,
    and a decorative feature does not get to fade an essential one. A pass
    beginning while the window is already faint hands the opacity back."""
    window = make_window()
    put_in_the_way(window)
    load(window, PLAIN)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is True

    window._begin_sync()
    assert window._syncing is True
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is False
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_hiding_the_window_hands_the_opacity_back_and_stops_watching(
    make_window, monkeypatch
):
    """A hidden window is in nobody's way, so there is nothing to look for.
    The level goes back BEFORE the flight borrows the opacity — a window
    that went away faded would come back faded, because the flight restores
    its own factor and not this one."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._set_lyrics_visible(False)
    assert window._yield_level == 0.0
    assert window._yield_timer.isActive() is False
    land(window)

    window._set_lyrics_visible(True)
    land(window)
    assert window._yield_timer.isActive() is True
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_a_flight_and_a_yield_compose_rather_than_overwrite(make_window, monkeypatch):
    """The pair that could not happen before this milestone and now can.
    Both scale the same window, so they multiply — and neither may reset the
    other's contribution on its way out."""
    window = make_window()
    window._set_opacity(0.9)
    window._yield_level = 1.0
    window._flight_opacity = 0.5
    window._apply_window_opacity()
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING * 0.5, abs=OPACITY_STEP)

    window._end_flight()
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_switching_the_layer_off_gives_the_window_back_at_once(
    make_window, monkeypatch
):
    """No fade on the way out of the layer: the user asked for the window
    back, not for it to drift back."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._set_yield_to_notifications(False)
    assert window._yield_anim is None
    assert window._yield_level == 0.0
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_the_setting_survives_a_restart(make_window):
    """And restoring it on must not need the menu to exist yet: the setter
    refreshes the menu, and _restore_settings runs before it is built —
    which is the bug the previous layer shipped and this one inherits the
    fix for."""
    first = make_window()
    first._set_yield_to_notifications(True)
    first._save_settings()
    first._settings.sync()

    second = make_window()
    assert second._yield_to_notifications is True
    assert second._yield_timer.isActive() is True
    assert second._menu.is_checked(m.YIELD_NOTIFICATIONS) is True


def test_a_restored_hidden_window_does_not_start_watching(make_window):
    """Both halves are read at startup, and watching depends on the pair."""
    first = make_window()
    first._set_yield_to_notifications(True)
    first._set_lyrics_visible(False)
    first._save_settings()
    first._settings.sync()

    second = make_window()
    assert second._yield_to_notifications is True
    assert second._lyrics_visible is False
    assert second._yield_timer.isActive() is False


def test_shutdown_stops_watching_and_hands_the_opacity_back(
    make_window, monkeypatch
):
    """A poll landing mid-teardown would ask the window server about a
    window being destroyed."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._shutdown()
    assert window._yield_timer.isActive() is False
    assert window._yield_anim is None
    assert window._yield_level == 0.0
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_the_yield_is_never_written_into_the_saved_opacity(make_window, monkeypatch):
    """What gets persisted is what the user chose, not what a banner
    happened to be doing when the app quit."""
    window = make_window()
    put_in_the_way(window)
    window._set_opacity(0.7)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    window._save_settings()
    assert window._settings.value("window/opacity", type=float) == pytest.approx(0.7)


def test_only_one_place_writes_the_windows_opacity():
    """Three things scale the window now — the user's setting, a yield and a
    flight — and each of them used to call setWindowOpacity directly. That
    worked only because no two were ever true at once. Enforced as a source
    scan rather than trusted, because a fourth caller would pass every
    behavioural test above while quietly dropping the other two."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(w))
    callers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "setWindowOpacity"
            for inner in ast.walk(node)
        )
    }
    assert callers == {"_apply_window_opacity"}


def test_a_banner_leaves_a_window_nowhere_near_it_alone(make_window, monkeypatch):
    """THE 16.1 BUG, at the level the user met it. macOS reports the whole
    display for a banner in one corner, so before the region was narrowed
    this window faded for something it was nothing like."""
    window = make_window()
    put_out_of_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    assert window._yielding is False
    assert window._yield_anim is None
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


def test_dragging_into_the_way_starts_the_fade_at_the_next_poll(
    make_window, monkeypatch
):
    """The position is read on every poll, not cached, so moving the window
    under a banner that is already up is picked up without anything having to
    tell the layer that the window moved."""
    window = make_window()
    put_out_of_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    assert window._yielding is False

    put_in_the_way(window)
    window._check_notifications()
    assert window._yielding is True
    settle_yield(window)
    assert window.windowOpacity() == pytest.approx(n.YIELD_CEILING, abs=OPACITY_STEP)


def test_dragging_out_of_the_way_ends_the_fade(make_window, monkeypatch):
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is True

    put_out_of_the_way(window)
    window._check_notifications()
    settle_yield(window)
    assert window._yielding is False
    assert window.windowOpacity() == pytest.approx(window._opacity, abs=OPACITY_STEP)


# -- the polling rate follows what the window is doing ---------------------


def test_the_idle_rate_is_what_a_fresh_window_polls_at(make_window):
    window = make_window()
    assert window._yield_timer.interval() == int(n.POLL_SECONDS * 1000)


def test_the_rate_goes_up_the_moment_the_fade_starts(make_window, monkeypatch):
    """Before the animation, not after it: the short interval is wanted for
    the poll that lands DURING the fade, which is where a banner dismissed
    early would otherwise be missed for a full idle interval."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)

    window._check_notifications()
    assert window._yield_anim is not None, "still fading"
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)


def test_the_rate_goes_back_down_once_the_window_is_restored(
    make_window, monkeypatch
):
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)

    notifications_at(monkeypatch)
    window._check_notifications()
    # Still fast: the window is not back yet. Reading only the target put the
    # rate back here, while the window was still faint — so a second banner
    # inside that 260ms met the idle rate.
    assert window._yield_level > 0
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)

    settle_yield(window)
    assert window._yield_level == 0
    assert window._yield_timer.interval() == int(n.POLL_SECONDS * 1000)


def test_giving_the_opacity_back_also_gives_the_rate_back(make_window, monkeypatch):
    """Every path out of a fade returns both, so there is no way to be left
    polling three times as often as the layer needs for the rest of the
    session."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    assert window._yield_timer.interval() == int(n.YIELDED_POLL_SECONDS * 1000)

    window._stop_yield()
    assert window._yield_timer.interval() == int(n.POLL_SECONDS * 1000)


def test_the_timer_stays_running_across_a_rate_change(make_window, monkeypatch):
    """setInterval on a running QTimer restarts its countdown, which is fine
    once per change and would be a timer that never fires if it happened on
    every poll — hence the only-when-it-changes guard."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    assert window._yield_timer.isActive()

    window._check_notifications()
    assert window._yield_timer.isActive()
    settle_yield(window)
    assert window._yield_timer.isActive()


def test_a_repeat_poll_does_not_rewrite_the_interval(make_window, monkeypatch):
    """The guard itself. Three polls a second land inside every banner; each
    one calling setInterval would restart the countdown every time and the
    timer would never actually reach it."""
    window = make_window()
    put_in_the_way(window)
    window._set_yield_to_notifications(True)
    notifications_at(monkeypatch, DISPLAY_RECT)
    window._check_notifications()
    settle_yield(window)

    writes = []
    real = window._yield_timer.setInterval
    monkeypatch.setattr(
        window._yield_timer,
        "setInterval",
        lambda ms: writes.append(ms) or real(ms),
    )
    for _ in range(5):
        window._check_notifications()
    assert writes == []
