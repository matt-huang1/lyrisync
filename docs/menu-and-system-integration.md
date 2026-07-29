# The menu, and living in the system

## One menu, two ways in

The menu bar item's menu and the window's right-click menu are **literally
the same `QMenu` object**. Two menus could drift apart; one cannot.

Its structure is built once and never rebuilt. Refreshing only flips
visibility, check marks and labels — rebuilding would make the native menu
bar item flicker every time anything changed.

Checkable entries connect to `triggered`, not `toggled`, because refresh
calls `setChecked` on all of them and `toggled` would feed those
programmatic changes straight back into the setters as if the user had
clicked.

Which entries are visible is pure logic in `menu.py`, tested without Qt.

## Entries appear only where they apply

The learning layers hide when they cannot act — there is nothing to
romanise without hangul on screen, nothing to speak without the Korean
voice installed. With every layer dormant the menu is show/hide, the two
choices about how the window looks, and quit.

Album colour is the exception: it is *always* visible. The others hide
because they cannot act; this one can always be answered and is a standing
preference about the window, so appearing and vanishing with each track
would hide it exactly when someone goes looking — before the music starts.

**Quit is visible in every state.** The menu bar item is the way back from
a hidden window, so it must never be a dead end.

## Hiding hides, and nothing else

Hiding the lyrics leaves the monitor running, a loop engaged, and a
tap-to-sync pass stamping. Showing the window again picks the song up
wherever it now is. The setting is remembered across launches.

That is the whole distinction: hiding is a display choice, not a stop
button.

## Open at Login

Uses `SMAppService.mainApp`, not a `LaunchAgent` plist, because the menu
entry has to show what the **system** thinks. A plist stays exactly as
written after the user disables the item in System Settings, so a menu
built on it would keep claiming the app starts at login when it no longer
does.

The cost is macOS 13 (the app's floor is 11); below that the entry is not
offered at all, which beats a fallback that cannot describe itself
honestly on the very systems it would serve.

**The tick never comes from the stored preference.** The setting records
what the user asked for; `status()` is re-read at startup and on every
menu opening, and the tick follows macOS. The two are only ever compared
in order to log the disagreement.

`REQUIRES_APPROVAL` is not enabled — registration can return no error and
still not start the app at login. So enabling re-reads the status instead
of trusting its own return, reports failure, and leaves the entry unticked
with a label naming System Settings. An unticked box that explains itself
beats a ticked one that is wrong until next login.

Measured, not assumed: a freshly built bundle reports `NOT_FOUND` (3)
before it has ever been registered, registers cleanly under an ad-hoc
signature with no approval step, and reports `NOT_REGISTERED` (0) after
being switched off. So `NOT_FOUND` is an ordinary "off", and the entry is
still offered from it.

Every native call goes through one accessor, so the test suite has one
door to shut. A stray call would leave a real login item on whoever ran
the suite.

## Settings

`QSettings` is **injected** into the window, not configured globally.
`QSettings.setDefaultFormat` / `setPath` are process-wide and silently do
nothing on macOS — trusting them meant tests writing into the real user's
preferences.

The bundle identifier *is* the settings contract: `com.lyrisync.lyrisync`
is what `QSettings("lyrisync", "lyrisync")` already resolves to, so the
bundled app and a terminal run share one plist. Verified by launching both
at once and reading the window geometry back — same position, same size,
same file.

There is **no appearance setting**. macOS already answers that question,
and a toggle would be a second source of truth for it — the same argument
as the login item's tick following the system.

## Shutdown

A `QThread` destroyed while still running is a `qFatal`: the process
aborts with "QThread: Destroyed while thread is still running" and names
whichever test was on screen, which is how a shutdown bug once came to
look like a bug in the quit test.

So shutdown drains everything the window owns — the hotkey first, then the
monitor thread, then the worker pool — before anything of the window's is
destroyed.

Its waits are **bounded and honest**. A worker that will not return in 3
seconds (`say` can hold a line for a minute) is logged and left to the
thread pool's own destructor, which is where it was always going to be
dealt with. Blocking quit on it would be worse than the warning.
