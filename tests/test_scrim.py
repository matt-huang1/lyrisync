"""The contrast floor, computed rather than eyeballed — for both palettes.

The window paints ``palette.scrim`` on top of the vibrancy material and
``palette.solid`` when there is no material at all. Both carry the same
promise in both appearances: the sung line stays readable over whatever
is behind the window. The material only ever helps — it renders as a
tint in the direction of its own mode — so the floor is the case where
it contributes nothing, and that is what is checked here.

Which backdrop is the hard one flips with the palette, and getting that
wrong is how a light mode ships broken. White text on a dark scrim is
worst over a WHITE page: the scrim is the palest it can get there. Dark
text on a light scrim is worst over a BLACK one. So every check below
runs over both extremes and takes the worse — no palette gets to be
graded on the backdrop that happens to suit it.

The compositing and the WCAG maths live in the test rather than in the
app: the app has no reason to compute a contrast ratio at runtime, but a
constant nudged by eye should fail the suite. Nothing here needs Qt —
the palettes are plain tuples — so the floor stays checkable even where
PySide6 will not load.
"""

import pytest

from lyrisync import appearance as ap
from lyrisync.appearance import DARK, LIGHT

WHITE = (255.0, 255.0, 255.0)
BLACK = (0.0, 0.0, 0.0)
# Representative real backdrops, for the numbers quoted in the README.
DARK_EDITOR = (30.0, 31.0, 36.0)
BRIGHT_VIDEO = (243.0, 241.0, 236.0)

EXTREMES = (WHITE, BLACK)


def _linear(channel: float) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(first, second) -> float:
    a, b = luminance(first), luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def over(top, top_alpha, bottom, bottom_alpha=1.0):
    """Source-over of two straight-alpha colours -> (colour, alpha)."""
    alpha = top_alpha + bottom_alpha * (1 - top_alpha)
    if alpha == 0:
        return (0.0, 0.0, 0.0), 0.0
    colour = tuple(
        (t * top_alpha + b * bottom_alpha * (1 - top_alpha)) / alpha
        for t, b in zip(top, bottom)
    )
    return colour, alpha


def text_contrast(background, text, page, window_opacity=1.0):
    """A line of text against its own background, both over ``page``.

    ``background`` is the window's painted background; the material below
    it is taken as fully transparent, which is the worst it can do.
    """
    bg, bg_alpha = background[:3], background[3] / 255
    fg, fg_alpha = over(text[:3], text[3] / 255, bg, bg_alpha)
    # The whole window is composited onto the page at its own opacity.
    on_page_bg, _ = over(bg, bg_alpha * window_opacity, page)
    on_page_fg, _ = over(fg, fg_alpha * window_opacity, page)
    return contrast(on_page_fg, on_page_bg)


def worst_over_extremes(background, text, window_opacity=1.0):
    """The ratio on the backdrop that suits this colour least."""
    return min(
        text_contrast(background, text, page, window_opacity) for page in EXTREMES
    )


PALETTES = pytest.mark.parametrize(
    "palette", (DARK, LIGHT), ids=("dark", "light")
)


# -- the promise ----------------------------------------------------------


@PALETTES
def test_the_scrim_clears_4_5_to_1_over_any_backdrop_with_no_material(palette):
    assert worst_over_extremes(palette.scrim, palette.current) >= 4.5


@PALETTES
def test_the_solid_background_clears_it_too(palette):
    """The fallback when vibrancy never installs carries the same promise
    and, being nearly opaque, has an easier time of it."""
    assert worst_over_extremes(palette.solid, palette.current) >= 4.5


@PALETTES
def test_the_scrim_keeps_no_more_than_a_hair_of_headroom(palette):
    """Guard the tuning from the other side too. Each scrim sits a few
    steps above where its own worst backdrop crosses 4.5:1 — enough that
    rounding cannot land under the threshold, little enough that it is
    still the lowest alpha the promise allows, and not a value drifted
    upwards by eye. Dark crosses at 147 and ships 150; light crosses at
    131 and ships 134."""
    red, green, blue, alpha = palette.scrim
    lighter = (red, green, blue, alpha - 6)
    assert worst_over_extremes(lighter, palette.current) < 4.5


