"""Per-app position memory: the map, the settling rule, and the two gates.

All of it pure, so none of this needs a display, a notification centre, or
a second application to switch to. What cannot be tested here — that
NSWorkspace actually calls back when the user changes apps — is verified
by hand against the real thing; see docs/per-app-position.md.
"""

TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce.app_positions import (
    ARRIVAL,
    GLOW_PEAK,
    GLOW_SECONDS,
    MAX_ENTRIES,
    REPEAT,
    SETTLE_SECONDS,
    UNKEYABLE,
    ActivationDebounce,
    AppPositions,
    display_label,
    glow_intensity,
    learn_refusal,
    may_acknowledge,
    may_learn,
    may_move,
    move_refusal,
    status_summary,
)

VSCODE = "com.microsoft.VSCode"
SAFARI = "com.apple.Safari"
NOTES = "com.apple.Notes"
OURS = "com.sottovoce.sottovoce"


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


def test_a_position_can_be_read_without_being_used():
    positions = AppPositions()
    positions.remember(VSCODE, 120, 340)
    assert positions.peek(VSCODE) == (120, 340)
    assert positions.peek(SAFARI) is None
    assert positions.peek(None) is None


def test_peeking_does_not_keep_an_entry_alive():
    """The one thing peek exists for. Opening the menu to look at what is
    remembered is not evidence the user still switches to that app; if it
    counted, the eviction order would describe where they have been looking
    rather than where they have been working."""
    positions = AppPositions(limit=3)
    for index, app in enumerate(("a", "b", "c")):
        positions.remember(app, index, index)

    positions.peek("a")  # merely looked at
    positions.remember("d", 9, 9)

    assert positions.peek("a") is None  # still the oldest, so still evicted


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


def test_a_name_is_stored_beside_the_position():
    """The map outlives the sessions that taught it: an app that is not
    running cannot be asked its name, and a list of bundle identifiers is
    a list nobody can read."""
    positions = AppPositions()
    positions.remember(VSCODE, 120, 340, "Code")
    assert positions.name_for(VSCODE) == "Code"

    restored = AppPositions.from_json(positions.to_json())
    assert restored.name_for(VSCODE) == "Code"
    assert restored.recall(VSCODE) == (120, 340)


def test_re_placing_an_app_without_a_name_keeps_the_one_it_had():
    """Not knowing what an app is called this time is not evidence that
    the name learned last time was wrong."""
    positions = AppPositions()
    positions.remember(VSCODE, 1, 1, "Code")
    positions.remember(VSCODE, 2, 2, None)
    assert positions.name_for(VSCODE) == "Code"
    assert positions.recall(VSCODE) == (2, 2)


def test_a_new_name_replaces_the_old_one():
    """Apps are renamed and localisations change; the last one seen wins."""
    positions = AppPositions()
    positions.remember(VSCODE, 1, 1, "Code")
    positions.remember(VSCODE, 1, 1, "Visual Studio Code")
    assert positions.name_for(VSCODE) == "Visual Studio Code"


def test_an_app_with_no_name_has_none_rather_than_an_empty_string():
    positions = AppPositions()
    positions.remember(VSCODE, 1, 1, "")
    assert positions.name_for(VSCODE) is None
    assert positions.name_for("never.seen") is None
    assert positions.name_for(None) is None


def test_a_map_saved_before_names_existed_still_loads():
    """Milestone 14 wrote three fields. Refusing that shape would cost the
    user every position they had, to gain a label."""
    positions = AppPositions.from_json('[["com.apple.Safari", 12, 34]]')
    assert positions.recall(SAFARI) == (12, 34)
    assert positions.name_for(SAFARI) is None


def test_a_stored_name_that_is_not_a_name_costs_only_its_own_entry():
    raw = '[["a", 1, 2, "Fine"], ["b", 3, 4, 99], ["c", 5, 6, null]]'
    positions = AppPositions.from_json(raw)
    assert positions.bundle_ids == ("a", "c")
    assert positions.name_for("a") == "Fine"
    assert positions.name_for("c") is None


def test_the_list_reads_most_recent_first():
    """The order a list should read in, and the reverse of the order things
    are evicted in."""
    positions = AppPositions()
    positions.remember(VSCODE, 1, 1, "Code")
    positions.remember(SAFARI, 2, 2, "Safari")
    positions.remember(NOTES, 3, 3, None)

    assert positions.listed() == (
        (NOTES, None),
        (SAFARI, "Safari"),
        (VSCODE, "Code"),
    )


