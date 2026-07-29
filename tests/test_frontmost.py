"""The activation observer, against a faked NSWorkspace.

Everything native goes through one door — ``frontmost._workspace()`` —
which is what lets this run headless on Linux and what stops a stray
observer sitting on the developer's own workspace for the life of the
suite. The door is faked here rather than blocked, so the real
FrontmostWatcher runs its real code against something that behaves like
AppKit.

What no test can check is that macOS actually posts the notification when
the user changes apps. That is verified by hand against the real thing;
see docs/per-app-position.md.
"""

import pytest

from lyrisync import frontmost


class FakeApp:
    def __init__(self, bundle_id):
        self._bundle_id = bundle_id

    def bundleIdentifier(self):
        return self._bundle_id


class FakeInfo:
    def __init__(self, app):
        self._app = app

    def objectForKey_(self, key):
        assert key == frontmost.APPLICATION_KEY
        return self._app


class FakeNotification:
    def __init__(self, app):
        self._info = FakeInfo(app) if app is not None else None

    def userInfo(self):
        return self._info


class FakeCentre:
    def __init__(self):
        self.blocks = {}
        self.removed = []
        self.token = 0

    def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
        self.token += 1
        self.blocks[self.token] = (name, block)
        return self.token

    def removeObserver_(self, token):
        self.removed.append(token)
        self.blocks.pop(token, None)

    def post(self, bundle_id):
        """What AppKit does when an app comes forward."""
        for _, block in list(self.blocks.values()):
            block(FakeNotification(FakeApp(bundle_id)))


class FakeWorkspace:
    def __init__(self, frontmost_app="com.apple.Safari"):
        self.centre = FakeCentre()
        self._frontmost = FakeApp(frontmost_app) if frontmost_app else None

    def notificationCenter(self):
        return self.centre

    def frontmostApplication(self):
        return self._frontmost


@pytest.fixture
def workspace(monkeypatch):
    fake = FakeWorkspace()
    monkeypatch.setattr(frontmost, "_workspace", lambda: fake)
    return fake


def test_nothing_is_observed_before_starting(workspace):
    seen = []
    frontmost.FrontmostWatcher(seen.append)
    assert workspace.centre.blocks == {}


def test_starting_subscribes_to_the_activation_notification(workspace):
    watcher = frontmost.FrontmostWatcher(lambda _: None)
    assert watcher.start() is True
    assert watcher.active
    names = [name for name, _ in workspace.centre.blocks.values()]
    assert names == [frontmost.DID_ACTIVATE]


def test_an_activation_reaches_the_callback_as_a_bundle_id(workspace):
    seen = []
    watcher = frontmost.FrontmostWatcher(seen.append)
    watcher.start()

    workspace.centre.post("com.microsoft.VSCode")

    assert seen == ["com.microsoft.VSCode"]


def test_stopping_removes_the_observer(workspace):
    seen = []
    watcher = frontmost.FrontmostWatcher(seen.append)
    watcher.start()
    token = next(iter(workspace.centre.blocks))

    watcher.stop()

    assert workspace.centre.removed == [token]
    assert not watcher.active
    workspace.centre.post("com.microsoft.VSCode")
    assert seen == []  # and nothing arrives afterwards


def test_stopping_twice_removes_it_once(workspace):
    """Shutdown is reached more than once."""
    watcher = frontmost.FrontmostWatcher(lambda _: None)
    watcher.start()
    watcher.stop()
    watcher.stop()
    assert len(workspace.centre.removed) == 1


def test_starting_twice_leaves_one_observer(workspace):
    """Switching the layer off and on again must not stack subscriptions."""
    watcher = frontmost.FrontmostWatcher(lambda _: None)
    watcher.start()
    watcher.start()
    assert len(workspace.centre.blocks) == 1


def test_an_activation_with_no_bundle_identifier_is_ignored(workspace):
    """Plenty of processes have none, and there is nothing to key on."""
    seen = []
    watcher = frontmost.FrontmostWatcher(seen.append)
    watcher.start()

    workspace.centre.post(None)
    for _, block in workspace.centre.blocks.values():
        block(FakeNotification(None))  # and no userInfo at all

    assert seen == []


def test_a_failing_handler_never_escapes_into_appkit(workspace):
    """This runs inside AppKit's own dispatch, where an exception would
    surface somewhere unhelpful — if at all."""
    watcher = frontmost.FrontmostWatcher(lambda _: 1 / 0)
    watcher.start()
    workspace.centre.post("com.microsoft.VSCode")  # must not raise


def test_the_current_app_can_be_asked_for_directly(workspace):
    """Needed as well as the notification: the watcher can start at any
    moment, and the app already in front has never been announced."""
    assert frontmost.current_bundle_id() == "com.apple.Safari"


# -- with no workspace at all ---------------------------------------------


@pytest.fixture
def no_workspace(monkeypatch):
    monkeypatch.setattr(frontmost, "_workspace", lambda: None)


def test_starting_without_a_workspace_reports_failure(no_workspace):
    """Off macOS, or without pyobjc. Not an error: the layer simply never
    fires and every other part of the app is unaffected."""
    watcher = frontmost.FrontmostWatcher(lambda _: None)
    assert watcher.start() is False
    assert not watcher.active


def test_stopping_what_never_started_is_harmless(no_workspace):
    frontmost.FrontmostWatcher(lambda _: None).stop()


def test_the_current_app_is_unknown_without_a_workspace(no_workspace):
    assert frontmost.current_bundle_id() is None


def test_a_workspace_that_raises_is_survivable(monkeypatch):
    """A future macOS that refuses the subscription must degrade to the
    feature not working, not to the app not starting."""

    class Hostile:
        def notificationCenter(self):
            raise RuntimeError("no")

        def frontmostApplication(self):
            raise RuntimeError("no")

    monkeypatch.setattr(frontmost, "_workspace", lambda: Hostile())
    watcher = frontmost.FrontmostWatcher(lambda _: None)
    assert watcher.start() is False
    assert frontmost.current_bundle_id() is None


# -- the door itself -------------------------------------------------------


def test_every_native_call_goes_through_one_door():
    """The property the conftest guard depends on: block ``_workspace``
    and nothing in this module can reach AppKit's workspace. Asserted on
    the source, because a second import added later would pass every
    behavioural test above while quietly reopening the door."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(frontmost.__file__).read_text(encoding="utf-8"))
    importers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "NSWorkspace" for alias in node.names)
    ]
    assert len(importers) == 1, "NSWorkspace is imported in more than one place"

    # And that one import is inside the door, not at module scope where it
    # would run — and fail — on a machine without pyobjc.
    door = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_workspace"
    )
    assert importers[0] in ast.walk(door)
