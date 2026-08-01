"""One menu model, two ways in, and the item that carries it.

The menu bar item and the window's right-click share one `menu.py` model
and one native NSMenu. What is checked here is what only a real window
can answer: which entries a state makes visible, that a click changes the
state rather than the tick, and that the glyph in the menu bar follows
the song without the menu being opened.
"""

TIER = "qt"  # a real window, driven by calling its own methods

import logging
from pathlib import Path

import pytest

from PySide6.QtCore import QPoint

from sottovoce import login_item
from sottovoce import menu as m
from sottovoce import menubar as mb
from sottovoce import window as w
from sottovoce.player_monitor import PlaybackState

from helpers import (
    APP,
    KOREAN_SYNCED,
    PLAIN,
    SYNCED,
    land,
    load,
    snapshot,
    visible_keys,
)


# -- one menu, two ways in ------------------------------------------------


class FakeStatusItem:
    """The far side of nsmenu.StatusItem, so the menu bar item's whole life
    can be watched without putting a glyph on the developer's menu bar.

    A fake rather than the real thing under a stubbed door, because what
    every test here is about is what the WINDOW does with the item: which
    menu it hands over, when it redraws the glyph, and that it gives the
    item back at shutdown.
    """

    frame_rect = (1159.0, 1073.0, 38.0, 34.0)

    def __init__(self):
        self.tooltip = None
        self.images = []
        self.menu = None
        self.released = 0

    def create(self, tooltip=""):
        self.tooltip = tooltip
        return True

    def set_menu(self, menu):
        self.menu = menu

    def set_image(self, png, points):
        self.images.append((png, points))

    def frame(self):
        return self.frame_rect

    def release(self):
        self.released += 1


@pytest.fixture
def with_tray(monkeypatch):
    """Force the menu bar item into existence, as a fake.

    Nothing native can be built here: the door is answered with None for
    every window this directory makes, and the conftest guard would fail
    any test that reached around it. So the item the window builds is this one, and
    what is being checked is the window's half of the arrangement.
    """
    items = []

    def make_item():
        item = FakeStatusItem()
        items.append(item)
        return item

    monkeypatch.setattr(w.nsmenu, "StatusItem", make_item)
    return items


def right_click_at(window, x=10, y=10):
    """A context-menu event, as Qt would deliver one. Only the global
    position is read, which is the point that crosses into Cocoa."""

    class Event:
        def globalPos(self):
            return QPoint(x, y)

    return Event()


class FakeMenuView:
    """The far side of nsmenu.NativeMenu: what a drawn menu would be told."""

    def __init__(self):
        self.applied = 0
        self.rows = {}
        self.popups = []

    def apply(self, menu):
        self.applied += 1

    def set_rows(self, key, rows):
        self.rows[key] = rows

    def popup(self, x, y):
        self.popups.append((x, y))
        return True


def test_the_menu_bar_item_and_the_right_click_share_one_menu(with_tray, make_window):
    """The whole point of the milestone: one model, one drawing of it, two
    ways in. The item is handed the same view object the window pops up."""
    window = make_window()
    view = FakeMenuView()
    window._menu.attach(view)
    window._tray.set_menu(window._menu.view)

    assert window._tray.menu is view
    window.contextMenuEvent(right_click_at(window))
    assert view.popups, "the right-click did not open the shared menu"


