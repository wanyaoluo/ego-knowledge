# ego-knowledge · 检索契约

把 `ek_search` 的参数语义、后端行为和 `SearchResult` 返回结构讲清楚。

## SearchResult 结构

真源：`search/` 的 `SearchResult` dataclass。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | `str` | 命中的条目 ID |
| `score` | `float` | 路由器最终排序分数（含 authority 加权） |
| `backends` | `list[str]` | 命中来源：`exact` / `fts_cn` / `fts_en` / `fts_tri` / `graph` / `dense` 中的一个或多个；这是返回粒度，比参数里能传的 `bm25` 更细 |
| `snippet` | `str \| None` | 根据 query 在 body/title 中截取的 raw text 摘要；Markdown/HTML 展示必须纯文本化或转义 |

## 查询参数

| 参数 | 默认值 | 行为 |
| --- | --- | --- |
| `query` | 必填 | 先做 fullwidth 规范化 + NFC；空字符串返回空列表 |
| `kinds` | `None` | 只保留匹配的 kind；不在列表里的结果被过滤 |
| `filters` | `None` | 对 entry 字段精确匹配（见下文） |
| `backends` | `None` | 可传 `exact`/`bm25`/`graph`/`dense`；不传时默认 `exact`+`bm25`，dense 可用时自动追加 |
| `limit` | `20` | 最终排序后截断 |
| `expand_graph` | `True` | `backends` 显式含 `graph` 时才扩一跳邻居 |
| `include_archived` | `False` | `True` 时不过滤 `status=archived` 的条目 |

## backends 的真实行为

### `exact`

命中 alias/title/ID/tag/search_term 时返回。一旦有结果直接短路，不再跑 BM25/graph/dense。

### `bm25`

内部按分词自动走 `fts_cn`（CJK）/ `fts_en`（ASCII）/ `fts_tri`（trigram）。MIXED/SYMBOL/VERSION 等类型会触发多路合并。

### `graph`

基于 BM25 种子扩一跳邻居；邻居得分为种子分数的 50%。`backends` 未显式含 `graph` 时即使 `expand_graph=True` 也不扩图。

### `dense`

需 SiliconFlow API Key 配置且索引已构建。未配置时自动降级回 `exact`+`bm25`。仅用 dense 时（`backends=["dense"]`）直接返回语义结果。

## filters 精确匹配口径

路由器用 `_match_filters` 做 entry 字段精确判断，不做模糊匹配。

| filter key | 示例 | 语义 |
| --- | --- | --- |
| `domain` | `{"domain": "rag"}` | 精确匹配规范化后的 domain |
| `status` | `{"status": "active"}` | 精确匹配（枚举取 `.value`） |
| `freshness` | `{"freshness": "watch"}` | 精确匹配 |
| `tags` | `{"tags": "weekly"}` | 单值：条目 tags 包含该值 |
| `tags` | `{"tags": ["rag", "retrieval"]}` | 列表：要求全部包含 |

## 排序规则

1. `score` 降序（含 authority 加权：`score *= 1 + 0.3 * authority_norm`）
2. `score` 相同时按 `id` 升序
3. 最后裁成 `limit`

## 推荐组合

| 目标 | 参数 |
| --- | --- |
| 查有没有完全命中 | `{"query": "RAG", "backends": ["exact"]}` |
| 日常检索 | `{"query": "RAG 检索增强生成"}` |
| 看图邻居 | `{"query": "RAG", "backends": ["bm25", "graph"], "expand_graph": true}` |
| 语义相似 | `{"query": "知识库架构", "backends": ["dense"]}` |
| 只查某个域 | `{"query": "排序", "filters": {"domain": "retrieval"}}` |
| 含已归档 | `{"query": "旧决策", "include_archived": true}` |

## 结果为空排查清单

1. `query` 太短或被错误切词
2. `kinds` 把目标类型全过滤了
3. `filters` 用了近义词而非精确值
4. `backends` 只传了 `graph`（无种子则无结果）

## access log

`search()` 成功后批量写 `access_log`，影响 `retrieval_heat`。空结果不写。单条读取用 `ek_get`，不要用 `search` 代替。
