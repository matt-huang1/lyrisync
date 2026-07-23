# LyriSync

Synced lyrics for the Spotify desktop app on macOS. Polls Spotify locally via
AppleScript (no Web API), fetches lyrics from [LRCLIB](https://lrclib.net),
and displays the current line in time with playback.

Status: work in progress — terminal display works; floating window is next.

## Usage

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/lyrisync-monitor   # print raw player events
.venv/bin/lyrisync-lyrics    # terminal-synced lyrics
```

Requires the Spotify desktop app. First run may trigger a macOS Automation
permission prompt.

## Development

```sh
.venv/bin/python -m pytest
```

## License

MIT
