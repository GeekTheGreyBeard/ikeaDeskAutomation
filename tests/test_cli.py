"""Tests for cli.py — no hardware required."""
from __future__ import annotations

import asyncio
import json
import pathlib
import random

import pytest

import ikea_desk_automation.cli as cli_module
from ikea_desk_automation.cli import (
    _dispatch,
    _cmd_camera_fit_target_logs,
    _expected_centerline_target_distance_m,
    _preset_height,
    _target_height_for_state,
    build_parser,
    cmd_height,
    cmd_monitor,
    cmd_preset,
    cmd_speed,
    cmd_status,
)
from ikea_desk_automation.automation import Observation, OccupancyState
from ikea_desk_automation.camera import DepthMetrics
from ikea_desk_automation.config import AppConfig, AutomationConfig, PresetsConfig
from ikea_desk_automation.desk.base import DeskStatus
from ikea_desk_automation.desk.fake import FakeDeskClient
from ikea_desk_automation.manual_override import ManualOverrideStore


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate_default_manual_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    class TestManualOverrideStore(ManualOverrideStore):
        def __init__(self, path: pathlib.Path | None = None) -> None:
            super().__init__(path or tmp_path / "manual_override.json")

    monkeypatch.setattr(cli_module, "ManualOverrideStore", TestManualOverrideStore)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_builds() -> None:
    parser = build_parser()
    args = parser.parse_args(["--version"])
    assert args.version is True


def test_parser_status_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_parser_height_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["height"])
    assert args.command == "height"


def test_parser_speed_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["speed"])
    assert args.command == "speed"


def test_parser_preset_sit() -> None:
    parser = build_parser()
    args = parser.parse_args(["preset", "sit"])
    assert args.command == "preset"
    assert args.preset_name == "sit"
    assert args.execute is False


def test_parser_preset_stand_execute() -> None:
    parser = build_parser()
    args = parser.parse_args(["preset", "stand", "--execute"])
    assert args.preset_name == "stand"
    assert args.execute is True
    assert args.no_alert is False


def test_parser_preset_no_alert() -> None:
    parser = build_parser()
    args = parser.parse_args(["preset", "stand", "--execute", "--no-alert"])
    assert args.no_alert is True


def test_parser_preset_invalid_choice() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["preset", "hover"])


def test_parser_camera_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["camera"])
    assert args.command == "camera"
    assert args.sample is False
    assert args.classify is False


def test_parser_camera_sample_classify() -> None:
    parser = build_parser()
    args = parser.parse_args(["camera", "--sample", "--classify", "--calibrate"])
    assert args.command == "camera"
    assert args.sample is True
    assert args.classify is True
    assert args.calibrate is True


def test_parser_camera_target_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["camera", "--target-distance-in", "24"])
    assert args.command == "camera"
    assert args.target_distance_in == pytest.approx(24.0)
    assert args.target_lateral_in == pytest.approx(0.0)
    assert args.target_width_in == pytest.approx(7.0)
    assert args.target_depth_in == pytest.approx(6.0)
    assert args.target_height_in == pytest.approx(4.0)
    assert args.target_log is None


def test_parser_camera_target_log() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "camera",
            "--target-distance-in",
            "48",
            "--target-lateral-in",
            "30",
            "--target-log",
            "capture.json",
        ]
    )

    assert args.target_distance_in == pytest.approx(48.0)
    assert args.target_lateral_in == pytest.approx(30.0)
    assert args.target_log.name == "capture.json"


def test_parser_camera_fit_target_log() -> None:
    parser = build_parser()
    args = parser.parse_args(["camera", "--fit-target-log", "capture.json"])

    assert args.fit_target_log[0].name == "capture.json"
    assert args.fit_roi_label == "lower-narrow-center"


def test_expected_centerline_target_distance() -> None:
    expected = _expected_centerline_target_distance_m(24.0, 4.0)
    assert expected == pytest.approx(2.14, abs=0.02)


def test_cmd_camera_fit_target_logs_reports_underconstrained(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "target.json"
    entries = []
    for distance_in, camera_y in [(24.0, 0.0), (48.0, 0.2), (72.0, 0.4)]:
        entries.append(
            {
                "roi": {"label": "lower-narrow-center"},
                "target": {"room_center_in": {"x": 0.0, "y": distance_in, "z": 2.0}},
                "deprojected_target": {
                    "point_count": 10,
                    "median_camera_m": {"x": 0.1, "y": camera_y, "z": 1.0 + camera_y},
                },
            }
        )
    path.write_text(json.dumps({"entries": entries}))

    rc = _cmd_camera_fit_target_logs([path], roi_label="lower-narrow-center")

    assert rc == 0
    out = capsys.readouterr().out
    assert "calibration-fit: read-only" in out
    assert "status: underconstrained" in out
    assert "left/right target captures" in out


def test_parser_alert_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["alert", "--no-volume-change"])
    assert args.command == "alert"
    assert args.no_volume_change is True


