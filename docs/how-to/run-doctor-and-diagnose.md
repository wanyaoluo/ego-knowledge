# 运行 doctor 与 diagnose

> 何时跑 `ek doctor` 与 `ek diagnose`，怎么读结果，怎么修。

## 两条命令的区别

| 命令 | 检查级别 | 关心什么 |
| --- | --- | --- |
| `ek doctor` | 文件 ↔ DB 一致性 | 文件存不存在、frontmatter 完整、catalog.sqlite 同步 |
| `ek diagnose` | 知识层一致性 | drift_score、冲突簇、孤儿引用、过期 dossier |

doctor 是技术健康，diagnose 是知识健康。两者互不替代。

副作用边界：两者不修复 entry 正文或关系，但都会写入 `logs/diagnose/*.json` 报告。`diagnose` 遇到高严重度 finding 时还会写入 `maintenance_queue` 并尝试创建 task-board 任务。

## 何时跑 doctor

- 写入失败后看到 `请运行 ek doctor --repair`
- 跨设备同步后（Windows ↔ WSL）
- 大量手动改 `.md` 文件后
- 备份恢复后
- 版本升级（schema_version 变化）后

## 跑 doctor

普通检查（不修复 catalog 或文件，但会写报告）：

```bash
ek doctor
```

输出 JSON 含：`summary`（通过/失败计数）、`issues`（每条 `{entry_id, kind, severity, message}`）、`repairable`（自动可修项数）。

修复模式：

```bash
ek doctor --repair
```

会做：重新解析 frontmatter 同步到 SQLite、补齐缺失 FTS 索引、标记孤儿 SQLite 行、收尾 recovery.log 中断事务。修不动的 issue 留在报告里需手动处理。

## doctor 常见问题

详见 [doctor-common-issues.md](doctor-common-issues.md)。

## 何时跑 diagnose

- 月度知识体检
- 准备做 promotion 前
- 觉得检索结果不对
- 决定 archive 大量旧 entry 前

## 跑 diagnose

```bash
ek diagnose
```

输出（JSON）含：

- `drift_high`：drift_score 高的 entry（内容陈旧或脱离主题）
- `conflict_clusters`：标题或别名相似的簇
- `orphan_refs`：指向不存在的 entry（doctor 也会报，diagnose 给上下文）
- `dossier_overdue`：review_due_at 已过期
- `low_evidence`：evidence_status="weak" 或 evidence_strength 低的 concept/decision

修复手段：

- 冲突簇：
  - 用 `ek update` 合并
  - 用 `ek link --type related` 标关系
  - 或用 `ek update` 改 status=archived
- drift 高 → 重写或拆分 entry
- orphan_refs → 改引用或补建被指 entry
- overdue → `ek update <id> --changes '{"reviewed_at": "..."}'`

## 自动化

把两者纳入定时任务：

- doctor：每日，失败发通知
- diagnose：每周，输出 review queue

可使用系统 cron、GitHub Actions 或自有调度器。

## 与 stats 的关系

`ek stats --by kind/status/freshness/domain` 是健康度概览，不做问题判定。

- stats 看趋势
- doctor 看技术错
- diagnose 看知识债

三者配合形成完整体检视图。

## 排障联动

- doctor 报 `db_missing_entry` 大量出现 → 看 [debug-catalog-drift.md](debug-catalog-drift.md)
- doctor 报 schema 不匹配 → 看 [migrate-schema.md](migrate-schema.md)
- diagnose 报 review 大量过期 → 走 `ek review --due`
