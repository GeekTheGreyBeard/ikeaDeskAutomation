"""Movement audio alert support.

The default implementation targets PulseAudio-compatible tools, which also work
on PipeWire systems that expose pactl/paplay. The alert uses a generated backing
beep pattern so the project does not need to ship binary audio assets.
"""
from __future__ import annotations

import dataclasses
import math
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Protocol


@dataclasses.dataclass
class SinkInputState:
    sink_input_id: str
    muted: bool


class MovementAlert(Protocol):
    def play(self) -> None:
        """Play a movement alert before desk motion."""

    def start_motion_sound(self) -> MotionSound:
        """Start a backing sound for the duration of desk motion."""


class MotionSound(Protocol):
    def stop(self) -> None:
        """Stop the movement backing sound."""


@dataclasses.dataclass
class PaplayMotionSound:
    process: subprocess.Popen[bytes]
    tempdir: tempfile.TemporaryDirectory[str]

    def stop(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        finally:
            self.tempdir.cleanup()


@dataclasses.dataclass
class PulseAudioMovementAlert:
    volume_percent: int = 8
    active_muted_volume_percent: int = 25
    active_muted_repeat_delay_seconds: float = 2.0
    pactl_bin: str = "pactl"
    paplay_bin: str = "paplay"

    def play(self) -> None:
        self._require_tools()
        audio_in_use = self.audio_in_use()
        muted = self.default_sink_muted()
        if audio_in_use and muted:
            stream_states = self.sink_input_states()
            self._run([self.pactl_bin, "set-sink-mute", "@DEFAULT_SINK@", "0"])
            self._run(
                [
                    self.pactl_bin,
                    "set-sink-volume",
                    "@DEFAULT_SINK@",
                    f"{self.active_muted_volume_percent}%",
                ]
            )
            self._play_flood_warning_sequence_with_stream_mute(stream_states)
            return

        if not audio_in_use:
            self._run([self.pactl_bin, "set-sink-mute", "@DEFAULT_SINK@", "0"])
            self._run([self.pactl_bin, "set-sink-volume", "@DEFAULT_SINK@", f"{self.volume_percent}%"])

        with tempfile.TemporaryDirectory(prefix="ikea-desk-alert-") as tmpdir:
            path = Path(tmpdir) / "backup-alert.wav"
            write_backup_alert_wav(path)
            self._run([self.paplay_bin, str(path)])

    def play_without_volume_change(self) -> None:
        self._require_tools()
        with tempfile.TemporaryDirectory(prefix="ikea-desk-alert-") as tmpdir:
            path = Path(tmpdir) / "backup-alert.wav"
            write_backup_alert_wav(path)
            self._run([self.paplay_bin, str(path)])

    def start_motion_sound(self) -> PaplayMotionSound:
        self._require_tools()
        tmpdir = tempfile.TemporaryDirectory(prefix="ikea-desk-motion-")
        path = Path(tmpdir.name) / "industrial-backing-loop.wav"
        write_industrial_backing_loop_wav(path)
        process = subprocess.Popen([self.paplay_bin, str(path)])
        return PaplayMotionSound(process=process, tempdir=tmpdir)

    def audio_in_use(self) -> bool:
        result = self._run([self.pactl_bin, "list", "short", "sink-inputs"], check=False)
        return bool(result.stdout.strip())

    def sink_input_ids(self) -> list[str]:
        result = self._run([self.pactl_bin, "list", "short", "sink-inputs"], check=False)
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]

    def sink_input_muted(self, sink_input_id: str) -> bool:
        result = self._run([self.pactl_bin, "list", "sink-inputs"], check=False)
        current_id: str | None = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Sink Input #"):
                current_id = stripped.removeprefix("Sink Input #")
            elif current_id == sink_input_id and stripped.startswith("Mute:"):
                return "yes" in stripped.lower()
        return False

    def sink_input_states(self) -> list[SinkInputState]:
        return [
            SinkInputState(sink_input_id=sink_input_id, muted=self.sink_input_muted(sink_input_id))
            for sink_input_id in self.sink_input_ids()
        ]

    def default_sink_muted(self) -> bool:
        result = self._run([self.pactl_bin, "get-sink-mute", "@DEFAULT_SINK@"], check=False)
        return "yes" in result.stdout.lower()

    def _play_flood_warning_sequence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ikea-desk-alert-") as tmpdir:
            path = Path(tmpdir) / "flood-warning-alert.wav"
            write_flood_warning_alert_wav(path)
            self._run([self.paplay_bin, str(path)])
            time.sleep(self.active_muted_repeat_delay_seconds)
            self._run([self.paplay_bin, str(path)])

    def _play_flood_warning_sequence_with_stream_mute(
        self, stream_states: list[SinkInputState]
    ) -> None:
        try:
            for state in stream_states:
                self._run([self.pactl_bin, "set-sink-input-mute", state.sink_input_id, "1"])
            self._play_flood_warning_sequence()
        finally:
            for state in stream_states:
                self._run(
                    [
                        self.pactl_bin,
                        "set-sink-input-mute",
                        state.sink_input_id,
                        "1" if state.muted else "0",
                    ],
                    check=False,
                )

    def _require_tools(self) -> None:
        missing = [
            tool
            for tool in (self.pactl_bin, self.paplay_bin)
            if shutil.which(tool) is None
        ]
        if missing:
            raise RuntimeError(f"missing audio tool(s): {', '.join(missing)}")

    @staticmethod
    def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, check=check, capture_output=True, text=True)