def test_parser_monitor_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["monitor", "--execute", "--stop-after-first-move", "--sample-interval", "0.5"]
    )
    assert args.command == "monitor"
    assert args.execute is True
    assert args.stop_after_first_move is True
    assert args.sample_interval == pytest.approx(0.5)


def test_parser_ui_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["ui", "--host", "0.0.0.0", "--port", "9999", "--open"])
    assert args.command == "ui"
    assert args.host == "0.0.0.0"
    assert args.port == 9999
    assert args.open is True


def test_parser_fake_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["--fake", "height"])
    assert args.fake is True


# ---------------------------------------------------------------------------
# Preset height lookup
# ---------------------------------------------------------------------------


def test_preset_height_all_names() -> None:
    presets = PresetsConfig(sit=0.730, stand=1.125, away=0.720)
    assert _preset_height(presets, "sit") == pytest.approx(0.730)
    assert _preset_height(presets, "stand") == pytest.approx(1.125)
    assert _preset_height(presets, "away") == pytest.approx(0.720)


def test_away_target_uses_configured_away_preset() -> None:
    cfg = AppConfig()
    name, height = _target_height_for_state(cfg, OccupancyState.AWAY, random.Random(4))
    assert name == "away"
    assert height == pytest.approx(cfg.presets.away)


def test_return_targets_match_detected_posture() -> None:
    cfg = AppConfig()
    assert _target_height_for_state(
        cfg,
        OccupancyState.SITTING,
        random.Random(),
    )[1] == pytest.approx(cfg.presets.sit)
    assert _target_height_for_state(
        cfg,
        OccupancyState.STANDING,
        random.Random(),
    )[1] == pytest.approx(cfg.presets.stand)


# ---------------------------------------------------------------------------
# Command handlers (using FakeDeskClient)
# ---------------------------------------------------------------------------


def test_cmd_status(capsys: pytest.CaptureFixture[str]) -> None:
    async def _inner() -> int:
        return await cmd_status(FakeDeskClient(initial_height_m=0.730))

    rc = _run(_inner())
    assert rc == 0
    out = capsys.readouterr().out
    assert "0.730" in out
    assert "height" in out
    assert "speed" in out


def test_cmd_height(capsys: pytest.CaptureFixture[str]) -> None:
    async def _inner() -> int:
        return await cmd_height(FakeDeskClient(initial_height_m=1.125))

    rc = _run(_inner())
    assert rc == 0
    assert "1.125" in capsys.readouterr().out


def test_cmd_speed(capsys: pytest.CaptureFixture[str]) -> None:
    async def _inner() -> int:
        return await cmd_speed(FakeDeskClient())

    rc = _run(_inner())
    assert rc == 0
    out = capsys.readouterr().out
    # FakeDeskClient returns 0.0 m/s
    assert "0.000" in out


def test_cmd_preset_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    async def _inner() -> int:
        return await cmd_preset(FakeDeskClient(), "stand", 1.125, execute=False)

    rc = _run(_inner())
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "1.125" in out


def test_cmd_preset_execute(capsys: pytest.CaptureFixture[str]) -> None:
    async def _inner() -> tuple[int, float]:
        client = FakeDeskClient(initial_height_m=0.730)
        rc = await cmd_preset(client, "stand", 1.125, execute=True)
        # After move, client should be disconnected (context manager exited)
        # Reconnect to verify height
        await client.connect()
        h = await client.get_height()
        await client.disconnect()
        return rc, h

    rc, h = _run(_inner())
    assert rc == 0
    assert h == pytest.approx(1.125, abs=1e-6)


def test_cmd_preset_execute_plays_alert_before_move() -> None:
    class RecordingMotionSound:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True

    class RecordingAlert:
        def __init__(self) -> None:
            self.played = False
            self.motion_sound = RecordingMotionSound()

        def play(self) -> None:
            self.played = True

        def start_motion_sound(self) -> RecordingMotionSound:
            return self.motion_sound

    async def _inner() -> tuple[RecordingAlert, float]:
        alert = RecordingAlert()
        client = FakeDeskClient(initial_height_m=0.730)
        await cmd_preset(
            FakeDeskClient(initial_height_m=0.730),
            "stand",
            1.125,
            execute=True,
            alert=alert,
        )
        await client.connect()
        height = await client.get_height()
        await client.disconnect()
        return alert, height

    alert, _height = _run(_inner())
    assert alert.played is True
    assert alert.motion_sound.stopped is True


