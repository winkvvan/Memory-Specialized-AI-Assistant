"""Human-readable confirmation reason rendering.

Sensitive inputs (e.g. computer_type_text content) are never echoed verbatim
into the reason text; they are replaced by their length. This keeps the reason
usable for display while not leaking secrets into logs/events.
"""

from __future__ import annotations

from typing import Any

from chris.tools.confirmation.models import ConfirmationRequest

_SENSITIVE_ARG_KEYS = {"text", "password", "secret", "token", "api_key", "content"}


def render_reason(request: ConfirmationRequest) -> str:
    """Build a concise, human-readable reason for the user to read."""
    tool = request.tool_name
    args = request.arguments or {}
    safe_args = _sanitize(args)
    details = ", ".join(f"{k}={v}" for k, v in safe_args.items()) if safe_args else ""
    summary = f"{request.risk_level} action '{tool}'"
    if details:
        summary += f" with {details}"
    return (
        f"Chris requests permission to run {summary}. "
        f"Reason: {request.reason}"
    )


def _sanitize(arguments: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in arguments.items():
        if key in _SENSITIVE_ARG_KEYS and isinstance(value, str):
            out[key] = f"<{len(value)} chars>"
        elif isinstance(value, (str, int, float, bool)):
            out[key] = str(value)
        else:
            out[key] = repr(value)[:80]
    return out
