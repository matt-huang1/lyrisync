"""Qt brought up once, and the helpers more than one file needs.

Every test file in this directory drives a real Qt object tree on the
offscreen platform, so this is where the platform is chosen, PySide6 is
checked, and the one QApplication is made. Nothing here is macOS-only:
everything native is guarded off-Cocoa in the code under test and is
asserted structurally rather than by calling into AppKit. CI installs the
system libraries PySide6 needs, and the import check below only catches
the case where that has gone wrong, so a broken runner degrades to a
visible skip of this whole directory instead of a collection error.

A helper lives here when a second file needs it and nowhere else: the
fixtures are in conftest.py, and a helper only one file uses stays in
that file where it can be read next to what it serves.
"""

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# exc_type=ImportError, not the default ModuleNotFoundError: PySide6 imports
# fine with its shared libraries missing and fails later on "libEGL.so.1:
# cannot open shared object file", which is an ImportError but not a
# ModuleNotFoundError.
pytest.importorskip(
    "PySide6.QtWidgets",
    reason="PySide6 unusable (missing system Qt libraries?)",
    exc_type=ImportError,
)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - platform plugin missing
    pytest.skip(f"Qt cannot start: {exc}", allow_module_level=True)

from sottovoce import accessibility  # noqa: E402
from sottovoce import frontmost  # noqa: E402
from sottovoce import menu as m  # noqa: E402
from sottovoce import proximity  # noqa: E402
from sottovoce import window as w  # noqa: E402
from sottovoce.lyrics_provider import TrackLyrics  # noqa: E402
from sottovoce.player_monitor import (  # noqa: E402
    PlaybackState,
    PlayerSnapshot,
)

# Captured before any fixture stubs it, so the one test that needs the
# real worker body can still reach it.
REAL_ARTWORK_RUN = w.ArtworkTask.run

PLAIN = TrackLyrics(plain="first line\nsecond line\nthird line")
SYNCED = TrackLyrics(synced=[(1.0, "one"), (5.0, "two")])
KOREAN_SYNCED = TrackLyrics(synced=[(1.0, "안녕하세요"), (5.0, "잘 가")])


def snapshot(
    track_id="t1", title="Song", state=PlaybackState.PLAYING, position=0.0
):
    return PlayerSnapshot(
        state=state,
        track_id=track_id,
        title=title,
        artist="Artist",
        album="Album",
        duration_ms=200000,
        position_seconds=position,
    )


def load(window, lyrics, track_id="t1"):
    window._on_track_change(snapshot(track_id=track_id))
    window._on_fetch_finished(track_id, lyrics, True)
    window._title_card_until = 0.0  # skip the 2s "song announces itself" card
    window._render()
    APP.processEvents()


def land(window):
    """Run the hide/show flight to its end without waiting it out.

    The window now travels to and from the menu bar item, so "hidden" is
    where the journey lands rather than what the click does — the same
    shape as finish_move for the travel to a remembered position.
    """
    if window._flight_anim is not None:
        window._flight_anim.setCurrentTime(window._flight_anim.duration())
    APP.processEvents()


def visible_keys(window):
    """The menu's visible entries, in menu order, as menu.py keys."""
    return tuple(key for key in m.MENU_ORDER if window._menu.is_visible(key))


def pixels_of(image, rect=None):
    """The raw bytes of an image, or of one rectangle of it.

    The copy is BOUND before it is read, and that is the whole of this
    function. ``image.copy(rect).constBits()`` hands back a memoryview onto
    a temporary QImage that PySide does not keep alive for it, so the copy
    can be released before ``tobytes()`` reads the buffer and the read
    returns whatever the allocator has since put there.

    That is not a theory. Written the short way, the straight-band test
    failed 9 runs in 20 of its own node, with the panel's colour reading as
    zeroes in a band that had been drawn correctly; written this way, 0 in
    40. It never failed in a full-suite run, which is how it survived a
    whole milestone being read as an intermittent bug in the painting.
    """
    held = image if rect is None else image.copy(rect)
    return held.constBits().tobytes()


def panel_pixels(window, damaged, straight, ratio):
    """What _paint_panel puts on an image for ``damaged``.

    ``straight`` picks the route by lying to the band test, which is what
    makes the two comparable: the same call, the same painter settings,
    the same window, one branch apart.

    ``ratio`` is the screen's device pixel ratio, because that is what the
    hairline's width is derived from and 1 is the one value a Mac never
    has. The image is allocated in DEVICE pixels and told its ratio, so
    the rasteriser works where the difference could actually show.
    """
    from PySide6.QtGui import QImage, QPainter

    image = QImage(
        int(window.width() * ratio),
        int(window.height() * ratio),
        QImage.Format.Format_ARGB32,
    )
    image.setDevicePixelRatio(ratio)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    with patch.object(w.LyricsWindow, "_straight_band", lambda self, r: straight), \
            patch.object(w.LyricsWindow, "devicePixelRatioF", lambda self: ratio):
        window._paint_panel(painter, QRectF(damaged))
    painter.end()
    return image


# -- the apps a position is learned against -------------------------------


VSCODE = "com.microsoft.VSCode"
SAFARI = "com.apple.Safari"


NAMES = {
    VSCODE: "Code",
    SAFARI: "Safari",
    "com.apple.Notes": "Notes",
    "com.sottovoce.sottovoce": "SottoVoce",
}


_UNSET = object()


def activate(window, bundle_id, name=_UNSET):
    """What NSWorkspace hands the window: an identifier AND a name, taken
    from the same announcement."""
    if name is _UNSET:
        name = NAMES.get(bundle_id)
    window._on_app_activated(frontmost.AppIdentity(bundle_id, name))


