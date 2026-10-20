# Memory-Specialized AI Assistant 架构文档

> Python 代码包名保留为 `chris`，用户可见名称统一为“Memory-Specialized AI Assistant”。

> 所有重要设计决策都在本文件记录。修改架构前先读本文档，禁止盲目重构。

## 1. 目标

Chris 是一个长期运行、可更换底层模型、具有独立人格、记忆、时间状态和行为决策能力的个人 AI Agent 系统。

**首要约束**：LLM 只是推理引擎。人格、记忆、时间、行为状态必须独立于具体 LLM，未来更换
Qwen / DeepSeek / 本地模型 / API 模型时不得破坏它们。

## 2. 总体架构

```
                    ┌────────────────────────────┐
                    │           CLI / 未来界面      │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │   EventBus (chris/events)   │
                    │  所有重要变化都发 Event       │
                    └───┬─────┬─────┬─────┬──────┘
                        │     │     │     │
            ┌───────────▼─┐ ┌─▼───────────┐ ...
            │ Personality │ │   Memory     │
            │ Temporal    │ │   World      │
            │ Behavior    │ │   (订阅/产生事件)│
            └──────┬──────┘ └─────────────┘
                   │
            ┌──────▼──────────────────────┐
            │  LLMProvider (chris/llm/base) │
            │  generate / stream / health   │
            └───────────┬──────────────────┘
                        │
                MockProvider (未来: Ollama/OpenAI/Qwen/DeepSeek...)
```

数据流方向：界面 → 事件总线 → 各状态模块更新 → 行为引擎决策 → 渲染最小 System Prompt →
`LLMProvider` 生成回复 → 回复事件与记忆写入。

## 3. 模块职责

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 配置 | `chris/config.py` | 加载/保存 `config.json`，相对路径统一解析到项目根 |
| 日志 | `chris/logging_setup.py` | 控制台 + 文件日志，防止重复注册 handler |
| 事件 | `chris/events.py` | `Event`（id/timestamp/type/source/payload）与同步 `EventBus`（支持类型过滤与通配订阅） |
| LLM | `chris/llm/base.py` | `LLMProvider` 抽象接口 |
| LLM | `chris/llm/mock.py` | 确定性 `MockProvider`（脚本回复/回显），用于测试与离线开发 |
| LLM | `chris/llm/openai_compat.py` | `OpenAICompatProvider`（通用 OpenAI 兼容 HTTP）+ 厂商子类（DeepSeek / Qwen / Moonshot / GLM） |
| LLM | `chris/llm/factory.py` | 按名称注册与创建 Provider，业务层禁止直接 import 具体实现 |
| 人格 | `chris/personality/models.py` | `CorePersonality` / `AdaptivePersonality` / 变更记录，结构化可序列化 |
| 人格 | `chris/personality/evolution.py` | 演化验证层：证据/置信度/时间窗规则，批准或拒绝 |
| 人格 | `chris/personality/store.py` | `VersionedPersonalityStore`：版本化快照、pending 证据、回滚、v1 迁移 |
| 记忆 | `chris/memory/base.py` | `MemoryEntry` 与 `MemorySystem` 接口 |
| 记忆 | `chris/memory/simple.py` | JSON 文件存储，关键词检索，发射 `MEMORY_CREATED` |
| 时间 | `chris/temporal/system.py` | `known_since`、会话边界、空闲时间、时间阈值（`TIME_THRESHOLD_REACHED`），时钟可注入以便测试 |
| 世界 | `chris/world/state.py` | 环境快照，更新时发射 `WORLD_STATE_CHANGED` |
| 行为 | `chris/behavior/engine.py` | `BehaviorEngine` 接口 + 阶段一规则引擎，动作限定为 `NO_ACTION/RESPOND/SUGGEST/REMIND` |
| 组装 | `chris/app.py` | 组合根 `build_runtime()`，`build_system_prompt()` 从结构化状态渲染最小 Prompt |
| CLI | `chris/cli.py` | 交互式对话循环，`/health` 命令与 `--health` 探活参数 |

## 4. 目录结构

### 4.1 首次启动状态模板

仓库只提交 `templates/` 中的通用人格和空记忆模板。`build_runtime()` 在
`cfg.test_mode == false` 时调用 `chris/bootstrap.py::ensure_default_state_files()`：
仅为项目 `data/` 下缺失的 `personality_file` 与 `memory_file` 复制模板。
已有文件不覆盖；外部自定义数据路径由用户自行复制模板。该层只做文件初始化，
不改变人格或记忆的公共接口。

