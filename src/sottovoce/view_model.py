"""Pure display logic for the lyrics window. No Qt, no I/O.

The window feeds player/fetch events in and renders the resulting
``Display``. Keeping this separate from the widget makes the state
machine — including the stale-fetch guard — testable without a display
server.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sottovoce import backoff
from sottovoce.failure import FetchFailure, describe, reached_lrclib
from sottovoce.lyrics_provider import TrackLyrics
from sottovoce.player_monitor import PlaybackState, PlayerSnapshot
from sottovoce.romanize import contains_hangul, romanize_korean
from sottovoce.sync import current_line_index
from sottovoce.sync_session import (
    SyncSession,
    sync_targets,
    sync_targets_from_lines,
)


class Mode(Enum):
    IDLE = "idle"          # Spotify closed/stopped or no track
    FETCHING = "fetching"  # lyrics lookup in flight
    SYNCED = "synced"      # timed lines advancing with playback
    PLAIN = "plain"        # lyrics exist but carry no timestamps
    SYNCING = "syncing"    # tap-to-sync pass in progress over plain lyrics
    NO_LYRICS = "no_lyrics"
    NON_MUSIC = "non_music"  # DJ narration, ads: header only, empty body
    ERROR = "error"        # fetch failed (network/server); not cached, will retry


# What stands between the song and whoever made it, everywhere the two are
# shown together: the window's header, both terminal tools. A middle dot
# rather than an em dash — the dash is a piece of punctuation and reads as
# one, which at header size in a line of two proper nouns is a word joined
# to a sentence. The dot is a separator and nothing else.
#
# One definition because there were two, and two copies of a format string
# is how a window and a terminal tool come to disagree about the same song.
HEADER_SEPARATOR = " · "


def header_text(snapshot: PlayerSnapshot) -> str:
    """The song and its artist, as every surface shows them."""
    return f"{snapshot.title}{HEADER_SEPARATOR}{snapshot.artist}"


@dataclass(frozen=True)
class Display:
    mode: Mode
    header: str = ""       # "Song · Artist"
    previous: str = ""
    current: str = ""
    upcoming: str = ""
    plain_text: str = ""   # full text, only in PLAIN mode
    pronunciation: str = ""  # romanised current line, SYNCED/SYNCING modes
    progress: str = ""     # "12 / 34 lines", SYNCING mode only
    # Why the lookup failed, ERROR mode only. Never shown unasked: the
    # window keeps it behind an affordance beside the message, because the
    # answer to "why" is wanted by a few people and by nobody twice.
    detail: str = ""


# The first retry, and the only one this used to have. Kept as a name here
# because it is what "will retry" has always meant to a reader of this
# module; the schedule that grows from it lives in backoff.py, with the
# measurement that chose it.
RETRY_INTERVAL_SECONDS = backoff.BASE_SECONDS


def card_yields(display: Display) -> bool:
    """Whether the title card should hand the window back before its time
    is up.

    The card fills the gap between a track change and lyrics arriving. It
    is a floor on how long that gap looks, not a floor on how long it
    lasts: once the fetch has landed there is no gap left to fill, and
    holding the song's name up for the rest of the two seconds is a delay
    this app is choosing to add.

    "Something to show" is the whole rule, and it is deliberately not
    "the fetch finished". A synced song joined before its first line has
    lyrics and nothing to put on screen yet — ending the card there would
    trade two seconds of the song's name for ten seconds of an empty
    window. So the question is asked of the display, not of the fetch.
    """
    return bool(display.plain_text or display.current)


class LyricsViewModel:
    """State machine behind the window.

    Each mutating method returns True when the visible display changed, so
    the caller re-renders only when needed. ``fetch_completed`` ignores
    results for tracks that are no longer current (the fetch raced a track
    change); the provider has already cached them by then.
    """

    def __init__(self) -> None:
        self.romanisation_enabled = False
        self._mode = Mode.IDLE
        self._track_id: Optional[str] = None
        self._identity: Optional[tuple] = None
        self._header = ""
        self._lyrics: Optional[TrackLyrics] = None
        self._index = -1
        self._error_at = 0.0
        self._failure: Optional[FetchFailure] = None
        # How many lookups in a row have failed, and it counts across
        # TRACKS rather than resetting with each one. That is the whole
        # point: what the growing interval is protecting against is an
        # outage, and an outage is not a property of the song on screen.
        # Reset by an answer from LRCLIB and by nothing else — see
        # fetch_completed.
        self._failures = 0
        self._suspended_mode: Optional[Mode] = None
        self._has_hangul_synced = False
        self._has_hangul_sync = False
        self._sync: Optional[SyncSession] = None
        self._sync_return_mode: Optional[Mode] = None

    def track_changed(self, snapshot: PlayerSnapshot) -> bool:
        """Returns True when the new track needs a lyrics fetch."""
        if not snapshot.has_track:
            self._reset()
            return False
        identity = (snapshot.track_kind, snapshot.track_id)
        if identity == self._identity:
            # Duplicate announcement of the item already shown (metadata
            # settling, transient monitor blips): keep the display — never
            # flash back to loading or dispatch a redundant fetch. ERROR is
            # the exception: fresh metadata is worth a new attempt.
            self._suspended_mode = None
            self._header = header_text(snapshot)
            if self._mode is Mode.ERROR and snapshot.is_music_track:
                self._mode = Mode.FETCHING
                return True
            return False
        self._identity = identity
        self._suspended_mode = None
        self._track_id = snapshot.track_id
        self._header = header_text(snapshot)
        self._lyrics = None
        self._index = -1
        # A failure belongs to the song it happened on. Cleared on the way
        # in rather than only on the way out, so a new song in FETCHING can
        # never be carrying the last song's reason.
        self._failure = None
        self._has_hangul_synced = False
        self._has_hangul_sync = False
        # A sync pass belongs to the song it started on.
        self._sync = None
        self._sync_return_mode = None
        if not snapshot.is_music_track:
            # DJ narration, ads: header only, nothing to look up.
            self._mode = Mode.NON_MUSIC
            return False
        self._mode = Mode.FETCHING
        return True

    def fetch_completed(
        self,
        track_id: str,
        lyrics: Optional[TrackLyrics],
        ok: bool = True,
        now: float = 0.0,
        failure: Optional[FetchFailure] = None,
        from_service: bool = True,
    ) -> bool:
        """Returns False for stale results, which must not be displayed.
        ``ok=False`` means the fetch errored: show the retryable
        "unavailable" state rather than claiming there are no lyrics;
        ``now`` timestamps the failure for the retry schedule and
        ``failure`` says what went wrong, for anyone who asks.

        ``from_service`` says whether LRCLIB is what answered. It is the
        only thing that resets the failure count, and the distinction is
        not pedantic: during an outage every song the user has played
        before still answers instantly from the cache, and a counter that
        reset on those would put the interval back to 30 seconds on every
        uncached track — a backoff that stops backing off exactly when
        somebody is listening to music.
        """
        if track_id != self._track_id:
            return False
        if not ok:
            resolved = Mode.ERROR
            self._lyrics = None
            self._error_at = now
            self._failure = failure
            if reached_lrclib(failure):
                self._failures += 1
        elif lyrics is None:
            resolved = Mode.NO_LYRICS
            self._lyrics = None
        else:
            resolved = Mode.SYNCED if lyrics.synced else Mode.PLAIN
            self._lyrics = lyrics
            # Synced only: romanisation renders under a single current
            # line, which exists in SYNCED mode and during a sync pass, but
            # never in the scrolling PLAIN body. A pass computes its own
            # flag from the lines it is about to stamp.
            self._has_hangul_synced = bool(lyrics.synced) and any(
                contains_hangul(text) for _, text in lyrics.synced
            )
        if ok and from_service:
            # LRCLIB answered, whatever it answered: a track it has no
            # lyrics for is a healthy service saying so. The count and the
            # pause it may have asked for both go.
            self._failures = 0
            self._failure = None
        if self._sync is not None:
            # A sync pass is modal and user-driven: a fetch landing under it
            # (a retry, a re-announcement) must not tear it down mid-song.
            # The session owns its own copy of the lines, so the new lyrics
            # simply become where cancelling lands.
            self._sync_return_mode = resolved
            return False
        if self._mode is Mode.IDLE and self._suspended_mode is not None:
            # Player is stopped right now; remember the outcome for the
            # resume-restore instead of showing lyrics over the idle state.
            self._suspended_mode = resolved
            return False
        self._mode = resolved
        return True

    @property
    def failures(self) -> int:
        """Consecutive lookups that LRCLIB did not answer. Read by the
        album warm, which has no business making speculative requests of a
        service that is currently failing."""
        return self._failures

    def retry_interval(self) -> float:
        """How long this app is waiting before asking again. Grows with the
        failures behind it, and is floored by any pause LRCLIB asked for —
        backoff.py holds both rules and the measurements that chose them."""
        asked = self._failure.retry_after if self._failure is not None else None
        return backoff.delay(self._failures, asked)

    def retry_due(self, now: float) -> bool:
        """True when a failed fetch should be re-attempted. Flips the mode
        back to FETCHING, so a True return means: dispatch a fetch now."""
        if self._mode is not Mode.ERROR:
            return False
        if now - self._error_at < self.retry_interval():
            return False
        self._mode = Mode.FETCHING
        return True

    def retry_now(self) -> bool:
        """The user asked for the retry rather than waiting for it.

        Returns True when there was something to retry, which means:
        dispatch a fetch now. The schedule goes back to the start, because
        somebody who knows the service is back has better information than
        a counter does — and if they are wrong, the next failure starts
        again at 30 seconds, which is where it would have started anyway.

        What this does NOT reset is a pause LRCLIB asked for. That one is
        not ours to waive: the schedule is this app being considerate and
        the pause is a condition of being allowed to ask at all, so a retry
        pressed during one goes out, is refused at the door, and says so.
        """
        if self._mode is not Mode.ERROR:
            return False
        self._failures = 0
        self._failure = None
        self._mode = Mode.FETCHING
        return True

    def position_changed(self, position_seconds: Optional[float]) -> bool:
        if self._mode is not Mode.SYNCED or position_seconds is None:
            return False
        index = current_line_index(self._lyrics.synced, position_seconds)
        if index == self._index:
            return False
        self._index = index
        return True

    def player_state_changed(self, state: PlaybackState) -> bool:
        if state in (PlaybackState.NOT_RUNNING, PlaybackState.STOPPED):
            if self._mode is Mode.IDLE:
                return False
            # Suspend rather than reset: a stop can be a one-poll blip mid
            # item-switch, and resuming the same track fires no track-change
            # event to rebuild from.
            self._suspended_mode = self._mode
            self._mode = Mode.IDLE
            return True
        if self._mode is Mode.IDLE and self._suspended_mode is not None:
            self._mode = self._suspended_mode
            self._suspended_mode = None
            return True
        return False

    def timeline(self) -> Optional[tuple[list, int]]:
        """(synced lines, current index) while in SYNCED mode — what the
        window's anticipatory line-fade scheduler needs."""
        if self._mode is Mode.SYNCED and self._lyrics is not None:
            return self._lyrics.synced, self._index
        return None

    @property
    def has_korean_lyrics(self) -> bool:
        """True when hangul is on screen in a form romanisation can sit
        under — controls whether the romanisation menu entry is offered.
        Never in PLAIN mode: the scrolling body has no current line, so the
        toggle would do nothing there."""
        if self._mode is Mode.SYNCING:
            return self._has_hangul_sync
        return self._has_hangul_synced

    # -- tap-to-sync -------------------------------------------------------

    @property
    def track_id(self) -> Optional[str]:
        return self._track_id

    @property
    def sync_session(self) -> Optional[SyncSession]:
        """The sync pass in progress, or None."""
        return self._sync

    def _stampable_lines(self) -> list[str]:
        """The lines a sync pass would stamp, from whichever lyrics are in
        hand.

        Plain text when there is any. Otherwise the synced lines, which is
        what a re-sync works from: a completed pass stamps every non-blank
        plain line, so the stored lines ARE the song's lines, already timed
        once. Deriving them this way means a re-sync needs no plain lyrics
        on disk or on the network — it still works after ``.lyrics_cache/``
        is cleared, and offline.
        """
        if self._lyrics is None:
            return []
        if self._lyrics.plain:
            return sync_targets(self._lyrics.plain)
        if self._lyrics.synced:
            return sync_targets_from_lines(text for _, text in self._lyrics.synced)
        return []

    @property
    def lines_to_stamp(self) -> list[str]:
        """The lines this song is offering a pass right now, for whoever
        has to decide whether an interrupted pass is still about them.

        Empty when the song has no lines of its own, which is not the same
        as "no pass is possible": a pasted pass has words nothing else
        knows, and ``sync_session.resume_targets`` is what reads that
        distinction.
        """
        return self._stampable_lines()

    @property
    def pass_may_start(self) -> bool:
        """Whether this song is in a state a pass can run in at all.

        Not the same question as ``sync_menu_entry``, which decides what to
        OFFER and refuses to re-sync somebody else's timings. Taking an
        interrupted pass of your own back up is not that offer, so it is
        held to the weaker rule: there is something to stamp, or there are
        no lyrics and the words came from you.
        """
        if self._mode in (Mode.PLAIN, Mode.SYNCED):
            return bool(self._stampable_lines())
        return self.paste_sync_offered

    def sync_menu_entry(self, has_user_sync: bool) -> Optional[str]:
        """Label for the tap-to-sync context-menu entry, or None when no
        pass can be started.

        Plain lyrics can always be stamped. A song that already shows as
        synced is only re-offered when the sync on screen is the user's own
        — LRCLIB's timings are not theirs to overwrite.
        """
        if not self._stampable_lines():
            return None
        if self._mode is Mode.PLAIN:
            return "Re-sync this song" if has_user_sync else "Sync this song"
        if self._mode is Mode.SYNCED and has_user_sync:
            return "Re-sync this song"
        return None

    @property
    def paste_sync_offered(self) -> bool:
        """Whether a sync could be started from lyrics the user supplies.

        Exactly the gap the ordinary entry cannot cover: a music track
        whose lyrics could not be had, either because the service could not
        answer (ERROR) or because it has none (NO_LYRICS). Anywhere else
        there are lines in hand and ``sync_menu_entry`` is the way in, so
        offering both would be two entries answering one question.

        It is at its most useful in ERROR, which is the point: tap-to-sync
        needed plain lyrics from LRCLIB, so during an outage the one
        feature that works entirely offline could not be started.
        """
        return self._mode in (Mode.ERROR, Mode.NO_LYRICS) and bool(self._track_id)

    def begin_sync(self) -> bool:
        """Start a tap-to-sync pass over the lines in hand. Possible from
        PLAIN (a first sync) and SYNCED (a re-sync); returns False (and
        changes nothing) when there is nothing to stamp."""
        if self._mode not in (Mode.PLAIN, Mode.SYNCED):
            return False
        return self._start_sync(self._stampable_lines())

    def resume_sync(self, lines: list[str], stamps: list[float]) -> bool:
        """Take an interrupted pass back up, stamps and all.

        The lines are the RECORD's rather than this song's, because the
        record is the only thing that knows which words those stamps were
        made against — and the caller has already asked
        ``sync_session.resume_targets`` whether the two still agree. What
        is checked here is only that a pass could run at all.
        """
        if not self.pass_may_start:
            return False
        return self._start_sync(list(lines), list(stamps))

    def begin_sync_from(self, lines: list[str]) -> bool:
        """Start a pass over lines that came from somewhere else — today,
        the paste window.

        Allowed only where ``paste_sync_offered`` is, so this cannot become
        a second way to overwrite lyrics that are already on screen. The
        lines are the caller's, already reduced to tap targets: this does
        not know or care that a person typed them.
        """
        if not self.paste_sync_offered:
            return False
        return self._start_sync(list(lines))

    def _start_sync(
        self, targets: list[str], stamps: Optional[list[float]] = None
    ) -> bool:
        """One pass, however its lines were found and whatever it already
        knows. Returns False (and changes nothing) when there is nothing to
        stamp."""
        if not targets:
            return False
        self._sync = SyncSession(targets, stamps=stamps)
        # Where cancelling lands: a re-sync that is abandoned must put the
        # existing sync back, not drop the song to plain lyrics — and a
        # pasted one abandoned must go back to saying why there are none.
        self._sync_return_mode = self._mode
        self._has_hangul_sync = any(contains_hangul(line) for line in targets)
        self._mode = Mode.SYNCING
        return True

    def end_sync(self) -> bool:
        """Leave sync mode, discarding the session, and fall back to the
        lyrics it started from. Returns True when a pass was actually in
        progress."""
        if self._sync is None:
            return False
        self._sync = None
        restored = self._sync_return_mode or Mode.PLAIN
        self._sync_return_mode = None
        if self._mode is Mode.SYNCING:
            self._mode = restored
        if self._suspended_mode is Mode.SYNCING:
            # Cancelled while the player was stopped: resuming must restore
            # the lyrics, not a session that no longer exists.
            self._suspended_mode = restored
        return True

    def begin_reload(self, track_id: str) -> bool:
        """Re-run the lyrics lookup for a track whose stored lyrics just
        changed underneath us (a sync was saved). Returns False for a track
        that is no longer current — nothing to reload."""
        if track_id != self._track_id or self._track_id is None:
            return False
        self._lyrics = None
        self._index = -1
        self._mode = Mode.FETCHING
        return True

    def pronunciation_for(self, line: str) -> str:
        """Romanised form of a lyric line, or "" when romanisation is off
        or the line has no hangul. Shared by display() and the window's
        predicted line swap."""
        if self.romanisation_enabled and contains_hangul(line):
            return romanize_korean(line)
        return ""

    def _reset(self) -> None:
        self._mode = Mode.IDLE
        self._track_id = None
        self._identity = None
        self._header = ""
        self._lyrics = None
        self._index = -1
        self._failure = None
        # _failures is deliberately NOT reset here. This runs when there is
        # no track at all — Spotify closed, the user stopped — and how many
        # times LRCLIB has just failed is not a fact about the song that
        # went away. Pausing an outage does not end it.
        self._suspended_mode = None
        self._has_hangul_synced = False
        self._has_hangul_sync = False
        self._sync = None
        self._sync_return_mode = None

    def display(self) -> Display:
        mode = self._mode
        if mode is Mode.IDLE:
            return Display(mode=mode, current="Spotify is not playing")
        if mode is Mode.FETCHING:
            # current stays empty: the window renders its animated
            # loading indicator for this mode.
            return Display(mode=mode, header=self._header)
        if mode is Mode.NO_LYRICS:
            return Display(mode=mode, header=self._header, current="no lyrics found")
        if mode is Mode.NON_MUSIC:
            return Display(mode=mode, header=self._header)
        if mode is Mode.ERROR:
            return Display(
                mode=mode,
                header=self._header,
                current="lyrics unavailable, will retry",
                detail=describe(self._failure),
            )
        if mode is Mode.PLAIN:
            return Display(
                mode=mode,
                header=self._header,
                previous="plain lyrics · not synced",
                plain_text=self._lyrics.plain or "",
            )
        if mode is Mode.SYNCING:
            session = self._sync
            current = session.current
            return Display(
                mode=mode,
                header=self._header,
                # The line just stamped stays up: the singer is still
                # partway through it, and watching it run out is the cue
                # for the next tap.
                previous=session.previous,
                current=current,
                # The next two lines, so the tapper can see what is coming
                # rather than reading the current line for the first time
                # at the moment they need to stamp it.
                upcoming="\n".join(session.upcoming(2)),
                pronunciation=self.pronunciation_for(current),
                progress=f"{session.index} / {session.total} lines",
            )
        lines = self._lyrics.synced
        index = self._index
        current = lines[index][1] if index >= 0 else ""
        return Display(
            mode=mode,
            header=self._header,
            previous=lines[index - 1][1] if index >= 1 else "",
            current=current,
            upcoming=lines[index + 1][1] if index + 1 < len(lines) else "",
            pronunciation=self.pronunciation_for(current),
        )
