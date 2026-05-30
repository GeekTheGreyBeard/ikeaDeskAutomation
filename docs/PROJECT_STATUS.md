# Project Status

Updated: 2026-05-29

## Phase

Functional hardware prototype with experimental RealSense-driven monitor automation and first-pass daemon deployment.

## Implemented

- Python package and CLI: `ikea-desk`.
- YAML config loader with validation.
- Direct BLE desk control through `idasen`.
- Fake desk client for tests.
- Read-only desk commands: `status`, `height`, `speed`.
- Preset commands for `sit`, `stand`, and `away`.
- Dry-run default for presets.
- Explicit `--execute` for physical movement.
- Preset bounds validation.
- Movement timeout and final height tolerance.
- Audible pre-movement alert.
- Active-stream aware warning behavior.
- Industrial backing alarm during movement.
- RealSense availability check.
- RealSense depth sampling and ROI metrics.
- Conservative observation classification: `unknown`, `sitting`, `standing`, `away`, `unsafe`.
- Read-only camera calibration advice.
- Experimental `monitor` command with sustained-state gating, same-state cooldown, desk motion detection, dry-run default, explicit `--execute`, configured away height, and posture-based return targets.
- Systemd user-service deployment files for running the monitor loop as a dry-run daemon before enabling executed movement.
- Local browser UI command, `ikea-desk ui`, for editing settings, polling read-only desk status, previewing camera ROI/posture thresholds, and taking one-shot camera samples without exposing movement controls.
- Public camera/room geometry calibration workflow in `docs/CAMERA_GEOMETRY.md` for moving from image-height thresholds to RealSense 3D coordinate transforms.
- Optional OpenClaw skill file.

## Hardware Verified

Tested on one local IKEA/LINAK desk setup with:

- Desk label: configured locally.
- Desk BLE address: configured locally, not published.
- Intel RealSense D455 serial: configured locally, not published.
- PipeWire/PulseAudio-compatible audio stack.

Verified desk presets:

- Chair sitting: 0.730 m.
- Standing: 1.125 m.
- Away: 0.9275 m.

Verified physical movement sequence:

- Chair sitting to standing.
- Standing to chair sitting.
- Supervised end-to-end monitor pass: sitting/standing/sitting/away behavior with direct local desk control.
- Randomized live monitor run with multiple sit/stand transitions, stable same-state cooldown behavior, and clean manual shutdown.
- Bounded 20-minute beta run using a `45s` cooldown, same-state gating, and post-move posture confirmation.

Final verified desk state: 0.730 m, stopped.

## Current RealSense Finding

The D455 can be detected and sampled with system Python. The project venv still needs `pyrealsense2` installed, a system-site-enabled environment, or the current `PYTHONPATH` bridge to the user/system Python packages.

Current desk-mounted D455 calibration cleanly separates true standing from chair sitting, but normal upright chair sitting and yoga-ball sitting were too close to classify reliably. Yoga-ball classification has been removed from the automation model; the live config should be treated as chair-sitting vs standing until repeated monitor cycles are verified.

Standing-height seated calibration note: when the desk was already at standing height, the earlier live config (`roi_top: 0.2`, `max_depth_m: 1.5`) could classify normal seated posture as `away` because the seated target was intermittently outside the valid depth window. The live config was adjusted to `roi_top: 0.3` and `max_depth_m: 2.0`; a dry-run one-shot monitor then sustained `sitting (1.00)` and reported it would move to the sitting preset. A follow-up supervised execute run sustained `sitting (1.00)`, moved to the sitting preset, read back the expected height, and stopped after the first accepted target action.

End-to-end monitor validation note: a supervised `monitor --execute --sample-interval 2` run accepted standing and sitting observations, moved between the configured sitting and standing presets, then accepted an away observation and reported that the desk was already near the configured away preset. Final readback matched the expected preset state.

Randomized monitor validation note: a supervised randomized run accepted multiple sit/stand transitions with good readbacks, then a detached monitor remained stable at `sitting (1.00)` with same-state cooldown active. The run was interrupted cleanly, no active monitor process remained, and final readback matched the expected sitting preset. Away was not separately accepted during the randomized run.

Away calibration note: normal empty-chair-in-frame sampling still classified as `sitting (1.00)` with foreground ratio about `0.2273`, while true sitting classified as `sitting (1.00)` with foreground ratio about `0.2870`. Pushing the chair fully outside the defined floor/ROI space and stepping away produced a clean `away (0.70)` read-only retest with `valid_ratio: 0.0000` and `foreground_ratio: 0.0000`. Until ROI/geometry logic is improved, away detection depends on the operating habit of moving the chair out of the ROI before leaving.

Cooldown validation note: the original 5-minute cooldown was too conservative for the camera behavior observed in beta logs. Short classification flickers were generally `0-5` seconds, while real posture holds were `15` seconds or longer. The committed default is now `45` seconds. A failed interim run showed that a false post-move `sitting` classification could lower the desk after a valid move to standing, so the monitor now requires the camera to re-confirm the moved-to sit/stand posture before accepting the opposite sit/stand posture.

Visitor-zone note: adjacent traffic in the broader floor space can trigger false movement decisions. The active test config narrowed the lateral ROI from a wide region to a centered lane as an image-space approximation of the expected user position. A read-only camera retest after the change still classified the seated primary user as `sitting (1.00)`. This is not yet a true physical safety zone; accepting that requires the camera-to-room transform to pass held-out validation.

## Test Status

Current gate:

```bash
pytest -q
ruff check .
```

Last verified result:

- 145 tests passed.
- Ruff clean.

## Not Yet Implemented

- Production-hardening around background daemon/service operations.
- Visual clear-path validation beyond nearest-depth obstruction.
- Accepted camera-to-desk transform for posture/safety decisions. Saved-log fitting exists, but the first non-collinear left target fit is still too noisy to trust.
- UI-drawn editable regions for direct drag calibration.
- Manual emergency pause/override service.
- Persistent event logs.
- Installation package/release artifact.

## Recommended Next Steps

1. Use `ikea-desk ui` to tune and review ROI, posture thresholds, motion thresholds, and preset heights.
2. Run `ikea-desk monitor` in dry-run mode through an away/return cycle.
3. Run `ikea-desk monitor --execute --stop-after-first-move` for supervised one-direction movement tests, with manual controls within reach and a clear desk path.
4. Capture matching left/right lateral target placements, then rerun the saved-log fit.
5. Add one repeated or held-out lateral placement before accepting a full camera-to-room transform; the first solvable left-anchor fit still has high RMSE.
6. Run the systemd user service in dry-run mode and validate logs/restart behavior after login or reboot.
7. Add manual pause/override controls.
8. Add persistent event logs.
