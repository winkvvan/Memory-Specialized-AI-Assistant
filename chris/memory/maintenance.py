"""MemoryMaintenanceScheduler - connects the Temporal tick to MemoryMaintenance.

Single service used by BOTH the automatic path (Temporal TIME_TICK cadence) and
the manual CLI (`/memory maintenance`). Idempotent: without a time change it
never triggers a second run; each run only processes candidate memories.
"""

from __future__ import annotations

from datetime import datetime

from chris.events import Event, EventBus, EventType


class MemoryMaintenanceScheduler:
    def __init__(
        self,
        ecology,
        bus: EventBus | None,
        temporal,
        interval_seconds: int | None = None,
    ) -> None:
        self._ecology = ecology
        self._bus = bus
        self._temporal = temporal
        self._interval = interval_seconds or ecology._config.maintenance_interval_seconds

    def tick(self) -> dict | None:
        """Called on every Temporal tick (O(1) interval check). Returns a report
        only when a maintenance actually ran."""
        if not self._due():
            return None
        return self.run()

    def _due(self) -> bool:
        meta = self._ecology.maintenance_metadata()
        last = meta.get("last_maintenance_at")
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
        except (TypeError, ValueError):
            return True
        return (self._temporal.now() - last_dt).total_seconds() >= self._interval

    def run(self) -> dict:
        """Execute one maintenance pass (manual CLI and automatic share this)."""
        report = self._ecology.maintenance()
        now = self._temporal.now()
        self._ecology.record_maintenance(now)
        if self._bus:
            self._bus.publish(
                Event(
                    EventType.MEMORY_MAINTENANCE_CHECKPOINT,
                    "memory",
                    {
                        "timestamp": now.isoformat(),
                        "reason": "maintenance_interval",
                        "report": {
                            key: report.get(key)
                            for key in ("processed", "decayed", "archived", "normalized", "errors")
                        },
                    },
                )
            )
        return report
