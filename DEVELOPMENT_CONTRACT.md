# DEVELOPMENT_CONTRACT.md — Chris 长期开发原则

> 定义 Chris 的长期开发原则。任何 Agent 在任何阶段都必须遵守。

## Principle 1 — Chris is not the LLM.

Chris 不是某个大语言模型。LLM 只是推理引擎。人格、记忆、时间、世界状态、行为决策
都不能绑定到任何具体模型。

## Principle 2 — State is independent from inference.

人格/记忆/时间/世界状态以结构化数据持久化，独立于任何一次推理。Prompt 只是运行时投影，
不是存储本身。

## Principle 3 — Models are replaceable.

更换 Qwen / DeepSeek / 本地模型 / API 模型不得破坏人格、记忆、时间、世界、行为状态。
业务层只通过 `LLMProvider` 接口调用模型。

## Principle 4 — Development agents are replaceable.

任何开发 Agent 都必须先读 AGENTS.md / DEVELOPMENT_CONTRACT.md / ARCHITECTURE.md /
ROADMAP.md / CHANGELOG.md / docs/PROJECT_STATE.md，再开始工作。知识不依赖聊天上下文。

## Principle 5 — Tests protect previous behavior.

测试是行为的保护网。禁止删除或弱化测试。修改前记录测试基线，修改后保证
`new_tests >= old_tests` 且 `failures = 0`。

## Principle 6 — Data is more valuable than temporary inference.

运行时数据（记忆、人格、时间、世界、备份）比单次推理更宝贵。禁止擅自删除或覆盖旧数据；
Schema 变更必须走迁移并在 `data/backups/` 留备份。

## Principle 7 — Architecture changes require explicit decisions.

修改受保护模块公共接口前，必须提出 Architecture Change Request（ACR），未经用户批准不实施。
禁止"顺手重构"。
