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
- Learning features are opt-in, toggleable layers. The default experience is always a simple synced-lyrics window; every layer off must equal the original core app.
- User-made syncs are not cache. They live in .user_syncs/ as plain .lrc, are consulted before cache and network, and nothing in the app or its docs may delete them — clearing .lyrics_cache/ must stay a safe reset.
- Tap timing = interpolated position (last poll + wall-clock since it landed) minus a reaction offset, clamped to >= 0 and >= the previous stamp. The UI thread never runs a subprocess to get a fresher position; the monitor stamps each poll instead.
- A sync is saved only when complete. Exiting early discards, and confirmation is inline (two-step control), never a modal — the window must never take focus or activate the app.
- A re-sync takes its lines from the sync it replaces, not a re-fetch: a completed pass stamps every non-blank plain line, so the stored lines ARE the song's lines. Re-syncing therefore works offline and after .lyrics_cache/ is cleared. LRCLIB's own timings are never offered for overwrite — only the user's.
- A sync pass is modal and user-driven: a fetch landing under it must not tear it down. The result is held and becomes where cancelling lands.

Parked
- Album-art background
- Karaoke word-by-word
- Side panels
- Menu bar app
- Japanese romanisation
- Global shortcut
- Focus fade
- Publishing user syncs to LRCLIB
- Starting a sync mid-song, partial saves, per-line editing/nudging of an existing sync

- End every session by committing the work with a descriptive message and pushing.