def test_the_list_is_empty_with_nothing_learned():
    assert AppPositions().listed() == ()


def test_forgetting_one_app_leaves_the_rest():
    positions = AppPositions()
    positions.remember(VSCODE, 1, 1, "Code")
    positions.remember(SAFARI, 2, 2, "Safari")

    assert positions.forget(VSCODE) is True
    assert positions.forget(VSCODE) is False  # already gone
    assert positions.listed() == ((SAFARI, "Safari"),)


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


def test_an_announcement_says_which_of_the_three_it_was():
    """So the log can report what became of an activation without asking
    the debounce a second question and reconstructing the answer — a
    reconstruction is a copy of the rule, and a copy can disagree."""
    debounce = ActivationDebounce(0.4)
    assert debounce.observe(VSCODE, now=10.0) == ARRIVAL
    assert debounce.observe(VSCODE, now=10.1) == REPEAT
    assert debounce.observe(SAFARI, now=10.2) == ARRIVAL
    assert debounce.observe(None, now=10.3) == UNKEYABLE
    assert debounce.observe("", now=10.3) == UNKEYABLE


def test_being_asked_what_an_announcement_was_changes_nothing():
    """The return value is advisory: the settling behaviour must be exactly
    what it was before anything read it."""
    debounce = ActivationDebounce(0.4)
    debounce.observe(VSCODE, now=10.0)
    debounce.observe(None, now=10.1)  # UNKEYABLE, and not a cancellation
    assert debounce.pending == VSCODE
    assert debounce.settled(now=10.4) == VSCODE


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
    window moving would mean SottoVoce had become frontmost, which it is
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


# -- the gates, saying why -------------------------------------------------
#
# Implicit learning with no feedback is indistinguishable from a broken
# feature, so every refusal has to be nameable. The reasons are one rule
# phrased once: may_learn and may_move are derived from them, which is what
# stops a log line from claiming something the gate did not decide.


def test_every_refusal_to_learn_names_itself():
    assert learn_refusal(enabled=False, frontmost=VSCODE, own_bundle_id=OURS)
    assert learn_refusal(enabled=True, frontmost=None, own_bundle_id=OURS)
    assert learn_refusal(enabled=True, frontmost=OURS, own_bundle_id=OURS)
    assert learn_refusal(enabled=True, frontmost=VSCODE, own_bundle_id=OURS) is None


def test_a_press_that_moved_nothing_is_not_a_placement():
    """The learn glow, in the report's own words. Every press that misses
    a control lands on the window, every one of those ends a "drag" of
    zero pixels, and every one of those recorded a position and lit the
    glow that says so — so the app answered a press meant for a control by
    announcing that it had learned something. Learning is implicit, which
    is exactly why it may only follow an act that meant it."""
    assert learn_refusal(
        enabled=True, frontmost=VSCODE, own_bundle_id=OURS, moved=False
    ) == "the window was not moved"
    assert learn_refusal(
        enabled=True, frontmost=VSCODE, own_bundle_id=OURS, moved=True
    ) is None
    assert not may_learn(
        enabled=True, frontmost=VSCODE, own_bundle_id=OURS, moved=False
    )


def test_a_drag_still_learns_by_default():
    """The parameter defaults to True so that the callers that already
    know the window went somewhere — docking, a settled travel — say
    nothing extra, and only the one caller that cannot know has to."""
    assert learn_refusal(
        enabled=True, frontmost=VSCODE, own_bundle_id=OURS
    ) is None


def test_the_reasons_to_refuse_learning_are_told_apart():
    """Each names its own cause. A single "cannot" would leave the user
    with the same silence the reasons exist to break."""
    reasons = {
        learn_refusal(enabled=False, frontmost=VSCODE, own_bundle_id=OURS),
        learn_refusal(enabled=True, frontmost=None, own_bundle_id=OURS),
        learn_refusal(enabled=True, frontmost=OURS, own_bundle_id=OURS),
        learn_refusal(
            enabled=True, frontmost=VSCODE, own_bundle_id=OURS, moved=False
        ),
    }
    assert len(reasons) == 4


