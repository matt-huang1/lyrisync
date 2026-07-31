"""Fetch and cache lyrics from LRCLIB.

Pure logic: given a ``PlayerSnapshot``, return ``TrackLyrics`` or ``None``.
Knows nothing about polling or the UI.

Fallback chain: synced lyrics → plain lyrics → ``None``. Responses are
cached on disk as JSON keyed by Spotify track ID, including negative
results, so a song known to have no lyrics is never re-queried.

Lookups consult ``.user_syncs/`` first: LRC files the user built by hand
with tap-to-sync. That directory is the user's own work, not a cache — it
is written only on an explicit save, is never invalidated or expired, and
nothing in this app deletes from it.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Optional

# Carries the app's version, resolved once from the installed
# distribution's metadata. Re-exported here because this module's callers
# have always read it from this module; what it must never be again is a
# second copy of the version, written by hand and right for one release.
from sottovoce import USER_AGENT
from sottovoce.failure import (
    ATTEMPT_ALBUM,
    ATTEMPT_EXACT,
    ATTEMPT_SEARCH,
    CONNECTION,
    FetchFailure,
    HTTP,
    PAYLOAD,
    TIMEOUT,
    UNKNOWN,
)
from sottovoce.http_client import ConnectionPool
from sottovoce.player_monitor import PlayerSnapshot

logger = logging.getLogger(__name__)

LRCLIB_HOST = "lrclib.net"
LRCLIB_GET_URL = f"https://{LRCLIB_HOST}/api/get"
LRCLIB_SEARCH_URL = f"https://{LRCLIB_HOST}/api/search"
DEFAULT_CACHE_DIR = Path(".lyrics_cache")
# Hand-made syncs. Deliberately NOT under the cache directory: clearing the
# cache is a documented reset, and it must never cost the user a sync they
# tapped out themselves.
DEFAULT_USER_SYNC_DIR = Path(".user_syncs")

_REQUEST_TIMEOUT = 10.0
# One second of slack over the socket's own timeout: the socket is what
# should give up first, and this only exists so a wedged attempt cannot
# hold the fallback chain open forever.
_ATTEMPT_WAIT = _REQUEST_TIMEOUT + 1.0
# How long an attempt gets on its own before the ones below it are asked
# as well.
#
# MEASURED, which is the only reason there is a number here at all. Across
# 30 lookups over 15 real tracks, spaced, in two rounds two minutes apart:
# the FIRST attempt produced the answer 30 times out of 30, and produced it
# in 61ms by median, 103ms at the 95th percentile and 170ms at its slowest.
# Nothing below the album match was ever needed.
#
# 250ms is 4x that median and 1.5x the slowest response in the sample, so
# no lookup in it would have fanned out at all. What that buys is two
# requests of every three not made: LRCLIB is free, runs on donations and
# is under load, and asking it three questions to use the first answer is
# a cost it carries so this app can save a wait that, at these speeds, is
# not there to save.
#
# What it costs is stated too. When LRCLIB IS slow — the same service was
# measured at 0.7 to 4.8 SECONDS per request in an earlier session — the
# hedge fires, the chain fans out exactly as it did before, and the lookup
# is at most 250ms longer than the all-at-once version. Against 4.8s that
# is 5%, and it is paid only in the case the concurrency was for.
_HEDGE_SECONDS = 0.25
# The widest the fallback chain gets, which is also how many connections
# are worth keeping alive.
_CHAIN_WIDTH = 3
# /api/get only matches durations within ~2s, so search results this far
# from Spotify's duration are considered a different recording.
_SEARCH_DURATION_TOLERANCE = 10.0

# [mm:ss.xx] — also tolerates [m:ss] and multiple stamps per line.
# Metadata tags like [ar:...] contain no m:ss pair and never match.
_TIMESTAMP_RE = re.compile(r"\[(\d+):(\d{1,2}(?:\.\d+)?)\]")

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_-]")


class LyricsError(Exception):
    """Transient LRCLIB failure (network trouble, 4xx/5xx, bad payload).
    The outcome is unknown, so callers must not cache it — retrying later
    may succeed.

    Carries a ``FetchFailure`` saying which of those it was and where in
    the fallback chain it happened, so the window can offer the reason to
    anyone who asks for it. The message stays what it always was; a raise
    that gives no failure gets an UNKNOWN one built from it, which is what
    keeps every existing ``LyricsError("...")`` a valid thing to raise.
    """

    def __init__(self, message: object, failure: Optional[FetchFailure] = None) -> None:
        super().__init__(message)
        self.failure = (
            failure
            if failure is not None
            else FetchFailure(kind=UNKNOWN, detail=str(message))
        )

    def at(self, attempt: str) -> "LyricsError":
        """The same failure, told where in the chain it happened.

        The attempt is known one level up from where the error is raised —
        ``_fetch_json`` makes a request and does not know which link of the
        chain it is — so it is stamped on the way past rather than passed
        down. A new exception rather than a mutated one: an exception that
        rewrites itself as it propagates is a poor thing to read a
        traceback from.
        """
        return LyricsError(
            self.args[0] if self.args else str(self),
            replace(self.failure, attempt=attempt),
        )


@dataclass(frozen=True)
class TrackLyrics:
    """Lyrics for one track. At least one of the two fields is set."""

    synced: Optional[list[tuple[float, str]]] = None
    plain: Optional[str] = None

    @property
    def kind(self) -> str:
        return "synced" if self.synced else "plain"


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """Parse LRC text into (timestamp_seconds, line) tuples, sorted by time.

    Lines may carry several timestamps (``[00:12.00][00:55.30] chorus``);
    each becomes its own entry. Lines with no timestamp (metadata, garbage)
    are skipped. Empty lyric lines are kept — they mark instrumental gaps.
    """
    entries: list[tuple[float, str]] = []
    for raw_line in text.splitlines():
        stamps = list(_TIMESTAMP_RE.finditer(raw_line))
        if not stamps:
            continue
        content = raw_line[stamps[-1].end() :].strip()
        for stamp in stamps:
            seconds = int(stamp.group(1)) * 60 + float(stamp.group(2))
            entries.append((seconds, content))
    entries.sort(key=lambda entry: entry[0])
    return entries


_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def _lrclib_pool() -> ConnectionPool:
    """The one door onto LRCLIB, and the connections kept alive to it.

    Module-level rather than per-provider because the point is to outlive
    a single lookup: the handshakes a track pays for are what the next
    track gets for free. Tests never reach it — the socket guard refuses
    first — and it is only ever built on demand, so importing this module
    opens nothing.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(
                LRCLIB_HOST, timeout=_REQUEST_TIMEOUT, limit=_CHAIN_WIDTH
            )
        return _pool


