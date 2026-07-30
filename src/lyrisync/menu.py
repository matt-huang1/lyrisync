"""Which settings-menu entries are shown, and in what order.

Pure logic, Qt-free like geometry.py. There is exactly one menu object in
the app, serving both the menu bar item and the window's right-click menu —
two separately built menus could drift apart; one cannot. Its structure is
built once from ``MENU_ORDER`` and never rebuilt, so the native menu bar
item is never reconstructed under the user; only visibility, check marks
and the sync label change, and this module decides the visibility half.
"""

from __future__ import annotations

SHOW_LYRICS = "show_lyrics"
ROMANISATION = "romanisation"
SPOKEN = "spoken"
SPEECH_RATE = "speech_rate"
ECHO = "echo"
ALBUM_COLOUR = "album_colour"
ALL_DESKTOPS = "all_desktops"
REMEMBER_POSITION = "remember_position"
POSITION_STATUS = "position_status"
POSITION_LIST = "position_list"
FORGET_POSITIONS = "forget_positions"
OPEN_AT_LOGIN = "open_at_login"
SYNC = "sync"
QUIT = "quit"

SEPARATOR_AFTER_SHOW = "separator:show"
SEPARATOR_BEFORE_QUIT = "separator:quit"
SEPARATORS = frozenset({SEPARATOR_AFTER_SHOW, SEPARATOR_BEFORE_QUIT})

MENU_ORDER = (
    SHOW_LYRICS,
    SEPARATOR_AFTER_SHOW,
    ROMANISATION,
    SPOKEN,
    SPEECH_RATE,
    ECHO,
    ALBUM_COLOUR,
    ALL_DESKTOPS,
    REMEMBER_POSITION,
    POSITION_STATUS,
    POSITION_LIST,
    FORGET_POSITIONS,
    OPEN_AT_LOGIN,
    SYNC,
    SEPARATOR_BEFORE_QUIT,
    QUIT,
)

# Always offered: the window can always be shown or hidden, the overlay can
# always change how it treats Spaces and how it takes its colour, and quit
# must never be unreachable — it is the only way out of an app with no Dock
# icon.
#
# Album colour is here rather than gated on a cover being available, unlike
# the learning layers below. Those hide because they cannot act — there is
# nothing to romanise without hangul. This one can always be answered: it
# is a standing preference about how the window looks, and appearing and
# vanishing as tracks came and went would make it hard to find at the
# moment the user wants it, which is before the music starts.
#
# Per-app position memory is here for the same reason: it is a standing
# preference about where the window lives, answerable whether or not
# anything is playing and whether or not any position has been learned
# yet. Its companion — forgetting what was learned — is NOT here, because
# that one genuinely cannot act on an empty map.
ALWAYS_VISIBLE = frozenset(
    {SHOW_LYRICS, ALBUM_COLOUR, ALL_DESKTOPS, REMEMBER_POSITION, QUIT}
)


def visible_entries(
    *,
    has_korean_lyrics: bool,
    speech_available: bool,
    synced: bool,
    sync_offered: bool,
    login_item_offered: bool = False,
    positions_remembered: bool = False,
    remembering_positions: bool = False,
) -> tuple[str, ...]:
    """The entries to show, in ``MENU_ORDER``, for this app state.

    Every learning layer stays hidden until it can actually do something:
    romanisation needs hangul under a current line, echo practice needs
    synced timestamps to loop, spoken reference needs the macOS voice
    installed, and tap-to-sync needs lines to stamp. With every layer
    dormant the menu is just show/hide, Spaces, and quit.

    Open at Login is the one entry gated on how the app was launched
    rather than on what the song is: there is nothing for macOS to start
    at login when the app is running from a source checkout, so offering
    the switch there would be offering something that cannot work.

    The list of remembered apps and the entry that forgets all of them
    both appear once there is something to forget, and disappear again the
    moment there is not — including when the layer itself is switched off,
    because a bad map should be clearable without turning the feature back
    on first. They follow the map for the same reason: a list of nothing
    and a way to clear nothing are both entries that cannot act.

    The position readout is shown only while the layer is ON, and unlike
    every other entry here that is not about whether it could act. It is
    about what the app is entitled to say: with the layer off nothing is
    watching which app is in front, so a line naming the frontmost app
    would either be stale or would mean going and looking — and going and
    looking with the layer off is exactly what "off" promises not to do.
    """
    shown = set(ALWAYS_VISIBLE)
    if has_korean_lyrics:
        shown.add(ROMANISATION)
    if speech_available:
        shown.update((SPOKEN, SPEECH_RATE))
    if synced:
        shown.add(ECHO)
    if sync_offered:
        shown.add(SYNC)
    if login_item_offered:
        shown.add(OPEN_AT_LOGIN)
    if positions_remembered:
        shown.update((POSITION_LIST, FORGET_POSITIONS))
    if remembering_positions:
        shown.add(POSITION_STATUS)
    return _with_separators(shown)


def _with_separators(shown: set[str]) -> tuple[str, ...]:
    """Drop separators that no longer divide anything — leading, trailing,
    or doubled up once the entries between them are hidden."""
    entries: list[str] = []
    for key in MENU_ORDER:
        if key in SEPARATORS:
            if entries and entries[-1] not in SEPARATORS:
                entries.append(key)
        elif key in shown:
            entries.append(key)
    while entries and entries[-1] in SEPARATORS:
        entries.pop()
    return tuple(entries)
