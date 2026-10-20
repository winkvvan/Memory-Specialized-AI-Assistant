"""World Awareness Engine.

Sensors provide facts (observations). World Awareness turns them into the
structured World State, detects meaningful changes (emitting events only on
change), maintains an Activity Timeline of sessions, and provides the context
that the Behavior Engine will later use to decide how Chris should act.

Design principle (recorded in ARCHITECTURE.md):
> World Awareness observes the world; it does not own Chris's interpretation.
> Sensor = data source, World State = fact layer, Behavior = decision layer,
> LLM = reasoning engine.
"""

from __future__ import annotations

import json
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from chris.events import Event, EventBus, EventType
from chris.temporal.clock import ManualClock
from chris.temporal.system import TemporalSystem
from chris.world.sensors import WorldSensor
from chris.world.state import (
    ComputerState,
    UserActivity,
    UserAttention,
    UserBusyState,
    UserPresence,
    WorldState,
)


@dataclass
class ActivitySession:
    activity: str
    started_at: datetime
    ended_at: datetime | None = None

    def duration(self, now: datetime | None = None) -> timedelta:
        if self.ended_at is not None:
            return self.ended_at - self.started_at
        if now is not None:
            return now - self.started_at
        return timedelta(0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ActivitySession":
        return cls(
            activity=raw.get("activity", "UNKNOWN"),
            started_at=datetime.fromisoformat(raw["started_at"]),
            ended_at=datetime.fromisoformat(raw["ended_at"]) if raw.get("ended_at") else None,
        )


class WorldAwareness:
    """Turns observations into World State + events + activity timeline."""

    def __init__(
        self,
        state: WorldState | None = None,
        sensor: WorldSensor | None = None,
        path: Path | None = None,
        sessions_path: Path | None = None,
        event_bus: EventBus | None = None,
        temporal: TemporalSystem | None = None,
        idle_threshold: float = 300.0,
    ) -> None:
        self.state = state or WorldState()
        self.sensor = sensor
        self.path = path
        self.sessions_path = sessions_path or (
            path.with_name(path.stem + ".sessions.json") if path else None
        )
        self._bus = event_bus
        self._temporal = temporal or TemporalSystem()
        self._idle_threshold = idle_threshold
        self._sessions: list[ActivitySession] = []
        self._was_idle = False
        self._last_observation: dict[str, Any] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._load_sessions()

    # -- time -----------------------------------------------------------

    def _now(self) -> datetime:
        return self._temporal.now()

    # -- public ---------------------------------------------------------

    def ingest(self, observation: dict[str, Any]) -> list[EventType]:
        """Apply one sensor observation. Returns the events that fired."""
        with self._lock:
            self._last_observation = dict(observation)
            return self._ingest_locked(observation)

    def poll_once(self) -> list[EventType]:
        if self.sensor is None:
            return []
        return self.ingest(self.sensor.poll())

    def simulate(self, **fields: Any) -> list[EventType]:
        """Apply a simulated observation; optional duration advances the clock."""
        duration = fields.pop("duration", None)
        observation: dict[str, Any] = {
            "presence": fields.get("presence", "PRESENT"),
            "activity": fields.get("activity"),
            "attention": fields.get("attention"),
            "idle_seconds": float(fields.get("idle_seconds", 0)),
            "foreground_app": fields.get("foreground_app", ""),
            "window_title": fields.get("window_title", ""),
            "active": bool(fields.get("active", True)),
            "confidence": float(fields.get("confidence", 0.9)),
        }
        events = self.ingest(observation)
        if duration:
            clock = getattr(self._temporal, "_clock", None)
            if isinstance(clock, ManualClock):
                clock.advance(timedelta(seconds=float(duration)))
        return events

    def start_polling(self, interval: float = 1.0) -> None:
        if self._thread is not None or self.sensor is None:
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception:  # noqa: BLE001
                    pass
                _time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True, name="world-poll")
        self._thread.start()

    def stop_polling(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def timeline(self) -> list[ActivitySession]:
        return list(self._sessions)

    def current_session(self) -> ActivitySession | None:
        if self._sessions and self._sessions[-1].ended_at is None:
            return self._sessions[-1]
        return None

    def activity_duration(self) -> timedelta:
        session = self.current_session()
        if session is None:
            return timedelta(0)
        return session.duration(self._now())

    # -- inference (facts -> state; never the single sensor alone) ------

    def _infer_activity(self, obs: dict[str, Any]) -> UserActivity:
        idle = float(obs.get("idle_seconds", 0.0))
        explicit = obs.get("activity")
        if explicit:
            try:
                return UserActivity(explicit)
            except ValueError:
                pass
        if idle >= self._idle_threshold and not obs.get("active", True):
            return UserActivity.IDLE
        foreground = (obs.get("foreground_app") or "").lower()
        title = (obs.get("window_title") or "").lower()
        groups = {
            UserActivity.CODING: ["code", "visual studio", "pycharm", "idea", "sublime", "vim", "cursor", "neovim", ".py", ".c", ".cpp", ".java", ".ts"],
            UserActivity.BROWSING: ["chrome", "edge", "firefox", "browser", "opera"],
            UserActivity.WATCHING: ["vlc", "mpv", "potplayer", "youtube", "bilibili", "netflix", "player"],
            UserActivity.GAMING: ["game", "steam", "epic games", "ubisoft"],
            UserActivity.COMMUNICATING: ["wechat", "qq", "discord", "teams", "slack", "dingtalk", "telegram", "whatsapp"],
            UserActivity.WRITING: ["word", "wps", "typora", "notion", "docs", "powerpoint"],
            UserActivity.READING: ["pdf", "reader", "kindle", "epub", "calibre"],
        }
        for activity, keywords in groups.items():
            for keyword in keywords:
                if keyword in foreground or keyword in title:
                    return activity
        if idle >= self._idle_threshold:
            return UserActivity.IDLE
        # no evidence -> keep previous activity to avoid flapping
        return self.state.user.activity if self.state.user.activity is not UserActivity.UNKNOWN else UserActivity.UNKNOWN

    def _infer_attention(self, obs: dict[str, Any], activity: UserActivity) -> UserAttention:
        explicit = obs.get("attention")
        if explicit:
            try:
                return UserAttention(explicit)
            except ValueError:
                pass
        idle = float(obs.get("idle_seconds", 0.0))
        if activity in (UserActivity.CODING, UserActivity.WRITING, UserActivity.READING):
            return UserAttention.FOCUSED if idle < 60 else UserAttention.DISTRACTED
        if activity is UserActivity.WATCHING:
            return UserAttention.RELAXED
        if activity is UserActivity.IDLE or idle >= self._idle_threshold:
            return UserAttention.DISTRACTED
        return UserAttention.UNKNOWN

    def _infer_busy(self, activity: UserActivity, presence: UserPresence, idle: float) -> UserBusyState:
        if presence is UserPresence.AWAY:
            return UserBusyState.UNKNOWN
        if idle >= self._idle_threshold:
            # idle + watching/reading is ambiguous, not necessarily free
            if activity in (UserActivity.WATCHING, UserActivity.READING):
                return UserBusyState.UNKNOWN
            return UserBusyState.AVAILABLE
        if activity in (UserActivity.CODING, UserActivity.WRITING, UserActivity.COMMUNICATING):
            return UserBusyState.BUSY
        return UserBusyState.UNKNOWN

    # -- apply ----------------------------------------------------------

    def _ingest_locked(self, obs: dict[str, Any]) -> list[EventType]:
        state = self.state
        now = self._now()
        emitted: list[EventType] = []

        changed_observation = obs != self._last_observation
        self._last_observation = dict(obs)

        presence = self._enum(obs.get("presence"), UserPresence, UserPresence.UNKNOWN)
        activity = self._infer_activity(obs)
        attention = self._infer_attention(obs, activity)
        idle = float(obs.get("idle_seconds", 0.0))
        active = bool(obs.get("active", True))
        foreground = (obs.get("foreground_app") or "").strip()
        window_title = (obs.get("window_title") or "").strip()
        confidence = float(obs.get("confidence", 0.5))

        if changed_observation:
            self._emit(EventType.WORLD_OBSERVATION, {"observation": dict(obs), "confidence": confidence})

        if presence is not state.user.presence:
            state.user.presence = presence
            state.user_presence = presence is UserPresence.PRESENT
            emitted.append(EventType.USER_PRESENCE_CHANGED)
            self._emit(EventType.USER_PRESENCE_CHANGED, {"presence": presence.value})

        if activity is not state.user.activity:
            self._close_session(now)
            self._start_session(activity, now)
            state.user.activity = activity
            state.current_activity = activity.value.lower()
            state.activity_started_at = now
            state.last_activity_change = now
            emitted.append(EventType.USER_ACTIVITY_CHANGED)
            self._emit(EventType.USER_ACTIVITY_CHANGED, {"activity": activity.value})

        if attention is not state.user.attention:
            state.user.attention = attention
            emitted.append(EventType.USER_ATTENTION_CHANGED)
            self._emit(EventType.USER_ATTENTION_CHANGED, {"attention": attention.value})

        computer_changed = (
            active is not state.computer.active
            or foreground != state.computer.foreground_app
            or window_title != state.computer.window_title
        )
        if computer_changed:
            old_foreground = state.computer.foreground_app
            state.computer.active = active
            state.computer.foreground_app = foreground
            state.computer.window_title = window_title
            if foreground and foreground != old_foreground:
                emitted.append(EventType.FOREGROUND_APP_CHANGED)
                self._emit(EventType.FOREGROUND_APP_CHANGED, {"app": foreground})
            emitted.append(EventType.COMPUTER_STATE_CHANGED)
            self._emit(EventType.COMPUTER_STATE_CHANGED, state.computer.to_dict())

        state.computer.idle_seconds = idle
        now_idle = idle >= self._idle_threshold
        if now_idle and not self._was_idle:
            emitted.append(EventType.USER_IDLE_STARTED)
            self._emit(EventType.USER_IDLE_STARTED, {"idle_seconds": idle})
        elif not now_idle and self._was_idle:
            emitted.append(EventType.USER_IDLE_ENDED)
            self._emit(EventType.USER_IDLE_ENDED, {"idle_seconds": idle})
        self._was_idle = now_idle

        busy = self._infer_busy(activity, presence, idle)
        if busy is not state.user.estimated_state:
            state.user.estimated_state = busy
            emitted.append(EventType.COMPUTER_STATE_CHANGED)
            self._emit(EventType.COMPUTER_STATE_CHANGED, {"estimated_state": busy.value})

        state.updated_at = now
        self._save_sessions()
        if emitted:
            # only emit WORLD_STATE_CHANGED when something meaningful changed
            self._emit(EventType.WORLD_STATE_CHANGED, state.to_dict())
        return emitted

    # -- sessions -------------------------------------------------------

    def _start_session(self, activity: UserActivity, now: datetime) -> None:
        self._sessions.append(ActivitySession(activity=activity.value, started_at=now))

    def _close_session(self, now: datetime) -> None:
        if self._sessions and self._sessions[-1].ended_at is None:
            self._sessions[-1].ended_at = now

    def _load_sessions(self) -> None:
        if self.sessions_path is None or not self.sessions_path.exists():
            return
        try:
            raw = json.loads(self.sessions_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return
        self._sessions = [
            ActivitySession.from_dict(item) for item in raw if isinstance(item, dict)
        ]

    def _save_sessions(self) -> None:
        if self.sessions_path is None:
            return
        self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_path.write_text(
            json.dumps([s.to_dict() for s in self._sessions], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _enum(value, enum_cls, default):
        if value is None:
            return default
        try:
            return enum_cls(value)
        except ValueError:
            return default

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self._bus:
            self._bus.publish(Event(event_type, "world", payload))
