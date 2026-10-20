"""Phase 8.3 File / Local Tools tests (offline, no network).

Covers: path security (traversal / absolute / drive / UNC / symlink escape /
prefix collision), list_directory, find_files (recursive/glob/limits/cancel),
read_file (UTF-8/BOM/binary/large/missing), write_file (create/overwrite/
denied/size limit/atomic/invalid path), create_directory, delete (permission
denial / confirmation_required / missing), metadata, permission model, Tool Core
integration (Registry -> Executor -> Permission -> Tool -> ToolResult), EventBus,
Temporal, config persistence.
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from chris.config import Config
from chris.events import EventBus, EventType
from chris.tools import ToolCall, ToolContext, ToolResult, ToolStatus
from chris.tools.executor import ToolExecutor
from chris.tools.file_tools import build_file_tools
from chris.tools.filesystem import (
    FileToolError,
    FileToolErrorCode,
    FilesystemLimits,
    WorkspacePathPolicy,
)
from chris.tools.filesystem.ops import Filesystem
from chris.tools.permission import DefaultPermissionPolicy

MAX = FilesystemLimits(
    max_read_bytes=100,
    max_write_bytes=100,
    max_results=50,
    max_directory_entries=100,
    max_search_depth=10,
)


def _context(**kwargs):
    return ToolContext(now=__import__("datetime").datetime(2026, 8, 12, 10, 0), **kwargs)


def _allow_all_policy():
    return DefaultPermissionPolicy(
        allowed_risks=list(__import__("chris.tools", fromlist=["RiskLevel"]).RiskLevel)
    )


class _Harness:
    """Temp workspace + registry + executor for integration tests."""

    def __init__(self, workspace=None, limits=None, reject_sensitive=False):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(workspace) if workspace else Path(self._tmp.name) / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.policy = WorkspacePathPolicy(self.workspace)
        self.limits = limits or MAX
        self.fs = Filesystem(
            self.policy, limits=self.limits, reject_sensitive=reject_sensitive
        )
        self.tools = build_file_tools(
            self.policy, limits=self.limits, reject_sensitive=reject_sensitive
        )
        from chris.tools.registry import ToolRegistry

        self.registry = ToolRegistry()
        for tool in self.tools:
            self.registry.register(tool)
        self.bus = EventBus()
        self.executor = ToolExecutor(
            self.registry, bus=self.bus, policy=_allow_all_policy()
        )
        self.executor_default_policy = ToolExecutor(
            self.registry, bus=EventBus(), policy=DefaultPermissionPolicy()
        )

    def call(self, name, args, executor=None):
        return (executor or self.executor).execute(
            ToolCall.new(name, args), _context()
        )

    def write(self, rel, content="hello"):
        path = self.workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def cleanup(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()
        return False


# ---------------------------------------------------------------------------
# Path security
# ---------------------------------------------------------------------------


class TestPathSecurity(unittest.TestCase):
    def _policy(self, workspace):
        return WorkspacePathPolicy(workspace)

    def test_normal_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            policy = self._policy(ws)
            self.assertEqual(policy.resolve("foo/bar"), ws / "foo" / "bar")

    def test_nested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            policy = self._policy(ws)
            resolved = policy.resolve("a/b/c.txt")
            self.assertEqual(resolved, ws / "a" / "b" / "c.txt")

    def test_dotdot_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            policy = self._policy(ws)
            for evil in ("../secret.txt", "foo/../../secret.txt", "..\\secret.txt"):
                with self.assertRaises(FileToolError) as ctx:
                    policy.resolve(evil)
                self.assertEqual(ctx.exception.code, FileToolErrorCode.PATH_OUTSIDE_WORKSPACE)

    def test_absolute_outside_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            outside = Path(tmp) / "secret.txt"
            outside.write_text("secret")
            policy = self._policy(ws)
            with self.assertRaises(FileToolError) as ctx:
                policy.resolve(str(outside))
            self.assertEqual(ctx.exception.code, FileToolErrorCode.PATH_OUTSIDE_WORKSPACE)

    def test_drive_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            policy = self._policy(ws)
            for evil in ("C:\\Windows\\system32", "D:\\secret", "C:/Windows"):
                with self.assertRaises(FileToolError):
                    policy.resolve(evil)

    def test_unc_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            policy = self._policy(ws)
            with self.assertRaises(FileToolError):
                policy.resolve("\\\\server\\share\\file.txt")

    def test_empty_and_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            policy = self._policy(ws)
            for evil in ("", "   ", None):
                with self.assertRaises(FileToolError):
                    policy.resolve(evil)

    def test_workspace_prefix_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            sibling = Path(tmp) / "ws2"
            sibling.mkdir()
            policy = self._policy(ws)
            # "ws2/..." is NOT inside "ws" even though it shares the prefix.
            with self.assertRaises(FileToolError):
                policy.resolve(str(sibling / "x"))

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("secret")
            try:
                link = ws / "escape.txt"
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks not available on this system")
            policy = self._policy(ws)
            with self.assertRaises(FileToolError) as ctx:
                policy.resolve("escape.txt")
            self.assertEqual(ctx.exception.code, FileToolErrorCode.PATH_OUTSIDE_WORKSPACE)

    def test_within_workspace_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            policy = self._policy(ws)
            resolved = policy.resolve("a.txt")
            self.assertTrue(policy.is_within(resolved))


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


class TestListDirectory(unittest.TestCase):
    def test_empty_directory(self):
        with _Harness() as h:
            result = h.call("list_directory", {"path": "."})
            self.assertTrue(result.ok)
            self.assertEqual(result.metadata["count"], 0)

    def test_files_and_directories(self):
        with _Harness() as h:
            h.write("a.txt", "a")
            h.write("sub/b.txt", "b")
            result = h.call("list_directory", {"path": "."})
            self.assertTrue(result.ok)
            names = {e["name"] for e in result.metadata["entries"]}
            self.assertIn("a.txt", names)
            self.assertIn("sub", names)
            entry = next(e for e in result.metadata["entries"] if e["name"] == "sub")
            self.assertEqual(entry["type"], "directory")

    def test_deterministic_ordering(self):
        with _Harness() as h:
            for name in ("z.txt", "a.txt", "m.txt"):
                h.write(name)
            first = [e["name"] for e in h.call("list_directory", {"path": "."}).metadata["entries"]]
            second = [e["name"] for e in h.call("list_directory", {"path": "."}).metadata["entries"]]
            self.assertEqual(first, sorted(first, key=str.casefold))
            self.assertEqual(first, second)

    def test_max_entries(self):
        with _Harness(limits=FilesystemLimits(max_directory_entries=3)) as h:
            for i in range(10):
                h.write(f"f{i}.txt")
            result = h.call("list_directory", {"path": "."})
            self.assertTrue(result.ok)
            self.assertEqual(result.metadata["count"], 3)
            self.assertTrue(result.metadata["truncated"])

    def test_missing_directory(self):
        with _Harness() as h:
            result = h.call("list_directory", {"path": "nope"})
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertEqual(result.metadata["error_code"], "directory_not_found")


# ---------------------------------------------------------------------------
# find_files
# ---------------------------------------------------------------------------


class TestFindFiles(unittest.TestCase):
    def test_glob_recursive(self):
        with _Harness() as h:
            h.write("a.py")
            h.write("sub/b.py")
            h.write("sub/c.txt")
            result = h.call("find_files", {"path": ".", "pattern": "*.py", "recursive": True})
            self.assertTrue(result.ok)
            self.assertEqual(result.metadata["count"], 2)

    def test_non_recursive(self):
        with _Harness() as h:
            h.write("a.py")
            h.write("sub/b.py")
            result = h.call("find_files", {"path": ".", "pattern": "*.py", "recursive": False})
            self.assertTrue(result.ok)
            self.assertEqual(result.metadata["count"], 1)

    def test_max_results(self):
        with _Harness() as h:
            for i in range(10):
                h.write(f"f{i}.txt")
            result = h.call("find_files", {"path": ".", "pattern": "*.txt", "max_results": 3})
            self.assertTrue(result.ok)
            self.assertEqual(result.metadata["count"], 3)
            self.assertTrue(result.metadata["truncated"])

    def test_max_depth(self):
        with _Harness() as h:
            h.write("a.py")
            h.write("d1/b.py")
            h.write("d1/d2/c.py")
            # max_depth=0: only files directly in the root
            result = h.call("find_files", {"path": ".", "pattern": "*.py", "max_depth": 0})
            self.assertEqual(result.metadata["count"], 1)
            # max_depth=1: root + one level down (a.py and d1/b.py)
            result = h.call("find_files", {"path": ".", "pattern": "*.py", "max_depth": 1})
            self.assertEqual(result.metadata["count"], 2)

    def test_cancellation(self):
        with _Harness() as h:
            for i in range(50):
                h.write(f"d{i}/f.txt")
            cancel = threading.Event()
            cancel.set()
            fs = Filesystem(h.policy, limits=h.limits, cancel_event=cancel)
            with self.assertRaises(FileToolError) as ctx:
                fs.find_files(".", "*.txt", recursive=True)
            self.assertEqual(ctx.exception.code, FileToolErrorCode.OPERATION_CANCELLED)

    def test_nonexistent_directory(self):
        with _Harness() as h:
            result = h.call("find_files", {"path": "nope", "pattern": "*"})
            self.assertEqual(result.metadata["error_code"], "directory_not_found")


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


class TestReadFile(unittest.TestCase):
    def test_utf8_normal(self):
        with _Harness() as h:
            h.write("a.txt", "hello 世界")
            result = h.call("read_file", {"path": "a.txt"})
            self.assertTrue(result.ok)
            self.assertIn("hello 世界", result.content)
            self.assertFalse(result.metadata["truncated"])

    def test_utf8_bom(self):
        with _Harness() as h:
            path = h.write("bom.txt", "content")
            path.write_bytes(b"\xef\xbb\xbfcontent")
            result = h.call("read_file", {"path": "bom.txt"})
            self.assertTrue(result.ok)
            self.assertIn("content", result.content)

    def test_large_file_marks_truncated(self):
        with _Harness(limits=FilesystemLimits(max_read_bytes=10)) as h:
            h.write("big.txt", "x" * 100)
            result = h.call("read_file", {"path": "big.txt"})
            self.assertTrue(result.ok)
            self.assertTrue(result.metadata["truncated"])
            self.assertIn("truncated", result.content)

    def test_binary_file_rejected(self):
        with _Harness() as h:
            path = h.write("bin.png", "x")
            path.write_bytes(b"\x89PNG\x00\x01\x02 binary data")
            result = h.call("read_file", {"path": "bin.png"})
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertEqual(result.metadata["error_code"], "binary_file_not_supported")

    def test_missing_file(self):
        with _Harness() as h:
            result = h.call("read_file", {"path": "nope.txt"})
            self.assertEqual(result.metadata["error_code"], "file_not_found")

    def test_invalid_encoding(self):
        with _Harness() as h:
            path = h.write("enc.bin", "x")
            # invalid UTF-8 WITHOUT NUL bytes (so binary detection does not fire first)
            path.write_bytes(b"\xff\xfe bad latin1-ish")
            result = h.call("read_file", {"path": "enc.bin", "encoding": "utf-8"})
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertEqual(result.metadata["error_code"], "invalid_encoding")


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


class TestWriteFile(unittest.TestCase):
    def test_create(self):
        with _Harness() as h:
            result = h.call("write_file", {"path": "new.txt", "content": "hi"})
            self.assertTrue(result.ok)
            self.assertEqual((h.workspace / "new.txt").read_text(), "hi")

    def test_overwrite_allowed(self):
        with _Harness() as h:
            h.write("a.txt", "old")
            result = h.call("write_file", {"path": "a.txt", "content": "new", "overwrite": True})
            self.assertTrue(result.ok)
            self.assertEqual((h.workspace / "a.txt").read_text(), "new")

    def test_overwrite_denied(self):
        with _Harness() as h:
            h.write("a.txt", "old")
            result = h.call("write_file", {"path": "a.txt", "content": "new"})
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertEqual(result.metadata["error_code"], "overwrite_not_allowed")
            self.assertEqual((h.workspace / "a.txt").read_text(), "old")

    def test_creates_parent_directories(self):
        with _Harness() as h:
            result = h.call("write_file", {"path": "a/b/c.txt", "content": "x"})
            self.assertTrue(result.ok)
            self.assertTrue((h.workspace / "a" / "b" / "c.txt").exists())

    def test_size_limit(self):
        with _Harness(limits=FilesystemLimits(max_write_bytes=10)) as h:
            result = h.call("write_file", {"path": "big.txt", "content": "x" * 50})
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertEqual(result.metadata["error_code"], "file_too_large")
            self.assertFalse((h.workspace / "big.txt").exists())

    def test_atomic_write_no_leftover_temp(self):
        with _Harness() as h:
            h.call("write_file", {"path": "a.txt", "content": "data"})
            temps = [p for p in h.workspace.rglob(".chris-write-*")]
            self.assertEqual(temps, [])

    def test_invalid_path_rejected(self):
        with _Harness() as h:
            result = h.call("write_file", {"path": "../outside.txt", "content": "x"})
            self.assertEqual(result.metadata["error_code"], "path_outside_workspace")

    def test_write_sensitive_file_when_policy_on(self):
        with _Harness(reject_sensitive=True) as h:
            result = h.call("write_file", {"path": ".env", "content": "x"})
            self.assertEqual(result.metadata["error_code"], "sensitive_file")
        # policy off (default): .env is writable inside the workspace
        with _Harness() as h:
            result = h.call("write_file", {"path": ".env", "content": "x"})
            self.assertTrue(result.ok)


# ---------------------------------------------------------------------------
# create_directory
# ---------------------------------------------------------------------------


class TestCreateDirectory(unittest.TestCase):
    def test_normal(self):
        with _Harness() as h:
            result = h.call("create_directory", {"path": "newdir"})
            self.assertTrue(result.ok)
            self.assertTrue((h.workspace / "newdir").is_dir())

    def test_nested(self):
        with _Harness() as h:
            result = h.call("create_directory", {"path": "a/b/c"})
            self.assertTrue(result.ok)
            self.assertTrue((h.workspace / "a" / "b" / "c").is_dir())

    def test_existing_ok(self):
        with _Harness() as h:
            h.write("d/keep.txt")
            result = h.call("create_directory", {"path": "d"})
            self.assertTrue(result.ok)
            self.assertFalse(result.metadata["created"])

    def test_outside_workspace_rejected(self):
        with _Harness() as h:
            result = h.call("create_directory", {"path": "../evil"})
            self.assertEqual(result.metadata["error_code"], "path_outside_workspace")


# ---------------------------------------------------------------------------
# delete / move / copy / info
# ---------------------------------------------------------------------------


class TestDeleteFile(unittest.TestCase):
    def test_delete_default_denied(self):
        with _Harness() as h:
            h.write("a.txt", "x")
            result = h.call("delete_file", {"path": "a.txt"}, executor=h.executor_default_policy)
            self.assertEqual(result.status, ToolStatus.DENIED)
            self.assertEqual(result.metadata["error_code"], "confirmation_required")
            self.assertTrue((h.workspace / "a.txt").exists())

    def test_delete_allowlisted_works(self):
        with _Harness() as h:
            h.write("a.txt", "x")
            executor = ToolExecutor(
                h.registry,
                bus=EventBus(),
                policy=DefaultPermissionPolicy(
                    allowed_risks=[
                        __import__("chris.tools", fromlist=["RiskLevel"]).RiskLevel.READ_ONLY,
                        __import__("chris.tools", fromlist=["RiskLevel"]).RiskLevel.LOW,
                        __import__("chris.tools", fromlist=["RiskLevel"]).RiskLevel.MEDIUM,
                        __import__("chris.tools", fromlist=["RiskLevel"]).RiskLevel.HIGH,
                    ],
                    allowlist_names=["delete_file"],
                ),
            )
            result = h.call("delete_file", {"path": "a.txt"}, executor=executor)
            self.assertTrue(result.ok)
            self.assertFalse((h.workspace / "a.txt").exists())

    def test_delete_missing_file(self):
        with _Harness() as h:
            result = h.call("delete_file", {"path": "nope.txt"}, executor=h.executor_default_policy)
            self.assertEqual(result.status, ToolStatus.DENIED)

    def test_delete_directory_refused(self):
        with _Harness() as h:
            h.write("d/x.txt")
            executor = ToolExecutor(
                h.registry, bus=EventBus(), policy=_allow_all_policy()
            )
            result = h.call("delete_file", {"path": "d"}, executor=executor)
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertEqual(result.metadata["error_code"], "is_a_directory")


class TestMoveCopy(unittest.TestCase):
    def test_move(self):
        with _Harness() as h:
            h.write("a.txt", "x")
            result = h.call("move_file", {"source": "a.txt", "destination": "sub/b.txt"})
            self.assertTrue(result.ok)
            self.assertFalse((h.workspace / "a.txt").exists())
            self.assertTrue((h.workspace / "sub" / "b.txt").exists())

    def test_move_outside_rejected(self):
        with _Harness() as h:
            h.write("a.txt", "x")
            result = h.call("move_file", {"source": "a.txt", "destination": "../x.txt"})
            self.assertEqual(result.metadata["error_code"], "path_outside_workspace")
            self.assertTrue((h.workspace / "a.txt").exists())

    def test_copy(self):
        with _Harness() as h:
            h.write("a.txt", "data")
            result = h.call("copy_file", {"source": "a.txt", "destination": "b.txt"})
            self.assertTrue(result.ok)
            self.assertTrue((h.workspace / "a.txt").exists())
            self.assertEqual((h.workspace / "b.txt").read_text(), "data")

    def test_copy_source_outside_rejected(self):
        with _Harness() as h:
            result = h.call("copy_file", {"source": "../secret.txt", "destination": "b.txt"})
            self.assertEqual(result.metadata["error_code"], "path_outside_workspace")


class TestFileInfo(unittest.TestCase):
    def test_file_info(self):
        with _Harness() as h:
            h.write("a.txt", "hello")
            result = h.call("file_info", {"path": "a.txt"})
            self.assertTrue(result.ok)
            data = result.metadata
            self.assertEqual(data["type"], "file")
            self.assertEqual(data["size"], 5)
            self.assertEqual(data["extension"], "txt")

    def test_file_info_missing(self):
        with _Harness() as h:
            result = h.call("file_info", {"path": "nope"})
            self.assertEqual(result.metadata["error_code"], "file_not_found")


# ---------------------------------------------------------------------------
# Permission model
# ---------------------------------------------------------------------------


class TestPermissionModel(unittest.TestCase):
    def _defs(self, h):
        return {t.name: t.definition() for t in h.tools}

    def test_readonly_allowed(self):
        with _Harness() as h:
            policy = DefaultPermissionPolicy()
            defs = self._defs(h)
            for name in ("file_info", "list_directory", "find_files", "read_file"):
                self.assertTrue(policy.check(defs[name], _context()).allowed, name)

    def test_medium_allowed_by_extended_policy_only(self):
        with _Harness() as h:
            default = DefaultPermissionPolicy()
            defs = self._defs(h)
            self.assertFalse(default.check(defs["write_file"], _context()).allowed)

    def test_high_denied_by_default(self):
        with _Harness() as h:
            default = DefaultPermissionPolicy()
            defs = self._defs(h)
            for name in ("delete_file", "move_file"):
                decision = default.check(defs[name], _context())
                self.assertFalse(decision.allowed, name)
                self.assertIn("confirmation", decision.reason)


# ---------------------------------------------------------------------------
# Tool Core integration
# ---------------------------------------------------------------------------


class TestToolCoreIntegration(unittest.TestCase):
    def test_executor_runs_file_tool_and_emits_events(self):
        with _Harness() as h:
            h.write("a.txt", "hi")
            seen = []
            h.bus.subscribe(seen.append, [EventType.TOOL_CALL_STARTED, EventType.TOOL_CALL_COMPLETED])
            result = h.executor.execute(
                ToolCall.new("read_file", {"path": "a.txt"}), _context()
            )
            self.assertTrue(result.ok)
            types = [e.type for e in seen]
            self.assertIn(EventType.TOOL_CALL_STARTED, types)
            self.assertIn(EventType.TOOL_CALL_COMPLETED, types)

    def test_temporal_records_activity(self):
        with _Harness() as h:
            h.write("a.txt", "hi")
            temporal = mock.Mock()
            executor = ToolExecutor(h.registry, bus=EventBus(), policy=_allow_all_policy(), temporal=temporal)
            executor.execute(ToolCall.new("read_file", {"path": "a.txt"}), _context())
            statuses = [c.args[1] for c in temporal.record_tool_activity.call_args_list]
            self.assertIn("STARTED", statuses)
            self.assertIn("COMPLETED", statuses)

    def test_validation_rejects_missing_args(self):
        with _Harness() as h:
            result = h.executor.execute(ToolCall.new("read_file", {}), _context())
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertTrue(any("missing required" in e for e in result.metadata.get("validation", [])))


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


class TestConfigPersistence(unittest.TestCase):
    def test_filesystem_config_loads_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config()
            cfg.tools = {
                "filesystem": {
                    "workspace_root": str(Path(tmp) / "ws"),
                    "max_read_bytes": 999,
                }
            }
            path = Path(tmp) / "config.json"
            cfg.save(path)
            restored = Config.load(path)
            fs = restored.tools["filesystem"]
            self.assertEqual(fs["workspace_root"], str(Path(tmp) / "ws"))
            self.assertEqual(fs["max_read_bytes"], 999)

    def test_limits_from_dict(self):
        limits = FilesystemLimits.from_dict({"max_read_bytes": 123, "max_results": 7})
        self.assertEqual(limits.max_read_bytes, 123)
        self.assertEqual(limits.max_results, 7)
        self.assertEqual(limits.max_search_depth, 20)


if __name__ == "__main__":
    unittest.main()
