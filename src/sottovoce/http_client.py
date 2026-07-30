"""HTTP to one host, over connections that are kept alive between requests.

urllib opens a connection per request and closes it again, so every lookup
paid for a DNS answer, a TCP handshake and a TLS handshake before the
server had even heard the question. Measured against lrclib.net on a warm
resolver: 2ms + 41ms + 60ms, about 105ms, on every single request — and
the lyrics fallback chain makes up to three of them.

So connections are kept and handed out again. lrclib.net holds an idle one
for at least four minutes (measured, at 10, 30, 60, 120, 180 and 240
seconds — every one answered 200 on the same socket), which is longer than
most songs: the next track's lookup usually starts with the handshakes
already paid for.

The one thing pooling has to get right is that a pooled connection can be
closed by the server while it sits idle, and nothing says so until the
next request on it fails. That is ordinary rather than exceptional, so it
is retried once on a fresh connection — and ONLY when the failed
connection was a reused one. Retrying a brand new connection would mean a
genuinely unreachable network is tried twice and every real failure takes
twice as long to report.

No Qt, no app knowledge; the connection factory is injectable so the suite
can exercise all of this without a socket.
"""

from __future__ import annotations

import http.client
import logging
import threading
from collections import deque
from typing import Callable, NamedTuple, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


class Response(NamedTuple):
    status: int
    body: bytes


class ConnectionPool:
    """Keep-alive connections to one host.

    Safe to use from several threads at once, which is the point: the
    lyrics chain runs its attempts concurrently and each one takes a
    connection of its own.
    """

    def __init__(
        self,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        limit: int = 3,
        connect: Optional[Callable[[], object]] = None,
    ) -> None:
        self.host = host
        self.timeout = timeout
        self.limit = limit
        # The one door onto the network. Injectable so tests can drive the
        # reuse and the retry without opening a socket.
        self._connect = connect or self._https
        self._idle: deque = deque()
        self._lock = threading.Lock()

    def _https(self):
        return http.client.HTTPSConnection(self.host, timeout=self.timeout)

    def get(self, path: str, headers: Optional[dict] = None) -> Response:
        """GET ``path``. Raises OSError or http.client.HTTPException the
        way the underlying connection does — the caller decides what a
        failure means."""
        connection = self._take()
        reused = connection is not None
        if connection is None:
            connection = self._connect()
        try:
            return self._exchange(connection, path, headers)
        except (OSError, http.client.HTTPException):
            _close_quietly(connection)
            if not reused:
                raise
            logger.debug("pooled connection to %s was stale; retrying", self.host)
            connection = self._connect()
            return self._exchange(connection, path, headers)

    def _exchange(self, connection, path: str, headers: Optional[dict]) -> Response:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        # will_close is the server's answer to whether this socket can be
        # asked another question. Reading the body first is what makes it
        # meaningful — and what leaves the connection usable at all.
        if getattr(response, "will_close", True):
            _close_quietly(connection)
        else:
            self._give_back(connection)
        return Response(response.status, body)

    def _take(self):
        with self._lock:
            return self._idle.popleft() if self._idle else None

    def _give_back(self, connection) -> None:
        with self._lock:
            if len(self._idle) >= self.limit:
                keep = False
            else:
                self._idle.append(connection)
                keep = True
        if not keep:
            _close_quietly(connection)

    @property
    def idle(self) -> int:
        """How many connections are waiting to be reused."""
        with self._lock:
            return len(self._idle)

    def close(self) -> None:
        """Drop every idle connection. Instant and cannot block: these are
        sockets with nothing in flight on them."""
        with self._lock:
            connections, self._idle = list(self._idle), deque()
        for connection in connections:
            _close_quietly(connection)


def _close_quietly(connection) -> None:
    try:
        connection.close()
    except Exception:  # pragma: no cover - closing a dead socket
        pass