```
chris/
├── __init__.py        # 包说明与版本
├── __main__.py        # python -m chris 入口
├── app.py             # 组合根
├── cli.py             # CLI 对话循环
├── config.py          # 配置系统
├── events.py          # 事件系统
├── logging_setup.py   # 日志
├── llm/               # LLM Provider 层
│   ├── base.py        #   接口
│   ├── mock.py        #   模拟实现
│   ├── openai_compat.py  #   OpenAI 兼容 HTTP 实现 + DeepSeek 子类
│   └── factory.py     #   注册表/工厂
| 人格 | `chris/personality/` | 人格系统 |
| | `models.py` | 数据结构（含 `PersonalityVersion`） |
| | `evolution.py` | 演化验证层 |
| | `store.py` | 版本化持久化/回滚 |
├── memory/            # 记忆系统
│   ├── base.py        #   接口
│   └── simple.py      #   简单文件实现
├── temporal/          # 时间系统
│   └── system.py
├── world/             # 世界状态
│   └── state.py
└── behavior/          # 行为引擎
    └── engine.py
tests/                 # unittest 测试（零第三方依赖）
templates/             # 通用人格/记忆模板，首次运行复制到 data/
config.json            # 运行配置
data/                  # 运行时状态（自动生成，gitignore）
```

## 5. LLM Provider 契约

业务层只能调用：

```python
LLMProvider.generate(messages, **kwargs) -> str
LLMProvider.stream(messages, **kwargs) -> Iterator[str]
LLMProvider.health_check() -> bool
```

- `messages` 为 `{"role", "content"}` 列表，与主流 Chat API 对齐。
- 新增 Provider：实现 `LLMProvider`，在 `factory.py` 注册，配置里改名字即可。
- 更换模型**不需要**修改人格、记忆、时间、世界、行为模块。

**API 接入方式**：DeepSeek 等国内厂商均提供 OpenAI 兼容的 chat-completions 接口，因此
`OpenAICompatProvider` 一个类即可覆盖各家；具体厂商通过子类只改默认值（DeepSeek / Qwen /
Moonshot / GLM，各带自己的 base_url、默认模型与 Key 环境变量）。API Key 从对应环境变量
（`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY` / `ZHIPU_API_KEY`，均可回退到
`CHRIS_API_KEY`）读取，绝不写入代码或配置文件。

## 6. 事件系统

`Event` 必含：`event_id`、`timestamp`、`type`、`source`、`payload`。不绑定任何具体 LLM。

阶段一已定义类型（`chris/events.EventType`）：

`SESSION_STARTED` `SESSION_ENDED` `USER_MESSAGE` `CHRIS_MESSAGE`
`ACTIVITY_STARTED` `ACTIVITY_STOPPED` `TIME_THRESHOLD_REACHED`
`MEMORY_CREATED` `MEMORY_UPDATED` `PERSONALITY_CHANGED` `WORLD_STATE_CHANGED`

`EventBus` 是同步发布订阅，支持按类型过滤与通配订阅，阶段一不做持久化（历史事件未来由
Event Log 负责）。

## 7. 人格系统设计

- 数据模型（`chris/personality/models.py`）由五部分构成：
  - `core`：`CorePersonality`（name/identity/core_traits/values/boundaries + 兼容旧字段）——AI 不可修改
  - `communication_style` / `relationship` / `behavioral_preferences`：人格基线区块，非演化目标
  - `adaptive`：`AdaptivePersonality`（preferences 字典）——唯一可演化部分
  - `evolution_policy`：`EvolutionPolicy`——数据驱动的演化规则，`config_from_policy()` 映射为
    `EvolutionConfig`（minimum_evidence、禁止单次事件突变等），不硬编码进演化逻辑
- 所有人格变化必须生成 `PersonalityChangeRecord`（时间/原因/证据/修改前/修改后/置信度）。
- **演化安全层**（`chris/personality/evolution.py`）：`EvolutionValidator` 按确定性规则
  决定批准/拒绝（`PersonalityChangeProposal` 生命周期 PENDING→APPROVED/REJECTED）：
  1. 是否修改 Core Personality（`identity`/`core_traits`/`values`/`boundaries`/旧字段全拒）
  2. 证据是否足够（`min_evidence`，默认 3）
  3. 是否重复行为模式（所有观测的 `after` 一致）
  4. 是否与近期行为冲突（一致性检查即"继续观察"机制，冲突即拒绝）
  5. 变化幅度是否过大（数值变化 delta <= `max_change_delta`，默认 0.2；如 0.50→0.55 可，
     0.50→0.20 拒）
  6. 是否一次性异常反馈（证据时间跨度 >= `min_window_days`，默认 7 天）
  全部通过才 `APPROVED`。单次对话永远不可能触发人格突变。
