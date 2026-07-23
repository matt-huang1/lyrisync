from lyrisync.lyrics_provider import TrackLyrics
from lyrisync.player_monitor import PlaybackState, PlayerSnapshot
from lyrisync.view_model import RETRY_INTERVAL_SECONDS, LyricsViewModel, Mode


SYNCED = TrackLyrics(synced=[(10.0, "one"), (20.0, "two"), (30.0, "three")])
PLAIN = TrackLyrics(plain="line a\nline b")
KOREAN_SYNCED = TrackLyrics(
    synced=[(10.0, "안녕하세요"), (20.0, "English line"), (30.0, "잘 가")]
)
KOREAN_PLAIN = TrackLyrics(plain="안녕하세요\n잘 가")


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
    assert display.header == "Song — Artist"
    assert display.current == ""  # window renders the loading indicator


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
    assert display.header == "Other — Artist"

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


def test_stop_suspends_and_resume_restores():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)
    assert vm.player_state_changed(PlaybackState.STOPPED) is True
    assert vm.display().mode is Mode.IDLE
    # Repeated stop reports no change (no redraw churn while idle).
    assert vm.player_state_changed(PlaybackState.NOT_RUNNING) is False
    # Resuming the same track fires no track-change event, so the display
    # must restore from the suspended state.
    assert vm.player_state_changed(PlaybackState.PLAYING) is True
    assert vm.display().mode is Mode.SYNCED
    # Pause doesn't disturb the display.
    assert vm.player_state_changed(PlaybackState.PAUSED) is False


def test_fetch_completing_while_suspended_stays_hidden_until_resume():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.player_state_changed(PlaybackState.STOPPED)  # suspend mid-fetch
    assert vm.fetch_completed("trackA", SYNCED) is False  # nothing visible
    assert vm.display().mode is Mode.IDLE
    assert vm.player_state_changed(PlaybackState.PLAYING) is True
    assert vm.display().mode is Mode.SYNCED


def test_duplicate_track_event_keeps_display_and_skips_fetch():
    vm = LyricsViewModel()
    assert vm.track_changed(snapshot()) is True
    vm.fetch_completed("trackA", SYNCED)
    # Same (kind, id) re-announced (metadata settling, monitor blips):
    # no loading flash, no redundant fetch.
    assert vm.track_changed(snapshot()) is False
    assert vm.display().mode is Mode.SYNCED


def test_duplicate_track_event_while_fetching_skips_second_fetch():
    vm = LyricsViewModel()
    assert vm.track_changed(snapshot()) is True
    assert vm.track_changed(snapshot()) is False  # first fetch still owns it
    assert vm.display().mode is Mode.FETCHING
    vm.fetch_completed("trackA", SYNCED)
    assert vm.display().mode is Mode.SYNCED


def test_duplicate_track_event_in_error_refetches():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", None, ok=False, now=10.0)
    assert vm.display().mode is Mode.ERROR
    # Re-announcement may carry corrected metadata — worth a new attempt.
    assert vm.track_changed(snapshot()) is True
    assert vm.display().mode is Mode.FETCHING


def test_dj_transition_sequence_no_loading_flash():
    """narration → song (same ID) → lyrics, once; duplicates change nothing."""
    vm = LyricsViewModel()
    assert vm.track_changed(dj_narration(track_id="shared123")) is False
    assert vm.display().mode is Mode.NON_MUSIC

    assert vm.track_changed(snapshot(track_id="shared123", title="Company")) is True
    vm.fetch_completed("shared123", SYNCED)
    assert vm.display().mode is Mode.SYNCED

    # Duplicate song announcement (settling metadata / debounced blip
    # leaking through): display must not flash back to loading.
    assert vm.track_changed(snapshot(track_id="shared123", title="Company")) is False
    assert vm.display().mode is Mode.SYNCED


def korean_vm(romanisation=True):
    vm = LyricsViewModel()
    vm.romanisation_enabled = romanisation
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", KOREAN_SYNCED)
    return vm


def test_pronunciation_for_current_korean_line():
    vm = korean_vm()
    vm.position_changed(12.0)  # current: 안녕하세요
    display = vm.display()
    assert display.pronunciation == "annyeonghaseyo"
    assert display.current == "안녕하세요"


def test_no_pronunciation_when_toggle_off():
    vm = korean_vm(romanisation=False)
    vm.position_changed(12.0)
    assert vm.display().pronunciation == ""


def test_no_pronunciation_for_english_line_of_korean_track():
    vm = korean_vm()
    vm.position_changed(22.0)  # current: "English line"
    assert vm.display().pronunciation == ""


def test_no_pronunciation_before_first_line():
    vm = korean_vm()
    vm.position_changed(5.0)  # index -1, current empty
    assert vm.display().pronunciation == ""


