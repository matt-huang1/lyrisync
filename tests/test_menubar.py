"""Which glyph the menu bar item shows.

Three states, and the count is the design: at 16 points the eye takes the
icon in without focusing, so every state past the third is a distinction
nobody can make. What cannot be checked here — whether they are actually
told apart at that size — is a question about pixels and is verified by
hand; see docs/menu-and-system-integration.md.
"""

from pathlib import Path

from lyrisync import menubar

ASSETS = Path(menubar.__file__).parent / "assets"


def state(**overrides):
    settings = dict(playing=False, lyrics_visible=True, practising=False)
    settings.update(overrides)
    return menubar.icon_state(**settings)


def test_playing_with_the_lyrics_up_is_the_active_glyph():
    assert state(playing=True) == menubar.ACTIVE


def test_nothing_playing_is_idle():
    """Paused, stopped, or no Spotify at all. Dimmer means less to say."""
    assert state(playing=False) == menubar.IDLE


def test_hiding_the_lyrics_dims_the_glyph():
    """Which makes the menu bar the confirmation that ⇧⌘J landed, on a
    keypress whose whole effect is that something disappears."""
    assert state(playing=True, lyrics_visible=False) == menubar.IDLE


def test_a_practice_mode_is_the_accented_glyph():
    assert state(playing=True, practising=True) == menubar.PRACTICE


def test_practice_outranks_everything_else():
    """A loop or a sync pass keeps running while the lyrics are hidden,
    and then the menu bar item is the ONLY evidence it is still going. An
    icon that went quiet there would be reporting on the window rather
    than on the app."""
    assert state(playing=False, lyrics_visible=False, practising=True) == (
        menubar.PRACTICE
    )
    assert state(playing=True, lyrics_visible=False, practising=True) == (
        menubar.PRACTICE
    )


def test_there_are_exactly_three_states():
    """The number is the point. A menu bar icon is 16 points tall and
    shares a strip with a dozen others."""
    assert len(menubar.STATES) == 3
    assert set(menubar.ICON_FILES) == set(menubar.STATES)


def test_every_state_has_an_image_that_exists():
    for state_name in menubar.STATES:
        assert (ASSETS / menubar.ICON_FILES[state_name]).is_file()


def test_every_glyph_is_a_template_image():
    """Solid black, shape in the alpha channel, so macOS tints them for a
    light or dark menu bar. A coloured icon stops following the menu bar,
    which is why the practice state is a DOT and not a hue."""
    for state_name in menubar.STATES:
        source = (ASSETS / menubar.ICON_FILES[state_name]).read_text(encoding="utf-8")
        assert 'fill="#000000"' in source
        body = source.split("-->")[-1]  # the comments explain the rule
        assert "fill=" not in body.replace('fill="#000000"', "").replace(
            "fill-opacity", ""
        )


def test_the_states_do_not_all_look_the_same():
    """Three files that happened to be identical would pass every test
    above and say nothing on the menu bar."""
    drawings = {
        (ASSETS / filename).read_text(encoding="utf-8").split("-->")[-1]
        for filename in menubar.ICON_FILES.values()
    }
    assert len(drawings) == 3


def test_idle_is_the_active_glyph_with_less_ink():
    """One shape at two strengths is one icon doing more or less; two
    shapes would be two icons."""
    active = (ASSETS / menubar.ICON_FILES[menubar.ACTIVE]).read_text(encoding="utf-8")
    idle = (ASSETS / menubar.ICON_FILES[menubar.IDLE]).read_text(encoding="utf-8")
    assert "fill-opacity" in idle and "fill-opacity" not in active
    shapes = lambda text: [  # noqa: E731 - a local reading aid, not an API
        line.strip() for line in text.splitlines() if line.strip().startswith("<rect")
    ]
    assert shapes(idle) == shapes(active)


def test_nothing_in_the_menu_bar_animates():
    """A moving menu bar icon is a thing to look at, and this is a thing
    to notice. Asserted on the images, because an animated SVG would need
    no code change to arrive."""
    for filename in menubar.ICON_FILES.values():
        source = (ASSETS / filename).read_text(encoding="utf-8")
        assert "animate" not in source
        assert "<style" not in source
