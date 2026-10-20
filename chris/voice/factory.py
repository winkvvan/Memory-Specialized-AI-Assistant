"""Voice provider factory: builds ASR/TTS/audio providers from config.

Never imports external ASR/TTS modules - external subsystems are invoked by
adapter (subprocess / HTTP). Provider selection is by name; unknown names fail
loudly.
"""

from __future__ import annotations

from chris.voice.asr import ASRProvider, ApiASRProvider, ChrisASRProvider, FakeASRProvider
from chris.voice.audio import (
    AudioInputProvider,
    AudioOutputProvider,
    FakeAudioInputProvider,
    FakeAudioOutputProvider,
    WindowsAudioInputProvider,
    WindowsAudioOutputProvider,
)
from chris.voice.config import VoiceConfig
from chris.voice.errors import VoiceError, VoiceErrorCode
from chris.voice.tts import ApiTTSProvider, FakeTTSProvider, HttpTTSProvider, TTSProvider


def build_asr(cfg: VoiceConfig, *, cwd: str | None = None) -> ASRProvider | None:
    if not cfg.asr_enabled:
        return None
    name = cfg.asr_provider
    if name == "fake":
        return FakeASRProvider()
    if name in ("api", "openai", "openai_asr"):
        return ApiASRProvider(
            base_url=cfg.asr_base_url,
            model=cfg.asr_model or "whisper-1",
            language=cfg.asr_language,
            api_key_env=cfg.asr_api_key_env,
            timeout=cfg.asr_timeout_seconds,
        )
    if name in ("chris", "chrisasr", "qwen3"):
        return ChrisASRProvider(command=list(cfg.asr_command), model=cfg.asr_model, cwd=cwd)
    raise VoiceError(VoiceErrorCode.ASR_NOT_AVAILABLE, f"unknown ASR provider {name!r}")


def build_tts(cfg: VoiceConfig) -> TTSProvider | None:
    if not cfg.tts_enabled:
        return None
    name = cfg.tts_provider
    if name == "fake":
        return FakeTTSProvider()
    if name in ("api", "openai", "openai_tts"):
        return ApiTTSProvider(
            base_url=cfg.tts_base_url,
            model=cfg.tts_model or "tts-1",
            voice=cfg.tts_voice or "alloy",
            response_format=cfg.tts_response_format or "wav",
            api_key_env=cfg.tts_api_key_env,
            timeout=cfg.tts_timeout_seconds,
        )
    if name in ("http", "tts-server", "qwen3"):
        from chris.voice.tts import HttpTTSProvider

        return HttpTTSProvider(
            base_url=cfg.tts_base_url,
            voice=cfg.tts_voice,
            seed=cfg.tts_seed,
        )
    raise VoiceError(VoiceErrorCode.TTS_NOT_AVAILABLE, f"unknown TTS provider {name!r}")


def build_audio_input(cfg: VoiceConfig) -> AudioInputProvider | None:
    if not cfg.input_enabled:
        return None
    name = cfg.input_backend
    if name == "fake":
        return FakeAudioInputProvider()
    if name == "windows":
        try:
            device = int(cfg.input_device) if cfg.input_device else -1
        except ValueError:
            device = -1
        return WindowsAudioInputProvider(
            device=device, sample_rate=cfg.input_sample_rate, channels=cfg.input_channels
        )
    raise VoiceError(VoiceErrorCode.INPUT_UNAVAILABLE, f"unknown input backend {name!r}")


def build_audio_output(cfg: VoiceConfig) -> AudioOutputProvider | None:
    if not cfg.output_enabled:
        return None
    name = cfg.output_backend
    if name == "fake":
        return FakeAudioOutputProvider()
    if name == "windows":
        return WindowsAudioOutputProvider(volume=cfg.output_volume)
    raise VoiceError(VoiceErrorCode.PLAYBACK_FAILED, f"unknown output backend {name!r}")
