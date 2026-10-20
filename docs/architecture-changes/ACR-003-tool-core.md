# ACR-003

## Problem

Phase 8.1 需要一个模型无关、可扩展、安全、可测试的 Tool Core。当前架构没有
Tool / Skill / Agent 基础设施；LLM Provider 只能返回文本（`generate -> str`），
无法表达"模型要调用工具"。需要在保留既有行为的前提下建立 Tool 基础设施。

## Current Architecture

- `LLMProvider.generate(messages) -> str` / `stream` / `health_check`；`Message`
  是 `{role, content}`。厂商 tool_call JSON 无处落地。
- EventBus（`chris/events.py`）为同步发布订阅，`EventType` 枚举，无 Tool 事件。
- Temporal 有 `_event_log`（recent_events），只记录时间事件。
- 对话流程：CLI / launcher `chat_turn` 直接 `provider.generate(...)`。
- 受保护模块：`chris/llm/ chris/events.py chris/temporal/`（公共接口默认禁止改动）。

## Proposed Change

- 新增 `chris/tools/`（新目录，非受保护）：
  - `models.py`：`ToolDefinition`(name/description/input_schema/enabled/permission/risk_level)、
    `ToolCall`(call_id/tool_name/arguments/source)、`ToolResult`(status=success/failure/
    denied/cancelled/timeout, content, error, metadata)、`RiskLevel`(READ_ONLY/LOW/MEDIUM/
    HIGH/CRITICAL)、`ToolStatus`。
  - `base.py`：`Tool` ABC（`execute(context, arguments) -> ToolResult`）+ `ToolContext`
    （now/session/memory_context/world_context/project_context/permission_context/logger/bus/
    cancel_event——最小依赖集，不塞 Runtime）。
  - `registry.py`：`ToolRegistry`（register/get/require/list/remove，duplicate/unknown/disabled）。
  - `validation.py`：确定性参数校验（missing/wrong-type/unexpected/invalid/malformed）。
  - `permission.py`：`PermissionPolicy` + `DefaultPermissionPolicy`（默认放行 READ_ONLY/LOW，
    HIGH+ 默认拒绝、可 allowlist）。
  - `executor.py`：`ToolExecutor`——lookup → validation → permission → 带 timeout 执行 → ToolResult；
    串行执行多个调用；异常转结构化 failure，不崩溃。
  - `adapter.py`：`LLMResponse.tool_calls`（厂商中立）→ 内部 `ToolCall`；`ToolDefinition` →
    provider tools 负载。
  - `builtin.py`：`get_current_time` / `echo` 两个无副作用测试 Tool。
  - `pipeline.py`：`run_tool_pipeline`——LLM↔Tool 循环，串行，最多 MAX_TOOL_ROUNDS。
- 受保护模块**仅做 additive 扩展**（不破坏现有接口/行为）：
  - `chris/events.py`：新增 `TOOL_CALL_STARTED/COMPLETED/FAILED/DENIED/CANCELLED` 枚举值。
  - `chris/llm/base.py`：新增 `LLMResponse` 数据类 + `LLMProvider.generate_with_tools()`
    默认实现（无 tools 时等同 `generate`，返回空 tool_calls）——既有 Provider（含 MockProvider）
    零改动即自动支持。
  - `chris/llm/openai_compat.py`：override `generate_with_tools`（发送 tools 负载、解析 tool_calls）。
  - `chris/temporal/system.py`：新增 `record_tool_activity()`（只追加 event log，不发布 bus 事件）。
  - `chris/config.py`：新增 additive `tools` 配置字段。
- 装配：`chris/app.py` `build_runtime` 构建 registry + executor 挂到 `Runtime.tool_registry/
  tool_executor`；`chris/cli.py` 与 `launcher/chat.py` 的阻塞对话路径改走 pipeline，未启用/
  无工具时行为与原来完全一致（`run_tool_pipeline` 返回 None → 回退 `provider.generate`）。

## Impact

- 新增目录 `chris/tools/`；修改 `chris/events.py`、`chris/llm/`、`chris/temporal/`（仅 additive）、
  `chris/config.py`、`chris/app.py`、`chris/cli.py`、`launcher/chat.py`。
- Vision（`mcp_server/`）不动：已作为外部 MCP 工具存在，迁移风险高于收益，本阶段不强制接入。

## Compatibility

- 向后兼容：现有 Provider 不实现 `generate_with_tools` 时走基类默认实现（等同 `generate`）；
  pipeline 检测 `hasattr(provider, "generate_with_tools")` 缺失时返回 None → 回退旧路径。
- 默认 `tools.enabled=true` 但注册的只有无副作用工具；`tools` 配置为 additive，缺失时用默认值。
- 受保护模块改动全部是新增成员，不修改既有枚举/方法/字段。

## Data Migration

- 无 schema 变化、无数据迁移。Tool 执行结果不写入永久 Memory（仅进对话上下文 + event/temporal log）。

## Provider Independence

- Core 只处理 `ToolDefinition / ToolCall / ToolResult`；厂商 tool_call JSON 由 Provider/Adapter
  转换。更换模型不需要改 Tool Core。
- Tool 不依赖任何具体 Provider，也不 import `requests`（guardrail 边界保持）。

## Testing

- 新增 `tests/test_tool_core.py`（44 个用例）：models/registry/validation/execution/
  permission/adapter/provider adapter（真实 `OpenAICompatProvider` 走 mock HTTP）/pipeline/builtin。

## Rollback

- 回滚 = 删除 `chris/tools/`、移除 `Runtime` 两个字段与 `build_runtime` 装配、移除受保护模块的
  additive 成员、恢复 `chat_turn`/`cli` 直接调用 `provider.generate`。既有测试不受影响。

## Status

APPROVED（Phase 8.1 指令授权实施）
