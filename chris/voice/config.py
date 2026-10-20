"""Voice configuration (from the existing config.json `voice` section).

Kept as plain dict parsing with safe defaults; no new config system.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VoiceConfig:
    enabled: bool = False

    # ASR
    asr_enabled: bool = False
    asr_provider: str = "fake"
    # command = list of args (parameter array, never a shell string)
    asr_command: tuple[str, ...] = ()
    asr_timeout_seconds: float = 120.0
    asr_model: str = ""
    asr_language: str = "Chinese"
    asr_base_url: str = "https://api.openai.com/v1"
    asr_api_key_env: str = "CHRIS_API_KEY"

    # TTS
    tts_enabled: bool = False
    tts_provider: str = "fake"
    tts_base_url: str = "http://127.0.0.1:8090"
    tts_voice: str = ""
    tts_timeout_seconds: float = 60.0
    tts_auto_start: bool = False
    tts_model: str = "tts-1"
    tts_api_key_env: str = "CHRIS_API_KEY"
    tts_response_format: str = "wav"
    # command = parameter array to launch the local tts-server (never a shell string)
    tts_command: tuple[str, ...] = ()
    tts_cuda_bin: str = ""
    tts_voice_spk: str = ""
    tts_voice_rvq: str = ""
    tts_voice_ref_text: str = ""
    # -1 = random seed per synthesis (server default); a fixed seed makes
    # cloned-voice output deterministic and avoids the leading rumble frames.
    tts_seed: int = -1

    # input / output
    input_enabled: bool = False
    input_backend: str = "fake"
    input_sample_rate: int = 16000
    input_channels: int = 1
    input_max_duration_seconds: float = 30.0
    input_device: str = ""

    output_enabled: bool = False
    output_backend: str = "fake"
    output_volume: float = 1.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "VoiceConfig":
        raw = raw or {}
        asr = raw.get("asr", {}) or {}
        tts = raw.get("tts", {}) or {}
        inp = raw.get("input", {}) or {}
        out = raw.get("output", {}) or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            asr_enabled=bool(asr.get("enabled", False)),
            asr_provider=asr.get("provider", "fake"),
            asr_command=tuple(asr.get("command", []) or []),
            asr_timeout_seconds=float(asr.get("timeout_seconds", 120.0)),
            asr_model=asr.get("model", ""),
            asr_language=asr.get("language", "Chinese"),
            asr_base_url=asr.get("base_url", "https://api.openai.com/v1"),
            asr_api_key_env=asr.get("api_key_env", "CHRIS_API_KEY"),
            tts_enabled=bool(tts.get("enabled", False)),
            tts_provider=tts.get("provider", "fake"),
            tts_base_url=tts.get("base_url", "http://127.0.0.1:8090"),
            tts_voice=tts.get("voice", ""),
            tts_timeout_seconds=float(tts.get("timeout_seconds", 60.0)),
            tts_auto_start=bool(tts.get("auto_start", False)),
            tts_model=tts.get("model", "tts-1"),
            tts_api_key_env=tts.get("api_key_env", "CHRIS_API_KEY"),
            tts_response_format=tts.get("response_format", "wav"),
            tts_command=tuple(tts.get("command", []) or []),
            tts_cuda_bin=tts.get("cuda_bin", ""),
            tts_voice_spk=tts.get("voice_spk", ""),
            tts_voice_rvq=tts.get("voice_rvq", ""),
            tts_voice_ref_text=tts.get("voice_ref_text", ""),
            tts_seed=int(tts.get("seed", -1)),
            input_enabled=bool(inp.get("enabled", False)),
            input_backend=inp.get("backend", "fake"),
            input_sample_rate=int(inp.get("sample_rate", 16000)),
            input_channels=int(inp.get("channels", 1)),
            input_max_duration_seconds=float(inp.get("max_duration_seconds", 30.0)),
            input_device=inp.get("device", ""),
            output_enabled=bool(out.get("enabled", False)),
            output_backend=out.get("backend", "fake"),
            output_volume=float(out.get("volume", 1.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise back to the config.json `voice` section shape."""
        return {
            "enabled": self.enabled,
            "asr": {
                "enabled": self.asr_enabled,
                "provider": self.asr_provider,
                "command": list(self.asr_command),
                "timeout_seconds": self.asr_timeout_seconds,
                "model": self.asr_model,
                "language": self.asr_language,
                "base_url": self.asr_base_url,
                "api_key_env": self.asr_api_key_env,
            },
            "tts": {
                "enabled": self.tts_enabled,
                "provider": self.tts_provider,
                "base_url": self.tts_base_url,
                "voice": self.tts_voice,
                "timeout_seconds": self.tts_timeout_seconds,
                "auto_start": self.tts_auto_start,
                "model": self.tts_model,
                "api_key_env": self.tts_api_key_env,
                "response_format": self.tts_response_format,
                "command": list(self.tts_command),
                "cuda_bin": self.tts_cuda_bin,
                "voice_spk": self.tts_voice_spk,
                "voice_rvq": self.tts_voice_rvq,
                "voice_ref_text": self.tts_voice_ref_text,
                "seed": self.tts_seed,
            },
            "input": {
                "enabled": self.input_enabled,
                "backend": self.input_backend,
                "sample_rate": self.input_sample_rate,
                "channels": self.input_channels,
                "max_duration_seconds": self.input_max_duration_seconds,
                "device": self.input_device,
            },
            "output": {
                "enabled": self.output_enabled,
                "backend": self.output_backend,
                "volume": self.output_volume,
            },
        }