def test_the_menu_bar_item_is_given_the_menu_and_a_glyph(with_tray, make_window):
    window = make_window()
    assert window._tray.tooltip == "SottoVoce"
    assert window._tray.images, "no glyph was ever drawn"
    png, points = window._tray.images[0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert points == mb.GLYPH_UNITS


def test_no_menu_bar_is_survivable(monkeypatch, make_window):
    """Nothing else may depend on the menu bar item existing. Forced rather
    than relying on the platform, so the path is exercised wherever the
    suite runs."""

    class NoMenuBar(FakeStatusItem):
        def create(self, tooltip=""):
            return False

    monkeypatch.setattr(w.nsmenu, "StatusItem", NoMenuBar)
    window = make_window()
    assert window._tray is None
    assert window._menu is not None
    window._refresh_menu()
    window._set_lyrics_visible(False)
    land(window)
    assert window.isVisible() is False


def test_the_item_is_given_back_at_shutdown(with_tray, make_window):
    """Qt used to own the item and destroy it with the widget. An item this
    app made is one this app has to remove, or it outlives the window that
    answers its menu."""
    window = make_window()
    item = window._tray
    window._shutdown()
    assert item.released == 1
    assert window._tray is None


def test_where_the_item_is_comes_back_in_qt_coordinates(with_tray, make_window):
    """The flight aims at the item, and the flight thinks in Qt rectangles.
    Cocoa measures up from the bottom of the primary screen; this is the one
    place that subtraction happens."""
    window = make_window()
    height = APP.primaryScreen().geometry().height()
    assert window._menubar_item_rect() == (1159, height - 1073 - 34, 38, 34)


def test_the_menu_is_built_once_and_never_rebuilt(make_window):
    """Structure is fixed; only visibility, check marks, chosen presets and
    two labels move. A rebuilt native menu bar item would flicker under the
    user while they were reading it."""
    window = make_window()
    view = FakeMenuView()
    window._menu.attach(view)
    load(window, SYNCED)
    load(window, PLAIN, track_id="t2")
    window._refresh_menu()
    assert window._menu.entries is m.MENU
    assert view.applied > 1, "the state was never pushed at the drawing"
    assert view.rows == {}, "something rebuilt a submenu without being asked"


def test_menu_visibility_follows_the_pure_gating(make_window):
    window = make_window()
    window._speech_available = False
    load(window, SYNCED)
    assert visible_keys(window) == m.visible_entries(
        has_korean_lyrics=False,
        speech_available=False,
        synced=True,
        sync_offered=False,
    )

    window._speech_available = True
    load(window, KOREAN_SYNCED, track_id="t2")
    window._view_model.romanisation_enabled = True
    window._refresh_menu()
    assert visible_keys(window) == m.visible_entries(
        has_korean_lyrics=True,
        speech_available=True,
        synced=True,
        sync_offered=False,
    )


def test_bare_menu_when_every_layer_is_dormant(make_window):
    window = make_window()
    window._speech_available = False
    window._refresh_menu()  # idle: no lyrics, nothing to sync
    assert visible_keys(window) == (
        m.SHOW_LYRICS,
        m.SEPARATOR_AFTER_SHOW,
        m.COMPACT,
        m.ALBUM_COLOUR,
        m.SEPARATOR_AFTER_WINDOW,
        m.POSITION_MENU,
        m.DOCK_TOP,
        m.SEPARATOR_AFTER_DOCK,
        m.REMEMBER_POSITION,
        m.SYSTEM_MENU,
        m.ALL_DESKTOPS,
        m.YIELD_NOTIFICATIONS,
        m.PROXIMITY,
        m.MENUBAR_ANIMATION,
        m.SEPARATOR_BEFORE_QUIT,
        m.QUIT,
    )


def test_sync_entry_label_switches_once_a_user_sync_exists(make_window):
    window = make_window()
    load(window, PLAIN)
    assert window._menu.is_visible(m.SYNC)
    assert window._menu.label(m.SYNC) == "Sync this song"

    window._provider.save_user_sync("t1", "[00:01.00] first line\n")
    window._refresh_menu()
    assert window._menu.label(m.SYNC) == "Re-sync this song"


def test_quit_is_visible_in_every_state(make_window):
    window = make_window()
    for lyrics in (None, PLAIN, SYNCED):
        if lyrics is not None:
            load(window, lyrics)
        window._refresh_menu()
        assert window._menu.is_visible(m.QUIT)


# -- toggles drive the same state from the menu ---------------------------


def test_toggling_from_the_menu_updates_state_and_settings(make_window):
    window = make_window()
    load(window, KOREAN_SYNCED)

    window._menu.trigger(m.ROMANISATION)
    assert window._view_model.romanisation_enabled is True
    assert window._settings.value("lyrics/romanisation", type=bool) is True

    window._menu.trigger(m.ECHO)
    assert window._echo_enabled is True
    assert window._loop.echo is True

    window._menu.trigger(m.SPEECH_RATE, 160)
    assert window._speech_rate == 160
    assert window._settings.value("lyrics/speech_rate", type=int) == 160


def test_a_refresh_does_not_feed_check_marks_back_into_the_setters(make_window):
    """Check marks are set programmatically on every render, and the refresh
    is the ONE writer of them. A refresh that reached the setters back would
    invert settings behind the user, three times over here."""
    window = make_window()
    window._view_model.romanisation_enabled = True
    window._spoken_enabled = False
    for _ in range(3):
        window._refresh_menu()
    assert window._view_model.romanisation_enabled is True
    assert window._spoken_enabled is False


# -- the menu bar glyph ---------------------------------------------------


def test_nothing_playing_shows_three_even_bars_at_full_brightness(
    with_tray, make_window
):
    """15.1: nothing playing no longer dims. The shape says there is no
    current line; the brightness says the lyrics are on screen."""
    window = make_window()
    assert window._tray_state.lengths == mb.EVEN_LENGTHS
    assert window._tray_state.dimmed is False
    assert window._tray_state.dot is False


def test_playing_changes_the_shape_and_not_the_brightness(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    before = window._tray_state
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS
    assert window._tray_state.dimmed == before.dimmed is False


def test_hiding_the_lyrics_dims_it_and_leaves_the_shape_alone(
    with_tray, make_window
):
    """The two axes, shown not to interfere: the shape still says a song is
    playing while the brightness says the window is away."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state == mb.IconSpec(mb.PLAYING_LENGTHS, False, False)

    window._set_lyrics_visible(False)
    land(window)
    assert window._tray_state == mb.IconSpec(mb.PLAYING_LENGTHS, True, False)

    window._set_lyrics_visible(True)
    land(window)
    assert window._tray_state == mb.IconSpec(mb.PLAYING_LENGTHS, False, False)


def test_a_practice_mode_adds_the_dot(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED)
    window._on_position_update(snapshot(position=2.0))  # a line to loop
    window._last_state = PlaybackState.PLAYING
    window._toggle_loop(True)
    assert window._loop.engaged
    window._refresh_tray_icon()
    assert window._tray_state.dot is True

    window._toggle_loop(False)
    window._refresh_tray_icon()
    assert window._tray_state.dot is False


def test_a_sync_pass_adds_the_dot_too(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    load(window, PLAIN, track_id="t7")
    window._begin_sync()
    window._refresh_tray_icon()
    assert window._tray_state.dot is True


def test_practice_keeps_it_bright_behind_a_hidden_window(with_tray, make_window):
    """A pass keeps running while the lyrics are away, and then the item is
    the only evidence it is going."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, PLAIN, track_id="t7")
    window._begin_sync()
    window._set_lyrics_visible(False)
    land(window)
    assert window._tray_state.dot is True
    assert window._tray_state.dimmed is False


def test_the_glyph_follows_a_pause_without_the_menu_being_opened(
    with_tray, make_window
):
    """THE 15.1 BUG. The icon was refreshed from _render, and a pause does not
    re-render — player_state_changed returns False for PAUSED because the
    display text is unchanged. So the item claimed a song was playing until
    somebody opened the menu. The monitor tick is what fixes it."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS

    window._on_position_update(snapshot(state=PlaybackState.PAUSED))

    assert window._tray_state.lengths == mb.EVEN_LENGTHS


def test_a_state_change_refreshes_it_before_anything_can_return_early(
    with_tray, make_window
):
    """Spotify quitting is the transition after which no more position
    updates arrive, so it is the tick's last chance to put the shape back."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS

    window._on_state_change(snapshot(state=PlaybackState.NOT_RUNNING))

    assert window._tray_state.lengths == mb.EVEN_LENGTHS


def test_the_glyph_is_set_only_when_it_changes(with_tray, make_window):
    """The refresh now runs on every monitor tick — three times a second —
    and handing the same icon back to an NSStatusItem that often is the menu
    bar item being rebuilt under the user."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))

    before = len(window._tray.images)
    for _ in range(5):
        window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert len(window._tray.images) == before

    window._on_position_update(snapshot(state=PlaybackState.PAUSED))
    assert len(window._tray.images) == before + 1


def test_each_glyph_is_drawn_once_and_kept(with_tray, make_window):
    """Eight combinations, times four arrangements with the animation on. A
    line change has to be a dictionary lookup, not a repaint."""
    window = make_window()
    window.apply_saved_visibility()
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    playing = window._tray_state
    assert playing in window._tray_pngs
    first = window._tray_pngs[playing]

    window._on_position_update(snapshot(state=PlaybackState.PAUSED))
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_pngs[playing] is first


def test_every_glyph_is_a_template_so_macos_owns_the_colour(with_tray, make_window):
    """A coloured menu bar icon stops following the menu bar, which is why
    practice is a DOT and not a hue.

    Two halves, and both are here because either alone would pass while the
    icon came out black on a dark bar: the pixels have to be black with the
    shape in the ALPHA channel, and the image has to be told it is a
    template. The first is a property of the drawing and is measured; the
    second is one call inside the one door and is asserted structurally,
    the way everything native in this suite is.
    """
    window = make_window()
    window.apply_saved_visibility()
    for playing in (True, False):
        for visible in (True, False):
            for practising in (True, False):
                spec = mb.icon_spec(
                    playing=playing,
                    lyrics_visible=visible,
                    practising=practising,
                )
                image = w.symbols.menubar_pixmap(spec, mb.GLYPH_UNITS).toImage()
                colours = {
                    image.pixelColor(x, y).getRgb()[:3]
                    for x in range(image.width())
                    for y in range(image.height())
                    if image.pixelColor(x, y).alpha() > 0
                }
                assert colours == {(0, 0, 0)}, spec
    source = (
        Path(w.nsmenu.__file__).read_text(encoding="utf-8")
    )
    assert "setTemplate_(True)" in source


def test_the_drawn_glyphs_are_not_all_the_same_pixels(with_tray, make_window):
    """Eight specs that happened to render identically would pass every test
    above and say nothing on the menu bar."""
    make_window()
    seen = set()
    for playing in (True, False):
        for visible in (True, False):
            for practising in (True, False):
                spec = mb.icon_spec(
                    playing=playing, lyrics_visible=visible, practising=practising
                )
                seen.add(w.symbols.menubar_png(spec, mb.GLYPH_UNITS))
    # practice forces bright and a dot, so hidden-vs-shown collapses there
    assert len(seen) == 6


# -- the optional arrangement stepping ------------------------------------


def test_the_animation_is_off_by_default(with_tray, make_window):
    window = make_window()
    assert window._menubar_animation is False
    assert window._menu.is_checked(m.MENUBAR_ANIMATION) is False


def test_off_means_the_shape_never_moves(with_tray, make_window):
    """The layers principle: off must equal the app before this existed."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))
    shapes = {window._tray_state.lengths}
    for position in (1.0, 5.0, 1.0, 5.0):
        window._on_position_update(
            snapshot(state=PlaybackState.PLAYING, track_id="t5", position=position)
        )
        shapes.add(window._tray_state.lengths)
    assert shapes == {mb.PLAYING_LENGTHS}


def test_a_line_change_steps_the_arrangement(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))

    seen = [window._tray_state.lengths]
    for position in (5.0, 1.0, 5.0):
        window._on_position_update(
            snapshot(state=PlaybackState.PLAYING, track_id="t5", position=position)
        )
        seen.append(window._tray_state.lengths)

    assert len(set(seen)) > 1, "the shape has to actually move"
    assert all(shape in mb.ARRANGEMENTS for shape in seen)


def test_the_step_counts_only_real_line_changes(with_tray, make_window):
    """_render re-runs _set_lines for reasons that have nothing to do with
    the song — a menu refresh, a resize — and those are not line changes."""
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))
    before = window._menubar_step

    for _ in range(4):
        window._render()
        window._refresh_menu()
    assert window._menubar_step == before


