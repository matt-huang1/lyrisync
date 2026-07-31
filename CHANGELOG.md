# Changelog

Milestones in the order they happened. Dates are commit dates; the deeper
reasoning behind each is in [docs/](docs/).

Entries before the rename below call the app **LyriSync**, because that is
what it was called when they happened.

## Milestone 22 — getting out of the pointer's way

**The opacity gesture works and is never used.** Option and a scroll over
the window has adjusted opacity since milestone 4, and it has to be
remembered, aimed and then undone — three deliberate acts for a problem
that lasts two seconds. So the pointer arriving over the window is the
request now, and a three-value setting says what the window does about it:
**Off** (the default), **Dodge**, **Ghost**.

Dodge vacates the window's rectangle and comes back exactly when the
pointer leaves. Ghost stays put, fades to 0.15 and passes clicks through
to whatever is behind it. Neither is a better version of the other: the
first is for a window parked over something you look at, the second for
one parked over something you click.

**It rides the poll the compact layout already had**, because macOS
delivers enter and leave events only to the active app and this one never
activates. Two layers, one timer, one reading of the pointer per tick:
0.973us a poll measured, 0.00097% of a core at 100ms, of which this layer
added 0.00026%.

**The region is anchored on where the window belongs**, never on where it
is, which is the one decision that stops a dodge oscillating — and leaving
takes both rectangles, so a dodged window can still be followed and
caught. The behaviour begins on the pointer arriving and nothing restarts
it, so a sync pass or an echo attempt that ends with the hand still on the
window does not step it aside from underneath.

**A dodge is a loan.** The window's real position is held for as long as
it stands aside, exactly the way the flight holds one, so a temporary
position is never learned as a per-app position, never saved at shutdown,
and never what a song anchors its width change on. A docked window docks
again to the pixel.

**The ghost's ceiling was set by arithmetic, looked at, and corrected.**
0.12 came out of the contrast maths and reads as indistinguishable from
nothing; rendered at five values in both appearances, 0.15 is where a
trace of the sung line survives and nothing else does. The measurements
followed to check it: 86% of the screen underneath untouched, and the work
under the window keeping 15.6:1 against a 4.5:1 floor that permits 0.51
and could not have picked the number.

Full reasoning in [docs/pointer-yield.md](docs/pointer-yield.md).

## Milestone 21 — one menu, natively drawn, and grouped

**The same menu looked like two menus.** The menu bar item and the window's
right-click share one object and always have, but Qt converts a tray menu
into a real `NSMenu` and draws a popup itself: one route got the system's
own font, check marks and separators, the other got Qt's widget style.

So the menu is a **model** now (`menu.py`, pure) and one native `NSMenu`
drawn from it serves both routes (`nsmenu.py`, one door onto AppKit). That
cost the Qt tray icon: Qt owns the `NSStatusItem` it makes and will not
take a menu, so **the menu bar item is this app's own** and is removed at
shutdown rather than destroyed with the window. The glyph still comes off
`symbols.py` at the screen's scale, and the sizing lesson milestone 15.1
paid for is stated directly instead of through a `QIconEngine`.

**Seventeen entries in one column became four rows and two submenus.**
Show/hide, then what is on screen, then what is about this song, then
**Position** (docking and per-app memory) and **System** (Spaces,
notifications, the menu bar item, login), then quit. Nothing that comes and
goes with the song was buried: an entry that has just appeared because it
can now act is one somebody is looking for.

Two measurements on the way: a background app really can put a native menu
on screen without coming forward (verified before anything was built), and
an attributed `labelColor` does **not** stop AppKit greying a disabled
item, so the remembered-apps rows are view-based items — the answer
`QWidgetAction` gave, in the native idiom.

## Milestone 20 — the strip gets a type size, the dock gets a shape

**Long lines in the compact strip no longer elide for no reason.** The bug
was real and the window was behaving exactly as designed: the type scale
followed the window's width, so the room for a line and the line itself
grew at the same rate and widening the strip showed the same words in
bigger type. The maths says so outright — the width cancels, and a line
past 316pt fits at no width at all.

So **the strip names its text size**, from five presets in the menu (14,
17, 20, 24, 28pt), and the width goes back to deciding how much of a line
is on screen. On the longest line in a 14-song corpus, at 28pt: a 320pt
strip shows six words, a 900pt strip shows most of it, and a 1400pt strip
shows all of it. Its height follows the size, so there is nothing to drag
vertically and it no longer offers to. The full layout is untouched — there
the type size *is* the width, which is why this control only appears inside
the strip.

