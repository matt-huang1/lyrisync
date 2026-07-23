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
):
    """What the single osascript call prints for a loaded track."""
    return "\n".join(
        [state, f"spotify:track:{track_id}", title, artist, album, duration, position]
    )


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


def test_quit_spotify_fires_state_and_track_change(monkeypatch):
    fake = use_output(monkeypatch, batched_output())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.output = "not_running"
    snapshot = monitor.poll_once()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert recorder.names() == ["state", "track"]


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
