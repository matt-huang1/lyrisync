# Publishing a sync back to LRCLIB

LRCLIB gives this app its lyrics for nothing, asks for no key and no
account, and runs on donations. A sync tapped out here is exactly the
thing it is missing for that song. So there is a way to offer it back, and
almost everything on this page is a restriction on that way.

## The one case, and why it is only one

A track may be published when **LRCLIB is holding the plain lyrics for it
and no timings, and the user has made timings for it**.

That is the clean case. The lines came from LRCLIB, so what goes back is
their own words with times against them, attached to the track record they
already have. Nothing is invented, nothing is replaced, and the thing being
added is the one thing missing.

Publishing lyrics for a track LRCLIB has **no** record of is a different
act: a whole set of words, from somewhere, with no record to attach them
to and nothing to check them against. It is out of scope in this version
and is a step of its own.

## Consent

Five rules, and they are the feature rather than a wrapper around it.

- **Nothing is ever published automatically.** Completing a sync saves it
  to `.user_syncs/` and stops. A sync that is never published works
  exactly as it did before any of this existed.
- **It is one explicit action, per track.** There is no bulk path, no
  queue and no "publish everything".
- **What is confirmed is the content, not the idea.** Before anything is
  sent, the window shows the exact submission: the four metadata fields
  and both sets of lyrics, whole, scrolling, neither summarised. The
  button underneath sends *that*.
- **The same unchanged sync is never offered twice.** A record of the
  publication is written beside the sync, and the menu says so instead of
  offering again.
- **The user's own work is untouched.** Publishing copies `.user_syncs/`
  outward. It never modifies, truncates or deletes anything in it.
- **A sync of part of a song is never offered.** A submission replaces
  that song's timings in a database other people read, so half a song is
  not an improvement to it. A pass kept short is a perfectly good sync
  locally and is refused here by name, before LRCLIB is asked anything.

## Where the gate is asked, and why it is asked twice

| | asked by | reads | answers |
|---|---|---|---|
| `publish.standing_refusal` | the menu, on every refresh | the sync on disk, the record beside it, the cached answer, Spotify's metadata | is this worth offering |
| `publish.verify` | the publish window, once | LRCLIB, freshly | is this true right now |

The cached answer is what LRCLIB said the first time the song played,
which may be weeks ago and may have been replaced by somebody else's
contribution since. Publishing is permanent and is done to somebody else's
database, so the condition it turns on is checked against the database at
the moment of publishing. A song can therefore be offered and then
refused, and that is the correct outcome rather than a gap.

The fresh check is also where the plain lyrics that go back out come from.

`verify` checks the words as well as the timings. A sync made here has
lines taken from LRCLIB's own plain lyrics, so they should be those lines
exactly; a sync whose lines are something else (lyrics pasted in during an
outage, say) is a set of stamps against words nobody else has, and
attaching them to LRCLIB's text would be wrong in a way nobody could see
afterwards.

## What is sent, and whose spelling it is in

```
POST /api/publish
X-Publish-Token: {prefix}:{nonce}

{ "trackName", "artistName", "albumName", "duration",
  "plainLyrics", "syncedLyrics" }
```

The metadata is **LRCLIB's own**, taken from the fresh check, not
Spotify's. This matters more than it looks. A submission is matched to an
existing track by its normalised names and a duration within two seconds,
so Spotify's 214 seconds against LRCLIB's 213 still finds the track — but
Spotify's spelling of an album against LRCLIB's different one does not,
and would quietly create a second track record carrying the timings while
the plain lyrics stayed where they were. Sending back what they gave us is
what makes this an addition to their record rather than a near-duplicate
of it.

`plainLyrics` is theirs, unchanged. `syncedLyrics` is the sync file's own
text, byte for byte.

## The proof of work

There is no account and no key. What LRCLIB asks for instead is a few
seconds of this machine's CPU:

1. `POST /api/request-challenge` answers with a `prefix` and a `target`.
2. Find a `nonce` where `SHA256(prefix + nonce)` clears the target.
3. `POST /api/publish` with `X-Publish-Token: prefix:nonce`.

A challenge lasts five minutes and each token is accepted once.

**The rule is the server's.** The hash and the target must be the same
length, and the hash is walked against the target most significant byte
first, failing on the first byte that is greater and passing on the first
that is smaller. That is `digest <= target` for two byte strings of equal
length. LRCLIB's documentation points at LRCGET's solver as an example and
LRCGET's loop stops one byte short of the end — a case nobody will ever
meet, and a difference, so the rule here was taken off `verify_answer` in
the server rather than off the example.

