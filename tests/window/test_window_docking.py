"""Docking to the top, and the shape a docked window is drawn in.

Docking is a command rather than a snap: nothing is magnetic and the
window stays freely draggable. Whether it is docked is asked of the
position on every move rather than held as a flag, and one path builds
both the rounded and the square-topped shape.
"""

TIER = "qt"  # a real window, driven by calling its own methods

from PySide6.QtCore import QPoint, QRect, QRectF, Qt

from sottovoce import vibrancy
from sottovoce import window as w

from helpers import (
    APP,
    finish_move,
    go_compact,
    land,
    move_and_notice,
    panel_pixels,
    pixels_of,
    resize_and_lay_out,
)


# -- docking to the top ---------------------------------------------------


def test_dock_to_top_centres_the_window_under_the_menu_bar(make_window):
    window = make_window()
    screen = window.screen() or APP.primaryScreen()
    geometry = screen.geometry()
    available = screen.availableGeometry()
    window.move(geometry.x() + 40, geometry.y() + 400)
    APP.processEvents()

    window._dock_to_top()
    finish_move(window)

    expected = w.docked_position(
        window.width(),
        (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
        (available.x(), available.y(), available.width(), available.height()),
        window._top_inset(),
    )
    assert (window.pos().x(), window.pos().y()) == expected


def test_dock_leaves_the_window_as_draggable_as_it_found_it(make_window):
    """An explicit command, not a snap: nothing holds the window there."""
    window = make_window()
    window._dock_to_top()
    finish_move(window)
    docked = window.pos()
    window.move(docked.x() + 120, docked.y() + 200)
    APP.processEvents()
    assert window.pos() != docked


def test_dock_records_where_it_put_the_window(make_window):
    """Written from the target rather than from the window: the travel
    takes a phase length, and a save mid-journey would record a waypoint."""
    window = make_window()
    window._dock_to_top()
    saved = window._settings.value("window/pos")
    finish_move(window)
    assert (saved.x(), saved.y()) == (window.pos().x(), window.pos().y())


def test_dock_is_learned_like_the_end_of_a_drag(make_window):
    """The per-app layer would otherwise undo it on the next app switch:
    docking is the user saying where the window goes, which is the same
    thing a drag says."""
    window = make_window()
    window._remember_position = True
    window._frontmost = "com.example.editor"
    window._dock_to_top()
    finish_move(window)
    assert window._positions.peek("com.example.editor") == (
        window.pos().x(),
        window.pos().y(),
    )


def test_dock_moves_where_the_flight_will_put_the_window_back(make_window):
    """Away at the menu bar, the flight is holding the real position and
    hands it back at the end of the journey. Moving the window instead
    would be undone by the landing."""
    window = make_window()
    window.apply_saved_visibility()
    window._set_lyrics_visible(False)
    land(window)
    window._flight_home = (10, 10, window.width(), window.height())

    window._dock_to_top()
    screen = window.screen() or APP.primaryScreen()
    geometry = screen.geometry()
    available = screen.availableGeometry()
    expected = w.docked_position(
        window.width(),
        (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
        (available.x(), available.y(), available.width(), available.height()),
        window._top_inset(),
    )
    assert window._flight_home[:2] == expected


def test_the_safe_area_is_zero_where_it_cannot_be_asked(make_window):
    """Off cocoa there is no NSWindow to ask, and the available area is
    already the whole answer."""
    window = make_window()
    assert window._nswindow() is None
    assert window._top_inset() == 0


# -- the docked shape ------------------------------------------------------


def dock_the_window(window):
    """Put the window exactly where docking would, without the travel."""
    x, y = window._docked_anchor()
    move_and_notice(window, QPoint(x, y))
    return x, y


def test_the_window_knows_when_it_is_docked(make_window):
    window = make_window()
    move_and_notice(window, QPoint(80, 300))
    assert window._docked is False

    dock_the_window(window)
    assert window._docked is True


def test_dragging_it_away_gives_the_rounded_shape_straight_back(make_window):
    window = make_window()
    dock_the_window(window)
    assert window._docked is True

    move_and_notice(window, window.pos() + QPoint(1, 0))
    assert window._docked is False


def test_a_width_change_under_a_docked_window_keeps_it_docked(make_window):
    """Docking centres on the screen, so the docked position moves with the
    width. The window is re-docked rather than centre-anchored, and it has
    to still recognise itself afterwards."""
    window = make_window()
    go_compact(window)
    dock_the_window(window)
    assert window._docked is True

    window._resize_width_to(window.width() + 180, animate=False)
    APP.processEvents()
    assert window._docked is True
    assert window.pos().toTuple() == window._docked_anchor()


def test_a_resize_alone_can_undock_a_window(make_window):
    """A window that stays put while its width changes is no longer
    centred, and says so."""
    window = make_window()
    dock_the_window(window)
    resize_and_lay_out(window, window.width() + 60)
    assert window._docked is False


def test_the_docked_panel_has_square_top_corners(make_window):
    """The whole visual claim, read off the pixels. The very corner pixel
    is the panel when docked and the backdrop when not."""
    window = make_window()
    resize_and_lay_out(window, 400, 90)

    def corner_pixels(docked):
        window._docked = docked
        image = panel_pixels(window, QRect(0, 0, 400, 90), False, 2.0)
        return (
            image.pixel(0, 0),
            image.pixel(image.width() - 1, 0),
            image.pixel(0, image.height() - 1),
            image.pixel(image.width() - 1, image.height() - 1),
        )

    rounded = corner_pixels(False)
    squared = corner_pixels(True)
    # Nothing is painted into a rounded corner: all four are the empty fill.
    assert len(set(rounded)) == 1
    # Docked, the two at the top are painted and the two underneath are not.
    assert squared[0] != rounded[0]
    assert squared[1] != rounded[1]
    assert squared[2] == rounded[2]
    assert squared[3] == rounded[3]


def test_the_rounded_path_is_what_drawroundedrect_always_drew(make_window):
    """One builder serves both shapes, so the undocked window must come out
    byte for byte as it did — otherwise "square top corners when docked"
    would have quietly restyled every other window in the app."""
    from PySide6.QtGui import QImage, QPainter

    for ratio in (1.0, 2.0):
        size, radius = 200, float(w._CORNER_RADIUS)
        rect = QRectF(0, 0, size, size)

        def render(use_path):
            image = QImage(int(size * ratio), int(size * ratio),
                           QImage.Format.Format_ARGB32)
            image.setDevicePixelRatio(ratio)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.white)
            if use_path:
                painter.drawPath(w._panel_path(rect, radius, False))
            else:
                painter.drawRoundedRect(rect, radius, radius)
            painter.end()
            return pixels_of(image)

        assert render(True) == render(False), f"the outline moved at {ratio}x"


def test_a_docked_band_is_still_drawn_straight_and_is_the_same_pixels(make_window):
    """The fast path is about the sides, which are straight in both shapes.
    Asserted for the docked one rather than assumed, because the whole
    reason the fast path is allowed to exist is that somebody checked."""
    window = make_window()
    window._docked = True
    for top in range(w._CORNER_RADIUS, window.height() - w._CORNER_RADIUS - 8, 7):
        damaged = QRect(0, top, window.width(), 8)
        fast = panel_pixels(window, damaged, True, 2.0)
        slow = panel_pixels(window, damaged, False, 2.0)
        device = QRect(0, int(top * 2), int(window.width() * 2), 16)
        assert pixels_of(fast, device) == pixels_of(slow, device), (
            f"the docked band at y={top} differs"
        )


def test_the_material_is_told_the_same_two_corners(make_window):
    """The blur is a native view under the painted scrim. Rounded at the
    top while the scrim is square would show the desktop through two
    notches at exactly the corners the shape exists to remove."""
    assert vibrancy.masked_corners(False) == vibrancy.ALL_CORNERS
    assert vibrancy.masked_corners(True) == vibrancy.BOTTOM_CORNERS
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_TOP_LEFT == 0
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_TOP_RIGHT == 0
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_BOTTOM_LEFT
    assert vibrancy.BOTTOM_CORNERS & vibrancy.CORNER_BOTTOM_RIGHT


def test_asking_for_the_material_corners_without_one_is_harmless(make_window):
    """Off cocoa there is no material, and this runs on every move."""
    window = make_window()
    assert window._material is None
    window._apply_material_corners()  # must not raise
