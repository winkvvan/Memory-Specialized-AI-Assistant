"""UI drawing primitives: rounded containers, pill/circle buttons, avatars.

Everything is tkinter-Canvas based for smooth rounded corners. Colours come
only from semantic Theme tokens - never hard-coded hex. Buttons support
keyboard activation and a visible focus ring for accessibility.
"""

from __future__ import annotations

import tkinter as tk


def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """Smooth rounded rectangle drawn as a polygon (r = corner radius)."""
    pts = [
        x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
        x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
        x1, y2 - r, x1, y1 + r, x1, y1, x1 + r, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


def parent_bg(widget, theme) -> str:
    """Best-effort parent background so canvas corners blend into their host."""
    try:
        bg = widget.master.cget("bg")
        if bg:
            return bg
    except tk.TclError:
        pass
    return theme.c("background")


def _bind_keys(widget, command) -> None:
    """Keyboard activation (Space/Return) + visible focus ring hooks."""
    widget.bind("<space>", lambda _e: (command(), "break") if widget._enabled else None)
    widget.bind("<Return>", lambda _e: (command(), "break") if widget._enabled else None)
    widget.bind("<FocusIn>", lambda _e: widget._set_focused(True))
    widget.bind("<FocusOut>", lambda _e: widget._set_focused(False))


class Bubble(tk.Canvas):
    """Rounded-rect container hosting one child widget, auto-fitting its size."""

    def __init__(self, parent, theme, fill, radius: int = 14, pad: int = 0,
                 outline: str = "", width: int = 1, stretch: bool = False,
                 canvas_bg: str = "", fixed_width: int | None = None) -> None:
        super().__init__(parent, bg=canvas_bg or parent_bg(parent, theme),
                         highlightthickness=0, bd=0)
        self._theme = theme
        self._canvas_bg = canvas_bg
        self._fill = fill
        self._radius = radius
        self._pad = pad
        self._outline = outline
        self._outline_w = width
        self._stretch = stretch
        self._last = None
        self._fixed_w = fixed_width
        self._child = None
        self._child_id = None
        self._min_w = 40
        self._min_h = 22
        self.bind("<Configure>", self._on_resize)

    def host(self, widget) -> None:
        self._child = widget
        widget.bind("<Configure>", self._fit)
        self._child_id = self.create_window(self._pad, self._pad, anchor="nw", window=widget)
        self._fit()

    def _on_resize(self, _e=None) -> None:
        self._redraw()
        if self._stretch:
            self._fit()

    def _content_width(self) -> int:
        if self._stretch and self.winfo_width() > 0:
            return max(self.winfo_width() - 2 * self._pad, self._min_w)
        if self._fixed_w is not None:
            return max(self._fixed_w, self._min_w)
        if self._child is not None:
            return max(self._child.winfo_reqwidth(), self._min_w)
        return self._min_w

    def _fit(self, _e=None) -> None:
        if self._child is None:
            return
        w = self._content_width()
        h = max(self._child.winfo_reqheight(), self._min_h)
        if (w + 2 * self._pad, h + 2 * self._pad) != (self.winfo_width(), self.winfo_height()):
            self.configure(width=w + 2 * self._pad, height=h + 2 * self._pad)
        self.itemconfig(self._child_id, width=w, height=h)
        self._redraw()

    def _redraw(self, _e=None) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 0 or h <= 0:
            return
        key = (w, h, self._fill, self._outline)
        if key == self._last:
            return
        self._last = key
        self.delete("shape")
        rounded_rect(self, 1, 1, w - 1, h - 1, self._radius,
                     fill=self._fill, outline=self._outline, width=self._outline_w, tags="shape")
        self.tag_lower("shape")

    def set_fill(self, fill: str) -> None:
        self._fill = fill
        self._redraw()

    def set_outline(self, outline: str, width: int = 1) -> None:
        self._outline = outline
        self._outline_w = width
        self._redraw()

    def apply_theme(self, theme) -> None:
        self._theme = theme
        self._sync_bg()
        self._redraw()

    def _sync_bg(self) -> None:
        bg = self._canvas_bg or parent_bg(self, self._theme)
        self.configure(bg=bg)


class PillButton(tk.Canvas):
    """Rounded pill button with hover/pressed/disabled/focus states."""

    def __init__(self, parent, theme, text: str = "", command=None, fill=None, fg=None,
                 hover_fill=None, radius: int = 12, padx: int = 14, pady: int = 5,
                 font_size: int = 9, bold: bool = False, disabled: bool = False) -> None:
        super().__init__(parent, bg=parent_bg(parent, theme), highlightthickness=0, bd=0,
                         takefocus=not disabled)
        self._theme = theme
        self._command = command
        self._fill = fill if fill is not None else theme.c("surface")
        self._fg = fg if fg is not None else theme.c("text-primary")
        self._hover = hover_fill if hover_fill is not None else theme.c("surface-hover")
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._font = theme.font(font_size, bold=bold)
        self._enabled = not disabled
        self._hovering = False
        self._focused = False
        self._text = text
        self._last = None
        self._text_id = self.create_text(0, 0, text=text, fill=self._fg, font=self._font, tags="txt")
        self._measure()
        self.bind("<Configure>", self._redraw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        _bind_keys(self, self._command or (lambda: None))
        self.configure(cursor="hand2" if self._enabled else "arrow")

    def _measure(self) -> None:
        bbox = self.bbox("txt")
        if bbox:
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            self.configure(width=w + 2 * self._padx, height=h + 2 * self._pady)

    def _redraw(self, _e=None) -> None:
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 0 or h <= 0:
            return
        key = (w, h, self._current_fill(), self._focused)
        if key == self._last:
            return
        self._last = key
        self.delete("bg")
        rounded_rect(self, 1, 1, w - 1, h - 1, self._radius,
                     fill=self._current_fill(), outline="", tags="bg")
        self.tag_lower("bg")
        if self._focused and self._enabled:
            self.delete("focus")
            rounded_rect(self, 3, 3, w - 3, h - 3, max(4, self._radius - 2),
                         outline=self._theme.c("focus-ring"), width=2, tags="focus")
            self.tag_lower("focus")
        else:
            self.delete("focus")
        self.coords("txt", w / 2, h / 2)
        self.tag_raise("txt")

    def _current_fill(self) -> str:
        if not self._enabled:
            return self._theme.c("surface-sunken")
        if self._hovering:
            return self._hover
        return self._fill

    def _on_enter(self, _e=None):
        self._hovering = True
        self._redraw()

    def _on_leave(self, _e=None):
        self._hovering = False
        self._redraw()

    def _on_click(self, _e=None):
        if self._enabled and self._command is not None:
            self._command()

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=enabled)
        self._redraw()

    def set_text(self, text: str) -> None:
        self._text = text
        self.itemconfig("txt", text=text)
        self._measure()
        self._redraw()

    def set_colors(self, fill=None, hover=None, fg=None) -> None:
        if fill is not None:
            self._fill = fill
        if hover is not None:
            self._hover = hover
        if fg is not None:
            self._fg = fg
            self.itemconfig("txt", fill=fg)
        self._last = None
        self._sync_bg()
        self._redraw()

    def apply_theme(self, theme) -> None:
        self._theme = theme
        self._last = None
        self._sync_bg()
        self._redraw()

    def _sync_bg(self) -> None:
        self.configure(bg=parent_bg(self, self._theme))


class RoundButton(tk.Canvas):
    """Circular icon button (send arrow etc.) with focus/hover states."""

    def __init__(self, parent, theme, glyph: str = "↑", command=None, fill=None,
                 fg=None, hover_fill=None, size: int = 34, font_size: int = 14,
                 disabled: bool = False) -> None:
        super().__init__(parent, bg=parent_bg(parent, theme), highlightthickness=0, bd=0,
                         takefocus=not disabled)
        self._theme = theme
        self._command = command
        self._fill = fill if fill is not None else theme.c("accent")
        self._fg = fg if fg is not None else theme.c("accent-fg")
        self._hover = hover_fill if hover_fill is not None else theme.c("accent-hover")
        self._size = size
        self._enabled = not disabled
        self._hovering = False
        self._focused = False
        self._font = theme.font(font_size, bold=True)
        self._glyph = glyph
        self._last = None
        self.configure(width=size, height=size)
        self._glyph_id = self.create_text(size / 2, size / 2, text=glyph, fill=self._fg,
                                          font=self._font, tags="glyph")
        self.bind("<Configure>", self._redraw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        _bind_keys(self, self._command or (lambda: None))
        self.configure(cursor="hand2" if self._enabled else "arrow")

    def _redraw(self, _e=None) -> None:
        key = (self._current_fill(), self._focused)
        if key == self._last:
            return
        self._last = key
        self.delete("bg")
        r = self._size / 2 - 1
        self.create_oval(1, 1, self._size - 1, self._size - 1,
                         fill=self._current_fill(), outline="", tags="bg")
        self.tag_lower("bg")
        if self._focused and self._enabled:
            self.delete("focus")
            self.create_oval(3, 3, self._size - 3, self._size - 3,
                             outline=self._theme.c("focus-ring"), width=2, tags="focus")
            self.tag_lower("focus")
        else:
            self.delete("focus")
        self.coords("glyph", self._size / 2, self._size / 2)
        self.tag_raise("glyph")

    def _current_fill(self) -> str:
        if not self._enabled:
            return self._theme.c("surface-sunken")
        if self._hovering:
            return self._hover
        return self._fill

    def _on_enter(self, _e=None):
        self._hovering = True
        self._redraw()

    def _on_leave(self, _e=None):
        self._hovering = False
        self._redraw()

    def _on_click(self, _e=None):
        if self._enabled and self._command is not None:
            self._command()

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=enabled)
        self._last = None
        self._redraw()

    def apply_theme(self, theme) -> None:
        self._theme = theme
        self._last = None
        self._sync_bg()
        self._redraw()

    def _sync_bg(self) -> None:
        self.configure(bg=parent_bg(self, self._theme))


class Avatar(tk.Canvas):
    """Small colored circle with a single-letter glyph."""

    def __init__(self, parent, theme, glyph: str, size: int = 28, fill=None, fg=None) -> None:
        super().__init__(parent, bg=parent_bg(parent, theme), highlightthickness=0, bd=0)
        self._theme = theme
        self._fill = fill if fill is not None else theme.c("avatar-chris")
        self._fg = fg if fg is not None else "#ffffff"
        self._size = size
        self._font = theme.font(max(9, size // 2), bold=True)
        self.configure(width=size, height=size)
        self._glyph_id = self.create_text(size / 2, size / 2, text=glyph, fill=self._fg,
                                          font=self._font, tags="glyph")
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _e=None) -> None:
        self.delete("bg")
        self.create_oval(1, 1, self._size - 1, self._size - 1,
                         fill=self._fill, outline="", tags="bg")
        self.tag_lower("bg")
        self.coords("glyph", self._size / 2, self._size / 2)
        self.tag_raise("glyph")

    def apply_theme(self, theme) -> None:
        self._theme = theme
        self._sync_bg()
        self._redraw()

    def _sync_bg(self) -> None:
        self.configure(bg=parent_bg(self, self._theme))
