"""idasen-backed desk client.

idasen is imported lazily inside connect() so the module can be imported
without BLE hardware present.  Actual BLE communication requires bleak and
a working BlueZ stack.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ikea_desk_automation.desk.base import DeskClient, DeskStatus

if TYPE_CHECKING:
    from idasen import IdasenDesk


class IdasenDeskClient(DeskClient):
    """Thin wrapper around idasen.IdasenDesk.

    API reference (idasen 0.12):
        IdasenDesk(mac, exit_on_fail=False)
        await desk.connect() / await desk.disconnect()
        await desk.get_height() -> float   (metres)
        await desk.get_speed()  -> float   (m/s)
        await desk.move_to_target(target)  (metres)
    """

    def __init__(self, address: str) -> None:
        self._address = address
        self._desk: IdasenDesk | None = None

    async def connect(self) -> None:
        from idasen import IdasenDesk  # noqa: PLC0415

        self._desk = IdasenDesk(self._address, exit_on_fail=False)
        await self._desk.connect()

    async def disconnect(self) -> None:
        if self._desk is not None:
            await self._desk.disconnect()
            self._desk = None

    async def get_height(self) -> float:
        self._require_connected()
        return await self._desk.get_height()  # type: ignore[union-attr]

    async def get_speed(self) -> float | None:
        self._require_connected()
        try:
            return await self._desk.get_speed()  # type: ignore[union-attr]
        except (AttributeError, NotImplementedError):
            return None

    async def get_status(self) -> DeskStatus:
        height = await self.get_height()
        speed = await self.get_speed()
        return DeskStatus(height_m=height, speed_ms=speed)

    async def move_to(self, height_m: float) -> None:
        self._require_connected()
        await self._desk.move_to_target(height_m)  # type: ignore[union-attr]

    async def stop(self) -> None:
        self._require_connected()
        stop = getattr(self._desk, "stop", None)
        if stop is not None:
            await stop()

    def _require_connected(self) -> None:
        if self._desk is None:
            raise RuntimeError("Not connected — call connect() first.")
