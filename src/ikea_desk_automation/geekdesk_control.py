"""Wayland-friendly desktop control for the local GeekDesk interface."""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import pathlib
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ikea_desk_automation.config import DEFAULT_CONFIG_PATH, AppConfig
from ikea_desk_automation.manual_override import (
    MANUAL_OVERRIDE_MODES,
    ManualOverride,
    ManualOverrideStore,
)
from ikea_desk_automation.monitor_status import (
    PHASE_CONNECTING,
    PHASE_SAMPLING,
    PHASE_STOPPED,
    MonitorStatus,
    MonitorStatusStore,
)
from ikea_desk_automation.webui import WebUiState, config_to_dict


APP_NAME = "GeekDesk Control"
SERVICE_NAME = "ikea-desk-monitor.service"
SERVICE_ENV_PATH = pathlib.Path.home() / ".config" / "ikea-desk" / "service.env"
SYSTEMD_UNIT_PATH = pathlib.Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ManualActionResult:
    """Outcome from one native Sit/Stand/Away button press.

    The manual button path always writes the persistent override first. In
    dry-run mode, that is the whole action; in execute mode, the same validated
    preset movement path used by the CLI is requested after the latch is active.
    """

    override: ManualOverride
    preset_name: str
    target_m: float
    execute: bool
    moved: bool
    message: str
    blocked: bool = False


@dataclass(frozen=True)
class SettingsField:
    section: str
    key: str
    label: str
    value_type: str


SETTINGS_FIELDS: tuple[SettingsField, ...] = (
    SettingsField("desk", "name", "Name", "text"),
    SettingsField("desk", "address", "BLE address", "text"),
    SettingsField("desk", "min_height_m", "Minimum height (m)", "float"),
    SettingsField("desk", "max_height_m", "Maximum height (m)", "float"),
    SettingsField("presets", "sit", "Sit height (m)", "float"),
    SettingsField("presets", "stand", "Stand height (m)", "float"),
    SettingsField("presets", "away", "Away height (m)", "float"),
    SettingsField("automation", "sustained_state_seconds", "Sustained state (s)", "float"),
    SettingsField("automation", "cooldown_seconds", "Cooldown (s)", "float"),
    SettingsField("automation", "camera_required", "Require camera", "bool"),
    SettingsField("automation", "min_confidence", "Minimum confidence", "float"),
    SettingsField("automation", "movement_timeout_seconds", "Movement timeout (s)", "float"),
    SettingsField("automation", "final_tolerance_m", "Final tolerance (m)", "float"),
    SettingsField("automation", "sample_interval_seconds", "Sample interval (s)", "float"),
    SettingsField("automation", "motion_speed_threshold_ms", "Motion speed threshold (m/s)", "float"),
    SettingsField(
        "automation",
        "motion_height_delta_threshold_m",
        "Motion height delta threshold (m)",
        "float",
    ),
    SettingsField("automation", "motion_settle_seconds", "Motion settle (s)", "float"),
    SettingsField("audio", "enabled", "Audio enabled", "bool"),
    SettingsField("audio", "volume_percent", "Volume (%)", "int"),
    SettingsField("camera", "enabled", "Camera enabled", "bool"),
    SettingsField("camera", "serial", "Serial", "text"),
    SettingsField("camera", "width", "Width", "int"),
    SettingsField("camera", "height", "Height", "int"),
    SettingsField("camera", "fps", "FPS", "int"),
    SettingsField("camera", "warmup_frames", "Warmup frames", "int"),
    SettingsField("camera", "sample_frames", "Sample frames", "int"),
    SettingsField("camera", "roi_left", "ROI left", "float"),
    SettingsField("camera", "roi_top", "ROI top", "float"),
    SettingsField("camera", "roi_right", "ROI right", "float"),
    SettingsField("camera", "roi_bottom", "ROI bottom", "float"),
    SettingsField("camera", "min_depth_m", "Minimum depth (m)", "float"),
    SettingsField("camera", "max_depth_m", "Maximum depth (m)", "float"),
    SettingsField("camera", "obstruction_distance_m", "Obstruction distance (m)", "float"),
    SettingsField("camera", "min_foreground_ratio", "Minimum foreground ratio", "float"),
    SettingsField("camera", "standing_top_y", "Standing top-y", "float"),
    SettingsField("camera", "min_confidence", "Minimum confidence", "float"),
)


