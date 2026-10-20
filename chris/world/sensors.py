"""World sensors / adapters.

A sensor only provides FACTS (observations with confidence). It never decides
the user's mental state - that is done by World Awareness (inference).
Future adapters (camera / microphone / phone / network / ...) implement the
same WorldSensor interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class WorldSensor(ABC):
    """Unified sensor interface. poll() returns an observation dict."""

    @abstractmethod
    def poll(self) -> dict[str, Any]:
        """Return one observation: facts + confidence, no interpretation."""


class MockWorldSensor(WorldSensor):
    """Deterministic fake sensor for tests and simulation."""

    name = "mock"

    def __init__(self, observation: dict[str, Any] | None = None) -> None:
        self._observation = observation or {
            "presence": "PRESENT",
            "activity": "CODING",
            "attention": "FOCUSED",
            "idle_seconds": 0,
            "foreground_app": "code.exe",
            "window_title": "app.py - Visual Studio Code",
            "active": True,
            "confidence": 0.9,
        }

    def set_observation(self, observation: dict[str, Any]) -> None:
        self._observation.update(observation)

    def poll(self) -> dict[str, Any]:
        return dict(self._observation)


class WindowsComputerAdapter(WorldSensor):
    """Reads real foreground window / title / process / idle time via Win32 API.

    Provides only facts. On any failure it degrades gracefully to an inactive
    observation instead of raising.
    """

    name = "windows"

    def poll(self) -> dict[str, Any]:
        try:
            return self._poll_impl()
        except Exception:  # noqa: BLE001
            return {
                "active": False,
                "foreground_app": "",
                "window_title": "",
                "idle_seconds": 0.0,
                "confidence": 0.0,
            }

    def _poll_impl(self) -> dict[str, Any]:
        import ctypes

        user32 = ctypes.windll.user32
        idle = self._get_idle_seconds()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"active": True, "foreground_app": "", "window_title": "", "idle_seconds": idle, "confidence": 0.6}
        title = self._get_window_title(user32, hwnd)
        process = self._get_process_name(hwnd)
        return {
            "active": True,
            "foreground_app": process,
            "window_title": title,
            "idle_seconds": idle,
            "confidence": 0.85,
        }

    @staticmethod
    def _get_idle_seconds() -> float:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)

    @staticmethod
    def _get_window_title(user32, hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    @staticmethod
    def _get_process_name(hwnd: int) -> str:
        import ctypes

        pid = ctypes.c_uint()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import psutil

            try:
                return psutil.Process(pid.value).name()
            except Exception:  # noqa: BLE001
                return ""
        except ImportError:
            pass
        # fallback without psutil
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ""
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_uint(260)
            ctypes.windll.psapi.GetModuleFileNameExW(handle, 0, buf, size)
            ctypes.windll.kernel32.CloseHandle(handle)
            return buf.value.split("\\")[-1]
        except Exception:  # noqa: BLE001
            return ""


def create_sensor(name: str = "mock") -> WorldSensor:
    if name == "windows":
        return WindowsComputerAdapter()
    if name == "mock":
        return MockWorldSensor()
    raise ValueError(f"Unknown sensor: {name!r}")
