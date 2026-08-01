"""The shape of the suite: its three tiers, and that every test in it
runs.

`pytest -m unit`, `-m integration` and `-m qt` are only worth running if
they add up to the suite, which needs two things that nothing else
asserts: every module says which tier it is in, and no test claims two.
The resolution itself lives in tests/conftest.py and fails collection for
a test in no tier — but that fires only for a file somebody has already
written, and this fires while it is being written.

The marker names have one definition, like everything else with two
places to be spelled: conftest names them and pyproject registers them,
and a tier registered under a name nothing uses is a `-m` that silently
selects nothing.

And one that has nothing to do with tiers and everything to do with the
same idea: a test defined twice in one module is a test that never ran.
"""

from __future__ import annotations

TIER = "unit"  # Qt-free logic, called directly

import ast
import tomllib
from pathlib import Path

from conftest import TIERS

TESTS = Path(__file__).resolve().parent
REPO_ROOT = TESTS.parent


def modules():
    return sorted(TESTS.rglob("test_*.py"))


def declared_tier(tree: ast.Module) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TIER"
            for target in node.targets
        ):
            return node.value.value if isinstance(node.value, ast.Constant) else None
    return None


def tier_markers(node: ast.FunctionDef) -> list[str]:
    names = []
    for decorator in node.decorator_list:
        # @pytest.mark.<tier>, and nothing else has that shape
        if (
            isinstance(decorator, ast.Attribute)
            and isinstance(decorator.value, ast.Attribute)
            and decorator.value.attr == "mark"
            and decorator.attr in TIERS
        ):
            names.append(decorator.attr)
    return names


def test_every_test_module_says_which_tier_it_is_in():
    """The line a new file is easiest to forget. Collection would refuse
    the file anyway; this names it, and names the three it may choose
    from, before anybody has to read a traceback."""
    missing = {}
    for path in modules():
        tier = declared_tier(ast.parse(path.read_text()))
        if tier not in TIERS:
            missing[path.relative_to(REPO_ROOT)] = tier
    assert not missing, (
        f"these modules declare no usable TIER (choose one of {TIERS}): {missing}"
    )


def test_no_test_claims_two_tiers():
    """A test in two tiers is a test counted twice, and the arithmetic
    that says the three tiers ARE the suite stops holding."""
    doubled = {}
    for path in modules():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                claimed = tier_markers(node)
                if len(claimed) > 1:
                    doubled[f"{path.name}::{node.name}"] = claimed
    assert not doubled, f"these tests claim more than one tier: {doubled}"


def test_no_test_name_is_defined_twice_in_a_module():
    """The second definition wins and the first has never run.

    Found rather than imagined: two tests were both called
    `test_the_setting_survives_a_restart` in the 7447-line window file,
    one about the notification yield and one about fitting the strip to
    the song, and for as long as they shared a module only the second
    existed. Nothing could have gone red for it — the count was simply
    one lower than anybody had counted. Splitting the file resurrected
    it; this is what catches the next one, in whichever file it lands.
    """
    shadowed = {}
    for path in modules():
        seen, twice = set(), set()
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                (twice if node.name in seen else seen).add(node.name)
        if twice:
            shadowed[path.name] = sorted(twice)
    assert not shadowed, (
        "these tests are defined twice in one module, so only the second "
        f"of each ever runs: {shadowed}"
    )


def test_the_registered_markers_are_exactly_the_tiers():
    """One definition. A marker registered under a name conftest does not
    resolve is a `-m` that quietly selects nothing, and a tier conftest
    resolves but pyproject has not registered is a warning per test."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    registered = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    names = tuple(entry.split(":", 1)[0].strip() for entry in registered)
    assert names == TIERS


def test_every_registered_marker_says_what_it_means():
    """The list is what `pytest --markers` prints, and a bare name there
    is the reader being told to go and read the source."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    for entry in pyproject["tool"]["pytest"]["ini_options"]["markers"]:
        name, _, description = entry.partition(":")
        assert description.strip(), f"{name} is registered with no description"