# -- the two palettes are held to each other ------------------------------

# Roles drawn straight onto the panel, so the scrim really is what is
# behind them. The button fills (tap, attempt) are their own backgrounds
# and are not measured this way.
PANEL_TEXT = (
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


@pytest.mark.parametrize("role", PANEL_TEXT)
def test_light_is_never_worse_than_dark_in_its_own_worst_case(role):
    """The property that makes light mode a real mode rather than a
    recolour: every role that recedes in dark mode recedes exactly as far
    in light mode, and none of them recedes further.

    Only the sung line carries the 4.5:1 promise — the context lines and
    the header are meant to fall back, and do in both. This is what keeps
    "meant to fall back" from turning into "gone" on one side only. Four
    light colours were swept to satisfy it (the accent blues, the idle
    control and the warning red), which is the whole reason it is pinned.
    """
    dark = worst_over_extremes(DARK.scrim, getattr(DARK, role))
    light = worst_over_extremes(LIGHT.scrim, getattr(LIGHT, role))
    assert light >= dark, (
        f"{role}: light bottoms out at {light:.2f}:1 against dark's "
        f"{dark:.2f}:1"
    )


@PALETTES
def test_the_sung_line_is_the_strongest_thing_on_the_panel(palette):
    """Colour carries the hierarchy: whatever direction the palette runs
    in, the line being sung has to out-read every line around it."""
    sung = worst_over_extremes(palette.scrim, palette.current)
    for role in ("context", "header", "pronunciation", "plain"):
        assert sung > worst_over_extremes(palette.scrim, getattr(palette, role))


# -- the named backdrops, as reported -------------------------------------


@PALETTES
def test_the_reported_backdrops_all_clear_the_floor(palette):
    """The three cases quoted in the README. Computed with the material
    contributing nothing, which is strictly worse than what a screenshot
    measures — the material always tints towards its own mode."""
    for page in (WHITE, DARK_EDITOR, BRIGHT_VIDEO):
        ratio = text_contrast(palette.scrim, palette.current, page)
        assert ratio >= 4.5, f"{page} -> {ratio:.2f}:1"


# -- the album-colour tint ------------------------------------------------

# Maximally saturated artwork of every hue. Real covers are gentler than
# this; the point is that none of them can be worse.
HOSTILE_HUES = tuple(range(0, 360, 3))


def hostile_artwork(hue):
    return ap.hsl_to_rgb(hue, 1.0, 0.5)


@pytest.mark.parametrize(
    "palette,appearance", ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)),
    ids=("dark", "light"),
)
def test_the_floor_holds_under_every_hue(palette, appearance):
    """THE claim the tint rests on. A hue-only tint is only safe if it
    really is hue-only, and the way that goes wrong is subtle: HSL's
    lightness is not relative luminance, and pinning the wrong one moves
    the contrast floor by hue. This sweeps every hue at full saturation
    against both extreme backdrops, which is more covers than anyone
    could think to try."""
    for hue in HOSTILE_HUES:
        tinted = ap.tinted(palette, hostile_artwork(hue), appearance)
        for background in (tinted.scrim, tinted.solid):
            ratio = worst_over_extremes(background, palette.current)
            assert ratio >= 4.5, f"hue {hue} -> {ratio:.2f}:1"


@pytest.mark.parametrize(
    "palette,appearance", ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)),
    ids=("dark", "light"),
)
def test_a_tint_barely_moves_the_luminance_it_was_given(palette, appearance):
    """The mechanism, checked directly rather than only through its
    consequence. What is left is 8-bit quantisation, not drift."""
    base = ap.relative_luminance(palette.scrim)
    for hue in HOSTILE_HUES:
        tinted = ap.tinted(palette, hostile_artwork(hue), appearance)
        moved = abs(ap.relative_luminance(tinted.scrim) - base) / base
        assert moved < 0.10, f"hue {hue} moved luminance by {moved:.1%}"


