"""Getting out of a notification's way: the overlap and the opacity.

Pure arithmetic and one structural pass over the module. What cannot be
checked here — whether a banner is actually readable through the window at
the ceiling this picks — is a question about pixels and is measured by hand
against a real notification; see docs/.

Nothing in this file calls into Quartz. It cannot: conftest.py shuts
``notifications._quartz`` for the whole session, because reading the real
window list would mean reading every window open on the machine running the
suite, and a result that depends on what the developer happens to have open
is not a result.
"""

import ast
from pathlib import Path

import pytest

from sottovoce import notifications as n

# The display this was all measured on, and the rectangle macOS actually
# reports for a notification: the whole of it. Both a top-right banner and
# the panel come back as this, in every field — which is why the region
# below is a heuristic and not a reading.
DISPLAY = (0, 0, 1710, 1107)
SECOND_DISPLAY = (1710, -200, 1920, 1080)

# Where the user might have put the window. AWAY is the case milestone 16
# got wrong: a window nowhere near where notifications appear.
AWAY = (600, 400, 460, 200)
IN_THE_STRIP = (1300, 400, 400, 200)

# The real rectangles, measured from pixels. Kept here so the region test
# is against what notifications actually do rather than against the
# constant restating itself.
SHORT_BANNER = (1349, 54, 346, 62)
LONG_BANNER = (1343, 44, 360, 120)
STACKED_BANNERS = (1340, 38, 368, 96)
PANEL = (1294, 34, 416, 608)


# -- the region: the one heuristic ----------------------------------------


def test_the_region_is_the_rightmost_strip_of_what_was_reported():
    x, y, width, height = n.plausible_region(DISPLAY)
    assert width == n.PLAUSIBLE_STRIP_WIDTH
    assert x == 1710 - n.PLAUSIBLE_STRIP_WIDTH
    assert (y, height) == (0, 1107), "full height: the panel's is content-dependent"


def test_the_region_is_anchored_to_the_reported_right_edge():
    """Not to a screen this module went and asked about. Narrowing the
    reported rectangle is what keeps 16's real property: the rectangle is
    the display the notification is on."""
    for display in (DISPLAY, SECOND_DISPLAY):
        x, _, width, _ = n.plausible_region(display)
        assert x + width == display[0] + display[2]


def test_every_measured_notification_falls_inside_the_region():
    """The check that the constant is big enough for the thing it
    approximates. If a future macOS moves banners left, this fails rather
    than the layer quietly stopping."""
    region = n.plausible_region(DISPLAY)
    for name, rect in (
        ("short banner", SHORT_BANNER),
        ("long banner", LONG_BANNER),
        ("three stacked", STACKED_BANNERS),
        ("the panel", PANEL),
    ):
        assert rect[0] >= region[0], f"{name} starts left of the region"
        assert rect[0] + rect[2] <= region[0] + region[2], f"{name} runs past it"


def test_the_strip_has_margin_over_the_widest_thing_measured():
    """440 against a measured 416, and the margin is the point: banner and
    panel widths move with the system text size and with localisation, and
    the two failure directions are not worth the same."""
    assert n.PLAUSIBLE_STRIP_WIDTH > PANEL[2]


def test_a_reported_rectangle_narrower_than_the_strip_is_left_alone():
    """The forward-compatible case, not a defensive one. The day macOS
    reports a real banner rectangle, this stops being a heuristic — without
    an edit."""
    assert n.plausible_region(SHORT_BANNER) == SHORT_BANNER
    assert n.plausible_region(PANEL) == PANEL


def test_a_display_narrower_than_the_strip_is_left_alone():
    narrow = (0, 0, 320, 480)
    assert n.plausible_region(narrow) == narrow


# -- what counts as being in the way --------------------------------------


def test_a_notification_over_the_window_is_in_the_way():
    assert n.in_the_way(IN_THE_STRIP, [DISPLAY])


def test_a_window_nowhere_near_the_notification_is_left_alone():
    """THE 16.1 BUG. macOS reports the whole display, so before the region
    was narrowed this window dimmed for a banner in a corner it was nothing
    like."""
    assert not n.in_the_way(AWAY, [DISPLAY])


