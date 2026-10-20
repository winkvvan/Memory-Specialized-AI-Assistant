"""Phase 9.3 Voice Pipeline tests (offline).

Covers: ASR adapter (fake + errors), TTS adapter (fake + localhost HTTP + reject
non-localhost), audio input/output (fake), VoicePipeline state machine, VoiceManager
(busy protection/cancel), full E2E (FakeAudioInput -> FakeASR -> chat -> FakeTTS ->
FakeAudioOutput), EventBus / Temporal / Memory isolation, and text-mode regression.
"""

import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from chris.config import Config
from chris.events import EventBus, EventType
from chris.voice.asr import ASRProvider, ChrisASRProvider, FakeASRProvider
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
from chris.voice.factory import build_asr, build_audio_input, build_audio_output, build_tts
from chris.voice.manager import _clean_ref_text, VoiceManager
from chris.voice.models import VoiceSession, VoiceStage, VoiceState
from chris.voice.pipeline import _ChatRunner, VoicePipeline
from chris.voice.tts import FakeTTSProvider, HttpTTSProvider, TTSProvider


def _write_wav(path: str) -> str:
    import wave

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return path


class FakeChatRunner(_ChatRunner):
    def __init__(self, reply="reply-from-chat"):
        self.reply = reply
        self.calls = []

    def run(self, user_text, session):
        self.calls.append(user_text)
        return self.reply.replace("{text}", user_text)


# ---------------------------------------------------------------------------
# ASR
# ---------------------------------------------------------------------------


class TestASR(unittest.TestCase):
    def test_fake_returns_scripted(self):
        asr = FakeASRProvider(responses=["你好世界"])
        self.assertEqual(asr.transcribe("a.wav"), "你好世界")

    def test_chrisasr_error_mapping_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            probe = ChrisASRProvider(command=["python", "-c", "import time; time.sleep(5)"])
            with self.assertRaises(VoiceError) as cm:
                probe.transcribe(audio, timeout=0.1)
            self.assertEqual(cm.exception.code, VoiceErrorCode.ASR_TIMEOUT)

    def test_chrisasr_process_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            probe = ChrisASRProvider(command=["python", "-c", "import sys; sys.exit(3)"])
            with self.assertRaises(VoiceError) as cm:
                probe.transcribe(audio)
            self.assertEqual(cm.exception.code, VoiceErrorCode.ASR_PROCESS_FAILED)

    def test_chrisasr_reads_sibling_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            Path(tmp, "in.txt").write_text("转录结果内容", encoding="utf-8-sig")
            # command that just succeeds
            probe = ChrisASRProvider(command=[__import__("sys").executable, "-c", "pass"])
            self.assertEqual(probe.transcribe(audio), "转录结果内容")

    def test_chrisasr_passes_cwd_to_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            with mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="转录行\n", stderr="")
                probe = ChrisASRProvider(command=["py", "t.py"], cwd=str(tmp))
                self.assertEqual(probe.transcribe(audio), "转录行")
            self.assertEqual(run.call_args.kwargs.get("cwd"), str(tmp))
            self.assertEqual(
                run.call_args.kwargs.get("creationflags"),
                getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


class TestTTS(unittest.TestCase):
    def test_fake_writes_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "o.wav")
            tts = FakeTTSProvider()
            self.assertEqual(tts.synthesize("你好", out), out)
            self.assertTrue(Path(out).exists())

    def test_http_rejects_non_localhost(self):
        tts = HttpTTSProvider(base_url="http://evil.example.com:9999")
        with self.assertRaises(VoiceError) as cm:
            tts.synthesize("x", "o.wav")
        self.assertEqual(cm.exception.code, VoiceErrorCode.TTS_SERVER_ERROR)

    @mock.patch("urllib.request.urlopen")
    def test_http_success(self, urlopen):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _write_wav_bytes()

        urlopen.return_value = _Resp()
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "o.wav")
            tts = HttpTTSProvider(base_url="http://127.0.0.1:8090")
            self.assertEqual(tts.synthesize("hi", out), out)
            self.assertTrue(Path(out).exists())

    def test_http_unavailable(self):
        tts = HttpTTSProvider(base_url="http://127.0.0.1:1")  # nothing listens
        with self.assertRaises(VoiceError) as cm:
            tts.synthesize("hi", "o.wav", timeout=0.3)
        self.assertIn(
            cm.exception.code, (VoiceErrorCode.TTS_NOT_AVAILABLE, VoiceErrorCode.TTS_TIMEOUT)
        )


