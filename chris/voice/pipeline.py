"""VoicePipeline: the voice interaction state machine (Phase 9.3).

States:
  IDLE -> LISTENING -> TRANSCRIBING -> THINKING -> SPEAKING -> IDLE
Failure paths from any stage return to IDLE; cancellation is always honored.

The pipeline reuses the existing chat/chat_turn entry point so that ASR text
flows through the normal Chris conversation (tools, memory, permission,
confirmation) - it never reimplements dialogue logic.
"""

from __future__ import annotations

import threading

from chris.events import Event, EventBus, EventType
from chris.voice.models import VoiceSession, VoiceStage, VoiceState


class _ChatRunner:
    """Abstraction over the chat entry so the pipeline can be tested with fakes.

    The real implementation routes to launcher.chat.chat_turn / stream_turn.
    """

    def run(self, user_text: str, session: VoiceSession) -> str:
        raise NotImplementedError


class RealChatRunner(_ChatRunner):
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def run(self, user_text: str, session: VoiceSession) -> str:
        from launcher.chat import chat_turn

        reply = chat_turn(self.runtime, user_text)
        return reply or ""


class VoicePipeline:
    def __init__(
        self,
        *,
        audio_input=None,
        asr=None,
        tts=None,
        audio_output=None,
        chat_runner: _ChatRunner | None = None,
        bus: EventBus | None = None,
        temporal=None,
        max_input_seconds: float = 30.0,
        output_dir: str = "",
    ) -> None:
        self.audio_input = audio_input
        self.asr = asr
        self.tts = tts
        self.audio_output = audio_output
        self.chat_runner = chat_runner
        self.bus = bus
        self.temporal = temporal
        self.max_input_seconds = max_input_seconds
        self.output_dir = output_dir
        self._session: VoiceSession | None = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._stop_recording = threading.Event()
        self._state = VoiceState.IDLE

    # -- public ---------------------------------------------------------

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    @property
    def session(self) -> VoiceSession | None:
        with self._lock:
            return self._session

    def run(self, *, audio_path: str | None = None, text: str | None = None) -> VoiceSession:
        """Run one voice session. If text is provided, skips input+ASR.
        Returns the final session. Any failure returns to IDLE."""
        self._ensure_idle()
        session = self._begin()

        def work() -> None:
            nonlocal text
            if text is None:
                self._listening(session, audio_path)
                self._transcribing(session)
                text = session.transcript
            self._thinking(session, text)
            self._speaking(session)

        return self._run_guarded(session, work)

    def speak(self, text: str) -> VoiceSession:
        """Speak text through TTS + playback without re-running chat.

        Used by the GUI to voice Chris's reply after a normal chat turn.
        Unlike ``run`` (where speech is optional), a missing TTS provider is
        a hard error so the UI can tell the user voice is not configured.
        """
        self._ensure_idle()
        session = self._begin()
        session.user_text = text
        session.reply_text = text

        def work() -> None:
            session.stage = VoiceStage.SPEAKING
            if self.tts is None or self.audio_output is None:
                from chris.voice.errors import VoiceError, VoiceErrorCode

                raise VoiceError(VoiceErrorCode.TTS_NOT_AVAILABLE, "TTS not configured")
            self._speaking(session)

        return self._run_guarded(session, work)

    def capture_transcript(self, audio_path: str | None = None) -> VoiceSession:
        """Record (if needed) and transcribe audio, then return the session
        with ``transcript`` set. Does not run chat or TTS - the GUI uses this
        to feed recognised speech into the normal streaming chat turn."""
        self._ensure_idle()
        session = self._begin()

        def work() -> None:
            self._listening(session, audio_path)
            self._transcribing(session)
            session.stage = VoiceStage.TRANSCRIBING

        return self._run_guarded(session, work)

    def cancel(self) -> None:
        self._cancel.set()
        if self.audio_output is not None:
            try:
                self.audio_output.stop()
            except Exception:  # noqa: BLE001
                pass
        session = self._session
        if session is not None and session.stage not in _TERMINAL:
            session.stage = VoiceStage.CANCELLED
            session.error = "cancelled"
            self._emit(EventType.VOICE_SESSION_CANCELLED, session)
            self._record_temporal(session, "CANCELLED")

    def request_stop_recording(self) -> None:
        """Stop the in-progress mic capture and continue to ASR.

        Unlike ``cancel`` (which aborts the whole session), this only ends the
        LISTENING stage early - the captured audio is still transcribed.
        """
        self._stop_recording.set()

    def _ensure_idle(self) -> None:
        if self._state is VoiceState.BUSY:
            from chris.voice.errors import VoiceError, VoiceErrorCode

            raise VoiceError(VoiceErrorCode.VOICE_BUSY, "voice pipeline busy")

    def _run_guarded(self, session: VoiceSession, work) -> VoiceSession:
        """Run one stage sequence with the shared error/cancel envelope."""
        succeeded = False
        try:
            work()
            succeeded = True
        except _Cancellation:
            session.stage = VoiceStage.CANCELLED
            session.error = "cancelled"
            session.error_code = "voice_cancelled"
            self._emit(EventType.VOICE_SESSION_CANCELLED, session)
            self._record_temporal(session, "CANCELLED")
        except Exception as exc:  # noqa: BLE001
            session.stage = _error_stage_for(session.stage)
            session.error = str(exc)
            session.error_code = _code_of(exc)
            self._emit(EventType.VOICE_SESSION_FAILED, session)
            self._record_temporal(session, "FAILED")
        finally:
            self._finish(session)
        if succeeded:
            self._emit(EventType.VOICE_SESSION_COMPLETED, session)
            self._record_temporal(session, "COMPLETED")
        return session

    # -- stages ---------------------------------------------------------

    def _begin(self) -> VoiceSession:
        with self._lock:
            self._state = VoiceState.BUSY
            self._cancel.clear()
            self._stop_recording.clear()
            self._session = VoiceSession(stage=VoiceStage.LISTENING)
        self._emit(EventType.VOICE_SESSION_STARTED, self._session)
        self._record_temporal(self._session, "STARTED")
        return self._session

    def _listening(self, session: VoiceSession, audio_path: str | None) -> VoiceSession:
        session.stage = VoiceStage.LISTENING
        self._emit(EventType.VOICE_LISTENING_STARTED, session)
        if self._cancel.is_set():
            raise _Cancellation()
        if audio_path is None and self.audio_input is not None:
            import os
            import tempfile

            audio_path = os.path.join(
                tempfile.gettempdir(), f"{session.session_id}_in.wav"
            )
            audio_path = self.audio_input.record(
                audio_path,
                max_seconds=self.max_input_seconds,
                cancel=self._cancel,
                stop=self._stop_recording,
            )
        if not audio_path:
            raise _StageError("listening failed")
        session.metadata["audio_path"] = audio_path
        session.stage = VoiceStage.TRANSCRIBING
        return session

    def _transcribing(self, session: VoiceSession) -> VoiceSession:
        session.stage = VoiceStage.TRANSCRIBING
        self._emit(EventType.VOICE_TRANSCRIPTION_STARTED, session)
        if self._cancel.is_set():
            raise _Cancellation()
        if self.asr is None:
            raise _StageError("ASR not configured")
        language = getattr(self.asr, "language", "Chinese")
        audio_path = session.metadata.get("audio_path", "")
        text = self.asr.transcribe(audio_path, language=language)
        session.transcript = text
        self._emit(EventType.VOICE_TRANSCRIPTION_COMPLETED, session)
        session.stage = VoiceStage.THINKING
        return session

    def _thinking(self, session: VoiceSession, user_text: str) -> VoiceSession:
        session.stage = VoiceStage.THINKING
        self._emit(EventType.VOICE_THINKING_STARTED, session)
        if self._cancel.is_set():
            raise _Cancellation()
        if self.chat_runner is None:
            raise _StageError("chat runner not configured")
        reply = self.chat_runner.run(user_text, session)
        session.user_text = user_text
        session.reply_text = reply
        session.stage = VoiceStage.SPEAKING
        return session

    def _speaking(self, session: VoiceSession) -> VoiceSession:
        session.stage = VoiceStage.SPEAKING
        self._emit(EventType.VOICE_SPEAKING_STARTED, session)
        if self._cancel.is_set():
            raise _Cancellation()
        if self.tts is None or self.audio_output is None:
            return session
        import os
        import tempfile

        out_dir = self.output_dir or tempfile.gettempdir()
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{session.session_id}.wav")
        voice = getattr(self.tts, "voice", "")
        self.tts.synthesize(session.reply_text, out_path, voice=voice)
        self.audio_output.play(out_path, cancel=self._cancel)
        session.audio_path = out_path
        self._emit(EventType.VOICE_SPEAKING_COMPLETED, session)
        return session

    def _finish(self, session: VoiceSession) -> None:
        with self._lock:
            self._state = VoiceState.IDLE
            self._session = None

    # -- infra ----------------------------------------------------------

    def _emit(self, event_type: EventType, session: VoiceSession) -> None:
        if self.bus is None:
            return
        payload = session.safe_summary()
        payload["duration"] = 0
        self.bus.publish(Event(event_type, "voice", payload))

    def _record_temporal(self, session: VoiceSession, status: str) -> None:
        if self.temporal is None:
            return
        try:
            self.temporal.record_tool_activity(
                "voice_session", status, duration_seconds=None, call_id=session.session_id
            )
        except Exception:  # noqa: BLE001
            pass


class _StageError(RuntimeError):
    pass


class _Cancellation(RuntimeError):
    pass


def _code_of(exc: Exception) -> str:
    """Extract a stable error_code from a VoiceError (or a generic fallback)."""
    from chris.voice.errors import VoiceError

    if isinstance(exc, VoiceError):
        return exc.code.value
    return "voice_unknown"


_TERMINAL = {
    VoiceStage.IDLE,
    VoiceStage.CANCELLED,
    VoiceStage.LISTENING_ERROR,
    VoiceStage.TRANSCRIPTION_ERROR,
    VoiceStage.CHAT_ERROR,
    VoiceStage.TTS_ERROR,
    VoiceStage.PLAYBACK_ERROR,
}


def _error_stage_for(stage: VoiceStage) -> VoiceStage:
    return {
        VoiceStage.LISTENING: VoiceStage.LISTENING_ERROR,
        VoiceStage.TRANSCRIBING: VoiceStage.TRANSCRIPTION_ERROR,
        VoiceStage.THINKING: VoiceStage.CHAT_ERROR,
        VoiceStage.SPEAKING: VoiceStage.TTS_ERROR,
    }.get(stage, VoiceStage.CHAT_ERROR)
