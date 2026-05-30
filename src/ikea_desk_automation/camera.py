"""RealSense camera availability, depth sampling, and conservative classification.

Imports pyrealsense2 lazily so the module can be used on systems without the
SDK installed (or without the optional [realsense] extra).  The live sampler is
read-only and returns an Observation for the automation state machine; it never
issues desk movement commands.
"""
from __future__ import annotations

import base64
import dataclasses
import math
import struct
import statistics
from collections.abc import Sequence
from typing import Any

from ikea_desk_automation.automation import Observation, OccupancyState
from ikea_desk_automation.config import CameraConfig


@dataclasses.dataclass
class CameraInfo:
    available: bool
    device_count: int = 0
    serial: str = ""
    message: str = ""


@dataclasses.dataclass
class DepthMetrics:
    available: bool
    width: int = 0
    height: int = 0
    image_data_url: str = ""
    valid_ratio: float = 0.0
    foreground_ratio: float = 0.0
    nearest_m: float | None = None
    median_m: float | None = None
    top_y: float | None = None
    centroid_y: float | None = None
    message: str = ""


@dataclasses.dataclass(frozen=True)
class CameraIntrinsics:
    """Depth stream intrinsics needed for pixel/depth deprojection."""

    width: int
    height: int
    fx: float
    fy: float
    ppx: float
    ppy: float
    model: str = ""
    coeffs: tuple[float, ...] = ()

    @classmethod
    def from_realsense(cls, intrinsics: Any) -> CameraIntrinsics:
        """Create serializable intrinsics from a pyrealsense2 intrinsics object."""
        return cls(
            width=int(intrinsics.width),
            height=int(intrinsics.height),
            fx=float(intrinsics.fx),
            fy=float(intrinsics.fy),
            ppx=float(intrinsics.ppx),
            ppy=float(intrinsics.ppy),
            model=str(getattr(intrinsics, "model", "")),
            coeffs=tuple(float(value) for value in getattr(intrinsics, "coeffs", ()) or ()),
        )


@dataclasses.dataclass(frozen=True)
class Point3D:
    x: float
    y: float
    z: float


@dataclasses.dataclass
class DeprojectedTargetMetrics:
    """Camera-space point summary for a known calibration target ROI."""

    available: bool
    width: int = 0
    height: int = 0
    intrinsics: CameraIntrinsics | None = None
    point_count: int = 0
    centroid_camera_m: Point3D | None = None
    median_camera_m: Point3D | None = None
    min_camera_m: Point3D | None = None
    max_camera_m: Point3D | None = None
    message: str = ""


@dataclasses.dataclass
class CalibrationAdvice:
    status: str
    messages: list[str]


def check_realsense() -> CameraInfo:
    """Return camera availability without starting a streaming pipeline."""
    try:
        import pyrealsense2 as rs  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError:
        return CameraInfo(available=False, message="pyrealsense2 not installed")

    try:
        ctx = rs.context()
        devices = ctx.query_devices()
        count = len(devices)
        if count == 0:
            return CameraInfo(available=False, device_count=0, message="no RealSense devices found")
        dev = devices[0]
        serial = dev.get_info(rs.camera_info.serial_number)
        return CameraInfo(available=True, device_count=count, serial=serial)
    except Exception as exc:  # noqa: BLE001
        return CameraInfo(available=False, message=str(exc))


def sample_realsense_depth(config: CameraConfig) -> DepthMetrics:
    """Sample RealSense depth frames and return ROI metrics.

    This function starts a depth-only RealSense pipeline briefly.  It depends on
    pyrealsense2 and numpy, both loaded lazily so normal CLI/config tests do not
    require RealSense dependencies.
    """
    if not config.enabled:
        return DepthMetrics(available=False, message="camera disabled in config")

    try:
        import numpy as np  # type: ignore[import-not-found]  # noqa: PLC0415
        import pyrealsense2 as rs  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError as exc:
        return DepthMetrics(available=False, message=f"RealSense dependency unavailable: {exc}")

    pipeline = rs.pipeline()
    rs_config = rs.config()
    if config.serial:
        rs_config.enable_device(config.serial)
    rs_config.enable_stream(rs.stream.depth, config.width, config.height, rs.format.z16, config.fps)

    try:
        profile = pipeline.start(rs_config)
        depth_sensor = profile.get_device().first_depth_sensor()
        depth_scale = float(depth_sensor.get_depth_scale())

        frames_to_collect = config.warmup_frames + config.sample_frames
        collected: list[Sequence[Sequence[float]]] = []
        for index in range(frames_to_collect):
            frames = pipeline.wait_for_frames()
            depth_frame = frames.get_depth_frame()
            if not depth_frame:
                continue
            if index < config.warmup_frames:
                continue
            depth = np.asanyarray(depth_frame.get_data()).astype(float) * depth_scale
            collected.append(depth.tolist())

        if not collected:
            return DepthMetrics(available=False, message="no depth frames captured")

        # Median-combine sampled frames to reduce transient noise.
        stack = np.asarray(collected, dtype=float)
        median_frame = np.median(stack, axis=0)
        metrics = depth_metrics_from_rows(median_frame.tolist(), config)
        metrics.image_data_url = depth_frame_bmp_data_url(median_frame.tolist(), config)
        return metrics
    except Exception as exc:  # noqa: BLE001
        return DepthMetrics(available=False, message=str(exc))
    finally:
        try:
            pipeline.stop()
        except Exception:  # noqa: BLE001
            pass