The width the song fits itself to is now **capped by the screen rather than
by half of it**. Half was the only brake there could be while the type size
was the width's to decide; the brake now is the size. Measured over 776
real lines, the old cap never bound anything at 20pt or below and would
have started clipping songs routinely at the larger sizes.

**A docked window is shaped like the notch.** Square across the top,
rounded underneath, flush against the underside of the menu bar, so it
reads as the bar's own band continuing downwards instead of a floating
panel with desktop showing through two corners. It works the same on
notched and unnotched Macs, takes nothing from the menu bar, and is round
again the moment it is dragged a pixel away — the window works out whether
it is docked from where it is, so there is no state to get stuck.

**Lyrics lookups ask LRCLIB less.** The three fallback attempts used to go
out together. Measured across 30 lookups of 15 real tracks: the first
attempt answered **every single time**, in 61ms by median. So the ones
below it are now asked only when they are needed — at once if the attempt
above returns a definitive "no", after a 250ms hedge if it is slow, and
never if it answers. That is two requests in three no longer made of a free
service running on donations. When LRCLIB is having a bad day the attempts
still overlap exactly as they did, a quarter of a second later.

## Milestone 19 — the same app, a fifteenth of the energy

No feature changed. The app's twelve-hour energy impact sat beside a chat
client's and about triple Spotify's, which is not what a lyrics window
should cost, and the measurement said almost none of it was the work.

**Spotify is asked from inside this process now.** Every poll used to
launch `osascript`: 58.8ms of CPU for a question whose answer takes 4.3ms,
three times a second, forever. Almost all of the difference was fork,
exec, LaunchServices, TCC and the AppleScript framework being loaded and
thrown away — and it did not land only on us. Four system daemons woke on
every poll and were flat without one, about 30 percentage points of one
core between them.

**And it is asked far less often.** Spotify broadcasts
`com.spotify.client.PlaybackStateChanged` whenever the track or the state
changes, so the app waits to be told instead of asking: track changes,
play, pause, a song ending, and Spotify starting and quitting all arrive
within about a fifth of a second of happening. Between times the playback
position is worked out from the clock, which turns out to be exact — over
92 seconds it disagreed with Spotify by 1.4ms. What is left for the loop
to catch is a **seek**, which Spotify announces to nobody, and it looks
once a second for one. A seek made in Spotify's own window is picked up in
up to a second where it used to be a third of one; a seek this app makes
is never waited for at all.

If the announcement never arrives — an older Spotify, a Mac where the
observer will not install — the app goes back to asking three times a
second, on its own, without sniffing for a version.

**A line change costs a fifth less.** The sung line was being laid out and
rasterised from scratch 37 times per change, for a fade and a ten-pixel
rise; nothing about it moves during a phase, so it is drawn once per phase
now. And the panel underneath it is filled straight rather than as two
rounded rectangles when the repainted strip is between the corners, which
is the same pixels — asserted byte for byte, at 1x and 2x.

Measured end to end over the same minute of playback, alternating between
the two revisions: the app's CPU including its subprocesses **21.8% of one
core to 1.5%**, Activity Monitor's energy impact **4.05 to 1.45**, idle
wakeups **105/s to 21/s**, and queries put to Spotify **197 to 70**.

## Milestone 18 — the strip fits the song

In the compact layout the window now sizes itself to the track. When a
song's lyrics arrive it measures every line, takes the widest, and becomes
as narrow as it can be **without moving again for the rest of the song**.
Never on a line change: that would be a window twitching its way through a
verse. The romanisation line is measured too when that layer is on.

**The obvious way to do this is impossible**, and the reason turned out to
be a property of the app rather than a bug in it. The type scale follows
the window's width, so the room for a line and the line itself grow at
exactly the same rate and the width cancels out: a line wider than 316
points at scale 1.0 fits at *no* width, and 13 of 14 real songs are past
that. So while the strip is sizing itself the type scale is held at the
width the user chose, which turns their width into a type size and lets
the song have the rest. It is also why the strip's height never moves when
its width does.

Measured over 14 songs from a real cache: the narrowest needs a 411pt
window, the median 610, the widest 839. The width is capped at half the
screen, checked against 776 lines — on the 1710pt screen this was measured
on, nothing in the corpus is clipped; on a 1440pt screen four songs are,
which is a screen-relative cap doing its job.

The change is anchored on the window's centre, so growing and shrinking
are the same gesture in opposite directions. A **docked** window is
re-docked instead, recognised by being exactly where docking put it rather
than by a flag: measured live, it stays centred to the pixel and clear of
the notch across a resize. The travel is the same 260ms and easing as
every other movement the window makes, and Reduce Motion gets the size
without it.

