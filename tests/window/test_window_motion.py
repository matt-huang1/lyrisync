"""The line change: the fade, the rise, and what it costs to draw.

Vertical motion is one signed property on a QGraphicsEffect, so it is
verified by tracing draw() rather than by grabbing the widget. The
choreography lands on the timestamp, plays once per target index, and
rasterises the line once per phase rather than once per frame.
"""

TIER = "qt"  # a real window, driven by calling its own methods

import pytest

from PySide6.QtCore import QEasingCurve, QRect, QRectF

from sottovoce import window as w
from sottovoce.lyrics_provider import TrackLyrics
from sottovoce.player_monitor import PlaybackState

from helpers import (
    APP,
    PLAIN,
    SYNCED,
    load,
    panel_pixels,
    pixels_of,
    shown,
    snapshot,
)


# -- the line change: fade and rise ---------------------------------------


def synced_window(make_window):
    window = make_window()
    load(window, SYNCED)
    window._last_state = PlaybackState.PLAYING
    APP.processEvents()
    return window


def test_a_line_at_rest_is_opaque_and_on_its_mark(make_window):
    window = synced_window(make_window)
    assert window._current_fx.progress == 0.0


def test_the_outgoing_line_leaves_upward(make_window):
    """Upward, in the direction the song is going — a line that sank as it
    left would read as the song going backwards."""
    window = synced_window(make_window)
    window._begin_fade_out()
    assert window._fade_anim is not None
    assert window._fade_anim.endValue() == -1.0

    window._fade_anim.setCurrentTime(window._fade_anim.duration())
    APP.processEvents()
    assert window._current_fx.progress == -1.0


def test_the_incoming_line_rises_from_below_into_place(make_window):
    window = synced_window(make_window)
    window._render()
    window._predicted_swap()
    # Starts below and transparent...
    assert window._fade_anim is not None
    assert window._fade_anim.endValue() == 0.0

    window._fade_anim.setCurrentTime(0)
    APP.processEvents()
    assert window._current_fx.progress == pytest.approx(1.0, abs=0.05)

    window._fade_anim.setCurrentTime(window._fade_anim.duration())
    APP.processEvents()
    assert window._current_fx.progress == 0.0  # ...and lands exactly on its mark


def test_the_motion_ends_on_the_timestamp_rather_than_starting_on_it(make_window):
    """The anticipatory schedule stays authoritative. The rise finishes as
    the line becomes current, so it is never still moving while being read."""
    window = synced_window(make_window)
    window._render()
    window._predicted_swap()
    assert window._fade_anim.duration() == w._FADE_MS
    # The swap is scheduled a full fade before the timestamp, so
    # fade-in-completes-at-ts holds by construction.
    assert w._SWAP_LEAD_MS >= w._FADE_MS


def test_the_line_is_eased_not_linear(make_window):
    window = synced_window(make_window)
    window._begin_fade_out()
    out_curve = window._fade_anim.easingCurve().type()
    window._render()
    window._predicted_swap()
    in_curve = window._fade_anim.easingCurve().type()
    assert out_curve != QEasingCurve.Type.Linear
    assert in_curve != QEasingCurve.Type.Linear
    assert in_curve != out_curve  # departure accelerates, arrival settles


def test_travel_is_scale_aware(make_window):
    """A few pixels at default width, proportionally more when the window
    is dragged wider — the same scale everything else in the window
    follows. Widths stay inside the offscreen screen (800px), or the
    resize is clamped and the test proves nothing."""
    window = make_window()
    # Shown, because Qt defers resize events for hidden widgets and
    # _apply_scale would never run — the test would pass on a stale value.
    window.show()
    window.resize(300, 240)
    APP.processEvents()
    narrow = window._current_fx.travel

    window.resize(460, 240)
    APP.processEvents()
    default = window._current_fx.travel

    window.resize(760, 260)
    APP.processEvents()
    wide = window._current_fx.travel

    assert narrow < default < wide
    assert 3 <= default <= 10  # a few pixels, at the width the app opens at


