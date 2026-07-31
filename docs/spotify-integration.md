# Spotify integration and polling

How SottoVoce knows what is playing, and why it asks the way it does.

## AppleScript, not the Web API

The app talks to the Spotify **desktop app** through AppleScript. It never
touches the Spotify Web API.

That decision buys three things:

- **No credentials, no OAuth, no server.** Nothing to sign up for, nothing
  to store, nothing to leak. The app has no account of yours at all.
- **The truth, not a copy of it.** The Web API reports what Spotify's
  servers believe; AppleScript reports what the app on this Mac is
  actually doing, including the playback position it is at right now.
- **No rate limit** worth caring about.

The cost is macOS-only and an **Automation** permission prompt on first
run. `NSAppleEventsUsageDescription` in the bundle's `Info.plist` is
load-bearing rather than paperwork: without it macOS refuses the calls
*silently*, and the app looks broken in the most confusing way possible —
window up, menu bar item present, never finds a song.

## The query is sent from this process

For thirteen milestones every query was `subprocess.run(["osascript", ...])`,
three times a second, forever. Measured on an M4 against the identical
script sent through `NSAppleScript` in-process:

| | CPU per query | wall |
|---|---|---|
| `osascript` subprocess | 58.8 ms | 200 ms |
| `NSAppleScript`, compiled once | 5.5 ms | 133 ms |
| `NSAppleScript`, compiled each time | 24.3 ms | 133 ms |

Almost none of that 58.8 ms was the question. It was fork, exec,
LaunchServices, TCC and the AppleScript framework being loaded and thrown
away — and it did not land only on this app. Sampling every process on the
machine across a polling window against an idle control, four daemons woke
on every poll and were flat without one:

| | polling | idle |
|---|---|---|
| `loginwindow` | 11.0% of one core | — |
| `tccd` | 8.6% | — |
| `launchservicesd` | 7.8% | — |
| `runningboardd` | 2.7% | — |

The script is compiled **once** and kept, and every execution is
**serialised behind one lock**. That lock is measured rather than
defensive: three threads executing one compiled script concurrently took
6.8 s per execution against 0.13 s serialised, with no errors and no wrong
answers. The monitor's thread and the worker pool's seek/pause/resume are
the two callers, and they do collide.

## One call, one snapshot

Every query runs a single AppleScript expression that returns the player
state and six track fields together: the Spotify URL (which carries both
the ID and the URI kind), title, artist, album, duration and position.

Six separate calls would be six round trips, and six chances to catch the
player mid-track-change and come back with a mix of two songs.

The artwork URL, added later for the album-colour layer, gets a `try` of
its **own** nested inside the script's existing one. Appended to the same
statement, a Spotify build that would not answer `artwork url` would fail
the whole expression and take the six track fields with it — the app would
show a running player and never find a song, for the sake of a colour.
Nested, the cost of that failure is one missing line.

Each answer is stamped with the monotonic time it landed (`polled_at`).
Anything needing a fresher position — the tap-to-sync stamper — interpolates
forward from that stamp rather than asking again. The UI thread never
blocks on Spotify.

### The timeout is written into the script

`subprocess.run`'s own timeout used to bound a query. `NSAppleScript` has
no equivalent: it sends with the Apple Event Manager's default, which is
about a minute, and a minute is one wedged Spotify away from a monitor
thread that outlives shutdown's three-second wait — which is a `QThread`
destroyed while running, which aborts the process.

`with timeout of 2 seconds` is AppleScript's own, needs no application
dictionary, and is the same two seconds `subprocess.run` was given.

## Being told, instead of asking

Spotify broadcasts `com.spotify.client.PlaybackStateChanged` on the
system-wide distributed notification centre. `player_events.py` observes
it, and every case below was **driven and timed** rather than assumed:

| what happened | what arrived | how long it took |
|---|---|---|
| pause | `Player State = Paused` | 0.50 s |
| play | `Player State = Playing` | 0.11 s |
| skip to the next track | the new track, position 0 | 0.14 s |
| a track ending on its own | the new track, position 0.008 | inside 0.5 s |
| Spotify quitting | `Stopped`, no `Track ID` | 0.89 s |
| Spotify launching | `Stopped` then `Paused` with the track | 0.67 s |

The quit and the launch matter more than they look: the case with no
notifications in it announces its own beginning and its own end.

**A seek announces nothing.** Driven twice from AppleScript and once by
`previous track` restarting the current song, nothing arrived on any
occasion. That is the entire remaining job of the poll loop.

The observer must be registered **by name**: registering with `name=None`
to watch everything receives nothing at all on a modern macOS, measured
over 32 seconds of driving Spotify through eight commands with zero
delivered, against all of them delivered when the name was given.

### The payload is thrown away

The notification carries thirteen keys — `Album`, `Album Artist`,
`Artist`, `Disc Number`, `Duration`, `Has Artwork`, `Name`, `Play Count`,
`Playback Position`, `Player State`, `Popularity`, `Track ID` (the full
URI) and `Track Number` — and the app uses none of them. It is a
**doorbell**: "ask again, now". Two reasons, and the second settles it:

- there is no `artwork url` in it, only `Has Artwork`, so a track change
  would have to ask Spotify anyway for the album colour to work.
- track identity would otherwise have two definitions, one parsed from
  the snapshot script and one from a dictionary of Objective-C strings.

## Between queries, the position is arithmetic

The window still hears a position every 300 ms, because the predicted line
swap reasons about that interval. Most of those updates no longer cost an
Apple event: the position is carried forward from the last answer on the
monotonic clock.

