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

Below the cache and above the network sits the album warm: lyrics fetched
while an earlier track was playing, for tracks that have not played yet.
It can answer yes and it can answer nothing; it can never answer "this
track has no lyrics", because it is a guess made without the track in hand.

One thing here is not per-provider and cannot be: the pause LRCLIB asks
for with ``Retry-After`` on a 429. It is a fact about the host, it is
checked at the single point every request goes through, and while it
stands nothing leaves this app.
"""

from __future__ import annotations

import hashlib
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
from sottovoce.backoff import Hold
from sottovoce.failure import (
    ATTEMPT_ALBUM,
    ATTEMPT_EXACT,
    ATTEMPT_SEARCH,
    CONNECTION,
    FetchFailure,
    HELD,
    HTTP,
    PAYLOAD,
    RATE_LIMITED_STATUS,
    TIMEOUT,
    UNKNOWN,
)
from sottovoce.http_client import ConnectionPool
from sottovoce.player_monitor import PlayerSnapshot

logger = logging.getLogger(__name__)

LRCLIB_HOST = "lrclib.net"
LRCLIB_GET_URL = f"https://{LRCLIB_HOST}/api/get"
LRCLIB_SEARCH_URL = f"https://{LRCLIB_HOST}/api/search"
# The two endpoints that write. Named here beside the two that read,
# because "where this app talks to LRCLIB" has one list and this is it.
LRCLIB_CHALLENGE_URL = f"https://{LRCLIB_HOST}/api/request-challenge"
LRCLIB_PUBLISH_URL = f"https://{LRCLIB_HOST}/api/publish"
DEFAULT_CACHE_DIR = Path(".lyrics_cache")
# Hand-made syncs. Deliberately NOT under the cache directory: clearing the
# cache is a documented reset, and it must never cost the user a sync they
# tapped out themselves.
DEFAULT_USER_SYNC_DIR = Path(".user_syncs")
# Lyrics fetched ahead of being needed, for tracks that have not played.
# INSIDE the cache directory, deliberately and for the mirror of the reason
# above: this is the one thing here nobody made, so clearing the cache must
# take it with everything else.
_WARM_SUBDIR = "album"

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
# What a warmed track has to agree about before it is used, and it is the
# TIGHT number rather than the loose one above. A warm entry is served
# instead of walking the chain, so it has to be as precise as the attempt
# it stands in for — the album match — or warming would quietly demote
# every track it touched from an exact answer to a search-shaped one.
#
# MEASURED, and it earns its keep: of 20 tracks warmed across 4 real albums
# the answer disagreed with the real recording's duration 3 times. Those
# three fall through to the ordinary chain, which is exactly what this
# number is for.
_WARM_DURATION_TOLERANCE = 2.0
# Between two requests this app makes in a row. LRCLIB's API documentation
# asks that requests be sent sequentially, one finishing before the next
# starts, with a short delay of 200 to 500ms between them — it names
# library scanning as the case, and an album warm is one, but the
# instruction is about the client rather than about the errand. This is the
# middle of the band they give, and it is what the album warm's coverage
# measurement was taken at: 4 searches and 20 gets, no 429.
#
# One number rather than one per errand, because it is one fact about
# somebody else's service. The publish exchange obeys it too.
REQUEST_GAP_SECONDS = 0.35
# The most requests one album's warm may spend. LRCLIB's search answers
# with 20 records, so today this binds nothing at all — it is a ceiling on
# a number that comes from somebody else's service, and the alternative is
# an app whose quiet background traffic is however long a response they
# decide to send.
_WARM_MAX_REQUESTS = 20

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

        A refusal by this app's own pause is left unstamped, because it
        belongs to no link of the chain: it happens before a request is
        made and it would have happened at every attempt equally. "Waiting,
        as LRCLIB asked · album match" reads as though the album match were
        what was waiting.
        """
        if self.failure.kind == HELD:
            return self
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

# A pause LRCLIB asked for. Module level for the pool's reason and a
# sharper version of it: this is a fact about the HOST, not about a
# provider instance, and a hold that only covered the lookups made through
# one object would be no hold at all the moment a second one existed.
#
# It sits in front of _fetch_json rather than in front of the retry timer
# because the retry timer is one of several ways a request leaves this app:
# a track change asks straight away, and so does the album warm. LRCLIB's
# documentation says ignoring Retry-After may earn a temporary ban, and a
# ban is not something a user skipping tracks should be able to walk into.
_hold = Hold()


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


