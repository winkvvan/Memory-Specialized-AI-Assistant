# Changelog

## 0.1.0 — 通用发布版

### Added
- 主脑 `LLMProvider` API 适配：`provider: api` / `openai` / `openai_compat`
- 语音识别 API 适配：OpenAI-compatible `POST /audio/transcriptions`
- 语音合成 API 适配：OpenAI-compatible `POST /audio/speech`
- 通用安全默认配置：mock 主脑、语音关闭、无个人路径和密钥
- 通用人格与记忆模板：`templates/personality.json`、`templates/memory.json`
- 首次运行初始化：`chris/bootstrap.py`，仅在状态文件缺失时复制模板，不覆盖已有数据
- README 增加人格、记忆、备份、恢复、数据路径和常用操作说明
- 可选 MCP 依赖：`pip install -e ".[mcp]"`

### Changed
- 用户可见名称统一为“Memory-Specialized AI Assistant”
- 默认人格和记忆改为可编辑的通用模板，不再依赖测试对话或测试偏好
- 默认时区改为 UTC
- Web 搜索和电脑操作默认关闭
- 发布副本移除个人测试日志、规划草稿、API 测试文件、模型、音频和本机路径

### Preserved
- 记忆生态、人格、时间、世界状态、工具系统、权限确认和桌面界面
- Phase 9.3.7 的语音状态机与 Windows 音频实现
