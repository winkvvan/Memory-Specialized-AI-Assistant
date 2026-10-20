"""Main application window: header, sidebar navigation, pages, tray, shortcuts.

Light-first shell. The sidebar is part of the light theme (never a dark rail).
Pages are routed from the sidebar; the chat page owns the composer.
"""

from __future__ import annotations

import ctypes
import json
import queue
import threading
import time as _time
from pathlib import Path

import tkinter as tk

from launcher.chat import build_chat_pipeline, stream_turn
from launcher.theme import Theme
from launcher.tray import TrayIcon
from launcher.ui.chat_widget import ChatView, InputBar
from launcher.ui.panels import PANELS
from launcher.ui.widgets import Avatar, PillButton
from launcher.viewmodel import ViewModel

_STATUS_COLORS = {
    "ONLINE": "online", "THINKING": "thinking", "RESPONDING": "thinking",
    "LISTENING": "accent", "SPEAKING": "accent",
    "IDLE": "online", "OFFLINE": "text-muted", "ERROR": "error",
}
_STATUS_TEXT = {
    "ONLINE": "Online", "THINKING": "Thinking", "RESPONDING": "Working",
    "LISTENING": "Listening", "SPEAKING": "Speaking",
    "IDLE": "Online", "OFFLINE": "Offline", "ERROR": "Error",
}

TRAY_OPEN = 0
TRAY_PAUSE = 1
TRAY_DEV = 2
TRAY_QUIT = 3

_THEME_CYCLE = ("light", "dark", "system")
_THEME_LABEL = {"light": "亮色", "dark": "暗色", "system": "跟随系统"}

_NAV = (
    ("chat", "对话"),
    ("memory", "记忆"),
    ("world", "世界"),
    ("projects", "项目"),
    ("tools", "工具"),
)

_SIDEBAR_MIN_WIDTH = 780