def _retry_after_seconds(response) -> Optional[float]:
    """The pause LRCLIB asked for, in seconds, or None.

    Only the delta-seconds form is read. RFC 7231 also allows an HTTP date,
    and reading one would mean trusting this machine's clock to agree with
    theirs about the current time — a skewed clock would turn "wait 30
    seconds" into a pause of hours or none at all. A header this app cannot
    read is treated as no number rather than as a guess, and the caller
    still holds for its own schedule.
    """
    raw = response.header("Retry-After")
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        logger.info("Retry-After was not a number of seconds: %r", raw)
        return None


def _refuse_while_held(url: str) -> None:
    """Turn a request away while a pause LRCLIB asked for is running.

    The one door in front of every request this app makes, whichever verb
    it uses. It is here rather than at any of the places a request is
    started from so that a caller added later cannot miss it, and
    publishing is exactly that caller: a POST during a pause would be the
    one request that walked through a hold set by a GET.
    """
    waiting = _hold.remaining(time.monotonic())
    if waiting <= 0:
        return
    # It is a failure the window can show and explain, and the one kind of
    # failure that is not evidence about LRCLIB: see failure.HELD.
    logger.info("not asking for %s: %.1fs left of the pause", url, waiting)
    raise LyricsError(
        "waiting as LRCLIB asked",
        FetchFailure(kind=HELD, retry_after=waiting),
    )


def _pause_asked_for(response) -> float:
    """Start the pause a 429 carries, and hand back the number.

    Set at the point the answer arrives, so every other request in flight
    or about to be made obeys it too — the fallback chain runs its attempts
    concurrently, and two of them being told to slow down while the third
    goes out anyway is the case this must not have.
    """
    asked = _retry_after_seconds(response)
    _hold.asked_to_wait(asked or 0.0, time.monotonic())
    logger.warning("LRCLIB asked for a pause of %ss", asked)
    return asked


def _path_of(url: str) -> str:
    split = urllib.parse.urlsplit(url)
    return split.path + (f"?{split.query}" if split.query else "")


def _parsed(response):
    """The body as JSON, or None when there is no body.

    An empty body is a real answer rather than a broken one: ``POST
    /api/publish`` says 201 and nothing else, and reading that as
    unparseable would turn every successful publish into a failure.
    """
    if not response.body:
        return None
    try:
        return json.loads(response.body)
    except ValueError as exc:
        raise LyricsError(
            str(exc), FetchFailure(kind=PAYLOAD, detail=str(exc))
        ) from exc


def _fetch_json(url: str):
    """GET a JSON document. Returns the parsed body, or None on 404 (a
    definitive "not found"). Raises LyricsError for anything whose outcome
    is unknown: network trouble, other HTTP errors, unparseable payload —
    and for a request this app declined to make at all, while a pause
    LRCLIB asked for is still running."""
    _refuse_while_held(url)
    try:
        response = _lrclib_pool().get(
            _path_of(url), headers={"User-Agent": USER_AGENT}
        )
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
    if response.status == RATE_LIMITED_STATUS:
        asked = _pause_asked_for(response)
        raise LyricsError(
            f"LRCLIB returned HTTP {response.status}",
            FetchFailure(
                kind=HTTP, status=response.status, retry_after=asked
            ),
        )
    if response.status != 200:
        raise LyricsError(
            f"LRCLIB returned HTTP {response.status}",
            FetchFailure(kind=HTTP, status=response.status),
        )
    return _parsed(response)


# Every 2xx LRCLIB answers a write with. 201 is what /api/publish says on
# success and 200 is what /api/request-challenge says; anything else is a
# failure whatever its shape, including a 3xx, because this app follows no
# redirect to a service it publishes lyrics to.
_CREATED = 201
_OK = 200


