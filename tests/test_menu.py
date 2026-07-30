from sottovoce import menu as m


ALL_LAYERS = dict(
    has_korean_lyrics=True,
    speech_available=True,
    synced=True,
    sync_offered=True,
    login_item_offered=True,
    positions_remembered=True,
    remembering_positions=True,
)
NO_LAYERS = dict(
    has_korean_lyrics=False,
    speech_available=False,
    synced=False,
    sync_offered=False,
    login_item_offered=False,
    positions_remembered=False,
    remembering_positions=False,
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
    """Layers off must equal the original core app: show/hide, the three
    standing choices about how the window looks and where it lives, and
    quit. Forgetting learned positions is not among them — there is
    nothing to forget until something has been learned."""
    assert without_separators(entries()) == (
        m.SHOW_LYRICS,
        m.COMPACT,
        m.ALBUM_COLOUR,
        m.ALL_DESKTOPS,
        m.MENUBAR_ANIMATION,
        m.YIELD_NOTIFICATIONS,
        m.DOCK_TOP,
        m.REMEMBER_POSITION,
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
    """Grouped with the other entries about how the app behaves rather
    than about the song: Spaces, how the menu bar item behaves, how it treats
    a notification, where the window goes when docked and where it lives per
    app, then login. None of them belongs among the learning layers."""
    shown = without_separators(m.visible_entries(**ALL_LAYERS))
    behaviour = (
        m.ALL_DESKTOPS,
        m.MENUBAR_ANIMATION,
        m.YIELD_NOTIFICATIONS,
        m.DOCK_TOP,
        m.REMEMBER_POSITION,
        m.POSITION_STATUS,
        m.POSITION_LIST,
        m.FORGET_POSITIONS,
        m.OPEN_AT_LOGIN,
    )
    positions = [shown.index(key) for key in behaviour]
    assert positions == list(range(positions[0], positions[0] + len(behaviour)))


def test_forgetting_is_offered_only_once_there_is_something_to_forget():
    """The other half of the layers principle: an entry that cannot act is
    an entry that should not be there. It appears with the first learned
    position and goes again when the map is cleared."""
    assert m.FORGET_POSITIONS not in entries()
    assert m.FORGET_POSITIONS in entries(positions_remembered=True)


def test_the_list_of_remembered_apps_follows_the_map():
    """A list of nothing is an entry that cannot act, which is the same
    rule the forget entry follows — and it appears beside it, because the
    list is where a single app is forgotten."""
    assert m.POSITION_LIST not in entries()
    assert m.POSITION_LIST in entries(positions_remembered=True)
    assert m.POSITION_LIST in entries(
        positions_remembered=True, remembering_positions=False
    )


def test_forgetting_stays_reachable_with_the_layer_switched_off():
    """A bad map must be clearable without turning the feature back on to
    reach the control that clears it. The entry follows the map, not the
    toggle — visible_entries is never told whether the layer is on."""
    assert m.FORGET_POSITIONS in entries(positions_remembered=True)


def test_the_position_readout_appears_with_the_layer_and_not_the_map():
    """Learning is implicit, so this line is the only thing in the app that
    says what has been learned — and it is needed most when nothing has
    been, which is exactly when a user cannot tell the feature from a
    broken one. So it follows the toggle, not the map."""
    assert m.POSITION_STATUS not in entries()
    assert m.POSITION_STATUS in entries(remembering_positions=True)
    assert m.POSITION_STATUS in entries(
        remembering_positions=True, positions_remembered=True
    )


def test_the_position_readout_goes_when_the_layer_goes():
    """Unlike the forget entry, which follows the map so a bad one stays
    clearable. This one names the frontmost app, and with the layer off
    nothing is watching which app that is — a stale line is worse than no
    line, and going to look would be the watching that "off" ends."""
    assert m.POSITION_STATUS not in entries(positions_remembered=True)


def test_remembering_is_offered_before_anything_has_been_learned():
    """Like album colour: a standing preference about the window, so it
    cannot appear and vanish with what the app happens to know. The moment
    a user goes looking for it is before it has ever been used."""
    assert m.REMEMBER_POSITION in entries()


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
