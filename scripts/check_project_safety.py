"""Memory Assistant Repository Safety Check.

Lightweight guardrail: verifies the project has the safety documents, core
directories, no obvious hardcoded API keys, and that business code respects the
LLMProvider isolation boundary. Prints a [OK]/[FAIL] report and exits non-zero
on failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = [
    "AGENTS.md",
    "ARCHITECTURE.md",
    "ROADMAP.md",
    "CHANGELOG.md",
    "DEVELOPMENT_CONTRACT.md",
    "docs/PROJECT_STATE.md",
    "docs/HANDOFF.md",
    "docs/architecture-changes/README.md",
    ".gitignore",
    "config.json",
    "pyproject.toml",
    "templates/personality.json",
    "templates/memory.json",
    "templates/memory.archive.json",
]

REQUIRED_DIRS = [
    "chris",
    "chris/llm",
    "chris/personality",
    "chris/memory",
    "chris/temporal",
    "chris/world",
    "chris/behavior",
    "tests",
]

# real-looking secrets (16+ chars); test placeholders like "sk-..." do not match
SECRET_RE = re.compile(r"\b(sk|ghp|gho|glpat|token|key)_[A-Za-z0-9]{16,}\b")
ASSIGN_RE = re.compile(
    r"(api[_-]?key|secret|password|token)\s*[=:]\s*['\"][A-Za-z0-9]{12,}['\"]",
    re.IGNORECASE,
)

# business code must not import these directly
FORBIDDEN_IMPORTS = ["import requests", "from requests", "import httpx", "from httpx"]

PROTECTED_MODULES = [
    "chris/personality",
    "chris/memory",
    "chris/temporal",
    "chris/world",
    "chris/behavior",
    "chris/llm",
    "chris/events.py",
]


def _iter_source_files() -> list[Path]:
    files = []
    for pattern in ("*.py", "*.md", "*.json", "*.toml", "*.ps1", "*.txt"):
        files.extend(ROOT.rglob(pattern))
    return [
        f
        for f in files
        if "node_modules" not in f.parts and ".git" not in f.parts and "build" not in f.parts
    ]


def main() -> int:
    print("Memory Assistant Safety Check")
    failures = 0

    def check(ok: bool, name: str) -> None:
        nonlocal failures
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {name}")
        if not ok:
            failures += 1

    for name in REQUIRED_FILES:
        check((ROOT / name).exists(), name)
    for name in REQUIRED_DIRS:
        check((ROOT / name).exists(), name + "/")

    # secret scan (tolerant: ignores short placeholders)
    leaks = []
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for match in SECRET_RE.findall(text):
            leaks.append(f"{path.relative_to(ROOT)}: sk-... secret-like token")
        for line_no, line in enumerate(text.splitlines(), 1):
            if ASSIGN_RE.search(line) and "test" not in path.as_posix():
                leaks.append(f"{path.relative_to(ROOT)}:{line_no} possible credential assignment")
    check(not leaks, "Secret scan")
    for leak in leaks[:10]:
        print(f"       leak: {leak}")

    # LLM provider isolation: business code must not import concrete providers/requests
    # HTTP adapter dirs (chris/llm/, chris/embedding/adapters/,
    # chris/tools/search/adapters/) are exempt like the boundary test.
    boundary_violations = []
    for path in sorted((ROOT / "chris").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if (
            rel.startswith("chris/llm/")
            or rel.startswith("chris/embedding/adapters/")
            or rel.startswith("chris/tools/search/adapters/")
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for forbidden in FORBIDDEN_IMPORTS:
            if forbidden in text:
                boundary_violations.append(f"{rel} imports {forbidden}")
    check(not boundary_violations, "LLM Provider isolation")
    for violation in boundary_violations:
        print(f"       violation: {violation}")

    # datetime.now() should not be scattered in protected modules (Temporal uses Clock)
    # Exceptions: chris/temporal/clock.py and chris/events.py ARE the time source.
    time_source_exceptions = {"chris/temporal/clock.py", "chris/events.py"}
    now_violations = []
    for mod in PROTECTED_MODULES:
        base = ROOT / mod
        if not base.exists():
            continue
        for path in (base.rglob("*.py") if base.is_dir() else [base]):
            rel = path.relative_to(ROOT).as_posix()
            if rel in time_source_exceptions:
                continue
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            if "datetime.now(" in text:
                now_violations.append(rel)
    check(not now_violations, "Temporal uses Clock (no scattered datetime.now)")

    result = "PASS" if failures == 0 else "FAIL"
    print(f"\nResult: {result}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
