"""The menu bar item, and the one native NSMenu behind both ways into it.

Everything here is a VIEW onto ``menu.Menu``. The structure, the labels,
the gating and the state live there and are pure; this draws them, and it
is the only place in the app that says NSMenu, NSMenuItem or NSStatusItem.

## Why it exists

The menu used to be one ``QMenu`` serving the menu bar item and the
window's right-click menu, on the argument that two menus would drift
apart. The object was one; the APPEARANCE was two. Qt hands a system
tray's menu to macOS, which converts it into a real NSMenu, so the menu bar
item got the system's own drawing — its font, its check marks, its
separators, its submenu timing. The same object popped up under the pointer
is drawn by Qt's widget style instead. Same entries, same order, two
different menus depending on how you opened it.

One NSMenu, used by both routes, is the whole fix. ``setMenu:`` on our own
status item covers the first; ``popUpMenuPositioningItem:atLocation:inView:``
covers the second, and it works from an accessory app that never activates
— verified by screenshot before any of this was written, because a menu
that needed the app to come forward would have been the end of the idea.

## One door, two things behind it

``_appkit()`` is the single door, and it opens on the status item as well
as on the menu. They are not two capabilities pretending to be one: the
item exists to carry the menu, they are created in the same breath and
released in the same breath, and a test that may not put an NSMenu on
screen may certainly not put an icon in the developer's menu bar. One door
is one thing for the suite to shut.

Off macOS, or without pyobjc, everything here answers False or None and the
app runs on with no menu bar item and no menu — which is exactly what a
Linux CI runner has anyway, and is why the whole suite is still headless.

## Rows that are facts, not controls

The remembered-apps list is a list of things that HAVE been learned, not a
list of things to click. macOS greys a disabled item, and four greyed rows
read as four things that are unavailable rather than as four facts. An
attributed title with an explicit ``labelColor`` does not help: AppKit dims
a disabled item when it draws it, whatever colour the string asked for
(measured, on a real menu). So those rows are NSMenuItems with a VIEW,
which AppKit draws at the ordinary text colour, does not highlight, and
does not treat as a control. It is the same answer QWidgetAction gave when
this was a QMenu, in the native idiom.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from sottovoce import menu as model

logger = logging.getLogger(__name__)

# NSControlStateValue.
STATE_ON = 1
STATE_OFF = 0

# NSStatusItem length: as wide as its content.
VARIABLE_LENGTH = -1.0

# How the readout rows are laid out, in points. The indent is where a
# native menu item with an icon puts its text, so a row lines up with the
# entries above it rather than announcing that it is made of something
# else.
ROW_INDENT = 21
ROW_ICON = 16
ROW_GAP = 6
ROW_TRAIL = 12
ROW_HEIGHT = 20


def _appkit():
    """AppKit's menu and status-bar classes, or None where there are none.

    The single door. Everything native in this module comes through here,
    so the suite has one seam to block and no test can put an icon in the
    developer's menu bar or a menu on their screen.
    """
    if sys.platform != "darwin":
        return None
    try:
        import objc
        from AppKit import (
            NSColor,
            NSFont,
            NSImage,
            NSImageView,
            NSMenu,
            NSMenuItem,
            NSStatusBar,
            NSTextField,
            NSView,
        )
        from Foundation import NSData, NSMakePoint, NSMakeRect, NSMakeSize, NSObject
    except Exception:  # pragma: no cover - pyobjc missing
        logger.info("AppKit unavailable: no menu bar item and no native menu")
        return None
    return {
        "objc": objc,
        "NSColor": NSColor,
        "NSData": NSData,
        "NSFont": NSFont,
        "NSImage": NSImage,
        "NSImageView": NSImageView,
        "NSMakePoint": NSMakePoint,
        "NSMakeRect": NSMakeRect,
        "NSMakeSize": NSMakeSize,
        "NSMenu": NSMenu,
        "NSMenuItem": NSMenuItem,
        "NSObject": NSObject,
        "NSStatusBar": NSStatusBar,
        "NSTextField": NSTextField,
        "NSView": NSView,
    }


# The class that receives clicks and menu openings. Built on first use
# rather than at import, and cached, for the reason frontmost.py gives
# about its watcher: defining an Objective-C class at import time is
# defining it on machines that have no Objective-C. Cached because
# defining the same class twice raises.
_HELPER_CLASS = None


def _helper_class(kit):
    """The one Objective-C class this module defines: a click target and a
    menu delegate in one object, because both are callbacks onto the same
    Python owner and neither holds any state of its own."""
    global _HELPER_CLASS
    if _HELPER_CLASS is not None:
        return _HELPER_CLASS

    class _MenuHelper(kit["NSObject"]):
        def fire_(self, sender):
            """A menu item was clicked. The tag is the index of what it
            stands for, so the menu carries no Python objects across into
            Cocoa and nothing has to be kept alive on the other side."""
            try:
                self.owner._fired(int(sender.tag()))
            except Exception:
                logger.exception("a menu click was not handled")

        def menuWillOpen_(self, opened):
            """The native ``aboutToShow``. Used for the one thing a refresh
            on a timer cannot do honestly: re-reading what the system says
            about the login item at the moment somebody looks at it."""
            try:
                self.owner._opening(opened)
            except Exception:
                logger.exception("a menu opening was not handled")

    _HELPER_CLASS = _MenuHelper
    return _HELPER_CLASS


def _image_from(kit, data: Optional[bytes], points: Optional[int] = None):
    """An NSImage from encoded bytes, or None.

    Bytes rather than anything Cocoa-shaped on the way in, so the modules
    that produce these (``symbols`` draws the glyph with Qt,
    ``frontmost.app_icon_tiff`` asks macOS for an app's icon) never have to
    import AppKit to hand one over.

    ``points`` is the size the image claims to be, and it is the whole of
    what milestone 15.1 measured the hard way: the drawing is done at the
    screen's own scale and the image is LABELLED with the size the menu bar
    wants, so 44 pixels of glyph occupy a 22-point slot at 2x rather than
    being taken for a 44-point image and clipped.
    """
    if not data:
        return None
    payload = kit["NSData"].dataWithBytes_length_(data, len(data))
    image = kit["NSImage"].alloc().initWithData_(payload)
    if image is None:
        logger.debug("an image handed to the menu did not decode")
        return None
    if points:
        image.setSize_(kit["NSMakeSize"](points, points))
    return image


class NativeMenu:
    """One NSMenu, built once from the model and only ever relabelled.

    Its structure never changes after ``build``: a refresh flips hidden,
    check marks, chosen presets and titles, because rebuilding a menu the
    status item owns is the menu bar item being rebuilt under whoever is
    reading it.

    The one exception is a ROWS submenu, whose entries ARE data. That one
    is rebuilt, and only while the user is looking at it.
    """

    def __init__(self, menu: model.Menu) -> None:
        self._menu = menu
        self._kit = None
        self._root = None
        self._items: dict = {}
        self._submenus: dict = {}
        self._helper = None
        # What each tag stands for: (key, value). A list rather than a dict
        # because a tag IS an index, and because the order is the order the
        # menu was built in and never changes.
        self._targets: list = []
        self._applied: dict = {}
        self._opened_by: dict = {}

    # -- building ----------------------------------------------------------

    def build(self) -> bool:
        """Make the menu. False when there is nothing to make it with."""
        kit = _appkit()
        if kit is None:
            return False
        self._kit = kit
        helper_class = _helper_class(kit)
        self._helper = helper_class.alloc().init()
        self._helper.owner = self
        self._root = self._build_menu(self._menu.entries)
        self._root.setDelegate_(self._helper)
        # Which menu is which, when one arrives back from Cocoa. By object
        # rather than by title: a delegate is told the NSMenu that opened
        # and nothing else, and the answer has to be the model's key.
        self._opened_by = {id(self._root): None}
        for key, submenu in self._submenus.items():
            submenu.setDelegate_(self._helper)
            self._opened_by[id(submenu)] = key
        logger.info("native menu built with %d entries", len(self._items))
        return True

    def _build_menu(self, entries):
        kit = self._kit
        menu = kit["NSMenu"].alloc().init()
        # Ours to decide, not AppKit's: without this every item with no
        # action is disabled for us, which is most of a menu whose clicks
        # all arrive through one selector.
        menu.setAutoenablesItems_(False)
        for entry in entries:
            menu.addItem_(self._build_item(entry))
        return menu

    def _build_item(self, entry):
        kit = self._kit
        if entry.kind == model.SEPARATOR:
            item = kit["NSMenuItem"].separatorItem()
            self._items[entry.key] = item
            return item
        item = kit["NSMenuItem"].alloc().initWithTitle_action_keyEquivalent_(
            entry.label, None, ""
        )
        if entry.kind == model.CHOICE:
            item.setSubmenu_(self._build_options(entry))
        elif entry.kind == model.SUBMENU:
            item.setSubmenu_(self._build_menu(entry.children))
        elif entry.kind == model.ROWS:
            rows = kit["NSMenu"].alloc().init()
            rows.setAutoenablesItems_(False)
            item.setSubmenu_(rows)
            self._submenus[entry.key] = rows
        elif entry.kind == model.READOUT:
            # A readout, so it is not a control and says so the way macOS
            # says it. Unlike the rows below, one grey line among ticked
            # entries reads as a note rather than as something broken.
            item.setEnabled_(False)
        else:
            self._arm(item, entry.key, None)
        self._items[entry.key] = item
        return item

    def _build_options(self, entry):
        """A CHOICE entry's submenu: one row per preset, exclusive by
        construction because the refresh ticks exactly one of them."""
        kit = self._kit
        menu = kit["NSMenu"].alloc().init()
        menu.setAutoenablesItems_(False)
        for value in entry.options:
            item = kit["NSMenuItem"].alloc().initWithTitle_action_keyEquivalent_(
                entry.option_label.format(value), None, ""
            )
            self._arm(item, entry.key, value)
            menu.addItem_(item)
        return menu

    def _arm(self, item, key: str, value) -> None:
        """Point an item at the one selector every click arrives through."""
        kit = self._kit
        item.setTag_(len(self._targets))
        self._targets.append((key, value))
        item.setTarget_(self._helper)
        item.setAction_(kit["objc"].selector(self._helper.fire_, signature=b"v@:@"))

    # -- callbacks from Cocoa ---------------------------------------------

    def _fired(self, tag: int) -> None:
        key, value = self._targets[tag]
        self._menu.trigger(key, value)

    def _opening(self, opened) -> None:
        key = self._opened_by.get(id(opened), _UNSET)
        if key is _UNSET:
            return
        self._menu.opening(key)

    # -- state -------------------------------------------------------------

    def apply(self, menu: model.Menu) -> None:
        """Bring every item in line with the model.

        Each property is written only when it has CHANGED. This runs on
        every render, three times a second, and an NSMenuItem told the same
        thing that often is work done for nothing; more to the point, it is
        the habit that put the flicker into the status item's own image.
        """
        if self._root is None:
            return
        for key, item in self._items.items():
            entry = model.ENTRIES[key]
            hidden = not menu.is_visible(key)
            if self._changed(key, "hidden", hidden):
                item.setHidden_(hidden)
            if entry.kind == model.SEPARATOR:
                continue
            if self._changed(key, "title", menu.label(key)):
                item.setTitle_(menu.label(key))
            if entry.kind == model.TOGGLE:
                if self._changed(key, "state", menu.is_checked(key)):
                    item.setState_(STATE_ON if menu.is_checked(key) else STATE_OFF)
            elif entry.kind == model.CHOICE:
                if self._changed(key, "chosen", menu.chosen(key)):
                    self._tick_option(key, entry, menu.chosen(key))
            elif entry.kind == model.READOUT:
                # The window only asks macOS for an icon when the app in
                # front changes, so this is usually the same bytes object
                # it was last time and the comparison never reaches the
                # pixels.
                if self._changed(key, "icon", menu.icon(key)):
                    item.setImage_(_image_from(self._kit, menu.icon(key), ROW_ICON))

    def _changed(self, key: str, name: str, value) -> bool:
        """Whether this property is worth writing, and remember that it was."""
        if self._applied.get((key, name), _UNSET) == value:
            return False
        self._applied[(key, name)] = value
        return True

    def _tick_option(self, key: str, entry, chosen) -> None:
        """Exactly one preset of a CHOICE entry, by construction: the model
        names the chosen value and every other row is cleared here, so two
        cannot be ticked at once and none of them decides for itself."""
        submenu = self._items[key].submenu()
        for index, value in enumerate(entry.options):
            submenu.itemAtIndex_(index).setState_(
                STATE_ON if value == chosen else STATE_OFF
            )

    def set_rows(self, key: str, rows) -> None:
        """Refill a ROWS submenu. The one thing here that is rebuilt."""
        submenu = self._submenus.get(key)
        if submenu is None:
            return
        submenu.removeAllItems()
        for row in rows:
            submenu.addItem_(self._row_item(row))
        logger.debug("menu rows rebuilt for %s: %d", key, len(rows))

    def _row_item(self, row):
        """One fact, at full brightness and not a control.

        A view rather than a title, because a disabled NSMenuItem is drawn
        grey whatever its attributed string says, and grey means "you
        cannot have this" rather than "this is what I know".
        """
        kit = self._kit
        label = kit["NSTextField"].labelWithString_(row.label)
        label.setFont_(kit["NSFont"].menuFontOfSize_(0))
        label.setTextColor_(kit["NSColor"].labelColor())
        label.sizeToFit()
        text_size = label.frame().size
        width = ROW_INDENT + ROW_ICON + ROW_GAP + text_size.width + ROW_TRAIL
        height = max(ROW_HEIGHT, text_size.height)
        view = kit["NSView"].alloc().initWithFrame_(
            kit["NSMakeRect"](0, 0, width, height)
        )
        icon = _image_from(kit, row.icon, ROW_ICON)
        if icon is not None:
            well = kit["NSImageView"].alloc().initWithFrame_(
                kit["NSMakeRect"](
                    ROW_INDENT, (height - ROW_ICON) / 2, ROW_ICON, ROW_ICON
                )
            )
            well.setImage_(icon)
            view.addSubview_(well)
        label.setFrame_(
            kit["NSMakeRect"](
                ROW_INDENT + ROW_ICON + ROW_GAP,
                (height - text_size.height) / 2,
                text_size.width,
                text_size.height,
            )
        )
        view.addSubview_(label)
        item = kit["NSMenuItem"].alloc().initWithTitle_action_keyEquivalent_("", None, "")
        item.setView_(view)
        return item

    # -- opening it --------------------------------------------------------

    def popup(self, x: float, y: float) -> bool:
        """Open the menu at a point in Cocoa screen coordinates.

        Modal while it is up, exactly as ``QMenu.exec`` was, and it does
        not activate the app: this window is unfocusable by design and a
        menu that stole the foreground to be read would undo that.
        """
        if self._root is None:
            return False
        try:
            self._root.popUpMenuPositioningItem_atLocation_inView_(
                None, self._kit["NSMakePoint"](x, y), None
            )
        except Exception:
            logger.exception("could not open the menu")
            return False
        return True

    @property
    def native(self):
        return self._root


class _Unset:
    """A value nothing can equal, so "never applied" is not "applied None"."""

    def __eq__(self, other):
        return False


_UNSET = _Unset()


class StatusItem:
    """The menu bar item: the glyph, the menu it carries, and where it is.

    Ours rather than QSystemTrayIcon's, and that is what buys the native
    menu: Qt owns the NSStatusItem it makes and there is no supported way
    to hand it a menu of our own, so the item that carries one NSMenu has
    to be an item this app made.
    """

    def __init__(self) -> None:
        self._kit = None
        self._item = None
        self._bar = None

    def create(self, tooltip: str = "") -> bool:
        """Put the item in the menu bar. False where there is no menu bar,
        which is not an error: everything that uses it asks first, exactly
        as it did when the item was Qt's and the platform reported no
        system tray."""
        kit = _appkit()
        if kit is None:
            return False
        try:
            self._bar = kit["NSStatusBar"].systemStatusBar()
            self._item = self._bar.statusItemWithLength_(VARIABLE_LENGTH)
        except Exception:
            logger.exception("could not create the menu bar item")
            self._item = None
            return False
        self._kit = kit
        if tooltip:
            self._item.button().setToolTip_(tooltip)
        logger.info("menu bar item created")
        return True

    @property
    def alive(self) -> bool:
        return self._item is not None

    def set_menu(self, menu: NativeMenu) -> None:
        """Give the item the one menu. Nothing else may be given one."""
        if self._item is None or menu.native is None:
            return
        self._item.setMenu_(menu.native)

    def set_image(self, png: bytes, points: int) -> None:
        """The glyph, as a template image so macOS tints it for a light or
        a dark menu bar and the dimmed alpha comes through as less ink
        rather than as grey."""
        if self._item is None:
            return
        image = _image_from(self._kit, png, points)
        if image is None:
            return
        image.setTemplate_(True)
        self._item.button().setImage_(image)

    def frame(self) -> Optional[tuple]:
        """The item's own window, in Cocoa screen coordinates, or None.

        The status item's button window IS the rectangle the flight aims
        at: measured against ``QSystemTrayIcon.geometry()`` in the same
        process while both existed, the two agree exactly once Cocoa's
        bottom-left origin is taken out. Handed over as plain numbers, so
        the arithmetic that flips it stays pure.
        """
        if self._item is None:
            return None
        try:
            window = self._item.button().window()
            if window is None:
                return None
            rect = window.frame()
        except Exception:
            logger.debug("could not read the menu bar item's frame", exc_info=True)
            return None
        return (
            float(rect.origin.x),
            float(rect.origin.y),
            float(rect.size.width),
            float(rect.size.height),
        )

    def release(self) -> None:
        """Take the item out of the menu bar. Idempotent, because shutdown
        is reached more than once, and done at all because an item this app
        made is not removed by the window being destroyed."""
        if self._item is None:
            return
        item, self._item = self._item, None
        try:
            self._bar.removeStatusItem_(item)
        except Exception:
            logger.debug("could not remove the menu bar item", exc_info=True)
            return
        logger.debug("menu bar item removed")
