"""Sizing the strip to the song, and the strip's own type size.

In the full layout the type scale follows the width; in the strip the
size is the setting and the width is what it buys. Nothing here pins a
pixel that is really a font measurement: the properties asserted are that
it fits, that it is the narrowest that does, and that nothing is elided.
"""

TIER = "qt"  # a real window, driven by calling its own methods

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFontMetricsF, QMouseEvent

from sottovoce import menu as m
from sottovoce import window as w
from sottovoce.lyrics_provider import TrackLyrics

from helpers import (
    APP,
    SYNCED,
    finish_fit,
    finish_move,
    go_compact,
    land,
    load,
    resize_and_lay_out,
    snapshot,
    tell,
    visible_keys,
)


# -- sizing the strip to the song -----------------------------------------
#
# The arithmetic is pure and lives in tests/test_geometry.py, including the
# cap and the docked case. What is checked here is the wiring: what gets
# measured, when it is allowed to move the window, and what stops it.

# Lines chosen to land between the narrowest window and the offscreen
# platform's cap (800pt screen, so 400), which is what makes a fitted width
# something other than one of the two clamps.
FITTED = TrackLyrics(
    synced=[
        (1.0, "a middling line"),
        (5.0, "a rather longer line than that"),
        (9.0, "short"),
    ]
)
# Far past any cap: one outlier that must not widen the whole song.
OUTLIER = TrackLyrics(
    synced=[(1.0, "short"), (5.0, "an extravagantly long line " * 8)]
)
# Hangul that romanises to something much longer than it is. Syllables
# picked for the ratio rather than the meaning: 쌍 is five Latin letters,
# and five letters of the pronunciation type are wider than one hangul
# glyph of the sung type by enough that no font substitution can reverse
# it. The obvious fixture ("안녕하세요 반갑습니다") does not: 174 against 170.
ROMANISED = TrackLyrics(synced=[(1.0, "쌍쌍쌍쌍쌍쌍"), (5.0, "잘 가")])


