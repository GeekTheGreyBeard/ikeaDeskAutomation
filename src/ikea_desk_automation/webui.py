"""Local settings UI for the IKEA desk automation prototype.

The UI is intentionally stdlib-only and read-only with respect to desk motion.
It can read live desk status and camera samples, but it never calls move_to().
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from ikea_desk_automation.camera import (
    check_realsense,
    classify_depth_metrics,
    sample_realsense_depth,
)
from ikea_desk_automation.config import AppConfig, DEFAULT_CONFIG_PATH
from ikea_desk_automation.desk.base import DeskStatus


def serve_ui(
    *,
    config_path: pathlib.Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    fake: bool = False,
) -> None:
    """Serve the local configuration UI until interrupted."""
    state = WebUiState(
        config_path=config_path or DEFAULT_CONFIG_PATH,
        fake=fake,
    )
    handler = _handler_for(state)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{httpd.server_port}/"
    print(f"ui: serving {url}")
    print("ui: no movement commands are exposed")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nui: stopped")
    finally:
        httpd.server_close()


class WebUiState:
    def __init__(self, *, config_path: pathlib.Path, fake: bool) -> None:
        self.config_path = config_path
        self.fake = fake

    def load_config(self) -> AppConfig:
        return AppConfig.load_or_default(self.config_path)

    def save_config(self, payload: dict[str, Any]) -> AppConfig:
        cfg = AppConfig.from_dict(payload)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(yaml.safe_dump(config_to_dict(cfg), sort_keys=False))
        return cfg

    def read_status(self) -> DeskStatus | None:
        cfg = self.load_config()
        if self.fake:
            return DeskStatus(height_m=cfg.presets.sit, speed_ms=0.0)
        if not cfg.desk.address:
            return None

        async def _read() -> DeskStatus:
            from ikea_desk_automation.desk.idasen_client import IdasenDeskClient  # noqa: PLC0415

            async with IdasenDeskClient(cfg.desk.address) as client:
                return await client.get_status()

        try:
            return asyncio.run(_read())
        except Exception as exc:  # noqa: BLE001
            print(f"ui: desk status unavailable: {exc}")
            return None

    def preview(self, *, sample_camera: bool = False) -> dict[str, Any]:
        cfg = self.load_config()
        camera = check_realsense()
        result: dict[str, Any] = {
            "camera": {
                "available": camera.available,
                "device_count": camera.device_count,
                "message": camera.message,
            },
            "metrics": None,
            "observation": None,
        }
        if sample_camera:
            metrics = sample_realsense_depth(cfg.camera)
            observation = classify_depth_metrics(metrics, cfg.camera)
            result["metrics"] = {
                "available": metrics.available,
                "width": metrics.width,
                "height": metrics.height,
                "image_data_url": metrics.image_data_url,
                "valid_ratio": metrics.valid_ratio,
                "foreground_ratio": metrics.foreground_ratio,
                "nearest_m": metrics.nearest_m,
                "median_m": metrics.median_m,
                "top_y": metrics.top_y,
                "centroid_y": metrics.centroid_y,
                "message": metrics.message,
            }
            result["observation"] = {
                "state": observation.state.value,
                "confidence": observation.confidence,
            }
        return result


def config_to_dict(cfg: AppConfig) -> dict[str, Any]:
    return {
        "desk": {
            "address": cfg.desk.address,
            "name": cfg.desk.name,
            "min_height_m": cfg.desk.min_height_m,
            "max_height_m": cfg.desk.max_height_m,
        },
        "presets": {
            "sit": cfg.presets.sit,
            "stand": cfg.presets.stand,
            "away": cfg.presets.away,
        },
        "automation": {
            "sustained_state_seconds": cfg.automation.sustained_state_seconds,
            "cooldown_seconds": cfg.automation.cooldown_seconds,
            "camera_required": cfg.automation.camera_required,
            "min_confidence": cfg.automation.min_confidence,
            "movement_timeout_seconds": cfg.automation.movement_timeout_seconds,
            "final_tolerance_m": cfg.automation.final_tolerance_m,
            "sample_interval_seconds": cfg.automation.sample_interval_seconds,
            "motion_speed_threshold_ms": cfg.automation.motion_speed_threshold_ms,
            "motion_height_delta_threshold_m": cfg.automation.motion_height_delta_threshold_m,
            "motion_settle_seconds": cfg.automation.motion_settle_seconds,
        },
        "audio": {
            "enabled": cfg.audio.enabled,
            "volume_percent": cfg.audio.volume_percent,
        },
        "camera": {
            "enabled": cfg.camera.enabled,
            "serial": cfg.camera.serial,
            "width": cfg.camera.width,
            "height": cfg.camera.height,
            "fps": cfg.camera.fps,
            "warmup_frames": cfg.camera.warmup_frames,
            "sample_frames": cfg.camera.sample_frames,
            "roi_left": cfg.camera.roi_left,
            "roi_top": cfg.camera.roi_top,
            "roi_right": cfg.camera.roi_right,
            "roi_bottom": cfg.camera.roi_bottom,
            "min_depth_m": cfg.camera.min_depth_m,
            "max_depth_m": cfg.camera.max_depth_m,
            "obstruction_distance_m": cfg.camera.obstruction_distance_m,
            "min_foreground_ratio": cfg.camera.min_foreground_ratio,
            "standing_top_y": cfg.camera.standing_top_y,
            "min_confidence": cfg.camera.min_confidence,
        },
    }


def _handler_for(state: WebUiState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self._send_html(INDEX_HTML)
                elif parsed.path == "/api/config":
                    self._send_json(
                        {
                            "config_path": str(state.config_path),
                            "config": config_to_dict(state.load_config()),
                        }
                    )
                elif parsed.path == "/api/status":
                    status = state.read_status()
                    self._send_json(
                        {
                            "status": None
                            if status is None
                            else {
                                "height_m": status.height_m,
                                "speed_ms": status.speed_ms,
                            }
                        }
                    )
                elif parsed.path == "/api/preview":
                    query = parse_qs(parsed.query)
                    sample = query.get("sample", ["0"])[0] in ("1", "true", "yes")
                    self._send_json(state.preview(sample_camera=sample))
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, "not found")
            except Exception as exc:  # noqa: BLE001
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path != "/api/config":
                    self._send_error(HTTPStatus.NOT_FOUND, "not found")
                    return
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    self._send_error(HTTPStatus.BAD_REQUEST, "expected JSON object")
                    return
                cfg = state.save_config(payload)
                self._send_json({"ok": True, "config": config_to_dict(cfg)})
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"ui: {self.address_string()} - {fmt % args}")

        def _send_html(self, body: str) -> None:
            encoded = body.encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, body: dict[str, Any]) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_error(self, status: HTTPStatus, message: str) -> None:
            encoded = json.dumps({"error": message}).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IKEA Desk Automation</title>
<style>
:root {
  color-scheme: light;
  --bg: #f6f7f4;
  --panel: #ffffff;
  --line: #cfd6cf;
  --text: #1d2520;
  --muted: #5c675f;
  --accent: #287a62;
  --warn: #b05b19;
  --danger: #a93636;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 20px;
  background: #223028;
  color: #fff;
}
h1 { margin: 0; font-size: 18px; font-weight: 650; }
main {
  display: grid;
  grid-template-columns: minmax(420px, 500px) minmax(480px, 1fr);
  min-height: calc(100vh - 58px);
}
aside {
  border-right: 1px solid var(--line);
  background: var(--panel);
  overflow: auto;
  padding: 16px;
}
section { padding: 16px 20px; }
fieldset {
  border: 1px solid var(--line);
  border-radius: 6px;
  margin: 0 0 14px;
  padding: 12px;
}
legend { color: var(--muted); font-weight: 650; padding: 0 6px; }
label {
  display: grid;
  grid-template-columns: 1fr minmax(132px, 180px);
  gap: 10px;
  align-items: center;
  margin: 8px 0;
}
input[type="text"], input[type="number"] {
  width: 100%;
  border: 1px solid #b9c2bb;
  border-radius: 4px;
  padding: 7px 8px;
  font: inherit;
}
input[type="checkbox"] { width: 18px; height: 18px; justify-self: end; }
.toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
button {
  border: 1px solid #1f604e;
  background: var(--accent);
  color: white;
  border-radius: 4px;
  padding: 8px 11px;
  font: inherit;
  cursor: pointer;
}
button.secondary { background: #fff; color: #1f604e; }
button:disabled { opacity: 0.55; cursor: not-allowed; }
.status {
  display: flex;
  gap: 12px;
  align-items: center;
  color: #d6e3dc;
  font-size: 13px;
}
.pill {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  border: 1px solid #97aa9f;
  border-radius: 999px;
  padding: 2px 9px;
  background: rgba(255,255,255,0.12);
}
.preview-grid {
  display: grid;
  grid-template-columns: minmax(320px, 1fr) minmax(260px, 340px);
  gap: 18px;
  align-items: start;
}
.frame {
  width: 100%;
  aspect-ratio: 4 / 3;
  border: 1px solid var(--line);
  background: #18231f;
}
.meter {
  position: relative;
  width: 96px;
  height: 360px;
  border: 1px solid var(--line);
  background: linear-gradient(#eef1ec, #dfe7df);
}
.desk-line, .preset-line {
  position: absolute;
  left: 0;
  right: 0;
  border-top: 2px solid var(--accent);
}
.preset-line { border-top: 1px dashed #6b766d; }
.meter-label {
  position: absolute;
  left: 104px;
  transform: translateY(-50%);
  white-space: nowrap;
  color: var(--muted);
  font-size: 12px;
}
.readout {
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 6px;
  padding: 12px;
}
.readout dl { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 10px; margin: 0; }
.readout dt { color: var(--muted); }
.readout dd { margin: 0; font-weight: 650; text-align: right; }
.notice { color: var(--muted); margin-top: 10px; }
.error { color: var(--danger); }
.ok { color: var(--accent); }
@media (max-width: 960px) {
  main, .preview-grid { grid-template-columns: 1fr; }
  aside { border-right: 0; border-bottom: 1px solid var(--line); }
}
</style>
</head>
<body>
<header>
  <h1>IKEA Desk Automation</h1>
  <div class="status">
    <span id="configPath" class="pill">loading config</span>
    <span id="saveState" class="pill">no movement controls</span>
  </div>
</header>
<main>
  <aside>
    <div class="toolbar">
      <button id="saveButton">Save Settings</button>
      <button class="secondary" id="reloadButton">Reload</button>
      <button class="secondary" id="sampleButton">Sample Camera</button>
    </div>
    <p id="message" class="notice"></p>
    <form id="settings"></form>
  </aside>
  <section>
    <div class="preview-grid">
      <div>
        <svg id="preview" class="frame" viewBox="0 0 640 480" role="img" aria-label="Camera preview overlay"></svg>
        <p class="notice">Live preview shows the latest sampled depth image with ROI and posture overlay. This page cannot move the desk.</p>
      </div>
      <div class="readout">
        <dl id="readout"></dl>
        <div id="meter" class="meter" aria-label="Desk height meter"></div>
      </div>
    </div>
  </section>
</main>
<script>
const fields = [
  ["desk", "name", "text"], ["desk", "address", "text"],
  ["desk", "min_height_m", "number"], ["desk", "max_height_m", "number"],
  ["presets", "sit", "number"],
  ["presets", "stand", "number"], ["presets", "away", "number"],
  ["automation", "sustained_state_seconds", "number"],
  ["automation", "cooldown_seconds", "number"],
  ["automation", "camera_required", "checkbox"],
  ["automation", "min_confidence", "number"],
  ["automation", "sample_interval_seconds", "number"],
  ["automation", "motion_speed_threshold_ms", "number"],
  ["automation", "motion_height_delta_threshold_m", "number"],
  ["automation", "motion_settle_seconds", "number"],
  ["audio", "enabled", "checkbox"], ["audio", "volume_percent", "number"],
  ["camera", "enabled", "checkbox"], ["camera", "serial", "text"],
  ["camera", "width", "number"], ["camera", "height", "number"],
  ["camera", "fps", "number"], ["camera", "roi_left", "number"],
  ["camera", "roi_top", "number"], ["camera", "roi_right", "number"],
  ["camera", "roi_bottom", "number"], ["camera", "min_depth_m", "number"],
  ["camera", "max_depth_m", "number"], ["camera", "obstruction_distance_m", "number"],
  ["camera", "min_foreground_ratio", "number"],
  ["camera", "standing_top_y", "number"],
  ["camera", "min_confidence", "number"]
];
let config = null;
let latestStatus = null;
let latestPreview = null;

function labelText(key) {
  return key.replaceAll("_", " ");
}

function buildForm() {
  const form = document.getElementById("settings");
  form.innerHTML = "";
  for (const section of ["desk", "presets", "automation", "audio", "camera"]) {
    const fs = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = section;
    fs.appendChild(legend);
    fields.filter(f => f[0] === section).forEach(([group, key, type]) => {
      const label = document.createElement("label");
      label.textContent = labelText(key);
      const input = document.createElement("input");
      input.dataset.group = group;
      input.dataset.key = key;
      input.type = type;
      if (type === "number") {
        input.step = key.includes("percent") || ["width", "height", "fps"].includes(key) ? "1" : "0.001";
      }
      input.addEventListener("input", () => {
        readForm();
        draw();
      });
      label.appendChild(input);
      fs.appendChild(label);
    });
    form.appendChild(fs);
  }
}

function populateForm() {
  for (const input of document.querySelectorAll("input[data-group]")) {
    const value = config[input.dataset.group][input.dataset.key];
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value ?? "";
  }
}

function readForm() {
  for (const input of document.querySelectorAll("input[data-group]")) {
    const group = input.dataset.group;
    const key = input.dataset.key;
    if (input.type === "checkbox") config[group][key] = input.checked;
    else if (input.type === "number") config[group][key] = Number(input.value);
    else config[group][key] = input.value;
  }
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "config load failed");
  config = data.config;
  document.getElementById("configPath").textContent = data.config_path;
  buildForm();
  populateForm();
  draw();
}

async function saveConfig() {
  readForm();
  setMessage("saving...", "");
  const response = await fetch("/api/config", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify(config)
  });
  const data = await response.json();
  if (!response.ok) {
    setMessage(data.error || "save failed", "error");
    return;
  }
  config = data.config;
  populateForm();
  setMessage("settings saved", "ok");
  draw();
}

async function pollStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    if (response.ok) latestStatus = data.status;
  } catch (_) {}
  draw();
}

async function samplePreview(sample=false) {
  try {
    const response = await fetch(`/api/preview${sample ? "?sample=1" : ""}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "preview failed");
    latestPreview = data;
    draw();
  } catch (error) {
    setMessage(error.message, "error");
  }
}

function setMessage(text, cls) {
  const el = document.getElementById("message");
  el.textContent = text;
  el.className = cls || "notice";
}

function draw() {
  if (!config) return;
  drawPreview();
  drawReadout();
  drawMeter();
}

function drawPreview() {
  const svg = document.getElementById("preview");
  const c = config.camera;
  const width = Number(c.width) || 640;
  const height = Number(c.height) || 480;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const roi = {
    x: c.roi_left * width,
    y: c.roi_top * height,
    w: (c.roi_right - c.roi_left) * width,
    h: (c.roi_bottom - c.roi_top) * height
  };
  const standingY = c.standing_top_y * height;
  const topY = latestPreview?.metrics?.top_y;
  const centroidY = latestPreview?.metrics?.centroid_y;
  const imageData = latestPreview?.metrics?.image_data_url;
  svg.innerHTML = `
    <rect x="0" y="0" width="${width}" height="${height}" fill="#18231f"/>
    <defs>
      <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
        <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#2d3b35" stroke-width="1"/>
      </pattern>
    </defs>
    ${imageData ? `<image href="${imageData}" x="0" y="0" width="${width}" height="${height}" preserveAspectRatio="none"/>` : ""}
    <rect x="0" y="0" width="${width}" height="${height}" fill="url(#grid)"/>
    <rect x="${roi.x}" y="${roi.y}" width="${roi.w}" height="${roi.h}" fill="rgba(40,122,98,0.16)" stroke="#39a982" stroke-width="3"/>
    <line x1="0" y1="${standingY}" x2="${width}" y2="${standingY}" stroke="#e9c46a" stroke-width="2"/>
    <text x="12" y="${Math.max(18, standingY - 10)}" fill="#e9c46a">standing top-y ${c.standing_top_y}</text>
    ${topY == null ? "" : `<line x1="${roi.x}" y1="${topY * height}" x2="${roi.x + roi.w}" y2="${topY * height}" stroke="#90e0ef" stroke-width="3"/>`}
    ${centroidY == null ? "" : `<circle cx="${roi.x + roi.w / 2}" cy="${centroidY * height}" r="8" fill="#90e0ef"/>`}
  `;
}

function drawReadout() {
  const metrics = latestPreview?.metrics;
  const obs = latestPreview?.observation;
  const camera = latestPreview?.camera;
  const rows = [
    ["desk height", latestStatus ? `${latestStatus.height_m.toFixed(3)} m` : "not read"],
    ["desk speed", latestStatus && latestStatus.speed_ms != null ? `${latestStatus.speed_ms.toFixed(3)} m/s` : "not read"],
    ["camera", camera ? (camera.available ? "available" : camera.message || "unavailable") : "not checked"],
    ["observation", obs ? `${obs.state} (${obs.confidence.toFixed(2)})` : "not sampled"],
    ["nearest", metrics?.nearest_m == null ? "n/a" : `${metrics.nearest_m.toFixed(3)} m`],
    ["foreground", metrics ? metrics.foreground_ratio.toFixed(4) : "n/a"],
    ["top y", metrics?.top_y == null ? "n/a" : metrics.top_y.toFixed(3)],
    ["centroid y", metrics?.centroid_y == null ? "n/a" : metrics.centroid_y.toFixed(3)]
  ];
  document.getElementById("readout").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

function drawMeter() {
  const meter = document.getElementById("meter");
  const min = config.desk.min_height_m;
  const max = config.desk.max_height_m;
  const current = latestStatus?.height_m;
  const meterHeight = 360;
  const toPixels = value => Math.max(0, Math.min(meterHeight, meterHeight - ((value - min) / (max - min) * meterHeight)));
  meter.innerHTML = "";
  const grouped = new Map();
  for (const [name, value] of Object.entries(config.presets)) {
    const key = Number(value).toFixed(3);
    const existing = grouped.get(key);
    if (existing) existing.names.push(name);
    else grouped.set(key, {value: Number(value), names: [name], current: false});
  }
  if (current != null) {
    const key = Number(current).toFixed(3);
    const existing = grouped.get(key);
    if (existing) existing.current = true;
    else grouped.set(key, {value: Number(current), names: [], current: true});
  }
  const entries = [...grouped.values()].sort((a, b) => toPixels(a.value) - toPixels(b.value));
  let lastLabelTop = -24;
  for (const entry of entries) {
    const top = toPixels(entry.value);
    const line = document.createElement("div");
    line.className = entry.current ? "desk-line" : "preset-line";
    line.style.top = `${top}px`;
    meter.appendChild(line);

    const label = document.createElement("span");
    label.className = "meter-label";
    const names = entry.names.join("/");
    const prefix = entry.current && names ? `live + ${names}` : entry.current ? "live" : names;
    label.textContent = `${prefix} ${entry.value.toFixed(3)} m`;
    lastLabelTop = Math.max(top, lastLabelTop + 22);
    label.style.top = `${Math.min(meterHeight - 8, lastLabelTop)}px`;
    meter.appendChild(label);
  }
}

document.getElementById("saveButton").addEventListener("click", saveConfig);
document.getElementById("reloadButton").addEventListener("click", loadConfig);
document.getElementById("sampleButton").addEventListener("click", () => samplePreview(true));
loadConfig().then(() => samplePreview(true)).catch(error => setMessage(error.message, "error"));
pollStatus();
setInterval(pollStatus, 3000);
</script>
</body>
</html>
"""


def add_ui_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ui_p = subparsers.add_parser("ui", help="Run the local settings and preview UI.")
    ui_p.add_argument("--host", default="127.0.0.1", help="Bind address for the local UI.")
    ui_p.add_argument("--port", type=int, default=8765, help="Bind port for the local UI.")
    ui_p.add_argument(
        "--open",
        action="store_true",
        help="Open the UI in the default browser after startup.",
    )