- **版本化**（`chris/personality/store.py` 的 `VersionedPersonalityStore`）：
  history 为**只追加**；每次批准追加新快照（v1 -> v2 -> v3）并把 current_version 指针移到新版本。
  `rollback(to_version)` 只移动指针、**不删除历史**，并写入 `RollbackEvent`（from/to/timestamp）；
  被放弃版本的 change 状态置为 `ROLLED_BACK`。所有被评估的提案（含被拒）进 `proposals` 日志，
  保证可追溯。文件结构：
  `{core, communication_style, relationship, behavioral_preferences, evolution_policy, current_version, history[], pending[], proposals[], rollbacks[]}`。
- 证据先进入 `pending`（`propose()`，status=PENDING），`run_evolution()` 统一评估后消费；
  被拒提案记入 proposal 日志（保留 decision_reason），继续观察需重新积累。
- **基线数据**：`data/personality.json` 的 v1 即用户提供的人格基线；`default_personality()`
  承载同款默认值作为无文件时的回退。
- **LLM 无写权限**：模型只收到文本消息，`personality.json` 唯一写入路径是用户命令
  （`/adjust` / `/evolve` / `/rollback`）与人工编辑，模型没有任何工具可触达该文件。
- **注意**：`build_system_prompt()` 渲染的是结构化状态的"投影"，Prompt 只是运行时视图，
  不是存储本身。

## 8. 记忆系统（Memory Ecology 2.0）

**本阶段最重要的设计原则：Memory Life Score is not Memory Retention（记忆生命值 ≠ 记忆是否保存）。**

```
life_score   -> 这条记忆现在有多活跃   (0..100)
importance   -> 值不值得长期保存       (0..1)
confidence   -> Chris 有多相信它        (0..1)
storage_tier -> 存在哪一层              (ACTIVE/DORMANT/ARCHIVE)
GC           -> 是否已无保存必要（多条件严格判定）
```

生命周期：BORN → ACTIVE → REINFORCED → STABLE → WEAKENING → DORMANT → ARCHIVED；
DORMANT/ARCHIVED 被重新提及时 → RECOVERED → ACTIVE；冲突 → SUPERSEDED；确认错误 → INVALIDATED。
生命值低 ≠ 删除（生日记忆 importance 0.99 即使 life=1 也永不被 GC）；生命值高 ≠ 当前真实
（旧记忆 life=90 也可被新事实 SUPERSEDED）。

- **分层存储**：Active（高频/进 Prompt/数量受 `active_limit` 控制）、Dormant（低频/不进普通
  Prompt/可唤醒）、Archive（冷存储于独立 `*.archive.json` 文件，低运行成本）。压缩与 GC 在
  Archive 层内批量执行。
- **强化**：`reinforce()`，收益递减（`reinforce_diminish`=0.85）+ 上限 `reinforce_cap`=90；单纯检索
  （`touch()`）只更新 last_used，不无限加生命值。
- **主动记住（最高优先级）**：用户在对话中说"记住 X / 别忘了 X"（`app.extract_remember()` 识别），
  自动创建 `remember=True` 记忆 → source=EXPLICIT_USER、importance=0.99、life≥70、confidence=0.98。
- **类型调参**（已按实验调优）：PREFERENCE 不易遗忘（half_life 180 天、initial 50、importance 0.7）——
  每周提及可长期保持 STABLE/ACTIVE；EVENT（行为/事件）相对不重要（importance 0.15、half_life 14 天、
  initial 25），易衰减、GC 可回收。全部参数在 `MemoryConfig`（可被 config.json `memory` 节覆盖，
  嵌套 dict 采用合并语义）。
- **衰减**：Memory 不直接读系统时钟，全部经 Temporal API
  （`temporal.calculate_time_factor(last_decay_at, half_life)`）；不同类型有不同半衰期与衰减
  间隔（全部在 `MemoryConfig` 中，不硬编码）。
- **恢复**：Dormant/Archived 被重新强化 → RECOVERED → ACTIVE，不产生重复记忆。
- **冲突**：`supersede(old, new)` 保留历史关系（superseded_by / supersedes / contradiction_count），
  旧记忆 → SUPERSEDED。
- **压缩**：`compress()` 把 Archive 中同主题（metadata.topic）历史合并为一条压缩记忆，保留
  `compressed_from` 映射，原记忆保留（`compressed_into` 标注），非简单删除。
