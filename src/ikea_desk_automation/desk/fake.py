"""Fake in-memory desk client for tests and dry-run previews.

No Bluetooth hardware is touched.
"""
from __future__ import annotations

from ikea_desk_automation.desk.base import DeskClient, DeskStatus

_DEFAULT_HEIGHT_M = 0.730  # sitting height


class FakeDeskClient(DeskClient):
    """In-memory desk client.  State is stored on the instance; no I/O."""

    def __init__(self, initial_height_m: float = _DEFAULT_HEIGHT_M) -> None:
        self._height_m = initial_height_m
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def get_height(self) -> float:
        self._require_connected()
        return self._height_m

    async def get_speed(self) -> float | None:
        self._require_connected()
        return 0.0

    async def get_status(self) -> DeskStatus:
        return DeskStatus(height_m=await self.get_height(), speed_ms=await self.get_speed())

    async def move_to(self, height_m: float) -> None:
        self._require_connected()
        self._height_m = height_m

    async def stop(self) -> None:
        self._require_connected()

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first.")
