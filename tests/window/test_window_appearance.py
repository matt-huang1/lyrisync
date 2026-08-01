"""What the window is made of: the palette, the hairline, the album
tint, and the three accessibility display settings.

The contrast floors themselves are pure and live in test_scrim.py; these
cover what only a real window answers — that a system change repaints it,
that the tint is a hue and nothing else, and that Reduce Motion, Reduce
Transparency and Increase Contrast reach the widgets.
"""

TIER = "qt"  # a real window, driven by calling its own methods

import pytest

from PySide6.QtCore import QPoint, Qt

from sottovoce import accessibility
from sottovoce import appearance as ap
from sottovoce import menu as m
from sottovoce import vibrancy
from sottovoce import window as w
from sottovoce.view_model import Mode

from helpers import (
    APP,
    PLAIN,
    REAL_ARTWORK_RUN,
    RED_COVER,
    SYNCED,
    art_snapshot,
    load,
    settle_tint,
    snapshot,
    tell,
)


# -- following the system appearance --------------------------------------


def set_scheme(scheme):
    """Publish an appearance change the way the platform does.

    The offscreen plugin will not change its own colour scheme —
    setColorScheme is ignored and it reports Unknown forever — so the
    signal is emitted directly. That still exercises the real connection
    the window makes in __init__ rather than calling its slot by hand,
    which is the half of this that could silently not be wired.
    """
    APP.styleHints().colorSchemeChanged.emit(scheme)
    APP.processEvents()


def test_the_window_starts_on_the_system_appearance(make_window):
    """Offscreen reports Unknown, which resolves to dark — so the suite
    runs against the palette the app has always had."""
    window = make_window()
    assert window._appearance is ap.Appearance.DARK
    assert window._palette is ap.DARK


def test_a_system_change_repaints_the_window(make_window):
    window = make_window()
    load(window, SYNCED)

    set_scheme(Qt.ColorScheme.Light)
    assert window._appearance is ap.Appearance.LIGHT
    assert window._palette is ap.LIGHT
    assert ap.rgba(ap.LIGHT.current) in window.styleSheet()

    set_scheme(Qt.ColorScheme.Dark)
    assert window._palette is ap.DARK
    assert ap.rgba(ap.DARK.current) in window.styleSheet()


def test_the_scrim_follows_the_palette(make_window, monkeypatch):
    """The background is painted, not styled, so it needs its own path out
    of the palette — and it is the one thing a stylesheet swap would
    silently leave behind."""
    window = make_window()
    # No material on the offscreen platform, so paintEvent reaches for the
    # solid background — the same code path, one field along.
    assert window._material is None
    painted = []
    real_qcolor = w._qcolor
    monkeypatch.setattr(
        w, "_qcolor", lambda colour: (painted.append(colour), real_qcolor(colour))[1]
    )

    # painted[0] is the fill; painted[1] is the hairline drawn over it.
    window.grab()  # a real paintEvent, into a pixmap
    assert painted[0] == ap.DARK.solid
    assert painted[1] == ap.DARK.border

    set_scheme(Qt.ColorScheme.Light)
    painted.clear()
    window.grab()
    assert painted[0] == ap.LIGHT.solid
    assert painted[1] == ap.LIGHT.border


def test_a_redundant_change_restyles_nothing(make_window, monkeypatch):
    """The signal fires for changes this window does not care about — an
    Unknown, or a re-announcement of what is already on screen. Rebuilding
    the stylesheet for those would repolish every widget for nothing."""
    window = make_window()
    # Spied on the instance, not the module: the signal reaches every
    # window alive in the process, and windows from earlier tests outlive
    # their deleteLater() until an event loop runs. Counting module-level
    # calls would be counting theirs.
    repaints = []
    real_apply = window._apply_appearance
    monkeypatch.setattr(
        window, "_apply_appearance", lambda: (repaints.append(1), real_apply())[1]
    )

    set_scheme(Qt.ColorScheme.Dark)      # already dark
    set_scheme(Qt.ColorScheme.Unknown)   # resolves to dark
    assert repaints == []
    assert window._palette is ap.DARK

    set_scheme(Qt.ColorScheme.Light)
    assert len(repaints) == 1


