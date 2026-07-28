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