@pytest.mark.parametrize(
    "palette,appearance", ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)),
    ids=("dark", "light"),
)
def test_the_cross_fade_never_dips_below_the_floor(palette, appearance):
    """Every frame between two covers is a colour the user reads lyrics
    against, so the promise covers the journey as well as the ends."""
    first = ap.tinted(palette, hostile_artwork(20), appearance).scrim
    second = ap.tinted(palette, hostile_artwork(200), appearance).scrim
    for step in range(0, 11):
        mixed = ap.blend(first, second, step / 10)
        assert worst_over_extremes(mixed, palette.current) >= 4.5
    # And fading in from untinted, which is what enabling the layer does.
    for step in range(0, 11):
        mixed = ap.blend(palette.scrim, second, step / 10)
        assert worst_over_extremes(mixed, palette.current) >= 4.5


@pytest.mark.parametrize(
    "palette,appearance", ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)),
    ids=("dark", "light"),
)
def test_a_pale_or_hot_cover_does_not_make_a_pale_or_hot_window(palette, appearance):
    """THE GOVERNING RULE, stated as a test. The artwork supplies a hue;
    its own lightness and saturation are discarded. Near-white, near-black
    and neon covers of the same hue must all produce the same window."""
    windows = {
        ap.tinted(palette, artwork, appearance).scrim
        for artwork in (
            (255, 230, 230),   # a nearly white sleeve
            (40, 6, 6),        # a nearly black one
            (255, 0, 0),       # neon
            (150, 90, 90),     # muted
        )
    }
    assert len(windows) == 1, f"the same hue produced {len(windows)} windows"


@pytest.mark.parametrize(
    "palette,appearance", ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)),
    ids=("dark", "light"),
)
def test_an_unusable_cover_leaves_the_palette_untouched(palette, appearance):
    """Greyscale covers, and no cover at all. "Off" and "nothing to tint
    with" have to be the same pixels, or the layer would be visible while
    doing nothing."""
    for artwork in (None, (128, 128, 128), (0, 0, 0), (255, 255, 255), (200, 198, 199)):
        assert ap.tinted(palette, artwork, appearance) is palette


@pytest.mark.parametrize(
    "palette,appearance", ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)),
    ids=("dark", "light"),
)
def test_a_tint_moves_the_backgrounds_and_the_edge_and_nothing_else(
    palette, appearance
):
    """Text keeps every value 12a measured. That is what lets the floor be
    re-checked above rather than re-derived from scratch — and it is why
    the hairline joining the tint costs nothing: no text is read against
    it."""
    tinted = ap.tinted(palette, (200, 40, 40), appearance)
    moved = {
        name for name, value in vars(tinted).items() if value != getattr(palette, name)
    }
    assert moved <= {"scrim", "solid", "border"}
    assert moved, "the tint did nothing at all"


def test_a_tint_keeps_the_alpha_it_was_given():
    """The scrim's alpha is the measured constant from 12a; a tint that
    changed it would be re-deciding the contrast floor by accident."""
    for palette, appearance in ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)):
        tinted = ap.tinted(palette, (200, 40, 40), appearance)
        assert tinted.scrim[3] == palette.scrim[3]
        assert tinted.solid[3] == palette.solid[3]


# -- how much colour a tint carries ---------------------------------------


def panel_chroma(colour):
    """The colour actually reaching the eye: the spread between the
    strongest and weakest channel, diluted by the background's alpha."""
    return (max(colour[:3]) - min(colour[:3])) * colour[3] / 255


