"""Persistent manual override state for desk automation."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
from typing import Any

from ikea_desk_automation.config import DEFAULT_CONFIG_PATH

MANUAL_OVERRIDE_PATH = DEFAULT_CONFIG_PATH.parent / "manual_override.json"
MANUAL_OVERRIDE_MODES = frozenset({"sit", "stand", "away"})


@dataclasses.dataclass(frozen=True)
class ManualOverride:
    active: bool = False
    mode: str | None = None
    activated_at: str | None = None

    @property
    def label(self) -> str:
        if not self.active or self.mode is None:
            return "Manual override: inactive"
        return f"Manual override: {self.mode}"


class ManualOverrideStore:
    """JSON-backed latch shared by GeekDesk Control and monitor automation."""

    def __init__(self, path: pathlib.Path = MANUAL_OVERRIDE_PATH) -> None:
        self.path = path

    def read(self) -> ManualOverride:
        if not self.path.exists():
            return ManualOverride()
        try:
            payload = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return ManualOverride()
        if not isinstance(payload, dict) or not payload.get("active"):
            return ManualOverride()
        mode = payload.get("mode")
        if mode not in MANUAL_OVERRIDE_MODES:
            return ManualOverride()
        activated_at = payload.get("activated_at")
        return ManualOverride(
            active=True,
            mode=mode,
            activated_at=activated_at if isinstance(activated_at, str) else None,
        )

    def activate(self, mode: str) -> ManualOverride:
        if mode not in MANUAL_OVERRIDE_MODES:
            raise ValueError(f"manual override mode must be one of: {', '.join(sorted(MANUAL_OVERRIDE_MODES))}")
        override = ManualOverride(
            active=True,
            mode=mode,
            activated_at=dt.datetime.now(dt.UTC).isoformat(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(_override_to_dict(override), indent=2, sort_keys=True) + "\n")
        return override

    def clear(self) -> ManualOverride:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return ManualOverride()


def _override_to_dict(override: ManualOverride) -> dict[str, Any]:
    return {
        "active": override.active,
        "mode": override.mode,
        "activated_at": override.activated_at,
    }
