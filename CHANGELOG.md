# Changelog

Milestones in the order they happened. Dates are commit dates; the deeper
reasoning behind each is in [docs/](docs/).

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
