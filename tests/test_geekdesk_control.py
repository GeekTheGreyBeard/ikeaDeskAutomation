from __future__ import annotations

import pathlib
import subprocess

import yaml

from ikea_desk_automation.config import AppConfig
from ikea_desk_automation.geekdesk_control import (
    SETTINGS_FIELDS,
    LocalInterfaceState,
    ServiceSnapshot,
    SystemdUserService,
    _decode_data_url,
    assess_automation_health,
    monitor_args_is_dry_run,
    read_monitor_args,
    write_monitor_args,
)
from ikea_desk_automation.manual_override import ManualOverrideStore
from ikea_desk_automation.monitor_status import MonitorStatus, MonitorStatusStore
from ikea_desk_automation.webui import config_to_dict


def test_read_monitor_args_defaults_to_dry_run_when_env_missing(tmp_path: pathlib.Path) -> None:
    assert read_monitor_args(tmp_path / "missing.env") == "monitor"


def test_read_monitor_args_parses_service_env(tmp_path: pathlib.Path) -> None:
    env = tmp_path / "service.env"
    env.write_text(
        "# comment\n"
        "IKEA_DESK_BIN=/tmp/ikea-desk\n"
        'IKEA_DESK_MONITOR_ARGS="monitor --sample-interval 2"\n'
    )

    assert read_monitor_args(env) == "monitor --sample-interval 2"


def test_write_monitor_args_updates_existing_service_env(tmp_path: pathlib.Path) -> None:
    env = tmp_path / "service.env"
    env.write_text(
        "IKEA_DESK_BIN=/tmp/ikea-desk\n"
        "IKEA_DESK_MONITOR_ARGS=monitor\n"
    )

    write_monitor_args("monitor --execute", env)

    assert read_monitor_args(env) == "monitor --execute"
    assert "IKEA_DESK_BIN=/tmp/ikea-desk" in env.read_text()


def test_write_monitor_args_creates_service_env(tmp_path: pathlib.Path) -> None:
    env = tmp_path / "nested" / "service.env"

    write_monitor_args("monitor", env)

    assert read_monitor_args(env) == "monitor"


def test_monitor_args_is_dry_run_rejects_execute() -> None:
    assert monitor_args_is_dry_run("monitor")
    assert monitor_args_is_dry_run("monitor --sample-interval 2")
    assert not monitor_args_is_dry_run("monitor --execute")


def test_service_snapshot_mode_buttons_show_active_execute(tmp_path: pathlib.Path) -> None:
    snapshot = ServiceSnapshot(
        active_state="inactive",
        sub_state="dead",
        unit_file_state="disabled",
        dry_run=False,
        monitor_args="monitor --execute",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
    )

    assert snapshot.execute_button_label == "Execute Mode (Active)"
    assert not snapshot.execute_button_enabled
    assert snapshot.dry_run_button_label == "Dry-run Mode"
    assert snapshot.dry_run_button_enabled


def test_service_snapshot_execute_button_explains_running_guard(tmp_path: pathlib.Path) -> None:
    snapshot = ServiceSnapshot(
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        dry_run=True,
        monitor_args="monitor",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
    )

    assert snapshot.execute_button_label == "Execute Mode (Off First)"
    assert not snapshot.execute_button_enabled


def test_service_snapshot_blocks_manual_controls_when_execute_monitor_runs(
    tmp_path: pathlib.Path,
) -> None:
    snapshot = ServiceSnapshot(
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        dry_run=False,
        monitor_args="monitor --execute",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
    )

    assert snapshot.manual_controls_blocked_reason is not None
    assert "Bluetooth connection" in snapshot.manual_controls_blocked_reason