def sample_realsense_target_points(config: CameraConfig) -> tuple[DepthMetrics, DeprojectedTargetMetrics]:
    """Sample depth once and summarize deprojected camera-space target points.

    This is read-only and intended for known calibration target captures.  It
    reports the same ROI depth metrics as the posture sampler plus a compact
    point-cloud summary using RealSense depth intrinsics.
    """
    if not config.enabled:
        unavailable = DepthMetrics(available=False, message="camera disabled in config")
        return unavailable, DeprojectedTargetMetrics(available=False, message=unavailable.message)

    try:
        import numpy as np  # type: ignore[import-not-found]  # noqa: PLC0415
        import pyrealsense2 as rs  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError as exc:
        message = f"RealSense dependency unavailable: {exc}"
        unavailable = DepthMetrics(available=False, message=message)
        return unavailable, DeprojectedTargetMetrics(available=False, message=message)

    pipeline = rs.pipeline()
    rs_config = rs.config()
    if config.serial:
        rs_config.enable_device(config.serial)
    rs_config.enable_stream(rs.stream.depth, config.width, config.height, rs.format.z16, config.fps)

    try:
        profile = pipeline.start(rs_config)
        stream_profile = profile.get_stream(rs.stream.depth).as_video_stream_profile()
        intrinsics = CameraIntrinsics.from_realsense(stream_profile.get_intrinsics())
        depth_sensor = profile.get_device().first_depth_sensor()
        depth_scale = float(depth_sensor.get_depth_scale())

        frames_to_collect = config.warmup_frames + config.sample_frames
        collected: list[Sequence[Sequence[float]]] = []
        for index in range(frames_to_collect):
            frames = pipeline.wait_for_frames()
            depth_frame = frames.get_depth_frame()
            if not depth_frame:
                continue
            if index < config.warmup_frames:
                continue
            depth = np.asanyarray(depth_frame.get_data()).astype(float) * depth_scale
            collected.append(depth.tolist())

        if not collected:
            unavailable = DepthMetrics(available=False, message="no depth frames captured")
            return unavailable, DeprojectedTargetMetrics(available=False, message=unavailable.message)

        stack = np.asarray(collected, dtype=float)
        median_frame = np.median(stack, axis=0)
        rows = median_frame.tolist()
        metrics = depth_metrics_from_rows(rows, config)
        metrics.image_data_url = depth_frame_bmp_data_url(rows, config)
        return metrics, deprojected_target_metrics_from_rows(rows, config, intrinsics)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        unavailable = DepthMetrics(available=False, message=message)
        return unavailable, DeprojectedTargetMetrics(available=False, message=message)
    finally:
        try:
            pipeline.stop()
        except Exception:  # noqa: BLE001
            pass


