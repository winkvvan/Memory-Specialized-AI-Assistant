# ACR-005

## Problem

Phase 8.3 需要让 Chris 具备安全、可控、可审计的本地文件操作能力（查看/查找/读取/创建/修改/
删除/移动/复制/元数据），但必须严格限制在明确的 Workspace 边界内，且绝不赋予任意电脑控制权。
当前项目（8.1 Tool Core + 8.2 Web Search）没有任何文件操作能力。

## Current Architecture

- Tool Core（8.1）：`chris/tools/`——ToolDefinition/ToolCall/ToolResult、ToolRegistry、
  ToolExecutor（lookup→validation→permission→timeout→result）、PermissionPolicy、
  `cancel_event` 协作取消、TOOL_CALL_* 事件、`TemporalSystem.record_tool_activity`。
- Web Search（8.2）：`chris/tools/search/`（SearchProvider/结构化结果/错误映射）。
- Config：`config.json` + `Config` dataclass（`tools` 节，additive）。
- 边界：业务代码禁 import `requests`（guardrail 豁免仅 llm/embedding-adapters/search-adapters）；
  文件工具只用 stdlib，无需豁免。

## Proposed Change

- 新增 `chris/tools/filesystem/`（stdlib：os/pathlib/shutil，无新依赖）：
  - `path.py`：`WorkspacePathPolicy`——canonical resolved path 包含性检查（`os.path.commonpath`，
    非字符串前缀），拒绝 `../`、绝对路径越界、Windows drive path、UNC path、symlink/junction escape；
    无法安全解析时宁可拒绝。
  - `errors.py`：`FileToolError` + `FileToolErrorCode`
    （path_outside_workspace/file_not_found/permission_denied/file_too_large/binary_file_not_supported/
    invalid_encoding/invalid_path/directory_not_found/target_exists/overwrite_not_allowed/
    confirmation_required/operation_cancelled/operation_timeout/io_error/not_a_directory/is_a_directory/
    too_many_results/sensitive_file/max_depth_exceeded）。
  - `limits.py`：`FilesystemLimits`（max_read_bytes=1MB/max_write_bytes=1MB/max_results=200/
    max_directory_entries=500/max_search_depth=20，全部 config 驱动）。
  - `ops.py`：`Filesystem`——list/find/read/write/mkdir/delete/move/copy/info；读文件二进制检测
    （NUL 字节启发式）、UTF-8 解码失败→invalid_encoding、大小超限→显式 truncated 标记；写文件
    原子化（tempfile + os.replace）；递归搜索检查 `cancel_event`；`reject_sensitive` 为可配置
    扩展点（默认关闭，工作区边界是主要防线；未来 8.6 confirmation 接管）。
- 新增 `chris/tools/file_tools.py`：9 个 Tool，Risk 依规范：
  | Tool | Risk |
  |---|---|
  | file_info / list_directory / find_files / read_file | READ_ONLY |
  | create_directory | LOW |
  | write_file / copy_file | MEDIUM |
  | move_file / delete_file | HIGH |
  ToolResult 内容为 LLM 可读文本；失败映射为结构化 ToolResult（error_code），traceback 不暴露。
- 装配：`build_runtime._build_tool_system` 读取 `tools.filesystem` 配置（workspace_root 相对项目根
  解析，默认 `workspace/`；不硬编码用户目录），构建 `Filesystem` + 注册全部 File Tools。
- Executor（8.1）最小 additive 改动：DENIED 结果附加 `metadata.error_code="confirmation_required"` +
  `risk_level`（为 8.6 预留；不改 `DefaultPermissionPolicy` 接口）。
- config.json 增加 `tools.filesystem` 节；`.gitignore` 增加 `workspace/`（运行时数据）。

## Impact

- 新增 `chris/tools/filesystem/`、`chris/tools/file_tools.py`；修改 `chris/app.py`、config.json、
  `.gitignore`、`chris/tools/executor.py`（仅 denied 分支 metadata additive）。
- 不触碰 `chris/llm/`、`chris/events.py`、`chris/temporal/`、`chris/memory/` 公共接口。

## Compatibility

- 向后兼容：无工具路径的普通对话不受影响（8.1 pipeline 保证）；File Tools 默认注册但仅在 LLM
  发出对应 ToolCall 时执行。`tools.filesystem` 为 additive 配置，缺失时用默认 workspace。
- 高风险工具（write/copy/move/delete）默认被 Permission Policy 拒绝（`confirmation_required`），
  不默认放行，避免"为测试通过而削弱安全"。

## Data Migration

- 无 schema 变更。文件内容**不写入永久 Memory**（仅当前对话上下文/ToolResult）。
  新增 `workspace/` 运行时目录（gitignore）。

## Provider Independence

- 文件工具不依赖任何 LLM Provider / 搜索 Provider；只依赖 stdlib + Tool Core 接口。

## Testing

- 新增 `tests/test_file_tools.py`（57 个）：路径安全（normal/nested/`../`/`..\`/绝对路径/drive/UNC/
  symlink escape/workspace 前缀碰撞）、list_directory（空/文件目录/确定性排序/max entries）、
  find_files（recursive/non/glob/max_results/max_depth/cancel/不存在目录）、read_file（UTF-8/BOM/
  大文件 truncated/二进制/缺失/invalid encoding）、write_file（创建/覆盖/拒绝覆盖/父目录/大小限制/
  原子无残留 temp/越界/敏感文件策略开关）、create_directory（normal/nested/existing/越界）、
  delete（默认拒绝+confirmation_required/allowlist 后成功/缺失/目录拒绝）、move/copy（含越界）、
  file_info、permission 模型（READ_ONLY allowed/MEDIUM 默认拒绝/HIGH 默认拒绝）、Tool Core 集成
  （Registry→Executor→Permission→Tool→ToolResult→EventBus→Temporal）、validation、config 持久化。

## Rollback

- 删除 `chris/tools/filesystem/` + `file_tools.py`；`_build_tool_system` 移除注册；还原 executor
  denied metadata；移除 config 节与 gitignore 行。

## Status

APPROVED（Phase 8.3 指令授权实施）
