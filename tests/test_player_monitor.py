import pytest

from lyrisync import player_monitor as pm


def batched_output(
    state="playing",
    track_id="4uLU6hMCjMI75M1A2tKUQC",
    title="Song",
    artist="Artist",
    album="Album",
    duration="225000",
    position="42.5",
    uri=None,
):
    """What the single osascript call prints for a loaded track."""
    url = uri if uri is not None else f"spotify:track:{track_id}"
    return "\n".join([state, url, title, artist, album, duration, position])


class FakeOsascript:
    """Stands in for _osascript: returns .output, or raises it if it is an
    exception. Asserts the batched snapshot script is what gets run."""

    def __init__(self, output):
        self.output = output
        self.calls = 0

    def __call__(self, script):
        assert script == pm._SNAPSHOT_SCRIPT
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def use_output(monkeypatch, output):
    fake = FakeOsascript(output)
    monkeypatch.setattr(pm, "_osascript", fake)
    return fake


# -- snapshot parsing ----------------------------------------------------


def test_snapshot_not_running(monkeypatch):
    use_output(monkeypatch, "not_running")
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert not snapshot.has_track


def test_snapshot_playing_full_fields(monkeypatch):
    use_output(monkeypatch, batched_output())
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert snapshot.track_id == "4uLU6hMCjMI75M1A2tKUQC"
    assert snapshot.title == "Song"
    assert snapshot.artist == "Artist"
    assert snapshot.album == "Album"
    assert snapshot.duration_ms == 225000
    assert snapshot.position_seconds == pytest.approx(42.5)


def test_snapshot_single_call_per_poll(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    pm.read_snapshot()
    assert fake.calls == 1


def test_snapshot_no_track_loaded(monkeypatch):
    # The AppleScript try block leaves only the state line when track
    # fields error (fresh launch, nothing loaded).
    use_output(monkeypatch, "stopped")
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.STOPPED
    assert not snapshot.has_track
    assert snapshot.title is None
    assert snapshot.position_seconds is None


def test_snapshot_unexpected_line_count_degrades_to_stateless(monkeypatch):
    use_output(monkeypatch, "playing\nspotify:track:abc\nTitle")
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert not snapshot.has_track


def test_snapshot_bad_number_degrades(monkeypatch):
    use_output(monkeypatch, batched_output(duration="garbage"))
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert not snapshot.has_track


def test_locale_comma_numbers(monkeypatch):
    use_output(monkeypatch, batched_output(position="42,5", duration="225000"))
    snapshot = pm.read_snapshot()
    assert snapshot.position_seconds == pytest.approx(42.5)
    assert snapshot.duration_ms == 225000


def test_empty_output_raises(monkeypatch):
    use_output(monkeypatch, "")
    with pytest.raises(pm.SpotifyQueryError):
        pm.read_snapshot()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("spotify:track:abc123", "abc123"),
        ("https://open.spotify.com/track/abc123?si=xyz", "abc123"),
        ("https://open.spotify.com/track/abc123", "abc123"),
        ("", None),
    ],
)
def test_parse_track_id(url, expected):
    assert pm._parse_track_id(url) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("playing", pm.PlaybackState.PLAYING),
        ("Paused", pm.PlaybackState.PAUSED),
        ("«constant ****kPSp»", pm.PlaybackState.PAUSED),
        ("«constant ****kPSP»", pm.PlaybackState.PLAYING),
        ("«constant ****kPSS»", pm.PlaybackState.STOPPED),
    ],
)
def test_parse_state(raw, expected):
    assert pm._parse_state(raw) is expected


def test_parse_state_unrecognized():
    with pytest.raises(pm.SpotifyQueryError):
        pm._parse_state("garbage")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("spotify:track:abc123", "track"),
        ("spotify:media:abc123", "media"),
        ("spotify:ad:abc123", "ad"),
        ("https://open.spotify.com/track/abc123?si=x", "track"),
        ("", "track"),
    ],
)
def test_parse_track_kind(url, expected):
    assert pm._parse_track_kind(url) == expected


