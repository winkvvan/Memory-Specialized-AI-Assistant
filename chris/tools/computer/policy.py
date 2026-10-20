"""Computer action policy: risk classification, coordinate/text/key safety,
and the confirmation seam for Phase 8.6.

Phase 8.5 default decisions:
  READ_ONLY (screen info / active window / screenshot) -> ALLOW
  LOW       (move_mouse / scroll / wait)              -> ALLOW
  MEDIUM    (click / double_click / key_press / hotkey / type_text) -> DENY
  HIGH / CRITICAL (sequences with side effects, future dangerous ops) -> DENY
Denied actions return confirmation_required because 8.6 is not implemented yet;
we do not fake a confirmation UI.
"""

from __future__ import annotations

from typing import Any, Iterable

from chris.tools.computer.errors import ComputerError, ComputerErrorCode
from chris.tools.models import RiskLevel

# action_type -> risk level
ACTION_RISK: dict[str, RiskLevel] = {
    "get_screen_size": RiskLevel.READ_ONLY,
    "get_active_window": RiskLevel.READ_ONLY,
    "screenshot": RiskLevel.READ_ONLY,
    "move_mouse": RiskLevel.LOW,
    "scroll": RiskLevel.LOW,
    "wait": RiskLevel.LOW,
    "click": RiskLevel.MEDIUM,
    "double_click": RiskLevel.MEDIUM,
    "key_press": RiskLevel.MEDIUM,
    "hotkey": RiskLevel.MEDIUM,
    "type_text": RiskLevel.MEDIUM,
    "sequence": RiskLevel.HIGH,
}

# Known high-risk hotkeys (stored sorted so comparison is order-independent):
# treated as requiring confirmation when detected.
HIGH_RISK_HOTKEYS = {
    ("alt", "ctrl", "delete"),
    ("r", "win"),
    ("alt", "f4"),
    ("ctrl", "esc", "shift"),
    ("l", "win"),
    ("win", "x"),
    ("e", "win"),
}

VALID_KEYS = {
    "enter", "space", "tab", "backspace", "delete", "esc", "escape",
    "shift", "ctrl", "control", "alt", "win", "super", "meta", "cmd",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "insert", "capslock", "numlock", "scrolllock", "printscreen", "pause",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m",
    "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z",
}


class ComputerActionPolicy:
    def __init__(
        self,
        allowlist_actions: Iterable[str] = (),
        default_allow: Iterable[RiskLevel] | None = None,
    ) -> None:
        self._allowlist = frozenset(allowlist_actions)
        # default allow: READ_ONLY + LOW
        self._default_allow = frozenset(
            default_allow
            if default_allow is not None
            else (RiskLevel.READ_ONLY, RiskLevel.LOW)
        )

    def risk_for(self, action_type: str) -> RiskLevel:
        return ACTION_RISK.get(action_type, RiskLevel.CRITICAL)

    def decision(self, action_type: str) -> tuple[bool, RiskLevel, bool]:
        """Returns (allowed, risk, confirmation_required)."""
        risk = self.risk_for(action_type)
        if action_type in self._allowlist:
            return True, risk, False
        if risk in self._default_allow:
            return True, risk, False
        return False, risk, True

    # -- validation helpers ---------------------------------------------

    def validate_coordinates(self, x: Any, y: Any, screen: tuple[int, int]) -> dict[str, Any]:
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ComputerError(
                ComputerErrorCode.INVALID_COORDINATES,
                "x and y must be numbers",
            )
        width, height = screen
        xi, yi = int(x), int(y)
        if xi < 0 or yi < 0 or xi >= width or yi >= height:
            raise ComputerError(
                ComputerErrorCode.INVALID_COORDINATES,
                f"coordinates ({xi},{yi}) are outside the screen {width}x{height}",
                detail={"x": xi, "y": yi, "width": width, "height": height},
            )
        return {"x": xi, "y": yi}

    def validate_text(self, text: Any, max_length: int) -> str:
        if not isinstance(text, str):
            raise ComputerError(
                ComputerErrorCode.INVALID_ARGUMENT, "text must be a string"
            )
        if len(text) > max_length:
            raise ComputerError(
                ComputerErrorCode.TEXT_TOO_LONG,
                f"text exceeds {max_length} character limit",
            )
        return text

    def validate_keys(self, keys: Any, max_keys: int = 8) -> list[str]:
        if not isinstance(keys, list) or not keys:
            raise ComputerError(
                ComputerErrorCode.INVALID_ARGUMENT, "keys must be a non-empty list"
            )
        if len(keys) > max_keys:
            raise ComputerError(
                ComputerErrorCode.TOO_MANY_KEYS,
                f"hotkey has more than {max_keys} keys",
            )
        normalized = [str(k).strip().casefold() for k in keys]
        for key in normalized:
            if key not in VALID_KEYS:
                raise ComputerError(
                    ComputerErrorCode.INVALID_KEY, f"invalid key: {key!r}"
                )
        return normalized

    def hotkey_risk_check(self, keys: list[str]) -> tuple[bool, str]:
        """Detect obviously high-risk hotkeys; returns (is_high_risk, name)."""
        normalized = tuple(sorted(keys))
        if normalized in HIGH_RISK_HOTKEYS:
            return True, "+".join(keys)
        return False, ""
