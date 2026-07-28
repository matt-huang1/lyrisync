# LyriSync

Synced lyrics for the Spotify desktop app on macOS, in a floating window built for language learners.

![Demo](docs/demo.gif)

## Features

- **Menu bar app** — LyriSync lives in the menu bar, not the Dock. Its menu is the same one you get by right-clicking the window, so every setting is reachable whether the lyrics are on screen or hidden away.
- **Floating synced lyrics** — a frameless, always-on-top window shows the previous, current, and next line, advancing in time with playback. Lines fade anticipatorily so the new line is fully legible exactly on its timestamp.
- **Native HUD** — a real macOS vibrancy material behind the lyrics, the system font with weights picked per row, SF Symbols on the controls, and a background sized so the line being sung stays readable over a white document, a dark editor, or video.
- **A real app** — builds into a double-clickable `LyriSync.app` that launches with no terminal and no Dock icon, keeping the settings you already had.
- **Full-screen persistence** — an optional "show on all desktops" mode keeps the window visible across Spaces and over full-screen apps.
- **Korean romanisation** — an optional pronunciation line renders the current lyric in Revised Romanization beneath the original hangul.
- **Spoken reference** — click the speech bubble to pause the music and hear the current line read slowly by macOS's Korean voice, then resume where you left off. Speech rate is adjustable.
- **Line looping** — repeat the current line until released; with **echo practice** on, each pass alternates with a silent, self-paced window for you to sing the line yourself.
- **Plain-lyrics scrolling** — songs without timestamps show their full lyrics in a scrollable view.
- **Tap-to-sync** — a song that only has plain lyrics can be timed by hand: the track restarts and you tap a wide bar once as each line begins. The finished sync is saved locally and used automatically from then on, so the song plays back like any other synced track. Not happy with the timings? Re-sync it and the pass starts over from line one.
- **Everything optional** — all learning features are toggles, off by default or hidden until relevant. With every layer off, LyriSync is just a simple synced-lyrics window.

## Requirements

- macOS 11 or later (uses AppleScript, native window behaviour, SF Symbols, and the `say` command)
- The Spotify desktop app
- Python 3.12+

## Install

```sh
git clone git@github.com:matt-huang1/lyrisync.git
cd lyrisync
python3 -m venv .venv
.venv/bin/pip install -e ".[build]"
make app
```

That produces `dist/LyriSync.app`. Drag it to `/Applications` (or `mv dist/LyriSync.app /Applications/`).

**First launch: right-click the app and choose Open**, then confirm. Double-clicking a fresh copy will be refused, because the app is signed only ad-hoc — there is no Apple Developer certificate behind it and it has not been notarised, so macOS has no one to check it against. Right-click → Open is how you tell macOS you vouch for it yourself; it is needed once, and every launch after that is a normal double-click.

Then, the first time it polls Spotify, macOS asks for **Automation** permission ("LyriSync wants to control Spotify"). Grant it — this is how the app reads the current track and playback position, and without it the window comes up but never finds a song. It is asked once. Moving the app afterwards can make macOS ask again, so put it where you want it before granting.

The app has no Dock icon by design: it lives in the menu bar, and everything is reachable from there.

For the spoken-reference feature, install the Korean system voice **Yuna** (System Settings → Accessibility → Spoken Content → System Voice → Manage Voices…). Without it, the feature quietly disables itself; everything else works.

### Running from the source tree instead

The app also runs straight from a checkout, which is what development uses:

```sh
.venv/bin/pip install -e .
.venv/bin/lyrisync
```

Both read and write the same settings (`~/Library/Preferences/com.lyrisync.lyrisync.plist`), so window position, size, opacity and every toggle carry over between the two.

## Usage

Play something in Spotify and the window follows along.

- **The menu bar item** is the app's home: show lyrics, romanisation, spoken reference and speech rate, echo practice, show on all desktops, sync this song, quit. Entries appear only where they apply, so with every layer off the menu is just show/hide, Spaces and quit.
- **Right-click** the window for that same menu — it is literally the same menu, so the two can never disagree.
- **Show lyrics** hides and shows the window without stopping anything: the music plays on, a loop stays engaged, and a sync pass in progress keeps going. Showing it again picks the song up wherever it now is. The setting is remembered, and the menu bar item is always the way back.
- **Drag** anywhere to move; **drag edges/corners** to resize (text scales with width).
- **Scroll** to adjust opacity; in plain-lyrics view, scroll moves the lyrics and **Option+scroll** adjusts opacity. The window starts fully opaque, which is the only setting macOS will render the blur at — dimming trades the frost for seeing the screen underneath, and scrolling back up to the top restores it.
- The **↻** button (top right) repeats the current line, and the **speech bubble** beside the lyric speaks it aloud; with echo practice enabled, the microphone button ends your silent attempt and replays the line.

