"""The global show/hide hotkey, through Carbon's event manager.

Why Carbon, in 2026: ``RegisterEventHotKey`` is the only way on macOS to
claim a key combination system-wide without asking for Accessibility
permission. The alternatives — a ``CGEventTap`` or ``NSEvent``'s global
monitors — both read every keystroke on the machine, so macOS puts them
behind System Settings → Privacy & Security → Accessibility and a prompt
the user has to leave the app to answer. This app needs one combination,
not a keylogger, and the event manager grants exactly that: macOS matches
the combination itself and calls back only when it fires. Deprecated it
may be, but nothing has replaced it, and every menu bar utility that
toggles with a shortcut and never asks for Accessibility is doing this.

pyobjc has no Carbon bindings, so the calls go through ctypes. That is
not the workaround it looks like — the symbols live in a framework that
is still shipped and still exported (measured: ``RegisterEventHotKey``,
``InstallEventHandler`` and friends all resolve), and the only thing
missing is ``NewEventHandlerUPP``, which on 64-bit is a no-op that
returns the function pointer it was given. So the ctypes callback is the
UPP.

``_carbon()`` is the single door. Everything native goes through it, so
tests have one seam to block — a stray registration in the suite would
take ⇧⌘J away from whoever was running it. The pure half — what the
combination is and how it reads — is a plain dataclass and a string, and
is tested everywhere.

Nothing here is required. Off macOS, or with the framework unreachable,
``register()`` answers False, says so in the log, and the app carries on
with the menu entry that has always been the way to hide the window.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import POINTER, Structure, c_int32, c_uint32, c_void_p, pointer
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# -- pure: what the combination is --------------------------------------

# Carbon modifier masks (Events.h). Apple's values, ABI-stable.
CONTROL = 1 << 12
OPTION = 1 << 11
SHIFT = 1 << 9
COMMAND = 1 << 8

# Virtual key codes (Events.h). A position on the keyboard, not a
# character: kVK_ANSI_J is where J sits on a US layout, and the same
# physical key on any other — which is what a hotkey should follow.
# Verified against the live layout rather than read off a header:
# CGEventKeyboardGetUnicodeString on a keyboard event built from 0x26
# answers "j" (and 0x25, the L this used to be, answers "l").
KEY_J = 0x26

# Apple's order for writing a combination down, outermost modifier first.
# ⌘⇧J and ⇧⌘J are the same keys; only one of them is how macOS spells it.
_MODIFIER_SYMBOLS = (
    (CONTROL, "⌃"),
    (OPTION, "⌥"),
    (SHIFT, "⇧"),
    (COMMAND, "⌘"),
)


@dataclass(frozen=True)
class Combination:
    """A key and its modifiers, as Carbon wants them plus how to say it."""

    key_code: int
    modifiers: int
    key_label: str


# The one constant. Not configurable in v1: a binding the user can change
# needs somewhere to change it, a way to describe a conflict, and a
# fallback when the new one is refused — all of which is a feature, not a
# setting. ⇧⌘J is unclaimed by macOS itself; it was ⇧⌘L until that turned
# out to collide with something the user already runs, which is the whole
# argument for keeping the combination in exactly one place — moving it
# was this constant, its label, and nothing else.
TOGGLE_LYRICS = Combination(key_code=KEY_J, modifiers=COMMAND | SHIFT, key_label="J")


def describe(combination: Combination) -> str:
    """The combination as macOS writes it, e.g. ``⇧⌘J``. For logs and the
    README; the menu deliberately does not show it (see window.py)."""
    return "".join(
        symbol for mask, symbol in _MODIFIER_SYMBOLS if combination.modifiers & mask
    ) + combination.key_label


# -- native: claiming it ------------------------------------------------

_CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"

_NO_ERR = 0
_EVENT_CLASS_KEYBOARD = 0x6B657962  # 'keyb'
_EVENT_HOT_KEY_PRESSED = 5          # kEventHotKeyPressed
# Four-char code identifying whose hotkey this is, and an id within it.
# Only ever one of ours, so the handler does not have to tell them apart.
_SIGNATURE = 0x4C595253             # 'LYRS'
_HOTKEY_ID = 1


class _EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


class _EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


# OSStatus (*)(EventHandlerCallRef, EventRef, void *). On 64-bit macOS a
# UPP is just the function pointer, which is why NewEventHandlerUPP is
# gone from the framework's exports and this can be passed straight in.
_HANDLER = ctypes.CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)


def _carbon():
    """The Carbon framework, ready to call, or None where unavailable.

    The single door: everything native goes through here, so the suite
    has one seam to block and no test can quietly take ⇧⌘J away from the
    developer running it. Signatures are declared here rather than at the
    call site for the same reason — what comes back is either fully
    configured or None, and there is no half-usable middle.
    """
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.cdll.LoadLibrary(_CARBON)
    except OSError:
        logger.info("Carbon unavailable: no global hotkey")
        return None

    lib.GetApplicationEventTarget.argtypes = []
    lib.GetApplicationEventTarget.restype = c_void_p
    lib.InstallEventHandler.argtypes = [
        c_void_p, _HANDLER, c_uint32, POINTER(_EventTypeSpec),
        c_void_p, POINTER(c_void_p),
    ]
    lib.InstallEventHandler.restype = c_int32
    lib.RemoveEventHandler.argtypes = [c_void_p]
    lib.RemoveEventHandler.restype = c_int32
    lib.RegisterEventHotKey.argtypes = [
        c_uint32, c_uint32, _EventHotKeyID, c_void_p, c_uint32, POINTER(c_void_p),
    ]
    lib.RegisterEventHotKey.restype = c_int32
    lib.UnregisterEventHotKey.argtypes = [c_void_p]
    lib.UnregisterEventHotKey.restype = c_int32
    return lib


class GlobalHotkey:
    """One system-wide combination, registered and released explicitly.

    The callback runs on the main thread: Carbon dispatches hotkey events
    from the application's event target, which the Qt event loop pumps as
    part of ordinary event delivery. So the callback may touch the UI
    directly, and does — there is no thread to hop off.

    Pressing the combination does not activate the app or move focus.
    Nothing here asks it to: the event is delivered to a target this
    process already owns, which is what makes the hotkey consistent with
    an accessory app whose window never takes focus.
    """

    def __init__(self, combination: Combination, on_pressed: Callable[[], None]) -> None:
        self._combination = combination
        self._on_pressed = on_pressed
        self._lib = None
        # The ctypes callback object. Held because C holds a pointer into
        # it: let this be collected and Carbon calls freed memory.
        self._callback = None
        self._handler_ref: Optional[c_void_p] = None
        self._hotkey_ref: Optional[c_void_p] = None

    @property
    def combination(self) -> Combination:
        return self._combination

    @property
    def registered(self) -> bool:
        """Whether macOS is currently routing the combination here."""
        return self._hotkey_ref is not None

    def register(self) -> bool:
        """Claim the combination. False means the app is exactly as usable
        as before, minus the shortcut.

        Registration is not exclusive across apps — measured, not assumed:
        two SottoVoce processes both claimed ⇧⌘J and both got noErr, and
        macOS decided between them for each press. ``eventHotKeyExistsErr``
        comes back only when *this* process already holds the combination.
        So a refusal here is never "another app owns it"; it is the event
        manager saying no for a reason this side cannot name. The response
        is the same either way — log it and carry on with the menu, since
        nothing else in the app depends on the hotkey existing.
        """
        if self.registered:
            return True
        try:
            lib = _carbon()
            if lib is None:
                return False
            target = lib.GetApplicationEventTarget()
            callback = _HANDLER(self._dispatch)
            spec = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOT_KEY_PRESSED)
            handler_ref = c_void_p()
            status = lib.InstallEventHandler(
                target, callback, 1, pointer(spec), None, pointer(handler_ref)
            )
            if status != _NO_ERR:
                logger.warning(
                    "could not install the hotkey handler (OSStatus %d): %s "
                    "will do nothing; use the menu bar item to show or hide "
                    "the lyrics",
                    status,
                    describe(self._combination),
                )
                return False
            hotkey_ref = c_void_p()
            status = lib.RegisterEventHotKey(
                self._combination.key_code,
                self._combination.modifiers,
                _EventHotKeyID(_SIGNATURE, _HOTKEY_ID),
                target,
                0,
                pointer(hotkey_ref),
            )
            if status != _NO_ERR:
                # Leave nothing behind on the way out of a failure.
                lib.RemoveEventHandler(handler_ref)
                logger.warning(
                    "could not claim %s (OSStatus %d): use the menu bar "
                    "item to show or hide the lyrics",
                    describe(self._combination),
                    status,
                )
                return False
        except Exception:
            logger.exception("failed to register the global hotkey")
            return False

        self._lib = lib
        self._callback = callback
        self._handler_ref = handler_ref
        self._hotkey_ref = hotkey_ref
        logger.info("global hotkey %s registered", describe(self._combination))
        return True

    def unregister(self) -> None:
        """Give the combination back. Idempotent, because shutdown can be
        reached more than once and a double release is a double free."""
        if self._hotkey_ref is None:
            return
        lib, hotkey_ref, handler_ref = self._lib, self._hotkey_ref, self._handler_ref
        # Cleared before the native calls, not after: whatever they do,
        # this object is done with those references.
        self._lib = None
        self._hotkey_ref = None
        self._handler_ref = None
        try:
            lib.UnregisterEventHotKey(hotkey_ref)
            lib.RemoveEventHandler(handler_ref)
        except Exception:
            logger.exception("failed to release the global hotkey")
        # Dropped only now: until the handler is removed, Carbon still
        # holds a pointer into it.
        self._callback = None
        logger.info("global hotkey %s released", describe(self._combination))

    def _dispatch(self, next_handler, event, user_data) -> int:
        """What Carbon calls. Never raises: an exception escaping a ctypes
        callback returns into C with nobody to catch it, and a shortcut
        that misbehaves must not take the app with it."""
        try:
            self._on_pressed()
        except Exception:
            logger.exception("global hotkey handler failed")
        return _NO_ERR