def post_json(url: str, payload: dict, headers: Optional[dict] = None):
    """POST a JSON document, and answer with the parsed body or None.

    The write-side twin of ``_fetch_json`` and it keeps everything that one
    keeps: the same pause in front of it, the same 429 handling behind it,
    the same User-Agent, the same pool. What it does NOT keep is 404 as an
    answer — there is no endpoint here that means anything by one — so
    every status that is not a success raises, carrying the status with it.

    That status is the whole of what the publish path needs to tell its
    failures apart: 400 is the token being refused, and it is the one that
    is recovered from by starting again with a fresh challenge.
    """
    _refuse_while_held(url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        **(headers or {}),
    }
    try:
        response = _lrclib_pool().post(_path_of(url), body, headers=sent)
    except (OSError, http.client.HTTPException) as exc:
        logger.warning("POST %s -> error: %s", url, exc)
        raise LyricsError(
            str(exc), FetchFailure(kind=CONNECTION, detail=str(exc))
        ) from exc

    logger.info("POST %s -> %d", url, response.status)
    if response.status == RATE_LIMITED_STATUS:
        asked = _pause_asked_for(response)
        raise LyricsError(
            f"LRCLIB returned HTTP {response.status}",
            FetchFailure(
                kind=HTTP, status=response.status, retry_after=asked
            ),
        )
    if response.status not in (_OK, _CREATED):
        raise LyricsError(
            f"LRCLIB returned HTTP {response.status}",
            FetchFailure(kind=HTTP, status=response.status),
        )
    return _parsed(response)


def track_record(
    title: str, artist: str, album: str, duration_seconds: float
) -> Optional[dict]:
    """LRCLIB's own record for one exact track signature, asked NOW.

    The whole of it, rather than the ``TrackLyrics`` the app displays: what
    publishing has to know is what LRCLIB is holding — plain lyrics,
    synced lyrics, whether it thinks the track is instrumental — and two of
    those three are thrown away on the way to a ``TrackLyrics``.

    Nothing here reads or writes the cache, and that is the point of it
    existing at all. The cached answer is what LRCLIB said the first time
    this song played, which may be weeks ago and may have been replaced by
    somebody else's contribution since. A publication is a permanent thing
    done to somebody else's database, so the question it turns on is asked
    fresh every time.

    None for a 404, which here means "LRCLIB has no such track" and is a
    definitive answer rather than a failure. Raises LyricsError otherwise,
    exactly like every other lookup.
    """
    return _fetch_json(
        LRCLIB_GET_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "track_name": title,
                "artist_name": artist,
                "album_name": album,
                "duration": str(round(duration_seconds)),
            }
        )
    )


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


# Where an answer came from. Only one distinction is acted on — whether
# LRCLIB itself answered — but naming all four is what makes that one
# readable at the other end, and a log line that says which of them it was
# is worth more than a boolean.
FROM_USER_SYNC = "user sync"
FROM_CACHE = "cache"
FROM_WARM = "warmed"
FROM_SERVICE = "lrclib"
FROM_NOWHERE = "nothing to ask"


@dataclass(frozen=True)
class Lookup:
    """One answer, and where it came from.

    The source exists for the retry schedule. "The lookup succeeded" and
    "LRCLIB is answering" are two different facts, and treating them as one
    is how a backoff resets itself in the middle of an outage: during one,
    every song the user has played before still answers instantly from the
    cache, and a counter that reset on those would be back to a 30 second
    retry on every uncached track. Only ``FROM_SERVICE`` is evidence about
    the service.
    """

    lyrics: Optional[TrackLyrics]
    source: str

    @property
    def from_service(self) -> bool:
        return self.source == FROM_SERVICE