It is on by default, and it is the first setting in this app that is. It
may be, because it is reachable only from inside a layout that is itself
opt-in and default off. Dragging an edge turns it off rather than fighting
you, at the start of the drag so the drag behaves exactly as it always
did. The full layout is untouched.

## Milestone 17 — a strip, and a place to put it

**Compact** reduces the window to the line being sung, with the
romanisation under it when that layer is on. No header, no line before, no
line after. The floor comes off the same type scale the full layout uses,
asked for two rows instead of five: **79 points against 183** at the
default width. Each layout keeps the height it was last left at, so
switching is not a way to lose the shape you gave it.

**Dock to top** centres the window under the menu bar on the screen it is
on. Centred on the screen rather than on what the Dock left of it, because
the menu bar spans the screen and the notch is centred on it. The notch is
cleared by the screen's own safe area rather than an assumed height, which
matters in exactly one case and it is a case people use: a menu bar set to
hide automatically gives the whole screen back and leaves the notch
exactly where it was. It is a command, not a snap — nothing holds the
window there, and it is as draggable the instant after as before.

**The overlay controls go away in the strip and come back under the
pointer.** The obvious way to do that is `enterEvent` and `leaveEvent`,
and it works perfectly while the app is frontmost, which this app never
is. Driven with the real pointer and the app backgrounded, the window
heard nothing at all: Qt installs its tracking area
`NSTrackingActiveInActiveApp`, and an accessory app that never activates
has no hover events to miss. So the window asks where the pointer is
instead, on a 100ms timer that runs only while the strip is on screen. One
poll costs 0.8 microseconds.

A long line used to wrap onto a second row that the strip had no room for,
landing halfway across the romanisation. Every number was right and it was
found by looking. Compact now elides.

A sync pass takes the full layout back for as long as it runs. Echo
practice does not: one line repeated is what the strip is for, and the
loop engages many times a song. The controls are held out while an attempt
is waiting, because that phase pauses the song and the done button is the
only way out of it.

## Session D — a polish sweep, and three settings nobody had asked about

**Every em dash in a user-visible string is gone**, and there were 29.
Three replacements, one per context: a middle dot where two things are
named side by side, following `HEADER_SEPARATOR`, which had already made
that argument for the song and its artist and was the only place obeying
it; a colon where the second half follows from the first; a comma where
the two halves are one sentence. A test scans the syntax of every module
and exempts docstrings, because the file explaining the rule contains one.

**A failed lookup will now say why, if asked.** The provider knew whether
it was a 503, a timeout, an unreachable socket or an unreadable body, and
which of the fallback attempts it happened on, and threw all of it away at
the door. It now travels to the window, where a small ⓘ beside "lyrics
unavailable, will retry" reveals it — and nothing else changed, because
the default is for the people who do not care why. A track that genuinely
has no lyrics still says "no lyrics found" and offers nothing to click.

**The three macOS accessibility display settings are followed, live.**
Reduce Motion takes the travel out of a line change and leaves the fade,
and removes the flight and the travel to a remembered position outright.
Reduce Transparency removes the vibrancy material and paints an opaque
panel. Increase Contrast lifts every text role to 4.5:1 and every mark to
3:1, measured — and dropping the material turns out to be worth more than
any recolouring: the sung line goes from 4.70:1 to 17.93:1 in dark.

**A Mac with no Spotify installed reported nothing at all.** The snapshot
script cannot compile without Spotify's dictionary, so its own "is not
running" first line was never reached, `poll_once` swallowed the error,
and osascript was spawned three times a second forever for a script that
could not run. It now answers "not running", which is the truth, from a
second script that needs no dictionary — asked only on failure, and
remembered.

The README's demo GIF is centred, and its build instructions install the
extra the suite actually needs: `".[build]"` alone builds a working app
and then fails `make test` with "No module named pytest".

## Session C — a demo of the app as it is now

**The README's GIF was 31 commits out of date.** It predated the menu bar
item, the vibrancy material, light mode, the album tint, the type
hierarchy, the flight and the rename; session B reported it and left it,
because a demo is a recording of a real session and it is the user's call
what it shows.

It now shows four things in one take. The window floats over this
repository's own source; the lines advance in time; the romanisation line
appears **only under the lines that need it** — "they don't know 'bout us"
alternates English and hangul every ~3.4 s, so the layer is seen arriving
and leaving rather than merely being present; and ⇧⌘J flies the window
into the menu bar item.

**The middle of it is per-app position memory**, which no previous demo
showed at all. Two backdrops, one position taught for each by dragging
with the layer on — the editor at (310, 90), the Finder window on the
repository folder at (870, 440) — and then the apps are switched. The
window is seen taking itself across the screen, twice, because that is
what it does.

