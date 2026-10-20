"""Minimal Windows system tray via Win32 ctypes (no external deps).

Falls back gracefully: if tray creation fails, the app simply minimizes.
Callbacks are marshalled to the tkinter main thread via the provided `dispatch`.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading

WM_TRAYICON = 0x8000 + 1  # custom
WM_USER = 0x0400
WM_TRAYMSG = WM_USER + 20
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_COMMAND = 0x0111

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
NIF_INFO = 0x10


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeout", wt.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wt.DWORD),
    ]


class TrayIcon:
    """Owns a Shell_NotifyIcon and a hidden-message-window message pump."""

    def __init__(self, title: str, icon, dispatch) -> None:
        self.title = title
        self._icon = icon
        self._dispatch = dispatch  # fn(menu_id) called on the main thread
        self._user32 = ctypes.windll.user32
        self._hwn = None
        self._thread = None
        self._menu_items: list[tuple[int, str]] = []

    def start(self, menu_items: list[tuple[int, str]], tooltip: str) -> bool:
        self._menu_items = menu_items
        try:
            self._create_window()
            self._add(tooltip)
        except Exception:  # noqa: BLE001
            return False
        self._thread = threading.Thread(target=self._pump, daemon=True, name="tray-pump")
        self._thread.start()
        return True

    def stop(self) -> None:
        try:
            nid = self._make_nid()
            self._user32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:  # noqa: BLE001
            pass
        if self._hwn:
            try:
                self._user32.PostMessageW(self._hwn, WM_USER + 1, 0, 0)  # quit pump
            except Exception:  # noqa: BLE001
                pass

    # -- internals ------------------------------------------------------

    def _create_window(self) -> None:
        wc = wt.WNDCLASS()
        wc.lpfnWndProc = ctypes.WINFUNCTYPE(ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)(self._wndproc)
        wc.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "ChrisTrayWindow"
        ctypes.windll.user32.RegisterClassW(ctypes.byref(wc))
        self._hwn = ctypes.windll.user32.CreateWindowExW(
            0, "ChrisTrayWindow", "Chris", 0, 0, 0, 0, 0, None, None, wc.hInstance, None
        )

    def _make_nid(self) -> NOTIFYICONDATA:
        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._hwn
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYMSG
        nid.hIcon = self._icon
        nid.szTip = self.title[:127]
        return nid

    def _add(self, tooltip: str) -> None:
        nid = self._make_nid()
        nid.szTip = tooltip[:127]
        self._user32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAYMSG:
            if lparam == WM_LBUTTONDBLCLK:
                self._dispatch(0)  # 0 = open
            elif lparam == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if msg == WM_USER + 1:
            return 0
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        menu = ctypes.windll.user32.CreatePopupMenu()
        ids = {}
        for mid, label in self._menu_items:
            ctypes.windll.user32.AppendMenuW(menu, 0, mid, label)
            ids[mid] = label
        pos = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pos))
        ctypes.windll.user32.SetForegroundWindow(self._hwn)
        command = ctypes.windll.user32.TrackPopupMenu(
            menu, 0x0100, pos.x, pos.y, 0, self._hwn, None
        )
        ctypes.windll.user32.DestroyMenu(menu)
        if command:
            self._dispatch(command)

    def _pump(self) -> None:
        msg = wt.MSG()
        while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_USER + 1:
                break
            ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
