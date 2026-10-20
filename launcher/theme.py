"""Theme system: light / dark / system, backed by the Design System tokens.

Light is the default and primary theme. Dark is a fully supported second
theme mapped from the same semantic token names. Components must only read
semantic tokens (via Theme.c) and never hard-code hex colours.
"""

from __future__ import annotations

from typing import Any

from launcher.design_tokens import (
    DARK_FULL,
    LIGHT_FULL,
    RADIUS,
    SPACING,
    TYPE_SCALE,
    FONT_BASE,
    FONT_CODE,
    type_size,
)

THEMES: dict[str, dict[str, Any]] = {"dark": DARK_FULL, "light": LIGHT_FULL}


def _system_dark() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
    except Exception:  # noqa: BLE001
        return True


def resolve_theme(name: str) -> dict[str, Any]:
    if name == "system":
        return DARK_FULL if _system_dark() else LIGHT_FULL
    return THEMES.get(name, LIGHT_FULL)


def _system_reduced_motion() -> bool:
    """Read the Windows 'show animations' setting (SPI_GETCLIENTAREAANIMATION)."""
    try:
        import ctypes

        value = ctypes.c_uint(0)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0)
        return bool(ok and value.value == 0)
    except Exception:  # noqa: BLE001
        return False


class Theme:
    """Active theme facade: token lookup + typography/spacing/radius helpers."""

    def __init__(self, name: str = "light") -> None:
        self.name = name
        self.colors = resolve_theme(name)

    def c(self, key: str, default: Any = "") -> Any:
        return self.colors.get(key, default)

    def switch(self, name: str) -> None:
        self.name = name
        self.colors = resolve_theme(name)

    @property
    def is_dark(self) -> bool:
        return self.name == "dark" or (
            self.name == "system" and self.colors.get("background", "").startswith("#1")
        )

    @property
    def motion_enabled(self) -> bool:
        return not _system_reduced_motion()

    def font(self, size: int = 10, bold: bool = False, mono: bool = False) -> tuple:
        family = FONT_CODE if mono else FONT_BASE
        return (family, size, "bold" if bold else "normal")

    def font_role(self, role: str = "body", bold: bool = False, mono: bool = False) -> tuple:
        return self.font(type_size(role, 10), bold=bold, mono=mono)

    def radius(self, role: str = "medium", default: int = 10) -> int:
        return RADIUS.get(role, default)

    def spacing(self, step: str = "md", default: int = 12) -> int:
        return SPACING.get(step, default)

    def type_scale(self) -> dict[str, int]:
        return dict(TYPE_SCALE)
