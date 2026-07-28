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
- Accessory activation policy is permanent and unconditional, applied before any window exists. It is no longer coupled to the all-desktops toggle, which now owns only collection behaviour and window level. There is no regular policy to fall back to, so nothing can bring the Dock icon back.
- One QMenu serves both the menu bar item and the window's right-click menu — two menus could drift apart, one cannot. Its structure is built once and never rebuilt; refresh only flips visibility, check marks and labels, so the native menu bar item never flickers. Checkable entries connect to triggered, not toggled: refresh calls setChecked on all of them.
- The menu bar item is the way back from a hidden window, so quit must be visible in every state and hiding must leave the monitor, loop and any sync pass running.
- QSettings location is injected into LyricsWindow, not global. QSettings.setDefaultFormat/setPath are process-wide and silently do nothing on macOS — trusting them meant tests writing into the real user's preferences.
- Tests must never reach the real Spotify. Entering a sync pass dispatches a seek-to-0 and a resume, so the Qt tests stub PlayerCommandTask/SeekTask/SpeakTask as well as the monitor thread; otherwise running the suite restarts whatever the developer is listening to.
- Legibility over an arbitrary background outranks the material. The scrim alpha is measured, not chosen by eye: the sung line must clear 4.5:1 against a PURE WHITE document with no blur behind it at all, so a material that fails to attach or renders pale costs depth and never readability.
- The vibrancy material is an NSVisualEffectView slid under the Qt content, pinned to the dark appearance — a material following a light system appearance would go pale over a white document and take the white lyric text with it. The opacity gesture needs no plumbing: it rides on the NSWindow's alpha, which dims the material too.
- typography.py owns the type scale and geometry.py imports it, so the height floor can never describe a type scale the stylesheet has moved on from.
- No colour emoji in the window. 🔊 renders in colour whatever is asked of it and macOS has no monochrome speaker glyph (U+1F56A and friends are tofu), so the speak button is ♬; 🎤 takes U+FE0E and goes monochrome.
- No test is macOS-only. Everything native is guarded off-cocoa in the code and asserted structurally, so the whole suite runs headless on the offscreen platform. CI must apt-get PySide6's system libraries (libegl1, libgl1, libxkbcommon-x11-0, ...) — the wheel imports fine without them and only fails at load with "libEGL.so.1: cannot open shared object file", which pytest.importorskip does NOT skip by default (it is an ImportError, not a ModuleNotFoundError).

Parked
- Album-art background
- Karaoke word-by-word
- Side panels
- Japanese romanisation
- Global shortcut
- Focus fade
- Publishing user syncs to LRCLIB
- Starting a sync mid-song, partial saves, per-line editing/nudging of an existing sync

- End every session by committing the work with a descriptive message and pushing.