"""Voice Pipeline (Phase 9.3): loo-definition voice interaction layer.

Bridges the independent ASR (Phase 9.1) and TTS (Phase 9.2) infrastructures
into Chris through small adapter interfaces. Chris only depends on the abstract
ASRProvider / TTSProvider / AudioInputProvider / AudioOutputProvider; the real
9.1/9.2 subsystems stay completely independent (never imported, never copied).
"""

from chris.voice.models import VoiceState, VoiceSession, VoiceStage
from chris.voice.errors import VoiceError, VoiceErrorCode

__all__ = [
    "VoiceError",
    "VoiceErrorCode",
    "VoiceSession",
    "VoiceStage",
    "VoiceState",
]
