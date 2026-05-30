"""Tests for generated movement audio alerts without playing sound."""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import pytest

from ikea_desk_automation.audio import (
    PulseAudioMovementAlert,
    write_backup_alert_wav,
    write_industrial_backing_loop_wav,
    write_flood_warning_alert_wav,
)


def test_write_backup_alert_wav(tmp_path: Path) -> None:
    path = tmp_path / "alert.wav"

    write_backup_alert_wav(path)

    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 48_000
        assert wav.getnframes() > 0


def test_write_flood_warning_alert_wav(tmp_path: Path) -> None:
    path = tmp_path / "flood.wav"

    write_flood_warning_alert_wav(path)

    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 48_000
        assert wav.getnframes() > 48_000


def test_write_industrial_backing_loop_wav(tmp_path: Path) -> None:
    path = tmp_path / "backing.wav"

    write_industrial_backing_loop_wav(path)

    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 48_000
        assert wav.getnframes() > 48_000 * 30


def test_alert_sets_idle_volume_then_plays(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_which(tool: str) -> str:
        return f"/usr/bin/{tool}"

    def fake_run(
        args: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:4] == ["pactl", "list", "short", "sink-inputs"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:3] == ["pactl", "get-sink-mute", "@DEFAULT_SINK@"]:
            return subprocess.CompletedProcess(args, 0, stdout="Mute: yes\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("ikea_desk_automation.audio.shutil.which", fake_which)
    monkeypatch.setattr("ikea_desk_automation.audio.subprocess.run", fake_run)

    PulseAudioMovementAlert(volume_percent=8).play()

    assert ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"] in calls
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "8%"] in calls
    assert any(call[0] == "paplay" for call in calls)


def test_alert_does_not_change_volume_when_audio_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_which(tool: str) -> str:
        return f"/usr/bin/{tool}"

    def fake_run(
        args: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:4] == ["pactl", "list", "short", "sink-inputs"]:
            return subprocess.CompletedProcess(args, 0, stdout="903\t59\t902\n", stderr="")
        if args[:3] == ["pactl", "get-sink-mute", "@DEFAULT_SINK@"]:
            return subprocess.CompletedProcess(args, 0, stdout="Mute: no\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("ikea_desk_automation.audio.shutil.which", fake_which)
    monkeypatch.setattr("ikea_desk_automation.audio.subprocess.run", fake_run)

    PulseAudioMovementAlert(volume_percent=8).play()

    assert ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"] not in calls
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "8%"] not in calls
    assert any(call[0] == "paplay" for call in calls)


def test_active_muted_alert_unmutes_sets_15_and_plays_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_which(tool: str) -> str:
        return f"/usr/bin/{tool}"

    def fake_run(
        args: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:4] == ["pactl", "list", "short", "sink-inputs"]:
            return subprocess.CompletedProcess(args, 0, stdout="903\t59\t902\n904\t59\t902\n", stderr="")
        if args[:3] == ["pactl", "list", "sink-inputs"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="Sink Input #903\n\tMute: no\nSink Input #904\n\tMute: yes\n",
                stderr="",
            )
        if args[:3] == ["pactl", "get-sink-mute", "@DEFAULT_SINK@"]:
            return subprocess.CompletedProcess(args, 0, stdout="Mute: yes\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("ikea_desk_automation.audio.shutil.which", fake_which)
    monkeypatch.setattr("ikea_desk_automation.audio.subprocess.run", fake_run)
    monkeypatch.setattr("ikea_desk_automation.audio.time.sleep", sleeps.append)

    PulseAudioMovementAlert(volume_percent=8).play()

    assert ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"] in calls
    assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "25%"] in calls
    assert ["pactl", "set-sink-input-mute", "903", "1"] in calls
    assert ["pactl", "set-sink-input-mute", "904", "1"] in calls
    assert calls.index(["pactl", "set-sink-input-mute", "903", "1"]) < calls.index(
        next(call for call in calls if call[0] == "paplay")
    )
    assert sum(1 for call in calls if call[0] == "paplay") == 2
    assert calls[-2:] == [
        ["pactl", "set-sink-input-mute", "903", "0"],
        ["pactl", "set-sink-input-mute", "904", "1"],
    ]
    assert sleeps == [2.0]


def test_missing_audio_tool_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ikea_desk_automation.audio.shutil.which", lambda _tool: None)

    with pytest.raises(RuntimeError, match="missing audio tool"):
        PulseAudioMovementAlert().play()


def test_motion_sound_starts_paplay_and_stop_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[list[str]] = []
    terminated: list[bool] = []

    class FakeProcess:
        def __init__(self, args: list[str]) -> None:
            self.args = args
            self._running = True

        def poll(self) -> int | None:
            return None if self._running else 0

        def terminate(self) -> None:
            terminated.append(True)
            self._running = False

        def wait(self, timeout: float) -> int:
            return 0

        def kill(self) -> None:
            self._running = False

    def fake_which(tool: str) -> str:
        return f"/usr/bin/{tool}"

    def fake_popen(args: list[str]) -> FakeProcess:
        popen_calls.append(args)
        return FakeProcess(args)

    monkeypatch.setattr("ikea_desk_automation.audio.shutil.which", fake_which)
    monkeypatch.setattr("ikea_desk_automation.audio.subprocess.Popen", fake_popen)

    handle = PulseAudioMovementAlert().start_motion_sound()
    handle.stop()

    assert popen_calls
    assert popen_calls[0][0] == "paplay"
    assert terminated == [True]
