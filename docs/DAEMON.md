# Daemon Deployment

This project uses the existing `ikea-desk monitor` loop as the daemon entrypoint.
The production path is a systemd user service so Bluetooth, RealSense, and audio
run in the same user context that already works during supervised testing.

The service starts in dry-run mode by default. Physical movement is not enabled
unless the local `service.env` file explicitly adds `--execute`.

## Files

- `deploy/systemd/ikea-desk-monitor.service`: systemd user service.
- `deploy/systemd/service.env.example`: local environment-file template.
- `~/.config/ikea-desk/config.yaml`: normal runtime configuration.
- `~/.config/ikea-desk/service.env`: local service override file.

## Install

Install the package or editable checkout first:

```bash
python -m pip install -e ".[realsense]"
```

Create the service config:

```bash
mkdir -p ~/.config/ikea-desk
cp deploy/systemd/service.env.example ~/.config/ikea-desk/service.env
```

If using a repo venv, edit `~/.config/ikea-desk/service.env` so `IKEA_DESK_BIN`
points at the venv console script, for example:

```bash
IKEA_DESK_BIN=/path/to/ikeaDeskAutomation/.venv/bin/ikea-desk
```

The service defaults `IKEA_DESK_CONFIG` to `~/.config/ikea-desk/config.yaml`.
Only set `IKEA_DESK_CONFIG` in `service.env` when using a different config path.

Install and start the user service:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/ikea-desk-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start ikea-desk-monitor.service
```

Enable it only after dry-run daemon behavior is stable:

```bash
systemctl --user enable ikea-desk-monitor.service
```

If the service should start before an interactive login, enable linger for the
service user:

```bash
loginctl enable-linger "$USER"
```

## Logs And Control

GeekDesk Control is the preferred local control surface for normal operation.
It can stop/start the user service, switch back to dry-run, enforce the
**Off -> Execute Mode -> On** guardrail, and show whether the monitor heartbeat
proves the loop is actually sampling. See [GEEKDESK_CONTROL.md](GEEKDESK_CONTROL.md)
for the screenshots, Mermaid state flow, and implementation notes.

Check status:

```bash
systemctl --user status ikea-desk-monitor.service
```

Follow logs:

```bash
journalctl --user -u ikea-desk-monitor.service -f
```

Stop the service before manual calibration, direct BLE troubleshooting, or any
other command that needs exclusive desk/camera access:

```bash
systemctl --user stop ikea-desk-monitor.service
```

## Dry-Run First

The default environment file keeps the daemon in dry-run mode:

```bash
IKEA_DESK_MONITOR_ARGS=monitor
```

This should be the first production-style validation target. It verifies service
startup, camera access, Bluetooth reads, logs, restart behavior, and posture
decisions without moving the desk.

## Enabling Movement

Only after supervised validation, prefer the GeekDesk Control sequence:

1. Turn automation **Off**.
2. Select **Execute Mode**.
3. Turn automation **On**.

That sequence writes the same service setting shown below, but prevents changing
into execute mode behind a running monitor process.

For manual service-file administration, edit `~/.config/ikea-desk/service.env`:

```bash
IKEA_DESK_MONITOR_ARGS=monitor --execute
```

Then restart:

```bash
systemctl --user restart ikea-desk-monitor.service
```

Keep manual controls within reach during the first live service runs. If camera
classification becomes unstable, return the service to dry-run mode and retest.

## Production Readiness Gate

Before treating this as production-ready:

- Service starts in dry-run mode after reboot/login.
- Journald logs show stable camera observations and no restart loop.
- The narrowed ROI still classifies the primary user correctly when sitting and standing.
- Adjacent visitor traffic stays outside the accepted work lane.
- `monitor --execute` has been validated under direct supervision.
- Manual stop through `systemctl --user stop ikea-desk-monitor.service` works.
- No concurrent foreground monitor process is running.