20.5 s, 720x462, 15 fps, 891 KB — still a fifth of the size budget, so
neither the frame rate nor the width had to be given up for it.

The recording rules that came out of it are in
[CLAUDE.md](CLAUDE.md#packaging-and-identity); what went wrong on the way
there — a throwaway editor profile, a Focus mode with no CLI, a pointer
that turned out to be captured, a take ruined by Spotify bringing itself
to the front, and a file browser quietly showing the developer's username
— is in [the decision log](docs/decision-log.md).

## Session B — lyrics arrive sooner, and the log becomes two files

**The fallback chain was sequential, and cost the sum of its attempts.**
Measured against LRCLIB before anything was touched, per attempt and per
step: DNS 1–4 ms, TCP ~41 ms, TLS ~55–70 ms — about **105 ms of handshake
per request** — and then a time-to-first-byte of **0.7 to 4.8 seconds**,
which is LRCLIB's own thinking time and is over 90% of every lookup.

Nothing about the chain needed to be sequential. The fallback is a
*preference between answers*, not a dependency: the attempts now go out
together and are read back in priority order, so the most precise answer
still wins and a lookup costs the longest attempt it actually needs.
A/B against the live service, same URLs, alternating so server variance
hit both arms equally:

| | sequential | concurrent |
|---|---|---|
| three-attempt lookup, median | 5973 ms | **1811 ms** |
| the first attempt's own time | 1567 ms | 1602 ms |

The second row is what made the first trustworthy. An end-to-end harness
first showed the exact-match case at 2154–5514 ms against ~1000 ms the day
before — which looks exactly like a regression and was server variance.
The A/B is what told them apart: LRCLIB is no slower under three
concurrent requests from one client.

Two edges preserve the old semantics exactly, and both have tests. An
error on an attempt that **outranks** an answer is still a retry state —
sequentially `search` was never asked, and caching its looser answer
because the precise attempt failed would write a wrong answer down
permanently. An attempt that never returns has an **unknown** outcome, not
a negative one.

Stated rather than buried: an uncached track now asks LRCLIB up to three
questions where it asked between one and three. It is a free service; the
mitigation is that a lookup happens once per track ever.

**Connections are kept alive**, which is worth doing only because
lrclib.net holds an idle one for at least four minutes — measured at 10,
30, 60, 120, 180 and 240 seconds. In the app: the first track opened three
connections, the second opened none. A pooled connection the server
dropped while idle costs one retry on a fresh one, and only when the
failed connection was reused.

`gzip` was measured and rejected: 250 KB of search response becomes 25 KB,
but the body read is ~130 ms of a ~1500 ms request and the comparison came
back slower.

**The title card was holding lyrics that had already arrived.** Its two
seconds ran whatever happened underneath, so it was a delay the app added
rather than a gap it filled — and a cache hit is **0.02 ms**, so every
replayed track had its lyrics ready immediately and then watched the song's
name for two seconds. It now yields the moment there is *something to
show*, which is deliberately not "the fetch finished": a synced song joined
before its first line has lyrics and an empty screen, and ending the card
there would trade two seconds of the song's name for ten of nothing.

**Song · Artist.** The header separator is a middle dot; an em dash is
punctuation and reads as one between two proper nouns. It had two
definitions and a third format in the monitor tool, which printed the
artist first — all three now use one function.

**`CLAUDE.md` split in two.** It is instructions for working in the repo:
architecture in brief, the session rules, the guards, and the constraints
that must not be reintroduced. The history moved whole to
[docs/decision-log.md](docs/decision-log.md) — every entry kept, including
the ones that contradict each other, because a `SUPERSEDES` entry is only
legible beside what it supersedes.

Also found in the tidy pass: the title card had never been documented in
any milestone; `vibrancy.autoresize_mask()` had been dead since milestone
10b; the two terminal tools had no tests at all, which is exactly why a
format could drift there unnoticed; and the test count was written in two
files and stale in both, so it now lives in one.

## Session A — the app is called SottoVoce

A rename, and one thing a rename cannot be on macOS: free. The package,
the module paths, the three console scripts, the app bundle, the icon, the
log variable and the bundle identifier all moved from `lyrisync` to
`sottovoce`.

The bundle identifier is the expensive one. macOS keys a preferences file
on it, so changing `com.lyrisync.lyrisync` to `com.sottovoce.sottovoce`
does not move the user's window position, size, opacity and toggles — it
**orphans** them, and the app opens at its first-run defaults as though it
had never been used. `settings.py` therefore copies the old plist across
once, on a launch that finds nothing of its own, and records that it did.
Copied and not moved: the old file is left exactly where it is, because
deleting a user's settings to tidy up after a rename is the tidying this
project does not do.

**The bug only a real run could find**, and it would have made the whole
migration a no-op on every Mac: `QSettings.allKeys()` does not answer for
one app. NSUserDefaults resolves through a search list, so a plist that
has never been written still reports **70 keys** from NSGlobalDomain —
`AppleLocale`, the trackpad gestures, half of System Settings. Measured,
against the developer's own preferences: "are there settings here already"
was true on a file that did not exist, so the carry refused itself. And
had it not refused, the same fall-through would have copied all 70 of
those keys *into* the app's own plist. Both halves now ask per group
(`window`, `lyrics`, `migration`), which answers 11 and 3 on the old file
and nothing at all on a new one. A test reads the keys `window.py`
actually saves out of its syntax and fails if one falls outside a group
the migration would carry.

Verified against the real file rather than a fixture: 14 settings carried,
`QPoint(293, 73)` and `QSize(489, 196)` still typed as a point and a size
on the other side, six remembered app positions intact, the old plist
byte-for-byte where it was, and a second run answering "already run".

**Two things no code can carry**, because macOS keys them on the
identifier *and* the code signature: the Automation grant (macOS asks
again on the first poll, now naming SottoVoce) and the login item
registration (Open at Login has to be switched on again). Both are stated
in the README's upgrade note rather than left to be discovered.

The published 1.0.0 download keeps its name and its hash. It was built
before the rename, the hash is a fact about those bytes, and re-pointing
either would be a claim about a file that does not exist.

A ninth guard in `conftest.py`: `settings._legacy_settings()`, the one
door onto the old plist. It is a *read* of the developer's own
preferences, which is the kind of escape that leaves nothing behind to
notice afterwards.

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

## Milestone 15.1 — the menu bar icon says the right things, live

Three fixes to the menu bar item and one addition.

**The remembered-apps list was grey.** The rows became disabled `QAction`s
when per-app forget was removed, and macOS greys disabled items — so four
remembered apps read as four things that were *unavailable* rather than as
four facts. They are `QWidgetAction`s carrying a label now, measured in a real
menu on both routes it opens: the widget row draws the same black an enabled
one does, hovering never selects it (the widget takes the mouse itself), even
forced active it draws no highlight, and clicking it leaves the menu open.
Marking it mouse-transparent was tried and is *wrong* — with the mouse passing
through, the menu selects the row on hover and it starts behaving like a
control.

**The icon only updated when the menu was opened.** The refresh ran from
`_render`, and a pause does not re-render — `player_state_changed` returns
`False` for `PAUSED` because the display text is unchanged — so the item
claimed a song was playing until somebody clicked it. It is now driven from
every position update and every state change.

**Brightness and shape are independent.** Milestone 15's three whole-glyph
states carried two questions on one axis: a paused song dimmed the icon
exactly as hiding the window did, so the one thing dimming was *for* was
indistinguishable from Spotify being paused. Now brightness asks only whether
the lyrics layer is on, shape asks only whether a song is playing (three bars
of equal length for no, short / long / short for yes), and the dot asks only
whether a practice mode is running. Practice still outranks a hidden window.
Nothing playing no longer dims anything.

Eight combinations from three booleans rather than eight drawings, which is
only affordable because the glyph is **drawn** now — three SVGs went, and this
would have needed twenty.

**New, off by default: Animate the menu bar icon.** With it on, the three bar
lengths step to the next of four arrangements each time the lyric line
advances. Not a timer and not a loop — it moves when the song does. The middle
bar is the same length in every arrangement, so "the current line is the
longest" is true of every frame it can show. Costs 0.020 ms of CPU per line
change, against the 92.7 ms one line change of the window already costs.

Two things were found by rendering the glyph and looking at it, after the
numbers had all come back healthy. The dot **overlapped** the even shape's
bottom bar by half a unit — at 16 points, a bar with a blob on the end rather
than a mark beside it. And handing the status item a 44-pixel pixmap at
`devicePixelRatio` 2 put a **clipped** glyph on the bar, two bars and no dot,
because `QIcon.availableSizes()` reports raw pixels and macOS took a 44-point
image for a 22-point slot. Five constructions were photographed on a real
status item before an icon engine — what the SVG engine had been doing all
along — came out both whole and crisp.

The closest pair at 16 points, stated rather than glossed: stopped-and-hidden
against playing-and-hidden, 9.8% of the square, both dim with only the shape
between them.

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
