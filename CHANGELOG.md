# Changelog

Milestones in the order they happened. Dates are commit dates; the deeper
reasoning behind each is in [docs/](docs/).

## Milestone 16.1 — dim for the banner, not for the display

Milestone 16 dimmed the window for banners it was **nowhere near**, because
it intersected against the rectangle macOS reports and that rectangle is the
whole display.

Before working around it, this went looking for a real signal: every field of
the notification window, dumped with nothing showing, with a banner up, and
with the Notification Centre panel open. **A banner and the open panel are
identical in every single field** — window number, layer, bounds, alpha,
sharing state, store type, memory usage, even the index in the on-screen list
and the set of keys returned. The window count does not change either, and
`kCGWindowMemoryUsage` is 2368 with nothing on screen at all, so it is not a
backing-store size. The only signal in the whole record is
`kCGWindowIsOnscreen` appearing, and it says *something* is showing, never
what or where.

So the fix is a heuristic, and it is named and documented as one: the
reported rectangle **narrowed to its rightmost 440 points**, full height.
Where notifications actually appear was measured from pixels — once, in a
harness that already had the permission, so the app ships a constant instead
of looking: a short banner is 346pt wide, a long one 360, three stacked 368,
the panel 416, every one of them anchored to the right edge. 440 rather than
416 because those widths move with system text size and localisation, and the
two failure directions are not worth the same.

Narrowing the *reported* rectangle rather than asking a screen for its right
edge keeps 16's one real property — a banner on another display still cannot
reach a window over here — and `min` means the day macOS reports a real
banner rectangle, this stops being a heuristic with no edit. Verified live: a
window at `(20, 400)` sat through a 6.4-second banner with its opacity at
1.000 for every traced frame.

Measuring where notifications appear **took three attempts**, and the first
two were confidently wrong: 1408pt wide, starting inside the menu bar. Three
pollutants — this script's own output scrolling in the editor, the lyrics
window animating, and its native shadow, which falls *outside* the window
bounds so masking the rectangle was not enough. Rendering the diff mask and
looking at it is what found all three. The numbers had been stable and
repeatable the whole time.

**Restoring is quicker.** The poll drops to 100ms while the window is faded
and returns to 300ms once it is back, because going away late costs nothing
and coming back late is the user waiting for their own lyrics. Restore now
begins 44–156ms after a notification vanishes (was 190ms) and completes in
289–395ms, mean 342ms (was 430ms). It costs 0.126% of one core, and only for
the seconds a notification is up.

And it is now written down that a fade **proportional** to how much is
covered is not implementable without pixel capture — it needs the real
rectangle, macOS reports the display, and the only public route to the real
one is the thing the Screen Recording prompt guards.

## Milestone 16 — yield to notifications

The window floats *above* notification banners — level 25 against their 21
— so when one arrives over the same corner of the screen, this app is what
is covering somebody's mail. It now **fades out of the way** while a banner
or the Notification Centre is over it, to opacity 0.15 over 260 ms, and
comes back the same way when the way is clear. Off by default.

Fading, never moving: moving would fight per-app position memory for
ownership of where the window lives, and would then have to decide when and
where to move back.

**It needs no permission, and that was measured rather than assumed** — with
a throwaway app bundle whose identifier had never been granted anything,
not from a terminal that inherits its editor's grant. The window list hands
back owner, PID, layer, bounds and on-screen state with no prompt and no TCC
entry. Exactly one field is withheld, `kCGWindowName`, the window's *title*,
and the app never asks for it; two tests forbid the module from mentioning
it or any screen-capture entry point.

Keyed on the bundle identifier rather than the owner's name, because that
name is **localised** — "Notification Centre" here, "Notification Center" in
the US — and a string match on it would have worked for whoever wrote it and
silently never fired for half the people who ran it. Control Centre is
deliberately excluded: it owns eleven permanently on-screen windows, and
yielding to it would fade the window once and never bring it back.

The rectangle macOS reports for a banner is **the whole display**, and the
page says so rather than dressing it up: there is no public way to find the
banner's own rect that does not involve capturing its pixels, which is the
one thing that would need the permission this avoids. So the overlap test
discriminates by display, and narrows for free if Apple ever reports
something tighter.