def test_the_narrowing_happens_inside_in_the_way():
    """One path from a reported rectangle to an answer, so no caller can
    compare against a whole display again — which is exactly what shipped
    in 16."""
    assert not n.in_the_way(AWAY, [DISPLAY])
    assert n.in_the_way(AWAY, [n.plausible_region(AWAY)]), "sanity: it can be covered"


def test_nothing_on_screen_is_not_in_the_way():
    """The ordinary answer, most of the time: no notification window is on
    screen, so occupied_rects comes back empty."""
    assert not n.in_the_way(IN_THE_STRIP, [])


def test_a_notification_beside_the_window_is_not_in_the_way():
    assert not n.in_the_way(IN_THE_STRIP, [(0, 0, 100, 100)])


def test_a_corner_of_overlap_is_enough():
    """Partial overlap counts. A threshold would be describing how much of a
    rectangle this code has never seen is over another, and the strip is
    already an over-approximation."""
    window = (1200, 400, 200, 200)  # its right edge reaches into the strip
    assert n.in_the_way(window, [DISPLAY])


def test_touching_edges_do_not_count():
    """A notification whose rectangle stops exactly where the window starts
    is not over it. Decided in one place — geometry.intersects — so this and
    the menu bar item's on-screen test cannot answer it differently."""
    region = n.plausible_region(DISPLAY)
    x, y, width, height = region
    just_left = (x - 460, y, 460, height)
    assert not n.in_the_way(just_left, [DISPLAY])
    assert n.in_the_way((x - 459, y, 460, height), [DISPLAY])


def test_any_one_of_several_is_enough():
    assert n.in_the_way(IN_THE_STRIP, [(0, 0, 10, 10), DISPLAY])


def test_the_region_still_covers_the_window_wherever_it_sits_in_the_strip():
    """The cost of the heuristic, stated as a test rather than left as a
    surprise: full height, so a window low on the right still fades for a
    banner at the top of it. Nothing distinguishes the panel from a banner,
    and the panel reaches that far."""
    for y in (0, 200, 600, 900):
        assert n.in_the_way((1400, y, 300, 180), [DISPLAY])


def test_a_notification_on_another_display_does_not_reach_this_one():
    """Milestone 16's one real property, preserved by narrowing the reported
    rectangle rather than by asking a screen where its right edge is."""
    on_second = (3200, 300, 400, 200)  # in the second display's own strip
    assert not n.in_the_way(on_second, [DISPLAY])
    assert n.in_the_way(on_second, [SECOND_DISPLAY])


# -- how often to look ----------------------------------------------------


def test_the_idle_rate_is_used_while_nothing_is_over_the_window():
    assert n.poll_interval_seconds(False) == n.POLL_SECONDS


def test_the_rate_goes_up_while_yielded():
    """Coming back late is worse than going away late: a banner nobody has
    read yet costs nothing, the user waiting for their own lyrics does."""
    assert n.poll_interval_seconds(True) == n.YIELDED_POLL_SECONDS
    assert n.YIELDED_POLL_SECONDS < n.POLL_SECONDS


def test_the_faster_rate_is_not_finer_than_the_fade():
    """The fade is 260ms and dominates the restore, so polling faster than
    this buys less and less off the total for double the cost each time."""
    assert n.YIELDED_POLL_SECONDS * 1000 <= n.YIELD_MS
    assert n.YIELDED_POLL_SECONDS >= 0.05


# -- how faint it goes ----------------------------------------------------


def test_level_zero_leaves_the_users_opacity_exactly_alone():
    for opacity in (0.25, 0.5, 0.87, 1.0):
        assert n.yielded_opacity(opacity, 0.0) == pytest.approx(opacity)


def test_level_one_is_the_ceiling():
    for opacity in (0.25, 0.5, 1.0):
        assert n.yielded_opacity(opacity, 1.0) == pytest.approx(n.YIELD_CEILING)


def test_halfway_is_halfway():
    assert n.yielded_opacity(1.0, 0.5) == pytest.approx((1.0 + n.YIELD_CEILING) / 2)


def test_the_fade_never_goes_brighter_than_the_user_asked_for():
    """The property the whole rule exists to hold. Swept rather than
    sampled, because it is the one thing a later change to either constant
    could break silently."""
    for step in range(101):
        level = step / 100
        for opacity in (0.25, 0.4, 0.6, 0.8, 1.0):
            assert n.yielded_opacity(opacity, level) <= opacity + 1e-9