def press(window, point):
    """A left-button press at a point in the window, as Qt delivers it."""
    window.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(point),
            QPointF(window.mapToGlobal(point)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def widest_sung(window, lyrics):
    """The widest line, measured here rather than asked of the window."""
    window._current.ensurePolished()
    metrics = QFontMetricsF(window._current.font())
    return max(metrics.horizontalAdvance(text) for _, text in lyrics.synced)


def expected_width(window, text_width):
    screen = window.screen() or APP.primaryScreen()
    return w.fitted_window_width(
        text_width,
        window._scale,
        w._MIN_WIDTH,
        w.width_cap(screen.availableGeometry().width()),
    )


def room_for_text(window):
    """How much of the window's width is the line's, at the scale in force.

    The gutters are asked for rather than assumed, because they follow the
    type size and the whole point of these tests is that neither the size
    nor the font is a constant.
    """
    return window.width() - 2 * w.compact_text_gutter(window._scale)


def screen_cap(window):
    screen = window.screen() or APP.primaryScreen()
    return w.width_cap(screen.availableGeometry().width())


def test_fitting_is_on_by_default_and_only_acts_in_compact(make_window):
    """The one default-on setting in the app, and it can be: it is reached
    only from inside a layout that is itself opt-in and default off, so the
    plain synced-lyrics window is untouched either way."""
    window = make_window()
    assert window._fit_to_song is True
    assert window._compact_applied is False
    assert window._fitting is False

    go_compact(window)
    assert window._fitting is True


def test_the_strip_sizes_itself_when_a_song_arrives(make_window):
    """The properties of the fit, none of which is a pixel count.

    This used to assert the width landed under 460, which passed on macOS
    and failed on the Linux runner at 465. Nothing was wrong: the fixture's
    longest line measures 246pt in the font macOS resolves and about 321pt
    in the one fontconfig picks, and 465 is exactly ceil(321) plus the two
    gutters. The bound was a font measurement in disguise, so the test was
    really asserting which machine it ran on.

    What it should protect is what the feature promises, and all of it
    survives the font changing underneath.
    """
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    widest = widest_sung(window, FITTED)

    # It is the width the pure arithmetic asks for, given what was measured.
    assert window.width() == expected_width(window, widest)

    # Neither bound is what decided it, so the fit below is the real answer
    # rather than a clamp. Checked first, because the tightness only means
    # anything when nothing else got in the way.
    assert w._MIN_WIDTH < window.width() < screen_cap(window)

    # The widest line fits, and by less than a pixel: as small as the strip
    # can be while still showing the whole of it.
    assert 0 <= room_for_text(window) - widest < 1

    # Which is the same thing said where the user would see it: nothing on
    # screen is elided.
    assert window._current.text() == window._full_text[window._current]
    assert "…" not in window._current.text()


def test_a_song_too_wide_for_the_screen_is_capped_and_elides(make_window):
    """The other side of it. Past the cap the line does not fit and says so,
    and the window stops at the cap rather than growing past the screen."""
    window = make_window()
    go_compact(window)
    load(window, OUTLIER)
    finish_fit(window)

    assert window.width() == screen_cap(window)
    assert room_for_text(window) < widest_sung(window, OUTLIER)
    window._on_position_update(snapshot(position=6.0))
    APP.processEvents()
    assert window._current.text().endswith("…")


def test_the_fit_is_right_whatever_the_font_is(make_window):
    """The guard the stale bound should have been.

    A fitted width is a font measurement plus two gutters, so any test that
    names a pixel is really naming a font. Measured across the families this
    machine has, the fixture's longest line runs from 237pt (Times New
    Roman) to 351pt (Menlo) — a 48% spread that straddles the 316pt where a
    strip stops fitting inside 460, which is exactly how a green suite on
    one platform became a red one on another.

    So the property is asserted against whatever the font turns out to be,
    over several real families rather than the one the platform happens to
    default to. Nothing here is skipped when a platform is short of fonts:
    one family is still a family.
    """
    from PySide6.QtGui import QFontDatabase

    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)

    # Real families, taken from the platform rather than named: a hard-coded
    # family list would be the same portability bug one level up. The
    # leading-dot names are the system's own internal faces and are skipped
    # over as candidates, not skipped as tests.
    available = [f for f in QFontDatabase.families() if not f.startswith(".")]
    original = window._family_stack
    seen = {}
    try:
        for family in [original, *available]:
            stack = family if family is original else f'"{family}"'
            window._family_stack = stack
            window._restyle()
            widest = widest_sung(window, FITTED)
            window._fit_width(animate=False)
            finish_fit(window)
            assert 0 <= room_for_text(window) - widest < 1, family
            assert w._MIN_WIDTH <= window.width() <= screen_cap(window), family
            seen[round(widest)] = window.width()
            if len(seen) >= 4:
                break
    finally:
        window._family_stack = original
        window._restyle()

    # And the families really did disagree, or the loop above proved
    # nothing: a test that only ever saw one measurement is the old one.
    assert len(seen) > 1, f"only one text width seen across {len(available)} families"


def test_the_fitted_width_follows_the_chosen_type_size(make_window):
    """The same song at a larger size needs a wider window, and the room it
    leaves for the line still matches what that line now measures. Stated as
    a relation between the sizes rather than as five numbers, because the
    numbers are the font's and the relation is the app's."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)

    widths = []
    for size in w.COMPACT_TEXT_SIZES:
        window._set_compact_text_size(size)
        finish_fit(window)
        APP.processEvents()
        assert 0 <= room_for_text(window) - widest_sung(window, FITTED) < 1
        widths.append(window.width())

    assert widths == sorted(widths)
    assert widths[-1] > widths[0]


def test_the_strip_does_not_resize_on_a_line_change(make_window):
    """The point of measuring the whole song: as small as it can be
    WITHOUT MOVING while the song plays. A window that re-sized itself line
    by line would twitch its way through a verse."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    fitted = window.width()

    for position in (2.0, 6.0, 10.0):
        window._on_position_update(snapshot(position=position))
        APP.processEvents()
        assert window._fit_anim is None
        assert window.width() == fitted


def test_a_new_song_resizes_again(make_window):
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    fitted = window.width()

    load(window, SYNCED, track_id="t2")  # much shorter lines
    finish_fit(window)
    assert window.width() < fitted
    assert window.width() == expected_width(window, widest_sung(window, SYNCED))


def test_the_romanisation_line_is_measured_too(make_window):
    """It is a row the strip shows, and a long romanised line can be the
    widest thing in a song even in its smaller type."""
    window = make_window()
    go_compact(window)
    window._view_model.romanisation_enabled = False
    load(window, ROMANISED)
    finish_fit(window)
    without = window.width()

    window._set_romanisation(True)
    finish_fit(window)
    with_romanisation = window.width()

    window._pron.ensurePolished()
    pron = QFontMetricsF(window._pron.font())
    romanised = max(
        pron.horizontalAdvance(window._view_model.pronunciation_for(text))
        for _, text in ROMANISED.synced
    )
    sung = widest_sung(window, ROMANISED)
    assert romanised > sung, "the fixture no longer exercises this"
    assert with_romanisation > without
    assert with_romanisation == expected_width(window, romanised)


