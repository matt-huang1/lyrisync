"""The two terminal tools, which had no tests at all until session B.

`sottovoce-monitor` and `sottovoce-lyrics` are debugging surfaces, not the
app, and that is exactly why they rot quietly: nothing fails when one of
them drifts. They are also the other two places the app writes a song's
name, so they are where a header format would go out of step with the
window.

Nothing here starts a monitor or reaches Spotify — the printers are driven
with snapshots directly, which is all they are.
"""

TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce import lyrics_cli, monitor_cli
from sottovoce.lyrics_provider import LyricsError, TrackLyrics
from sottovoce.player_monitor import PlaybackState, PlayerSnapshot
from sottovoce.view_model import HEADER_SEPARATOR


def snapshot(**fields):
    base = dict(
        state=PlaybackState.PLAYING,
        track_id="t1",
        title="Spring Day",
        artist="BTS",
        album="You Never Walk Alone",
        duration_ms=284000,
        position_seconds=12.0,
    )
    base.update(fields)
    return PlayerSnapshot(**base)


class FakeProvider:
    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error

    def get_lyrics(self, snapshot):
        if self.error is not None:
            raise self.error
        return self.answer


# -- the song's name, on every surface ------------------------------------


@pytest.mark.parametrize("printer", ["monitor", "lyrics"])
def test_both_tools_name_the_song_the_way_the_window_does(printer, capsys):
    """One format, three surfaces. It used to be written out twice — and a
    window and a terminal tool with two copies of one format string are two
    things that can disagree about the same song."""
    if printer == "monitor":
        monitor_cli.EventPrinter().on_track_change(snapshot())
    else:
        app = lyrics_cli.LyricsApp(FakeProvider(TrackLyrics(plain="a line")))
        app.on_track_change(snapshot())

    printed = capsys.readouterr().out
    assert f"Spring Day{HEADER_SEPARATOR}BTS" in printed
    assert "Spring Day — BTS" not in printed


def test_the_monitor_still_reports_the_album_and_the_clock(capsys):
    """The header carries song and artist; the album, the duration and the
    track id are what make this tool worth having."""
    monitor_cli.EventPrinter().on_track_change(snapshot())

    printed = capsys.readouterr().out
    assert "[You Never Walk Alone]" in printed
    assert "(4:44)" in printed
    assert "id=t1" in printed


# -- what the lyrics tool does with each answer ---------------------------


def test_synced_lyrics_draw_the_three_line_block(capsys):
    app = lyrics_cli.LyricsApp(
        FakeProvider(TrackLyrics(synced=[(1.0, "one"), (5.0, "two"), (9.0, "three")]))
    )
    app.on_track_change(snapshot())
    app.on_position_update(snapshot(position_seconds=6.0))

    printed = capsys.readouterr().out
    assert "synced lyrics" in printed
    assert "▶ two" in printed  # the current line, with its marker
    assert "one" in printed and "three" in printed


def test_plain_lyrics_print_once(capsys):
    app = lyrics_cli.LyricsApp(FakeProvider(TrackLyrics(plain="line a\nline b")))
    app.on_track_change(snapshot())

    printed = capsys.readouterr().out
    assert "plain lyrics (not synced)" in printed
    assert "line a" in printed and "line b" in printed


def test_a_missing_answer_says_so(capsys):
    app = lyrics_cli.LyricsApp(FakeProvider(None))
    app.on_track_change(snapshot())

    assert "no lyrics found" in capsys.readouterr().out


def test_a_failed_fetch_is_a_retry_state_not_a_crash(capsys):
    """The same distinction the window makes: an error is about the
    network, never about the song."""
    app = lyrics_cli.LyricsApp(FakeProvider(error=LyricsError("timed out")))
    app.on_track_change(snapshot())

    printed = capsys.readouterr().out
    assert "lyrics unavailable" in printed
    assert "will retry" in printed


def test_a_trackless_snapshot_says_nothing_is_loaded(capsys):
    app = lyrics_cli.LyricsApp(FakeProvider(None))
    app.on_track_change(PlayerSnapshot(state=PlaybackState.STOPPED))

    assert "(no track loaded)" in capsys.readouterr().out


def test_stopping_drops_the_lines_it_was_drawing(capsys):
    app = lyrics_cli.LyricsApp(FakeProvider(TrackLyrics(synced=[(1.0, "one")])))
    app.on_track_change(snapshot())
    app.on_state_change(snapshot(state=PlaybackState.STOPPED))

    assert app.synced is None
    assert "[stopped]" in capsys.readouterr().out
