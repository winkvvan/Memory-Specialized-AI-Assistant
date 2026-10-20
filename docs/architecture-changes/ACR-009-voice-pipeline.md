# ACR-009

## Problem

Phase 9.3 需要把已验收的 9.1 ASR（chrisASR）与 9.2 TTS（chrisTTS）以"松耦合、可替换、可关闭"
的方式接入 Chris，形成本地语音链：麦克风输入 → ASR → 文本 → 现有 Chat/Tool Pipeline → 回复 →
TTS → 本地播放。必须在不让模型/源码进入 Chris 主仓库、不修改 ASR/TTS 源码、不重构 Chat Pipeline
的前提下完成。

## Current Architecture

- 9.1 ASR：独立 chrisASR（transcribe.py CLI + QwenASREngine Python API，GPU/CPU 已验证）。
- 9.2 TTS：独立 chrisTTS（tts-server.exe @ 127.0.0.1:8090，OpenAI-compatible /v1/audio/speech，
  GPU/CPU 已验证）。
- 8.1-8.6：Tool Core / Permission / Confirmation / EventBus / Temporal 完整。
- **已知缺口**：`launcher/chat.py` 的 `stream_turn`（GUI 主路径）**未接入 Tool Pipeline**（直接
  `provider.stream`），而 `chat_turn`（阻塞，测试/CLI）已接 tool loop。语音输入要触发 web_search /
  file / coding / computer / confirmation，就必须打通 GUI 流式路径的 tool loop。
- Guardrail：business code（chris/ 除 llm/embedding-adapters/search-adapters）不能 import requests。

## Proposed Change

- 新增 `chris/voice/`（依赖抽象接口，不 import chrisASR/chrisTTS，不复制模型）：
  - `models.py`：`VoiceStage`（IDLE/LISTENING/TRANSCRIBING/THINKING/SPEAKING + 错误态）、
    `VoiceState`（IDLE/BUSY）、`VoiceSession`（safe_summary 不含音频/密钥）。
  - `errors.py`：`VoiceError` + 稳定 codes（asr_*/tts_*/input_*/voice_*）。
  - `config.py`：`VoiceConfig`（从现有 config.json `voice` 节解析，不建第二套配置）。
  - `asr.py`：`ASRProvider` ABC + `FakeASRProvider` + `ChrisASRProvider`（subprocess **参数数组**
    调 transcribe.py，`errors="replace"` 容错，读 sibling .txt，失败映射结构化错误）。
  - `tts.py`：`TTSProvider` ABC + `FakeTTSProvider` + `HttpTTSProvider`（**只用标准库 urllib**，
    校验 base_url 必须 localhost，调 127.0.0.1:8090 /v1/audio/speech）。
  - `audio.py`：`AudioInputProvider`/`AudioOutputProvider` ABC + Fake（确定性测试）+ Windows
    （ctypes waveIn 录音 / winsound 播放，纯 stdlib）。
  - `pipeline.py`：`VoicePipeline` 状态机（含 busy 保护、cancel、各阶段失败回 IDLE、复用注入的
    `ChatRunner` —— 不重写对话逻辑）。
  - `manager.py`：`VoiceManager`（enabled/busy/cancel + `run_voice_session`/`set_chat_runner`）。
  - `factory.py`：按配置选 provider（fake/chris/http/windows）。
- 新增 `chris/audio_backend/windows_input.py`：纯 ctypes waveIn 录音（参数化 device/rate/channels/
  max_seconds/cancel，结构化失败）。
- `chris/events.py`：additive 增 11 个 `VOICE_*` 事件。
- `chris/config.py`：additive 加 `voice` 字段；config.json 加 `voice` 节（默认 disabled）。
- `chris/app.py`：`Runtime.voice` + `_build_voice`（注入 chat_runner 以解耦 launcher 层）。
- `launcher/chat.py`：
  - 修复 `stream_turn`：tools active 时经 Tool Pipeline（支持工具 + Permission Confirmation），
    tools 不活跃时保持原流式路径（文字延迟不变）。
  - `RealVoiceChatRunner` 把 ASR 文本导入现有 `chat_turn`；`_wire_voice`。
- `chris/cli.py`：main 里动态 wire voice runner（CLI 文字模式不变）。

## Impact

- 新增 `chris/voice/`、`chris/audio_backend/`；修改 app/events/config/cli/launcher-chat/config.json。
- 不触碰 chris/llm/、memory/、temporal/、tool core 公共接口（仅 additive）。
- 不修改 chrisASR / chrisTTS 任何源码或模型。

## Compatibility

- voice 默认 disabled：`voice.enabled=false` 时完全按原文字模式运行（有测试）。
- `stream_turn` 对 tools-inactive 保持原流式；tools-active 走 pipeline（GUI 行为变正确）。
- Provider 可替换：配置选 fake/真实，不影响核心。

## Data Migration

- 无数据迁移。Voice 不写永久 Memory（ASR 文本仅进对话上下文，沉淀交由 Memory Ecology）。音频/
  敏感语音不进 EventBus/Temporal/safe_summary。

## Security

- 无 shell=True / os.system；ASR subprocess 参数数组；TTS 仅 localhost（非 localhost 拒绝）；
  不把 API Key 传给 ASR/TTS 子进程（不注入 env secret）；无下载/上传；无任意程序启动。

## Testing

- 新增 `tests/test_voice_pipeline.py`（26 个）：ASR（fake/超时/进程失败/读 sibling txt）、TTS
  （fake/localhost 成功/非 localhost 拒绝/unavailable）、audio（fake 录制/取消/播放）、factory、
  pipeline 状态机（文字/音频流程、事件、ASR/TTS 失败回 IDLE）、manager（disabled/busy/cancel）、
  完整 fake E2E、text-mode 回归。

## Rollback

- 删除 `chris/voice/` + `chris/audio_backend/`；还原 app/events/config/cli/launcher-chat/config.json。

## Status

APPROVED（Phase 9.3 指令授权实施）
