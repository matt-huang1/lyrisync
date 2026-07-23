import pytest

from lyrisync import player_monitor as pm


class FakeOsascript:
    """Maps AppleScript expressions to canned responses; a response that is
    an exception instance gets raised instead."""

    def __init__(self, responses):
        self.responses = dict(responses)

    def __call__(self, expression):
        value = self.responses[expression]
        if isinstance(value, Exception):
            raise value
        return value


def playing_responses(
    track_id="4uLU6hMCjMI75M1A2tKUQC",
    title="Song",
    artist="Artist",
    album="Album",
    state="playing",
    duration="225000",
    position="42.5",
):
    return {
        pm._SCRIPT_IS_RUNNING: "true",
        pm._SCRIPT_PLAYER_STATE: state,
        pm._SCRIPT_TRACK_URL: f"spotify:track:{track_id}",
        pm._SCRIPT_TRACK_NAME: title,
        pm._SCRIPT_TRACK_ARTIST: artist,
        pm._SCRIPT_TRACK_ALBUM: album,
        pm._SCRIPT_TRACK_DURATION: duration,
        pm._SCRIPT_PLAYER_POSITION: position,
    }


def use_responses(monkeypatch, responses):
    fake = FakeOsascript(responses)
    monkeypatch.setattr(pm, "_osascript", fake)
    return fake


def test_snapshot_not_running(monkeypatch):
    use_responses(monkeypatch, {pm._SCRIPT_IS_RUNNING: "false"})
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert not snapshot.has_track


def test_snapshot_playing_full_fields(monkeypatch):
    use_responses(monkeypatch, playing_responses())
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert snapshot.track_id == "4uLU6hMCjMI75M1A2tKUQC"
    assert snapshot.title == "Song"
    assert snapshot.artist == "Artist"
    assert snapshot.album == "Album"
    assert snapshot.duration_ms == 225000
    assert snapshot.position_seconds == pytest.approx(42.5)


def test_snapshot_no_track_loaded(monkeypatch):
    responses = playing_responses(state="stopped")
    responses[pm._SCRIPT_TRACK_URL] = pm.SpotifyQueryError("no current track")
    use_responses(monkeypatch, responses)
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.STOPPED
    assert not snapshot.has_track
    assert snapshot.title is None
    assert snapshot.position_seconds is None


def test_locale_comma_position(monkeypatch):
    use_responses(monkeypatch, playing_responses(position="42,5"))
    assert pm.read_snapshot().position_seconds == pytest.approx(42.5)


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
    use_responses(monkeypatch, playing_responses())
    recorder = Recorder()
    make_monitor(recorder).poll_once()
    assert recorder.names() == ["state", "track", "position"]


def test_steady_state_only_fires_position(monkeypatch):
    fake = use_responses(monkeypatch, playing_responses())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.responses[pm._SCRIPT_PLAYER_POSITION] = "43.1"
    monitor.poll_once()
    assert recorder.names() == ["position"]


def test_track_change_fires_callback(monkeypatch):
    fake = use_responses(monkeypatch, playing_responses())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.responses.update(playing_responses(track_id="other999", title="Next Song"))
    monitor.poll_once()
    assert recorder.names() == ["track", "position"]
    assert recorder.events[0][1].track_id == "other999"


def test_pause_fires_state_change(monkeypatch):
    fake = use_responses(monkeypatch, playing_responses())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.responses[pm._SCRIPT_PLAYER_STATE] = "paused"
    monitor.poll_once()
    assert recorder.names() == ["state", "position"]
    assert recorder.events[0][1].state is pm.PlaybackState.PAUSED


def test_quit_spotify_fires_state_and_track_change(monkeypatch):
    fake = use_responses(monkeypatch, playing_responses())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.responses = {pm._SCRIPT_IS_RUNNING: "false"}
    snapshot = monitor.poll_once()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert recorder.names() == ["state", "track"]


def test_transient_query_failure_keeps_state(monkeypatch):
    fake = use_responses(monkeypatch, playing_responses())
    recorder = Recorder()
    monitor = make_monitor(recorder)
    monitor.poll_once()
    recorder.events.clear()

    fake.responses[pm._SCRIPT_IS_RUNNING] = pm.SpotifyQueryError("osascript timed out")
    assert monitor.poll_once() is None
    assert recorder.names() == []

    # Recovery: same track again, only position fires (no spurious changes).
    fake.responses[pm._SCRIPT_IS_RUNNING] = "true"
    monitor.poll_once()
    assert recorder.names() == ["position"]
