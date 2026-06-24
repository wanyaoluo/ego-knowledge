# 迁移 schema

> Schema 升级（schema_version 跳版本）时怎么改代码、改数据、保兼容。
> 默认数据目录是 `~/.ego-knowledge/data`；下文用 `EK_DATA_ROOT` 显式指向数据目录。

当前：`REGISTRY_SCHEMA_VERSION = "2.3"`，写在
[`registry/_schema.py`](../../src/ego_knowledge/registry/_schema.py)。

## 何时算 schema 升级

| 改动 | 是否升 schema_version |
| --- | --- |
| 加新 kind | 是（次要版） |
| 加新 RelationType | 否（枚举扩展，旧数据兼容） |
| 加新可选字段 | 否 |
| 改字段名 | 是（破坏） |
| 改字段类型 | 是（破坏） |
| 改 fts 分词策略 | 否（重建索引即可） |
| 改 SCHEMA_SQL 表结构 | 是 |
| 加新表 | 是（次要版） |

破坏改动用主版本号（2.0），向后兼容用次要版本号（1.1）。

## 步骤

### 1. 改源码

- `models.py`：dataclass / 枚举
- `registry/_ddl.py::SCHEMA_SQL`：表结构
- `registry/_schema.py::REGISTRY_SCHEMA_VERSION`：版本号
- `_validation.py`：jsonschema 与字段校验
- `schemas/*.schema.json`：JSON Schema

### 2. 写迁移脚本

升级窗口内临时放到根级 `migrations/<from>-to-<to>.py`
作为 CLI 入口；可 import 的实现放到
`src/ego_knowledge/migrations/`。迁移完成并成为维护基线后，
一次性脚本应退役，避免长期保留旧 schema 路径。

```python
def migrate(data_root: Path) -> None:
    """
    1. 备份 catalog.sqlite 到 _backup/
    2. 遍历所有 .md
    3. 改 frontmatter 字段（重命名/默认值填充）
    4. registry.build_registry() 重建
    5. 写 schema_version
    """
```

迁移脚本必须：

- **只改文件**，不直接改 SQLite（DB 是派生物）
- 全程在事务内（用 `transactional_write`）
- 失败可重入

### 3. 跑迁移

```bash
# 备份
export EK_DATA_ROOT="${EK_DATA_ROOT:-$HOME/.ego-knowledge/data}"
cp -r "$EK_DATA_ROOT" "$EK_DATA_ROOT.backup-$(date +%Y%m%d)"

# 跑迁移（文件名按本次升级版本替换）
uv run python migrations/<from>-to-<to>.py \
      "$EK_DATA_ROOT"

# 验证
ek doctor
```

### 4. 升 registry_meta

迁移脚本最后写：

```sql
INSERT INTO registry_meta (key, value)
VALUES ('schema_version', '<target_version>')
ON CONFLICT(key) DO UPDATE SET value = excluded.value;
```

`registry_meta` 表记录当前活动 schema。`Registry.__init__` 启动时检查，版本不匹配会拒绝写入。

### 5. 更新文档

- [reference/data-model.md](../reference/data-model.md)：schema_version 字段
- [CHANGELOG.md](../../CHANGELOG.md)：记录版本升级说明

## 兼容策略

### 向后读

旧 frontmatter 文件应能被新代码读懂。`_fm_to_entry` 对缺失字段填默认值。如果不能，迁移脚本必须先跑。

### 向前写

新代码不应写入旧版本能读懂的格式。一旦升级即"截断"，旧版本不再能读新数据。

### 混合不允许

不允许部分文件 1.0 部分 1.1。要么全迁完，要么全回滚。`Registry.__init__` 会查 registry_meta 与每个文件的 schema_version。

## 不要做

- ❌ 直接 `ALTER TABLE` SQLite — DB 是派生物，会被下次 `build-registry` 抹掉
- ❌ 跳过迁移脚本只升版本号 — 旧数据会被新代码当作畸形
- ❌ 手改 frontmatter 的 schema_version 字段 — 会破坏 doctor 检查

## 紧急回滚

```bash
rm -rf "$EK_DATA_ROOT"
cp -r "$EK_DATA_ROOT.backup-<date>" "$EK_DATA_ROOT"
git checkout <旧 commit> -- .
```

数据恢复 + 代码回退必须配套。

## 多人/多设备

EgoKnowledge 是单用户工具，但跨设备：

1. 升级前在所有设备上 commit + push
2. 在主设备升级（WSL）
3. 同步到其他设备
4. 其他设备 `uv sync` 代码 + `ek doctor` 验证

不要在多设备同时升级，一定漂移。

## 后续工具

需要 schema 升级时使用双层迁移目录：

- `migrations/`：命令行入口，文件名可包含版本号和横线。
- `src/ego_knowledge/migrations/`：Python 包内实现，文件名必须可 import。

历史一次性迁移脚本不作为常驻工具维护；当前 schema 2.3 旧迁移脚本已退役。
