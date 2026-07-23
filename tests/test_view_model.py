from lyrisync.lyrics_provider import TrackLyrics
from lyrisync.player_monitor import PlaybackState, PlayerSnapshot
from lyrisync.view_model import LyricsViewModel, Mode


SYNCED = TrackLyrics(synced=[(10.0, "one"), (20.0, "two"), (30.0, "three")])
PLAIN = TrackLyrics(plain="line a\nline b")


def snapshot(track_id="trackA", title="Song", artist="Artist"):
    return PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id=track_id,
        title=title,
        artist=artist,
        album="Album",
        duration_ms=225000,
        position_seconds=0.0,
    )


def test_starts_idle():
    vm = LyricsViewModel()
    display = vm.display()
    assert display.mode is Mode.IDLE
    assert display.current


def test_track_change_requests_fetch_and_shows_fetching():
    vm = LyricsViewModel()
    assert vm.track_changed(snapshot()) is True
    display = vm.display()
    assert display.mode is Mode.FETCHING
    assert display.header == "Artist — Song"
    assert display.current == "fetching…"


def test_trackless_snapshot_goes_idle_without_fetch():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    no_track = PlayerSnapshot(state=PlaybackState.PLAYING)
    assert vm.track_changed(no_track) is False
    assert vm.display().mode is Mode.IDLE


def test_fetch_result_for_current_track_is_displayed():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    assert vm.fetch_completed("trackA", SYNCED) is True
    assert vm.display().mode is Mode.SYNCED


def test_stale_fetch_result_is_ignored():
    vm = LyricsViewModel()
    vm.track_changed(snapshot(track_id="trackA"))
    vm.track_changed(snapshot(track_id="trackB", title="Other"))

    # Result for trackA arrives after the switch to trackB.
    assert vm.fetch_completed("trackA", SYNCED) is False
    display = vm.display()
    assert display.mode is Mode.FETCHING  # still waiting on trackB
    assert display.header == "Artist — Other"

    assert vm.fetch_completed("trackB", PLAIN) is True
    assert vm.display().mode is Mode.PLAIN


def test_fetch_after_going_idle_is_ignored():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.player_state_changed(PlaybackState.NOT_RUNNING)
    assert vm.fetch_completed("trackA", SYNCED) is False
    assert vm.display().mode is Mode.IDLE


def test_failed_fetch_shows_retryable_error_not_no_lyrics():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    assert vm.fetch_completed("trackA", None, ok=False) is True
    display = vm.display()
    assert display.mode is Mode.ERROR
    assert "unavailable" in display.current
    assert display.current != "no lyrics found"


def test_stale_failed_fetch_is_ignored():
    vm = LyricsViewModel()
    vm.track_changed(snapshot(track_id="trackA"))
    vm.track_changed(snapshot(track_id="trackB"))
    assert vm.fetch_completed("trackA", None, ok=False) is False
    assert vm.display().mode is Mode.FETCHING


def test_no_lyrics_mode():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", None)
    display = vm.display()
    assert display.mode is Mode.NO_LYRICS
    assert display.current == "no lyrics found"


def test_plain_mode_carries_full_text():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", PLAIN)
    display = vm.display()
    assert display.mode is Mode.PLAIN
    assert display.plain_text == "line a\nline b"
    assert "not synced" in display.previous


def test_synced_lines_advance_with_position():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)

    assert vm.position_changed(5.0) is False  # index still -1 (before first)
    display = vm.display()
    assert display.current == ""
    assert display.upcoming == "one"

    assert vm.position_changed(12.0) is True
    display = vm.display()
    assert (display.previous, display.current, display.upcoming) == ("", "one", "two")

    assert vm.position_changed(12.5) is False  # same line: no redraw needed
    assert vm.position_changed(21.0) is True
    display = vm.display()
    assert (display.previous, display.current, display.upcoming) == ("one", "two", "three")

    assert vm.position_changed(99.0) is True  # past the end: last line holds
    assert vm.display().current == "three"


def test_seek_backwards_recovers():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)
    vm.position_changed(31.0)
    assert vm.display().current == "three"
    assert vm.position_changed(11.0) is True
    assert vm.display().current == "one"


def test_position_ignored_unless_synced():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    assert vm.position_changed(12.0) is False  # still fetching
    vm.fetch_completed("trackA", PLAIN)
    assert vm.position_changed(12.0) is False  # plain lyrics don't advance
    assert vm.position_changed(None) is False


def test_stop_and_quit_reset_to_idle():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)
    assert vm.player_state_changed(PlaybackState.STOPPED) is True
    assert vm.display().mode is Mode.IDLE
    # Repeated stop reports no change (no redraw churn while idle).
    assert vm.player_state_changed(PlaybackState.NOT_RUNNING) is False
    # Playing/paused don't disturb the display by themselves.
    assert vm.player_state_changed(PlaybackState.PLAYING) is False
    assert vm.player_state_changed(PlaybackState.PAUSED) is False


def test_new_track_after_lyrics_resets_lines():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)
    vm.position_changed(25.0)
    assert vm.track_changed(snapshot(track_id="trackB", title="Next")) is True
    display = vm.display()
    assert display.mode is Mode.FETCHING
    assert display.previous == ""
    assert display.upcoming == ""
