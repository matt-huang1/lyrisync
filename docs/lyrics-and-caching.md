# Lyrics sources and caching

Where lyrics come from, what is stored, and what is safe to delete.

## The fallback chain

For each track, in order:

1. **Your own sync**, if one exists in `.user_syncs/`.
2. **The local cache** in `.lyrics_cache/`, keyed by Spotify track ID.
3. **[LRCLIB](https://lrclib.net)**, itself in three attempts: an exact
   `get` with title, artist, album and duration; the same `get` without
   the album; then a `search`. LRCLIB's exact endpoint 404s when Spotify's
   album name or duration does not match its record precisely, and the
   album name is the field most likely to differ (deluxe editions,
   regional releases) — so dropping it is tried before giving up on an
   exact match.
4. **Synced → plain → "no lyrics"**, whichever the result supports.

Your own syncs are consulted first, always. They are the one source the
app knows to be about *this* recording, because you timed it against this
recording.

### The three attempts go out at once

The fallback is a **preference between answers**, and it never depended on
asking one question only after another had failed. So all three attempts
are made together and read back in the order above: the most precise
answer wins, whichever arrives first, and a lookup whose exact match hits
costs one attempt's time rather than three.

Two things that behaviour has to keep, and does:

- An error on an attempt that **outranks** an answer is still a retry
  state. Sequentially, an exact match that failed ended the chain and
  `search` was never asked; concurrently it *is* asked, and its looser
  answer must still be refused — caching it because the precise attempt
  happened to fail would write a wrong answer down permanently.
- An attempt that never comes back has an **unknown** outcome, not a
  negative one. Falling through to a looser answer would be treating "we
  could not ask" as "the answer was no".

The cost, stated rather than buried: an uncached track now asks LRCLIB up
to three questions where it used to ask between one and three. LRCLIB is a
free service, so this is a real cost — the mitigation is the one that was
always there, that a lookup happens once per track ever and the answer is
cached. A track with no album reported asks two questions, not three,
because the first two would otherwise be the same request.

### Connections are kept alive

Every request used to open a connection and close it again, paying for a
DNS answer, a TCP handshake and a TLS handshake before the server had
heard the question — measured at 2 + 41 + 60 ms, about 105 ms, *per
request*. The connections are now pooled: lrclib.net holds an idle one for
at least four minutes (measured at 10, 30, 60, 120, 180 and 240 seconds),
which is longer than most songs, so the next track's lookup usually starts
with the handshakes already paid.

A pooled connection can be closed by the server while it sits idle, and
nothing says so until the next request on it fails. That is ordinary
rather than exceptional, so it costs one retry on a fresh connection —
and only when the failed connection was a reused one, or an unreachable
network would be tried twice and every real failure would take twice as
long to report.

### What it costs now

Measured against the live service, the same three URLs asked both ways,
alternating so server variance hits both arms equally:

| | sequential | concurrent |
|---|---|---|
| three-attempt lookup, median | **5973 ms** | **1811 ms** |
| the first attempt's own time | 1567 ms | 1602 ms |

The second row is the important one: LRCLIB is no slower under three
concurrent requests from one client, so a track whose exact match hits is
neither faster nor slower than before. What went away is the waiting for
attempts that were only ever asked because an earlier one failed.

Almost all of what remains is LRCLIB's own response time, which was
measured between 0.7 s and 4.8 s per request and varies far more than
anything this app controls. `gzip` was measured and **not** adopted: it
takes the 250 KB search response down to 25 KB, but the body transfer is
only ~130 ms of a ~1500 ms request and the one comparison available did
not show a win worth a decode path.

## While the lookup is out: the title card

A track change puts the song's name in the window for up to two seconds —
the song announcing itself, instead of a loading indicator over an empty
panel. It was undocumented until session B, which is how the following
went unnoticed for as long as it did.

The card is a floor on how long that gap **looks**, not on how long it
lasts. It used to run its full two seconds whatever happened underneath,
so it was a delay the app was adding rather than a gap it was filling. A
cache hit is **0.02 ms** — a replayed track had its lyrics ready
immediately and then watched the card for two full seconds.

It now hands the window back the moment there is **something to show**,
which is deliberately not the same as "the fetch finished":

| | |
|---|---|
| lookup still out | card stays — this is the gap it exists for |
| plain lyrics arrived | card ends, the text is there |
| synced lyrics, playback past the first line | card ends, the line is there |
| synced lyrics, joined before the first line | **card stays** |
| no lyrics, or a failed lookup | card ends, the message says so |

The fourth row is the whole reason the rule is about the display rather
than the fetch. A song whose first lyric is at 0:12 has lyrics and nothing
to put on screen yet; ending the card there would trade two seconds of the
song's name for ten seconds of an empty window.

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
