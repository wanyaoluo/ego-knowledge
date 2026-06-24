# 核心模块与子包索引

> `src/ego_knowledge/` 的核心模块与子包索引。单文件条目写行数，子包条目用 `—`；完整文件清单以源码目录为真源。

## 入口

| 模块 | 行数 | 职责 |
| --- | --- | --- |
| [`__init__.py`](../../src/ego_knowledge/__init__.py) | 36 | 公开 API 懒加载导出 |
| [`cli.py`](../../src/ego_knowledge/cli.py) | 591 | Click CLI（薄壳） |
| [`mcp_server/`](../../src/ego_knowledge/mcp_server/) | — | FastMCP 适配层（薄壳） |

## 主类

| 模块 | 行数 | 职责 |
| --- | --- | --- |
| [`core.py`](../../src/ego_knowledge/core.py) | 536 | `EgoKnowledge` 聚合门面，组合 service 与 DAO |

## DAO（单一）

| 模块 | 行数 | 职责 |
| --- | --- | --- |
| [`registry/`](../../src/ego_knowledge/registry/) | — | `Registry` DAO 子包，封装 SQLite 读写 + schema + FTS5 + audit |
| [`registry/__init__.py`](../../src/ego_knowledge/registry/__init__.py) | 93 | `Registry` 门面与导出 |
| [`registry/_ddl.py`](../../src/ego_knowledge/registry/_ddl.py) | 212 | DDL、索引与 schema bootstrap |
| [`registry/_schema.py`](../../src/ego_knowledge/registry/_schema.py) | 155 | schema 版本、迁移与初始化 |
| [`registry/_read.py`](../../src/ego_knowledge/registry/_read.py) | 238 | entry 读取与查询辅助 |
| [`registry/_write.py`](../../src/ego_knowledge/registry/_write.py) | 299 | entry 写入、FTS 同步与事务内更新 |
| [`registry/_fts.py`](../../src/ego_knowledge/registry/_fts.py) | 119 | FTS upsert/delete 与检索辅助 |
| [`registry/_relations.py`](../../src/ego_knowledge/registry/_relations.py) | 205 | relation / refs / graph 邻接读写 |
| [`registry/_audit.py`](../../src/ego_knowledge/registry/_audit.py) | 176 | access log、maintenance queue、dense queue |
| [`registry/_typing.py`](../../src/ego_knowledge/registry/_typing.py) | 42 | registry mixin Protocol 类型面 |

> `registry.py` 已拆为 `registry/` 子包；现行口径以本模块索引与源码目录为准。

## Service 层（下划线前缀，门面调用，不对外暴露）

| 模块 | 行数 | 职责 |
| --- | --- | --- |
| [`_entry_store.py`](../../src/ego_knowledge/_entry_store.py) | 590 | `EntryStore`：ingest/get/update 主流程，含 post-commit 可观测性（`PostCommitError` 队列 + `post_commit_errors` 属性） |
| [`_mutations.py`](../../src/ego_knowledge/_mutations.py) | 257 | `MutationService`：rename + apply_primary_updates |
| [`_promotion.py`](../../src/ego_knowledge/_promotion.py) | 305 | `PromotionService`：kind 升格 |
| [`_relations.py`](../../src/ego_knowledge/_relations.py) | 151 | `RelationService`：link/unlink/related |
| [`_domains.py`](../../src/ego_knowledge/_domains.py) | 119 | `DomainRegistry`：domain 词表与迁移 |
| [`_validation.py`](../../src/ego_knowledge/_validation.py) | 534 | schema/三桶/冲突/引用完整性/body 校验 |
| [`_md_format.py`](../../src/ego_knowledge/_md_format.py) | 68 | mdformat + GFM body 规范化（纯函数，不碰 frontmatter） |

## 横切支撑

| 模块 | 行数 | 职责 |
| --- | --- | --- |
| [`models.py`](../../src/ego_knowledge/models.py) | 252 | 全部 dataclass 与枚举 |
| [`errors.py`](../../src/ego_knowledge/errors.py) | 117 | 4 类错误 + body error_code + `to_transport` 序列化 |
| [`transactions.py`](../../src/ego_knowledge/transactions.py) | 257 | `transactional_write`、body 快照恢复、`write_snapshot` |
| [`search/`](../../src/ego_knowledge/search/) | — | 检索路由子包 |
| [`search/_router.py`](../../src/ego_knowledge/search/_router.py) | 328 | `SearchRouter`：解析→exact/bm25/graph/dense→融合 |
| [`search/_types.py`](../../src/ego_knowledge/search/_types.py) | 22 | 检索结果与后端类型 |
| [`search/_helpers.py`](../../src/ego_knowledge/search/_helpers.py) | 55 | snippet、fullwidth、结果合并辅助 |
| [`search/_backends/`](../../src/ego_knowledge/search/_backends/) | — | exact / BM25 / graph / dense 后端 |
| [`tokenizer.py`](../../src/ego_knowledge/tokenizer.py) | 170 | jieba 分词 + 自定义词典 |
| [`frontmatter.py`](../../src/ego_knowledge/frontmatter.py) | 169 | YAML frontmatter 读写 |
| [`paths.py`](../../src/ego_knowledge/paths.py) | 106 | data_root 与 entry 路径解析 |
| [`slug.py`](../../src/ego_knowledge/slug.py) | 51 | slug 生成（含稳定 slug 校验） |
| [`metrics.py`](../../src/ego_knowledge/metrics.py) | 334 | 五元指标更新 |
| [`unicode_utils.py`](../../src/ego_knowledge/unicode_utils.py) | 49 | NFC + CJK 判定 |
| [`doctor/`](../../src/ego_knowledge/doctor/) | — | 文件级一致性体检子包 |
| [`doctor/__init__.py`](../../src/ego_knowledge/doctor/__init__.py) | 238 | doctor 门面与报告聚合 |
| [`doctor/_helpers.py`](../../src/ego_knowledge/doctor/_helpers.py) | 77 | doctor 辅助工具函数 |
| [`doctor/_types.py`](../../src/ego_knowledge/doctor/_types.py) | 36 | doctor 类型定义 |
| [`doctor/_checks/`](../../src/ego_knowledge/doctor/_checks/) | — | alias / integrity / metrics / terminology / unicode 检查 |
| [`diagnose.py`](../../src/ego_knowledge/diagnose.py) | 255 | 知识级语义诊断 |