def test_a_resize_after_a_switch_keeps_the_new_palette(make_window):
    """_apply_scale rebuilds the stylesheet too. If it reached for a
    constant instead of the current palette, the window would snap back to
    dark the next time it was dragged wider."""
    window = make_window()
    set_scheme(Qt.ColorScheme.Light)

    window.resize(600, 260)
    APP.processEvents()
    assert ap.rgba(ap.LIGHT.current) in window.styleSheet()
    assert ap.rgba(ap.DARK.current) not in window.styleSheet()


def test_everything_that_is_not_a_colour_survives_a_switch(make_window):
    """The switch repaints; it must not disturb anything else. Geometry,
    opacity, an engaged loop and a sync pass in progress all carry on."""
    window = make_window()
    load(window, PLAIN)
    window._begin_sync()
    window._on_position_update(snapshot())
    window._on_tap()

    window.resize(520, 240)
    window._set_opacity(0.6)
    APP.processEvents()
    geometry, opacity = window.geometry(), window._opacity
    scale, stamped = window._scale, window._view_model.sync_session.index

    set_scheme(Qt.ColorScheme.Light)

    assert window.geometry() == geometry
    assert window._opacity == opacity
    assert window._scale == scale
    assert window._view_model.sync_session.index == stamped
    assert window._view_model.display().mode is Mode.SYNCING
    assert window._monitor_thread.isRunning() is True
    # isVisibleTo, not isVisible: this window was never shown, so every
    # child reports hidden regardless. What matters is that the switch did
    # not take the tap row out of the layout.
    assert window._tap_button.isVisibleTo(window) is True


def test_the_armed_discard_prompt_is_coloured_per_mode(make_window):
    """It carries its colour inline rather than by object name, so it is
    the one piece of text a stylesheet swap cannot reach."""
    window = make_window()
    load(window, PLAIN)
    window._begin_sync()
    window._on_sync_exit()  # arms it
    assert ap.rgba(ap.DARK.confirm_text) in window._progress.text()

    set_scheme(Qt.ColorScheme.Light)
    assert ap.rgba(ap.LIGHT.confirm_text) in window._progress.text()


def test_the_speak_icon_is_redrawn_for_the_new_mode(make_window, monkeypatch):
    """An SF Symbol is a template image tinted by us, so a white glyph
    stays white on a pale panel unless it is rendered again."""
    window = make_window()
    tints = []
    monkeypatch.setattr(
        w, "symbol_icon", lambda name, size, normal, **kw: tints.append(normal) or None
    )

    set_scheme(Qt.ColorScheme.Light)
    assert tints, "the icon was never re-rendered"
    assert tints[-1].alpha() == ap.LIGHT.control_idle[3]
    assert tints[-1].blue() == ap.LIGHT.control_idle[2]


def test_the_material_appearance_is_asked_for_the_same_mode(make_window):
    """No material off cocoa, so this is the guard rather than the call —
    but the guard is what keeps the suite headless."""
    window = make_window()
    assert window._material is None
    window._apply_material_appearance()  # must be a no-op, not a crash


def test_the_material_and_the_scrim_cannot_disagree():
    """One answer drives both: whichever mode the palette came from is the
    NSAppearance the material is told to adopt."""
    assert vibrancy.appearance_name(True) == vibrancy.DARK_APPEARANCE
    assert vibrancy.appearance_name(False) == vibrancy.LIGHT_APPEARANCE
    assert "NSAppearanceName" in vibrancy.LIGHT_APPEARANCE


def test_there_is_no_appearance_setting(make_window):
    """Following the system is the whole feature. A toggle would be a
    second source of truth for something macOS already answers."""
    window = make_window()
    assert not any("appearance" in key for key in m.MENU_ORDER)
    assert not any("theme" in key for key in m.MENU_ORDER)
    window._save_settings()
    keys = window._settings.allKeys()
    assert not any("appearance" in k or "theme" in k for k in keys)


# -- depth ----------------------------------------------------------------


def test_both_palettes_carry_a_hairline(make_window):
    """Light over the dark panel, dark over the pale one — the way macOS
    edges its own HUD surfaces."""
    assert ap.DARK.border[:3] == (255, 255, 255)
    assert ap.LIGHT.border[:3] == (0, 0, 0)
    for palette in (ap.DARK, ap.LIGHT):
        assert 0 < palette.border[3] < 64, "a hairline, not a border"


