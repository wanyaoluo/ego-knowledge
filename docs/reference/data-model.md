# reference/data-model.md

> EgoKnowledge 数据模型权威清单。每条都是 `kind/枚举/表/字段` 的事实，不解释为什么。

真源：
[`models.py`](../../src/ego_knowledge/models.py)、
[`registry/` SCHEMA_SQL](../../src/ego_knowledge/registry/)。

## Kind（6 种）

| 值 | dataclass | 用途 |
| --- | --- | --- |
| `source` | `SourceEntry` | 外部材料：链接、文章、视频 |
| `note` | `NoteEntry` | 个人笔记，引用 `source_refs` |
| `dossier` | `DossierEntry` | 专题档案，含 `review_due_at` |
| `concept` | `ConceptEntry` | 概念定义 |
| `decision` | `DecisionEntry` | 决策记录，可被 `superseded_by` |
| `view` | `ViewEntry` | 视图/聚合 |

## 数据库表（共 21 张：核心 17 + kind 字段扩展 4）

`registry.list_tables()` 返回值包含全部 21 张用户表（含 3 张 FTS5 虚拟表），并会包含 FTS5 内部影子表。其中核心表 17 张，kind 字段扩展表 4 张。

### 核心表（17）

1. `entries`：主表，14 列含 `id/kind/title/slug/file_path/status/freshness/confidence/schema_version/domain/created_at/updated_at/frontmatter_json/body`，`kind` 列带 CHECK 约束限定 6 种。
2. `aliases`：`(alias_nfc, entry_id)`，NFC 规范化别名索引。
3. `entry_tags`：`(entry_id, tag)`。
4. `entry_search_terms`：`(entry_id, term)`，触发"三桶"校验。
5. `entry_metrics`：五元指标 + `authority_score REAL NOT NULL DEFAULT 0`（v2.1 新增，PageRank 全图计算，详见下文「图权威传播」）+ `updated_at`。
6. `relations`：`(source_id, target_id, type, source)`，关系图。
7. `entries_fts_cn`：FTS5 表，`tokenize='unicode61 tokenchars '-' remove_diacritics 0'`，应用层用 jieba 预分词后写入；列：title/aliases/search_terms/tags/body。
8. `entries_fts_en`：FTS5 表，`tokenize='unicode61 tokenchars '+-./#$%^&*_=' remove_diacritics 0'`，列：title/aliases/search_terms/body（无 tags）。
9. `entries_fts_tri`：FTS5 表，`tokenize='trigram'`（FTS5 原生 trigram），列：title/aliases/search_terms/body（无 tags）。
10. `access_log`：访问/检索热度日志。
11. `semantic_index_meta`：语义索引元信息（`index_name` PK + `model_id` + `model_revision` + `index_schema_version` + `indexed_at`，5 列）。dense 索引记录为 `dense_bge_m3`。
12. `dense_embeddings`：稠密向量索引（v3/Phase 7 新增）。`entry_id` PK 外键 `entries(id) ON DELETE CASCADE`，`embedding BLOB NOT NULL`（float32 × 1024 = 4096 字节），`embedding_content_hash TEXT NOT NULL`（SHA-256 前 16 字符），`model_id TEXT DEFAULT 'bge-m3'`，`model_revision TEXT NOT NULL`，`indexed_at TEXT NOT NULL`。索引 `idx_dense_hash` 在 `embedding_content_hash` 上。
13. `registry_meta`：含 `schema_version="2.3"`、`maintenance_queue_version="1"`；**domain 词表**也存放在此表 `key='domains'` 的 JSON value 中（无独立 `domains` 表）。
14. `maintenance_queue`：维护队列，承载 doctor、自动规则与 AI 自主操作记录。
    基础列见「AI 自主操作队列字段」前文说明，Phase 8（schema 2.3）新增
    `origin/proposed_op/proposed_payload_json/agent_id`。
15. `source_fields`：source 专属字段。
16. `note_fields`：note 专属字段。
17. `external_watch`：L1 GitHub 轮询监听表，记录外部仓库 watch 目标与轮询状态。

### Kind 字段扩展表（4）

`dossier_fields` / `concept_fields` / `decision_fields` / `view_fields`，分别承载对应 kind 的专属字段（如 `dossier_fields.review_due_at`）。这些表不计入 `list_tables()` 默认基线统计。

> Domain 不是独立表：`_domains.py` 通过 `registry_meta(key='domains')` 存 JSON 数组，详见 `_domains.py:83-111`。

## 枚举

### Status（6）

`draft / active / authoritative / legacy / deprecated / archived`

### Freshness（3）

`stable / watch / volatile`

### RelationType（11）