def close_connections() -> None:
    """Drop the kept-alive connections. Called at shutdown so the app does
    not leave sockets open on a public service; instant, because nothing
    is in flight on an idle connection."""
    with _pool_lock:
        pool = _pool
    if pool is not None:
        pool.close()


def _fetch_json(url: str):
    """GET a JSON document. Returns the parsed body, or None on 404 (a
    definitive "not found"). Raises LyricsError for anything whose outcome
    is unknown: network trouble, other HTTP errors, unparseable payload."""
    split = urllib.parse.urlsplit(url)
    path = split.path + (f"?{split.query}" if split.query else "")
    try:
        response = _lrclib_pool().get(path, headers={"User-Agent": USER_AGENT})
    except (OSError, http.client.HTTPException) as exc:
        # The socket's own words go on the record here and nowhere else:
        # this is the log line that has room for them, and the window's
        # reveal is a HUD (see failure.describe).
        logger.warning("GET %s -> error: %s", url, exc)
        raise LyricsError(
            str(exc), FetchFailure(kind=CONNECTION, detail=str(exc))
        ) from exc

    logger.info("GET %s -> %d", url, response.status)
    if response.status == 404:
        return None
    if response.status != 200:
        raise LyricsError(
            f"LRCLIB returned HTTP {response.status}",
            FetchFailure(kind=HTTP, status=response.status),
        )
    try:
        return json.loads(response.body)
    except ValueError as exc:
        raise LyricsError(
            str(exc), FetchFailure(kind=PAYLOAD, detail=str(exc))
        ) from exc


