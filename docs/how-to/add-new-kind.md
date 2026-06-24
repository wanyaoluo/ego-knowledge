# 新增 Kind

> 加一种新的 Kind（如 `experiment / habit`）的完整步骤。

加 kind 是 schema 升级，必须升 `REGISTRY_SCHEMA_VERSION`。

## 决策先行

加 kind 前问：

- 现有 6 种是否真不能覆盖？source/note/dossier/concept/decision/view 已经覆盖了"原始资料/个人笔记/专题档案/概念定义/决策记录/聚合视图"
- 新 kind 是否有独特字段（不是只换名字）
- 是否有独立的生命周期或 promotion 路径

只有满足以上，才考虑加。

## 改动清单

### 1. 数据模型（[`models.py`](../../src/ego_knowledge/models.py)）

```python
class Kind(StrEnum):
    SOURCE = "source"
    # ...
    EXPERIMENT = "experiment"   # ← 新加

@dataclass(slots=True)
class ExperimentEntry(EntryBase):
    hypothesis: str = ""
    started_at: date | None = None
    result: str = ""

KIND_TO_CLASS[Kind.EXPERIMENT] = ExperimentEntry
_KIND_SHORT[Kind.EXPERIMENT] = "exp"   # id 中的短码
_PERSISTENT_FIELDS |= {"hypothesis", "started_at", "result"}
```

### 2. SCHEMA_SQL（[`registry/`](../../src/ego_knowledge/registry/)）

- `entries.kind` 列的 CHECK 约束加新值
- 加 `experiment_fields` 表（如果有 kind 专属字段）：

```sql
CREATE TABLE IF NOT EXISTS experiment_fields (
    entry_id TEXT PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
    hypothesis TEXT,
    started_at TEXT,
    result TEXT
);
```

### 3. JSON Schema

新建 `schemas/experiment.schema.json`，参考现有 6 个 kind 的 schema。
关键字段：`type/title/required/properties` + 引 `_common.schema.json` 的公共字段。

### 4. Service 默认值（[`_entry_store.py`](../../src/ego_knowledge/_entry_store.py)）

```python
_DEFAULT_STATUS[Kind.EXPERIMENT] = Status.ACTIVE
_DEFAULT_FRESHNESS[Kind.EXPERIMENT] = Freshness.WATCH
```

如果是稳定 slug 类型：

```python
STABLE_SLUG_KINDS = frozenset({..., Kind.EXPERIMENT})
```

### 5. CLI（[`cli.py`](../../src/ego_knowledge/cli.py)）

```python
KINDS = ["source", "note", "dossier", "concept", "decision", "view", "experiment"]
```

### 6. 升级 schema_version

`REGISTRY_SCHEMA_VERSION = "1.1"`。

### 7. 写迁移脚本

新 kind 不影响旧数据，迁移脚本只需：

- 备份 catalog.sqlite
- 升 registry_meta.schema_version
- `build_registry` 重建

详见 [migrate-schema.md](migrate-schema.md)。

### 8. 更新文档

- [reference/data-model.md](../reference/data-model.md)：Kind 表 + 字段表
- [reference/cli-commands.md](../reference/cli-commands.md)：ingest --kind 列表
- 用户文档 `docs/`

## 是否进入 promotion 路径

`PromotionService` 当前定义的 promotion 链是：

```text
source → note → dossier
concept (终态)
decision (终态)
view (终态)
```

新 kind 要不要加入链？决定路径：

- 新 kind 是终态 → 不进 PromotionService，单独 ingest
- 新 kind 来自某 kind 的"提炼" → 改 `PromotionService.promote()`，加分支

加 promotion 分支需同步改：

- [`_promotion.py`](../../src/ego_knowledge/_promotion.py)：promote 方法
- promotion_targets 字段引用同步逻辑
- 测试：`tests/regression/test_promotion.py`

## 测试覆盖

`tests/unit/`：

- 模型 dataclass 默认值
- jsonschema 校验

`tests/integration/`：

- `ek ingest --kind experiment --payload ...` 全链路
- `ek search` 在新 kind 上工作
- `ek doctor` 不报新 kind 异常
- 如有 promotion，跑一遍升级路径

## 自动绑定的位置

加 kind 后**会自动生效**：

- FTS 索引（三索引按字段不按 kind）
- 三桶 search_terms 校验
- 错误契约
- transactional_write

## 不会自动同步

- AI skill 提示词（如有 AI 编排需求，需同步更新对应配置）
- dashboard（如未来有）
- 用户使用文档

## 删除 kind

不要直接删。先把所有该 kind 的 entry 迁移成其他 kind 或归档，再走 migrate-schema 升主版本号。

## 简化路径

如果只是想分类已有 kind 的 entry，**用 tags 或 domain**。kind 是数据形状的分类，
tag 是话题的分类。混淆这两层会让 schema 膨胀。
