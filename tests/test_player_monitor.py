import time

import pytest

from sottovoce import player_monitor as pm


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
    """Stands in for _osascript.

    Answers the batched snapshot script with ``.output`` (raising it if it
    is an exception), and the dictionary-free running probe with
    ``.running`` — which defaults to "yes", so a snapshot failure means
    what it always meant here: a transient one, on a Mac that has Spotify.
    Anything else is a script this module did not mean to run.
    """

    def __init__(self, output, running=True):
        self.output = output
        self.running = running
        self.calls = 0
        self.probes = 0

    def __call__(self, script):
        if script == pm._RUNNING_SCRIPT:
            self.probes += 1
            return "running" if self.running else "not_running"
        assert script == pm._SNAPSHOT_SCRIPT
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def use_output(monkeypatch, output, running=True):
    fake = FakeOsascript(output, running=running)
    monkeypatch.setattr(pm, "_osascript", fake)
    return fake


@pytest.fixture(autouse=True)
def _forget_whether_spotify_exists(monkeypatch):
    """``read_snapshot`` remembers a Mac with no Spotify on it, so that it
    stops paying for a compile that cannot succeed. Module state, and
    therefore state one test can leave lying around for the next."""
    monkeypatch.setattr(pm, "_no_spotify_installed", False)


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


# -- poll freshness (tap-to-sync interpolates from this) -----------------


def test_snapshot_records_when_the_query_answered(monkeypatch):
    import time

    use_output(monkeypatch, batched_output())
    before = time.monotonic()
    snapshot = pm.read_snapshot()
    after = time.monotonic()
    assert before <= snapshot.polled_at <= after


def test_poll_time_is_stamped_after_the_query_not_before(monkeypatch):
    """The position is only as fresh as the moment the answer came back.
    Stamping before a slow osascript would claim it was newer than it is,
    and every interpolated tap would land early."""
    ticks = iter([100.0, 101.0, 102.0])
    monkeypatch.setattr(pm.time, "monotonic", lambda: next(ticks))

    def slow(script):
        next(ticks)  # a second of wall-clock burned inside the query
        return batched_output()

    monkeypatch.setattr(pm, "_osascript", slow)
    assert pm.read_snapshot().polled_at == 101.0  # not 100.0


@pytest.mark.parametrize(
    "output",
    ["not_running", "playing", batched_output(position="nonsense")],
)
def test_every_snapshot_shape_carries_a_poll_time(monkeypatch, output):
    use_output(monkeypatch, output)
    assert pm.read_snapshot().polled_at is not None


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


# -- starting and stopping the poll loop ---------------------------------


def test_a_stop_before_run_is_not_erased(monkeypatch):
    """The race that made shutdown flaky.

    ``stop()`` used to clear a flag that ``run()`` raised again on entry,
    so a stop arriving in the gap between starting the thread and the
    thread body beginning was lost and the loop polled on forever. In the
    app that is "monitor thread did not stop in time", and one bounded
    wait later, a QThread destroyed while still running — a qFatal that
    takes the process with it.
    """
    fake = use_output(monkeypatch, batched_output())
    monitor = pm.PlayerMonitor(poll_interval=0.01)

    monitor.stop()
    monitor.run()  # must return, not poll

    assert fake.calls == 0


def test_run_stops_when_asked(monkeypatch):
    """And stops promptly: the remaining poll interval is waited on rather
    than slept through, so quit does not first sit out a poll."""
    use_output(monkeypatch, batched_output())
    monitor = pm.PlayerMonitor(poll_interval=30.0)

    def stop_after_first_poll(snapshot):
        monitor.stop()

    monitor.on_position_update = stop_after_first_poll

    started = time.monotonic()
    monitor.run()
    assert time.monotonic() - started < 5.0  # not the 30s interval


# -- album artwork --------------------------------------------------------


def test_the_artwork_url_comes_back_with_the_track(monkeypatch):
    use_output(monkeypatch, batched_output() + "\nhttps://i.scdn.co/image/abc")
    snapshot = pm.read_snapshot()
    assert snapshot.artwork_url == "https://i.scdn.co/image/abc"
    assert snapshot.title == "Song"  # the rest still parsed


def test_a_track_without_artwork_still_parses(monkeypatch):
    """The artwork line sits in a try of its own inside the script, so a
    Spotify build that will not answer `artwork url` costs one field
    rather than the whole track."""
    use_output(monkeypatch, batched_output())
    snapshot = pm.read_snapshot()
    assert snapshot.artwork_url is None
    assert snapshot.title == "Song"
    assert snapshot.track_id == "4uLU6hMCjMI75M1A2tKUQC"
    assert snapshot.position_seconds == 42.5


def test_an_empty_artwork_line_is_no_artwork(monkeypatch):
    use_output(monkeypatch, batched_output() + "\n   ")
    assert pm.read_snapshot().artwork_url is None


def test_the_script_keeps_artwork_in_a_try_of_its_own():
    """Structural, because the failure it prevents is invisible in a
    passing suite: appended to the same statement, a build that does not
    answer `artwork url` would fail the whole expression and the app would
    show a running player that never finds a song."""
    body = pm._SNAPSHOT_SCRIPT

    def open_trys(text):
        # "end try" contains "try", so opens are the difference.
        return text.count("try") - 2 * text.count("end try")

    assert open_trys(body) == 0, "the script's trys are unbalanced"
    before_artwork = body[: body.index("artwork url")]
    assert open_trys(before_artwork) == 2, "artwork is not nested inside both trys"


