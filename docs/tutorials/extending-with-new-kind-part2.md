# tutorials/extending-with-new-kind-part2.md

> 续 [extending-with-new-kind.md](extending-with-new-kind.md)：为新 kind 写测试与文档收尾。预计 10 分钟。
> 默认数据目录是 `~/.ego-knowledge/data`；测试如需真实数据目录，显式设置 `EK_DATA_ROOT`。

前置：part 1 已跑通 1-7 步，新 kind 能 ingest / get / search。

## 1. 写单元测试

`tests/unit/test_models.py`：

```python
def test_experiment_kind_short():
    assert _KIND_SHORT[Kind.EXPERIMENT] == "exp"

def test_experiment_id_parse():
    kind, _ulid = parse_id("ek_exp_01HXXXX...")
    assert kind is Kind.EXPERIMENT

def test_experiment_default_status():
    """新 kind 不应在 STABLE_SLUG_KINDS 里。"""
    from ego_knowledge._entry_store import STABLE_SLUG_KINDS
    assert Kind.EXPERIMENT not in STABLE_SLUG_KINDS
```

## 2. 写集成测试

`tests/integration/test_experiment_kind.py`：

```python
def test_ingest_get_search_experiment(ek_instance):
    payload = {
        "title": "test 番茄钟实验",
        "hypothesis": "25min 不切窗口能写更多",
        "started_at": "2026-04-26",
        "search_terms": ["番茄钟", "pomodoro", "writing", "deep-work", "block-writing"],
    }
    entry = ek_instance.ingest("experiment", payload)
    assert entry.id.startswith("ek_exp_")

    fetched = ek_instance.get(entry.id)
    assert fetched.hypothesis == payload["hypothesis"]

    hits = ek_instance.search("番茄钟")
    assert any(h.id == entry.id for h in hits)
```

## 3. 跑测试

```bash
cd <repo>
uv run pytest tests/unit/ tests/integration/ -k experiment -v
```

预期全 PASS。如果失败，常见原因：

- `_KIND_SHORT` 漏改 → `parse_id` 报 `KEY_ERROR`
- SCHEMA_SQL 没升 → ingest 报 `EK_VALIDATION`（CHECK 约束拦下）
- fields 表没建 → ingest 报 `EK_STORAGE: no such table`

## 4. 加对抗用例

`tests/adversarial/test_experiment_errors.py`：

```python
def test_experiment_missing_hypothesis(ek_instance):
    """JSON Schema 应拦下缺 hypothesis。"""
    payload = {
        "title": "bad",
        "search_terms": ["a", "b", "c", "d", "e"],
    }
    with pytest.raises(ValidationError) as ei:
        ek_instance.ingest("experiment", payload)
    assert ei.value.code == "EK_VALIDATION"
```

## 5. 更新参考文档

按"加 kind"修改：

- [reference/data-model.md](../reference/data-model.md)：Kind 表加一行；fields 表清单加 `experiment_fields`
- [reference/cli-commands.md](../reference/cli-commands.md)：`ingest --kind` 列表
- [explanation/stable-slug.md](../explanation/stable-slug.md)：如果新 kind 是稳定 slug，加进列表

## 6. 写设计说明

新建一份设计说明，例如 `docs/explanation/add-experiment-kind.md`：

- **背景**：为什么需要 experiment kind（个人实验记录无合适归属）
- **决策**：加为顶层 kind 而非 note 子类
- **替代方案**：用 note + tag=experiment（已拒绝，理由：experiment 有强结构化字段 hypothesis/started_at/result）
- **影响**：schema 1.0→1.1；存量数据无影响

## 7. 提交

```bash
cd <repo>
git add .
git commit -m "feat(ego-knowledge): add experiment kind (schema 1.1)"
```

## 下一步

- 加 RelationType：[how-to/add-new-relation-type.md](../how-to/add-new-relation-type.md)
- schema 升级完整考虑：[how-to/migrate-schema.md](../how-to/migrate-schema.md)
- 决策清单（什么时候不该加 kind）：[how-to/add-new-kind.md](../how-to/add-new-kind.md)
