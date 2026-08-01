"""Spotify's own announcement, and the door it comes through.

Everything native goes through ``player_events._distributed_center()``,
so these run everywhere: the door is faked, and the module's real code
runs against the fake exactly as it would against Foundation. What cannot
be faked — that Spotify posts this at all, what it carries, and what it
stays silent about — was measured by driving a real Spotify and watching,
and the transcript of that is the module's own docstring.
"""

TIER = "unit"  # Qt-free logic, called directly

import ast
from pathlib import Path

import pytest

from sottovoce import player_events as pe


class FakeCentre:
    """The distributed notification centre, as far as this module uses it."""

    def __init__(self):
        self.observers = []
        self.removed = []

    def defaultCenter(self):
        return self

    def addObserver_selector_name_object_suspensionBehavior_(
        self, observer, selector, name, obj, behaviour
    ):
        self.observers.append((observer, selector, name, obj, behaviour))

    def removeObserver_(self, observer):
        self.removed.append(observer)


class FakeNSObject:
    """Enough of NSObject for a pyobjc-shaped subclass to be built."""

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


@pytest.fixture
def door(monkeypatch):
    centre = FakeCentre()
    monkeypatch.setattr(pe, "_distributed_center", lambda: (centre, FakeNSObject))
    # The receiver class is built once and kept, because the Objective-C
    # runtime owns its name. Built from a fake base here, so it must not
    # outlive the test that built it.
    monkeypatch.setattr(pe, "_receiver_class", None)
    return centre


def test_nothing_to_observe_with_is_not_a_failure(monkeypatch):
    """Off macOS, without pyobjc, or on a Mac where registering fails: the
    app carries on and asks Spotify on a timer, which is what every Mac
    did before this existed."""
    monkeypatch.setattr(pe, "_distributed_center", lambda: None)
    announcer = pe.PlaybackAnnouncer(lambda: None)
    assert announcer.start() is False
    assert announcer.listening is False
    announcer.stop()  # and stopping what never started is fine


def test_it_registers_for_the_one_name_it_knows(door):
    """Registering with name=None to watch everything receives NOTHING on
    a modern macOS: measured over 32 seconds of driving Spotify through
    eight commands, zero delivered, against all of them delivered when the
    name was given."""
    announcer = pe.PlaybackAnnouncer(lambda: None)
    assert announcer.start() is True
    assert announcer.listening is True
    (observer, selector, name, obj, behaviour) = door.observers[0]
    assert name == pe.NOTIFICATION_NAME == "com.spotify.client.PlaybackStateChanged"
    assert selector == "heard:"
    assert obj is None
    assert behaviour == pe._DELIVER_IMMEDIATELY


def test_the_handler_is_called_when_something_arrives(door):
    heard = []
    announcer = pe.PlaybackAnnouncer(lambda: heard.append(1))
    announcer.start()
    observer = door.observers[0][0]
    observer.heard_(object())
    observer.heard_(object())
    assert heard == [1, 1]


def test_the_payload_is_not_passed_on(door):
    """It is a doorbell. The answer still comes from the snapshot script,
    because there is no artwork URL in the payload and because track
    identity may have exactly one definition."""
    heard = []
    announcer = pe.PlaybackAnnouncer(lambda: heard.append("rang"))
    announcer.start()
    door.observers[0][0].heard_({"Track ID": "spotify:track:abc"})
    assert heard == ["rang"]


def test_a_handler_that_raises_does_not_escape_into_the_run_loop(door):
    """It is called from the main run loop, where an exception is somebody
    else's problem — and where it would be raised once per track, forever.
    """
    def explode():
        raise ValueError("no")

    announcer = pe.PlaybackAnnouncer(explode)
    announcer.start()
    door.observers[0][0].heard_(object())  # must not raise


def test_starting_twice_registers_once(door):
    announcer = pe.PlaybackAnnouncer(lambda: None)
    announcer.start()
    announcer.start()
    assert len(door.observers) == 1


def test_stopping_gives_the_observer_back_and_is_idempotent(door):
    announcer = pe.PlaybackAnnouncer(lambda: None)
    announcer.start()
    observer = door.observers[0][0]
    announcer.stop()
    assert door.removed == [observer]
    assert announcer.listening is False
    announcer.stop()
    assert door.removed == [observer]


def test_it_can_be_started_again_after_stopping(door):
    """The suite does it in a loop, and so does anything that rebuilds a
    window. The Objective-C runtime owns the receiver's class name, so
    building a second class under it would be a runtime error."""
    announcer = pe.PlaybackAnnouncer(lambda: None)
    announcer.start()
    first = pe._receiver_class
    announcer.stop()
    announcer.start()
    assert pe._receiver_class is first
    assert len(door.observers) == 2


# -- one door -------------------------------------------------------------

TREE = ast.parse(Path(pe.__file__).read_text(encoding="utf-8"))


def test_the_native_import_lives_inside_the_door():
    """Foundation is imported in exactly one place, and it is the function
    the suite shuts. A second import site would pass every behavioural
    test here while quietly reopening the door — the same claim
    notifications.py makes about Quartz, for the same reason."""
    native = ("NSDistributedNotificationCenter", "NSObject")

    def import_sites(tree):
        return sorted(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
            if alias.name in native
        )

    inside = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.FunctionDef) and node.name == "_distributed_center"
    )
    assert import_sites(inside) == sorted(native)
    assert import_sites(TREE) == sorted(native)


def test_only_start_walks_through_it():
    callers = {
        node.name
        for node in ast.walk(TREE)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "_distributed_center"
            for inner in ast.walk(node)
        )
    }
    assert callers == {"start"}
