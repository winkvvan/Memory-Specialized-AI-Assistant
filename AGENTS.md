# AGENTS.md — 记忆特化型 AI 助手开发契约

> 任何参与本项目的 Agent（OpenCode / Cursor / 其他）都必须首先阅读本文件。
> 本项目的用户可见名称是“记忆特化型 AI 助手”，Python 代码包名为 `chris`。
> 它不是某一个 Agent 的项目，也不是某一个模型的项目，而是一个独立的软件系统。

## 项目身份

- **LLM = 推理引擎**。Chris = 独立于 LLM 的长期状态与行为系统。
- 人格、记忆、时间、世界状态不能绑定到任何具体模型。
- 业务代码禁止直接 import 具体 Provider / `requests`，必须走 `LLMProvider` 接口。

## 架构受保护模块（ARCHITECTURE PROTECTED）

默认禁止重构或改动公共接口：

```
chris/personality/
chris/memory/
chris/temporal/
chris/world/
chris/behavior/
chris/llm/
chris/events.py
```

如果必须修改公共接口：必须先提出 Architecture Change Request（见 `docs/architecture-changes/`），
未经用户批准不得实施。

## 修改前必须执行的流程

```
1. 阅读 AGENTS.md
2. 阅读 docs/HANDOFF.md（命令/环境/数据/验证清单）
3. 阅读 DEVELOPMENT_CONTRACT.md
4. 阅读 ARCHITECTURE.md
5. 阅读 ROADMAP.md
6. 阅读 CHANGELOG.md / docs/PROJECT_STATE.md
7. git status + git branch
8. 运行 python -m unittest discover，记录测试基线
```

例：`Tests before change: 237 passed`

## 修改原则

- **最小修改原则**：优先 small patch，不擅自大重构。
- **禁止删除/弱化测试**：不得为了让测试通过而删测试、把 assert 改 pass、降低测试要求。
- **禁止擅自改 Schema**：旧 Schema → Migration → 新 Schema，不直接覆盖旧数据。
- **禁止擅自删除数据**：未经用户明确允许，不删除 data/、记忆、人格、时间、世界数据。
- **禁止绕过 Provider / EventBus**：模型调用必须走 `LLMProvider`；既定跨模块通信走 EventBus。
- **禁止擅自引入大型依赖**（向量库 / Embedding / Agent 框架 / 大型 UI 框架）：如确需，说明
  依赖名称、用途、为什么现有方案不足、维护成本、体积、许可证。
- **修改测试来适应实现**是禁止的：先确认设计是否真的发生了变化。

## 任务类型判定

每次任务属于：`FEATURE | BUGFIX | REFACTOR | TEST | DOCS | ARCHITECTURE`。
如果属于 `ARCHITECTURE`，先停止并提出 ACR，不实施。

## 完成后的固定报告格式

每次修改完成，必须输出：

```
## Task
## Changed Files
## Architecture Impact    (NONE / LOW / MEDIUM / HIGH)
## Data Impact            (NONE / LOW / MEDIUM / HIGH)
## Tests Before
## Tests After
## New Tests
## Git Status
## Known Risks
## Next Step              (WAITING FOR USER)
```

## Git 规则

- 每阶段完成提交，Commit message：`phase-X.Y: short description`（本类护栏用 `phase-guardrail: ...`）。
- 禁止无意义 commit message（`update` / `fix` / `test` / `final`）。
- **禁止自动 push**，除非用户明确要求。

## 数据保护

- `data/`（运行时数据 + 备份）、`.env`、`*.key`、`*.secret`、`secrets/` 已在 `.gitignore`。
- 敏感信息（API Key / Token / Password / Secret）不写入源码、文档、测试；用环境变量。
- 不要在提交历史中泄露真实 Key；如已泄露，只报告，不擅自做 history rewrite。

## 完成阶段后

停止。不擅自进入下一阶段，不"顺手"实现 Embedding / RAG / Vision 等未要求功能。
