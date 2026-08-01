"""A click on a native menu item, and the state it produces.

The menu was covered in two halves that never met. On one side,
test_nsmenu.py checks that AppKit is imported in exactly one place and that
everything answers with the door shut — which is all it can check, because
the door is shut for the whole session. On the other, test_window_menu.py
drives the app's setters directly and asks what the state became. Nothing
joined them: the tag table an NSMenuItem carries, the selector every click
arrives through, ``Menu.trigger`` deciding which state a toggle is moving
TO, and the refresh that puts the tick back afterwards were four things
each covered alone.

So the door is ANSWERED here rather than blocked, with a stand-in for
AppKit — the same shape as the four doors tests/window/conftest.py already
answers with None, one step further along: None is the branch a machine
with no AppKit takes, and this is the branch a Mac takes, with the drawing
replaced by objects that record what they were told. Everything above it is
the real thing. The window builds its own ``NativeMenu``, the real
``_build_item`` arms each item with a real tag, and a click is delivered
the way Cocoa delivers one: to ``fire_``, with the item as the sender.

What that buys is one rule asserted end to end rather than reasoned about:
nothing checks or unchecks an entry from a click. The handler changes the
app's state, the refresh that follows says what the state now is, and the
tick on the native item is read back from the item.

Every acting entry is here. Not a sample, and not a list that can quietly
fall behind either: the last test in the file asks the model which entries
have a handler and fails if one of them is not covered above.
"""

TIER = "integration"  # a click, the model, the window, and the tick coming back

import pytest

from PySide6.QtCore import QTimer

from sottovoce import login_item
from sottovoce import menu as m
from sottovoce import nsmenu
from sottovoce import proximity
from sottovoce import speech
from sottovoce import typography
from sottovoce import window as w

from helpers import APP, PLAIN, SYNCED, land, load


# -- AppKit, with the drawing left out -------------------------------------


class Cocoa:
    """Base for the stand-ins: ``alloc()``/``init()`` the way pyobjc spells
    a constructor, so nsmenu's own code is what builds these."""

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


class Size:
    def __init__(self, width=0.0, height=0.0):
        self.width, self.height = width, height


class Rect:
    def __init__(self, x=0.0, y=0.0, width=0.0, height=0.0):
        self.origin = Size(x, y)
        self.origin.x, self.origin.y = x, y
        self.size = Size(width, height)


class FakeItem(Cocoa):
    """One NSMenuItem: its title, its tag, its tick and whether it is
    hidden. Everything a refresh writes, kept so it can be read back."""

    def __init__(self):
        self.title_text = ""
        self.tag_value = -1
        self.submenu_menu = None
        self.state = nsmenu.STATE_OFF
        self.hidden = False
        self.enabled = True
        self.image = None
        self.view = None
        self.target = None
        self.action = None
        self.separator = False

    @classmethod
    def separatorItem(cls):
        item = cls()
        item.separator = True
        return item

    def initWithTitle_action_keyEquivalent_(self, title, action, key):
        self.title_text = title
        return self

    def setTitle_(self, text):
        self.title_text = text

    def setTag_(self, tag):
        self.tag_value = tag

    def tag(self):
        return self.tag_value

    def setSubmenu_(self, submenu):
        self.submenu_menu = submenu

    def submenu(self):
        return self.submenu_menu

    def setState_(self, state):
        self.state = state

    def setHidden_(self, hidden):
        self.hidden = hidden

    def setEnabled_(self, enabled):
        self.enabled = enabled

    def setImage_(self, image):
        self.image = image

    def setView_(self, view):
        self.view = view

    def setTarget_(self, target):
        self.target = target

    def setAction_(self, action):
        self.action = action


class FakeMenu(Cocoa):
    def __init__(self):
        self.items = []
        self.delegate = None
        self.autoenables = True
        self.popups = []

    def setAutoenablesItems_(self, value):
        self.autoenables = value

    def addItem_(self, item):
        self.items.append(item)

    def itemAtIndex_(self, index):
        return self.items[index]

    def removeAllItems(self):
        self.items = []

    def setDelegate_(self, delegate):
        self.delegate = delegate

    def popUpMenuPositioningItem_atLocation_inView_(self, item, point, view):
        self.popups.append(point)


