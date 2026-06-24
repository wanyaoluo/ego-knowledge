# 排查 catalog 漂移

> `catalog.sqlite` 与文件不同步时如何定位与修复。
> 默认数据目录是 `~/.ego-knowledge/data`；下文用 `EK_DATA_ROOT` 显式指向数据目录。

## 现象

- `ek search` 找不到刚写入的条目
- `ek get <id>` 抛 `not_found_error` 但文件确实存在
- `ek doctor` 报 `db_missing_entry` 或 `file_missing`
- recovery.log 非空

## 三种漂移

| 类型 | 表现 | 常见原因 |
| --- | --- | --- |
| **DB 缺文件有** | SQLite 没记录，`.md` 文件在 | 手动改文件、同步丢库、重建中断 |
| **DB 有文件无** | SQLite 有行，`.md` 文件被删 | 手动 rm、git 回滚、备份恢复不彻底 |
| **内容不一致** | 都存在但 frontmatter 与 SQLite 字段不匹配 | recovery.log 里的 COMMIT 失败事务 |

## 第一步：确认范围

```bash
ek doctor > /tmp/doctor.json
```

读 JSON 的 `summary` + `issues`：

- `summary.total_files` vs `summary.total_db_rows`：差距说明范围
- `issues[].severity == "error"`：必修
- `issues[].severity == "warning"`：可放后

## 第二步：决定策略

### 范围小（< 10 条）→ 单条修

`--repair` 后看剩余 issue，按 entry_id 逐条处理：

```bash
ek doctor --repair
ek doctor   # 再看一遍剩多少
```

### 范围大（全库受影响）→ 全量重建

```bash
export EK_DATA_ROOT="${EK_DATA_ROOT:-$HOME/.ego-knowledge/data}"
mv "$EK_DATA_ROOT/registry/catalog.sqlite" \
   "$EK_DATA_ROOT/registry/catalog.sqlite.bak"
ek build-registry
```

`build-registry` 扫所有 `.md` 文件重建 SQLite。文件是真源，重建总是安全的。

> 不走 `--repair` 而走全量重建的判断：手动改文件 > 50 条、schema_version 升级、SQLite 文件损坏。

## 第三步：处理具体类型

### DB 缺文件有

```bash
ek doctor --repair
```

`--repair` 走 `build-registry` 子流程，把缺的文件读回。

### DB 有文件无

`--repair` 默认把 SQLite 行 archived（不删，保留历史）。
如果文件是误删：

1. 从 `_backup/` 或 git 历史恢复 `.md`
2. `ek build-registry` 或 `ek doctor --repair`

### 内容不一致

frontmatter 与 SQLite 不匹配通常是 recovery.log 留下的，文件先新但 DB 未提交。
`--repair` 以**文件为准**重写 SQLite 行。

如果是反过来（DB 新文件旧），属于异常情况，需要检查 recovery.log 里 message：

```bash
cat "$EK_DATA_ROOT/logs/refresh/recovery.log"
```

按记录手动决定保留哪边。

## 第四步：验证

```bash
ek doctor          # 应全 PASS
ek search "随便查"  # FTS 应工作
ek stats --by kind # 计数应合理
```

跑一遍 [run-doctor-and-diagnose.md](run-doctor-and-diagnose.md) 中描述的"健康基线"检查。

## 预防

- 不手改 `.md` frontmatter，用 `ek update`
- 不手删 `.md`，用 `ek update --changes '{"status":"archived"}'`
- 跨设备同步用 `git`/`rsync` 时把 `$EK_DATA_ROOT` 整目录一起搬，不要只搬 `.md`
- 用 cron 或自有调度器跑日检 doctor

## 紧急回滚

如果 `--repair` 把数据搞更糟：

```bash
cp "$EK_DATA_ROOT/registry/catalog.sqlite.bak" \
   "$EK_DATA_ROOT/registry/catalog.sqlite"
```

`build-registry` 会自动备份旧 catalog（待确认实现细节）；如未自动备份，写脚本前先手动 cp。

## 常见误区

- ❌ "重启就好"：SQLite 不靠重启恢复，必须显式 doctor 或 build-registry
- ❌ "删 catalog.sqlite 解决一切"：会丢 recovery.log 的中断事务证据，应先 backup
- ❌ "改 frontmatter 改成什么样都行"：不是 ek 写的字段（比如手加 metrics）会被 doctor 当噪音清掉