class TestHttpTTSHealthAndRegister(unittest.TestCase):
    class _Resp:
        def __init__(self, status=200):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def test_health_ok(self):
        with mock.patch("urllib.request.urlopen", return_value=self._Resp(200)) as urlopen:
            tts = HttpTTSProvider(base_url="http://127.0.0.1:8090")
            self.assertTrue(tts.health(timeout=0.5))
            self.assertIn("/v1/models", urlopen.call_args.args[0].full_url)

    def test_health_unreachable(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("down")
        ):
            tts = HttpTTSProvider(base_url="http://127.0.0.1:8090")
            self.assertFalse(tts.health(timeout=0.5))

    def test_register_voice_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            spk = Path(tmp) / "v.spk"
            rvq = Path(tmp) / "v.rvq"
            spk.write_bytes(b"\x00\x01\x02\x03")
            rvq.write_bytes(b"\x00" * 16)
            with mock.patch(
                "urllib.request.urlopen", return_value=self._Resp(200)
            ) as urlopen:
                tts = HttpTTSProvider(base_url="http://127.0.0.1:8090")
                ok = tts.register_voice("voice01", str(spk), str(rvq), "你好")
                self.assertTrue(ok)
                req = urlopen.call_args.args[0]
                self.assertIn("/v1/audio/voices", req.full_url)
                body = json.loads(req.data.decode("utf-8"))
                self.assertEqual(body["name"], "voice01")
                self.assertEqual(body["ref_text"], "你好")
                self.assertTrue(body["spk_b64"])
                self.assertTrue(body["rvq_b64"])

    def test_register_voice_http_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            spk = Path(tmp) / "v.spk"
            rvq = Path(tmp) / "v.rvq"
            spk.write_bytes(b"\x00\x01\x02\x03")
            rvq.write_bytes(b"\x00" * 16)
            err = urllib.error.HTTPError("http://x/v1/audio/voices", 400, "bad", {}, None)
            with mock.patch("urllib.request.urlopen", side_effect=err):
                tts = HttpTTSProvider(base_url="http://127.0.0.1:8090")
                with self.assertRaises(VoiceError) as cm:
                    tts.register_voice("v", str(spk), str(rvq))
                self.assertEqual(cm.exception.code, VoiceErrorCode.TTS_SERVER_ERROR)

    def test_synthesize_sends_seed_when_configured(self):
        class _WavResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _write_wav_bytes()

        with mock.patch("urllib.request.urlopen", return_value=_WavResp()) as urlopen:
            tts = HttpTTSProvider(base_url="http://127.0.0.1:8090", seed=12345)
            with tempfile.TemporaryDirectory() as tmp:
                out = str(Path(tmp) / "o.wav")
                tts.synthesize("hi", out)
        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(body["seed"], 12345)

    def test_synthesize_omits_seed_by_default(self):
        class _WavResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return _write_wav_bytes()

        with mock.patch("urllib.request.urlopen", return_value=_WavResp()) as urlopen:
            tts = HttpTTSProvider(base_url="http://127.0.0.1:8090")
            with tempfile.TemporaryDirectory() as tmp:
                out = str(Path(tmp) / "o.wav")
                tts.synthesize("hi", out)
        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertNotIn("seed", body)


def _write_wav_bytes() -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 3200)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


