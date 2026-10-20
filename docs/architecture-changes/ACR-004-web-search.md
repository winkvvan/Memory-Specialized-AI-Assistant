# ACR-004

## Problem

Phase 8.2 需要让 Chris 具备正式的 Web Search Tool：用户问"今天 AI 有什么新闻？"时，
Chris 能通过 LLM → ToolCall → ToolRegistry → ToolExecutor → web_search → SearchProvider
→ 结构化结果 → 回喂 LLM → 最终回答。当前项目没有任何搜索能力，且没有可用的搜索 API Key。

## Current Architecture

- Phase 8.1 Tool Core 已就绪：`chris/tools/`（models/base/registry/validation/permission/
  executor/adapter/builtin/pipeline）。Tool 必须经 Registry 注册、Executor 执行、Permission 门控。
- 业务代码禁止 import `requests`（guardrail：`chris/llm/`、`chris/embedding/adapters/` 为
  HTTP 适配器豁免目录）。
- 现有搜索相关：`mcp_server/`（独立外部 Zhipu 视觉 MCP 工具，无搜索能力）；`.env` 仅有
  `ZHIPU_API_KEY`。无搜索 Provider / Key。

## Proposed Change

- 新增 `chris/tools/search/`（非 HTTP 业务层）：
  - `base.py`：`SearchProvider` ABC（`search(query, *, max_results, freshness, domains,
    timeout) -> SearchResult`，同步、与核心架构一致）、`SearchError` + `SearchErrorCode`
    （invalid_request / authentication_error / rate_limited / timeout / provider_unavailable /
    parse_error / unknown）、`SearchItem`（title/url/snippet/source/published_at）、
    `SearchResult`（query/results/provider/raw_response + `to_text()` 投影）。
  - `factory.py`：`create_search_provider(config)` 注册表；按配置选 Provider，未知名字 loud fail。
  - `mock.py`：`MockSearchProvider`（确定性、无网络，供测试/离线）。
- 新增 `chris/tools/search/adapters/`（HTTP 适配器豁免目录，与 llm/embedding 同类）：
  - `duckduckgo.py`：`DuckDuckGoSearchProvider`——keyless、合法稳定、无新依赖（stdlib
    HTMLParser 解析 `html.duckduckgo.com/html/`），raw HTML 只进 `raw_response`（调试用），
    vendor 错误统一映射为 `SearchError`。
- 新增 `chris/tools/web_search.py`：`WebSearchTool`（Tool）：
  - `name=web_search`，`permission=READ_ONLY`，`risk_level=READ_ONLY`（搜索不需逐次确认，
    但必须过 Permission Policy）。
  - `input_schema`：`query`（必填、非空、≤500 字符）+ `max_results`（1-20，默认 5）。
  - 执行：校验 query → provider.search → 按归一化 URL 去重（+ 轻量标题去重）→ snippet
    截断（`snippet_max_chars`，默认 300，末尾加 …）→ 结构化 `SearchResult.to_text()` 作为
    ToolResult.content；`raw_response` 不进入 content。失败 → `ToolResult.failure` + 友好
    错误文本（+ metadata.error_code）。
  - 不自动打开任何结果、不写永久 Memory、不返回 raw vendor JSON。
- 装配：`chris/app.py` `_build_tool_system` 读取 `tools.web_search` 配置节注册该 Tool；
  默认 `provider=duckduckgo`（keyless）。Config 用现有 `config.json`/`Config.tools` 体系。
- Guardrail 豁免：`chris/tools/search/adapters/` 加入 `tests/test_architecture_boundaries.py`
  与 `scripts/check_project_safety.py` 的 HTTP 适配器豁免（沿用 ACR-002 先例）。

## Impact

- 新增 `chris/tools/search/`、`chris/tools/web_search.py`；修改 `chris/app.py`、config.json、
  guardrail 两处（仅追加豁免路径）。未改动受保护模块公共接口；不触碰 `chris/llm/`、
  `chris/events.py`、`chris/temporal/`、`chris/memory/`。
- `mcp_server/`（视觉）保持独立，不迁移。

## Compatibility

- Tool Core 完全复用，无重构。web_search 默认注册但仅在 LLM 发出对应 ToolCall 时执行，
  不影响无 Tool 路径的普通对话（pipeline 8.1 已保证：无 tool_calls 时行为与旧版一致）。
- 搜索 Provider 可替换：新增 Provider 只需在 `factory.py` 注册 + config 改名。

## Data Migration

- 无数据迁移。搜索结果不写入永久 Memory（仅当前会话上下文 + ToolResult metadata）。

## Provider Independence

- Core 只依赖 `SearchProvider` 接口与结构化 `SearchItem/SearchResult`；vendor raw response
  隔离在 Provider 内部。更换搜索服务不改变 Chris Core。

## Testing

- 新增 `tests/test_web_search.py`（32 个）：normalize_url/deduplicate、MockSearchProvider、
  factory、DDG adapter（mock HTTP：成功/超时/429/403/解析失败）、WebSearchTool（合法/空 query/
  过长/超长/max_results/snippet 截断/去重/Provider 错误映射/raw 不泄漏/definition schema）、
  Tool Core 集成（Registry→Executor→Permission→events）、完整 LLM→ToolCall→search→ToolResult→
  LLM 循环（假 LLM）。

## Rollback

- 删除 `chris/tools/search/` + `web_search.py`，`_build_tool_system` 移除注册，撤回两处
  guardrail 豁免，移除 config 节。其余不受影响。

## Status

APPROVED（Phase 8.2 指令授权实施）
