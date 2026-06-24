# explanation/transactions.md

> EgoKnowledge 写入协议：tmp 文件 + rename + SQLite IMMEDIATE 锁，单一事务覆盖文件与数据库。

真源：[`transactions.py`](../../src/ego_knowledge/transactions.py)。

## 问题

文件是真源，SQLite 是索引。两者要么都更新，要么都不更新，否则 `ek doctor` 会发现漂移。
但单机文件系统和 SQLite 不能开真正的分布式事务，必须用恢复协议模拟。

## 协议

`transactional_write(target_path, new_content, conn)` 上下文管理器执行：

```text
1. 写 tmp_path = target_path + ".tmp"
   - open w → write → flush → fsync     失败 → StorageError("临时文件写入失败")
2. cursor.execute("BEGIN IMMEDIATE")
   - 失败 → 清理 tmp → StorageError("无法获取 SQLite 写锁")
3. yield cursor                            ← 调用方在事务内更新数据库
4. os.rename(tmp_path, target_path)        失败 → StorageError("文件 rename 失败")
5. conn.commit()
   - 失败 → body 写入优先从 `.txn-snapshots/` 还原 → StorageError
异常路径：rollback + 清理 tmp
```

## 为什么这个顺序

### tmp + rename 在前

`os.rename` 在 POSIX 是原子操作，要么旧文件还在，要么新文件就绪，不会半截。先写 tmp 把内容备好，
拿到 SQLite 写锁后再 rename，缩短"文件已新但 DB 未提交"的窗口。

### BEGIN IMMEDIATE 而非 DEFERRED

`IMMEDIATE` 立即占写锁，避免上层 yield 期间多写者并发挤进来。`DEFERRED` 会推迟到第一个写语句，
中间窗口可能让另一个写事务先 commit，导致回滚成本变高。

### rename 在 commit 之前

| 顺序 | 失败窗口 | 后果 |
| --- | --- | --- |
| **rename → commit**（当前） | rename 后 commit 前崩溃 | 普通写入需 recovery.log + `ek doctor --repair`；body 写入先按快照还原 |
| commit → rename | commit 后 rename 前崩溃 | DB 已提交，文件未替换 → 数据库说有，文件没有 |

两者都需要恢复，但当前选择更倾向"文件先新"，因为 DB 可从文件重建，反过来不行。

## recovery.log

COMMIT 失败时往 `<data_root>/logs/refresh/recovery.log` 追加恢复事件（权限 `0600`）：

```json
{"ts": "2026-04-26T10:00:00+0800", "target_path": ".../entry.md", "message": "COMMIT 失败: ..."}
```

普通写入由 `ek doctor --repair` 读取该日志，对受影响 entry 走"文件→DB"重建。body 写入会先用 `.txn-snapshots/<txn_id>/manifest.json` 指向的快照还原旧文件；快照缺失或还原失败时抛 `body_recovery_failed_snapshot_missing`，并保留 recovery 证据。

`_find_data_root(path)` 从 target_path 向上找含 `registry/` 的目录定位 data_root；找不到回退到 `path.parent.parent`。

## fsync 的代价与必要

`os.fsync(handle.fileno())` 强制脏页落盘。代价是写入慢一倍，但避免：

- 系统崩溃后 tmp 文件存在但内容空
- rename 成功但目标文件内容丢失

EgoKnowledge 是单机个人工具，写入频率低（人/AI 触发），fsync 代价可接受。

## 调用边界

只有 mutating service（`_entry_store / _mutations / _promotion`）调 `transactional_write`。
DAO 层（`Registry`）的方法不直接调，因为不知道目标文件路径。

## write_snapshot

`write_snapshot(data, data_root)` 是另一个简单函数，往 `logs/stats/YYYY-MM-DD.json` 写统计快照。
不走事务，快照丢失不影响真源。

## 不变量

任何成功返回的 mutating 操作满足：

1. target_path 内容是新的
2. SQLite 中 entries/aliases/relations/fts 同步更新
3. `ek doctor` 不报漂移

任何抛 StorageError 的操作满足：

1. target_path 要么不变（rename 前失败），要么已按 body 快照还原，普通写入至少有 recovery.log 记录
2. SQLite 已 rollback
3. tmp 文件已清理

## 多进程风险

SQLite 默认连接级锁，多进程写并发会因 BEGIN IMMEDIATE 失败 → StorageError。EgoKnowledge 当前是单进程模型
（CLI / MCP server 都是单实例），多进程并发不在保证范围。后续如需，应在 facade 层加文件锁。
