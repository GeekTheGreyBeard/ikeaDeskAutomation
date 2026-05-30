# OpenClaw Integration

This project includes a root `SKILL.md` so OpenClaw agents can operate it safely.

## What the Skill Does

The skill tells an OpenClaw agent how to:

- Install the Python package.
- Configure desk, preset, audio, automation, and camera options.
- Run read-only diagnostics first.
- Use RealSense calibration safely.
- Execute manual preset movement only after safety checks.
- Recover from common BlueZ/BLE connection failures.

The skill does not grant automatic permission to move furniture. Movement remains a physical-world action and should be treated as safety-sensitive.

## Install Into OpenClaw

Use whichever skill installation path your OpenClaw deployment supports.

Recommended local pattern:

```bash
mkdir -p ~/.openclaw/skills/ikea-desk-automation
cp SKILL.md ~/.openclaw/skills/ikea-desk-automation/SKILL.md
```

If your deployment uses a plugin or managed skill directory, place `SKILL.md` in the equivalent skill folder and reload/restart the agent runtime so the skill metadata is discovered.

## Agent Workflow

When asked to work with the desk, an OpenClaw agent should:

1. Read `SKILL.md`.
2. Confirm the repo path and config path.
3. Run read-only status checks.
4. Run camera diagnostics when RealSense is relevant.
5. Refuse to bypass unsafe camera observations.
6. Use dry-run preset commands before movement.
7. Ask for explicit human confirmation before physical movement during testing.
8. Report final desk height and process cleanup state.

## Useful Commands

Read-only:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml status
ikea-desk --config ~/.config/ikea-desk/config.yaml camera
ikea-desk --config ~/.config/ikea-desk/config.yaml camera --sample --classify --calibrate
```

Dry-run movement:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml preset stand
```

Executed movement:

```bash
ikea-desk --config ~/.config/ikea-desk/config.yaml preset stand --execute
```

## Current Safety Boundary

OpenClaw agents should not wire camera observations to automatic movement yet. The current RealSense path is for calibration and observation only. Autonomous movement should wait until:

- RealSense USB bandwidth is stable.
- ROI and thresholds are calibrated.
- Clear-path and blocked-path samples have been tested.
- Manual override behavior is implemented.
- A daemon or service loop has explicit safety gates and logs.
