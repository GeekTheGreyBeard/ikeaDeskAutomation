# Safety Notes

This project controls motorized furniture. Treat all movement as potentially hazardous.

## Baseline Rules

- Read-only status commands must work before movement commands are enabled.
- Movement must be limited to configured presets.
- Movement commands must require explicit user intent during early testing.
- Dry-run commands should be used before executed commands.
- Audible alerts should play before movement begins.
- A backing alarm should continue while the desk is in motion.
- Automation must include sustained-state detection and cooldowns.
- Automation must refuse movement when the camera is unavailable, confidence is low, or the desk path appears blocked.
- Manual controls must remain authoritative.
- Manual GeekDesk Control modes must create a persistent override latch that camera automation honors until explicitly cleared.
- Logs should explain why a movement was accepted or refused.
- Unsafe camera observations must be calibrated, not bypassed as defaults.

## Before Physical Movement Testing

- Confirm dry-run preset output for every configured preset.
- Confirm every preset is inside configured desk bounds.
- Run read-only `status`, `height`, and `speed` commands successfully.
- Run `camera` and `camera --sample --classify --calibrate` successfully when camera automation is enabled.
- Calibrate the camera ROI and posture thresholds with read-only observations before enabling automatic movement.
- Play and confirm the audible movement alert.
- Confirm the in-motion backing alarm is audible during movement.
- Keep a physical hand on or near the desk controller during first movement tests.
- Test the smallest safe height change first.
- Stop immediately if the desk stalls, overshoots, or reports unexpected height.

## RealSense Safety

RealSense sampling is read-only for `camera` commands. The experimental `monitor` command may move the desk only when `--execute` is explicitly supplied.

GeekDesk Control manual Sit/Stand/Away modes are authoritative. Activating a
manual mode writes `~/.config/ikea-desk/manual_override.json`; while that latch
is active, the monitor skips camera-driven moves and logs the skip. Clear the
override in GeekDesk Control before expecting camera automation to resume.

Treat these as hard blockers for autonomous movement:

- `unsafe` observation.
- `unknown` observation.
- Camera unavailable when `automation.camera_required` is true.
- Confidence below configured threshold.
- Uncalibrated ROI.
- Unverified obstruction threshold.
- Unstable USB/camera connection.

For first live monitor tests:

- Run `monitor` without `--execute` first and verify the printed decisions.
- Use `monitor --execute --stop-after-first-move` for one-direction supervised tests.
- Keep manual desk controls within reach.
- Start with a clear desk path and no pets, people, or loose equipment near the travel zone.
- Stop the monitor immediately if observations flicker between postures or report `unsafe`.
- Treat the configured away height as experimental until repeated away/return cycles are verified.

## BLE Safety

If BlueZ or BLE becomes unstable:

- Do not chain movement commands.
- Wait for current `ikea-desk` processes to finish.
- Check for stale `paplay`, `ikea-desk`, or `bluetoothctl` processes.
- Reconnect, re-pair, or rediscover the desk before retrying.
- Verify final height with `status` after every movement.

## Release Safety

Before making this repo public:

- Remove local hostnames, private paths, and private operational notes from public docs.
- Keep real MAC addresses and personal room details out of examples.
- Clearly mark automation as experimental until the safety loop is complete.
