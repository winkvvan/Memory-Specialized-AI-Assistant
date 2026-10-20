"""Chris desktop app entry point.

Shows the UI first, then initialises the Core runtime asynchronously, so the
window never blocks on model/API loading. The UI is only a presentation layer:
all state flows through launcher.chat / chris modules.
"""

from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path

import tkinter as tk

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# stdlib modules required by the (unbundled) chris package at runtime.
# Keep this list in sync with the stdlib imports used across chris/ (phases
# 8.x added os/shutil/subprocess/tempfile/fnmatch/hashlib/html/urllib/logging;
# html.parser is a separate submodule used by the duckduckgo search adapter).
import abc  # noqa: F401
import argparse  # noqa: F401
import collections  # noqa: F401
import dataclasses  # noqa: F401
import datetime  # noqa: F401
import enum  # noqa: F401
import fnmatch  # noqa: F401
import hashlib  # noqa: F401
import html  # noqa: F401
import html.parser  # noqa: F401
import logging  # noqa: F401
import math  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import struct  # noqa: F401
import subprocess  # noqa: F401
import tempfile  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import typing  # noqa: F401
import urllib.error  # noqa: F401
import urllib.parse  # noqa: F401
import urllib.request  # noqa: F401
import uuid  # noqa: F401
import wave  # noqa: F401
import winsound  # noqa: F401
import zoneinfo  # noqa: F401

from launcher.chat import build_chat_pipeline, chat_turn, stream_turn  # noqa: F401
from launcher.ui.app_window import MainWindow


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def _load_theme(base_dir: Path) -> str:
    """Read the persisted theme choice (default: light)."""
    try:
        path = base_dir / "data" / "ui.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            name = data.get("theme")
            if name in ("light", "dark", "system"):
                return name
    except Exception:  # noqa: BLE001
        pass
    return "light"


def main() -> None:
    _enable_dpi_awareness()
    root = tk.Tk()
    MainWindow(root, BASE_DIR, theme_name=_load_theme(BASE_DIR))
    root.mainloop()


if __name__ == "__main__":
    main()