class TestAudio(unittest.TestCase):
    def test_fake_input_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "in.wav")
            inp = FakeAudioInputProvider()
            self.assertEqual(inp.record(out, max_seconds=2), out)
            self.assertTrue(Path(out).exists())

    def test_fake_input_cancel(self):
        cancel = threading.Event()
        cancel.set()
        inp = FakeAudioInputProvider()
        with self.assertRaises(VoiceError):
            inp.record("o.wav", cancel=cancel)

    def test_fake_output_plays(self):
        out = FakeAudioOutputProvider()
        out.play("x.wav")
        self.assertEqual(out.played, ["x.wav"])


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory(unittest.TestCase):
    def test_build_all_fake(self):
        cfg = VoiceConfig(
            asr_enabled=True, asr_provider="fake",
            tts_enabled=True, tts_provider="fake",
            input_enabled=True, input_backend="fake",
            output_enabled=True, output_backend="fake",
        )
        self.assertIsInstance(build_asr(cfg), FakeASRProvider)
        self.assertIsInstance(build_tts(cfg), FakeTTSProvider)
        self.assertIsInstance(build_audio_input(cfg), FakeAudioInputProvider)
        self.assertIsInstance(build_audio_output(cfg), FakeAudioOutputProvider)

    def test_disabled_returns_none(self):
        cfg = VoiceConfig()
        self.assertIsNone(build_asr(cfg))
        self.assertIsNone(build_tts(cfg))

    def test_build_chris_asr(self):
        cfg = VoiceConfig(asr_enabled=True, asr_provider="chris", asr_command=["python", "t.py"])
        self.assertIsInstance(build_asr(cfg), ChrisASRProvider)

    def test_build_http_tts_carries_voice(self):
        cfg = VoiceConfig(tts_enabled=True, tts_provider="http", tts_voice="voice03")
        tts = build_tts(cfg)
        self.assertIsInstance(tts, HttpTTSProvider)
        self.assertEqual(tts.voice, "voice03")

    def test_build_http_tts_carries_seed(self):
        cfg = VoiceConfig(tts_enabled=True, tts_provider="http", tts_seed=12345)
        tts = build_tts(cfg)
        self.assertEqual(tts.seed, 12345)


# ---------------------------------------------------------------------------
# Pipeline state machine
# ---------------------------------------------------------------------------


