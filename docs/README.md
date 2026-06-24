# ego-knowledge 开发文档

面向 **开发者 / 维护者** 的文档。终端用户使用策略见本目录下
[`how-to/`](how-to/) 与 [`tutorials/`](tutorials/)，本目录不重复，只交叉引用。

## 文档架构（Diátaxis）

按"学习 vs 工作"和"实践 vs 理论"两轴切四象限。新增内容请先决定归属象限，避免错放。

| 象限 | 目录 | 何时进入 |
| --- | --- | --- |
| 教程（learning） | [tutorials/](tutorials/) | 第一次接触，跟着步骤跑通 |
| 操作指南（task） | [how-to/](how-to/) | 已熟悉，目标明确，要查"怎么做某事" |
| 参考（lookup） | [reference/](reference/) | 查字段、查命令、查错误码 |
| 解释（understand） | [explanation/](explanation/) | 想理解为什么这样设计 |

静态前端演示位于 [frontend-demo/](frontend-demo/)，用于展示知识图谱、搜索、深读与结构地图等交互，不属于四象限长期说明文档。

## 入口索引

### tutorials/

- [first-knowledge-entry.md](tutorials/first-knowledge-entry.md)：写入第一条知识并检索
- [extending-with-new-kind.md](tutorials/extending-with-new-kind.md)：跟着流程加一种
  kind（数据模型 + schema + 迁移）
- [extending-with-new-kind-part2.md](tutorials/extending-with-new-kind-part2.md)：
  续 part 1，补测试与文档收尾

### how-to/

H1 使用中文描述式标题；文件名保留英文 kebab-case slug，作为稳定路径与链接锚点。

- [add-new-kind.md](how-to/add-new-kind.md)
- [add-new-relation-type.md](how-to/add-new-relation-type.md)
- [body-update-standard.md](how-to/body-update-standard.md)
- [debug-catalog-drift.md](how-to/debug-catalog-drift.md)
- [migrate-schema.md](how-to/migrate-schema.md)
- [orchestration-workflows.md](how-to/orchestration-workflows.md)
- [agent-write-flow.md](how-to/agent-write-flow.md)
- [governance-lifecycle.md](how-to/governance-lifecycle.md)
- [run-doctor-and-diagnose.md](how-to/run-doctor-and-diagnose.md)
- [doctor-common-issues.md](how-to/doctor-common-issues.md)

### reference/

- [data-model.md](reference/data-model.md)：核心 17 表 + kind 字段表 + 三路 FTS、
  kind/status/freshness/relation_type 全集
- [module-index.md](reference/module-index.md)：核心模块与子包索引
- [error-types.md](reference/error-types.md)：4 类错误 + transport 序列化
- [conflict-policies.md](reference/conflict-policies.md)：strict / merge_suggest /
  allow
- [cli-commands.md](reference/cli-commands.md)：ek 子命令与存量修复脚本完整参数
- [mcp-tools.md](reference/mcp-tools.md)：MCP tool 签名与错误载荷
- [search-contract.md](reference/search-contract.md)：检索参数、后端与结果契约

### frontend-demo/

- [frontend-demo/](frontend-demo/)：静态前端演示，使用合成公开数据展示仪表盘、图谱、搜索、深读与结构地图。

### explanation/

- [architecture-layers.md](explanation/architecture-layers.md)：CLI/MCP → Core →
  Service → DAO
- [why-registry-is-dao.md](explanation/why-registry-is-dao.md)：为什么 registry/ 不再拆
- [transactions.md](explanation/transactions.md)：tmp + rename + BEGIN IMMEDIATE 协议
- [fts-strategy.md](explanation/fts-strategy.md)：jieba + trigram + BM25 + 图邻居
- [stable-slug.md](explanation/stable-slug.md)：稳定 slug 与重命名语义

## 维护边界

- **真源**：源码 + `reference/`（事实真源）。本目录不复制源码，只引用 `file:line`。
- **行数约束**：单文件 ≤150 行；超了就拆子文档，原位留指针。
- **不重复**：与 [`how-to/`](how-to/) 和 [`tutorials/`](tutorials/) 中的
  策略/编排内容严格分工，本目录只讲"开发者怎么读懂代码、改代码、排障"。
- **每改一处真源（schema / kind / relation / error）**，同步更新 reference/。
