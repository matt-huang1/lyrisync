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

TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce import frontmost


class FakeApp:
    def __init__(self, bundle_id, name=None):
        self._bundle_id = bundle_id
        self._name = name

    def bundleIdentifier(self):
        return self._bundle_id

    def localizedName(self):
        return self._name


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

    def post(self, bundle_id, name=None):
        """What AppKit does when an app comes forward."""
        for _, block in list(self.blocks.values()):
            block(FakeNotification(FakeApp(bundle_id, name)))


class FakeWorkspace:
    def __init__(self, frontmost_app="com.apple.Safari", name="Safari"):
        self.centre = FakeCentre()
        self._frontmost = FakeApp(frontmost_app, name) if frontmost_app else None
        self.urls = {}
        self.icons = {}

    def notificationCenter(self):
        return self.centre

    def frontmostApplication(self):
        return self._frontmost

    def URLForApplicationWithBundleIdentifier_(self, bundle_id):
        path = self.urls.get(bundle_id)
        return FakeURL(path) if path else None

    def iconForFile_(self, path):
        return self.icons.get(path)


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


def test_an_activation_reaches_the_callback_as_an_identity(workspace):
    """Both halves at once. The name is taken while the notification still
    has the application in hand — asking later would mean walking the
    running-application list, and would have no answer at all for an app
    that has since quit."""
    seen = []
    watcher = frontmost.FrontmostWatcher(seen.append)
    watcher.start()

    workspace.centre.post("com.microsoft.VSCode", "Code")

    assert seen == [frontmost.AppIdentity("com.microsoft.VSCode", "Code")]


def test_an_app_that_will_not_say_its_name_is_still_an_arrival(workspace):
    """The identifier is what the map is keyed on; the name is a courtesy,
    and the readout falls back to the identifier when there is none."""
    seen = []
    watcher = frontmost.FrontmostWatcher(seen.append)
    watcher.start()

    workspace.centre.post("com.microsoft.VSCode", None)

    assert seen == [frontmost.AppIdentity("com.microsoft.VSCode", None)]


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
    assert frontmost.current_app() == frontmost.AppIdentity(
        "com.apple.Safari", "Safari"
    )


# -- icons -----------------------------------------------------------------


class FakeURL:
    def __init__(self, path):
        self._path = path

    def path(self):
        return self._path


class FakeImage:
    """Enough NSImage to be drawn into another one and asked for TIFF."""

    def __init__(self, tiff=b"TIFF"):
        self._tiff = tiff
        self.drawn = []

    def initWithSize_(self, size):
        self.size = size
        return self

    def lockFocus(self):
        self.locked = True

    def unlockFocus(self):
        self.locked = False

    def drawInRect_fromRect_operation_fraction_(self, rect, from_rect, op, alpha):
        self.drawn.append((rect, op, alpha))

    def TIFFRepresentation(self):
        return self._tiff


def test_an_icon_comes_back_as_plain_bytes(workspace, monkeypatch):
    """Bytes, not an NSImage: nothing pyobjc-shaped crosses out of this
    module, so the caller needs no AppKit to turn it into a pixmap."""
    workspace.urls = {"com.apple.Safari": "/Applications/Safari.app"}
    workspace.icons = {"/Applications/Safari.app": FakeImage(b"SAFARI")}
    monkeypatch.setitem(
        __import__("sys").modules,
        "AppKit",
        _fake_appkit(FakeImage(b"DRAWN")),
    )

    assert frontmost.app_icon_tiff("com.apple.Safari", 16) == b"DRAWN"


def test_an_app_that_is_not_installed_has_no_icon(workspace, monkeypatch):
    """A remembered app can be uninstalled between sessions. Its name is
    still known, so the list still reads — it simply has no face."""
    workspace.urls = {}
    monkeypatch.setitem(__import__("sys").modules, "AppKit", _fake_appkit(FakeImage()))

    assert frontmost.app_icon_tiff("com.apple.Safari", 16) is None


def test_no_icon_without_a_workspace(no_workspace):
    assert frontmost.app_icon_tiff("com.apple.Safari", 16) is None


def test_an_empty_identifier_is_never_asked_about(workspace):
    assert frontmost.app_icon_tiff("", 16) is None


def _fake_appkit(image):
    """A stand-in AppKit module holding just what app_icon_tiff imports."""
    import types

    module = types.ModuleType("AppKit")
    module.NSImage = type("NSImage", (), {"alloc": staticmethod(lambda: image)})
    module.NSMakeRect = lambda *args: args
    module.NSZeroRect = ()
    return module


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
    assert frontmost.current_app() is None


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
    assert frontmost.current_app() is None


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
