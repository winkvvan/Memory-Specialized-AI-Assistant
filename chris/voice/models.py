"""Voice data models for Chris (Phase 9.3)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from chris.events import utcnow


class VoiceStage(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    LISTENING_ERROR = "LISTENING_ERROR"
    TRANSCRIPTION_ERROR = "TRANSCRIPTION_ERROR"
    CHAT_ERROR = "CHAT_ERROR"
    TTS_ERROR = "TTS_ERROR"
    PLAYBACK_ERROR = "PLAYBACK_ERROR"
    CANCELLED = "CANCELLED"


class VoiceState(str, Enum):
    IDLE = "IDLE"
    BUSY = "BUSY"


@dataclass
class VoiceSession:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    stage: VoiceStage = VoiceStage.IDLE
    started_at: datetime = field(default_factory=utcnow)
    user_text: str = ""
    reply_text: str = ""
    transcript: str = ""
    audio_path: str = ""
    error: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stage": self.stage.value,
            "error": self.error,
            "error_code": self.error_code,
        }