Costs 0.105 ms of CPU per poll — 0.035% of one core at 300 ms, against 2.3%
for the line change. Traced live: the fade starts 90 ms after a banner
appears and 190 ms after it clears, both inside one poll interval.

And a correction kept in the docs because the obvious assumption is wrong:
the banner's **own** text contrast is never the casualty. It *rises* under
the window's pale panel — 8.32:1 alone, 12.90:1 fully covered — and never
approaches 4.5:1 at any ceiling. The first measurement said the opposite,
because it sampled the whole window rectangle where most of the pixels are
the app behind. Looking at the capture is what found it.

## Milestone 15 — menu bar presence

The window no longer blinks out when you hide it: it **shrinks and fades
towards the menu bar item**, and grows back out of it. The content is
scaled by the compositor rather than by Qt, so nothing reflows on the way,
and everything the journey borrows — position, opacity, scale, the
material, the shadow — is given back by one method, so an interruption
leaves no ghost. With no item to fly to (behind the notch, in an
overflow), it fades in place.

The item itself now says what is happening, in **three states**: idle when
nothing is playing or the lyrics are hidden, active while they are up, and
a dot beside the glyph while a loop, an echo pass or a tap-to-sync is
running. Template images throughout, so macOS still tints them for the
menu bar, and nothing animates up there.

The learn glow was too quiet to notice — the fix was not more colour but a
**thicker edge**: one device pixel to three, peaking at the full amber over
780 ms. And the Remembered apps list lost its per-app forget: re-dragging
overwrites a position, so forgetting one app could only mean "stop moving
the window for this one", which nobody wants.

## Milestone 14.2 — names, faces, and a word back

The menu now says **Safari**, with Safari's icon, where it used to say
`com.apple.Safari`; the name is taken from the same activation as the
identifier and stored beside the position, so an app that is not running
is still readable. Coordinates left the menu for the log. A new
**Remembered apps** submenu lists what has been learned, most recent
first, and clicking one forgets that app.

And the drag that teaches a position now gets an answer: the hairline
warms for half a second and hands the edge straight back to the album
tint. That reverses 14.1's refusal under a rule this milestone adds to
the design philosophy — transient feedback may borrow a surface that
persistent decoration owns, provided the return is structural rather than
remembered.

## Milestone 14.1 — making it visible, and finding out why it was not

Reported as not working, and — the more useful half — as impossible to
tell apart from working. Dragging the window turns out to **activate
LyriSync**, so our own identifier arrived as the frontmost app and the
self-filter refused to learn from every drag; the map could never gain an
entry. Our own activation is now dropped rather than believed, so a drag
records against the last app that was not us.

The rest is feedback, because implicit learning with nothing to see is
indistinguishable from a broken feature. The menu carries a live readout —
how many apps are remembered, and where the app in front is placed — and
`LYRISYNC_LOG=DEBUG` prints the whole chain, one line per decision, with
every refusal naming itself. Verified with real mouse events against the
built bundle, including switching between full-screen Spaces.

## Milestone 14 — per-app window position

The window returns to wherever you last put it for whichever app just
came to the front, learned implicitly by watching you drag it — there is
no save action. Frontmost apps come from an `NSWorkspace` notification
that needs no permission; an app must hold the front for 400 ms before
the window follows, so a Cmd-Tab sweep does not drag it across the
screen. Off by default, and off removes the subscription rather than
ignoring it.

## Milestone 13 — the feel pass (and 13.1, 13.2)

No new behaviour; four changes to how the window reads. Lines **rise** as
they change rather than only fading, over 520 ms scheduled entirely before
the line is due. The panel gained a one-device-pixel hairline and the
system's own `NSWindow` shadow. The album tint was cut by two thirds — the
contrast floor permitted more than the eye wanted. The type hierarchy went
from 1.29x and one weight step to 1.54x and three.

