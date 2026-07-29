"""Per-app position memory: the map, the settling rule, and the two gates.

All of it pure, so none of this needs a display, a notification centre, or
a second application to switch to. What cannot be tested here — that
NSWorkspace actually calls back when the user changes apps — is verified
by hand against the real thing; see docs/per-app-position.md.
"""

import pytest

from lyrisync.app_positions import (
    MAX_ENTRIES,
    SETTLE_SECONDS,
    ActivationDebounce,
    AppPositions,
    may_learn,
    may_move,
)

VSCODE = "com.microsoft.VSCode"
SAFARI = "com.apple.Safari"
NOTES = "com.apple.Notes"
OURS = "com.lyrisync.lyrisync"


# -- the map ---------------------------------------------------------------


def test_an_unknown_app_has_no_position():
    """The answer that makes the feature safe: nothing remembered means
    the window is left exactly where it is, never moved to a default."""
    assert AppPositions().recall(VSCODE) is None


def test_a_position_survives_the_round_trip():
    positions = AppPositions()
    positions.remember(VSCODE, 120, 340)
    assert positions.recall(VSCODE) == (120, 340)


def test_the_newest_position_for_an_app_replaces_the_old_one():
    positions = AppPositions()
    positions.remember(VSCODE, 120, 340)
    positions.remember(VSCODE, 700, 80)
    assert positions.recall(VSCODE) == (700, 80)
    assert len(positions) == 1


def test_apps_are_remembered_independently():
    positions = AppPositions()
    positions.remember(VSCODE, 10, 20)
    positions.remember(SAFARI, 900, 500)
    assert positions.recall(VSCODE) == (10, 20)
    assert positions.recall(SAFARI) == (900, 500)


def test_an_app_with_no_identifier_is_not_remembered():
    positions = AppPositions()
    positions.remember("", 10, 20)
    assert len(positions) == 0


def test_forgetting_clears_everything():
    positions = AppPositions()
    positions.remember(VSCODE, 10, 20)
    positions.remember(SAFARI, 30, 40)
    positions.forget_all()
    assert len(positions) == 0
    assert positions.recall(VSCODE) is None


# -- the cap ---------------------------------------------------------------


def test_the_least_recently_used_entry_is_dropped_when_full():
    """Positions cost one drag to relearn, so a bound on the map is
    cheaper than any cleverness about which to keep."""
    positions = AppPositions(limit=3)
    for index, app in enumerate(("a", "b", "c")):
        positions.remember(app, index, index)
    positions.remember("d", 9, 9)

    assert len(positions) == 3
    assert positions.recall("a") is None  # the oldest went
    assert positions.recall("d") == (9, 9)


def test_recalling_an_app_keeps_it_alive():
    """Recency counts a use, not just a write. The app you switch to
    constantly but rarely re-place must not be the first evicted."""
    positions = AppPositions(limit=3)
    for index, app in enumerate(("a", "b", "c")):
        positions.remember(app, index, index)

    positions.recall("a")  # still in use
    positions.remember("d", 9, 9)

    assert positions.recall("a") == (0, 0)
    assert positions.recall("b") is None  # the genuinely stale one went


def test_re_remembering_refreshes_recency_too():
    positions = AppPositions(limit=2)
    positions.remember("a", 1, 1)
    positions.remember("b", 2, 2)
    positions.remember("a", 3, 3)
    positions.remember("c", 4, 4)
    assert positions.recall("a") == (3, 3)
    assert positions.recall("b") is None


def test_the_default_cap_is_well_past_a_working_day():
    assert MAX_ENTRIES >= 20


# -- persistence -----------------------------------------------------------


def test_the_map_round_trips_through_json():
    positions = AppPositions()
    positions.remember(VSCODE, 120, 340)
    positions.remember(SAFARI, -40, 900)

    restored = AppPositions.from_json(positions.to_json())

    assert restored.recall(VSCODE) == (120, 340)
    assert restored.recall(SAFARI) == (-40, 900)


def test_recency_order_survives_the_round_trip():
    """Stored as a list rather than an object so the eviction order is
    part of the format, not a property of whichever JSON reader loads it."""
    positions = AppPositions()
    for app in (VSCODE, SAFARI, NOTES):
        positions.remember(app, 1, 1)
    restored = AppPositions.from_json(positions.to_json())
    assert restored.bundle_ids == (VSCODE, SAFARI, NOTES)


def test_nothing_stored_yields_an_empty_map():
    for raw in ("", "   ", None, 42, [], {}):
        assert len(AppPositions.from_json(raw)) == 0


def test_unreadable_storage_yields_an_empty_map_rather_than_raising():
    """A settings file somebody edited by hand must not take the app down
    on launch."""
    assert len(AppPositions.from_json("{not json at all")) == 0
    assert len(AppPositions.from_json('{"an": "object"}')) == 0


def test_one_bad_entry_costs_only_itself():
    raw = '[["com.good.app", 1, 2], ["missing y", 3], ["bad", "x", "y"], 7]'
    positions = AppPositions.from_json(raw)
    assert positions.bundle_ids == ("com.good.app",)


def test_booleans_are_not_coordinates():
    """JSON true/false decode to Python ints, so a sloppy check would
    accept [id, true, false] as the point (1, 0)."""
    assert len(AppPositions.from_json('[["app", true, false]]')) == 0


