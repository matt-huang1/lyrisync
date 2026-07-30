"""Map a playback position onto a parsed lyrics timeline."""

from __future__ import annotations

from bisect import bisect_right


def current_line_index(
    parsed_lyrics: list[tuple[float, str]], position_seconds: float
) -> int:
    """Index of the lyric line active at ``position_seconds``.

    Returns -1 while the position is before the first line (or the list is
    empty); after the last line's timestamp the last index stays current.
    ``parsed_lyrics`` must be sorted by timestamp, as ``parse_lrc`` returns.
    """
    timestamps = [timestamp for timestamp, _ in parsed_lyrics]
    return bisect_right(timestamps, position_seconds) - 1