def test_artwork_is_not_part_of_track_identity():
    """A cover arriving a poll after the metadata must not read as a
    different song and restart the lyrics lookup."""
    without = pm.PlayerSnapshot(state=pm.PlaybackState.PLAYING, track_id="t1")
    with_art = pm.PlayerSnapshot(
        state=pm.PlaybackState.PLAYING, track_id="t1", artwork_url="http://cover"
    )
    assert without.track_key == with_art.track_key


# -- a Mac with no Spotify on it -------------------------------------------
#
# Never tested, and it turned out not to work the way the script implies.
# `if application "Spotify" is not running then return "not_running"` is the
# first line and reads like the answer, but everything below it is
# Spotify's OWN terminology and AppleScript resolves terminology at COMPILE
# time out of the application bundle. With no bundle there is nothing to
# resolve, so the script never runs at all: it fails to compile, with a
# syntax error, and the first line is never reached.
#
# Measured by asking with an application name that is not installed:
#   141:146: syntax error: Expected “,” but found identifier. (-2741)
#
# What that produced was an app that reported NOTHING — poll_once swallowed
# the error, no state callback ever fired, and osascript was spawned three
# times a second forever for a script that could not run.


COMPILE_ERROR = pm.SpotifyQueryError(
    "141:146: syntax error: Expected “,” but found identifier. (-2741)"
)


def test_no_spotify_installed_reports_not_running(monkeypatch):
    """The whole point. "Not running" is the truth on a Mac with no
    Spotify, and it is what the monitor is supposed to report."""
    fake = use_output(monkeypatch, COMPILE_ERROR, running=False)
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.NOT_RUNNING
    assert not snapshot.has_track
    assert snapshot.polled_at is not None


def test_the_window_sits_in_its_idle_state(monkeypatch):
    """What the display does with that, end to end through the pure view
    model: the idle line, no header, no fetch, no error."""
    from sottovoce.view_model import LyricsViewModel, Mode

    use_output(monkeypatch, COMPILE_ERROR, running=False)
    model = LyricsViewModel()
    snapshot = pm.read_snapshot()
    assert model.track_changed(snapshot) is False  # nothing to look up
    model.player_state_changed(snapshot.state)
    display = model.display()
    assert display.mode is Mode.IDLE
    assert display.current == "Spotify is not playing"
    assert display.header == ""
    assert display.detail == ""


def test_the_monitor_fires_the_state_callback(monkeypatch):
    """It used to fire nothing at all: poll_once returned None before any
    callback, so a Mac with no Spotify was indistinguishable from a Mac
    where the app had not started polling yet."""
    use_output(monkeypatch, COMPILE_ERROR, running=False)
    states, tracks = [], []
    monitor = pm.PlayerMonitor(
        on_state_change=lambda s: states.append(s.state),
        on_track_change=lambda s: tracks.append(s.track_key),
    )
    assert monitor.poll_once() is not None
    assert states == [pm.PlaybackState.NOT_RUNNING]
    assert tracks == [None]


def test_it_stops_paying_for_a_compile_that_cannot_succeed(monkeypatch):
    """Measured: the snapshot script costs 184ms where Spotify exists, and
    the dictionary-free probe costs 37ms there and 182ms where the
    application is absent. Asking both, three times a second, forever, is
    what the first version of this did."""
    fake = use_output(monkeypatch, COMPILE_ERROR, running=False)
    for _ in range(5):
        assert pm.read_snapshot().state is pm.PlaybackState.NOT_RUNNING
    assert fake.calls == 1, "kept trying to compile the snapshot script"
    assert fake.probes == 5


def test_spotify_arriving_later_is_noticed(monkeypatch):
    """The probe that keeps answering is also the one that watches for
    Spotify being installed, so this never needs a restart."""
    fake = use_output(monkeypatch, COMPILE_ERROR, running=False)
    assert pm.read_snapshot().state is pm.PlaybackState.NOT_RUNNING

    fake.running = True
    fake.output = batched_output()
    snapshot = pm.read_snapshot()
    assert snapshot.state is pm.PlaybackState.PLAYING
    assert snapshot.title == "Song"


def test_a_transient_failure_on_a_running_spotify_is_still_a_failure(monkeypatch):
    """The distinction the probe exists to make. An osascript that timed
    out while Spotify is right there is not "Spotify is not installed" —
    it is the transient failure the poll loop has always kept state
    across."""
    fake = use_output(monkeypatch, pm.SpotifyQueryError("timed out"), running=True)
    with pytest.raises(pm.SpotifyQueryError):
        pm.read_snapshot()
    assert fake.probes == 1
    # And nothing was remembered: the next poll asks properly again.
    fake.output = batched_output()
    assert pm.read_snapshot().state is pm.PlaybackState.PLAYING


def test_the_probe_needs_no_dictionary():
    """The property the whole fix rests on, asserted on the script itself:
    it may use nothing but AppleScript's own generic application class.
    Every term Spotify supplies — `player state`, `current track`,
    `spotify url` — is a term that cannot be compiled without Spotify."""
    assert "running" in pm._RUNNING_SCRIPT
    for spotify_term in (
        "player state",
        "current track",
        "spotify url",
        "player position",
        "artwork url",
    ):
        assert spotify_term not in pm._RUNNING_SCRIPT
    # And the snapshot script is exactly the one that cannot: this is what
    # makes the two separate scripts rather than one.
    assert "player state" in pm._SNAPSHOT_SCRIPT


def test_the_probe_answers_a_yes_or_no(monkeypatch):
    use_output(monkeypatch, "", running=True)
    assert pm.spotify_running() is True
    use_output(monkeypatch, "", running=False)
    assert pm.spotify_running() is False