- **GC**：`gc()` 多条件严格判定才永久删除——importance≤阈值、life≤阈值、长期未用且未确认、
  非身份/关系类型、不被其它记忆引用。每次 GC 产生 `MEMORY_GARBAGE_COLLECTED` 事件（含 reason
  与 previous_state），可审计"这条记忆为何最终死亡"。
- **检索评分**：`relevance × life_factor × confidence × importance × recency × storage_factor`；
  Archive 可被高相关查询唤醒，不会完全不可检索。
- **Prompt 投影**：只提供高相关/高置信/当前活跃的少量记忆；Dormant 不进普通 Prompt；Archive 默认不进。
- **Temporal 集成**：监听 `TIME_PASSED/DAY_CHANGED/WEEK_CHANGED/MONTH_CHANGED` → `process_due()`
  增量处理到期记忆，绝不每秒全量扫描。
- **事件**：`MEMORY_CREATED/REINFORCED/WEAKENED/DORMANT/RECOVERED/SUPERSEDED/INVALIDATED/
  CONTRADICTED/ARCHIVED/COMPRESSED/GARBAGE_COLLECTED`。
- **统计**：`stats()` 输出各层数量、status 分布、估算存储字节，便于观察长期增长。
- **容量策略**：`active_limit` / `archive_limit` / `compression_threshold` / `gc_enabled`，可配置。
- **CLI**：`/memory list|inspect|reinforce|decay|invalidate|archive|compress|gc|stats`。
- 实现：`chris/memory/ecology.py`（`MemoryEcology`，实现 `MemorySystem` 接口，替换 `SimpleFileMemory`，
  运行时数据仍存 `data/memory.json` + `data/memory.archive.json`）。

## 9. 时间系统（Temporal Engine 2.0）

Temporal 是底层基础设施：Personality / Memory / Behavior 都依赖它，它不依赖任何 LLM。

- **Clock 抽象层**（`chris/temporal/clock.py`）：业务代码禁止直接 `datetime.now()`。
  - `RealClock`：1 现实秒 = 1 模拟秒（正式运行）
  - `AcceleratedClock(scale)`：1 现实秒 = scale 模拟秒（如 3600 = 1s→1h），用于长时间测试
  - `ManualClock`：手动推进（`advance()`），用于模拟与自动化测试
  - `create_clock(mode, scale)` 工厂；`TemporalSystem.switch_clock()` 运行中切换
- **Time State**：`known_since` / `last_interaction_time` / `session_start_time` /
  `last_shutdown_time` / `total_runtime` / `timezone` / 最近日期边界，持久化到 `data/temporal.json`
- **连续时间轴**：`checkpoint()` 是时间推进引擎——把时间轴推进到当前时钟时间，检测并生成事件。
  会话/交互/手动推进/心跳都会触发。
- **分层检测**：Level 1 Tick(1s)→TIME_TICK；Level 2 Min(60s)→IDLE_STARTED/ENDED；
  Level 3 Hour→LONG_IDLE/LONG_SESSION；Level 4/5/6→DAY_CHANGED/WEEK_CHANGED/MONTH_CHANGED。
- **离线补偿**：启动时若 `now - last_shutdown > 0` 生成 `TIME_SKIPPED`；≥3 天再生成 `LONG_ABSENCE`；
  日期边界跨过生成 DAY/WEEK/MONTH_CHANGED。Chris 不用真跑一个月也知道"一个月过去了"。
- **心跳**：`start_ticker(interval)` 起 daemon 线程每秒 checkpoint + TIME_TICK；`stop_ticker()` 停止。
- **Memory Ecology 接口**（未来记忆生命周期用）：`get_age()` / `get_elapsed()` /
  `calculate_time_factor(created_at, half_life)`（0.5^(age/half_life) 衰减因子）。Temporal 只管
  提供时间能力，不管记忆怎么变化。
- **Prompt 投影**：`prompt_summary()` 只给最小信息（当前时间/距上次交流/会话时长），不泄露完整 JSON。
- **事件结构**：payload 含 `previous`/`current` 状态（如 DAY_CHANGED 的日期前后）。

事件：`TIME_TICK TIME_PASSED TIME_SKIPPED SESSION_STARTED SESSION_ENDED IDLE_STARTED IDLE_ENDED
LONG_IDLE LONG_SESSION LONG_ABSENCE DAY_CHANGED WEEK_CHANGED MONTH_CHANGED`（+ 原有
`TIME_THRESHOLD_REACHED`）。

CLI：`/time`、`/time events`、`/time advance <n>[smhdw]`、`/time mode <mode>`。

## 10. 世界感知（World Awareness 2.0）

**本阶段设计原则（已记录）**：
> **World Awareness observes the world; it does not own Chris's interpretation of the world.**
> **Sensor 是数据源，World State 是事实层，Behavior 是决策层，LLM 是推理引擎。**