def test_dispatch_preset_dry_run_does_not_require_address(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    args = parser.parse_args(["preset", "stand"])
    rc = _run(_dispatch(args, AppConfig()))
    assert rc == 0
    assert "dry-run" in capsys.readouterr().out


def test_cmd_preset_execute_already_at_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def _inner() -> int:
        return await cmd_preset(
            FakeDeskClient(initial_height_m=1.125),
            "stand",
            1.125,
            execute=True,
            final_tolerance_m=0.010,
        )

    rc = _run(_inner())
    assert rc == 0
    assert "already at" in capsys.readouterr().out


def test_cmd_preset_execute_readback_mismatch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class BadMoveDesk(FakeDeskClient):
        async def move_to(self, height_m: float) -> None:
            await super().move_to(height_m - 0.100)

    async def _inner() -> int:
        return await cmd_preset(
            BadMoveDesk(initial_height_m=0.730),
            "stand",
            1.125,
            execute=True,
            final_tolerance_m=0.010,
        )

    rc = _run(_inner())
    assert rc == 2
    assert "expected" in capsys.readouterr().err


def test_cmd_monitor_moves_away_then_back_to_sitting(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = AppConfig(
        automation=AutomationConfig(
            sustained_state_seconds=10.0,
            cooldown_seconds=60.0,
            min_confidence=0.5,
            camera_required=True,
            sample_interval_seconds=0.001,
        )
    )
    samples = iter(
        [
            (DepthMetrics(available=True), Observation(OccupancyState.AWAY, 0.9, timestamp=1.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.AWAY, 0.9, timestamp=12.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=13.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=24.0)),
        ]
    )
    client = FakeDeskClient(initial_height_m=cfg.presets.sit)

    async def _inner() -> tuple[int, float]:
        rc = await cmd_monitor(
            client,
            cfg,
            execute=True,
            sample_interval_seconds=0.001,
            max_samples=4,
            rng=random.Random(2),
            observation_sampler=lambda: next(samples),
        )
        await client.connect()
        height = await client.get_height()
        await client.disconnect()
        return rc, height

    rc, height = _run(_inner())
    assert rc == 0
    assert height == pytest.approx(cfg.presets.sit)
    out = capsys.readouterr().out
    assert "moving to away" in out
    assert "moving to sit" in out


def test_cmd_monitor_already_near_does_not_start_false_cooldown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = AppConfig(
        automation=AutomationConfig(
            sustained_state_seconds=10.0,
            cooldown_seconds=60.0,
            min_confidence=0.5,
            camera_required=True,
            sample_interval_seconds=0.001,
        )
    )
    samples = iter(
        [
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=1.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=12.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=13.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=24.0)),
        ]
    )
    client = FakeDeskClient(initial_height_m=cfg.presets.sit)

    async def _inner() -> tuple[int, float]:
        rc = await cmd_monitor(
            client,
            cfg,
            execute=True,
            sample_interval_seconds=0.001,
            max_samples=4,
            rng=random.Random(2),
            observation_sampler=lambda: next(samples),
        )
        await client.connect()
        height = await client.get_height()
        await client.disconnect()
        return rc, height

    rc, height = _run(_inner())
    assert rc == 0
    assert height == pytest.approx(cfg.presets.stand)
    out = capsys.readouterr().out
    assert "already near sit" in out
    assert "moving to stand" in out
    assert "cooldown active" not in out


def test_cmd_monitor_can_stop_after_first_move(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = AppConfig(
        automation=AutomationConfig(
            sustained_state_seconds=10.0,
            cooldown_seconds=60.0,
            min_confidence=0.5,
            camera_required=True,
            sample_interval_seconds=0.001,
        )
    )
    samples = iter(
        [
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=1.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=12.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=13.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=24.0)),
        ]
    )
    client = FakeDeskClient(initial_height_m=cfg.presets.sit)

    async def _inner() -> tuple[int, float]:
        rc = await cmd_monitor(
            client,
            cfg,
            execute=True,
            stop_after_first_move=True,
            sample_interval_seconds=0.001,
            max_samples=4,
            rng=random.Random(2),
            observation_sampler=lambda: next(samples),
        )
        await client.connect()
        height = await client.get_height()
        await client.disconnect()
        return rc, height

    rc, height = _run(_inner())
    assert rc == 0
    assert height == pytest.approx(cfg.presets.stand)
    out = capsys.readouterr().out
    assert "moving to stand" in out
    assert "moving to sit" not in out
    assert "stopping after first accepted target action" in out