def test_one_outlier_line_cannot_widen_the_whole_song(make_window):
    window = make_window()
    go_compact(window)
    load(window, OUTLIER)
    finish_fit(window)
    # From the AVAILABLE width, which is what the window fits against.
    assert window.width() == screen_cap(window)


def test_the_song_takes_the_width_and_leaves_the_type_alone(make_window):
    """The song chooses the width; the type size is the user's and does not
    move with it. Without that there would be no width to find at all — the
    type would grow exactly as fast as the window, so a line that does not
    fit at one width would not fit at any.

    "The song moved it" is said as "the width is the fitted one", not as
    "the width is no longer 460": whether the fit happens to land on the
    width it started from is a fact about the font, and on some platform it
    will.
    """
    window = make_window()
    go_compact(window)
    users_width = window._shapes.recall(True).width
    scale = window._scale

    load(window, FITTED)
    finish_fit(window)
    assert window.width() == expected_width(window, widest_sung(window, FITTED))
    assert window._scale == scale
    assert window._shapes.recall(True).width == users_width  # the user's own, untouched


def test_the_strips_height_does_not_move_when_its_width_does(make_window):
    """A consequence of holding the scale, and the reason height adaptation
    needs no code: the floor comes off the same scale."""
    window = make_window()
    go_compact(window)
    height = window.height()
    floor = window.minimumHeight()

    load(window, FITTED)
    finish_fit(window)
    assert window.height() == height
    assert window.minimumHeight() == floor


def test_the_full_layout_is_unaffected(make_window):
    window = make_window()
    window.resize(460, 220)
    APP.processEvents()
    load(window, FITTED)
    APP.processEvents()
    assert window._fit_anim is None
    assert window.width() == 460


def test_the_fitted_width_never_becomes_the_full_layouts(make_window):
    """Each layout keeps the width the USER gave it. A fitted width is the
    song's and does not follow the window out of the strip."""
    window = make_window()
    window.resize(500, 220)
    APP.processEvents()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    # The song's width, whatever it works out to be in this font. Asserting
    # it is "not 500" would be asserting that the font does not happen to
    # produce 500, which is not a claim about the app.
    assert window.width() == expected_width(window, widest_sung(window, FITTED))

    window._set_compact(False)
    APP.processEvents()
    assert window.width() == 500


def test_a_manual_resize_turns_the_fitting_off(make_window):
    """Rather than fighting the user. Answered at the START of the drag, so
    the type scale follows the edge live instead of being pinned for the
    length of the gesture and jumping when it is let go."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    assert window._fit_to_song is True

    press(window, QPoint(2, 40))  # the left edge: inside the resize margin
    assert window._fit_to_song is False
    assert window._settings.value("window/fit_to_song", type=bool) is False
    assert window._menu.is_checked(m.FIT_TO_SONG) is False


def test_dragging_the_window_does_not_turn_the_fitting_off(make_window):
    """Moving is not resizing. Only an edge is the user taking the width
    back."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)

    press(window, QPoint(200, 40))  # the middle: a drag
    assert window._fit_to_song is True


def test_turning_it_off_gives_the_users_width_back(make_window):
    window = make_window()
    window.resize(500, 220)
    APP.processEvents()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    assert window.width() != 500

    window._set_fit_to_song(False)
    finish_fit(window)
    assert window.width() == 500
    # And the type is where it was throughout: the strip's size is the
    # user's to name, and the width was never what set it.
    assert window._scale == w.compact_scale(window._compact_text_size)


def test_the_setting_survives_a_restart(make_window):
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    window._save_settings()
    window._settings.sync()

    reopened = make_window()
    assert reopened._fit_to_song is False
    assert reopened._menu.is_checked(m.FIT_TO_SONG) is False


def test_reduce_motion_changes_the_size_without_travelling(make_window):
    """There is nothing here but travel, so it arrives instantly and the
    window is the same shape either way."""
    window = make_window()
    go_compact(window)
    tell(window, reduce_motion=True)
    load(window, FITTED)
    assert window._fit_anim is None
    assert window.width() == expected_width(window, widest_sung(window, FITTED))