@dataclass(frozen=True)
class _Outcome:
    """What one attempt came back with: a parsed body, a definitive 404
    (``data`` None with no error), or the reason it could not be answered."""

    data: object = None
    error: Optional[BaseException] = None


def _run_attempts(urls: list[str], labels: list[str]) -> Iterator[_Outcome]:
    """Ask the chain, hedged; hand the results back in the order they were
    given, which is PRIORITY order and not completion order.

    ## Why the order is priority and not completion

    The chain used to be sequential, and its cost was the sum of the
    attempts rather than the longest of them — measured against LRCLIB at
    3.3-10.4s for the three-attempt case, where no single attempt took
    more than 4.9s. Nothing about the chain needed to be sequential: the
    attempts do not depend on each other, only the CHOICE between their
    answers does, and that is a question about results rather than about
    when they arrive. That much has not changed, and it is what every rule
    the chain has to keep is stated in terms of.

    ## Why they are no longer all asked at once

    Because they were not needed, and that was MEASURED rather than
    assumed. Over 30 lookups of 15 real tracks, the first attempt answered
    30 times, in 61ms by median. Two of every three requests were being
    made so that an answer nobody would read could arrive at the same time
    as the one they would.

    So an attempt is asked when the chain reaches it, and the ones below
    it are asked early only if it is taking long enough that overlapping
    them would actually save something. ``_HEDGE_SECONDS`` is where that
    line is and carries the measurement it came from.

    Three cases, all of them the same rule:

    - The attempt ANSWERS quickly. Nothing below it is ever asked, which
      is exactly what the caller would have done with the answers anyway.
    - The attempt 404s. The next one is asked at once, with no hedge to
      wait out: there is nothing left to overlap with.
    - The attempt is SLOW. At the hedge the rest go out beside it, and
      from there this behaves as it did when everything was asked
      together, one hedge later.

    An attempt that errors is not a case here at all: the caller raises on
    it, so the ones below it were never going to be read.
    """
    outcomes: list[Optional[_Outcome]] = [None] * len(urls)
    landed = [threading.Event() for _ in urls]
    asked = [False] * len(urls)
    asking = threading.Lock()

    def run(index: int, url: str) -> None:
        try:
            outcomes[index] = _Outcome(data=_fetch_json(url))
        except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
            outcomes[index] = _Outcome(error=exc)
        finally:
            landed[index].set()

    def ask(index: int) -> None:
        """Put one attempt on the wire, at most once. The lock is not
        defensive: the hedge asks for everything from the waiting thread
        while the loop asks for the next one, and a question sent twice is
        the exact cost this function exists to avoid."""
        with asking:
            if asked[index]:
                return
            asked[index] = True
        threading.Thread(
            target=run,
            args=(index, urls[index]),
            name=f"lyrics-attempt-{index}",
            daemon=True,
        ).start()

    for index in range(len(urls)):
        ask(index)
        deadline = time.monotonic() + _ATTEMPT_WAIT
        if not landed[index].wait(min(_HEDGE_SECONDS, _ATTEMPT_WAIT)):
            # Slow enough to be worth overlapping. From here this is the
            # all-at-once chain, and everything it had to keep it keeps.
            for below in range(index + 1, len(urls)):
                ask(below)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not landed[index].wait(remaining):
                # A higher-priority attempt that never came back is not a
                # licence to use a lower-priority answer: the outcome is
                # unknown, which is a retry state.
                yield _Outcome(
                    error=LyricsError(
                        "LRCLIB did not answer in time",
                        FetchFailure(kind=TIMEOUT, attempt=labels[index]),
                    )
                )
                return
        yield outcomes[index]