def test_may_learn_is_the_refusal_without_its_reason():
    """One rule, not two. If the gate and the explanation were separate
    code, a log line could disagree with what actually happened."""
    for enabled in (True, False):
        for front in (VSCODE, OURS, None, ""):
            for own in (OURS, None):
                allowed = may_learn(
                    enabled=enabled, frontmost=front, own_bundle_id=own
                )
                refusal = learn_refusal(
                    enabled=enabled, frontmost=front, own_bundle_id=own
                )
                assert allowed == (refusal is None)


ALLOWED = dict(enabled=True, visible=True, dragging=False, syncing=False, flying=False)


def test_every_refusal_to_move_names_itself():
    assert move_refusal(**{**ALLOWED, "enabled": False})
    assert move_refusal(**{**ALLOWED, "dragging": True})
    assert move_refusal(**{**ALLOWED, "syncing": True})
    assert move_refusal(**{**ALLOWED, "flying": True})
    assert move_refusal(**{**ALLOWED, "visible": False})
    assert move_refusal(**ALLOWED) is None


def test_the_reasons_to_refuse_moving_are_told_apart():
    reasons = {
        move_refusal(**{**ALLOWED, key: value})
        for key, value in (
            ("enabled", False),
            ("dragging", True),
            ("syncing", True),
            ("flying", True),
            ("visible", False),
        )
    }
    assert len(reasons) == 5


def test_the_window_is_not_moved_while_it_is_flying():
    """The journey to or from the menu bar item owns the window's position
    until it lands. Two animations of one window's position could only
    fight, and the loser would be whichever finished last."""
    assert not may_move(**{**ALLOWED, "flying": True})


def test_the_reason_reported_is_what_the_user_is_in_the_middle_of():
    """Several can hold at once — a hidden window during a sync pass, say.
    The one named is the most specific, because "the layer is off" would be
    a misleading answer to give someone who has just switched it on."""
    assert "drag" in move_refusal(
        **{**ALLOWED, "visible": False, "dragging": True, "syncing": True}
    )
    assert "sync" in move_refusal(
        **{**ALLOWED, "visible": False, "syncing": True}
    )
    assert "menu bar" in move_refusal(
        **{**ALLOWED, "visible": False, "flying": True}
    )


def test_may_move_is_the_refusal_without_its_reason():
    for enabled in (True, False):
        for visible in (True, False):
            for dragging in (True, False):
                for syncing in (True, False):
                    for flying in (True, False):
                        state = dict(
                            enabled=enabled,
                            visible=visible,
                            dragging=dragging,
                            syncing=syncing,
                            flying=flying,
                        )
                        assert may_move(**state) == (move_refusal(**state) is None)


# -- saying what is known --------------------------------------------------


def test_the_summary_names_the_count_and_the_app_in_front():
    """Both halves, because there are two ways to doubt an implicit
    feature: whether anything has been learned at all, and whether THIS
    app — the one a drag would record against — is one of them."""
    summary = status_summary(
        count=3, frontmost=SAFARI, frontmost_name="Safari", placed=True
    )
    assert "3 apps remembered" in summary
    assert "Safari is placed" in summary


def test_the_summary_says_when_the_app_in_front_has_no_position():
    summary = status_summary(
        count=3, frontmost=SAFARI, frontmost_name="Safari", placed=False
    )
    assert "3 apps remembered" in summary
    assert "Safari not placed yet" in summary


def test_the_summary_carries_no_coordinates():
    """They answered a question nobody asks of a menu: a number pair cannot
    be checked by eye, and the window is already sitting at it. They stay in
    the DEBUG log, where a reader is comparing them with something."""
    summary = status_summary(
        count=1, frontmost=SAFARI, frontmost_name="Safari", placed=True
    )
    assert not any(character.isdigit() for character in summary.split("·")[1])


def test_the_summary_uses_the_name_a_person_would_use():
    """The identifier was the first answer and it was the wrong one:
    precision is what the log is for, and com.microsoft.VSCode makes a
    reader translate before they can answer the question they came with."""
    summary = status_summary(
        count=1, frontmost=VSCODE, frontmost_name="Code", placed=True
    )
    assert "Code is placed" in summary
    assert VSCODE not in summary


def test_the_summary_falls_back_to_the_identifier_with_no_name():
    """An app never seen running has no name to show, and its identifier
    beats a blank."""
    summary = status_summary(count=1, frontmost=VSCODE, frontmost_name=None, placed=True)
    assert VSCODE in summary


