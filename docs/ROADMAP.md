# EgoKnowledge 演进路线

> 版本节奏与特性状态。已实现内容看 [tutorials/](tutorials/)、[reference/](reference/) 与 [explanation/](explanation/)。

## 版本节奏

| 版本 | 形态 | 状态 |
| --- | --- | --- |
| v1 | CLI + Core Library + 三路 FTS | ✅ 已实现 |
| v1.1 | MCP Server + 11 tools | ✅ 已实现 |
| v2 | 图权威传播 + 五元指标 + L1/L3/L4 自动化 + maintenance_queue | ✅ 已实现 |
| v3 | 稠密向量 + 五路融合 + AI 自主操作门禁 | ✅ 已实现 |
| v4 | 多向量/重排层 | 📋 待定 |

v2 子特性：图权威传播、L1 GitHub 轮询均已实装。HTTP API 已否决。

v3 子特性：SiliconFlow BGE-M3 dense embedder、`dense_embeddings` 表、schema 2.2、五路融合、semantic CLI、延迟 embed 队列均已实装。中英混合查询字符类拆分已实装。AI 自主操作门禁（权限矩阵、L3 语义规则、自主 ingest 编排、origin 三态、approve/reject、red-team）已实装，schema 升至 2.3。

## v1 明确缺口

以下项 v1 不实施，显式声明避免实现时打架：

| 缺口 | v1 替代方案 | 状态 |
| --- | --- | --- |
| 简繁归一化（OpenCC） | aliases 人工补充 | 📋 推迟；数据量 >50 条或出现简繁混召后再做 |
| 全角→半角预处理 | NFC 规范化后保留原样 | ✅ v1.1 已实装 |
| 正文内容 NFC | 仅 frontmatter + 查询 + 路径段 NFC | ✅ v1.1 已覆盖 |
| 拼音 / 首字母联想检索 | aliases / search_terms 人工补 | 📋 v1.2+；v3 语义检索可覆盖大半需求 |
| 时间表达归一化（农历 / 朝代 / "上周"） | 超出 spec 范围 | 不承诺 |
| 红线 9 证据可追溯（图遍历） | doctor 文件级检查 | ✅ v1.1 已实装（`diagnose.py` BFS 遍历） |
