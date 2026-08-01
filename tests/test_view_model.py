TIER = "unit"  # Qt-free logic, called directly

from sottovoce.lyrics_provider import TrackLyrics
from sottovoce.player_monitor import PlaybackState, PlayerSnapshot
from sottovoce.view_model import RETRY_INTERVAL_SECONDS, LyricsViewModel, Mode


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
    assert display.header == "Song · Artist"
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
    assert display.header == "Other · Artist"

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
    assert display.header == "Up next · DJ X"
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


# -- tap-to-sync ----------------------------------------------------------

MANY_PLAIN = TrackLyrics(plain="one\n\ntwo\nthree\nfour")


def plain_vm(lyrics=MANY_PLAIN):
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", lyrics)
    return vm


def test_sync_starts_from_plain_lyrics():
    vm = plain_vm()
    assert vm.begin_sync() is True
    assert vm.display().mode is Mode.SYNCING
    assert vm.sync_session.total == 4  # the blank line is not a tap target


def test_sync_display_shows_current_next_two_and_progress():
    vm = plain_vm()
    vm.begin_sync()
    display = vm.display()
    assert display.header == "Song · Artist"
    assert display.previous == ""  # nothing stamped yet
    assert display.current == "one"
    assert display.upcoming == "two\nthree"
    assert display.progress == "0 / 4 lines"

    vm.sync_session.stamp(1.0)
    display = vm.display()
    assert display.current == "two"
    assert display.upcoming == "three\nfour"
    assert display.progress == "1 / 4 lines"


def test_sync_keeps_the_just_stamped_line_above_the_target():
    """The stamped line is still being sung, and watching it run out is the
    cue for the next tap."""
    vm = plain_vm()
    vm.begin_sync()
    vm.sync_session.stamp(1.0)
    assert vm.display().previous == "one"
    vm.sync_session.stamp(2.0)
    display = vm.display()
    assert display.previous == "two"
    assert display.current == "three"


def test_undo_restores_the_line_above_as_well():
    vm = plain_vm()
    vm.begin_sync()
    for position in (1.0, 2.0, 3.0):
        vm.sync_session.stamp(position)
    assert (vm.display().previous, vm.display().current) == ("three", "four")

    vm.sync_session.undo()
    assert (vm.display().previous, vm.display().current) == ("two", "three")
    vm.sync_session.undo()
    assert (vm.display().previous, vm.display().current) == ("one", "two")
    vm.sync_session.undo()
    display = vm.display()
    assert (display.previous, display.current) == ("", "one")
    assert display.progress == "0 / 4 lines"


def test_the_line_above_is_the_stamped_one_not_a_lyrics_neighbour():
    """Blank lines are skipped as tap targets, so the line above the target
    is the previous TARGET, not the previous line of the lyrics file."""
    vm = plain_vm()  # "one\n\ntwo\nthree\nfour": a blank sits between 1 and 2
    vm.begin_sync()
    vm.sync_session.stamp(1.0)
    assert vm.display().previous == "one"


def test_sync_display_near_the_end_runs_out_of_upcoming_lines():
    vm = plain_vm()
    vm.begin_sync()
    for position in (1.0, 2.0, 3.0):
        vm.sync_session.stamp(position)
    display = vm.display()
    assert display.current == "four"
    assert display.upcoming == ""
    assert display.progress == "3 / 4 lines"


def test_sync_refused_without_lyrics_in_hand():
    vm = LyricsViewModel()
    assert vm.begin_sync() is False  # idle
    vm.track_changed(snapshot())
    assert vm.begin_sync() is False  # fetching
    assert vm.display().mode is Mode.FETCHING
    vm.fetch_completed("trackA", None)
    assert vm.begin_sync() is False  # no lyrics found
    assert vm.display().mode is Mode.NO_LYRICS


def test_sync_refused_when_there_is_nothing_to_stamp():
    vm = plain_vm(TrackLyrics(plain="   \n\n"))
    assert vm.begin_sync() is False
    assert vm.sync_session is None


def test_ending_a_sync_returns_to_the_plain_lyrics():
    vm = plain_vm()
    vm.begin_sync()
    vm.sync_session.stamp(1.0)
    assert vm.end_sync() is True
    assert vm.sync_session is None
    display = vm.display()
    assert display.mode is Mode.PLAIN
    assert display.plain_text == MANY_PLAIN.plain
    assert vm.end_sync() is False  # nothing left to end


def test_a_new_track_discards_the_sync():
    vm = plain_vm()
    vm.begin_sync()
    vm.track_changed(snapshot(track_id="trackB", title="Next"))
    assert vm.sync_session is None
    assert vm.display().mode is Mode.FETCHING


def test_a_repeat_announcement_of_the_same_track_keeps_the_sync():
    """Metadata settling re-announces the current track; a pass in progress
    must survive it."""
    vm = plain_vm()
    vm.begin_sync()
    vm.sync_session.stamp(1.0)
    assert vm.track_changed(snapshot()) is False
    assert vm.display().mode is Mode.SYNCING
    assert vm.sync_session.index == 1