def test_enable_enables_and_starts_service(tmp_path: pathlib.Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "show" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="ActiveState=inactive\nSubState=dead\nUnitFileState=disabled\n",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    env = tmp_path / "service.env"
    env.write_text("IKEA_DESK_MONITOR_ARGS=monitor\n")
    service = SystemdUserService(env_path=env, unit_path=tmp_path / "unit.service", runner=runner)

    snapshot = service.enable()

    assert snapshot.last_error is None
    assert ["systemctl", "--user", "enable", "--now", "ikea-desk-monitor.service"] in calls


def test_enable_allows_intentional_execute_mode(tmp_path: pathlib.Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="ActiveState=inactive\nSubState=dead\nUnitFileState=disabled\n",
            stderr="",
        )

    env = tmp_path / "service.env"
    env.write_text("IKEA_DESK_MONITOR_ARGS=monitor --execute\n")
    service = SystemdUserService(env_path=env, unit_path=tmp_path / "unit.service", runner=runner)

    snapshot = service.enable()

    assert snapshot.last_error is None
    assert ["systemctl", "--user", "enable", "--now", "ikea-desk-monitor.service"] in calls


def test_set_execute_refuses_while_service_is_running(tmp_path: pathlib.Path) -> None:
    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="ActiveState=active\nSubState=running\nUnitFileState=enabled\n",
            stderr="",
        )

    env = tmp_path / "service.env"
    env.write_text("IKEA_DESK_MONITOR_ARGS=monitor\n")
    service = SystemdUserService(env_path=env, unit_path=tmp_path / "unit.service", runner=runner)

    snapshot = service.set_execute()

    assert snapshot.last_error is not None
    assert "Refusing to switch to execute" in snapshot.last_error
    assert read_monitor_args(env) == "monitor"


def test_set_execute_updates_env_when_service_is_inactive(tmp_path: pathlib.Path) -> None:
    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="ActiveState=inactive\nSubState=dead\nUnitFileState=disabled\n",
            stderr="",
        )

    env = tmp_path / "service.env"
    env.write_text("IKEA_DESK_MONITOR_ARGS=monitor\n")
    service = SystemdUserService(env_path=env, unit_path=tmp_path / "unit.service", runner=runner)

    snapshot = service.set_execute()

    assert snapshot.last_error is None
    assert read_monitor_args(env) == "monitor --execute"


def test_set_dry_run_updates_env_and_restarts_running_service(tmp_path: pathlib.Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="ActiveState=active\nSubState=running\nUnitFileState=enabled\n",
            stderr="",
        )

    env = tmp_path / "service.env"
    env.write_text("IKEA_DESK_MONITOR_ARGS=monitor --execute\n")
    service = SystemdUserService(env_path=env, unit_path=tmp_path / "unit.service", runner=runner)

    snapshot = service.set_dry_run()

    assert snapshot.last_error is None
    assert read_monitor_args(env) == "monitor"
    assert ["systemctl", "--user", "restart", "ikea-desk-monitor.service"] in calls


def test_disable_disables_and_stops_service(tmp_path: pathlib.Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "show" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="ActiveState=active\nSubState=running\nUnitFileState=enabled\n",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    service = SystemdUserService(
        env_path=tmp_path / "missing.env",
        unit_path=tmp_path / "unit.service",
        runner=runner,
    )

    snapshot = service.disable()

    assert snapshot.last_error is None
    assert ["systemctl", "--user", "disable", "--now", "ikea-desk-monitor.service"] in calls


def test_snapshot_reports_enabled_and_active_state(tmp_path: pathlib.Path) -> None:
    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="SubState=running\nUnitFileState=enabled\nActiveState=active\n",
            stderr="",
        )

    service = SystemdUserService(
        env_path=tmp_path / "missing.env",
        unit_path=tmp_path / "unit.service",
        runner=runner,
    )

    snapshot = service.snapshot()

    assert snapshot.is_enabled
    assert snapshot.is_running
    assert snapshot.enabled_label == "Auto-start: enabled"


