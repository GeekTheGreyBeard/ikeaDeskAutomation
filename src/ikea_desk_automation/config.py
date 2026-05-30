"""YAML configuration loader."""
from __future__ import annotations

import dataclasses
import math
import pathlib
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = pathlib.Path.home() / ".config" / "ikea-desk" / "config.yaml"


@dataclasses.dataclass
class PresetsConfig:
    sit: float = 0.730
    stand: float = 1.125
    away: float = 0.9275

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PresetsConfig:
        sit = _finite_float(d.get("sit", d.get("sit_m", 0.730)), "presets.sit")
        stand = _finite_float(d.get("stand", d.get("stand_m", 1.125)), "presets.stand")
        default_away = (sit + stand) / 2
        return cls(
            sit=sit,
            stand=stand,
            away=_finite_float(d.get("away", d.get("away_m", default_away)), "presets.away"),
        )

    def values(self) -> dict[str, float]:
        return {
            "sit": self.sit,
            "stand": self.stand,
            "away": self.away,
        }


@dataclasses.dataclass
class DeskConfig:
    address: str = ""
    name: str = "desk"
    min_height_m: float = 0.620
    max_height_m: float = 1.270

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeskConfig:
        return cls(
            address=str(d.get("address", d.get("mac_address", ""))),
            name=str(d.get("name", "desk")),
            min_height_m=_finite_float(d.get("min_height_m", 0.620), "desk.min_height_m"),
            max_height_m=_finite_float(d.get("max_height_m", 1.270), "desk.max_height_m"),
        )

    def validate(self) -> None:
        if self.min_height_m <= 0:
            raise ValueError("desk.min_height_m must be positive")
        if self.max_height_m <= self.min_height_m:
            raise ValueError("desk.max_height_m must be greater than desk.min_height_m")

    def validate_height(self, height_m: float, label: str) -> None:
        value = _finite_float(height_m, label)
        if not self.min_height_m <= value <= self.max_height_m:
            raise ValueError(
                f"{label}={value:.3f} must be within desk bounds "
                f"{self.min_height_m:.3f}..{self.max_height_m:.3f} m"
            )


@dataclasses.dataclass
class AutomationConfig:
    sustained_state_seconds: float = 10.0
    cooldown_seconds: float = 45.0
    camera_required: bool = True
    min_confidence: float = 0.55
    movement_timeout_seconds: float = 45.0
    final_tolerance_m: float = 0.010
    sample_interval_seconds: float = 2.0
    motion_speed_threshold_ms: float = 0.005
    motion_height_delta_threshold_m: float = 0.003
    motion_settle_seconds: float = 2.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutomationConfig:
        return cls(
            sustained_state_seconds=_finite_float(
                d.get("sustained_state_seconds", 10.0), "automation.sustained_state_seconds"
            ),
            cooldown_seconds=_finite_float(
                d.get("cooldown_seconds", d.get("movement_cooldown_seconds", 45.0)),
                "automation.cooldown_seconds",
            ),
            camera_required=_strict_bool(d.get("camera_required", True), "automation.camera_required"),
            min_confidence=_finite_float(d.get("min_confidence", 0.55), "automation.min_confidence"),
            movement_timeout_seconds=_finite_float(
                d.get("movement_timeout_seconds", 45.0), "automation.movement_timeout_seconds"
            ),
            final_tolerance_m=_finite_float(
                d.get("final_tolerance_m", 0.010), "automation.final_tolerance_m"
            ),
            sample_interval_seconds=_finite_float(
                d.get("sample_interval_seconds", 2.0), "automation.sample_interval_seconds"
            ),
            motion_speed_threshold_ms=_finite_float(
                d.get("motion_speed_threshold_ms", 0.005),
                "automation.motion_speed_threshold_ms",
            ),
            motion_height_delta_threshold_m=_finite_float(
                d.get("motion_height_delta_threshold_m", 0.003),
                "automation.motion_height_delta_threshold_m",
            ),
            motion_settle_seconds=_finite_float(
                d.get("motion_settle_seconds", 2.0), "automation.motion_settle_seconds"
            ),
        )

    def validate(self) -> None:
        if self.sustained_state_seconds < 0:
            raise ValueError("automation.sustained_state_seconds must be non-negative")
        if self.cooldown_seconds < 0:
            raise ValueError("automation.cooldown_seconds must be non-negative")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("automation.min_confidence must be between 0 and 1")
        if self.movement_timeout_seconds <= 0:
            raise ValueError("automation.movement_timeout_seconds must be positive")
        if self.final_tolerance_m <= 0:
            raise ValueError("automation.final_tolerance_m must be positive")
        if self.sample_interval_seconds <= 0:
            raise ValueError("automation.sample_interval_seconds must be positive")
        if self.motion_speed_threshold_ms < 0:
            raise ValueError("automation.motion_speed_threshold_ms must be non-negative")
        if self.motion_height_delta_threshold_m < 0:
            raise ValueError("automation.motion_height_delta_threshold_m must be non-negative")
        if self.motion_settle_seconds < 0:
            raise ValueError("automation.motion_settle_seconds must be non-negative")


@dataclasses.dataclass
class AudioConfig:
    enabled: bool = True
    volume_percent: int = 8

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AudioConfig:
        return cls(
            enabled=_strict_bool(d.get("enabled", True), "audio.enabled"),
            volume_percent=_int_percent(d.get("volume_percent", 8), "audio.volume_percent"),
        )

    def validate(self) -> None:
        if not 0 <= self.volume_percent <= 100:
            raise ValueError("audio.volume_percent must be between 0 and 100")


