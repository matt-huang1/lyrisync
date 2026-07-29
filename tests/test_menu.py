from lyrisync import menu as m


ALL_LAYERS = dict(
    has_korean_lyrics=True,
    speech_available=True,
    synced=True,
    sync_offered=True,
    login_item_offered=True,
)
NO_LAYERS = dict(
    has_korean_lyrics=False,
    speech_available=False,
    synced=False,
    sync_offered=False,
    login_item_offered=False,
)


def entries(**overrides):
    return m.visible_entries(**{**NO_LAYERS, **overrides})


def without_separators(keys):
    return tuple(key for key in keys if key not in m.SEPARATORS)


# -- order and structure --------------------------------------------------


def test_full_menu_is_in_the_declared_order():
    assert m.visible_entries(**ALL_LAYERS) == m.MENU_ORDER


def test_visible_entries_never_reorder_or_invent_keys():
    for state in (ALL_LAYERS, NO_LAYERS, {**NO_LAYERS, "synced": True}):
        shown = m.visible_entries(**state)
        assert set(shown) <= set(m.MENU_ORDER)
        positions = [m.MENU_ORDER.index(key) for key in shown]
        assert positions == sorted(positions)


def test_quit_is_last_and_show_lyrics_first():
    for state in (ALL_LAYERS, NO_LAYERS):
        shown = without_separators(m.visible_entries(**state))
        assert shown[0] == m.SHOW_LYRICS
        assert shown[-1] == m.QUIT


# -- gating ---------------------------------------------------------------


def test_bare_menu_with_every_layer_dormant():
    """Layers off must equal the original core app: show/hide, the two
    standing choices about how the window looks, and quit."""
    assert without_separators(entries()) == (
        m.SHOW_LYRICS,
        m.ALBUM_COLOUR,
        m.ALL_DESKTOPS,
        m.QUIT,
    )


def test_album_colour_is_offered_before_any_music_has_played():
    """Unlike the learning layers it is not gated on the song. It is a
    standing preference about the window, and one that appeared and
    vanished with each track would be hardest to find at the moment the
    user goes looking — before the music starts."""
    assert m.ALBUM_COLOUR in entries()
    assert m.ALBUM_COLOUR in m.visible_entries(**ALL_LAYERS)


def test_show_hide_spaces_and_quit_are_always_offered():
    for state in (ALL_LAYERS, NO_LAYERS):
        shown = m.visible_entries(**state)
        assert m.ALWAYS_VISIBLE <= set(shown)


def test_quit_survives_every_state():
    """It is the only way out of an app with no Dock icon."""
    assert m.QUIT in entries()
    assert m.QUIT in m.visible_entries(**ALL_LAYERS)


def test_romanisation_needs_hangul_under_a_current_line():
    assert m.ROMANISATION not in entries()
    assert m.ROMANISATION in entries(has_korean_lyrics=True)


def test_spoken_reference_and_rate_need_the_installed_voice():
    assert m.SPOKEN not in entries()
    assert m.SPEECH_RATE not in entries()
    shown = entries(speech_available=True)
    assert m.SPOKEN in shown and m.SPEECH_RATE in shown


def test_echo_practice_needs_synced_timestamps():
    assert m.ECHO not in entries()
    assert m.ECHO in entries(synced=True)


def test_sync_entry_follows_the_view_models_offer():
    assert m.SYNC not in entries()
    assert m.SYNC in entries(sync_offered=True)


def test_open_at_login_is_offered_only_when_something_can_be_registered():
    """Gated on how the app was launched, not on the song: from a source
    checkout there is no bundle for macOS to start."""
    assert m.OPEN_AT_LOGIN not in entries()
    assert m.OPEN_AT_LOGIN in entries(login_item_offered=True)


def test_open_at_login_defaults_to_hidden():
    """The default has to be 'not offered': every caller that has not been
    taught about login items yet must get a menu without it, rather than an
    entry that cannot work."""
    assert m.OPEN_AT_LOGIN not in m.visible_entries(
        has_korean_lyrics=True,
        speech_available=True,
        synced=True,
        sync_offered=True,
    )


def test_open_at_login_sits_with_the_window_behaviour_entries():
    """Next to Show on all desktops: both are about how the app behaves,
    not about the song, and neither belongs among the learning layers."""
    shown = without_separators(m.visible_entries(**ALL_LAYERS))
    assert shown.index(m.OPEN_AT_LOGIN) == shown.index(m.ALL_DESKTOPS) + 1


def test_layers_gate_independently():
    only_echo = entries(synced=True)
    assert m.ECHO in only_echo
    assert m.ROMANISATION not in only_echo
    assert m.SPOKEN not in only_echo
    assert m.SYNC not in only_echo


# -- separators -----------------------------------------------------------


def test_separators_survive_where_they_still_divide_groups():
    # ALL_DESKTOPS always sits between them, so both always have work to do.
    assert m.SEPARATOR_AFTER_SHOW in entries()
    assert m.SEPARATOR_BEFORE_QUIT in entries()


def test_no_separator_ever_leads_trails_or_doubles_up():
    states = [
        {**NO_LAYERS, **dict(zip(NO_LAYERS, bits))}
        for bits in _bit_combinations(len(NO_LAYERS))
    ]
    for state in states:
        shown = m.visible_entries(**state)
        assert shown, state
        assert shown[0] not in m.SEPARATORS, state
        assert shown[-1] not in m.SEPARATORS, state
        pairs = zip(shown, shown[1:])
        assert not any(a in m.SEPARATORS and b in m.SEPARATORS for a, b in pairs), state


def test_a_separator_is_dropped_when_its_group_empties():
    """Directly exercising the collapse rule: with only the outer groups
    left, one separator does the whole job."""
    collapsed = m._with_separators({m.SHOW_LYRICS, m.QUIT})
    assert collapsed == (m.SHOW_LYRICS, m.SEPARATOR_AFTER_SHOW, m.QUIT)


def test_collapse_drops_leading_and_trailing_separators():
    assert m._with_separators({m.QUIT}) == (m.QUIT,)
    assert m._with_separators({m.SHOW_LYRICS}) == (m.SHOW_LYRICS,)
    assert m._with_separators(set()) == ()


def _bit_combinations(width):
    for value in range(1 << width):
        yield [bool(value & (1 << bit)) for bit in range(width)]
