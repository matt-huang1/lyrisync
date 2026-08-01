TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce import menu as m


ALL_LAYERS = dict(
    has_korean_lyrics=True,
    speech_available=True,
    synced=True,
    sync_offered=True,
    login_item_offered=True,
    positions_remembered=True,
    remembering_positions=True,
    compact=True,
)
NO_LAYERS = dict(
    has_korean_lyrics=False,
    speech_available=False,
    synced=False,
    sync_offered=False,
    login_item_offered=False,
    positions_remembered=False,
    remembering_positions=False,
    compact=False,
)


def entries(**overrides):
    return m.visible_entries(**{**NO_LAYERS, **overrides})


def children(key):
    """The keys inside a submenu, in the order they are declared."""
    return tuple(entry.key for entry in m.ENTRIES[key].children)


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
    standing choices about how the window looks, the two submenus and quit.
    Forgetting learned positions is not among them — there is nothing to
    forget until something has been learned.

    Four rows and two submenus at the top level, which is what the grouping
    is for: the same set of standing preferences that used to be eight
    entries in one column."""
    assert without_separators(entries()) == (
        m.SHOW_LYRICS,
        m.COMPACT,
        m.ALBUM_COLOUR,
        m.POSITION_MENU,
        m.DOCK_TOP,
        m.REMEMBER_POSITION,
        m.SYSTEM_MENU,
        m.ALL_DESKTOPS,
        m.YIELD_NOTIFICATIONS,
        m.PROXIMITY,
        m.MENUBAR_ANIMATION,
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


def test_the_standing_preferences_live_in_the_two_submenus():
    """The grouping, stated as the property it is for: everything about how
    the app behaves rather than about the song is inside one of two
    submenus, and nothing about the song is.

    Position answers "where does the window go and where does it live";
    System answers "how does the app sit in the system". They are the long
    tail, and the top level is what is reached for.
    """
    assert children(m.POSITION_MENU) == (
        m.DOCK_TOP,
        m.SEPARATOR_AFTER_DOCK,
        m.REMEMBER_POSITION,
        m.POSITION_STATUS,
        m.POSITION_LIST,
        m.FORGET_POSITIONS,
    )
    assert children(m.SYSTEM_MENU) == (
        m.ALL_DESKTOPS,
        m.YIELD_NOTIFICATIONS,
        m.PROXIMITY,
        m.MENUBAR_ANIMATION,
        m.SEPARATOR_BEFORE_LOGIN,
        m.OPEN_AT_LOGIN,
    )


def test_nothing_about_the_song_is_buried_in_a_submenu():
    """The half of the grouping that is a promise rather than a layout: an
    entry that comes and goes with the song is one somebody is looking for
    NOW, and a submenu is one more click and one more place to look."""
    top = tuple(entry.key for entry in m.MENU)
    for key in (m.ROMANISATION, m.SPOKEN, m.ECHO, m.SYNC, m.SHOW_LYRICS):
        assert key in top


def test_a_submenu_goes_when_everything_inside_it_has():
    """A submenu holding nothing is an entry that cannot act, which is the
    same rule its contents follow."""
    assert m.POSITION_MENU in m._with_separators({m.DOCK_TOP})
    assert m.POSITION_MENU not in m._with_separators({m.QUIT})
    assert m.SYSTEM_MENU not in m._with_separators({m.QUIT})


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


# -- the strip's own entries ----------------------------------------------


def test_the_strips_two_settings_appear_only_inside_the_strip():
    """Both are about a type size the full layout does not have: there the
    size IS the width, so a control for it would be a second answer to the
    same question."""
    assert m.COMPACT_SIZE not in entries()
    assert m.FIT_TO_SONG not in entries()
    assert m.COMPACT_SIZE in entries(compact=True)
    assert m.FIT_TO_SONG in entries(compact=True)


def test_the_compact_switch_itself_is_always_reachable():
    """Its two dependants come and go with it; the switch cannot, or there
    would be no way back into the layout that offers them."""
    assert m.COMPACT in entries()


def test_the_size_sits_with_the_layout_it_belongs_to():
    """Directly under the switch that turns the layout on, and above the
    fit that measures against it, which is also the order they depend on
    each other in."""
    order = m.MENU_ORDER
    assert order.index(m.COMPACT) + 1 == order.index(m.COMPACT_SIZE)
    assert order.index(m.COMPACT_SIZE) + 1 == order.index(m.FIT_TO_SONG)


# -- the model itself -----------------------------------------------------


def test_every_entry_is_reachable_and_named_once():
    """A key that appears twice in the tree would be two entries the window
    could only address one of, and the flattening would hide which."""
    assert len(m.MENU_ORDER) == len(set(m.MENU_ORDER))
    assert set(m.ENTRIES) == set(m.MENU_ORDER)


def test_every_entry_a_person_reads_has_something_to_read():
    """Except the readout, whose text the app writes on every refresh, and
    the separators, which are not entries so much as gaps."""
    for key, entry in m.ENTRIES.items():
        if entry.kind in (m.SEPARATOR, m.READOUT):
            continue
        assert entry.label, key


def test_the_presets_come_from_where_they_are_defined():
    """One definition: the sizes belong to typography and the rates to
    speech, and a second copy here is how a menu comes to offer a preset
    the app does not have."""
    from sottovoce.speech import SPEECH_RATE_PRESETS
    from sottovoce.typography import COMPACT_TEXT_SIZES

    assert m.ENTRIES[m.COMPACT_SIZE].options == COMPACT_TEXT_SIZES
    assert m.ENTRIES[m.SPEECH_RATE].options == SPEECH_RATE_PRESETS
    assert m.ENTRIES[m.COMPACT_SIZE].option_label.format(20) == "20 pt"
    assert m.ENTRIES[m.SPEECH_RATE].option_label.format(120) == "120 wpm"


def test_the_login_label_is_the_login_modules_own():
    from sottovoce import login_item

    assert m.ENTRIES[m.OPEN_AT_LOGIN].label == login_item.MENU_LABEL


# -- what a click does ----------------------------------------------------


def test_a_toggle_is_handed_the_state_it_is_moving_to():
    """Not the state it is in, and not a tick that moved itself. The handler
    changes the app and the refresh that follows says what the app now is,
    which is the same rule the QMenu entries followed by connecting to
    triggered rather than toggled."""
    menu = m.Menu()
    seen = []
    menu.on(m.COMPACT, seen.append)

    menu.trigger(m.COMPACT)
    assert seen == [True]

    menu.set_checked(m.COMPACT, True)
    menu.trigger(m.COMPACT)
    assert seen == [True, False]


def test_a_click_never_moves_the_tick_by_itself():
    """The whole of why toggled was wrong: a refresh sets every check mark,
    and an entry that also set its own would be a second answer to what the
    setting is."""
    menu = m.Menu()
    menu.on(m.COMPACT, lambda enabled: None)
    menu.trigger(m.COMPACT)
    assert menu.is_checked(m.COMPACT) is False


def test_a_choice_is_handed_the_preset_that_was_clicked():
    menu = m.Menu()
    seen = []
    menu.on(m.SPEECH_RATE, seen.append)
    menu.trigger(m.SPEECH_RATE, 160)
    assert seen == [160]


def test_a_command_is_handed_nothing():
    menu = m.Menu()
    seen = []
    menu.on(m.DOCK_TOP, lambda: seen.append("docked"))
    menu.trigger(m.DOCK_TOP)
    assert seen == ["docked"]


def test_a_click_with_nothing_wired_to_it_lands_nowhere():
    """The readout and the rows are facts rather than controls, so this is
    the case that has to be quiet rather than the case that has to raise."""
    m.Menu().trigger(m.POSITION_STATUS)


def test_wiring_a_key_that_does_not_exist_is_an_error():
    """A typo in a handler name is otherwise a setting that silently stops
    working."""
    menu = m.Menu()
    with pytest.raises(KeyError):
        menu.on("no_such_entry", lambda: None)
    with pytest.raises(KeyError):
        menu.on_open("no_such_entry", lambda: None)


# -- state, and what is drawn from it -------------------------------------


def test_a_label_is_the_entrys_own_until_something_changes_it():
    menu = m.Menu()
    assert menu.label(m.SYNC) == "Sync this song"
    menu.set_label(m.SYNC, "Re-sync this song")
    assert menu.label(m.SYNC) == "Re-sync this song"


def test_visibility_arrives_whole_rather_than_one_entry_at_a_time():
    """It is decided in one place by pure logic, and handing it over whole
    is what stops the model and that logic disagreeing."""
    menu = m.Menu()
    assert menu.is_visible(m.QUIT) is False
    menu.show_only(m.visible_entries(**NO_LAYERS))
    assert menu.is_visible(m.QUIT) is True
    assert menu.is_visible(m.ECHO) is False


def test_a_view_is_told_the_moment_it_is_attached():
    """Or the first opening would be the first refresh, and a menu bar item
    would be right only after somebody had already read it wrong."""

    class View:
        def __init__(self):
            self.applied = 0

        def apply(self, menu):
            self.applied += 1

        def set_rows(self, key, rows):
            pass

        def popup(self, x, y):
            return True

    view = View()
    menu = m.Menu()
    menu.attach(view)
    assert view.applied == 1
    assert menu.view is view


def test_rows_reach_the_view_as_they_are_set():
    """The one part of the menu that is data rather than structure, so the
    one part that is rebuilt rather than relabelled."""

    class View:
        def __init__(self):
            self.rows = None

        def apply(self, menu):
            pass

        def set_rows(self, key, rows):
            self.rows = (key, rows)

        def popup(self, x, y):
            return False

    view = View()
    menu = m.Menu()
    menu.attach(view)
    menu.set_rows(m.POSITION_LIST, [m.Row("Safari", b"tiff")])
    assert menu.rows(m.POSITION_LIST) == (m.Row("Safari", b"tiff"),)
    assert view.rows == (m.POSITION_LIST, (m.Row("Safari", b"tiff"),))


def test_a_row_carries_bytes_or_nothing():
    """Nothing pyobjc-shaped and nothing Qt-shaped crosses into the model,
    which is what lets a row be built and asserted on a machine with
    neither."""
    assert m.Row("Safari").icon is None
    assert m.Row("Safari", b"tiff").icon == b"tiff"


def test_opening_a_menu_reaches_the_handler_for_that_menu():
    """Two things need this and neither can be done honestly on a timer:
    what macOS says about the login item, and a list that is data."""
    menu = m.Menu()
    opened = []
    menu.on_open(None, lambda: opened.append("root"))
    menu.on_open(m.POSITION_LIST, lambda: opened.append("rows"))

    menu.opening()
    menu.opening(m.POSITION_LIST)
    menu.opening(m.SYSTEM_MENU)  # nothing wired to it, and that is quiet

    assert opened == ["root", "rows"]


def test_an_entry_with_nothing_wired_to_it_says_so():
    """The readout and the rows state facts. That there is nothing for a
    click on them to reach is a claim about the app, not about the wiring:
    per-app forget was REMOVED, it was not left unconnected."""
    menu = m.Menu()
    menu.on(m.QUIT, lambda: None)
    assert menu.has_handler(m.QUIT) is True
    assert menu.has_handler(m.POSITION_STATUS) is False
    assert menu.has_handler(m.POSITION_LIST) is False