def test_the_step_is_counted_even_with_the_layer_off(with_tray, make_window):
    """So switching it on mid-song picks up where the song is rather than
    restarting a cycle."""
    window = make_window()
    window.apply_saved_visibility()
    load(window, SYNCED, track_id="t5")
    window._on_position_update(snapshot(state=PlaybackState.PLAYING, track_id="t5"))
    before = window._menubar_step
    window._on_position_update(
        snapshot(state=PlaybackState.PLAYING, track_id="t5", position=5.0)
    )
    assert window._menubar_step > before


def test_nothing_moves_the_shape_while_nothing_is_playing(with_tray, make_window):
    """There are no line changes with nothing playing, and an arrangement
    frozen mid-cycle would be a shape that means nothing."""
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    window._menubar_step = 2
    window._on_position_update(snapshot(state=PlaybackState.PAUSED))
    assert window._tray_state.lengths == mb.EVEN_LENGTHS


def test_switching_the_animation_off_puts_the_shape_back(with_tray, make_window):
    window = make_window()
    window.apply_saved_visibility()
    window._set_menubar_animation(True)
    window._menubar_step = 2
    window._on_position_update(snapshot(state=PlaybackState.PLAYING))
    assert window._tray_state.lengths != mb.PLAYING_LENGTHS

    window._set_menubar_animation(False)
    assert window._tray_state.lengths == mb.PLAYING_LENGTHS


