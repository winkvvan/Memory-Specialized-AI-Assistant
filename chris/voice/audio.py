"""Audio input / output providers (Phase 9.3).

Chris depends on the abstract providers only. Windows backends use pure stdlib
(ctypes waveIn for capture, winsound for playback) - no new dependency is
introduced. Playback is non-blocking and stoppable; capture supports max
duration and cancellation.
"""

from __future__ import annotations

import os
import threading
import time
import wave
from abc import ABC, abstractmethod

from chris.voice.errors import VoiceError, VoiceErrorCode


class AudioInputProvider(ABC):
    name = "abstract"

    @abstractmethod
    def record(
        self,
        out_path: str,
        *,
        max_seconds: float = 30.0,
        cancel=None,
        stop=None,
    ) -> str:
        """Record from the active input to a WAV file; returns out_path."""

    def is_available(self) -> bool:
        return True


class AudioOutputProvider(ABC):
    name = "abstract"

    @abstractmethod
    def play(self, audio_path: str, *, cancel) -> None:
        """Play a WAV file locally (non-blocking context is provider concern)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop current playback."""


class FakeAudioInputProvider(AudioInputProvider):
    name = "fake"

    def __init__(self, audio: bytes | None = None) -> None:
        self.recorded: list[str] = []
        self._audio = audio

    def record(
        self,
        out_path: str,
        *,
        max_seconds: float = 30.0,
        cancel=None,
        stop=None,
    ) -> str:
        self.recorded.append(out_path)
        if cancel is not None and cancel.is_set():
            raise VoiceError(VoiceErrorCode.CANCELLED, "recording cancelled")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with wave.open(out_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(self._audio or (b"\x00\x00" * 16000))
        return out_path


class FakeAudioOutputProvider(AudioOutputProvider):
    name = "fake"

    def __init__(self) -> None:
        self.played: list[str] = []
        self._stop = False

    def play(self, audio_path: str, *, cancel=None) -> None:
        self.played.append(audio_path)

    def stop(self) -> None:
        self._stop = True


class WindowsAudioInputProvider(AudioInputProvider):
    """ctypes WinMM waveIn capture to a mono 16-bit WAV file.

    Degrades with a structured error when no capture device is present.
    """

    name = "windows"

    def __init__(self, device: int = -1, sample_rate: int = 16000, channels: int = 1) -> None:
        """device: WinMM waveIn index, or -1 for the system default (WAVE_MAPPER)."""
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels

    def record(
        self,
        out_path: str,
        *,
        max_seconds: float = 30.0,
        cancel=None,
        stop=None,
    ) -> str:
        from chris.audio_backend.windows_input import record_wavein

        return record_wavein(
            out_path,
            device=self.device,
            sample_rate=self.sample_rate,
            channels=self.channels,
            max_seconds=max_seconds,
            cancel=cancel,
            stop=stop,
        )


class WindowsAudioOutputProvider(AudioOutputProvider):
    """winsound playback of a WAV (non-blocking, stoppable)."""

    name = "windows"

    def __init__(self, volume: float = 1.0) -> None:
        self.volume = volume
        self._playing = False

    def play(self, audio_path: str, *, cancel=None) -> None:
        import winsound

        if not os.path.exists(audio_path):
            raise VoiceError(VoiceErrorCode.PLAYBACK_FAILED, "audio file missing")
        try:
            winsound.PlaySound(audio_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            self._playing = True
        except Exception as exc:  # noqa: BLE001
            raise VoiceError(VoiceErrorCode.PLAYBACK_FAILED, f"playback failed: {exc}") from exc

    def stop(self) -> None:
        import winsound

        try:
            winsound.PlaySound(None, 0)
        except Exception:  # noqa: BLE001
            pass
        self._playing = False
