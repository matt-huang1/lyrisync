"""The window where somebody reads what is about to be sent, and says so.

## Why there is a window

The same reason ``paste_window.py`` is one, plus a sharper one of its own.
The lyrics window is frameless, refuses focus, shows without activating,
and is 460 points of HUD floating over somebody's work: everything about
it is built for reading one line of a song. What has to happen here is a
person reading a whole submission — four fields of metadata and two full
sets of lyrics — and deciding whether it goes to a public database under
nobody's name but their own. That is not a thing to do in a HUD, and it is
not a thing to do behind a control that could be pressed by accident.

So it is an ordinary window: system-drawn, focusable, resizable, scrolling,
with the platform's own controls. It borrows exactly one thing from the
window it serves, staying on top, because the window it serves does.

## What it is for

Consent, and the consent is to CONTENT rather than to an idea. The window
opens, asks LRCLIB what it is holding for this song, and only then shows
anything: what it shows is the exact ``Submission`` that would be sent,
built from LRCLIB's own answer. Both bodies are on screen, both scroll,
and neither is summarised. The button underneath sends that, and nothing
sends without it being pressed.

Everything after the press is mechanical and slow enough to need saying
out loud: a challenge, seconds of proof of work, and a POST. It runs off
the UI thread, it says where it has got to, and the button beside it stops
it. Stopping before the POST costs nothing but the CPU already spent.

## What it does not do

It does not decide anything. Whether this song may be published is
``publish.py``'s, twice over, and both answers are shown here rather than
made here. It does not touch ``.user_syncs/`` except to have the record of
a publication written beside the sync afterwards, which is the provider's
own method and the only writer this file reaches.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from sottovoce import publish
from sottovoce.failure import describe

logger = logging.getLogger(__name__)

# Tall rather than wide, because what fills it is two columns of lyrics.
# Set by eye and said so: it is a starting size for a resizable window and
# nothing is measured against it.
_REVIEW_SIZE = (520, 620)
# And what it is before there is anything to read, which is a sentence and
# a button. LOOKED AT rather than reasoned about: at the review size, a
# window holding one line of text put it in the middle of 620 points of
# nothing, because the layout has to give the spare room to something and
# with the bodies hidden the only takers were the gaps between labels.
# Two things fix it and both are needed — a filler that takes the spare
# room so the text stays at the top, and a smaller window so there is less
# of it to take.
_WAITING_SIZE = (420, 190)

PUBLISH_LABEL = "Publish to LRCLIB"
AGAIN_LABEL = "Try again"
CANCEL_LABEL = "Cancel"
CLOSE_LABEL = "Close"
SYNCED_HEADING = "Synced lyrics · yours, and what this adds"
PLAIN_HEADING = "Plain lyrics · LRCLIB's own, sent back unchanged"
INTRO = (
    "This is exactly what will be sent. Nothing goes to LRCLIB until you "
    "press the button below."
)

# Where the window has got to. Its own small vocabulary rather than
# publish.py's, because these are the states of a WINDOW: what it shows and
# what its two buttons say. The stages of the exchange are reported into
# the CHECKING and WORKING states of this one.
CHECKING = "checking"
REVIEW = "review"
WORKING = "working"
DONE = "done"
STOPPED = "stopped"
IMPOSSIBLE = "impossible"  # refused: a fact about the song, so no retry
BROKEN = "broken"          # failed: a fact about a moment, so a retry


class _VerifySignals(QObject):
    finished = Signal(object)  # publish.Verification


class VerifyTask(QRunnable):
    """The fresh check, off the UI thread.

    One request, and the answer is what the window is built from. It is a
    task rather than an inline call for the ordinary reason and one extra:
    LRCLIB's response time has been measured at anything from 61ms to
    nearly five seconds, and this window appears the instant the menu entry
    is clicked.
    """

    def __init__(self, *, title, artist, album, duration_ms, lrc_text) -> None:
        super().__init__()
        self.signals = _VerifySignals()
        self._asked = dict(
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
            lrc_text=lrc_text,
        )

    def run(self) -> None:
        try:
            answer = publish.verify(**self._asked)
        except Exception:  # noqa: BLE001 - reported, never swallowed
            logger.exception("the publish check failed")
            answer = publish.Verification(refusal=publish.NO_RECORD)
        try:
            self.signals.finished.emit(answer)
        except RuntimeError:
            pass  # the app tore down the signal object mid-check


class _SendSignals(QObject):
    progress = Signal(object)  # publish.Progress
    finished = Signal(object)  # publish.Result


class SendTask(QRunnable):
    """The challenge, the proof of work and the POST, in one worker.

    One worker rather than three, because the three are one sequence: the
    nonce is only good for the challenge it was solved against, and a
    challenge handed between workers is a challenge whose clock somebody
    has to carry.

    ``stop`` is a ``threading.Event`` the window sets when it is closed or
    cancelled. It is read by the solver every 50,000 hashes and at each
    step of the exchange, which is what makes closing this window
    something other than a wait.
    """

    def __init__(self, provider, track_id: str, submission, stop) -> None:
        super().__init__()
        self.signals = _SendSignals()
        self._provider = provider
        self._track_id = track_id
        self._submission = submission
        self._stop = stop

    def run(self) -> None:
        try:
            result = publish.send(
                self._submission,
                on_progress=self._say,
                should_stop=self._stop.is_set,
            )
            if result.published:
                result = self._remember(result)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            logger.exception("the publish failed")
            result = publish.Result(publish.FAILED, reason=str(exc))
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            pass  # the app tore down the signal object mid-publish

    def _remember(self, result):
        """Write the record of the publication, beside the sync it is about.

        In the worker rather than back on the UI thread, so it happens
        whatever the user does with the window next, and so the file is on
        disk before the menu is asked whether this sync has been published.

        A failure here does not turn a publication into a failure — it
        already happened, and saying otherwise would be the app lying about
        somebody else's database. It changes the sentence instead, because
        a record that was not written means the menu will offer this again.
        """
        try:
            self._provider.record_published(
                self._track_id, self._submission.synced_lyrics
            )
        except OSError:
            logger.exception("the publication could not be recorded")
            return replace(
                result,
                reason=f"{result.reason}, but this Mac could not note it down",
            )
        return result

    def _say(self, progress) -> None:
        try:
            self.signals.progress.emit(progress)
        except RuntimeError:
            pass  # the app tore down the signal object mid-publish


class PublishWindow(QWidget):
    """One song, one submission, one decision.

    Emits ``published`` with the track id once LRCLIB has accepted it, so
    the window that opened this can bring its menu back in line. Nothing
    else leaves here: the sync, the provider and the record of what was
    sent are all handled where they belong.
    """

    published = Signal(str)

    def __init__(
        self,
        provider,
        *,
        track_id: str,
        title: str,
        artist: str,
        album: str,
        duration_ms: int,
        lrc_text: str,
        song: str = "",
        pool: Optional[QThreadPool] = None,
    ) -> None:
        super().__init__()
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Publish this sync")
        self.resize(*_WAITING_SIZE)

        self._provider = provider
        self._track_id = track_id
        self._asked = dict(
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
            lrc_text=lrc_text,
        )
        self._pool = pool or QThreadPool.globalInstance()
        self._stop = threading.Event()
        self._submission = None
        self._state = CHECKING
        self._showing = False

        self._song = QLabel(song)
        self._song.setWordWrap(True)
        self._song.setVisible(bool(song))
        heading = self._song.font()
        heading.setBold(True)
        self._song.setFont(heading)

        self._intro = QLabel(INTRO)
        self._intro.setWordWrap(True)
        self._metadata = QLabel("")
        self._metadata.setWordWrap(True)
        self._metadata.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._status = QLabel("")
        self._status.setWordWrap(True)

        self._synced = _reader()
        self._plain = _reader()

        # A splitter rather than two fixed boxes: which of the two somebody
        # wants to read all of depends on what they are checking, and the
        # one thing this window may not do is make either of them the
        # summary of the other.
        self._bodies = QSplitter(Qt.Orientation.Vertical)
        self._bodies.addWidget(_stacked(QLabel(SYNCED_HEADING), self._synced))
        self._bodies.addWidget(_stacked(QLabel(PLAIN_HEADING), self._plain))
        self._bodies.setVisible(False)
        # Takes the room the bodies are not using, so a window with one
        # sentence in it reads from the top rather than from the middle.
        self._filler = QWidget()
        self._filler.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )

        self._secondary = QPushButton(CANCEL_LABEL)
        self._secondary.clicked.connect(self._on_secondary)
        self._primary = QPushButton(PUBLISH_LABEL)
        self._primary.setDefault(True)
        self._primary.clicked.connect(self._on_primary)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._secondary)
        buttons.addWidget(self._primary)

        layout = QVBoxLayout(self)
        layout.addWidget(self._song)
        layout.addWidget(self._intro)
        layout.addWidget(self._metadata)
        layout.addWidget(self._bodies, 1)
        layout.addWidget(self._status)
        # Below the status rather than above it, so what is left to read
        # stacks from the top: with the filler between them the sentence
        # was pushed to the bottom of the window and the heading stayed at
        # the top, which reads as two unrelated things.
        layout.addWidget(self._filler, 1)
        layout.addLayout(buttons)

        self._apply_state(
            CHECKING, publish.progress_text(publish.Progress(publish.CHECKING))
        )

    # -- the check -----------------------------------------------------------

    def begin(self) -> None:
        """Ask LRCLIB what it has. The window is already on screen saying
        so, because the answer may take seconds and a window that appeared
        only once it arrived would be a menu entry that did nothing."""
        task = VerifyTask(**self._asked)
        task.signals.finished.connect(self._on_verified)
        self._pool.start(task)

    def _on_verified(self, verification) -> None:
        if self._stop.is_set():
            return  # the window was closed while the check was in flight
        if verification.failure is not None:
            self._apply_state(
                BROKEN,
                f"LRCLIB could not be asked · {describe(verification.failure)}",
            )
            return
        if not verification.ready:
            self._apply_state(IMPOSSIBLE, verification.refusal)
            return
        self._submission = verification.submission
        self._metadata.setText(
            "\n".join(
                f"{label}: {value}" for label, value in self._submission.rows()
            )
        )
        self._synced.setPlainText(self._submission.synced_lyrics)
        self._plain.setPlainText(self._submission.plain_lyrics)
        self._apply_state(REVIEW, "")

    # -- the send ------------------------------------------------------------

    def _on_primary(self) -> None:
        """The one button that sends anything, and the one press that does.

        In REVIEW it is the confirmation; after a failure it is a fresh
        attempt at the same submission, which is the same content the user
        already read and said yes to. It is never armed in any other state,
        so there is no path where a press sends something unseen.

        With no submission in hand it is the CHECK that failed rather than
        the send, and trying again means asking LRCLIB again. The same
        button, because from where the user is standing it is the same
        thing: that did not work, do it again.
        """
        if self._state not in (REVIEW, BROKEN, STOPPED):
            return
        self._stop.clear()
        if self._submission is None:
            self._apply_state(
                CHECKING,
                publish.progress_text(publish.Progress(publish.CHECKING)),
            )
            self.begin()
            return
        self._apply_state(
            WORKING, publish.progress_text(publish.Progress(publish.ASKING))
        )
        task = SendTask(self._provider, self._track_id, self._submission, self._stop)
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(self._on_sent)
        self._pool.start(task)

    def _on_progress(self, progress) -> None:
        if self._state == WORKING:
            self._status.setText(publish.progress_text(progress))

    def _on_sent(self, result) -> None:
        if result.published:
            self.published.emit(self._track_id)
            self._apply_state(DONE, result.reason)
            return
        if result.outcome == publish.STOPPED:
            self._apply_state(STOPPED, "nothing was sent")
            return
        self._apply_state(
            BROKEN if result.may_try_again else IMPOSSIBLE, result.reason
        )

    def _on_secondary(self) -> None:
        """Cancel and Close are one button, because they are one act: stop
        whatever is happening and go away. What differs is only whether
        anything was happening."""
        self.close()

    # -- what the window looks like ------------------------------------------

    def _apply_state(self, state: str, said: str) -> None:
        """The one place that decides what is shown and what the buttons
        say. A state machine rather than six paths that each set five
        widgets, because a window whose button says Publish in a state that
        cannot publish is the failure mode worth designing out."""
        self._state = state
        self._status.setText(said)
        reviewable = (
            state in (REVIEW, WORKING, DONE, BROKEN, STOPPED)
            and self._submission is not None
        )
        if reviewable != self._showing:
            # Only when it CHANGES, so a window somebody has resized is not
            # put back to our size every time the status line moves on.
            self._showing = reviewable
            self.resize(*(_REVIEW_SIZE if reviewable else _WAITING_SIZE))
        self._bodies.setVisible(reviewable)
        self._filler.setVisible(not reviewable)
        self._metadata.setVisible(reviewable)
        self._intro.setVisible(state == REVIEW)
        self._primary.setVisible(state in (REVIEW, BROKEN, STOPPED))
        self._primary.setText(PUBLISH_LABEL if state == REVIEW else AGAIN_LABEL)
        self._secondary.setText(
            CANCEL_LABEL if state in (CHECKING, REVIEW, WORKING) else CLOSE_LABEL
        )

    @property
    def state(self) -> str:
        """Which of the six the window is in. Read by the suite, which
        asserts the state rather than the widgets wherever the state is
        what the test is about."""
        return self._state

    @property
    def submission(self):
        """What would be sent, once the check has answered."""
        return self._submission

    # -- opening and closing -------------------------------------------------

    def present(self) -> None:
        """On screen, and in front. Not enough on its own: under the
        accessory activation policy the APP has to come forward first, and
        ``window.bring_to_front`` does that immediately before this is
        called. See paste_window.present, which measured it."""
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """Closing is cancelling, and it is the only cancel there is.

        The flag is set before anything else so a solve in flight stops at
        its next chunk rather than running to a nonce nobody will send, and
        so the check's answer, if it lands after this, finds a window that
        has gone.
        """
        self._stop.set()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        """Escape closes it, like any sheet. The only key this window has
        an opinion about."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


def _reader() -> QPlainTextEdit:
    """A box that shows text and cannot change it. Read-only rather than
    disabled: what is in it has to be legible and selectable, because it is
    the thing being agreed to."""
    box = QPlainTextEdit()
    box.setReadOnly(True)
    return box


def _stacked(heading: QLabel, box: QPlainTextEdit) -> QWidget:
    """A heading and the box under it, as one thing the splitter can hold."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(heading)
    layout.addWidget(box, 1)
    return holder