def test_the_animation_setting_survives_a_restart(with_tray, make_window):
    first = make_window()
    first._set_menubar_animation(True)
    first._save_settings()
    first._settings.sync()

    second = make_window()
    assert second._menubar_animation is True
    assert second._menu.is_checked(m.MENUBAR_ANIMATION) is True


def test_no_menu_bar_item_is_not_a_crash(monkeypatch, make_window):
    """Everything about the glyph has to survive there being nowhere to
    put it — the same rule the rest of the menu bar code follows."""

    class NoMenuBar(FakeStatusItem):
        def create(self, tooltip=""):
            return False

    monkeypatch.setattr(w.nsmenu, "StatusItem", NoMenuBar)
    window = make_window()
    window._last_state = PlaybackState.PLAYING
    window._refresh_menu()  # must not raise
    assert window._tray is None


# -- activation policy ----------------------------------------------------


# -- open at login --------------------------------------------------------


def test_open_at_login_is_hidden_when_running_from_source(make_window):
    """The suite runs from a checkout, which is the case every developer
    sees: no bundle for macOS to launch, so no switch."""
    window = make_window()
    assert window._bundled is False
    load(window, SYNCED)
    assert m.OPEN_AT_LOGIN not in visible_keys(window)


def test_open_at_login_appears_for_a_bundle(make_window):
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.NOT_REGISTERED
    load(window, SYNCED)
    assert m.OPEN_AT_LOGIN in visible_keys(window)


