"""Tests for FakeDeskClient — no hardware required."""
from __future__ import annotations

import asyncio

import pytest

from ikea_desk_automation.desk.fake import FakeDeskClient


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_connect_disconnect() -> None:
    async def _inner() -> None:
        client = FakeDeskClient()
        assert not client._connected
        await client.connect()
        assert client._connected
        await client.disconnect()
        assert not client._connected

    _run(_inner())


def test_get_height_default() -> None:
    async def _inner() -> None:
        async with FakeDeskClient() as client:
            h = await client.get_height()
        assert h == pytest.approx(0.730, abs=1e-6)

    _run(_inner())


def test_get_height_custom_initial() -> None:
    async def _inner() -> None:
        async with FakeDeskClient(initial_height_m=1.125) as client:
            h = await client.get_height()
        assert h == pytest.approx(1.125, abs=1e-6)

    _run(_inner())


def test_get_speed() -> None:
    async def _inner() -> float | None:
        async with FakeDeskClient() as client:
            return await client.get_speed()

    assert _run(_inner()) == pytest.approx(0.0)


def test_get_status() -> None:
    async def _inner():  # type: ignore[no-untyped-def]
        async with FakeDeskClient(initial_height_m=0.799) as client:
            return await client.get_status()

    status = _run(_inner())
    assert status.height_m == pytest.approx(0.799, abs=1e-6)
    assert status.speed_ms == pytest.approx(0.0)


def test_move_to() -> None:
    async def _inner() -> float:
        async with FakeDeskClient() as client:
            await client.move_to(1.125)
            return await client.get_height()

    assert _run(_inner()) == pytest.approx(1.125, abs=1e-6)


def test_move_multiple_times() -> None:
    async def _inner() -> list[float]:
        results: list[float] = []
        async with FakeDeskClient() as client:
            for h in [0.730, 1.125, 0.799]:
                await client.move_to(h)
                results.append(await client.get_height())
        return results

    heights = _run(_inner())
    assert heights == pytest.approx([0.730, 1.125, 0.799], abs=1e-6)


def test_raises_when_not_connected() -> None:
    async def _inner() -> None:
        client = FakeDeskClient()
        with pytest.raises(RuntimeError, match="Not connected"):
            await client.get_height()

    _run(_inner())


def test_context_manager_disconnects_on_exit() -> None:
    async def _inner() -> bool:
        client = FakeDeskClient()
        async with client:
            pass
        return client._connected

    assert _run(_inner()) is False