def test_snapshot_reports_runtime_properties(tmp_path: pathlib.Path) -> None:
    def runner(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "SubState=running\n"
                "UnitFileState=enabled\n"
                "ActiveState=active\n"
                "MainPID=1234\n"
                "NRestarts=2\n"
                "ExecMainStartTimestamp=Fri 2026-05-29 17:33:18 MDT\n"
            ),
            stderr="",
        )

    service = SystemdUserService(
        env_path=tmp_path / "missing.env",
        unit_path=tmp_path / "unit.service",
        runner=runner,
    )

    snapshot = service.snapshot()

    assert snapshot.main_pid == 1234
    assert snapshot.n_restarts == "2"
    assert snapshot.exec_main_start_timestamp == "Fri 2026-05-29 17:33:18 MDT"


def test_automation_health_requires_monitor_heartbeat(tmp_path: pathlib.Path) -> None:
    snapshot = ServiceSnapshot(
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        dry_run=True,
        monitor_args="monitor",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
    )

    health = assess_automation_health(snapshot, MonitorStatus(present=False))

    assert not health.live
    assert health.stale
    assert "NOT confirmed live" in health.headline


def test_automation_health_reports_live_sampling(tmp_path: pathlib.Path) -> None:
    snapshot = ServiceSnapshot(
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        dry_run=True,
        monitor_args="monitor",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
        main_pid=1234,
    )
    status = MonitorStatus(
        present=True,
        pid=1234,
        phase="sampling",
        execute=False,
        interval_seconds=2.0,
        updated_at=100.0,
        sample_count=7,
        last_state="away",
        last_decision="desk idle",
    )

    health = assess_automation_health(snapshot, status, now=105.0)

    assert health.live
    assert not health.stale
    assert "Monitor live (Dry-run): sample 7" in health.headline
    assert "Manual desk controls" not in health.detail


def test_automation_health_reports_manual_controls_locked_in_execute(
    tmp_path: pathlib.Path,
) -> None:
    snapshot = ServiceSnapshot(
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        dry_run=False,
        monitor_args="monitor --execute",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
        main_pid=1234,
    )
    status = MonitorStatus(
        present=True,
        pid=1234,
        phase="sampling",
        execute=True,
        interval_seconds=2.0,
        updated_at=100.0,
        sample_count=7,
        last_state="sitting",
        last_decision="desk idle",
    )

    health = assess_automation_health(snapshot, status, now=105.0)

    assert health.live
    assert "Manual desk controls are locked" in health.detail


def test_automation_health_reports_manual_override_as_paused(
    tmp_path: pathlib.Path,
) -> None:
    snapshot = ServiceSnapshot(
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        dry_run=False,
        monitor_args="monitor --execute",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
        main_pid=1234,
    )
    status = MonitorStatus(
        present=True,
        pid=1234,
        phase="sampling",
        execute=True,
        interval_seconds=2.0,
        updated_at=100.0,
        sample_count=8,
        last_state="sitting",
        last_decision="camera automation skipped",
        message="manual override active (stand)",
    )

    health = assess_automation_health(snapshot, status, now=101.0)

    assert not health.live
    assert not health.stale
    assert "Automation paused (Execute): manual override active" in health.headline
    assert "Clear Manual Override" in health.detail


def test_automation_health_reports_pending_restart_on_execute_divergence(
    tmp_path: pathlib.Path,
) -> None:
    snapshot = ServiceSnapshot(
        active_state="active",
        sub_state="running",
        unit_file_state="enabled",
        dry_run=False,
        monitor_args="monitor --execute",
        env_path=tmp_path / "service.env",
        env_exists=True,
        unit_path=tmp_path / "unit.service",
        unit_exists=True,
        main_pid=1234,
    )
    status = MonitorStatus(
        present=True,
        pid=1234,
        phase="sampling",
        execute=False,
        interval_seconds=2.0,
        updated_at=100.0,
        sample_count=3,
    )

    health = assess_automation_health(snapshot, status, now=101.0)

    assert not health.live
    assert health.stale
    assert "restart is pending" in health.detail