def test_the_entry_follows_the_system_not_the_stored_preference(make_window):
    """The requirement this feature turns on: the tick is macOS's answer.
    A user who switches this off in System Settings must see it switched
    off here, whatever this app last wrote down."""
    window = make_window()
    window._bundled = True
    window._settings.setValue("window/open_at_login", True)  # what we asked for

    window._login_status = login_item.LoginItemStatus.NOT_REGISTERED  # what is true
    window._refresh_menu()
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False

    window._login_status = login_item.LoginItemStatus.ENABLED
    window._refresh_menu()
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is True


def test_awaiting_approval_stays_unchecked_and_says_why(make_window):
    """Registered but not yet approved is not enabled. The entry must not
    claim a launch that will not happen, and the label is the only place
    that can point at System Settings."""
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.REQUIRES_APPROVAL
    window._refresh_menu()

    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False
    assert "System Settings" in window._menu.label(m.OPEN_AT_LOGIN)


def test_the_label_returns_to_normal_once_approved(make_window):
    window = make_window()
    window._bundled = True
    window._login_status = login_item.LoginItemStatus.REQUIRES_APPROVAL
    window._refresh_menu()
    window._login_status = login_item.LoginItemStatus.ENABLED
    window._refresh_menu()
    assert window._menu.label(m.OPEN_AT_LOGIN) == login_item.MENU_LABEL