def test_the_fade_only_ever_moves_one_way():
    """Monotonic in the level, so there is no point in the animation where
    the window brightens on its way to being faint."""
    values = [n.yielded_opacity(1.0, step / 50) for step in range(51)]
    assert values == sorted(values, reverse=True)


def test_an_opacity_already_below_the_ceiling_is_left_alone():
    """Unreachable today — the window's own floor is 0.25 and the ceiling
    is 0.15 — which is exactly why it is pinned here. A ceiling that was
    ever raised above the floor must still not brighten the window."""
    assert n.yielded_opacity(0.10, 1.0) == pytest.approx(0.10)
    assert n.yielded_opacity(0.10, 0.5) == pytest.approx(0.10)


def test_the_ceiling_is_below_the_window_opacity_floor():
    """Not a tautology: it is the reason yielding is visible at all for a
    user who has already dimmed the window as far as the wheel allows."""
    from sottovoce import window as w

    assert n.YIELD_CEILING < w._MIN_OPACITY


def test_the_level_is_clamped():
    assert n.yielded_opacity(1.0, -3.0) == pytest.approx(1.0)
    assert n.yielded_opacity(1.0, 9.0) == pytest.approx(n.YIELD_CEILING)


# -- how long it takes ----------------------------------------------------


def test_a_full_fade_takes_the_whole_duration():
    assert n.duration_ms(0.0, 1.0) == n.YIELD_MS
    assert n.duration_ms(1.0, 0.0) == n.YIELD_MS


def test_a_reversal_costs_what_is_left_rather_than_the_whole_journey():
    """A banner that clears while the window is still fading turns around
    from where it got to, in the time that distance is worth."""
    assert n.duration_ms(0.5, 0.0) == pytest.approx(n.YIELD_MS / 2, abs=1)
    assert n.duration_ms(0.25, 0.0) == pytest.approx(n.YIELD_MS / 4, abs=1)
    assert n.duration_ms(0.75, 1.0) == pytest.approx(n.YIELD_MS / 4, abs=1)


def test_a_zero_length_fade_still_has_a_duration():
    """A zero-duration QVariantAnimation never reports finishing, which
    would leave the level stuck part-way with no animation to nudge it."""
    assert n.duration_ms(0.5, 0.5) >= 1


# -- when the window may not yield at all ---------------------------------


REASONS = dict(enabled=True, visible=True, syncing=False, flying=False)


def test_a_showing_window_with_the_layer_on_may_yield():
    assert n.yield_refusal(**REASONS) is None
    assert n.may_yield(**REASONS)


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"enabled": False}, n.DISABLED),
        ({"visible": False}, n.HIDDEN),
        ({"syncing": True}, n.SYNCING),
        ({"flying": True}, n.FLYING),
    ],
)
def test_every_refusal_names_itself(change, reason):
    """One rule that both decides and explains. A log line that restated
    the rule would be a second copy of it, free to disagree."""
    assert n.yield_refusal(**{**REASONS, **change}) == reason


@pytest.mark.parametrize(
    "change",
    [{"enabled": False}, {"visible": False}, {"syncing": True}, {"flying": True}],
)
def test_may_yield_is_derived_from_the_refusal(change):
    assert not n.may_yield(**{**REASONS, **change})


def test_the_layer_being_off_outranks_everything_else():
    """Order matters for the log line, not for the answer: off is the
    reason worth reporting when the layer is off."""
    assert (
        n.yield_refusal(enabled=False, visible=False, syncing=True, flying=True)
        == n.DISABLED
    )


# -- the door, and what it may not ask for --------------------------------


SOURCE = Path(n.__file__).read_text()
TREE = ast.parse(SOURCE)


def test_occupied_rects_answers_nothing_without_a_door(monkeypatch):
    """Off macOS, without pyobjc, or with the door shut in the suite. A
    layer that cannot see is a layer that does not fade — never one that
    fades and stays faded."""
    monkeypatch.setattr(n, "_quartz", lambda: None)
    assert n.occupied_rects() == ()


def test_a_shut_door_is_what_the_suite_actually_has(escapes):
    """The guard has a test of its own, because an unrun guard is no
    guard. Reading the real window list would mean reading every window
    open on the machine running the suite."""
    with pytest.raises(RuntimeError, match="test escape"):
        n._quartz()
    assert any("CGWindowList" in e for e in escapes.drain())


