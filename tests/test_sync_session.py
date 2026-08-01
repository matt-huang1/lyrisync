TIER = "unit"  # Qt-free logic, called directly

import pytest

from sottovoce.sync_session import (
    MAX_EXTRAPOLATION_SECONDS,
    SYNC_REACTION_OFFSET_SECONDS,
    SyncSession,
    format_timestamp,
    interpolated_position,
    sync_targets,
    sync_targets_from_lines,
    targets_from_paste,
)


PLAIN = "First line\n\nSecond line\n   \nThird line\n"


def session(lines=("one", "two", "three"), offset=0.0):
    return SyncSession(list(lines), offset=offset)


# -- tap targets ------------------------------------------------------------


def test_sync_targets_drops_blank_lines():
    assert sync_targets(PLAIN) == ["First line", "Second line", "Third line"]


def test_sync_targets_strips_surrounding_whitespace():
    assert sync_targets("  padded  \n\tindented") == ["padded", "indented"]


def test_sync_targets_of_empty_text_is_empty():
    assert sync_targets("") == []
    assert sync_targets("\n\n  \n") == []


def test_sync_targets_from_lines_drops_the_same_blanks():
    """A re-sync takes its targets from stored lines rather than a block of
    text; instrumental gaps must fall out the same way."""
    assert sync_targets_from_lines(["one", "", " ", "two"]) == ["one", "two"]
    assert sync_targets_from_lines(iter([" padded "])) == ["padded"]
    assert sync_targets_from_lines([]) == []


# -- lyrics somebody pasted in ---------------------------------------------


def test_pasted_lyrics_are_tap_targets_like_any_others():
    """The ordinary case, and it must stay ordinary: what a person pastes
    is words, and words become targets by the same rule plain lyrics do."""
    assert targets_from_paste(PLAIN) == ["First line", "Second line", "Third line"]
    assert targets_from_paste("") == []


def test_a_pasted_lrc_file_loses_its_timestamps_and_keeps_its_words():
    """What people have to hand is frequently a .lrc from somewhere. Left
    alone, every line of the pass would read "[00:12.34] words" and the
    sync saved afterwards would carry two timestamps in front of one line.
    """
    assert targets_from_paste(
        "[00:12.34] first line\n[01:05.00]second line\n"
    ) == ["first line", "second line"]


def test_several_stamps_on_one_line_all_go():
    """A chorus in an LRC file carries a stamp per repeat."""
    assert targets_from_paste("[00:12.00][00:55.30] chorus\n") == ["chorus"]


def test_metadata_tags_are_not_lines_anybody_sings():
    """A pass that made the user tap through four of these before the
    first lyric is one they would abandon."""
    pasted = "[ar:Someone]\n[ti:A Song]\n[by:whoever]\n[00:01.00] the words\n"
    assert targets_from_paste(pasted) == ["the words"]


def test_a_bracket_inside_a_line_is_left_alone():
    """Only a stamp at the START of a line is structure. A bracket in the
    middle of a lyric is somebody's punctuation, and guessing further
    about a person's own words is how a sync loses the line they were
    waiting for."""
    assert targets_from_paste("she said [pause] nothing\n") == [
        "she said [pause] nothing"
    ]
    assert targets_from_paste("(chorus) again\n") == ["(chorus) again"]


# -- stamp / undo / complete ------------------------------------------------


def test_fresh_session_starts_at_the_first_line():
    s = session()
    assert s.index == 0
    assert s.total == 3
    assert s.current == "one"
    assert s.stamps == []
    assert s.is_complete is False


def test_stamping_advances_through_the_lines():
    s = session()
    assert s.stamp(1.0) is True
    assert s.index == 1
    assert s.current == "two"
    assert s.stamp(2.0) is True
    assert s.stamp(3.0) is True
    assert s.is_complete is True
    assert s.current == ""
    assert s.stamps == [1.0, 2.0, 3.0]


def test_stamping_past_the_end_does_nothing():
    s = session(("only",))
    s.stamp(5.0)
    assert s.stamp(9.0) is False
    assert s.stamps == [5.0]


def test_undo_steps_back_and_drops_the_stamp():
    s = session()
    s.stamp(1.0)
    s.stamp(2.0)
    assert s.undo() is True
    assert s.index == 1
    assert s.current == "two"
    assert s.stamps == [1.0]


def test_undo_at_the_start_is_a_no_op():
    s = session()
    assert s.undo() is False
    assert s.index == 0


def test_undo_after_completion_reopens_the_session():
    s = session(("only",))
    s.stamp(5.0)
    assert s.is_complete is True
    s.undo()
    assert s.is_complete is False
    assert s.current == "only"


def test_restamping_after_undo_replaces_the_mistake():
    s = session()
    s.stamp(1.0)
    s.stamp(99.0)  # tapped far too late
    s.undo()
    s.stamp(2.0)
    assert s.stamps == [1.0, 2.0]


def test_an_empty_session_is_never_complete():
    s = session(())
    assert s.is_complete is False
    assert s.stamp(1.0) is False
    assert s.current == ""


def test_previous_is_the_line_just_stamped():
    s = session()
    assert s.previous == ""  # nothing tapped yet
    s.stamp(1.0)
    assert s.previous == "one"
    s.stamp(2.0)
    assert s.previous == "two"


