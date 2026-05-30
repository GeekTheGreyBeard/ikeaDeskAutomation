"""Tests for the automation state machine — no hardware required."""
from __future__ import annotations

from ikea_desk_automation.automation import (
    AutomationPolicy,
    DeskAutomation,
    DeskMotionDetector,
    DeskMotionObservation,
    DeskMotionState,
    Observation,
    OccupancyState,
)

_POLICY = AutomationPolicy(
    sustained_state_seconds=10.0,
    cooldown_seconds=60.0,
    min_confidence=0.5,
    camera_required=True,
)

T0 = 1_000.0  # arbitrary base timestamp


def _obs(
    state: OccupancyState,
    confidence: float = 0.9,
    t: float = T0,
) -> Observation:
    return Observation(state=state, confidence=confidence, timestamp=t)


# ---------------------------------------------------------------------------
# Safety guards
# ---------------------------------------------------------------------------


def test_camera_unavailable_blocks_move() -> None:
    auto = DeskAutomation(_POLICY)
    auto.update_camera(False)
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 20))
    assert not dec.should_move
    assert "camera" in dec.reason


def test_camera_not_required_skips_check() -> None:
    policy = AutomationPolicy(
        sustained_state_seconds=5.0,
        cooldown_seconds=0.0,
        min_confidence=0.5,
        camera_required=False,
    )
    auto = DeskAutomation(policy)
    auto.update_camera(False)  # unavailable, but not required
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    # not yet sustained, but shouldn't refuse due to camera
    assert "camera" not in dec.reason


def test_low_confidence_blocks_move() -> None:
    auto = DeskAutomation(_POLICY)
    dec = auto.evaluate(_obs(OccupancyState.SITTING, confidence=0.3))
    assert not dec.should_move
    assert "confidence" in dec.reason


def test_low_confidence_resets_sustained_state() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    auto.evaluate(_obs(OccupancyState.SITTING, confidence=0.3, t=T0 + 8.0))
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 14.0))
    assert not dec.should_move
    assert "waiting" in dec.reason


def test_camera_unavailable_resets_sustained_state() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    auto.update_camera(False)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 8.0))
    auto.update_camera(True)
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 14.0))
    assert not dec.should_move
    assert "waiting" in dec.reason


def test_unknown_state_blocks_move() -> None:
    auto = DeskAutomation(_POLICY)
    dec = auto.evaluate(_obs(OccupancyState.UNKNOWN))
    assert not dec.should_move
    assert "unknown" in dec.reason


def test_unsafe_state_blocks_move() -> None:
    auto = DeskAutomation(_POLICY)
    dec = auto.evaluate(_obs(OccupancyState.UNSAFE))
    assert not dec.should_move
    assert "unsafe" in dec.reason


# ---------------------------------------------------------------------------
# Sustained-state logic
# ---------------------------------------------------------------------------


def test_not_yet_sustained() -> None:
    auto = DeskAutomation(_POLICY)
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    assert not dec.should_move
    assert "waiting" in dec.reason


def test_sustained_triggers_move() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 11.0))
    assert dec.should_move
    assert dec.target_state == OccupancyState.SITTING


def test_state_change_resets_timer() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    # switch to standing — resets the clock
    auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 8.0))
    # still not 10s from the state change
    dec = auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 15.0))
    assert not dec.should_move  # only 7s of standing (15 - 8)


def test_state_change_then_sustained() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 8.0))
    dec = auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 20.0))
    assert dec.should_move  # 12s of standing
    assert dec.target_state == OccupancyState.STANDING


# ---------------------------------------------------------------------------
# Cooldown logic
# ---------------------------------------------------------------------------


def test_cooldown_blocks_second_move() -> None:
    auto = DeskAutomation(_POLICY)
    # First move triggers
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 11.0))
    assert dec.should_move
    auto.record_move(timestamp=T0 + 11.0)

    # Immediate re-evaluate should be blocked by cooldown
    dec2 = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 12.0))
    assert not dec2.should_move
    assert "cooldown" in dec2.reason


def test_cooldown_allows_different_state_transition() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.AWAY, t=T0))
    dec = auto.evaluate(_obs(OccupancyState.AWAY, t=T0 + 11.0))
    assert dec.should_move
    auto.record_move(timestamp=T0 + 11.0, state=OccupancyState.AWAY)

    auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 12.0))
    dec2 = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 23.0))
    assert dec2.should_move
    assert dec2.target_state == OccupancyState.SITTING


def test_cooldown_blocks_immediate_posture_flip() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.STANDING, t=T0))
    dec = auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 11.0))
    assert dec.should_move
    auto.record_move(timestamp=T0 + 11.0, state=OccupancyState.STANDING)

    auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 12.0))
    dec2 = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 23.0))
    assert not dec2.should_move
    assert "cooldown" in dec2.reason