class FakeImage(Cocoa):
    def __init__(self):
        self.data = None
        self.size = None
        self.template = False

    def initWithData_(self, data):
        self.data = data
        return self

    def setSize_(self, size):
        self.size = size

    def setTemplate_(self, value):
        self.template = value


class FakeLabel(Cocoa):
    def __init__(self, text=""):
        self.text = text
        self._frame = Rect(0, 0, 80, 16)

    @classmethod
    def labelWithString_(cls, text):
        return cls(text)

    def setFont_(self, font):
        self.font = font

    def setTextColor_(self, colour):
        self.colour = colour

    def sizeToFit(self):
        pass

    def frame(self):
        return self._frame

    def setFrame_(self, rect):
        self._frame = rect


class FakeView(Cocoa):
    def __init__(self):
        self.subviews = []
        self.image = None

    def initWithFrame_(self, rect):
        self.rect = rect
        return self

    def addSubview_(self, view):
        self.subviews.append(view)

    def setImage_(self, image):
        self.image = image


class FakeButton:
    def __init__(self):
        self.tooltip = None
        self.image = None

    def setToolTip_(self, text):
        self.tooltip = text

    def setImage_(self, image):
        self.image = image

    def window(self):
        return self

    def frame(self):
        return Rect(1159.0, 1073.0, 38.0, 34.0)


class FakeStatusItem:
    def __init__(self):
        self._button = FakeButton()
        self.menu = None

    def button(self):
        return self._button

    def setMenu_(self, menu):
        self.menu = menu


class FakeStatusBar:
    def __init__(self):
        self.items = []
        self.removed = []

    def statusItemWithLength_(self, length):
        item = FakeStatusItem()
        self.items.append(item)
        return item

    def removeStatusItem_(self, item):
        self.removed.append(item)


STATUS_BAR = FakeStatusBar()


class FakeObjc:
    @staticmethod
    def selector(function, signature=None):
        return function


def fake_kit():
    """What ``_appkit()`` hands back on a Mac, with the pixels taken out."""
    return {
        "objc": FakeObjc,
        "NSColor": type("NSColor", (), {"labelColor": staticmethod(lambda: "label")}),
        "NSData": type(
            "NSData",
            (),
            {"dataWithBytes_length_": staticmethod(lambda data, length: bytes(data))},
        ),
        "NSFont": type(
            "NSFont", (), {"menuFontOfSize_": staticmethod(lambda size: "menu font")}
        ),
        "NSImage": FakeImage,
        "NSImageView": FakeView,
        "NSMakePoint": lambda x, y: (x, y),
        "NSMakeRect": lambda x, y, width, height: Rect(x, y, width, height),
        "NSMakeSize": lambda width, height: Size(width, height),
        "NSMenu": FakeMenu,
        "NSMenuItem": FakeItem,
        "NSObject": Cocoa,
        "NSStatusBar": type(
            "NSStatusBar", (), {"systemStatusBar": staticmethod(lambda: STATUS_BAR)}
        ),
        "NSTextField": FakeLabel,
        "NSView": FakeView,
    }


@pytest.fixture
def drawn(monkeypatch):
    """Open the door onto the stand-in, for the length of one test.

    Applied after tests/window/conftest.py has answered it with None, so
    this wins for every window built inside the test. The session guard one
    directory up stays armed for anything that reaches around both.

    ``_HELPER_CLASS`` is reset because nsmenu caches the one Objective-C
    class it defines — defining it twice raises — and the cached one would
    otherwise be built against whichever kit came first and outlive the
    test that made it.
    """
    monkeypatch.setattr(nsmenu, "_HELPER_CLASS", None)
    monkeypatch.setattr(w.nsmenu, "_appkit", fake_kit)


@pytest.fixture
def menued(drawn, make_window):
    """A window whose menu really was built, with a song under it."""
    window = make_window()
    assert window._menu.view is not None, "the menu was not built"
    load(window, SYNCED)
    return window


# -- a click, delivered the way Cocoa delivers one -------------------------


