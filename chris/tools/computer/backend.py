"""ComputerBackend: the only place that touches the operating system desktop.

The Tool / Manager layers never call Windows APIs directly. Three backends:
  - ComputerBackend (ABC)
  - FakeComputerBackend (deterministic, for tests and offline development)
  - WindowsComputerBackend (pure ctypes Win32, no new dependency - follows the
    same approach as chris/world/sensors.WindowsComputerAdapter).

No shell / subprocess / arbitrary program launch is used anywhere here.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any


class ComputerBackend(ABC):
    name = "abstract"

    @abstractmethod
    def move_mouse(self, x: int, y: int) -> dict[str, Any]: ...

    @abstractmethod
    def click(self, x: int, y: int, button: str = "left") -> dict[str, Any]: ...

    @abstractmethod
    def double_click(self, x: int, y: int, button: str = "left") -> dict[str, Any]: ...

    @abstractmethod
    def scroll(self, x: int, y: int, amount: int) -> dict[str, Any]: ...

    @abstractmethod
    def key_press(self, key: str) -> dict[str, Any]: ...

    @abstractmethod
    def hotkey(self, keys: list[str]) -> dict[str, Any]: ...

    @abstractmethod
    def type_text(self, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def screenshot(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_screen_size(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_active_window(self) -> dict[str, Any]: ...

    @abstractmethod
    def wait(self, seconds: float) -> dict[str, Any]: ...


class FakeComputerBackend(ComputerBackend):
    """Deterministic fake backend for tests - no real desktop interaction."""

    name = "fake"

    def __init__(self, screen: tuple[int, int] = (1920, 1080)) -> None:
        self.screen = screen
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cursor = (0, 0)

    def _record(self, action: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((action, dict(kwargs)))
        return {"ok": True, "action": action}

    def move_mouse(self, x, y):
        self.cursor = (x, y)
        return self._record("move_mouse", x=x, y=y)

    def click(self, x, y, button="left"):
        self.cursor = (x, y)
        return self._record("click", x=x, y=y, button=button)

    def double_click(self, x, y, button="left"):
        self.cursor = (x, y)
        return self._record("double_click", x=x, y=y, button=button)

    def scroll(self, x, y, amount):
        return self._record("scroll", x=x, y=y, amount=amount)

    def key_press(self, key):
        return self._record("key_press", key=key)

    def hotkey(self, keys):
        return self._record("hotkey", keys=list(keys))

    def type_text(self, text):
        return self._record("type_text", text_length=len(text))

    def screenshot(self):
        return self._record("screenshot")

    def get_screen_size(self):
        return {"width": self.screen[0], "height": self.screen[1]}

    def get_active_window(self):
        return {"title": "Fake Window", "process_name": "fake.exe", "bounds": (0, 0, 800, 600)}

    def wait(self, seconds):
        time.sleep(seconds)
        return self._record("wait", seconds=seconds)


class WindowsComputerBackend(ComputerBackend):
    """Real Windows backend using pure ctypes Win32 API.

    No new dependency. Every method degrades to a structured ComputerError on
    failure instead of leaking tracebacks. Screenshot returns raw bytes only
    (acquisition, not understanding - Phase 9 does vision).
    """

    name = "windows"

    def __init__(self) -> None:
        try:
            import ctypes  # noqa: F401

            self._user32 = ctypes.windll.user32
            self._gdi32 = ctypes.windll.gdi32
            self._kernel32 = ctypes.windll.kernel32
            self._available = True
        except Exception:  # noqa: BLE001
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def _require(self) -> None:
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        if not self._available:
            raise ComputerError(
                ComputerErrorCode.BACKEND_UNAVAILABLE,
                "windows backend unavailable on this platform",
            )

    # -- screen / window (read-only) ------------------------------------

    def get_screen_size(self):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            width = self._user32.GetSystemMetrics(0)  # SM_CXSCREEN
            height = self._user32.GetSystemMetrics(1)  # SM_CYSCREEN
            return {"width": width, "height": height}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to get screen size", detail=str(exc)) from exc

    def get_active_window(self):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd:
                return {"title": None, "process_name": None, "bounds": None}
            title = self._window_text(hwnd)
            pid = self._window_pid(hwnd)
            rect = self._window_rect(hwnd)
            return {
                "title": title or None,
                "process_name": self._process_name(pid) if pid else None,
                "bounds": rect,
            }
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to read active window", detail=str(exc)) from exc

    def _window_text(self, hwnd):
        length = self._user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        import ctypes

        buf = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _window_pid(self, hwnd):
        import ctypes

        pid = ctypes.c_uint()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _window_rect(self, hwnd):
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        if self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return [rect.left, rect.top, rect.right, rect.bottom]
        return None

    def _process_name(self, pid):
        try:
            import ctypes

            handle = self._kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_uint(260)
            try:
                psapi = ctypes.windll.psapi
            except Exception:  # noqa: BLE001
                self._kernel32.CloseHandle(handle)
                return None
            psapi.GetModuleFileNameExW(handle, 0, buf, size)
            self._kernel32.CloseHandle(handle)
            return buf.value.split("\\")[-1] or None
        except Exception:  # noqa: BLE001
            return None

    def screenshot(self):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            import ctypes

            width = self._user32.GetSystemMetrics(0)
            height = self._user32.GetSystemMetrics(1)
            hdc = self._user32.GetDC(0)
            mem = self._gdi32.CreateCompatibleDC(hdc)
            bmp = self._gdi32.CreateCompatibleBitmap(hdc, width, height)
            old = self._gdi32.SelectObject(mem, bmp)
            self._gdi32.BitBlt(mem, 0, 0, width, height, hdc, 0, 0, 0x00CC0020)  # SRCCOPY
            self._gdi32.SelectObject(mem, old)
            BITMAPINFOHEADER = ctypes.c_ubyte * 40
            bi = BITMAPINFOHEADER()
            ctypes.memset(bi, 0, 40)
            ctypes.cast(bi, ctypes.POINTER(ctypes.c_int32))[0] = 40
            ctypes.cast(bi, ctypes.POINTER(ctypes.c_int32))[1] = width
            ctypes.cast(bi, ctypes.POINTER(ctypes.c_int32))[2] = -height
            ctypes.cast(bi, ctypes.POINTER(ctypes.c_int16))[6] = 1
            ctypes.cast(bi, ctypes.POINTER(ctypes.c_int16))[7] = 32
            bufsize = width * height * 4
            buffer = ctypes.create_string_buffer(bufsize)
            self._gdi32.GetDIBits(mem, bmp, 0, height, buffer, ctypes.byref(bi), 0)
            self._gdi32.DeleteObject(bmp)
            self._gdi32.DeleteDC(mem)
            self._user32.ReleaseDC(0, hdc)
            return {"width": width, "height": height, "format": "bgra", "bytes": bytes(buffer)}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to capture screenshot", detail=str(exc)) from exc

    # -- input actions --------------------------------------------------

    def move_mouse(self, x, y):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            self._user32.SetCursorPos(x, y)
            return {"x": x, "y": y}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to move mouse", detail=str(exc)) from exc

    def click(self, x, y, button="left"):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            self._user32.SetCursorPos(x, y)
            down, up = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
            self._user32.mouse_event(down, 0, 0, 0, 0)
            self._user32.mouse_event(up, 0, 0, 0, 0)
            return {"x": x, "y": y, "button": button}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to click", detail=str(exc)) from exc

    def double_click(self, x, y, button="left"):
        self.click(x, y, button)
        self.click(x, y, button)
        return {"x": x, "y": y, "button": button}

    def scroll(self, x, y, amount):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            self._user32.SetCursorPos(x, y)
            self._user32.mouse_event(0x0800, 0, 0, int(amount), 0)  # WHEEL
            return {"x": x, "y": y, "amount": amount}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to scroll", detail=str(exc)) from exc

    def key_press(self, key):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            vk = self._key_to_vk(key)
            self._user32.keybd_event(vk, 0, 0, 0)
            self._user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
            return {"key": key}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to press key", detail=str(exc)) from exc

    def hotkey(self, keys):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            vks = [self._key_to_vk(k) for k in keys]
            for vk in vks:
                self._user32.keybd_event(vk, 0, 0, 0)
            for vk in reversed(vks):
                self._user32.keybd_event(vk, 0, 2, 0)
            return {"keys": list(keys)}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to send hotkey", detail=str(exc)) from exc

    def type_text(self, text):
        self._require()
        from chris.tools.computer.errors import ComputerError, ComputerErrorCode

        try:
            import ctypes

            for char in text:
                ctypes.windll.user32.keybd_event(ord(char), 0, 0, 0)
                ctypes.windll.user32.keybd_event(ord(char), 0, 2, 0)
            return {"text_length": len(text)}
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(ComputerErrorCode.BACKEND_ERROR, "failed to type text", detail=str(exc)) from exc

    def wait(self, seconds):
        import time

        time.sleep(seconds)
        return {"seconds": seconds}

    @staticmethod
    def _key_to_vk(key: str) -> int:
        import ctypes

        mapping = {
            "enter": 0x0D, "space": 0x20, "tab": 0x09, "backspace": 0x08,
            "delete": 0x2E, "esc": 0x1B, "escape": 0x1B, "shift": 0x10,
            "ctrl": 0x11, "control": 0x11, "alt": 0x12, "win": 0x5B,
            "super": 0x5B, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
            "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        }
        if key in mapping:
            return mapping[key]
        # single char / digit / letter -> use VkKeyScan or ord
        if len(key) == 1:
            return ord(key.upper())
        # F-keys
        if key.startswith("f") and key[1:].isdigit():
            return 0x70 + int(key[1:]) - 1
        raise ValueError(f"unknown key: {key}")


def create_backend(name: str = "fake", **kwargs: Any) -> ComputerBackend:
    if name in ("auto", "windows"):
        try:
            backend = WindowsComputerBackend()
            if backend.available:
                return backend
        except Exception:  # noqa: BLE001
            pass
        if name == "windows":
            raise ValueError("windows backend unavailable")
    return FakeComputerBackend(**kwargs)
