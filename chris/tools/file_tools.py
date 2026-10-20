"""File / local tools (Phase 8.3).

Each tool is a thin wrapper over :class:`Filesystem` that translates results
into LLM-readable ToolResult content and maps :class:`FileToolError` into
structured failures. Risk levels follow the 8.3 spec; HIGH tools (delete, move)
are denied by the default permission policy with ``error_code=confirmation_required``
- the seam for the future 8.6 confirmation flow. No tool ever logs file contents
or writes search/read results to permanent Memory.
"""

from __future__ import annotations

from typing import Any

from chris.tools.base import Tool, ToolContext
from chris.tools.filesystem.errors import FileToolError
from chris.tools.filesystem.ops import Filesystem
from chris.tools.models import RiskLevel, ToolResult, ToolStatus

_README_BOUNDS = "All paths are resolved inside the configured workspace."


def _result_for(tool_name: str, operation: str, data: dict[str, Any]) -> ToolResult:
    lines = [operation]
    for key in ("name", "path", "type", "size", "modified_at", "count", "pattern"):
        if key in data and data[key] is not None:
            lines.append(f"{key}: {data[key]}")
    lines.append("")
    content = "\n".join(lines)
    metadata = {k: v for k, v in data.items() if k not in ("entries", "matches", "content")}
    return ToolResult(
        tool_name=tool_name,
        status=ToolStatus.SUCCESS,
        content=content,
        metadata=metadata,
    )


def _failure(tool_name: str, exc: FileToolError) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        status=ToolStatus.FAILURE,
        error=exc.message,
        metadata={"error_code": exc.code.value},
    )


class FileInfoTool(Tool):
    name = "file_info"
    description = (
        "Return metadata (name, path, type, size, timestamps, extension) for a "
        f"file or directory inside the workspace. {_README_BOUNDS}"
    )
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Workspace-relative path."}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.file_info(arguments["path"])
        except FileToolError as exc:
            return _failure(self.name, exc)
        return _result_for(self.name, "File info:", data)


class ListDirectoryTool(Tool):
    name = "list_directory"
    description = (
        "List the entries of a directory inside the workspace (non-recursive, "
        "deterministic order, size and modification time included). "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Workspace-relative directory path."}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.list_directory(arguments["path"])
        except FileToolError as exc:
            return _failure(self.name, exc)
        lines = [f"Directory: {data['path']}  ({data['count']} entries)"]
        for entry in data.get("entries", []):
            kind = "dir " if entry["type"] == "directory" else "file"
            size = f"{entry['size']:>10}" if entry["size"] is not None else " " * 10
            lines.append(f"  {kind} {size}  {entry['modified_at'][:16]}  {entry['name']}")
        if data.get("truncated"):
            lines.append(f"(truncated at {len(data.get('entries', []))} entries)")
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content="\n".join(lines),
            metadata={
                "path": data["path"],
                "count": data["count"],
                "truncated": data.get("truncated", False),
                "entries": data.get("entries", []),
            },
        )


