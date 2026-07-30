"""Open at Login, through SMAppService.

Why SMAppService and not a LaunchAgent plist: the entry has to show what
the system actually thinks, not what this app last asked for, and only one
of the two can answer that. ``SMAppService.status`` is the system's own
answer and changes the moment the user flips the switch in System Settings
→ General → Login Items. A LaunchAgent is a file we write; the file stays
exactly as written after the user disables the item in System Settings, so
a menu built on it would keep claiming the app starts at login when it no
longer does — the precise drift this is meant to avoid. SMAppService also
puts the app where a user looks for it, and takes it away again on
unregister, leaving nothing behind in ~/Library/LaunchAgents.

The cost is macOS 13, where SMAppService arrived; the app's floor is 11.
On 11 and 12 ``status()`` answers UNSUPPORTED and the menu entry is not
offered at all, which is the honest outcome — better than a LaunchAgent
that cannot be trusted to describe itself on exactly the systems it would
be there to serve.

Everything native is guarded: off macOS, without pyobjc, or below 13,
this module answers UNSUPPORTED and never imports ServiceManagement. The
pure half — what the status means for a menu entry — is where the logic
lives, so it can be tested anywhere.
"""

from __future__ import annotations

import logging
import sys
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

MENU_LABEL = "Open at Login"
# Shown instead when macOS is holding the registration for the user to
# approve. The entry stays unchecked — the app does not start at login yet
# — and the label is the only place that can say why.
NEEDS_APPROVAL_LABEL = "Open at Login (approve in System Settings)"


class LoginItemStatus(Enum):
    """What the system says about this app as a login item."""

    UNSUPPORTED = "unsupported"  # not macOS 13+, no pyobjc, or not cocoa
    NOT_REGISTERED = "not_registered"  # known to macOS, switched off
    ENABLED = "enabled"
    REQUIRES_APPROVAL = "requires_approval"
    # What a bundle that has never been registered reports — measured, not
    # assumed: a freshly built app answers NOT_FOUND, registers cleanly,
    # and answers NOT_REGISTERED after being switched off again. So this is
    # an ordinary "off", not a broken install, and the entry is still
    # offered from it.
    NOT_FOUND = "not_found"


# SMAppServiceStatus, as published by ServiceManagement.
_RAW_STATUS = {
    0: LoginItemStatus.NOT_REGISTERED,
    1: LoginItemStatus.ENABLED,
    2: LoginItemStatus.REQUIRES_APPROVAL,
    3: LoginItemStatus.NOT_FOUND,
}


# -- pure: what a status means ------------------------------------------


def from_raw(raw: int) -> LoginItemStatus:
    """Map an SMAppServiceStatus to ours; anything unrecognised is treated
    as not found rather than assumed to be working."""
    return _RAW_STATUS.get(int(raw), LoginItemStatus.NOT_FOUND)


def is_enabled(status: LoginItemStatus) -> bool:
    """Whether the menu entry should be ticked.

    Only ENABLED. REQUIRES_APPROVAL is the case worth being strict about:
    the registration exists but macOS will not act on it until the user
    approves it, so a tick there would promise a launch that will not
    happen.
    """
    return status is LoginItemStatus.ENABLED


def label_for(status: LoginItemStatus) -> str:
    """The entry's text. Only the approval case differs — it is the one
    state where an unticked box needs explaining."""
    if status is LoginItemStatus.REQUIRES_APPROVAL:
        return NEEDS_APPROVAL_LABEL
    return MENU_LABEL


def is_bundled(frozen: bool, executable: str) -> bool:
    """Whether this process is running from inside a .app.

    Both halves matter: `frozen` alone is true for any PyInstaller build,
    and the path alone could be a script that happens to live inside a
    bundle. Login registration only means anything for the bundle.
    """
    return bool(frozen) and ".app/Contents/MacOS/" in executable


def offered(*, bundled: bool, status: LoginItemStatus) -> bool:
    """Whether to show the entry at all.

    Hidden when running from a source checkout — there is no bundle for
    macOS to launch, and a switch that cannot work is worse than no switch
    — and hidden on a macOS too old to answer for itself.
    """
    return bundled and status is not LoginItemStatus.UNSUPPORTED


def running_bundled() -> bool:
    """``is_bundled`` for this process."""
    return is_bundled(getattr(sys, "frozen", False), sys.executable)


# -- native: what the system says ---------------------------------------


def _main_app_service():
    """The SMAppService for this app, or None where unavailable.

    The single door to ServiceManagement: everything native goes through
    here, so tests have one seam to block and no test can register a real
    login item on the developer's Mac.
    """
    if sys.platform != "darwin":
        return None
    try:
        import ServiceManagement
    except ImportError:
        logger.info("ServiceManagement unavailable — no Open at Login")
        return None
    service_class = getattr(ServiceManagement, "SMAppService", None)
    if service_class is None:  # macOS 12 or older
        logger.info("SMAppService needs macOS 13 — no Open at Login")
        return None
    try:
        return service_class.mainAppService()
    except Exception:
        logger.exception("failed to obtain the main app service")
        return None


def status() -> LoginItemStatus:
    """Ask the system, every time. Never cached here: the user can change
    this in System Settings while the app is running, and a cached answer
    is how the menu and the system drift apart."""
    service = _main_app_service()
    if service is None:
        return LoginItemStatus.UNSUPPORTED
    try:
        return from_raw(service.status())
    except Exception:
        logger.exception("failed to read the login item status")
        return LoginItemStatus.NOT_FOUND


def set_enabled(enabled: bool) -> tuple[bool, LoginItemStatus]:
    """Register or unregister, and report what the system says afterwards.

    Returns (did what was asked, status now). The status is re-read rather
    than assumed, because "the call returned no error" and "the app will
    start at login" are different claims — registration can land in
    REQUIRES_APPROVAL, which is a success that has not happened yet.
    """
    service = _main_app_service()
    if service is None:
        return False, LoginItemStatus.UNSUPPORTED
    try:
        if enabled:
            ok, error = service.registerAndReturnError_(None)
        else:
            ok, error = service.unregisterAndReturnError_(None)
    except Exception:
        logger.exception("login item %s failed", "register" if enabled else "unregister")
        return False, status()

    now = status()
    if not ok:
        logger.warning(
            "login item %s refused by macOS: %s (status now %s)",
            "registration" if enabled else "removal",
            error,
            now.value,
        )
        return False, now
    if enabled and now is LoginItemStatus.REQUIRES_APPROVAL:
        logger.warning(
            "login item registered but needs approval: System Settings → "
            "General → Login Items → enable SottoVoce"
        )
        return False, now
    if enabled and now is not LoginItemStatus.ENABLED:
        logger.warning("login item registered but status is %s", now.value)
        return False, now
    logger.info("login item %s (status %s)", "enabled" if enabled else "disabled", now.value)
    return True, now
