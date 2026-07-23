"""Fetch and cache lyrics from LRCLIB.

Pure logic: given a ``PlayerSnapshot``, return ``TrackLyrics`` or ``None``.
Knows nothing about polling or the UI.

Fallback chain: synced lyrics → plain lyrics → ``None``. Responses are
cached on disk as JSON keyed by Spotify track ID, including negative
results, so a song known to have no lyrics is never re-queried.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lyrisync.player_monitor import PlayerSnapshot

LRCLIB_GET_URL = "https://lrclib.net/api/get"
USER_AGENT = "lyrisync/0.1.0 (https://github.com/matthewhuang/lyrisync)"
DEFAULT_CACHE_DIR = Path(".lyrics_cache")

_REQUEST_TIMEOUT = 10.0

# [mm:ss.xx] — also tolerates [m:ss] and multiple stamps per line.
# Metadata tags like [ar:...] contain no m:ss pair and never match.
_TIMESTAMP_RE = re.compile(r"\[(\d+):(\d{1,2}(?:\.\d+)?)\]")

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_-]")

# Distinguishes "couldn't reach LRCLIB" (don't cache) from "no lyrics" (cache).
_NETWORK_ERROR = object()


@dataclass(frozen=True)
class TrackLyrics:
    """Lyrics for one track. At least one of the two fields is set."""

    synced: Optional[list[tuple[float, str]]] = None
    plain: Optional[str] = None

    @property
    def kind(self) -> str:
        return "synced" if self.synced else "plain"


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """Parse LRC text into (timestamp_seconds, line) tuples, sorted by time.

    Lines may carry several timestamps (``[00:12.00][00:55.30] chorus``);
    each becomes its own entry. Lines with no timestamp (metadata, garbage)
    are skipped. Empty lyric lines are kept — they mark instrumental gaps.
    """
    entries: list[tuple[float, str]] = []
    for raw_line in text.splitlines():
        stamps = list(_TIMESTAMP_RE.finditer(raw_line))
        if not stamps:
            continue
        content = raw_line[stamps[-1].end() :].strip()
        for stamp in stamps:
            seconds = int(stamp.group(1)) * 60 + float(stamp.group(2))
            entries.append((seconds, content))
    entries.sort(key=lambda entry: entry[0])
    return entries


def _fetch_json(url: str) -> Optional[dict]:
    """GET a JSON document. Returns None on 404 (a definitive "not found");
    raises on network trouble or other HTTP errors."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


class LyricsProvider:
    def __init__(self, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir)

    def get_lyrics(self, snapshot: PlayerSnapshot) -> Optional[TrackLyrics]:
        """Lyrics for the snapshot's track, from cache or LRCLIB.

        Returns None when the track has no lyrics, the snapshot has no
        usable track metadata, or the network is unavailable. Only
        definitive answers (found / confirmed not found) are cached.
        """
        if snapshot.track_id is None:
            return None

        cached = self._read_cache(snapshot.track_id)
        if cached is not None:
            return self._decode_cache_entry(cached)

        if not snapshot.title or not snapshot.artist:
            return None

        lyrics = self._fetch(snapshot)
        if lyrics is _NETWORK_ERROR:
            return None
        self._write_cache(snapshot.track_id, lyrics)
        return lyrics

    def _fetch(self, snapshot: PlayerSnapshot):
        params = {
            "track_name": snapshot.title,
            "artist_name": snapshot.artist,
        }
        if snapshot.album:
            params["album_name"] = snapshot.album
        if snapshot.duration_ms is not None:
            params["duration"] = str(round(snapshot.duration_ms / 1000))
        url = LRCLIB_GET_URL + "?" + urllib.parse.urlencode(params)
        try:
            data = _fetch_json(url)
        except (OSError, ValueError):
            return _NETWORK_ERROR
        if data is None:
            return None

        synced_text = data.get("syncedLyrics")
        plain_text = data.get("plainLyrics")
        synced = parse_lrc(synced_text) if synced_text else None
        plain = plain_text.strip() if plain_text and plain_text.strip() else None
        if synced or plain:
            return TrackLyrics(synced=synced or None, plain=plain)
        return None  # instrumental or empty response

    # -- cache ------------------------------------------------------------

    def _cache_path(self, track_id: str) -> Path:
        return self.cache_dir / (_SAFE_FILENAME_RE.sub("_", track_id) + ".json")

    def _read_cache(self, track_id: str) -> Optional[dict]:
        path = self._cache_path(track_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(self, track_id: str, lyrics: Optional[TrackLyrics]) -> None:
        entry = {
            "found": lyrics is not None,
            "synced": lyrics.synced if lyrics else None,
            "plain": lyrics.plain if lyrics else None,
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(track_id).write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # cache is best-effort

    @staticmethod
    def _decode_cache_entry(entry: dict) -> Optional[TrackLyrics]:
        if not entry.get("found"):
            return None
        synced_raw = entry.get("synced")
        synced = [(float(t), str(line)) for t, line in synced_raw] if synced_raw else None
        return TrackLyrics(synced=synced, plain=entry.get("plain"))