**UI 表现层（7.1）设计原则**：
> **The UI is Chris's body, not her mind.**
> Personality=Chris是谁、Memory=记住什么、Temporal=如何体验连续性、World Awareness=知道什么、
> Behavior=决定做什么、LLM=如何推理、UI=如何呈现在用户面前。
> UI 只负责显示/输入/交互/状态可视化；所有状态经 Application/ViewModel 从 Core 读取，绝不直接改
> personality.json/memory.json/world.json，也绝不直接调 Provider。

**Memory 2.0（7.2）设计原则**：
> **Memory is not a database of everything Chris has seen. It is a living ecology of experiences,
> facts, preferences, relationships, and knowledge whose relevance changes over time.**
> 记忆不是 Chris 见过的一切的数据库，而是会随时间、访问、强化、遗忘和重新激活而变化的记忆生态。
> Importance=这件事有多重要；Vitality=现在有多活跃；Confidence=有多确定；Recency=多久前；
> Access=想起过多少次；Provenance=为什么知道。这些概念不得混为一谈。

**Memory 7.3（生命周期强化）**：
- **strength**（=vitality=life_score/100）与 importance 严格分离；strength 随时间/访问/强化变化，importance 是固有价值
- **MemoryDecayPolicy**：独立策略对象，类型化半衰期（episodic 快 / semantic 慢 / preference 很慢 / relationship 极慢），参数在 `config.json` `memory.decay`，**lazy decay**——只在访问/检索/维护/归档时按真实时间戳重算，绝不每秒遍历
- **access ≠ reinforcement**：普通检索只更新 access_count/last_accessed_at（并应用 lazy decay），不强化；显式再提及/确认才 `reinforce()` 提升 strength（有上限+递减收益）
- **MemoryOperationProposal + MemoryPolicy**：LLM/其它模块只能"提建议"（CREATE/ACCESS/REINFORCE/MERGE/DECAY/ARCHIVE/REACTIVATE），由 MemoryPolicy 验证、MemoryEcology 执行——LLM 不能直接改记忆
- **MemoryMaintenance**：批量 lazy decay + 归档阈值评估，默认 5 分钟间隔（`maintenance_interval_seconds`，可配），由 Temporal 节奏驱动，不每秒执行
- **PatternCandidate**：World→Behavioral Pattern 的预留接口（本阶段不实现行为学习）
- CLI：`/memory maintenance`

**Memory 7.4（自动化与 Temporal 集成）**：
- **MemoryMaintenanceScheduler**：Temporal TIME_TICK → O(1) 间隔检查 → 到期才执行维护；CLI `/memory
  maintenance` 与自动路径共用**同一个** `run()`（单一 Service，无两套实现）
- **自动维护**：`build_runtime` 注册 scheduler 订阅 TIME_TICK；维护间隔取 `memory.maintenance_interval_seconds`
  （默认 300s，配置驱动，不写死）；无时间变化时不重复执行（幂等）
- **维护只处理候选**：`_maintenance_candidates` = 到期（next_decay_at≤now）∪ WEAKENING/DORMANT，
  不做无条件全扫描重写；lazy decay 保持
- **容错**：单条坏 Memory 不阻塞整批（逐条 try/except，report.errors 计数）
- **归一化**：clamp strength/importance/confidence、校验 status/type
- **维护元数据持久化**：`last_maintenance_at / next_maintenance_at / maintenance_count` 随 memory.json
  保存，重启恢复
- 事件：`MEMORY_MAINTENANCE_CHECKPOINT`（带 timestamp/reason/report）
- **Reinforcement 重置衰减基准**：`reinforce()` 同时重置 `last_decay_at`，强化后从新基准重新衰减
- **对话 LLM 召回意图（7.4 补完，非关键词）**：`process_recall_intent()` 用一次轻量 LLM 调用判断
  用户是否"明确再次确认"某条旧记忆（→ `MemoryOperationProposal(REINFORCE)` 经 MemoryPolicy 执行，
  归档记忆确认后自动恢复 ACTIVE）以及是否在"询问过去/很久以前"（→ prompt 检索带 `include_archived`）。
  这样 `你知不知道…` 等无关键词说法也能触发。LLM 只提议不直接改；故障安全回退为 no-op；
   普通召回仍只 `access_count++`（Access ≠ Reinforcement）