def write_backup_alert_wav(path: Path) -> None:
    sample_rate = 48_000
    beep_hz = 900
    beep_seconds = 0.22
    gap_seconds = 0.18
    repeats = 4
    amplitude = 0.38

    frames: list[int] = []
    beep_frames = int(sample_rate * beep_seconds)
    gap_frames = int(sample_rate * gap_seconds)

    for repeat in range(repeats):
        for i in range(beep_frames):
            t = i / sample_rate
            envelope = min(1.0, i / 800, (beep_frames - i) / 800)
            sample = amplitude * envelope * math.sin(2 * math.pi * beep_hz * t)
            frames.append(int(sample * 32767))
        if repeat != repeats - 1:
            frames.extend([0] * gap_frames)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in frames))


def write_industrial_backing_loop_wav(path: Path) -> None:
    """Write a long reverse-alarm loop for playback during desk motion."""
    sample_rate = 48_000
    beep_hz = 1000
    beep_seconds = 0.42
    gap_seconds = 0.30
    repeats = 80
    amplitude = 0.48

    frames: list[int] = []
    beep_frames = int(sample_rate * beep_seconds)
    gap_frames = int(sample_rate * gap_seconds)

    for _ in range(repeats):
        for i in range(beep_frames):
            t = i / sample_rate
            envelope = min(1.0, i / 900, (beep_frames - i) / 900)
            primary = math.sin(2 * math.pi * beep_hz * t)
            secondary = 0.35 * math.sin(2 * math.pi * (beep_hz * 0.5) * t)
            sample = amplitude * envelope * (primary + secondary) / 1.35
            frames.append(int(sample * 32767))
        frames.extend([0] * gap_frames)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in frames))


def write_flood_warning_alert_wav(path: Path) -> None:
    """Write a two-tone warning horn similar to outdoor flood/dam sirens."""
    sample_rate = 48_000
    tone_seconds = 1.2
    cycles = 3
    amplitude = 0.42
    low_hz = 430
    high_hz = 570

    frames: list[int] = []
    tone_frames = int(sample_rate * tone_seconds)

    for cycle in range(cycles):
        for i in range(tone_frames):
            progress = i / tone_frames
            frequency = low_hz + (high_hz - low_hz) * progress
            envelope = min(1.0, i / 1600, (tone_frames - i) / 1600)
            t = (cycle * tone_frames + i) / sample_rate
            sample = amplitude * envelope * math.sin(2 * math.pi * frequency * t)
            frames.append(int(sample * 32767))
        for i in range(tone_frames):
            progress = i / tone_frames
            frequency = high_hz - (high_hz - low_hz) * progress
            envelope = min(1.0, i / 1600, (tone_frames - i) / 1600)
            t = (cycle * tone_frames + tone_frames + i) / sample_rate
            sample = amplitude * envelope * math.sin(2 * math.pi * frequency * t)
            frames.append(int(sample * 32767))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in frames))
