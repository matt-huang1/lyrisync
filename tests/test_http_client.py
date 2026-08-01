"""Connections kept alive between requests, and the one way that bites.

Every test here drives a fake connection: the suite may not open a socket,
and the interesting behaviour — a pooled connection the server closed
while it was idle — is not something a real server can be asked for on
demand anyway.
"""

TIER = "unit"  # Qt-free logic, called directly

import http.client

import pytest

from sottovoce.http_client import ConnectionPool, Response


class FakeResponse:
    def __init__(self, status=200, body=b"{}", will_close=False, headers=()):
        self.status = status
        self._body = body
        self.will_close = will_close
        self._headers = list(headers)

    def read(self):
        return self._body

    def getheaders(self):
        return list(self._headers)


class FakeConnection:
    """One connection. Counts what it was asked and whether it was closed;
    can be told to fail on its next request, which is what a connection
    the server dropped while idle looks like from here."""

    def __init__(self, *, fail_next=False, will_close=False, status=200, headers=()):
        self.requests = []
        self.closed = False
        self.fail_next = fail_next
        self.will_close = will_close
        self.status = status
        self.headers = list(headers)

    def request(self, method, path, headers=None):
        self.requests.append((method, path, headers or {}))
        if self.fail_next:
            self.fail_next = False
            raise http.client.RemoteDisconnected("closed by server")

    def getresponse(self):
        return FakeResponse(
            status=self.status, will_close=self.will_close, headers=self.headers
        )

    def close(self):
        self.closed = True


def pool_of(*connections, **kwargs):
    made = list(connections)
    opened = []

    def connect():
        connection = made.pop(0) if made else FakeConnection()
        opened.append(connection)
        return connection

    pool = ConnectionPool("example.test", connect=connect, **kwargs)
    return pool, opened


def test_a_request_returns_the_status_and_the_body():
    pool, _ = pool_of()
    assert pool.get("/api/get?x=1") == Response(200, b"{}")


def test_the_connection_is_kept_and_used_again():
    """The whole point: the second request pays no DNS, no TCP handshake
    and no TLS handshake — measured at about 105ms against lrclib.net,
    on every request, for a server that then takes a second to answer."""
    pool, opened = pool_of()

    pool.get("/first")
    assert pool.idle == 1
    pool.get("/second")

    assert len(opened) == 1
    assert [path for _, path, _ in opened[0].requests] == ["/first", "/second"]


def test_a_connection_the_server_says_it_will_close_is_not_kept():
    pool, opened = pool_of(FakeConnection(will_close=True))

    pool.get("/first")

    assert pool.idle == 0
    assert opened[0].closed is True


def test_a_pooled_connection_that_died_while_idle_is_retried_once():
    """The one thing pooling has to get right. A server may close an idle
    connection at any time and nothing says so until the next request on
    it fails — that is ordinary, not an error, so it costs a retry rather
    than a failed lookup."""
    first = FakeConnection()
    second = FakeConnection()
    pool, opened = pool_of(first, second)

    pool.get("/first")  # fills the pool
    first.fail_next = True
    assert pool.get("/second") == Response(200, b"{}")

    assert first.closed is True
    assert len(opened) == 2
    assert [path for _, path, _ in second.requests] == ["/second"]


def test_a_brand_new_connection_that_fails_is_not_retried():
    """Retrying here would mean an unreachable network is tried twice and
    every real failure takes twice as long to report."""
    pool, opened = pool_of(FakeConnection(fail_next=True))

    with pytest.raises(http.client.HTTPException):
        pool.get("/first")

    assert len(opened) == 1
    assert opened[0].closed is True


def test_the_pool_does_not_grow_past_its_limit():
    pool, _ = pool_of(limit=2)
    kept = [FakeConnection() for _ in range(3)]
    for connection in kept:
        pool._give_back(connection)

    assert pool.idle == 2
    assert kept[2].closed is True


def test_closing_drops_every_idle_connection():
    pool, opened = pool_of()
    pool.get("/first")
    assert pool.idle == 1

    pool.close()

    assert pool.idle == 0
    assert opened[0].closed is True


def test_headers_reach_the_connection():
    pool, opened = pool_of()
    pool.get("/first", headers={"User-Agent": "sottovoce/test"})
    assert opened[0].requests[0][2] == {"User-Agent": "sottovoce/test"}
