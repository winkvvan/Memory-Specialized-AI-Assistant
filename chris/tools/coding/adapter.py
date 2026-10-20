"""Controlled process adapter for the OpenCode CLI.

Runs OpenCode as an external process with:
  - argument arrays (never shell=True / never string-joined user input),
  - a fixed working directory inside the project workspace,
  - a sanitized environment (see CodingExecutionPolicy),
  - explicit start / stream / wait / cancel lifecycle.

The adapter is the ONLY place that touches subprocess for coding tasks. The
CodingTaskTool / Manager never construct shell commands directly.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Any

from chris.tools.coding.errors import CodingError, CodingErrorCode
from chris.tools.coding.models import CodingResult, CodingStatus
from chris.tools.coding.policy import CodingExecutionPolicy

_TAIL_LINES = 40
_TAIL_CHARS = 6000


class OpenCodeAdapter:
    """Starts OpenCode, captures output, supports cancel + timeout."""

    name = "opencode"

    def __init__(
        self,
        command: str = "opencode",
        policy: CodingExecutionPolicy | None = None,
    ) -> None:
        self.command = command
        self.policy = policy or CodingExecutionPolicy()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._cancel = False

    # -- detection ------------------------------------------------------

    def is_available(self) -> bool:
        return shutil.which(self.command) is not None

    def version(self) -> str:
        if not self.is_available():
            raise CodingError(
                CodingErrorCode.OPENCODE_NOT_INSTALLED,
                "opencode is not installed",
            )
        try:
            result = subprocess.run(
                [self.command, "--version"],
                capture_output=True,
                text=True,
                timeout=15.0,
            )
            return (result.stdout or result.stderr or "").strip() or "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    # -- lifecycle ------------------------------------------------------

    def start(
        self,
        project_root: str,
        prompt: str,
        *,
        extra_args: list[str] | None = None,
    ) -> None:
        if not self.is_available():
            raise CodingError(
                CodingErrorCode.OPENCODE_NOT_INSTALLED,
                "opencode is not installed; coding task cannot start",
            )
        env = self.policy.sanitized_environment()
        args = [self.command]
        if extra_args:
            args.extend(extra_args)
        args.append(prompt)
        self._cancel = False
        try:
            self._proc = subprocess.Popen(
                args,
                cwd=project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as exc:
            raise CodingError(
                CodingErrorCode.EXECUTION_ERROR,
                f"failed to launch opencode: {exc}",
                detail=str(exc),
            ) from exc

    def stream_lines(self) -> list[str]:
        """Read available stdout/stderr lines without blocking forever."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return []
        lines: list[str] = []
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
        return lines

    def wait(
        self, timeout: float | None = None, poll: float = 0.2
    ) -> CodingResult:
        """Wait for completion with a deadline. On timeout, terminate the process."""
        proc = self._proc
        if proc is None:
            return CodingResult(status=CodingStatus.FAILED, error="not started")

        started = time.monotonic()
        deadline = time.monotonic() + (timeout if timeout is not None else float("inf"))
        while proc.poll() is None:
            if self._cancel:
                self._terminate()
                proc.wait(timeout=5)
                return CodingResult(
                    status=CodingStatus.CANCELLED,
                    summary="coding task cancelled",
                    exit_code=proc.returncode,
                )
            if time.monotonic() >= deadline:
                self._terminate()
                proc.wait(timeout=5)
                return CodingResult(
                    status=CodingStatus.TIMEOUT,
                    summary=f"coding task timed out after {timeout}s",
                    error=f"timed out after {timeout}s",
                    exit_code=proc.returncode,
                )
            time.sleep(poll)

        output = self._read_all_output()
        duration = time.monotonic() - started
        status = (
            CodingStatus.COMPLETED if proc.returncode == 0 else CodingStatus.FAILED
        )
        return CodingResult(
            status=status,
            summary=_tail(output),
            exit_code=proc.returncode,
            duration_seconds=duration,
            error=None if proc.returncode == 0 else f"exit code {proc.returncode}",
            raw_tail=_tail(output),
        )

    def cancel(self) -> None:
        self._cancel = True

    # -- internals ------------------------------------------------------

    def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        with self._lock:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass

    def _read_all_output(self) -> str:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return ""
        try:
            return proc.stdout.read() or ""
        except OSError:
            return ""


def _tail(output: str) -> str:
    lines = output.splitlines()
    if len(lines) > _TAIL_LINES:
        lines = lines[-_TAIL_LINES:]
    text = "\n".join(lines)
    if len(text) > _TAIL_CHARS:
        text = text[-_TAIL_CHARS:]
    return text