**Memory 7.5（Semantic Memory Retrieval，解耦）**：
```
                Memory Query
                     │
                     ↓
              Retrieval Gateway          ← 业务层唯一入口，自动 fallback
          ┌──────────┴──────────┐
          ↓                     ↓
 Keyword Retriever        Semantic Retriever
   (子串/字符重叠)          (EmbeddingProvider → cache → cosine)
          └──────────┬──────────┘
                     ↓
              Candidate Set（+recent）
                     ↓
               Hybrid Ranking
        relevance = semantic_weight×sem + keyword_weight×kw
                     ↓
                MemoryContext → LLM
```
- **Embedding 独立能力**：`chris/embedding/`（`EmbeddingProvider` 接口、Mock / Remote 适配器、Factory、
  `EmbeddingCache`、`cosine_similarity`），与 `chris/llm/` 完全分开；业务层只依赖接口
- **Embedding Cache 与 Memory 分离**：独立 JSON 文件（`memory.embeddings.json`），记录含 `content_hash`
  （内容变化→失效重生成）、`model`/`dimension`（换模型→旧向量标记 stale）、created/updated_at（用 Clock，
  但 embedding 不随 Decay 衰减）
- **检索 ≠ 强化**：`SemanticRetriever`/`HybridRetriever` 只找候选并交给 7.3 Ranking；不自动强化、
  不自动 reactivate 归档（归档默认排除，`include_archived` 才纳入）
- **lazy 生成**：首次语义检索时补生成 embedding，不阻塞写记忆；显式 `/memory embedding rebuild`
- **Fallback**：Embedding Provider 故障/无 Key → `keyword fallback`，Chris 照常对话并记录
  `semantic_available=false / fallback=keyword`
- CLI：`/memory search <query>`（semantic/keyword/final 打分 + reason）、`/memory embeddings`、`/memory embedding rebuild`

- **统一 World State Schema**（`chris/world/state.py`）：`user`（presence/activity/attention/estimated_state）、
  `computer`（active/foreground_app/window_title/idle_seconds）、`environment`（location/camera/mic）、
  `activity_started_at`、`last_activity_change`。保留旧字段（location/current_activity/user_presence）兼容。
- **枚举**：Presence=PRESENT/AWAY/UNKNOWN；Activity=IDLE/CODING/BROWSING/WATCHING/GAMING/READING/WRITING/COMMUNICATING/UNKNOWN；
  Attention=FOCUSED/DISTRACTED/RELAXED/UNKNOWN；Busy=BUSY/AVAILABLE/UNKNOWN。
- **Sensor Adapter 接口**（`chris/world/sensors.py`）：`WorldSensor.poll() -> dict`（只给事实+confidence，不解释心理状态）。
  - `MockWorldSensor`：确定性，供测试/模拟
  - `WindowsComputerAdapter`：Win32 读前台窗口/标题/进程/idle（纯 ctypes，失败优雅降级为 inactive）
- **WorldAwareness**（`chris/world/awareness.py`）：`ingest(observation)` → 推断 activity/attention/busy →
  状态变化检测（只在有意义变化时发事件，`CODING→CODING` 不发）→ Activity Session 时间线（连续同活动合并）。
  时间戳全部来自 Temporal（虚拟时钟可驱动）。
- **Busy 推断**：多信号综合（presence + activity + attention + idle），绝不让单个传感器（如鼠标 5 分钟没动）
  直接判定 AWAY——idle 300s + 视频播放器 → WATCHING/RELAXED/UNKNOWN，不误判。
- **World ≠ Memory**：World Awareness 产生 `WORLD_OBSERVATION`，但不把每个观察写成记忆；长期行为模式由
  Memory Ecology 决定是否沉淀为 Memory（本阶段未实现 pattern 提取）。
- **事件**：`WORLD_STATE_CHANGED/WORLD_OBSERVATION/USER_PRESENCE_CHANGED/USER_ACTIVITY_CHANGED/
  USER_ATTENTION_CHANGED/COMPUTER_STATE_CHANGED/FOREGROUND_APP_CHANGED/USER_IDLE_STARTED/USER_IDLE_ENDED`。
- **持久化**：当前状态 `data/world.json`（WORLD_STATE_CHANGED 时保存）；活动会话 `data/world.sessions.json`。
- **性能**：1s 轮询（可配 `world_poll_interval`），事件驱动变化检测，不每次调 LLM、不截图分析。
- **CLI**：`/world`、`/world timeline`、`/world simulate ...`、`/world sensor`。
- **Behavior 接口预留**：`build_behavior_context(runtime)` 提供
  user_state/activity/attention/activity_duration/presence/idle_seconds/foreground_app/time_of_day/
  recent_interaction，供未来主动行为（`INITIATE` 已入枚举但本阶段不实现）。

## 11. 行为引擎

阶段一只做规则：

