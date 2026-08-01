"""The global hotkey: what the combination is, and what registering it does.

Registration itself is manual-verify — it claims a key combination on the
whole machine for as long as the process lives, and no assertion can tell
you the keys actually worked from inside another app's full-screen Space.
So the framework is blocked suite-wide (see conftest) and what is tested
here is everything on this side of it: the combination and how it reads,
and the register/press/release sequence driven through a stand-in for the
one door that reaches Carbon.

Nothing here is macOS-only. The door is faked, and the unavailable branch
is the one the Linux runner takes for real.
"""

TIER = "unit"  # Qt-free logic, called directly

import logging

import pytest

from sottovoce import hotkey
from sottovoce.hotkey import Combination, GlobalHotkey


# -- what the combination is ----------------------------------------------


def test_the_default_is_command_shift_l():
    assert hotkey.TOGGLE_LYRICS.key_code == hotkey.KEY_J
    assert hotkey.TOGGLE_LYRICS.modifiers == hotkey.COMMAND | hotkey.SHIFT
    assert hotkey.TOGGLE_LYRICS.key_label == "J"


def test_there_is_exactly_one_combination_to_change():
    """Not configurable in v1, so the constant is the whole contract: the
    README quotes it and the window asks for it by name."""
    assert isinstance(hotkey.TOGGLE_LYRICS, Combination)
    assert hotkey.describe(hotkey.TOGGLE_LYRICS) == "⇧⌘J"


def test_modifiers_read_in_apples_order():
    """⌘⇧J and ⇧⌘J are the same keys and only one is how macOS spells it,
    so the order is fixed rather than however the masks happen to sit."""
    every = Combination(
        key_code=hotkey.KEY_J,
        modifiers=hotkey.COMMAND | hotkey.SHIFT | hotkey.OPTION | hotkey.CONTROL,
        key_label="J",
    )
    assert hotkey.describe(every) == "⌃⌥⇧⌘J"


def test_an_unmodified_key_is_just_the_key():
    assert hotkey.describe(Combination(hotkey.KEY_J, 0, "J")) == "J"


def test_the_carbon_constants_are_apples():
    """These are the values Carbon publishes; getting one wrong registers
    a combination nobody asked for and silently never fires."""
    assert hotkey.COMMAND == 0x0100
    assert hotkey.SHIFT == 0x0200
    assert hotkey.OPTION == 0x0800
    assert hotkey.CONTROL == 0x1000
    assert hotkey.KEY_J == 38  # kVK_ANSI_J, verified against the live layout


# -- registering it, through a stand-in for the one door ------------------


class FakeCarbon:
    """The framework as ``_carbon()`` hands it over: already configured,
    answering the six calls the hotkey makes. Out-parameters are written
    through exactly as ctypes writes them, so the code under test is the
    real code and only the far side of the door is invented."""

    TARGET = 0xEE
    HANDLER_REF = 0xA1
    HOTKEY_REF = 0xB2

    def __init__(self, install_status=0, register_status=0):
        self._install_status = install_status
        self._register_status = register_status
        self.handler = None
        self.registered = []
        self.unregistered = []
        self.removed = []
        self.calls = []

    def GetApplicationEventTarget(self):
        self.calls.append("target")
        return self.TARGET

    def InstallEventHandler(self, target, callback, count, types, user_data, out):
        self.calls.append("install")
        assert target == self.TARGET
        assert count == 1
        assert types.contents.eventClass == 0x6B657962  # 'keyb'
        assert types.contents.eventKind == 5  # kEventHotKeyPressed
        if self._install_status == 0:
            self.handler = callback
            out.contents.value = self.HANDLER_REF
        return self._install_status

    def RemoveEventHandler(self, ref):
        self.calls.append("remove")
        self.removed.append(ref.value)
        return 0

    def RegisterEventHotKey(self, key_code, modifiers, hotkey_id, target, options, out):
        self.calls.append("register")
        assert target == self.TARGET
        if self._register_status == 0:
            self.registered.append((key_code, modifiers, hotkey_id.signature))
            out.contents.value = self.HOTKEY_REF
        return self._register_status

    def UnregisterEventHotKey(self, ref):
        self.calls.append("unregister")
        self.unregistered.append(ref.value)
        return 0

    def press(self):
        """What macOS does when the combination is hit."""
        return self.handler(None, None, None)


@pytest.fixture
def carbon(monkeypatch):
    """Open the door onto a fake. The suite-wide guard is what it replaces,
    and it goes back afterwards."""

    def install(lib):
        monkeypatch.setattr(hotkey, "_carbon", lambda: lib)
        return lib

    return install


