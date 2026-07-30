"""The three macOS accessibility display settings, and what they cost.

Two halves, and they are separate on purpose. The first is the module
that reads the settings: one door onto NSWorkspace, one watcher, and a
NamedTuple that answers all False wherever it cannot ask. The second is
the palette those settings produce, which is arithmetic and is checked the
way tests/test_scrim.py checks the shipped one — computed, over the
backdrop that suits it least, rather than looked at.

Nothing here is macOS-only. The door is faked, and the colour maths needs
no Qt, no screen and no Mac.

What is NOT here, and is manual: whether the settings themselves flip the
app. Toggling Reduce Motion means writing com.apple.universalaccess, which
is TCC-protected and refused from a terminal without Full Disk Access
(measured: "Could not write domain com.apple.universalaccess; exiting").
So the app's response to each option is tested by handing it the option,
and the reading of the real switch is verified by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sottovoce import accessibility as a
from sottovoce import appearance as ap
from test_scrim import contrast, over


# -- what the settings say -------------------------------------------------


def test_nothing_switched_on_is_the_default():
    assert a.DisplayOptions() == a.NONE
    assert a.NONE.reduce_motion is False
    assert a.NONE.reduce_transparency is False
    assert a.NONE.increase_contrast is False
    assert a.NONE.solid_background is False


def test_increase_contrast_implies_no_transparency():
    """macOS turns Reduce Transparency on and locks it there while
    Increase Contrast is on, because a blurred backdrop and a contrast
    guarantee cannot both be honoured. Derived here rather than trusted to
    arrive, so the app is right even where the pair come apart."""
    assert a.DisplayOptions(reduce_transparency=True).solid_background is True
    assert a.DisplayOptions(increase_contrast=True).solid_background is True
    assert a.DisplayOptions(reduce_motion=True).solid_background is False


def test_describe_names_what_is_on():
    assert a.describe(a.NONE) == "none"
    assert a.describe(a.DisplayOptions(reduce_motion=True)) == "reduce motion"
    assert (
        a.describe(a.DisplayOptions(reduce_motion=True, increase_contrast=True))
        == "reduce motion, increase contrast"
    )


# -- the door --------------------------------------------------------------


class FakeWorkspace:
    """A workspace that answers, and remembers what it was asked."""

    def __init__(self, motion=False, transparency=False, contrast_=False):
        self._answers = (motion, transparency, contrast_)
        self.centre = FakeCentre()

    def accessibilityDisplayShouldReduceMotion(self):
        return self._answers[0]

    def accessibilityDisplayShouldReduceTransparency(self):
        return self._answers[1]

    def accessibilityDisplayShouldIncreaseContrast(self):
        return self._answers[2]

    def notificationCenter(self):
        return self.centre


class FakeCentre:
    def __init__(self):
        self.observed = []
        self.removed = []
        self.block = None

    def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
        self.observed.append(name)
        self.block = block
        return object()

    def removeObserver_(self, token):
        self.removed.append(token)


def test_no_workspace_means_nothing_switched_on(monkeypatch):
    """Off macOS and without pyobjc. The plain window, not a half-read
    one."""
    monkeypatch.setattr(a, "_workspace", lambda: None)
    assert a.current_options() == a.NONE
    assert a.DisplayOptionsWatcher(lambda _: None).start() is False


def test_the_options_are_read_from_the_workspace(monkeypatch):
    monkeypatch.setattr(
        a, "_workspace", lambda: FakeWorkspace(motion=True, contrast_=True)
    )
    assert a.current_options() == a.DisplayOptions(
        reduce_motion=True, reduce_transparency=False, increase_contrast=True
    )


def test_a_workspace_that_will_not_answer_gives_nothing_switched_on(monkeypatch):
    """A future macOS that renames or refuses these must degrade to the
    plain window, not to the app failing to start."""

    class Hostile:
        def accessibilityDisplayShouldReduceMotion(self):
            raise RuntimeError("no")

    monkeypatch.setattr(a, "_workspace", lambda: Hostile())
    assert a.current_options() == a.NONE


def test_the_watcher_subscribes_and_unsubscribes(monkeypatch):
    workspace = FakeWorkspace()
    monkeypatch.setattr(a, "_workspace", lambda: workspace)
    watcher = a.DisplayOptionsWatcher(lambda _: None)
    assert watcher.start() is True
    assert watcher.active
    assert workspace.centre.observed == [a.OPTIONS_CHANGED]
    # Starting twice must not leave two observers behind.
    assert watcher.start() is True
    assert workspace.centre.observed == [a.OPTIONS_CHANGED]
    watcher.stop()
    assert not watcher.active
    assert len(workspace.centre.removed) == 1
    watcher.stop()  # idempotent: shutdown is reached more than once
    assert len(workspace.centre.removed) == 1


def test_a_notification_re_reads_and_hands_on(monkeypatch):
    """The notification carries no payload — it says only that something
    moved — so the watcher asks again rather than unpacking it."""
    workspace = FakeWorkspace(transparency=True)
    monkeypatch.setattr(a, "_workspace", lambda: workspace)
    seen = []
    watcher = a.DisplayOptionsWatcher(seen.append)
    watcher.start()
    workspace.centre.block(object())
    assert seen == [a.DisplayOptions(reduce_transparency=True)]


def test_a_handler_that_raises_does_not_escape_into_appkit(monkeypatch):
    """This runs inside AppKit's own dispatch, where an exception surfaces
    somewhere unhelpful."""
    workspace = FakeWorkspace()
    monkeypatch.setattr(a, "_workspace", lambda: workspace)

    def explode(_options):
        raise RuntimeError("boom")

    watcher = a.DisplayOptionsWatcher(explode)
    watcher.start()
    workspace.centre.block(object())  # must not raise


def test_a_workspace_that_refuses_the_subscription_is_not_fatal(monkeypatch):
    class Hostile:
        def notificationCenter(self):
            raise RuntimeError("no")

    monkeypatch.setattr(a, "_workspace", lambda: Hostile())
    assert a.DisplayOptionsWatcher(lambda _: None).start() is False


def test_every_native_call_goes_through_one_door():
    """The property the conftest guard depends on: block ``_workspace``
    and nothing here can reach AppKit. Asserted on the source, because a
    second import added later would pass every behavioural test above
    while quietly reopening the door — the same claim frontmost.py makes
    about the same class, for the same reason."""
    tree = ast.parse(Path(a.__file__).read_text(encoding="utf-8"))
    importers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "NSWorkspace" for alias in node.names)
    ]
    assert len(importers) == 1, "NSWorkspace is imported in more than one place"
    door = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_workspace"
    )
    assert importers[0] in ast.walk(door)


def test_the_guard_shuts_this_door(escapes):
    """An unrun guard is not a guard."""
    with pytest.raises(RuntimeError):
        a._workspace()
    assert any("accessibility display settings" in e for e in escapes.drain())


# -- the palette those settings produce ------------------------------------


def flat(top, bottom_rgb):
    """A straight-alpha colour painted over an opaque one."""
    colour, _ = over(top[:3], top[3] / 255, bottom_rgb)
    return colour


def ratio(palette, role, on=None):
    """A role against what it is actually drawn on: the opaque panel, or
    a button fill that is itself over the opaque panel."""
    panel = palette.solid[:3]
    background = flat(getattr(palette, on), panel) if on else panel
    return contrast(flat(getattr(palette, role), background), background)


APPEARANCES = pytest.mark.parametrize(
    "appearance", (ap.Appearance.DARK, ap.Appearance.LIGHT), ids=("dark", "light")
)

# Everything a person reads to follow a song, held to the promise the sung
# line already carries. Increase Contrast is a request for exactly this:
# the roles that recede by design stop receding.
TEXT_ROLES = (
    "current",
    "plain",
    "pronunciation",
    "context",
    "header",
    "progress",
    "control_idle",
    "control_hover",
    "control_engaged",
    "sync_text",
    "sync_text_hover",
    "confirm_text",
)

# Held to 3:1 instead, and the difference is not a lower standard: none of
# these is text anybody reads to follow a song. Two are marks (the
# scrollbar, the hairline) and two are labels on controls that are
# switched off, which WCAG exempts outright and which this holds anyway.
DECORATION_ROLES = ("scrollbar", "border")
DISABLED_ROLES = {"sync_text_off": "sync_fill", "tap_text_off": "tap_fill_off"}


@APPEARANCES
def test_the_shipped_palette_does_not_clear_this_floor(appearance):
    """The finding, kept, because it is the reason any of this exists and
    not a defect in the default. Over the scrim with the material
    contributing nothing, the sung line clears 4.5:1 and NOTHING else
    does: the header sits at 2.48:1 in both modes and the idle control at
    about 2.1:1. That is the hierarchy working — and the wrong answer to
    somebody who has asked the system for more contrast.
    """
    palette = ap.palette_for(appearance)
    worst = min(
        contrast(
            flat(getattr(palette, role), flat(palette.scrim, (255, 255, 255))),
            flat(palette.scrim, (255, 255, 255)),
        )
        for role in ("header", "context", "control_idle")
    )
    assert worst < 4.5


@APPEARANCES
@pytest.mark.parametrize("role", TEXT_ROLES)
def test_increase_contrast_puts_every_text_role_over_the_floor(appearance, role):
    palette = ap.palette_for(appearance, high_contrast=True, opaque_background=True)
    got = ratio(palette, role)
    assert got >= 4.5, f"{role} bottoms out at {got:.2f}:1"


@APPEARANCES
@pytest.mark.parametrize("role", DECORATION_ROLES)
def test_increase_contrast_makes_the_marks_visible(appearance, role):
    palette = ap.palette_for(appearance, high_contrast=True, opaque_background=True)
    got = ratio(palette, role)
    assert got >= 3.0, f"{role} bottoms out at {got:.2f}:1"


@APPEARANCES
@pytest.mark.parametrize("role,fill", sorted(DISABLED_ROLES.items()))
def test_increase_contrast_reaches_the_switched_off_labels(appearance, role, fill):
    """"PAUSED" on the tap bar is a disabled control and is still the one
    word explaining why tapping does nothing."""
    palette = ap.palette_for(appearance, high_contrast=True, opaque_background=True)
    got = ratio(palette, role, on=fill)
    assert got >= 3.0, f"{role} on {fill} bottoms out at {got:.2f}:1"


@APPEARANCES
def test_increase_contrast_keeps_the_button_labels_readable(appearance):
    """The roles drawn on their own fill rather than on the panel. The
    binding one is amber-on-amber in light mode: 4.25:1 at the alpha that
    ships, which is why the high-contrast palette lifts it to 255."""
    palette = ap.palette_for(appearance, high_contrast=True, opaque_background=True)
    for role, fill in (
        ("tap_text", "tap_fill"),
        ("tap_text", "tap_fill_hover"),
        ("tap_text", "tap_fill_pressed"),
        ("attempt_text", "attempt_fill"),
        ("attempt_text", "attempt_fill_hover"),
        ("exit_text_hover", "exit_fill_hover"),
    ):
        got = ratio(palette, role, on=fill)
        assert got >= 4.5, f"{role} on {fill} is {got:.2f}:1"


@APPEARANCES
def test_the_sung_line_is_still_the_strongest_thing_on_the_panel(appearance):
    """Lifting the roles that recede must not flatten the hierarchy: more
    contrast everywhere is still a window where the line being sung
    out-reads the lines around it."""
    palette = ap.palette_for(appearance, high_contrast=True, opaque_background=True)
    sung = ratio(palette, "current")
    for role in ("context", "header", "pronunciation", "plain"):
        assert sung > ratio(palette, role), role


@APPEARANCES
def test_increase_contrast_moves_colours_and_nothing_structural(appearance):
    """Every override is a role's own colour. A high-contrast palette that
    changed the scrim would be re-deciding the contrast floor by accident,
    and one that changed a fill would be redesigning the buttons."""
    plain = ap.palette_for(appearance)
    lifted = ap.palette_for(appearance, high_contrast=True)
    moved = {
        name for name, value in vars(lifted).items() if value != getattr(plain, name)
    }
    assert moved == set(ap.HIGH_CONTRAST_OVERRIDES[appearance])
    assert "scrim" not in moved and "solid" not in moved
    for name in moved:
        # Same colour, more of it — except the one role that was already
        # as dark as its pairing allows and only had opacity left to give.
        assert len(getattr(lifted, name)) == 4


@APPEARANCES
def test_reduce_transparency_leaves_nothing_showing_through(appearance):
    palette = ap.palette_for(appearance, opaque_background=True)
    assert palette.solid[3] == 255
    assert palette.solid[:3] == ap.palette_for(appearance).solid[:3]
    # And the fallback for a vibrancy that would not install is untouched:
    # that is a different case and its alphas are measured for it.
    assert ap.palette_for(appearance).solid[3] in (232, 236)


@APPEARANCES
def test_an_opaque_background_only_ever_helps_the_floor(appearance):
    """Worth stating rather than assuming: the shipped solid alpha lets a
    little of the page through, so going fully opaque cannot lower any
    ratio. It is what lets the high-contrast numbers be measured against
    the panel alone."""
    plain = ap.palette_for(appearance)
    solid = ap.palette_for(appearance, opaque_background=True)
    for role in TEXT_ROLES:
        assert ratio(solid, role) >= ratio(plain, role)


@APPEARANCES
def test_nothing_switched_on_is_the_palette_that_ships(appearance):
    """By identity, not by value: "no accessibility setting on" and "this
    app before those settings were followed" have to be the same pixels."""
    assert ap.palette_for(appearance) is ap.palette_for(appearance)
    assert ap.palette_for(appearance) is {
        ap.Appearance.DARK: ap.DARK,
        ap.Appearance.LIGHT: ap.LIGHT,
    }[appearance]


@APPEARANCES
def test_the_album_tint_still_clears_the_floor_on_an_opaque_panel(appearance):
    """The layers principle, checked where the two features meet: album
    colour on and Increase Contrast on is a combination somebody will
    have, and every hue has to hold there too."""
    palette = ap.palette_for(appearance, high_contrast=True, opaque_background=True)
    for hue in range(0, 360, 5):
        tinted = ap.tinted(palette, ap.hsl_to_rgb(hue, 1.0, 0.5), appearance)
        got = contrast(
            flat(tinted.current, tinted.solid[:3]), tinted.solid[:3]
        )
        assert got >= 4.5, f"hue {hue} -> {got:.2f}:1"
        assert tinted.solid[3] == 255, "the tint gave the transparency back"