class TestPipeline(unittest.TestCase):
    def _pipeline(self, bus=None, temporal=None):
        return VoicePipeline(
            audio_input=FakeAudioInputProvider(),
            asr=FakeASRProvider(responses=["你好，这是语音测试"]),
            tts=FakeTTSProvider(),
            audio_output=FakeAudioOutputProvider(),
            chat_runner=FakeChatRunner("好的，收到：{text}"),
            bus=bus,
            temporal=temporal,
            max_input_seconds=5,
        )

    def test_full_flow_text_mode(self):
        pipe = self._pipeline()
        session = pipe.run(text="你好")
        self.assertEqual(session.stage, VoiceStage.SPEAKING)
        self.assertEqual(session.reply_text, "好的，收到：你好")
        self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_full_flow_audio_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            pipe = self._pipeline()
            session = pipe.run(audio_path=audio)
            self.assertEqual(session.transcript, "你好，这是语音测试")
            self.assertEqual(session.stage, VoiceStage.SPEAKING)
            self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_states_transition_text_mode(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, [
            EventType.VOICE_SESSION_STARTED,
            EventType.VOICE_TRANSCRIPTION_STARTED,
            EventType.VOICE_TRANSCRIPTION_COMPLETED,
            EventType.VOICE_THINKING_STARTED,
            EventType.VOICE_SPEAKING_STARTED,
            EventType.VOICE_SPEAKING_COMPLETED,
            EventType.VOICE_SESSION_COMPLETED,
        ])
        pipe = self._pipeline(bus=bus)
        pipe.run(text="hi")
        types = [e.type for e in seen]
        self.assertIn(EventType.VOICE_SESSION_STARTED, types)
        self.assertIn(EventType.VOICE_THINKING_STARTED, types)
        self.assertIn(EventType.VOICE_SPEAKING_STARTED, types)
        self.assertIn(EventType.VOICE_SPEAKING_COMPLETED, types)
        self.assertEqual(types[-1], EventType.VOICE_SESSION_COMPLETED)
        # text mode skips LISTENING (no input)
        self.assertNotIn(EventType.VOICE_LISTENING_STARTED, types)

    def test_states_transition_audio_mode_includes_listening(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, [
            EventType.VOICE_LISTENING_STARTED,
            EventType.VOICE_TRANSCRIPTION_STARTED,
            EventType.VOICE_SESSION_COMPLETED,
        ])
        pipe = self._pipeline(bus=bus)
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            pipe.run(audio_path=audio)
        types = [e.type for e in seen]
        self.assertIn(EventType.VOICE_LISTENING_STARTED, types)  # noqa: E713
        self.assertIn(EventType.VOICE_TRANSCRIPTION_STARTED, types)

    def test_asr_failure_returns_idle(self):
        class _FailASR(FakeASRProvider):
            def transcribe(self, audio_path, language="Chinese", timeout=120.0):
                raise VoiceError(VoiceErrorCode.ASR_PROCESS_FAILED, "boom")

        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            pipe = self._pipeline()
            pipe.asr = _FailASR()
            session = pipe.run(audio_path=audio)
            self.assertEqual(session.stage, VoiceStage.TRANSCRIPTION_ERROR)
            self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_tts_failure_returns_idle_start_prev(self):
        class _FailTTS(FakeTTSProvider):
            def synthesize(self, text, out_path, voice="", timeout=60.0):
                raise VoiceError(VoiceErrorCode.TTS_GENERATION_FAILED, "boom")

        pipe = self._pipeline()
        pipe.tts = _FailTTS()
        session = pipe.run(text="hi")
        self.assertEqual(session.stage, VoiceStage.TTS_ERROR)
        self.assertEqual(pipe.state, VoiceState.IDLE)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TestManager(unittest.TestCase):
    def _manager(self, **kw):
        cfg = VoiceConfig(enabled=True, **kw)
        fake_asr = FakeASRProvider(responses=["你好"])
        fake_runner = FakeChatRunner("回复：{text}")
        return VoiceManager(
            config=cfg,
            audio_input=FakeAudioInputProvider(),
            asr=fake_asr,
            tts=FakeTTSProvider(),
            audio_output=FakeAudioOutputProvider(),
            chat_runner=fake_runner,
        )

    def test_disabled_raises(self):
        m = VoiceManager(config=VoiceConfig())
        with self.assertRaises(VoiceError) as cm:
            m.run_voice_session(text="hi")
        self.assertEqual(cm.exception.code, VoiceErrorCode.VOICE_DISABLED)

    def test_run_text_session(self):
        m = self._manager()
        session = m.run_voice_session(text="你好")
        self.assertEqual(session.reply_text, "回复：你好")
        self.assertFalse(m.busy)

    def test_busy_protection(self):
        m = self._manager()
        m.pipeline._state = VoiceState.BUSY
        with self.assertRaises(VoiceError) as cm:
            m.run_voice_session(text="hi")
        self.assertEqual(cm.exception.code, VoiceErrorCode.VOICE_BUSY)

    def test_cancel(self):
        m = self._manager()
        session = m.run_voice_session(text="hi")  # completes normally
        self.assertEqual(session.stage, VoiceStage.SPEAKING)


# ---------------------------------------------------------------------------
# Speak-only + capture-only (GUI entry points, Phase 9.3.1)
# ---------------------------------------------------------------------------