def test_no_pronunciation_for_non_korean_track():
    vm = LyricsViewModel()
    vm.romanisation_enabled = True
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)
    vm.position_changed(12.0)
    display = vm.display()
    assert display.pronunciation == ""
    assert vm.has_korean_lyrics is False


def test_no_pronunciation_for_plain_lyrics():
    vm = LyricsViewModel()
    vm.romanisation_enabled = True
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", KOREAN_PLAIN)
    display = vm.display()
    assert display.mode is Mode.PLAIN
    assert display.pronunciation == ""
    # Milestone 6 review fix: no menu entry for plain Korean lyrics —
    # the toggle would do nothing without synced timestamps.
    assert vm.has_korean_lyrics is False


def test_menu_gating_requires_korean_AND_synced():
    korean_synced = LyricsViewModel()
    korean_synced.track_changed(snapshot())
    korean_synced.fetch_completed("trackA", KOREAN_SYNCED)
    assert korean_synced.has_korean_lyrics is True

    english_synced = LyricsViewModel()
    english_synced.track_changed(snapshot())
    english_synced.fetch_completed("trackA", SYNCED)
    assert english_synced.has_korean_lyrics is False


def test_has_korean_lyrics_lifecycle():
    vm = korean_vm()
    assert vm.has_korean_lyrics is True
    # New (English) track clears it immediately — no stale menu entry
    # while the next fetch is in flight.
    vm.track_changed(snapshot(track_id="trackB", title="English Song"))
    assert vm.has_korean_lyrics is False
    vm.fetch_completed("trackB", SYNCED)
    assert vm.has_korean_lyrics is False


def test_pronunciation_for_helper_matches_display():
    vm = korean_vm()
    assert vm.pronunciation_for("잘 가") == "jal ga"
    assert vm.pronunciation_for("plain english") == ""
    vm.romanisation_enabled = False
    assert vm.pronunciation_for("잘 가") == ""


def test_timeline_only_in_synced_mode():
    vm = LyricsViewModel()
    assert vm.timeline() is None
    vm.track_changed(snapshot())
    assert vm.timeline() is None  # fetching
    vm.fetch_completed("trackA", SYNCED)
    lines, index = vm.timeline()
    assert lines == SYNCED.synced
    assert index == -1
    vm.position_changed(12.0)
    assert vm.timeline()[1] == 0


def dj_narration(track_id="shared123"):
    return PlayerSnapshot(
        state=PlaybackState.PLAYING,
        track_id=track_id,
        track_kind="media",
        title="Up next",
        artist="DJ X",
        album="DJ",
        duration_ms=0,
        position_seconds=1.0,
    )


def test_dj_narration_shows_header_with_empty_body():
    vm = LyricsViewModel()
    assert vm.track_changed(dj_narration()) is False  # no fetch requested
    display = vm.display()
    assert display.mode is Mode.NON_MUSIC
    assert display.header == "Up next — DJ X"
    assert display.current == ""  # never "no lyrics found" for narration
    assert display.previous == "" and display.upcoming == ""


def test_dj_narration_into_song_with_same_id_fetches():
    vm = LyricsViewModel()
    vm.track_changed(dj_narration(track_id="shared123"))
    # The announced song arrives with the SAME id, different kind.
    assert vm.track_changed(snapshot(track_id="shared123", title="Company")) is True
    assert vm.display().mode is Mode.FETCHING
    vm.fetch_completed("shared123", SYNCED)
    assert vm.display().mode is Mode.SYNCED


def test_error_retries_after_interval():
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", None, ok=False, now=100.0)
    assert vm.display().mode is Mode.ERROR

    assert vm.retry_due(100.0 + RETRY_INTERVAL_SECONDS - 1) is False
    assert vm.display().mode is Mode.ERROR

    assert vm.retry_due(100.0 + RETRY_INTERVAL_SECONDS) is True
    assert vm.display().mode is Mode.FETCHING  # retry in flight
    assert vm.retry_due(100.0 + RETRY_INTERVAL_SECONDS + 1) is False  # no double-fire

    # Second failure re-arms the clock from the new failure time.
    vm.fetch_completed("trackA", None, ok=False, now=140.0)
    assert vm.retry_due(140.0 + RETRY_INTERVAL_SECONDS - 1) is False
    assert vm.retry_due(140.0 + RETRY_INTERVAL_SECONDS) is True

    # Success ends the retry loop.
    vm.fetch_completed("trackA", SYNCED)
    assert vm.display().mode is Mode.SYNCED
    assert vm.retry_due(1000.0) is False


def test_retry_not_due_in_other_modes():
    vm = LyricsViewModel()
    assert vm.retry_due(1e9) is False  # idle
    vm.track_changed(snapshot())
    assert vm.retry_due(1e9) is False  # fetching
    vm.fetch_completed("trackA", SYNCED)
    assert vm.retry_due(1e9) is False  # synced


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