@pytest.mark.parametrize(
    "disturbance",
    (
        "render",
        "seek",
        "pause",
        "loop",
        "sync",
        "track_change",
    ),
)
def test_nothing_leaves_a_line_mid_flight(make_window, disturbance):
    """A line parked off its mark, or fading, after the world moved is the
    failure mode of animating this at all. Every path back to a known
    state has to snap."""
    window = synced_window(make_window)
    window._begin_fade_out()
    window._fade_anim.setCurrentTime(window._fade_anim.duration() // 2)
    APP.processEvents()
    assert window._current_fx.progress != 0.0  # genuinely mid-flight

    if disturbance == "render":
        window._render()
    elif disturbance == "seek":
        window._on_position_update(snapshot())
        window._render()
    elif disturbance == "pause":
        window._on_state_change(snapshot(state=PlaybackState.PAUSED))
        window._render()
    elif disturbance == "loop":
        window._do_loop_wrap()
        window._render()
    elif disturbance == "sync":
        load(window, PLAIN, track_id="t2")
        window._begin_sync()
    elif disturbance == "track_change":
        window._on_track_change(snapshot(track_id="t9"))
    APP.processEvents()

    assert window._current_fx.progress == 0.0
    assert window._fade_anim is None or not window._fade_anim.state()


def test_the_choreography_is_two_equal_phases_before_the_timestamp():
    """One number, three constants derived from it, so the swap point and
    the total window cannot drift apart from the phase length."""
    assert w._SWAP_LEAD_MS == w._FADE_MS
    assert w._FADE_OUT_LEAD_MS == 2 * w._FADE_MS


def test_the_transition_is_unhurried_but_still_lands_on_time(make_window):
    """Extended EARLIER rather than finishing later: the arrival still
    ends on the timestamp, it just starts moving well before it."""
    window = synced_window(make_window)
    lines = [(0.0, "a"), (10.0, "b")]
    window._schedule_line_advance(lines, 0, 0.0)

    assert window._transition_ms == w._FADE_MS
    # swap one phase before the line, fade-out two phases before it
    assert window._swap_timer.interval() == 10000 - w._FADE_MS
    assert window._fadeout_timer.interval() == 10000 - 2 * w._FADE_MS


@pytest.mark.parametrize(
    "gap_ms,expected_phase",
    (
        (10000, 260),   # ordinary spacing: the full choreography
        (1040, 260),    # exactly twice the phase: still full
        (600, 260),     # the phases fit with room to spare
        (400, 200),     # too tight for the full movement: scaled down
        (120, 60),      # a rapid-fire line
        (20, 10),       # absurdly fast
    ),
)
def test_a_short_gap_gets_a_quicker_movement_not_a_truncated_one(
    make_window, gap_ms, expected_phase
):
    """Lines can arrive faster than the animation window — ad-libs, rapid
    call-and-response. Both phases still fit and the arrival still ends
    exactly on the timestamp; the movement is simply quicker."""
    window = synced_window(make_window)
    lines = [(0.0, "a"), (gap_ms / 1000, "b")]
    window._schedule_line_advance(lines, 0, 0.0)

    assert window._transition_ms == expected_phase
    swap_at = window._swap_timer.interval()
    # The arrival begins one phase before the line and lasts one phase,
    # so it settles ON it — the property that must hold at any tempo.
    assert swap_at + window._transition_ms == pytest.approx(gap_ms, abs=1)
    assert swap_at >= 0
    assert window._fadeout_timer.interval() >= 0


def test_a_shortened_transition_is_what_the_animation_actually_uses(make_window):
    """The clamp is worthless if the animation still runs at the nominal
    duration — it would overrun the line it belongs to."""
    window = synced_window(make_window)
    window._schedule_line_advance([(0.0, "a"), (0.4, "b")], 0, 0.0)
    assert window._transition_ms == 200

    window._begin_fade_out()
    assert window._fade_anim.duration() == 200


def test_the_easing_is_gentle_at_both_ends(make_window):
    """Sine, not cubic: cubic's ends are steep enough that even a 260ms
    phase reads as a flick."""
    window = synced_window(make_window)
    window._begin_fade_out()
    assert window._fade_anim.easingCurve().type() == QEasingCurve.Type.InSine
    window._render()
    window._predicted_swap()
    assert window._fade_anim.easingCurve().type() == QEasingCurve.Type.OutSine


def test_a_cancelled_schedule_leaves_no_timers_armed(make_window):
    window = synced_window(make_window)
    window._on_position_update(snapshot())
    window._cancel_line_schedule()
    assert not window._fadeout_timer.isActive()
    assert not window._swap_timer.isActive()
    assert window._current_fx.progress == 0.0


# -- one line change plays once -------------------------------------------


def expire(timer, fire):
    """What Qt does when a single-shot timer runs out: it stops, and then
    the slot runs. Driven by hand because the choreography is measured in
    hundreds of milliseconds and a test that waited them out would be slow
    and racy, while what is being checked here is purely the order events
    arrive in."""
    timer.stop()
    fire()


def record_lines(window):
    """Every index the window puts on screen, in order. A line change that
    plays twice shows up as [1, 0, 1] where it should read [1]."""
    shown = []
    original = window._set_lines

    def spy(lines, index):
        shown.append(index)
        original(lines, index)

    window._set_lines = spy
    return shown


def test_a_repeated_trigger_for_the_same_line_does_not_restart_it(make_window):
    """The identity dedupe, on the path a re-armed timer takes. A second
    fade-out for a line already leaving must not start the movement over
    from wherever it had got to."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=1.5))
    window._begin_fade_out()
    animation = window._fade_anim
    assert animation is not None
    animation.setCurrentTime(animation.duration() // 2)
    half = animation.currentTime()

    window._begin_fade_out()
    window._begin_fade_out()

    assert window._fade_anim is animation, "the animation was replaced"
    assert window._fade_anim.currentTime() == half, "the movement restarted"


def test_a_poll_landing_mid_change_does_not_play_it_again(make_window):
    """The bug. One line change is 520ms and a poll arrives every 300ms,
    so a poll lands inside almost every change. It used to re-arm the
    timers from what was left of the gap AND snap the display back to the
    line being left — so the same change played a second time, faster,
    right on top of itself."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=1.5))
    shown = record_lines(window)

    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)
    assert shown == [1]

    # Polls between the swap and the line's own timestamp, which is where
    # the poll interval puts them nearly every time.
    for position in (4.8, 4.9, 4.95):
        window._on_position_update(snapshot(position=position))

    assert shown == [1], "the line change played again"
    assert window._current.text() == "two"
    assert not window._fadeout_timer.isActive()
    assert not window._swap_timer.isActive()


