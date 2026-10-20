"""Chat page: scrollable conversation, markdown messages (incl. tables),
streaming cursor, friendly error cards, tool/thinking chips and composer.

Assistant messages are the visual anchor: avatar + name + full-width text on
the page background. User messages are compact bubbles aligned right.
"""

from __future__ import annotations

import webbrowser

import tkinter as tk
from tkinter import ttk

from launcher.markdown import parse, render_text
from launcher.ui.widgets import Avatar, Bubble, PillButton, RoundButton

COLUMN_MAX = 820
STREAM_MARK = "▌"
STREAM_TAG = "stream_mark"


def markdown_tags(text: tk.Text, theme) -> None:
    family = theme.c("font_family", "Microsoft YaHei UI")
    text.tag_configure("bold", font=(family, 10, "bold"))
    text.tag_configure("italic", font=(family, 10, "italic"))
    text.tag_configure("code", background=theme.c("code-bg"), foreground=theme.c("code-fg"),
                       font=("Consolas", 9))
    text.tag_configure("link", foreground=theme.c("link"), underline=1)
    text.tag_configure("h1", font=(family, 14, "bold"))
    text.tag_configure("h2", font=(family, 12, "bold"))
    text.tag_configure("h3", font=(family, 11, "bold"))
    text.tag_configure("list", foreground=theme.c("text-primary"))
    text.tag_configure("quote", foreground=theme.c("text-muted"),
                       font=(family, 10, "italic"))
    text.tag_configure("hr", foreground=theme.c("text-muted"))


def _soft_scrollbar() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass


def configure_scrollbar(theme) -> None:
    _soft_scrollbar()
    style = ttk.Style()
    style.configure(
        "Soft.Vertical.TScrollbar",
        background=theme.c("scrollbar"),
        troughcolor=theme.c("background"),
        bordercolor=theme.c("background"),
        arrowcolor=theme.c("text-muted"),
        relief="flat",
    )
    style.map("Soft.Vertical.TScrollbar",
              background=[("active", theme.c("text-muted"))])


class CodeCard(Bubble):
    """Rounded code block card with a language label and a copy pill."""

    def __init__(self, parent, theme, content: str, lang: str) -> None:
        super().__init__(parent, theme, fill=theme.c("code-bg"),
                         radius=theme.radius("medium"), stretch=True,
                         canvas_bg=theme.c("background"))
        self._theme = theme
        self._content = content
        inner = tk.Frame(self, bg=theme.c("code-bg"))
        bar = tk.Frame(inner, bg=theme.c("code-bg"))
        bar.pack(fill="x", pady=(4, 2))
        tk.Label(bar, text=lang or "code", bg=theme.c("code-bg"),
                 fg=theme.c("text-muted"), font=theme.font_role("small")).pack(side="left", padx=10)
        self.copy_btn = PillButton(bar, theme, text="复制",
                                   command=lambda: self._copy(),
                                   fill=theme.c("surface-elevated"),
                                   hover_fill=theme.c("surface-hover"),
                                   radius=theme.radius("small"), padx=10, pady=2, font_size=8)
        self.copy_btn.pack(side="right", padx=6, pady=2)
        code = tk.Text(inner, wrap="none", bg=theme.c("code-bg"), fg=theme.c("code-fg"),
                       relief="flat", font=("Consolas", 9),
                       height=min(max(content.count("\n") + 1, 2), 18),
                       padx=8, pady=4, borderwidth=0, highlightthickness=0)
        xs = ttk.Scrollbar(inner, orient="horizontal", command=code.xview)
        code.configure(xscrollcommand=xs.set)
        code.insert("end", content)
        code.configure(state="disabled")
        code.pack(fill="x", padx=6, pady=(0, 2))
        xs.pack(fill="x", padx=6, pady=(0, 4))
        self._code = code
        self.host(inner)

    def _copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._content)

    def apply_theme(self, theme) -> None:
        self._theme = theme
        self.configure(bg=theme.c("background"))
        self.set_fill(theme.c("code-bg"))
        self._code.configure(bg=theme.c("code-bg"), fg=theme.c("code-fg"))
        for child in self.winfo_children():
            child.configure(bg=theme.c("code-bg"))
        self.copy_btn.apply_theme(theme)


