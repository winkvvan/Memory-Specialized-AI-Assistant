"""Voice error model (structured, never tracebacks to the LLM/UI)."""

from __future__ import annotations

from enum import Enum
from typing import Any


class VoiceErrorCode(str, Enum):
    VOICE_DISABLED = "voice_disabled"
    VOICE_BUSY = "voice_pipeline_busy"
    INPUT_UNAVAILABLE = "input_unavailable"
    ASR_NOT_AVAILABLE = "asr_not_available"
    ASR_TIMEOUT = "asr_timeout"
    ASR_INVALID_AUDIO = "asr_invalid_audio"
    ASR_PROCESS_FAILED = "asr_process_failed"
    ASR_PROVIDER_ERROR = "asr_provider_error"
    TTS_NOT_AVAILABLE = "tts_not_available"
    TTS_TIMEOUT = "tts_timeout"
    TTS_GENERATION_FAILED = "tts_generation_failed"
    TTS_INVALID_AUDIO = "tts_invalid_audio"
    TTS_SERVER_ERROR = "tts_server_error"
    PLAYBACK_FAILED = "playback_failed"
    CANCELLED = "voice_cancelled"
    UNKNOWN = "voice_unknown"


class VoiceError(Exception):
    def __init__(self, code: VoiceErrorCode, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message}
