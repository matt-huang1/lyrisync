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
PACKAGE_DIR = REPO_ROOT / "src" / "lyrisync"

# The modules that introduce this app to somebody else's server. Both used
# to carry "lyrisync/0.1.0 (…)" written out by hand, which stayed correct
# for exactly as long as the version did.
OUTBOUND_MODULES = ("artwork.py", "lyrics_provider.py")

# The two Info.plist keys that state a version. Finder shows the first,
# macOS compares the second; both have to be the package's own.
VERSION_KEYS = ("CFBundleShortVersionString", "CFBundleVersion")

# A version-shaped string, for the check that none is hard-written.
_VERSION_LITERAL = re.compile(r"^\d+\.\d+(\.\d+)*$")

# The same shape, anywhere INSIDE a string. The User-Agent hid its copy of
# the version in the middle of one ("lyrisync/0.1.0 (…)"), so a
# whole-string match would have walked straight past it.
_VERSION_ANYWHERE = re.compile(r"\d+\.\d+(\.\d+)*")

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


def docstring_ids(tree: ast.Module) -> set[int]:
    """Every string that is prose rather than a value.

    Needed because this file's own examples are version-shaped: the LRC
    parser's docstring says ``[00:12.00][00:55.30] chorus``, and a scan
    that counted that as a version would be a test nobody could satisfy
    without deleting the documentation.
    """
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            ids.add(id(node.body[0].value))
    return ids


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


# -- and neither does anything else the app says about itself -------------


@pytest.mark.parametrize("module", OUTBOUND_MODULES)
def test_no_version_number_is_written_into_an_outbound_module(module):
    """The User-Agent LRCLIB and the artwork host see must not be a hand
    written copy of the version. Both of these carried one, and it went
    stale the first time the version moved — the app introduced itself as
    0.1.0 while being 1.0.0.

    Substring, not whole-string: the number was buried mid-sentence in
    "lyrisync/0.1.0 (…)", which is exactly where a check for a literal
    version would fail to look.
    """
    tree = ast.parse((PACKAGE_DIR / module).read_text(encoding="utf-8"))
    prose = docstring_ids(tree)
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and _VERSION_ANYWHERE.search(node.value)
    ]
    assert not offenders, f"{module} states a version in: {offenders}"


@pytest.mark.parametrize("module", OUTBOUND_MODULES)
def test_the_outbound_modules_take_the_user_agent_from_the_package(module):
    """Structural half of the same claim: no version literal is easy to
    satisfy by deleting the version. The string still has to come from the
    one place that resolves it."""
    tree = ast.parse((PACKAGE_DIR / module).read_text(encoding="utf-8"))
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "lyrisync"
        and any(alias.name == "USER_AGENT" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported, f"{module} does not import USER_AGENT from the package"


def test_every_user_agent_is_the_same_string_and_carries_the_version():
    """The behavioural half, at runtime. One identity, and it states what
    the app actually is."""
    from lyrisync import USER_AGENT, __version__
    from lyrisync.artwork import USER_AGENT as artwork_agent
    from lyrisync.lyrics_provider import USER_AGENT as lyrics_agent

    assert artwork_agent == lyrics_agent == USER_AGENT
    assert __version__ == installed_version("lyrisync")
    assert f"lyrisync/{__version__}" in USER_AGENT


def test_the_bundle_carries_the_metadata_that_answer_depends_on(spec_tree):
    """__init__.py asks importlib.metadata for the version, and PyInstaller
    freezes the package WITHOUT its .dist-info unless the spec says
    otherwise — verified by finding none in a built bundle. Without this
    the frozen app falls back to "unknown" and misstates itself to every
    server it talks to, which is a failure nothing else here would catch.
    """
    source = SPEC_PATH.read_text(encoding="utf-8")
    assert "copy_metadata" in source, (
        "the spec does not copy the distribution metadata into the bundle"
    )
    call = next(
        (
            node
            for node in ast.walk(spec_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "copy_metadata"
        ),
        None,
    )
    assert call is not None, "copy_metadata is mentioned but never called"
    assert [ast.literal_eval(arg) for arg in call.args] == ["lyrisync"]


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