class TableCard(Bubble):
    """Markdown table rendered as a flat grid (headers + rows)."""

    def __init__(self, parent, theme, headers: list[str], rows: list[list[str]]) -> None:
        super().__init__(parent, theme, fill=theme.c("surface"),
                         radius=theme.radius("medium"), outline=theme.c("border-soft"),
                         width=1, stretch=True, canvas_bg=theme.c("background"))
        self._theme = theme
        inner = tk.Frame(self, bg=theme.c("surface"))
        header = tk.Frame(inner, bg=theme.c("surface-sunken"))
        header.pack(fill="x")
        for cell in headers:
            tk.Label(header, text=cell, bg=theme.c("surface-sunken"),
                     fg=theme.c("text-secondary"), font=theme.font(9, bold=True),
                     anchor="w", padx=12, pady=6, wraplength=180).pack(side="left", fill="x", expand=True)
        for row in rows:
            divider = tk.Frame(inner, bg=theme.c("border-soft"), height=1)
            divider.pack(fill="x")
            row_frame = tk.Frame(inner, bg=theme.c("surface"))
            row_frame.pack(fill="x")
            for cell in row:
                tk.Label(row_frame, text=cell, bg=theme.c("surface"),
                         fg=theme.c("text-primary"), font=theme.font(9),
                         anchor="w", padx=12, pady=4, wraplength=180).pack(
                    side="left", fill="x", expand=True)
        self.host(inner)

    def apply_theme(self, theme) -> None:
        self._theme = theme
        self.configure(bg=theme.c("background"))
        self.set_fill(theme.c("surface"))
        self.set_outline(theme.c("border-soft"), 1)
        for child in self.winfo_children():
            child.configure(bg=theme.c("surface"))
            for sub in child.winfo_children():
                if isinstance(sub, tk.Label):
                    sub.configure(bg=theme.c("surface"))