@dataclasses.dataclass
class CameraConfig:
    enabled: bool = True
    serial: str = ""
    width: int = 640
    height: int = 480
    fps: int = 15
    warmup_frames: int = 5
    sample_frames: int = 3
    roi_left: float = 0.0
    roi_top: float = 0.0
    roi_right: float = 1.0
    roi_bottom: float = 1.0
    min_depth_m: float = 0.25
    max_depth_m: float = 4.0
    obstruction_distance_m: float = 0.35
    min_foreground_ratio: float = 0.01
    standing_top_y: float = 0.35
    min_confidence: float = 0.55

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CameraConfig:
        return cls(
            enabled=_strict_bool(d.get("enabled", True), "camera.enabled"),
            serial=str(d.get("serial", "")),
            width=_positive_int(d.get("width", 640), "camera.width"),
            height=_positive_int(d.get("height", 480), "camera.height"),
            fps=_positive_int(d.get("fps", 15), "camera.fps"),
            warmup_frames=_nonnegative_int(d.get("warmup_frames", 5), "camera.warmup_frames"),
            sample_frames=_positive_int(d.get("sample_frames", 3), "camera.sample_frames"),
            roi_left=_finite_float(d.get("roi_left", 0.0), "camera.roi_left"),
            roi_top=_finite_float(d.get("roi_top", 0.0), "camera.roi_top"),
            roi_right=_finite_float(d.get("roi_right", 1.0), "camera.roi_right"),
            roi_bottom=_finite_float(d.get("roi_bottom", 1.0), "camera.roi_bottom"),
            min_depth_m=_finite_float(d.get("min_depth_m", 0.25), "camera.min_depth_m"),
            max_depth_m=_finite_float(d.get("max_depth_m", 4.0), "camera.max_depth_m"),
            obstruction_distance_m=_finite_float(
                d.get("obstruction_distance_m", 0.35), "camera.obstruction_distance_m"
            ),
            min_foreground_ratio=_finite_float(
                d.get("min_foreground_ratio", 0.01), "camera.min_foreground_ratio"
            ),
            standing_top_y=_finite_float(d.get("standing_top_y", 0.35), "camera.standing_top_y"),
            min_confidence=_finite_float(d.get("min_confidence", 0.55), "camera.min_confidence"),
        )

    def validate(self) -> None:
        if not 0 <= self.roi_left < self.roi_right <= 1:
            raise ValueError("camera ROI left/right must satisfy 0 <= left < right <= 1")
        if not 0 <= self.roi_top < self.roi_bottom <= 1:
            raise ValueError("camera ROI top/bottom must satisfy 0 <= top < bottom <= 1")
        if self.min_depth_m <= 0:
            raise ValueError("camera.min_depth_m must be positive")
        if self.max_depth_m <= self.min_depth_m:
            raise ValueError("camera.max_depth_m must be greater than camera.min_depth_m")
        if self.obstruction_distance_m <= 0:
            raise ValueError("camera.obstruction_distance_m must be positive")
        if not 0 <= self.min_foreground_ratio <= 1:
            raise ValueError("camera.min_foreground_ratio must be between 0 and 1")
        if not 0 <= self.standing_top_y <= 1:
            raise ValueError("camera.standing_top_y must be between 0 and 1")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("camera.min_confidence must be between 0 and 1")


@dataclasses.dataclass
class AppConfig:
    desk: DeskConfig = dataclasses.field(default_factory=DeskConfig)
    presets: PresetsConfig = dataclasses.field(default_factory=PresetsConfig)
    automation: AutomationConfig = dataclasses.field(default_factory=AutomationConfig)
    audio: AudioConfig = dataclasses.field(default_factory=AudioConfig)
    camera: CameraConfig = dataclasses.field(default_factory=CameraConfig)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AppConfig:
        desk_raw = d.get("desk", {})
        presets_raw = d.get("presets", {})
        if isinstance(desk_raw, dict) and not presets_raw and isinstance(desk_raw.get("presets"), dict):
            presets_raw = desk_raw["presets"]

        cfg = cls(
            desk=DeskConfig.from_dict(d.get("desk", {})),
            presets=PresetsConfig.from_dict(presets_raw),
            automation=AutomationConfig.from_dict(d.get("automation", {})),
            audio=AudioConfig.from_dict(d.get("audio", {})),
            camera=CameraConfig.from_dict(d.get("camera", {})),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        self.desk.validate()
        self.automation.validate()
        self.audio.validate()
        self.camera.validate()
        for name, height_m in self.presets.values().items():
            self.desk.validate_height(height_m, f"presets.{name}")

    @classmethod
    def load(cls, path: pathlib.Path) -> AppConfig:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"Config must be a YAML mapping: {path}")
        return cls.from_dict(raw)

    @classmethod
    def load_or_default(cls, path: pathlib.Path | None = None) -> AppConfig:
        resolved = path if path is not None else DEFAULT_CONFIG_PATH
        if not resolved.exists():
            return cls()
        return cls.load(resolved)


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _strict_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{label} must be a YAML boolean true/false")


def _int_percent(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer percent")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer percent") from exc
    return result


def _positive_int(value: Any, label: str) -> int:
    result = _nonnegative_int(value, label)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if result < 0:
        raise ValueError(f"{label} must be non-negative")
    return result