def depth_metrics_from_rows(rows: Sequence[Sequence[float]], config: CameraConfig) -> DepthMetrics:
    """Compute conservative person/path metrics from a depth matrix in metres."""
    height = len(rows)
    width = len(rows[0]) if height else 0
    if width == 0:
        return DepthMetrics(available=False, message="empty depth frame")

    left = int(width * config.roi_left)
    right = max(left + 1, int(math.ceil(width * config.roi_right)))
    top = int(height * config.roi_top)
    bottom = max(top + 1, int(math.ceil(height * config.roi_bottom)))

    total = 0
    valid_depths: list[float] = []
    foreground_ys: list[int] = []

    for y in range(top, min(bottom, height)):
        row = rows[y]
        for x in range(left, min(right, len(row))):
            total += 1
            value = float(row[x])
            if not math.isfinite(value) or value <= 0:
                continue
            if config.min_depth_m <= value <= config.max_depth_m:
                valid_depths.append(value)
                foreground_ys.append(y)

    if total == 0:
        return DepthMetrics(available=False, width=width, height=height, message="empty ROI")

    if not valid_depths:
        return DepthMetrics(
            available=True,
            width=width,
            height=height,
            valid_ratio=0.0,
            foreground_ratio=0.0,
            message="no valid foreground depth",
        )

    foreground_ratio = len(valid_depths) / total
    nearest_m = min(valid_depths)
    median_m = statistics.median(valid_depths)
    min_y = min(foreground_ys)
    centroid_y = statistics.mean(foreground_ys)

    return DepthMetrics(
        available=True,
        width=width,
        height=height,
        valid_ratio=len(valid_depths) / total,
        foreground_ratio=foreground_ratio,
        nearest_m=nearest_m,
        median_m=median_m,
        top_y=min_y / max(height - 1, 1),
        centroid_y=centroid_y / max(height - 1, 1),
    )


def deprojected_target_metrics_from_rows(
    rows: Sequence[Sequence[float]],
    config: CameraConfig,
    intrinsics: CameraIntrinsics,
) -> DeprojectedTargetMetrics:
    """Deproject valid ROI depth pixels into camera-space summary points."""
    height = len(rows)
    width = len(rows[0]) if height else 0
    if width == 0:
        return DeprojectedTargetMetrics(available=False, message="empty depth frame")

    left = int(width * config.roi_left)
    right = max(left + 1, int(math.ceil(width * config.roi_right)))
    top = int(height * config.roi_top)
    bottom = max(top + 1, int(math.ceil(height * config.roi_bottom)))

    points: list[Point3D] = []
    for y in range(top, min(bottom, height)):
        row = rows[y]
        for x in range(left, min(right, len(row))):
            depth_m = float(row[x])
            if not math.isfinite(depth_m) or depth_m <= 0:
                continue
            if config.min_depth_m <= depth_m <= config.max_depth_m:
                points.append(deproject_pixel_to_point(intrinsics, x, y, depth_m))

    if not points:
        return DeprojectedTargetMetrics(
            available=True,
            width=width,
            height=height,
            intrinsics=intrinsics,
            message="no valid target points",
        )

    xs = [point.x for point in points]
    ys = [point.y for point in points]
    zs = [point.z for point in points]
    return DeprojectedTargetMetrics(
        available=True,
        width=width,
        height=height,
        intrinsics=intrinsics,
        point_count=len(points),
        centroid_camera_m=Point3D(statistics.mean(xs), statistics.mean(ys), statistics.mean(zs)),
        median_camera_m=Point3D(statistics.median(xs), statistics.median(ys), statistics.median(zs)),
        min_camera_m=Point3D(min(xs), min(ys), min(zs)),
        max_camera_m=Point3D(max(xs), max(ys), max(zs)),
    )


def deproject_pixel_to_point(
    intrinsics: CameraIntrinsics,
    x: int | float,
    y: int | float,
    depth_m: float,
) -> Point3D:
    """Convert a depth pixel into RealSense camera-space metres."""
    return Point3D(
        x=(float(x) - intrinsics.ppx) / intrinsics.fx * depth_m,
        y=(float(y) - intrinsics.ppy) / intrinsics.fy * depth_m,
        z=depth_m,
    )


