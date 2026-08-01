"""Why a lyrics lookup could not be answered, in words.

"lyrics unavailable, will retry" is the right thing for the window to say
and the wrong thing for it to be the only thing it can say. It is true of
a server that returned 503, of a laptop with the wifi off, and of a
request that timed out on the third attempt after two 404s — three
situations with three different answers to "is this me or is this them",
and the app knew which one it was and threw it away at the door.

So a failure carries what it was: the KIND of thing that went wrong, the
HTTP status where there was one, and the attempt in the fallback chain it
came from. ``describe`` turns that into one line in the window's own
register, and that line is the only rendering of it — the window and the
terminal tool both call this rather than each writing their own sentence,
which is how the two came to disagree about the same song once already
(see view_model.HEADER_SEPARATOR).

Pure and Qt-free, and deliberately not part of lyrics_provider.py: the
provider raises these, the view model renders them, and neither has to
import the other to do it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# What kind of thing went wrong. Four, because four are distinguishable
# from where the request is made and each one means something different to
# the person reading it: their network, our request, the server's answer,
# or nobody knows.
HTTP = "http"              # a status that is neither 200 nor a 404
TIMEOUT = "timeout"        # nothing came back in time
CONNECTION = "connection"  # the socket never got there
PAYLOAD = "payload"        # an answer arrived and was not JSON
UNKNOWN = "unknown"        # anything else, reported rather than guessed at
# And a fifth, which is the odd one out on purpose: nothing went wrong out
# there at all. LRCLIB answered a 429 earlier and asked for a pause, and
# this lookup was refused by this app before a socket was opened. It is a
# failure from the window's point of view — there are still no lyrics — and
# it is not a failure of the service, which is a distinction the retry
# schedule is built on. See ``reached_lrclib``.
HELD = "held"

# The status that carries an instruction rather than just a complaint.
# LRCLIB's API documentation asks callers to honour Retry-After on this one
# and says that ignoring it may result in a temporary ban, which is why it
# gets a line of its own below rather than falling into the generic HTTP
# sentence.
RATE_LIMITED_STATUS = 429

# Where in the fallback chain it happened. The chain's own vocabulary,
# spelled the way a person would say it rather than as a URL — "album
# match" is what the first attempt IS, and /api/get?album_name=... is only
# how it is spelled.
ATTEMPT_ALBUM = "album match"
ATTEMPT_EXACT = "title and artist"
ATTEMPT_SEARCH = "search"


@dataclass(frozen=True)
class FetchFailure:
    """One lookup that could not be answered.

    ``attempt`` is empty for a failure that belongs to no single attempt —
    which is a real case, not a defensive one: an error raised before the
    chain is built has nowhere in the chain to point at.

    ``retry_after`` is seconds, and it is LRCLIB's number rather than ours:
    it is set from the header on a 429, and from what is left of the
    resulting pause on a lookup this app refused. Everything else leaves it
    None, which means "our own schedule decides".
    """

    kind: str = UNKNOWN
    attempt: str = ""
    status: Optional[int] = None
    detail: str = ""
    retry_after: Optional[float] = None


def reached_lrclib(failure: Optional[FetchFailure]) -> bool:
    """Whether this failure is evidence about the SERVICE.

    Only ``HELD`` is not. A lookup refused by our own pause never opened a
    socket, so counting it as a consecutive failure would grow the backoff
    on the strength of the backoff — the interval would run away from the
    user while LRCLIB sat there perfectly healthy, waiting out a pause it
    asked for once.

    The one place this is asked is the retry schedule, and it is a function
    rather than a field for the reason gates here always give: the boolean
    is derived from the reason, never recorded beside it, so the two cannot
    come to disagree about the same failure.
    """
    return failure is None or failure.kind != HELD


def describe(failure: Optional[FetchFailure]) -> str:
    """One line saying what went wrong, in the window's register.

    Lower case and terse, like "no lyrics found" and "plain lyrics · not
    synced" beside it: this is a HUD floating over somebody's work, not a
    dialog. The two halves are joined by the same middle dot that joins a
    song to its artist — a separator and nothing else.

    The socket's own message is deliberately NOT here. "[Errno 8]
    nodename nor servname provided, or not known" is the right thing to
    put in a log and the wrong thing to put on a 460-point window; the
    kind already says which of the four things happened, and the detail
    is kept on the record for the log line that reports it.
    """
    if failure is None:
        return ""
    said = _what_happened(failure)
    if not failure.attempt:
        return said
    return f"{said} · {failure.attempt}"


def _what_happened(failure: FetchFailure) -> str:
    if failure.kind == HELD:
        return "waiting, as LRCLIB asked"
    if failure.status == RATE_LIMITED_STATUS:
        # Named rather than numbered, because this is the one status the
        # app does something about: a person reading "HTTP 429" learns that
        # something is wrong, and "asked this app to slow down" tells them
        # the same thing plus the fact that it is being obeyed.
        return "LRCLIB asked this app to slow down"
    if failure.kind == HTTP and failure.status is not None:
        return f"LRCLIB answered HTTP {failure.status}"
    if failure.kind == TIMEOUT:
        return "LRCLIB did not answer in time"
    if failure.kind == CONNECTION:
        return "could not reach lrclib.net"
    if failure.kind == PAYLOAD:
        return "LRCLIB's answer could not be read"
    if failure.detail:
        return f"the lookup failed: {failure.detail}"
    return "the lookup failed"
