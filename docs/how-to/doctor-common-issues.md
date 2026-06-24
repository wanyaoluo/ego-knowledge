# doctor 常见问题

> `ek doctor` 常见问题与处理方法。

## `frontmatter parse failed`

文件 YAML 损坏。打开手改 → 重跑。

## `db_missing_entry`

SQLite 没这条但文件有。`--repair` 自动补。

## `file_missing`

SQLite 有但文件没了。`--repair` 把 SQLite 行标记 archived；如属误删，从备份恢复文件后再重建。

## `recovery.log not empty`

存在中断事务。`--repair` 按日志逐条重写，写完清日志。

## `fts_out_of_sync`

FTS 索引未同步。`--repair` 触发 `refresh_fts`。

## `fullwidth_in_body`

检测到全角字符。message 头部标签区分来源：

- `[fm]`：frontmatter 全角结构标点残留（`U+3000`、`：`、`，`、`"`/`"`、`'`/`'`），应已半角化
- `[body]`：body 全角空格 `U+3000` 或全角 ASCII 字母数字（`U+FF01`–`U+FF5E` 且半角为 alnum）

中文正文全角标点（，。：；！？""''）不在检测范围，不报。

处理：按 `[fm]`/`[body]` 标签定位 frontmatter 或 body 区域，手动或批量修复。

## `broken_relations`

检测到断裂关系：`relations.target_id` 在 `entries` 中不存在。

message 为结构化键值对：`broken relation: source=<source_id> target=<target_id> type=<rel_type> origin=<origin>`

按 origin 分级处理：

| origin | 处理方式 |
| --- | --- |
| `ai_suggested` | 默认列入清理候选，只删除关系边不删除条目 |
| `ai_confirmed` | 默认列入清理候选并保留报告，仍非人工确认 |
| `confirmed` | 生成清单，人工裁决或补建目标，不静默删除 |

处理：`--repair` 不自动修复断裂关系；需运行批量归一化脚本按 origin 分级处理：

```bash
# 先预览清理计划
python3 -m ego_knowledge.scripts.cleanup_broken_relations --dry-run

# 确认后执行清理（自动备份）
python3 -m ego_knowledge.scripts.cleanup_broken_relations --apply

# 如需恢复，从备份反向恢复
python3 -m ego_knowledge.scripts.cleanup_broken_relations --restore --backup-dir <备份目录>
```

详见 [cli-commands.md](../reference/cli-commands.md) 存量修复脚本章节。