## 自动化、dense 与迁移支撑

| 模块 | 行数 | 职责 |
| --- | --- | --- |
| [`maintenance_queue_store.py`](../../src/ego_knowledge/maintenance_queue_store.py) | 354 | maintenance_queue 读写 |
| [`local_rules.py`](../../src/ego_knowledge/local_rules.py) | 272 | 本地规则与 findings 入队 |
| [`_autonomous.py`](../../src/ego_knowledge/_autonomous.py) | 295 | AI 自主 ingest 编排 |
| [`_approval_executor.py`](../../src/ego_knowledge/_approval_executor.py) | 105 | approve/reject 执行器 |
| [`_external_watch.py`](../../src/ego_knowledge/_external_watch.py) | 490 | GitHub releases L1 轮询 |
| [`_dense_embedder.py`](../../src/ego_knowledge/_dense_embedder.py) | 305 | dense embedding 调用与缓存 |
| [`_dense_index.py`](../../src/ego_knowledge/_dense_index.py) | 289 | dense 索引重建 |
| [`_dense_queue.py`](../../src/ego_knowledge/_dense_queue.py) | 189 | dense 延迟队列 |
| [`_graph_authority.py`](../../src/ego_knowledge/_graph_authority.py) | 114 | 图权威传播 |
| [`_query_preprocess.py`](../../src/ego_knowledge/_query_preprocess.py) | 147 | 查询预处理与字符类拆分 |
| [`_diagnose_rules/`](../../src/ego_knowledge/_diagnose_rules/) | — | diagnose action / push / decay / structure 规则 |
| [`migrations/`](../../src/ego_knowledge/migrations/) | — | 临时 schema 迁移实现入口（当前无常驻脚本） |
| [`scripts/`](../../src/ego_knowledge/scripts/) | — | 可 import 脚本入口 |
| [`scripts/normalize_legacy/`](../../src/ego_knowledge/scripts/normalize_legacy/) | — | 存量 frontmatter 全角结构标点修复（dry-run/apply/restore） |
| [`scripts/cleanup_broken_relations/`](../../src/ego_knowledge/scripts/cleanup_broken_relations/) | — | 存量 AI 断裂关系边清理（dry-run/apply/restore） |

## 调用层级

```text
CLI / MCP (薄壳)
   ↓
core.EgoKnowledge (门面)
   ↓
_entry_store / _mutations / _promotion / _relations / _domains (Service)
   ↓
registry.Registry (DAO 子包)
   ↓
SQLite (catalog.sqlite) + 文件系统
```

横切：`_validation` 被 service 层调用；`transactions` 被 service 与 DAO 共用；`search/` 被 core 直接调用。

## Post-commit 可观测性 API

事务提交后运行的 step（metrics recompute、local findings、dense enqueue）失败无法回滚，通过以下 API 对外可观测。

| API | 定义 | 职责 |
| --- | --- | --- |
| `PostCommitError`（dataclass） | `_entry_store.py:66` | post-commit 失败的结构化记录（label + entry_id + exception），frozen + slots，通过 `core.py` re-export |
| `EntryStore.post_commit_errors`（property） | `_entry_store.py:173` | 只读副本，按发生顺序返回 `list[PostCommitError]`，每次 `update()` 开头清空 |
| `EgoKnowledge.post_commit_errors`（property） | `core.py:202` | 透传 `EntryStore.post_commit_errors`，CLI / MCP / AI orchestration 等外部调用方通过此属性感知失败 |
| `EntryStore.run_post_commit_step`（方法） | `_entry_store.py:401` | 执行单个 post-commit step，异常捕获入队不向上冒泡，日志 warning + `exc_info=True` |

## 测试

| 目录 | 用途 |
| --- | --- |
| `tests/unit/` | 单元测试 |
| `tests/integration/` | 真 stdio + write_verify |
| `tests/regression/` | Core 方法回归 |
| `tests/adversarial/` | 错误契约对抗 |
| `tests/benchmark/` | 检索基线 |

详细命令见 [`README.md`](../../README.md#测试)。
