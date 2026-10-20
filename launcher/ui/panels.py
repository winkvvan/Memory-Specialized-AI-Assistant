"""Chris capability pages: Memory, World, Projects, Tools + detail views.

Pages are human-readable ("Chris remembers...", "Right now...") with raw
technical fields collapsed behind a Details toggle - never a raw database
table by default. Empty states are designed, not accidental.
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from launcher.ui.widgets import Bubble, PillButton


def _soft_scrollbar(theme) -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "Page.Vertical.TScrollbar",
        background=theme.c("scrollbar"),
        troughcolor=theme.c("background"),
        bordercolor=theme.c("background"),
        arrowcolor=theme.c("text-muted"),
        relief="flat",
    )
    style.map("Page.Vertical.TScrollbar", background=[("active", theme.c("text-muted"))])


class Page(tk.Frame):
    """Scrollable page with a title and human-readable content."""

    title = ""

    def __init__(self, parent, theme) -> None:
        super().__init__(parent, bg=theme.c("background"))
        self.theme = theme
        self._details_visible = False
        self._last_sig = None
        self._build()

    def _build(self) -> None:
        t = self.theme
        header = tk.Frame(self, bg=t.c("background"))
        header.pack(fill="x", padx=24, pady=(18, 6))
        tk.Label(header, text=self.title, bg=t.c("background"),
                 fg=t.c("text-primary"), font=t.font_role("title", bold=True),
                 anchor="w").pack(fill="x")
        self.subtitle = tk.Label(header, text="", bg=t.c("background"),
                                 fg=t.c("text-secondary"), font=t.font_role("small"),
                                 anchor="w")
        self.subtitle.pack(fill="x", pady=(2, 0))

        _soft_scrollbar(t)
        body = tk.Frame(self, bg=t.c("background"))
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=t.c("background"), highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(body, orient="vertical", style="Page.Vertical.TScrollbar",
                                    command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.content = tk.Frame(self.canvas, bg=t.c("background"))
        self._window = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

    def _on_resize(self, event) -> None:
        self.canvas.itemconfig(self._window, width=max(200, event.width))

    # -- reusable pieces ---------------------------------------------

    def add_section(self, text: str) -> tk.Label:
        label = tk.Label(self.content, text=text, bg=self.theme.c("background"),
                         fg=self.theme.c("text-muted"), font=self.theme.font_role("caption", bold=True),
                         anchor="w")
        label.pack(fill="x", padx=24, pady=(16, 6))
        return label

    def add_card(self, fill=None, outline=""):
        card = Bubble(self.content, self.theme,
                      fill=fill or self.theme.c("surface"),
                      radius=self.theme.radius("large"),
                      outline=outline or "",
                      width=1, stretch=True,
                      canvas_bg=self.theme.c("background"))
        inner = tk.Frame(card, bg=fill or self.theme.c("surface"))
        card.host(inner)
        card.pack(fill="x", padx=24, pady=4)
        return card, inner

    def add_key_value(self, parent, key: str, value: str, bg: str | None = None) -> None:
        row = tk.Frame(parent, bg=bg or self.theme.c("surface"))
        row.pack(fill="x", pady=1)
        tk.Label(row, text=key, bg=bg or self.theme.c("surface"),
                 fg=self.theme.c("text-muted"), font=self.theme.font_role("small"),
                 width=12, anchor="w").pack(side="left", padx=(12, 4))
        tk.Label(row, text=str(value), bg=bg or self.theme.c("surface"),
                 fg=self.theme.c("text-primary"), font=self.theme.font_role("small"),
                 anchor="w", justify="left", wraplength=360).pack(side="left", fill="x", expand=True)

    def add_empty(self, title: str, subtitle: str) -> None:
        box = tk.Frame(self.content, bg=self.theme.c("background"))
        box.pack(fill="x", padx=24, pady=(32, 8))
        dot = tk.Label(box, text="●", bg=self.theme.c("background"),
                       fg=self.theme.c("accent"), font=self.theme.font(16))
        dot.pack(pady=(0, 6))
        tk.Label(box, text=title, bg=self.theme.c("background"),
                 fg=self.theme.c("text-secondary"), font=self.theme.font_role("heading", bold=True),
                 anchor="center").pack()
        tk.Label(box, text=subtitle, bg=self.theme.c("background"),
                 fg=self.theme.c("text-muted"), font=self.theme.font_role("small"),
                 anchor="center", justify="center", wraplength=380).pack(pady=(4, 0))

    def add_details_toggle(self, rows: list[tuple[str, str]]) -> None:
        bar = tk.Frame(self.content, bg=self.theme.c("background"))
        bar.pack(fill="x", padx=24, pady=(4, 8))
        self.detail_btn = PillButton(bar, self.theme, text="Details",
                                     command=self._toggle_details,
                                     fill=self.theme.c("surface"),
                                     fg=self.theme.c("text-muted"),
                                     hover_fill=self.theme.c("surface-hover"),
                                     radius=self.theme.radius("pill"),
                                     padx=12, pady=3, font_size=8)
        self.detail_btn.pack(side="left")
        self._detail_rows = rows
        self._details_frame = tk.Frame(self.content, bg=self.theme.c("surface"))
        card = Bubble(self.content, self.theme, fill=self.theme.c("surface"),
                      radius=self.theme.radius("medium"),
                      outline=self.theme.c("border-soft"), width=1, stretch=True,
                      canvas_bg=self.theme.c("background"))
        inner = tk.Frame(card, bg=self.theme.c("surface"))
        card.host(inner)
        self._details_host = card
        self._details_inner = inner
        for key, value in rows:
            self.add_key_value(inner, key, value)

    def _toggle_details(self) -> None:
        if self._details_visible:
            self._details_host.pack_forget()
            self.detail_btn.set_text("Details")
            self._details_visible = False
        else:
            self._details_host.pack(fill="x", padx=24, pady=(0, 12))
            self.detail_btn.set_text("Hide details")
            self._details_visible = True

    def refresh(self, snapshot: dict) -> None:
        """Rebuild only when the displayed data actually changed (no flicker)."""
        sig = self._sig(snapshot)
        if sig == self._last_sig:
            return
        self._last_sig = sig
        self._render(snapshot)

    def _sig(self, snapshot: dict):
        return json.dumps(snapshot, sort_keys=True, default=str)

    def _render(self, snapshot: dict) -> None:
        pass

    def apply_theme(self, theme) -> None:
        self.theme = theme
        self._last_sig = None  # force rebuild with the new palette
        self.configure(bg=theme.c("background"))
        self.canvas.configure(bg=theme.c("background"))
        self.content.configure(bg=theme.c("background"))
        _soft_scrollbar(theme)
        for child in self.winfo_children():
            if isinstance(child, (tk.Label, tk.Frame)):
                child.configure(bg=theme.c("background"))
        for child in self.content.winfo_children():
            if isinstance(child, (tk.Label, tk.Frame)):
                child.configure(bg=theme.c("background"))


class WorldPage(Page):
    title = "世界感知"

    def _sig(self, snapshot: dict):
        world = snapshot.get("world", {})
        user = world.get("user", {})
        computer = world.get("computer", {})
        return (
            snapshot.get("world_summary", ""),
            user.get("attention", "-"),
            computer.get("foreground_app", "-"),
            snapshot.get("session_str", "-"),
            user.get("presence", "-"),
            user.get("estimated_state", "-"),
            world.get("location", "-"),
        )

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        world = snapshot.get("world", {})
        user = world.get("user", {})
        computer = world.get("computer", {})
        summary = snapshot.get("world_summary", "世界感知等待中")
        self.subtitle.configure(text="Right now")
        card, inner = self.add_card()
        tk.Label(inner, text=summary, bg=self.theme.c("surface"),
                 fg=self.theme.c("text-primary"), font=self.theme.font_role("heading", bold=True),
                 anchor="w", justify="left", wraplength=440).pack(fill="x", padx=14, pady=(12, 8))
        for key, value in (
            ("Attention", user.get("attention", "-")),
            ("Computer", computer.get("foreground_app") or "-"),
            ("Session", snapshot.get("session_str", "-")),
        ):
            self.add_key_value(inner, key, str(value))
        raw = [
            ("Activity", user.get("activity", "-")),
            ("Presence", user.get("presence", "-")),
            ("Estimated State", user.get("estimated_state", "-")),
            ("Window Title", computer.get("window_title") or "-"),
            ("Idle", f"{computer.get('idle_seconds', 0):.0f}s"),
            ("Location", world.get("location", "-")),
        ]
        self.add_details_toggle(raw)
        if not user and not computer:
            self.add_empty("World awareness is waiting...", "助手还没有感知到环境信号，稍后会在这里显示你正在做什么。")


class MemoryPage(Page):
    title = "记忆"

    def _sig(self, snapshot: dict):
        memories = snapshot.get("recent_memories", [])
        return tuple((m.get("kind"), m.get("age"), m.get("status"), m.get("content"))
                     for m in memories[:8])

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        memories = snapshot.get("recent_memories", [])
        self.subtitle.configure(text="记忆特化型 AI 助手会记住")
        if not memories:
            self.add_empty(
                "Nothing here yet.",
                "随着你们的交流，记忆会逐步建立。",
            )
            return
        self.add_section("最近记忆")
        for item in memories[:8]:
            card, inner = self.add_card()
            head = tk.Frame(inner, bg=self.theme.c("surface"))
            head.pack(fill="x", padx=14, pady=(10, 2))
            kind = item.get("kind", "conversation")
            tk.Label(head, text=kind, bg=self.theme.c("surface"),
                     fg=self.theme.c("accent"), font=self.theme.font_role("caption", bold=True)).pack(side="left")
            tk.Label(head, text=str(item.get("age", "")), bg=self.theme.c("surface"),
                     fg=self.theme.c("text-muted"), font=self.theme.font_role("caption")).pack(side="right")
            tk.Label(inner, text=item.get("content", ""), bg=self.theme.c("surface"),
                     fg=self.theme.c("text-primary"), font=self.theme.font_role("body"),
                     anchor="w", justify="left", wraplength=420).pack(fill="x", padx=14, pady=(2, 10))
        self.add_details_toggle([
            ("Status", str(item.get("status", "-")))
            for item in memories[:8]
        ])


class ProjectsPage(Page):
    title = "项目"

    def _sig(self, snapshot: dict):
        return ()

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        self.subtitle.configure(text="Coding Agent 即将到来")
        self.add_empty(
            "No projects yet.",
            "未来的 Coding Agent 会在这里显示你的项目、改动与测试结果。本阶段仅为 UI 占位。",
        )
        self.add_section("规划中的能力")
        for name, desc in (
            ("File Tree", "项目文件结构"),
            ("Diff", "改动对比"),
            ("Test Results", "测试结果"),
            ("Terminal", "终端输出"),
        ):
            card, inner = self.add_card()
            tk.Label(inner, text=name, bg=self.theme.c("surface"),
                     fg=self.theme.c("text-secondary"), font=self.theme.font_role("body", bold=True),
                     anchor="w").pack(fill="x", padx=14, pady=(8, 0))
            tk.Label(inner, text=desc, bg=self.theme.c("surface"),
                     fg=self.theme.c("text-muted"), font=self.theme.font_role("small"),
                     anchor="w").pack(fill="x", padx=14, pady=(0, 8))


class ToolsPage(Page):
    title = "工具"

    def _sig(self, snapshot: dict):
        return ()

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        self.subtitle.configure(text="未来能力（UI 占位）")
        self.add_empty(
            "Tools are coming.",
            "联网、电脑控制、视觉与记忆检索能力会通过可替换的工具接口接入。",
        )
        self.add_section("规划中的工具")
        for name, desc, tag in (
            ("Web", "联网搜索与阅读", "即将推出"),
            ("Computer", "电脑状态与控制", "即将推出"),
            ("Vision", "视觉理解", "即将推出"),
            ("Memory", "记忆检索", "即将推出"),
        ):
            card, inner = self.add_card()
            head = tk.Frame(inner, bg=self.theme.c("surface"))
            head.pack(fill="x", padx=14, pady=(8, 0))
            tk.Label(head, text=name, bg=self.theme.c("surface"),
                     fg=self.theme.c("text-primary"), font=self.theme.font_role("body", bold=True),
                     anchor="w").pack(side="left")
            tk.Label(head, text=tag, bg=self.theme.c("surface"),
                     fg=self.theme.c("text-muted"), font=self.theme.font_role("caption")).pack(side="right")
            tk.Label(inner, text=desc, bg=self.theme.c("surface"),
                     fg=self.theme.c("text-muted"), font=self.theme.font_role("small"),
                     anchor="w").pack(fill="x", padx=14, pady=(0, 8))


class PersonalityPage(Page):
    title = "人格"

    def _sig(self, snapshot: dict):
        p = snapshot.get("personality", {})
        adaptive = p.get("adaptive", {})
        return (
            p.get("name", "-"), p.get("version", "-"), p.get("traits", 0),
            p.get("identity", "-"), p.get("last_updated", "-"),
            tuple(sorted(adaptive.items())),
        )

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        p = snapshot.get("personality", {})
        self.subtitle.configure(text=f"v{p.get('version', '-')}")
        card, inner = self.add_card()
        tk.Label(inner, text=str(p.get("identity", "-")), bg=self.theme.c("surface"),
                 fg=self.theme.c("text-primary"), font=self.theme.font_role("body"),
                 anchor="w", justify="left", wraplength=440).pack(fill="x", padx=14, pady=12)
        adaptive = p.get("adaptive", {})
        prefs = "；".join(f"{k}={v}" for k, v in list(adaptive.items())[:4]) or "-"
        self.add_details_toggle([
            ("Name", p.get("name", "-")),
            ("Version", p.get("version", "-")),
            ("Core Traits", p.get("traits", 0)),
            ("Last Updated", p.get("last_updated", "-")),
            ("Adaptive", prefs),
        ])


class TemporalPage(Page):
    title = "时间"

    def _sig(self, snapshot: dict):
        t = snapshot.get("temporal", {})
        return (
            str(t.get("current_time", "-"))[:16],
            t.get("timezone", "-"), t.get("known_duration", "-"),
            t.get("session_minutes", 0), t.get("clock_mode", "-"),
        )

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        t = snapshot.get("temporal", {})
        self.subtitle.configure(text="连续时间轴")
        card, inner = self.add_card()
        tk.Label(inner, text=str(t.get("current_time", "-")),
                 bg=self.theme.c("surface"), fg=self.theme.c("text-primary"),
                 font=self.theme.font_role("heading", bold=True), anchor="w").pack(fill="x", padx=14, pady=12)
        self.add_details_toggle([
            ("Timezone", t.get("timezone", "-")),
            ("Known", t.get("known_duration", "-")),
            ("Session", f"{t.get('session_minutes', 0)} min"),
            ("Idle", f"{t.get('idle_seconds', 0)}s"),
            ("Clock", t.get("clock_mode", "-")),
        ])


class EventsPage(Page):
    title = "事件"

    def _sig(self, snapshot: dict):
        events = snapshot.get("recent_events", [])
        return tuple((e["timestamp"], e["type"], e["summary"]) for e in events[-18:])

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        events = snapshot.get("recent_events", [])
        self.subtitle.configure(text=f"{len(events)} 条最近事件")
        if not events:
            self.add_empty("No events yet.", "助手的重要变化会以事件形式记录在这里。")
            return
        for event in events[-18:]:
            card, inner = self.add_card()
            head = tk.Frame(inner, bg=self.theme.c("surface"))
            head.pack(fill="x", padx=14, pady=(8, 2))
            tk.Label(head, text=event["type"], bg=self.theme.c("surface"),
                     fg=self.theme.c("accent"), font=self.theme.font_role("caption", bold=True)).pack(side="left")
            tk.Label(head, text=event["timestamp"], bg=self.theme.c("surface"),
                     fg=self.theme.c("text-muted"), font=self.theme.font_role("caption")).pack(side="right")
            tk.Label(inner, text=event["summary"] or "-", bg=self.theme.c("surface"),
                     fg=self.theme.c("text-secondary"), font=self.theme.font_role("small"),
                     anchor="w", wraplength=420).pack(fill="x", padx=14, pady=(0, 8))


class DeveloperPage(Page):
    title = "开发"

    def _sig(self, snapshot: dict):
        return (
            snapshot.get("provider_name", "-"), snapshot.get("model", "-"),
            snapshot.get("provider_status", "-"), snapshot.get("chris_status", "-"),
            snapshot.get("theme", "-"), snapshot.get("version", 0),
        )

    def _render(self, snapshot: dict) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._details_visible = False
        self.subtitle.configure(text="运行时信息")
        card, inner = self.add_card()
        for key, value in (
            ("Provider", snapshot.get("provider_name", "-")),
            ("Model", snapshot.get("model", "-")),
            ("Provider Status", snapshot.get("provider_status", "-")),
            ("Assistant Status", snapshot.get("chris_status", "-")),
            ("Theme", snapshot.get("theme", "-")),
            ("Version", snapshot.get("version", 0)),
        ):
            self.add_key_value(inner, key, str(value))


PANELS = {
    "memory": MemoryPage,
    "world": WorldPage,
    "projects": ProjectsPage,
    "tools": ToolsPage,
    "personality": PersonalityPage,
    "temporal": TemporalPage,
    "events": EventsPage,
    "developer": DeveloperPage,
}