def item_for(window, key, value=None):
    """The NSMenuItem a user would click, found the way a click finds it.

    A CHOICE entry has no item of its own to click: its row lives in the
    submenu built from the preset list, and which row is which is the order
    that list is in.
    """
    view = window._menu.view
    if value is None:
        return view._items[key]
    options = m.ENTRIES[key].options
    return view._items[key].submenu().itemAtIndex_(options.index(value))


def click(window, key, value=None):
    """Fire the one selector every menu click arrives through.

    ``fire_`` is the whole of what Cocoa calls: it reads the tag off the
    sender and hands it to the view, which looks up what that tag stands
    for and triggers it. Nothing here names the handler, the key or the
    value — all three come out of the tag table the real ``_arm`` built.
    """
    window._menu.view._helper.fire_(item_for(window, key, value))
    APP.processEvents()


def ticked(window, key):
    """Whether the native item is showing a check mark, read off the item
    rather than off the model. The model saying it is checked and the menu
    drawing a tick are the two halves this file exists to join."""
    return item_for(window, key).state == nsmenu.STATE_ON


def chosen_rows(window, key):
    """Which rows of a CHOICE submenu are ticked, by their preset."""
    options = m.ENTRIES[key].options
    submenu = window._menu.view._items[key].submenu()
    return tuple(
        value
        for index, value in enumerate(options)
        if submenu.itemAtIndex_(index).state == nsmenu.STATE_ON
    )


# -- the tag table is the real one -----------------------------------------


def test_every_acting_entry_has_an_item_with_a_tag_pointing_back_at_it(menued):
    """The harness's own guard. A click is only worth delivering if the tag
    it carries resolves to the entry the user aimed at, and a table that
    had drifted by one would make every test below pass against the wrong
    handler."""
    view = menued._menu.view
    for key in m.MENU_ORDER:
        entry = m.ENTRIES[key]
        if entry.kind in (m.SEPARATOR, m.SUBMENU, m.ROWS, m.READOUT):
            continue
        if entry.kind == m.CHOICE:
            for value in entry.options:
                item = item_for(menued, key, value)
                assert view._targets[item.tag()] == (key, value)
            continue
        item = view._items[key]
        assert view._targets[item.tag()] == (key, None)


# -- the toggles -----------------------------------------------------------
#
# Each of these is a click, a state, and a tick read back off the native
# item. Twice, because the second click is the one that proves the model
# never checked anything itself: `trigger` hands the handler `not
# is_checked(key)`, and `is_checked` is only ever written by the refresh
# that followed the first click. A tick that moved itself would make the
# second click a no-op.

TOGGLES = (
    (m.ROMANISATION, lambda win: win._view_model.romanisation_enabled),
    (m.SPOKEN, lambda win: win._spoken_enabled),
    (m.ECHO, lambda win: win._echo_enabled),
    (m.COMPACT, lambda win: win._compact),
    (m.FIT_TO_SONG, lambda win: win._fit_to_song),
    (m.ALBUM_COLOUR, lambda win: win._album_colour),
    (m.ALL_DESKTOPS, lambda win: win._all_desktops),
    (m.MENUBAR_ANIMATION, lambda win: win._menubar_animation),
    (m.YIELD_NOTIFICATIONS, lambda win: win._yield_to_notifications),
    (m.REMEMBER_POSITION, lambda win: win._remember_position),
)


@pytest.mark.parametrize("key,read", TOGGLES, ids=[key for key, _ in TOGGLES])
def test_a_click_moves_the_setting_and_the_tick_follows_the_setting(menued, key, read):
    was = read(menued)
    assert ticked(menued, key) is was, "the tick disagreed before anything happened"

    click(menued, key)
    assert read(menued) is (not was)
    assert ticked(menued, key) is (not was)

    click(menued, key)
    assert read(menued) is was
    assert ticked(menued, key) is was


# -- the choices -----------------------------------------------------------


CHOICES = (
    (m.SPEECH_RATE, speech.SPEECH_RATE_PRESETS, lambda win: win._speech_rate),
    (m.COMPACT_SIZE, typography.COMPACT_TEXT_SIZES, lambda win: win._compact_text_size),
    (m.PROXIMITY, proximity.MODES, lambda win: win._proximity_mode),
)


