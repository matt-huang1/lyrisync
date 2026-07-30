"""Spotify saying something changed, instead of being asked three times a
second.

The desktop app posts ``com.spotify.client.PlaybackStateChanged`` on the
system-wide distributed notification centre whenever playback state or
track changes. Observing it costs nothing while nothing happens, which is
the whole point: the app's largest single energy cost was asking a
question whose answer was almost always the same one as last time.

## What it carries, read off the wire

Every notification observed on macOS 26.5.2, Spotify 1.2.x, carries the
same thirteen keys::

    Album, Album Artist, Artist, Disc Number, Duration, Has Artwork,
    Name, Play Count, Playback Position, Player State, Popularity,
    Track ID, Track Number

``Track ID`` is the full URI (``spotify:track:...``), so the URI kind that
track identity depends on is in there; ``Duration`` is milliseconds and
``Playback Position`` is seconds, the same units the snapshot script
answers in.

## And what it is used for anyway, which is less

This module treats the notification as a DOORBELL and throws the payload
away. Two reasons, and the second is the one that settles it:

- there is no ``artwork url`` in it, only ``Has Artwork``, so a track
  change would have to ask Spotify anyway for the album colour to work.
- track identity would then have two definitions, one parsed from the
  snapshot script and one from a dictionary of Objective-C strings, and
  two copies of a fact is the shape of most bugs in this project's log.

So what arrives is "ask again, now", and the answer still comes from the
one snapshot script it always came from.

## What it does NOT announce, measured

A **seek** produces nothing at all. Driven from AppleScript (``set player
position to 60``, twice) and by ``previous track`` restarting the current
song, and in neither case did anything arrive, on either occasion. That is
the whole reason a reconciliation poll survives at all: everything else
here rings, and a seek does not.

What does ring, each verified by driving it and watching:

| what happened | what arrived | how long it took |
|---|---|---|
| pause | ``Player State = Paused`` | 0.50s |
| play | ``Player State = Playing`` | 0.11s |
| skip to the next track | the new track, position 0 | 0.14s |
| a track ending on its own | the new track, position 0.008 | inside 0.5s |
| Spotify quitting | ``Stopped``, no ``Track ID`` | 0.89s |
| Spotify launching | ``Stopped`` then ``Paused`` with the track | 0.67s |

The quit and the launch matter more than they look: the case with no
notifications in it announces its own beginning and its own end.

## The observer must be registered by name

Registering with ``name=None`` to watch everything receives NOTHING on a
modern macOS: measured over 32 seconds of driving Spotify through eight
commands, with zero notifications delivered, and the same run with the
name given delivered all of them. Anything looking for a notification here
has to know what it is called.

Pure and Qt-free apart from one door, so the rules can be checked without
a run loop, a Mac, or Spotify.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# What Spotify calls it. Not a family and not a prefix: this is the one
# name, and registering for anything else (see the docstring) receives
# nothing at all.
NOTIFICATION_NAME = "com.spotify.client.PlaybackStateChanged"

# NSNotificationSuspensionBehaviorDeliverImmediately. Spelled as its value
# because pyobjc exposes the enum from Foundation and this module may not
# import Foundation outside its door.
_DELIVER_IMMEDIATELY = 4

# The Objective-C class that receives the callback, built once and kept.
# Registering a second class under the same name is a runtime error in the
# Objective-C runtime, and this module can be started and stopped many
# times in one process (the suite does it in a loop).
_receiver_class = None


def _distributed_center():
    """The distributed notification centre and NSObject, or None where
    there is no such thing.

    The single door, the same shape as ``notifications._quartz()``,
    ``frontmost._workspace()`` and the rest. Returns None off macOS and
    without pyobjc, so every caller has one branch to handle and the suite
    has one seam to shut, and it needs shutting: without it a test would
    register a live observer on the developer's own machine for the life
    of the process.
    """
    if sys.platform != "darwin":
        return None
    try:
        from Foundation import NSDistributedNotificationCenter, NSObject
    except Exception:  # pragma: no cover - pyobjc missing
        logger.info(
            "Foundation unavailable: asking Spotify on a timer instead of "
            "waiting to be told"
        )
        return None
    return NSDistributedNotificationCenter, NSObject


def _receiver(nsobject):
    """The class whose one method the notification centre calls.

    Defined here rather than at module scope because its base class comes
    from the door, and built once because the Objective-C runtime owns the
    name.
    """
    global _receiver_class
    if _receiver_class is None:

        class _Announcement(nsobject):
            """One Objective-C selector, and a Python attribute holding
            what to call. ``handler`` is assigned after ``init`` rather
            than passed into it: an Objective-C initialiser taking a
            Python callable is more ceremony than one line of assignment,
            and the object is never handed anywhere before it is set.
            """

            def heard_(self, note):
                try:
                    self.handler()
                except Exception:  # pragma: no cover - defensive
                    logger.debug("announcement handler raised", exc_info=True)

        _receiver_class = _Announcement
    return _receiver_class


class PlaybackAnnouncer:
    """Spotify's own announcement that something changed.

    ``on_announcement`` is called with no arguments, on whichever thread
    the run loop delivers on (the main one, in this app). It is a
    doorbell: what changed is not passed along, because the answer still
    comes from the snapshot script.

    ``start()`` says whether an observer was actually registered, and the
    caller is expected to carry on either way. Nothing here is required
    for the app to work: a Mac where this returns False is a Mac that asks
    Spotify on a timer, which is what every Mac did before this existed.
    """

    def __init__(self, on_announcement: Callable[[], None]) -> None:
        self._on_announcement = on_announcement
        self._observer = None
        self._centre = None

    @property
    def listening(self) -> bool:
        return self._observer is not None

    def start(self) -> bool:
        """Register the observer. False when there is nothing to register
        with, or when registering failed."""
        if self._observer is not None:
            return True
        door = _distributed_center()
        if door is None:
            return False
        centre_class, nsobject = door
        try:
            receiver = _receiver(nsobject)
            observer = receiver.alloc().init()
            observer.handler = self._on_announcement
            centre = centre_class.defaultCenter()
            centre.addObserver_selector_name_object_suspensionBehavior_(
                observer, "heard:", NOTIFICATION_NAME, None, _DELIVER_IMMEDIATELY
            )
        except Exception:
            logger.debug("could not observe Spotify's announcements", exc_info=True)
            return False
        self._observer = observer
        self._centre = centre
        logger.info("listening for Spotify's own playback announcements")
        return True

    def stop(self) -> None:
        """Give the observer back.

        Idempotent, and safe to call having never started: shutdown drains
        everything the window owns without asking each one whether it has
        anything to drain.
        """
        if self._observer is None:
            return
        try:
            self._centre.removeObserver_(self._observer)
        except Exception:  # pragma: no cover - defensive
            logger.debug("could not remove the observer", exc_info=True)
        self._observer = None
        self._centre = None