def test_the_hairline_is_where_the_album_colour_goes(make_window):
    """SUPERSEDES "a coloured hairline reads as a border". The panel's
    luminance is pinned by the contrast floor and has no gamut left to
    spend, least of all in light mode; the edge has no text on it and can
    take the hue properly. What is checked here is the wiring — that the
    window paints the tinted edge rather than the palette's own — with
    the derivation itself measured in test_scrim.py."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    assert window._current_border() == ap.DARK.border  # untinted until a cover

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    expected = ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).border
    assert window._current_border() == expected
    assert window._current_border() != ap.DARK.border


def test_the_painted_edge_is_the_tinted_one(make_window):
    """From the pixels paintEvent produced, not from the colour it was
    asked for: the top row of the grab is the hairline over the fill, and
    it has to be the album's hue rather than the palette's neutral edge.
    grab() does not apply a QGraphicsEffect, but paintEvent is exactly
    what it does run."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    middle = window.width() // 2
    neutral = window.grab().toImage().pixelColor(middle, 0)
    assert neutral.red() == neutral.green()  # a grey edge over a grey fill

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    edge = window.grab().toImage().pixelColor(middle, 0)
    fill = window.grab().toImage().pixelColor(middle, 4)
    assert edge.red() > edge.green() and edge.red() > edge.blue(), "not red at all"
    assert edge.red() - min(edge.green(), edge.blue()) > 3 * (
        fill.red() - min(fill.green(), fill.blue())
    ), "the edge is carrying no more colour than the panel"


def test_the_edge_and_the_panel_arrive_together(make_window):
    """One cross-fade drives both. Two fades of the same tint could only
    drift apart, and an edge that changed colour before its panel would
    read as a flicker at the rim."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)

    assert window._current_border() == ap.DARK.border  # still where it began
    window._tint_anim.setCurrentTime(w._TINT_FADE_MS // 2)
    APP.processEvents()
    assert window._current_border() not in (
        ap.DARK.border,
        ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).border,
    )

    settle_tint(window)
    assert window._current_border() == ap.tinted(
        ap.DARK, RED_COVER, ap.Appearance.DARK
    ).border


def test_switching_the_layer_off_restores_the_plain_edge(make_window):
    """The layers principle reaches the rim too: off is the app before
    this existed, to the byte."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert window._current_border() != ap.DARK.border

    window._set_album_colour(False)
    settle_tint(window)
    assert window._current_border() == ap.DARK.border


def test_the_edge_is_re_derived_for_the_new_appearance(make_window):
    """The two modes want different lightnesses for the same hue — the
    edge has to stay lighter than a dark panel and darker than a pale
    one — so a switch cannot carry the old colour across."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    set_scheme(Qt.ColorScheme.Light)
    assert window._current_border() == ap.tinted(
        ap.LIGHT, RED_COVER, ap.Appearance.LIGHT
    ).border


def test_the_shadow_is_guarded_off_cocoa(make_window):
    """No NSWindow on the offscreen platform, so these must be no-ops
    rather than crashes — which is what keeps the suite headless."""
    window = make_window()
    window._apply_shadow()
    window._invalidate_shadow()


# -- album colour ---------------------------------------------------------


@pytest.fixture
def artwork_tasks(monkeypatch):
    """Record the cover lookups the window starts.

    A recording subclass rather than a patched thread pool: _pool is
    QThreadPool.globalInstance(), a process-wide singleton, so assigning
    to its start() leaks into every test that runs afterwards.
    """
    started = []
    real = w.ArtworkTask

    class Recording(real):
        def __init__(self, provider, track_id, url):
            super().__init__(provider, track_id, url)
            started.append((track_id, url))

    monkeypatch.setattr(w, "ArtworkTask", Recording)
    return started


def painted_background(window):
    """The colour paintEvent actually reaches for, mid-fade included."""
    return window._current_background()


def test_album_colour_is_off_by_default(make_window):
    """The layers principle: the plain window is what the app is."""
    window = make_window()
    assert window._album_colour is False
    assert window._menu.is_checked(m.ALBUM_COLOUR) is False
    assert painted_background(window) == ap.DARK.solid


def test_nothing_is_fetched_while_the_layer_is_off(make_window, artwork_tasks):
    """A disabled feature does not get to make network requests."""
    window = make_window()
    window._on_track_change(art_snapshot())
    assert artwork_tasks == []


def test_enabling_it_asks_for_the_current_track(make_window, artwork_tasks):
    """Switched on mid-song, it must not wait for the next track."""
    window = make_window()
    window._on_track_change(art_snapshot())
    assert artwork_tasks == []

    window._menu.trigger(m.ALBUM_COLOUR)
    assert window._album_colour is True
    assert artwork_tasks == [("t1", "http://cover")]


def test_a_cover_colour_tints_the_background(make_window):
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    expected = ap.tinted(ap.DARK, RED_COVER, ap.Appearance.DARK).solid
    assert painted_background(window) == expected
    assert painted_background(window) != ap.DARK.solid


def test_switching_it_off_restores_the_previous_look_exactly(make_window):
    """The acceptance criterion, and the layers principle: off must equal
    the app before this feature existed, to the byte."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert painted_background(window) != ap.DARK.solid

    window._menu.trigger(m.ALBUM_COLOUR)  # off
    settle_tint(window)
    assert window._album_colour is False
    assert painted_background(window) == ap.DARK.solid
    assert window._menu.is_checked(m.ALBUM_COLOUR) is False