**13.1** re-specified the tint as the *chroma* of the finished panel
rather than an HSL saturation, after diagnosing why album colour looked
switched off in light mode; the saturation that delivers a wanted chroma
is bisected for, because correcting the closed form iteratively
oscillates. The line change was slowed from 100 ms to 260 ms per phase,
with sine easing.

**13.2** fixed a line change that played twice — the choreography had
outgrown the poll interval, so a poll landing mid-flight re-armed the
timers and snapped the display back. Changes are now deduped by target
line index. The album colour moved to the hairline, the one surface with
no contrast obligation, where it delivers the same chroma for every hue
and the same amount in light mode as in dark.

## Milestone 12 — the global hotkey (and 12a, 12b)

**⇧⌘J** shows and hides the lyrics from anywhere, through Carbon's
`RegisterEventHotKey` (via ctypes) so that no Accessibility permission is
needed. Also fixed the monitor's lost `stop()`, which had been costing
every quit a poll interval and occasionally outliving shutdown.

**12a** made the window follow the system appearance in both directions,
live, with a full light palette pinned role by role against the dark one —
and moved the hotkey from ⇧⌘L after a collision.

**12b** added the album-colour layer: the cover supplies a hue and nothing
else, extracted by a dominant-hue vote and cached as three integers.

## Open at Login

`SMAppService.mainApp`, with the menu entry re-reading what macOS thinks
rather than what was last clicked.

## Milestone 11 — a real app

PyInstaller bundle, ad-hoc signed, `make app`. `LSUIElement` and
`NSAppleEventsUsageDescription` in `Info.plist`; the bundle identifier
shares one settings plist with a terminal run. The speak button became an
SF Symbol.

## Milestone 10 — native look and feel (a, b, c)

**10a** the menu bar app: one `QMenu` serving both the menu bar item and
the window's right-click menu, and show/hide that stops nothing.

**10b** the vibrancy material, the system font with weights per row, the
scrim, and the measured contrast floor.

**10c** verified the material on screen rather than through its handle,
and tuned the scrim to the measurement.

Between them, a CI fix that installed PySide6's system libraries so the
window tests actually ran, an assertion that no test is ever skipped on
the runner, and the test-escape guards after a live LRCLIB fetch aborted
CI mid-handshake.

## Milestone 9 — tap-to-sync (and 9.1)

Songs with plain lyrics only can be timed by hand: the track restarts and
a wide bar is tapped once per line. Saved as `.lrc` in `.user_syncs/`,
consulted ahead of cache and network, only ever written on a complete
pass.

**9.1** kept the just-stamped line on screen (watching it run out is the
cue for the next tap) and made re-syncing reachable — a re-sync draws its
lines from the sync it replaces, so it works offline.

## Milestone 8 — echo practice

Each loop pass alternates with a silent, user-paced window to sing the
line back. **8.1** made the attempt end on the user's own button rather
than on a timer.

## Milestone 7 — spoken reference

The current line read aloud by macOS's Korean voice at a slow, adjustable
rate, pausing and resuming the music around it.

## Milestone 6 — line looping

Repeat the current line until released, with a 0.46 s seek lead and grace
windows so ordinary position jitter does not cancel it.

## Milestone 5 — Korean romanisation

An optional Revised-Romanization line under the current lyric, offered
only where hangul is on screen in a form it can sit under.

## Milestone 4 — the window grows up (and 4.2–4.4)

DJ-narration and ad items handled as non-music (header only, never a
lookup), errors as a retryable state rather than "no lyrics", plus drag
clamping, debounce, and the Spaces flags. **4.4** found that a full-screen
overlay needs the accessory activation policy, not just collection
behaviour: a Regular-policy app switches Spaces when activated.

## Milestone 3.1 — one batched poll, off the UI thread

Six AppleScript calls became one, moved onto a worker thread, with a cache
poisoning fix and a clean SIGINT.

## Milestone 2 — lyrics

The LRCLIB provider, the local cache keyed by track ID, the synced → plain
→ none fallback chain, the sync engine, and a terminal runner.

## Milestone 1 — the player monitor

Polling the Spotify desktop app over AppleScript and turning polls into
track/position/state events. No Web API, no credentials.