- 有用户消息 → `RESPOND`
- `proactive_enabled` 且空闲 >= 24h → `REMIND`
- 否则 → `NO_ACTION`

未来扩展点（时间、用户状态、互动频率、偏好、成功率、随机性、长期反馈）已在
`BehaviorEngine.decide(context)` 接口上预留，不破坏现有接口。`BehaviorAction` 已含
`INITIATE`（主动发起），本阶段规则引擎不产生它。

## 11a. Tool Core（Phase 8.1-8.3）

Tool 能力基于同步 Tool Core 建立（详细设计见 ACR-003/004/005）：

```
LLM → Provider Adapter(LLMResponse) → ToolCall → ToolRegistry → Validation
    → Permission → ToolExecutor → ToolResult → 对话上下文 → LLM
```

- `chris/tools/`：ToolDefinition/ToolCall/ToolResult、ToolRegistry、ToolExecutor（lookup→
  validation→permission→timeout→result，串行、`cancel_event` 协作取消）、PermissionPolicy、
  pipeline（LLM↔Tool 循环）。TOOL_CALL_* 事件进现有 EventBus；Temporal 记 Tool activity。
- `chris/tools/search/`（8.2）：SearchProvider 抽象 + Mock + DuckDuckGo adapter（HTTP 适配器
  目录，guardrail 豁免）；`web_search` Tool 返回结构化 SearchResult，raw vendor 数据不泄漏。
- `chris/tools/filesystem/`（8.3）：`WorkspacePathPolicy`（canonical 路径包含性检查，拒绝
  traversal/drive/UNC/symlink escape）+ 9 个 File Tool；写文件原子化；大小/数量/深度限制由
  `tools.filesystem` 配置驱动；MEDIUM/HIGH 工具默认被权限策略拒绝（`confirmation_required`，
  8.6 预留）。文件内容不写永久 Memory。
- `chris/tools/coding/`（8.4）：外部 Coding Agent（OpenCode）的受控 adapter 层。Chris 是
  orchestrator，不复制/fork/重写 coding agent。CodingTask/CodingResult 结构化；OpenCodeAdapter
  是唯一 subprocess 触碰点（参数数组、无 shell、固定 cwd、净化环境、timeout/cancel）；
  CodingExecutionPolicy 做执行 scope 分类（PACKAGE/GIT_WRITE/SYSTEM 默认 `confirmation_required`）
  与环境净化（过滤 api_key/token/secret）；ProjectResolver 复用 8.3 WorkspacePathPolicy；
  后台线程执行不阻塞 GUI；CODING_* 事件进 EventBus；git 只读不自动 push；未安装 → opencode_not_installed。
- `chris/tools/computer/`（8.5）：Computer Action Core——为 Phase 9 Vision 提供执行层。只做**固定
  坐标 action**（move/click/double_click/scroll/key_press/hotkey/type_text/screenshot/screen_info/
  active_window/wait/sequence），**不做语义定位/不做 Vision/不做微信专用逻辑/不做 shell**。
  ComputerBackend 抽象（Fake + 纯 ctypes Win32）；ComputerActionPolicy 做 action→Risk 分类
  （READ_ONLY/LOW 默认放行，MEDIUM/HIGH/CRITICAL 默认 `confirmation_required`）与坐标/key/文本/
  高危快捷键校验；ComputerActionManager 管生命周期/超时/有界 sequence/cancel/事件/Temporal。
  所有 action 必须经 Tool Core（Registry→Executor→Permission→Tool→Manager→Backend），
  Tool/Manager 层绝不直接调 Windows API。type_text 原文与截图字节不进日志/事件/Temporal。
- `chris/tools/confirmation/`（8.6）：Permission Confirmation——把 8.1-8.5 的
  `confirmation_required` seam 升级为真正的确认流程。PermissionDecision 扩展三态
  （ALLOW/DENY/CONFIRMATION_REQUIRED；MEDIUM/HIGH 可确认，CRITICAL 永远 DENY 不可确认）。
  `ConfirmationManager` 维护一次性、TTL 有界、in-memory 的 ConfirmationRequest（重启失效），
  状态机 PENDING→APPROVED→CONSUMED/DENIED/EXPIRED/CANCELLED。`ToolExecutor.execute_approved`
  批准后重新 lookup+validation+permission（TOCTOU 安全），绝不跳过安全检查；敏感参数脱敏。