def test_a_cover_landing_after_the_layer_is_off_changes_nothing(make_window):
    """Covers are in flight when the toggle is clicked."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._set_album_colour(False)

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert painted_background(window) == ap.DARK.solid


def test_a_cover_for_a_track_that_has_moved_on_is_dropped(make_window):
    """Skipping through tracks puts several lookups in flight at once, and
    the last to land is not the one on screen."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot(track_id="t2"))

    window._on_artwork_ready("t1", RED_COVER)  # the previous track's cover
    settle_tint(window)
    assert painted_background(window) == ap.DARK.solid


def test_a_cover_with_no_usable_colour_leaves_the_window_alone(make_window):
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    window._on_artwork_ready("t1", None)
    settle_tint(window)
    assert painted_background(window) == ap.DARK.solid


def test_the_setting_is_persisted_and_restored(make_window):
    window = make_window()
    window._set_album_colour(True)
    window._settings.sync()

    reopened = make_window()
    assert reopened._album_colour is True
    assert reopened._menu.is_checked(m.ALBUM_COLOUR) is True


def test_the_tint_cross_fades_rather_than_snapping(make_window):
    """A colour that changed in one frame reads as a glitch, not as the
    song changing."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())

    window._on_artwork_ready("t1", RED_COVER)
    assert window._tint_anim is not None
    assert window._tint_anim.duration() == w._TINT_FADE_MS
    assert painted_background(window) == ap.DARK.solid  # still where it began

    window._tint_anim.setCurrentTime(w._TINT_FADE_MS // 2)
    APP.processEvents()
    midway = painted_background(window)
    assert midway not in (ap.DARK.solid,)

    settle_tint(window)
    assert painted_background(window) == ap.tinted(
        ap.DARK, RED_COVER, ap.Appearance.DARK
    ).solid


def test_a_second_cover_fades_on_from_where_the_first_had_got_to(make_window):
    """Tracks skipped quickly interrupt a fade in progress; restarting
    from the old target would jump backwards first."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    window._tint_anim.setCurrentTime(w._TINT_FADE_MS // 2)
    APP.processEvents()
    midway = painted_background(window)

    window._on_track_change(art_snapshot(track_id="t2"))
    window._on_artwork_ready("t2", (40, 60, 200))
    assert window._tint_from == midway


def test_the_tint_survives_an_appearance_switch(make_window):
    """The cover colour is kept; what it derives from is not. It must come
    out re-derived against the new palette, not carried across."""
    window = make_window()
    window._set_album_colour(True)
    window._on_track_change(art_snapshot())
    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)

    set_scheme(Qt.ColorScheme.Light)
    assert window._tint_rgb == RED_COVER
    assert painted_background(window) == ap.tinted(
        ap.LIGHT, RED_COVER, ap.Appearance.LIGHT
    ).solid


def test_the_tint_never_touches_the_text(make_window):
    """Contrast is a promise about the sung line, and the stylesheet is
    where the sung line's colour lives."""
    window = make_window()
    window._set_album_colour(True)
    load(window, SYNCED)
    before = window.styleSheet()

    window._on_artwork_ready("t1", RED_COVER)
    settle_tint(window)
    assert window.styleSheet() == before
    assert ap.rgba(ap.DARK.current) in window.styleSheet()