def test_the_resize_travels_by_default(make_window):
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    assert window._fit_anim is not None
    assert window._fit_anim.duration() == w._MOVE_MS
    assert window._fit_anim.easingCurve().type() == w._MOVE_CURVE
    finish_fit(window)
    assert window._fit_anim is None


def test_the_entry_is_offered_only_where_it_can_act(make_window):
    window = make_window()
    window._refresh_menu()
    assert m.FIT_TO_SONG not in visible_keys(window)

    go_compact(window)
    assert m.FIT_TO_SONG in visible_keys(window)

    window._set_compact(False)
    assert m.FIT_TO_SONG not in visible_keys(window)


def test_nothing_to_fit_to_leaves_the_width_alone(make_window):
    """A song with no lyrics yet, or none at all: the width stays where it
    was rather than snapping to the floor."""
    window = make_window()
    window.resize(500, 220)
    APP.processEvents()
    go_compact(window)
    window._on_track_change(snapshot())
    APP.processEvents()
    assert window.width() == 500


def test_a_hidden_strip_is_fitted_before_it_flies_home(make_window):
    """The flight holds the window's real geometry and hands it back on
    landing, so a song that changed while the strip was away has to be
    answered before the journey rather than after it."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    window._set_lyrics_visible(False)
    land(window)

    load(window, FITTED)  # a new song, with the window away
    assert window._fit_anim is None  # refused: the flight owns the geometry

    window._set_lyrics_visible(True)
    land(window)
    assert window.width() == expected_width(window, widest_sung(window, FITTED))


def test_a_move_lands_a_resize_rather_than_abandoning_it(make_window):
    """The travel to a remembered position writes this window's position
    too. A window left at a waypoint would stay there, because nothing
    would ask again."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    load(window, FITTED)
    target = window._fit_anim.endValue()
    assert window._fit_anim is not None

    window._move_to(QPoint(80, 90))
    finish_move(window)
    assert window._fit_anim is None
    assert window.width() == target.width()


