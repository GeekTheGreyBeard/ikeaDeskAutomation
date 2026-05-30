"""Read-only camera calibration log loading and transform fitting."""
from __future__ import annotations

import dataclasses
import json
import math
import pathlib
from collections.abc import Sequence
from typing import Any

INCH_TO_M = 0.0254


@dataclasses.dataclass(frozen=True)
class CalibrationAnchor:
    """A known room point paired with a deprojected camera-space point."""

    source: str
    roi_label: str
    room_m: tuple[float, float, float]
    camera_m: tuple[float, float, float]
    point_count: int


@dataclasses.dataclass(frozen=True)
class TransformFitResult:
    """Result of fitting camera-space anchors to room coordinates."""

    status: str
    message: str
    anchor_count: int
    room_rank: int
    camera_rank: int
    rmse_m: float | None = None
    rotation: tuple[tuple[float, float, float], ...] | None = None
    translation_m: tuple[float, float, float] | None = None


def load_target_calibration_logs(
    paths: Sequence[pathlib.Path],
    *,
    roi_label: str = "lower-narrow-center",
    point_kind: str = "median_camera_m",
) -> list[CalibrationAnchor]:
    """Load target calibration anchors from one or more JSON capture logs."""
    anchors: list[CalibrationAnchor] = []
    for path in paths:
        payload = json.loads(path.read_text())
        for entry in payload.get("entries", []):
            anchor = anchor_from_log_entry(
                entry,
                source=str(path),
                roi_label=roi_label,
                point_kind=point_kind,
            )
            if anchor is not None:
                anchors.append(anchor)
    return anchors


def anchor_from_log_entry(
    entry: dict[str, Any],
    *,
    source: str,
    roi_label: str = "lower-narrow-center",
    point_kind: str = "median_camera_m",
) -> CalibrationAnchor | None:
    """Extract a single anchor from a target-log entry if it has the requested point."""
    roi = entry.get("roi", {})
    if roi.get("label") != roi_label:
        return None

    target = entry.get("target", {})
    room_center_in = target.get("room_center_in") or {}
    deprojected = entry.get("deprojected_target", {})
    point = deprojected.get(point_kind)
    if not point:
        return None

    room_m = (
        float(room_center_in["x"]) * INCH_TO_M,
        float(room_center_in["y"]) * INCH_TO_M,
        float(room_center_in["z"]) * INCH_TO_M,
    )
    camera_m = (float(point["x"]), float(point["y"]), float(point["z"]))
    return CalibrationAnchor(
        source=source,
        roi_label=str(roi_label),
        room_m=room_m,
        camera_m=camera_m,
        point_count=int(deprojected.get("point_count", 0)),
    )


def fit_camera_to_room_transform(anchors: Sequence[CalibrationAnchor]) -> TransformFitResult:
    """Fit a rigid transform from camera-space points to room points.

    A full 3D rigid transform needs non-collinear anchors. Centerline-only floor
    target captures are useful evidence, but they should fail this check until
    left/right positions add lateral geometry.
    """
    if len(anchors) < 3:
        return TransformFitResult(
            status="insufficient-anchors",
            message="at least three non-collinear anchors are required",
            anchor_count=len(anchors),
            room_rank=0,
            camera_rank=0,
        )

    room_points = [anchor.room_m for anchor in anchors]
    camera_points = [anchor.camera_m for anchor in anchors]
    room_rank = point_rank(room_points)
    camera_rank = point_rank(camera_points)
    if room_rank < 2 or camera_rank < 2:
        return TransformFitResult(
            status="underconstrained",
            message=(
                "anchors are collinear; add left/right target captures before "
                "fitting a camera-to-room transform"
            ),
            anchor_count=len(anchors),
            room_rank=room_rank,
            camera_rank=camera_rank,
        )

    try:
        import numpy as np  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return TransformFitResult(
            status="missing-dependency",
            message="numpy is required to fit the rigid transform",
            anchor_count=len(anchors),
            room_rank=room_rank,
            camera_rank=camera_rank,
        )

    camera = np.asarray(camera_points, dtype=float)
    room = np.asarray(room_points, dtype=float)
    camera_centroid = camera.mean(axis=0)
    room_centroid = room.mean(axis=0)
    centered_camera = camera - camera_centroid
    centered_room = room - room_centroid
    covariance = centered_camera.T @ centered_room
    u_matrix, _, vt_matrix = np.linalg.svd(covariance)
    rotation = vt_matrix.T @ u_matrix.T
    if np.linalg.det(rotation) < 0:
        vt_matrix[-1, :] *= -1
        rotation = vt_matrix.T @ u_matrix.T
    translation = room_centroid - rotation @ camera_centroid
    predicted = (rotation @ camera.T).T + translation
    errors = predicted - room
    rmse = math.sqrt(float((errors * errors).sum(axis=1).mean()))

    return TransformFitResult(
        status="ok",
        message="camera-to-room transform fitted",
        anchor_count=len(anchors),
        room_rank=room_rank,
        camera_rank=camera_rank,
        rmse_m=rmse,
        rotation=tuple(tuple(float(value) for value in row) for row in rotation.tolist()),
        translation_m=tuple(float(value) for value in translation.tolist()),
    )


def point_rank(points: Sequence[tuple[float, float, float]], *, tolerance: float = 1e-6) -> int:
    """Return the geometric rank of 3D points after subtracting their centroid."""
    if len(points) < 2:
        return 0
    try:
        import numpy as np  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return _point_rank_without_numpy(points, tolerance=tolerance)

    matrix = np.asarray(points, dtype=float)
    centered = matrix - matrix.mean(axis=0)
    return int(np.linalg.matrix_rank(centered, tol=tolerance))


def _point_rank_without_numpy(
    points: Sequence[tuple[float, float, float]],
    *,
    tolerance: float,
) -> int:
    origin = points[0]
    vectors = [_subtract(point, origin) for point in points[1:]]
    if not any(_norm(vector) > tolerance for vector in vectors):
        return 0

    first = next(vector for vector in vectors if _norm(vector) > tolerance)
    for vector in vectors:
        if _norm(_cross(first, vector)) > tolerance:
            return 2
    return 1


def _subtract(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _norm(vector: tuple[float, float, float]) -> float:
    return math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