def test_occupied_rects_is_the_only_thing_that_uses_the_door():
    """One door, one caller. Every other function here is arithmetic, which
    is what lets the rules above be tested at all."""
    callers = {
        node.name
        for node in ast.walk(TREE)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "_quartz"
            for inner in ast.walk(node)
        )
    }
    assert callers == {"occupied_rects"}


def test_the_native_imports_live_inside_the_door():
    """Quartz and AppKit are imported in exactly one place, and it is the
    function the suite shuts. A second import site would pass every
    behavioural test here while quietly reopening the door — the same claim
    frontmost.py makes about NSWorkspace, for the same reason."""
    door = next(
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.FunctionDef) and node.name == "_quartz"
    )
    native = ("Quartz", "NSRunningApplication")

    def import_sites(tree) -> list[str]:
        return [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
            if alias.name in native
        ]

    assert sorted(import_sites(door)) == sorted(native)
    # Nowhere else in the file, so the count over the whole module is the
    # count inside the door.
    assert sorted(import_sites(TREE)) == sorted(native)


def _code_names() -> set[str]:
    """Every name the module's CODE mentions: bare names and attributes.

    Scanned as syntax rather than as text, because the module docstring
    names the very symbols these tests forbid — it has to, in order to
    record that ``kCGWindowName`` is withheld and that the screen-capture
    calls are not made. A substring scan over the file could only be
    satisfied by deleting the explanation, which is the trap
    test_packaging.py already documents for the version literal.
    """
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.name for alias in node.names)
    return names


def test_the_window_title_is_never_asked_for():
    """The permission claim, enforced rather than promised.

    ``kCGWindowName`` is the ONE field macOS withholds without Screen
    Recording — measured: absent for 160 of 166 windows under an
    ungranted identity. Reading the titles of other people's windows is
    precisely the thing that deserves a prompt, and this module has no use
    for them: it needs to know a notification is on screen, not what it
    says. A future edit that reached for the title would be an edit that
    changed what this app asks of the user, and it has to argue with this
    test first.
    """
    assert "kCGWindowName" not in _code_names()
    assert "kCGWindowName" not in _code_strings()


def test_screen_capture_is_never_requested():
    """The other half of the same claim. Nothing here may call the API that
    puts the prompt up, capture a pixel, or preflight an access it does not
    need — including the tempting one, ``CGWindowListCreateImage``, which is
    the only way to find the banner's real rectangle and is exactly what
    would make this app ask for Screen Recording."""
    forbidden = {
        "CGRequestScreenCaptureAccess",
        "CGPreflightScreenCaptureAccess",
        "CGWindowListCreateImage",
        "CGDisplayCreateImage",
        "SCShareableContent",
    }
    assert not (forbidden & _code_names())


def test_control_centre_is_not_treated_as_the_notification_system():
    """The trap this design walked into first. Control Centre owns eleven
    permanently on-screen windows — one per menu bar item, measured — so an
    app that yielded to it would fade on the first poll and never come
    back.

    Checked against the code's own strings, not the file: the docstring
    names Control Centre in order to explain the exclusion, and a scan that
    counted that could only be satisfied by deleting the explanation. The
    same distinction test_packaging.py draws for the version literal.
    """
    assert n.NOTIFICATION_BUNDLE_ID == "com.apple.notificationcenterui"
    assert "com.apple.controlcenter" not in _code_strings()


def _code_strings() -> set[str]:
    """Every string literal in the module except docstrings.

    The docstring names Control Centre in order to explain why it is
    excluded, which is the opposite of a bug — so the scan is over code,
    the same distinction test_packaging.py draws for the version literal.
    """
    docstrings = set()
    for node in ast.walk(TREE):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return {
        node.value
        for node in ast.walk(TREE)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def test_the_owner_name_is_never_matched_on():
    """It is LOCALISED — "Notification Centre" here, "Notification Center"
    on a US system. A string match on it would work for whoever wrote this
    and quietly never fire for half the people who ran it, which is the
    worst failure shape available: a layer that is simply off, silently,
    for reasons nobody can see."""
    assert "kCGWindowOwnerName" not in _code_names()
    literals = _code_strings()
    assert not any("Notification Cent" in text for text in literals)