@dataclass(frozen=True)
class ServiceSnapshot:
    """Point-in-time view of systemd, service.env, and installed unit state."""

    active_state: str
    sub_state: str
    unit_file_state: str
    dry_run: bool
    monitor_args: str
    env_path: pathlib.Path
    env_exists: bool
    unit_path: pathlib.Path
    unit_exists: bool
    last_error: str | None = None
    main_pid: int | None = None
    n_restarts: str | None = None
    exec_main_start_timestamp: str | None = None

    @property
    def is_running(self) -> bool:
        return self.active_state == "active"

    @property
    def is_enabled(self) -> bool:
        return self.unit_file_state == "enabled"

    @property
    def status_label(self) -> str:
        if self.active_state == "unknown":
            return "Unknown"
        if self.sub_state and self.sub_state != self.active_state:
            return f"{self.active_state} ({self.sub_state})"
        return self.active_state

    @property
    def dry_run_label(self) -> str:
        if self.dry_run:
            return "Dry-run mode: monitor only, no --execute"
        return "Execute mode: monitor service can move the desk"

    @property
    def dry_run_button_label(self) -> str:
        if self.dry_run:
            return "Dry-run Mode (Active)"
        return "Dry-run Mode"

    @property
    def execute_button_label(self) -> str:
        if not self.dry_run:
            return "Execute Mode (Active)"
        if self.is_running:
            return "Execute Mode (Off First)"
        return "Execute Mode"

    @property
    def dry_run_button_enabled(self) -> bool:
        return not self.dry_run

    @property
    def execute_button_enabled(self) -> bool:
        return self.dry_run and not self.is_running

    @property
    def enabled_label(self) -> str:
        if self.unit_file_state == "unknown":
            return "Auto-start: unknown"
        return f"Auto-start: {self.unit_file_state}"

    @property
    def manual_controls_blocked_reason(self) -> str | None:
        if self.is_running and not self.dry_run:
            return (
                "Manual desk controls are locked while Execute automation is running. "
                "Turn automation Off first so the monitor releases the desk Bluetooth connection."
            )
        return None


@dataclass(frozen=True)
class AutomationHealth:
    """Honest assessment combining systemd state with the monitor heartbeat."""

    live: bool
    stale: bool
    headline: str
    detail: str


def assess_automation_health(
    snapshot: ServiceSnapshot,
    status: MonitorStatus,
    now: float | None = None,
) -> AutomationHealth:
    """Decide whether automation is actually live, not just whether the unit is active.

    systemd reports the unit active while the monitor process exists, even if it
    is stuck before sampling. We only call automation live when a fresh monitor
    heartbeat proves it is sampling.
    """
    if not snapshot.is_running:
        return AutomationHealth(
            live=False,
            stale=False,
            headline="Automation off - monitor service is not running.",
            detail="Use On to start the monitor service.",
        )

    configured_execute = not snapshot.dry_run
    configured_mode = "Execute" if configured_execute else "Dry-run"

    if not status.present:
        return AutomationHealth(
            live=False,
            stale=True,
            headline=(
                f"Service running ({configured_mode}) but the monitor has not reported a "
                "heartbeat - automation is NOT confirmed live."
            ),
            detail=(
                "No monitor heartbeat found. The monitor may be starting, or stuck "
                "before it can sample (for example blocked connecting to the desk)."
            ),
        )

    running_mode = "Execute" if status.execute else "Dry-run"
    pid_mismatch = (
        snapshot.main_pid is not None
        and status.pid is not None
        and snapshot.main_pid != status.pid
    )
    mode_mismatch = configured_execute != status.execute
    pending_restart = ""
    if mode_mismatch:
        pending_restart = (
            f" Service env is {configured_mode}, but running monitor reports {running_mode}; "
            "restart is pending."
        )
    if pid_mismatch:
        pending_restart += (
            f" Heartbeat PID {status.pid} does not match service PID {snapshot.main_pid}."
        )

    age = status.age_seconds(now)
    age_text = f"{age:.0f}s ago" if age is not None else "time unknown"

    if status.is_fresh(now):
        if status.phase == PHASE_SAMPLING:
            state = status.last_state or "unknown"
            decision = status.last_decision or "evaluating"
            if "manual override active" in (status.message or "").lower():
                return AutomationHealth(
                    live=False,
                    stale=False,
                    headline=(
                        f"Automation paused ({running_mode}): manual override active; "
                        f"sample {status.sample_count}, {state} - {decision}."
                    ),
                    detail=(
                        f"Last heartbeat {age_text}. Clear Manual Override to resume "
                        f"camera automation.{pending_restart}"
                    ),
                )
            return AutomationHealth(
                live=not mode_mismatch and not pid_mismatch,
                stale=mode_mismatch or pid_mismatch,
                headline=(
                    f"Monitor live ({running_mode}): sample {status.sample_count}, "
                    f"{state} - {decision}."
                ),
                detail=(
                    f"Last heartbeat {age_text}.{pending_restart}"
                    if running_mode == "Dry-run"
                    else (
                        f"Last heartbeat {age_text}. Manual desk controls are locked "
                        f"while the execute monitor owns the desk Bluetooth connection."
                        f"{pending_restart}"
                    )
                ),
            )
        return AutomationHealth(
            live=False,
            stale=False,
            headline=f"Monitor starting ({running_mode}): {status.phase}.",
            detail=(
                f"{status.message or status.phase} "
                f"(last heartbeat {age_text}).{pending_restart}"
            ),
        )

    phase_hint = ""
    if status.phase == PHASE_CONNECTING:
        phase_hint = " It was still connecting to the desk - the desk may be unreachable."
    elif status.phase == PHASE_STOPPED:
        phase_hint = " The monitor reported it stopped."
    return AutomationHealth(
        live=False,
        stale=True,
        headline=(
            f"Service running ({configured_mode}) but the monitor heartbeat is stale "
            f"({age_text}) - automation is NOT confirmed live; the monitor may be stuck."
        ),
        detail=(
            f"Last phase {status.phase}, {status.sample_count} sample(s)."
            f"{phase_hint}{pending_restart}"
        ),
    )


