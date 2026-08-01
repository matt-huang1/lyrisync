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

### The attempts are read in priority order, and asked only when needed

The fallback is a **preference between answers**, and it never depended on
asking one question only after another had failed. So the results are read
back in the order above: the most precise answer wins, whichever arrives
first.

Two things that behaviour has to keep, and does:

- An error on an attempt that **outranks** an answer is still a retry
  state. Sequentially, an exact match that failed ended the chain and
  `search` was never asked; once the attempts overlap it *is* asked, and
  its looser answer must still be refused — caching it because the precise
  attempt happened to fail would write a wrong answer down permanently.
- An attempt that never comes back has an **unknown** outcome, not a
  negative one. Falling through to a looser answer would be treating "we
  could not ask" as "the answer was no".

For a while all three went out together. They no longer do, and the reason
is a measurement.

### The hit rate, measured

15 real tracks, taken from the local cache so they are songs actually
played, with **Spotify's own metadata** rather than a transcription of it:
each track was made the current one so the album string and duration it
reports could be read back. The chain was then asked in order, spaced 20
seconds apart, twice, two minutes between rounds.

| which attempt answered | |
|---|---|
| `get` with the album | **30 of 30** |
| `get` without the album | 0 |
| `search` | 0 |

Every one of them synced. The first attempt's own response time: **53ms**
fastest, **61ms** median, 74ms at the 90th percentile, 103ms at the 95th,
170ms slowest.

The sample's limits, because a measurement that oversells itself is worse
than none: fifteen tracks, one person's library, and every one of them a
track that *had* lyrics. So the claim is "for tracks that resolve at all,
the album match resolves them" — not "the album match never misses". A
track whose album name disagrees with LRCLIB's record still 404s and still
falls through.

### So the chain is hedged

An attempt is asked when the chain reaches it. The ones below it go out
early only if it is taking long enough that overlapping them would actually
save something — **250ms**, which is 4x the measured median and 1.5x the
slowest response in the sample, so no lookup in it would have fanned out at
all.

| | |
|---|---|
| the attempt answers quickly | nothing below it is ever asked |
| the attempt 404s | the next one goes at once, with no hedge to wait out |
| the attempt errors | the chain raises; the rest were never going to be read |
| the attempt is slow | at 250ms the rest go out beside it, as they used to |

What that buys is **two requests of every three not made**. LRCLIB is free,
runs on donations and is under load, and asking it three questions to use
the first answer is a cost it carries so this app can save a wait that, at
these speeds, is not there to save.

What it costs is paid only in the case the concurrency was for. The same
service was measured at 0.7 to 4.8 *seconds* per request in an earlier
session; when it is that slow the hedge fires, the chain fans out exactly
as before, and the lookup is at most 250ms longer than the all-at-once
version. Against 4.8s that is 5%.

A track with no album reported asks two questions rather than three anyway,
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

Measured against the live service when the attempts still all went out
together, the same three URLs asked both ways, alternating so server
variance hit both arms equally:

| | sequential | concurrent |
|---|---|---|
| three-attempt lookup, median | **5973 ms** | **1811 ms** |
| the first attempt's own time | 1567 ms | 1602 ms |

The second row is why the concurrency was safe to keep for the slow case:
LRCLIB is no slower under three concurrent requests from one client. Those
figures are also from a period when the service was answering in seconds.
It is currently answering the first attempt in **61ms by median**, which is
what makes the hedge above the right shape: the fan-out is still there for
the bad day, and is simply never reached on a good one.

`gzip` was measured and **not** adopted: it takes the 250 KB search
response down to 25 KB, but the body transfer was only ~130 ms of a
~1500 ms request and the one comparison available did not show a win worth
a decode path. With `search` now rarely asked at all, it is further from
being worth it than it was.

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
  as a retry state in the window and are re-attempted on the schedule
  below.

Conflating those two is how an app ends up permanently convinced a song
has no lyrics because of one bad minute of Wi-Fi.

## How often "will retry" retries

The first retry is 30 seconds, which is what it has always been: most
failures are a blip and the promise on screen should be answered at that
speed. After that the interval **doubles** and stops at five minutes:

| consecutive failures | 1 | 2 | 3 | 4 | 5+ |
|---|---|---|---|---|---|
| wait | 30s | 60s | 2m | 4m | 5m |

The reason is what a stuck song used to cost. At a flat 30 seconds a
window left on a failing track through a three hour outage made **360**
requests of a free, donation-funded service that was already having a bad
day, every one of them asking the same question and getting the same
answer. On this schedule it makes **38**. The saving holds at other
lengths — 14 against 120 over an hour, 98 against 960 over eight.

