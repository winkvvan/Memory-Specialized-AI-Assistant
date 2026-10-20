"""Lightweight markdown rendering for tkinter (no external deps).

Parses markdown into segments: text / fenced code / tables. Text segments
render into a tk.Text with inline tags (bold/italic/code/link/heading/list/
quote). Code blocks render as separate frames by the UI layer. Tables render
as a grid (headers + rows) by the UI layer.

Links are clickable: render_text records (start, end) -> url in the widget's
``_link_urls`` map and the UI layer binds the "link" tag.
"""

from __future__ import annotations

import re

FENCE = re.compile(r"^```([\w+.-]*)\s*$")
INLINE = re.compile(r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]]+\]\([^)\s]+\))")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")


def _cells(line: str) -> list[str]:
    parts = line.strip().strip("|").split("|")
    return [p.strip() for p in parts]


def parse(text: str) -> list[dict]:
    """Split markdown into [{'type': 'text'|'code'|'table', ...}] segments."""
    segments: list[dict] = []
    buf: list[str] = []
    lines = text.split("\n")
    i = 0

    def flush_text() -> None:
        if buf:
            segments.append({"type": "text", "content": "\n".join(buf), "lang": ""})
            buf.clear()

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        match = FENCE.match(line.strip())
        if match is not None:
            flush_text()
            code_lang = match.group(1)
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and FENCE.match(lines[i].strip()) is None:
                code_lines.append(lines[i].rstrip())
                i += 1
            if i < len(lines):
                i += 1  # closing fence
            segments.append({"type": "code", "content": "\n".join(code_lines), "lang": code_lang})
            continue

        # table: first line starts with | and the next line is a separator
        if TABLE_LINE.match(line) and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1].strip()):
            flush_text()
            headers = _cells(line)
            i += 2  # skip header + separator
            rows: list[list[str]] = []
            while i < len(lines) and TABLE_LINE.match(lines[i].strip()):
                rows.append(_cells(lines[i].strip()))
                i += 1
            segments.append({"type": "table", "headers": headers, "rows": rows, "lang": ""})
            continue

        buf.append(line)
        i += 1

    flush_text()
    return segments


def render_text(widget, content: str, tags: dict, link_map: dict | None = None) -> None:
    """Insert markdown text into a tk.Text using pre-configured tags.

    `tags` maps: h1,h2,list,quote,hr,code,bold,italic,link,normal. When
    ``link_map`` is given, clickable link ranges are recorded there as
    {(start_index, end_index): url}.
    """
    if link_map is not None:
        widget._link_urls = link_map  # noqa: SLF001
    for line in content.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("# ") or stripped.startswith("## ") or stripped.startswith("### "):
            level = min(len(stripped) - stripped.lstrip("#").count("#"), 3)
            body = stripped.lstrip("# ").strip()
            widget.insert("end", body + "\n", (f"h{level}",))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            widget.insert("end", "• " + stripped[2:].strip() + "\n", ("list",))
        elif stripped.startswith("> "):
            widget.insert("end", stripped[2:].strip() + "\n", ("quote",))
        elif stripped == "---":
            widget.insert("end", "─" * 48 + "\n", ("hr",))
        else:
            _render_inline(widget, line, tags, link_map)
            widget.insert("end", "\n")


def _render_inline(widget, text: str, tags: dict, link_map: dict | None) -> None:
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            widget.insert("end", part[1:-1], ("code",))
        elif part.startswith("**") and part.endswith("**") and len(part) > 3:
            widget.insert("end", part[2:-2], ("bold",))
        elif part.startswith("*") and part.endswith("*") and len(part) > 1:
            widget.insert("end", part[1:-1], ("italic",))
        else:
            link = LINK.match(part)
            if link:
                start = widget.index("end-1c")
                widget.insert("end", link.group(1), ("link",))
                end = widget.index("end-1c")
                if link_map is not None:
                    link_map[(str(start), str(end))] = link.group(2)
            else:
                widget.insert("end", part)