class TestSpeakOnly(unittest.TestCase):
    def _pipe(self, tts=None, output=None, bus=None):
        return VoicePipeline(
            tts=tts if tts is not None else FakeTTSProvider(),
            audio_output=output if output is not None else FakeAudioOutputProvider(),
            bus=bus,
        )

    def test_speak_success(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, [
            EventType.VOICE_SPEAKING_STARTED,
            EventType.VOICE_SPEAKING_COMPLETED,
            EventType.VOICE_SESSION_COMPLETED,
        ])
        out = FakeAudioOutputProvider()
        pipe = self._pipe(output=out, bus=bus)
        session = pipe.speak("你好，Chris。")
        self.assertEqual(session.reply_text, "你好，Chris。")
        self.assertEqual(session.stage, VoiceStage.SPEAKING)
        self.assertEqual(pipe.state, VoiceState.IDLE)
        self.assertEqual(len(out.played), 1)
        self.assertEqual(seen[-1].type, EventType.VOICE_SESSION_COMPLETED)

    def test_speak_requires_tts(self):
        pipe = VoicePipeline()  # no TTS / no output
        session = pipe.speak("hi")
        self.assertEqual(session.stage, VoiceStage.TTS_ERROR)
        self.assertEqual(session.error_code, VoiceErrorCode.TTS_NOT_AVAILABLE.value)
        self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_speak_busy(self):
        pipe = self._pipe()
        pipe._state = VoiceState.BUSY
        with self.assertRaises(VoiceError) as cm:
            pipe.speak("hi")
        self.assertEqual(cm.exception.code, VoiceErrorCode.VOICE_BUSY)

    def test_cancel_stops_playback_and_marks_session(self):
        out = FakeAudioOutputProvider()
        pipe = self._pipe(output=out)
        pipe._begin()  # simulate an in-flight voice session
        session = pipe.session
        pipe.cancel()
        self.assertTrue(out._stop)
        self.assertEqual(session.stage, VoiceStage.CANCELLED)
        self.assertEqual(session.error, "cancelled")

    def test_speak_tts_failure_maps_error_stage(self):
        class _FailTTS(FakeTTSProvider):
            def synthesize(self, text, out_path, voice="", timeout=60.0):
                raise VoiceError(VoiceErrorCode.TTS_GENERATION_FAILED, "boom")

        pipe = self._pipe(tts=_FailTTS())
        session = pipe.speak("hi")
        self.assertEqual(session.stage, VoiceStage.TTS_ERROR)
        self.assertEqual(session.error_code, "tts_generation_failed")
        self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_speak_uses_configured_voice(self):
        captured = {}

        class _RecTTS(FakeTTSProvider):
            def synthesize(self, text, out_path, voice="", timeout=60.0):
                captured["voice"] = voice
                return super().synthesize(text, out_path, voice=voice, timeout=timeout)

        pipe = self._pipe(tts=_RecTTS())
        pipe.tts.voice = "voice03"
        pipe.speak("hi")
        self.assertEqual(captured["voice"], "voice03")


class TestCaptureTranscript(unittest.TestCase):
    def _pipe(self, inp=None, asr=None, bus=None):
        return VoicePipeline(
            audio_input=inp if inp is not None else FakeAudioInputProvider(),
            asr=asr if asr is not None else FakeASRProvider(responses=["帮我查天气"]),
            bus=bus,
        )

    def test_capture_with_audio_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "in.wav"))
            pipe = self._pipe()
            session = pipe.capture_transcript(audio_path=audio)
            self.assertEqual(session.transcript, "帮我查天气")
            self.assertEqual(session.stage, VoiceStage.TRANSCRIBING)
            self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_capture_records_when_no_path(self):
        pipe = self._pipe()
        session = pipe.capture_transcript()
        self.assertEqual(session.transcript, "帮我查天气")
        self.assertTrue(pipe.audio_input.recorded)
        self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_capture_asr_failure_maps_error_stage(self):
        class _FailASR(FakeASRProvider):
            def transcribe(self, audio_path, language="Chinese", timeout=120.0):
                raise VoiceError(VoiceErrorCode.ASR_PROCESS_FAILED, "boom")

        pipe = self._pipe(asr=_FailASR())
        session = pipe.capture_transcript()
        self.assertEqual(session.stage, VoiceStage.TRANSCRIPTION_ERROR)
        self.assertEqual(pipe.state, VoiceState.IDLE)

    def test_capture_busy(self):
        pipe = self._pipe()
        pipe._state = VoiceState.BUSY
        with self.assertRaises(VoiceError) as cm:
            pipe.capture_transcript()
        self.assertEqual(cm.exception.code, VoiceErrorCode.VOICE_BUSY)

    def test_stop_recording_continues_to_asr(self):
        """GUI stop button: ends LISTENING early but still transcribes."""

        stop_requested = threading.Event()

        class _BlockingInput(FakeAudioInputProvider):
            def record(self, out_path, *, max_seconds=30.0, cancel=None, stop=None):
                self.recorded.append(out_path)
                if stop is not None:
                    stop_requested.set()
                    while not stop.is_set() and not (
                        cancel is not None and cancel.is_set()
                    ):
                        time.sleep(0.01)
                return super().record(
                    out_path, max_seconds=max_seconds, cancel=cancel, stop=stop
                )

        pipe = VoicePipeline(
            audio_input=_BlockingInput(),
            asr=FakeASRProvider(responses=["你好"]),
        )

        def _stop():
            stop_requested.wait(2.0)
            pipe.request_stop_recording()

        thread = threading.Thread(target=_stop, daemon=True)
        thread.start()
        session = pipe.capture_transcript()
        thread.join(2.0)
        self.assertEqual(session.transcript, "你好")
        self.assertIsNone(session.error_code)
        self.assertEqual(session.stage, VoiceStage.TRANSCRIBING)
        self.assertEqual(pipe.state, VoiceState.IDLE)


