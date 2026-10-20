"""Configuration system for Chris."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


@dataclass
class LLMConfig:
    provider: str = "mock"
    model: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LLMConfig":
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    personality_file: str = "data/personality.json"
    memory_file: str = "data/chats/memory.json"
    world_state_file: str = "data/world.json"
    temporal_file: str = "data/temporal.json"
    log_file: str = "data/chris.log"
    log_level: str = "INFO"
    max_response_tokens: int = 512
    time_threshold_hours: tuple[int, ...] = (1, 8, 24)
    clock_mode: str = "real"
    time_scale: float = 1.0
    timezone: str = "Asia/Shanghai"
    test_mode: bool = False
    memory: dict[str, Any] | None = None
    world_sensor: str = "none"
    world_poll_interval: float = 0.0
    tools: dict[str, Any] | None = None
    voice: dict[str, Any] | None = None
    config_path: str | None = None

    def resolve(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("config_path", None)
        data["llm"] = self.llm.to_dict()
        data["time_threshold_hours"] = list(self.time_threshold_hours)
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        llm_raw = raw.get("llm", {})
        llm = LLMConfig.from_dict(llm_raw) if isinstance(llm_raw, dict) else LLMConfig()
        known = {k for k in cls.__dataclass_fields__ if k != "llm"}
        kwargs: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        kwargs["llm"] = llm
        if isinstance(kwargs.get("time_threshold_hours"), list):
            kwargs["time_threshold_hours"] = tuple(kwargs["time_threshold_hours"])
        return cls(**kwargs)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if path is None:
            path = os.environ.get("CHRIS_CONFIG")
        config_path = Path(path) if path else DEFAULT_CONFIG_PATH
        config = cls()
        if config_path.exists():
            try:
                raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
                config = cls.from_dict(raw)
            except (json.JSONDecodeError, OSError):
                pass
        config.config_path = str(config_path)
        if os.environ.get("CHRIS_TEST_MODE") == "1":
            config.test_mode = True
        return config

    def save(self, path: str | Path | None = None) -> None:
        if path is not None:
            config_path = Path(path)
        elif self.config_path:
            config_path = Path(self.config_path)
        else:
            config_path = DEFAULT_CONFIG_PATH
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