### Timing a song yourself

When a song has plain lyrics only, right-click and choose **Sync this song**. The track jumps back to the start and starts playing, and the window shows the line you are waiting for, with the line you just stamped above it and the next two beneath. Tap the wide bar at the bottom the instant each line begins — watching the line above run out is the cue for the next tap. ↩ undoes the last tap if you are early or late, and the counter tracks how far you have got. Taps are ignored while playback is paused, so you can stop to catch up. Finish the last line and the sync saves itself and reloads immediately, ready to listen back. ✕ abandons the pass and asks for a second click to confirm; a sync can only be saved complete, so there is nothing to keep otherwise.

Once a song carries a sync of your own, the menu offers **Re-sync this song** instead — a fresh full pass from line one that replaces the old timings when you finish it. Abandoning a re-sync leaves the sync you already had exactly as it was.

Two auxiliary terminal tools exist for debugging: `lyrisync-monitor` (raw player events) and `lyrisync-lyrics` (synced lyrics in the terminal).

## Building the app bundle

```sh
make app     # dist/LyriSync.app, ad-hoc signed
make icon    # regenerate the icon from packaging/appicon.svg
make clean   # remove build/, dist/ and the generated icon
```

`make app` renders the icon, freezes the app with PyInstaller (`packaging/LyriSync.spec`), and ad-hoc signs the finished bundle. It needs `pip install -e ".[build]"` and nothing else — no certificate, no keychain, no Xcode. Bundle building is deliberately not part of CI: the artefact is macOS-only and the things worth checking about it (menu bar icon, no Dock icon, the Automation prompt) can only be accepted by a person.

Ad-hoc signing is what lets the bundle run at all on Apple silicon, where unsigned code is refused outright. It is not a developer-ID signature and says nothing about who built it, which is why a copy that has been downloaded rather than built locally still needs right-click → Open the first time.

## Architecture

A worker thread polls the Spotify desktop app via a single batched AppleScript call (~300 ms); no Spotify Web API and no credentials. Lyrics come from [LRCLIB](https://lrclib.net) and are cached locally as JSON keyed by Spotify track ID, including definitive "no lyrics" results (delete `.lyrics_cache/` to reset). Syncs you tap out yourself are not part of that cache: they are plain `.lrc` files in `.user_syncs/`, written only when you finish a pass, consulted ahead of both the cache and the network, and left alone by that reset. A re-sync takes its lines from the sync it is replacing rather than re-fetching them, so it works offline and after that reset too. The app runs under the macOS accessory activation policy from the moment it starts — that is what keeps it out of the Dock and lets the overlay sit over full-screen Spaces instead of switching to one. The window sits on an NSVisualEffectView pinned to the dark appearance, with the painted background reduced to a scrim over it — sized by measurement so the sung line clears 4.5:1 against a pure white page even if the material never renders, because legibility over someone else's screen outranks the blur. macOS only draws that blur while the window's alpha is exactly 1, so the window starts there and treats dimming as the deliberate trade it is; screenshots over a white document, a dark editor and a bright video put the sung line at 8.9:1, 17.0:1 and 8.8:1 with the material rendering, against a 4.7:1 floor without it. Display logic, loop/echo state, tap-to-sync sessions, menu gating, gesture routing, the type scale, and geometry live in pure, Qt-free modules behind a thin PySide6 window. All 372 tests run on every push via GitHub Actions, the window ones included: the runner installs PySide6's system libraries and drives a real Qt object tree on the offscreen platform, so nothing is macOS-only and nothing is skipped. No test touches anything real — not your settings, your Spotify, your lyrics cache or the network — and that is enforced rather than intended: the suite blocks outbound sockets, subprocesses, the real preferences file and the default cache directories, and fails the test that reached for one even when the attempt happened on a worker thread.

## Credits

Lyrics are provided by [LRCLIB](https://lrclib.net). Romanisation uses [korean-romanizer](https://github.com/osori/korean-romanizer).

## License

[MIT](LICENSE)
