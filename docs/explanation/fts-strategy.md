# explanation/fts-strategy.md

> EgoKnowledge 三路 FTS5 索引设计：jieba / trigram / ASCII，为什么不是一路。

真源：
[`registry/` SCHEMA_SQL](../../src/ego_knowledge/registry/)、
[`tokenizer.py`](../../src/ego_knowledge/tokenizer.py)、
[`search/`](../../src/ego_knowledge/search/)。

## 三个索引

| 表 | FTS5 tokenize | 列 | 用途 |
| --- | --- | --- | --- |
| `entries_fts_cn` | `unicode61 tokenchars '-' remove_diacritics 0`（应用层预先 jieba 分词后写入） | title / aliases / search_terms / tags / body | 中文主路径 |
| `entries_fts_tri` | `trigram`（FTS5 原生 trigram tokenizer） | title / aliases / search_terms / body | 中文兜底 + 短词召回 |
| `entries_fts_en` | `unicode61 tokenchars '+-./#$%^&*_=' remove_diacritics 0` | title / aliases / search_terms / body | 英文/代码标识符 |

注：`fts_cn` 不直接用 jieba tokenizer（FTS5 自带的 unicode61 不支持中文分词），而是在应用层调用 `tokenizer.tokenize` 把文本预切成空格分隔词流再写入。`fts_en` 的 tokenchars 保留代码标识符常见符号，让 `getUserById` / `v1.27.3` 不被切散。

`tags` 列只在 `_cn` 索引存在，tags 是中文短词域，trigram 和英文索引重复打分会污染结果。

## 为什么不是一路

### 一路 jieba 的问题

jieba 是统计分词，对短语召回好，但：

- 代码标识符 `getUserById` 会被切成无意义片段
- 罕见词或新词被切成单字 → 召回率崩
- 数字版本号 `v1.27.3` 不可控

### 一路 trigram 的问题

字符 trigram 对短词全召回，但：

- 中文长词命中分散，BM25 打分不稳
- 噪音多 → 排序质量差
- 标识符精度尚可但权重难调

### 三路并查 + BM25 融合

[`search.SearchRouter`](../../src/ego_knowledge/search/) 做的就是分而治之：

```text
parse_query → 切分查询为 segments（CJK / ASCII_WORD / NUMBER / VERSION / SYMBOL_TOKEN / ...）
            → 对每段挑合适的 backend（CJK→cn+tri，ASCII→en+tri）
            → 各自 bm25 打分
            → 融合（按 backend 权重 + 文档频率归一化）
            → graph 扩展邻居
```

每路索引都按它擅长的形态打分，融合时按段权重叠加。

## BM25 权重

`search/` 三组常量：

```python
BM25_WEIGHTS_CN  = (3.0, 2.0, 2.0, 1.5, 1.0)   # title/aliases/search_terms/tags/body
BM25_WEIGHTS_TRI = (2.0, 1.5, 1.5, 1.0)        # 不含 tags
BM25_WEIGHTS_EN  = (3.0, 2.0, 2.0, 1.0)        # 不含 tags
```

设计取舍：

- title 永远权重最高，人类标题最浓缩
- aliases 与 search_terms 同权，别名等价于命名空间
- tags 仅 cn 索引参与，避免英文索引误召
- body 最低，长文本噪音多

权重可通过 SearchRouter 注入覆盖（仅测试用）。

## 三桶 search_terms 与 FTS 的关系

`_validation.validate_search_terms` 强制 search_terms 包含中文 + ASCII + 别称（详见 [data-model.md](../reference/data-model.md)）。
原因正是为三路索引各自喂料：

- 中文 term → fts_cn 命中
- ASCII term → fts_en 命中
- 别称（误写/缩写）→ 扩展召回，避免主词漏召

无三桶则三索引各自盲区都会暴露。

## jieba 自定义词典

`tokenizer.rebuild_custom_dict(registry, output_dir)` 把所有 aliases + tags 抽出，写成 `ek-auto.txt`，
让 jieba 把这些词当整词不切。`sync_runtime_words` 在每次 ingest 后增量注入新词，避免每次都全量重建。

代价：jieba 加自定义词太多会拖慢分词。EgoKnowledge 量级（千级条目）可承受。

## fallback 路径

`tokenize` 在 jieba 异常或返回空时退到 `_trigram_fallback`，并往 fallback log 追加记录。
log 路径默认 `<data_root>/logs/refresh/jieba-fallback.log`（需调用方传入）。

## 索引同步与重建

**单条同步**（ingest/update/rename）：`Registry._upsert_entry` 内显式调用 `_sync_fts_index(entry, body)` 写三路 FTS 表。**FTS 同步是应用层显式动作，SCHEMA_SQL 中没有任何 `CREATE TRIGGER`**，绕过 `_upsert_entry` 直接写 entries 表会漏 FTS 更新。

**全量重建**：`build_registry(data_root)` 从 .md 文件全量重扫并重建 catalog（含三路 FTS）。触发时机：

- `ek build-registry` 全量
- `ek doctor --repair` 检测到 FTS 缺失或漂移

## 演进

- v2 可能加语义检索（向量索引），作为第四路 backend
- 中文分词器可换（如 pkuseg / hanlp），SearchRouter 接口不变
- 应用层同步若成为瓶颈，可考虑迁移到 SQLite trigger（需评估跨表同步复杂度）