def test_non_music_items_are_never_looked_up(make_window, artwork_tasks):
    """DJ narration and ads reuse other tracks' identity, so a cover
    fetched for one would be cached against the wrong song."""
    window = make_window()
    window._set_album_colour(True)
    artwork_tasks.clear()

    window._on_track_change(art_snapshot(track_id="t9", kind="media"))
    assert artwork_tasks == []


def test_a_track_without_a_cover_is_still_looked_up_from_cache(
    make_window, artwork_tasks
):
    """No URL is not the same as no answer: the colour may already be
    known from a previous play."""
    window = make_window()
    window._set_album_colour(True)
    artwork_tasks.clear()

    window._on_track_change(art_snapshot(url=None))
    assert artwork_tasks == [("t1", None)]


def test_the_artwork_task_never_raises_into_the_pool(make_window):
    """It runs on a pool thread where an exception would be swallowed
    somewhere unhelpful, so it has to catch its own."""

    class Exploding:
        def colour_for(self, track_id, url):
            raise RuntimeError("boom")

    reported = []
    task = w.ArtworkTask(Exploding(), "t1", "http://cover")
    task.signals.finished.connect(lambda tid, colour: reported.append((tid, colour)))
    REAL_ARTWORK_RUN(task)  # the fixture stubs run(); this test is about it
    APP.processEvents()
    assert reported == [("t1", None)]


# -- macOS accessibility display settings ----------------------------------
#
# Read live, like the appearance: somebody who switches Reduce Motion on
# because a migraine has started should not have to relaunch the app to be
# believed. The settings themselves cannot be toggled from a test — the
# domain is TCC-protected — so what is checked here is what the window does
# when it is told, and `tell` in helpers.py is how it is told.


def test_a_window_starts_with_nothing_switched_on(make_window):
    window = make_window()
    assert window._display_options == accessibility.NONE
    assert window._palette is ap.palette_for(window._appearance)


def test_the_window_watches_for_changes(make_window):
    """The wiring, not the effect: an app that only looked at startup is
    the app that is wrong for the rest of the session."""
    window = make_window()
    assert isinstance(window._display_watcher, accessibility.DisplayOptionsWatcher)
    # No workspace in the suite, so the subscription simply finds nothing
    # to observe — the same branch a machine without pyobjc takes.
    assert window._display_watcher.active is False


def test_the_observer_is_released_before_anything_is_destroyed(make_window):
    """NSWorkspace holds a block that repaints a window being torn down,
    the same hazard the activation watcher has."""
    window = make_window()
    stopped = []
    window._display_watcher.stop = lambda: stopped.append(True)
    window._shutdown()
    assert stopped == [True]


def test_the_same_options_twice_change_nothing(make_window):
    window = make_window()
    palette = window._palette
    tell(window)
    assert window._palette is palette


# Reduce Motion.


def test_reduce_motion_takes_the_travel_out_of_a_line_change(make_window):
    """The fade stays and the rise goes. ``progress`` is one signed number
    carrying both, so the travel is a length and this sets it to zero."""
    window = make_window()
    load(window, SYNCED)
    assert window._current_fx.travel > 0

    tell(window, reduce_motion=True)
    assert window._current_fx.travel == 0.0
    # And the choreography itself is untouched: the same timers, the same
    # phase length, the arrival still on the timestamp.
    window._on_position_update(snapshot(position=0.2))
    assert window._swap_timer.isActive()


def test_the_travel_comes_back(make_window):
    window = make_window()
    tell(window, reduce_motion=True)
    tell(window)
    assert window._current_fx.travel > 0


def test_a_resize_under_reduce_motion_does_not_restore_the_travel(make_window):
    """_apply_scale recomputes it, so it has to go through the same
    place."""
    window = make_window()
    tell(window, reduce_motion=True)
    window.resize(640, 300)
    APP.processEvents()
    assert window._current_fx.travel == 0.0


def test_reduce_motion_hides_the_window_without_the_flight(make_window):
    window = make_window()
    tell(window, reduce_motion=True)
    window._set_lyrics_visible(False)
    APP.processEvents()
    assert window._flight_anim is None
    assert not window.isVisible()
    window._set_lyrics_visible(True)
    APP.processEvents()
    assert window._flight_anim is None
    assert window.isVisible()


