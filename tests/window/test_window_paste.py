"""The one window in this app that takes a keyboard.

It exists because the lyrics window cannot: that one is frameless, refuses
focus, shows without activating and is mouse-only by design, and a text
field is the single thing none of that can support. So the properties
worth pinning here are the ones that say it is a DIFFERENT KIND of window
from the one it serves, plus what it hands over when the user is done.

The pass it starts is covered where it belongs — through a real menu click
in test_window_menu_clicks.py, and as pure state in test_view_model.py.
"""

TIER = "qt"  # a real widget tree, on the offscreen platform

import pytest

from PySide6.QtCore import Qt

from sottovoce import paste_window as pw
from sottovoce.paste_window import PasteWindow

from helpers import APP


@pytest.fixture
def paste():
    """One window, closed however the test leaves it.

    WA_DeleteOnClose is set by the app rather than by this class, so a
    window this fixture makes has to be closed by hand — and closing one
    the test already closed is what the guard is for.
    """
    made = []

    def factory(song=""):
        window = PasteWindow(song=song)
        made.append(window)
        return window

    yield factory
    for window in made:
        try:
            window.close()
        except RuntimeError:
            pass  # handed its lines over and closed itself
    APP.processEvents()


# -- what kind of window it is ---------------------------------------------


def test_it_accepts_focus_where_the_lyrics_window_refuses_it(paste):
    """The whole reason it exists. Every flag the overlay sets to stay out
    of the user's way is a flag that would make typing into it impossible,
    so this one sets none of them.
    """
    window = paste()
    flags = window.windowFlags()
    assert not (flags & Qt.WindowType.WindowDoesNotAcceptFocus)
    assert not (flags & Qt.WindowType.FramelessWindowHint)
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) is False


def test_it_stays_on_top_because_the_window_it_serves_does(paste):
    """The one thing it borrows from the overlay. Without this it would be
    the one window a user could lose behind the one they opened it for."""
    assert paste().windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_it_names_the_song_it_was_opened_for(paste):
    window = paste(song="Blue Hour · Someone")
    assert window._song.text() == "Blue Hour · Someone"
    assert window._song.isVisibleTo(window) is True
    assert paste()._song.isVisibleTo(window) is False  # nothing to name


# -- what comes out of it --------------------------------------------------


def test_nothing_can_be_started_from_an_empty_box(paste):
    """A button that cannot do anything says so, rather than taking a press
    and beginning a pass over no lines."""
    window = paste()
    assert window._start.isEnabled() is False
    assert window._count.text() == pw.EMPTY_COUNT

    window.set_text("   \n\n  ")
    assert window._start.isEnabled() is False

    window.set_text("a line")
    assert window._start.isEnabled() is True


def test_the_row_under_the_box_counts_what_would_be_tapped(paste):
    """The count is of TARGETS rather than of lines typed, which is the
    number that matters: blank lines and LRC syntax are not things anybody
    taps through."""
    window = paste()
    window.set_text("one\n\ntwo\nthree\n")
    assert window._count.text() == "3 lines to tap"

    window.set_text("[ar:Someone]\n[00:01.00] just the one\n")
    assert window._count.text() == "1 line to tap"


def test_starting_hands_over_the_targets_and_closes(paste):
    """Both halves. The lines leave, and the window does — a paste window
    still standing after the pass began would be a second answer to what
    the song's lyrics are."""
    window = paste()
    handed = []
    window.lines_ready.connect(handed.append)
    window.set_text("[00:01.00] first\n\n[00:09.00] second\n")

    window._on_start()
    APP.processEvents()

    assert handed == [["first", "second"]]
    assert window.isVisible() is False


def test_escape_closes_it_without_handing_anything_over(paste):
    """A window somebody opened by mistake has to be dismissable the way
    every other sheet on the system is."""
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    window = paste()
    handed = []
    window.lines_ready.connect(handed.append)
    window.set_text("some lines")
    window.show()
    APP.processEvents()

    window.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )
    APP.processEvents()

    assert handed == []
    assert window.isVisible() is False


# -- and the window that opens it ------------------------------------------


def test_a_second_press_raises_the_one_that_is_open(make_window):
    """Two boxes both offering to sync the same song would be two answers
    to what its lyrics are."""
    window = make_window()
    window._open_paste_window()
    first = window._paste_window
    assert first is not None

    window._open_paste_window()

    assert window._paste_window is first
    first.close()
    APP.processEvents()
    assert window._paste_window is None


def test_shutdown_takes_the_paste_window_with_it(make_window):
    """It is a top-level of its own with no parent to take it down, and it
    holds a connection back into the window being torn down."""
    window = make_window()
    window._open_paste_window()
    assert window._paste_window is not None

    window._shutdown()
    APP.processEvents()

    assert window._paste_window is None
