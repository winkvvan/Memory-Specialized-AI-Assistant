# ACR-001

## Problem

7.3 的 Memory Ecology 只有 `MemoryMaintenance`（用户手动触发）与 lazy decay，但维护没有接入运行时生命周期：
Chris 长时间不对话时，记忆不会自动衰减/归档/归一化，记忆生态会偏离真实状态。需要让维护成为
Temporal 时间系统的一部分，同时保持 lazy decay（绝不每 5 分钟全量扫描重写）。

## Current Architecture

- Temporal：`TemporalSystem` + Clock/VirtualClock + `tick()` + `EventBus`（`TIME_TICK` 等事件）。
- Memory：`MemoryEcology`（生命周期/衰减/归档/访问≠强化），维护逻辑散落在 `decay()/archive_one()`，
  `MemoryMaintenance` 只能由 CLI `/memory maintenance` 手动触发。
- `MemoryConfig.maintenance_interval_seconds=300` 已存在但未真正接入自动调度。

## Proposed Change

- 新增 `chris/memory/maintenance.py::MemoryMaintenanceScheduler`：订阅 Temporal `TIME_TICK`，O(1)
  间隔检查（读 `maintenance_metadata` 的 `last_maintenance_at`），到期才调用统一 `run()`。
- `chris/events.py` 新增事件 `MEMORY_MAINTENANCE_CHECKPOINT`（payload：timestamp/reason/report）。
- `MemoryEcology.maintenance()`：只处理候选（到期 ∪ WEAKENING/DORMANT），幂等、逐条容错（errors 计数）、
  归一化（clamp strength/importance/confidence、校验 status/type）。
- 维护元数据（last/next_maintenance_at、maintenance_count）随 memory.json 持久化。
- `reinforce()` 重置 `last_decay_at`（强化后重新开始衰减）。
- CLI `/memory maintenance` 与自动路径共用同一个 `run()`。
- `MemoryConfig` 增加 additive 字段（不改默认衰减/强化参数）。

## Impact

- 受保护模块：`chris/memory/ecology.py`（新增方法/字段，additive）、`chris/events.py`（新增事件枚举值）、
  `chris/app.py`（build_runtime 装配，非受保护）。
- 均为新增接口，不改既有方法行为；`memory/`、`temporal/` 生命周期规则与衰减默认值不变。

## Compatibility

- 向后兼容：既有调用方（`query/search/add/reinforce/archive` 等）行为不变；旧 schema 数据可正常加载。
- 新事件枚举为新增值，不影响现有 handler。

## Data Migration

- 无 schema 变更；`last_maintenance_at` 等为空时视为"首次即到期"，不迁移旧数据。

## Provider Independence

- 维护完全由 Temporal/Clock 驱动，不依赖任何 LLM/Embedding Provider；模型可替换不影响生命周期。

## Testing

- VirtualClock + Temporal 驱动：自动衰减（+1d/+7d/+30d）、强化重置衰减基准、归档→重激活全生命周期、
  幂等（时间不变重复 maintenance 无额外变化）、容错（单条坏 Memory 不阻塞）、无重复 scheduler、
  元数据持久化重启恢复、性能（1000 条）、Provider 独立。

## Rollback

- 回滚 = 移除 scheduler 注册与 maintenance() 重构；因 additive 且不改默认参数，可安全回退到 7.3。

## Status

IMPLEMENTED
