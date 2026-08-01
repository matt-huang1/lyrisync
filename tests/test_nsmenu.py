"""The one native menu, tested where it can be: the model it draws, and the
structure of the door it draws through.

Nothing here calls AppKit. It cannot: the conftest guard shuts
``nsmenu._appkit`` for the whole session, because unblocked a suite run
would put a glyph in the developer's menu bar for every window it built and
could block the run outright on a modal menu tracking loop. What is left is
what matters and is testable everywhere:

- the MODEL — every entry, its kind, its gating, what a click does — which
  is pure and lives in menu.py;
- the DOOR — that AppKit is imported in exactly one place and that place is
  ``_appkit``, so the guard actually guards something;
- the fallback — that with no door at all, every part of this answers and
  nothing raises, which is the branch a Linux runner takes.

What cannot be asserted here is what the menu LOOKS like, and that is
verified by screenshot on a real machine and written down in the decision
log.
"""

from __future__ import annotations

TIER = "unit"  # Qt-free logic, called directly

import ast
from pathlib import Path

import pytest

from sottovoce import menu as m
from sottovoce import nsmenu


# -- the door --------------------------------------------------------------


def test_the_native_imports_live_inside_the_door():
    """The property the conftest guard depends on: block ``_appkit`` and
    nothing in this module can reach AppKit. Asserted on the source,
    because a second import added later would pass every behavioural test
    while quietly reopening the door — and this door leads to the
    developer's own menu bar."""
    tree = ast.parse(Path(nsmenu.__file__).read_text(encoding="utf-8"))
    importers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (getattr(node, "module", None) in ("AppKit", "Foundation")
             or any(alias.name == "objc" for alias in node.names))
    ]
    assert importers, "the door does not import anything native"
    door = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_appkit"
    )
    inside = list(ast.walk(door))
    for node in importers:
        assert node in inside, "a native import outside _appkit"


def test_the_guard_shuts_this_door(escapes):
    """An unrun guard is not a guard. Walked into through the app's own
    entry points, both of them, because they are two capabilities behind
    one door and either alone would leave the other open."""
    with pytest.raises(RuntimeError, match="test escape"):
        nsmenu.NativeMenu(m.Menu()).build()
    assert any("menu bar" in e for e in escapes.drain())

    with pytest.raises(RuntimeError, match="test escape"):
        nsmenu.StatusItem().create("SottoVoce")
    assert escapes.drain()


def test_the_status_item_is_created_through_the_same_door():
    """Both classes ask ``_appkit`` and nothing else does. The menu bar item
    and its menu are one capability for the suite's purposes: a test that
    may not draw a menu may certainly not leave an icon behind."""
    tree = ast.parse(Path(nsmenu.__file__).read_text(encoding="utf-8"))
    callers = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_appkit"
    }
    assert callers == {"_appkit"}


# -- no door at all --------------------------------------------------------


@pytest.fixture
def shut(monkeypatch):
    """The branch a machine with no AppKit takes: the door answers None
    rather than raising, which is what every caller is written for."""
    monkeypatch.setattr(nsmenu, "_appkit", lambda: None)


def test_a_menu_with_nothing_to_draw_it_still_answers(shut):
    menu = m.Menu()
    view = nsmenu.NativeMenu(menu)
    assert view.build() is False
    assert view.native is None
    assert view.popup(10.0, 20.0) is False
    view.apply(menu)  # must not raise
    view.set_rows(m.POSITION_LIST, (m.Row("Safari"),))


def test_a_menu_bar_item_with_no_menu_bar_still_answers(shut):
    item = nsmenu.StatusItem()
    assert item.create("SottoVoce") is False
    assert item.alive is False
    assert item.frame() is None
    item.set_image(b"", 22)  # must not raise
    item.set_menu(nsmenu.NativeMenu(m.Menu()))
    item.release()
    item.release()  # idempotent: shutdown is reached more than once


def test_a_model_with_no_view_reports_the_popup_as_unopened(shut):
    """The window asks the model, not the view, and off macOS the honest
    answer is that no menu went up."""
    assert m.Menu().popup(0.0, 0.0) is False