def test_post_move_confirmation_blocks_false_posture_flip_after_cooldown() -> None:
    policy = AutomationPolicy(
        sustained_state_seconds=5.0,
        cooldown_seconds=30.0,
        min_confidence=0.5,
        camera_required=False,
    )
    auto = DeskAutomation(policy)
    auto.evaluate(_obs(OccupancyState.STANDING, t=T0))
    dec = auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 6.0))
    assert dec.should_move
    auto.record_move(timestamp=T0 + 6.0, state=OccupancyState.STANDING)

    auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 40.0))
    dec2 = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 46.0))
    assert not dec2.should_move
    assert "post-move standing confirmation" in dec2.reason


def test_post_move_confirmation_allows_confirmed_posture_flip_after_cooldown() -> None:
    policy = AutomationPolicy(
        sustained_state_seconds=5.0,
        cooldown_seconds=30.0,
        min_confidence=0.5,
        camera_required=False,
    )
    auto = DeskAutomation(policy)
    auto.evaluate(_obs(OccupancyState.STANDING, t=T0))
    dec = auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 6.0))
    assert dec.should_move
    auto.record_move(timestamp=T0 + 6.0, state=OccupancyState.STANDING)

    assert not auto.evaluate(_obs(OccupancyState.STANDING, t=T0 + 12.0)).should_move
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 40.0))
    dec2 = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 46.0))
    assert dec2.should_move
    assert dec2.target_state == OccupancyState.SITTING


def test_cooldown_clears_after_elapsed() -> None:
    policy = AutomationPolicy(
        sustained_state_seconds=5.0,
        cooldown_seconds=30.0,
        min_confidence=0.5,
        camera_required=False,
    )
    auto = DeskAutomation(policy)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 6.0))
    assert dec.should_move
    auto.record_move(timestamp=T0 + 6.0)

    # still in cooldown at +20s
    assert not auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 20.0)).should_move
    # cooldown cleared at +40s
    dec3 = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 40.0))
    assert dec3.should_move


# ---------------------------------------------------------------------------
# UNSAFE resets sustained state
# ---------------------------------------------------------------------------


def test_unsafe_resets_sustained() -> None:
    auto = DeskAutomation(_POLICY)
    auto.evaluate(_obs(OccupancyState.SITTING, t=T0))
    # interrupted by unsafe
    auto.evaluate(_obs(OccupancyState.UNSAFE, t=T0 + 5.0))
    # resume sitting — clock should have reset
    dec = auto.evaluate(_obs(OccupancyState.SITTING, t=T0 + 14.0))
    assert not dec.should_move  # only 9s of sitting after unsafe


def test_all_occupancy_states_represented() -> None:
    members = {s.value for s in OccupancyState}
    assert "sitting" in members
    assert "standing" in members
    assert "away" in members
    assert "unknown" in members
    assert "unsafe" in members


# ---------------------------------------------------------------------------
# Desk motion detection
# ---------------------------------------------------------------------------


def test_motion_detector_uses_reported_speed() -> None:
    detector = DeskMotionDetector(speed_threshold_ms=0.005)

    motion = detector.evaluate(
        DeskMotionObservation(height_m=0.800, speed_ms=0.020, timestamp=T0)
    )

    assert motion.state == DeskMotionState.MOVING
    assert motion.active is True
    assert "speed" in motion.reason


def test_motion_detector_uses_height_delta_when_speed_missing() -> None:
    detector = DeskMotionDetector(height_delta_threshold_m=0.003)

    assert (
        detector.evaluate(
            DeskMotionObservation(height_m=0.800, speed_ms=None, timestamp=T0)
        ).state
        == DeskMotionState.IDLE
    )
    motion = detector.evaluate(
        DeskMotionObservation(height_m=0.806, speed_ms=None, timestamp=T0 + 1.0)
    )

    assert motion.state == DeskMotionState.MOVING
    assert "height changed" in motion.reason


def test_motion_detector_reports_settling_after_motion() -> None:
    detector = DeskMotionDetector(
        speed_threshold_ms=0.005,
        height_delta_threshold_m=0.003,
        settling_seconds=2.0,
    )

    detector.evaluate(DeskMotionObservation(height_m=0.800, speed_ms=0.020, timestamp=T0))
    settling = detector.evaluate(
        DeskMotionObservation(height_m=0.800, speed_ms=0.0, timestamp=T0 + 1.0)
    )
    idle = detector.evaluate(
        DeskMotionObservation(height_m=0.800, speed_ms=0.0, timestamp=T0 + 3.0)
    )

    assert settling.state == DeskMotionState.SETTLING
    assert settling.active is True
    assert idle.state == DeskMotionState.IDLE