class SystemdUserService:
    """Small wrapper around the user-level monitor service controls.

    The mode guard is intentionally asymmetric: switching back to dry-run can
    restart a running service immediately, but switching into execute mode is
    refused while the service is active so movement-capable behavior cannot be
    enabled behind an already-running process.
    """

    def __init__(
        self,
        *,
        service_name: str = SERVICE_NAME,
        env_path: pathlib.Path = SERVICE_ENV_PATH,
        unit_path: pathlib.Path = SYSTEMD_UNIT_PATH,
        runner: Runner = subprocess.run,
    ) -> None:
        self.service_name = service_name
        self.env_path = env_path
        self.unit_path = unit_path
        self._runner = runner

    def snapshot(self) -> ServiceSnapshot:
        """Read service state and local monitor args without changing anything."""

        active_state = "unknown"
        sub_state = "unknown"
        unit_file_state = "unknown"
        last_error = None
        main_pid = None
        n_restarts = None
        exec_main_start_timestamp = None
        result = self._systemctl(
            "show",
            self.service_name,
            "--property=ActiveState",
            "--property=SubState",
            "--property=UnitFileState",
            "--property=MainPID",
            "--property=NRestarts",
            "--property=ExecMainStartTimestamp",
            check=False,
        )
        if result.returncode == 0:
            properties = _parse_systemctl_show(result.stdout)
            active_state = properties.get("ActiveState") or "unknown"
            sub_state = properties.get("SubState") or active_state
            unit_file_state = properties.get("UnitFileState") or "unknown"
            main_pid = _parse_optional_int(properties.get("MainPID"))
            n_restarts = properties.get("NRestarts") or None
            exec_main_start_timestamp = properties.get("ExecMainStartTimestamp") or None
        else:
            last_error = _clean_process_error(result)

        monitor_args = read_monitor_args(self.env_path)
        dry_run = monitor_args_is_dry_run(monitor_args)
        return ServiceSnapshot(
            active_state=active_state,
            sub_state=sub_state,
            unit_file_state=unit_file_state,
            dry_run=dry_run,
            monitor_args=monitor_args,
            env_path=self.env_path,
            env_exists=self.env_path.exists(),
            unit_path=self.unit_path,
            unit_exists=self.unit_path.exists(),
            last_error=last_error,
            main_pid=main_pid,
            n_restarts=n_restarts,
            exec_main_start_timestamp=exec_main_start_timestamp,
        )

    def enable(self) -> ServiceSnapshot:
        """Enable and start the monitor service with the currently configured mode."""

        result = self._systemctl("enable", "--now", self.service_name, check=False)
        after = self.snapshot()
        if result.returncode != 0:
            return replace(after, last_error=_clean_process_error(result))
        return after

    def disable(self) -> ServiceSnapshot:
        """Disable and stop the monitor service."""

        result = self._systemctl("disable", "--now", self.service_name, check=False)
        after = self.snapshot()
        if result.returncode != 0:
            return replace(after, last_error=_clean_process_error(result))
        return after

    def set_dry_run(self) -> ServiceSnapshot:
        """Persist dry-run mode and restart an active service into the safer mode."""

        write_monitor_args("monitor", self.env_path)
        before = self.snapshot()
        if before.is_running:
            result = self._systemctl("restart", self.service_name, check=False)
            after = self.snapshot()
            if result.returncode != 0:
                return replace(after, last_error=_clean_process_error(result))
            return after
        return before

    def set_execute(self) -> ServiceSnapshot:
        """Persist execute mode only when the monitor is stopped."""

        before = self.snapshot()
        if before.is_running:
            return replace(
                before,
                last_error=(
                    "Refusing to switch to execute while the monitor is running. "
                    "Turn automation Off first, then select Execute Mode, then turn it On."
                ),
            )
        write_monitor_args("monitor --execute", self.env_path)
        return self.snapshot()

    def _systemctl(self, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        return self._runner(
            ["systemctl", "--user", *args],
            check=check,
            text=True,
            capture_output=True,
        )


class LocalInterfaceState:
    """Shared non-visual state for GeekDesk Control's local UI panels."""

    def __init__(
        self,
        *,
        config_path: pathlib.Path = DEFAULT_CONFIG_PATH,
        fake: bool = False,
        override_store: ManualOverrideStore | None = None,
        status_store: MonitorStatusStore | None = None,
    ) -> None:
        self.config_path = config_path
        self.fake = fake
        self._web_state = WebUiState(config_path=config_path, fake=fake)
        self.override_store = override_store or ManualOverrideStore()
        self.status_store = status_store or MonitorStatusStore()

    def load_config(self) -> AppConfig:
        return self._web_state.load_config()

    def load_config_dict(self) -> dict[str, Any]:
        return config_to_dict(self.load_config())

    def save_config_dict(self, payload: dict[str, Any]) -> AppConfig:
        return self._web_state.save_config(payload)

    def read_status(self) -> Any:
        return self._web_state.read_status()

    def read_monitor_status(self) -> MonitorStatus:
        return self.status_store.read()

    def preview(self, *, sample_camera: bool = False) -> dict[str, Any]:
        return self._web_state.preview(sample_camera=sample_camera)

    def read_manual_override(self) -> ManualOverride:
        return self.override_store.read()

    def clear_manual_override(self) -> ManualOverride:
        return self.override_store.clear()

    def activate_manual_mode(self, mode: str, *, execute: bool) -> ManualActionResult:
        """Activate a manual override and optionally request validated preset movement."""

        if mode not in MANUAL_OVERRIDE_MODES:
            raise ValueError(f"unknown manual mode: {mode}")
        cfg = self.load_config()
        target_m = cfg.presets.values()[mode]
        cfg.desk.validate_height(target_m, f"presets.{mode}")
        existing = self.override_store.read()
        if existing.active:
            selected = existing.mode or "unknown"
            return ManualActionResult(
                override=existing,
                preset_name=mode,
                target_m=target_m,
                execute=execute,
                moved=False,
                blocked=True,
                message=(
                    f"Manual {selected} override is already active. Clear Manual Override "
                    f"before selecting {mode}."
                ),
            )
        override = self.override_store.activate(mode)
        if not execute:
            return ManualActionResult(
                override=override,
                preset_name=mode,
                target_m=target_m,
                execute=False,
                moved=False,
                message=(
                    f"Manual {mode} override active. Dry-run mode: would move to "
                    f"{target_m:.3f} m in Execute Mode."
                ),
            )

        from ikea_desk_automation.cli import cmd_preset  # noqa: PLC0415

        client = self._make_manual_client(cfg)
        alert = None
        if cfg.audio.enabled:
            from ikea_desk_automation.audio import PulseAudioMovementAlert  # noqa: PLC0415

            alert = PulseAudioMovementAlert(volume_percent=cfg.audio.volume_percent)
        asyncio.run(
            cmd_preset(
                client,
                mode,
                target_m,
                execute=True,
                timeout_seconds=cfg.automation.movement_timeout_seconds,
                final_tolerance_m=cfg.automation.final_tolerance_m,
                alert=alert,
            )
        )
        return ManualActionResult(
            override=override,
            preset_name=mode,
            target_m=target_m,
            execute=True,
            moved=True,
            message=f"Manual {mode} override active. Move requested to {target_m:.3f} m.",
        )

    def _make_manual_client(self, cfg: AppConfig):  # type: ignore[no-untyped-def]
        if self.fake:
            from ikea_desk_automation.desk.fake import FakeDeskClient  # noqa: PLC0415

            return FakeDeskClient(initial_height_m=cfg.presets.sit)
        if not cfg.desk.address:
            raise ValueError("No desk address configured; manual movement requires desk.address.")
        from ikea_desk_automation.desk.idasen_client import IdasenDeskClient  # noqa: PLC0415

        return IdasenDeskClient(cfg.desk.address)


def read_monitor_args(path: pathlib.Path = SERVICE_ENV_PATH) -> str:
    if not path.exists():
        return "monitor"
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "IKEA_DESK_MONITOR_ARGS":
            continue
        return _strip_env_value(value.strip())
    return "monitor"


def write_monitor_args(value: str, path: pathlib.Path = SERVICE_ENV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_line = f'IKEA_DESK_MONITOR_ARGS="{value}"'
    if not path.exists():
        path.write_text(f"{new_line}\n")
        return

    lines = path.read_text().splitlines()
    updated = False
    next_lines = []
    for raw_line in lines:
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _value = line.split("=", 1)
            if key.strip() == "IKEA_DESK_MONITOR_ARGS":
                next_lines.append(new_line)
                updated = True
                continue
        next_lines.append(raw_line)
    if not updated:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(new_line)
    path.write_text("\n".join(next_lines) + "\n")


def monitor_args_is_dry_run(value: str) -> bool:
    try:
        args = shlex.split(value)
    except ValueError:
        return False
    return "--execute" not in args


def _parse_systemctl_show(output: str) -> dict[str, str]:
    properties = {}
    for raw_line in output.splitlines():
        key, separator, value = raw_line.partition("=")
        if separator:
            properties[key] = value.strip()
    return properties


def _parse_optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _strip_env_value(value: str) -> str:
    try:
        parts = shlex.split(value)
    except ValueError:
        return value
    return " ".join(parts)


def _clean_process_error(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout or "").strip()
    if message:
        return message
    return f"command exited with status {result.returncode}"


def _decode_data_url(data_url: str) -> bytes | None:
    _prefix, separator, payload = data_url.partition(",")
    if not separator:
        return None
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(prog="geekdesk-control", description=APP_NAME)
    parser.add_argument("--status", action="store_true", help="Print service status and exit.")
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the IKEA desk YAML config.",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use fake read-only desk status in the local interface.",
    )
    parsed = parser.parse_args(args)
    if parsed.status:
        service = SystemdUserService()
        snapshot = service.snapshot()
        health = assess_automation_health(snapshot, MonitorStatusStore().read())
        print(f"{APP_NAME}: {snapshot.enabled_label}; active: {snapshot.status_label}")
        print(snapshot.dry_run_label)
        print(f"Automation: {health.headline}")
        print(health.detail)
        if snapshot.manual_controls_blocked_reason:
            print(f"Manual controls: {snapshot.manual_controls_blocked_reason}")
        if snapshot.last_error:
            print(snapshot.last_error, file=sys.stderr)
            return 1
        return 0
    return run_gui(config_path=parsed.config, fake=parsed.fake)


def run_gui(*, config_path: pathlib.Path = DEFAULT_CONFIG_PATH, fake: bool = False) -> int:
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtWidgets import (
            QApplication,
            QCheckBox,
            QDoubleSpinBox,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QScrollArea,
            QSpinBox,
            QTabWidget,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        print(f"{APP_NAME} requires PyQt6 for the desktop app: {exc}", file=sys.stderr)
        print("Install PyQt6, or run `geekdesk-control --status`.", file=sys.stderr)
        return 1

    class GeekDeskWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.service = SystemdUserService()
            self.local = LocalInterfaceState(config_path=config_path, fake=fake)
            self.config_data: dict[str, Any] = {}
            self.field_widgets: dict[tuple[str, str], QWidget] = {}
            self.manual_mode_buttons: dict[str, QPushButton] = {}
            self.setWindowTitle(APP_NAME)
            self.setMinimumSize(760, 620)

            root = QWidget()
            layout = QVBoxLayout(root)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(12)
            self.setCentralWidget(root)

            title = QLabel(APP_NAME)
            title.setStyleSheet("font-size: 18px; font-weight: 600;")
            layout.addWidget(title)

            self._build_service_controls(layout)
            self._build_manual_controls(layout)

            self.tabs = QTabWidget()
            layout.addWidget(self.tabs, 1)
            self._build_settings_tab()
            self._build_readout_tab()

            self.refresh()
            self.reload_config()
            self.refresh_camera(sample=False)

        def _build_service_controls(self, layout: QVBoxLayout) -> None:
            self.state_label = QLabel("")
            layout.addWidget(self.state_label)

            self.enabled_label = QLabel("")
            layout.addWidget(self.enabled_label)

            self.dry_run_label = QLabel("")
            self.dry_run_label.setWordWrap(True)
            layout.addWidget(self.dry_run_label)

            button_row = QHBoxLayout()
            layout.addLayout(button_row)

            self.on_button = QPushButton("On - Enable Automation")
            self.on_button.clicked.connect(self._on_enable_clicked)
            button_row.addWidget(self.on_button)

            self.off_button = QPushButton("Off - Disable Automation")
            self.off_button.clicked.connect(self._on_disable_clicked)
            button_row.addWidget(self.off_button)

            mode_row = QHBoxLayout()
            layout.addLayout(mode_row)

            self.dry_run_button = QPushButton("Dry-run Mode")
            self.dry_run_button.clicked.connect(self._on_dry_run_clicked)
            mode_row.addWidget(self.dry_run_button)

            self.execute_button = QPushButton("Execute Mode")
            self.execute_button.clicked.connect(self._on_execute_clicked)
            mode_row.addWidget(self.execute_button)

            self.refresh_button = QPushButton("Refresh")
            self.refresh_button.clicked.connect(self._on_refresh_clicked)
            layout.addWidget(self.refresh_button)

            self.detail_label = QLabel("")
            self.detail_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.detail_label.setWordWrap(True)
            layout.addWidget(self.detail_label)

        def _build_manual_controls(self, layout: QVBoxLayout) -> None:
            group = QGroupBox("Manual Desk Controls")
            group_layout = QVBoxLayout(group)
            layout.addWidget(group)

            self.manual_override_label = QLabel("")
            self.manual_override_label.setWordWrap(True)
            group_layout.addWidget(self.manual_override_label)

            manual_row = QHBoxLayout()
            group_layout.addLayout(manual_row)

            self.sit_button = QPushButton("Sit")
            self.sit_button.setCheckable(True)
            self.sit_button.clicked.connect(lambda: self._on_manual_clicked("sit"))
            manual_row.addWidget(self.sit_button)

            self.stand_button = QPushButton("Stand")
            self.stand_button.setCheckable(True)
            self.stand_button.clicked.connect(lambda: self._on_manual_clicked("stand"))
            manual_row.addWidget(self.stand_button)

            self.away_button = QPushButton("Away")
            self.away_button.setCheckable(True)
            self.away_button.clicked.connect(lambda: self._on_manual_clicked("away"))
            manual_row.addWidget(self.away_button)
            self.manual_mode_buttons = {
                "sit": self.sit_button,
                "stand": self.stand_button,
                "away": self.away_button,
            }

            self.clear_manual_button = QPushButton("Clear Manual Override")
            self.clear_manual_button.clicked.connect(self._on_clear_manual_clicked)
            manual_row.addWidget(self.clear_manual_button)

            self.manual_message_label = QLabel("")
            self.manual_message_label.setWordWrap(True)
            group_layout.addWidget(self.manual_message_label)

        def _build_settings_tab(self) -> None:
            tab = QWidget()
            outer = QVBoxLayout(tab)
            top_row = QHBoxLayout()
            outer.addLayout(top_row)

            self.config_path_label = QLabel("")
            self.config_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            top_row.addWidget(self.config_path_label, 1)

            reload_button = QPushButton("Reload Config")
            reload_button.clicked.connect(self.reload_config)
            top_row.addWidget(reload_button)

            save_button = QPushButton("Save Settings")
            save_button.clicked.connect(self.save_config)
            top_row.addWidget(save_button)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            outer.addWidget(scroll, 1)
            body = QWidget()
            body_layout = QVBoxLayout(body)
            scroll.setWidget(body)

            for section in ("desk", "presets", "automation", "audio", "camera"):
                group = QGroupBox(section.title())
                form = QFormLayout(group)
                for field in SETTINGS_FIELDS:
                    if field.section != section:
                        continue
                    widget = self._make_field_widget(field)
                    self.field_widgets[(field.section, field.key)] = widget
                    form.addRow(field.label, widget)
                body_layout.addWidget(group)
            body_layout.addStretch(1)

            self.settings_message = QLabel("")
            self.settings_message.setWordWrap(True)
            outer.addWidget(self.settings_message)
            self.tabs.addTab(tab, "Settings")

        def _build_readout_tab(self) -> None:
            tab = QWidget()
            outer = QVBoxLayout(tab)

            action_row = QHBoxLayout()
            outer.addLayout(action_row)
            desk_button = QPushButton("Refresh Desk Status")
            desk_button.clicked.connect(self.refresh_desk_status)
            action_row.addWidget(desk_button)
            camera_button = QPushButton("Check Camera")
            camera_button.clicked.connect(lambda: self.refresh_camera(sample=False))
            action_row.addWidget(camera_button)
            sample_button = QPushButton("Sample Camera")
            sample_button.clicked.connect(lambda: self.refresh_camera(sample=True))
            action_row.addWidget(sample_button)
            action_row.addStretch(1)

            self.readout_label = QLabel("")
            self.readout_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.readout_label.setWordWrap(True)
            outer.addWidget(self.readout_label)

            self.preview_label = QLabel("No camera sample loaded.")
            self.preview_label.setMinimumHeight(280)
            self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_label.setStyleSheet("border: 1px solid #b9c2bb; background: #18231f; color: #d6e3dc;")
            outer.addWidget(self.preview_label, 1)
            self.tabs.addTab(tab, "Readout")

        def _make_field_widget(self, field: SettingsField) -> QWidget:
            if field.value_type == "bool":
                return QCheckBox()
            if field.value_type == "int":
                widget = QSpinBox()
                widget.setRange(0, 10000)
                if field.key == "volume_percent":
                    widget.setRange(0, 100)
                return widget
            if field.value_type == "float":
                widget = QDoubleSpinBox()
                widget.setRange(-1000.0, 1000.0)
                widget.setDecimals(4)
                widget.setSingleStep(0.01)
                return widget
            return QLineEdit()

        def _on_enable_clicked(self) -> None:
            self.refresh(self.service.enable())

        def _on_disable_clicked(self) -> None:
            self.refresh(self.service.disable())

        def _on_dry_run_clicked(self) -> None:
            self.refresh(self.service.set_dry_run())

        def _on_execute_clicked(self) -> None:
            snapshot = self.service.set_execute()
            self.refresh(snapshot)
            if snapshot.last_error:
                QMessageBox.warning(self, APP_NAME, snapshot.last_error)

        def _on_refresh_clicked(self) -> None:
            self.refresh()

        def _on_manual_clicked(self, mode: str) -> None:
            snapshot = self.service.snapshot()
            if not snapshot.dry_run and snapshot.is_running:
                message = (
                    "Turn automation Off before using manual movement controls. "
                    "The monitor service owns the desk Bluetooth connection while it is running."
                )
                self.manual_message_label.setText(message)
                QMessageBox.warning(self, APP_NAME, message)
                self.refresh(snapshot)
                return
            for button in self.manual_mode_buttons.values():
                button.setEnabled(False)
            try:
                result = self.local.activate_manual_mode(mode, execute=not snapshot.dry_run)
            except Exception as exc:  # noqa: BLE001
                self.manual_message_label.setText(f"Manual {mode} failed: {exc}")
                QMessageBox.warning(self, APP_NAME, str(exc))
                self.refresh(snapshot)
                return
            self.manual_message_label.setText(result.message)
            self.refresh(snapshot)

        def _on_clear_manual_clicked(self) -> None:
            self.local.clear_manual_override()
            self.manual_message_label.setText("Manual override cleared. Camera automation may resume.")
            self.refresh()

        def reload_config(self) -> None:
            try:
                self.config_data = self.local.load_config_dict()
            except Exception as exc:  # noqa: BLE001
                self.settings_message.setText(f"Config load failed: {exc}")
                return
            self.config_path_label.setText(f"Config: {self.local.config_path}")
            for field in SETTINGS_FIELDS:
                widget = self.field_widgets[(field.section, field.key)]
                value = self.config_data[field.section][field.key]
                self._set_widget_value(widget, field, value)
            self.settings_message.setText("Config loaded.")

        def save_config(self) -> None:
            try:
                payload = self._read_settings_payload()
                cfg = self.local.save_config_dict(payload)
            except Exception as exc:  # noqa: BLE001
                self.settings_message.setText(f"Save failed: {exc}")
                QMessageBox.warning(self, APP_NAME, str(exc))
                return
            self.config_data = config_to_dict(cfg)
            self.settings_message.setText(f"Saved {self.local.config_path}")

        def refresh_desk_status(self) -> None:
            status = self.local.read_status()
            if status is None:
                self.readout_label.setText("Desk status: unavailable or no desk address configured.")
                return
            self.readout_label.setText(
                f"Desk height: {status.height_m:.3f} m\nDesk speed: {status.speed_ms:.3f} m/s"
            )

        def refresh_camera(self, *, sample: bool) -> None:
            preview = self.local.preview(sample_camera=sample)
            camera = preview["camera"]
            lines = [
                f"Camera: {'available' if camera['available'] else 'unavailable'}",
                f"Devices: {camera['device_count']}",
            ]
            if camera["message"]:
                lines.append(f"Message: {camera['message']}")
            metrics = preview.get("metrics")
            if metrics:
                lines.extend(
                    [
                        f"Sample: {'available' if metrics['available'] else 'unavailable'}",
                        f"Nearest: {self._format_optional_float(metrics['nearest_m'], 'm')}",
                        f"Foreground: {self._format_optional_float(metrics['foreground_ratio'], '')}",
                        f"Top-y: {self._format_optional_float(metrics['top_y'], '')}",
                        f"Centroid-y: {self._format_optional_float(metrics['centroid_y'], '')}",
                    ]
                )
                if metrics["message"]:
                    lines.append(f"Sample message: {metrics['message']}")
                self._set_preview_image(metrics.get("image_data_url") or "")
            elif sample:
                self.preview_label.setText("No camera image returned.")
            observation = preview.get("observation")
            if observation:
                lines.append(
                    f"Observation: {observation['state']} ({observation['confidence']:.2f})"
                )
            self.readout_label.setText("\n".join(lines))

        def refresh(self, snapshot: ServiceSnapshot | None = None) -> None:
            snapshot = snapshot or self.service.snapshot()
            health = assess_automation_health(snapshot, self.local.read_monitor_status())
            self.enabled_label.setText(snapshot.enabled_label)
            self.state_label.setText(f"Automation: {health.headline}")
            self.dry_run_label.setText(snapshot.dry_run_label)
            self.on_button.setEnabled(snapshot.active_state != "unknown")
            self.off_button.setEnabled(snapshot.active_state != "unknown")
            self.dry_run_button.setText(snapshot.dry_run_button_label)
            self.execute_button.setText(snapshot.execute_button_label)
            self.dry_run_button.setEnabled(snapshot.dry_run_button_enabled)
            self.execute_button.setEnabled(snapshot.execute_button_enabled)
            override = self.local.read_manual_override()
            self.manual_override_label.setText(override.label)
            self.clear_manual_button.setEnabled(override.active)
            manual_blocked_reason = snapshot.manual_controls_blocked_reason
            for mode, button in self.manual_mode_buttons.items():
                button.setEnabled(not override.active and manual_blocked_reason is None)
                button.setChecked(override.active and override.mode == mode)
                button.setToolTip(manual_blocked_reason or "")
            if manual_blocked_reason:
                self.manual_message_label.setText(manual_blocked_reason)

            details = [
                f"Service: {snapshot.status_label}",
                f"Unit: {snapshot.unit_path}",
                f"Service env: {snapshot.env_path if snapshot.env_exists else 'default dry-run'}",
                f"Monitor args: {snapshot.monitor_args}",
                health.detail,
            ]
            if snapshot.main_pid is not None:
                details.append(f"MainPID: {snapshot.main_pid}")
            if snapshot.n_restarts is not None:
                details.append(f"NRestarts: {snapshot.n_restarts}")
            if snapshot.exec_main_start_timestamp:
                details.append(f"Started: {snapshot.exec_main_start_timestamp}")
            if not snapshot.unit_exists:
                details.append("Install the user service before using On.")
            if snapshot.dry_run and snapshot.is_running:
                details.append("Turn automation Off before switching to Execute Mode.")
            if manual_blocked_reason:
                details.append(f"Manual controls: {manual_blocked_reason}")
            if snapshot.last_error:
                details.append(snapshot.last_error)
            self.detail_label.setText("\n".join(details))

        def _read_settings_payload(self) -> dict[str, Any]:
            if not self.config_data:
                self.config_data = self.local.load_config_dict()
            payload = {
                section: dict(values)
                for section, values in self.config_data.items()
                if isinstance(values, dict)
            }
            for field in SETTINGS_FIELDS:
                widget = self.field_widgets[(field.section, field.key)]
                payload[field.section][field.key] = self._widget_value(widget, field)
            return payload

        def _set_widget_value(self, widget: QWidget, field: SettingsField, value: Any) -> None:
            if field.value_type == "bool" and isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif field.value_type == "int" and isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif field.value_type == "float" and isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QLineEdit):
                widget.setText("" if value is None else str(value))

        def _widget_value(self, widget: QWidget, field: SettingsField) -> Any:
            if field.value_type == "bool" and isinstance(widget, QCheckBox):
                return widget.isChecked()
            if field.value_type == "int" and isinstance(widget, QSpinBox):
                return widget.value()
            if field.value_type == "float" and isinstance(widget, QDoubleSpinBox):
                return widget.value()
            if isinstance(widget, QLineEdit):
                return widget.text()
            raise TypeError(f"Unsupported widget for {field.section}.{field.key}")

        def _set_preview_image(self, data_url: str) -> None:
            image_bytes = _decode_data_url(data_url)
            if not image_bytes:
                self.preview_label.setText("No camera image returned.")
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(image_bytes):
                self.preview_label.setText("Camera sample image could not be decoded.")
                return
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)

        def _format_optional_float(self, value: float | None, suffix: str) -> str:
            if value is None:
                return "n/a"
            separator = " " if suffix else ""
            return f"{value:.3f}{separator}{suffix}"

    app = QApplication([APP_NAME])
    window = GeekDeskWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
