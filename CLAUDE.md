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
- SESSION RULE: tests must never touch real user state, the network, or Spotify. Settings are injected; player commands, speech, and lyrics fetching are stubbed. Four escapes were found by their symptoms rather than by the suite — real QSettings writes, real player commands, a tray test that never ran, and a live LRCLIB fetch that aborted CI — so the seams are now backed by guards in tests/conftest.py: outbound sockets, subprocess, a bare LyricsProvider, and QSettings("lyrisync", "lyrisync") all refuse.
- A guard must record, not merely raise. Every escape happens on a worker thread, and the app catches broad exceptions there by design (a failed fetch is a retry state, not a crash), so an exception alone is swallowed exactly where it matters. Each block is recorded and an autouse fixture fails the test that caused it. The guards have tests of their own — an unrun guard is the tray test again.
- A QThread destroyed while it is still running is a qFatal: the process aborts (exit 134, "QThread: Destroyed while thread is still running") and names whichever test was on screen, which is how a shutdown bug came to look like a bug in the quit test. So _shutdown drains everything the window owns — monitor thread, then the worker pool — before anything of the window's is destroyed, and the Qt fixture forces any surviving thread down so a regression fails with a sentence instead of a signal.
- Shutdown's waits are bounded and honest. A worker that will not return in 3s (`say` can hold a line for a minute) is logged and left to the thread pool's own destructor, which is where it was always going to be dealt with; blocking quit on it would be worse than the warning.
- Legibility over an arbitrary background outranks the material. The scrim alpha is measured, not chosen by eye: the sung line must clear 4.5:1 against a PURE WHITE document with no blur behind it at all, so a material that fails to attach or renders pale costs depth and never readability.
- The vibrancy material is an NSVisualEffectView slid under the Qt content, pinned to the dark appearance — a material following a light system appearance would go pale over a white document and take the white lyric text with it. The opacity gesture needs no plumbing: it rides on the NSWindow's alpha, which dims the material too.
- Blur and window translucency are mutually exclusive: macOS renders the behind-window blur only while alphaValue is exactly 1, and dimming either native view instead of the window does not save it. So the window starts fully opaque and dimming is understood as spending the frost to see through. Verified by screenshot, not by API docs, which say none of this.
- Screenshots are the readback for anything the compositor draws. _material is not None only proves the view attached; whether it blurs is a question about pixels, so the check is a capture over a text-heavy backdrop — sharp glyphs inside the window mean no blur however healthy the readback looks.
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