def test_the_stored_cap_is_applied_on_load():
    raw = AppPositions.from_json(
        "[" + ",".join(f'["app{i}", {i}, {i}]' for i in range(10)) + "]",
        limit=4,
    )
    assert len(raw) == 4
    assert raw.recall("app9") == (9, 9)


# -- the settling rule -----------------------------------------------------


def test_an_arrival_is_not_acted_on_immediately():
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    assert debounce.settled(now=10.0) is None
    assert debounce.settled(now=10.39) is None


def test_an_app_that_stays_in_front_settles():
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    assert debounce.settled(now=10.4) == VSCODE


def test_a_settled_app_is_handed_over_once():
    """The window moves on arrival, not on every tick of whatever is
    asking."""
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    assert debounce.settled(now=10.5) == VSCODE
    assert debounce.settled(now=11.0) is None


def test_cmd_tabbing_through_apps_settles_only_on_the_last():
    """THE case this exists for: holding Cmd and stepping through six apps
    announces six activations, and acting on each would drag the window
    across the screen six times."""
    debounce = ActivationDebounce(0.4)
    for index, app in enumerate(("a", "b", "c", "d", "e")):
        debounce.observe(app, now=10.0 + index * 0.1)
        assert debounce.settled(now=10.0 + index * 0.1) is None

    # 10.4 is 0.4s after the first app, but only 0.0s after the last.
    assert debounce.settled(now=10.4) is None
    assert debounce.settled(now=10.8) == "e"


def test_a_repeat_announcement_does_not_restart_the_clock():
    """macOS can report the same activation more than once. A rule that
    reset on every announcement would let an app settle never."""
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    debounce.observe(VSCODE, now=10.2)
    debounce.observe(VSCODE, now=10.3)
    assert debounce.settled(now=10.4) == VSCODE


def test_switching_away_and_back_settles_again():
    """Leaving an app and returning to it is a new arrival, not a repeat."""
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    assert debounce.settled(now=10.4) == VSCODE

    debounce.observe(SAFARI, now=11.0)
    assert debounce.settled(now=11.4) == SAFARI

    debounce.observe(VSCODE, now=12.0)
    assert debounce.settled(now=12.4) == VSCODE


def test_an_app_with_no_identifier_never_settles():
    debounce = ActivationDebounce(0.4)
    debounce.observe(None, now=10.0)
    debounce.observe("", now=10.0)
    assert debounce.settled(now=99.0) is None


def test_cancelling_drops_what_was_settling():
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    debounce.cancel()
    assert debounce.settled(now=10.5) is None


def test_the_shipped_interval_sits_out_a_cmd_tab_sweep():
    assert 0.2 <= SETTLE_SECONDS <= 1.0


def test_how_much_longer_an_app_must_stay_in_front():
    """What a caller woken too early needs in order to ask again. Found
    live: a QTimer fired at 390ms against a 400ms rule, and without this
    the single-shot timer dropped the arrival for good."""
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    assert debounce.remaining(now=10.0) == pytest.approx(0.4)
    assert debounce.remaining(now=10.39) == pytest.approx(0.01)
    assert debounce.remaining(now=10.4) == 0.0
    assert debounce.remaining(now=99.0) == 0.0


def test_nothing_pending_has_nothing_remaining():
    assert ActivationDebounce(0.4).remaining(now=10.0) == 0.0


def test_asking_early_leaves_the_arrival_intact():
    """The property the re-arm depends on: being asked too soon must not
    consume the pending app."""
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    assert debounce.settled(now=10.39) is None
    assert debounce.pending == VSCODE
    assert debounce.settled(now=10.4) == VSCODE


# -- the gates -------------------------------------------------------------


def test_learning_needs_the_layer_on():
    assert not may_learn(enabled=False, frontmost=VSCODE, own_bundle_id=OURS)
    assert may_learn(enabled=True, frontmost=VSCODE, own_bundle_id=OURS)


def test_learning_needs_an_app_to_learn_against():
    assert not may_learn(enabled=True, frontmost=None, own_bundle_id=OURS)
    assert not may_learn(enabled=True, frontmost="", own_bundle_id=OURS)


def test_the_app_never_learns_a_position_against_itself():
    """An entry keyed on our own identifier could never be recalled — the
    window moving would mean LyriSync had become frontmost, which it is
    built never to do — and it would evict a real one to sit there."""
    assert not may_learn(enabled=True, frontmost=OURS, own_bundle_id=OURS)


def test_a_source_run_has_no_identity_to_collide_with():
    """No bundle, so no frontmost app can be us."""
    assert may_learn(enabled=True, frontmost=VSCODE, own_bundle_id=None)


def test_moving_needs_the_layer_on():
    assert not may_move(enabled=False, visible=True, dragging=False, syncing=False)
    assert may_move(enabled=True, visible=True, dragging=False, syncing=False)


def test_the_window_is_never_moved_out_from_under_the_hand():
    assert not may_move(enabled=True, visible=True, dragging=True, syncing=False)


def test_the_window_is_never_moved_during_a_sync_pass():
    """A pass is a rhythm game against a moving target; the tap bar
    sliding mid-pass would cost stamps."""
    assert not may_move(enabled=True, visible=True, dragging=False, syncing=True)


def test_a_hidden_window_is_not_moved():
    """Nothing to move, and moving it anyway would mean it comes back
    somewhere it was never seen to go."""
    assert not may_move(enabled=True, visible=False, dragging=False, syncing=False)