def test_the_player_catching_up_is_not_a_second_change(make_window):
    """The position finally crosses the timestamp and the view model
    agrees with the screen. Nothing should move: the change already
    happened."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=1.5))
    shown = record_lines(window)
    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)
    window._fade_anim.setCurrentTime(window._fade_anim.duration())  # it lands
    APP.processEvents()

    window._on_position_update(snapshot(position=5.1))

    assert shown == [1]
    assert window._displayed_index == 1
    assert window._current_fx.progress == 0.0  # still on its mark, not moving


def test_the_line_after_it_is_still_scheduled_normally(make_window):
    """Dedupe by target, not a latch: the change to the next line is a
    different change and arms as usual."""
    window = make_window()
    load(window, TrackLyrics(synced=[(1.0, "one"), (5.0, "two"), (9.0, "three")]))
    window._last_state = PlaybackState.PLAYING
    window._on_position_update(snapshot(position=1.5))
    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)

    window._on_position_update(snapshot(position=5.1))

    assert window._swap_timer.isActive()
    assert window._transition.may_arm(2)
    window._begin_fade_out()
    assert window._transition.target == 2


def test_a_seek_back_into_the_current_line_still_snaps(make_window):
    """The bound on being ahead. Once the player is further from the line
    than the choreography can explain, the screen showing it is not a
    prediction any more — it is wrong, and snapping is the whole point of
    that check."""
    window = synced_window(make_window)
    window._on_position_update(snapshot(position=4.5))
    expire(window._fadeout_timer, window._begin_fade_out)
    expire(window._swap_timer, window._predicted_swap)
    assert window._current.text() == "two"

    window._on_position_update(snapshot(position=1.2))  # seek, backwards

    assert window._displayed_index == 0
    assert window._current.text() == "one"
    assert window._swap_timer.isActive()  # and the change is scheduled afresh


# -- the line change: what it costs to draw --------------------------------
#
# `sourcePixmap` re-renders the whole source widget — two labels, so two
# text layouts and two runs of glyph rasterisation — and Qt has no cache
# for it on a widget source. Measured with the sampler on a real window,
# that render was the single largest thing in a line change, and inside it
# QPainter::drawText alone was a third of every frame's paint. Nothing
# about the source moves during a phase, so it is rasterised once per
# phase and not once per frame.


class CountingFade(w.LineFade):
    """The real effect, with the one expensive call counted.

    A subclass rather than a patch, for the reason the pool has: assigning
    to something process-wide leaks into every test that runs afterwards.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renders = 0

    def sourcePixmap(self, *args, **kwargs):
        self.renders += 1
        return super().sourcePixmap(*args, **kwargs)


def counting_window(make_window):
    """A window whose sung line is behind a counting effect.

    Shown, because a hidden widget does not paint and an effect that is
    never drawn counts nothing: the first version of this measured zero
    renders and would have passed for any implementation at all.
    """
    window = synced_window(make_window)
    effect = CountingFade(window._current_box)
    window._current_fx = effect
    window._current_box.setGraphicsEffect(effect)
    window._apply_motion()
    window.show()
    APP.processEvents()
    window._current_box.repaint()
    assert effect.renders, "the effect is not being drawn at all"
    return window, effect


def repaint(window, times=1):
    """Force the effect to draw, the way an animation frame does."""
    for _ in range(times):
        window._current_box.repaint()


def test_a_phase_rasterises_the_line_once_however_many_frames_it_has(make_window):
    window, effect = counting_window(make_window)
    window._animate_line(-1.0, QEasingCurve.Type.InSine)
    effect.renders = 0
    for step in range(1, 9):
        effect.progress = -step / 8.0
        repaint(window)
    assert effect.renders == 1, "re-rendered the same words once a frame"