def test_registering_claims_exactly_the_combination_it_was_given(carbon):
    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)

    assert hk.register() is True
    assert hk.registered is True
    assert lib.registered == [
        (hotkey.KEY_J, hotkey.COMMAND | hotkey.SHIFT, 0x4C595253)
    ]


def test_a_press_reaches_the_callback(carbon):
    lib = carbon(FakeCarbon())
    presses = []
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: presses.append(True))
    hk.register()

    lib.press()
    lib.press()
    assert presses == [True, True]


def test_the_callback_object_is_held_for_as_long_as_carbon_has_it(carbon):
    """C keeps a pointer into the ctypes callback. Letting Python collect
    it while the handler is installed is a crash, not a leak — so it is
    dropped only after the handler has been removed."""
    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)
    hk.register()
    assert hk._callback is not None

    hk.unregister()
    assert hk._callback is None
    assert lib.calls == ["target", "install", "register", "unregister", "remove"]


def test_registering_twice_claims_it_once(carbon):
    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)

    assert hk.register() is True
    assert hk.register() is True
    assert len(lib.registered) == 1


def test_unregistering_gives_back_both_the_hotkey_and_the_handler(carbon):
    """A stale registration after quit is the bug this milestone names, and
    the handler is half of it: leaving that installed leaves Carbon holding
    a pointer to a callback into a window that is being torn down."""
    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)
    hk.register()

    hk.unregister()
    assert hk.registered is False
    assert lib.unregistered == [FakeCarbon.HOTKEY_REF]
    assert lib.removed == [FakeCarbon.HANDLER_REF]


def test_unregistering_twice_releases_nothing_twice(carbon):
    """Shutdown can be reached more than once — the menu's Quit and
    aboutToQuit both land on it — and a double release is a double free."""
    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)
    hk.register()

    hk.unregister()
    hk.unregister()
    assert lib.unregistered == [FakeCarbon.HOTKEY_REF]
    assert lib.removed == [FakeCarbon.HANDLER_REF]


def test_unregistering_something_never_registered_is_a_no_op(carbon):
    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)
    hk.unregister()
    assert lib.calls == []


def test_a_refused_registration_leaves_nothing_installed(carbon, caplog):
    """The app has to stay entirely usable and leave nothing behind it.

    The message deliberately does not name a cause. Measured: two
    processes can both claim ⇧⌘J and both get noErr, so a refusal here is
    never "another app owns it" — eventHotKeyExistsErr comes back only
    when this process already holds the combination.
    """
    lib = carbon(FakeCarbon(register_status=-9878))  # eventHotKeyExistsErr
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)

    with caplog.at_level(logging.WARNING, logger="sottovoce.hotkey"):
        assert hk.register() is False
    assert hk.registered is False
    assert lib.removed == [FakeCarbon.HANDLER_REF]  # handler not left behind
    assert "could not claim ⇧⌘J" in caplog.text
    assert "menu bar item" in caplog.text
    assert "another app" not in caplog.text  # a cause we cannot know


def test_a_handler_that_will_not_install_never_registers_a_hotkey(carbon, caplog):
    lib = carbon(FakeCarbon(install_status=-50))
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)

    with caplog.at_level(logging.WARNING, logger="sottovoce.hotkey"):
        assert hk.register() is False
    assert lib.registered == []
    assert "menu bar item" in caplog.text


def test_no_carbon_at_all_is_survivable(carbon):
    """The branch the Linux runner takes for real, and every machine
    without the framework: answer honestly and change nothing."""
    carbon(None)
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)
    assert hk.register() is False
    assert hk.registered is False
    hk.unregister()  # still safe


def test_a_native_call_that_blows_up_is_not_a_crash(carbon, caplog):
    class Exploding(FakeCarbon):
        def RegisterEventHotKey(self, *args):
            raise OSError("boom")

    carbon(Exploding())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, lambda: None)
    with caplog.at_level(logging.ERROR, logger="sottovoce.hotkey"):
        assert hk.register() is False
    assert hk.registered is False


def test_the_handler_never_raises_into_c(carbon):
    """An exception escaping a ctypes callback returns into a caller with
    no way to catch it. A bad toggle must not take the app with it."""

    def explode():
        raise ValueError("nope")

    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(hotkey.TOGGLE_LYRICS, explode)
    hk.register()

    assert lib.press() == 0  # noErr, and no exception on the way out


def test_the_hotkey_keeps_the_combination_it_was_built_with(carbon):
    other = Combination(key_code=0x00, modifiers=hotkey.CONTROL, key_label="A")
    lib = carbon(FakeCarbon())
    hk = GlobalHotkey(other, lambda: None)
    hk.register()

    assert hk.combination is other
    assert lib.registered == [(0x00, hotkey.CONTROL, 0x4C595253)]
