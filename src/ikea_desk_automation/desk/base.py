"""Abstract desk client interface."""
from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod


@dataclasses.dataclass
class DeskStatus:
    height_m: float
    speed_ms: float | None = None


class DeskClient(ABC):
    """Abstract desk client. Subclasses must implement all abstract methods.

    Supports use as an async context manager (connect on enter, disconnect on exit).
    """

    async def __aenter__(self) -> DeskClient:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def get_height(self) -> float:
        """Return current height in metres."""

    @abstractmethod
    async def get_speed(self) -> float | None:
        """Return current speed in m/s, or None if unavailable."""

    @abstractmethod
    async def get_status(self) -> DeskStatus:
        """Return a single height+speed snapshot."""

    @abstractmethod
    async def move_to(self, height_m: float) -> None:
        """Move desk to target height in metres."""

    async def stop(self) -> None:
        """Best-effort stop hook for implementations that support it."""
