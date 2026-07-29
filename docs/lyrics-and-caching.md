# Lyrics sources and caching

Where lyrics come from, what is stored, and what is safe to delete.

## The fallback chain

For each track, in order:

1. **Your own sync**, if one exists in `.user_syncs/`.
2. **The local cache** in `.lyrics_cache/`, keyed by Spotify track ID.
3. **[LRCLIB](https://lrclib.net)**, itself in three steps: an exact `get`
   with title, artist, album and duration; the same `get` without the
   album; then a `search`. LRCLIB's exact endpoint 404s when Spotify's
   album name or duration does not match its record precisely, and the
   album name is the field most likely to differ (deluxe editions,
   regional releases) — so dropping it is tried before giving up on an
   exact match.
4. **Synced → plain → "no lyrics"**, whichever the result supports.

Your own syncs are consulted first, always. They are the one source the
app knows to be about *this* recording, because you timed it against this
recording.

## Choosing a search result

An exact `get` is trusted. A `search` result has to earn it: same title
and artist (case-insensitive), a duration within 10 seconds of what
Spotify reports when a duration is known at all, and synced timings
preferred over plain.

The rule behind that: **prefer no lyrics over mismatched-duration
lyrics.** A wrong sync is worse than no sync — it puts confident,
authoritative-looking text on screen at the wrong moments, and the user
has no way to tell it is wrong except by disbelieving the app.

## What gets cached, and what never does

Only **definitive** answers are cached:

- Lyrics that were found — cached.
- A genuine 404, meaning LRCLIB says this track has no lyrics — cached as
  a negative result, so the app stops asking.
- A network error, a timeout, a 5xx — **never** cached. It is not an
  answer about the song; it is an answer about the network. These surface
  as a retry state in the window and re-attempt every 30 seconds.

Conflating those two is how an app ends up permanently convinced a song
has no lyrics because of one bad minute of Wi-Fi.

## Your syncs are not cache

This is the sharpest line in the whole storage design.

`.lyrics_cache/` is **derived data**: everything in it can be fetched
again, and deleting it is a safe reset. `.artwork_cache/` is the same —
three integers of JSON per track (see [album colour](album-colour.md)).

`.user_syncs/` is **your work**. Those are plain `.lrc` files you tapped
out by hand, and nothing in the app or its documentation may delete them.
No "clear cache" action touches them. That is enforced, not merely
intended: a test asserts that exactly one module in the codebase writes to
the user-sync directory, and that the album-colour module may not so much
as *mention* it — a module that learns to write there has to argue for
itself in that test first.

A sync is written only when a pass is finished. There are no partial
saves, so there is never a half-timed file to reason about.

## Re-syncing works offline

A re-sync takes its lines from the sync it is replacing, **not** from a
fresh fetch. A completed pass stamps every non-blank plain line, so the
stored lines *are* the song's lines, already timed once. Deriving the new
pass's lines from them means a re-sync still works with no network, and
after `.lyrics_cache/` has been cleared.

LRCLIB's own timings are never offered for overwrite. "Re-sync this song"
appears only when the sync on screen is yours; someone else's timings are
not yours to replace, and re-syncing them would silently fork from the
shared source.

## Stale results are dropped, not displayed

A fetch is dispatched per track and carries the track ID it was for. If
the track changed while it was in flight, the result is rejected by the
view model rather than shown — the provider has already cached it, so
nothing is wasted, but it never reaches the screen for the wrong song.

The same guard covers a fetch that lands *under* a tap-to-sync pass in
progress. That pass is modal and user-driven; a retry that completed
underneath it must not tear it down. The result is held and becomes where
cancelling lands.

## Files

```
.lyrics_cache/    derived — JSON per track ID, including definitive "no lyrics"
.artwork_cache/   derived — the album colour, three integers per track
.user_syncs/      yours   — hand-made .lrc files, which the app only ever adds to
```

The two derived directories can be thrown away at any time: everything in
them can be fetched again. The third cannot.
