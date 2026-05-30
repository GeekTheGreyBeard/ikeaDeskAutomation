"""Tests for camera calibration log analysis."""
from __future__ import annotations

import json
import pathlib

import pytest

from ikea_desk_automation.calibration import (
    CalibrationAnchor,
    anchor_from_log_entry,
    fit_camera_to_room_transform,
    load_target_calibration_logs,
    point_rank,
)


def _entry(
    *,
    roi_label: str = "lower-narrow-center",
    room_y_in: float = 24.0,
    camera_point: tuple[float, float, float] = (0.1, 0.2, 1.0),
) -> dict:
    return {
        "roi": {"label": roi_label},
        "target": {
            "room_center_in": {
                "x": 0.0,
                "y": room_y_in,
                "z": 2.0,
            }
        },
        "deprojected_target": {
            "point_count": 12,
            "median_camera_m": {
                "x": camera_point[0],
                "y": camera_point[1],
                "z": camera_point[2],
            },
        },
    }


def test_anchor_from_log_entry_converts_inches_to_metres() -> None:
    anchor = anchor_from_log_entry(_entry(room_y_in=24.0), source="capture.json")

    assert anchor is not None
    assert anchor.room_m == pytest.approx((0.0, 0.6096, 0.0508))
    assert anchor.camera_m == pytest.approx((0.1, 0.2, 1.0))
    assert anchor.point_count == 12


def test_load_target_calibration_logs_filters_roi(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "target.json"
    path.write_text(
        json.dumps(
            {
                "schema": "ikea_desk_automation.calibration_target.v1",
                "entries": [
                    _entry(roi_label="full-frame"),
                    _entry(roi_label="lower-narrow-center", room_y_in=48.0),
                ],
            }
        )
    )

    anchors = load_target_calibration_logs([path])

    assert len(anchors) == 1
    assert anchors[0].room_m[1] == pytest.approx(1.2192)


def test_fit_camera_to_room_transform_rejects_collinear_centerline_anchors() -> None:
    anchors = [
        CalibrationAnchor("a", "lower-narrow-center", (0.0, 0.6, 0.05), (0.1, 0.0, 1.0), 10),
        CalibrationAnchor("b", "lower-narrow-center", (0.0, 1.2, 0.05), (0.1, 0.2, 1.4), 10),
        CalibrationAnchor("c", "lower-narrow-center", (0.0, 1.8, 0.05), (0.1, 0.4, 1.8), 10),
        CalibrationAnchor("d", "lower-narrow-center", (0.0, 2.2, 0.05), (0.1, 0.6, 2.2), 10),
    ]

    result = fit_camera_to_room_transform(anchors)

    assert result.status == "underconstrained"
    assert result.room_rank == 1
    assert "left/right" in result.message


def test_fit_camera_to_room_transform_with_non_collinear_anchors() -> None:
    anchors = [
        CalibrationAnchor("a", "lower-narrow-center", (1.0, 2.0, 3.0), (0.0, 0.0, 0.0), 10),
        CalibrationAnchor("b", "lower-narrow-center", (2.0, 2.0, 3.0), (1.0, 0.0, 0.0), 10),
        CalibrationAnchor("c", "lower-narrow-center", (1.0, 3.0, 3.0), (0.0, 1.0, 0.0), 10),
        CalibrationAnchor("d", "lower-narrow-center", (1.0, 2.0, 4.0), (0.0, 0.0, 1.0), 10),
    ]

    result = fit_camera_to_room_transform(anchors)

    assert result.status == "ok"
    assert result.room_rank == 3
    assert result.camera_rank == 3
    assert result.rmse_m is not None
    assert result.rmse_m < 1e-9
    assert result.translation_m == pytest.approx((1.0, 2.0, 3.0))


def test_point_rank() -> None:
    assert point_rank([(0.0, 0.0, 0.0)]) == 0
    assert point_rank([(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 2.0, 0.0)]) == 1
    assert point_rank([(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)]) == 2
