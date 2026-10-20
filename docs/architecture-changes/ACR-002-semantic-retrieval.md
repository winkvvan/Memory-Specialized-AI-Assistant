# ACR-002

## Problem

7.3/7.4 的 Memory Recall 依赖关键词/字符重叠。"AI 项目" 与 "开发 Chris" 表面词汇不一致时无法命中；
需要语义检索（Embedding），但必须与 Memory Ecology 解耦，且不能成为运行依赖。

## Current Architecture

- Memory：`MemoryEcology`（生命周期/衰减/归档）+ `query()` 关键词检索 + 7.3 Ranking。
- Provider：`LLMProvider`（chris/llm）+ factory；无独立 Embedding 能力。
- 架构边界：业务代码禁止直接 import requests / 具体 Provider（guardrail 测试 + safety 脚本）。

## Proposed Change

- 新增 `chris/embedding/`（独立子系统的接口 + 实现）：
  - `EmbeddingProvider`（embed / embed_batch / health_check），与 LLM Provider 完全分开。
  - `MockEmbeddingProvider`（离线、无 Key、确定性）+ `RemoteEmbeddingProvider`（OpenAI 兼容，放
    `chris/embedding/adapters/` 适配器目录，与 `chris/llm/` 同类的 HTTP 适配器豁免）。
  - `EmbeddingProviderFactory`；`EmbeddingCache`（JSON 独立文件、content_hash、model/dimension 身份、stale）。
  - `cosine_similarity`（[0,1]，空向量/维数不一致/畸形安全处理）。
- 新增 `chris/retrieval/`：`KeywordRetriever`、`SemanticRetriever`（lazy 生成，只找候选）、
  `HybridRetriever`（semantic ∪ keyword ∪ recent → 统一 Ranking）、`RetrievalGateway`（业务唯一入口 + fallback）。
- `MemoryConfig` 增加 additive 字段（retrieval/embedding 配置，默认 mock，不改衰减/强化默认值）。
- `build_runtime` 装配 `Runtime.retrieval`；`build_system_prompt` 走 hybrid 检索。
- CLI：`/memory search`、`/memory embeddings`、`/memory embedding rebuild`。

## Impact

- 受保护模块：`chris/memory/ecology.py`（仅 MemoryConfig additive 字段）；`chris/app.py` 非受保护。
- 新增目录 `chris/embedding/`、`chris/retrieval/`；`chris/embedding/adapters/` 为 HTTP 适配器目录，
  加入 guardrail 豁免（与 chris/llm/ 一致）。

## Compatibility

- 向后兼容：既有 `query()/search()` 行为不变；未配置 embedding 时 `Runtime.retrieval` 仍构建（默认 mock），
  provider 故障自动回退 keyword。
- Memory 本体与 Embedding Cache 分离：换模型只标记旧向量 stale，不触碰记忆。

## Data Migration

- 新增独立缓存文件 `memory.embeddings.json`；memory.json schema 不变；无迁移。

## Provider Independence

- Embedding 独立于 LLM Provider；LLM/Embedding 均可替换；业务层只依赖接口。
- Embedding 不拥有 Memory、不强化、不自动 reactivate 归档。

## Testing

- 21 个新测试（test_memory_75.py）：provider/cache/hash/维数/cosine/语义/关键词/hybrid/排序/访问≠强化/
  归档/故障回退/重启/换模型/性能 100/1000/5000。

## Rollback

- 回滚 = 移除 retrieval 装配与新目录；`build_system_prompt` 退回 `memory.query`；additive 字段无影响。

## Status

IMPLEMENTED
