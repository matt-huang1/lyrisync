"""The settings menu: what is in it, what is shown, and where a click lands.

Pure logic, Qt-free like geometry.py, and now Cocoa-free as well. There is
exactly one menu in the app, serving both the menu bar item and the
window's right-click menu. Until milestone 21 "one menu" meant one
``QMenu``, which was one OBJECT but two appearances: Qt hands a system
tray's menu to macOS and it comes back a real NSMenu, while the same object
popped up under the pointer is drawn by Qt's own style. The same entries
looked like two different menus depending on which way you opened them.

So the menu is now a MODEL — the tree below — and ``nsmenu.py`` renders it
as a native NSMenu that both routes use. This module knows nothing about
either: it holds the structure, the labels, the gating and the live state,
and a click arriving from anywhere lands in ``trigger``. That is what lets
the whole of it be tested on a machine with no menu bar at all.

## The shape of it

Seventeen entries in one flat column had become a list to read rather than
a menu to use. The tree groups them by what they are about:

- what is on screen, at the top, because it is what is reached for most:
  show/hide, the strip and its two settings, the album tint;
- then what is about THIS SONG, which is also everything that hides itself
  when it cannot act: romanisation, the spoken reference and its rate,
  echo practice, tap-to-sync;
- then two submenus for the standing preferences, which are the long tail:
  **Position** (where the window goes and where it lives per app) and
  **System** (Spaces, notifications, the menu bar item, login);
- then quit, which is visible in every state because the menu bar item is
  the way back from a hidden window.

A submenu is shown only when something inside it is, and separators
collapse the same way they always did, so the gating rules did not change
when the shape did.
"""

from __future__ import annotations

from typing import Callable, NamedTuple, Optional

from sottovoce.login_item import MENU_LABEL as OPEN_AT_LOGIN_LABEL
from sottovoce.proximity import MODES as PROXIMITY_MODES
from sottovoce.speech import SPEECH_RATE_PRESETS
from sottovoce.typography import COMPACT_TEXT_SIZES

SHOW_LYRICS = "show_lyrics"
ROMANISATION = "romanisation"
SPOKEN = "spoken"
SPEECH_RATE = "speech_rate"
ECHO = "echo"
COMPACT = "compact"
COMPACT_SIZE = "compact_size"
FIT_TO_SONG = "fit_to_song"
ALBUM_COLOUR = "album_colour"
ALL_DESKTOPS = "all_desktops"
MENUBAR_ANIMATION = "menubar_animation"
YIELD_NOTIFICATIONS = "yield_notifications"
PROXIMITY = "proximity"
DOCK_TOP = "dock_top"
REMEMBER_POSITION = "remember_position"
POSITION_STATUS = "position_status"
POSITION_LIST = "position_list"
FORGET_POSITIONS = "forget_positions"
OPEN_AT_LOGIN = "open_at_login"
SYNC = "sync"
PASTE_SYNC = "paste_sync"
PUBLISH = "publish"
PUBLISH_STATUS = "publish_status"
QUIT = "quit"

# The two submenus. They are entries like any other, which is what lets one
# gating rule cover both them and their contents.
POSITION_MENU = "position_menu"
SYSTEM_MENU = "system_menu"

SEPARATOR_AFTER_SHOW = "separator:show"
SEPARATOR_AFTER_WINDOW = "separator:window"
SEPARATOR_AFTER_SONG = "separator:song"
SEPARATOR_AFTER_DOCK = "separator:dock"
SEPARATOR_BEFORE_LOGIN = "separator:login"
SEPARATOR_BEFORE_QUIT = "separator:quit"


# -- what an entry is -----------------------------------------------------

TOGGLE = "toggle"  # a tick: the state is the setting
COMMAND = "command"  # does something once, no state to describe
CHOICE = "choice"  # a submenu of mutually exclusive presets
SUBMENU = "submenu"  # a submenu of other entries
READOUT = "readout"  # a line of text, not a control
ROWS = "rows"  # a submenu whose contents are data, not structure
SEPARATOR = "separator"


