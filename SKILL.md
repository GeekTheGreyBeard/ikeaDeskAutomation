---
name: ikea-desk-automation
description: Use when an OpenClaw agent needs to install, configure, diagnose, or operate a local IKEA/LINAK Bluetooth standing desk integration with optional Intel RealSense depth sensing, audible movement alerts, conservative preset-based movement, and safety-first calibration.
---

# IKEA Desk Automation

Use this skill for the `ikea-desk-automation` project. The integration controls a compatible IKEA/LINAK standing desk over BLE and can sample Intel RealSense depth data for read-only occupancy and obstruction observations.

## Safety Rules

- Treat every executed preset as physical movement.
- Start with read-only diagnostics: `status`, `height`, `speed`, and `camera`.
- Use dry-run preset commands before `--execute`.
- Do not bypass `unsafe`, `unknown`, low-confidence, or unavailable-camera states.
- Camera observations are calibration inputs until autonomous movement is explicitly implemented.
- Keep movement limited to configured presets.
- Keep audible alerts enabled unless the human explicitly disables them.
- If BLE becomes unstable, stop issuing movement commands and recover the connection first.

## Setup

From the repo root:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

For RealSense hosts:

```bash
python -m pip install -e ".[dev,realsense]"
```

Copy `config.example.yaml` to `~/.config/ikea-desk/config.yaml` or pass `--config <path>`.

For full option details, read `docs/CONFIGURATION.md`.

## Verify

Run these before movement:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml status
ikea-desk --config ~/.config/ikea-desk/config.yaml camera
ikea-desk --config ~/.config/ikea-desk/config.yaml camera --sample --classify --calibrate
ikea-desk --config ~/.config/ikea-desk/config.yaml preset stand
```

The preset command is a dry-run unless `--execute` is present.

## Move

Only after safety checks and human confirmation during testing:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml preset sit --execute
ikea-desk --config ~/.config/ikea-desk/config.yaml preset stand --execute
```

Expected behavior: pre-movement alert, in-motion backing alarm, movement, alarm cleanup, final height readback.

## BLE Recovery

If BlueZ reports `InProgress`, `br-connection-canceled`, or device-not-found:

1. Wait for the current `ikea-desk` process to finish.
2. Check stale processes with `ps -eo pid,ppid,stat,cmd | rg 'ikea-desk|paplay|bluetoothctl'`.
3. Disconnect stale state with `bluetoothctl disconnect <address>`.
4. If needed, remove and rediscover with `bluetoothctl remove <address>` and `bluetoothctl scan on`.
5. Re-pair/trust before retrying.
6. Verify final height with `status`.

## Docs To Read

- `docs/PROJECT_STATUS.md`: current state and known limits.
- `docs/CONFIGURATION.md`: every config option.
- `docs/OPENCLAW.md`: OpenClaw installation/use.
- `docs/SAFETY.md`: safety boundaries.