class MainWindow:
    def __init__(
        self,
        root: tk.Tk,
        base_dir: Path,
        theme_name: str = "light",
        config=None,
        provider=None,
    ) -> None:
        self.root = root
        self.base_dir = base_dir
        self.theme = Theme(theme_name)
        self._config = config
        self._provider = provider
        self.runtime = None
        self.vm: ViewModel | None = None
        self.busy = False
        self.cancel = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._history: list = []
        self._session_start = None
        self._tray: TrayIcon | None = None
        self._tray_active = False
        self._quitting = False
        self._current_page = "chat"
        self._nav_buttons: dict[str, PillButton] = {}
        self._sidebar_visible = True
        self._collapsed_by_width = False
        self._last_text = ""
        self.voice_mode = False
        self._recording = False
        self._after_ids: list[str] = []

        root.title("Memory-Specialized AI Assistant")
        root.geometry("1080x720")
        root.minsize(860, 580)
        root.configure(bg=self.theme.c("background"))
        root.option_add("*Font", self.theme.font(10))

        self._build_layout()
        self._bind_shortcuts()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Configure>", self._on_window_resize)

        self._schedule(100, self._poll_queue)
        self._schedule(250, self._poll_viewmodel)
        self._schedule(50, self._init_runtime_async)

    def _schedule(self, ms: int, callback) -> None:
        """Track root.after ids so quit can cancel them cleanly."""
        try:
            after_id = self.root.after(ms, callback)
            self._after_ids.append(after_id)
        except tk.TclError:
            pass

    def _cancel_after_callbacks(self) -> None:
        for after_id in self._after_ids:
            try:
                self.root.after_cancel(after_id)
            except Exception:  # noqa: BLE001
                pass
        self._after_ids = []

    # -- build ---------------------------------------------------------

    def _build_layout(self) -> None:
        c = self.theme.c
        self.root.configure(bg=c("background"))

        # --- header ----------------------------------------------------
        self.header = tk.Frame(self.root, bg=c("surface"))
        self.header.pack(side="top", fill="x")
        tk.Frame(self.header, bg=c("border-soft"), height=1).pack(side="bottom", fill="x")
        head_inner = tk.Frame(self.header, bg=c("surface"))
        head_inner.pack(fill="x", padx=16, pady=(10, 10))

        self.collapse_btn = PillButton(head_inner, self.theme, text="☰",
                                       command=self._toggle_sidebar,
                                       fill=c("surface"), fg=c("text-secondary"),
                                       hover_fill=c("surface-hover"),
                                       radius=self.theme.radius("medium"),
                                       padx=10, pady=4, font_size=11)
        self.collapse_btn.pack(side="left", padx=(0, 12))

        logo = Avatar(head_inner, self.theme, "M", size=28, fill=c("avatar-chris"))
        logo.pack(side="left", padx=(0, 10))
        brand = tk.Frame(head_inner, bg=c("surface"))
        brand.pack(side="left")
        tk.Label(brand, text="Memory-Specialized", bg=c("surface"), fg=c("text-primary"),
                 font=self.theme.font(13, bold=True), anchor="w").pack(anchor="w")
        tk.Label(brand, text="AI Assistant", bg=c("surface"),
                 fg=c("text-muted"), font=self.theme.font_role("caption"),
                 anchor="w").pack(anchor="w")

        right = tk.Frame(head_inner, bg=c("surface"))
        right.pack(side="right")
        self.status_dot = tk.Label(right, text="● Online", bg=c("surface"),
                                   fg=c("online"), font=self.theme.font(9))
        self.status_dot.pack(side="left", padx=(0, 12))
        self.theme_btn = PillButton(right, self.theme, text=_THEME_LABEL.get(self.theme.name, "亮色"),
                                    command=self._cycle_theme,
                                    fill=c("surface"), fg=c("text-secondary"),
                                    hover_fill=c("surface-hover"),
                                    radius=self.theme.radius("pill"),
                                    padx=12, pady=4, font_size=9)
        self.theme_btn.pack(side="left")

        # --- sidebar ---------------------------------------------------
        self.sidebar = tk.Frame(self.root, width=208, bg=c("surface"))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        sb = self.sidebar
        tk.Frame(sb, bg=c("border-soft"), width=1).pack(side="right", fill="y")

        self._section_label(sb, "能力")
        for key, label in _NAV:
            btn = PillButton(sb, self.theme, text=label,
                             command=lambda k=key: self._open_page(k),
                             fill=c("surface"), fg=c("text-secondary"),
                             hover_fill=c("surface-hover"),
                             radius=self.theme.radius("medium"),
                             padx=12, pady=6, font_size=10)
            btn.pack(fill="x", padx=10, pady=1)
            self._nav_buttons[key] = btn

        self._section_label(sb, "当前活动")
        self.activity_card = tk.Frame(sb, bg=c("surface-sunken"))
        self.activity_card.pack(fill="x", padx=10, pady=(0, 8))
        self.activity_summary = tk.Label(self.activity_card, text="世界感知等待中",
                                         bg=c("surface-sunken"), fg=c("text-primary"),
                                         font=self.theme.font_role("small"),
                                         anchor="w", justify="left", wraplength=170)
        self.activity_summary.pack(fill="x", padx=12, pady=(8, 2))
        self.activity_session = tk.Label(self.activity_card, text="Session 00:00",
                                         bg=c("surface-sunken"), fg=c("text-muted"),
                                         font=self.theme.font_role("caption"), anchor="w")
        self.activity_session.pack(fill="x", padx=12, pady=(0, 8))

        tk.Frame(sb, bg=c("surface")).pack(fill="both", expand=True)

        footer = tk.Frame(sb, bg=c("surface"))
        footer.pack(fill="x", padx=10, pady=(4, 10))
        self.dev_btn = PillButton(footer, self.theme, text="开发",
                                  command=lambda: self._open_page("developer"),
                                  fill=c("surface"), fg=c("text-muted"),
                                  hover_fill=c("surface-hover"),
                                  radius=self.theme.radius("medium"),
                                  padx=12, pady=5, font_size=9)
        self.dev_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.quit_btn = PillButton(footer, self.theme, text="退出",
                                   command=self._quit,
                                   fill=c("surface"), fg=c("text-muted"),
                                   hover_fill=c("surface-hover"),
                                   radius=self.theme.radius("medium"),
                                   padx=12, pady=5, font_size=9)
        self.quit_btn.pack(side="right", fill="x", expand=True, padx=(4, 0))

        # --- main area -------------------------------------------------
        self.main = tk.Frame(self.root, bg=c("background"))
        self.main.pack(side="left", fill="both", expand=True)
        self.page_host = tk.Frame(self.main, bg=c("background"))
        self.page_host.pack(fill="both", expand=True)
        self._current_page_widget = None
        self._open_page("chat", force=True)
        self._sync_nav()

    def _section_label(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=self.theme.c("surface"),
                 fg=self.theme.c("text-muted"),
                 font=self.theme.font_role("caption", bold=True),
                 anchor="w").pack(fill="x", padx=14, pady=(12, 4))

    # -- pages ---------------------------------------------------------

    def _open_page(self, key: str, force: bool = False) -> None:
        if key == self._current_page and not force and self._current_page_widget is not None:
            return
        if self._current_page_widget is not None:
            self._current_page_widget.destroy()
            self._current_page_widget = None
        if getattr(self, "input", None) is not None:
            self.input.destroy()
            self.input = None
        self.chat = None
        self._current_page = key
        c = self.theme.c
        if key == "chat":
            self.chat = ChatView(self.page_host, self.theme)
            self.chat.pack(fill="both", expand=True)
            self.input = InputBar(
                self.page_host, self._send, self._stop, self.theme,
                on_voice=self._toggle_voice, on_mic=self._toggle_mic,
            )
            self.input.pack(fill="x", side="bottom")
            self._current_page_widget = self.chat
            self.chat_page = True
        else:
            cls = PANELS.get(key)
            if cls:
                self._current_page_widget = cls(self.page_host, self.theme)
                self._current_page_widget.pack(fill="both", expand=True)
                if self.vm:
                    self._current_page_widget.refresh(self.vm.snapshot())
            self.chat_page = False
        self._sync_nav()

    def _sync_nav(self) -> None:
        c = self.theme.c
        for key, btn in self._nav_buttons.items():
            active = key == self._current_page
            btn.set_colors(
                fill=c("accent-soft") if active else c("surface"),
                fg=c("accent") if active else c("text-secondary"),
                hover=c("surface-hover"),
            )

    # -- theme ---------------------------------------------------------

    def _cycle_theme(self) -> None:
        idx = _THEME_CYCLE.index(self.theme.name) if self.theme.name in _THEME_CYCLE else 0
        next_name = _THEME_CYCLE[(idx + 1) % len(_THEME_CYCLE)]
        self._apply_theme(next_name)

    def _apply_theme(self, name: str) -> None:
        self.theme.switch(name)
        if self.vm:
            self.vm.theme = name
        c = self.theme.c
        self.root.configure(bg=c("background"))
        self.header.configure(bg=c("surface"))
        self.sidebar.configure(bg=c("surface"))
        self.main.configure(bg=c("background"))
        self.page_host.configure(bg=c("background"))
        for child in self.header.winfo_children():
            if isinstance(child, (tk.Label, tk.Frame)):
                child.configure(bg=c("surface"))
                for sub in child.winfo_children():
                    if isinstance(sub, tk.Label):
                        sub.configure(bg=c("surface"))
        for child in self.sidebar.winfo_children():
            if isinstance(child, (tk.Label, tk.Frame)):
                child.configure(bg=c("surface"))
        self.activity_card.configure(bg=c("surface-sunken"))
        self.activity_summary.configure(bg=c("surface-sunken"))
        self.activity_session.configure(bg=c("surface-sunken"))
        self.status_dot.configure(bg=c("surface"))
        self.theme_btn.set_text(_THEME_LABEL.get(name, name))
        for btn in list(self._nav_buttons.values()) + [self.dev_btn, self.quit_btn, self.collapse_btn]:
            btn.apply_theme(self.theme)
        self._sync_nav()
        if self.chat_page:
            self.chat.apply_theme(self.theme)
            self.input.apply_theme(self.theme)
        elif self._current_page_widget is not None:
            self._current_page_widget.apply_theme(self.theme)
            if self.vm:
                self._current_page_widget.refresh(self.vm.snapshot())
        self._persist_theme(name)

    def _persist_theme(self, name: str) -> None:
        try:
            path = Path(self.base_dir) / "data" / "ui.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"theme": name}, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # -- sidebar collapse (responsive-ish) ------------------------------

    def _on_window_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        narrow = event.width < _SIDEBAR_MIN_WIDTH
        if narrow and self._sidebar_visible:
            self._collapsed_by_width = True
            self._set_sidebar(False)
        elif not narrow and not self._sidebar_visible and self._collapsed_by_width:
            self._set_sidebar(True)

    def _toggle_sidebar(self) -> None:
        self._collapsed_by_width = False
        self._set_sidebar(not self._sidebar_visible)

    def _set_sidebar(self, visible: bool) -> None:
        self._sidebar_visible = visible
        if visible:
            self.sidebar.pack(side="left", fill="y", before=self.main)
        else:
            self.sidebar.pack_forget()

    # -- runtime init (UI first) ---------------------------------------

    def _init_runtime_async(self) -> None:
        def work() -> None:
            try:
                runtime = build_chat_pipeline(self._config, self._provider)
                self._queue.put(("runtime_ok", runtime))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("runtime_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _on_runtime_ok(self, runtime) -> None:
        self.runtime = runtime
        if getattr(self, "chat", None) is None:
            self._open_page("chat", force=True)
        runtime.temporal.start_session()
        self._session_start = runtime.temporal.now()
        runtime.temporal.start_ticker(interval=1.0)
        self.vm = ViewModel(runtime)
        if self.vm:
            self.vm.set_status("IDLE")
            self.vm.set_provider_status("UNKNOWN")
        self.chat.add_message("chris", "Memory-Specialized AI Assistant 已就绪。开始对话吧。", self._time_str())
        self._update_status()
        self._sync_voice_ui()
        threading.Thread(target=self._health_check, daemon=True).start()
        if self._tray_active is False:
            self._start_tray()

    def _on_runtime_error(self, message: str) -> None:
        try:
            log_path = Path(self.base_dir) / "launcher" / "launcher_error.log"
            log_path.write_text(
                f"{_time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
        self.chat.add_error(
            "Memory-Specialized AI Assistant 暂时无法启动。",
            message,
        )

    def _health_check(self) -> None:
        try:
            ok = self.runtime.provider.health_check()
        except Exception:  # noqa: BLE001
            ok = False
        if self.vm:
            self.vm.set_provider_status("Connected" if ok else "Offline")

    # -- voice chat ----------------------------------------------------

    def _sync_voice_ui(self) -> None:
        voice = getattr(self.runtime, "voice", None) if self.runtime else None
        if voice is None:
            if getattr(self, "input", None) is not None:
                self.input.set_voice_available(False)
            return
        self.input.set_voice_available(True)
        self.input.set_voice_active(self.voice_mode)
        self._sync_mic_state()

    def _sync_mic_state(self) -> None:
        voice = getattr(self.runtime, "voice", None) if self.runtime else None
        enabled = bool(voice) and self.voice_mode and voice.can_capture
        if getattr(self, "input", None) is not None:
            self.input.set_mic_enabled(enabled)

    def _toggle_voice(self) -> None:
        voice = getattr(self.runtime, "voice", None) if self.runtime else None
        if voice is None:
            return
        self.voice_mode = not self.voice_mode
        self.input.set_voice_active(self.voice_mode)
        if not self.voice_mode:
            voice.set_enabled(False)
            if voice.busy:
                voice.cancel_voice_session()
            if self._recording:
                self._set_recording(False)
            self.chat.add_message("chris", "语音回复已关闭。", self._time_str())
            self._sync_mic_state()
            return
        self.chat.add_message("chris", "正在开启语音回复…", self._time_str())
        threading.Thread(target=self._enable_voice_worker, daemon=True).start()

    def _enable_voice_worker(self) -> None:
        voice = self.runtime.voice
        try:
            ok, msg = voice.enable_voice_chat()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, str(exc)
        self._queue.put(("voice_enabled", (ok, msg)))

    def _toggle_mic(self) -> None:
        voice = getattr(self.runtime, "voice", None) if self.runtime else None
        if voice is None or not self.voice_mode:
            return
        if self._recording:
            voice.stop_recording()
            self._set_recording(False)
            return
        if self.busy or voice.busy:
            return
        if not voice.can_capture:
            self.chat.add_message(
                "chris",
                "语音输入未配置：请在 config.json → voice.asr 中选择 api provider，"
                "或填写本地转写命令。",
                self._time_str(),
            )
            return
        self._set_recording(True)
        threading.Thread(target=self._capture_worker, daemon=True).start()

    def _capture_worker(self) -> None:
        voice = self.runtime.voice
        try:
            session = voice.capture_transcript()
            if session.error_code:
                self._queue.put(("voice_error", self._voice_error_text(session)))
                return
            text = (session.transcript or "").strip()
            if text:
                self._queue.put(("voice_transcript", text))
            else:
                self._queue.put(("voice_empty", None))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("voice_error", getattr(exc, "message", None) or str(exc)))

    def _speak_worker(self, reply: str) -> None:
        voice = self.runtime.voice
        try:
            session = voice.speak(reply)
            if session.error_code:
                self._queue.put(("voice_error", self._voice_error_text(session)))
        except Exception as exc:  # noqa: BLE001
            code = getattr(getattr(exc, "code", None), "value", "")
            if code == "voice_pipeline_busy":
                return  # user already started another turn; nothing to do
            self._queue.put(("voice_error", getattr(exc, "message", None) or str(exc)))

    @staticmethod
    def _voice_error_text(session) -> str:
        msg = session.error or session.error_code or "voice_unknown"
        if session.error_code and session.error_code not in ("voice_unknown", ""):
            return f"{session.error_code}: {msg}"
        return msg

    def _set_recording(self, recording: bool) -> None:
        self._recording = recording
        self.input.set_recording(recording)
        self._sync_mic_state()

    # -- turn flow -----------------------------------------------------

    def _send(self, text: str) -> None:
        if self.busy or self.runtime is None or self.vm is None:
            return
        self.chat.add_message("user", text, self._time_str())
        self._history.append({"role": "user", "content": text})
        self._last_text = text
        self.chat.start_streaming(self._time_str())
        self.busy = True
        self.input.set_busy(True)
        self.cancel.clear()
        self.vm.set_status("THINKING")
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

    def _work(self, text: str) -> None:
        try:
            reply = stream_turn(
                self.runtime,
                text,
                self.cancel,
                history=list(self._history[:-1]),
                exclude_since=self._session_start,
                on_chunk=lambda chunk: self._queue.put(("chunk", chunk)),
            )
            if reply is None:
                self._history.pop()
                self._queue.put(("canceled", None))
            else:
                self._history.append({"role": "assistant", "content": reply})
                self._queue.put(("done", reply))
        except Exception as exc:  # noqa: BLE001
            self._history.pop()
            self._queue.put(("error", str(exc)))

    def _stop(self) -> None:
        if self.busy:
            self.cancel.set()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "chunk":
                    self.chat.append_stream_chunk(payload)
                elif kind == "done":
                    self.chat.finish_streaming()
                    self._finish_turn("IDLE")
                    if self.voice_mode and payload:
                        self._queue.put(("speak", payload))
                elif kind == "canceled":
                    self.chat.finish_streaming()
                    self._finish_turn("IDLE")
                elif kind == "error":
                    self.chat.finish_streaming()
                    self.chat.add_error(
                        "无法连接主脑 API。",
                        payload,
                        on_retry=lambda: self._send(self._last_text),
                    )
                    self._finish_turn("ERROR")
                elif kind == "voice_transcript":
                    self._set_recording(False)
                    self._send(payload)
                elif kind == "voice_empty":
                    self._set_recording(False)
                    self.chat.add_message("chris", "没有听清，请再说一次。", self._time_str())
                elif kind == "voice_error":
                    self._set_recording(False)
                    self.chat.add_message("chris", f"语音处理失败：{payload}", self._time_str())
                elif kind == "voice_enabled":
                    ok, msg = payload
                    if ok:
                        self.chat.add_message(
                            "chris", f"语音回复已开启：{msg}", self._time_str()
                        )
                    else:
                        self.voice_mode = False
                        self.input.set_voice_active(False)
                        if self._recording:
                            self._set_recording(False)
                        if self.runtime is not None:
                            self.runtime.voice.set_enabled(False)
                        self.chat.add_message(
                            "chris", f"语音开启失败：{msg}", self._time_str()
                        )
                    self._sync_mic_state()
                elif kind == "speak":
                    self._speak_worker(payload)
                elif kind == "runtime_ok":
                    self._on_runtime_ok(payload)
                elif kind == "runtime_error":
                    self._on_runtime_error(payload)
        except queue.Empty:
            pass
        self._schedule(100, self._poll_queue)

    def _finish_turn(self, status: str) -> None:
        self.busy = False
        self.input.set_busy(False)
        if self.vm:
            self.vm.set_status(status)
        self._update_status()

    def _clear_chat(self) -> None:
        if self.busy:
            self.cancel.set()
        self.chat.clear_messages()
        self._history = []

    # -- viewmodel-driven refresh --------------------------------------

    def _poll_viewmodel(self) -> None:
        if self.vm is not None:
            snapshot = self.vm.snapshot()
            self._update_status(snapshot)
            self._render_activity(snapshot)
            if self._current_page_widget is not None and not self.chat_page:
                self._current_page_widget.refresh(snapshot)
        self._schedule(250, self._poll_viewmodel)

    def _update_status(self, snapshot: dict | None = None) -> None:
        if self.vm is None and snapshot is None:
            return
        snap = snapshot or self.vm.snapshot()
        status = snap.get("chris_status", "OFFLINE")
        color_key = _STATUS_COLORS.get(status, "text-muted")
        text = f"● {_STATUS_TEXT.get(status, status)}"
        if self.status_dot.cget("text") != text or self.status_dot.cget("fg") != self.theme.c(color_key):
            self.status_dot.configure(text=text, fg=self.theme.c(color_key))

    def _render_activity(self, snapshot: dict) -> None:
        summary = snapshot.get("world_summary", "世界感知等待中")
        session = snapshot.get("session_str", "00:00")
        self.activity_summary.configure(text=summary)
        self.activity_session.configure(text=f"Session {session}")

    # -- shortcuts -----------------------------------------------------

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-Shift-M>", lambda _e: (self._open_page("memory"), "break"))
        self.root.bind("<Control-Shift-W>", lambda _e: (self._open_page("world"), "break"))
        self.root.bind("<Control-Shift-P>", lambda _e: (self._open_page("projects"), "break"))
        self.root.bind("<Control-Shift-T>", lambda _e: (self._open_page("tools"), "break"))
        self.root.bind("<Control-Shift-D>", lambda _e: (self._open_page("developer"), "break"))
        self.root.bind("<Control-Shift-space>", self._wake)
        self.root.bind("<Control-b>", lambda _e: (self._toggle_sidebar(), "break"))
        self.root.bind("<Escape>", lambda _e: (self._stop() if self.busy else None))
        self.root.bind("<Control-n>", lambda _e: (self._clear_chat(), "break"))

    def _wake(self, _event=None):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        return "break"

    # -- tray / window lifecycle ---------------------------------------

    def _start_tray(self) -> None:
        try:
            icon_path = self.base_dir / "launcher" / "launcher.ico"
            hicon = 0
            if icon_path.exists():
                hicon = ctypes.windll.user32.LoadImageW(
                    None, str(icon_path), 1, 32, 32, 0x00000010
                )
            if not hicon:
                return
            self._tray = TrayIcon("Memory-Specialized AI Assistant", hicon, lambda mid: self.root.after(0, lambda: self._tray_action(mid)))
            menu = [(TRAY_OPEN, "Open"), (TRAY_PAUSE, "Pause"), (TRAY_DEV, "Developer Mode"), (TRAY_QUIT, "Quit")]
            self._tray_active = self._tray.start(menu, "Memory-Specialized AI Assistant")
        except Exception:  # noqa: BLE001
            self._tray_active = False

    def _tray_action(self, menu_id: int) -> None:
        if menu_id == TRAY_OPEN:
            self._wake()
        elif menu_id == TRAY_DEV:
            self._open_page("developer")
            self._wake()
        elif menu_id == TRAY_QUIT:
            self._quit()
        elif menu_id == TRAY_PAUSE:
            self._wake()

    def _on_close(self) -> None:
        if self._quitting:
            return
        if self.busy:
            self.cancel.set()
        if self._tray_active:
            self.root.withdraw()
        else:
            self.root.iconify()

    def _quit(self) -> None:
        self._quitting = True
        self._cancel_after_callbacks()
        if self.busy:
            self.cancel.set()
        if self.runtime is not None:
            self.runtime.temporal.stop_ticker()
            self.runtime.temporal.end_session()
            voice = getattr(self.runtime, "voice", None)
            if voice is not None:
                voice.cancel_voice_session()
                voice.shutdown()
        if self._tray is not None:
            self._tray.stop()
        self.root.destroy()

    def _time_str(self) -> str:
        return _time.strftime("%H:%M")
