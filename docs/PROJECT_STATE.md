# PROJECT STATE

## Version

0.1.0 — 通用发布版（代码包名 `chris`，用户可见名称“记忆特化型 AI 助手”）。

## Interfaces

- LLM: `chris/llm/base.py::LLMProvider`，API 适配 `chris/llm/openai_compat.py`
- ASR: `chris/voice/asr.py::ASRProvider`，API 适配 `ApiASRProvider`
- TTS: `chris/voice/tts.py::TTSProvider`，API 适配 `ApiTTSProvider`
- Audio input/output: `chris/voice/audio.py`
- Chat pipeline: `chris/voice/pipeline.py`

## Default Configuration

- `llm.provider = mock`
- `voice.enabled = false`
- `tools.web_search.enabled = false`
- `tools.computer.enabled = false`
- `memory.embedding.provider = mock`
- API Key 只从环境变量读取

## Verification

发布前应在 Python 3.11+ 环境运行：

```powershell
python -m unittest discover
python scripts/check_project_safety.py
```

## Known Risks

- 默认无真实模型 Key 时只能使用 mock
- Windows 音频后端需要在真实设备上验收
- JSON 存储在记忆规模很大时需要迁移到数据库
- GUI 权限确认仍以事件/文本为主

## Data Boundary

`data/`、`.env`、模型、音频、密钥和个人配置均不进入版本库。