def test_chroma_is_solved_against_what_is_actually_achieved():
    """Bisected on saturation rather than computed from HSL's closed
    form. The closed form answers a different question — chroma at some
    lightness — and pinning the luminance then moves the lightness by
    wildly different amounts per hue. Feeding that back as a correction
    oscillates instead of converging.
    """
    for luminance in (0.005, 0.2, 0.5, 0.93):
        for target in (6.0, 14.0):
            for hue in (0, 60, 120, 180, 240, 300):
                got = ap.chroma_of(ap._at_chroma(hue, target, luminance))
                # Either it hit the target, or the gamut would not allow
                # it — never wildly over, and never oscillating away.
                assert got <= target + 2, f"hue {hue} overshot to {got}"


def test_the_solver_is_monotonic_in_what_it_is_asked_for():
    """More chroma asked for is never less chroma delivered — the
    property that makes bisection valid at all."""
    for hue in (0, 90, 200, 300):
        got = [ap.chroma_of(ap._at_chroma(hue, t, 0.93)) for t in (2, 6, 10, 20, 40)]
        assert got == sorted(got), f"hue {hue}: {got}"


def test_a_hue_that_cannot_reach_the_target_settles_for_its_most():
    """A gamut limit, not an error. At the light panel's luminance a blue
    is nearly white — blue carries 7% of luminance, so buying blue chroma
    costs brightness the pinned luminance will not give up."""
    reachable = ap.chroma_of(ap._at_chroma(240, 60.0, 0.93))
    assert 0 < reachable < 60
    # And asking for even more does not change the answer.
    assert ap.chroma_of(ap._at_chroma(240, 200.0, 0.93)) == reachable


@PALETTES
def test_a_tint_is_actually_visible(palette):
    """The bug this replaced: at 0.22 HSL saturation the light tint moved
    the scrim by 3/255 — less than the palette's own built-in blue cast,
    so album colour looked switched off however strong the cover was.
    Every hue must now clear the untinted panel's own chroma."""
    baseline = panel_chroma(palette.scrim)
    appearance = (
        ap.Appearance.DARK if palette is DARK else ap.Appearance.LIGHT
    )
    for hue in range(0, 360, 30):
        tinted = ap.tinted(palette, ap.hsl_to_rgb(hue, 1.0, 0.5), appearance).scrim
        assert panel_chroma(tinted) > baseline, f"hue {hue} is invisible"


def test_the_chroma_target_means_the_same_thing_in_both_modes():
    """Stated as delivered chroma rather than HSL saturation, because one
    saturation produced 2.4x more colour in dark than in light — the same
    number describing two different amounts of colour."""
    assert ap.TINT_CHROMA[ap.Appearance.DARK] == ap.TINT_CHROMA_DARK
    assert ap.TINT_CHROMA[ap.Appearance.LIGHT] == ap.TINT_CHROMA_LIGHT
    for value in ap.TINT_CHROMA.values():
        assert 0 < value < 64  # a cast, not a colour


@PALETTES
def test_the_tint_never_exceeds_what_was_asked_for(palette):
    """Clamping may deliver less — the gamut says so — but nothing may
    deliver more than the target, or the constant would not bound it."""
    appearance = (
        ap.Appearance.DARK if palette is DARK else ap.Appearance.LIGHT
    )
    target = ap.TINT_CHROMA[appearance]
    for hue in range(0, 360, 15):
        tinted = ap.tinted(palette, ap.hsl_to_rgb(hue, 1.0, 0.5), appearance)
        for background in (tinted.scrim, tinted.solid):
            assert panel_chroma(background) <= target + 1.5


def test_the_tint_strength_does_not_depend_on_which_hue_the_album_is():
    """Solved by bisection rather than computed once, because pinning the
    luminance moves the lightness by different amounts per hue.

    Dark has the gamut room to be even; light does not, and that is
    checked separately below rather than pretended away.
    """
    delivered = [
        panel_chroma(ap.tinted(DARK, ap.hsl_to_rgb(hue, 1.0, 0.5), ap.Appearance.DARK).scrim)
        for hue in range(0, 360, 15)
    ]
    assert max(delivered) / min(delivered) < 1.5


