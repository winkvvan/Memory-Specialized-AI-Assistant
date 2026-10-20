"""Coding execution policy: what a coding task is allowed to do.

The Coding Agent is granted project-scoped execution (tests/build/lint/git) but
NOT arbitrary system access. This policy classifies the task scope, maps it to
a risk level, and - because Phase 8.6 confirmation is not built yet - denies
high-risk scopes with ``confirmation_required`` rather than pretending to have
a confirmation UI.

Environment sanitization lives here too: the coding subprocess only gets a
small allow-listed set of variables; sensitive keys (API keys, tokens,
passwords, private keys) are never passed through.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable

from chris.tools.models import RiskLevel

# Execution scopes a Coding Task can request.
READ_ONLY = "READ_ONLY"
PROJECT_BUILD = "PROJECT_BUILD"
PROJECT_TEST = "PROJECT_TEST"
PROJECT_LINT = "PROJECT_LINT"
PROJECT_PACKAGE = "PROJECT_PACKAGE"
GIT_READ = "GIT_READ"
GIT_WRITE = "GIT_WRITE"
SYSTEM_COMMAND = "SYSTEM_COMMAND"

SCOPE_TO_RISK: dict[str, RiskLevel] = {
    READ_ONLY: RiskLevel.READ_ONLY,
    PROJECT_BUILD: RiskLevel.MEDIUM,
    PROJECT_TEST: RiskLevel.MEDIUM,
    PROJECT_LINT: RiskLevel.MEDIUM,
    PROJECT_PACKAGE: RiskLevel.HIGH,
    GIT_READ: RiskLevel.READ_ONLY,
    GIT_WRITE: RiskLevel.HIGH,
    SYSTEM_COMMAND: RiskLevel.CRITICAL,
}

# Scopes that are never auto-allowed until a real confirmation flow exists.
DEFAULT_DENIED_SCOPES = frozenset({PROJECT_PACKAGE, GIT_WRITE, SYSTEM_COMMAND})

# Minimal env allow-list for the coding subprocess (Windows-friendly names).
DEFAULT_ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "TEMP",
        "TMP",
        "SystemRoot",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LC_ALL",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "USERNAME",
        "USERDOMAIN",
        "APPDATA",
        "LOCALAPPDATA",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramData",
    }
)

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|secret|private[_-]?key|credential)", re.IGNORECASE
)


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    scope: str
    risk: RiskLevel
    reason: str = ""
    confirmation_required: bool = False


class CodingExecutionPolicy:
    def __init__(
        self,
        allowed_scopes: Iterable[str] | None = None,
        env_allowlist: Iterable[str] | None = None,
    ) -> None:
        self._allowed = set(allowed_scopes) if allowed_scopes is not None else None
        self.env_allowlist = frozenset(
            env_allowlist if env_allowlist is not None else DEFAULT_ENV_ALLOWLIST
        )

    def check_scope(self, scope: str) -> ScopeDecision:
        if scope not in SCOPE_TO_RISK:
            # Unknown scopes are never implicitly allowed and never confirmable.
            return ScopeDecision(
                False,
                scope,
                RiskLevel.CRITICAL,
                f"unknown execution scope {scope!r}",
                confirmation_required=False,
            )
        risk = SCOPE_TO_RISK[scope]
        if self._allowed is not None:
            if scope in self._allowed:
                return ScopeDecision(True, scope, risk, "scope allowed")
            return ScopeDecision(False, scope, risk, "scope not allowed", True)
        if scope in DEFAULT_DENIED_SCOPES:
            # CRITICAL (SYSTEM_COMMAND) is never confirmable; HIGH scopes
            # (PROJECT_PACKAGE / GIT_WRITE) require confirmation.
            confirmable = risk is not RiskLevel.CRITICAL
            return ScopeDecision(
                False,
                scope,
                risk,
                f"scope {scope} requires confirmation (Phase 8.6 not implemented)"
                if confirmable
                else f"scope {scope} is not allowed (CRITICAL)",
                confirmation_required=confirmable,
            )
        return ScopeDecision(True, scope, risk, "scope allowed by default")

    # -- environment sanitization ---------------------------------------

    def sanitized_environment(self) -> dict[str, str]:
        """Build the subprocess environment from an allow-list only.

        Explicitly drops API keys / tokens / passwords / private keys even if
        they are present in os.environ (e.g. ZHIPU_API_KEY, DEEPSEEK_API_KEY).
        """
        env: dict[str, str] = {}
        for key, value in os.environ.items():
            if key in self.env_allowlist and not SENSITIVE_KEY_RE.search(key):
                env[key] = value
        return env

    @staticmethod
    def sensitive_keys_present() -> list[str]:
        return [k for k in os.environ if SENSITIVE_KEY_RE.search(k)]
