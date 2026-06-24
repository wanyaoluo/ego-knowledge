# explanation/why-registry-is-dao.md

> 直觉看到 `registry/` 1697 行就想拆。这篇讲清楚为什么"该拆什么"和"什么不该拆"是两回事。

本文只做**教学展开**，帮你识别"看似该拆其实不该拆"的模式。现行结构以 [module-index.md](../reference/module-index.md) 与源码目录为准。

## 一句话定位

`Registry` 是 **DAO（Data Access Object）**，不是上帝类。它把 SQLite + FTS5 索引封装成 Python 方法。**所有业务编排已经拆到上一层 service**（`_entry_store / _mutations / _promotion / _relations / _domains`）。

判断"是否上帝类"的简单测试：

| 问题 | Registry 答案 |
| --- | --- |
| 它调用别的模块吗？ | 不调用任何 service |
| 方法里有业务分支吗？ | 没有，全是 SQL + 参数绑定 + dataclass 映射 |
| 调用方知道它内部多少表吗？ | 不需要知道，只调方法 |

三项都通过 → DAO；任意一项失败 → 该拆。Registry 三项全过。

## 拆 DAO 的两种典型错误

### 错误 1：按子领域切（EntryDAO / SearchDAO / RelationDAO）

直觉很强：1697 行，按"实体 / 搜索 / 关系"切三块，每块 500 行不香吗？

实战拆完会发现：

1. **共享连接难**：同一 SQLite 连接要在 DAO 间共享 → 要么传 `conn` 参数（污染所有签名），要么搞个 ConnectionManager 单例（回到原起点）。
2. **跨 DAO 事务灾难**：`ingest` 一次写 `entries / aliases / entry_tags / entries_fts_*` 四五张表，必须同事务。拆成多 DAO 后调用方要协调事务边界，service 层反而变重。
3. **schema 归属问题**：`SCHEMA_SQL` 该放谁？放 EntryDAO？SearchDAO 也依赖。放公共模块？又一个新文件，又一处真源。
4. **触发器跨表**：FTS5 同步触发器横跨 `entries / aliases / entry_tags` 三张表，任何 DAO 改写都得懂 FTS5。拆完反而需要更多上下文才能安全改一处。

### 错误 2：按表切（一表一 DAO）

更糟。`entries` 与 `aliases / entry_tags / relations` 通过外键 + 触发器强耦合。按表切等于把数据库的物理结构暴露给上层，上层要知道"改 entry.title 顺便要更新 aliases"，封装泡汤。

## 拆 service vs 拆 DAO 的本质区别

| 维度 | service (`_*.py`) | DAO (`registry/`) |
| --- | --- | --- |
| 内容 | 业务流程：顺序、条件、跨步状态 | 数据访问：SQL + 参数 + 行映射 |
| 拆分价值 | 高，把"做什么"按业务隔离 | 低，SQL 没有业务可隔离 |
| 实例 | `_entry_store` 决定 ingest 顺序；`_promotion` 协调 kind 升格 | `upsert_entry / search_bm25 / list_relations` |

记忆点：**有流程的拆，没流程的合**。Registry 1697 行内全是 SQL + dataclass 映射，没有"先做 A 再做 B"的流程，就不该拆。

## 内部凝聚度的快速验证

打开 `registry/` 翻一遍：

| 区段 | 行（粗） | 性质 |
| --- | --- | --- |
| SCHEMA_SQL | 48-199 | DDL，不可拆 |
| 初始化 | 240-340 | `__init__` / `init_schema` |
| Entry CRUD | 340-700 | 主表读写 |
| Alias / Tag | 700-900 | 应用层显式同步（DELETE + INSERT，非数据库触发器） |
| Relations | 900-1100 | 关系图 CRUD |
| 三索引 BM25 | 1100-1500 | search 路由 |
| FTS / 统计 | 1500-1697 | refresh / close |

每一段都是"接收参数 → 拼 SQL → 绑参数 → 取行 → 映射 dataclass"。**没有任何一段在编排其他段**。这就是 DAO 的特征。

## 什么时候应该重新讨论

模块边界文档列了再讨论的触发条件（突破 3000 行 / 多后端 / 多引擎并行 / 多进程访问）。这里补一个**反向**判断：

> 如果某天你打开 `registry/` 发现一个方法里出现了 `if entry.kind == ...:` 或 `for relation in self.list_relations(...): self.upsert_entry(...)` 这种**跨方法编排**，那就是业务流程渗透到 DAO 了，该往 service 抽，不是拆 DAO。

## 推荐阅读

- [module-index.md](../reference/module-index.md)：现行模块结构与职责索引
- [architecture-layers.md](architecture-layers.md)：CLI/MCP → Core → Service → DAO 的完整四层
- [transactions.md](transactions.md)：跨表事务为何必须留在 DAO 内
