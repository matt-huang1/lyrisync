# LyriSync documentation

The [README](../README.md) is the ninety-second version. These pages are
the reasoning: what was decided, why, and what was measured to check it.

**Start here**

- [Architecture](architecture.md) — the module map and the threading model
- [Design philosophy](../DESIGN_PHILOSOPHY.md) — the principles the rest
  of this is downstream of
- [Changelog](../CHANGELOG.md) — the milestones in order

**How it works**

- [Spotify integration and polling](spotify-integration.md) — AppleScript
  over the Web API, track identity, why the poll loop stops the way it does
- [Lyrics sources and caching](lyrics-and-caching.md) — the fallback
  chain, what is cached and what never is, why your syncs are not cache
- [Tap-to-sync](tap-to-sync.md) — timing a song by hand, and the timing
  model behind a tap
- [The learning layers](learning-features.md) — romanisation, spoken
  reference, looping, echo practice

**How it looks**

- [Contrast and accessibility](contrast-and-accessibility.md) — the 4.5:1
  promise and every number behind it
- [Appearance, materials and window behaviour](appearance-and-materials.md)
  — vibrancy, light/dark, accessory policy, hairline and shadow
- [Album colour](album-colour.md) — hue-only tinting, and the two bugs
  that shaped it
- [Per-app window position](per-app-position.md) — following the app you
  switched to, and the clock bug behind it
- [Yielding to notifications](notification-yield.md) — fading out of a
  banner's way, and why it needs no permission
- [Motion and typography](motion-and-typography.md) — the line change, the
  type scale, and how motion is verified

**How it is built**

- [The global hotkey, and why it is Carbon](hotkey-and-carbon.md)
- [The menu, and living in the system](menu-and-system-integration.md)
- [Testing, and the guards that make it safe](testing-and-ci.md)
- [Packaging](packaging.md)
- [Making a release](releasing.md)
- [Gatekeeper, and why a downloaded build is blocked](gatekeeper.md)

`CLAUDE.md` in the repository root is the raw working decision log — every
decision in the order it was made, including the ones these pages
summarise.
