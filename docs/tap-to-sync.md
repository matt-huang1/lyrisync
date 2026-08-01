# Tap-to-sync

Timing a song by hand: when LRCLIB only has plain lyrics for it, or when
it has nothing at all and you bring the words yourself.

## The pass

Right-click → **Sync this song**. The track seeks to 0 and resumes, and
the window turns into a stamping surface: the line you are waiting for in
the middle, the line you just stamped above it, the next two beneath. You
tap a wide bar once as each line begins.

The line just stamped stays on screen deliberately. The singer is still
partway through it, and watching it run out is the cue for the next tap —
a display that cleared it would leave you timing from silence.

Showing the next two lines matters for the same reason: you should not be
reading a line for the first time at the moment you need to stamp it.

- **↩** undoes the last tap.
- **✕** abandons the pass, and asks for a second click to confirm.
- Taps are ignored while playback is paused, so you can stop to catch up.
- The counter shows how far you have got.

## The timing model

A tap's timestamp is:

```
interpolated position  =  last polled position + (now - polled_at)
stamp                  =  interpolated position - reaction offset
                          clamped to >= 0 and >= the previous stamp
```

The reaction offset is 0.25 s — you tap *after* hearing the line start,
not as it starts, and every tap carries roughly that same lag.

Interpolation matters because the poll interval is 300 ms: without it,
every stamp would be quantised to the last poll and land up to a third of
a second early. The elapsed term is clamped to 2 s — polls are 300 ms
apart, so anything beyond that means the poll loop stalled (a slow
`osascript`, a wedged Spotify), and extending further would invent a
position rather than refine one. While paused, nothing is added at all:
the position does not advance, so the last reading stands.

The UI thread never runs an `osascript` to get a fresher position. The
monitor stamps each poll with the wall-clock time it landed, and the
stamper does arithmetic on that instead — a subprocess on the UI thread
would block the very frame the user is judging their tap against.

## A pass is saved only when complete

Exiting early discards. There are no partial saves.

This keeps the storage story simple — every `.lrc` in `.user_syncs/` is a
complete timing of a whole song — and it keeps the *interface* story
simple: there is no half-synced state to explain, resume, or repair.

The confirmation for abandoning is **inline**, a two-step control, never a
modal dialog. The window must never take focus or activate the app; a
modal would do both, and would also be the only thing in the app that
demands attention rather than waits for it.

## Re-syncing

Once a song carries a sync of your own, the menu offers **Re-sync this
song**: a fresh full pass from line one, replacing the old timings when
you finish it. Abandoning it leaves the sync you already had exactly as it
was — which is why the pass remembers where cancelling lands rather than
assuming it lands on plain lyrics.

Its lines come from the sync being replaced, not from a new fetch, so a
re-sync works offline and after `.lyrics_cache/` has been cleared. See
[lyrics and caching](lyrics-and-caching.md).

## When there are no lyrics to stamp

Tap-to-sync used to need LRCLIB to have answered, which made it useless in
the one situation it is best at: a song nobody has written down, or a
service that is down. When there are no lines to stamp and the song could
still have some — the lookup failed, or LRCLIB genuinely has nothing — the
menu offers **Paste lyrics to sync…** instead.

That opens a small window with a text box: paste the words, one line per
lyric, and press *Start tap-to-sync*. From there it is an ordinary pass —
the same stamping surface, the same save into `.user_syncs/`, the same
reload afterwards — and no part of it touches the network.

The two entries are never offered together. A song with lyrics has "Sync
this song"; a song without has the way to bring some.

**Why a second window.** The lyrics window is frameless, refuses focus and
shows without activating, because it is an overlay that must never take
the keyboard away from what you are actually doing. A text field is the
one thing none of that can support. So the box is an ordinary window
drawn by the system, with a title bar and a close button, and it is gone
the moment the pass begins. It borrows exactly one thing from the overlay
— staying on top — because otherwise it would be the one window you could
lose behind the one you opened it for.

**Pasting an `.lrc` file works.** What people have to hand is frequently
one, so leading timestamps are stripped and the words kept, and metadata
tags like `[ar:...]` are dropped rather than made into lines you have to
tap through. A bracket in the middle of a line is left alone: that is your
punctuation, and guessing further about your own words is how a sync loses
the line you were waiting for.

## What a pass survives

- **A fetch landing under it.** Held, not applied; it becomes where
  cancelling lands.
- **Hiding the window.** The pass keeps stamping. Hiding is a display
  choice, not a stop button.
- **Pausing.** Taps are ignored, and the tap bar shows it.

## What ends it

Spotify stopping or quitting cancels the pass, before the view model
suspends, so that resuming restores the plain lyrics rather than a dead
session. A pass that cannot be completed should not be pretended to.

## Not in v1

Starting a sync mid-song, partial saves, per-line editing or nudging of an
existing sync, and publishing syncs back to LRCLIB. Each is a real
feature; none of them is *this* feature, which is "stamp a song end to end
and get it back".
