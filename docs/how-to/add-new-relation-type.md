# 新增 RelationType

> 加一种新的 RelationType（如 `inspired_by / blocked_by`）的完整步骤。

不需要升级 schema_version，枚举扩展是向后兼容改动。

## 决策先行

### 命名约定

- 全小写 + 下划线
- 主语→动词→宾语视角，主语是关系发起方
- 可读："A `derived_from` B" = A 派生自 B
- 不与现有 8 种重复或近义

现有 8 种：

`derived_from / related / supersedes / applied_in / evidence_for / part_of /
contradicts / depends_on`

### 是否双向

每个 RelationType 在数据上是单向（source → target）。如需双向语义：

- 要么定义"对偶对"（`supersedes` ↔ `superseded_by`，但 superseded_by 在 EgoKnowledge 是字段不是 relation）
- 要么只定义一种，遍历时双向查（`Registry.list_relations(direction='in')`）

新加时优先单向，遍历端做双向，避免冗余。

## 三处必改

### 1. 枚举（[`models.py`](../../src/ego_knowledge/models.py)）

```python
class RelationType(StrEnum):
    DERIVED_FROM = "derived_from"
    # ...
    INSPIRED_BY = "inspired_by"   # ← 新加
```

### 2. JSON Schema（`schemas/`）

如果 schema 里枚举了 RelationType（通常用 `$ref` 引到一处），更新该处的 `enum` 数组。
确认改动覆盖所有 kind 的 schema。

### 3. 文档（[reference/data-model.md](../reference/data-model.md)）

在 RelationType 列表加新值 + 一行用途说明。

## 不需要改

- `registry/` SCHEMA_SQL：`relations.type` 列没枚举约束（CHECK），新值直接写入
- `_validation.py`：用 `RelationType(rel_type)` 动态验证，新枚举自动生效
- FTS：relations 不进 FTS

## 测试加什么

`tests/unit/test_models.py`：

- 枚举值断言
- `RelationType("inspired_by")` 能解析

`tests/integration/`：

- `ek link <a> <b> --type inspired_by` 应成功
- `ek related <a>` 应能返回该关系
- 错误输入（typo）应抛 `ValidationError`

## 何时需要新关系类型

新关系类型是设计决策，不是字段堆砌。加之前问：

- 是不是 `related` 加 tag 就够？
- 是不是 evidence_refs / source_refs 这类字段更合适？
- 是不是该 promote 成 kind（如 `dependency` 应该是 view）？

只有以下情况建议新加：

- 大量 entry 间会形成该关系（> 20 个潜在用例）
- 关系对检索/升格/裁决有特殊语义
- 现有类型混用会污染统计

否则用 `related` + tag 区分。

## 不会被自动渲染的位置

加新 RelationType 后还需手动同步：

- 用户文档 `docs/`（如存在）
- AI agent 的 skill 提示词（如有 AI 编排需求，需同步更新对应配置）
- 可视化工具的图例（如未来加 view）

这些不在自动测试覆盖内，加 RelationType 时务必检查。

## 删除/重命名 RelationType

不能直接删/改名。已有数据会指向旧值，破坏读取。
正确做法：

1. 先扫所有 `.md` + DB 中是否有该值
2. 如有，迁移脚本批量改成新值
3. 改代码 + schema
4. 走 [migrate-schema.md](migrate-schema.md) 流程，升 schema_version
