"""Where the window belongs: per-app memory, the acknowledgement, and
each layout's own place and size.

Learning is implicit, which is why it may only follow an act that meant
it. The layer off removes the work rather than the output, and the place
kept is where the window belongs rather than wherever a dodge has it
standing.
"""

TIER = "qt"  # a real window, driven by calling its own methods

from unittest.mock import patch

import pytest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from sottovoce import appearance as ap
from sottovoce import frontmost
from sottovoce import menu as m
from sottovoce import proximity
from sottovoce import window as w

from helpers import (
    APP,
    NAMES,
    PLAIN,
    RED_COVER,
    SAFARI,
    SYNCED,
    VSCODE,
    activate,
    arrive,
    art_snapshot,
    finish_fit,
    finish_move,
    finish_reveal,
    go_compact,
    hover,
    load,
    press_at,
    release,
    settle,
    settle_tint,
    snapshot,
    visible_keys,
    with_mode,
)


def remembering(make_window, frontmost_app=VSCODE):
    """A window with the layer on and an app in front of it."""
    window = make_window()
    window.show()
    window._set_remember_position(True)
    window._frontmost = frontmost_app
    window._frontmost_name = NAMES.get(frontmost_app)
    APP.processEvents()
    return window


def end_a_drag(window, x, y):
    """What the user finishing a drag looks like from here: the window is
    somewhere new, and the release handler runs."""
    window.move(x, y)
    window._drag_offset = QPoint(0, 0)  # as mousePressEvent would have left it
    window.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(1, 1),
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    APP.processEvents()  # the learn is deferred by one tick, like the nudge


def test_the_layer_is_off_by_default(make_window):
    """Every layer off must equal the app before this existed — including
    not observing anything."""
    window = make_window()
    assert window._remember_position is False
    assert window._menu.is_checked(m.REMEMBER_POSITION) is False
    assert not window._watcher.active


def test_nothing_is_learned_while_the_layer_is_off(make_window):
    window = make_window()
    window.show()
    window._frontmost = VSCODE
    end_a_drag(window, 300, 200)
    assert len(window._positions) == 0


def test_finishing_a_drag_records_the_position_for_the_frontmost_app(make_window):
    """Learning is implicit: there is no save action, only the drag the
    user was going to do anyway."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    assert window._positions.recall(VSCODE) == (300, 200)


def test_finishing_a_resize_records_it_too(make_window):
    window = remembering(make_window)
    window.move(120, 90)
    window._resize_edges = Qt.Edge.RightEdge
    window.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(1, 1),
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    APP.processEvents()
    assert window._positions.recall(VSCODE) == (120, 90)


def test_each_app_keeps_its_own_position(make_window):
    """The acceptance test, in miniature: place it one way here, another
    way there."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    window._frontmost = SAFARI
    end_a_drag(window, 60, 480)

    assert window._positions.recall(VSCODE) == (300, 200)
    assert window._positions.recall(SAFARI) == (60, 480)


