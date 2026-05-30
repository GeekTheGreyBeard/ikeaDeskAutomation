"""Tests for the local web UI helpers."""
from __future__ import annotations

import pathlib

import pytest
import yaml

from ikea_desk_automation.config import AppConfig
from ikea_desk_automation.webui import WebUiState, config_to_dict


def test_config_to_dict_contains_ui_fields() -> None:
    data = config_to_dict(AppConfig())
    assert data["presets"]["sit"] == pytest.approx(0.730)
    assert data["automation"]["motion_speed_threshold_ms"] == pytest.approx(0.005)
    assert data["camera"]["roi_left"] == pytest.approx(0.0)
    assert data["audio"]["volume_percent"] == 8


def test_webui_save_validates_and_writes_config(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "config.yaml"
    state = WebUiState(config_path=path, fake=True)
    payload = config_to_dict(AppConfig())
    payload["desk"]["name"] = "test-desk"
    payload["presets"]["stand"] = 1.100
    saved = state.save_config(payload)

    assert saved.desk.name == "test-desk"
    assert saved.presets.stand == pytest.approx(1.100)
    written = yaml.safe_load(path.read_text())
    assert written["desk"]["name"] == "test-desk"
    assert written["presets"]["stand"] == pytest.approx(1.100)


def test_webui_fake_status_is_read_only(tmp_path: pathlib.Path) -> None:
    state = WebUiState(config_path=tmp_path / "missing.yaml", fake=True)
    status = state.read_status()
    assert status is not None
    assert status.height_m == pytest.approx(AppConfig().presets.sit)
    assert status.speed_ms == pytest.approx(0.0)
