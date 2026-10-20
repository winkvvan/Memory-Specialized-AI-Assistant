"""Temporal System - Chris's continuous internal time axis.

Not "what time is it now" but a running timeline: time state is persisted,
offline gaps are compensated on restart, layered time checks generate events,
and other modules (personality / memory / behavior) all depend on this module
for time. It never depends on any LLM.
"""

from __future__ import annotations

import json
import threading
import time as _time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from chris.events import Event, EventBus, EventType
from chris.temporal.clock import (
    AcceleratedClock,
    Clock,
    ManualClock,
    RealClock,
    create_clock,
    localize,
)

MINUTE_DURATION = timedelta(seconds=60)
HOUR_DURATION = timedelta(hours=1)


class TemporalSystem:
    """Continuous time state machine with layered checks and event emission."""

    def __init__(
        self,
        path: Path | None = None,
        event_bus: EventBus | None = None,
        threshold_hours: tuple[int, ...] = (1, 8, 24),
        clock: Clock | Callable[[], datetime] | None = None,
        timezone: str = "Asia/Shanghai",
        long_absence_days: float = 3.0,
        clock_scale: float = 1.0,
    ) -> None:
        self.path = path
        self._bus = event_bus
        self._timezone = timezone
        self._long_absence = timedelta(days=long_absence_days)
        self._clock_scale = clock_scale
        self._threshold_hours = sorted(set(threshold_hours))
        self._crossed: set[int] = set()

        self._known_since: datetime | None = None
        self._last_interaction: datetime | None = None
        self._session_start: datetime | None = None
        self._last_shutdown: datetime | None = None
        self._total_runtime = timedelta(0)
        self._last_day: date | None = None
        self._last_week: tuple[int, int] | None = None
        self._last_month: tuple[int, int] | None = None

        self._idle = False
        self._long_idle_fired = False
        self._long_session_fired = False

        self._event_log: deque[dict[str, Any]] = deque(maxlen=50)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ticker: threading.Thread | None = None

        if clock is not None:
            self._clock = clock
        else:
            self._clock = create_clock("real")
        self._load()

    # -- time source -----------------------------------------------------

    def _now(self) -> datetime:
        if hasattr(self._clock, "now"):
            return self._clock.now()
        return self._clock()

    def _local_now(self) -> datetime:
        return localize(self._now(), self._timezone)

    def now(self) -> datetime:
        return self._now()

    def clock(self) -> Clock:
        return self._clock

    def clock_mode(self) -> str:
        if isinstance(self._clock, ManualClock):
            return "manual"
        if isinstance(self._clock, AcceleratedClock):
            return "accelerated"
        return "real"

    def switch_clock(self, mode: str, scale: float | None = None) -> None:
        """Switch the active clock. Used by /time mode and simulation."""
        with self._lock:
            if mode == "manual":
                self._clock = ManualClock(self._now())
            elif mode == "accelerated":
                if scale is not None:
                    self._clock_scale = scale
                self._clock = AcceleratedClock(scale=self._clock_scale)
            else:
                self._clock = RealClock()
            self.checkpoint()

    # -- lifecycle -------------------------------------------------------

    def start_session(self) -> None:
        with self._lock:
            now = self._now()
            if self._known_since is None:
                self._known_since = now
            if self._last_shutdown is not None:
                offline = now - self._last_shutdown
                if offline > timedelta(seconds=1):
                    self._emit(EventType.TIME_SKIPPED, {"seconds": offline.total_seconds()})
                    if offline >= self._long_absence:
                        self._emit(
                            EventType.LONG_ABSENCE,
                            {"days": offline.days, "seconds": offline.total_seconds()},
                        )
            self._session_start = now
            self._emit(EventType.SESSION_STARTED, {"current_time": now.isoformat()})
            self.checkpoint()
            self._save()

    def end_session(self) -> None:
        with self._lock:
            now = self._now()
            if self._session_start is not None:
                self._total_runtime += now - self._session_start
            self._session_start = None
            self._last_shutdown = now
            self._emit(
                EventType.SESSION_ENDED,
                {"uptime_seconds": self.session_uptime().total_seconds()},
            )
            self._save()

    def record_interaction(self) -> None:
        with self._lock:
            self.checkpoint()
            now = self._now()
            if self._idle:
                idle_seconds = (
                    now - self._last_interaction
                ).total_seconds() if self._last_interaction else 0.0
                self._idle = False
                self._emit(EventType.IDLE_ENDED, {"idle_seconds": idle_seconds})
            self._last_interaction = now
            for hours in self._threshold_hours:
                if hours in self._crossed or self._known_since is None:
                    continue
                elapsed = now - self._known_since
                if elapsed.total_seconds() >= hours * 3600:
                    self._crossed.add(hours)
                    self._emit(EventType.TIME_THRESHOLD_REACHED, {"hours": hours})
            self._save()

    def record_tool_activity(
        self,
        tool_name: str,
        status: str,
        *,
        duration_seconds: float | None = None,
        call_id: str | None = None,
    ) -> None:
        """Record meaningful tool activity on the time axis.

        Only tool lifecycle steps (started / completed / failed) are recorded -
        not every internal function call. No permanent memory write happens here.
        """
        with self._lock:
            self._event_log.append(
                {
                    "type": f"TOOL_{status}",
                    "timestamp": self._now().isoformat(),
                    "payload": {
                        "tool": tool_name,
                        "status": status,
                        "duration_seconds": duration_seconds,
                        "call_id": call_id,
                    },
                }
            )

    # -- layered checks --------------------------------------------------

    def checkpoint(self) -> None:
        """Advance the timeline to the current clock time and detect crossings.

        Layered checks:
          Level 1 tick  -> TIME_TICK (emitted by ticker)
          Level 2 min   -> IDLE_STARTED
          Level 3 hour  -> LONG_IDLE / LONG_SESSION
          Level 4+      -> DAY_CHANGED / WEEK_CHANGED / MONTH_CHANGED
        """
        now = self._now()
        local = self._local_now()
        day = local.date()
        week = (local.isocalendar().year, local.isocalendar().week)
        month = (local.year, local.month)

        if self._last_day is None:
            self._last_day = day
            self._last_week = week
            self._last_month = month
            return

        if day != self._last_day:
            self._emit(
                EventType.DAY_CHANGED,
                {"previous": self._last_day.isoformat(), "current": day.isoformat()},
            )
        if week != self._last_week:
            self._emit(EventType.WEEK_CHANGED, {"current": f"{week[0]}-W{week[1]}"})
        if month != self._last_month:
            self._emit(EventType.MONTH_CHANGED, {"current": f"{month[0]}-{month[1]:02d}"})
        self._last_day = day
        self._last_week = week
        self._last_month = month

        base = self._last_interaction or self._known_since or now
        idle = max(timedelta(0), now - base)
        if idle >= MINUTE_DURATION and not self._idle:
            self._idle = True
            self._emit(EventType.IDLE_STARTED, {"idle_seconds": idle.total_seconds()})
        if idle >= HOUR_DURATION and not self._long_idle_fired:
            self._long_idle_fired = True
            self._emit(EventType.LONG_IDLE, {"idle_seconds": idle.total_seconds()})
        if (
            self._session_start is not None
            and now - self._session_start >= HOUR_DURATION
            and not self._long_session_fired
        ):
            self._long_session_fired = True
            self._emit(
                EventType.LONG_SESSION,
                {"session_seconds": (now - self._session_start).total_seconds()},
            )

    # -- heartbeat ticker ------------------------------------------------

    def start_ticker(self, interval: float = 1.0) -> None:
        if self._ticker is not None:
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                with self._lock:
                    self.checkpoint()
                    self._emit(
                        EventType.TIME_TICK,
                        {"current_time": self._now().isoformat()},
                    )
                _time.sleep(interval)

        self._ticker = threading.Thread(target=_loop, daemon=True, name="temporal-ticker")
        self._ticker.start()

    def stop_ticker(self) -> None:
        self._stop_event.set()
        if self._ticker is not None:
            self._ticker.join(timeout=2.0)
            self._ticker = None

    # -- simulation ------------------------------------------------------

    def advance(self, delta: timedelta) -> bool:
        """Advance time manually. Only works on the manual clock."""
        with self._lock:
            if not isinstance(self._clock, ManualClock):
                return False
            self._clock.advance(delta)
            self.checkpoint()
            self._emit(EventType.TIME_PASSED, {"seconds": delta.total_seconds()})
            self._save()
            return True

    # -- queries ---------------------------------------------------------

    def session_uptime(self) -> timedelta:
        if self._session_start is None:
            return timedelta(0)
        return self._now() - self._session_start

    def session_active(self) -> bool:
        return self._session_start is not None

    def idle_time(self) -> timedelta:
        if self._last_interaction is None:
            return timedelta(0)
        return self._now() - self._last_interaction

    def days_known(self) -> int:
        if self._known_since is None:
            return 0
        return max(0, (self._now() - self._known_since).days)

    def time_of_day(self) -> str:
        hour = self._local_now().hour
        if 5 <= hour < 12:
            return "morning"
        if 12 <= hour < 18:
            return "afternoon"
        if 18 <= hour < 23:
            return "evening"
        return "night"

    def total_runtime(self) -> timedelta:
        base = self._total_runtime
        if self._session_start is not None:
            base += self._now() - self._session_start
        return base

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._event_log)[-limit:]

    # -- Memory Ecology interface (future) -------------------------------

    def get_age(self, created_at: datetime) -> timedelta:
        return self._now() - created_at

    def get_elapsed(self, since: datetime) -> timedelta:
        return self._now() - since

    def calculate_time_factor(self, created_at: datetime, half_life: float) -> float:
        """Decay factor in [0,1]: 0.5 ** (age / half_life)."""
        age_seconds = self.get_age(created_at).total_seconds()
        if half_life <= 0:
            return 1.0
        return 0.5 ** (age_seconds / half_life)

    # -- prompt projection -----------------------------------------------

    def prompt_summary(self) -> str:
        """Minimal time context for the LLM. Never the full JSON."""
        local = self._local_now()
        idle = self.idle_time().total_seconds()
        return (
            f"Current time: {local:%Y-%m-%d %H:%M:%S} ({self._timezone}). "
            f"Last interaction {idle / 3600:.1f} hours ago."
        )

    def known_since_local(self) -> str:
        """Local date when Chris first met the user ('' if unknown)."""
        if self._known_since is None:
            return ""
        return localize(self._known_since, self._timezone).strftime("%Y-%m-%d")

    def local_str(self, dt: datetime) -> str:
        """Format an absolute datetime in the configured timezone (MM-DD HH:MM)."""
        return localize(dt, self._timezone).strftime("%m-%d %H:%M")

    def time_context(self) -> list[str]:
        """Authoritative absolute time reference for the LLM.

        The single source of truth for answering ANY time-span question.
        Rules out the model inferring time from conversation wording.
        """
        local = self._local_now()
        lines = [
            "Time reference (use these absolute values to answer any time question):",
            f"  Time of day: {self.time_of_day()}. Current time: {local:%Y-%m-%d %H:%M} ({self._timezone}).",
        ]
        if self._known_since is not None:
            known_local = localize(self._known_since, self._timezone)
            lines.append(
                f"  You have known the user for {self.known_duration()} "
                f"(first met {known_local:%Y-%m-%d %H:%M})."
            )
        if self._last_interaction is not None:
            idle = self.idle_time()
            lines.append(
                f"  Last interaction: {self.local_str(self._last_interaction)} "
                f"({idle.total_seconds() / 60:.0f} minutes ago)."
            )
        if self._session_start is not None:
            lines.append(f"  Session started: {self.local_str(self._session_start)}.")
        lines.append(
            "  Rule: compute every time span by subtracting these absolute timestamps. "
            "Never infer time from conversation wording."
        )
        return lines

    def humanize_age(self, since: datetime) -> str:
        """Human readable relative time: 刚刚 / N分钟前 / N小时前 / N天前 / N个月前 / N年前."""
        seconds = max(0, (self._now() - since).total_seconds())
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{int(seconds // 60)}分钟前"
        if seconds < 86400:
            return f"{int(seconds // 3600)}小时前"
        if seconds < 86400 * 30:
            return f"{int(seconds // 86400)}天前"
        if seconds < 86400 * 365:
            return f"{int(seconds // (86400 * 30))}个月前"
        return f"{int(seconds // (86400 * 365))}年前"

    def known_duration(self) -> str:
        """How long Chris has known the user, at natural granularity."""
        if self._known_since is None:
            return "just now"
        seconds = max(0, (self._now() - self._known_since).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)} minutes"
        if seconds < 86400:
            return f"{int(seconds // 3600)} hours"
        if seconds < 86400 * 30:
            return f"{int(seconds // 86400)} days"
        if seconds < 86400 * 365:
            return f"{int(seconds // (86400 * 30))} months"
        return f"{int(seconds // (86400 * 365))} years"

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        iso = lambda d: d.isoformat() if d else None
        return {
            "timezone": self._timezone,
            "known_since": iso(self._known_since),
            "last_interaction": iso(self._last_interaction),
            "session_start": iso(self._session_start),
            "last_shutdown_time": iso(self._last_shutdown),
            "total_runtime_seconds": self.total_runtime().total_seconds(),
            "last_day": self._last_day.isoformat() if self._last_day else None,
            "last_week": f"{self._last_week[0]}-W{self._last_week[1]}" if self._last_week else None,
            "last_month": (
                f"{self._last_month[0]}-{self._last_month[1]:02d}" if self._last_month else None
            ),
            "crossed_thresholds": sorted(self._crossed),
            "threshold_hours": list(self._threshold_hours),
        }

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return
        self._known_since = self._parse(raw.get("known_since"))
        self._last_interaction = self._parse(raw.get("last_interaction"))
        self._session_start = self._parse(raw.get("session_start"))
        self._last_shutdown = self._parse(raw.get("last_shutdown_time"))
        self._total_runtime = timedelta(seconds=float(raw.get("total_runtime_seconds", 0.0)))
        self._timezone = raw.get("timezone") or self._timezone
        day_raw = raw.get("last_day")
        self._last_day = date.fromisoformat(day_raw) if day_raw else None
        week_raw = raw.get("last_week")
        if week_raw:
            try:
                year_s, _, week_s = week_raw.partition("-W")
                self._last_week = (int(year_s), int(week_s))
            except (ValueError, TypeError):
                self._last_week = None
        month_raw = raw.get("last_month")
        if month_raw:
            try:
                year_s, _, month_s = month_raw.partition("-")
                self._last_month = (int(year_s), int(month_s))
            except (ValueError, TypeError):
                self._last_month = None
        self._crossed = set(int(h) for h in raw.get("crossed_thresholds", []) if isinstance(h, (int, float)))

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        self._event_log.append(
            {
                "type": event_type.value,
                "timestamp": self._now().isoformat(),
                "payload": dict(payload),
            }
        )
        if self._bus:
            self._bus.publish(Event(event_type, "temporal", payload))

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value)
