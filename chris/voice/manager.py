"""VoiceManager: composes VoicePipeline from config and exposes the voice
session entry points to the runtime / GUI. Strictly one active session
(busy protection); cancellation is exposed through a single method.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from chris.events import EventBus
from chris.voice.config import VoiceConfig
from chris.voice.errors import VoiceError, VoiceErrorCode
from chris.voice.factory import build_audio_input, build_audio_output, build_asr, build_tts
from chris.voice.models import VoiceState
from chris.voice.pipeline import VoicePipeline


def _auto_tts_config(cfg: VoiceConfig) -> VoiceConfig:
    """Auto-select the local HTTP TTS while keeping every other setting."""
    return replace(
        cfg,
        tts_enabled=True,
        tts_provider="http",
        tts_base_url=cfg.tts_base_url or "http://127.0.0.1:8090",
    )


_GIGGLE_SUFFIX = re.compile(
    r"[，,、。.！!？?\s]*(哈哈哈|嘿嘿嘿|呼呼|哈哈|嘻嘻|嘿嘿|呵呵|咯咯|咔咔)[，,、。.！!？?~～\s]*$"
)


def _clean_ref_text(text: str) -> str:
    """Strip a trailing laughter/giggle from the ICL reference transcript.

    Some cloned voices carry a character ending like "…，呼呼~" in the reference
    line; the 0.6B model then reproduces that giggle at the start of EVERY
    synthesis. Removing it from the ICL prompt fixes the artifact (verified by
    ASR: output starts with the target text directly).
    """
    return _GIGGLE_SUFFIX.sub("", text).strip()


class VoiceManager:
    def __init__(
        self,
        config: VoiceConfig | None = None,
        *,
        audio_input=None,
        asr=None,
        tts=None,
        audio_output=None,
        chat_runner=None,
        bus: EventBus | None = None,
        temporal=None,
        base_dir: str | None = None,
        persist=None,
    ) -> None:
        self.config = config or VoiceConfig()
        self.base_dir = base_dir
        self._persist = persist
        self._enabled_override: bool | None = None
        self._tts_server: subprocess.Popen | None = None
        self.pipeline = VoicePipeline(
            audio_input=audio_input,
            asr=asr,
            tts=tts,
            audio_output=audio_output,
            chat_runner=chat_runner,
            bus=bus,
            temporal=temporal,
            max_input_seconds=self.config.input_max_duration_seconds,
        )

    @property
    def enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        return self.config.enabled

    def set_enabled(self, enabled: bool) -> None:
        """Runtime override so the GUI toggle works without editing config.json.
        Providers still come from config; this only gates the manager."""
        self._enabled_override = bool(enabled)

    @property
    def can_speak(self) -> bool:
        """True when TTS + playback providers are configured."""
        return self.pipeline.tts is not None and self.pipeline.audio_output is not None

    @property
    def can_capture(self) -> bool:
        """True when mic + ASR providers are configured."""
        return self.pipeline.audio_input is not None and self.pipeline.asr is not None

    @property
    def state(self) -> VoiceState:
        return self.pipeline.state

    @property
    def busy(self) -> bool:
        return self.pipeline.state is VoiceState.BUSY

    def run_voice_session(self, *, audio_path: str | None = None, text: str | None = None):
        if not self.enabled:
            raise VoiceError(VoiceErrorCode.VOICE_DISABLED, "voice is disabled")
        return self.pipeline.run(audio_path=audio_path, text=text)

    def speak(self, text: str):
        """Speak a reply (TTS + playback only). Raises VoiceError when
        disabled, busy, or TTS is not configured."""
        if not self.enabled:
            raise VoiceError(VoiceErrorCode.VOICE_DISABLED, "voice is disabled")
        return self.pipeline.speak(text)

    def capture_transcript(self, *, audio_path: str | None = None):
        """Record + transcribe user speech; returns the VoiceSession with
        ``transcript`` set (no chat, no TTS)."""
        if not self.enabled:
            raise VoiceError(VoiceErrorCode.VOICE_DISABLED, "voice is disabled")
        return self.pipeline.capture_transcript(audio_path=audio_path)

    # -- GUI auto-configuration (Phase 9.3.2) -------------------------

    def enable_voice_chat(self) -> tuple[bool, str]:
        """Turn voice chat on with zero manual config.

        1. Build the TTS provider (local HTTP tts-server) if none is configured.
        2. Build the audio output (winsound) if none is configured.
        3. Auto-start the local tts-server when requested and it is down.
        4. Register the configured cloned voice (spk/rvq/ref_text).
        5. Auto-configure mic input + ASR transcription when a command is set.
        6. Enable the manager and persist the config for the next launch.

        Returns (ok, user-facing message). Never raises.
        """
        try:
            if self.pipeline.tts is None:
                if self.config.tts_enabled:
                    tts_cfg = self.config
                elif self.config.tts_provider not in ("", "fake"):
                    tts_cfg = replace(self.config, tts_enabled=True)
                else:
                    tts_cfg = _auto_tts_config(self.config)
                tts = build_tts(tts_cfg)
                if tts is None:
                    return False, "TTS provider unavailable"
                self.pipeline.tts = tts
            if self.pipeline.audio_output is None:
                backend = self.config.output_backend if self.config.output_enabled else "windows"
                out_cfg = replace(self.config, output_enabled=True, output_backend=backend)
                out = build_audio_output(out_cfg)
                if out is None:
                    return False, "audio output unavailable"
                self.pipeline.audio_output = out

            tts = self.pipeline.tts
            voice_msg = ""
            if self.config.tts_auto_start and not tts.health(timeout=1.0):
                ok, msg = self._start_tts_server()
                if not ok:
                    return False, msg
                voice_msg = msg
            if self.config.tts_voice and self.config.tts_voice_spk and self.config.tts_voice_rvq:
                registered = self._register_voice(tts)
                if not registered:
                    voice_msg = "（音色注册失败，将使用默认音色）"
                elif self.config.tts_voice:
                    voice_msg = f"（音色 {self.config.tts_voice}）"

            input_msg = self._configure_input()
            self._mark_enabled()
            tts_kind = "TTS API" if getattr(tts, "name", "") == "apiTts" else "本地 tts-server"
            tts_part = f"使用{tts_kind}" + (f" {voice_msg}" if voice_msg else "")
            if input_msg:
                return True, f"{tts_part}；{input_msg}"
            return True, tts_part
        except VoiceError as exc:
            return False, exc.message
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def shutdown(self) -> None:
        """Stop the tts-server subprocess owned by this manager (GUI quit)."""
        if self._tts_server is not None and self._tts_server.poll() is None:
            try:
                self._tts_server.terminate()
            except Exception:  # noqa: BLE001
                pass
        self._tts_server = None

    def _start_tts_server(self) -> tuple[bool, str]:
        if not self.config.tts_command:
            return False, "voice.tts.command 未配置，无法自动启动 tts-server"
        cmd = [self._resolve_command_arg(a) for a in self.config.tts_command]
        env = dict(os.environ)
        cuda_bin = self.config.tts_cuda_bin
        if not cuda_bin and os.environ.get("CUDA_PATH"):
            cuda_bin = os.path.join(os.environ["CUDA_PATH"], "bin")
        if cuda_bin and os.path.isdir(cuda_bin):
            env["PATH"] = cuda_bin + os.pathsep + env.get("PATH", "")
        cwd = os.path.dirname(os.path.abspath(cmd[0])) or None
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._tts_server = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=create_no_window,
            )
        except OSError as exc:
            return False, f"无法启动 tts-server：{exc}"

        deadline = time.time() + 30.0
        while time.time() < deadline:
            if self.pipeline.tts.health(timeout=1.0):
                return True, "tts-server 已自动启动"
            if self._tts_server.poll() is not None:
                return False, "tts-server 启动后即退出（检查模型路径/CUDA）"
            time.sleep(0.5)
        return False, "tts-server 启动超时"

    def _register_voice(self, tts) -> bool:
        register = getattr(tts, "register_voice", None)
        if register is None:
            return True
        ref_text = self.config.tts_voice_ref_text
        if ref_text:
            ref_path = self._resolve(ref_text)
            try:
                ref_text = _clean_ref_text(
                    ref_path.read_text(encoding="utf-8-sig")
                )
            except OSError:
                return False
        try:
            return bool(
                register(
                    self.config.tts_voice,
                    str(self._resolve(self.config.tts_voice_spk)),
                    str(self._resolve(self.config.tts_voice_rvq)),
                    ref_text,
                )
            )
        except Exception:  # noqa: BLE001
            return False

    def _configure_input(self) -> str:
        """Auto-configure mic input + ASR when a command is configured.
        Returns a user-facing message about the input status."""
        if self.pipeline.asr is None:
            if self.config.asr_enabled:
                asr_cfg = self.config
            elif self.config.asr_command:
                asr_cfg = replace(self.config, asr_enabled=True, asr_provider="chris")
            elif self.config.asr_provider not in ("", "fake"):
                asr_cfg = replace(self.config, asr_enabled=True)
            else:
                return "语音输入未配置（voice.asr 未启用）"
            asr = build_asr(asr_cfg, cwd=self.base_dir)
            if asr is not None:
                self.pipeline.asr = asr
        if self.pipeline.audio_input is None and self.pipeline.asr is not None:
            if self.config.input_enabled:
                inp_cfg = self.config
            else:
                inp_cfg = replace(self.config, input_enabled=True, input_backend="windows")
            inp = build_audio_input(inp_cfg)
            if inp is not None:
                self.pipeline.audio_input = inp
        if self.pipeline.asr is not None and self.pipeline.audio_input is not None:
            return "麦克风语音输入已就绪"
        return ""

    def _mark_enabled(self) -> None:
        """Enable + persist the auto-configured voice settings."""
        self.config.enabled = True
        if not self.config.tts_enabled:
            self.config.tts_enabled = True
            self.config.tts_provider = self.config.tts_provider or "http"
        if not self.config.output_enabled:
            self.config.output_enabled = True
            self.config.output_backend = "windows"
        if self.pipeline.asr is not None and not self.config.asr_enabled:
            self.config.asr_enabled = True
            self.config.asr_provider = self.config.asr_provider or "chris"
        if self.pipeline.audio_input is not None and not self.config.input_enabled:
            self.config.input_enabled = True
            self.config.input_backend = "windows"
        self.set_enabled(True)
        if self._persist is not None:
            try:
                self._persist(self.config)
            except Exception:  # noqa: BLE001
                pass

    def _resolve(self, path: str):
        p = Path(path)
        if p.is_absolute():
            return p
        return Path(self.base_dir or os.getcwd()) / p

    def _resolve_command_arg(self, arg: str) -> str:
        """Resolve a server command argument against base_dir.

        Flags (``--x``) and plain values (e.g. ``8090``) pass through; only the
        executable and path-looking arguments are made absolute.
        """
        if arg.startswith("-") or os.path.isabs(arg):
            return arg
        p = Path(self.base_dir or os.getcwd()) / arg
        if p.exists() or "/" in arg or "\\" in arg:
            return str(p)
        return arg

    def cancel_voice_session(self) -> None:
        self.pipeline.cancel()

    def stop_recording(self) -> None:
        """End the in-progress mic capture and continue to ASR (GUI stop button)."""
        self.pipeline.request_stop_recording()

    def set_chat_runner(self, runner) -> None:
        """Attach the chat runner (wired after the Runtime exists)."""
        self.pipeline.chat_runner = runner
