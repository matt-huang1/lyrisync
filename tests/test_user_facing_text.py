"""No em dash may reach a person.

The app used to write one everywhere two clauses met: "lyrics unavailable
— will retry", "plain lyrics — not synced", "AppKit unavailable — no
per-app position memory", both terminal banners. It reads as a typographic
tic rather than as punctuation, it is the one dash a terminal cannot be
relied on to render, and in a HUD 460 points wide it is a wide character
spent on nothing.

So there are three replacements and each context gets exactly one:

- **a middle dot** where two things are simply named side by side — a
  song and its artist (view_model.HEADER_SEPARATOR, which already worked
  this way and is what the rest now follows), "plain lyrics · not synced",
  a tool and what it does. It is a separator and nothing else.
- **a colon** where the second half is what follows from the first:
  "pyobjc unavailable: no vibrancy material".
- **a comma** where the two halves are one sentence: "activation: %s is
  us, keeping %s as the frontmost app".

This file holds the rule rather than the taste. Docstrings are exempt,
because they are documentation and are allowed to read like prose — this
one has an em dash in it three lines up. So do the decision log, the
README and the changelog, which are history and are not rewritten to suit
a later preference.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "src" / "sottovoce"
EM_DASH = "—"

MODULES = sorted(PACKAGE_DIR.glob("*.py"))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Every string node that is a docstring, by identity.

    By position rather than by content: a docstring is the first statement
    of a module, class or function, and the same text appearing elsewhere
    is not one.
    """
    found = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def literals(module: Path) -> list[tuple[int, str]]:
    """Every string this module builds, docstrings excluded.

    Scanned as syntax rather than as text, for the reason
    test_notifications.py gives about ``kCGWindowName``: this module's own
    docstring says the words "em dash" and has to, and a substring scan
    over the file could only be satisfied by deleting the explanation.

    f-strings are covered without special handling — their literal parts
    are Constant nodes inside a JoinedStr, so a dash spelled into one is
    found the same way.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    exempt = _docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in exempt
    ]


@pytest.mark.parametrize("module", MODULES, ids=lambda p: p.name)
def test_no_string_the_app_builds_contains_an_em_dash(module):
    """The guard. Every string in the package that is not a docstring can
    reach somebody — on the window, in the menu, down a log the user was
    asked to read (``SOTTOVOCE_LOG=DEBUG``), or out of one of the two
    terminal tools — so the rule is stated over all of them rather than
    over a list of the ones anyone remembered to include.
    """
    offenders = [
        f"{module.name}:{line}: {text!r}"
        for line, text in literals(module)
        if EM_DASH in text
    ]
    assert not offenders, "em dash in a string the app builds:\n  " + "\n  ".join(
        offenders
    )


def test_the_guard_can_see_an_em_dash(tmp_path):
    """An unrun guard is not a guard. This is the same shape the code
    under test has: a docstring that contains one, and a string that must
    not."""
    source = tmp_path / "sample.py"
    source.write_text(
        '"""A docstring — allowed."""\n'
        'BANNER = "a — b"\n'
        'def f():\n'
        '    """Also — allowed."""\n'
        '    return f"x {1} — y"\n',
        encoding="utf-8",
    )
    found = [text for _, text in literals(source) if EM_DASH in text]
    assert found == ["a — b", " — y"]


def test_the_guard_ignores_docstrings_only_where_they_are_docstrings(tmp_path):
    """The same text, in the two places it can appear. One is
    documentation and one is a string the app hands somebody."""
    source = tmp_path / "sample.py"
    source.write_text(
        'def f():\n'
        '    """A — B"""\n'
        '    return "A — B"\n',
        encoding="utf-8",
    )
    assert [text for _, text in literals(source) if EM_DASH in text] == ["A — B"]


def test_every_module_is_actually_scanned():
    """A glob that matched nothing would make every case above vacuous."""
    assert len(MODULES) > 25
    names = {module.name for module in MODULES}
    for expected in ("window.py", "view_model.py", "lyrics_cli.py", "monitor_cli.py"):
        assert expected in names


# -- the replacements themselves ------------------------------------------


def test_the_pairings_use_the_middle_dot():
    """The window's two standing pairings, and the separator they follow.
    Pinned because "which of the three" is the part a later edit would get
    wrong, and the suite would otherwise only notice the dash coming back.
    """
    from sottovoce.lyrics_provider import TrackLyrics
    from sottovoce.player_monitor import PlaybackState, PlayerSnapshot
    from sottovoce.view_model import HEADER_SEPARATOR, LyricsViewModel, Mode

    assert HEADER_SEPARATOR == " · "

    snapshot = PlayerSnapshot(
        state=PlaybackState.PLAYING, track_id="t1", title="Song", artist="Artist"
    )
    model = LyricsViewModel()
    model.track_changed(snapshot)
    model.fetch_completed("t1", TrackLyrics(plain="a line"))
    assert model.display().previous == "plain lyrics · not synced"
    assert model.display().header == "Song · Artist"

    model.fetch_completed("t1", None, ok=False, now=0.0)
    assert model.display().mode is Mode.ERROR
    # The message itself is a sentence, so it takes the comma.
    assert model.display().current == "lyrics unavailable, will retry"