def attempts(snapshot: PlayerSnapshot) -> list[tuple[str, str]]:
    """The fallback chain as (label, URL) pairs, most precise first.

    /get with the album is the exact match; without it, the same question
    with one fewer thing to disagree about; /search is the loose one. Two
    attempts rather than three when Spotify reports no album, because the
    first two would then be the same request.

    The label travels WITH the url rather than being a second list beside
    it: the chain is two attempts long or three depending on the track,
    and two lists that have to stay the same length are how a failure comes
    to name the wrong attempt.
    """
    params = {"track_name": snapshot.title, "artist_name": snapshot.artist}
    if snapshot.duration_ms is not None:
        params["duration"] = str(round(snapshot.duration_ms / 1000))

    chain = []
    if snapshot.album:
        chain.append(
            (
                ATTEMPT_ALBUM,
                LRCLIB_GET_URL
                + "?"
                + urllib.parse.urlencode({**params, "album_name": snapshot.album}),
            )
        )
    chain.append((ATTEMPT_EXACT, LRCLIB_GET_URL + "?" + urllib.parse.urlencode(params)))
    chain.append(
        (
            ATTEMPT_SEARCH,
            LRCLIB_SEARCH_URL
            + "?"
            + urllib.parse.urlencode(
                {"track_name": snapshot.title, "artist_name": snapshot.artist}
            ),
        )
    )
    return chain


def attempt_urls(snapshot: PlayerSnapshot) -> list[str]:
    """The fallback chain as URLs alone."""
    return [url for _, url in attempts(snapshot)]