def test_the_flight_does_not_take_a_waypoint_home(make_window):
    """It captures the window's real geometry and hands it back on
    landing, so a resize in flight has to be landed first."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    load(window, FITTED)
    target = window._fit_anim.endValue()

    window._set_lyrics_visible(False)
    land(window)
    window._set_lyrics_visible(True)
    land(window)
    assert window.width() == target.width()


def test_shutdown_saves_the_width_it_was_heading_for(make_window):
    """Not the one it happened to be passing through."""
    window = make_window()
    window.apply_saved_visibility()
    go_compact(window)
    load(window, FITTED)
    target = window._fit_anim.endValue()

    window._shutdown()
    assert window._settings.value("window/size").width() == target.width()


# -- the strip's own type size --------------------------------------------


def elided_now(window):
    """What the sung row is actually showing, which in a strip is the line
    cut to fit rather than the line."""
    return window._current.text()


def test_the_strips_type_is_the_setting_and_not_the_width(make_window):
    """The bug this replaced: the type scale followed the window's width,
    so the room for a line and the line grew together and widening the
    strip could not show one more character of it."""
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    APP.processEvents()
    scale = window._scale

    for width in (300, 460, 900, 1400):
        resize_and_lay_out(window, width)
        assert window._scale == scale, width
        assert window._scale == w.compact_scale(window._compact_text_size)


def test_the_full_layout_still_takes_its_type_from_its_width(make_window):
    """Untouched, which is the layers rule: the setting above is reachable
    only from inside the strip and may not change the plain window."""
    window = make_window()
    for width in (300, 460, 900):
        resize_and_lay_out(window, width, 260)
        assert window._scale == w._scale_for(width)


def test_widening_the_strip_shows_more_of_the_line(make_window):
    """The whole point of decoupling them. Measured on the elided text
    rather than on a width: what the user is looking at is how much of the
    line is on screen."""
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    load(window, TrackLyrics(synced=[(1.0, "a line long enough that a narrow strip has to cut it short")]))
    window._on_position_update(snapshot(position=2.0))
    APP.processEvents()

    seen = []
    for width in (300, 500, 800, 1200):
        resize_and_lay_out(window, width)
        seen.append(len(elided_now(window)))
    assert seen == sorted(seen)
    assert seen[-1] > seen[0]


def test_the_strips_height_follows_its_type_size(make_window):
    window = make_window()
    go_compact(window)
    APP.processEvents()

    heights = []
    for size in w.COMPACT_TEXT_SIZES:
        window._set_compact_text_size(size)
        finish_fit(window)
        APP.processEvents()
        assert window._scale == w.compact_scale(size), size
        assert window.height() == w.min_window_height(
            window._scale, compact=True
        ), size
        heights.append(window.height())
    assert heights == sorted(heights)
    assert heights[-1] > heights[0]


def test_the_size_is_the_only_thing_that_moves_the_strips_height(make_window):
    """A width change must leave it alone, or the strip would be a window
    changing shape in two directions every time a song arrived."""
    window = make_window()
    go_compact(window)
    window._set_fit_to_song(False)
    APP.processEvents()
    height = window.height()
    for width in (300, 700, 1100):
        resize_and_lay_out(window, width)
        assert window.height() == height, width


def test_the_strip_offers_no_vertical_resize(make_window):
    """Its height has one right answer, so an edge that showed a resize
    cursor and then refused would be worse than one that never claimed to.
    A press there moves the window instead."""
    window = make_window()
    go_compact(window)
    APP.processEvents()
    assert not window._hit_edges(QPoint(window.width() // 2, 1))
    assert not window._hit_edges(QPoint(window.width() // 2, window.height() - 1))
    # The sides still resize, and they are what the width is dragged by.
    assert window._hit_edges(QPoint(1, window.height() // 2)) & Qt.Edge.LeftEdge
    assert (
        window._hit_edges(QPoint(window.width() - 1, window.height() // 2))
        & Qt.Edge.RightEdge
    )


def test_the_full_layout_still_resizes_vertically(make_window):
    window = make_window()
    APP.processEvents()
    assert window._hit_edges(QPoint(window.width() // 2, 1)) & Qt.Edge.TopEdge
    assert (
        window._hit_edges(QPoint(window.width() // 2, window.height() - 1))
        & Qt.Edge.BottomEdge
    )


def test_dragging_a_side_of_the_strip_keeps_its_derived_height(make_window):
    """The drag path has its own height arithmetic, and it used to compute
    the floor from the width it was landing on. In a strip the width has
    nothing to say about it."""
    window = make_window()
    go_compact(window)
    APP.processEvents()
    height = window.height()
    window._press_global = QPoint(0, 0)
    window._press_geometry = window.geometry()
    window._resize_edges = Qt.Edge.RightEdge
    window._apply_resize(QPoint(120, 0))
    APP.processEvents()
    assert window.height() == height
    assert window.width() == window._press_geometry.width() + 120


def test_the_size_survives_a_restart(make_window):
    window = make_window()
    go_compact(window)
    window._set_compact_text_size(28)
    window._save_settings()
    window._settings.sync()

    reopened = make_window()
    assert reopened._compact_text_size == 28
    reopened._refresh_menu()
    assert reopened._menu.chosen(m.COMPACT_SIZE) == 28


def test_a_stored_size_that_is_not_a_preset_falls_back(make_window):
    """The same rule the speech rate is held to: a hand-edited or outgrown
    preference is not honoured, because the menu could not show it and the
    user would have no way back."""
    window = make_window()
    window._settings.setValue("window/compact_text_size", 9)
    window._settings.sync()

    reopened = make_window()
    assert reopened._compact_text_size == w.DEFAULT_COMPACT_TEXT_SIZE


def test_the_size_menu_appears_only_inside_the_strip(make_window):
    window = make_window()
    window._refresh_menu()
    assert window._menu.is_visible(m.COMPACT_SIZE) is False

    go_compact(window)
    window._refresh_menu()
    assert window._menu.is_visible(m.COMPACT_SIZE) is True


def test_changing_the_size_refits_the_window_to_the_song(make_window):
    """The widest line is a different width at a different size, so the
    fit has to be asked again — and it is the same travel a new song
    takes."""
    window = make_window()
    go_compact(window)
    load(window, FITTED)
    finish_fit(window)
    small = window.width()

    window._set_compact_text_size(28)
    finish_fit(window)
    APP.processEvents()
    assert window.width() > small
    assert window.width() == expected_width(window, widest_sung(window, FITTED))


def test_changing_the_size_outside_the_strip_moves_nothing(make_window):
    """Off is off: the full layout's shape is not this setting's business,
    and the value is still recorded for the next time the strip is worn."""
    window = make_window()
    window.resize(460, 260)
    APP.processEvents()
    before = (window.size(), window._scale)

    window._set_compact_text_size(28)
    APP.processEvents()
    assert (window.size(), window._scale) == before
    assert window._compact_text_size == 28