**What it costs, measured.** Twelve solves at the documented target
(`000000FF` followed by 28 zero bytes, about one hash in 2³²/255), Python
3.13 on Apple silicon:

| | |
|---|---|
| median | 4.67s |
| mean | 7.33s |
| slowest of the twelve | 24.29s |
| rate | 3.32M hashes/s |

The spread is the shape of the thing rather than noise: each attempt is an
independent coin, so the time to a hit is geometric and the tail is long.
That is why there is a progress line and a way to stop rather than a
spinner and a promise. It is also why the target is not a constant to plan
around — the server divides it down as recent submissions rise, so a busy
hour is a harder challenge.

Two decisions came out of the measurement. The hasher is **primed with the
prefix once and copied** per attempt rather than rebuilt: 3.14M hashes/s
against 2.01M, a 56% difference, and the prefix is 32 bytes of every
40-odd hashed. And the deadline and the stop flag are read **every 50,000
attempts** rather than every attempt: at this rate that is a look every
15ms, and it costs nothing measurable (3.61M/s chunked against 3.53M/s
unchunked, five runs each, which is inside the noise).

The solve runs on a worker thread, reports a count against an expected
count, and stops when the window is closed. It is never a percentage:
being a third of the way through the expected count says nothing about
being a third of the way through the work, because the odds of the next
hash landing are what they were at the start. What the two numbers do say
is how big the problem is, which is what somebody deciding whether to wait
actually wants.

## The failures, and what each one does

| | what it means | what happens |
|---|---|---|
| 400 | the token was wrong, expired or already spent | reported; **Try again** starts from a fresh challenge |
| 429 | LRCLIB asked this app to slow down | the pause starts, and nothing at all leaves the app until it is over — a retry included |
| the challenge expired mid-solve | the target was hard enough to outlast five minutes | another challenge is requested, up to three, and the window says so |
| the network, a 5xx | unknown | reported with the reason; **Try again** |
| the fresh check failed | nobody knows yet | reported; **Try again** asks LRCLIB again, and this is *not* a refusal |

A refusal is a fact about the song and offers no retry, because pressing
again would only produce it a second time. A failure is a fact about a
moment and offers one.

**A POST is never retried by the connection pool**, unlike a GET. A
connection that failed cannot say whether the request was heard, and the
one POST this app makes carries a token accepted exactly once: a resend
that was really a duplicate either publishes twice or is refused for a
token already spent. So it is reported, and asking again is somebody's
decision.

## Rate limiting

The exchange is three requests in a row — the fresh check, the challenge,
the publish — sent sequentially with LRCLIB's own gap between them
(350ms, the middle of the 200 to 500ms band their documentation asks for,
and the same number the album warm was measured at). One rule with no
exception, which costs 350ms in front of a publish that has just spent
seconds solving; the alternative is a rule with a case in it.

## Where the record lives

`.user_syncs/{track}.published`, beside the sync it is about, holding the
SHA-256 of the text that was sent.

Not in `.lyrics_cache/`, because clearing the cache is a documented reset
and forgetting what has been published is not a reset — it is an app
offering to send somebody's work a second time. Not in the preferences,
because it belongs to the file rather than to this Mac.

It is the digest of the **text**, so a re-sync is a different thing to
send and the entry comes back for it. Which is right: the timings are what
a re-sync changes, and the timings are what publishing is for.

It is a sidecar per sync rather than one index, so a failure to write can
only cost the record of one publication, and nothing in that directory
ever truncates a file another publication's record is also in.

## The window

`publish_window.py`, and it is a second window for the reason
`paste_window.py` is one plus a sharper one of its own. The lyrics window
is frameless, refuses focus, shows without activating and is 460 points of
HUD floating over somebody's work. What has to happen here is a person
reading a whole submission and deciding whether it goes to a public
database. That is not a thing to do in a HUD, and not a thing to do behind
a control that could be pressed by accident.

So it is ordinary: system-drawn, focusable, resizable, scrolling, made of
the platform's own controls. It borrows exactly one thing from the window
it serves, staying on top, because the window it serves does. Like the
paste window it has to **activate the app first** — an accessory app
showing a window while it is not the active app gets an inactive window
with no focus.

It opens small, saying it is asking LRCLIB, and grows when there is
something to read. That was looked at rather than reasoned about: at the
review size a window holding one sentence put it in the middle of 620
points of nothing, because the layout has to give the spare room to
something and with the bodies hidden the only takers were the gaps between
labels.

## Not in this version

- Publishing tracks LRCLIB has no lyrics for at all
- Bulk publishing
- Editing a sync before publishing it
