# Spotify integration and polling

How SottoVoce knows what is playing, and why it asks the way it does.

## AppleScript, not the Web API

The app talks to the Spotify **desktop app** through a single batched
AppleScript call every ~300 ms. It never touches the Spotify Web API.

That decision buys three things:

- **No credentials, no OAuth, no server.** Nothing to sign up for, nothing
  to store, nothing to leak. The app has no account of yours at all.
- **The truth, not a copy of it.** The Web API reports what Spotify's
  servers believe; AppleScript reports what the app on this Mac is
  actually doing, including the playback position it is at right now.
- **No rate limit** worth caring about at a poll every 300 ms.

The cost is macOS-only and an **Automation** permission prompt on first
run. `NSAppleEventsUsageDescription` in the bundle's `Info.plist` is
load-bearing rather than paperwork: without it macOS refuses the calls
*silently*, and the app looks broken in the most confusing way possible —
window up, menu bar item present, never finds a song.

## One call, one snapshot

Every poll runs a single `osascript` expression that returns the player
state and six track fields together: the Spotify URL (which carries both
the ID and the URI kind), title, artist, album, duration and position.

Six separate calls would be six process launches per poll, and six chances
to catch the player mid-track-change and come back with a mix of two
songs.

The artwork URL, added later for the album-colour layer, gets a `try` of
its **own** nested inside the script's existing one. Appended to the same
statement, a Spotify build that would not answer `artwork url` would fail
the whole expression and take the six track fields with it — the app would
show a running player and never find a song, for the sake of a colour.
Nested, the cost of that failure is one missing line.

Each poll is stamped with the wall-clock time it landed (`polled_at`).
Anything needing a fresher position than the last poll — the tap-to-sync
stamper — interpolates forward from that stamp rather than running its own
`osascript` on the UI thread. The UI thread never shells out.

## Track identity includes the URI kind

A track is identified by `(kind, id)`, not by ID alone. Spotify reuses IDs
across URI schemes: `spotify:track:…` is a song, `spotify:media:…` is DJ
narration or a share item. A narration item turning into the song it
announced keeps the same ID and changes only the scheme, so identity that
ignored the kind would call that "the same track" and never look the song
up.

Non-music items (`kind != "track"`) never touch the lyrics cache or the
network at all. The window shows the header and an empty body — there is
nothing to look up, and looking it up anyway would poison the cache with
narration under a song's ID.

`artwork_url` is deliberately **not** part of that identity. A cover
arriving a poll later than the metadata must not read as a different song.

## Events, not polls, reach the UI

The monitor turns polls into three callbacks — track changed, position
updated, state changed — and the window never sees a poll that changed
nothing. The monitor and the lyrics provider know nothing about the UI;
they are importable and testable without Qt.

Polling runs on a worker `QThread`. Signals cross to the UI thread by
queued connection, so no widget is ever touched from the polling thread.

## Stopping

The monitor's stop is a `threading.Event` set once and never cleared —
**not** a flag the loop raises when it starts. The old shape lost any
`stop()` that landed between starting the thread and the thread body
beginning, and the loop then ran forever.

That bug was found while building the global hotkey rather than by the
suite: it showed up as a 3-second stall in one visibility test on every
run, and an intermittent "monitor thread outlived shutdown" in teardown —
which is one bounded wait away from destroying a running `QThread`, which
is a `qFatal` that aborts the process.

The remaining poll interval is now *waited on* rather than slept through,
so quitting does not first sit out a poll. Measured: a real run quits in
0.3 s, and the test suite went from 4.2 s to 1.3 s.

## Transient failures

A poll that fails (`osascript` timeout, Spotify quitting mid-call) keeps
the previous snapshot rather than reporting "nothing is playing". Spotify
not running and Spotify failing to answer one question are different
things, and only the first should empty the window.

## A Mac with no Spotify on it

The snapshot script opens with

```applescript
if application "Spotify" is not running then return "not_running"
```

which reads like the answer to this and is not, because it is never
reached. Everything below it is Spotify's **own** terminology — `player
state`, `current track`, `spotify url`, `player position` — and AppleScript
resolves terminology at **compile** time, out of the application bundle on
disk. With no bundle to read, the script does not run and report "not
running"; it fails to compile. Measured, by asking with an application
name that is not installed:

```
141:146: syntax error: Expected “,” but found identifier. (-2741)
```

What that produced was an app that reported *nothing*: `poll_once`
swallowed the error, no state callback ever fired, and osascript was
spawned three times a second forever for a script that could not compile.
The window happened to look right — "Spotify is not playing" is its
initial state — which is exactly why nothing had ever noticed.

The fix is a second script that needs no dictionary at all. `running` is a
property of AppleScript's own generic application class, so
`_RUNNING_SCRIPT` compiles anywhere:

```applescript
if application "Spotify" is not running then return "not_running"
return "running"
```

It is asked **only when the snapshot fails**, which is what tells a Mac
with no Spotify apart from a Mac whose osascript timed out while Spotify
sat right there — the second is the transient failure the poll loop has
always kept state across, and it is still re-raised.

And the answer is **remembered**, because the two questions cost very
different amounts:

| | Spotify installed | application absent |
|---|---|---|
| snapshot script | 184 ms | fails to compile |
| running probe | 37 ms | 182 ms |

The 182 ms is LaunchServices going to look for something that is not
there. Without the memory, a Mac with no Spotify pays for a doomed compile
*and* a probe on every poll; with it, it settles into the probe alone —
which is also what notices Spotify being installed later, since it is
asked again every time.

`run script` was measured as the alternative — one call always, with the
terminology-dependent half deferred to runtime — and rejected: 199.9 ms
against 183.9 ms median, 9% of a 300 ms poll interval paid by every Mac to
fix the case of the Mac that has none.

`poll_once` now logs the failure it used to swallow, at debug rather than
warning: a genuinely transient failure happens, and one line three times a
second is a stream rather than a diagnostic.
