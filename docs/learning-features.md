# The learning layers

SottoVoce was built for language learners, and every learning feature is an
**optional layer**. They are off by default, or hidden until they can act,
and with all of them off the app is a simple synced-lyrics window —
identical to what it would be if none of this had been written.

That is the rule they are all built against, not a summary of how they
happen to behave.

## Korean romanisation

An optional line under the current lyric, rendering hangul in Revised
Romanization ([korean-romanizer](https://github.com/osori/korean-romanizer)).

- It sits lighter and smaller than the lyric, close enough to read as one
  block with it rather than as a separate row.
- Mixed Korean/English lines keep their English words: the romanizer only
  transforms hangul.
- A line the library chokes on comes back untouched. A lyrics display
  never crashes over pronunciation.
- The menu entry appears only when there is hangul on screen *in a form
  romanisation can sit under* — never in the plain-lyrics view, which has
  no current line, so the toggle would do nothing there.

Japanese romanisation is parked.

## Spoken reference

Click the speech bubble to pause the music, hear the current line read
slowly by macOS's Korean voice (**Yuna**), and resume where you left off.
Rate presets are 100 / 120 / 140 / 160 wpm; the default is 120, well under
conversational speed.

Speech runs on the worker pool, never the UI thread — `say` can hold a
line for a long time. If Yuna is not installed, the feature quietly
disables itself and everything else works.

## Line looping

The ↻ button repeats the current line until released. The loop bounds are
[this line's timestamp, the next line's), or the track duration for the
last line.

The wrap seek is dispatched 0.46 s early, because the write to Spotify
takes a round trip and firing exactly at the end bound would bleed the
next line through.

**Only one wrap is outstanding at a time.** That lead is also a gap: for
the whole round trip the player is still where it was, so every position
that arrives in between is inside the lead, the eta clamps to zero and the
scheduler would dispatch the wrap again — measured at 7 to 8 seeks where 4
were wanted, the second landing a round trip after the first and
restarting a line that had already restarted. A position *earlier* than
the one the wrap was dispatched from is what says it landed, because a
seek is the only thing that moves a position backwards. A wrap that never
lands dispatches nothing more, and the position running on past the end
bound is what cancels the loop — which is what a failed seek has always
done.

While a loop is engaged the line never advances, so the anticipatory line
scheduler is suppressed and the wrap is armed from the line's known end
instead. Grace windows — 0.5 s before the start, 1.0 s past the end — stop
a loop being cancelled by ordinary position jitter, but a position outside
those means the user has seeked away, and at that point they have voted:
the loop releases.

## Echo practice

With echo practice on, each loop pass alternates: you hear the line, then
the music **pauses** for a silent, self-paced window in which you sing it
yourself, and a button ends your turn and replays the line.

The silence is user-paced rather than timed to the line's length. Singing
it back takes longer than hearing it, and a fixed window would either rush
the learner or make the fast case feel dead.

If the user un-pauses during the silent attempt, the loop is released
rather than fought: they have taken over.

## Tap-to-sync

Timing a song by hand when only plain lyrics exist. It has its own page —
see [tap-to-sync](tap-to-sync.md).

## Plain-lyrics view

Songs with no timestamps show their whole lyrics in a scrollable view,
with a note saying they are not synced. In that view, scroll moves the
lyrics and Option+scroll adjusts opacity — the gesture that is normally
opacity is given to the thing that obviously wants it.

## What these layers are not allowed to do

- **Change the default experience.** Every layer off must equal the
  original core app, byte for byte where that is checkable.
- **Announce themselves when they cannot act.** An entry that appears and
  does nothing is worse than no entry.
- **Take focus.** Nothing in the app opens a modal or activates the app —
  including the tap-to-sync discard confirmation, which is an inline
  two-step control for exactly that reason.
