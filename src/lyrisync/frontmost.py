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
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# NSWorkspace's own names. Spelled out rather than imported so this module
# can be reasoned about — and its structure tested — without pyobjc.
DID_ACTIVATE = "NSWorkspaceDidActivateApplicationNotification"
APPLICATION_KEY = "NSWorkspaceApplicationKey"


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


def current_bundle_id() -> Optional[str]:
    """The bundle identifier of the frontmost app right now, or None.

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
        bundle_id = str(app.bundleIdentifier()) if app is not None else None
    except Exception:
        logger.debug("could not read the frontmost application", exc_info=True)
        return None
    logger.debug("asked macOS for the frontmost app: %s", bundle_id)
    return bundle_id


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
    """Calls back with a bundle identifier each time an app comes forward.

    Block-based rather than an NSObject subclass with a selector: there is
    no state to hold on the Objective-C side, and a subclass would mean
    defining a class at import time on a machine that may have no pyobjc.
    The token that comes back is the only thing to keep, and the only
    thing to hand back when stopping.
    """

    def __init__(self, on_activate: Callable[[str], None]) -> None:
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
        """Pull the bundle identifier out of a notification and hand it on.

        Deliberately forgiving: this runs inside AppKit's own dispatch, so
        an exception here would surface somewhere unhelpful. An activation
        that cannot be read is one the window does not follow, which is the
        same outcome as an app with no remembered position.
        """
        try:
            info = notification.userInfo()
            app = info.objectForKey_(APPLICATION_KEY) if info is not None else None
            bundle_id = app.bundleIdentifier() if app is not None else None
        except Exception:
            logger.debug("unreadable activation notification", exc_info=True)
            return
        if not bundle_id:
            # A process with no bundle identifier is nothing to key on. Said
            # out loud, because from further up the chain this is
            # indistinguishable from no notification having arrived at all.
            logger.debug("activation notification with no bundle identifier")
            return
        logger.debug("activation notification: %s", bundle_id)
        try:
            self._on_activate(str(bundle_id))
        except Exception:
            logger.exception("activation handler failed")