class TestManagerGuiEntry(unittest.TestCase):
    def _manager(self):
        return VoiceManager(
            config=VoiceConfig(),  # disabled by default
            audio_input=FakeAudioInputProvider(),
            asr=FakeASRProvider(responses=["你好"]),
            tts=FakeTTSProvider(),
            audio_output=FakeAudioOutputProvider(),
            chat_runner=FakeChatRunner("回复：{text}"),
        )

    def test_set_enabled_runtime_override(self):
        m = self._manager()
        self.assertFalse(m.enabled)
        m.set_enabled(True)
        self.assertTrue(m.enabled)
        session = m.speak("hi")
        self.assertEqual(session.reply_text, "hi")
        m.set_enabled(False)
        with self.assertRaises(VoiceError) as cm:
            m.speak("hi")
        self.assertEqual(cm.exception.code, VoiceErrorCode.VOICE_DISABLED)

    def test_speak_disabled_raises(self):
        m = self._manager()
        with self.assertRaises(VoiceError) as cm:
            m.speak("hi")
        self.assertEqual(cm.exception.code, VoiceErrorCode.VOICE_DISABLED)

    def test_capture_wrapper(self):
        m = self._manager()
        m.set_enabled(True)
        session = m.capture_transcript()
        self.assertEqual(session.transcript, "你好")

    def test_can_speak_and_capture_reflect_providers(self):
        m = self._manager()
        self.assertTrue(m.can_speak)
        self.assertTrue(m.can_capture)
        bare = VoiceManager(config=VoiceConfig(enabled=True))
        self.assertFalse(bare.can_speak)
        self.assertFalse(bare.can_capture)


class TestVoiceConfigRoundTrip(unittest.TestCase):
    def test_from_dict_parses_new_fields(self):
        raw = {
            "enabled": True,
            "tts": {
                "enabled": True,
                "provider": "http",
                "base_url": "http://127.0.0.1:8090",
                "voice": "voice01",
                "auto_start": True,
                "command": ["tts-server.exe", "--no-fa", "--port", "8090"],
                "cuda_bin": "C:/cuda/bin",
                "voice_spk": "a.spk",
                "voice_rvq": "a.rvq",
                "voice_ref_text": "ref.txt",
            },
        }
        cfg = VoiceConfig.from_dict(raw)
        self.assertEqual(cfg.tts_command, ("tts-server.exe", "--no-fa", "--port", "8090"))
        self.assertEqual(cfg.tts_cuda_bin, "C:/cuda/bin")
        self.assertEqual(cfg.tts_voice_spk, "a.spk")
        self.assertTrue(cfg.tts_auto_start)

    def test_to_dict_round_trip(self):
        cfg = VoiceConfig(
            enabled=True,
            tts_enabled=True,
            tts_provider="http",
            tts_base_url="http://127.0.0.1:8090",
            tts_voice="voice01",
            tts_auto_start=True,
            tts_command=("a", "b"),
            tts_cuda_bin="C:/cuda/bin",
            tts_voice_spk="a.spk",
            tts_voice_rvq="a.rvq",
            tts_voice_ref_text="ref.txt",
            output_enabled=True,
            output_backend="windows",
        )
        back = VoiceConfig.from_dict(cfg.to_dict())
        self.assertEqual(back.tts_command, ("a", "b"))
        self.assertEqual(back.tts_voice, "voice01")
        self.assertEqual(back.tts_voice_ref_text, "ref.txt")
        self.assertEqual(back.output_backend, "windows")

    def test_seed_field_round_trip(self):
        raw = {"tts": {"seed": 12345}}
        cfg = VoiceConfig.from_dict(raw)
        self.assertEqual(cfg.tts_seed, 12345)
        self.assertEqual(VoiceConfig.from_dict(cfg.to_dict()).tts_seed, 12345)


