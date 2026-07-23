from lyrisync import macspaces as ms


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
