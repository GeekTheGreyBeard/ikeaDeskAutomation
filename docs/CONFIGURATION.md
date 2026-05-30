# Configuration Reference

The default config path is:

```bash
~/.config/ikea-desk/config.yaml
```

You can pass a different file with:

```bash
ikea-desk --config /path/to/config.yaml <command>
```

All heights are metres. Boolean values must be YAML booleans, `true` or `false`, not quoted strings.

## desk

```yaml
desk:
  address: "AA:BB:CC:DD:EE:FF"
  name: "my-desk"
  min_height_m: 0.620
  max_height_m: 1.270
```

- `address`: BLE address for the desk. Required for real hardware. Use `bluetoothctl scan on` to discover it.
- `name`: human-readable desk label.
- `min_height_m`: lowest allowed preset height.
- `max_height_m`: highest allowed preset height.

The loader rejects presets outside `min_height_m` and `max_height_m`.

## presets

```yaml
presets:
  sit: 0.730
  stand: 1.125
  away: 0.9275
```

- `sit`: normal chair-sitting height.
- `stand`: standing height.
- `away`: height to use when the user is away. If omitted, it defaults to halfway between `sit` and `stand`.

CLI names:

- `sit`
- `stand`
- `away`

## automation

```yaml
automation:
  sustained_state_seconds: 10
  cooldown_seconds: 45
  camera_required: true
  min_confidence: 0.55
  movement_timeout_seconds: 45
  final_tolerance_m: 0.010
  sample_interval_seconds: 2.0
  motion_speed_threshold_ms: 0.005
  motion_height_delta_threshold_m: 0.003
  motion_settle_seconds: 2.0
```

- `sustained_state_seconds`: how long the same observation must persist before automation may recommend movement.
- `cooldown_seconds`: validated post-move cooldown before repeated movement is allowed. The current beta default is `45` seconds after camera-log review; short camera flickers were about `0-5` seconds, while real posture holds were `15` seconds or longer.
- `camera_required`: when true, automation refuses movement if the camera is unavailable.
- `min_confidence`: observations below this confidence are ignored. This should align with the camera classifier's occupied-state confidence floor unless live calibration supports a higher threshold.
- `movement_timeout_seconds`: maximum time to wait for a movement command.
- `final_tolerance_m`: accepted difference between requested preset and final readback.
- `sample_interval_seconds`: default delay between monitor-loop camera samples.
- `motion_speed_threshold_ms`: desk speed at or above this value counts as active movement.
- `motion_height_delta_threshold_m`: fallback per-sample height change that counts as movement when speed is unavailable or unreliable.
- `motion_settle_seconds`: after detected movement stops, keep automation paused this long so posture classification can settle.

Current status: an experimental monitor exists as `ikea-desk monitor`, and first-pass systemd user-service files are available in `deploy/systemd/`. The service uses the same monitor loop and stays dry-run unless the local environment file explicitly adds `--execute`.
Use `monitor --stop-after-first-move` during supervised one-direction tests so the loop exits after the first accepted target action instead of continuing into the opposite posture direction.
After an executed sit/stand move, the monitor requires the camera to confirm the posture it just moved to before it accepts the opposite sit/stand posture. This guards against stale post-move classifications, such as a false `sitting` sample immediately after raising to standing.

The local UI can edit these values and show their live effect on the preview overlay:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml ui
```

The UI validates the complete config before saving. It writes normalized YAML, so comments from hand-edited config files are not preserved.

## audio

```yaml
audio:
  enabled: true
  volume_percent: 8
```

- `enabled`: enables audible pre-movement alerts and in-motion backing alarm for executed preset commands.
- `volume_percent`: default alert volume for idle-speaker alert behavior.

On PipeWire/PulseAudio systems, the implementation uses `pactl` and `paplay`.

Current alert behavior:

- Idle audio: unmute default sink, set configured volume, play backing alert.
- Active audio and sink unmuted: preserve current volume/mute state and play alert.
- Active audio and sink muted: mute active streams, unmute sink, play louder warning horn twice, restore active stream mute states.
- During movement: launch a generated industrial backing alarm loop and stop it after movement/readback completes or fails.

## camera

```yaml
camera:
  enabled: true
  serial: ""
  width: 640
  height: 480
  fps: 15
  warmup_frames: 5
  sample_frames: 3
  roi_left: 0.0
  roi_top: 0.0
  roi_right: 1.0
  roi_bottom: 1.0
  min_depth_m: 0.25
  max_depth_m: 4.0
  obstruction_distance_m: 0.35
  min_foreground_ratio: 0.01
  standing_top_y: 0.35
  min_confidence: 0.55
```

- `enabled`: enables camera diagnostics and sampling. Disabled cameras return unknown observations.
- `serial`: optional RealSense serial. Leave blank to use the first connected camera.
- `width`, `height`, `fps`: depth stream profile.
- `warmup_frames`: frames to discard before sampling.
- `sample_frames`: frames to median-combine for one sample.
- `roi_left`, `roi_top`, `roi_right`, `roi_bottom`: normalized region of interest. Values are 0.0 to 1.0. A narrowed lateral ROI, such as `0.38..0.60`, can approximate a centered work lane and reject people walking through adjacent floor space.
- `min_depth_m`: ignore depth values closer than this unless they trigger obstruction.
- `max_depth_m`: ignore depth values farther than this.
- `obstruction_distance_m`: nearest valid depth below this is classified as `unsafe`.
- `min_foreground_ratio`: minimum valid foreground coverage before the scene is treated as occupied.
- `standing_top_y`: topmost foreground pixel at or above this normalized y-value is classified as standing. Occupied samples below this threshold are classified as chair sitting.
- `min_confidence`: minimum confidence emitted by the camera classifier for occupied states.

Coordinate note: `top_y` is normalized image position. Lower values are higher in the image. ROI tightening is still an image-space safety gate; do not treat it as an exact physical 32-inch square until a camera-to-room transform is accepted for safety decisions.

## Calibration Workflow

Run this command in each physical state:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml camera --sample --classify --calibrate
```

Record:

- nearest depth
- foreground ratio
- top_y
- centroid_y
- observation
- calibration messages

Tune ROI first. Threshold changes come after ROI and camera aim are stable.

Recommended order:

1. Aim the camera so the user and desk travel path are visible.
2. Narrow ROI away from fixed desk hardware that appears very close.
3. Capture chair sitting, standing, away, and clear-path samples.
4. Set `obstruction_distance_m` only after a safe blocked-path test.
5. Set `standing_top_y` from observed chair-sitting and standing samples.
6. Re-run tests before enabling any automation.