def test_local_interface_state_saves_validated_config(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "config.yaml"
    state = LocalInterfaceState(config_path=path, fake=True)
    payload = config_to_dict(AppConfig())
    payload["desk"]["name"] = "geekdesk"
    payload["camera"]["sample_frames"] = 4

    saved = state.save_config_dict(payload)

    assert saved.desk.name == "geekdesk"
    assert state.load_config_dict()["camera"]["sample_frames"] == 4
    written = yaml.safe_load(path.read_text())
    assert written["desk"]["name"] == "geekdesk"


def test_local_interface_state_fake_status_is_read_only(tmp_path: pathlib.Path) -> None:
    state = LocalInterfaceState(config_path=tmp_path / "missing.yaml", fake=True)

    status = state.read_status()

    assert status is not None
    assert status.height_m == AppConfig().presets.sit
    assert status.speed_ms == 0.0


def test_local_interface_state_reads_monitor_status(tmp_path: pathlib.Path) -> None:
    status_path = tmp_path / "monitor_status.json"
    store = MonitorStatusStore(status_path)
    store.write(
        MonitorStatus(
            present=True,
            pid=55,
            phase="sampling",
            execute=False,
            interval_seconds=2.0,
            updated_at=100.0,
            sample_count=2,
        )
    )
    state = LocalInterfaceState(
        config_path=tmp_path / "missing.yaml",
        fake=True,
        status_store=store,
    )

    assert state.read_monitor_status().sample_count == 2


def test_manual_mode_dry_run_sets_persistent_override_without_moving(
    tmp_path: pathlib.Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    state = LocalInterfaceState(
        config_path=config_path,
        fake=True,
        override_store=ManualOverrideStore(tmp_path / "manual_override.json"),
    )
    payload = config_to_dict(AppConfig())
    payload["presets"]["stand"] = 1.111
    state.save_config_dict(payload)

    result = state.activate_manual_mode("stand", execute=False)

    assert result.override.active
    assert result.override.mode == "stand"
    assert result.moved is False
    assert result.target_m == 1.111
    assert "Dry-run mode" in result.message

    restarted_state = LocalInterfaceState(
        config_path=config_path,
        fake=True,
        override_store=ManualOverrideStore(tmp_path / "manual_override.json"),
    )
    assert restarted_state.read_manual_override().active
    assert restarted_state.read_manual_override().mode == "stand"


def test_manual_mode_blocks_second_selection_until_override_is_cleared(
    tmp_path: pathlib.Path,
) -> None:
    state = LocalInterfaceState(
        config_path=tmp_path / "config.yaml",
        fake=True,
        override_store=ManualOverrideStore(tmp_path / "manual_override.json"),
    )

    first = state.activate_manual_mode("sit", execute=False)
    second = state.activate_manual_mode("stand", execute=False)

    assert first.override.active
    assert first.override.mode == "sit"
    assert not first.blocked
    assert second.blocked
    assert second.moved is False
    assert "Clear Manual Override" in second.message
    assert state.read_manual_override().mode == "sit"


def test_clear_manual_override_deactivates_persistent_latch(tmp_path: pathlib.Path) -> None:
    state = LocalInterfaceState(
        config_path=tmp_path / "config.yaml",
        fake=True,
        override_store=ManualOverrideStore(tmp_path / "manual_override.json"),
    )
    state.activate_manual_mode("away", execute=False)

    cleared = state.clear_manual_override()

    assert not cleared.active
    assert not state.read_manual_override().active


def test_settings_fields_cover_serialized_config_fields() -> None:
    data = config_to_dict(AppConfig())
    expected = {(section, key) for section, values in data.items() for key in values}
    actual = {(field.section, field.key) for field in SETTINGS_FIELDS}

    assert expected <= actual


def test_decode_data_url_rejects_invalid_payload() -> None:
    assert _decode_data_url("data:image/bmp;base64,QUJD") == b"ABC"
    assert _decode_data_url("not-a-data-url") is None
    assert _decode_data_url("data:image/bmp;base64,%%") is None