class MessageWidget(tk.Frame):
    """One chat message. Assistant = anchor style; user = compact right bubble."""

    def __init__(self, parent, role: str, content: str, timestamp: str, theme) -> None:
        super().__init__(parent, bg=theme.c("background"))
        self.theme = theme
        self.role = role
        self.body_text = None
        self.code_frames: list = []
        self.table_frames: list = []
        self._avatar = None
        self._bubble = None
        self._body_host = None
        self._name_label = None
        self._time_label = None
        self._page = theme.c("background")
        self._stream_marker = None
        self._thinking_chip = None
        self._link_urls: dict = {}

        if role == "user":
            self._build_user(timestamp, content)
        else:
            self._build_chris(timestamp, content)

    # -- builders -----------------------------------------------------

    def _build_chris(self, timestamp: str, content: str) -> None:
        page = self.theme.c("background")
        header = tk.Frame(self, bg=page)
        header.pack(fill="x", padx=(4, 12), pady=(12, 4))
        self._avatar = Avatar(header, self.theme, "M", size=30,
                              fill=self.theme.c("avatar-chris"))
        self._avatar.pack(side="left", padx=(2, 10))
        name = tk.Label(header, text="Memory-Specialized AI Assistant", bg=page, fg=self.theme.c("text-primary"),
                        font=self.theme.font(10, bold=True))
        name.pack(side="left")
        self._thinking_chip = PillButton(header, self.theme, text="思考中…",
                                         fill=self.theme.c("accent-soft"),
                                         fg=self.theme.c("accent"),
                                         hover_fill=self.theme.c("accent-soft"),
                                         radius=self.theme.radius("pill"),
                                         padx=8, pady=2, font_size=8)
        self._thinking_chip.pack(side="left", padx=(10, 0))
        self._thinking_chip.pack_forget()
        time = tk.Label(header, text=timestamp, bg=page, fg=self.theme.c("text-muted"),
                        font=self.theme.font(9))
        time.pack(side="right")
        self._name_label = name
        self._time_label = time

        self._body_host = tk.Frame(self, bg=page)
        self._body_host.pack(fill="x", padx=(44, 16), pady=(0, 8))
        self.body_text = self._new_text(self._body_host, bg=page, fg=self.theme.c("text-primary"))
        self.body_text.pack(fill="x")
        self._insert_markdown(content, host=self._body_host)

    def _build_user(self, timestamp: str, content: str) -> None:
        page = self.theme.c("background")
        header = tk.Frame(self, bg=page)
        header.pack(fill="x", padx=(12, 4), pady=(10, 2))
        time = tk.Label(header, text=timestamp, bg=page, fg=self.theme.c("text-muted"),
                        font=self.theme.font(9))
        time.pack(side="right")
        self._time_label = time

        self._bubble = Bubble(self, self.theme, fill=self.theme.c("user-bubble"),
                              radius=self.theme.radius("large"),
                              fixed_width=self._bubble_width(content),
                              canvas_bg=page)
        text = self._new_text(self._bubble, bg=self.theme.c("user-bubble"),
                              fg=self.theme.c("user-fg"), padx=12, pady=8)
        self.body_text = text
        self._bubble.host(text)
        self._insert_markdown(content, host=self._bubble)
        self._bubble.pack(side="right", anchor="e", padx=(60, 4), pady=(2, 8))

    @staticmethod
    def _bubble_width(content: str) -> int:
        max_chars = max((len(line) for line in content.split("\n")), default=12)
        px = min(int(COLUMN_MAX * 0.7), max_chars * 9 + 24)
        return max(120, px)

    # -- shared -------------------------------------------------------

    def _new_text(self, parent, bg, fg, padx=6, pady=4) -> tk.Text:
        text = tk.Text(
            parent, wrap="word", bg=bg, fg=fg, relief="flat",
            font=self.theme.font(10), padx=padx, pady=pady, height=1,
            borderwidth=0, highlightthickness=0, insertbackground=fg,
        )
        text.configure(state="disabled")
        markdown_tags(text, self.theme)
        text.tag_bind("link", "<Button-1>", self._open_link)
        text.tag_bind("link", "<Enter>", lambda _e: text.configure(cursor="hand2"))
        text.tag_bind("link", "<Leave>", lambda _e: text.configure(cursor=""))
        text._link_urls = self._link_urls  # noqa: SLF001
        return text

    def _open_link(self, event) -> None:
        text = self.body_text
        idx = text.index(f"@{event.x},{event.y}")
        for (start, end), url in self._link_urls.items():
            if text.compare(start, "<=", idx) and text.compare(idx, "<", end):
                webbrowser.open(url)
                return

    def _insert_markdown(self, content: str, host) -> None:
        for seg in parse(content):
            if seg["type"] == "text":
                self._insert_text(seg["content"])
            elif seg["type"] == "code":
                self._insert_code(host, seg["content"], seg["lang"])
            elif seg["type"] == "table":
                self._insert_table(host, seg["headers"], seg["rows"])

    def _insert_text(self, content: str) -> None:
        if not content.strip():
            return
        self.body_text.configure(state="normal")
        render_text(self.body_text, content, {}, self._link_urls)
        self.body_text.configure(state="disabled")
        self._fit()

    def _insert_code(self, host, content: str, lang: str) -> None:
        if self.role == "user":
            self.body_text.configure(state="normal")
            self.body_text.insert("end", content + "\n", ("code",))
            self.body_text.configure(state="disabled")
            self._fit()
            return
        self.body_text.configure(state="normal")
        self.body_text.insert("end", "\n")
        self.body_text.configure(state="disabled")
        card = CodeCard(host, self.theme, content, lang)
        card.pack(fill="x", pady=4)
        self.code_frames.append(card)

    def _insert_table(self, host, headers: list[str], rows: list[list[str]]) -> None:
        if self.role == "user":
            self.body_text.configure(state="normal")
            self.body_text.insert("end", " | ".join(headers) + "\n")
            for row in rows:
                self.body_text.insert("end", " | ".join(row) + "\n")
            self.body_text.configure(state="disabled")
            self._fit()
            return
        self.body_text.configure(state="normal")
        self.body_text.insert("end", "\n")
        self.body_text.configure(state="disabled")
        card = TableCard(host, self.theme, headers, rows)
        card.pack(fill="x", pady=4)
        self.table_frames.append(card)

    def _fit(self) -> None:
        lines = int(self.body_text.index("end-1c").split(".")[0])
        self.body_text.configure(height=max(min(lines, 30), 1))

    # -- streaming ----------------------------------------------------

    def show_thinking(self) -> None:
        if self._thinking_chip is not None:
            self._thinking_chip.pack(side="left", padx=(10, 0))

    def hide_thinking(self) -> None:
        if self._thinking_chip is not None:
            self._thinking_chip.pack_forget()

    def begin_stream(self) -> None:
        self.show_thinking()
        if self.theme.motion_enabled:
            self.body_text.configure(state="normal")
            self.body_text.insert("end-1c", STREAM_MARK, ("stream", STREAM_TAG))
            self.body_text.configure(state="disabled")

    def append_chunk(self, chunk: str) -> None:
        self.body_text.configure(state="normal")
        if self.body_text.tag_ranges(STREAM_TAG):
            self.body_text.insert(STREAM_TAG + ".first", chunk)
        else:
            self.body_text.insert("end", chunk)
        self.body_text.configure(state="disabled")
        self._fit()

    def end_stream(self) -> None:
        self.hide_thinking()
        self.body_text.configure(state="normal")
        ranges = self.body_text.tag_ranges(STREAM_TAG)
        if ranges:
            self.body_text.delete(ranges[0], ranges[1])
        self.body_text.configure(state="disabled")
        self._fit()

    def apply_theme(self, theme) -> None:
        self.configure(bg=theme.c("background"))
        self.theme = theme
        page = theme.c("background")
        for child in self.winfo_children():
            if isinstance(child, (tk.Label, tk.Frame)):
                child.configure(bg=page)
                for sub in child.winfo_children():
                    if isinstance(sub, tk.Label):
                        sub.configure(bg=page)
        if self._avatar is not None:
            self._avatar.apply_theme(theme)
        if self._name_label is not None:
            self._name_label.configure(fg=theme.c("text-primary"))
        if self._time_label is not None:
            self._time_label.configure(fg=theme.c("text-muted"))
        if self._bubble is not None:
            self._bubble.set_fill(theme.c("user-bubble"))
            self._bubble.configure(bg=page)
        if self.body_text is not None:
            bg = theme.c("user-bubble" if self.role == "user" else "background")
            fg = theme.c("user-fg" if self.role == "user" else "text-primary")
            self.body_text.configure(bg=bg, fg=fg, insertbackground=fg)
            markdown_tags(self.body_text, theme)
        for card in self.code_frames:
            card.apply_theme(theme)
        for card in self.table_frames:
            card.apply_theme(theme)
        if self._thinking_chip is not None:
            self._thinking_chip.apply_theme(theme)


