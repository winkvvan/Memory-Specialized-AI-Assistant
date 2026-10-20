# Memory-Specialized AI Assistant

一个长期运行、以记忆为核心的本地 AI 助手。主脑、语音识别和语音合成都通过可替换接口接入，不绑定任何单一模型或厂商。

## 设计原则

- **主脑可替换**：业务层只依赖 `LLMProvider`，通过 OpenAI-compatible API 或本地 mock 运行。
- **语音可替换**：语音识别只依赖 `ASRProvider`，默认支持 OpenAI-compatible `/audio/transcriptions` API。
- **发声可替换**：语音合成只依赖 `TTSProvider`，默认支持 OpenAI-compatible `/audio/speech` API。
- **状态独立于推理**：人格、记忆、时间、世界状态持久化在 `data/`，不绑定具体模型。
- **隐私优先**：仓库不包含任何个人配置、API Key、运行数据、模型权重或音频样本。

## 当前能力

- 长期记忆：分层记忆、活力衰减、归档、检索、维护调度
- 人格与时间：版本化人格、连续时间轴、世界状态
- 工具系统：文件、搜索、Coding、电脑操作、权限确认
- 桌面界面：基于 tkinter 的聊天窗口
- 语音链路：麦克风输入 → ASR API → 对话/工具链 → TTS API → 播放
- 完全离线模式：`provider: mock` + `voice.enabled: false`

## 快速开始

要求 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

默认 `config.json` 使用离线 `mock` 主脑，不发起网络请求：

```powershell
python -m chris
```

检查运行时和 Provider 配置：

```powershell
python -m chris --health
```

## 接入主脑 API

将 `config.json` 的 `llm` 节改为 OpenAI-compatible API。任何兼容 `/chat/completions` 的服务都可以使用：

```json
{
  "llm": {
    "provider": "api",
    "model": "gpt-4o-mini",
    "options": {
      "base_url": "https://api.openai.com/v1"
    }
  }
}
```

API Key 从环境变量读取，不写入配置文件：

```powershell
$env:CHRIS_API_KEY = "your-key"
```

也可以使用内置厂商快捷配置：`deepseek`、`qwen`、`moonshot`、`glm`、`openai_compat`。每个厂商的 Key 也可统一放在 `CHRIS_API_KEY`。

## 接入语音识别 API

语音识别接口实现 OpenAI-compatible `POST /audio/transcriptions`。配置示例：

```json
{
  "voice": {
    "enabled": true,
    "asr": {
      "enabled": true,
      "provider": "api",
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "CHRIS_API_KEY",
      "model": "whisper-1",
      "language": "zh"
    },
    "input": {
      "enabled": true,
      "backend": "windows"
    }
  }
}
```

`base_url` 也可以直接写成完整端点，例如 `https://example.com/v1/audio/transcriptions`。

## 接入语音合成 API

语音合成接口实现 OpenAI-compatible `POST /audio/speech`。远程地址必须使用 HTTPS；纯 HTTP 只允许 localhost。配置示例：

```json
{
  "voice": {
    "enabled": true,
    "tts": {
      "enabled": true,
      "provider": "api",
      "base_url": "https://api.openai.com/v1",
      "api_key_env": "CHRIS_API_KEY",
      "model": "tts-1",
      "voice": "alloy",
      "response_format": "wav"
    },
    "output": {
      "enabled": true,
      "backend": "windows"
    }
  }
}
```

也可以把 `tts.provider` 设置为 `http`，接入本地 OpenAI-compatible TTS 服务。音频输出目前支持 Windows `winsound` 和测试用 fake backend。

## 自定义接口

新增 Provider 时实现对应抽象类，然后在 factory 中注册：

- 主脑：`chris.llm.base.LLMProvider`
- 语音识别：`chris.voice.asr.ASRProvider`
- 语音合成：`chris.voice.tts.TTSProvider`
- 麦克风输入：`chris.voice.audio.AudioInputProvider`
- 音频播放：`chris.voice.audio.AudioOutputProvider`

核心业务代码不会直接导入具体厂商实现。

## 数据与隐私

运行时数据写入 `data/`，已被 `.gitignore` 排除。请勿提交：

- `data/` 下的记忆、人格、时间、世界状态
- `.env`、API Key、Token
- 个人音频、模型权重、构建产物
- 外部 ASR/TTS 模型、构建目录和虚拟环境

Schema 变更应保留迁移和备份，不直接覆盖旧数据。

## 桌面应用

```powershell
python launcher\launcher.py
```

重新构建 Windows EXE：

```powershell
powershell -ExecutionPolicy Bypass -File launcher\build.ps1
```

## 测试

```powershell
python -m unittest discover
python scripts/check_project_safety.py
```

安装 MCP 可选依赖后可运行 MCP 服务器测试：

```powershell
python -m pip install -e ".[mcp]"
python -m mcp_server
```

## 目录结构

```text
chris/        核心运行时、记忆、人格、时间、世界、工具、语音接口
launcher/     桌面应用
mcp_server/   可选的 MCP 视觉工具
tests/        离线测试
docs/         架构与交接文档
scripts/      项目安全自检
config.json   通用默认配置（mock、无密钥、语音关闭）
```

## 许可证

MIT License。详见 `LICENSE`。
