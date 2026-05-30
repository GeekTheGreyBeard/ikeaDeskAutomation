"""Cross-process liveness heartbeat for the desk monitor loop.

The monitor process (``ikea-desk monitor``) and GeekDesk Control run in
separate processes, so systemd ``ActiveState=active`` only proves the monitor
process exists — not that it is actually sampling and deciding. The monitor
writes a small JSON heartbeat here each loop iteration; GeekDesk Control reads
it to report honestly whether automation is effectively live or the monitor is
stuck (e.g. blocked connecting to the desk).

Timestamps are wall-clock ``time.time()`` so they stay comparable across
processes (``time.monotonic()`` is not).
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import time
from typing import Any

from ikea_desk_automation.config import DEFAULT_CONFIG_PATH

MONITOR_STATUS_PATH = DEFAULT_CONFIG_PATH.parent / "monitor_status.json"

# A heartbeat older than max(sample_interval * multiplier, floor) means the
# monitor is not sampling on schedule and automation cannot be trusted as live.
STALE_INTERVAL_MULTIPLIER = 4.0
MIN_STALE_SECONDS = 15.0

PHASE_STARTING = "starting"
PHASE_CONNECTING = "connecting"
PHASE_SAMPLING = "sampling"
PHASE_STOPPED = "stopped"


@dataclasses.dataclass(frozen=True)
class MonitorStatus:
    present: bool = False
    pid: int | None = None
    phase: str = "unknown"
    execute: bool = False
    interval_seconds: float | None = None
    started_at: float | None = None
    updated_at: float | None = None
    sample_count: int = 0
    last_state: str | None = None
    last_confidence: float | None = None
    last_decision: str | None = None
    last_motion: str | None = None
    last_move_at: float | None = None
    last_move_target: str | None = None
    message: str | None = None

    def age_seconds(self, now: float | None = None) -> float | None:
        if self.updated_at is None:
            return None
        reference = now if now is not None else time.time()
        return max(0.0, reference - self.updated_at)

    def stale_threshold_seconds(self) -> float:
        interval = self.interval_seconds or 0.0
        return max(MIN_STALE_SECONDS, interval * STALE_INTERVAL_MULTIPLIER)

    def is_fresh(self, now: float | None = None) -> bool:
        age = self.age_seconds(now)
        if age is None:
            return False
        return age <= self.stale_threshold_seconds()


class MonitorStatusStore:
    """JSON-backed heartbeat shared by the monitor loop and GeekDesk Control."""

    def __init__(self, path: pathlib.Path = MONITOR_STATUS_PATH) -> None:
        self.path = path

    def read(self) -> MonitorStatus:
        if not self.path.exists():
            return MonitorStatus(present=False)
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return MonitorStatus(present=False)
        if not isinstance(payload, dict):
            return MonitorStatus(present=False)
        return _status_from_dict(payload)

    def write(self, status: MonitorStatus) -> MonitorStatus:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(_status_to_dict(status), indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)
        return status

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def new_status(*, execute: bool, interval_seconds: float, phase: str = PHASE_STARTING) -> MonitorStatus:
    now = time.time()
    return MonitorStatus(
        present=True,
        pid=os.getpid(),
        phase=phase,
        execute=execute,
        interval_seconds=interval_seconds,
        started_at=now,
        updated_at=now,
    )


def _status_to_dict(status: MonitorStatus) -> dict[str, Any]:
    return dataclasses.asdict(status)


def _status_from_dict(payload: dict[str, Any]) -> MonitorStatus:
    fields = {f.name for f in dataclasses.fields(MonitorStatus)}
    known = {key: value for key, value in payload.items() if key in fields}
    known["present"] = True
    return MonitorStatus(**known)