- `chris/voice/`（9.3）：Voice Pipeline 集成——松耦合接入可选的外部 ASR 与 TTS。
  只依赖抽象 ASRProvider/TTSProvider/AudioInputProvider/AudioOutputProvider，
  不 import/不复制模型。ASR 支持 OpenAI-compatible `/audio/transcriptions` API、本地
  subprocess 命令；TTS 支持 OpenAI-compatible `/audio/speech` API 和 localhost HTTP；
  麦克风用 ctypes waveIn、播放用 winsound。VoicePipeline 状态机
  IDLE→LISTENING→TRANSCRIBING→THINKING→SPEAKING（含 busy/cancel/错误回 IDLE）；
  `launcher/chat.py` 的 `stream_turn` 已接入 Tool Pipeline（GUI 语音/文字都能触发工具 + Permission
  Confirmation），ASR 文本经 `RealVoiceChatRunner` 导入现有 chat_turn。voice 默认 disabled（文字模式不变）。
- 主脑、ASR、TTS 的 API Key 均通过 `api_key_env` 指定的环境变量读取，不写入仓库；
  远程 TTS 只允许 HTTPS，ASR/TTS 都可在 factory 中替换为自建 Provider。
- 关键安全边界：所有 File/Search/Coding/Computer/Voice 工具必须经 ToolRegistry/ToolExecutor/
  Permission/Confirmation；LLM 生成的路径/URL/命令/坐标绝不直接执行；API Key 不落库且不传给
  ASR/TTS 子进程；本地音频不上传；确认是一次性的，不允许 LLM 自证"用户已确认"。

## 12. 设计决策记录 (ADR)

| 编号 | 决策 | 理由 |
| --- | --- | --- |
| ADR-001 | Python 3.11+，仅依赖 `requests` | 可靠、可测试、易部署；HTTP 调用用成熟库，测试层全部 mock 不打网络 |
| ADR-002 | 事件驱动 + 单一 EventBus | 模块间解耦，未来接入主动行为/外部设备不需要改核心 |
| ADR-003 | 核心状态存结构化 JSON，Prompt 只是运行时投影 | 满足"换模型不丢人格与记忆"；不把系统状态塞进 System Prompt |
| ADR-004 | Provider 注册表 + 工厂 | 业务层永不 import 具体模型实现 |
| ADR-005 | 人格分 Core / Adaptive，变更必须留痕 | 防止一次偶然对话导致人格突变 |
| ADR-006 | 用 unittest 而非第三方测试框架 | 测试零网络依赖，HTTP Provider 的请求全部用 mock |
| ADR-007 | 用单个 `OpenAICompatProvider` 覆盖各家 API，厂商只改默认值 | DeepSeek/Qwen/OpenAI 协议相同，避免重复 Provider；API Key 走环境变量 |
| ADR-008 | 人格演化必须过规则验证层 + 版本化 + 可回滚 | 人格可以成长，但绝不会因一次对话突变成另一个人 |
| ADR-009 | World State 变更通过 `WORLD_STATE_CHANGED` 事件即时落盘 | 崩溃/重启后世界状态不丢失；保持 WorldState 为纯数据 |
| ADR-010 | 空回复在 Provider 层重试一次 | DeepSeek v4-flash 为思考型模型，偶发空 content；不让单次空回复打挂 Agent |

## 13. 当前已知风险

1. **空回复重试是"猜测式修复"**：目前对空 content 重试一次，尚未做思考型模型的
   reasoning/stream 深度适配（如 `reasoning_content` 处理）。
2. **同步 EventBus**：单线程、无背压，未来若事件量大（传感器高频事件）需评估异步化。
3. **关键词检索的简单记忆**：无法处理语义检索与去重，长期记忆能力受限。
4. **数据文件迁移策略单一**：只有 v1→v2 的隐式迁移，无显式 schema 版本管理。
5. **单进程 CLI**：崩溃时依赖"结束即保存"；World State 已改为更新即落盘，其余模块仍以
   变更时保存为主。
6. **HTTP Provider 无限流退避**：429/5xx 时 `generate` 直接抛异常，无退避重试与错误分类。
7. **Prompt 投影是拼接字符串**：`build_system_prompt` 手工拼接，未来字段增多需防失控；
   最近记忆注入仅取 5 条，无排序/权重策略。

## 14. 下一阶段建议
1. 把空回复重试升级为完整的重试/退避策略（含 429、5xx 错误分类）。
2. `OllamaProvider`（本地部署），与 API Provider 共用同一接口。
3. Event Log：把事件流持久化，支撑未来分析与回放。
4. 记忆检索增强：语义检索 + 重要度排序，替换当前 5 条平铺注入。
5. 主动行为：在 `BehaviorEngine` 接入定时器驱动（`TIME_THRESHOLD_REACHED`）。
6. `build_system_prompt` 改为结构化投影管线，避免拼接失控。
