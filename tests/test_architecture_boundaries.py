"""Architecture boundary checks (guardrail).

These tests verify the protected boundaries that keep Chris model-independent
and maintainable, without calling any real API:
  - business code never imports concrete providers / requests / httpx
  - Memory / Personality / Temporal / World / Behavior do not depend on a model
  - Temporal does not scatter datetime.now() (goes through Clock)
  - sensitive data rules are in .gitignore
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_relative(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8-sig")


class TestLLMProviderIsolation(unittest.TestCase):
    def test_business_code_never_imports_concrete_providers_or_http(self):
        for path in (ROOT / "chris").rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("chris/llm/") or rel.startswith(
                "chris/embedding/adapters/"
            ) or rel.startswith("chris/tools/search/adapters/"):
                continue
            text = path.read_text(encoding="utf-8-sig")
            for forbidden in ("import requests", "from requests", "import httpx"):
                self.assertNotIn(forbidden, text, f"{rel} must not import {forbidden}")
            for concrete in (
                "DeepSeekProvider",
                "QwenProvider",
                "MoonshotProvider",
                "GLMProvider",
                "OpenAICompatProvider",
            ):
                self.assertNotIn(concrete, text, f"{rel} must not import {concrete}")


class TestStateModulesModelIndependent(unittest.TestCase):
    def test_core_modules_do_not_mention_any_vendor(self):
        vendors = ("deepseek", "qwen", "openai", "moonshot", "glm", "ollama")
        for module in (
            "chris/personality",
            "chris/memory",
            "chris/temporal",
            "chris/world",
            "chris/behavior",
        ):
            for path in (ROOT / module).rglob("*.py"):
                text = path.read_text(encoding="utf-8-sig")
                for vendor in vendors:
                    self.assertNotIn(
                        vendor,
                        text.casefold(),
                        f"{module} must not depend on {vendor}",
                    )

    def test_events_not_model_bound(self):
        text = _read_relative("chris/events.py")
        for vendor in ("deepseek", "qwen", "openai", "moonshot", "glm"):
            self.assertNotIn(vendor, text.casefold())


class TestTemporalUsesClock(unittest.TestCase):
    def test_no_scattered_datetime_now_in_protected_modules(self):
        exceptions = {"chris/temporal/clock.py", "chris/events.py"}
        for module in (
            "chris/personality",
            "chris/memory",
            "chris/temporal",
            "chris/world",
            "chris/behavior",
        ):
            for path in (ROOT / module).rglob("*.py"):
                rel = path.relative_to(ROOT).as_posix()
                if rel in exceptions:
                    continue
                text = path.read_text(encoding="utf-8-sig")
                self.assertNotIn(
                    "datetime.now(",
                    text,
                    f"{rel} should go through Clock/utcnow, not datetime.now()",
                )


class TestGitignoreSensitiveData(unittest.TestCase):
    def test_sensitive_patterns_ignored(self):
        text = _read_relative(".gitignore")
        for pattern in ("data/", ".env", "*.key", "*.secret", "secrets/"):
            self.assertIn(pattern, text, f".gitignore must ignore {pattern}")

    def test_runtime_data_not_tracked(self):
        from subprocess import run

        result = run(
            ["git", "-C", str(ROOT), "ls-files", "data/"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "", "runtime data must not be tracked")


class TestGuardrailDocsExist(unittest.TestCase):
    def test_docs_present(self):
        for rel in (
            "AGENTS.md",
            "ARCHITECTURE.md",
            "ROADMAP.md",
            "CHANGELOG.md",
            "DEVELOPMENT_CONTRACT.md",
            "docs/PROJECT_STATE.md",
            "docs/HANDOFF.md",
            "docs/architecture-changes/README.md",
            "docs/architecture-changes/ACR-000-template.md",
            "scripts/check_project_safety.py",
        ):
            self.assertTrue((ROOT / rel).exists(), f"missing {rel}")


if __name__ == "__main__":
    unittest.main()
