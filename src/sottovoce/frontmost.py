"""Which application is in front, and when that changes.

NSWorkspace publishes an activation notification every time the frontmost
application changes, and subscribing to it needs no permission at all —
nothing here reads a keystroke, a window's contents, or anything about
the other app beyond the identifier it advertises. That is the whole
reason this is the mechanism: the alternatives that could answer the same
question (an Accessibility observer, polling the window list) all ask the
user to grant something first, for information macOS is already
broadcasting.

A full-screen Space switch counts as an activation, and that is wanted
rather than tolerated: moving into a full-screen editor IS arriving at
that app, and the window should follow it there.

Everything native goes through ``_workspace()`` — one door, the same
shape as hotkey._carbon() and login_item._main_app_service(). A stray
observer in the test suite would sit on the developer's own workspace for
the life of the process, so there is exactly one thing for the suite to
shut.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, NamedTuple, Optional

logger = logging.getLogger(__name__)

# NSWorkspace's own names. Spelled out rather than imported so this module
# can be reasoned about — and its structure tested — without pyobjc.
DID_ACTIVATE = "NSWorkspaceDidActivateApplicationNotification"
APPLICATION_KEY = "NSWorkspaceApplicationKey"

# NSCompositingOperationSourceOver. Spelled out for the same reason as the
# names above.
_SOURCE_OVER = 2


class AppIdentity(NamedTuple):
    """An application as this app needs to talk about one: the identifier
    it is keyed on, and the name a person would call it.

    The name travels WITH the identifier rather than being looked up when
    wanted. Both arrive together in the activation notification, so taking
    the name then costs nothing and needs no second question; asking later
    would mean walking the running-application list on every menu refresh,
    and would have no answer at all for an app that has since quit.
    """

    bundle_id: str
    name: Optional[str] = None


def _workspace():
    """The shared NSWorkspace, or None where there is not one.

    The single door. Returns None off macOS and without pyobjc, so every
    caller has one branch to handle and the suite has one seam to block.
    """
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSWorkspace
    except Exception:  # pragma: no cover - pyobjc missing
        logger.info("AppKit unavailable — no per-app position memory")
        return None
    return NSWorkspace.sharedWorkspace()


def current_app() -> Optional[AppIdentity]:
    """The frontmost app right now, or None.

    Needed as well as the notification: the watcher can start at any
    moment, and until something else is activated the app in front has
    never been announced. Without this, learning a position in the first
    app you use after switching the layer on would go nowhere.
    """
    workspace = _workspace()
    if workspace is None:
        return None
    try:
        app = workspace.frontmostApplication()
        identity = _identity(app)
    except Exception:
        logger.debug("could not read the frontmost application", exc_info=True)
        return None
    logger.debug("asked macOS for the frontmost app: %s", identity)
    return identity


def _identity(app) -> Optional[AppIdentity]:
    """One NSRunningApplication as an AppIdentity, or None.

    The name is optional in both directions: an app with no identifier is
    nothing to key on and gives None, while an app with an identifier and
    no name is perfectly usable — the readout falls back to the identifier
    and says so.
    """
    if app is None:
        return None
    bundle_id = app.bundleIdentifier()
    if not bundle_id:
        return None
    try:
        name = app.localizedName()
    except Exception:  # pragma: no cover - an app that will not say
        name = None
    return AppIdentity(str(bundle_id), str(name) if name else None)


def app_icon_tiff(bundle_id: str, points: int) -> Optional[bytes]:
    """The application's icon as TIFF bytes at ``points``, or None.

    Bytes rather than an NSImage, so nothing pyobjc-shaped crosses out of
    this module: the caller turns them into a QPixmap and the test suite
    hands over a file's worth of bytes without AppKit.

    Redrawn at the size wanted rather than handed over as it comes.
    ``iconForFile_`` returns an image carrying every representation from
    16 to 1024 — measured at **74 MB** of TIFF, decoding to a 1024x1024
    pixmap — which is not a menu icon by any reading. Drawing it once into
    an image of the size asked for costs 37 KB and comes back at the
    screen's own scale.

    Keyed on the bundle identifier through the workspace rather than on a
    running process, so an app that is remembered but not running still
    has a face. That is the whole point: the list of remembered apps
    outlives the sessions that taught it.
    """
    workspace = _workspace()
    if workspace is None or not bundle_id:
        return None
    try:
        from AppKit import NSImage, NSMakeRect, NSZeroRect

        url = workspace.URLForApplicationWithBundleIdentifier_(bundle_id)
        if url is None:
            return None  # not installed any more; the name still is known
        image = workspace.iconForFile_(url.path())
        if image is None:
            return None
        drawn = NSImage.alloc().initWithSize_((points, points))
        drawn.lockFocus()
        image.drawInRect_fromRect_operation_fraction_(
            NSMakeRect(0, 0, points, points), NSZeroRect, _SOURCE_OVER, 1.0
        )
        drawn.unlockFocus()
        data = drawn.TIFFRepresentation()
    except Exception:
        logger.debug("could not draw an icon for %s", bundle_id, exc_info=True)
        return None
    return bytes(data) if data is not None else None


def own_bundle_id() -> Optional[str]:
    """This process's own bundle identifier, or None when it has none.

    Asked rather than written down, so there is no second copy of the
    identifier to drift from the one in the bundle. A source run answers
    None (or the interpreter's own), which is the right answer too: a
    process with no bundle cannot be mistaken for the frontmost app.
    """
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSBundle

        identifier = NSBundle.mainBundle().bundleIdentifier()
    except Exception:
        return None
    return str(identifier) if identifier else None


class FrontmostWatcher:
    """Calls back with an AppIdentity each time an app comes forward.

    Block-based rather than an NSObject subclass with a selector: there is
    no state to hold on the Objective-C side, and a subclass would mean
    defining a class at import time on a machine that may have no pyobjc.
    The token that comes back is the only thing to keep, and the only
    thing to hand back when stopping.
    """

    def __init__(self, on_activate: Callable[[AppIdentity], None]) -> None:
        self._on_activate = on_activate
        self._observer = None
        self._centre = None

    @property
    def active(self) -> bool:
        return self._observer is not None

    def start(self) -> bool:
        """Begin observing. False when there is nothing to observe with —
        off macOS, or without pyobjc — which is not an error: the layer
        simply never fires, and every other part of the app is unaffected.
        """
        if self._observer is not None:
            return True
        workspace = _workspace()
        if workspace is None:
            return False
        try:
            centre = workspace.notificationCenter()
            # queue=None delivers on the posting thread, which for these
            # notifications is the main thread — the same thread Qt runs
            # on, so the callback is an ordinary UI call.
            self._observer = centre.addObserverForName_object_queue_usingBlock_(
                DID_ACTIVATE, None, None, self._handle
            )
            self._centre = centre
        except Exception:
            logger.exception("could not observe application activations")
            self._observer = None
            return False
        logger.info("watching for application activations")
        return True

    def stop(self) -> None:
        """Remove the observer. Idempotent, because shutdown is reached
        more than once and because switching the layer off and on again
        must not leave two observers behind."""
        if self._observer is None:
            return
        observer, centre, self._observer, self._centre = (
            self._observer,
            self._centre,
            None,
            None,
        )
        try:
            centre.removeObserver_(observer)
        except Exception:
            logger.debug("could not remove the activation observer", exc_info=True)
            return
        logger.debug("stopped watching for application activations")

    def _handle(self, notification) -> None:
        """Pull the app out of a notification and hand it on.

        Both halves of the identity are taken here, while the notification
        still has the application object in hand: the identifier to key on
        and the name to show. Deliberately forgiving otherwise — this runs
        inside AppKit's own dispatch, so an exception here would surface
        somewhere unhelpful. An activation that cannot be read is one the
        window does not follow, which is the same outcome as an app with no
        remembered position.
        """
        try:
            info = notification.userInfo()
            app = info.objectForKey_(APPLICATION_KEY) if info is not None else None
            identity = _identity(app)
        except Exception:
            logger.debug("unreadable activation notification", exc_info=True)
            return
        if identity is None:
            # A process with no bundle identifier is nothing to key on. Said
            # out loud, because from further up the chain this is
            # indistinguishable from no notification having arrived at all.
            logger.debug("activation notification with no bundle identifier")
            return
        logger.debug(
            "activation notification: %s (%s)", identity.bundle_id, identity.name
        )
        try:
            self._on_activate(identity)
        except Exception:
            logger.exception("activation handler failed")
