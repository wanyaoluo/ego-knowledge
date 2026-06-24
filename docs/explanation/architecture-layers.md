# explanation/architecture-layers.md

> EgoKnowledge 是分层架构。每层只解决一类问题，跨层互不耦合。本文解释为什么这样切。

## 五层

```text
┌─────────────────────────────────────────────┐
│ Transport：CLI / MCP / (HTTP)               │  薄壳
├─────────────────────────────────────────────┤
│ Facade：core.EgoKnowledge                   │  组合
├─────────────────────────────────────────────┤
│ Service：_entry_store / _mutations / ...    │  业务流程
├─────────────────────────────────────────────┤
│ DAO：registry.Registry                      │  SQLite 封装
├─────────────────────────────────────────────┤
│ Storage：文件系统 + catalog.sqlite          │  真源 + 索引
└─────────────────────────────────────────────┘
```

横切：`_validation`（任何层调用都不变）、`transactions`（service+DAO 共用）、`search`（被 facade 直接调用）、
`tokenizer/slug/unicode_utils/frontmatter`（纯函数支撑）。

## 为什么这么切

### 文件 vs 数据库的真源关系

文件是真源，`catalog.sqlite` 是派生物。任何时候 `ek build-registry` 都能从 `.md` 文件全量重建索引。
这条关系决定：

- 任何 mutating 操作必须**同时**改文件和数据库（事务策略，见 [transactions.md](transactions.md)）
- 数据库失联或损坏不致命，重建即可
- 但文件失联致命，所以备份要面向文件

### Transport 必须是薄壳

CLI、MCP、未来的 HTTP 都不能持有业务逻辑。原因：

- 一处改动，三处同步会漂移
- AI agent 走 MCP，人类走 CLI，前端走 HTTP，用户体验需统一
- 错误契约（[error-types.md](../reference/error-types.md)）只在 transport 层做序列化适配

CLI 当前 395 行已偏厚，但都是参数解析与 JSON 序列化，不含业务判断，符合定义。

### Facade 而非直接暴露 Service

`EgoKnowledge` 类聚合 6 个 service + 1 个 DAO。Transport 只看 facade，不看 service。原因：

- service 之间有调用顺序（如 `MutationService` 调用 `_entry_store` 再调用 `Registry`），facade 决定编排
- 单一注入点便于测试替换
- 未来加缓存/审计/锁，可以在 facade 拦截

### Service 拆分按"流程"而非"实体"

不是一个 Entry 一个 service，而是按动作切：

- `EntryStore` = 增/查/改主流程
- `MutationService` = rename + 主字段更新（封装更复杂的引用同步）
- `PromotionService` = kind 升格
- `RelationService` = 关系图操作
- `DomainRegistry` = domain 词表

按流程切的好处：每个 service 内部状态机封闭，不跨流程共享中间状态。

### DAO 单体合理

`Registry` 1697 行看起来大，但全是 SQLite 操作的同质代码。按 search/relations/stats 切会引入 DAO 间相互调用，
反而破坏单一职责。现行模块职责见 [module-index.md](../reference/module-index.md)。

## 数据流向（以 ingest 为例）

```text
用户/AI → CLI/MCP（解析参数、调 facade）
        → core.EgoKnowledge.ingest()
        → _entry_store.EntryStore.ingest()
            ├── _validation.validate_schema  → ValidationError
            ├── _validation.check_conflicts  → ConflictError
            ├── slug.generate_slug
            ├── frontmatter.dump
            ├── transactions.transactional_write
            │     └── registry.Registry.upsert_entry  (SQLite)
            └── tokenizer.sync_runtime_words
        → 返回 Entry dataclass
        → CLI _emit_json / MCP toolresult
```

## 横切支撑选择

- `_validation` 不入 service：被多 service 共用，独立模块避免循环依赖
- `transactions` 是 contextmanager：写文件 + 写 DB 必须原子，单独抽出更易复用
- `search` 不在 service：检索不修改状态，是只读路径，独立简单

## 演进路径

- HTTP 层加入：在 facade 之上加 transport，service 不动
- 缓存：在 facade 内拦截
- 多副本：DAO 替换为 RegistryCluster，service/facade 不变
- 语义检索 v3/v4：search/ 内部新增 backend，SearchRouter 接口稳定