class Entry(NamedTuple):
    """One row of the menu, or one submenu of them.

    ``label`` is what a person reads and is the only copy of it: the native
    menu takes its title from here, and so does every test that asserts
    what the menu says.

    ``options`` and ``option_label`` belong to CHOICE entries, whose rows
    are generated from a tuple of presets that lives with the thing it
    configures (``speech.SPEECH_RATE_PRESETS``,
    ``typography.COMPACT_TEXT_SIZES``) rather than being spelled a second
    time here.
    """

    key: str
    label: str = ""
    kind: str = COMMAND
    children: tuple = ()
    options: tuple = ()
    option_label: str = "{}"


class Row(NamedTuple):
    """One line of a ROWS submenu: what it says, and the icon beside it.

    ``icon`` is TIFF bytes or None. Bytes rather than anything Cocoa-shaped,
    for the reason frontmost.py gives about ``app_icon_tiff``: nothing
    native crosses into a pure module, so the whole of this can be built
    and asserted on a machine with no AppKit.
    """

    label: str
    icon: Optional[bytes] = None


MENU = (
    Entry(SHOW_LYRICS, "Show lyrics", TOGGLE),
    Entry(SEPARATOR_AFTER_SHOW, kind=SEPARATOR),
    # What is on screen. The strip's two settings sit directly under the
    # switch that turns the layout on, which is also the order they depend
    # on each other in: the size decides the type, and the fit measures
    # against it.
    Entry(COMPACT, "Compact", TOGGLE),
    Entry(
        COMPACT_SIZE,
        "Compact text size",
        CHOICE,
        options=COMPACT_TEXT_SIZES,
        option_label="{} pt",
    ),
    Entry(FIT_TO_SONG, "Fit the width to the song", TOGGLE),
    Entry(ALBUM_COLOUR, "Album colour", TOGGLE),
    Entry(SEPARATOR_AFTER_WINDOW, kind=SEPARATOR),
    # This song. Every one of these hides itself when it cannot act, so
    # this whole group and the separator above it disappear together on a
    # machine with nothing playing.
    Entry(ROMANISATION, "Romanisation", TOGGLE),
    Entry(SPOKEN, "Spoken reference", TOGGLE),
    Entry(
        SPEECH_RATE,
        "Speech rate",
        CHOICE,
        options=SPEECH_RATE_PRESETS,
        option_label="{} wpm",
    ),
    Entry(ECHO, "Echo practice", TOGGLE),
    # Label swaps between "Sync" and "Re-sync" once a user sync exists.
    Entry(SYNC, "Sync this song", COMMAND),
    # The same act with the lines brought from somewhere else, and it
    # appears exactly where the entry above cannot: a song whose lyrics
    # could not be had. The two are never visible together.
    Entry(PASTE_SYNC, "Paste lyrics to sync…", COMMAND),
    # Under the entry that makes a sync, because it is the next thing that
    # can be done with one and because it can only ever appear where one
    # exists. The ellipsis is the platform's promise that a press opens
    # something to read rather than doing the thing, which here is the
    # whole of the design: nothing is sent from this menu.
    Entry(PUBLISH, "Publish this sync to LRCLIB…", COMMAND),
    # And the fact that replaces it once the sync has gone. A row that
    # states a fact rather than a control, so it is drawn as one.
    Entry(PUBLISH_STATUS, "Published to LRCLIB", READOUT),
    Entry(SEPARATOR_AFTER_SONG, kind=SEPARATOR),
    # Where the window goes, and where it lives. Docking is a command and
    # per-app memory is a layer, and they are one submenu because they are
    # one question: a user looking for either is looking for "where does
    # this thing sit".
    Entry(
        POSITION_MENU,
        "Position",
        SUBMENU,
        children=(
            Entry(DOCK_TOP, "Dock to top", COMMAND),
            Entry(SEPARATOR_AFTER_DOCK, kind=SEPARATOR),
            Entry(REMEMBER_POSITION, "Remember position per app", TOGGLE),
            Entry(POSITION_STATUS, "", READOUT),
            Entry(POSITION_LIST, "Remembered apps", ROWS),
            Entry(FORGET_POSITIONS, "Forget remembered positions", COMMAND),
        ),
    ),
    # How the app sits in the system. None of these is about the song and
    # none of them can be answered by looking at one.
    Entry(
        SYSTEM_MENU,
        "System",
        SUBMENU,
        children=(
            Entry(ALL_DESKTOPS, "Show on all desktops", TOGGLE),
            Entry(YIELD_NOTIFICATIONS, "Yield to notifications", TOGGLE),
            # Directly under the yield, because it is the same sentence
            # said about a different intruder: that one gets out of the
            # way of a banner, this one gets out of the way of the hand.
            Entry(
                PROXIMITY,
                "Yield to the pointer",
                CHOICE,
                options=PROXIMITY_MODES,
                option_label="{}",
            ),
            Entry(MENUBAR_ANIMATION, "Animate the menu bar icon", TOGGLE),
            Entry(SEPARATOR_BEFORE_LOGIN, kind=SEPARATOR),
            # Label swaps to name the approval case.
            Entry(OPEN_AT_LOGIN, OPEN_AT_LOGIN_LABEL, TOGGLE),
        ),
    ),
    Entry(SEPARATOR_BEFORE_QUIT, kind=SEPARATOR),
    Entry(QUIT, "Quit", COMMAND),
)


