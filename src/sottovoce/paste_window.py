"""A small window that exists to hold a text field, and closes again.

## Why there is a second window at all

Tap-to-sync could only ever be started from lyrics LRCLIB had already
answered with, which made it useless in exactly the situation it is best
at: a song nobody has written down, or a service that is down. The lines
have to be able to come from the user, and that means somewhere to put
them.

The lyrics window cannot be that place, and the reasons are all rules it
is built on. It is frameless, it is ``WindowDoesNotAcceptFocus``, it shows
with ``WA_ShowWithoutActivating``, and every interaction on it is
mouse-only — because it is an overlay that must never take the keyboard
away from whatever the user is actually doing. A text field is the one
thing that cannot work under any of that: there is no way to type into a
window that refuses focus, and giving it focus for this would undo the
property the whole design rests on.

So this is a separate top-level window, drawn by the system, with a title
bar and a close button, that appears when it is asked for and is gone the
moment the pass begins. It is the opposite of the lyrics window on purpose:
ordinary, focusable, and made of the platform's own controls, because the
job is typing rather than reading a song.

It IS on top, though, and that is the one thing it borrows. The window it
serves is ``WindowStaysOnTopHint``, so a paste window that was not would
be the one window a user could lose behind the one they were using it for.

## What it does not do

It does not confirm anything, save anything, or reach the provider. It
hands back tap targets and closes; every rule about what a sync is and
where it is written stays where it was. The confirmation for a saved sync
is still inline in the lyrics window, still never a modal.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sottovoce.sync_session import targets_from_paste

logger = logging.getLogger(__name__)

# Big enough that a verse is visible while it is pasted, small enough to be
# obviously temporary. Set by eye and nothing is measured against it: it is
# a starting size for a resizable window.
_INITIAL_SIZE = (420, 380)

HINT = "One line per lyric. Blank lines are skipped, and so are LRC timestamps."
START_LABEL = "Start tap-to-sync"
CANCEL_LABEL = "Cancel"
EMPTY_COUNT = "no lines yet"


def count_text(lines: int) -> str:
    """What the row under the box says. Its own function because it is the
    only sentence here that changes, and the singular case is a real one:
    a chorus pasted on its own is one line."""
    if lines <= 0:
        return EMPTY_COUNT
    return f"{lines} line to tap" if lines == 1 else f"{lines} lines to tap"


class PasteWindow(QWidget):
    """Somewhere to put lyrics, and a button that starts the pass.

    Emits ``lines_ready`` with the tap targets and closes. Nothing else
    leaves this class: the window that opened it owns the song, the player
    and the session.
    """

    lines_ready = Signal(object)

    def __init__(self, song: str = "") -> None:
        super().__init__()
        # Top-level and system-drawn: see the module docstring. On top
        # because the window it serves is.
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("Paste lyrics")
        self.resize(*_INITIAL_SIZE)

        self._song = QLabel(song)
        self._song.setWordWrap(True)
        self._song.setVisible(bool(song))
        # The one bit of styling here, and it is weight rather than colour
        # or size: the song is what this window is ABOUT and the line under
        # it is instructions, and at equal weight they read as two
        # instructions. Everything else is left to the platform on purpose.
        heading = self._song.font()
        heading.setBold(True)
        self._song.setFont(heading)
        hint = QLabel(HINT)
        hint.setWordWrap(True)
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText("Paste the lyrics here")
        self._text.textChanged.connect(self._on_text_changed)
        self._count = QLabel(count_text(0))
        self._cancel = QPushButton(CANCEL_LABEL)
        self._cancel.clicked.connect(self.close)
        self._start = QPushButton(START_LABEL)
        self._start.setDefault(True)
        self._start.setEnabled(False)
        self._start.clicked.connect(self._on_start)

        buttons = QHBoxLayout()
        buttons.addWidget(self._count)
        buttons.addStretch(1)
        buttons.addWidget(self._cancel)
        buttons.addWidget(self._start)

        layout = QVBoxLayout(self)
        layout.addWidget(self._song)
        layout.addWidget(hint)
        layout.addWidget(self._text, 1)
        layout.addLayout(buttons)

    # -- what is in the box --------------------------------------------------

    def targets(self) -> list[str]:
        """The tap targets the box currently holds."""
        return targets_from_paste(self._text.toPlainText())

    def set_text(self, text: str) -> None:
        """For a caller that has the lyrics already. Nothing in the app
        uses it; the suite does, because driving a real paste is the only
        honest way to ask what this window would hand over."""
        self._text.setPlainText(text)

    def _on_text_changed(self) -> None:
        lines = len(self.targets())
        self._count.setText(count_text(lines))
        # A button that cannot do anything says so rather than accepting a
        # press and starting a pass over nothing.
        self._start.setEnabled(lines > 0)

    def _on_start(self) -> None:
        targets = self.targets()
        if not targets:
            return
        logger.info("paste window handed over %d lines", len(targets))
        self.lines_ready.emit(targets)
        self.close()

    # -- opening and closing -------------------------------------------------

    def present(self) -> None:
        """Put it on screen and put the cursor in the box.

        Not enough on its own, and that is worth knowing here rather than
        only at the call site: under the accessory activation policy all
        three of these leave the window inactive and the focus widget None,
        MEASURED on the real platform. The APP has to come forward first,
        which is native and therefore not this module's to do —
        ``window.bring_to_front`` does it, immediately before this is
        called.
        """
        self.show()
        self.raise_()
        self.activateWindow()
        self._text.setFocus()

    def keyPressEvent(self, event) -> None:
        """Escape closes it, like any sheet. The only key this window has an
        opinion about; everything else belongs to the text box."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
