# ACR-006

## Problem

Phase 8.4 需要让 Chris 能识别"编程/项目开发任务"并安全、可控、可审计地调度外部 Coding Agent
（OpenCode）完成实际开发。Chris 是 orchestrator，不是 coding agent 的重实现——绝不复制/fork/
重写 OpenCode。当前项目（8.1 Tool Core + 8.2 Web Search + 8.3 File/Local Tools）没有任何
coding 集成。

## Current Architecture

- Tool Core（8.1）：ToolRegistry/ToolExecutor（lookup→validation→permission→timeout→result）/
  PermissionPolicy（READ_ONLY/LOW 默认放行，MEDIUM+ 拒绝 `confirmation_required`）/pipeline。
- 8.3 `WorkspacePathPolicy`：canonical 路径包含性检查（唯一路径策略，必须复用）。
- 8.3 `TemporalSystem.record_tool_activity`、EventBus（`TOOL_CALL_*` 事件）。
- 环境：OpenCode **未安装**（`where opencode` 无结果）；npm/npx 存在但本阶段不自动安装。

## Proposed Change

- 新增 `chris/tools/coding/`（外部 Coding Agent 的受控 adapter 层，模型无关）：
  - `models.py`：`CodingTask`（task_id/project_root/prompt/status/时间戳/provider/session_id/
    exit_code/summary/changed_files/test_status/error/metadata）、`CodingResult`（status/summary/
    changed_files/tests/exit_code/duration/error/raw_tail + `to_text()` LLM 投影）、`CodingStatus`
    （PENDING/STARTING/RUNNING/WAITING/COMPLETED/FAILED/CANCELLED/TIMEOUT/DENIED）、
    `ProjectContext`（project_id/root/name/language/framework/git_root）、`CodingTaskIntent`。
  - `errors.py`：`CodingError` + codes（opencode_not_installed/project_context_required/
    project_not_found/project_busy/confirmation_required/...）。
  - `policy.py`：`CodingExecutionPolicy`——执行 scope（READ_ONLY/PROJECT_BUILD/PROJECT_TEST/
    PROJECT_LINT/PROJECT_PACKAGE/GIT_READ/GIT_WRITE/SYSTEM_COMMAND）→ Risk；未知 scope 拒绝；
    PACKAGE/GIT_WRITE/SYSTEM 默认 `confirmation_required`；**环境净化**：白名单变量 + 过滤
    api_key/token/password/secret/private_key（如 ZHIPU_API_KEY 绝不传给 coding 子进程）。
  - `adapter.py`：`OpenCodeAdapter`——唯一触碰 subprocess 的地方；参数数组（无 shell=True）、
    固定 cwd（项目根）、净化 env、start/stream_lines/wait(deadline→terminate→kill)/cancel；
    未安装 → `opencode_not_installed`。
  - `manager.py`：`CodingTaskManager`——生命周期状态机；后台线程执行（不阻塞 GUI/executor）；
    同项目并发保护（`project_busy`）；timeout（default/max，超时 terminate 进程）；`cancel()`；
    CODING_* 事件进现有 EventBus；`record_tool_activity` 进 Temporal。
  - `project.py`：`ProjectResolver`——复用 8.3 `WorkspacePathPolicy` 做边界；无明确项目 →
    `project_context_required`；从 marker 文件推断 language/framework；git_root 检测。
  - `parser.py`：`CodingResultParser`——changed files 用只读 `git status --porcelain`；
    test 状态启发式（PASS/FAILED/SKIPPED/NOT_RUN，不猜测）。
  - `tool.py`：三个工具——`coding_task`（MEDIUM，非阻塞 submit，返回 task_id）、
    `coding_task_status`（READ_ONLY 轮询）、`coding_task_cancel`（MEDIUM）。
- 装配：`_build_tool_system` 读取 `tools.coding` 配置（workspace_root 默认 `workspace/`、
  command 默认 `opencode`、default/max timeout）；events.py 增加 additive `CODING_*` 事件。

## Impact

- 新增 `chris/tools/coding/`；修改 `chris/app.py`、config.json、`chris/events.py`（仅 additive
  枚举值）。不触碰 `chris/llm/`、`chris/temporal/`、`chris/memory/` 公共接口。
- 复用 8.3 `WorkspacePathPolicy`（不复制第二套路径策略）。

## Compatibility

- 完全向后兼容：不注册新工具时不改变既有行为；coding 工具默认注册但 MEDIUM 被默认权限策略拒绝
  （`confirmation_required`），需 allowlist 或未来 8.6。OpenCode 未安装时 submit 返回
  `opencode_not_installed`，不安装不崩溃。

## Data Migration

- 无数据迁移。Coding 任务不写永久 Memory（仅 ToolResult / conversation context / Temporal
  activity / EventBus 事件）。

## Provider Independence

- Chris 只依赖 `OpenCodeAdapter` 接口与结构化 CodingTask/CodingResult；Coding Agent 可替换
  （OpenCode → 其它 CLI 只需换 adapter）。OpenCode 内部 session/command/schema 永不进入 Core。

## Security

- 无 shell=True / 无字符串拼接命令；环境白名单 + 敏感变量过滤；项目边界 = 复用 8.3 policy；
  git 只读（status/diff）不 push/reset/clean；scope 分类（GIT_WRITE/PACKAGE/SYSTEM 默认拒绝）。
- 不为测试而默认放行高风险 scope。

## Testing

- 新增 `tests/test_coding.py`（30 个）：models/policy（scope 风险、env 净化、未知 scope 拒绝）/
  project（名称/相对路径/缺失/无上下文/越界/绝对路径）/manager（成功/失败/超时/取消/并发/
  denied/事件/Temporal）/tools（submit/status/cancel/校验/权限/opencode_not_installed）/
  parser（test 启发式）/完整 E2E（LLM→ToolCall→CodingTaskTool→Manager→FakeOpenCodeAdapter→
  CodingResult→ToolResult→LLM）。

## Rollback

- 删除 `chris/tools/coding/`；`_build_tool_system` 移除注册；还原 events.py 枚举；移除 config 节。

## Status

APPROVED（Phase 8.4 指令授权实施）
