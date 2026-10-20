"""Windows microphone capture via ctypes WinMM waveIn (pure stdlib).

Records mono 16-bit PCM to a WAV file. Uses a small set of pre-allocated
waveHDR buffers and polls for data with cancellation support - no callbacks,
no new dependency. If no capture device exists, raises a structured VoiceError
instead of crashing.
"""

from __future__ import annotations

import ctypes
import os
import struct
import time
import wave
from ctypes import wintypes

from chris.voice.errors import VoiceError, VoiceErrorCode

_WAVE_FORMAT_PCM = 1
_BUFFER_MS = 200  # per-buffer duration


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", wintypes.LPSTR),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", wintypes.LPVOID),
        ("reserved", wintypes.LPVOID),
    ]


def record_wavein(
    out_path: str,
    *,
    device: int = -1,
    sample_rate: int = 16000,
    channels: int = 1,
    max_seconds: float = 30.0,
    cancel=None,
    stop=None,
) -> str:
    winmm = ctypes.windll.winmm

    def _mk_header_data():
        bytes_per_buffer = sample_rate * channels * 2 * _BUFFER_MS // 1000
        raw = ctypes.create_string_buffer(bytes_per_buffer)
        hdr = WAVEHDR()
        hdr.lpData = ctypes.cast(raw, wintypes.LPSTR)
        hdr.dwBufferLength = bytes_per_buffer
        return raw, hdr

    # open input
    fmt = (ctypes.c_byte * 18)()
    dw = struct.pack("<HHIIHH", _WAVE_FORMAT_PCM, channels, sample_rate,
                     sample_rate * channels * 2, channels * 2, 16)
    ctypes.memmove(fmt, dw, len(dw))

    hWaveIn = wintypes.HANDLE()
    if winmm.waveInOpen(ctypes.byref(hWaveIn), device, fmt, 0, 0, 0) != 0:
        raise VoiceError(VoiceErrorCode.INPUT_UNAVAILABLE, "no capture device available")

    n_buffers = max(2, int(max_seconds * 1000 // _BUFFER_MS) + 1)
    n_buffers = min(n_buffers, 64)
    bufs: list[tuple[bytes, WAVEHDR]] = []
    collected = bytearray()

    try:
        # prepare a moderate number of buffers
        for _ in range(min(n_buffers, 8)):
            raw, hdr = _mk_header_data()
            if winmm.waveInPrepareHeader(hWaveIn, ctypes.byref(hdr), ctypes.sizeof(hdr)) != 0:
                break
            if winmm.waveInAddBuffer(hWaveIn, ctypes.byref(hdr), ctypes.sizeof(hdr)) != 0:
                break
            bufs.append((raw, hdr))
        if not bufs:
            raise VoiceError(VoiceErrorCode.INPUT_UNAVAILABLE, "failed to prepare capture buffers")

        winmm.waveInStart(hWaveIn)

        started = time.monotonic()
        deadline = started + max_seconds
        # read loop: some Windows audio stacks never set WHDR_DONE reliably,
        # so buffer completion is detected through dwBytesRecorded instead
        # (the driver always maintains that field).
        while time.monotonic() < deadline:
            if (cancel is not None and cancel.is_set()) or (
                stop is not None and stop.is_set()
            ):
                break
            for raw, hdr in list(bufs):
                n = hdr.dwBytesRecorded
                if n >= hdr.dwBufferLength:
                    collected += raw.raw[:n]
                    hdr.dwBytesRecorded = 0
                    winmm.waveInAddBuffer(hWaveIn, ctypes.byref(hdr), ctypes.sizeof(hdr))
            time.sleep(0.02)
        # collect the tail of the buffer that was still being filled
        for raw, hdr in bufs:
            n = hdr.dwBytesRecorded
            if 0 < n < hdr.dwBufferLength:
                collected += raw.raw[:n]
    finally:
        try:
            winmm.waveInStop(hWaveIn)
        except Exception:  # noqa: BLE001
            pass
        try:
            winmm.waveInClose(hWaveIn)
        except Exception:  # noqa: BLE001
            pass

    if not collected:
        raise VoiceError(VoiceErrorCode.ASR_INVALID_AUDIO, "no audio captured")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(collected))
    return out_path
