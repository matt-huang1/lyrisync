"""The album cover's colour: fetch it, reduce it to one hue, remember it.

What comes out of here is an RGB triple whose only job is to carry a
hue — appearance.py throws away its luminance and its saturation and
keeps nothing else (see THE GOVERNING RULE there). That is worth knowing
while reading the extraction below, because it changes what "the right
colour" means: getting the hue of the cover right matters, and getting
its brightness right does not matter at all.

Extraction is a dominant-hue vote rather than an average. Averaging a
cover is how you get mud: a red sleeve on a black background averages to
dark maroon, and a photograph averages to grey almost every time. So
pixels that cannot carry a hue are discarded first — near-black,
near-white, and anything too flat — and what survives votes in hue bins
weighted by saturation. The winner is the colour the album actually
reads as.

Caching is of the derived colour, never the image. A three-integer JSON
file per track is the whole point: the images are 100KB+ each, they are
already on Spotify's CDN, and nothing here ever needs to look at one
twice. A definitive "this cover has no usable hue" is cached too, the
same way a definitive "no lyrics" is; a fetch that FAILED is never
cached, because its outcome is unknown and retrying may succeed.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Carries the app's version, resolved once from the installed
# distribution's metadata — the same string the lyrics fetch sends, so
# the two cannot introduce themselves differently.
from sottovoce import USER_AGENT
from sottovoce import storage

logger = logging.getLogger(__name__)

# Absolute, from the one module that knows where this app's files go. A
# relative default put this directory wherever the app was launched from,
# which for the bundle is a read-only one: see storage.py.
DEFAULT_ARTWORK_CACHE_DIR = storage.ARTWORK_CACHE_DIR

_REQUEST_TIMEOUT = 10.0
# Covers are a few hundred KB; anything wildly bigger is not a cover and
# is not worth pulling down a phone tether to find out.
_MAX_BYTES = 8 * 1024 * 1024

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_-]")

# The cover is reduced to this many pixels a side before sampling. Small
# enough to be instant, large enough that a detail covering a few percent
# of the sleeve still gets a vote.
SAMPLE_EDGE = 48

# A pixel this dark or this pale is carrying no hue anybody can see, and
# covers are full of both — black backgrounds, white borders, blown-out
# highlights. Letting them vote is how every album comes out grey.
MIN_SAMPLE_LIGHTNESS = 0.12
MAX_SAMPLE_LIGHTNESS = 0.92
MIN_SAMPLE_SATURATION = 0.15

# Hue bins for the vote. 15 degrees is fine enough to separate red from
# orange and coarse enough that a gradient still lands together.
HUE_BINS = 24

# The winning bin must carry at least this much weight per sample before
# it counts as a colour rather than as noise in a monochrome sleeve.
MIN_WINNING_WEIGHT = 0.02


class ArtworkError(Exception):
    """The cover could not be fetched. Outcome unknown — never cached."""


# -- pure: what colour is this cover ------------------------------------


def dominant_colour(samples) -> Optional[tuple[int, int, int]]:
    """The hue this cover reads as, as an RGB triple, or None.

    None means "no usable hue": a monochrome sleeve, a photograph with no
    dominant cast, or an image that was all black. That is a real answer
    and the window treats it as "do not tint", rather than inventing a
    colour from noise.
    """
    from sottovoce.appearance import rgb_to_hsl

    bins: dict[int, list[float]] = {}
    members: dict[int, list[tuple[float, tuple[int, int, int]]]] = {}
    for sample in samples:
        hue, saturation, lightness = rgb_to_hsl(sample)
        if not MIN_SAMPLE_LIGHTNESS <= lightness <= MAX_SAMPLE_LIGHTNESS:
            continue
        if saturation < MIN_SAMPLE_SATURATION:
            continue
        index = int(hue / (360 / HUE_BINS)) % HUE_BINS
        bins.setdefault(index, []).append(saturation)
        members.setdefault(index, []).append((saturation, sample))

    if not bins:
        return None
    best = max(bins, key=lambda index: sum(bins[index]))
    weight = sum(bins[best])
    if not samples or weight < len(samples) * MIN_WINNING_WEIGHT:
        return None

    # The bin's saturation-weighted mean. Averaging inside one 15-degree
    # bin cannot produce mud — every member already agrees about the hue,
    # and the weighting keeps the washed-out members from dragging it in.
    total = sum(w for w, _ in members[best])
    channels = [
        round(sum(w * sample[i] for w, sample in members[best]) / total)
        for i in range(3)
    ]
    return (channels[0], channels[1], channels[2])


def decode_colour(data: bytes) -> Optional[tuple[int, int, int]]:
    """The dominant colour of an encoded image, or None if the image has
    no usable hue. Raises ArtworkError if it will not decode at all.

    The two are told apart deliberately, because only one of them is an
    answer. "This cover is monochrome" is a fact about the cover and gets
    remembered; "these bytes are not an image" is a truncated download or
    a format this build cannot read, and caching it would make one bad
    fetch permanent — the same rule the lyrics cache follows, where only
    a genuine 404 is ever cached negatively.

    QImage rather than a new dependency: it already ships with the app,
    reads every format Spotify serves, and is safe to use off the UI
    thread — it is QPixmap that is not.
    """
    # Imported here rather than at module scope so the pure half above
    # stays importable without Qt.
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
    except ImportError:  # pragma: no cover - PySide6 is a hard dependency
        raise ArtworkError("PySide6 unavailable: cannot read artwork")
    image = QImage()
    if not image.loadFromData(data):
        raise ArtworkError(f"artwork did not decode ({len(data)} bytes)")
    # IgnoreAspectRatio, so the sample count is the same for every cover
    # however it is shaped; smooth, so downscaling averages rather than
    # picking one pixel in every fourteen.
    small = image.scaled(
        SAMPLE_EDGE,
        SAMPLE_EDGE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    samples = []
    for y in range(small.height()):
        for x in range(small.width()):
            colour = small.pixelColor(x, y)
            samples.append((colour.red(), colour.green(), colour.blue()))
    return dominant_colour(samples)


# -- impure: getting the bytes, and not getting them twice --------------


def _download(url: str) -> bytes:
    """The cover, as bytes. The single door to the network from here.

    Raises ArtworkError for everything — a missing cover is not worth a
    taxonomy, because every failure has the same consequence: no tint,
    and the lyrics carry on exactly as they were.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            data = response.read(_MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ArtworkError(f"artwork fetch failed: {exc}") from exc
    if len(data) > _MAX_BYTES:
        raise ArtworkError(f"artwork larger than {_MAX_BYTES} bytes")
    return data


class ArtworkProvider:
    """Derived cover colours, cached on disk by track id."""

    def __init__(self, cache_dir: Path = DEFAULT_ARTWORK_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir)

    def colour_for(
        self, track_id: Optional[str], url: Optional[str]
    ) -> Optional[tuple[int, int, int]]:
        """This track's cover colour, from disk if it has been worked out
        before. None means no tint, for any reason at all.

        Never raises: the caller is a worker whose failure mode is simply
        that the window stays the colour it already was.
        """
        if not track_id:
            return None
        cached = self._read_cache(track_id)
        if cached is not None:
            return cached[0]
        if not url:
            return None
        try:
            colour = decode_colour(_download(url))
        except ArtworkError as exc:
            # Never cached: a fetch that failed and an image that would not
            # decode are both unknown outcomes, and the cover may well be
            # readable next time.
            logger.info("no album colour for %s: %s", track_id, exc)
            return None
        except Exception:
            logger.exception("unexpected error reading artwork for %s", track_id)
            return None
        # A cover that decoded and has no usable hue IS the answer, so it
        # is remembered like any other.
        self._write_cache(track_id, colour)
        logger.debug("album colour for %s: %s", track_id, colour)
        return colour

    # -- cache ----------------------------------------------------------

    def _cache_path(self, track_id: str) -> Path:
        return self.cache_dir / (_SAFE_FILENAME_RE.sub("_", track_id) + ".json")

    def _read_cache(self, track_id: str):
        """``(colour,)`` when this track has been worked out — colour may
        be None — or None when it has not. The tuple is what separates
        "cached as no colour" from "not cached"."""
        try:
            entry = json.loads(self._cache_path(track_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        raw = entry.get("colour")
        if raw is None:
            return (None,)
        try:
            red, green, blue = (int(c) for c in raw)
        except (TypeError, ValueError):
            return None
        if not all(0 <= c <= 255 for c in (red, green, blue)):
            return None
        return ((red, green, blue),)

    def _write_cache(self, track_id: str, colour) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(track_id).write_text(
                json.dumps({"colour": list(colour) if colour else None}),
                encoding="utf-8",
            )
        except OSError:
            pass  # cache is best-effort
