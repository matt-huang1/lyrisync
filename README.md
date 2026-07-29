# LyriSync

Synced lyrics for the Spotify desktop app on macOS, in a floating window built for language learners.

![Demo](docs/demo.gif)

## Features

- **Synced lyrics that float** — a frameless, always-on-top window shows the previous, current and next line, in time with playback.
- **Lives in the menu bar**, not the Dock. Right-clicking the window gives you the same menu.
- **Looks like macOS** — real vibrancy behind the lyrics, follows light and dark, and the sung line stays readable over a white document, a dark editor or video.
- **⇧⌘J from anywhere** shows and hides the lyrics without switching away from what you were doing. No Accessibility permission.
- **Album colour** — an optional layer that colours the window from the current cover's hue.
- **Show on all desktops** — an optional mode that keeps the window visible across Spaces and over full-screen apps.
- **Korean romanisation** — an optional pronunciation line under the current lyric.
- **Spoken reference** — pause the music and hear the current line read slowly, then carry on.
- **Line looping and echo practice** — repeat a line, or alternate hearing it with a silent turn to sing it yourself.
- **Tap-to-sync** — a song with plain lyrics only can be timed by hand. Your timings are saved and used from then on.
- **Everything optional** — every learning feature is a toggle. With them all off, this is a simple synced-lyrics window.

## Requirements

- macOS 11 or later (macOS 13+ for Open at Login)
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

That produces `dist/LyriSync.app`. Drag it to `/Applications`.

**First launch: right-click the app and choose Open**, then confirm. Double-clicking a fresh copy is refused, because the app is signed only ad-hoc — there is no Apple Developer certificate behind it, so macOS has nobody to check it against. It is needed once; every launch after that is a normal double-click.

Then, on its first poll, macOS asks for **Automation** permission ("LyriSync wants to control Spotify"). Grant it — that is how the app reads the current track and position. Moving the app afterwards can make macOS ask again, so put it where you want it first.

For the spoken-reference feature, install the Korean system voice **Yuna** (System Settings → Accessibility → Spoken Content → System Voice → Manage Voices…). Without it, that one feature quietly disables itself.

To run from the checkout instead — which is what development uses:

```sh
.venv/bin/pip install -e .
.venv/bin/lyrisync
```

Both share the same settings, so window position and every toggle carry over. More in [docs/packaging.md](docs/packaging.md).

## Usage

Play something in Spotify and the window follows along.

- **⇧⌘J** hides and shows the lyrics from any app, full-screen ones included. Nothing takes focus and LyriSync never comes to the front.
- **The menu bar item** is the app's home — every setting is there, including Open at Login in the built app, and entries appear only where they apply. Right-clicking the window gives the same menu.
- **Drag** anywhere to move, **drag the edges** to resize (text scales with width), **scroll** to dim. In the plain-lyrics view, scroll moves the lyrics and Option+scroll dims.
- **↻** repeats the current line; the **speech bubble** speaks it aloud.
- **Sync this song** (right-click) times a song by hand: the track restarts, and you tap the wide bar as each line begins. ↩ undoes a tap, ✕ abandons the pass. Finish the last line and it saves itself. Once a song has your sync, the entry becomes **Re-sync this song**.

Two terminal tools exist for debugging: `lyrisync-monitor` and `lyrisync-lyrics`.

## Architecture

A worker thread polls the Spotify desktop app with one batched AppleScript call every ~300 ms — no Web API, no credentials. Lyrics come from [LRCLIB](https://lrclib.net), cached locally by track ID; syncs you tap out yourself live separately in `.user_syncs/` and are never treated as cache. The monitor and the lyrics provider know nothing about the UI. Display state, timing, menu gating, the type scale, geometry and the colour palettes live in pure, Qt-free modules behind a thin PySide6 window — which is why the contrast floor is a test rather than a judgement, and why all 620 tests run headless on Linux CI without touching the network, your settings or your Spotify.

## Documentation

The reasoning, the trade-offs and the measurements live in **[docs/](docs/)** — one page per topic. Good places to start:

| | |
|---|---|
| [Design philosophy](DESIGN_PHILOSOPHY.md) | the ten principles the rest is downstream of |
| [Architecture](docs/architecture.md) | modules, threads, what knows about what |
| [Contrast and accessibility](docs/contrast-and-accessibility.md) | the 4.5:1 promise, and every number behind it |
| [Album colour](docs/album-colour.md) | hue-only tinting, and the two bugs that shaped it |
| [Testing and CI](docs/testing-and-ci.md) | the guards that keep the suite off your Spotify |
| [Changelog](CHANGELOG.md) | the milestones in order |

Also in `docs/`: [Spotify integration](docs/spotify-integration.md), [lyrics and caching](docs/lyrics-and-caching.md), [tap-to-sync](docs/tap-to-sync.md), [appearance and materials](docs/appearance-and-materials.md), [motion and typography](docs/motion-and-typography.md), [the hotkey and Carbon](docs/hotkey-and-carbon.md), [the menu and system integration](docs/menu-and-system-integration.md), [the learning layers](docs/learning-features.md), and [packaging](docs/packaging.md).

## Credits

Lyrics by [LRCLIB](https://lrclib.net). Romanisation by [korean-romanizer](https://github.com/osori/korean-romanizer).

## License

[MIT](LICENSE)
