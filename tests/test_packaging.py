"""The bundle may not invent its own version number.

`make app` is the only way SottoVoce reaches a user as an app, and the
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

TIER = "unit"  # Qt-free logic, called directly

import ast
import re
import tomllib
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "packaging" / "SottoVoce.spec"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PACKAGE_DIR = REPO_ROOT / "src" / "sottovoce"

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
    assert [ast.literal_eval(arg) for arg in call.args] == ["sottovoce"], (
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


def test_the_bundle_identifier_is_the_preferences_file(spec_tree):
    """The identifier IS the settings contract. QSettings("sottovoce",
    "sottovoce") resolves to ~/Library/Preferences/com.sottovoce.sottovoce
    .plist, and the bundle declaring that same string is the whole reason
    a checkout and the built app read one file instead of two.

    Pinned rather than assumed because the rename proved what happens when
    it moves: the old identifier's plist was orphaned, which is what
    settings.py exists to carry across. A typo here would orphan the file
    again, silently, and look like a first launch.
    """
    from sottovoce.settings import APPLICATION, ORGANISATION

    for keyword in bundle_call(spec_tree).keywords:
        if keyword.arg == "bundle_identifier":
            assert isinstance(keyword.value, ast.Constant)
            assert keyword.value.value == f"com.{ORGANISATION}.{APPLICATION}"
            return
    raise AssertionError("BUNDLE(...) does not declare a bundle_identifier")


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
        and node.module == "sottovoce"
        and any(alias.name == "USER_AGENT" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported, f"{module} does not import USER_AGENT from the package"


def test_every_user_agent_is_the_same_string_and_carries_the_version():
    """The behavioural half, at runtime. One identity, and it states what
    the app actually is."""
    from sottovoce import USER_AGENT, __version__
    from sottovoce.artwork import USER_AGENT as artwork_agent
    from sottovoce.lyrics_provider import USER_AGENT as lyrics_agent

    assert artwork_agent == lyrics_agent == USER_AGENT
    assert __version__ == installed_version("sottovoce")
    assert f"sottovoce/{__version__}" in USER_AGENT


def test_the_contact_url_names_the_repository_the_readme_clones():
    """A User-Agent URL exists so somebody at LRCLIB can find whoever is
    making the requests. This one pointed at github.com/matthewhuang for
    thirteen milestones — a 404 — because nothing compared it to anything.

    The README's clone line is the check: it is the address a real person
    is told to use, so it is the one that gets noticed when it breaks.
    Comparing against `git remote` instead would pass on a developer's
    fork and is unavailable in some CI checkouts.
    """
    from sottovoce import REPOSITORY_URL, USER_AGENT

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    clone = re.search(r"git clone git@github\.com:([\w.-]+)/([\w.-]+)\.git", readme)
    assert clone, "the README no longer shows a clone URL to check against"
    owner, repo = clone.group(1), clone.group(2)

    assert REPOSITORY_URL == f"https://github.com/{owner}/{repo}", (
        f"the contact URL is {REPOSITORY_URL} but the README clones "
        f"{owner}/{repo}"
    )
    assert f"({REPOSITORY_URL})" in USER_AGENT


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
    assert [ast.literal_eval(arg) for arg in call.args] == ["sottovoce"]


# -- and that it cannot go stale ------------------------------------------


def test_the_installed_version_matches_the_one_pyproject_declares(declared_version):
    """An editable install's metadata is a snapshot taken at install time,
    so editing pyproject.toml without reinstalling leaves the two saying
    different things — and the bundle would quietly claim the older one.

    The spec refuses to build on this; here it is a red test rather than a
    surprise at release time.
    """
    assert installed_version("sottovoce") == declared_version, (
        "installed sottovoce is "
        f"{installed_version('sottovoce')} but pyproject.toml declares "
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


def test_the_readme_installs_the_extras_that_exist():
    """The install line is the one a real person follows, so it is the one
    that gets noticed when it breaks — the same argument the clone URL
    above rests on.

    It broke: the build instructions installed ``".[build]"`` and then told
    the reader to run the suite, which fails with "No module named pytest"
    because pytest is the `dev` extra. A missing extra, not a broken
    checkout, and nothing compared the two.
    """
    import re
    import tomllib

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    project = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    declared = set(project["project"]["optional-dependencies"])

    asked_for = set()
    for match in re.finditer(r'pip install -e "\.\[([\w,\s-]+)\]"', readme):
        asked_for.update(name.strip() for name in match.group(1).split(","))
    assert asked_for, "the README no longer shows an extras install to check"
    assert asked_for <= declared, (
        f"the README installs {sorted(asked_for - declared)}, which "
        f"pyproject.toml does not define"
    )
    # And the suite's own dependency is one of them, or the instructions
    # tell somebody to run `make test` against a checkout that cannot.
    assert "dev" in asked_for
    assert any(
        "pytest" in requirement
        for requirement in project["project"]["optional-dependencies"]["dev"]
    )
