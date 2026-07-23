# LyriSync

Decision log so far

- Stack: Python + PySide6, MVP first, possible Swift rewrite later touches UI only
- Spotify integration: local AppleScript polling (~300ms), not Web API
- Lyrics: LRCLIB, cache locally keyed by Spotify track ID, fallback chain synced → plain → "no lyrics"
- Architecture: three separated components (player monitor, lyrics provider, UI); monitor and provider know nothing about the UI
- Milestone order: monitor script → terminal-synced lyrics → floating window → polish
- v1 excludes: menu bar, shortcuts, focus mode, learning/translation modes, Web API, database
- Only genuine 404s are cached negatively, errors are never cached
- Prefer no lyrics over mismatched-duration lyrics
- Track identity includes URI kind (media vs track share IDs)
- Non-music items never touch the lyrics cache or network
- A line "errors surface as a retry state and re-attempt every 30s"
- Qt defaults windows to FullScreenPrimary; Primary and Auxiliary are mutually exclusive, so the all-desktops toggle must clear Primary. Native state is verified by readback, not assumed.
- Full-screen overlay requires accessory activation policy, not just collection-behavior flags; a Regular-policy app triggers a Space switch on activation. Window is unfocusable by design.

Parked
- Album-art background
- Karaoke word-by-word
- Side panels
- Menu bar app
- Japanese romanisation
- Global shortcut
- Focus fade