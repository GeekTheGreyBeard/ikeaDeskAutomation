"""Tests for camera.py availability check — no hardware required.

pyrealsense2 is mocked via sys.modules so the tests run without the SDK.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from ikea_desk_automation.automation import OccupancyState
from ikea_desk_automation.camera import (
    CameraIntrinsics,
    CameraInfo,
    DeprojectedTargetMetrics,
    DepthMetrics,
    check_realsense,
    classify_depth_metrics,
    deproject_pixel_to_point,
    deprojected_target_metrics_from_rows,
    depth_frame_bmp_data_url,
    depth_metrics_from_rows,
    recommend_camera_calibration,
    sample_realsense_depth,
)
from ikea_desk_automation.config import CameraConfig


def test_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate pyrealsense2 not being installed."""
    monkeypatch.setitem(sys.modules, "pyrealsense2", None)  # type: ignore[arg-type]
    info = check_realsense()
    assert not info.available
    assert "not installed" in info.message


def test_no_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_rs = MagicMock()
    fake_ctx = MagicMock()
    fake_ctx.query_devices.return_value = []
    fake_rs.context.return_value = fake_ctx
    monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

    info = check_realsense()
    assert not info.available
    assert info.device_count == 0
    assert "no RealSense" in info.message


def test_one_device(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_rs = MagicMock()
    fake_device = MagicMock()
    fake_device.get_info.return_value = "123456789"
    fake_ctx = MagicMock()
    fake_ctx.query_devices.return_value = [fake_device]
    fake_rs.context.return_value = fake_ctx
    fake_rs.camera_info.serial_number = "serial_number_attr"
    monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

    info = check_realsense()
    assert info.available
    assert info.device_count == 1
    assert info.serial == "123456789"


def test_exception_during_query(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_rs = MagicMock()
    fake_rs.context.side_effect = RuntimeError("BLE error")
    monkeypatch.setitem(sys.modules, "pyrealsense2", fake_rs)

    info = check_realsense()
    assert not info.available
    assert "BLE error" in info.message


def test_camera_info_dataclass() -> None:
    info = CameraInfo(available=True, device_count=2, serial="abc", message="ok")
    assert info.device_count == 2
    assert info.serial == "abc"


def test_disabled_camera_sample_returns_unavailable() -> None:
    metrics = sample_realsense_depth(CameraConfig(enabled=False))
    assert not metrics.available
    assert "disabled" in metrics.message


def test_depth_metrics_empty_frame() -> None:
    metrics = depth_metrics_from_rows([], CameraConfig())
    assert not metrics.available
    assert "empty" in metrics.message


def test_depth_metrics_with_foreground() -> None:
    rows = [
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 1.2, 1.1, 0.0],
        [0.0, 1.3, 1.2, 0.0],
        [0.0, 0.0, 0.0, 0.0],
    ]
    metrics = depth_metrics_from_rows(rows, CameraConfig(min_foreground_ratio=0.1))
    assert metrics.available
    assert metrics.width == 4
    assert metrics.height == 4
    assert metrics.foreground_ratio == pytest.approx(0.25)
    assert metrics.nearest_m == pytest.approx(1.1)
    assert metrics.top_y == pytest.approx(1 / 3)


def test_deproject_pixel_to_point() -> None:
    intrinsics = CameraIntrinsics(width=4, height=4, fx=2.0, fy=4.0, ppx=1.0, ppy=1.0)
    point = deproject_pixel_to_point(intrinsics, x=3, y=3, depth_m=2.0)

    assert point.x == pytest.approx(2.0)
    assert point.y == pytest.approx(1.0)
    assert point.z == pytest.approx(2.0)


def test_deprojected_target_metrics_from_rows() -> None:
    rows = [
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 2.0, 0.0],
        [0.0, 3.0, 4.0, 0.0],
        [0.0, 0.0, 0.0, 0.0],
    ]
    intrinsics = CameraIntrinsics(width=4, height=4, fx=2.0, fy=2.0, ppx=1.0, ppy=1.0)

    metrics = deprojected_target_metrics_from_rows(rows, CameraConfig(), intrinsics)

    assert metrics.available
    assert metrics.point_count == 4
    assert metrics.centroid_camera_m is not None
    assert metrics.centroid_camera_m.z == pytest.approx(2.5)
    assert metrics.median_camera_m is not None
    assert metrics.median_camera_m.z == pytest.approx(2.5)


def test_deprojected_target_metrics_empty_points() -> None:
    intrinsics = CameraIntrinsics(width=2, height=2, fx=1.0, fy=1.0, ppx=0.0, ppy=0.0)
    metrics = deprojected_target_metrics_from_rows([[0.0, 0.0], [0.0, 0.0]], CameraConfig(), intrinsics)

    assert metrics.available
    assert isinstance(metrics, DeprojectedTargetMetrics)
    assert metrics.point_count == 0
    assert "no valid" in metrics.message


def test_depth_frame_bmp_data_url_is_browser_image() -> None:
    rows = [
        [0.0, 1.0, 1.5],
        [2.0, 2.5, 3.0],
    ]
    data_url = depth_frame_bmp_data_url(rows, CameraConfig(min_depth_m=1.0, max_depth_m=3.0))

    assert data_url.startswith("data:image/bmp;base64,")


def test_classify_obstruction_as_unsafe() -> None:
    metrics = DepthMetrics(available=True, foreground_ratio=0.2, nearest_m=0.2, top_y=0.5)
    obs = classify_depth_metrics(metrics, CameraConfig(obstruction_distance_m=0.35))
    assert obs.state == OccupancyState.UNSAFE
    assert obs.confidence == pytest.approx(1.0)


def test_classify_away_when_foreground_too_small() -> None:
    metrics = DepthMetrics(available=True, foreground_ratio=0.001, nearest_m=1.5, top_y=0.7)
    obs = classify_depth_metrics(metrics, CameraConfig(min_foreground_ratio=0.01))
    assert obs.state == OccupancyState.AWAY


def test_classify_posture_from_top_y() -> None:
    cfg = CameraConfig(standing_top_y=0.35, min_foreground_ratio=0.01)

    standing = classify_depth_metrics(
        DepthMetrics(available=True, foreground_ratio=0.10, nearest_m=1.5, top_y=0.20),
        cfg,
    )
    upright_sitting = classify_depth_metrics(
        DepthMetrics(available=True, foreground_ratio=0.10, nearest_m=1.5, top_y=0.45),
        cfg,
    )
    sitting = classify_depth_metrics(
        DepthMetrics(available=True, foreground_ratio=0.10, nearest_m=1.5, top_y=0.75),
        cfg,
    )

    assert standing.state == OccupancyState.STANDING
    assert upright_sitting.state == OccupancyState.SITTING
    assert sitting.state == OccupancyState.SITTING


def test_calibration_advice_for_obstruction() -> None:
    metrics = DepthMetrics(
        available=True,
        foreground_ratio=0.2,
        valid_ratio=0.2,
        nearest_m=0.25,
        top_y=0.4,
    )
    advice = recommend_camera_calibration(metrics, CameraConfig(obstruction_distance_m=0.35))
    assert advice.status == "needs-calibration"
    assert any("obstruction" in message for message in advice.messages)


def test_calibration_advice_disabled() -> None:
    advice = recommend_camera_calibration(DepthMetrics(available=True), CameraConfig(enabled=False))
    assert advice.status == "disabled"
    assert "camera.enabled" in advice.messages[0]
