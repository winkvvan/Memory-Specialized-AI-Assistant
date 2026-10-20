"""Clock abstraction layer.

Business code must never call datetime.now() directly. All modules get time
through a Clock, so Chris's time axis can run real, accelerated or manual
(step-by-step) without touching any other module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_FIXED_OFFSETS = {
    "Asia/Shanghai": timedelta(hours=8),
    "Asia/Hong_Kong": timedelta(hours=8),
    "Asia/Taipei": timedelta(hours=8),
    "Asia/Macau": timedelta(hours=8),
    "Asia/Tokyo": timedelta(hours=9),
    "Asia/Seoul": timedelta(hours=9),
    "Asia/Singapore": timedelta(hours=8),
    "Asia/Kuala_Lumpur": timedelta(hours=8),
    "UTC": timedelta(0),
    "Etc/UTC": timedelta(0),
}


def localize(dt: datetime, timezone_name: str = "Asia/Shanghai") -> datetime:
    """Convert an aware datetime to the target timezone.

    Prefers zoneinfo; falls back to a fixed-offset table so it also works in
    the bundled Launcher.exe where tzdata may be unavailable.
    """
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(timezone_name))
    except Exception:  # noqa: BLE001
        offset = _FIXED_OFFSETS.get(timezone_name)
        if offset is None:
            return dt
        return dt + offset


class Clock(ABC):
    """Unified time source. Implementations share the same time domain."""

    @abstractmethod
    def now(self) -> datetime:
        """Current time on this clock's axis."""

    def timestamp(self) -> str:
        return self.now().isoformat()

    def elapsed_since(self, past: datetime) -> timedelta:
        return self.now() - past


class RealClock(Clock):
    """1 real second = 1 simulated second. Used for real operation."""

    def now(self) -> datetime:
        return utcnow()


class ManualClock(Clock):
    """Time only advances when advance() is called. Used for simulation/tests."""

    def __init__(self, initial: datetime | None = None) -> None:
        self._time = initial or utcnow()

    def now(self) -> datetime:
        return self._time

    def advance(self, delta: timedelta) -> None:
        self._time += delta


class AcceleratedClock(Clock):
    """1 real second = `scale` simulated seconds (e.g. scale=3600 => 1s = 1h)."""

    def __init__(self, scale: float = 1.0) -> None:
        self._scale = max(scale, 1e-6)
        self._real_start = utcnow()
        self._sim_start = utcnow()

    @property
    def scale(self) -> float:
        return self._scale

    def now(self) -> datetime:
        return self._sim_start + (utcnow() - self._real_start) * self._scale


def create_clock(mode: str = "real", scale: float = 1.0) -> Clock:
    if mode == "manual":
        return ManualClock()
    if mode == "accelerated":
        return AcceleratedClock(scale=scale)
    return RealClock()
