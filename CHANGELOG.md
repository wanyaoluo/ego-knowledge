# Changelog

## [Unreleased]

## [0.2.0]

### Added

- 新增静态前端 demo，覆盖仪表盘、图谱、搜索、深读、结构地图等视图。
- 新增 demo 数据层模块：图数据加载、搜索索引、统计聚合与视觉映射。
- 新增合成公开演示数据，避免发布真实知识库样本。
- 新增 post-commit 可观测性：`PostCommitError` dataclass（label/entry_id/exception）、`EntryStore.post_commit_errors` / `EgoKnowledge.post_commit_errors` property、`EntryStore.run_post_commit_step` 公开方法；事务已提交后失败不再静默丢弃。

### Changed

- `.gitignore` 的数据目录规则改为根目录锚定 `/data/`，避免误忽略源码中的 `data` 子目录。
- 清理公开发布中的内部路径、内部调度说明与密钥样式占位。
- `source_url` 公开来源白名单改为 `knowledge://`，替代不可公开的本机数据路径口径。
- `_entry_store.py` update 方法重构：108 行单体拆为 25 行主体 + 9 个职责单一的私有子方法，事务语义不变。
- `_run_post_commit_steps` docstring 补协调器/执行器配对说明，消除近名歧义。
- doctor unicode 检查分层：`fullwidth_in_body` rule_id 下，`[fm]` 标签检测 frontmatter 全角结构标点残留，`[body]` 标签检测 body 全角空格与全角 ASCII 字母数字；中文正文全角标点不报。
- doctor `broken_relations` 输出结构化 message：`source/target/type/origin` 四字段，支持按 origin 分级处理（ai_suggested / ai_confirmed / confirmed）。
- 测试拆分：fullwidth 测试 → `test_doctor_unicode.py`、broken_relations 测试 → `test_doctor_broken_relations.py`、公共辅助 → `_doctor_helpers.py`。
- 文本归一化分层口径与断裂关系分级处理同步沉淀到维护文档。

## [0.1.0]

### Added

- `ek` CLI：知识库增删改查、五路检索、诊断与统计。
- Core Library（`ego_knowledge.core.EgoKnowledge`）：可在 Python 代码中直接调用。
- MCP Server（`ek-mcp`）：供 AI 助手接入的 MCP 服务端。
- 五路检索引擎：exact / BM25 / graph / dense / fusion。
- 知识升格：note → concept → decision → dossier 层级提升。
- 文件级健康检查（`ek doctor`）与知识级诊断（`ek diagnose`）。
- `ek-archive`：脏 concept 归档工具。
- AGPL-3.0 + dual-license 商业授权。
- GitHub Actions CI（ruff + mypy + pytest）。

### Known Limitations

- dense 语义检索需配置 SiliconFlow API Key。
- 数据索引基于 SQLite。