def depth_frame_bmp_data_url(
    rows: Sequence[Sequence[float]],
    config: CameraConfig,
    *,
    max_width: int = 320,
) -> str:
    """Render a compact browser-safe BMP data URL from a depth frame."""
    height = len(rows)
    width = len(rows[0]) if height else 0
    if width == 0:
        return ""

    scale = max(1, math.ceil(width / max_width))
    out_width = max(1, width // scale)
    out_height = max(1, height // scale)
    pixels: list[bytes] = []
    depth_span = max(config.max_depth_m - config.min_depth_m, 0.001)

    for y in range(out_height):
        source_y = min(y * scale, height - 1)
        row = rows[source_y]
        for x in range(out_width):
            source_x = min(x * scale, len(row) - 1)
            value = float(row[source_x])
            if not math.isfinite(value) or value <= 0:
                red, green, blue = 18, 26, 23
            else:
                normalized = max(0.0, min(1.0, (value - config.min_depth_m) / depth_span))
                red, green, blue = _depth_heat_color(normalized)
            pixels.append(bytes((blue, green, red)))

    row_stride = ((out_width * 3 + 3) // 4) * 4
    padding = b"\0" * (row_stride - out_width * 3)
    image_rows = []
    for y in range(out_height - 1, -1, -1):
        offset = y * out_width
        image_rows.append(b"".join(pixels[offset : offset + out_width]) + padding)
    pixel_data = b"".join(image_rows)

    header_size = 14 + 40
    file_size = header_size + len(pixel_data)
    bmp = (
        b"BM"
        + struct.pack("<IHHI", file_size, 0, 0, header_size)
        + struct.pack("<IIIHHIIIIII", 40, out_width, out_height, 1, 24, 0, len(pixel_data), 2835, 2835, 0, 0)
        + pixel_data
    )
    encoded = base64.b64encode(bmp).decode("ascii")
    return f"data:image/bmp;base64,{encoded}"


def _depth_heat_color(normalized: float) -> tuple[int, int, int]:
    """Map near-to-far depth to warm-to-cool RGB colors."""
    if normalized < 0.5:
        t = normalized / 0.5
        return int(232 - 44 * t), int(76 + 124 * t), int(61 + 60 * t)
    t = (normalized - 0.5) / 0.5
    return int(188 - 98 * t), int(200 + 24 * t), int(121 + 118 * t)


def classify_depth_metrics(metrics: DepthMetrics, config: CameraConfig) -> Observation:
    """Convert depth metrics into an automation observation.

    The classifier is intentionally conservative.  It can distinguish away,
    standing, sitting, and unsafe path obstruction using configurable thresholds,
    but callers should still require sustained state and confidence before movement.
    """
    if not config.enabled:
        return Observation(OccupancyState.UNKNOWN, confidence=0.0)
    if not metrics.available:
        return Observation(OccupancyState.UNKNOWN, confidence=0.0)
    if metrics.nearest_m is not None and metrics.nearest_m < config.obstruction_distance_m:
        return Observation(OccupancyState.UNSAFE, confidence=1.0)
    if metrics.foreground_ratio < config.min_foreground_ratio or metrics.top_y is None:
        return Observation(OccupancyState.AWAY, confidence=max(config.min_confidence, 0.70))

    confidence = _confidence_from_ratio(metrics.foreground_ratio, config)
    if metrics.top_y <= config.standing_top_y:
        state = OccupancyState.STANDING
    else:
        state = OccupancyState.SITTING
    return Observation(state, confidence=confidence)


def sample_realsense_observation(config: CameraConfig) -> tuple[DepthMetrics, Observation]:
    """Sample the camera once and classify the result."""
    metrics = sample_realsense_depth(config)
    return metrics, classify_depth_metrics(metrics, config)


def recommend_camera_calibration(metrics: DepthMetrics, config: CameraConfig) -> CalibrationAdvice:
    """Return read-only calibration guidance for the current camera sample."""
    messages: list[str] = []
    if not config.enabled:
        return CalibrationAdvice("disabled", ["camera.enabled is false"])
    if not metrics.available:
        reason = metrics.message or "no depth metrics available"
        return CalibrationAdvice("unavailable", [reason])

    if metrics.nearest_m is not None and metrics.nearest_m < config.obstruction_distance_m:
        messages.append(
            "nearest depth is inside the obstruction zone "
            f"({metrics.nearest_m:.3f} m < {config.obstruction_distance_m:.3f} m)"
        )
        messages.append("narrow the ROI away from fixed desk hardware or raise the camera angle")
        messages.append("only increase obstruction_distance_m after confirming the desk path is clear")

    if metrics.foreground_ratio < config.min_foreground_ratio:
        messages.append(
            "foreground ratio is below the occupancy threshold "
            f"({metrics.foreground_ratio:.4f} < {config.min_foreground_ratio:.4f})"
        )
        messages.append("lower min_foreground_ratio or enlarge the ROI if the person is visible")

    if metrics.top_y is not None:
        if metrics.top_y <= config.standing_top_y:
            messages.append("current top_y falls in the standing range")
        else:
            messages.append("current top_y falls in the chair-sitting range")
        messages.append(
            "capture samples at chair-sitting, standing, and away positions before enabling automation"
        )
    else:
        messages.append("no top_y detected; verify camera aim and ROI")

    if metrics.valid_ratio > 0.90:
        messages.append("ROI is very broad; consider narrowing it to the body/desk path")

    status = "needs-calibration" if messages else "ok"
    return CalibrationAdvice(status, messages)


def _confidence_from_ratio(foreground_ratio: float, config: CameraConfig) -> float:
    if config.min_foreground_ratio <= 0:
        return 1.0
    scaled = foreground_ratio / (config.min_foreground_ratio * 5)
    return max(config.min_confidence, min(1.0, scaled))