def test_light_mode_carries_less_colour_and_that_is_the_gamut_not_a_bug():
    """Honest limit, recorded so it is not rediscovered as a regression.
    The light panel sits at luminance 0.93, and holding that luminance —
    which is what protects the contrast floor — leaves the hues that
    carry little luminance of their own (blues, magentas, reds) with
    almost no room for chroma: buying their colour costs brightness the
    pinned luminance will not give up. Relaxing the luminance by even 5% was
    measured to buy little and to leave the floor at 4.52, with none of
    the rounding headroom the rest of the palette keeps.
    """
    light = [
        panel_chroma(ap.tinted(LIGHT, ap.hsl_to_rgb(hue, 1.0, 0.5), ap.Appearance.LIGHT).scrim)
        for hue in range(0, 360, 15)
    ]
    dark = [
        panel_chroma(ap.tinted(DARK, ap.hsl_to_rgb(hue, 1.0, 0.5), ap.Appearance.DARK).scrim)
        for hue in range(0, 360, 15)
    ]
    assert max(light) < max(dark)
    # Still worth far more than the 1.6-2.1 that shipped in 13.0.
    assert max(light) > 8


# -- the hairline, where the colour actually went --------------------------

# The backdrop that suits each mode least — the one the panel is closest
# to the edge's own lightness over, and therefore where an edge is hardest
# to keep on the right side of its panel.
WORST_PAGE = {ap.Appearance.DARK: WHITE, ap.Appearance.LIGHT: BLACK}

APPEARANCES = pytest.mark.parametrize(
    "palette,appearance",
    ((DARK, ap.Appearance.DARK), (LIGHT, ap.Appearance.LIGHT)),
    ids=("dark", "light"),
)


def flatten(top, bottom):
    """A straight-alpha colour painted over an opaque one."""
    colour, _ = over(top[:3], top[3] / 255, bottom)
    return colour


def panel_and_edge(palette, appearance, artwork):
    """What the eye gets: the panel over the worst backdrop for this mode,
    with no material contributing, and the hairline over that."""
    tinted = ap.tinted(palette, artwork, appearance)
    panel = flatten(tinted.scrim, WORST_PAGE[appearance])
    return panel, flatten(tinted.border, panel)


@APPEARANCES
def test_the_hairline_carries_far_more_colour_than_the_panel(palette, appearance):
    """The point of the whole change. The panel's luminance is pinned by
    the 4.5:1 promise and has almost nothing left to spend on colour; the
    hairline has no such obligation, so that is where the album goes."""
    for hue in range(0, 360, 15):
        artwork = ap.hsl_to_rgb(hue, 1.0, 0.5)
        tinted = ap.tinted(palette, artwork, appearance)
        assert panel_chroma(tinted.border) > 3 * panel_chroma(tinted.scrim), (
            f"hue {hue}: the edge is not carrying the colour"
        )


def test_the_hairline_carries_the_same_colour_whatever_the_hue():
    """What pinning LIGHTNESS buys, and the panel can never have: at a
    fixed lightness an HSL colour's chroma is exactly saturation x
    (1 - |2L - 1|), with no hue term at all. The panel has to bisect for
    its chroma hue by hue and still lands anywhere between 4.7 and 14."""
    for appearance in ap.Appearance:
        delivered = [
            panel_chroma(ap.tinted_border(hue, appearance))
            for hue in range(0, 360)
        ]
        assert max(delivered) - min(delivered) <= 1.0, "hue changed the strength"
        assert abs(max(delivered) - ap.BORDER_CHROMA) <= 1.5


@APPEARANCES
def test_the_hairline_is_still_an_edge_under_every_hue(palette, appearance):
    """A coloured line is not automatically an edge. It has to stay
    lighter than the dark panel and darker than the pale one — for every
    hue, over the backdrop that leaves the panel closest to it. The
    binding cases are blue in dark mode and yellow in light mode, the
    hues furthest from their own panel in luminance, and they are what
    fixes BORDER_LIGHTNESS."""
    for hue in range(0, 360, 5):
        panel, edge = panel_and_edge(palette, appearance, ap.hsl_to_rgb(hue, 1.0, 0.5))
        if appearance is ap.Appearance.DARK:
            assert luminance(edge) > luminance(panel), f"hue {hue} sank into the panel"
        else:
            assert luminance(edge) < luminance(panel), f"hue {hue} lifted off the panel"