8 个语义关系 + 3 个实体化字段名：

- `derived_from / related / supersedes / applied_in / evidence_for / part_of / contradicts / depends_on`
- `source_refs / evidence_refs / superseded_by`（实体化字段名，由 `_materialized_relation_rows` 同步到 `relations` 表）

> `promotion_targets` 不进 RelationType，它存的是允许升格到的 kind（如 `"concept"`/`"dossier"`），不是 entry id，留在 frontmatter。

### RelationSource（3）

`confirmed / ai_suggested / ai_confirmed`

## ID 格式

`ek_<short>_<ULID>`，例如 `ek_con_01HXXXX...`。

- short 码：`source→src / note→note / dossier→dos / concept→con / decision→dec / view→view`（见 `models.py::_KIND_SHORT`）
- ULID：26 字符
- slug 与 id 无关，slug 是 entry 的字段，可改（除 concept/dossier/decision 外）

## 图权威传播（authority_score）

`entry_metrics.authority_score` 由 `_graph_authority.compute_pagerank` 基于全图 `relations` 计算（标准 PageRank，damping=0.85，出度归一化，悬挂节点均匀分配）。触发时机：

| 触发方式 | 说明 |
| --- | --- |
| `ek diagnose --recompute-authority` | CLI 手动触发全图重算 |
| `metrics.full_recompute` | 程序化调用全图指标重算 |
| 单点 ingest / `recompute_for_neighbors` | **不触发** PageRank，仅更新五元指标 |

`search/` 融合排序：`final = base × (1 + 0.3 × authority_norm)`，归一化策略 C（`authority_norm = authority / max(authority)`）。

## 实体化关系（materialized）

`_serde._materialized_relation_rows` 将以下字段同步到 `relations` 表：
`relations`（显式声明）、`source_refs`、`evidence_refs`、`superseded_by`。

`promotion_targets` 不写入 `relations` 表。

## AI 自主操作队列字段（schema 2.3）

`maintenance_queue` 基础列：
`id/rule_id/severity/entry_id/channel/status/message/details_json/created_at/updated_at`。
`entry_id` 外键 `ON DELETE SET NULL`。
索引：`idx_mq_status_channel`、`idx_mq_entry`、`idx_mq_created`。

Phase 8 新增 4 列：

| 列 | 含义 |
| --- | --- |
| `origin` | 三态：`human` / `ai_auto` / `ai_proposed` |
| `proposed_op` | AI 提议的操作名，如 `unlink_critical` / `rename` |
| `proposed_payload_json` | 待 approve/reject 执行的参数 JSON |
| `agent_id` | AI 审计追踪标识，如 `ingest-bot` |

## schema_version

当前 `REGISTRY_SCHEMA_VERSION = "2.3"`，写入 `registry_meta(key='schema_version')`。升级见
[how-to/migrate-schema.md](../how-to/migrate-schema.md)。

## 三桶校验（search_terms）

`_validation.validate_search_terms` 强制：

- `len(terms) >= 5`
- 至少一条含中文
- 至少一条 ASCII 字母（无英文用 `""` 占位）
- 至少一条"别称/误写"，既不等于 title 也不互相包含

违反抛 `ValidationError("search_terms 三桶未覆盖: ...")`。

## 文件 ↔ 数据库

文件是真源，`registry/catalog.sqlite` 是派生物。`build_registry()` 可从文件全量重建。详见
[explanation/architecture-layers.md](../explanation/architecture-layers.md)。

## dense 索引（Phase 7 / schema 2.2）

`dense_embeddings` 表存储 BGE-M3 1024 维 float32 向量（BLOB，4KB/条），通过 `_dense_embedder.py` 调 SiliconFlow API 生成，`_dense_index.py` 管理存储与 stale 检测。

### embedding_content_hash

`_embedding_hash.compute_embedding_content_hash` 对每条 entry 生成稳定 SHA-256 前 16 字符，用于检测内容是否变更、是否需要重新 embed。哈希输入（按顺序拼 `\n`）：

1. `title`
2. `slug`
3. `tags`（sorted 后逗号连接）
4. `aliases`（sorted 后逗号连接）
5. `search_terms`（sorted 后逗号连接）
6. `body` 前 2000 字符
7. （仅 source kind）`source_url` + `content_hash`

设计取舍：body 超 2000 字符的部分不参与 hash，该部分变更不触发 stale 检测（非 bug，降级 embed 成本）。

### CLI 命令

- `ek rebuild-dense-index [--stale] [--resume] [--batch-size N]`：全量/增量重建
- `ek search --semantic` / `--no-semantic` / `--backend dense`：dense 路控制

详见 [cli-commands.md](cli-commands.md)。