That is exact rather than approximate, and it was measured: checked against
Spotify's own answer every five seconds for 92 seconds of one track, the
largest disagreement was **1.4 ms**, with no trend. Spotify's player
position and this machine's monotonic clock are the same clock. So the
reconciliation poll exists to catch **seeks**, not to correct drift.

A position carried past the end of the track is not carried at all — the
song has finished, and the answer is to go and ask.

### How often it actually asks

`RECONCILE_SECONDS = 1.0`, and it is a trade stated as one. A query costs
4.3 ms of CPU, so the loop costs 4.3 ms / T:

| interval | cost |
|---|---|
| 0.3 s (what it used to be, always) | 1.43% of one core |
| 0.52 s (one line change) | 0.83% |
| **1.0 s** | **0.43%** |
| 2.0 s | 0.21% |
| the `osascript` subprocess at 0.3 s | 19.6% |

1.0 s buys most of what there is to buy and leaves a seek corrected inside
about two line changes, which is the unit the window already moves in. The
cost is honest: a seek made in Spotify's own window is picked up in up to
a second where it used to be a third of one.

A seek **this app** makes is never waited for. `set_position`,
`pause_playback` and `resume_playback` all say so, in a `finally`, because
a command that failed is exactly as much of a reason to go and look as one
that worked. The loop's wrap, tap-to-sync and echo practice move the
position several times a song and none of them waits.

A seek is also an **answer**, not only a question, and `set_position`
records one: `moved()` says where this app put the player, and the
position is carried forward from there rather than from the last query.
Saying "go and ask" is enough almost all of the time — but `poll_once`
clears the wake *before* the query and gives up when it raises, so a
transient failure spends the notification and the next answer is a whole
reconciliation interval away. Measured, with one failed poll after a
loop's wrap seek: the window was told a position **9.673 s** from where
Spotify was. With the recording, 0.000 s.

It is recorded on **success only**, while `disturb()` stays in the
`finally`. A seek that failed moved nothing, and its whole signal is the
position drifting out of the loop's bounds — the one thing that must not
be papered over. And it is applied to the **last** answer rather than to a
fresh one: one lock means a query cannot execute while a seek is
executing, so an answer that has just come back is always stamped after
any seek that has already finished. Both stamps are read the instant that
call's own round trip came back, which is what makes them comparable.

### The slower rate is lost, not earned

The first version earned it from the first announcement that arrived, and
measured as no saving at all: announcements only arrive when something
*changes*, and a 60-second window on one uninterrupted song produced 197
queries — exactly the old rate.

So it starts the moment the observer registers, and a track or state change
discovered by **asking**, with nothing having rung for it, takes it away
again. The next properly announced change gives it back. A Mac where the
observer will not install, or a Spotify that does not announce, behaves
exactly as this app did before any of this existed, and nothing has to
sniff a version.

The ring is counted **after** the query and against the previous poll's
reading, so an announcement landing during a 133 ms round trip still counts
for the change that answer is about to report. Counted before, a track
change racing its own announcement would read as a change nobody announced,
and the doorbell would be blamed for arriving on time.

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
arriving a query later than the metadata must not read as a different song.

## Events, not polls, reach the UI

The monitor turns ticks into three callbacks — track changed, position
updated, state changed — and the window never sees a tick that changed
nothing. The monitor and the lyrics provider know nothing about the UI;
they are importable and testable without Qt.

The loop runs on a worker `QThread`. Signals cross to the UI thread by
queued connection, so no widget is ever touched from it. The announcement
is delivered on the **main** thread, and all it does there is set a flag
the monitor's thread reads.

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

The loop now *waits on the wake signal* rather than on the stop, so a
doorbell or one of this app's own seeks interrupts it. `stop()` sets both,
and `_stop` is still the only thing the loop's condition reads, so a stop
can no more be lost now than it could before.

## Transient failures

A query that fails (a timeout, Spotify quitting mid-call) keeps the
previous snapshot rather than reporting "nothing is playing". Spotify not
running and Spotify failing to answer one question are different things,
and only the first should empty the window.

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
running"; it fails to compile.

What that *costs* depends entirely on where it is compiled, and that is
what changed:

- inside a fresh `osascript` process it failed with a syntax error in
  182 ms, silently, three times a second, forever:
  `141:146: syntax error: Expected “,” but found identifier. (-2741)`
- inside **this** process it does not fail. macOS puts up its "Where is
  Spotify?" application chooser, in front of the user, and blocks the
  thread that asked until somebody dismisses it. Measured, by compiling
  against an application name that is not installed: still blocked after
  five minutes, with a file panel on screen owned by the app.

And it is not a property of the `tell` block. The dictionary-free probe
this app used to keep for exactly this case — `if application "X" is not
running` and nothing else, which compiles anywhere — puts up the **same**
dialogue, because what cannot be resolved is the application, not its
vocabulary. **There is no AppleScript that is safe to ask about an
application that might not be there.**

So the gate stopped being a script:

```python
NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.spotify.client")
```

0.017 ms of CPU against 25.7 ms for the AppleScript probe it replaces. No
Apple event, no LaunchServices search, no permission. It is asked **before**
anything is compiled or sent rather than after a failure, and it lives
inside `_ask` — the one way anything reaches Spotify — so a command added
later cannot miss it.

An application that is running is one whose bundle is on disk, so the
chooser has nothing to ask about. And because the gate is asked every time,
it is also what notices Spotify starting later: nothing needs a restart,
and nothing has to be remembered.