def test_reduce_motion_gives_back_everything_the_flight_borrowed(make_window):
    """Switched on mid-journey: the flight in the air must not be left
    holding the window's position, opacity or scale."""
    window = make_window()
    window.move(400, 300)
    window._set_lyrics_visible(False)  # a flight is now running
    assert window._flight_anim is not None

    tell(window, reduce_motion=True)
    window._set_lyrics_visible(True)
    APP.processEvents()
    assert window._flight_anim is None
    assert window._flight_home is None
    assert window._flight_opacity == 1.0
    assert window.pos() == QPoint(400, 300)


def test_reduce_motion_moves_the_window_without_travelling(make_window):
    """Per-app position memory is about where the window lives, not about
    how it gets there: it still arrives, it simply does not travel."""
    window = make_window()
    window.move(100, 100)
    tell(window, reduce_motion=True)
    window._move_to(QPoint(300, 240))
    APP.processEvents()
    assert window._move_anim is None
    assert window.pos() == QPoint(300, 240)


# Reduce Transparency.


def test_reduce_transparency_paints_the_solid_background(make_window):
    window = make_window()
    tell(window, reduce_transparency=True)
    assert window._palette.solid[3] == 255
    assert window._material is None
    assert window._current_background() == window._palette.solid


def test_reduce_transparency_refuses_to_install_a_material(make_window):
    """The setting is about that view and nothing else, so the honest
    answer is not to build one."""
    window = make_window()
    tell(window, reduce_transparency=True)
    assert window._apply_vibrancy() is False


def test_the_material_is_removed_rather_than_hidden(make_window):
    """A hidden effect view is still an effect view, and the flight hides
    and shows this one for its own reasons — which would put a suppressed
    material straight back."""
    window = make_window()

    class FakeMaterial:
        def __init__(self):
            self.removed = False
            self.hidden = None

        def removeFromSuperview(self):
            self.removed = True

        def setHidden_(self, value):
            self.hidden = value

    material = FakeMaterial()
    window._native_applied = True
    window._material = material
    tell(window, reduce_transparency=True)
    assert material.removed
    assert material.hidden is None
    assert window._material is None


def test_switching_it_off_asks_for_the_material_back(make_window, monkeypatch):
    window = make_window()
    window._native_applied = True
    tell(window, reduce_transparency=True)
    asked = []
    monkeypatch.setattr(
        window, "_apply_vibrancy", lambda: asked.append(True) or False
    )
    tell(window)
    assert asked == [True]


def test_the_background_before_the_window_is_shown_is_not_touched(make_window):
    """The first install happens in showEvent and consults the same
    options; asking for one before that would be asking about a window
    that has no native view yet."""
    window = make_window()
    window._native_applied = False
    window._material = None
    tell(window, reduce_transparency=True)  # must not raise
    assert window._material is None


# Increase Contrast.


def test_increase_contrast_lifts_the_palette_and_drops_the_material(make_window):
    """macOS turns Reduce Transparency on with it, and the app derives the
    same thing rather than trusting the pair to arrive together."""
    window = make_window()
    tell(window, increase_contrast=True)
    assert window._palette.solid[3] == 255
    assert window._palette is not ap.palette_for(window._appearance)
    for role, value in ap.HIGH_CONTRAST_OVERRIDES[window._appearance].items():
        assert getattr(window._palette, role) == value


def test_the_lifted_palette_reaches_the_stylesheet(make_window):
    """The colours are painted from a stylesheet, so a palette nobody
    applied is a setting that did nothing."""
    window = make_window()
    before = window.styleSheet()
    tell(window, increase_contrast=True)
    assert window.styleSheet() != before
    assert ap.rgba(window._palette.control_idle) in window.styleSheet()


def test_an_appearance_change_keeps_the_accessibility_settings(make_window):
    """Two systems the window follows, one palette. Whichever moves, both
    are asked again."""
    window = make_window()
    tell(window, increase_contrast=True)
    other = (
        ap.Appearance.LIGHT
        if window._appearance is ap.Appearance.DARK
        else ap.Appearance.DARK
    )
    window._appearance = other
    window._palette = window._palette_now()
    assert window._palette.solid[3] == 255
    assert (
        window._palette.border == ap.HIGH_CONTRAST_OVERRIDES[other]["border"]
    )