def test_the_window_moves_to_a_remembered_position_on_activation(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window.move(10, 10)

    activate(window, VSCODE)
    window._settle_timer.stop()  # fire the settle by hand, deterministically
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()
    finish_move(window)

    assert window.pos() == QPoint(300, 200)


def test_a_timer_that_fires_early_asks_again_rather_than_dropping_the_move(
    make_window,
):
    """Found live, not here: QTimer fired at 390ms against the 400ms rule,
    the debounce said "not yet", and because the timer is single-shot the
    arrival was dropped for good. The rule stays authoritative and the
    timer re-arms for what is left."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    activate(window, VSCODE)
    window._settle_timer.stop()
    window._apply_settled_app()  # asked immediately: far too early

    assert window._move_anim is None, "moved before the app had settled"
    assert window._settle_timer.isActive(), "the arrival was dropped"
    assert window._debounce.pending == VSCODE

    # And when it is genuinely due, the same arrival still lands.
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()
    finish_move(window)
    assert window.pos() == QPoint(400, 300)


def test_an_app_with_no_remembered_position_leaves_the_window_alone(make_window):
    """Doing nothing is the right answer: a default would move the window
    somewhere the user never put it."""
    window = remembering(make_window)
    window.move(123, 456)

    settle(window, "com.apple.Notes")
    finish_move(window)

    assert window.pos() == QPoint(123, 456)


def test_the_move_is_animated_rather_than_a_teleport(make_window):
    """Consistent with the motion work in 13: eased, and sine rather than
    cubic."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    settle(window, VSCODE)

    assert window._move_anim is not None
    assert window._move_anim.duration() == w._MOVE_MS
    assert window._move_anim.easingCurve().type() == w._MOVE_CURVE
    assert window.pos() != QPoint(400, 300)  # not there yet
    finish_move(window)
    assert window.pos() == QPoint(400, 300)


def test_a_second_activation_mid_travel_retargets_from_where_it_got_to(make_window):
    """The same rule the tint cross-fade follows: the user is looking at
    where it is, not at where it was going."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window._frontmost = SAFARI
    end_a_drag(window, 80, 500)
    window.move(10, 10)

    settle(window, VSCODE)
    first = window._move_anim
    first.setCurrentTime(first.duration() // 2)
    APP.processEvents()
    midway = window.pos()

    settle(window, SAFARI)
    assert window._move_anim is not first
    assert window._move_anim.startValue() == midway
    finish_move(window)
    assert window.pos() == QPoint(80, 500)


def test_cmd_tabbing_through_apps_does_not_move_the_window(make_window):
    """The debounce, on the path a real Cmd-Tab sweep takes: several
    activations in quick succession, none of them settled."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    for app in (VSCODE, SAFARI, "com.apple.Notes", VSCODE):
        activate(window, app)

    assert window._move_anim is None
    assert window.pos() == QPoint(10, 10)
    assert window._settle_timer.isActive()  # still waiting for things to stop


@pytest.mark.parametrize("obstacle", ("dragging", "syncing", "hidden"))
def test_the_window_is_not_moved_while_the_user_is_busy(make_window, obstacle):
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    if obstacle == "dragging":
        window._drag_offset = QPoint(5, 5)
    elif obstacle == "syncing":
        load(window, PLAIN, track_id="t2")
        window._frontmost = VSCODE
        window._begin_sync()
        window.move(10, 10)
    else:
        window._set_lyrics_visible(False)

    settle(window, VSCODE)
    finish_move(window)

    assert window.pos() == QPoint(10, 10)


def test_a_remembered_position_is_still_clamped_on_arrival(make_window):
    """The screen it was learned on may be gone. A remembered position is
    not a licence to put the window somewhere unreachable."""
    window = remembering(make_window)
    window._positions.remember(VSCODE, 100_000, 100_000)

    settle(window, VSCODE)
    finish_move(window)

    available = window._available_geometry()
    assert window.pos().x() < available.right()
    assert window.pos().y() < available.bottom()


def test_switching_the_layer_off_stops_observing_and_moving(make_window):
    """Off means off: the subscription goes, not just the acting on it."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window.move(10, 10)

    window._set_remember_position(False)

    assert not window._watcher.active
    assert not window._settle_timer.isActive()
    settle(window, VSCODE)
    finish_move(window)
    assert window.pos() == QPoint(10, 10)


def test_switching_it_off_keeps_what_it_learned(make_window):
    """So that turning it back on does not start from nothing — and so the
    forget entry still has something to clear."""
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    window._set_remember_position(False)
    assert window._positions.recall(VSCODE) == (400, 300)


def test_forgetting_clears_the_map_and_the_setting(make_window):
    window = remembering(make_window)
    end_a_drag(window, 400, 300)
    assert m.FORGET_POSITIONS in visible_keys(window)

    window._menu.trigger(m.FORGET_POSITIONS)

    assert len(window._positions) == 0
    assert m.FORGET_POSITIONS not in visible_keys(window)
    assert window._settings.value("window/app_positions") == "[]"


def test_the_layer_and_the_map_are_persisted_and_restored(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._save_settings()
    window._settings.sync()

    reopened = make_window()
    assert reopened._remember_position is True
    assert reopened._positions.recall(VSCODE) == (300, 200)
    assert reopened._menu.is_checked(m.REMEMBER_POSITION) is True


def test_a_corrupt_stored_map_does_not_stop_the_app_starting(make_window):
    window = make_window()
    window._settings.setValue("window/app_positions", "{not json")
    window._settings.setValue("window/remember_position", True)
    window._settings.sync()

    reopened = make_window()
    assert len(reopened._positions) == 0
    assert reopened._remember_position is True


def test_the_window_never_learns_a_position_against_itself(make_window):
    """It should never be frontmost — the window is unfocusable and the
    app is an accessory — but an entry keyed on us could never be recalled
    and would evict a real one."""
    window = remembering(make_window)
    window._own_bundle_id = "com.sottovoce.sottovoce"
    window._frontmost = "com.sottovoce.sottovoce"

    end_a_drag(window, 300, 200)

    assert len(window._positions) == 0


def test_our_own_activation_does_not_become_the_frontmost_app(make_window):
    """Opening the menu bar item can bring an accessory app forward. Taken
    at face value that would replace the app the user is working in with
    ourselves, after which every drag would be refused by the self-filter
    and nothing would be learned for no visible reason. The window follows
    the last app that was not us."""
    window = remembering(make_window, frontmost_app=VSCODE)
    window._own_bundle_id = "com.sottovoce.sottovoce"

    activate(window, "com.sottovoce.sottovoce")

    assert window._frontmost == VSCODE
    end_a_drag(window, 300, 200)
    assert window._positions.recall(VSCODE) == (300, 200)


def test_our_own_activation_does_not_disturb_an_app_that_is_settling(make_window):
    """It is dropped before the debounce, not through it: an arrival that
    was almost due must not be restarted, or reaching for the menu bar
    would cost the move the user was waiting for."""
    window = remembering(make_window)
    window._own_bundle_id = "com.sottovoce.sottovoce"
    window._positions.remember(SAFARI, 400, 300)
    window.move(10, 10)

    activate(window, SAFARI)
    activate(window, "com.sottovoce.sottovoce")

    assert window._debounce.pending == SAFARI
    window._settle_timer.stop()
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()
    finish_move(window)
    assert window.pos() == QPoint(400, 300)


def test_asking_who_is_in_front_refuses_ourselves(make_window):
    """The same rule at the other door: switching the layer on from a menu
    opened over our own window must not seed the frontmost app as us, or the
    first drag would be refused for a reason the user cannot act on."""
    window = make_window()
    window._own_bundle_id = "com.sottovoce.sottovoce"
    ourselves = frontmost.AppIdentity("com.sottovoce.sottovoce", "SottoVoce")
    with patch.object(w.frontmost, "current_app", return_value=ourselves):
        window._set_remember_position(True)
    assert window._frontmost is None

    with patch.object(
        w.frontmost, "current_app", return_value=frontmost.AppIdentity(VSCODE, "Code")
    ):
        window._set_remember_position(False)
        window._set_remember_position(True)
    assert (window._frontmost, window._frontmost_name) == (VSCODE, "Code")


def test_the_menu_says_what_has_been_learned_and_where_we_are(make_window):
    """The feedback half of the milestone: learning is implicit, so without
    this the only evidence it works is the window happening to move."""
    window = remembering(make_window)
    window._refresh_menu()  # what opening the menu does
    assert m.POSITION_STATUS in visible_keys(window)
    assert "No positions remembered" in window._menu.label(m.POSITION_STATUS)
    assert "Code not placed yet" in window._menu.label(m.POSITION_STATUS)

    end_a_drag(window, 300, 200)

    assert "1 app remembered" in window._menu.label(m.POSITION_STATUS)
    assert "Code is placed" in window._menu.label(m.POSITION_STATUS)


def test_the_readout_names_an_app_it_only_knows_from_the_map(make_window):
    """The name is stored beside the position precisely so an app that is
    not running — and cannot be asked what it is called — is still
    readable in the menu."""
    window = remembering(make_window)
    window._positions.remember(SAFARI, 10, 20, "Safari")
    window._frontmost, window._frontmost_name = SAFARI, None

    window._refresh_menu()

    assert "Safari is placed" in window._menu.label(m.POSITION_STATUS)


def test_the_readout_falls_back_to_the_identifier_with_no_name(make_window):
    """An app never seen running and never placed has no name anywhere.
    Its identifier beats a blank."""
    window = remembering(make_window)
    window._frontmost, window._frontmost_name = "com.unknown.app", None

    window._refresh_menu()

    assert "com.unknown.app" in window._menu.label(m.POSITION_STATUS)


def test_the_readout_is_a_readout_and_not_a_control(make_window):
    """It is the one entry in the model that is neither a switch nor a
    command, and the kind is what nsmenu.py disables it from. Asserted on
    the model rather than on a menu item, because the claim is about what
    the entry IS."""
    window = remembering(make_window)
    assert m.ENTRIES[m.POSITION_STATUS].kind == m.READOUT
    assert window._menu.has_handler(m.POSITION_STATUS) is False
    window._menu.trigger(m.POSITION_STATUS)  # lands nowhere, and does not raise


def test_the_readout_carries_another_apps_name_and_stays_where_it_is(make_window):
    """The only entry whose text the app does not write: it carries another
    app's name — "System Settings" now that names are shown, and the
    identifier when there is no name. It used to need QAction.MenuRole.NoRole
    to survive that, because Qt's text heuristic matches substrings and
    would have moved "com.apple.systempreferences" into the application
    menu: a diagnostic that disappears when you go to read it. A native
    NSMenuItem has no such heuristic, so the hazard went with the QMenu."""
    window = remembering(make_window)

    for bundle_id, name in (
        ("com.apple.systempreferences", None),
        ("com.apple.systempreferences", "System Settings"),
    ):
        window._frontmost, window._frontmost_name = bundle_id, name
        window._refresh_menu()
        assert (name or bundle_id) in window._menu.label(m.POSITION_STATUS)


def test_the_readout_follows_the_frontmost_app(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    activate(window, SAFARI)
    window._refresh_menu()

    status = window._menu.label(m.POSITION_STATUS)
    assert "Safari not placed yet" in status
    assert "1 app remembered" in status  # what is known has not changed


def test_reading_the_menu_is_not_using_a_position(make_window):
    """peek, not recall. A glance must not refresh recency, or the eviction
    order would describe where the user has been looking."""
    window = remembering(make_window)
    window._positions = w.AppPositions(limit=2)
    window._positions.remember(VSCODE, 1, 1)
    window._positions.remember(SAFARI, 2, 2)
    window._frontmost = VSCODE

    window._refresh_menu()
    window._positions.remember("com.apple.Notes", 3, 3)

    assert window._positions.peek(VSCODE) is None  # still the oldest


def test_the_learned_name_reaches_the_map(make_window):
    """Learned from the activation that brought the app forward, so the map
    can name it long after it has quit."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    assert window._positions.name_for(VSCODE) == "Code"
    assert '"Code"' in window._settings.value("window/app_positions")


# -- the list of remembered apps ------------------------------------------


def readout_rows(window):
    """The app rows, which are all of them: the list is a readout."""
    return list(window._menu.rows(m.POSITION_LIST))


def row_text(row):
    """The name a row shows."""
    labels = [row.label]
    return labels[0] if labels else ""


def listed_apps(window):
    return [row_text(action) for action in readout_rows(window)]


def test_the_remembered_apps_menu_lists_them_by_name(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._frontmost, window._frontmost_name = SAFARI, "Safari"
    end_a_drag(window, 40, 60)

    window._rebuild_positions_menu()

    assert listed_apps(window) == ["Safari", "Code"]  # most recently used first


def test_the_remembered_apps_menu_is_rebuilt_from_the_map(make_window):
    """The entries ARE the map, and the map changes without the menu being
    involved — so it is assembled on opening rather than kept in step. That
    is also why it is the one menu here whose contents are rebuilt at all."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._rebuild_positions_menu()
    assert listed_apps(window) == ["Code"]

    window._positions.forget_all()
    window._rebuild_positions_menu()

    assert listed_apps(window) == []


def test_the_list_is_a_readout_with_nothing_to_click(make_window):
    """Per-app forget was removed rather than kept. Re-dragging the window
    in an app overwrites its position, so forgetting one app can only mean
    "stop moving the window for this one" — which is not a thing anybody
    wants for one app while wanting it for the others. Forget-all covers
    the wish that is real."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    window._rebuild_positions_menu()

    rows = readout_rows(window)
    assert rows
    for row in rows:
        assert isinstance(row, m.Row)
    # A row is DATA and carries no handler, which is what per-app forget
    # being removed MEANS: there is nothing for a click to reach. The map is
    # untouched by anything the list can do.
    before = window._positions.listed()
    assert window._menu.has_handler(m.POSITION_LIST) is False
    assert window._positions.listed() == before
    assert m.FORGET_POSITIONS in visible_keys(window)


def test_the_rows_are_not_disabled_so_macos_draws_them_normally(make_window):
    """THE 15.1 FIX, carried into the native menu. They were disabled
    QActions when per-app forget was removed, and macOS greys a disabled
    item — so four remembered apps read as four things that were unavailable
    rather than as four facts.

    The answer was a QWidgetAction and is now an NSMenuItem with a view, for
    exactly the same reason: an attributed title with an explicit labelColor
    does NOT help, because AppKit dims a disabled item when it draws it
    whatever the string asked for (measured, on a real menu). The kind is
    what carries the claim, so it is what is asserted here; the brightness
    itself is a question about pixels and is verified by hand.
    """
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._rebuild_positions_menu()

    assert readout_rows(window)
    assert m.ENTRIES[m.POSITION_LIST].kind == m.ROWS
    # The one entry that is neither: a readout the app disables on purpose,
    # because one grey line among ticked entries reads as a note.
    assert m.ENTRIES[m.POSITION_STATUS].kind == m.READOUT


def test_forgetting_everything_takes_the_list_away_with_it(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    assert m.POSITION_LIST in visible_keys(window)

    window._menu.trigger(m.FORGET_POSITIONS)

    assert m.POSITION_LIST not in visible_keys(window)
    assert m.FORGET_POSITIONS not in visible_keys(window)


def test_an_app_with_no_icon_is_still_listed(make_window):
    """Off macOS there are no icons at all, and on it an app can have been
    uninstalled since. A name with no face still reads."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)

    window._rebuild_positions_menu()

    entry = next(row for row in readout_rows(window) if row.label == "Code")
    # No icon at all: the offscreen platform has no AppKit to ask for one.
    assert entry.icon is None


def test_the_readout_asks_for_an_icon_only_when_the_app_changes(make_window):
    """_refresh_menu runs on every render — three times a second — and
    drawing an icon is two calls into AppKit."""
    window = remembering(make_window)
    asked = []

    with patch.object(w, "_MENU_ICON_POINTS", 16), patch.object(
        window, "_app_icon", side_effect=lambda key: asked.append(key)
    ):
        window._refresh_menu()
        window._refresh_menu()
        window._refresh_menu()
        assert asked == [VSCODE]

        activate(window, SAFARI)
        window._refresh_menu()
        window._refresh_menu()
        assert asked == [VSCODE, SAFARI]


# -- the acknowledgement ---------------------------------------------------


def finish_glow(window):
    """Run the acknowledgement to its end without waiting it out."""
    if window._glow_anim is not None:
        window._glow_anim.setCurrentTime(window._glow_anim.duration())
    APP.processEvents()


def test_a_learned_position_is_acknowledged_on_the_window(make_window):
    """The gesture ends in silence otherwise, which is this feature's
    oldest problem restated: nothing on screen distinguishes a drag that
    was learned from a drag that was not."""
    window = remembering(make_window)
    assert window._glow == 0.0

    end_a_drag(window, 300, 200)

    assert window._glow_anim is not None
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()
    assert window._glow > 0.0


def test_nothing_is_acknowledged_when_nothing_was_learned(make_window):
    """A refused drag must look like a refused drag. The glow says "that
    counted", so it may only appear where something was recorded."""
    window = make_window()  # the layer is off
    window.show()
    window._frontmost = VSCODE
    end_a_drag(window, 300, 200)
    assert window._glow_anim is None
    assert window._glow == 0.0


def test_the_edge_is_handed_back_exactly(make_window):
    """Borrowed, not taken. The album's own edge before and after, to the
    channel, with a cover in hand so there is something to give back."""
    window = remembering(make_window)
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    before = window._painted_border()
    assert before == ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).border

    end_a_drag(window, 300, 200)
    # Halfway, because the shape starts and ends at nothing: at the moment
    # the drag lands there is deliberately no glow yet to see.
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()
    assert window._painted_border() != before  # borrowed
    finish_glow(window)

    assert window._painted_border() == before  # and returned
    assert window._glow == 0.0
    assert window._current_border() == before  # never written into the tint


def test_a_cover_arriving_mid_glow_is_not_captured_with_the_glow_in_it(make_window):
    """The reason the glow is a paint-time mix and not part of the tint
    state: a cross-fade beginning mid-acknowledgement would otherwise take
    a warmed edge as its start and keep some of it for good."""
    window = remembering(make_window)
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    end_a_drag(window, 300, 200)
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()
    window._on_artwork_ready("t1", RED_COVER)  # a cover lands mid-glow
    settle_tint(window)
    finish_glow(window)

    assert window._painted_border() == ap.tinted(
        ap.DARK, RED_COVER, ap.Appearance.DARK
    ).border


def test_the_glow_does_not_fire_twice_for_one_gesture(make_window):
    """One drag is one thing the user did. A second glow starting inside
    the first would read as a flicker rather than as two answers — and a
    release delivered twice is the same case."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    first = window._glow_anim

    window._learn_position()  # as a second release would
    window._learn_position()

    assert window._glow_anim is first


def test_a_later_drag_is_acknowledged_again(make_window):
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    finish_glow(window)
    window._glow_at -= w.GLOW_SECONDS  # as the clock would have moved on

    end_a_drag(window, 120, 90)

    assert window._glow_anim is not None


def test_the_acknowledgement_leaves_the_line_transition_alone(make_window):
    """Different surfaces, different animations: the glow is a colour on
    the hairline, the line change is an effect on the text. Neither may
    stop the other."""
    window = remembering(make_window)
    load(window, SYNCED, track_id="t9")
    window._on_position_update(snapshot(position=0.0, track_id="t9"))
    fx_before = window._current_fx.progress

    end_a_drag(window, 300, 200)
    window._glow_anim.setCurrentTime(window._glow_anim.duration() // 2)
    APP.processEvents()

    assert window._current_fx.progress == fx_before
    assert window._glow > 0.0


def test_the_readout_goes_with_the_layer(make_window):
    """It names the frontmost app, and with the layer off nothing is
    watching which app that is."""
    window = remembering(make_window)
    end_a_drag(window, 300, 200)
    window._set_remember_position(False)
    assert m.POSITION_STATUS not in visible_keys(window)
    assert m.FORGET_POSITIONS in visible_keys(window)  # the map is still clearable


def test_shutdown_stops_observing(make_window):
    """NSWorkspace holds a block that moves a window being torn down —
    the same hazard as the hotkey, released beside it."""
    window = remembering(make_window)
    window._shutdown()
    assert not window._watcher.active
    assert not window._settle_timer.isActive()


# -- a press that missed is not a placement --------------------------------
#
# Reported as the loop and mic buttons intermittently doing nothing, with
# the press reaching the window's drag handler and producing the learn
# glow. REPRODUCED, by posting real clicks at the controls' own centres
# with the app backgrounded: in the compact strip a press 0ms or 30ms
# after the pointer arrived reached the window, one 60ms after it reached
# the control. The gap is the pointer poll — there is no hover event for a
# window that never activates, so the controls come out when the poll
# notices, up to _POINTER_POLL_MS later, and until they do there is
# nothing under the hand to press.
#
# Two things were wrong with that and they are separate. The controls
# arriving late is one. The other is what the app did INSTEAD, which was
# to record a position and say so.


@pytest.fixture
def learning(make_window):
    """A window with the position layer on, some other app in front, and
    a record of every position it decides to remember."""
    window = make_window()
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


def press_and_release(window, at=None, to=None):
    """A press and a release on the window, with the window moved between
    them if `to` is given — which is what a drag IS."""
    point = at if at is not None else QPoint(window.width() // 2, 8)
    window.mousePressEvent(press_at(window, window.mapToGlobal(point)))
    if to is not None:
        window.move(to)
    window.mouseReleaseEvent(release())
    APP.processEvents()


def test_a_press_that_moved_nothing_records_nothing(make_window, learning):
    """The learn glow in the report. A click that missed a control is not
    somebody saying where they want the window."""
    window, positions = learning
    press_and_release(window)
    assert positions == []


def test_a_drag_that_moved_the_window_still_records(make_window, learning):
    """The other half, and it has to be asserted here or the fix above is
    indistinguishable from turning the layer off."""
    window, positions = learning
    press_and_release(window, to=QPoint(320, 240))
    assert positions == [(320, 240)]


def test_a_resize_that_changed_nothing_records_nothing(make_window, learning):
    """A press on an edge that never moved is the same click by another
    name: the resize path sets no drag offset, so it needs the rule too."""
    window, positions = learning
    window.mousePressEvent(
        press_at(window, window.mapToGlobal(QPoint(window.width() - 2,
                                                   window.height() // 2)))
    )
    window.mouseReleaseEvent(release())
    APP.processEvents()
    assert positions == []


def test_a_press_on_the_window_says_where_the_pointer_is(make_window):
    """A press ON this window IS a pointer on this window, and it is a
    better answer than the poll: exact, and now. Without it the strip's
    controls come out when the next poll runs, which is what a hand that
    knows where the button is beats."""
    window = make_window()
    load(window, SYNCED)
    go_compact(window)
    hover(window, inside=False)
    assert window._reveal == 0.0
    assert window._loop_button.isVisibleTo(window) is False

    window.mousePressEvent(
        press_at(window, window.mapToGlobal(QPoint(window.width() // 2, 8)))
    )

    assert window._hovered is True
    assert window._reveal_to == 1.0
    finish_reveal(window)
    assert window._loop_button.isVisibleTo(window) is True


def test_it_does_not_claim_a_pointer_nothing_is_watching(make_window):
    """Whoever stops the poll also clears the hover, so a hover set with
    nothing left to clear it would hold the controls out for ever."""
    window = make_window()
    load(window, SYNCED)  # full layout, no proximity layer: no poll
    assert window._pointer_timer.isActive() is False

    window.mousePressEvent(
        press_at(window, window.mapToGlobal(QPoint(window.width() // 2, 8)))
    )

    assert window._hovered is False


# -- each layout keeps its own place and size ------------------------------
#
# The size half has been kept since milestone 20 and works; the POSITION
# half was queued and missing, and they are one fact. A strip is a quarter
# the height of the full layout and usually a different width, so coming
# back from it handed the full layout its old size at wherever the strip
# happened to be standing — which, after a song had fitted the strip's
# width, is not a place the full layout had ever been.


def test_each_layout_comes_back_to_its_own_place_and_size(make_window):
    window = make_window()
    load(window, SYNCED)
    window.resize(520, 340)
    window.move(300, 200)
    APP.processEvents()

    window._set_compact(True)
    window.move(120, 60)
    APP.processEvents()
    strip_height = window.height()

    window._set_compact(False)
    assert (window.width(), window.height()) == (520, 340)
    assert window.pos() == QPoint(300, 200)

    window._set_compact(True)
    assert window.pos() == QPoint(120, 60)
    assert window.height() == strip_height


def test_a_sync_pass_hands_the_strip_back_where_it_found_it(make_window):
    """The pass borrows the full layout without being asked to, so it is
    the one layout change the user did not make — and it may not teach
    either layout a new place."""
    window = make_window()
    load(window, SYNCED)
    window.resize(520, 340)
    window.move(300, 200)
    APP.processEvents()
    window._set_compact(True)
    window.move(120, 60)
    APP.processEvents()

    window._begin_sync()
    APP.processEvents()
    assert window.pos() == QPoint(300, 200)  # the full layout's own place
    window._leave_sync()
    APP.processEvents()

    assert window.pos() == QPoint(120, 60)


def test_a_layout_never_worn_is_left_where_the_resize_put_it(make_window):
    """The same answer _width_for gives about a width nobody has chosen:
    nothing to give back, so nothing is given. What decides the place is
    then the rule that already did — a width change is anchored on the
    window's centre — and this must not quietly become a second answer to
    where the window goes."""
    window = make_window()
    load(window, SYNCED)
    window.move(240, 160)
    APP.processEvents()
    centre = window.frameGeometry().center().x()

    window._set_compact(True)

    assert window.frameGeometry().center().x() == centre
    assert window.pos().y() == 160


def test_a_remembered_place_is_clamped_to_the_screen(make_window):
    """A position is a preference expressed on a display that may be gone
    by the time it is given back."""
    window = make_window()
    load(window, SYNCED)
    window._set_compact(True)
    APP.processEvents()
    window._shapes.remember(True, x=99_000, y=99_000)

    window._set_compact(False)
    window._set_compact(True)

    assert window._available_geometry().contains(window.frameGeometry())


def test_the_place_kept_is_where_the_window_belongs(make_window):
    """Not where a dodge has it standing. Four bugs in this project have
    been a temporary position written down as a permanent one."""
    window = with_mode(make_window(), proximity.DODGE)
    load(window, SYNCED)
    home = window.pos()
    arrive(window)
    assert window.pos() != home

    window._set_compact(True)

    assert window._shapes.recall(False).x == home.x()
    assert window._shapes.recall(False).y == home.y()


def test_a_song_choosing_the_width_does_not_choose_the_place(make_window):
    """_remember_shape declines the fitted width and keeps the position:
    the song has an opinion about how wide the strip is and none at all
    about where it sits."""
    window = make_window()
    load(window, SYNCED)
    window._set_compact(True)
    APP.processEvents()
    users_width = window._shapes.recall(True).width
    window.move(140, 90)
    window._fit_width(animate=False)
    finish_fit(window)

    window._set_compact(False)

    assert window._shapes.recall(True).width == users_width
    assert (window._shapes.recall(True).x, window._shapes.recall(True).y) == (140, 90)


def test_both_layouts_survive_a_restart(make_window):
    """The window is restored into whichever layout it was quit in, and
    the OTHER one has to come back with it — its shape is not in
    window/size, which holds only the layout that was on screen."""
    window = make_window()
    load(window, SYNCED)
    window.resize(520, 340)
    window.move(300, 200)
    APP.processEvents()
    window._set_compact(True)
    window.move(120, 60)
    APP.processEvents()
    window._save_settings()

    second = make_window()
    second._restore_settings()  # the factory resizes after construction
    APP.processEvents()
    second._set_compact(False)

    assert (second.width(), second.height()) == (520, 340)
    assert second.pos() == QPoint(300, 200)