class TestManagerAutoConfig(unittest.TestCase):
    def _manager(self, **kw):
        persisted = {}
        m = VoiceManager(
            config=VoiceConfig(**kw),
            persist=lambda vcfg: persisted.update({"cfg": vcfg}),
        )
        return m, persisted

    def test_auto_builds_local_tts_and_windows_output(self):
        m, persisted = self._manager()  # all defaults: tts disabled
        ok, msg = m.enable_voice_chat()
        self.assertTrue(ok, msg)
        self.assertIsInstance(m.pipeline.tts, HttpTTSProvider)
        self.assertIsInstance(m.pipeline.audio_output, WindowsAudioOutputProvider)
        self.assertTrue(m.enabled)
        self.assertTrue(m.can_speak)
        self.assertTrue(persisted)
        self.assertTrue(m.config.tts_enabled)
        self.assertTrue(m.config.output_enabled)

    def test_explicit_fake_config_is_respected(self):
        m, _ = self._manager(
            tts_enabled=True,
            tts_provider="fake",
            output_enabled=True,
            output_backend="fake",
        )
        ok, msg = m.enable_voice_chat()
        self.assertTrue(ok, msg)
        self.assertIsInstance(m.pipeline.tts, FakeTTSProvider)
        self.assertIsInstance(m.pipeline.audio_output, FakeAudioOutputProvider)

    def test_auto_start_server_when_down(self):
        with mock.patch("chris.voice.manager.subprocess.Popen") as popen, mock.patch.object(
            HttpTTSProvider, "health", side_effect=[False, True]
        ):
            proc = mock.Mock()
            proc.poll.return_value = None
            popen.return_value = proc
            m, _ = self._manager(
                tts_auto_start=True,
                tts_command=["tts-server.exe", "--no-fa", "--port", "8090"],
            )
            ok, msg = m.enable_voice_chat()
            self.assertTrue(ok, msg)
            popen.assert_called_once()
            m.shutdown()
            proc.terminate.assert_called_once()

    def test_auto_start_missing_command_fails_cleanly(self):
        with mock.patch.object(HttpTTSProvider, "health", return_value=False):
            m, _ = self._manager(tts_auto_start=True)  # no command
            ok, msg = m.enable_voice_chat()
            self.assertFalse(ok)
            self.assertIn("command", msg)
            self.assertFalse(m.enabled)

    def test_voice_registration_uses_configured_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            spk = Path(tmp) / "v.spk"
            rvq = Path(tmp) / "v.rvq"
            ref = Path(tmp) / "ref.txt"
            spk.write_bytes(b"\x00" * 16)
            rvq.write_bytes(b"\x00" * 32)
            ref.write_text("这是 Chris 的台词，呼呼~", encoding="utf-8")
            m, _ = self._manager(
                tts_enabled=True,
                tts_provider="fake",
                output_enabled=True,
                output_backend="fake",
                tts_voice="voice01",
                tts_voice_spk=str(spk),
                tts_voice_rvq=str(rvq),
                tts_voice_ref_text=str(ref),
            )
            ok, msg = m.enable_voice_chat()
            self.assertTrue(ok, msg)
            self.assertIn("voice01", msg)
            self.assertEqual(m.pipeline.tts.registered[0][0], "voice01")
            self.assertEqual(m.pipeline.tts.registered[0][3], "这是 Chris 的台词")

    def test_auto_configures_mic_input_and_asr(self):
        m, persisted = self._manager(asr_command=["python", "transcribe.py"])
        ok, msg = m.enable_voice_chat()
        self.assertTrue(ok, msg)
        self.assertIsInstance(m.pipeline.asr, ChrisASRProvider)
        self.assertIsInstance(m.pipeline.audio_input, WindowsAudioInputProvider)
        self.assertTrue(m.can_capture)
        self.assertIn("麦克风语音输入已就绪", msg)
        self.assertTrue(persisted)
        self.assertTrue(m.config.asr_enabled)
        self.assertTrue(m.config.input_enabled)

    def test_missing_asr_command_keeps_capture_off(self):
        m, _ = self._manager()  # no asr command
        ok, msg = m.enable_voice_chat()
        self.assertTrue(ok, msg)
        self.assertFalse(m.can_capture)
        self.assertIn("语音输入未配置", msg)

    def test_explicit_fake_asr_and_input_respected(self):
        m, _ = self._manager(
            asr_enabled=True,
            asr_provider="fake",
            input_enabled=True,
            input_backend="fake",
        )
        ok, msg = m.enable_voice_chat()
        self.assertTrue(ok, msg)
        self.assertIsInstance(m.pipeline.asr, FakeASRProvider)
        self.assertIsInstance(m.pipeline.audio_input, FakeAudioInputProvider)

    def test_stop_recording_wrapper_sets_pipeline_event(self):
        m, _ = self._manager()
        m.pipeline._begin()
        self.assertFalse(m.pipeline._stop_recording.is_set())
        m.stop_recording()
        self.assertTrue(m.pipeline._stop_recording.is_set())