class ErrorCard(Bubble):
    """Friendly error state with Retry + expandable Details."""

    def __init__(self, parent, theme, message: str, detail: str,
                 on_retry=None) -> None:
        super().__init__(parent, theme, fill=theme.c("error-soft"),
                         radius=theme.radius("large"), stretch=True,
                         canvas_bg=theme.c("background"))
        self._theme = theme
        inner = tk.Frame(self, bg=theme.c("error-soft"))
        row = tk.Frame(inner, bg=theme.c("error-soft"))
        row.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(row, text="!", bg=theme.c("error-soft"), fg=theme.c("error"),
                 font=theme.font(14, bold=True)).pack(side="left", padx=(0, 10))
        col = tk.Frame(row, bg=theme.c("error-soft"))
        col.pack(side="left", fill="x", expand=True)
        tk.Label(col, text="Something went wrong.", bg=theme.c("error-soft"),
                 fg=theme.c("text-primary"), font=theme.font(10, bold=True),
                 anchor="w").pack(fill="x")
        tk.Label(col, text=message, bg=theme.c("error-soft"),
                 fg=theme.c("text-secondary"), font=theme.font(9),
                 anchor="w", justify="left", wraplength=560).pack(fill="x", pady=(2, 0))
        buttons = tk.Frame(inner, bg=theme.c("error-soft"))
        buttons.pack(fill="x", padx=16, pady=(2, 10))
        self.retry_btn = PillButton(buttons, theme, text="Retry",
                                    command=on_retry or (lambda: None),
                                    fill=theme.c("accent"), fg=theme.c("accent-fg"),
                                    hover_fill=theme.c("accent-hover"),
                                    radius=theme.radius("medium"), padx=14, pady=4, font_size=9)
        self.retry_btn.pack(side="left")
        self.detail_btn = PillButton(buttons, theme, text="Details",
                                     command=self._toggle_detail,
                                     fill=theme.c("surface-elevated"),
                                     fg=theme.c("text-secondary"),
                                     hover_fill=theme.c("surface-hover"),
                                     radius=theme.radius("medium"), padx=12, pady=4, font_size=9)
        self.detail_btn.pack(side="left", padx=6)
        self._detail_text = tk.Text(inner, wrap="word", height=4,
                                    bg=theme.c("surface"), fg=theme.c("text-secondary"),
                                    font=("Consolas", 8), relief="flat",
                                    padx=10, pady=8, borderwidth=0, highlightthickness=0)
        self._detail_text.insert("1.0", detail)
        self._detail_text.configure(state="disabled")
        self.host(inner)

    def _toggle_detail(self) -> None:
        if self._detail_text.winfo_ismapped():
            self._detail_text.pack_forget()
            self.detail_btn.set_text("Details")
        else:
            self._detail_text.pack(fill="x", padx=10, pady=(0, 8))
            self.detail_btn.set_text("Hide")

    def apply_theme(self, theme) -> None:
        self._theme = theme
        self.configure(bg=theme.c("background"))
        self.set_fill(theme.c("error-soft"))
        for child in self.winfo_children():
            child.configure(bg=theme.c("error-soft"))
            for sub in child.winfo_children():
                if isinstance(sub, (tk.Label, tk.Frame)):
                    sub.configure(bg=theme.c("error-soft"))
        self._detail_text.configure(bg=theme.c("surface"), fg=theme.c("text-secondary"))
        self.retry_btn.apply_theme(theme)
        self.detail_btn.apply_theme(theme)