def test_cmd_monitor_blocks_immediate_posture_flip_after_stand(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = AppConfig(
        automation=AutomationConfig(
            sustained_state_seconds=10.0,
            cooldown_seconds=300.0,
            min_confidence=0.5,
            camera_required=True,
            sample_interval_seconds=0.001,
        )
    )
    samples = iter(
        [
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=1.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=12.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=13.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=24.0)),
        ]
    )
    client = FakeDeskClient(initial_height_m=cfg.presets.sit)

    async def _inner() -> tuple[int, float]:
        rc = await cmd_monitor(
            client,
            cfg,
            execute=True,
            sample_interval_seconds=0.001,
            max_samples=4,
            rng=random.Random(2),
            observation_sampler=lambda: next(samples),
        )
        await client.connect()
        height = await client.get_height()
        await client.disconnect()
        return rc, height

    rc, height = _run(_inner())
    assert rc == 0
    assert height == pytest.approx(cfg.presets.stand)
    out = capsys.readouterr().out
    assert "moving to stand" in out
    assert "moving to sit" not in out
    assert "cooldown active" in out


def test_cmd_monitor_blocks_unconfirmed_posture_flip_after_cooldown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = AppConfig(
        automation=AutomationConfig(
            sustained_state_seconds=10.0,
            cooldown_seconds=45.0,
            min_confidence=0.5,
            camera_required=True,
            sample_interval_seconds=0.001,
        )
    )
    samples = iter(
        [
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=1.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=12.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=20.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=55.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.SITTING, 0.9, timestamp=70.0)),
        ]
    )
    client = FakeDeskClient(initial_height_m=cfg.presets.sit)

    async def _inner() -> tuple[int, float]:
        rc = await cmd_monitor(
            client,
            cfg,
            execute=True,
            sample_interval_seconds=0.001,
            max_samples=5,
            rng=random.Random(2),
            observation_sampler=lambda: next(samples),
        )
        await client.connect()
        height = await client.get_height()
        await client.disconnect()
        return rc, height

    rc, height = _run(_inner())
    assert rc == 0
    assert height == pytest.approx(cfg.presets.stand)
    out = capsys.readouterr().out
    assert "moving to stand" in out
    assert "moving to sit" not in out
    assert "awaiting post-move standing confirmation" in out


def test_cmd_monitor_pauses_automation_during_desk_motion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ScriptedStatusDesk(FakeDeskClient):
        def __init__(self) -> None:
            super().__init__(initial_height_m=0.730)
            self.moves: list[float] = []
            self._statuses = iter(
                [
                    DeskStatus(height_m=0.730, speed_ms=0.020),
                    DeskStatus(height_m=0.730, speed_ms=0.000),
                    DeskStatus(height_m=0.730, speed_ms=0.000),
                ]
            )

        async def get_status(self) -> DeskStatus:
            self._require_connected()
            return next(self._statuses)

        async def move_to(self, height_m: float) -> None:
            self.moves.append(height_m)
            await super().move_to(height_m)

    cfg = AppConfig(
        automation=AutomationConfig(
            sustained_state_seconds=0.0,
            cooldown_seconds=0.0,
            min_confidence=0.5,
            camera_required=True,
            sample_interval_seconds=0.001,
            motion_settle_seconds=2.0,
        )
    )
    samples = iter(
        [
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=1.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=2.0)),
            (DepthMetrics(available=True), Observation(OccupancyState.STANDING, 0.9, timestamp=4.1)),
        ]
    )
    client = ScriptedStatusDesk()

    async def _inner() -> int:
        return await cmd_monitor(
            client,
            cfg,
            execute=True,
            sample_interval_seconds=0.001,
            max_samples=3,
            rng=random.Random(2),
            observation_sampler=lambda: next(samples),
        )

    rc = _run(_inner())

    assert rc == 0
    assert client.moves == pytest.approx([cfg.presets.stand])
    out = capsys.readouterr().out
    assert "desk moving" in out
    assert "desk settling" in out
    assert "moving to stand" in out


def test_cmd_monitor_skips_camera_automation_while_manual_override_active(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    override_store = ManualOverrideStore(tmp_path / "manual_override.json")
    override_store.activate("sit")

    def sampler() -> object:
        raise AssertionError("manual override should skip camera sampling")

    async def _inner() -> int:
        return await cmd_monitor(
            FakeDeskClient(),
            AppConfig(
                automation=AutomationConfig(
                    sample_interval_seconds=0.001,
                    sustained_state_seconds=0.0,
                )
            ),
            execute=True,
            max_samples=2,
            observation_sampler=sampler,
            override_store=override_store,
        )

    rc = _run(_inner())

    assert rc == 0
    out = capsys.readouterr().out
    assert "manual override active (sit)" in out
    assert "camera automation skipped" in out