@APPEARANCES
def test_the_hairline_takes_the_hue_it_was_given(palette, appearance):
    """Hue-only, the same governing rule as the panel: the artwork says
    which colour, never how much."""
    for hue in range(0, 360, 15):
        border = ap.tinted(palette, ap.hsl_to_rgb(hue, 1.0, 0.5), appearance).border
        got, _, _ = ap.rgb_to_hsl(border)
        assert min(abs(got - hue), 360 - abs(got - hue)) < 3


@APPEARANCES
def test_a_pale_or_hot_cover_makes_the_same_edge(palette, appearance):
    """The failure a hue-only tint exists to prevent, checked on the
    hairline too: four covers of one hue, one edge."""
    edges = {
        ap.tinted(palette, artwork, appearance).border
        for artwork in ((255, 230, 230), (40, 6, 6), (255, 0, 0), (150, 90, 90))
    }
    assert len(edges) == 1


@APPEARANCES
def test_an_unusable_cover_leaves_the_hairline_neutral(palette, appearance):
    """No cover, or a black-and-white one: the palette's own edge, exactly
    as it was before album colour existed."""
    for artwork in (None, (128, 128, 128), (200, 198, 199)):
        assert ap.tinted(palette, artwork, appearance).border == palette.border


@APPEARANCES
def test_the_tinted_hairline_costs_the_contrast_floor_nothing(palette, appearance):
    """The argument for putting the colour here rather than in the panel:
    no text is read against the edge, so its strength is not the floor's
    business. Every text colour, and both backgrounds, are what they were
    — which is why the floor tests above did not have to be re-derived."""
    tinted = ap.tinted(palette, (200, 40, 40), appearance)
    for name in ("current", "context", "header", "pronunciation", "plain", "progress"):
        assert getattr(tinted, name) == getattr(palette, name)
    assert worst_over_extremes(tinted.scrim, tinted.current) >= 4.5
    assert worst_over_extremes(tinted.solid, tinted.current) >= 4.5


def test_the_hairline_keeps_the_alpha_its_mode_asks_for():
    """Alpha is a knob here, not a constant to preserve: it trades against
    saturation for the same delivered chroma, and it is what stops a
    strongly coloured edge having to be a strongly saturated one."""
    for appearance in ap.Appearance:
        border = ap.tinted_border(200.0, appearance)
        assert border[3] == ap.BORDER_ALPHA[appearance]


# -- the colour maths itself ----------------------------------------------


def test_hsl_round_trips():
    for rgb in ((200, 40, 40), (40, 200, 90), (10, 20, 200), (128, 128, 128), (0, 0, 0)):
        hue, saturation, lightness = ap.rgb_to_hsl(rgb)
        assert ap.hsl_to_rgb(hue, saturation, lightness) == rgb


def test_relative_luminance_matches_the_reference_points():
    assert ap.relative_luminance((255, 255, 255)) == pytest.approx(1.0)
    assert ap.relative_luminance((0, 0, 0)) == pytest.approx(0.0)
    # Green carries most of the weight, blue least — the whole reason a
    # hue shift at constant HSL lightness would not have been safe.
    assert ap.relative_luminance((0, 255, 0)) > ap.relative_luminance((255, 0, 0))
    assert ap.relative_luminance((255, 0, 0)) > ap.relative_luminance((0, 0, 255))


def test_an_achromatic_colour_has_no_hue_to_take():
    assert ap.usable_hue((128, 128, 128)) is None
    assert ap.usable_hue((10, 10, 11)) is None
    assert ap.usable_hue(None) is None
    assert ap.usable_hue((200, 40, 40)) is not None