class ChatView(tk.Frame):
    """Scrollable list of messages in a centered max-width column."""

    def __init__(self, parent, theme) -> None:
        super().__init__(parent, bg=theme.c("background"))
        self.theme = theme
        self.canvas = tk.Canvas(self, bg=theme.c("background"), highlightthickness=0, bd=0)
        configure_scrollbar(theme)
        self.scroll = ttk.Scrollbar(self, orient="vertical", style="Soft.Vertical.TScrollbar",
                                    command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=theme.c("background"))
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.messages: list[MessageWidget] = []
        self._current_stream: MessageWidget | None = None

    def _on_resize(self, event) -> None:
        w = max(120, min(event.width, COLUMN_MAX))
        x = (event.width - w) // 2
        self.canvas.itemconfig(self._window, width=w)
        self.canvas.coords(self._window, x, 0)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def add_message(self, role: str, content: str, timestamp: str) -> MessageWidget:
        msg = MessageWidget(self.inner, role, content, timestamp, self.theme)
        msg.pack(fill="x")
        self.messages.append(msg)
        self._scroll_to_bottom()
        return msg

    def start_streaming(self, timestamp: str) -> MessageWidget:
        msg = MessageWidget(self.inner, "chris", "", timestamp, self.theme)
        msg.pack(fill="x")
        self.messages.append(msg)
        self._current_stream = msg
        msg.begin_stream()
        self._scroll_to_bottom()
        return msg

    def append_stream_chunk(self, chunk: str) -> None:
        if self._current_stream is not None:
            self._current_stream.append_chunk(chunk)
            self._scroll_to_bottom()

    def finish_streaming(self) -> None:
        if self._current_stream is not None:
            self._current_stream.end_stream()
            self._current_stream = None
        self._scroll_to_bottom()

    def add_error(self, message: str, detail: str = "", on_retry=None) -> None:
        card = ErrorCard(self.inner, self.theme, message, detail, on_retry)
        card.pack(fill="x", padx=44, pady=8)
        self.messages.append(card)  # keep for theme refresh
        self._scroll_to_bottom()

    def clear_messages(self) -> None:
        self._current_stream = None
        for msg in self.messages:
            msg.destroy()
        self.messages = []
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def apply_theme(self, theme) -> None:
        self.theme = theme
        configure_scrollbar(theme)
        self.configure(bg=theme.c("background"))
        self.canvas.configure(bg=theme.c("background"))
        self.inner.configure(bg=theme.c("background"))
        for msg in self.messages:
            msg.apply_theme(theme)