The **ceiling is five minutes** because of how long songs are, not because
it is a round number. A track change re-attempts immediately whatever the
schedule says, so the ceiling only ever governs a song left on screen: of
69 tracks across 5 real albums the median is 232 seconds and 88% run under
300, so for seven songs in eight the next track gets there first.

**Only an answer from LRCLIB resets the count.** A cache hit, one of your
own syncs and a warmed track are all successes, and none of them is
evidence that the service is back — during an outage every song you have
played before still answers instantly, and a counter that reset on those
would be back to 30 seconds on every uncached track.

The count is also not reset by changing track or stopping playback. An
outage is not a property of the song on screen.

### When LRCLIB asks for a pause

LRCLIB's API documentation asks callers to honour `Retry-After` on a 429
and says that ignoring it may result in a temporary ban. So a 429 starts a
**hold**, and while it runs *nothing* leaves the app: not a retry, not a
new track's first lookup, not the album warm, and not the retry control
below. It is checked at the single point every request goes through.

Only the seconds form of the header is read. The date form would mean
trusting your clock to agree with theirs, and a skew turns "wait 30
seconds" into a pause of hours or none at all; a header the app cannot
read is treated as no header, and it still backs off on its own schedule.

A lookup the app refuses this way is shown as a failure — there are still
no lyrics — but it is **not** counted as one of theirs. It never reached
them, and counting it would grow the backoff on the strength of the
backoff.

## The rest of the album, before you get to it

While the network is up, the app quietly fetches lyrics for tracks you
have not played yet, so a later outage has less to be noticeable about. It
never blocks anything: it runs on a worker five seconds after the song's
own lookup landed, and nothing waits for the result.

Spotify's scripting dictionary describes the *current* track and nothing
else, so the track list has to come from LRCLIB too. That makes this two
stages, and which one runs depends on how much you have listened to.

**Stage one, for any album a track was played from: one search.** It names
the album's tracks and carries their lyrics with it, so whatever comes
back is kept as it stands. One request, and it is the only one most albums
ever cost.

**Stage two, once a second track from the same album plays: one lookup per
name.** A second track is the difference between a song you heard and an
album you are listening to, and it is the only signal available for that.

Measured over four real albums (47 tracks, with the track listings taken
from the iTunes catalogue):

| | requests per album | of the album, warm and usable |
|---|---|---|
| stage one alone | 1 | 26% |
| both stages | 20 | 34% |

The second stage buys another eight points for nineteen more requests,
which is a poor trade for every album and a fair one for an album you are
playing through. Splitting them is what makes that choice possible: an
album you played one track from costs a single request.

A name keeps **every** record either stage found, because LRCLIB genuinely
answers with the same title at several lengths and nobody can say which is
your recording until it plays. A warmed track is only served if one of
those records matches the duration of the track that actually starts, to
within the two seconds LRCLIB's own exact endpoint matches on — it is
standing in for the album match, so it has to be as precise as one. Stage
two therefore *adds* rather than replaces: its answer can be a different
recording than the search found, and throwing the search's away cost a
track in the measurement.

The store can say *yes* and it can say *nothing*. It can never say "this
track has no lyrics": it is a guess made without the track in hand, and a
guess may not stop the real question being asked. It lives inside
`.lyrics_cache/`, so clearing the cache clears it too.

Warming is polite by construction. Stage two's requests go out one at a
time with 350ms between them, which is the middle of the band LRCLIB's
documentation asks for; one album at a time; once per album ever; a single
failure ends that album; and neither stage runs at all while the service
is failing.

## Your syncs are not cache

This is the sharpest line in the whole storage design.

`.lyrics_cache/` is **derived data**: everything in it can be fetched
again, and deleting it is a safe reset. `.artwork_cache/` is the same —
three integers of JSON per track (see [album colour](album-colour.md)).

`.user_syncs/` is **your work**. Those are plain `.lrc` files you tapped
out by hand, and nothing in the app or its documentation may delete them.
No "clear cache" action touches them. That is enforced, not merely
intended: a test asserts which functions in the codebase write to the
user-sync directory, that only one of them knows the path to a sync
itself, and that the album-colour module may not so much as *mention* the
directory — a module that learns to write there has to argue for
itself in that test first.

A sync is written only when a pass is finished. There are no partial
saves, so there is never a half-timed file to reason about.

One other kind of file lives in that directory, and it is the app's
rather than yours: a `.published` sidecar beside a sync that has been
offered back to LRCLIB, holding the digest of the text that went. It is
there rather than in the cache because clearing the cache is a reset and
forgetting what has been published is not one — it would be the app
offering to send your work a second time. Writing it is the second
writer that had to argue for itself in the test above, and it writes a
file of its own beside a sync rather than ever touching one. See
[publishing](publishing.md).

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

They live in `~/Library/Application Support/SottoVoce/`, whichever way
the app was started:

