# tutorials/first-knowledge-entry.md

> 默认数据目录 `~/.ego-knowledge/data`，下文用 `EK_DATA_ROOT` 指向。

## 准备

```bash
cd <repo>
uv sync
export EK_DATA_ROOT="${EK_DATA_ROOT:-$HOME/.ego-knowledge/data}"
mkdir -p "$EK_DATA_ROOT/registry"
uv run ek build-registry
ls "$EK_DATA_ROOT/registry/"   # 应看到 catalog.sqlite
```

`build-registry` 在空目录上创建 `catalog.sqlite` 与表结构，再跑一次幂等。

## 1. 写入一条 source

`source` 是最简单的 kind，保存外部链接：

```bash
uv run ek ingest --kind source --payload '{
  "title": "Diátaxis 文档框架",
  "source_type": "url",
  "source_url": "https://diataxis.fr/",
  "search_terms": ["Diátaxis", "文档框架", "documentation framework", "技术写作", "doc-arch"],
  "tags": ["documentation", "framework"]
}'
```

返回 JSON 含 `id`，形如 `ek_src_01HXXXX...`。记下来。

> **search_terms 必须 ≥5 条且覆盖三桶**：至少一条中文、至少一条 ASCII 字母、至少一条非 title 的别称。详见 [reference/data-model.md](../reference/data-model.md#三桶校验search_terms)。

## 2. 看刚写入的内容

```bash
uv run ek get ek_src_01HXXXX...
```

输出含 frontmatter + body。
对应的 `.md` 文件位置：

```bash
find "$EK_DATA_ROOT" -name "*.md" | head
```

打开 `.md` 看 frontmatter，应与 `ek get` 返回一致。**文件是真源**，SQLite 是派生物。

## 3. 检索

```bash
uv run ek search "文档框架"            # 中文
uv run ek search "documentation framework"  # 英文
uv run ek search "Diátaxis"            # 别称
```

三种查询都应能命中，这是三路 FTS 索引（cn/en/tri）的效果。详见 [explanation/fts-strategy.md](../explanation/fts-strategy.md)。

## 4. 写入一条 concept

`concept` 是稳定 kind，slug 不能改：

```bash
uv run ek ingest --kind concept --payload '{
  "title": "Diátaxis 框架",
  "search_terms": ["Diátaxis", "diataxis", "文档四象限", "documentation framework", "doc-arch"],
  "evidence_refs": ["ek_src_01HXXXX..."],
  "tags": ["documentation"]
}'
```

`evidence_refs` 指向第 1 步的 source id。这会自动在 `relations` 表插入 `evidence_for` 关系（concept ← source）。

返回 id 形如 `ek_con_01HYYYY...`。

## 5. 查关系

```bash
uv run ek related ek_con_01HYYYY... --depth 1
```

应看到 source 作为 evidence 出现。
试 depth 2 看二跳邻居（当前应该没有，下一步加）。

## 6. 加一条手动关系

写一条 note：

```bash
uv run ek ingest --kind note --payload '{
  "title": "应用 Diátaxis 到 ego-knowledge 文档",
  "search_terms": ["Diátaxis", "ego-knowledge", "文档重构", "doc refactor", "diataxis-application"],
  "source_refs": ["ek_src_01HXXXX..."]
}'
```

返回 `ek_note_01HZZZZ...`。

把 note 与 concept 关联（"该 note 是 concept 的应用案例"）：

```bash
uv run ek link ek_note_01HZZZZ... ek_con_01HYYYY... --type applied_in
```

再查：

```bash
uv run ek related ek_con_01HYYYY... --depth 2
```

应看到 source（depth 1）+ note（depth 1，反向）+ note 的 source_refs 指向的 source（depth 2）。

## 7. 健康检查

```bash
uv run ek doctor
```

应全 PASS。
看一眼统计：

```bash
uv run ek stats --by kind
```

应看到 source=1 / concept=1 / note=1。

## 下一步

- 加新 kind：[extending-with-new-kind.md](extending-with-new-kind.md)
- 报错 / 命令 / 概念：[reference/](../reference/) · [explanation/](../explanation/)