def test_cancelling_while_stopped_restores_plain_not_the_session():
    vm = plain_vm()
    vm.begin_sync()
    vm.end_sync()
    vm.player_state_changed(PlaybackState.STOPPED)
    assert vm.display().mode is Mode.IDLE
    vm.player_state_changed(PlaybackState.PLAYING)
    assert vm.display().mode is Mode.PLAIN


def test_a_fetch_landing_during_a_pass_does_not_tear_it_down():
    """A pass is modal and user-driven. A retry or a re-announcement's fetch
    completing underneath it must not throw away the taps so far."""
    vm = plain_vm()
    vm.begin_sync()
    vm.sync_session.stamp(1.0)
    assert vm.fetch_completed("trackA", SYNCED) is False  # nothing to redraw
    assert vm.display().mode is Mode.SYNCING
    assert vm.sync_session.index == 1
    assert vm.display().current == "two"

    # The result is not discarded either: it becomes where cancelling lands.
    vm.end_sync()
    assert vm.display().mode is Mode.SYNCED


def test_a_failed_fetch_during_a_pass_is_held_back_the_same_way():
    vm = plain_vm()
    vm.begin_sync()
    assert vm.fetch_completed("trackA", None, ok=False, now=1.0) is False
    assert vm.display().mode is Mode.SYNCING
    vm.end_sync()
    assert vm.display().mode is Mode.ERROR


def test_reload_after_saving_a_sync_refetches_the_same_track():
    vm = plain_vm()
    vm.begin_sync()
    vm.end_sync()
    assert vm.begin_reload("trackA") is True
    assert vm.display().mode is Mode.FETCHING
    # The saved sync comes back through the provider as synced lyrics.
    assert vm.fetch_completed("trackA", SYNCED) is True
    assert vm.display().mode is Mode.SYNCED


def test_reload_refused_for_a_track_that_is_no_longer_current():
    vm = plain_vm()
    assert vm.begin_reload("trackB") is False
    assert vm.display().mode is Mode.PLAIN
    assert LyricsViewModel().begin_reload("trackA") is False  # no track at all


def test_romanisation_offered_during_a_sync_of_korean_plain_lyrics():
    """Plain Korean lyrics get no romanisation toggle — there is no current
    line to put it under — but a sync pass has exactly that."""
    vm = plain_vm(KOREAN_PLAIN)
    assert vm.has_korean_lyrics is False
    vm.begin_sync()
    assert vm.has_korean_lyrics is True
    vm.romanisation_enabled = True
    assert vm.display().pronunciation == "annyeonghaseyo"
    vm.end_sync()
    assert vm.has_korean_lyrics is False


def test_no_romanisation_offered_syncing_non_korean_lyrics():
    vm = plain_vm()
    vm.begin_sync()
    assert vm.has_korean_lyrics is False


# -- re-syncing -----------------------------------------------------------

USER_SYNC = TrackLyrics(synced=[(1.0, "alpha"), (4.0, "beta"), (9.0, "gamma")])


def synced_vm(lyrics=USER_SYNC):
    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", lyrics)
    return vm


def test_menu_offers_a_first_sync_for_plain_lyrics():
    vm = plain_vm()
    assert vm.sync_menu_entry(has_user_sync=False) == "Sync this song"


def test_menu_offers_a_resync_wherever_the_users_own_sync_is_showing():
    assert synced_vm().sync_menu_entry(has_user_sync=True) == "Re-sync this song"
    # Plain lyrics with a sync already on disk (a hand-edit broke it, say).
    assert plain_vm().sync_menu_entry(has_user_sync=True) == "Re-sync this song"


def test_menu_never_offers_overwriting_lyrics_that_are_not_the_users():
    """A song synced by LRCLIB has no user sync to redo."""
    assert synced_vm().sync_menu_entry(has_user_sync=False) is None


def test_menu_entry_absent_where_there_is_nothing_to_stamp():
    vm = LyricsViewModel()
    assert vm.sync_menu_entry(has_user_sync=True) is None  # idle
    vm.track_changed(snapshot())
    assert vm.sync_menu_entry(has_user_sync=True) is None  # fetching
    vm.fetch_completed("trackA", None)
    assert vm.sync_menu_entry(has_user_sync=True) is None  # no lyrics
    assert vm.sync_menu_entry(has_user_sync=False) is None

    blank = plain_vm(TrackLyrics(plain="  \n\n"))
    assert blank.sync_menu_entry(has_user_sync=True) is None


def test_menu_entry_absent_during_a_pass_already_running():
    vm = plain_vm()
    vm.begin_sync()
    assert vm.sync_menu_entry(has_user_sync=False) is None


def test_resync_stamps_the_lines_of_the_existing_sync():
    """A completed pass stamps every non-blank plain line, so the stored
    lines are the song's lines — a re-sync needs no plain lyrics on disk or
    on the network."""
    vm = synced_vm()
    assert vm.begin_sync() is True
    session = vm.sync_session
    assert session.lines == ["alpha", "beta", "gamma"]
    assert session.stamps == []  # a fresh pass, from line one
    display = vm.display()
    assert display.mode is Mode.SYNCING
    assert display.current == "alpha"
    assert display.progress == "0 / 3 lines"