def test_blending_stays_inside_its_endpoints():
    assert ap.blend((0, 0, 0, 100), (100, 200, 40, 100), 0.0) == (0, 0, 0, 100)
    assert ap.blend((0, 0, 0, 100), (100, 200, 40, 100), 1.0) == (100, 200, 40, 100)
    assert ap.blend((0, 0, 0, 100), (100, 200, 40, 100), 0.5) == (50, 100, 20, 100)
    # Out-of-range progress is clamped rather than extrapolated.
    assert ap.blend((0, 0, 0, 100), (100, 200, 40, 100), 5.0) == (100, 200, 40, 100)
    assert ap.blend((0, 0, 0, 100), (100, 200, 40, 100), -5.0) == (0, 0, 0, 100)


# -- opacity --------------------------------------------------------------


def test_the_window_starts_at_full_opacity():
    """Below 1.0 macOS renders the material without its blur, so the
    default has to be exactly 1.0 for the frost to exist at all."""
    w = pytest.importorskip(
        "lyrisync.window",
        reason="PySide6 unusable (missing system Qt libraries?)",
        exc_type=ImportError,
    )
    assert w._DEFAULT_OPACITY == 1.0
    assert w._MAX_OPACITY == 1.0


@PALETTES
def test_dimming_is_the_users_own_trade(palette):
    """No promise is made below full opacity — dimming is a deliberate
    request to see through the window — but it is worth knowing where it
    lands, and that it degrades smoothly rather than falling off a cliff.
    True in both modes: the gesture is not a colour and must not become
    one."""
    w = pytest.importorskip(
        "lyrisync.window",
        reason="PySide6 unusable (missing system Qt libraries?)",
        exc_type=ImportError,
    )
    ratios = [
        worst_over_extremes(palette.scrim, palette.current, window_opacity=opacity)
        for opacity in (1.0, 0.75, 0.5, w._MIN_OPACITY)
    ]
    assert ratios == sorted(ratios, reverse=True)
    assert ratios[-1] > 1.0


# -- resolving which palette to use ---------------------------------------


def test_only_an_explicit_light_is_light():
    assert ap.from_color_scheme(ap.COLOR_SCHEME_LIGHT) is ap.Appearance.LIGHT
    assert ap.from_color_scheme(ap.COLOR_SCHEME_DARK) is ap.Appearance.DARK


def test_a_platform_that_will_not_say_gets_dark():
    """Unknown is what the offscreen plugin reports, and what a Linux
    session without a portal reports. Dark is what this window has always
    been and the safer guess over somebody else's screen."""
    assert ap.from_color_scheme(ap.COLOR_SCHEME_UNKNOWN) is ap.Appearance.DARK
    assert ap.from_color_scheme(99) is ap.Appearance.DARK


def test_each_appearance_has_a_palette():
    assert ap.palette_for(ap.Appearance.LIGHT) is LIGHT
    assert ap.palette_for(ap.Appearance.DARK) is DARK


def test_the_two_palettes_describe_the_same_things():
    """A field added to one and forgotten in the other is a colour that
    silently falls back to whatever the stylesheet said last."""
    assert set(vars(LIGHT)) == set(vars(DARK))
    for name, value in vars(LIGHT).items():
        assert len(value) == 4, name
        assert all(0 <= channel <= 255 for channel in value), name


def test_dark_is_exactly_what_shipped_before_this_feature():
    """Following the system must not have quietly restyled dark mode.
    These are the literals from the old stylesheet."""
    assert DARK.scrim == (14, 15, 20, 150)
    assert DARK.solid == (18, 18, 24, 232)
    assert DARK.current == (255, 255, 255, 250)
    assert DARK.context == (255, 255, 255, 148)
    assert DARK.control_idle == (255, 255, 255, 105)
    assert DARK.control_hover == (255, 255, 255, 225)
    assert DARK.control_engaged == (130, 200, 255, 235)


def test_the_stylesheet_spelling_of_a_colour():
    assert ap.rgba((1, 2, 3, 4)) == "rgba(1, 2, 3, 4)"