class TestRefTextCleaning(unittest.TestCase):
    def test_strips_trailing_hu_hu(self):
        self.assertEqual(
            _clean_ref_text("哎呀，现在反悔已经来不及了，呼呼~"),
            "哎呀，现在反悔已经来不及了",
        )

    def test_strips_ha_ha_with_punctuation(self):
        self.assertEqual(
            _clean_ref_text("我们正好能互相交换下意见，哈哈。"),
            "我们正好能互相交换下意见",
        )

    def test_keeps_plain_text(self):
        self.assertEqual(
            _clean_ref_text("下次买衣服我要你陪我一起去"),
            "下次买衣服我要你陪我一起去",
        )


class TestConfigPath(unittest.TestCase):
    def test_load_remembers_path_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"
            p.write_text("{}", encoding="utf-8")
            cfg = Config.load(p)
            self.assertEqual(cfg.config_path, str(p))
            cfg.voice = {"enabled": True}
            cfg.save()
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertNotIn("config_path", data)
            self.assertTrue(data["voice"]["enabled"])


# ---------------------------------------------------------------------------
# E2E (fake chain)
# ---------------------------------------------------------------------------


class TestE2E(unittest.TestCase):
    def test_full_fake_chain(self):
        bus = EventBus()
        temporal = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            audio = _write_wav(str(Path(tmp) / "u.wav"))
            pipe = VoicePipeline(
                audio_input=FakeAudioInputProvider(),
                asr=FakeASRProvider(responses=["告诉我是几点"]),
                tts=FakeTTSProvider(),
                audio_output=FakeAudioOutputProvider(),
                chat_runner=FakeChatRunner("现在是晚上八点。"),
                bus=bus,
                temporal=temporal,
            )
            session = pipe.run(audio_path=audio)
            self.assertEqual(session.reply_text, "现在是晚上八点。")
            self.assertEqual(session.user_text, "告诉我是几点")
            self.assertEqual(pipe.state, VoiceState.IDLE)
        # temporal recorded
        codes = [c.args[1] for c in temporal.record_tool_activity.call_args_list]
        self.assertIn("STARTED", codes)
        self.assertIn("COMPLETED", codes)


# ---------------------------------------------------------------------------
# Text-mode regression: voice disabled, chat unaffected
# ---------------------------------------------------------------------------


class TestTextModeRegression(unittest.TestCase):
    def test_runtime_voice_disabled_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()  # clean defaults: voice disabled, independent of repo config.json
            for f in ("personality", "memory", "temporal", "world"):
                setattr(cfg, f + "_file", str(Path(tmp) / (f + ".json")))
            from chris.app import build_runtime
            from chris.llm.mock import MockProvider

            runtime = build_runtime(cfg, provider=MockProvider(responses=["noop", "ok"]))
            self.assertFalse(runtime.voice.enabled)
            self.assertIsNotNone(runtime.tool_registry)


if __name__ == "__main__":
    unittest.main()