@pytest.mark.parametrize(
    "key,options,read", CHOICES, ids=[key for key, _, _ in CHOICES]
)
def test_a_click_on_a_preset_chooses_it_and_unticks_the_others(
    menued, key, options, read
):
    """Exclusive by construction: the model names one value and the refresh
    clears every other row, so two cannot be ticked at once and no row
    decides for itself."""
    wanted = next(value for value in options if value != read(menued))

    click(menued, key, wanted)

    assert read(menued) == wanted
    assert chosen_rows(menued, key) == (wanted,)


# -- the commands, each of which has a story -------------------------------


def test_show_lyrics_takes_the_window_away_and_brings_it_back(menued):
    """The one toggle whose effect is a journey rather than a value, and
    the only way back from a hidden window is this entry."""
    menued.apply_saved_visibility()
    land(menued)
    assert menued._lyrics_visible is True

    click(menued, m.SHOW_LYRICS)
    land(menued)
    assert menued._lyrics_visible is False
    assert menued.isVisible() is False
    assert ticked(menued, m.SHOW_LYRICS) is False

    click(menued, m.SHOW_LYRICS)
    land(menued)
    assert menued._lyrics_visible is True
    assert ticked(menued, m.SHOW_LYRICS) is True


def test_dock_to_top_is_a_command_and_carries_no_tick(menued):
    """It puts the window somewhere once. Nothing holds it there, so there
    is no state for a tick to describe."""
    screen = menued.screen() or APP.primaryScreen()
    geometry, available = screen.geometry(), screen.availableGeometry()
    menued.move(geometry.x() + 40, geometry.y() + 400)
    APP.processEvents()

    click(menued, m.DOCK_TOP)
    if menued._move_anim is not None:
        menued._move_anim.setCurrentTime(menued._move_anim.duration())
    APP.processEvents()

    assert (menued.pos().x(), menued.pos().y()) == w.docked_position(
        menued.width(),
        (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
        (available.x(), available.y(), available.width(), available.height()),
        menued._top_inset(),
    )
    assert ticked(menued, m.DOCK_TOP) is False


def test_forgetting_positions_empties_the_map_and_takes_the_entry_away(menued):
    """An entry that appears with the first thing there is to forget and
    goes again with the last."""
    menued._positions.remember("com.apple.Safari", 100, 120, "Safari")
    menued._refresh_menu()
    assert menued._menu.is_visible(m.FORGET_POSITIONS) is True
    assert item_for(menued, m.FORGET_POSITIONS).hidden is False

    click(menued, m.FORGET_POSITIONS)

    assert len(menued._positions) == 0
    assert item_for(menued, m.FORGET_POSITIONS).hidden is True


def test_open_at_login_asks_macos_and_takes_the_answer_back(menued, monkeypatch):
    """The tick follows the system rather than the click: ``set_enabled``
    re-reads the status, and what comes back is what the item draws."""
    menued._bundled = True
    asked = []
    monkeypatch.setattr(
        login_item,
        "set_enabled",
        lambda enabled: (
            asked.append(enabled),
            (True, login_item.LoginItemStatus.ENABLED if enabled
             else login_item.LoginItemStatus.NOT_REGISTERED),
        )[1],
    )
    menued._refresh_menu()

    click(menued, m.OPEN_AT_LOGIN)
    assert asked == [True]
    assert ticked(menued, m.OPEN_AT_LOGIN) is True

    click(menued, m.OPEN_AT_LOGIN)
    assert asked == [True, False]
    assert ticked(menued, m.OPEN_AT_LOGIN) is False


def test_a_click_the_system_refuses_leaves_the_tick_where_it_was(menued, monkeypatch):
    """Where "nothing checks or unchecks an entry from a click" is load
    bearing rather than tidy. The user clicked, macOS refused, and a tick
    that had moved itself on the way through would be the menu claiming a
    launch that will not happen. Nothing here puts it back: it was never
    moved, because the only thing that writes it is the refresh that ran
    after the handler had the system's answer.
    """
    menued._bundled = True
    monkeypatch.setattr(
        login_item,
        "set_enabled",
        lambda enabled: (False, login_item.LoginItemStatus.REQUIRES_APPROVAL),
    )
    menued._refresh_menu()
    assert ticked(menued, m.OPEN_AT_LOGIN) is False

    click(menued, m.OPEN_AT_LOGIN)

    assert ticked(menued, m.OPEN_AT_LOGIN) is False
    assert "System Settings" in item_for(menued, m.OPEN_AT_LOGIN).title_text


def test_sync_this_song_begins_a_pass_and_the_label_says_which(drawn, make_window):
    """A command whose label is the state: "Sync" until there is one of
    theirs, "Re-sync" afterwards."""
    window = make_window()
    load(window, PLAIN, track_id="t7")
    assert item_for(window, m.SYNC).title_text == "Sync this song"

    click(window, m.SYNC)

    assert window._syncing is True
    window._cancel_sync()
    window._provider.save_user_sync("t7", "[00:01.00] first line\n")
    window._refresh_menu()
    assert item_for(window, m.SYNC).title_text == "Re-sync this song"


def test_quit_from_a_native_click_runs_the_clean_shutdown(drawn, make_window):
    """Straight to the app's own quit, so the aboutToQuit shutdown runs
    however quit is reached. Driven through a real event loop because that
    is the only thing quit can be observed against."""
    window = make_window()
    assert window._monitor_thread.isRunning() is True

    timed_out = []
    # Owned and stopped rather than fired and forgotten: a singleShot that
    # outlived this test would still be armed when a later one calls exec()
    # and would quit somebody else's event loop.
    rescue = QTimer()
    rescue.setSingleShot(True)
    rescue.timeout.connect(lambda: (timed_out.append(True), APP.quit()))
    rescue.start(5000)
    QTimer.singleShot(0, lambda: click(window, m.QUIT))
    APP.exec()
    rescue.stop()

    assert not timed_out, "quit did not come from the menu item"
    assert window._monitor_thread.isRunning() is False


# -- the item carries the one menu -----------------------------------------


def test_the_menu_bar_item_is_handed_the_same_menu_the_clicks_arrive_on(menued):
    """One model, one drawing of it, two ways in. The right-click opens
    that same NSMenu, so an entry cannot behave differently depending on
    which way it was reached."""
    assert menued._tray is not None, "no menu bar item was made"
    assert menued._tray._item.menu is menued._menu.view.native

    class Event:
        def globalPos(self):
            from PySide6.QtCore import QPoint

            return QPoint(10, 10)

    menued.contextMenuEvent(Event())
    assert menued._menu.view.native.popups, "the right-click opened nothing"


def test_opening_the_remembered_apps_submenu_is_what_builds_its_rows(menued):
    """The one part of the menu that is DATA rather than structure, and the
    one part rebuilt rather than relabelled — only while somebody is
    looking at it, never while the item sits idle."""
    menued._positions.remember("com.apple.Safari", 100, 120, "Safari")
    rows = menued._menu.view._submenus[m.POSITION_LIST]
    assert rows.items == [], "the rows were built before anybody looked"

    menued._menu.opening(m.POSITION_LIST)

    assert len(rows.items) == 1
    assert rows.items[0].view is not None, "a fact was drawn as a control"


# -- and nothing is left out -----------------------------------------------


COVERED = (
    {key for key, _ in TOGGLES}
    | {key for key, _, _ in CHOICES}
    | {
        m.SHOW_LYRICS,
        m.DOCK_TOP,
        m.FORGET_POSITIONS,
        m.OPEN_AT_LOGIN,
        m.SYNC,
        m.QUIT,
    }
)


def test_every_entry_a_click_can_reach_is_covered_above(menued):
    """What keeps "all of them" true rather than true when it was written.

    Asked of the model, so an entry wired up later without a test here
    fails this one. The other direction matters too: a key covered above
    that nothing connects would be a test of a click that goes nowhere.
    """
    acting = {key for key in m.MENU_ORDER if menued._menu.has_handler(key)}
    assert COVERED == acting