class InputBar(tk.Frame):
    """Rounded composer: placeholder text, send/stop, voice toggle + mic."""

    def __init__(self, parent, on_send, on_stop, theme,
                 on_voice=None, on_mic=None) -> None:
        super().__init__(parent, bg=theme.c("background"))
        self.theme = theme
        self.on_send = on_send
        self.on_stop = on_stop
        self.on_voice = on_voice
        self.on_mic = on_mic
        self._placeholder = "输入消息…"
        self._showing_placeholder = True
        self._modified = False

        self._col = tk.Frame(self, bg=theme.c("background"))
        self._col_pad = 0
        self._updating_resize = False
        self._col.pack(fill="x", pady=(0, 10))

        self._box = Bubble(self._col, theme, fill=theme.c("surface"),
                           radius=theme.radius("large"), outline=theme.c("border"),
                           width=1, stretch=True)
        self._box.pack(fill="x")

        inner = tk.Frame(self._box, bg=theme.c("surface"))
        self._inner = inner
        self.text = tk.Text(
            inner, height=2, wrap="word", font=theme.font(10),
            bg=theme.c("surface"), fg=theme.c("text-primary"), relief="flat",
            padx=8, pady=8, borderwidth=0, highlightthickness=0,
            insertbackground=theme.c("text-primary"),
        )
        self.text.pack(fill="x", padx=8, pady=(8, 2))
        self.text.insert("1.0", self._placeholder)
        self.text.configure(fg=theme.c("placeholder"))
        self.text.bind("<Return>", self._on_return)
        self.text.bind("<Control-Return>", lambda _e: (self._send(), "break"))
        self.text.bind("<Escape>", lambda _e: (self.on_stop(), "break"))
        self.text.bind("<FocusIn>", self._on_focus_in)
        self.text.bind("<FocusOut>", self._on_focus_out)
        self.text.bind("<<Modified>>", self._on_modified)

        bottom = tk.Frame(inner, bg=theme.c("surface"))
        bottom.pack(fill="x", padx=8, pady=(2, 8))
        self.hint = tk.Label(bottom, text="Enter 发送 · Shift+Enter 换行 · Esc 停止",
                             bg=theme.c("surface"), fg=theme.c("text-muted"),
                             font=theme.font_role("caption"))
        self.hint.pack(side="left", pady=4)

        self.send_btn = RoundButton(bottom, theme, glyph="↑", command=self._send,
                                    size=32, font_size=13, disabled=True)
        self.send_btn.pack(side="right", padx=(4, 0))
        self.stop_btn = PillButton(bottom, theme, text="停止", command=self.on_stop,
                                   fill=theme.c("surface-hover"), fg=theme.c("text-primary"),
                                   hover_fill=theme.c("border-soft"), radius=theme.radius("medium"),
                                   padx=14, pady=4, font_size=9, disabled=True)
        self.stop_btn.pack(side="right", padx=6)

        self.voice_btn = PillButton(bottom, theme, text="语音",
                                    command=self._on_voice,
                                    fill=theme.c("surface"), fg=theme.c("text-secondary"),
                                    hover_fill=theme.c("surface-hover"),
                                    radius=theme.radius("pill"), padx=12, pady=4,
                                    font_size=9, disabled=on_voice is None)
        self.voice_btn.pack(side="right", padx=(0, 6))
        self.mic_btn = PillButton(bottom, theme, text="录音",
                                  command=self._on_mic,
                                  fill=theme.c("surface"), fg=theme.c("text-secondary"),
                                  hover_fill=theme.c("surface-hover"),
                                  radius=theme.radius("pill"), padx=12, pady=4,
                                  font_size=9, disabled=True)
        self.mic_btn.pack(side="right", padx=(0, 6))
        self.attach_btn = PillButton(bottom, theme, text="＋",
                                     fill=theme.c("surface"), fg=theme.c("text-muted"),
                                     hover_fill=theme.c("surface-hover"),
                                     radius=theme.radius("pill"), padx=10, pady=4,
                                     font_size=11, disabled=True)
        self.attach_btn.pack(side="right", padx=(0, 6))

        self._box.host(inner)
        self._current_text = ""

        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event) -> None:
        if event.width <= 0 or self._updating_resize:
            return
        w = min(event.width, COLUMN_MAX)
        pad = max(0, (event.width - w) // 2)
        if pad != self._col_pad:
            self._col_pad = pad
            self._updating_resize = True
            self._col.pack(fill="x", padx=pad, pady=(0, 10))
            self._updating_resize = False

    def _on_focus_in(self, _e=None):
        if self._showing_placeholder:
            self.text.delete("1.0", "end")
            self._showing_placeholder = False
            self.text.configure(fg=self.theme.c("text-primary"))
        self._box.set_outline(self.theme.c("accent"), 2)

    def _on_focus_out(self, _e=None):
        self._box.set_outline(self.theme.c("border"), 1)
        content = self.text.get("1.0", "end").strip()
        if not content:
            self.text.delete("1.0", "end")
            self.text.insert("1.0", self._placeholder)
            self.text.configure(fg=self.theme.c("placeholder"))
            self._showing_placeholder = True
            self.send_btn.set_enabled(False)

    def _on_modified(self, _e=None):
        if self._showing_placeholder:
            self.text.edit_modified(False)
            return
        content = self.text.get("1.0", "end").strip()
        self.send_btn.set_enabled(bool(content))
        self.text.edit_modified(False)

    def _on_return(self, event):
        if event.state & 0x0001:  # Shift held -> newline
            return None
        return self._send()

    def _send(self):
        if self._showing_placeholder:
            return "break"
        text = self.text.get("1.0", "end").strip()
        if text:
            self.text.delete("1.0", "end")
            self.text.edit_modified(False)
            self.send_btn.set_enabled(False)
            self.on_send(text)
        return "break"

    def set_busy(self, busy: bool) -> None:
        self.send_btn.set_enabled(not busy)
        self.stop_btn.set_enabled(busy)

    def _on_voice(self) -> None:
        if self.on_voice is not None:
            self.on_voice()

    def _on_mic(self) -> None:
        if self.on_mic is not None:
            self.on_mic()

    def set_voice_active(self, active: bool) -> None:
        c = self.theme.c
        if active:
            self.voice_btn.set_text("● 语音")
            self.voice_btn.set_colors(fill=c("accent-soft"), fg=c("accent"),
                                      hover=c("accent-soft"))
        else:
            self.voice_btn.set_text("语音")
            self.voice_btn.set_colors(fill=c("surface"), fg=c("text-secondary"),
                                      hover=c("surface-hover"))

    def set_voice_available(self, available: bool) -> None:
        self.voice_btn.set_enabled(available)

    def set_mic_enabled(self, enabled: bool) -> None:
        self.mic_btn.set_enabled(enabled)

    def set_recording(self, recording: bool) -> None:
        c = self.theme.c
        if recording:
            self.mic_btn.set_text("■ 停止")
            self.mic_btn.set_colors(fill=c("error-soft"), fg=c("error"),
                                    hover=c("error-soft"))
        else:
            self.mic_btn.set_text("录音")
            self.mic_btn.set_colors(fill=c("surface"), fg=c("text-secondary"),
                                    hover=c("surface-hover"))

    def apply_theme(self, theme) -> None:
        self.theme = theme
        self.configure(bg=theme.c("background"))
        self._col.configure(bg=theme.c("background"))
        self._box.configure(bg=theme.c("background"))
        self._box.set_fill(theme.c("surface"))
        self._box.set_outline(theme.c("border"), 1)
        self._inner.configure(bg=theme.c("surface"))
        self.text.configure(bg=theme.c("surface"), fg=theme.c("text-primary"),
                            insertbackground=theme.c("text-primary"))
        if self._showing_placeholder:
            self.text.configure(fg=theme.c("placeholder"))
        for child in self._inner.winfo_children():
            if isinstance(child, (tk.Label, tk.Frame)):
                child.configure(bg=theme.c("surface"))
        self.hint.configure(bg=theme.c("surface"), fg=theme.c("text-muted"))
        self.send_btn.apply_theme(theme)
        self.stop_btn.apply_theme(theme)
        self.voice_btn.apply_theme(theme)
        self.mic_btn.apply_theme(theme)
        self.attach_btn.apply_theme(theme)