def finish_move(window):
    """Run the travel animation to its end without waiting it out."""
    if window._move_anim is not None:
        window._move_anim.setCurrentTime(window._move_anim.duration())
    APP.processEvents()


def settle(window, bundle_id):
    """An activation that has been frontmost long enough to act on."""
    activate(window, bundle_id)
    window._settle_timer.stop()
    window._debounce._since -= w.SETTLE_SECONDS
    window._apply_settled_app()


# -- a cover, and the fades that have to be run out -----------------------


RED_COVER = (200, 40, 40)


def art_snapshot(track_id="t1", url="http://cover", kind="track"):
    return PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id=track_id,
        track_kind=kind,
        title="Song",
        artist="Artist",
        album="Album",
        duration_ms=200000,
        position_seconds=0.0,
        artwork_url=url,
    )


def settle_tint(window):
    """Run the cross-fade to its end without waiting out the animation."""
    if window._tint_anim is not None:
        window._tint_anim.setCurrentTime(w._TINT_FADE_MS)
    APP.processEvents()

# windowOpacity() does not hand back what it was given: Qt stores it as an
# 8-bit alpha, so 0.15 reads back as 38/255 = 0.14902. Measured, not
# guessed at — the first version of these tests compared exactly and failed
# on the third decimal. The same 8-bit residue the album tint's luminance
# sweep runs into, and the tolerance for reading a real window's opacity.
OPACITY_STEP = 1 / 255


def settle_yield(window):
    """Run the fade to its end without waiting it out — the same shape as
    land() for the flight."""
    if window._yield_anim is not None:
        window._yield_anim.setCurrentTime(window._yield_anim.duration())
    APP.processEvents()


# -- the accessibility display settings, and the strip --------------------


def tell(window, **options):
    """Hand the window a set of display options, the way the watcher
    would."""
    window._on_display_options_changed(accessibility.DisplayOptions(**options))
    APP.processEvents()


def go_compact(window):
    """Switch to the compact layout with the pointer definitely elsewhere.

    The offscreen platform parks its cursor at (10, 10) and these windows
    are placed over it, so a plain _set_compact would find itself hovered
    and the controls would come out at once — which is right, and is not
    what any of these tests is about.
    """
    with pointer_at(window, away_from(window)):
        window._set_compact(True)


def over(window):
    """A screen coordinate inside the window's frame."""
    frame = window.frameGeometry()
    return QPoint(frame.center())


def away_from(window):
    """A screen coordinate well clear of the window, release margin and
    all. Derived from the frame rather than named, so it stays outside a
    window that has been resized or moved."""
    frame = window.frameGeometry()
    return QPoint(
        frame.right() + proximity.RELEASE_MARGIN + 100,
        frame.bottom() + proximity.RELEASE_MARGIN + 100,
    )


def pointer_at(window, point):
    """Put the pointer at a screen coordinate for the length of a block.

    QCursor.pos() is whatever the offscreen platform last saw and cannot
    be driven from here, so the ONE thing that asks it is replaced.
    Everything above that is the real code — the frame test, the region
    test, the hysteresis and the gate all run for real on a real
    coordinate, which is what lets one helper drive both layers that read
    this poll.
    """
    return patch.object(w.LyricsWindow, "_pointer_position", lambda self: point)


def hover(window, inside):
    """One turn of the pointer poll, with the pointer inside the window or
    well clear of it."""
    with pointer_at(window, over(window) if inside else away_from(window)):
        window._check_pointer()
    finish_reveal(window)


def finish_reveal(window):
    """Run the controls' fade to its end without waiting it out."""
    if window._reveal_anim is not None:
        window._reveal_anim.setCurrentTime(window._reveal_anim.duration())
    APP.processEvents()


def shown(window):
    """On screen, so it has a window handle to route a press through."""
    window.show()
    APP.processEvents()
    return window


def finish_fit(window):
    """Run the width animation to its end without waiting it out."""
    if window._fit_anim is not None:
        window._fit_anim.setCurrentTime(window._fit_anim.duration())
    APP.processEvents()


def resize_and_lay_out(window, width, height=None):
    """Resize a window nobody has shown, the way a shown one behaves.

    A hidden widget defers its resize event until it is shown, so these
    windows never run one; _relayout is the app's own answer to that and
    is what every path that changes the shape itself calls.
    """
    window.resize(width, window.height() if height is None else height)
    window._relayout()
    APP.processEvents()


def move_and_notice(window, point):
    """Move a window nobody has shown, and let it notice where it landed.
    moveEvent is deferred for the same reason resizeEvent is."""
    window.move(point)
    window._update_docked()
    APP.processEvents()


# -- yielding to the pointer ----------------------------------------------


def poll(window, point):
    """One turn of the pointer poll with the pointer at a screen point,
    then everything it started run to its end."""
    with pointer_at(window, point):
        window._check_pointer()
    finish_move(window)
    finish_ghost(window)
    finish_reveal(window)


def press_at(window, point):
    """A left press landing on the window at a screen point."""
    local = QPointF(point - window.frameGeometry().topLeft())
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        local,
        QPointF(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def release():
    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(1, 1),
        QPointF(1, 1),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def finish_ghost(window):
    """Run the ghost fade to its end without waiting it out."""
    if window._ghost_anim is not None:
        window._ghost_anim.setCurrentTime(window._ghost_anim.duration())
    APP.processEvents()


def arrive(window):
    """The pointer arrives over the middle of the window."""
    poll(window, over(window))


def with_mode(window, mode):
    """Turn the layer on with the pointer definitely elsewhere, and give
    the window a position with room around it in every direction."""
    window.apply_saved_visibility()
    land(window)
    window.move(600, 400)
    APP.processEvents()
    with pointer_at(window, away_from(window)):
        window._set_proximity_mode(mode)
    return window
