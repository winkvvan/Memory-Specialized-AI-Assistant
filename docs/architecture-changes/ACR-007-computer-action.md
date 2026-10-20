# ACR-007

## Problem

Phase 8.5 需要为 Chris 建立安全、可取消、可审计、权限受控的电脑操作基础设施（Computer Action
Core），作为 Phase 9 Vision 的执行层。8.5 只负责"按指定方式执行操作"（固定坐标 action），
**不负责理解屏幕内容**（那是 Phase 9）。当前项目（8.1-8.4）没有任何桌面操作能力。

## Current Architecture

- Tool Core（8.1）：ToolRegistry/ToolExecutor（lookup→validation→permission→timeout→result）/
  PermissionPolicy（READ_ONLY/LOW 默认放行，MEDIUM+ 拒绝 `confirmation_required`）/pipeline。
- 8.3/8.4 提供 backend/adapter 先例：`chris/world/sensors.WindowsComputerAdapter` 用纯 ctypes 读
  Win32；`chris/tools/coding.OpenCodeAdapter` 是唯一 subprocess 触碰点。Tool 事件进 EventBus、
  Temporal `record_tool_activity`。
- 环境：**无 pyautogui/pynput/pywinauto/mss**（均未安装）；只有 PIL。按规范不自动安装大型依赖。

## Proposed Change

- 新增 `chris/tools/computer/`：
  - `models.py`：`ComputerAction`（action_id/action_type/arguments/status/created_at/started_at/
    completed_at/source/risk_level/error/metadata）、`ComputerActionResult`（status/action_id/
    action_type/error/error_code/metadata/duration + `to_text()`）、`ComputerStatus`
    （PENDING/STARTING/RUNNING/COMPLETED/FAILED/CANCELLED/TIMEOUT/DENIED）。
  - `errors.py`：`ComputerError` + codes（backend_error/invalid_coordinates/text_too_long/
    invalid_key/too_many_keys/invalid_action/sequence_too_long/timeout_limit_exceeded/
    confirmation_required/...）。
  - `policy.py`：`ComputerActionPolicy`——action→Risk（READ_ONLY：screen_info/active_window/
    screenshot；LOW：move_mouse/scroll/wait；MEDIUM：click/double_click/key_press/hotkey/
    type_text；HIGH：sequence；CRITICAL：未知/未来危险能力）；坐标校验（负数/越界拒绝，不 clamp）、
    文本长度、key 白名单+数量、高危快捷键检测（Ctrl+Alt+Delete/Win+R/Alt+F4 等，confirmation
    hook，8.6 预留）、allowlist。
  - `backend.py`：`ComputerBackend` ABC（move_mouse/click/double_click/scroll/key_press/hotkey/
    type_text/screenshot/get_screen_size/get_active_window/wait）+ `FakeComputerBackend`（确定性，
    测试/离线）+ `WindowsComputerBackend`（**纯 ctypes Win32**，无新依赖，读屏/截图/窗口信息已实测；
    输入用 SetCursorPos/keybd_event/mouse_event）。**无 shell/subprocess/程序启动**。
  - `manager.py`：`ComputerActionManager`——action 生命周期；dispatch 前做坐标/文本/key 校验与
    hotkey 高危检测；单 action 在 daemon 线程 join 超时（timeout→terminate 语义由 backend 处理）；
    sequence 有界（max_actions/max_duration/cancel_event，任一步失败即停）；`cancel()` 预留；
    COMPUTER_ACTION_* 事件进 EventBus；Temporal `record_tool_activity`。
  - `tools.py`：14 个 Tool（computer_move_mouse/click/double_click/scroll/key_press/hotkey/
    type_text/screenshot/screen_info/active_window/wait/sequence/action_status/action_cancel），
    全部经 Tool Core（Registry→Executor→Permission→Tool→Manager→Backend）。
- 装配：`_build_tool_system` 读取 `tools.computer`（backend 默认 fake；windows/auto 可选）；
  events.py additive 增 `COMPUTER_ACTION_*`；config.json 加节。

## Impact

- 新增 `chris/tools/computer/`；修改 `chris/app.py`、config.json、`chris/events.py`（仅 additive）。
- 不触碰 `chris/llm/`、`chris/temporal/`、`chris/memory/` 公共接口；不复制 Tool Core。

## Compatibility

- 完全向后兼容；computer 工具默认注册，但 MEDIUM/HIGH 默认被权限策略拒绝
  （`confirmation_required`）。默认 backend=fake（安全，不碰真实桌面）；真实输入动作不默认执行。

## Data Migration

- 无数据迁移。Computer Action 不写永久 Memory；type_text 原文/截图字节不进日志/事件/Temporal。

## Provider Independence

- 不依赖任何 LLM Provider；backend 可替换（Fake/Windows/未来其它）。Core 只见
  ComputerAction/ComputerActionResult。

## Security

- 无 shell/subprocess/os.system/任意程序启动；MEDIUM/HIGH 默认拒绝；坐标越界拒绝（不 clamp）；
  文本不落日志；高危快捷键 confirmation hook；不绕过 Permission/Tool Core。

## Testing

- 新增 `tests/test_computer.py`（42 个）：models / validation（坐标/文本/key/sequence/timeout）/
  permission（READ_ONLY/LOW allow，MEDIUM/HIGH/CRITICAL deny，allowlist）/backend（fake mouse/
  click/keyboard/screenshot/window/type_text 只记长度）/manager（success/failure/denied/timeout/
  cancel/sequence 成功+失败+上限+取消/事件/Temporal/坐标校验）/tools（screen_info 放行、click 默认
  拒绝、type_text 元数据不泄漏、sequence 默认拒绝、validation）/E2E（假 LLM→ToolCall→computer→
  ToolResult→LLM）。

## Rollback

- 删除 `chris/tools/computer/`；`_build_tool_system` 移除注册；还原 events.py；移除 config 节。

## Status

APPROVED（Phase 8.5 指令授权实施）
