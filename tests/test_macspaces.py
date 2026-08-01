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


def test_nsapplication_is_reached_through_exactly_one_door():
    """Two things in this app ask NSApplication something — the accessory
    policy at startup, and bringing the app forward so the paste window can
    take the keyboard — and they are one capability.

    Structural, like every other door here: a second import site would pass
    every behavioural test while giving the suite a way to activate the
    developer's app, which is precisely what ``_nsapp`` answering None off
    cocoa is there to prevent.
    """
    import ast
    from pathlib import Path

    from sottovoce import window as w

    tree = ast.parse(Path(w.__file__).read_text(encoding="utf-8"))
    importers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any(alias.name == "NSApplication" for alias in node.names)
    ]
    assert len(importers) == 1, "NSApplication is imported in more than one place"
    door = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_nsapp"
    )
    assert importers[0] in list(ast.walk(door)), "the import is outside _nsapp"
