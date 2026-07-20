"""CLI entry point for ikea-desk."""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import json
import pathlib
import random
import sys
import time
from collections.abc import Callable
from typing import Any

from ikea_desk_automation.automation import (
    AutomationPolicy,
    DeskAutomation,
    DeskMotionDetector,
    DeskMotionObservation,
    OccupancyState,
)
from ikea_desk_automation.audio import MovementAlert, PulseAudioMovementAlert
from ikea_desk_automation.config import AppConfig, PresetsConfig
from ikea_desk_automation.desk.base import DeskClient
from ikea_desk_automation.manual_override import ManualOverrideStore
from ikea_desk_automation.monitor_status import (
    PHASE_CONNECTING,
    PHASE_SAMPLING,
    PHASE_STOPPED,
    MonitorStatusStore,
    new_status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ikea-desk",
        description="Local-first Bluetooth standing desk automation.",
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument(
        "--config",
        metavar="PATH",
        type=pathlib.Path,
        default=None,
        help="Path to YAML config file.",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Use the in-memory fake desk client (no BLE hardware required).",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Print current height and speed (read-only).")
    sub.add_parser("height", help="Print current height in metres (read-only).")
    sub.add_parser("speed", help="Print current speed in m/s (read-only).")

    preset_p = sub.add_parser("preset", help="Move to a named preset (dry-run by default).")
    preset_p.add_argument(
        "preset_name",
        choices=["sit", "stand", "away"],
        help="Name of the preset to apply.",
    )
    preset_p.add_argument(
        "--execute",
        action="store_true",
        help="Actually move the desk.  Omit to perform a dry-run.",
    )
    preset_p.add_argument(
        "--no-alert",
        action="store_true",
        help="Skip the audible movement alert before an executed move.",
    )

    camera_p = sub.add_parser("camera", help="Check RealSense camera availability (no movement).")
    camera_p.add_argument(
        "--show-serial",
        action="store_true",
        help="Print the camera serial number. Hidden by default for cleaner public logs.",
    )
    camera_p.add_argument(
        "--sample",
        action="store_true",
        help="Start a depth-only RealSense sample and print conservative ROI metrics.",
    )
    camera_p.add_argument(
        "--classify",
        action="store_true",
        help="Sample depth and print the resulting automation observation.",
    )
    camera_p.add_argument(
        "--calibrate",
        action="store_true",
        help="Sample depth and print read-only calibration guidance.",
    )
    camera_p.add_argument(
        "--target-distance-in",
        type=float,
        default=None,
        help="Run a read-only calibration-target pass for a known object this many inches from the desk face.",
    )
    camera_p.add_argument(
        "--target-lateral-in",
        type=float,
        default=0.0,
        help=(
            "Calibration target center offset in inches from desk centerline. "
            "Positive values are left when facing the desk."
        ),
    )
    camera_p.add_argument(
        "--target-width-in",
        type=float,
        default=7.0,
        help="Calibration target width in inches. Defaults to a 7 inch calibration box.",
    )
    camera_p.add_argument(
        "--target-depth-in",
        type=float,
        default=6.0,
        help="Calibration target depth in inches. Defaults to a 6 inch calibration box.",
    )
    camera_p.add_argument(
        "--target-height-in",
        type=float,
        default=4.0,
        help="Calibration target height in inches. Defaults to a 4 inch calibration box.",
    )
    camera_p.add_argument(
        "--target-log",
        type=pathlib.Path,
        default=None,
        help="Write a JSON calibration-target capture log for this read-only pass.",
    )
    camera_p.add_argument(
        "--fit-target-log",
        type=pathlib.Path,
        action="append",
        default=[],
        help="Read target capture log(s) and fit/analyze the camera-to-room transform.",
    )
    camera_p.add_argument(
        "--fit-roi-label",
        default="lower-narrow-center",
        help="ROI label to use when fitting target logs.",
    )

    alert_p = sub.add_parser("alert", help="Play the configured movement alert without moving.")
    alert_p.add_argument(
        "--no-volume-change",
        action="store_true",
        help="Play alert without unmuting or setting idle volume first.",
    )

    monitor_p = sub.add_parser(
        "monitor",
        help="Continuously sample the camera and apply posture/away automation.",
    )
    monitor_p.add_argument(
        "--execute",
        action="store_true",
        help="Actually move the desk when sustained observations trigger automation.",
    )
    monitor_p.add_argument(
        "--no-alert",
        action="store_true",
        help="Skip the audible movement alert before executed monitor moves.",
    )
    monitor_p.add_argument(
        "--stop-after-first-move",
        action="store_true",
        help=(
            "Exit after the first accepted monitor target action. Useful for supervised "
            "one-direction movement tests."
        ),
    )
    monitor_p.add_argument(
        "--sample-interval",
        type=float,
        default=None,
        help="Seconds between camera samples. Defaults to automation.sample_interval_seconds.",
    )
    monitor_p.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    monitor_p.add_argument(
        "--seed",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )

    from ikea_desk_automation.webui import add_ui_parser  # noqa: PLC0415

    add_ui_parser(sub)

    return parser


def _make_desk_client(args: argparse.Namespace, config: AppConfig) -> DeskClient:
    if args.fake:
        from ikea_desk_automation.desk.fake import FakeDeskClient  # noqa: PLC0415

        return FakeDeskClient()
    address = config.desk.address
    if not address:
        print(
            "error: no desk address configured — set desk.address in config or pass --fake",
            file=sys.stderr,
        )
        raise SystemExit(1)
    from ikea_desk_automation.desk.idasen_client import IdasenDeskClient  # noqa: PLC0415

    return IdasenDeskClient(address)


def _preset_height(presets: PresetsConfig, name: str) -> float:
    mapping = {
        "sit": presets.sit,
        "stand": presets.stand,
        "away": presets.away,
    }
    return mapping[name]


def _automation_policy(config: AppConfig) -> AutomationPolicy:
    return AutomationPolicy(
        sustained_state_seconds=config.automation.sustained_state_seconds,
        cooldown_seconds=config.automation.cooldown_seconds,
        min_confidence=config.automation.min_confidence,
        camera_required=config.automation.camera_required,
    )


def _target_height_for_state(
    config: AppConfig,
    state: OccupancyState,
    rng: random.Random,
) -> tuple[str, float] | None:
    if state == OccupancyState.SITTING:
        return "sit", config.presets.sit
    if state == OccupancyState.STANDING:
        return "stand", config.presets.stand
    if state == OccupancyState.AWAY:
        return "away", config.presets.away
    return None


# ---------------------------------------------------------------------------
# Async command handlers
# ---------------------------------------------------------------------------


async def cmd_status(client: DeskClient) -> int:
    async with client:
        status = await client.get_status()
        speed = f"{status.speed_ms:.3f} m/s" if status.speed_ms is not None else "N/A"
        print(f"height: {status.height_m:.3f} m")
        print(f"speed:  {speed}")
    return 0


async def cmd_height(client: DeskClient) -> int:
    async with client:
        h = await client.get_height()
        print(f"{h:.3f}")
    return 0


async def cmd_speed(client: DeskClient) -> int:
    async with client:
        s = await client.get_speed()
        print("N/A" if s is None else f"{s:.3f}")
    return 0


async def cmd_preset(
    client: DeskClient,
    preset_name: str,
    target_m: float,
    execute: bool,
    *,
    timeout_seconds: float = 45.0,
    final_tolerance_m: float = 0.010,
    alert: MovementAlert | None = None,
) -> int:
    if not execute:
        print(
            f"dry-run: would move to preset '{preset_name}' ({target_m:.3f} m) "
            f"— pass --execute to move"
        )
        return 0
    async with client:
        current_m = await client.get_height()
        if abs(current_m - target_m) <= final_tolerance_m:
            print(
                f"already at preset '{preset_name}' "
                f"({current_m:.3f} m within {final_tolerance_m:.3f} m)"
            )
            return 0
        if alert is not None:
            alert.play()
        print(f"moving to preset '{preset_name}' ({target_m:.3f} m) …")
        motion_sound = alert.start_motion_sound() if alert is not None else None
        try:
            await asyncio.wait_for(client.move_to(target_m), timeout=timeout_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await client.stop()
            raise
        finally:
            if motion_sound is not None:
                motion_sound.stop()
        h = await client.get_height()
        if abs(h - target_m) > final_tolerance_m:
            print(
                f"error: expected {target_m:.3f} m but read back {h:.3f} m "
                f"(tolerance {final_tolerance_m:.3f} m)",
                file=sys.stderr,
            )
            return 2
        print(f"done — height now {h:.3f} m")
    return 0


def cmd_alert(config: AppConfig, no_volume_change: bool = False) -> int:
    alert = PulseAudioMovementAlert(volume_percent=config.audio.volume_percent)
    if no_volume_change:
        alert.play_without_volume_change()
    else:
        alert.play()
    print("movement alert played")
    return 0


def cmd_camera(
    config: AppConfig,
    show_serial: bool = False,
    sample: bool = False,
    calibrate: bool = False,
    target_distance_in: float | None = None,
    target_lateral_in: float = 0.0,
    target_width_in: float = 7.0,
    target_depth_in: float = 6.0,
    target_height_in: float = 4.0,
    target_log: pathlib.Path | None = None,
    fit_target_logs: list[pathlib.Path] | None = None,
    fit_roi_label: str = "lower-narrow-center",
) -> int:
    from ikea_desk_automation.camera import (  # noqa: PLC0415
        check_realsense,
        classify_depth_metrics,
        recommend_camera_calibration,
        sample_realsense_depth,
    )

    if fit_target_logs:
        return _cmd_camera_fit_target_logs(fit_target_logs, roi_label=fit_roi_label)

    info = check_realsense()
    if info.available:
        suffix = f", serial={info.serial}" if show_serial else ""
        print(f"realsense: available — {info.device_count} device(s){suffix}")
    else:
        print(f"realsense: unavailable — {info.message}")
        return 1 if sample else 0

    if target_distance_in is not None:
        return _cmd_camera_target(
            config,
            target_distance_in=target_distance_in,
            target_lateral_in=target_lateral_in,
            target_width_in=target_width_in,
            target_depth_in=target_depth_in,
            target_height_in=target_height_in,
            target_log=target_log,
        )

    if not sample:
        if not config.camera.enabled:
            print("camera: disabled in config")
        return 0

    metrics = sample_realsense_depth(config.camera)
    print(f"depth: {'available' if metrics.available else 'unavailable'}")
    if metrics.message:
        print(f"message: {metrics.message}")
    print(f"frame: {metrics.width}x{metrics.height}")
    print(f"valid_ratio: {metrics.valid_ratio:.4f}")
    print(f"foreground_ratio: {metrics.foreground_ratio:.4f}")
    if metrics.nearest_m is not None:
        print(f"nearest: {metrics.nearest_m:.3f} m")
    if metrics.median_m is not None:
        print(f"median: {metrics.median_m:.3f} m")
    if metrics.top_y is not None:
        print(f"top_y: {metrics.top_y:.3f}")
    if metrics.centroid_y is not None:
        print(f"centroid_y: {metrics.centroid_y:.3f}")

    observation = classify_depth_metrics(metrics, config.camera)
    print(f"observation: {observation.state.value} ({observation.confidence:.2f})")
    if calibrate:
        advice = recommend_camera_calibration(metrics, config.camera)
        print(f"calibration: {advice.status}")
        for message in advice.messages:
            print(f"- {message}")
    return 0 if metrics.available else 1


def _cmd_camera_fit_target_logs(paths: list[pathlib.Path], *, roi_label: str) -> int:
    """Analyze saved target logs without starting the camera or touching the desk."""
    from ikea_desk_automation.calibration import (  # noqa: PLC0415
        fit_camera_to_room_transform,
        load_target_calibration_logs,
    )

    anchors = load_target_calibration_logs(paths, roi_label=roi_label)
    result = fit_camera_to_room_transform(anchors)
    print("calibration-fit: read-only")
    print(f"logs: {len(paths)}")
    print(f"roi: {roi_label}")
    print(f"anchors: {result.anchor_count}")
    print(f"room_rank: {result.room_rank}")
    print(f"camera_rank: {result.camera_rank}")
    print(f"status: {result.status}")
    print(f"message: {result.message}")
    if result.rmse_m is not None:
        print(f"rmse: {result.rmse_m:.4f} m")
    if result.rotation is not None and result.translation_m is not None:
        print("rotation:")
        for row in result.rotation:
            print(f"- [{row[0]:.8f}, {row[1]:.8f}, {row[2]:.8f}]")
        point = result.translation_m
        print(f"translation_m: x={point[0]:.4f}, y={point[1]:.4f}, z={point[2]:.4f}")
    if result.status != "ok":
        print("next: add left/right target captures, then rerun this fit.")
    return 0 if anchors else 1


def _cmd_camera_target(
    config: AppConfig,
    *,
    target_distance_in: float,
    target_lateral_in: float,
    target_width_in: float,
    target_depth_in: float,
    target_height_in: float,
    target_log: pathlib.Path | None = None,
) -> int:
    """Run read-only target calibration sweeps without using posture ROI decisions."""
    from ikea_desk_automation.camera import sample_realsense_target_points  # noqa: PLC0415

    if target_distance_in <= 0:
        raise ValueError("--target-distance-in must be positive")
    if min(target_width_in, target_depth_in, target_height_in) <= 0:
        raise ValueError("target dimensions must be positive")

    print("calibration-target: read-only")
    print(
        "target: "
        f"{target_width_in:.1f}w x {target_depth_in:.1f}d x {target_height_in:.1f}h in, "
        f"{target_distance_in:.1f} in from desk face, "
        f"{target_lateral_in:+.1f} in lateral from centerline"
    )
    expected_m = _expected_target_distance_m(target_distance_in, target_height_in, target_lateral_in)
    print(f"expected straight-line camera-to-target-center distance: {expected_m:.3f} m")
    print("note: this is a geometry sanity check, not a desk-movement decision")

    any_available = False
    log_entries: list[dict[str, Any]] = []
    for label, overrides in _target_calibration_rois():
        camera_cfg = dataclasses.replace(
            config.camera,
            min_depth_m=min(config.camera.min_depth_m, 0.20),
            max_depth_m=max(config.camera.max_depth_m, expected_m + 0.75),
            min_foreground_ratio=0.0,
            **overrides,
        )
        metrics, target_metrics = sample_realsense_target_points(camera_cfg)
        any_available = any_available or metrics.available
        print(f"\nroi: {label}")
        print(
            "bounds: "
            f"left={camera_cfg.roi_left:.2f}, top={camera_cfg.roi_top:.2f}, "
            f"right={camera_cfg.roi_right:.2f}, bottom={camera_cfg.roi_bottom:.2f}"
        )
        print(f"depth: {'available' if metrics.available else 'unavailable'}")
        if metrics.message:
            print(f"message: {metrics.message}")
        print(f"frame: {metrics.width}x{metrics.height}")
        print(f"valid_ratio: {metrics.valid_ratio:.4f}")
        print(f"foreground_ratio: {metrics.foreground_ratio:.4f}")
        if metrics.nearest_m is not None:
            delta = metrics.nearest_m - expected_m
            print(f"nearest: {metrics.nearest_m:.3f} m ({delta:+.3f} m vs expected)")
        if metrics.median_m is not None:
            delta = metrics.median_m - expected_m
            print(f"median: {metrics.median_m:.3f} m ({delta:+.3f} m vs expected)")
        if metrics.top_y is not None:
            print(f"top_y: {metrics.top_y:.3f}")
        if metrics.centroid_y is not None:
            print(f"centroid_y: {metrics.centroid_y:.3f}")
        if target_metrics.intrinsics is not None:
            print(
                "intrinsics: "
                f"{target_metrics.intrinsics.width}x{target_metrics.intrinsics.height}, "
                f"fx={target_metrics.intrinsics.fx:.2f}, fy={target_metrics.intrinsics.fy:.2f}, "
                f"ppx={target_metrics.intrinsics.ppx:.2f}, ppy={target_metrics.intrinsics.ppy:.2f}"
            )
        print(f"target_points: {target_metrics.point_count}")
        if target_metrics.centroid_camera_m is not None:
            point = target_metrics.centroid_camera_m
            print(f"centroid_camera_m: x={point.x:.3f}, y={point.y:.3f}, z={point.z:.3f}")
        if target_metrics.median_camera_m is not None:
            point = target_metrics.median_camera_m
            print(f"median_camera_m: x={point.x:.3f}, y={point.y:.3f}, z={point.z:.3f}")

        log_entries.append(
            _target_calibration_log_entry(
                label=label,
                camera_cfg=camera_cfg,
                target_distance_in=target_distance_in,
                target_lateral_in=target_lateral_in,
                target_width_in=target_width_in,
                target_depth_in=target_depth_in,
                target_height_in=target_height_in,
                expected_m=expected_m,
                metrics=metrics,
                target_metrics=target_metrics,
            )
        )

    if target_log is not None:
        _write_target_calibration_log(target_log, log_entries)
        print(f"\ncalibration log written: {target_log}")

    print(
        "\nnext: use the deprojected centroid/median camera points from the 24, 48, 72, "
        "and 87 inch passes to fit the camera-to-room transform."
    )
    return 0 if any_available else 1


def _target_calibration_log_entry(
    *,
    label: str,
    camera_cfg: Any,
    target_distance_in: float,
    target_lateral_in: float,
    target_width_in: float,
    target_depth_in: float,
    target_height_in: float,
    expected_m: float,
    metrics: Any,
    target_metrics: Any,
) -> dict[str, Any]:
    return {
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "roi": {
            "label": label,
            "left": camera_cfg.roi_left,
            "top": camera_cfg.roi_top,
            "right": camera_cfg.roi_right,
            "bottom": camera_cfg.roi_bottom,
        },
        "target": {
            "distance_in": target_distance_in,
            "width_in": target_width_in,
            "depth_in": target_depth_in,
            "height_in": target_height_in,
            "room_center_in": {
                "x": target_lateral_in,
                "y": target_distance_in,
                "z": target_height_in / 2,
            },
        },
        "expected_camera_to_target_center_m": expected_m,
        "depth_metrics": dataclasses.asdict(metrics),
        "deprojected_target": dataclasses.asdict(target_metrics),
    }


def _write_target_calibration_log(path: pathlib.Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "ikea_desk_automation.calibration_target.v1",
        "entry_count": len(entries),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _target_calibration_rois() -> list[tuple[str, dict[str, float]]]:
    """Candidate normalized ROIs for floor-target capture."""
    return [
        ("full-frame", {"roi_left": 0.0, "roi_top": 0.0, "roi_right": 1.0, "roi_bottom": 1.0}),
        ("lower-center", {"roi_left": 0.20, "roi_top": 0.45, "roi_right": 0.80, "roi_bottom": 1.0}),
        ("lower-narrow-center", {"roi_left": 0.35, "roi_top": 0.50, "roi_right": 0.65, "roi_bottom": 1.0}),
        ("floor-band", {"roi_left": 0.15, "roi_top": 0.62, "roi_right": 0.85, "roi_bottom": 1.0}),
    ]


def _expected_centerline_target_distance_m(target_distance_in: float, target_height_in: float) -> float:
    return _expected_target_distance_m(target_distance_in, target_height_in, target_lateral_in=0.0)


def _expected_target_distance_m(
    target_distance_in: float,
    target_height_in: float,
    target_lateral_in: float,
) -> float:
    """Approximate distance from mounted camera lens to target center.

    Positive lateral target coordinates are left when facing the desk. The
    known lens reference is 11.5 inches right of desk center, 29.5 inches
    behind the desk face, and 66 inches above the floor at the lowest desk
    position.
    """
    camera_x_in = -11.5
    camera_y_in = 29.5
    camera_z_in = 66.0
    target_center_z_in = target_height_in / 2
    distance_in = (
        (target_lateral_in - camera_x_in) ** 2
        + (camera_y_in + target_distance_in) ** 2
        + (camera_z_in - target_center_z_in) ** 2
    ) ** 0.5
    return distance_in * 0.0254


async def cmd_monitor(
    client: DeskClient,
    config: AppConfig,
    *,
    execute: bool,
    stop_after_first_move: bool = False,
    sample_interval_seconds: float | None = None,
    max_samples: int | None = None,
    rng: random.Random | None = None,
    alert: MovementAlert | None = None,
    observation_sampler: Callable[[], Any] | None = None,
    override_store: ManualOverrideStore | None = None,
    status_store: MonitorStatusStore | None = None,
) -> int:
    """Run the camera-driven posture/away automation loop."""
    from ikea_desk_automation.camera import sample_realsense_observation  # noqa: PLC0415

    interval = sample_interval_seconds or config.automation.sample_interval_seconds
    if interval <= 0:
        raise ValueError("sample interval must be positive")
    rng = rng or random.Random()
    automation = DeskAutomation(_automation_policy(config))
    motion_detector = DeskMotionDetector(
        speed_threshold_ms=config.automation.motion_speed_threshold_ms,
        height_delta_threshold_m=config.automation.motion_height_delta_threshold_m,
        settling_seconds=config.automation.motion_settle_seconds,
    )
    sampler = observation_sampler or (lambda: sample_realsense_observation(config.camera))
    override_store = override_store or ManualOverrideStore()
    status_store = status_store or MonitorStatusStore()

    status = new_status(execute=execute, interval_seconds=interval, phase=PHASE_CONNECTING)
    status = dataclasses.replace(status, message="connecting to desk")
    status_store.write(status)

    print(
        "monitor: started "
        f"({'execute' if execute else 'dry-run'}, interval={interval:.1f}s)"
    )

    async with client:
        status = dataclasses.replace(
            status, phase=PHASE_SAMPLING, updated_at=time.time(), message=None
        )
        status_store.write(status)
        sample_count = 0
        while max_samples is None or sample_count < max_samples:
            sample_count += 1
            override = override_store.read()
            if override.active:
                automation.reset_sustained()
                print(
                    "sample "
                    f"{sample_count}: manual override active ({override.mode}); "
                    "camera automation skipped"
                )
                status = dataclasses.replace(
                    status,
                    updated_at=time.time(),
                    sample_count=sample_count,
                    last_decision="camera automation skipped",
                    message=f"manual override active ({override.mode})",
                )
                status_store.write(status)
                if max_samples is None or sample_count < max_samples:
                    await asyncio.sleep(interval)
                continue
            sampled = sampler()
            metrics, observation = sampled
            desk_status = await client.get_status()
            motion = motion_detector.evaluate(
                DeskMotionObservation(
                    height_m=desk_status.height_m,
                    speed_ms=desk_status.speed_ms,
                    timestamp=observation.timestamp,
                )
            )
            automation.update_camera(metrics.available)
            if motion.active:
                automation.reset_sustained()
                decision = None
                decision_reason = f"desk {motion.state.value}: {motion.reason}"
            else:
                decision = automation.evaluate(observation)
                decision_reason = decision.reason

            print(
                "sample "
                f"{sample_count}: {observation.state.value} "
                f"({observation.confidence:.2f}), desk {motion.state.value} - "
                f"{decision_reason}"
            )

            status = dataclasses.replace(
                status,
                updated_at=time.time(),
                sample_count=sample_count,
                last_state=observation.state.value,
                last_confidence=observation.confidence,
                last_motion=motion.state.value,
                last_decision=decision_reason,
                message=None,
            )
            status_store.write(status)

            if decision is not None and decision.should_move and decision.target_state is not None:
                target = _target_height_for_state(config, decision.target_state, rng)
                target_action_accepted = False
                if target is None:
                    print(f"skip: no movement target for {decision.target_state.value}")
                else:
                    target_name, target_m = target
                    config.desk.validate_height(target_m, f"automation.{target_name}")
                    current_m = await client.get_height()
                    if abs(current_m - target_m) <= config.automation.final_tolerance_m:
                        print(f"already near {target_name} ({current_m:.3f} m)")
                    elif not execute:
                        print(
                            f"dry-run: would move to {target_name} "
                            f"({target_m:.3f} m) from {current_m:.3f} m"
                        )
                        automation.record_move(
                            timestamp=observation.timestamp,
                            state=decision.target_state,
                            requires_confirmation=False,
                        )
                        target_action_accepted = True
                    else:
                        if alert is not None:
                            alert.play()
                        print(
                            f"moving to {target_name} "
                            f"({target_m:.3f} m) from {current_m:.3f} m"
                        )
                        motion_sound = alert.start_motion_sound() if alert is not None else None
                        try:
                            await asyncio.wait_for(
                                client.move_to(target_m),
                                timeout=config.automation.movement_timeout_seconds,
                            )
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            await client.stop()
                            raise
                        finally:
                            if motion_sound is not None:
                                motion_sound.stop()
                        height_m = await client.get_height()
                        if abs(height_m - target_m) > config.automation.final_tolerance_m:
                            print(
                                f"error: expected {target_m:.3f} m but read back {height_m:.3f} m "
                                f"(tolerance {config.automation.final_tolerance_m:.3f} m)",
                                file=sys.stderr,
                            )
                            return 2
                        motion_detector.mark_idle(
                            DeskMotionObservation(
                                height_m=height_m,
                                speed_ms=0.0,
                                timestamp=observation.timestamp,
                            )
                        )
                        print(f"done - height now {height_m:.3f} m")
                        automation.record_move(
                            timestamp=observation.timestamp,
                            state=decision.target_state,
                        )
                        target_action_accepted = True

                if target_action_accepted and target is not None:
                    status = dataclasses.replace(
                        status,
                        updated_at=time.time(),
                        last_move_at=time.time(),
                        last_move_target=target[0],
                    )
                    status_store.write(status)

                if stop_after_first_move and target_action_accepted:
                    print("monitor: stopping after first accepted target action")
                    break

            if max_samples is None or sample_count < max_samples:
                await asyncio.sleep(interval)

    status_store.write(
        dataclasses.replace(status, phase=PHASE_STOPPED, updated_at=time.time())
    )
    return 0


class FakeNoopDeskClient(DeskClient):
    """Desk client placeholder for dry-run preset dispatch.

    It should never connect; cmd_preset returns before touching the client when
    execute=False. Keeping this object local to CLI dispatch proves dry-runs do
    not require a configured real desk.
    """

    async def connect(self) -> None:
        raise AssertionError("dry-run should not connect to a desk")

    async def disconnect(self) -> None:
        raise AssertionError("dry-run should not disconnect from a desk")

    async def get_height(self) -> float:
        raise AssertionError("dry-run should not read desk height")

    async def get_speed(self) -> float | None:
        raise AssertionError("dry-run should not read desk speed")

    async def get_status(self):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run should not read desk status")

    async def move_to(self, height_m: float) -> None:
        raise AssertionError("dry-run should not move a desk")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _dispatch(args: argparse.Namespace, config: AppConfig) -> int:
    cmd = args.command

    if cmd == "ui":
        from ikea_desk_automation.webui import serve_ui  # noqa: PLC0415

        serve_ui(
            config_path=args.config,
            host=args.host,
            port=args.port,
            open_browser=args.open,
            fake=args.fake,
        )
        return 0

    if cmd == "camera":
        return cmd_camera(
            config,
            show_serial=args.show_serial,
            sample=args.sample or args.classify or args.calibrate or args.target_distance_in is not None,
            calibrate=args.calibrate,
            target_distance_in=args.target_distance_in,
            target_lateral_in=args.target_lateral_in,
            target_width_in=args.target_width_in,
            target_depth_in=args.target_depth_in,
            target_height_in=args.target_height_in,
            target_log=args.target_log,
            fit_target_logs=args.fit_target_log,
            fit_roi_label=args.fit_roi_label,
        )

    if cmd == "alert":
        return cmd_alert(config, no_volume_change=args.no_volume_change)

    if cmd == "preset":
        target_m = _preset_height(config.presets, args.preset_name)
        config.desk.validate_height(target_m, f"presets.{args.preset_name}")
        if not args.execute:
            return await cmd_preset(
                FakeNoopDeskClient(),
                args.preset_name,
                target_m,
                execute=False,
                timeout_seconds=config.automation.movement_timeout_seconds,
                final_tolerance_m=config.automation.final_tolerance_m,
            )

    client = _make_desk_client(args, config)

    if cmd == "status":
        return await cmd_status(client)
    if cmd == "height":
        return await cmd_height(client)
    if cmd == "speed":
        return await cmd_speed(client)
    if cmd == "preset":
        target_m = _preset_height(config.presets, args.preset_name)
        return await cmd_preset(
            client,
            args.preset_name,
            target_m,
            args.execute,
            timeout_seconds=config.automation.movement_timeout_seconds,
            final_tolerance_m=config.automation.final_tolerance_m,
            alert=(
                None
                if args.no_alert or not config.audio.enabled
                else PulseAudioMovementAlert(volume_percent=config.audio.volume_percent)
            ),
        )
    if cmd == "monitor":
        return await cmd_monitor(
            client,
            config,
            execute=args.execute,
            stop_after_first_move=args.stop_after_first_move,
            sample_interval_seconds=args.sample_interval,
            max_samples=args.max_samples,
            rng=random.Random(args.seed),
            alert=(
                None
                if args.no_alert or not config.audio.enabled
                else PulseAudioMovementAlert(volume_percent=config.audio.volume_percent)
            ),
        )

    return 1  # unreachable after argparse validation


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(line_buffering=True)

    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        from ikea_desk_automation import __version__

        print(__version__)
        return 0

    if args.command is None:
        parser.print_help()
        return 0

    config = AppConfig.load_or_default(args.config)
    return asyncio.run(_dispatch(args, config))


if __name__ == "__main__":
    raise SystemExit(main())
