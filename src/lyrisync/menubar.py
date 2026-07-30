"""Which glyph the menu bar item is showing.

The item used to be one image whatever the app was doing, which made it a
launcher rather than an indicator. It is the only part of this app that is
always on screen, so it is the natural place to say — quietly, without
being read — whether anything is happening.

**Three states, and the number is the point.** A menu bar icon is 16
points tall and shares a strip with a dozen others; the eye takes it in
without focusing, or not at all. Every state added past the third is a
distinction nobody can make at that size, so the ones here are chosen to
differ in the two ways that survive being small: how much ink there is,
and whether there is a mark that is not usually there.

- **idle** — the same glyph, lighter. Nothing is playing, or the lyrics
  are hidden. Dimmer means less to say, which is what it means everywhere
  else.
- **active** — the glyph at full strength: lyrics on screen, following a
  song.
- **practice** — the glyph with a dot. A loop, an echo pass or a
  tap-to-sync is running, and this is the one state where the app is
  doing something *for* you rather than showing you something.

All three are template images: solid black with the shape in the alpha
channel, so macOS tints them for a light or dark menu bar and the idle
one's lower alpha comes through as a dimmer glyph rather than as a grey
one. Nothing here animates — a moving menu bar icon is a thing to look at,
and this is a thing to notice.

Pure and Qt-free: the state is chosen here, the images are loaded by the
window.
"""

from __future__ import annotations

IDLE = "idle"
ACTIVE = "active"
PRACTICE = "practice"

STATES = (IDLE, ACTIVE, PRACTICE)

# The file for each state, inside the assets directory.
ICON_FILES = {
    IDLE: "menubar-idle.svg",
    ACTIVE: "menubar.svg",
    PRACTICE: "menubar-practice.svg",
}


def icon_state(*, playing: bool, lyrics_visible: bool, practising: bool) -> str:
    """Which glyph belongs on the menu bar right now.

    Practice wins over everything, including the window being hidden: a
    sync pass or an engaged loop keeps running when the lyrics are hidden,
    and in that state the menu bar item is the ONLY evidence it is still
    going. An icon that went quiet then would be reporting on the window
    rather than on the app.

    Otherwise the question is whether there is anything to see: lyrics on
    screen and a song playing is active, and everything else — paused,
    stopped, no Spotify, or the window hidden — is idle. Hiding the window
    therefore dims the icon, which makes the menu bar the confirmation
    that the hotkey landed even though nothing else on screen moved.
    """
    if practising:
        return PRACTICE
    if playing and lyrics_visible:
        return ACTIVE
    return IDLE
