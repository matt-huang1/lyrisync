# LyriSync

Synced lyrics for the Spotify desktop app on macOS, in a floating window built for language learners.

![Demo](docs/demo.gif)
*(demo GIF placeholder — record with the floating window over a full-screen app)*

## Features

- **Floating synced lyrics** — a frameless, always-on-top, translucent window shows the previous, current, and next line, advancing in time with playback. Lines fade anticipatorily so the new line is fully legible exactly on its timestamp.
- **Full-screen persistence** — an optional "show on all desktops" mode keeps the window visible across Spaces and over full-screen apps.
- **Korean romanisation** — an optional pronunciation line renders the current lyric in Revised Romanization beneath the original hangul.
- **Spoken reference** — click the speaker to pause the music and hear the current line read slowly by macOS's Korean voice, then resume where you left off. Speech rate is adjustable.
- **Line looping** — repeat the current line until released; with **echo practice** on, each pass alternates with a silent, self-paced window for you to sing the line yourself.
- **Plain-lyrics scrolling** — songs without timestamps show their full lyrics in a scrollable view.
- **Everything optional** — all learning features are toggles, off by default or hidden until relevant. With every layer off, LyriSync is just a simple synced-lyrics window.

## Requirements

- macOS (uses AppleScript, native window behaviour, and the `say` command)
- The Spotify desktop app
- Python 3.12+

## Install

```sh
git clone git@github.com:matt-huang1/lyrisync.git
cd lyrisync
python3 -m venv .venv
.venv/bin/pip install -e .
```

On first run, macOS will ask for Automation permission so LyriSync can read playback state from Spotify — this is required.

For the spoken-reference feature, install the Korean system voice **Yuna** (System Settings → Accessibility → Spoken Content → System Voice → Manage Voices…). Without it, the feature quietly disables itself; everything else works.

## Usage

```sh
.venv/bin/lyrisync
```

Play something in Spotify and the window follows along.

- **Right-click** the window for the menu: show on all desktops, romanisation, spoken reference and speech rate, echo practice, quit.
- **Drag** anywhere to move; **drag edges/corners** to resize (text scales with width).
- **Scroll** to adjust opacity; in plain-lyrics view, scroll moves the lyrics and **Option+scroll** adjusts opacity.
- The loop button (top right) repeats the current line; with echo practice enabled, the microphone button ends your silent attempt and replays the line.

Two auxiliary terminal tools exist for debugging: `lyrisync-monitor` (raw player events) and `lyrisync-lyrics` (synced lyrics in the terminal).

## Architecture

A worker thread polls the Spotify desktop app via a single batched AppleScript call (~300 ms); no Spotify Web API and no credentials. Lyrics come from [LRCLIB](https://lrclib.net) and are cached locally as JSON keyed by Spotify track ID, including definitive "no lyrics" results (delete `.lyrics_cache/` to reset). Display logic, loop/echo state, gesture routing, and geometry live in pure, Qt-free modules behind a thin PySide6 window; 198 tests cover them, run by GitHub Actions on every push.

## Credits

Lyrics are provided by [LRCLIB](https://lrclib.net). Romanisation uses [korean-romanizer](https://github.com/osori/korean-romanizer).

## License

[MIT](LICENSE)
