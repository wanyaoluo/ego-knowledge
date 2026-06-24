# ego-knowledge

![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)
![CI](https://github.com/wanyaoluo/ego-knowledge/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)

面向中文的个人知识库。文件存储，命令行操作，AI 可以直接读写。

## Why

知识库用久了最怕一件事：旧内容成了不想翻的包袱。

ego-knowledge 把**低摩擦**作为设计底线：

- **文件是真源**：所有知识以 Markdown 文件存储，索引（catalog.sqlite）是派生物，坏了随时重建。用户的内容不被锁定在任何数据库或云服务里。
- **AI 是一等公民**：MCP Server 不是事后集成的插件，而是与 CLI 平级的原生入口。AI 助手直接读写知识库，不需要逆向操作界面。
- **只做核心引擎**：不做编辑器（用户用什么写都行），不做云同步，不做社区。克制意味着每一层都可以被替换、被脚本化、被信任。

## How It Differs

| 维度 | ego-knowledge | 笔记应用（如 Obsidian） | 云知识库（如 Notion） |
| --- | --- | --- | --- |
| 数据归属 | 文件真源，本地优先 | 文件真源，本地优先 | 云优先，数据锁定 |
| 检索能力 | 五路（exact/BM25/graph/dense/fusion） | 关键词 + 插件 | 全文 + 过滤器 |
| 知识层级 | note → concept → decision → dossier 升格 | 扁平或标签 | 数据库视图 |
| AI 集成 | MCP Server 原生，Core Library 可直接调用 | 插件，事后集成 | API，事后集成 |
| 适用场景 | 开发者 / AI 助手 / 自动化 | 终端用户笔记 | 团队协作 |

ego-knowledge 不是笔记软件的替代品，而是**知识库引擎**：给开发者和 AI 助手提供结构化的知识存取能力。

## Features

| 特性 | 说明 |
| --- | --- |
| **五路检索** | exact / BM25 / graph / dense（语义）/ fusion 组合检索 |
| **知识升格** | note → concept → decision → dossier 层级提升，支持关系声明与图遍历 |
| **三入口** | CLI（`ek`）、Core Library（`ego_knowledge.core.EgoKnowledge`）、MCP Server（`ek-mcp`） |
| **中文优先** | jieba 分词、NFC 规范化、术语一致性技术强制 |
| **自动诊断** | 文件级健康检查（`ek doctor`）+ 知识级诊断（`ek diagnose`），含衰减治理 |
| **事务安全** | tmp + rename + BEGIN IMMEDIATE，文件与 SQLite 原子写入 |

## Quick Start

```bash
git clone https://github.com/wanyaoluo/ego-knowledge.git
cd ego-knowledge
uv sync
```

```bash
export EK_DATA_ROOT=~/my-knowledge
uv run ek build-registry
uv run ek ingest --kind source --payload '{"title":"Hello","source_type":"web","source_url":"https://example.com","content_hash":"demo","search_terms":["hello","demo"],"tags":["demo"]}'
uv run ek search hello
```

未设置 `EK_DATA_ROOT` 时，默认使用 `~/.ego-knowledge/data`。

## Semantic Search

dense 语义检索需配置 SiliconFlow API Key：

```bash
export SILICONFLOW_API_KEY=<your-siliconflow-api-key>
```

配置后 `ek search` 自动启用 dense 路。`ek rebuild-dense-index` 全量构建索引。

## MCP Server

```bash
uv --directory <repo> run ek-mcp
```

数据目录通过 `EGOKNOWLEDGE_DATA_ROOT` 指定。各客户端配置示例见 [`docs/reference/mcp-tools.md`](docs/reference/mcp-tools.md)。

## Roadmap

**已实现**：

- 五路检索
- 知识升格
- MCP Server
- 自动诊断
- 衰减治理
- AI 自主操作门禁
- 文本归一化（分层规则 + 存量修复 normalize_legacy / cleanup_broken_relations）

**计划中**：

- 多向量 / 重排层（late interaction，提升语义检索精度）
- 简繁归一化（OpenCC，降低中文混召，当前延后到后续版本评估）
- 拼音 / 首字母联想检索
- 时间表达归一化（农历 / 朝代 / 相对时间）

## Docs

- 快速上手：[`docs/tutorials/first-knowledge-entry.md`](docs/tutorials/first-knowledge-entry.md)
- CLI 命令参考：[`docs/reference/cli-commands.md`](docs/reference/cli-commands.md)
- 架构说明：[`docs/explanation/architecture-layers.md`](docs/explanation/architecture-layers.md)
- 数据模型：[`docs/reference/data-model.md`](docs/reference/data-model.md)
- 静态前端演示：[`docs/frontend-demo/`](docs/frontend-demo/)
- 完整文档入口：[`docs/README.md`](docs/README.md)

## License

AGPL-3.0，详见 [LICENSE](LICENSE)。与 AGPL-3.0 不兼容的使用场景（如闭源或商业集成）可联系维护者获取单独的 commercial license。

## Contributing

欢迎提交 Issue 和 Pull Request，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。