class LyricsProvider:
    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        user_sync_dir: Path = DEFAULT_USER_SYNC_DIR,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.user_sync_dir = Path(user_sync_dir)
        # Derived rather than injected: it is cache, so it belongs under
        # the cache directory, and one argument fewer is one fewer way for
        # a caller to put it somewhere clearing the cache would not reach.
        self.warm_dir = self.cache_dir / _WARM_SUBDIR

    def get_lyrics(self, snapshot: PlayerSnapshot) -> Optional[TrackLyrics]:
        """Lyrics for the snapshot's track, and nothing about where they
        came from. What the terminal tools use, and what every caller used
        before the retry schedule needed to tell a cache hit from an
        answer."""
        return self.look_up(snapshot).lyrics

    def look_up(self, snapshot: PlayerSnapshot) -> Lookup:
        """Lyrics for the snapshot's track: user sync, cache, the album
        warm, then LRCLIB — with the source they came from.

        ``lyrics`` is None when the track definitively has no lyrics or the
        snapshot has no usable track metadata. Raises LyricsError when
        LRCLIB can't be reached or errors — that outcome is never cached,
        so the track is retried next time.
        """
        # Non-music items (DJ narration, ads) must not touch the cache at
        # all: DJ narration reuses the upcoming song's ID, so even a cache
        # READ here would show the song's lyrics during the narration —
        # and a write would poison the song's entry with narration metadata.
        if not snapshot.is_music_track:
            return Lookup(None, FROM_NOWHERE)

        # The user's own sync outranks everything: they made it because the
        # remote answer was plain (or wrong), so neither the cached copy of
        # that answer nor a fresh fetch may override it.
        user_sync = self.read_user_sync(snapshot.track_id)
        if user_sync is not None:
            return Lookup(user_sync, FROM_USER_SYNC)

        cached = self._read_cache(snapshot.track_id)
        if cached is not None:
            return Lookup(self._decode_cache_entry(cached), FROM_CACHE)

        if not snapshot.title or not snapshot.artist:
            return Lookup(None, FROM_NOWHERE)

        # Fetched ahead of time, while this album's first track was
        # playing. Below the cache and above the network, and it can only
        # answer YES: a track the warm never reached, or reached and got a
        # different recording of, falls through to the chain exactly as if
        # warming had never run. Nothing here is ever written down as a
        # miss — see read_warm.
        warmed = self.read_warm(snapshot)
        if warmed is not None:
            # Promoted to an ordinary cache entry on the way past, so the
            # second play of this track takes the fast path above and the
            # duration is never checked twice.
            self._write_cache(snapshot.track_id, warmed)
            return Lookup(warmed, FROM_WARM)

        lyrics = self._fetch(snapshot)
        self._write_cache(snapshot.track_id, lyrics)
        return Lookup(lyrics, FROM_SERVICE)

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

    # -- the album, fetched before it is needed ----------------------------
    #
    # An outage is only invisible for songs that were already answered, so
    # the one useful thing to do while the network is up is to answer more
    # of them. The rest of the album is the obvious guess: people play
    # albums in order, and the app is holding the album's name already.
    #
    # ## Two stages, because most albums get one track played
    #
    # Spotify's scripting dictionary describes the CURRENT track and
    # nothing else — there is no way to ask it what else is on the album.
    # So the track list has to come from LRCLIB too.
    #
    # **Stage one, on any track: one search.** It names the album's tracks
    # and carries their lyrics with it, so whatever comes back is stored as
    # it stands. One request, and it is the only one most albums ever cost.
    #
    # **Stage two, when a SECOND track from the album plays: one /get per
    # name.** A second track is the difference between a song somebody
    # heard and an album somebody is listening to, and it is the only
    # signal available for that. It is worth waiting for, because the two
    # stages cost wildly different amounts.
    #
    # MEASURED over 4 real albums (47 tracks, ground truth from the iTunes
    # catalogue), counted the way this code actually stores things:
    #
    # | | requests per album | of the album, warm and usable |
    # |---|---|---|
    # | stage one alone | 1 | 26% |
    # | both stages | 20 | 34% |
    #
    # So the second stage buys another 8 points for nineteen more requests,
    # which is a poor trade to make for every album and a fair one for an
    # album somebody is playing through. That is the whole design of
    # splitting them: an album with one track played costs ONE request.
    #
    # Stage two does not replace what stage one stored, it ADDS to it: a
    # name keeps every record either stage found, and which one is right is
    # decided when the track plays and its duration is finally known. That
    # is not tidiness — a /get answer can be a different recording than the
    # search's was, and replacing cost a track of the 16 in the
    # measurement above (16 against 15).
    #
    # Filtering the names by album was measured too, and it is why there is
    # no filter: keeping only records whose albumName matches the one
    # playing costs 24 requests and reaches ZERO of the 47 tracks. LRCLIB's
    # album names are user-supplied and rarely spelled the way Spotify
    # spells them.

    def warm_path(self, artist: str, album: str, title: str) -> Path:
        """Where one warmed name's records live.

        Keyed by what will be KNOWN when it plays — artist, album, title —
        because a Spotify track ID is exactly what this app does not have
        for a song it has never seen. The readable stem is for whoever
        looks in the directory; the digest after it is what makes the name
        safe and short whatever the song is called.
        """
        key = "\n".join(part.strip().lower() for part in (artist, album, title))
        stem = _SAFE_FILENAME_RE.sub("_", title.strip().lower())[:48]
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        return self.warm_dir / f"{stem}-{digest}.json"

    def album_index_path(self, artist: str, album: str) -> Path:
        """What is known about this album's warm: the names the search
        returned, which of this album's tracks have been seen playing, and
        whether the per-track stage has run."""
        key = "\n".join(part.strip().lower() for part in (artist, album))
        stem = _SAFE_FILENAME_RE.sub("_", album.strip().lower())[:48]
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        return self.warm_dir / f"{stem}-{digest}.album"

    def album_index(self, snapshot: PlayerSnapshot) -> Optional[dict]:
        """This album's index, or None when it has never been searched."""
        if not snapshot.artist or not snapshot.album:
            return None
        try:
            return json.loads(
                self.album_index_path(
                    snapshot.artist, snapshot.album
                ).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def _write_album_index(self, artist: str, album: str, index: dict) -> None:
        try:
            self.warm_dir.mkdir(parents=True, exist_ok=True)
            self.album_index_path(artist, album).write_text(
                json.dumps(index, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # it will simply be warmed again another day

    def album_is_searched(self, snapshot: PlayerSnapshot) -> bool:
        return self.album_index(snapshot) is not None

    def album_is_warm(self, snapshot: PlayerSnapshot) -> bool:
        """Whether the per-track stage has run for this album. An album
        whose stage two was cut short by a failure or by shutdown answers
        False, so a later track from it may try again."""
        index = self.album_index(snapshot)
        return bool(index and index.get("warmed"))

    def read_warm(self, snapshot: PlayerSnapshot) -> Optional[TrackLyrics]:
        """The warmed answer for this track, if one of the records stored
        under its name is about this recording.

        Two ways to answer None and they mean the same thing to the caller
        — ask LRCLIB — which is the whole design of this store: it can say
        yes and it can say nothing, and it can never say "this track has no
        lyrics". Warming is a guess made without the track in hand, and a
        guess is not allowed to write anything down that would stop the
        real question being asked.

        SEVERAL records per name, because LRCLIB genuinely returns several:
        a search for an album answers with the same title at three
        different lengths, and which of them is this recording is a
        question nobody could answer at the time they were stored. So they
        are all kept and the duration decides here, where the track is
        finally in hand.

        The tolerance is the one /api/get itself matches on rather than the
        looser one search results get, because this answer is standing in
        for the album match rather than for the search.
        """
        if not snapshot.artist or not snapshot.album or not snapshot.title:
            return None
        if snapshot.duration_ms is None:
            # Nothing to check the records against. "Prefer no lyrics to
            # mismatched-duration lyrics" is the rule, and an unverifiable
            # warm entry is exactly the case it was written for.
            return None
        entry = self._read_warm_entry(snapshot.artist, snapshot.album, snapshot.title)
        if entry is None:
            return None
        wanted = snapshot.duration_ms / 1000
        for record in entry.get("records", []):
            stored = record.get("duration")
            if stored is None:
                continue
            if abs(float(stored) - wanted) <= _WARM_DURATION_TOLERANCE:
                return self._decode_cache_entry(record)
        logger.info(
            "nothing warmed for %r is %.0fs long", snapshot.title, wanted
        )
        return None

    def _read_warm_entry(self, artist: str, album: str, title: str) -> Optional[dict]:
        try:
            return json.loads(
                self.warm_path(artist, album, title).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def _keep_warm(
        self, artist: str, album: str, title: str, records: list, asked: bool
    ) -> bool:
        """Add records to what is known about one name, and say whether any
        of them were worth keeping.

        A record is worth keeping when it has lyrics in it AND a duration
        to recognise it by later; one without a duration could never be
        served, because there would be no way to tell whether it is this
        recording.

        ADDS rather than replaces. Stage two's answer is better sourced
        than stage one's, so it goes first and wins a tie — but it can also
        be a different recording than the search found, and throwing the
        search's away would be losing a track to a request meant to gain
        one.
        """
        keep = []
        for record in records:
            lyrics = self._decode_record(record)
            duration = record.get("duration")
            if lyrics is None or duration is None:
                continue
            keep.append(
                {
                    "found": True,
                    "duration": float(duration),
                    "synced": lyrics.synced,
                    "plain": lyrics.plain,
                }
            )
        existing = self._read_warm_entry(artist, album, title) or {}
        known = list(existing.get("records", []))
        merged = keep + [
            record for record in known
            if not any(record["duration"] == kept["duration"] for kept in keep)
        ]
        if not merged:
            return False
        entry = {"asked": bool(asked) or bool(existing.get("asked")), "records": merged}
        try:
            self.warm_dir.mkdir(parents=True, exist_ok=True)
            self.warm_path(artist, album, title).write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            return False  # cache is best-effort, and this most of all
        return bool(keep)

    def search_album(self, snapshot: PlayerSnapshot) -> int:
        """Stage one: one search, and everything usable in it kept.

        Returns how many names came back with something worth keeping. The
        index is written whatever the answer, because "asked and got
        nothing" is an answer too and is what stops this being asked again
        on the next track of the same album.
        """
        artist, album = snapshot.artist, snapshot.album
        query = urllib.parse.urlencode({"q": f"{album} {artist}"})
        records = _fetch_json(f"{LRCLIB_SEARCH_URL}?{query}") or []
        by_name: dict = {}
        for record in records:
            name = str(record.get("trackName", "")).strip()
            if name:
                by_name.setdefault(name, []).append(record)
        stored = 0
        for name, found in by_name.items():
            if self._keep_warm(artist, album, name, found, asked=False):
                stored += 1
        self._write_album_index(
            artist,
            album,
            {
                "names": list(by_name),
                # The track that prompted the search. A SECOND id landing
                # here is what says somebody is listening to the album
                # rather than to a song, and it is the only signal there is.
                "tracks": [snapshot.track_id] if snapshot.track_id else [],
                "warmed": False,
            },
        )
        logger.info(
            "album search for %r: %d names, %d with lyrics to keep",
            album,
            len(by_name),
            stored,
        )
        return stored

    def warm_album_tracks(
        self,
        snapshot: PlayerSnapshot,
        index: dict,
        sleep=time.sleep,
        should_stop=None,
    ) -> int:
        """Stage two: one /get per name the search returned.

        Sequential and spaced, which is LRCLIB's own instruction for work
        like this. Returns how many names gained a record.
        """
        artist, album = snapshot.artist, snapshot.album
        names = [name for name in index.get("names", []) if name]
        stored = 0
        asked = 0
        for name in names:
            if should_stop is not None and should_stop():
                logger.info("album warm stopped after %d of %d", asked, len(names))
                return stored
            if asked >= _WARM_MAX_REQUESTS:
                logger.info("album warm capped at %d requests", _WARM_MAX_REQUESTS)
                break
            entry = self._read_warm_entry(artist, album, name)
            if entry is not None and entry.get("asked"):
                continue  # a /get has been spent on this name already
            # Before the request rather than after it, so the first one is
            # spaced from whatever the playing track's own lookup was doing
            # a moment ago.
            sleep(REQUEST_GAP_SECONDS)
            asked += 1
            try:
                record = _fetch_json(
                    LRCLIB_GET_URL
                    + "?"
                    + urllib.parse.urlencode(
                        {
                            "track_name": name,
                            "artist_name": artist,
                            "album_name": album,
                        }
                    )
                )
            except LyricsError as exc:
                # One failure ends the album. Nothing is waiting on this,
                # and a service that just refused one request is not one to
                # keep asking eighteen more times. The index is left
                # unwarmed, so a later track may pick it up again.
                logger.info("album warm stopped: %s", exc)
                return stored
            if self._keep_warm(artist, album, name, [record] if record else [], asked=True):
                stored += 1
        self._write_album_index(artist, album, {**index, "warmed": True})
        logger.info(
            "album warm for %r: %d of %d names stored, %d requests",
            album,
            stored,
            len(names),
            asked,
        )
        return stored

    def warm_album(
        self,
        snapshot: PlayerSnapshot,
        sleep=time.sleep,
        should_stop=None,
    ) -> int:
        """Whichever stage this album is owed, or nothing.

        The decision lives here rather than in the caller because it is a
        question about what is already on disk: has this album been
        searched, has a second of its tracks been heard, has the per-track
        stage already run. The window's job is only to say "a track from
        this album is playing and the service is healthy".

        Raises nothing: every failure here is a failure of something nobody
        is waiting for, so it is logged and abandoned. What it does NOT do
        is swallow the pause — a 429 anywhere in here sets the same
        module-level hold every other request obeys, and the next call to
        _fetch_json refuses on its own.

        ``sleep`` and ``should_stop`` are the caller's: the gap between
        requests is real time on a worker thread, and shutdown has to be
        able to end it without waiting out the album.
        """
        if not snapshot.is_music_track or not snapshot.album or not snapshot.artist:
            return 0
        index = self.album_index(snapshot)
        try:
            if index is None:
                return self.search_album(snapshot)
            if index.get("warmed"):
                return 0
            seen = list(index.get("tracks", []))
            if not snapshot.track_id or snapshot.track_id in seen:
                return 0  # the same track again is not a second track
            # A second track from this album. That is the intent the
            # per-track stage waits for, and it is worth eighteen requests
            # where one track was worth none.
            index = {**index, "tracks": seen + [snapshot.track_id]}
            self._write_album_index(snapshot.artist, snapshot.album, index)
            return self.warm_album_tracks(
                snapshot, index, sleep=sleep, should_stop=should_stop
            )
        except LyricsError as exc:
            logger.info("album warm gave up: %s", exc)
            return 0

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

    def user_sync_text(self, track_id: Optional[str]) -> Optional[str]:
        """The sync file's own text, exactly as it sits on disk.

        What publishing needs, and it needs the TEXT rather than the parsed
        lyrics: the file is what would be sent and the file is what is
        remembered as having been sent, so anything that took a round trip
        through ``parse_lrc`` and back would be a second version of it.
        """
        if not track_id:
            return None
        try:
            return self.user_sync_path(track_id).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None

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

    def save_user_sync(
        self, track_id: str, lrc_text: str, partial: bool = False
    ) -> Path:
        """Write a sync and return its path. Unlike the cache this is NOT
        best-effort: the user just tapped through a song, so a failure must
        reach them rather than vanish. Raises OSError.

        ``partial`` is written down beside the file rather than into it,
        for the reason every other sidecar here exists: the ``.lrc`` is
        what any other player reads and what would be sent, so nothing
        this app happens to know about it may live inside it. A partial
        sync is a real sync of the lines it covers and is treated as one
        everywhere except publishing, which is somebody else's database
        and gets the whole song or nothing.

        A complete pass writes the marker too, holding no digest, rather
        than removing it. The answer has to change — a re-sync that
        finishes the job must not be refused on the strength of the pass
        it replaced — and OVERWRITING it says so without this becoming a
        second place in the package that can delete a file. There is
        exactly one of those and it is not the one that touches ``.lrc``.
        """
        self.user_sync_dir.mkdir(parents=True, exist_ok=True)
        path = self.user_sync_path(track_id)
        path.write_text(lrc_text, encoding="utf-8")
        self.partial_path(track_id).write_text(
            json.dumps(
                {"digest": self.sync_digest(lrc_text) if partial else None},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.info(
            "saved %s user sync for %s -> %s",
            "partial" if partial else "complete",
            track_id,
            path,
        )
        return path

    def partial_path(self, track_id: str) -> Path:
        """Where the note that this sync covers part of a song lives."""
        return self.user_sync_dir / (
            _SAFE_FILENAME_RE.sub("_", track_id) + ".partial"
        )

    def sync_is_partial(self, track_id: Optional[str], lrc_text: str) -> bool:
        """Whether THIS text is the partial sync the marker is about.

        The text and not merely the track, exactly as ``is_published``
        does it and for the same reason: a sync edited by hand or replaced
        by a complete pass is a different thing, and a stale marker must
        not be allowed to speak for it.
        """
        if not track_id:
            return False
        try:
            record = json.loads(self.partial_path(track_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(record, dict) or record.get("digest") is None:
            return False
        return record["digest"] == self.sync_digest(lrc_text)

    # -- a pass in progress ------------------------------------------------
    #
    # The one thing in this directory the app may remove, and the rule is
    # narrow on purpose. ``.user_syncs/`` is the user's work and nothing
    # here deletes any of it: not the ``.lrc``, not the record of what was
    # published. A pass journal is the exception because it is the only
    # file here whose whole purpose is to stop existing — it is written so
    # that an interrupted pass can be finished, and it is removed at the
    # two moments its stamps have somewhere better to be: they became a
    # sync, or the user said discard them.
    #
    # It lives here rather than in ``.lyrics_cache/`` because clearing the
    # cache is a documented safe reset, and minutes of somebody tapping
    # through a song is not something a reset may take.

    def pass_path(self, track_id: str) -> Path:
        """Where this track's pass in progress is written down."""
        return self.user_sync_dir / (_SAFE_FILENAME_RE.sub("_", track_id) + ".pass")

    def read_pass(self, track_id: Optional[str]) -> Optional[dict]:
        """The pass written down for this track, or None. Never raises: a
        journal that cannot be read is a pass that cannot be resumed, and
        the song simply offers a fresh one."""
        if not track_id:
            return None
        try:
            record = json.loads(self.pass_path(track_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def save_pass(self, track_id: str, record: dict) -> Path:
        """Write the pass in progress down. Raises OSError.

        Not best-effort, and it is the reason this exists: the caller has
        to be able to tell somebody that their taps are no longer being
        kept. A journal that failed silently would be worse than no
        journal at all, because it would be a promise.
        """
        self.user_sync_dir.mkdir(parents=True, exist_ok=True)
        path = self.pass_path(track_id)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return path

    def clear_pass(self, track_id: Optional[str]) -> None:
        """Forget the pass written down for this track.

        Best-effort, unlike writing one: the stamps have already gone
        wherever they were going by the time this is called, so a journal
        left behind costs an offer to resume a pass that is finished, and
        that is recoverable. Failing the save because the tidy-up failed
        would not be.
        """
        if not track_id:
            return
        try:
            self.pass_path(track_id).unlink(missing_ok=True)
        except OSError:
            logger.debug("could not clear the pass journal for %s", track_id)

    # -- what has been published ------------------------------------------
    #
    # A sync that has been sent to LRCLIB is still the user's sync and is
    # not changed by having been sent, so this stores one fact about it and
    # nothing else: which TEXT went out. That is what "the same unchanged
    # sync" means, and a digest is the whole of it — re-syncing the song
    # produces different stamps, a different digest, and an offer to
    # publish again, while opening the menu twice produces neither.
    #
    # It lives beside the sync it is about, in ``.user_syncs/``, and that
    # is deliberate on both counts. Not in ``.lyrics_cache/``, because
    # clearing the cache is a documented reset and forgetting what has been
    # published is not a reset, it is an app offering to send somebody's
    # work a second time. And not in the preferences, because it belongs to
    # the file rather than to this Mac's settings: a sync deleted by hand
    # takes its record with it, which is the right answer to a question
    # nobody has asked yet.
    #
    # It is a sidecar rather than one index, so a failure to write can only
    # ever cost the record of one publication, and so nothing ever
    # truncates a file in this directory that another publication's record
    # is also in.

    def published_path(self, track_id: str) -> Path:
        """Where the record of this track's publication lives."""
        return self.user_sync_dir / (
            _SAFE_FILENAME_RE.sub("_", track_id) + ".published"
        )

    @staticmethod
    def sync_digest(lrc_text: str) -> str:
        """What identifies one version of a sync. The text itself, hashed:
        the file is what was sent, so the file is what is remembered."""
        return hashlib.sha256(lrc_text.encode("utf-8")).hexdigest()

    def published_record(self, track_id: Optional[str]) -> Optional[dict]:
        """What is known about this track's publication, or None."""
        if not track_id:
            return None
        try:
            return json.loads(
                self.published_path(track_id).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None

    def is_published(self, track_id: Optional[str], lrc_text: str) -> bool:
        """Whether THIS text has already been sent for this track.

        The text and not merely the track, because a re-sync is a new
        thing to publish and the old record must not stand in for it.
        """
        record = self.published_record(track_id)
        return bool(record) and record.get("digest") == self.sync_digest(lrc_text)

    def record_published(self, track_id: str, lrc_text: str) -> Path:
        """Remember that this text went to LRCLIB. Raises OSError.

        Not best-effort, for the same reason ``save_user_sync`` is not: the
        thing that just happened cannot be undone, so the app has to be
        able to say it happened. A record that failed to write would leave
        the menu offering to publish something already published.
        """
        self.user_sync_dir.mkdir(parents=True, exist_ok=True)
        path = self.published_path(track_id)
        path.write_text(
            json.dumps({"digest": self.sync_digest(lrc_text)}, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("recorded the publication of %s", track_id)
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

    def remote_was_plain_only(self, track_id: Optional[str]) -> bool:
        """Whether the last answer LRCLIB gave about this track was words
        with no timings.

        Read off the cache, so it costs nothing and is available with the
        network down — which is what a menu needs, since a menu is opened
        far more often than a song is published. It is what was TRUE once
        rather than what is true now, and that distinction is the whole
        reason ``track_record`` exists: this decides whether to offer the
        entry, and a fresh lookup decides whether anything is sent.
        """
        if not track_id:
            return False
        entry = self._read_cache(track_id)
        if not entry or not entry.get("found"):
            return False
        return bool(entry.get("plain")) and not entry.get("synced")

    @staticmethod
    def _decode_cache_entry(entry: dict) -> Optional[TrackLyrics]:
        if not entry.get("found"):
            return None
        synced_raw = entry.get("synced")
        synced = [(float(t), str(line)) for t, line in synced_raw] if synced_raw else None
        return TrackLyrics(synced=synced, plain=entry.get("plain"))
