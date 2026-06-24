# explanation/stable-slug.md

> 为什么 concept / dossier / decision 的 slug 是稳定 ID，不能用 update 改，必须走 rename()。

真源：
[`_validation.validate_update_fields`](../../src/ego_knowledge/_validation.py)、
[`_mutations.MutationService.rename`](../../src/ego_knowledge/_mutations.py)、
[`slug.generate_slug`](../../src/ego_knowledge/slug.py)。

## 哪些 kind 有稳定 slug

| Kind | slug 性质 | 改 slug 路径 |
| --- | --- | --- |
| `concept` / `dossier` / `decision` | **稳定** | 必须 `rename()` |
| `source` / `note` / `view` | 可变 | `update()` 直接改 |

## 为什么要稳定

entry id 本身是 `ek_<short>_<ULID>`，slug 不进 id。但这三类 kind 的 slug 决定**文件路径**（`path_for_entry`），且文件路径被大量外部材料引用：

- `decision` 记录互相引用（supersedes / superseded_by）
- `dossier` 内嵌 evidence_refs / source_refs
- `concept` 被 note / dossier / decision 引用

随手改 slug → 文件路径变 → 外部引用断链；同时 entry id 不变但被各种 ref 引用的视图错位。

`source / note / view` 不构成知识骨架，被引用频率低，可以宽松。

## update 为什么直接禁止改 slug

`validate_update_fields` 在前面就拦截：

```python
# _validation.py 中实际签名（参数化稳定 kind 集合）：
if field_name == "slug" and entry.kind in stable_slug_kinds:
    raise ValidationError("concept/dossier/decision 改 slug 请走 rename()")
```

`stable_slug_kinds` 由 `_entry_store.STABLE_SLUG_KINDS = frozenset({Kind.CONCEPT, Kind.DOSSIER, Kind.DECISION})` 提供，调用时由 service 层注入。

不是事后修，而是事前禁。原因：

- update 路径不走引用同步逻辑，硬塞会留下隐性断链
- 显式区分两条路径让调用方明白"改 slug 是大动作"

## rename 做了什么

`MutationService.rename(entry_id, new_slug)`（见 `_mutations.py:58-80`）：

```text
1. 校验 entry.kind ∈ STABLE_SLUG_KINDS（concept/dossier/decision），否则 ValidationError
2. 验证新 slug 合法（validate_explicit_slug 规则）
3. 计算新文件路径，若新路径已存在 → ConflictError
4. 在 transactional_write 内：
   - 改 entry.slug 与 updated_at（id 不动）
   - 物理文件 rename（旧路径 → 新路径）
   - upsert_entry 重写 frontmatter
   - 跨文件扫描 body 内 Markdown 链接，重写指向旧路径的相对链接
   - FTS 索引同步（_sync_fts_index）
```

**entry id（`ek_<short>_<ULID>`）从头到尾不变**，所有 relations / evidence_refs 等通过 id 引用的地方都不需要动。改的只是 slug、文件路径，以及 body 里通过路径引用的 Markdown 链接。整个过程在单一 SQLite 事务内，要么全成要么全回滚。

## 为什么 id 不变 slug 还要 rename

可能有人想：既然 id 稳定，slug 自由改岂不是更简单？**没采用**，原因：

- slug 决定文件路径，文件名是"人类可见的标识"，稳定 slug 类型一旦改名，外部笔记/分享链接里的路径会失效
- `concept/dossier/decision` 是长生命周期沉淀物，rename 是高成本动作，应走显式仪式而非 update() 顺手改
- 反过来 `note/source/view` 的 slug 跟着 title 走，不进 STABLE_SLUG_KINDS，自然不需要 rename

## slug 生成规则

`generate_slug(title)`：

1. NFC 规范化
2. 应用术语映射（`C++ → cpp` 等，见 `slug.TERM_MAP`）
3. 保留 CJK + ASCII 字母数字 + `-`，其他字符替换为 `-`
4. 多 `-` 合并，去首尾 `-`
5. 截断到 40 字符，去尾 `-`

输出例：

- `"FastAPI 应用架构"` → `FastAPI-应用架构`
- `"C++ 对象模型"` → `cpp-对象模型`
- `"v1.27.3 升级笔记"` → `v1-27-3-升级笔记`

空 slug（标题全是符号）抛 `ValueError`，由调用方包装为 `ValidationError`。

## 中文优先

slug 不像传统 URL 强制 ASCII。原因：

- 仓库面向中文使用者
- `concept/事件溯源` 比 `concept/event-sourcing` 在 ek search 中可读性更好
- 文件名在 macOS/Linux 都支持 NFC 中文

代价：跨平台传输需注意 NFC vs NFD 规范化。`unicode_utils.to_nfc` 已在所有入口处理。

## 改 slug 的代价

rename 成本与该 entry 被引用次数成正比：

- 孤立 entry：~10ms
- 被 50 个 entry 引用：~100ms
- FTS 索引刷新另算

不要把 slug 当显示名，title 是显示名，slug 是机器 id。要改名改 title 即可。

## 与 alias 的关系

如果只是想给 entry 多个名字，**加 alias** 而不是改 slug。aliases 字段支持任意中英文同义词，
检索时 `ek search` 会通过 NFC 别名命中。
