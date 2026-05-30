"""Tests for the idasen-backed client without BLE hardware."""
from __future__ import annotations

import sys
import types
import asyncio

import pytest

from ikea_desk_automation.desk.idasen_client import IdasenDeskClient


class StubIdasenDesk:
    instances: list[StubIdasenDesk] = []

    def __init__(self, address: str, exit_on_fail: bool) -> None:
        self.address = address
        self.exit_on_fail = exit_on_fail
        self.connected = False
        self.disconnected = False
        self.height = 0.730
        self.speed = 0.0
        self.target: float | None = None
        self.stopped = False
        StubIdasenDesk.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def get_height(self) -> float:
        return self.height

    async def get_speed(self) -> float:
        return self.speed

    async def move_to_target(self, target: float) -> None:
        self.target = target
        self.height = target

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def fake_idasen_module(monkeypatch: pytest.MonkeyPatch) -> None:
    StubIdasenDesk.instances.clear()
    module = types.SimpleNamespace(IdasenDesk=StubIdasenDesk)
    monkeypatch.setitem(sys.modules, "idasen", module)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_connect_uses_exit_on_fail_false() -> None:
    client = IdasenDeskClient("AA:BB:CC:DD:EE:FF")
    _run(client.connect())

    desk = StubIdasenDesk.instances[0]
    assert desk.address == "AA:BB:CC:DD:EE:FF"
    assert desk.exit_on_fail is False
    assert desk.connected is True


def test_disconnect_clears_wrapped_desk() -> None:
    async def _inner() -> StubIdasenDesk:
        client = IdasenDeskClient("AA:BB:CC:DD:EE:FF")
        await client.connect()
        desk = StubIdasenDesk.instances[0]

        await client.disconnect()

        with pytest.raises(RuntimeError, match="Not connected"):
            await client.get_height()
        return desk

    desk = _run(_inner())
    assert desk.disconnected is True


def test_status_and_move_delegate_to_wrapped_desk() -> None:
    async def _inner():
        client = IdasenDeskClient("AA:BB:CC:DD:EE:FF")
        await client.connect()

        await client.move_to(1.125)
        return await client.get_status()

    status = _run(_inner())
    desk = StubIdasenDesk.instances[0]
    assert desk.target == pytest.approx(1.125)
    assert status.height_m == pytest.approx(1.125)
    assert status.speed_ms == pytest.approx(0.0)


def test_stop_delegates_when_available() -> None:
    async def _inner() -> None:
        client = IdasenDeskClient("AA:BB:CC:DD:EE:FF")
        await client.connect()
        await client.stop()

    _run(_inner())

    assert StubIdasenDesk.instances[0].stopped is True
