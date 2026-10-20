"""Offline tests for the generic API interfaces (no network)."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from chris.config import LLMConfig
from chris.llm.factory import create_provider
from chris.llm.openai_compat import APIProvider
from chris.voice.asr import ApiASRProvider
from chris.voice.config import VoiceConfig
from chris.voice.errors import VoiceError, VoiceErrorCode
from chris.voice.factory import build_asr, build_tts
from chris.voice.tts import ApiTTSProvider


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self.body


def _wav_bytes() -> bytes:
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 160)
    return buf.getvalue()


class TestBrainAPI(unittest.TestCase):
    def test_api_provider_uses_custom_key_environment(self):
        cfg = LLMConfig(
            provider="api",
            model="test-model",
            options={
                "base_url": "https://llm.example.com/v1",
                "api_key_env": "CUSTOM_LLM_KEY",
            },
        )
        with mock.patch.dict(os.environ, {"CUSTOM_LLM_KEY": "test-key"}):
            provider = create_provider(cfg)
        self.assertIsInstance(provider, APIProvider)
        self.assertEqual(provider.base_url, "https://llm.example.com/v1")
        self.assertEqual(provider.model, "test-model")
        self.assertEqual(provider.api_key, "test-key")

    def test_api_provider_requires_base_url(self):
        with self.assertRaises(ValueError):
            APIProvider()


class TestASRAPI(unittest.TestCase):
    def test_transcribe_posts_multipart_and_reads_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(_wav_bytes())
            response = _Response(json.dumps({"text": "你好，世界"}).encode("utf-8"))
            with mock.patch.dict(os.environ, {"CHRIS_API_KEY": "asr-key"}):
                with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                    asr = ApiASRProvider(
                        base_url="https://asr.example.com/v1",
                        model="whisper-1",
                        language="Chinese",
                    )
                    text = asr.transcribe(str(audio))

        self.assertEqual(text, "你好，世界")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://asr.example.com/v1/audio/transcriptions")
        self.assertEqual(request.headers.get("Authorization"), "Bearer asr-key")
        self.assertIn(b'name="model"', request.data)
        self.assertIn(b"whisper-1", request.data)
        self.assertIn(b'name="language"', request.data)
        self.assertIn(b"zh", request.data)
        self.assertIn(b'filename="sample.wav"', request.data)

    def test_missing_key_is_structured_error(self):
        asr = ApiASRProvider(base_url="https://asr.example.com/v1", api_key_env="MISSING_ASR_KEY")
        with mock.patch.dict(os.environ, {"CHRIS_API_KEY": "", "MISSING_ASR_KEY": ""}):
            with self.assertRaises(VoiceError) as cm:
                asr.transcribe("missing.wav")
        self.assertEqual(cm.exception.code, VoiceErrorCode.ASR_NOT_AVAILABLE)

    def test_http_error_is_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            audio.write_bytes(_wav_bytes())
            error = urllib.error.HTTPError(
                "https://asr.example.com/v1/audio/transcriptions",
                401,
                "unauthorized",
                {},
                io.BytesIO(b"bad key"),
            )
            with mock.patch.dict(os.environ, {"CHRIS_API_KEY": "asr-key"}):
                with mock.patch("urllib.request.urlopen", side_effect=error):
                    asr = ApiASRProvider(base_url="https://asr.example.com/v1")
                    with self.assertRaises(VoiceError) as cm:
                        asr.transcribe(str(audio))
        self.assertEqual(cm.exception.code, VoiceErrorCode.ASR_PROCESS_FAILED)


class TestTTSAPI(unittest.TestCase):
    def test_synthesize_posts_openai_shape(self):
        response = _Response(_wav_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "out.wav")
            with mock.patch.dict(os.environ, {"CHRIS_API_KEY": "tts-key"}):
                with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                    tts = ApiTTSProvider(
                        base_url="https://tts.example.com/v1",
                        model="tts-1",
                        voice="alloy",
                    )
                    result = tts.synthesize("你好", out)
            self.assertEqual(result, out)
            self.assertTrue(Path(out).exists())
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://tts.example.com/v1/audio/speech")
        self.assertEqual(request.headers.get("Authorization"), "Bearer tts-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "tts-1")
        self.assertEqual(body["input"], "你好")
        self.assertEqual(body["voice"], "alloy")
        self.assertEqual(body["response_format"], "wav")

    def test_remote_plain_http_is_rejected(self):
        tts = ApiTTSProvider(base_url="http://tts.example.com/v1")
        with self.assertRaises(VoiceError) as cm:
            tts.synthesize("你好", "out.wav")
        self.assertEqual(cm.exception.code, VoiceErrorCode.TTS_SERVER_ERROR)

    def test_missing_key_is_structured_error(self):
        tts = ApiTTSProvider(base_url="https://tts.example.com/v1", api_key_env="MISSING_TTS_KEY")
        with mock.patch.dict(os.environ, {"CHRIS_API_KEY": "", "MISSING_TTS_KEY": ""}):
            with self.assertRaises(VoiceError) as cm:
                tts.synthesize("你好", "out.wav")
        self.assertEqual(cm.exception.code, VoiceErrorCode.TTS_NOT_AVAILABLE)


class TestVoiceFactories(unittest.TestCase):
    def test_api_providers_are_built_from_config(self):
        cfg = VoiceConfig(
            asr_enabled=True,
            asr_provider="api",
            asr_base_url="https://asr.example.com/v1",
            asr_model="whisper-1",
            tts_enabled=True,
            tts_provider="api",
            tts_base_url="https://tts.example.com/v1",
            tts_model="tts-1",
            tts_voice="alloy",
        )
        self.assertIsInstance(build_asr(cfg), ApiASRProvider)
        self.assertIsInstance(build_tts(cfg), ApiTTSProvider)

    def test_api_settings_round_trip(self):
        cfg = VoiceConfig(
            asr_base_url="https://asr.example.com/v1",
            asr_api_key_env="ASR_KEY",
            tts_base_url="https://tts.example.com/v1",
            tts_api_key_env="TTS_KEY",
            tts_model="tts-1",
            tts_response_format="mp3",
        )
        restored = VoiceConfig.from_dict(cfg.to_dict())
        self.assertEqual(restored.asr_base_url, "https://asr.example.com/v1")
        self.assertEqual(restored.asr_api_key_env, "ASR_KEY")
        self.assertEqual(restored.tts_base_url, "https://tts.example.com/v1")
        self.assertEqual(restored.tts_api_key_env, "TTS_KEY")
        self.assertEqual(restored.tts_model, "tts-1")
        self.assertEqual(restored.tts_response_format, "mp3")


if __name__ == "__main__":
    unittest.main()