def test_resync_skips_instrumental_gaps_in_the_stored_lines():
    """LRCLIB-style empty lines mark instrumental gaps and are not taps."""
    vm = synced_vm(TrackLyrics(synced=[(1.0, "alpha"), (4.0, ""), (9.0, "gamma")]))
    vm.begin_sync()
    assert vm.sync_session.lines == ["alpha", "gamma"]


def test_a_track_with_both_forms_syncs_from_the_plain_text():
    """LRCLIB usually returns plain alongside synced; that is the fuller
    source, so prefer it."""
    both = TrackLyrics(synced=[(1.0, "alpha")], plain="alpha\nbeta\ngamma")
    vm = synced_vm(both)
    vm.begin_sync()
    assert vm.sync_session.lines == ["alpha", "beta", "gamma"]


def test_abandoning_a_resync_puts_the_existing_sync_back():
    vm = synced_vm()
    vm.position_changed(5.0)
    vm.begin_sync()
    vm.sync_session.stamp(2.0)
    assert vm.end_sync() is True
    assert vm.display().mode is Mode.SYNCED
    assert vm.timeline()[0] == USER_SYNC.synced  # untouched


def test_abandoning_a_resync_while_stopped_restores_synced_not_plain():
    vm = synced_vm()
    vm.begin_sync()
    vm.end_sync()
    vm.player_state_changed(PlaybackState.STOPPED)
    assert vm.display().mode is Mode.IDLE
    vm.player_state_changed(PlaybackState.PLAYING)
    assert vm.display().mode is Mode.SYNCED


def test_a_new_track_discards_a_resync_too():
    vm = synced_vm()
    vm.begin_sync()
    vm.track_changed(snapshot(track_id="trackB", title="Next"))
    assert vm.sync_session is None
    assert vm.display().mode is Mode.FETCHING


def test_reload_after_a_resync_shows_the_new_timings():
    vm = synced_vm()
    vm.begin_sync()
    for position in (2.0, 6.0, 11.0):
        vm.sync_session.stamp(position)
    assert vm.sync_session.is_complete is True
    lrc = vm.sync_session.to_lrc()
    vm.end_sync()
    assert vm.begin_reload("trackA") is True

    from sottovoce.lyrics_provider import parse_lrc

    vm.fetch_completed("trackA", TrackLyrics(synced=parse_lrc(lrc)))
    assert vm.display().mode is Mode.SYNCED
    assert vm.timeline()[0] != USER_SYNC.synced  # the redone timings


def test_romanisation_offered_when_resyncing_korean_stored_lines():
    vm = synced_vm(TrackLyrics(synced=[(1.0, "안녕하세요"), (5.0, "잘 가")]))
    vm.romanisation_enabled = True
    vm.begin_sync()
    assert vm.has_korean_lyrics is True
    assert vm.display().pronunciation == "annyeonghaseyo"


# -- the header, and what the title card is allowed to hold up -------------


def test_the_header_separates_song_from_artist_with_a_middle_dot():
    """One definition, and it is a separator rather than punctuation. The
    format used to be written out twice — a window and a terminal tool with
    two copies of one format string is two things that can disagree about
    the same song."""
    from sottovoce.view_model import HEADER_SEPARATOR, header_text

    vm = LyricsViewModel()
    vm.track_changed(snapshot(title="Spring Day", artist="BTS"))

    assert HEADER_SEPARATOR == " · "
    assert vm.display().header == "Spring Day · BTS"
    assert header_text(snapshot(title="Spring Day", artist="BTS")) == "Spring Day · BTS"
    assert "—" not in vm.display().header


def test_the_card_holds_while_there_is_nothing_to_show():
    """FETCHING is the gap the card exists to fill."""
    from sottovoce.view_model import card_yields

    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    assert vm.display().mode is Mode.FETCHING
    assert card_yields(vm.display()) is False


def test_the_card_gives_way_the_moment_lyrics_can_be_shown():
    from sottovoce.view_model import card_yields

    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)
    vm.position_changed(15.0)  # past the first timestamp: a line to show

    assert card_yields(vm.display()) is True


def test_plain_lyrics_end_the_card_too():
    from sottovoce.view_model import card_yields

    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", PLAIN)

    assert card_yields(vm.display()) is True


def test_a_synced_song_joined_before_its_first_line_keeps_the_card():
    """The rule is "something to show", not "the fetch finished". Ending
    the card here would trade two seconds of the song's name for ten
    seconds of an empty window."""
    from sottovoce.view_model import card_yields

    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", SYNCED)
    vm.position_changed(2.0)  # first line is at 10.0

    assert vm.display().mode is Mode.SYNCED
    assert vm.display().current == ""
    assert card_yields(vm.display()) is False


def test_a_song_with_no_lyrics_says_so_rather_than_waiting_out_the_card():
    from sottovoce.view_model import card_yields

    vm = LyricsViewModel()
    vm.track_changed(snapshot())
    vm.fetch_completed("trackA", None)

    assert card_yields(vm.display()) is True