def _flatten(entries: tuple) -> tuple:
    """Every entry in the tree, depth first, parents before children."""
    found = []
    for entry in entries:
        found.append(entry)
        found.extend(_flatten(entry.children))
    return tuple(found)


ENTRIES = {entry.key: entry for entry in _flatten(MENU)}
MENU_ORDER = tuple(entry.key for entry in _flatten(MENU))
SEPARATORS = frozenset(
    key for key, entry in ENTRIES.items() if entry.kind == SEPARATOR
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
#
# Yielding to notifications, likewise: a standing preference about how the
# window behaves when it is in somebody's way, which has nothing to do with
# what is playing and everything to do with the window. Gating it on a
# notification being on screen right now would offer it for five seconds at
# a time, which is not a way to find a setting.
#
# Yielding to the pointer is the same argument once more, and the reductio
# of it: gating that one on the pointer being over the window would mean
# the only way to find the setting is to be in the state it exists to
# change, and reaching the menu bar item to look would leave that state.
# The menu bar animation is here too, and it is the same argument once more:
# a standing preference about how the item behaves, not about the song. It
# only has anything to do while a song is playing, but a toggle that appeared
# and vanished with playback would be one nobody could find when they wanted
# it — which is, as ever, before the music starts.
#
# The compact layout and docking to the top are the same argument a third
# and fourth time. They no longer sit next to each other, and that is the
# one thing milestone 21 changed about the gating: the pairing was an
# argument about VISIBILITY, and it still holds — both are answerable with
# nothing playing, and both are here. Where they sit is a different
# question, and docking answers "where does the window go", which is what
# the Position submenu is.
ALWAYS_VISIBLE = frozenset(
    {
        SHOW_LYRICS,
        COMPACT,
        ALBUM_COLOUR,
        ALL_DESKTOPS,
        MENUBAR_ANIMATION,
        YIELD_NOTIFICATIONS,
        PROXIMITY,
        DOCK_TOP,
        REMEMBER_POSITION,
        QUIT,
    }
)


def visible_entries(
    *,
    has_korean_lyrics: bool,
    speech_available: bool,
    synced: bool,
    sync_offered: bool,
    paste_sync_offered: bool = False,
    publish_offered: bool = False,
    sync_published: bool = False,
    login_item_offered: bool = False,
    positions_remembered: bool = False,
    remembering_positions: bool = False,
    compact: bool = False,
) -> tuple[str, ...]:
    """The entries to show, in ``MENU_ORDER``, for this app state.

    Every learning layer stays hidden until it can actually do something:
    romanisation needs hangul under a current line, echo practice needs
    synced timestamps to loop, spoken reference needs the macOS voice
    installed, and tap-to-sync needs lines to stamp. With every layer
    dormant the menu is show/hide, the two standing choices about how the
    window looks, the two submenus and quit.

    Pasting lyrics to sync is the mirror of that last one and the two are
    mutually exclusive by construction: it is offered only where there are
    no lines to stamp and the song could still have some. A song with
    lyrics shows "Sync this song"; a song without shows the way to bring
    some. Neither ever shows both, so the group gains a row rather than the
    menu gaining a column.

    Publishing a sync is gated hardest of anything here, and every part of
    that gate is somebody else's rule rather than this app's taste: there
    has to be a sync, LRCLIB has to be holding the words for this song and
    no timings, and the same sync must not have been sent already. The
    reason it is not offered is ``publish.standing_refusal``'s, and the
    entry appearing at all is derived from that reason.

    The line saying it HAS been published takes its place, and the two can
    never both be shown, because the second is exactly the case the first
    refuses for. It states a fact rather than offering an act, so it is a
    readout: there is nothing to do about a sync that has gone.

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

    Fitting the window to the song follows the compact layout, because it
    is the only place it means anything: the full layout's width is the
    user's and stays theirs. It is the one entry here whose own default is
    ON, and it can be, because it is reachable only from inside a layout
    that is itself default-off. The plain window is unchanged either way.

    The strip's text size follows it for the same reason and pays the same
    way: in the full layout the type size IS the width, so there would be
    nothing for a second control to say.
    """
    shown = set(ALWAYS_VISIBLE)
    if compact:
        shown.update((COMPACT_SIZE, FIT_TO_SONG))
    if has_korean_lyrics:
        shown.add(ROMANISATION)
    if speech_available:
        shown.update((SPOKEN, SPEECH_RATE))
    if synced:
        shown.add(ECHO)
    if sync_offered:
        shown.add(SYNC)
    if paste_sync_offered:
        shown.add(PASTE_SYNC)
    if publish_offered:
        shown.add(PUBLISH)
    if sync_published:
        shown.add(PUBLISH_STATUS)
    if login_item_offered:
        shown.add(OPEN_AT_LOGIN)
    if positions_remembered:
        shown.update((POSITION_LIST, FORGET_POSITIONS))
    if remembering_positions:
        shown.add(POSITION_STATUS)
    return _resolve(MENU, shown)


def _resolve(entries: tuple, shown: set) -> tuple[str, ...]:
    """One level of the tree, with its separators collapsed and its empty
    submenus dropped, then the levels below it.

    Recursive because a submenu is a menu: a group whose entries have all
    gone takes its own separators with it, and a submenu holding only
    those is itself an entry that cannot act.
    """
    keep: list = []
    for entry in entries:
        if entry.kind == SEPARATOR:
            if keep and keep[-1][0].kind != SEPARATOR:
                keep.append((entry, ()))
        elif entry.children:
            inside = _resolve(entry.children, shown)
            if inside:
                keep.append((entry, inside))
        elif entry.key in shown:
            keep.append((entry, ()))
    while keep and keep[-1][0].kind == SEPARATOR:
        keep.pop()
    flat: list = []
    for entry, inside in keep:
        flat.append(entry.key)
        flat.extend(inside)
    return tuple(flat)


def _with_separators(shown: set) -> tuple[str, ...]:
    """The collapse rule on the top level alone, kept as its own name
    because it is the property worth asserting directly: a separator that
    no longer divides anything is not drawn."""
    return _resolve(MENU, shown)


# -- the live menu --------------------------------------------------------


class Menu:
    """One menu's structure, its current state, and where a click lands.

    Built once and never rebuilt — refreshing only flips visibility, check
    marks, chosen presets and labels — because a native menu bar item
    whose structure changes is one that flickers under the user while they
    are reading it.

    Nothing here is a widget, and that is the point: the window drives this
    and this drives whatever is rendering it. A ``view`` is anything with
    ``apply``, ``set_rows`` and ``popup``; in the app it is
    ``nsmenu.NativeMenu``, and off macOS it is nothing at all, which is the
    same code path with the drawing left out.
    """

    def __init__(self, entries: tuple = MENU) -> None:
        self.entries = entries
        self._handlers: dict = {}
        self._openers: dict = {}
        self._visible: set = set()
        self._checked: dict = {}
        self._labels: dict = {}
        self._chosen: dict = {}
        self._rows: dict = {}
        self._icons: dict = {}
        self._view = None

    # -- wiring ------------------------------------------------------------

    def has_handler(self, key: str) -> bool:
        """Whether a click on this entry reaches anything.

        False is the interesting answer and it is a claim about the app
        rather than about the wiring: the readout and the remembered-apps
        rows state facts, and per-app forget was removed rather than left
        unconnected, so there is nothing for a click on either to reach.
        """
        return key in self._handlers

    def on(self, key: str, handler: Callable) -> None:
        """What a click on ``key`` does.

        A CHOICE entry's handler is called with the preset that was
        clicked; everything else is called with the state the entry is
        moving TO for a toggle, and with nothing for a command.
        """
        if key not in ENTRIES:
            raise KeyError(f"no menu entry named {key!r}")
        self._handlers[key] = handler

    def on_open(self, key: Optional[str], handler: Callable) -> None:
        """What happens the moment a menu is put on screen.

        ``None`` is the menu itself; a key is one of its submenus. Two
        things need this and neither can be done honestly on a timer: what
        macOS says about the login item has to be re-read at the moment
        somebody looks at it, and the list of remembered apps is data
        rather than structure and is assembled only while it is being
        read.
        """
        if key is not None and key not in ENTRIES:
            raise KeyError(f"no menu entry named {key!r}")
        self._openers[key] = handler

    def opening(self, key: Optional[str] = None) -> None:
        """A menu is about to be shown. The one way in, from anywhere."""
        handler = self._openers.get(key)
        if handler is not None:
            handler()

    def attach(self, view) -> None:
        """Hand the model to something that can draw it, and bring it up to
        date at once so the first opening is not the first refresh."""
        self._view = view
        self.sync()

    @property
    def view(self):
        return self._view

    # -- what the entries say ---------------------------------------------

    def label(self, key: str) -> str:
        """What the entry reads, which is its own label until something
        changes it (the sync entry, the login item's approval case)."""
        return self._labels.get(key, ENTRIES[key].label)

    def set_label(self, key: str, text: str) -> None:
        self._labels[key] = text

    def is_visible(self, key: str) -> bool:
        return key in self._visible

    def show_only(self, keys) -> None:
        """The whole visible set at once, from ``visible_entries``.

        One call rather than a setter per entry, because visibility is
        decided in one place by pure logic and handing it over whole is
        what stops the two disagreeing.
        """
        self._visible = set(keys)

    def is_checked(self, key: str) -> bool:
        return bool(self._checked.get(key))

    def set_checked(self, key: str, checked: bool) -> None:
        self._checked[key] = bool(checked)

    def chosen(self, key: str):
        """Which preset of a CHOICE entry is ticked."""
        return self._chosen.get(key)

    def set_chosen(self, key: str, value) -> None:
        self._chosen[key] = value

    def icon(self, key: str) -> Optional[bytes]:
        return self._icons.get(key)

    def set_icon(self, key: str, icon: Optional[bytes]) -> None:
        self._icons[key] = icon

    def rows(self, key: str) -> tuple:
        return self._rows.get(key, ())

    def set_rows(self, key: str, rows) -> None:
        """The contents of a ROWS submenu.

        The one part of the menu whose entries are DATA rather than
        structure, so this is the one part that is rebuilt rather than
        relabelled — and it is rebuilt only when the submenu is opened,
        never while the menu bar item sits idle.
        """
        self._rows[key] = tuple(rows)
        if self._view is not None:
            self._view.set_rows(key, self._rows[key])

    # -- what a click does -------------------------------------------------

    def trigger(self, key: str, value=None) -> None:
        """A click landed on ``key``.

        The one way in, whether the click came from the menu bar item, from
        the window's right-click menu, or from a test. Nothing is checked
        or unchecked here, and that is the whole of what protects the rule:
        this method READS ``is_checked`` to work out what the handler is
        being asked for, and the only thing that ever WRITES it is the
        refresh, from the app's own state. One writer, and it is not the
        click. A tick that moved itself would be a second answer to what
        the setting is.
        """
        handler = self._handlers.get(key)
        if handler is None:
            return
        entry = ENTRIES[key]
        if entry.kind == CHOICE:
            handler(value)
        elif entry.kind == TOGGLE:
            handler(not self.is_checked(key))
        else:
            handler()

    def popup(self, x: float, y: float) -> bool:
        """Open the menu at a point on screen. False when there is nothing
        to draw it with, which off macOS is every time."""
        if self._view is None:
            return False
        return bool(self._view.popup(x, y))

    def sync(self) -> None:
        """Push the current state at whatever is drawing it."""
        if self._view is not None:
            self._view.apply(self)
