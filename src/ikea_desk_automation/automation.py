"""State-machine primitives for sitting / standing / away / unsafe decisions.

The DeskAutomation class accumulates observations and decides whether to
recommend a desk move.  It does NOT issue movement commands — callers
inspect the returned AutomationDecision and act on it.

Safety invariants:
- UNKNOWN and UNSAFE states never trigger a move and reset the sustained counter.
- Camera absence blocks moves when camera_required=True.
- Low-confidence observations are ignored.
- A move is only recommended after the same state has been observed continuously
  for at least ``sustained_state_seconds``.
- A configurable cooldown prevents moves from firing too frequently.
"""
from __future__ import annotations

import dataclasses
import enum
import time


class OccupancyState(enum.Enum):
    UNKNOWN = "unknown"
    SITTING = "sitting"
    STANDING = "standing"
    AWAY = "away"
    UNSAFE = "unsafe"


class DeskMotionState(enum.Enum):
    IDLE = "idle"
    MOVING = "moving"
    SETTLING = "settling"


@dataclasses.dataclass
class Observation:
    state: OccupancyState
    confidence: float
    timestamp: float = dataclasses.field(default_factory=time.monotonic)


@dataclasses.dataclass
class DeskMotionObservation:
    height_m: float
    speed_ms: float | None = None
    timestamp: float = dataclasses.field(default_factory=time.monotonic)


@dataclasses.dataclass
class DeskMotionEvaluation:
    state: DeskMotionState
    reason: str = ""

    @property
    def active(self) -> bool:
        return self.state in (DeskMotionState.MOVING, DeskMotionState.SETTLING)


@dataclasses.dataclass
class AutomationPolicy:
    sustained_state_seconds: float = 10.0
    cooldown_seconds: float = 45.0
    min_confidence: float = 0.55
    camera_required: bool = True


@dataclasses.dataclass
class AutomationDecision:
    should_move: bool
    target_state: OccupancyState | None = None
    reason: str = ""


class DeskMotionDetector:
    """Detect manual or external desk motion from speed and height samples."""

    def __init__(
        self,
        *,
        speed_threshold_ms: float = 0.005,
        height_delta_threshold_m: float = 0.003,
        settling_seconds: float = 2.0,
    ) -> None:
        self._speed_threshold_ms = speed_threshold_ms
        self._height_delta_threshold_m = height_delta_threshold_m
        self._settling_seconds = settling_seconds
        self._previous: DeskMotionObservation | None = None
        self._last_motion_time: float | None = None

    def evaluate(self, observation: DeskMotionObservation) -> DeskMotionEvaluation:
        moving_by_speed = (
            observation.speed_ms is not None
            and abs(observation.speed_ms) >= self._speed_threshold_ms
        )
        moving_by_height = False
        if self._previous is not None:
            delta_m = abs(observation.height_m - self._previous.height_m)
            moving_by_height = delta_m >= self._height_delta_threshold_m
        else:
            delta_m = 0.0

        self._previous = observation

        if moving_by_speed:
            self._last_motion_time = observation.timestamp
            return DeskMotionEvaluation(
                state=DeskMotionState.MOVING,
                reason=f"speed {observation.speed_ms:.3f} m/s",
            )

        if moving_by_height:
            self._last_motion_time = observation.timestamp
            return DeskMotionEvaluation(
                state=DeskMotionState.MOVING,
                reason=f"height changed {delta_m:.3f} m",
            )

        if self._last_motion_time is not None:
            elapsed = observation.timestamp - self._last_motion_time
            if elapsed < self._settling_seconds:
                return DeskMotionEvaluation(
                    state=DeskMotionState.SETTLING,
                    reason=f"settling ({elapsed:.1f}/{self._settling_seconds:.1f}s)",
                )

        return DeskMotionEvaluation(state=DeskMotionState.IDLE, reason="desk idle")

    def mark_idle(self, observation: DeskMotionObservation) -> None:
        """Prime the detector after a known completed command-driven movement."""
        self._previous = observation
        self._last_motion_time = None


class DeskAutomation:
    def __init__(self, policy: AutomationPolicy | None = None) -> None:
        self._policy = policy or AutomationPolicy()
        self._sustained_state: OccupancyState | None = None
        self._sustained_since: float | None = None
        self._last_move_time: float | None = None
        self._last_move_state: OccupancyState | None = None
        self._last_move_confirmed: bool = True
        self._camera_available: bool = True

    def update_camera(self, available: bool) -> None:
        self._camera_available = available

    def evaluate(self, observation: Observation) -> AutomationDecision:
        p = self._policy

        if p.camera_required and not self._camera_available:
            self._reset_sustained()
            return AutomationDecision(should_move=False, reason="camera unavailable")

        if observation.confidence < p.min_confidence:
            self._reset_sustained()
            return AutomationDecision(
                should_move=False,
                reason=(
                    f"confidence {observation.confidence:.2f} below "
                    f"threshold {p.min_confidence:.2f}"
                ),
            )

        if observation.state in (OccupancyState.UNKNOWN, OccupancyState.UNSAFE):
            self._reset_sustained()
            return AutomationDecision(
                should_move=False, reason=f"state is {observation.state.value}"
            )

        if observation.state != self._sustained_state:
            self._sustained_state = observation.state
            self._sustained_since = observation.timestamp

        elapsed = observation.timestamp - (self._sustained_since or observation.timestamp)

        if elapsed < p.sustained_state_seconds:
            return AutomationDecision(
                should_move=False,
                reason=(
                    f"waiting for sustained state "
                    f"({elapsed:.1f}/{p.sustained_state_seconds:.1f}s)"
                ),
            )

        if self._last_move_time is not None and self._last_move_state is not None:
            since_last = observation.timestamp - self._last_move_time
            posture_flip = (
                self._last_move_state in (OccupancyState.SITTING, OccupancyState.STANDING)
                and observation.state in (OccupancyState.SITTING, OccupancyState.STANDING)
                and observation.state != self._last_move_state
            )
            if not self._last_move_confirmed and observation.state == self._last_move_state:
                self._last_move_confirmed = True
            if (
                observation.state == self._last_move_state or posture_flip
            ) and since_last < p.cooldown_seconds:
                return AutomationDecision(
                    should_move=False,
                    reason=f"cooldown active ({since_last:.1f}/{p.cooldown_seconds:.1f}s)",
                )
            if not self._last_move_confirmed:
                if posture_flip:
                    return AutomationDecision(
                        should_move=False,
                        reason=(
                            "awaiting post-move "
                            f"{self._last_move_state.value} confirmation"
                        ),
                    )

        return AutomationDecision(
            should_move=True,
            target_state=observation.state,
            reason=f"sustained {observation.state.value} for {elapsed:.1f}s",
        )

    def record_move(
        self,
        timestamp: float | None = None,
        state: OccupancyState | None = None,
        *,
        requires_confirmation: bool = True,
    ) -> None:
        """Record that a move was executed.  Starts the cooldown timer."""
        self._last_move_time = timestamp if timestamp is not None else time.monotonic()
        self._last_move_state = state if state is not None else self._sustained_state
        self._last_move_confirmed = not (
            requires_confirmation
            and self._last_move_state in (OccupancyState.SITTING, OccupancyState.STANDING)
        )

    def reset_sustained(self) -> None:
        """Clear current posture accumulation without clearing movement cooldown."""
        self._reset_sustained()

    def _reset_sustained(self) -> None:
        self._sustained_state = None
        self._sustained_since = None