def test_an_empty_map_is_said_out_loud():
    """The state a user cannot tell from a broken feature, so it is the one
    that most needs words. Still names the app in front: that is the app
    the next drag would record against."""
    summary = status_summary(
        count=0, frontmost=SAFARI, frontmost_name="Safari", placed=False
    )
    assert "No positions remembered" in summary
    assert "Safari" in summary


def test_one_app_is_not_one_apps():
    assert "1 app remembered" in status_summary(
        count=1, frontmost=SAFARI, frontmost_name="Safari", placed=True
    )


def test_the_summary_admits_when_it_does_not_know_what_is_in_front():
    """Off macOS, or before any activation has been announced. Saying so
    beats a blank half-sentence."""
    for absent in (None, ""):
        summary = status_summary(count=2, frontmost=absent, placed=False)
        assert "2 apps remembered" in summary
        assert "unknown" in summary


def test_the_summary_is_one_line_whatever_it_says():
    """It is a menu entry. A second line would be shown as a box glyph."""
    for state in (
        dict(count=0, frontmost=None, placed=False),
        dict(count=1, frontmost=SAFARI, frontmost_name="Safari", placed=False),
        dict(count=50, frontmost=VSCODE, placed=True),
    ):
        assert "\n" not in status_summary(**state)


# -- what an app is called -------------------------------------------------


def test_an_app_is_labelled_by_its_name():
    assert display_label(VSCODE, "Code") == "Code"


def test_an_app_with_no_name_is_labelled_by_its_identifier():
    """The old behaviour, kept exactly, for an app never seen running."""
    assert display_label(VSCODE, None) == VSCODE
    assert display_label(VSCODE, "") == VSCODE


def test_an_app_with_neither_is_labelled_with_nothing_rather_than_None():
    assert display_label(None, None) == ""


# -- the acknowledgement ---------------------------------------------------


def test_the_glow_starts_and_ends_at_nothing():
    """So the hairline leaves and returns to exactly the album's own colour,
    with no step at either boundary — borrowed, not taken."""
    assert glow_intensity(0.0) == 0.0
    assert glow_intensity(1.0) == 0.0
    assert glow_intensity(-0.5) == 0.0
    assert glow_intensity(2.0) == 0.0


def test_the_glow_rises_and_falls_within_one_property():
    """One property with the whole shape in it, like the line change's
    signed progress, rather than two animations handing over."""
    rising = [glow_intensity(phase / 10) for phase in range(1, 6)]
    falling = [glow_intensity(phase / 10) for phase in range(5, 10)]
    assert rising == sorted(rising)
    assert falling == sorted(falling, reverse=True)
    assert glow_intensity(0.5) == pytest.approx(GLOW_PEAK)


def test_the_glow_reaches_the_warm_colour_completely():
    """It did not, and that was the whole of why it went unnoticed: at 0.85
    the amber was still being averaged with a hairline that is nearly
    transparent at rest, and what arrived was a slightly warmer grey. The
    restraint is in the colour's own alpha and in how briefly it is there,
    not in stopping short of it."""
    peak = max(glow_intensity(phase / 100) for phase in range(101))
    assert peak == pytest.approx(GLOW_PEAK)
    assert GLOW_PEAK == 1.0


def test_the_first_acknowledgement_is_always_allowed():
    assert may_acknowledge(now=100.0, last=None)


def test_a_second_acknowledgement_inside_the_first_is_refused():
    """One per gesture. A glow starting inside a glow reads as a flicker
    rather than as two answers — and it is what makes a release delivered
    twice harmless."""
    assert not may_acknowledge(now=100.0, last=100.0)
    assert not may_acknowledge(now=100.0 + GLOW_SECONDS / 2, last=100.0)


def test_a_later_drag_is_acknowledged_again():
    """A hair past the gap rather than exactly on it: 100.0 + 0.52 - 100.0
    is 0.519999999999996 in binary floating point, so an exact-boundary
    assertion would be testing the arithmetic rather than the rule. Two
    monotonic clock readings never land on it either."""
    assert may_acknowledge(now=100.0 + GLOW_SECONDS + 0.001, last=100.0)
    assert may_acknowledge(now=200.0, last=100.0)


def test_the_acknowledgement_is_brief():
    """Short and quiet: the window is ambient, and this is a confirmation
    rather than an event."""
    assert 0.2 <= GLOW_SECONDS <= 1.0
