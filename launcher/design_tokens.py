"""Chris Design System - semantic tokens, typography, spacing, radius.

Pipeline: Component -> Semantic Token -> Theme (light / dark / system).

Light is the primary theme (warm, calm, premium personal-product feel).
Dark reuses the exact same token names with its own palette - never a
mechanical white<->black inversion.

Legacy color names (used by older widgets and tests) are aliased to their
semantic equivalents so the transition stays backward compatible.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- spacing
SPACING: dict[str, int] = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "2xl": 32,
    "3xl": 48,
}

# ----------------------------------------------------------------- radius
RADIUS: dict[str, int] = {
    "small": 6,
    "medium": 10,
    "large": 14,
    "pill": 999,
}

# ------------------------------------------------------------- typography
FONT_BASE = "Microsoft YaHei UI"
FONT_CODE = "Consolas"

TYPE_SCALE: dict[str, int] = {
    "display": 20,
    "title": 16,
    "heading": 13,
    "body": 10,
    "small": 9,
    "caption": 8,
    "code": 9,
}


def type_size(role: str, default: int = 10) -> int:
    return TYPE_SCALE.get(role, default)


# ------------------------------------------------------------- light theme
LIGHT: dict[str, Any] = {
    # base layers: warm light gray app background, near-white surfaces
    "background": "#F7F8FA",
    "surface": "#FFFFFF",
    "surface-elevated": "#FFFFFF",
    "surface-hover": "#EFF1F4",
    "surface-sunken": "#EEF0F3",
    # hairlines (used sparingly; hierarchy comes from bg + spacing)
    "border": "#E3E6EB",
    "border-soft": "#EAEDF1",
    "divider": "#ECEEF2",
    # text
    "text-primary": "#1B1E24",
    "text-secondary": "#5C6370",
    "text-muted": "#767C88",
    # accent: calm, low-saturation GPT-green family
    "accent": "#0F9D7C",
    "accent-hover": "#0B8A6C",
    "accent-soft": "#E3F4EF",
    "accent-fg": "#FFFFFF",
    # semantic states
    "success": "#128F6B",
    "warning": "#C98A1B",
    "error": "#D6484C",
    "error-soft": "#FBEAEA",
    # chat surfaces
    "user-bubble": "#ECEDF0",
    "user-fg": "#1B1E24",
    "chris-surface": "#FFFFFF",
    "chris-fg": "#1B1E24",
    "code-bg": "#F1F2F5",
    "code-fg": "#2A2E35",
    # status
    "online": "#128F6B",
    "offline": "#D6484C",
    "thinking": "#C98A1B",
    # misc
    "link": "#0B7A68",
    "placeholder": "#9299A4",
    "scrollbar": "#C9CED6",
    "avatar-chris": "#0F9D7C",
    "avatar-user": "#6E63D6",
    "focus-ring": "#0F9D7C",
}


# -------------------------------------------------------------- dark theme
DARK: dict[str, Any] = {
    # base layers: warm charcoal, slight indigo, never pure black
    "background": "#17181C",
    "surface": "#1F2126",
    "surface-elevated": "#262930",
    "surface-hover": "#2C2F37",
    "surface-sunken": "#1B1D21",
    "border": "#32363F",
    "border-soft": "#2A2D34",
    "divider": "#262930",
    # text
    "text-primary": "#E8E9EC",
    "text-secondary": "#A8ADB8",
    "text-muted": "#737A86",
    # accent
    "accent": "#12A683",
    "accent-hover": "#17B98C",
    "accent-soft": "#1E3A33",
    "accent-fg": "#FFFFFF",
    # semantic states
    "success": "#2FBF8F",
    "warning": "#E6A23C",
    "error": "#E5484D",
    "error-soft": "#3A2426",
    # chat surfaces
    "user-bubble": "#35363D",
    "user-fg": "#E8E9EC",
    "chris-surface": "#1F2126",
    "chris-fg": "#E8E9EC",
    "code-bg": "#141519",
    "code-fg": "#D7DBE0",
    # status
    "online": "#2FBF8F",
    "offline": "#E5484D",
    "thinking": "#E6A23C",
    # misc
    "link": "#3EC6A3",
    "placeholder": "#5E646F",
    "scrollbar": "#3E424B",
    "avatar-chris": "#12A683",
    "avatar-user": "#7C74E8",
    "focus-ring": "#12A683",
}


# ------------------------------------------------- legacy name aliases
LEGACY_ALIASES: dict[str, str] = {
    "bg": "background",
    "bg_header": "surface",
    "bg_sidebar": "surface",
    "bg_card": "surface-elevated",
    "bg_elevated": "surface-elevated",
    "bg_input": "surface",
    "bg_code": "code-bg",
    "fg": "text-primary",
    "fg_muted": "text-muted",
    "fg_bright": "text-primary",
    "accent_hover": "accent-hover",
    "accent_fg": "accent-fg",
    "user_bubble": "user-bubble",
    "user_fg": "user-fg",
    "chris_bubble": "chris-surface",
    "chris_fg": "chris-fg",
    "code_fg": "code-fg",
    "border_soft": "border-soft",
    "avatar_chris": "avatar-chris",
    "avatar_user": "avatar-user",
    "focus_ring": "focus-ring",
    "error_soft": "error-soft",
    "accent_soft": "accent-soft",
    "surface_hover": "surface-hover",
    "text_secondary": "text-secondary",
    "surface_elevated": "surface-elevated",
    "surface_sunken": "surface-sunken",
}


def _with_aliases(theme: dict[str, Any]) -> dict[str, Any]:
    merged = dict(theme)
    for legacy, semantic in LEGACY_ALIASES.items():
        if semantic in theme:
            merged[legacy] = theme[semantic]
    return merged


LIGHT_FULL = _with_aliases(LIGHT)
DARK_FULL = _with_aliases(DARK)
