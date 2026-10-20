"""CodingResult parsing: turn adapter output + git state into a structured result.

Never dump the full raw output to the LLM. Changed files come from read-only
`git status` / `git diff` (never reset/clean/checkout/push). Test status is a
best-effort heuristic over the output tail - if tests were not run, we say
NOT_RUN rather than guessing.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from chris.tools.coding.errors import CodingError, CodingErrorCode
from chris.tools.coding.models import CodingResult, CodingStatus

_TEST_PASS = re.compile(
    r"(test[s]?\s+(passed|ok|succeeded|complete[d]?)|all\s+test[s]?\s+passed|"
    r"(\d+)\s+passed|Ran\s+\d+\s+test)",
    re.IGNORECASE,
)
_TEST_FAIL = re.compile(
    r"(test[s]?\s+(failed|error)|fail(ures?|ed)\s*[:=]?\s*[1-9]|(\d+)\s+failed|"
    r"\bFAILED\b|\bFAIL\b)",
    re.IGNORECASE,
)
_TEST_SKIP = re.compile(r"(skipped|xfail)", re.IGNORECASE)


class CodingResultParser:
    def __init__(self, git: bool = True) -> None:
        self.git = git

    def build(
        self,
        project_root: str,
        result: CodingResult,
        *,
        raw_output: str = "",
    ) -> CodingResult:
        result.tests = self._detect_tests(raw_output or result.raw_tail)
        if self.git:
            result.changed_files = self._changed_files(project_root)
        return result

    # -- changed files (read-only git) ----------------------------------

    def _changed_files(self, project_root: str) -> list[str]:
        files: list[str] = []
        try:
            status = subprocess.run(
                ["git", "-C", project_root, "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=10.0,
            )
        except Exception:  # noqa: BLE001
            return files
        if status.returncode != 0:
            return files
        for line in status.stdout.splitlines():
            if len(line) > 3:
                files.append(line[3:].strip())
        return files[:100]

    # -- tests ----------------------------------------------------------

    def _detect_tests(self, output: str) -> str:
        if not output:
            return "NOT_RUN"
        if _TEST_FAIL.search(output):
            return "FAILED"
        if _TEST_PASS.search(output):
            return "PASS"
        if _TEST_SKIP.search(output):
            return "SKIPPED"
        return "NOT_RUN"
