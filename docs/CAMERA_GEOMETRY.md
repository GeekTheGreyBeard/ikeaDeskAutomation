# Camera Geometry Model

Updated: 2026-05-29

This note describes the public calibration method for moving from normalized image-height thresholds to a physical coordinate model. Keep private room measurements, raw camera captures, and site-specific calibration logs outside the public repository unless they have been explicitly sanitized.

## Coordinate Frames

Use a consistent right-handed coordinate model.

### Room Frame

- `X_room`: left/right across the desk, positive left when facing the desk.
- `Y_room`: outward from the desk face toward the user and chair.
- `Z_room`: up from the floor.
- Origin: center of the desk face at floor level.

This frame matches the safety question: where are people, chairs, and objects relative to the clear desk-travel zone?

### Desk Frame

- `X_desk`: left/right across the desktop.
- `Y_desk`: front/back over the desktop.
- `Z_desk`: up from the desktop surface.

Record the mounted camera position in desk coordinates. The camera is mounted to the desk, so this transform should stay fixed unless the mount moves.

### Camera Frame

Use the RealSense SDK deprojection model for raw samples:

```text
pixel + depth + camera intrinsics -> point in camera frame
```

Then transform camera-frame points into room coordinates:

```text
P_room = T_room_desk * T_desk_camera * P_camera
```

`T_desk_camera` comes from calibration target captures. `T_room_desk` changes mainly with desk height.

## Measurements To Collect

Collect these locally and keep the raw values in a private calibration file:

- Desk surface width and depth.
- Lowest and highest usable desktop heights above the floor.
- Camera lens position relative to the desktop.
- Camera aim/orientation estimate, then a fitted transform from target captures.
- Clear floor zone in front of the desk.
- Normal sitting and standing user-lane boundaries.
- Chair footprint and any desk-interference dimensions.
- Any nearby fixed geometry that may appear in depth frames.

## Calibration Target Workflow

Start with a high-contrast calibration target of known width, depth, and height. Place it at measured floor positions in front of the desk, then run read-only target captures:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml camera --target-distance-in 24 --target-log calibration/target-24.json
ikea-desk --config ~/.config/ikea-desk/config.yaml camera --target-distance-in 48 --target-log calibration/target-48.json
ikea-desk --config ~/.config/ikea-desk/config.yaml camera --target-distance-in 48 --target-lateral-in 24 --target-log calibration/target-left-48.json
ikea-desk --config ~/.config/ikea-desk/config.yaml camera --target-distance-in 48 --target-lateral-in -24 --target-log calibration/target-right-48.json
```

The target command captures RealSense depth intrinsics and deprojects valid ROI pixels into camera-space point summaries. Each ROI prints and can log:

- raw ROI depth metrics
- depth stream intrinsics
- target point count
- camera-space centroid in metres
- camera-space median point in metres
- known room target center in inches

Calibration logs are ignored by git because they are local lab artifacts.

## Fit Saved Logs

Saved target logs can be checked without starting the camera:

```bash
ikea-desk camera --fit-target-log calibration/target-24.json --fit-target-log calibration/target-48.json
```

The fit command reads saved target captures, selects the `lower-narrow-center` ROI by default, reports the room/camera anchor rank, and only emits a rigid camera-to-room transform when the anchors are non-collinear. Centerline-only captures should report `underconstrained`; that is a useful guardrail, not a failure.

## Acceptance Criteria

Do not feed a fitted transform into posture or desk-path safety decisions until all of these are true:

- Centerline and left/right target captures are present.
- At least one lateral target placement is repeated or held out for validation.
- Transform RMSE is low enough for the intended safety margin.
- Predicted `X_room`, `Y_room`, and `Z_room` values match held-out placements.
- The clear desk-travel zone is validated with safe test objects.
- The model refuses movement for unsafe, unknown, low-confidence, or unavailable-camera states.

Until then, treat the camera path as conservative diagnostics plus image-space ROI gating, not as a complete physical safety model.