```
.lyrics_cache/    derived — JSON per track ID, including definitive "no lyrics"
.artwork_cache/   derived — the album colour, three integers per track
.user_syncs/      yours   — hand-made .lrc files, which the app only ever adds to
                            (plus a .published sidecar per sync offered back to LRCLIB)
```

The two derived directories can be thrown away at any time: everything in
them can be fetched again. The third cannot.

Those names begin with a dot, so Finder hides them until you press
⇧⌘. — they kept the names they have always had, because everything
written about them elsewhere is still true.

Until they had a home of their own, these three were relative paths, which
meant they landed beside whatever directory the app was started in. That
is fine for a run from a checkout and wrong for an app you launch: macOS
starts one in `/`, which is read-only, so the built app could not write a
cache entry, a sync, or the journal that keeps a pass you are part way
through. Syncs sitting beside a checkout are copied in on the next launch
— copied, never moved, and never over the top of a file already there.

## Why a lookup failed

The window says **"lyrics unavailable, will retry"** and, on its own,
nothing else. That is the right default: most people want to know the
lyrics are not there and are coming back, and no more.

It was also the only thing the app *could* say. The provider knew whether
it was a 503, a timeout, a socket that never got there or a body that
would not parse, and which of the two or three fallback attempts it
happened on, and threw all of it away at `except LyricsError:`.

Now a failure carries a `FetchFailure(kind, attempt, status, detail)` from
the provider, through the fetch signal, into the view model, and out as
`Display.detail`. `failure.describe` turns it into one line — and it is
the *only* rendering of it, shared by the window and `sottovoce-lyrics`,
because two surfaces writing their own sentence about one fact is how the
window and a terminal tool came to disagree about a song's title once
already.

| what happened | what it says |
|---|---|
| a status that is neither 200 nor 404 | `LRCLIB answered HTTP 503 · album match` |
| nothing came back in time | `LRCLIB did not answer in time · search` |
| the socket never got there | `could not reach lrclib.net · title and artist` |
| an answer arrived and was not JSON | `LRCLIB's answer could not be read · search` |
| they asked us to slow down | `LRCLIB asked this app to slow down · album match` |
| we are obeying that request | `waiting, as LRCLIB asked` |

429 gets a sentence of its own because it is the one status the app *does*
something about rather than only reports. The last row has no attempt on
it, and that is deliberate: a request the app declined to make belongs to
no link of the chain, and would have been declined at every one equally.

The attempt is stamped **on the way past**: `_fetch_json` makes a request
and does not know which link of the chain it is, `_fetch` does, and
`LyricsError.at(label)` returns a *new* exception rather than mutating
one. `attempt_urls` became `attempts()`, returning `(label, url)` pairs —
the chain is two attempts long or three depending on whether Spotify
reported an album, and two lists that have to stay the same length is
exactly how a failure comes to name the wrong attempt.

**The socket's own message does not go on the window.** "[Errno 8]
nodename nor servname provided, or not known" is the right thing for a log
and the wrong thing for a 460-point HUD; the kind already says which of
the four things happened. It stays on the failure, and on the log line
that already reported it.

### The affordance

A small ⓘ (`info.circle`, with a text glyph to fall back to) beside the
message, mouse-only like everything else on this window. Clicking it puts
the reason in the row underneath, which in this mode is empty, already the
dim context colour, and already directly under the message it explains — a
second widget would have been a second thing to place, style and keep in
the type scale for a line on screen about as often as a track fails.
Clicking again puts it away.

It is placed from the *text* rather than pinned to a corner: the message
is centred and the window is resizable, so a fixed position is beside the
message at one width and stranded at every other.
`geometry.beside_centred_text` owns the rule, including the wrapping case
— where the laid-out width *is* the row and the control goes to the button
gutter.

The reveal survives a retry (which takes the mode ERROR → FETCHING →
ERROR) and not a track change. Hiding the reason under somebody
who had just asked for it would make the control feel broken; a new song is
a different failure or none at all.

**A track that genuinely has no lyrics offers nothing to click.** It says
"no lyrics found", plainly, and that difference is the point of the whole
thing.

### Asking again, now

Beside the *reason* — not beside the message — there is a retry control.
The placement is the argument: the message already says "will retry" and
means it, so a button next to that sentence would be inviting you to do
the thing the app has just promised to do for you. Beside the reason it is
for somebody who opened the explanation, read it, and knows something the
schedule does not: the Wi-Fi is back, the VPN is off.

Pressing it asks immediately and takes the interval back to 30 seconds. If
you were wrong, the next failure starts where it would have started
anyway.

It cannot waive a pause LRCLIB asked for. That is a condition of being
allowed to ask at all rather than a politeness of ours, so the press goes
out, is refused before a socket is opened, and the reason says so.
