"""The bundle may not invent its own version number.

`make app` is the only way LyriSync reaches a user as an app, and the
version in its Info.plist is what they will quote in a bug report. A
second copy of that number written into the spec is the kind of thing
that stays right for one release.

So the spec is read here as source — it cannot be imported, because a
PyInstaller spec runs with Analysis/EXE/BUNDLE and SPECPATH injected into
its globals — and what is asserted is where the number comes FROM, not
what it currently is. A test pinning the value would be the second copy
all over again.
"""

from __future__ import annotations

import ast
import re
import tomllib
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "packaging" / "LyriSync.spec"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

# The two Info.plist keys that state a version. Finder shows the first,
# macOS compares the second; both have to be the package's own.
VERSION_KEYS = ("CFBundleShortVersionString", "CFBundleVersion")

# A version-shaped string, for the check that none is hard-written.
_VERSION_LITERAL = re.compile(r"^\d+\.\d+(\.\d+)*$")

# LSMinimumSystemVersion is version-shaped and is deliberately a literal:
# it is the macOS floor (SF Symbols arrived in 11.0), not this app's
# version, and it moves for entirely different reasons.
_LITERAL_ALLOWED_UNDER = {"LSMinimumSystemVersion"}


@pytest.fixture(scope="module")
def spec_tree() -> ast.Module:
    return ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))


@pytest.fixture(scope="module")
def declared_version() -> str:
    with open(PYPROJECT_PATH, "rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def metadata_alias(tree: ast.Module) -> str:
    """Whatever name the spec imported ``importlib.metadata.version`` as."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib.metadata":
            for alias in node.names:
                if alias.name == "version":
                    return alias.asname or alias.name
    raise AssertionError(
        "the spec does not import version() from importlib.metadata — the "
        "bundle's version has to come from the installed package"
    )


def version_binding(tree: ast.Module) -> str:
    """The name the spec binds the package version to."""
    alias = metadata_alias(tree)
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.Try):
            # The call is allowed to sit inside a try/except that turns a
            # missing install into a readable build error.
            for inner in node.body:
                if isinstance(inner, ast.Assign):
                    targets, value = inner.targets, inner.value
                    if _is_metadata_call(value, alias):
                        return targets[0].id
            continue
        else:
            continue
        if _is_metadata_call(value, alias):
            return targets[0].id
    raise AssertionError(f"nothing in the spec is assigned from {alias}()")


def _is_metadata_call(value: ast.AST, alias: str) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == alias
    )


def bundle_call(tree: ast.Module) -> ast.Call:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BUNDLE"
        ):
            return node
    raise AssertionError("no BUNDLE(...) call in the spec")


def info_plist(tree: ast.Module) -> ast.Dict:
    for keyword in bundle_call(tree).keywords:
        if keyword.arg == "info_plist":
            assert isinstance(keyword.value, ast.Dict)
            return keyword.value
    raise AssertionError("BUNDLE(...) has no info_plist")


# -- where the number comes from ------------------------------------------


def test_the_spec_asks_the_installed_package_for_its_version(spec_tree):
    """importlib.metadata, not a re-read of pyproject.toml and certainly
    not a literal: the installed distribution is what PyInstaller freezes,
    so it is the only thing that knows what is going into the bundle."""
    alias = metadata_alias(spec_tree)
    name = version_binding(spec_tree)
    assert name, "the spec binds no name to the package version"

    call = next(
        node
        for node in ast.walk(spec_tree)
        if _is_metadata_call(node, alias)
    )
    assert [ast.literal_eval(arg) for arg in call.args] == ["lyrisync"], (
        "the spec asks for some other distribution's version"
    )


def test_both_plist_version_keys_come_from_that_one_name(spec_tree):
    """Finder shows CFBundleShortVersionString, macOS compares
    CFBundleVersion. Two keys, one source — or the bundle can disagree
    with itself."""
    name = version_binding(spec_tree)
    plist = info_plist(spec_tree)
    found = {}
    for key, value in zip(plist.keys, plist.values):
        if isinstance(key, ast.Constant) and key.value in VERSION_KEYS:
            found[key.value] = value

    assert set(found) == set(VERSION_KEYS), f"missing plist keys: {found.keys()}"
    for key, value in found.items():
        assert isinstance(value, ast.Name) and value.id == name, (
            f"{key} is not taken from {name} — it must not be written out again"
        )


def test_the_bundle_version_argument_comes_from_it_too(spec_tree):
    name = version_binding(spec_tree)
    for keyword in bundle_call(spec_tree).keywords:
        if keyword.arg == "version":
            assert isinstance(keyword.value, ast.Name)
            assert keyword.value.id == name
            return
    raise AssertionError("BUNDLE(...) does not pass a version")


def test_no_version_number_is_written_into_the_spec(spec_tree, declared_version):
    """The regression this whole file exists for: someone pastes the
    number in "just for now". The macOS floor is exempt — it is
    version-shaped but it is not this app's version."""
    exempt = set()
    plist = info_plist(spec_tree)
    for key, value in zip(plist.keys, plist.values):
        if isinstance(key, ast.Constant) and key.value in _LITERAL_ALLOWED_UNDER:
            exempt.add(id(value))

    offenders = [
        node.value
        for node in ast.walk(spec_tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in exempt
        and _VERSION_LITERAL.match(node.value)
    ]
    assert not offenders, f"version literals in the spec: {offenders}"
    assert declared_version not in SPEC_PATH.read_text(encoding="utf-8")


# -- and that it cannot go stale ------------------------------------------


def test_the_installed_version_matches_the_one_pyproject_declares(declared_version):
    """An editable install's metadata is a snapshot taken at install time,
    so editing pyproject.toml without reinstalling leaves the two saying
    different things — and the bundle would quietly claim the older one.

    The spec refuses to build on this; here it is a red test rather than a
    surprise at release time.
    """
    assert installed_version("lyrisync") == declared_version, (
        "installed lyrisync is "
        f"{installed_version('lyrisync')} but pyproject.toml declares "
        f"{declared_version} — reinstall with: pip install -e '.[dev]'"
    )


def test_the_spec_refuses_to_build_on_a_mismatch(spec_tree):
    """Structural, because the spec only runs under PyInstaller: there is a
    comparison of the two versions, and it raises rather than warning."""
    source = SPEC_PATH.read_text(encoding="utf-8")
    name = version_binding(spec_tree)
    assert re.search(rf"if {name} != \w+:", source), (
        "the spec does not compare the installed version against the "
        "declared one"
    )
    raising = [
        node
        for node in ast.walk(spec_tree)
        if isinstance(node, ast.Raise)
    ]
    assert raising, "a mismatch has to stop the build, not warn about it"