def test_toggling_registers_and_records_what_was_asked(make_window, monkeypatch):
    window = make_window()
    window._bundled = True
    asked = []

    def fake_set(enabled):
        asked.append(enabled)
        return True, (
            login_item.LoginItemStatus.ENABLED
            if enabled
            else login_item.LoginItemStatus.NOT_REGISTERED
        )

    monkeypatch.setattr(login_item, "set_enabled", fake_set)

    window._set_open_at_login(True)
    assert asked == [True]
    assert window._settings.value("window/open_at_login", type=bool) is True
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is True

    window._set_open_at_login(False)
    assert asked == [True, False]
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False


def test_a_failed_registration_leaves_the_entry_unchecked(make_window, monkeypatch, caplog):
    """Rather than lying about it. The user clicked, macOS refused, and
    the menu has to show the refusal."""
    window = make_window()
    window._bundled = True
    monkeypatch.setattr(
        login_item,
        "set_enabled",
        lambda enabled: (False, login_item.LoginItemStatus.REQUIRES_APPROVAL),
    )
    with caplog.at_level(logging.WARNING, logger="sottovoce.window"):
        window._set_open_at_login(True)

    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False
    assert "System Settings" in window._menu.label(m.OPEN_AT_LOGIN)
    assert "Open at Login stays off" in caplog.text


def test_opening_the_menu_rereads_the_system(make_window, monkeypatch):
    """Not cached: the user can change this in System Settings while the
    app runs, so every opening asks again."""
    window = make_window()
    window._bundled = True
    answers = iter(
        [login_item.LoginItemStatus.ENABLED, login_item.LoginItemStatus.NOT_REGISTERED]
    )
    monkeypatch.setattr(login_item, "status", lambda: next(answers))

    window._menu.opening()
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is True
    window._menu.opening()
    assert window._menu.is_checked(m.OPEN_AT_LOGIN) is False


def test_a_source_run_never_asks_the_system(make_window, monkeypatch):
    """No bundle, no question: the entry is hidden, and asking would be
    asking about an app that does not exist as far as macOS is
    concerned."""
    window = make_window()
    window._bundled = False
    asked = []
    monkeypatch.setattr(
        login_item,
        "status",
        lambda: asked.append(True) or login_item.LoginItemStatus.ENABLED,
    )
    window._menu.opening()
    assert asked == []


def test_the_all_desktops_toggle_cannot_touch_the_activation_policy():
    """Accessory is applied once at startup and never revoked, so no toggle
    state can bring the Dock icon (or the Space switch) back."""
    assert not hasattr(w.LyricsWindow, "_apply_activation_policy")
    assert callable(w.apply_accessory_policy)
    source = w.LyricsWindow._apply_all_desktops.__doc__ or ""
    assert "activation policy is NOT part of this" in source
