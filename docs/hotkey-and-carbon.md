# The global hotkey, and why it is Carbon

**⇧⌘J** shows and hides the lyrics from anywhere — another app, another
Space, a full-screen editor or video — with no Accessibility permission
and no prompt.

## Three ways to do this, and why the deprecated one wins

| approach | what it reads | permission |
|---|---|---|
| `CGEventTap` | every keystroke on the machine | Accessibility |
| `NSEvent` global monitor | every keystroke on the machine | Accessibility |
| **Carbon `RegisterEventHotKey`** | one combination, matched by the OS | **none** |

The first two ask macOS to hand this app every key you press anywhere, so
macOS quite reasonably puts them behind a prompt you have to leave the app
to answer. The Carbon event manager matches the one combination itself and
calls back only when it fires. Nothing else is ever seen.

`RegisterEventHotKey` is deprecated, and nothing replaced it. This is what
every menu bar utility with a shortcut and no Accessibility prompt is
doing.

## ctypes, because pyobjc has no Carbon bindings

Measured rather than assumed: `RegisterEventHotKey`,
`InstallEventHandler` and `GetApplicationEventTarget` all resolve out of
`Carbon.framework`, registration returns `noErr` with no prompt, and the
one missing symbol — `NewEventHandlerUPP` — is a 64-bit no-op that
returned its argument, so **the ctypes callback *is* the UPP**.

Verified again in the real app: "global hotkey ⇧⌘J registered" under the
accessory policy, released on quit.

Out-parameters go through `ctypes.pointer`, not `byref`, so the whole
native door can be faked in tests and the register/press/release sequence
exercised for real.

## One door

Every native call goes through a single private accessor. A stray
registration in the test suite would take ⇧⌘J from whoever is running it,
for as long as the process lives — so the suite has exactly one thing to
block. (The login item follows the same pattern for the same reason.)

## Registration is not exclusive, and the code had to be corrected for it

Measured: two LyriSync processes both claimed ⇧⌘J and both got `noErr`,
with macOS deciding between them per press. `eventHotKeyExistsErr`
(-9878) comes back only when the **same** process already holds the
combination.

So a refusal is never "another app owns it", and neither the log line nor
the README may say that it is. The honest message names the `OSStatus` and
points at the menu bar item, which still works.

## One piece of state

The hotkey drives `_set_lyrics_visible` — the same setter the menu entry
uses — so there is one piece of state and the tick matches the window
whichever way you got there.

The combination is deliberately **not** shown on that menu entry. A
`QAction` shortcut would be a second mechanism firing one action, and a
label printing ⇧⌘J while another app owned the combination would be an
entry claiming something untrue. The README carries the keys; the log says
whether they landed.

## ⇧⌘J, moved from ⇧⌘L

⇧⌘L collided with something the user already runs. The move was one
constant and its label — which is the argument for keeping it in one
place.

Key codes are verified against the live layout rather than read off a
header: `CGEventKeyboardGetUnicodeString` on an event built from `0x26`
answers "j". Building a `CGEvent` needs no Accessibility permission; only
*posting* one does.

Configurable bindings, and any hotkey beyond show/hide, are parked.

## Release order at shutdown

Unregistering is the **first** thing shutdown does. It is the only thing
left that can still call *in* — Carbon holds a pointer to a callback that
toggles a window being torn down.

Unregister is idempotent, because shutdown is reached more than once, and
the ctypes callback object is dropped only *after* the handler is removed:
until then, C holds a pointer into it.