def test_a_repaint_that_is_not_a_frame_rasterises_again(make_window):
    """The state the window spends almost all of its time in. A repaint
    arriving without progress having moved is not a frame of an animation,
    so the ordinary case is exactly what it always was — and a funnel
    somebody forgets to invalidate is caught here rather than shown
    stale."""
    window, effect = counting_window(make_window)
    repaint(window)
    effect.renders = 0
    repaint(window, times=3)
    assert effect.renders == 3


@pytest.mark.parametrize(
    ("what", "change"),
    [
        ("the words", lambda win: win._set_line_text(win._current, "a new line")),
        ("the romanisation", lambda win: win._set_pronunciation("saeroun")),
        ("the romanisation going", lambda win: win._set_pronunciation("")),
        ("the type and colour", lambda win: win._restyle()),
    ],
)
def test_anything_that_changes_the_line_drops_what_was_drawn_of_it(
    make_window, what, change
):
    """Mid-phase, which is the only time it can matter and the only time
    it is hard: a resize re-elides the line while it is moving, and an
    appearance change repaints it. Either one drawn from the cache would
    be the line as it used to be."""
    window, effect = counting_window(make_window)
    window._animate_line(-1.0, QEasingCurve.Type.InSine)
    effect.progress = -0.25
    repaint(window)
    effect.renders = 0

    change(window)
    effect.progress = -0.5  # the phase carries on
    repaint(window)
    assert effect.renders == 1, f"{what} changed and the old drawing was reused"


def test_a_resize_mid_change_re_elides_and_is_not_drawn_from_the_cache(make_window):
    """The route the parametrised case above stands in for, driven for
    real: the strip elides against the window's width, so a drag while a
    line is moving changes the words themselves."""
    window, effect = counting_window(make_window)
    window._compact_applied = True
    window._animate_line(-1.0, QEasingCurve.Type.InSine)
    effect.progress = -0.25
    repaint(window)
    effect.renders = 0

    window._relayout()
    effect.progress = -0.5
    repaint(window)
    assert effect.renders == 1


@pytest.mark.parametrize("ratio", [1.0, 2.0])
@pytest.mark.parametrize("glow", [0.0, 0.5, 1.0])
def test_a_band_is_drawn_straight_and_it_is_the_same_pixels(make_window, glow, ratio):
    """The claim, checked rather than reasoned about. Between the corner
    radii the panel is a rectangle with a line down each side, so three
    axis-aligned fills produce exactly what the two rounded rectangles do
    — including at 2x, where the hairline is half a logical pixel wide and
    a rounding difference between the two routes would be visible, and
    while the edge is thickened by an acknowledged position."""
    window = make_window()
    window._glow = glow
    for top in range(w._CORNER_RADIUS, window.height() - w._CORNER_RADIUS - 8, 7):
        damaged = QRect(0, top, window.width(), 8)
        fast = panel_pixels(window, damaged, True, ratio)
        slow = panel_pixels(window, damaged, False, ratio)
        device = QRect(
            int(damaged.x() * ratio), int(damaged.y() * ratio),
            int(damaged.width() * ratio), int(damaged.height() * ratio),
        )
        assert pixels_of(fast, device) == pixels_of(slow, device), (
            f"the band at y={top} differs at {ratio}x"
        )


def test_the_corners_are_not_a_band(make_window):
    """The rounded part has to go through the path, or the window would
    have square corners while a line changed near the top of it."""
    window = make_window()
    assert not window._straight_band(QRectF(0, 0, window.width(), 20))
    assert not window._straight_band(
        QRectF(0, window.height() - 20, window.width(), 20)
    )
    assert not window._straight_band(QRectF(0, 0, window.width(), window.height()))
    middle = QRectF(0, w._CORNER_RADIUS, window.width(), 10)
    assert window._straight_band(middle)


def test_the_line_change_repaints_inside_the_band(make_window):
    """The whole reason the branch exists: the sung line sits between the
    corners, so a line change takes the cheap route 37 times."""
    window = synced_window(make_window)
    window.show()
    APP.processEvents()
    box = window._current_box
    damaged = window._current_fx.boundingRectFor(QRectF(box.rect())).translated(
        box.mapTo(window, box.rect().topLeft())
    )
    assert window._straight_band(damaged)


def test_the_effect_reserves_room_for_the_travel(make_window):
    """Without this the moving block is clipped to its own box and reads
    as dissolving at the edge instead of leaving."""
    window = synced_window(make_window)
    fx = window._current_fx
    source = QRectF(0, 0, 100, 40)
    grown = fx.boundingRectFor(source)
    assert grown.top() < source.top()
    assert grown.bottom() > source.bottom()
