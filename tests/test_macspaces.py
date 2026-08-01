TIER = "unit"  # Qt-free logic, called directly

from sottovoce import macspaces as ms


def test_enabling_clears_primary_and_sets_both_flags():
    # Qt's default behavior on this window, from runtime readback.
    qt_default = ms.FULL_SCREEN_PRIMARY  # 0x80
    behavior = ms.all_desktops_behavior(qt_default)
    assert behavior & ms.CAN_JOIN_ALL_SPACES
    assert behavior & ms.FULL_SCREEN_AUXILIARY
    assert not behavior & ms.FULL_SCREEN_PRIMARY  # the mutually-exclusive bit


def test_enabling_is_idempotent():
    once = ms.all_desktops_behavior(ms.FULL_SCREEN_PRIMARY)
    assert ms.all_desktops_behavior(once) == once


def test_unrelated_bits_are_preserved():
    stationary = 1 << 4
    ignores_cycle = 1 << 6
    current = ms.FULL_SCREEN_PRIMARY | stationary | ignores_cycle
    behavior = ms.all_desktops_behavior(current)
    assert behavior & stationary
    assert behavior & ignores_cycle


def test_activation_policy_is_accessory_only():
    """The app is a menu bar accessory permanently: there is no regular
    policy to fall back to, so the toggle can never drag the Dock icon
    (and the full-screen Space switch) back."""
    assert ms.ACTIVATION_POLICY_ACCESSORY == 1
    assert not hasattr(ms, "activation_policy_for")
    assert not hasattr(ms, "ACTIVATION_POLICY_REGULAR")
