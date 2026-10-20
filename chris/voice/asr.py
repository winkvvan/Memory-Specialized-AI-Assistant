"""ASR adapters (Phase 9.3).

The project depends only on the abstract ASRProvider. A local ASR subsystem can
be invoked as an external process through a parameter array (never a shell
string); the project never imports or copies it.
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from chris.voice.errors import VoiceError, VoiceErrorCode


class ASRProvider(ABC):
    name = "abstract"

    @abstractmethod
    def transcribe(self, audio_path: str, language: str = "Chinese", timeout: float = 120.0) -> str:
        """Transcribe audio to text. Raises VoiceError on failure."""

    def is_available(self) -> bool:
        return True


class FakeASRProvider(ASRProvider):
    """Deterministic fake for tests / offline development."""

    name = "fake"

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["你好，这是语音识别测试。"]
        self._index = 0
        self.transcribed_paths: list[str] = []

    def transcribe(self, audio_path: str, language: str = "Chinese", timeout: float = 120.0) -> str:
        self.transcribed_paths.append(audio_path)
        text = self._responses[self._index % len(self._responses)]
        self._index += 1
        return text


class ApiASRProvider(ASRProvider):
    """Generic OpenAI-compatible speech-to-text provider.

    The endpoint is ``<base_url>/audio/transcriptions``. ``base_url`` may also
    be the full endpoint. API keys are read from an environment variable so
    they are never stored in config.json.
    """

    name = "apiAsr"

    def __init__(
        self,
        base_url: str,
        model: str = "whisper-1",
        language: str = "zh",
        api_key: str | None = None,
        api_key_env: str = "CHRIS_API_KEY",
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.language = language
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.timeout = timeout

    def _endpoint(self) -> str:
        if self.base_url.endswith("/audio/transcriptions"):
            return self.base_url
        return f"{self.base_url}/audio/transcriptions"

    def _key(self) -> str:
        return self.api_key or os.environ.get(self.api_key_env) or os.environ.get("CHRIS_API_KEY") or ""

    def is_available(self) -> bool:
        return bool(self.base_url and self._key())

    def transcribe(self, audio_path: str, language: str = "", timeout: float = 120.0) -> str:
        key = self._key()
        if not key:
            raise VoiceError(
                VoiceErrorCode.ASR_NOT_AVAILABLE,
                f"ASR API key not configured (set {self.api_key_env})",
            )
        try:
            audio = Path(audio_path).read_bytes()
        except OSError as exc:
            raise VoiceError(
                VoiceErrorCode.ASR_INVALID_AUDIO,
                "ASR audio file could not be read",
                detail=str(exc),
            ) from exc

        lang = _normalise_language(language or self.language)
        fields = {
            "model": self.model or "whisper-1",
            "response_format": "json",
        }
        if lang:
            fields["language"] = lang
        body, content_type = _multipart(
            fields,
            file_field="file",
            filename=Path(audio_path).name,
            file_bytes=audio,
            content_type=_audio_mime(audio_path),
        )
        request = urllib.request.Request(
            self._endpoint(),
            data=body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": content_type,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise VoiceError(
                VoiceErrorCode.ASR_PROCESS_FAILED,
                f"ASR API error HTTP {exc.code}",
                detail=(exc.read() or b"")[:300].decode("utf-8", "replace"),
            ) from exc
        except urllib.error.URLError as exc:
            raise VoiceError(
                VoiceErrorCode.ASR_NOT_AVAILABLE,
                "ASR API unreachable",
                detail=str(exc.reason) if exc.reason else "",
            ) from exc
        except TimeoutError as exc:
            raise VoiceError(VoiceErrorCode.ASR_TIMEOUT, "ASR API timed out", detail=str(exc)) from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
            text = payload.get("text", "")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as exc:
            raise VoiceError(
                VoiceErrorCode.ASR_PROCESS_FAILED,
                "ASR API returned invalid JSON",
                detail=str(exc),
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise VoiceError(VoiceErrorCode.ASR_INVALID_AUDIO, "ASR API returned no transcript")
        return text.strip()


class ChrisASRProvider(ASRProvider):
    """Invokes a local ASR command via a parameter array.

    The command is fully configurable (``asr.command``); Chris never hardcodes
    the absolute path. Output is read from the sibling ``<audio>.txt`` file that
    transcribe.py writes (falling back to last stdout line).
    """

    name = "chrisAsr"

    def __init__(
        self,
        command: list[str] | None = None,
        model: str = "",
        cwd: str | None = None,
    ) -> None:
        self.command = command or ["python", "transcribe.py"]
        self.model = model
        self.cwd = cwd

    def transcribe(self, audio_path: str, language: str = "Chinese", timeout: float = 120.0) -> str:
        base_out = _strip_suffix(audio_path)
        args = list(self.command) + [audio_path, "-l", language, "-y", "-q"]
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                cwd=self.cwd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise VoiceError(VoiceErrorCode.ASR_TIMEOUT, "ASR timed out", detail=str(exc)) from exc
        except OSError as exc:
            raise VoiceError(VoiceErrorCode.ASR_NOT_AVAILABLE, "ASR command failed to launch", detail=str(exc)) from exc

        if completed.returncode != 0:
            raise VoiceError(
                VoiceErrorCode.ASR_PROCESS_FAILED,
                f"ASR exited with {completed.returncode}",
                detail=(completed.stderr or completed.stdout or "")[-500:],
            )

        text = _read_sibling(base_out + ".txt")
        if text:
            return text.strip()
        # Fallback: last non-empty stdout line (console transcript)
        lines = [ln for ln in (completed.stdout or "").splitlines() if ln.strip()]
        if lines:
            return lines[-1].strip()
        raise VoiceError(VoiceErrorCode.ASR_INVALID_AUDIO, "ASR produced no transcript")


def _strip_suffix(path: str) -> str:
    p = Path(path)
    return str(p.with_suffix(""))


def _read_sibling(txt_path: str) -> str:
    p = Path(txt_path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _normalise_language(language: str) -> str:
    aliases = {
        "chinese": "zh",
        "english": "en",
        "japanese": "ja",
        "korean": "ko",
        "spanish": "es",
        "french": "fr",
        "german": "de",
    }
    return aliases.get(language.strip().casefold(), language.strip())


def _audio_mime(path: str) -> str:
    suffix = Path(path).suffix.casefold()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }.get(suffix, "application/octet-stream")


def _multipart(
    fields: dict[str, str],
    *,
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    boundary = f"----memory-assistant-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    safe_name = filename.replace('"', "_")
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