def test_previous_follows_undo_back_up_the_list():
    s = session()
    s.stamp(1.0)
    s.stamp(2.0)
    s.undo()
    assert s.previous == "one"
    s.undo()
    assert s.previous == ""


def test_previous_after_the_last_stamp_is_the_last_line():
    s = session()
    for position in (1.0, 2.0, 3.0):
        s.stamp(position)
    assert s.is_complete is True
    assert s.previous == "three"
    assert s.current == ""


def test_upcoming_shows_the_next_lines_and_runs_out_at_the_end():
    s = session()
    assert s.upcoming(2) == ["two", "three"]
    s.stamp(1.0)
    assert s.upcoming(2) == ["three"]
    s.stamp(2.0)
    assert s.upcoming(2) == []


# -- reaction offset --------------------------------------------------------


def test_offset_is_subtracted_from_every_stamp():
    s = SyncSession(["a", "b"])
    s.stamp(10.0)
    s.stamp(20.0)
    assert s.stamps == pytest.approx(
        [10.0 - SYNC_REACTION_OFFSET_SECONDS, 20.0 - SYNC_REACTION_OFFSET_SECONDS]
    )


def test_offset_never_pushes_a_stamp_below_zero():
    s = SyncSession(["a"], offset=0.25)
    s.stamp(0.1)
    assert s.stamps == [0.0]


def test_offset_never_pushes_a_stamp_behind_its_predecessor():
    # Two taps closer together than the offset: the second would land
    # before the first once corrected.
    s = SyncSession(["a", "b"], offset=0.25)
    s.stamp(10.0)
    s.stamp(10.1)
    assert s.stamps == pytest.approx([9.75, 9.85])
    s2 = SyncSession(["a", "b"], offset=0.25)
    s2.stamp(10.0)
    s2.stamp(10.0)  # same instant: clamped, never earlier
    assert s2.stamps == pytest.approx([9.75, 9.75])


def test_a_backwards_tap_is_clamped_not_recorded():
    """A seek backwards mid-pass must not write a decreasing timeline."""
    s = SyncSession(["a", "b", "c"], offset=0.0)
    s.stamp(30.0)
    s.stamp(5.0)
    s.stamp(40.0)
    assert s.stamps == [30.0, 30.0, 40.0]
    assert s.stamps == sorted(s.stamps)


# -- interpolation ----------------------------------------------------------


def test_interpolation_advances_by_the_time_since_the_poll():
    assert interpolated_position(12.0, polled_at=100.0, now=100.2) == pytest.approx(12.2)


def test_interpolation_at_the_moment_of_the_poll_is_the_polled_value():
    assert interpolated_position(12.0, polled_at=100.0, now=100.0) == 12.0


def test_interpolation_does_not_advance_while_paused():
    assert (
        interpolated_position(12.0, polled_at=100.0, now=105.0, playing=False) == 12.0
    )


def test_interpolation_ignores_a_clock_going_backwards():
    assert interpolated_position(12.0, polled_at=100.0, now=99.0) == 12.0


def test_interpolation_caps_extrapolation_from_a_stalled_poll():
    """A wedged poll loop must not invent a position minutes ahead."""
    result = interpolated_position(12.0, polled_at=100.0, now=160.0)
    assert result == pytest.approx(12.0 + MAX_EXTRAPOLATION_SECONDS)


def test_interpolation_without_a_position_has_nothing_to_offer():
    assert interpolated_position(None, polled_at=100.0, now=100.2) is None


def test_interpolation_without_a_poll_time_falls_back_to_the_raw_position():
    assert interpolated_position(12.0, polled_at=None, now=100.2) == 12.0


# -- LRC serialisation ------------------------------------------------------


def test_format_timestamp_shape():
    assert format_timestamp(0.0) == "[00:00.00]"
    assert format_timestamp(12.34) == "[00:12.34]"
    assert format_timestamp(65.5) == "[01:05.50]"
    assert format_timestamp(3600.0) == "[60:00.00]"


def test_format_timestamp_carries_rounding_into_the_next_minute():
    assert format_timestamp(59.999) == "[01:00.00]"


def test_format_timestamp_never_renders_a_negative():
    assert format_timestamp(-1.0) == "[00:00.00]"


def test_to_lrc_serialises_stamped_lines_in_order():
    s = SyncSession(["First line", "Second line"], offset=0.0)
    s.stamp(12.0)
    s.stamp(17.5)
    assert s.to_lrc() == "[00:12.00] First line\n[00:17.50] Second line\n"


def test_to_lrc_only_covers_what_has_been_stamped():
    s = SyncSession(["a", "b", "c"], offset=0.0)
    s.stamp(1.0)
    assert s.to_lrc() == "[00:01.00] a\n"


def test_to_lrc_round_trips_through_the_provider_parser():
    from sottovoce.lyrics_provider import parse_lrc

    s = SyncSession(["First", "Second", "Third"], offset=0.0)
    for position in (3.5, 61.25, 129.0):
        s.stamp(position)
    assert parse_lrc(s.to_lrc()) == [
        (3.5, "First"),
        (61.25, "Second"),
        (129.0, "Third"),
    ]