class FindFilesTool(Tool):
    name = "find_files"
    description = (
        "Find files inside the workspace matching a glob pattern (e.g. *.py, "
        "*.json, README*). Supports recursive search with depth and result "
        "limits. Does not follow symlinks escaping the workspace. "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative directory to search."},
            "pattern": {"type": "string", "description": "Glob pattern, e.g. *.py."},
            "recursive": {"type": "boolean", "default": True},
            "max_results": {"type": "integer", "description": "Max matches (capped by config)."},
            "max_depth": {"type": "integer", "description": "Max recursion depth."},
        },
        "required": ["path", "pattern"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.find_files(
                arguments["path"],
                arguments["pattern"],
                recursive=bool(arguments.get("recursive", True)),
                max_results=arguments.get("max_results"),
                max_depth=arguments.get("max_depth"),
            )
        except FileToolError as exc:
            return _failure(self.name, exc)
        lines = [f"Found {data['count']} match(es) for pattern {data['pattern']!r}:"]
        for match in data.get("matches", []):
            lines.append(f"  {match['path']}  ({match['size']} bytes)")
        if data.get("truncated"):
            lines.append("(results truncated)")
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content="\n".join(lines),
            metadata={
                "pattern": data["pattern"],
                "count": data["count"],
                "truncated": data.get("truncated", False),
                "matches": data.get("matches", []),
            },
        )


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a text file inside the workspace. Returns content, size, encoding "
        "and whether it was truncated at the size limit. Binary files and "
        "protected files (e.g. .env, *.key) are rejected. "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Workspace-relative file path."},
            "encoding": {"type": "string", "default": "utf-8"},
            "max_bytes": {"type": "integer", "description": "Optional read cap."},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.read_file(
                arguments["path"],
                encoding=arguments.get("encoding", "utf-8"),
                max_bytes=arguments.get("max_bytes"),
            )
        except FileToolError as exc:
            return _failure(self.name, exc)
        truncated = data["truncated"]
        header = (
            f"File: {data['path']}\n"
            f"Size: {data['size']} bytes\n"
            f"Encoding: {data['encoding']}"
            + ("\n(truncated at size limit)" if truncated else "")
            + "\n\n<content>\n"
        )
        content = header + data["content"] + "\n</content>"
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=content,
            metadata={
                "name": data["name"],
                "path": data["path"],
                "size": data["size"],
                "encoding": data["encoding"],
                "truncated": truncated,
            },
        )


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create or overwrite a text file inside the workspace (atomic write). "
        "Requires overwrite=true to replace an existing file. Protected files "
        "(e.g. .env) are rejected. "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.MEDIUM
    risk_level = RiskLevel.MEDIUM
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "encoding": {"type": "string", "default": "utf-8"},
            "overwrite": {"type": "boolean", "default": False},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.write_file(
                arguments["path"],
                arguments["content"],
                encoding=arguments.get("encoding", "utf-8"),
                overwrite=bool(arguments.get("overwrite", False)),
            )
        except FileToolError as exc:
            return _failure(self.name, exc)
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=f"Wrote {data['size']} bytes to {data['path']} (overwrite={data['overwritten']}).",
            metadata=data,
        )


class CreateDirectoryTool(Tool):
    name = "create_directory"
    description = (
        "Create a directory (and parents) inside the workspace. "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.LOW
    risk_level = RiskLevel.LOW
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.create_directory(arguments["path"])
        except FileToolError as exc:
            return _failure(self.name, exc)
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=f"Directory {data['path']} {'created' if data['created'] else 'already exists'}.",
            metadata=data,
        )


class DeleteFileTool(Tool):
    name = "delete_file"
    description = (
        "Permanently delete a file inside the workspace. High-risk operation - "
        "denied by default policy until a confirmation flow (Phase 8.6) exists. "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.HIGH
    risk_level = RiskLevel.HIGH
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.delete_file(arguments["path"])
        except FileToolError as exc:
            return _failure(self.name, exc)
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=f"Deleted {data['path']}.",
            metadata=data,
        )


class MoveFileTool(Tool):
    name = "move_file"
    description = (
        "Move a file inside the workspace. Both source and destination are "
        "boundary-checked. High-risk operation. "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.HIGH
    risk_level = RiskLevel.HIGH
    input_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "destination": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False},
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.move_file(
                arguments["source"],
                arguments["destination"],
                overwrite=bool(arguments.get("overwrite", False)),
            )
        except FileToolError as exc:
            return _failure(self.name, exc)
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=f"Moved {data['source']} -> {data['destination']}.",
            metadata=data,
        )


class CopyFileTool(Tool):
    name = "copy_file"
    description = (
        "Copy a file inside the workspace. Both source and destination are "
        "boundary-checked. "
        f"{_README_BOUNDS}"
    )
    permission = RiskLevel.MEDIUM
    risk_level = RiskLevel.MEDIUM
    input_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "destination": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False},
        },
        "required": ["source", "destination"],
        "additionalProperties": False,
    }

    def __init__(self, fs: Filesystem) -> None:
        self.fs = fs

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = self.fs.copy_file(
                arguments["source"],
                arguments["destination"],
                overwrite=bool(arguments.get("overwrite", False)),
            )
        except FileToolError as exc:
            return _failure(self.name, exc)
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=f"Copied {data['source']} -> {data['destination']}.",
            metadata=data,
        )


def build_file_tools(policy, limits=None, cancel_event=None, reject_sensitive: bool = False) -> list[Tool]:
    """Construct all file tools bound to one workspace policy."""
    fs = Filesystem(
        policy=policy,
        limits=limits,
        cancel_event=cancel_event,
        reject_sensitive=reject_sensitive,
    )
    return [
        FileInfoTool(fs),
        ListDirectoryTool(fs),
        FindFilesTool(fs),
        ReadFileTool(fs),
        WriteFileTool(fs),
        CreateDirectoryTool(fs),
        DeleteFileTool(fs),
        MoveFileTool(fs),
        CopyFileTool(fs),
    ]