class LyricsProvider:
    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        user_sync_dir: Path = DEFAULT_USER_SYNC_DIR,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.user_sync_dir = Path(user_sync_dir)

    def get_lyrics(self, snapshot: PlayerSnapshot) -> Optional[TrackLyrics]:
        """Lyrics for the snapshot's track: user sync, then cache, then
        LRCLIB.

        Returns None when the track definitively has no lyrics or the
        snapshot has no usable track metadata. Raises LyricsError when
        LRCLIB can't be reached or errors — that outcome is never cached,
        so the track is retried next time.
        """
        # Non-music items (DJ narration, ads) must not touch the cache at
        # all: DJ narration reuses the upcoming song's ID, so even a cache
        # READ here would show the song's lyrics during the narration —
        # and a write would poison the song's entry with narration metadata.
        if not snapshot.is_music_track:
            return None

        # The user's own sync outranks everything: they made it because the
        # remote answer was plain (or wrong), so neither the cached copy of
        # that answer nor a fresh fetch may override it.
        user_sync = self.read_user_sync(snapshot.track_id)
        if user_sync is not None:
            return user_sync

        cached = self._read_cache(snapshot.track_id)
        if cached is not None:
            return self._decode_cache_entry(cached)

        if not snapshot.title or not snapshot.artist:
            return None

        lyrics = self._fetch(snapshot)
        self._write_cache(snapshot.track_id, lyrics)
        return lyrics

    def _fetch(self, snapshot: PlayerSnapshot) -> Optional[TrackLyrics]:
        """Fallback chain against LRCLIB's exact-match /get endpoint, which
        404s when Spotify's album name or duration (tolerance ~2s) doesn't
        exactly match LRCLIB's record: full params → without album →
        /search. Raises LyricsError on transient failure at any step.

        The attempts are read back in the order above, which is the same
        chain it always was — the fallback is a preference between answers,
        and never depended on asking one question only after another had
        failed. What has changed underneath is only WHEN each question goes
        out, which _run_attempts owns and measured.
        """
        chain = attempts(snapshot)
        labels = [label for label, _ in chain]
        urls = [url for _, url in chain]
        for (label, url), outcome in zip(chain, _run_attempts(urls, labels)):
            if outcome.error is not None:
                # An attempt that outranks the rest could not be answered,
                # so the outcome of the chain is unknown — exactly as when
                # it was sequential and stopped here. Never cached.
                #
                # Stamped with the attempt on the way past: this is the one
                # place that knows which link of the chain the error came
                # from, and it is what lets the window answer "which one"
                # rather than only "it failed".
                if isinstance(outcome.error, LyricsError):
                    raise outcome.error.at(label)
                raise LyricsError(
                    str(outcome.error),
                    FetchFailure(
                        kind=UNKNOWN, attempt=label, detail=str(outcome.error)
                    ),
                ) from outcome.error
            if outcome.data is None:
                continue  # a definitive 404: the next attempt has the floor
            if url.startswith(LRCLIB_SEARCH_URL):
                return self._pick_search_result(outcome.data or [], snapshot)
            return self._decode_record(outcome.data)
        return None

    def _pick_search_result(
        self, results: list, snapshot: PlayerSnapshot
    ) -> Optional[TrackLyrics]:
        """Best search hit: same title/artist (case-insensitive), duration
        close to Spotify's when known, synced preferred over plain."""
        duration = (
            snapshot.duration_ms / 1000 if snapshot.duration_ms is not None else None
        )

        def acceptable(record: dict) -> bool:
            if str(record.get("trackName", "")).lower() != snapshot.title.lower():
                return False
            if str(record.get("artistName", "")).lower() != snapshot.artist.lower():
                return False
            if duration is not None and record.get("duration"):
                return abs(float(record["duration"]) - duration) <= _SEARCH_DURATION_TOLERANCE
            return True

        candidates = [r for r in results if acceptable(r)]
        candidates.sort(key=lambda r: 0 if r.get("syncedLyrics") else 1)
        for record in candidates:
            lyrics = self._decode_record(record)
            if lyrics is not None:
                return lyrics
        return None

    @staticmethod
    def _decode_record(data: dict) -> Optional[TrackLyrics]:
        """A /get response or /search result item → TrackLyrics, or None
        for instrumental/empty records."""
        synced_text = data.get("syncedLyrics")
        plain_text = data.get("plainLyrics")
        synced = parse_lrc(synced_text) if synced_text else None
        plain = plain_text.strip() if plain_text and plain_text.strip() else None
        if synced or plain:
            return TrackLyrics(synced=synced or None, plain=plain)
        return None

    # -- user syncs --------------------------------------------------------

    def user_sync_path(self, track_id: str) -> Path:
        """Where this track's hand-made sync lives. Plain ``.lrc`` on
        purpose: readable, editable by hand, and portable to any other
        player."""
        return self.user_sync_dir / (_SAFE_FILENAME_RE.sub("_", track_id) + ".lrc")

    def has_user_sync(self, track_id: Optional[str]) -> bool:
        """Whether a hand-made sync already exists — the difference between
        offering "Sync this song" and "Re-sync this song"."""
        return bool(track_id) and self.user_sync_path(track_id).is_file()

    def read_user_sync(self, track_id: Optional[str]) -> Optional[TrackLyrics]:
        """The user's sync for this track, or None when there isn't one (or
        it no longer parses as timed lyrics — a hand-edit gone wrong falls
        back to the normal chain rather than showing an empty song)."""
        if not track_id:
            return None
        try:
            text = self.user_sync_path(track_id).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        entries = parse_lrc(text)
        return TrackLyrics(synced=entries) if entries else None

    def save_user_sync(self, track_id: str, lrc_text: str) -> Path:
        """Write a completed sync and return its path. Unlike the cache
        this is NOT best-effort: the user just tapped through a whole song,
        so a failure must reach them rather than vanish. Raises OSError."""
        self.user_sync_dir.mkdir(parents=True, exist_ok=True)
        path = self.user_sync_path(track_id)
        path.write_text(lrc_text, encoding="utf-8")
        logger.info("saved user sync for %s -> %s", track_id, path)
        return path

    # -- cache ------------------------------------------------------------

    def _cache_path(self, track_id: str) -> Path:
        return self.cache_dir / (_SAFE_FILENAME_RE.sub("_", track_id) + ".json")

    def _read_cache(self, track_id: str) -> Optional[dict]:
        path = self._cache_path(track_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(self, track_id: str, lyrics: Optional[TrackLyrics]) -> None:
        entry = {
            "found": lyrics is not None,
            "synced": lyrics.synced if lyrics else None,
            "plain": lyrics.plain if lyrics else None,
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(track_id).write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # cache is best-effort

    @staticmethod
    def _decode_cache_entry(entry: dict) -> Optional[TrackLyrics]:
        if not entry.get("found"):
            return None
        synced_raw = entry.get("synced")
        synced = [(float(t), str(line)) for t, line in synced_raw] if synced_raw else None
        return TrackLyrics(synced=synced, plain=entry.get("plain"))
