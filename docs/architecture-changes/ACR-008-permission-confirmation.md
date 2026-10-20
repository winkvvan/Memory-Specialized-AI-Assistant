# ACR-008

## Problem

Phase 8.6 需要把当前"高风险工具默认 DENY + 返回 confirmation_required"升级为真正可用的
Permission Confirmation：用户明确确认后，用原始 ToolCall 重新通过安全检查并执行。之前
8.1-8.5 只是预留了 `error_code=confirmation_required` 的 seam，没有任何可恢复的确认流程。

## Current Architecture

- `chris/tools/permission.py`：`PermissionDecision(allowed, reason)` 只有 ALLOW/DENY；
  `DefaultPermissionPolicy` 允许 READ_ONLY/LOW，其余返回 `allowed=False`。
- `chris/tools/executor.py`：`ToolExecutor.execute` 在 `allowed=False` 时直接返回
  `confirmation_required` 的 DENIED 结果，不创建任何请求。
- computer/coding 各有独立的第二层 policy（`ComputerActionPolicy.decision` 返回三元组、
  `CodingExecutionPolicy.check_scope` 返回 `ScopeDecision`），它们也在 manager 内 DENY
  MEDIUM/HIGH（双重门控）。
- `Runtime` 已挂 `tool_registry`/`tool_executor`；EventBus/Temporal 已有 TOOL/CODING/COMPUTER 事件。

## Proposed Change

- `chris/tools/permission.py`（additive）：
  - `PermissionDecision` 增加 `outcome: PermissionOutcome`（ALLOW/DENY/CONFIRMATION_REQUIRED）
    与 `confirmation_required: bool`，保留 `allowed` 字段向后兼容；提供 `allow/deny/
    require_confirmation` 工厂。
  - `DefaultPermissionPolicy`：READ_ONLY/LOW→ALLOW，MEDIUM/HIGH→CONFIRMATION_REQUIRED，
    CRITICAL→DENY（不可确认）。
- 新增 `chris/tools/confirmation/`：
  - `models.py`：`ConfirmationRequest`（confirmation_id/call_id/tool_name/arguments/risk_level/
    reason/source/status/created_at/expires_at/decided_at/decided_by/metadata）+ `ConfirmationStatus`
    （PENDING/APPROVED/DENIED/EXPIRED/CANCELLED/CONSUMED）；`safe_summary()` 绝不包含 arguments。
  - `errors.py`：`ConfirmationError` + 8 个稳定错误码。
  - `manager.py`：`ConfirmationManager`——状态机（PENDING→APPROVED→CONSUMED / DENIED / EXPIRED /
    CANCELLED），非法转换拒绝；TTL（default/max，配置驱动，clamp）；线程安全（多 pending 并发）；
    一次性 consume；`expire_pending()`；in-memory（重启失效）。事件进 EventBus、activity 进 Temporal。
  - `reason.py`：`render_reason()`——敏感参数（text/password/secret/token/api_key/content）脱敏
    为 `<N chars>`。
- `chris/tools/executor.py`：
  - 构造器增加可选 `confirmation: ConfirmationManager`（无则回退到 8.1-8.5 行为）。
  - `execute`：CONFIRMATION_REQUIRED → `create_request` → 返回结构化 `confirmation_required`
    结果（含 confirmation_id/expires_at）。
  - 新增 `execute_approved(call, context, confirmation_id)`：重新 lookup+validation+permission，
    `_consume_approval` 校验 call 匹配 + approve + consume（一次），标记 `context.approved=True`
    后执行（TOCTOU 安全：批准不跳过任何安全检查）。
  - CRITICAL 仍走 `permission_denied`，不创建 confirmation。
- 双重门控解决：`ToolContext.approved` 字段；computer/coding 的 inner manager 在 `authorized` 时
  放行 MEDIUM/HIGH 但仍拒绝 CRITICAL（`run_action(authorized=...)` / `create_task(authorized=...)`）。
- `chris/events.py`：additive 增 7 个 `CONFIRMATION_*` 事件。
- `chris/app.py`：`_build_tool_system` 构建 ConfirmationManager 挂到 `Runtime.confirmation` 并传给 executor。
- `chris/cli.py`：`_confirm_prompt`（显式 y/yes 白名单）通过 `confirm` 回调接入 pipeline；
  `interpret_confirmation_answer` 纯函数，模糊输入一律不批准。
- `chris/tools/pipeline.py`：`run_tool_pipeline` 增加可选 `confirm` 回调；confirmation_required 结果
  经回调批准后走 `execute_approved`。
- config.json 增加 `tools.permission_confirmation`（default_ttl_seconds=60 / max_ttl_seconds=300）。

## Impact

- 新增 `chris/tools/confirmation/`；修改 `chris/tools/permission.py`（additive）、`chris/tools/executor.py`、
  `chris/tools/pipeline.py`、`chris/tools/base.py`（ToolContext.approved 字段）、computer/coding 的
  manager+tools（authorized 参数）、`chris/events.py`、`chris/app.py`、`chris/cli.py`、config.json。
- 不触碰 `chris/llm/`、`chris/temporal/`、`chris/memory/` 公共接口。

## Compatibility

- 完全向后兼容：无 confirmation manager 时 executor 行为与 8.1-8.5 一致；`PermissionDecision.allowed`
  保留；`run_tool_pipeline` 无 `confirm` 回调时 confirmation_required 结果原样传回 LLM。
- 现有 8.5 computer/coding 的 `decision()`/`check_scope()` 签名不变（inner manager 仅加 authorized 参数）。

## Data Migration

- 无数据迁移。Confirmation 是 in-memory 短生命周期状态，重启后 PENDING 全部失效，不写永久 Memory。

## Provider Independence

- 不依赖任何 LLM Provider；Confirmation 只绑定原始 ToolCall 与结构化状态。

## Testing

- 新增 `tests/test_confirmation.py`（41 个）：模型 / 状态机（approve/deny/cancel/expire/consume、非法转换、
  TTL clamp、并发独立、ID 唯一、事件、Temporal、sensitive 不泄漏）/ render_reason 脱敏 / 权限三态 /
  executor 集成（confirmation_required 结构化、approve 执行、one-shot、call mismatch、post-approval 校验、
  CRITICAL 不可确认、无 manager 回退）/ CLI 答案解释（显式 yes/no + 模糊输入不批准）/ E2E（fake LLM
  click approve 与 deny）。

## Rollback

- 删除 `chris/tools/confirmation/`；还原 executor/permission/pipeline 改动；移除 Runtime.confirmation；
  移除 config 节与 CLI 回调。

## Status

APPROVED（Phase 8.6 指令授权实施）
