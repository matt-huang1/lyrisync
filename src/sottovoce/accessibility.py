"""Which macOS accessibility display settings are on, and when they change.

Three switches in System Settings > Accessibility > Display say things
about this window that this window had never asked:

- **Reduce Motion** asks for less movement. The window flies to and from
  the menu bar item, travels to remembered positions, and rises each lyric
  into place. All three are movement, and none of them was optional.
- **Reduce Transparency** asks for no see-through surfaces. The window's
  whole material is an NSVisualEffectView, which is exactly the thing the
  setting is about.
- **Increase Contrast** asks for more separation than the default. Every
  role but the sung line deliberately recedes here, which is the right
  default and the wrong answer to somebody who has said they need more.

Read live rather than at startup, for the same reason the appearance is
(window.py's ``_on_color_scheme_changed``): a person who turns Reduce
Motion on because a migraine has started is not going to relaunch the
app to be believed. NSWorkspace posts
``NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification`` for all
three, so one observer answers all of them.

Everything native goes through ``_workspace()`` — one door, the same
shape as frontmost._workspace(), hotkey._carbon() and
login_item._main_app_service(). NSWorkspace is imported in exactly one
place in this module and the suite shuts that one place, because a test
that read these would be a test whose result depends on how the developer
has their Mac set up.

Deliberately NOT the same door as frontmost.py's, even though both stand
on NSWorkspace. They are two capabilities with two lifetimes: per-app
position memory is an opt-in layer that unsubscribes when it is switched
off, and this is a system setting the app follows for as long as it runs.
Sharing one door would mean the suite could not block one without
blocking the other.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, NamedTuple, Optional

logger = logging.getLogger(__name__)

# NSWorkspace's own name for the change notification. Spelled out rather
# than imported so this module can be reasoned about — and its structure
# tested — without pyobjc, the same convention frontmost.py uses.
OPTIONS_CHANGED = "NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification"


class DisplayOptions(NamedTuple):
    """What the system has asked for, as three independent answers.

    All False is both the macOS default and what every platform that
    cannot answer reports, which is what makes "no pyobjc", "not macOS"
    and "nothing switched on" the same code path: the plain window.
    """

    reduce_motion: bool = False
    reduce_transparency: bool = False
    increase_contrast: bool = False

    @property
    def solid_background(self) -> bool:
        """Whether the vibrancy material must go.

        Reduce Transparency says so directly. Increase Contrast says so
        too, and that is not this app being clever: macOS itself turns
        Reduce Transparency on and locks it there while Increase Contrast
        is on, because a blurred backdrop and a contrast guarantee cannot
        both be honoured. Deriving it here rather than trusting the pair
        to arrive together means the app is right even where they do not.
        """
        return self.reduce_transparency or self.increase_contrast


# What a machine with nothing switched on reports, and what everything
# that cannot answer reports. One object so "unknown" and "off" are
# indistinguishable downstream, which is the whole promise.
NONE = DisplayOptions()


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
        logger.info("AppKit unavailable: accessibility display settings unread")
        return None
    return NSWorkspace.sharedWorkspace()


def current_options() -> DisplayOptions:
    """What the three switches say right now.

    Asked in full rather than one at a time: the notification says only
    that something changed, so every read is a read of all three anyway.
    A workspace that will not answer gives ``NONE``, which is the plain
    window — never a half-applied set.
    """
    workspace = _workspace()
    if workspace is None:
        return NONE
    try:
        options = DisplayOptions(
            reduce_motion=bool(workspace.accessibilityDisplayShouldReduceMotion()),
            reduce_transparency=bool(
                workspace.accessibilityDisplayShouldReduceTransparency()
            ),
            increase_contrast=bool(
                workspace.accessibilityDisplayShouldIncreaseContrast()
            ),
        )
    except Exception:
        logger.debug("could not read the accessibility display options", exc_info=True)
        return NONE
    logger.debug("accessibility display options: %s", describe(options))
    return options


def describe(options: DisplayOptions) -> str:
    """The switches that are on, for the log. "none" when none are."""
    on = [
        name
        for name, value in zip(
            ("reduce motion", "reduce transparency", "increase contrast"), options
        )
        if value
    ]
    return ", ".join(on) if on else "none"


class DisplayOptionsWatcher:
    """Calls back with fresh ``DisplayOptions`` each time they change.

    Block-based rather than an NSObject subclass, and shaped exactly like
    frontmost.FrontmostWatcher for the same reasons: no state to hold on
    the Objective-C side, and no class to define at import time on a
    machine that may have no pyobjc.

    The callback receives the whole set, re-read at the moment of the
    notification. The notification itself carries no payload — it says
    only that something moved — so asking again is not an optimisation
    that was skipped, it is the API.
    """

    def __init__(self, on_change: Callable[[DisplayOptions], None]) -> None:
        self._on_change = on_change
        self._observer = None
        self._centre = None

    @property
    def active(self) -> bool:
        return self._observer is not None

    def start(self) -> bool:
        """Begin observing. False when there is nothing to observe with —
        off macOS, or without pyobjc — which is not an error: the options
        stay at whatever the first read said, which is ``NONE``.
        """
        if self._observer is not None:
            return True
        workspace = _workspace()
        if workspace is None:
            return False
        try:
            centre = workspace.notificationCenter()
            # queue=None delivers on the posting thread, which for this
            # notification is the main thread — the same thread Qt runs
            # on, so the callback is an ordinary UI call.
            self._observer = centre.addObserverForName_object_queue_usingBlock_(
                OPTIONS_CHANGED, None, None, self._handle
            )
            self._centre = centre
        except Exception:
            logger.exception("could not observe the accessibility display options")
            self._observer = None
            return False
        logger.info("watching for accessibility display option changes")
        return True

    def stop(self) -> None:
        """Remove the observer. Idempotent, because shutdown is reached
        more than once."""
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
            logger.debug("could not remove the display options observer", exc_info=True)
            return
        logger.debug("stopped watching the accessibility display options")

    def _handle(self, notification) -> None:
        """Re-read and hand on.

        Deliberately forgiving: this runs inside AppKit's own dispatch, so
        an exception here would surface somewhere unhelpful. A change that
        cannot be read leaves the window as it was, which is the same
        outcome as the notification never arriving.
        """
        try:
            options = current_options()
        except Exception:  # pragma: no cover - current_options catches its own
            logger.debug("unreadable display options notification", exc_info=True)
            return
        try:
            self._on_change(options)
        except Exception:
            logger.exception("display options handler failed")
