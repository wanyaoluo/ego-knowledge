# scripts/

ego-knowledge 可安装辅助 CLI 实现目录。

| 子包 | 用途 | 入口 |
| --- | --- | --- |
| `archive_dirty_concepts/` | 脏 concept 高风险归档工作流，按 snapshot 驱动 dry-run / execute / reconcile / restore | `ek-archive` |
| `cleanup_broken_relations/` | 存量 AI 断裂关系边清理，dry-run / apply / restore 三模式 | `python3 -m ego_knowledge.scripts.cleanup_broken_relations` |
| `normalize_legacy/` | 存量 frontmatter 全角结构标点修复，dry-run / apply / restore 三模式 | `python3 -m ego_knowledge.scripts.normalize_legacy` |

执行备份型模式（dry-run / execute / reconcile / restore）时，必须显式传入备份目录：
`--backup-dir <backup-dir>`。
