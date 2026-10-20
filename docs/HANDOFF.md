# HANDOFF

## Environment

- Python 3.11+
- `pip install -e .`
- MCP 可选：`pip install -e ".[mcp]"`

## Run

```powershell
python -m chris
python -m chris --health
python launcher\launcher.py
```

## Configuration

- `config.json` 是通用默认配置，可安全提交
- 环境变量优先于配置中的非敏感选项
- API Key 使用 `CHRIS_API_KEY` 或各适配器配置的 `api_key_env`

## Main Interfaces

- 主脑：`LLMProvider`
- 语音识别：`ASRProvider`
- 语音合成：`TTSProvider`
- 音频输入/输出：`AudioInputProvider` / `AudioOutputProvider`

## Verification

```powershell
python -m unittest discover
python scripts/check_project_safety.py
```

## Safety

- 不提交 `data/`
- 不提交密钥、模型、个人音频
- 不直接覆盖旧数据；Schema 变更必须迁移并备份
- 工具执行默认遵循权限策略