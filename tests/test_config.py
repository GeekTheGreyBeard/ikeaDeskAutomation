"""Tests for config.py — no hardware required."""
from __future__ import annotations

import pathlib
import textwrap

import pytest

from ikea_desk_automation.config import (
    AppConfig,
    AutomationConfig,
    CameraConfig,
    DeskConfig,
    PresetsConfig,
)


def test_defaults() -> None:
    cfg = AppConfig()
    assert cfg.desk.address == ""
    assert cfg.presets.sit == pytest.approx(0.730)
    assert cfg.presets.stand == pytest.approx(1.125)
    assert cfg.presets.away == pytest.approx(0.9275)
    assert cfg.automation.sustained_state_seconds == pytest.approx(10.0)
    assert cfg.automation.camera_required is True
    assert cfg.automation.motion_speed_threshold_ms == pytest.approx(0.005)
    assert cfg.automation.motion_height_delta_threshold_m == pytest.approx(0.003)
    assert cfg.automation.motion_settle_seconds == pytest.approx(2.0)
    assert cfg.camera.enabled is True
    assert cfg.camera.width == 640


def test_load_from_yaml(tmp_path: pathlib.Path) -> None:
    yaml_text = textwrap.dedent("""\
        desk:
          address: "AA:BB:CC:DD:EE:FF"
          name: lab-desk
        presets:
          sit: 0.710
          stand: 1.100
          away: 0.710
        automation:
          sustained_state_seconds: 15
          cooldown_seconds: 120
          camera_required: false
          min_confidence: 0.6
          motion_speed_threshold_ms: 0.010
          motion_height_delta_threshold_m: 0.004
          motion_settle_seconds: 3
        camera:
          enabled: false
          serial: "1234567890"
          width: 424
          height: 240
          fps: 15
          roi_left: 0.1
          roi_right: 0.9
    """)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml_text)
    cfg = AppConfig.load(cfg_file)

    assert cfg.desk.address == "AA:BB:CC:DD:EE:FF"
    assert cfg.desk.name == "lab-desk"
    assert cfg.presets.sit == pytest.approx(0.710)
    assert cfg.presets.stand == pytest.approx(1.100)
    assert cfg.automation.sustained_state_seconds == pytest.approx(15.0)
    assert cfg.automation.cooldown_seconds == pytest.approx(120.0)
    assert cfg.automation.camera_required is False
    assert cfg.automation.min_confidence == pytest.approx(0.6)
    assert cfg.automation.motion_speed_threshold_ms == pytest.approx(0.010)
    assert cfg.automation.motion_height_delta_threshold_m == pytest.approx(0.004)
    assert cfg.automation.motion_settle_seconds == pytest.approx(3.0)
    assert cfg.camera.enabled is False
    assert cfg.camera.serial == "1234567890"
    assert cfg.camera.width == 424
    assert cfg.camera.roi_left == pytest.approx(0.1)
    assert cfg.camera.roi_right == pytest.approx(0.9)


def test_legacy_nested_preset_aliases(tmp_path: pathlib.Path) -> None:
    yaml_text = textwrap.dedent("""\
        desk:
          mac_address: "AA:BB:CC:DD:EE:FF"
          presets:
            sit_m: 0.720
            stand_m: 1.120
            away_m: 1.120
        automation:
          movement_cooldown_seconds: 90
    """)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml_text)
    cfg = AppConfig.load(cfg_file)
    assert cfg.desk.address == "AA:BB:CC:DD:EE:FF"
    assert cfg.presets.sit == pytest.approx(0.720)
    assert cfg.presets.stand == pytest.approx(1.120)
    assert cfg.presets.away == pytest.approx(1.120)
    assert cfg.automation.cooldown_seconds == pytest.approx(90)


def test_partial_yaml_gets_defaults(tmp_path: pathlib.Path) -> None:
    """Missing YAML sections fall back to dataclass defaults."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("desk:\n  address: XX:XX:XX:XX:XX:XX\n")
    cfg = AppConfig.load(cfg_file)
    assert cfg.presets.stand == pytest.approx(1.125)
    assert cfg.presets.away == pytest.approx((0.730 + 1.125) / 2)
    assert cfg.automation.cooldown_seconds == pytest.approx(45.0)


def test_invalid_yaml_raises(tmp_path: pathlib.Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("- list\n- not\n- a\n- mapping\n")
    with pytest.raises(ValueError, match="YAML mapping"):
        AppConfig.load(cfg_file)


def test_quoted_bool_raises(tmp_path: pathlib.Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text('automation:\n  camera_required: "false"\n')
    with pytest.raises(ValueError, match="camera_required"):
        AppConfig.load(cfg_file)


def test_preset_outside_bounds_raises(tmp_path: pathlib.Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("desk:\n  min_height_m: 0.7\n  max_height_m: 1.0\npresets:\n  stand: 1.2\n")
    with pytest.raises(ValueError, match="presets.stand"):
        AppConfig.load(cfg_file)


def test_committed_example_config_loads() -> None:
    cfg = AppConfig.load(pathlib.Path("config.example.yaml"))
    assert cfg.desk.address == "AA:BB:CC:DD:EE:FF"
    assert cfg.presets.away == pytest.approx(0.9275)
    assert cfg.automation.movement_timeout_seconds == pytest.approx(45)
    assert cfg.audio.enabled is True
    assert cfg.audio.volume_percent == 8
    assert cfg.camera.enabled is True
    assert cfg.camera.serial == ""
    assert cfg.camera.min_foreground_ratio == pytest.approx(0.01)


def test_invalid_audio_percent_raises(tmp_path: pathlib.Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("audio:\n  volume_percent: 101\n")
    with pytest.raises(ValueError, match="audio.volume_percent"):
        AppConfig.load(cfg_file)


def test_invalid_motion_threshold_raises(tmp_path: pathlib.Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("automation:\n  motion_height_delta_threshold_m: -0.001\n")
    with pytest.raises(ValueError, match="motion_height_delta_threshold_m"):
        AppConfig.load(cfg_file)


def test_load_or_default_missing_file() -> None:
    missing = pathlib.Path("/tmp/definitely-does-not-exist-ikea-desk-config.yaml")
    cfg = AppConfig.load_or_default(missing)
    assert isinstance(cfg, AppConfig)


def test_from_dict_empty() -> None:
    cfg = AppConfig.from_dict({})
    assert cfg.desk.address == ""
    assert isinstance(cfg.presets, PresetsConfig)
    assert isinstance(cfg.automation, AutomationConfig)
    assert isinstance(cfg.desk, DeskConfig)
    assert isinstance(cfg.camera, CameraConfig)


def test_invalid_camera_roi_raises(tmp_path: pathlib.Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("camera:\n  roi_left: 0.9\n  roi_right: 0.1\n")
    with pytest.raises(ValueError, match="ROI"):
        AppConfig.load(cfg_file)


def test_invalid_camera_threshold_raises(tmp_path: pathlib.Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("camera:\n  standing_top_y: 1.8\n")
    with pytest.raises(ValueError, match="standing_top_y"):
        AppConfig.load(cfg_file)
