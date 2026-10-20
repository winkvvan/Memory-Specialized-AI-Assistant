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

## 首次运行与可编辑状态

仓库只包含通用模板，不包含任何人的记忆、人格或测试对话。首次启动时，缺失的状态文件会从 `templates/` 复制到 `data/`：

| 模板 | 首次生成位置 | 用途 |
|---|---|---|
| `templates/personality.json` | `data/personality.json` | 人格、语言风格、关系设定 |
| `templates/memory.json` | `data/chats/memory.json` | 长期记忆 |
| `templates/memory.archive.json` | `data/chats/memory.archive.json` | 归档记忆 |

初始化只会在文件不存在时执行，不会覆盖你已有的状态。`data/` 已被 `.gitignore` 忽略，因此不会进入 Git 仓库。

如果希望在第一次启动前就修改模板，可以手动复制：

```powershell
New-Item -ItemType Directory -Force data\chats | Out-Null
Copy-Item templates\personality.json data\personality.json
Copy-Item templates\memory.json data\chats\memory.json
Copy-Item templates\memory.archive.json data\chats\memory.archive.json
```

修改人格或记忆前请先停止程序，避免运行时写入覆盖手工修改。

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

## 修改人格

人格文件默认是 `data/personality.json`。它由通用模板生成，可以按自己的需要直接编辑；不需要修改代码。

主要字段：

| 字段 | 作用 |
|---|---|
| `core.name` | 助手名称 |
| `core.identity` | 身份描述 |
| `core.core_traits` | 核心性格列表 |
| `core.values` | 价值观列表 |
| `core.boundaries` | 行为边界 |
| `core.language_style` | 语言、称呼、正式程度、句式和 emoji 使用 |
| `core.humor_tendency` | 幽默倾向 |
| `core.proactiveness` | 主动性 |
| `core.emotion_expression` | 情绪表达程度 |
| `communication_style` | 整体语气、幽默、温暖度、正式度、直接度等数值 |
| `relationship` | 关系类型、态度、信任和熟悉度 |
| `behavioral_preferences.usually` | 通常应该做的事 |
| `behavioral_preferences.avoid` | 应避免的行为 |
| `evolution_policy` | 人格自动演化规则（证据数、回滚、单次变化限制） |
| `history[0].adaptive.preferences` | 可自动调整的偏好数值 |

建议只修改你明确理解的字段，并保持 JSON 合法。`core` 是稳定人格层，程序不会自动改写；`adaptive` 是受保护的可演化层，通常通过 `/adjust` 和 `/evolve` 修改。

修改前备份：

```powershell
Copy-Item data\personality.json data\personality.backup.json
```

编辑后验证：

```powershell
python -m chris --health
```

如果想恢复为通用人格，停止程序后删除人格文件并重新启动。程序会从模板重新生成：

```powershell
Remove-Item data\personality.json
python -m chris
```

## 修改记忆

默认记忆文件是 `data/chats/memory.json`，归档文件是 `data/chats/memory.archive.json`。推荐通过对话或 CLI 修改，避免手写复杂的生命周期字段：

```powershell
python -m chris
```

常用命令：

```text
/memory list
/memory recall 关键词
/memory search 关键词
/memory inspect <memory_id>
/memory forget <memory_id>
/memory archive <memory_id>
/memory restore <memory_id>
/memory stats
/memory maintenance
```

也可以直接说“记住……”让助手创建记忆。手工编辑时，先停止程序并备份：

```powershell
Copy-Item data\chats\memory.json data\chats\memory.backup.json
```

文件的基本结构：

```json
{
  "schema_version": 2,
  "entries": []
}
```

每条记忆至少需要唯一的 `memory_id` 和有意义的 `content`。完整单条示例见 `templates/memory-entry.example.json`，可以复制到 `entries` 数组中再修改。常见字段包括 `kind`、`memory_type`、`importance`、`timestamp`、`tags` 和 `pinned`。

`data/chats/memory.embeddings.json` 是语义检索缓存。修改记忆、切换 Embedding 模型或出现检索异常时，可以停止程序后删除它，程序会重新生成：

```powershell
Remove-Item data\chats\memory.embeddings.json -ErrorAction SilentlyContinue
```

清空本地测试记忆：

```powershell
Remove-Item data\chats\memory.json, data\chats\memory.archive.json, data\chats\memory.embeddings.json -ErrorAction SilentlyContinue
python -m chris
```

## 备份与恢复

停止程序后备份整个状态目录：

```powershell
Copy-Item -Recurse data data-backup-20261020
```

恢复时先停止程序，再把备份目录中的内容复制回 `data/`。不要提交 `data/` 或备份目录到 Git；它们可能包含你的记忆、人格和时间状态。

## 修改数据路径

`config.json` 中的以下字段可以改为绝对路径或其他相对路径：

```json
{
  "personality_file": "data/personality.json",
  "memory_file": "data/chats/memory.json",
  "world_state_file": "data/world.json",
  "temporal_file": "data/temporal.json",
  "log_file": "data/chris.log"
}
```

自动初始化只处理项目 `data/` 下的默认路径。使用外部目录时，请手动复制对应模板，或先把 `personality_file`、`memory_file` 指回 `data/`。

## 常用操作

```powershell
# 命令行聊天
python -m chris

# 桌面界面
python launcher\launcher.py

# 健康检查
python -m chris --health

# 运行测试（不包括 GUI 和可选 MCP）
$env:PYTHONPATH="."
python -m unittest tests.test_api_interfaces tests.test_bootstrap tests.test_app tests.test_personality

# 项目安全检查
python scripts/check_project_safety.py
```

## 数据与隐私

运行时数据写入 `data/`，已被 `.gitignore` 排除。请勿提交：

- `data/` 下的记忆、人格、时间、世界状态和备份
- `.env`、API Key、Token
- 个人音频、模型权重、构建产物
- 外部 ASR/TTS 模型、构建目录和虚拟环境

API Key 只放在环境变量中，不要写入 `config.json`、README 或提交历史。Schema 变更应保留迁移和备份，不直接覆盖旧数据。

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
templates/    通用人格与记忆模板（首次运行使用）
launcher/     桌面应用
mcp_server/   可选的 MCP 视觉工具
tests/        离线测试
docs/         架构与交接文档
scripts/      项目安全自检
config.json   通用默认配置（mock、无密钥、语音关闭）
```

## 许可证

MIT License。详见 `LICENSE`。
