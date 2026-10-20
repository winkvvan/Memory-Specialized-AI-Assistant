"""Presentation Model (ViewModel): a snapshot of Core state for the UI.

The UI never reads JSON files or touches Providers directly; it reads this
ViewModel, which is updated from the Core EventBus and the runtime.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field

# dynamic import so PyInstaller never bundles the chris package into Launcher.exe
EventType = importlib.import_module("chris.events").EventType


_VOICE_STATUS = {
    EventType.VOICE_SESSION_STARTED: "LISTENING",
    EventType.VOICE_LISTENING_STARTED: "LISTENING",
    EventType.VOICE_TRANSCRIPTION_STARTED: "THINKING",
    EventType.VOICE_TRANSCRIPTION_COMPLETED: "THINKING",
    EventType.VOICE_THINKING_STARTED: "THINKING",
    EventType.VOICE_SPEAKING_STARTED: "SPEAKING",
    EventType.VOICE_SPEAKING_COMPLETED: "IDLE",
    EventType.VOICE_SESSION_COMPLETED: "IDLE",
    EventType.VOICE_SESSION_FAILED: "IDLE",
    EventType.VOICE_SESSION_CANCELLED: "IDLE",
}


@dataclass
class EventLine:
    timestamp: str
    type: str
    source: str
    summary: str


class ViewModel:
    """Thread-safe snapshot holder, updated by bus events + explicit refresh."""

    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self._lock = threading.Lock()
        self.chris_status = "OFFLINE"
        self.provider_status = "UNKNOWN"
        self.provider_name = getattr(runtime.provider, "name", "?")
        self.model = getattr(runtime.provider, "model", "")
        self.theme = "light"
        self.world = runtime.world.to_dict()
        self.world_summary = "世界感知等待中"
        self.temporal: dict = {}
        self.session_str = "00:00"
        self.personality: dict = {}
        self.recent_memories: list = []
        self.recent_events: list[EventLine] = []
        self.version = 0
        bus = runtime.bus
        bus.subscribe(self._on_event, EventType.WORLD_STATE_CHANGED)
        bus.subscribe(self._on_event, EventType.USER_PRESENCE_CHANGED)
        bus.subscribe(self._on_event, EventType.USER_ACTIVITY_CHANGED)
        bus.subscribe(self._on_event, EventType.USER_ATTENTION_CHANGED)
        bus.subscribe(self._on_event, EventType.COMPUTER_STATE_CHANGED)
        bus.subscribe(self._on_event, EventType.FOREGROUND_APP_CHANGED)
        bus.subscribe(self._on_event, EventType.MEMORY_CREATED)
        bus.subscribe(self._on_event, EventType.MEMORY_REINFORCED)
        bus.subscribe(self._on_event, EventType.MEMORY_DORMANT)
        bus.subscribe(self._on_event, EventType.MEMORY_RECOVERED)
        bus.subscribe(self._on_event, EventType.SESSION_STARTED)
        bus.subscribe(self._on_event, EventType.SESSION_ENDED)
        bus.subscribe(self._on_event, EventType.TIME_PASSED)
        for evt in (
            EventType.VOICE_SESSION_STARTED,
            EventType.VOICE_LISTENING_STARTED,
            EventType.VOICE_TRANSCRIPTION_STARTED,
            EventType.VOICE_TRANSCRIPTION_COMPLETED,
            EventType.VOICE_THINKING_STARTED,
            EventType.VOICE_SPEAKING_STARTED,
            EventType.VOICE_SPEAKING_COMPLETED,
            EventType.VOICE_SESSION_COMPLETED,
            EventType.VOICE_SESSION_FAILED,
            EventType.VOICE_SESSION_CANCELLED,
        ):
            bus.subscribe(self._on_event, evt)
        self.refresh()

    def _on_event(self, event) -> None:
        with self._lock:
            line = EventLine(
                timestamp=event.timestamp.strftime("%H:%M:%S"),
                type=event.type.value,
                source=event.source,
                summary=self._summarize(event),
            )
            self.recent_events.append(line)
            self.recent_events = self.recent_events[-60:]
        self.refresh()
        status = _VOICE_STATUS.get(event.type)
        if status is not None:
            self.set_status(status)

    def _summarize(self, event) -> str:
        payload = event.payload or {}
        if event.type in (EventType.USER_MESSAGE, EventType.CHRIS_MESSAGE):
            content = payload.get("content", "")
            return content[:40]
        if event.type is EventType.WORLD_STATE_CHANGED:
            return f"activity={payload.get('user', {}).get('activity', '?')}"
        if event.type.value.startswith("VOICE_"):
            return f"voice:{payload.get('stage', '?')}"
        for key in ("activity", "presence", "attention", "memory_id", "hours", "days"):
            if key in payload:
                return f"{key}={payload[key]}"
        return ""

    def set_status(self, status: str) -> None:
        with self._lock:
            self.chris_status = status
            self.version += 1

    def set_provider_status(self, status: str) -> None:
        with self._lock:
            self.provider_status = status
            self.version += 1

    def refresh(self) -> None:
        with self._lock:
            self.world = self.runtime.world.to_dict()
            user = self.world.get("user", {})
            computer = self.world.get("computer", {})
            activity = user.get("activity", "UNKNOWN")
            app = computer.get("foreground_app") or ""
            if activity in ("CODING", "WRITING", "READING", "BROWSING", "COMMUNICATING", "GAMING", "WATCHING"):
                activity_zh = {
                    "CODING": "写代码", "WRITING": "写作", "READING": "阅读",
                    "BROWSING": "浏览网页", "COMMUNICATING": "交流", "GAMING": "玩游戏",
                    "WATCHING": "看视频",
                }.get(activity, activity)
                world_summary = f"正在 {activity_zh}" + (f"（{app}）" if app else "")
            elif activity == "IDLE":
                world_summary = "空闲中"
            else:
                world_summary = "世界感知等待中"
            self.temporal = {
                "current_time": self.runtime.temporal.now().strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": getattr(self.runtime.temporal, "_timezone", "?"),
                "known_duration": self.runtime.temporal.known_duration(),
                "session_active": self.runtime.temporal.session_active(),
                "session_minutes": int(self.runtime.temporal.session_uptime().total_seconds() // 60),
                "idle_seconds": int(self.runtime.temporal.idle_time().total_seconds()),
                "clock_mode": self.runtime.temporal.clock_mode(),
            }
            session_min = self.temporal["session_minutes"]
            self.world_summary = world_summary
            self.session_str = f"{session_min // 60:02d}:{session_min % 60:02d}"
            try:
                entries = self.runtime.memory.list_recent(limit=8, include_dormant=True)
                self.recent_memories = [
                    {
                        "kind": e.memory_type or e.kind,
                        "content": e.content[:60],
                        "age": self.runtime.temporal.humanize_age(e.timestamp),
                        "status": e.status,
                    }
                    for e in entries
                ]
            except Exception:  # noqa: BLE001
                self.recent_memories = []
            try:
                p = self.runtime.personality
                self.personality = {
                    "name": p.core.name,
                    "version": p.version,
                    "traits": len(p.core.core_traits),
                    "identity": (p.core.identity or p.core.base_character)[:120],
                    "adaptive": dict(p.adaptive.preferences),
                    "last_updated": p.adaptive.updated_at.strftime("%Y-%m-%d %H:%M")
                    if p.adaptive.updated_at
                    else "-",
                }
            except Exception:  # noqa: BLE001
                self.personality = {}
            self.version += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "chris_status": self.chris_status,
                "provider_status": self.provider_status,
                "provider_name": self.provider_name,
                "model": self.model,
                "theme": self.theme,
                "world": dict(self.world),
                "world_summary": self.world_summary,
                "temporal": dict(self.temporal),
                "session_str": self.session_str,
                "personality": dict(self.personality),
                "recent_memories": list(self.recent_memories),
                "recent_events": list(self.recent_events),
                "version": self.version,
            }
