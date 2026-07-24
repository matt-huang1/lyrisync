"""Spoken reference line via the macOS `say` command.

The decision logic here (voice parsing, SpeechSession, button gating) is
pure and testable; the actual `say` subprocess is blocking and must only
run in a worker task, never on the UI thread.
"""

from __future__ import annotations

import logging
import re
import subprocess

from lyrisync.romanize import contains_hangul

logger = logging.getLogger(__name__)

VOICE = "Yuna"

# Tuning knob: rate of the spoken line, in words per minute. Natural Korean
# speech is around 200; noticeably slower so a learner can follow the
# syllables. Raise it as your ear improves.
SPEECH_RATE_WPM = 120

_SAY_TIMEOUT = 60.0


def parse_voice_names(say_output: str) -> list[str]:
    """Voice names from `say -v ?` output. Each line is a name (which may
    itself contain single spaces) padded with runs of spaces before the
    locale column."""
    names = []
    for line in say_output.splitlines():
        name = re.split(r"\s{2,}", line.rstrip(), maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


def voice_available(say_output: str, voice: str = VOICE) -> bool:
    """True when ``voice`` (or a variant like "Yuna (Enhanced)") is in the
    installed-voices listing."""
    for name in parse_voice_names(say_output):
        if name == voice or name.startswith(voice + " ("):
            return True
    return False


def detect_voice() -> bool:
    """One startup check for the configured voice. When missing, the
    spoken-reference feature disables itself for the session."""
    try:
        proc = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, timeout=10.0
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.info("spoken reference disabled: cannot run `say` (%s)", exc)
        return False
    if voice_available(proc.stdout):
        return True
    logger.info(
        "spoken reference disabled: macOS voice %r is not installed. "
        "Install it under System Settings → Accessibility → Spoken Content "
        "→ System Voice → Manage Voices…, then restart lyrisync.",
        VOICE,
    )
    return False


SPEECH_RATE_PRESETS = (100, 120, 140, 160)  # wpm choices in the menu


def say_command(text: str, rate: int = SPEECH_RATE_WPM) -> list[str]:
    """The `say` invocation for a line — pure, for testing the plumbing."""
    return ["say", "-v", VOICE, "-r", str(rate), "--", text]


def speak_korean(text: str, rate: int = SPEECH_RATE_WPM) -> None:
    """Speak ``text`` aloud, blocking until done — worker threads only."""
    subprocess.run(say_command(text, rate), check=False, timeout=_SAY_TIMEOUT)


class SpeechSession:
    """Pause/resume bookkeeping around one spoken line.

    - ``begin`` refuses while a speech is active, so rapid clicks never
      stack two speeches.
    - Playback resumes afterwards only if it was playing when the speech
      started (we paused it); clicked while already paused, it stays
      paused.
    """

    def __init__(self) -> None:
        self._speaking = False
        self._resume_after = False

    @property
    def speaking(self) -> bool:
        return self._speaking

    def begin(self, player_playing: bool) -> bool:
        """Returns False when a speech is already in flight (ignore the
        click). Otherwise records whether playback must resume after."""
        if self._speaking:
            return False
        self._speaking = True
        self._resume_after = player_playing
        return True

    @property
    def should_resume(self) -> bool:
        return self._speaking and self._resume_after

    def finish(self) -> bool:
        """Clears the session; returns whether playback should resume."""
        resume = self._resume_after
        self._speaking = False
        self._resume_after = False
        return resume


def button_visible(
    synced: bool, line_text: str, feature_enabled: bool, voice_ok: bool
) -> bool:
    """The speaker button shows only for a hangul line of synced lyrics,
    with the feature toggled on and the voice actually installed."""
    return synced and feature_enabled and voice_ok and contains_hangul(line_text)