def test_dj_narration_snapshot_is_not_music(monkeypatch):
    # Spotify's DJ reports the upcoming song's ID under spotify:media:.
    use_output(
        monkeypatch,
        batched_output(
            uri="spotify:media:61uyGDPJ06MkxJtHgPmuyO",
            title="Up next", artist="DJ X", album="DJ", duration="0", position="1.0",
        ),
    )
    snapshot = pm.read_snapshot()
    assert snapshot.track_id == "61uyGDPJ06MkxJtHgPmuyO"
    assert snapshot.track_kind == "media"
    assert snapshot.has_track
    assert not snapshot.is_music_track


# -- monitor callbacks ---------------------------------------------------


class Recorder:
    def __init__(self):
        self.events = []

    def hook(self, name):
        return lambda snapshot: self.events.append((name, snapshot))

    def names(self):
        return [name for name, _ in self.events]


def make_monitor(recorder):
    return pm.PlayerMonitor(
        on_state_change=recorder.hook("state"),
        on_track_change=recorder.hook("track"),
        on_position_update=recorder.hook("position"),
    )


def test_first_poll_fires_initial_events(monkeypatch):
    use_output(monkeypatch, batched_output())
    recorder = Recorder()
    make_monitor(recorder).poll_once()
    assert recorder.names() == ["state", "track", "position"]


def test_steady_state_only_fires_position(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(position="43.1")
    monitor.poll_once()
    assert recorder.names() == ["position"]


def test_track_change_fires_callback(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(track_id="other999", title="Next Song")
    monitor.poll_once()
    assert recorder.names() == ["track", "position"]
    assert recorder.events[0][1].track_id == "other999"


def test_dj_to_song_transition_fires_track_change(monkeypatch):
    # Same ID, different URI scheme: must register as a track change,
    # or the app stays stuck on the DJ state for the whole song.
    fake = use_output(
        monkeypatch,
        batched_output(
            uri="spotify:media:61uyGDPJ06MkxJtHgPmuyO",
            title="Up next", artist="DJ X", album="DJ", duration="0",
        ),
    )
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(
        uri="spotify:track:61uyGDPJ06MkxJtHgPmuyO",
        title="Company", artist="Justin Bieber", album="Purpose (Deluxe)",
        duration="198195",
    )
    monitor.poll_once()
    assert "track" in recorder.names()
    changed = dict(recorder.events)["track"]
    assert changed.title == "Company"
    assert changed.is_music_track


def test_pause_fires_state_change(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = batched_output(state="paused")
    monitor.poll_once()
    assert recorder.names() == ["state", "position"]
    assert recorder.events[0][1].state is pm.PlaybackState.PAUSED


def test_quit_spotify_fires_state_then_track_change(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "not_running"
    # First trackless poll is debounced (could be a one-poll blip): only
    # the state change fires, track metadata is retained.
    snapshot = monitor.poll_once()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert snapshot.has_track
    assert recorder.names() == ["state"]

    # Second consecutive trackless poll confirms the loss.
    snapshot = monitor.poll_once()
    assert not snapshot.has_track
    assert recorder.names() == ["state", "track"]


def test_single_trackless_blip_is_debounced(monkeypatch):
    # Mid item-switch AppleScript can report no track for one poll; that
    # must not fire a track change to nothing and back.
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "playing"  # state-only: no track fields this poll
    monitor.poll_once()
    fake.output = batched_output()  # track is back, unchanged
    monitor.poll_once()
    assert "track" not in recorder.names()


def test_state_change_during_blip_still_fires(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "paused"  # trackless blip AND a state change
    snapshot = monitor.poll_once()
    assert recorder.names() == ["state"]
    assert snapshot.state is pm.PlaybackState.PAUSED
    assert snapshot.has_track  # metadata retained through the blip


def test_blip_then_new_track_fires_change(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "playing"
    monitor.poll_once()
    fake.output = batched_output(track_id="next999", title="Next")
    monitor.poll_once()
    assert "track" in recorder.names()
    assert dict(recorder.events)["track"].track_id == "next999"


def test_transient_query_failure_keeps_state(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = pm.SpotifyQueryError("osascript timed out")
    assert monitor.poll_once() is None
    assert recorder.names() == []

    # Recovery: same track again, only position fires (no spurious changes).
    fake.output = batched_output()
    monitor.poll_once()
    assert recorder.names() == ["position"]
