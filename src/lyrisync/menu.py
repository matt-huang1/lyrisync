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
ALL_DESKTOPS = "all_desktops"
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
    ALL_DESKTOPS,
    SYNC,
    SEPARATOR_BEFORE_QUIT,
    QUIT,
)

# Always offered: the window can always be shown or hidden, the overlay can
# always change how it treats Spaces, and quit must never be unreachable —
# it is the only way out of an app with no Dock icon.
ALWAYS_VISIBLE = frozenset({SHOW_LYRICS, ALL_DESKTOPS, QUIT})


def visible_entries(
    *,
    has_korean_lyrics: bool,
    speech_available: bool,
    synced: bool,
    sync_offered: bool,
) -> tuple[str, ...]:
    """The entries to show, in ``MENU_ORDER``, for this app state.

    Every learning layer stays hidden until it can actually do something:
    romanisation needs hangul under a current line, echo practice needs
    synced timestamps to loop, spoken reference needs the macOS voice
    installed, and tap-to-sync needs lines to stamp. With every layer
    dormant the menu is just show/hide, Spaces, and quit.
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
