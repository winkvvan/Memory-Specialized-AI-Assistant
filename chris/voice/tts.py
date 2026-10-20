"""TTS adapters (Phase 9.3).

The project depends only on the abstract TTSProvider. A local tts-server can be
reached over HTTP at the configured localhost endpoint using the stdlib urllib.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from urllib.parse import urlparse

from chris.voice.errors import VoiceError, VoiceErrorCode

_LOCALHOSTS = ("127.0.0.1", "localhost", "::1")


class TTSProvider(ABC):
    name = "abstract"

    @abstractmethod
    def synthesize(self, text: str, out_path: str, voice: str = "", timeout: float = 60.0) -> str:
        """Synthesize text to audio at out_path; returns out_path. Raises VoiceError."""

    def health(self, timeout: float = 2.0) -> bool:
        """True when the backend is reachable. Optional for fakes."""
        return True

    def register_voice(self, name: str, spk_path: str, rvq_path: str, ref_text: str = "") -> bool:
        """Register a cloned voice by name (server-side). Optional for fakes."""
        return True

    def is_available(self) -> bool:
        return True


class FakeTTSProvider(TTSProvider):
    """Deterministic fake: writes a minimal valid WAV placeholder."""

    name = "fake"

    def __init__(self) -> None:
        self.synthesized: list[str] = []
        self.registered: list[tuple[str, str, str, str]] = []

    def synthesize(self, text: str, out_path: str, voice: str = "", timeout: float = 60.0) -> str:
        _write_silent_wav(out_path, seconds=1.0)
        self.synthesized.append(text)
        return out_path

    def register_voice(self, name: str, spk_path: str, rvq_path: str, ref_text: str = "") -> bool:
        self.registered.append((name, spk_path, rvq_path, ref_text))
        return True


class HttpTTSProvider(TTSProvider):
    """Calls the Phase 9.2 tts-server OpenAI-compatible /v1/audio/speech.

    Only localhost endpoints are allowed; a non-localhost url is treated as a
    config error (never dial arbitrary hosts).
    """

    name = "httpTts"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8090",
        model: str = "",
        voice: str = "",
        seed: int = -1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.seed = seed

    def _validate_localhost(self) -> None:
        host = urlparse(self.base_url).hostname or ""
        if host not in _LOCALHOSTS:
            raise VoiceError(
                VoiceErrorCode.TTS_SERVER_ERROR,
                f"TTS base_url must be localhost, got {host!r}",
            )

    def synthesize(self, text: str, out_path: str, voice: str = "", timeout: float = 60.0) -> str:
        self._validate_localhost()
        body: dict = {"input": text, "voice": voice, "response_format": "wav"}
        if self.seed >= 0:
            body["seed"] = self.seed
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/audio/speech",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            raise VoiceError(
                VoiceErrorCode.TTS_SERVER_ERROR,
                f"TTS server error HTTP {exc.code}",
                detail=(exc.read() or b"")[:300].decode("utf-8", "replace"),
            ) from exc
        except urllib.error.URLError as exc:
            raise VoiceError(
                VoiceErrorCode.TTS_NOT_AVAILABLE,
                "TTS server unreachable",
                detail=str(exc.reason) if exc.reason else "",
            ) from exc
        except TimeoutError as exc:
            raise VoiceError(VoiceErrorCode.TTS_TIMEOUT, "TTS timed out", detail=str(exc)) from exc

        if not data or len(data) < 44:
            raise VoiceError(VoiceErrorCode.TTS_INVALID_AUDIO, "TTS returned no usable audio")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(data)
        return out_path

    def health(self, timeout: float = 2.0) -> bool:
        """GET /v1/models - True when the local tts-server is up."""
        self._validate_localhost()
        req = urllib.request.Request(f"{self.base_url}/v1/models", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 - any failure means not healthy
            return False

    def register_voice(
        self,
        name: str,
        spk_path: str,
        rvq_path: str,
        ref_text: str = "",
        timeout: float = 30.0,
    ) -> bool:
        """Register a cloned voice from pre-extracted .spk/.rvq latents.

        The tts-server keeps a name-keyed registry; a re-register replaces the
        previous entry. ref_text enables ICL clone mode.
        """
        self._validate_localhost()
        import base64

        def _b64(path: str) -> str:
            with open(path, "rb") as fh:
                return base64.b64encode(fh.read()).decode("ascii")

        payload = json.dumps(
            {
                "name": name,
                "spk_b64": _b64(spk_path),
                "rvq_b64": _b64(rvq_path),
                "ref_text": ref_text,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/audio/voices",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except urllib.error.HTTPError as exc:
            raise VoiceError(
                VoiceErrorCode.TTS_SERVER_ERROR,
                f"voice registration failed HTTP {exc.code}",
                detail=(exc.read() or b"")[:300].decode("utf-8", "replace"),
            ) from exc
        except urllib.error.URLError as exc:
            raise VoiceError(
                VoiceErrorCode.TTS_NOT_AVAILABLE,
                "TTS server unreachable",
                detail=str(exc.reason) if exc.reason else "",
            ) from exc


class ApiTTSProvider(TTSProvider):
    """Generic OpenAI-compatible text-to-speech provider.

    The endpoint is ``<base_url>/audio/speech``. Remote endpoints must use
    HTTPS; plain HTTP is accepted only for localhost. API keys are read from an
    environment variable and are never written to config.json.
    """

    name = "apiTts"

    def __init__(
        self,
        base_url: str,
        model: str = "tts-1",
        voice: str = "alloy",
        response_format: str = "wav",
        api_key: str | None = None,
        api_key_env: str = "CHRIS_API_KEY",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.response_format = response_format
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.timeout = timeout

    def _endpoint(self) -> str:
        if self.base_url.endswith("/audio/speech"):
            return self.base_url
        return f"{self.base_url}/audio/speech"

    def _root(self) -> str:
        suffix = "/audio/speech"
        if self.base_url.endswith(suffix):
            return self.base_url[: -len(suffix)]
        return self.base_url

    def _key(self) -> str:
        return self.api_key or os.environ.get(self.api_key_env) or os.environ.get("CHRIS_API_KEY") or ""

    def _validate_endpoint(self) -> None:
        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""
        if parsed.scheme == "https" or (parsed.scheme == "http" and host in _LOCALHOSTS):
            return
        raise VoiceError(
            VoiceErrorCode.TTS_SERVER_ERROR,
            "TTS API base_url must use HTTPS (HTTP is allowed only for localhost)",
        )

    def is_available(self) -> bool:
        return bool(self.base_url and self._key())

    def synthesize(self, text: str, out_path: str, voice: str = "", timeout: float = 60.0) -> str:
        self._validate_endpoint()
        key = self._key()
        if not key:
            raise VoiceError(
                VoiceErrorCode.TTS_NOT_AVAILABLE,
                f"TTS API key not configured (set {self.api_key_env})",
            )
        body: dict[str, str] = {
            "model": self.model or "tts-1",
            "input": text,
            "voice": voice or self.voice or "alloy",
            "response_format": self.response_format or "wav",
        }
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(),
            data=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            raise VoiceError(
                VoiceErrorCode.TTS_SERVER_ERROR,
                f"TTS API error HTTP {exc.code}",
                detail=(exc.read() or b"")[:300].decode("utf-8", "replace"),
            ) from exc
        except urllib.error.URLError as exc:
            raise VoiceError(
                VoiceErrorCode.TTS_NOT_AVAILABLE,
                "TTS API unreachable",
                detail=str(exc.reason) if exc.reason else "",
            ) from exc
        except TimeoutError as exc:
            raise VoiceError(VoiceErrorCode.TTS_TIMEOUT, "TTS API timed out", detail=str(exc)) from exc

        if not data:
            raise VoiceError(VoiceErrorCode.TTS_INVALID_AUDIO, "TTS API returned no audio")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with open(out_path, "wb") as fh:
            fh.write(data)
        return out_path

    def health(self, timeout: float = 2.0) -> bool:
        self._validate_endpoint()
        key = self._key()
        if not key:
            return False
        req = urllib.request.Request(
            f"{self._root()}/models",
            headers={"Authorization": f"Bearer {key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001 - any failure means not healthy
            return False

    def register_voice(self, name: str, spk_path: str, rvq_path: str, ref_text: str = "") -> bool:
        return False


def _write_silent_wav(path: str, seconds: float = 1.0, rate: int = 24000) -> None:
    import struct
    import wave

    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    n = int(rate * seconds)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)
