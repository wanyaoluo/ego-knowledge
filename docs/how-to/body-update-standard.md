# body 更新标准方法

本指南定义 `ek_update` 写入 `changes.body` 时的授权边界、停点和调用检查。代码做 UTF-8、NFC、长度与 frontmatter 一致性校验；**格式合规由引擎自动修复**（ingest 必走 / update 仅 `body_changed` / touch 不接入；`EK_MD_FORMAT=0` 可关闭）。

**frontmatter 全角结构标点自动修复**：写入时引擎自动将 frontmatter 中的全角结构字符映射为半角，确保 YAML 合法性。此修复不涉及 body 正文段落（中文全角标点保留）。

## 适用边界

- **授权要求**：body 写入需由 `BODY_AUTHORIZED_AGENTS` 列出的 agent（当前 `ops-manage-knowledge`）发起，代码真源见 `src/ego_knowledge/_autonomous.py`。
- **入口**：只使用 `ek_update(id, changes={"body": "..."})` 单条更新。
- **禁止**：不使用批量 body 更新，不把 body 与会迁移文件路径的字段同批提交，不在写入路径要求 sanitization。

## 调用前检查

1. **确认对象**：调用方必须显式确认目标 `id`，不得从 body 内推断条目。
   - **验收**：`id` 来自用户确认或上游精确查询结果。
   - **失败**：无法确认目标时停止。
2. **确认正文范围**：传入内容必须是正文主体；若包含 frontmatter，frontmatter 必须与目标条目一致。
   - **验收**：body 不含 frontmatter，或 frontmatter 字段与目标条目一致。
   - **失败**：出现冲突时停止并重取条目。
3. **确认内容责任**：调用方必须确认正文不需要脱敏、HTML 清洗或路径安全重写。
   - **验收**：内容仅进入本地 Markdown 真源，不进入富文本展示链路。
   - **失败**：命中下方停点时停止。
4. **确认单条语义**：一次调用只更新一个条目的一份 body。
    - **验收**：没有条目列表、批次字段或多份 body。
    - **失败**：拆成逐条调用。
5. **确认原地写入**：body 不与会改变文件路径的字段同批提交。
   - **验收**：只改正文，或只改不触发路径迁移的 frontmatter。
   - **失败**：先完成非 body 更新并回读，再单独写 body。

## 停点

- **富文本停点**：body 或 `ek_search` snippet 被 Markdown/HTML 富文本渲染、导出或对外展示。
- **授权停点**：出现第二个 body 写入 agent。
- **外流停点**：ego-knowledge 数据离开本地仓库。
- **共享停点**：`catalog.sqlite` 或 entries Markdown 加入项目级共享场景。
- **清理停点**：`.txn-snapshots/` 或 `logs/refresh/recovery.log` 需要进入备份、同步或共享链路。

## “对外”定义

“对外”指 ego-knowledge 数据对本地机器之外的用户或系统暴露，或在本地被 Markdown 转 HTML、图形界面 HTML 渲染等富文本管线展示。纯文本展示只有在消费方明确声明纯文本化或转义边界时才不触发富文本停点。

## 豁免执行清单

调用方在每次 body 写入前按以下字段形成可审计记录；记录可以进入调用方任务日志，不写入 body。

| 字段 | 要求 |
| --- | --- |
| `entry_id` | 被更新条目的显式 ID。 |
| `source` | 正文来源或用户指令来源。 |
| `plain_text_boundary` | 若会被展示，写明纯文本化或转义责任方。 |
| `stop_points_checked` | 全部停点的检查结论。 |
| `batch_semantics` | 必须为 `single_entry_single_body`。 |

## 本地副本与展示边界

- `.txn-snapshots/` 保存旧 Markdown 完整快照，仅用于本地事务恢复，不进入 Git、同步、共享或发布链路。
- 需要清理快照时，先确认无未处理 recovery 事件，再按本机运维流程移入 `_backup/` 或清理；不得把快照正文复制到报告。
- `logs/refresh/recovery.log` 只记录事务恢复事件，文件权限应为 `0600`。
- `ek_search` snippet 来源于 raw body；任何 Markdown/HTML 展示方必须纯文本化或转义，不能直接富文本渲染。

## 失败信号

| 错误码 | 调用方动作 |
| --- | --- |
| `body_invalid_utf8` | 转为合法 UTF-8 文本后重试。 |
| `body_length_below_min` | 提供非空 body 后重试。 |
| `body_length_above_max` | 裁剪到 40960 字节以内后重试。 |
| `body_frontmatter_mismatch` | 移除 body 内 frontmatter，或先同步目标 frontmatter。 |
| `body_batch_not_supported` | 拆成逐条、原地 body `ek_update`；路径迁移字段先单独提交。 |
| `body_recovery_failed_snapshot_missing` | 停止重试，人工检查 `.txn-snapshots/` 与 `logs/refresh/recovery.log`。 |

格式化失败不是 `error_code`：引擎记录 mdformat WARNING 日志，并 best-effort fallback 保留原 body；调用方可按日志人工检查。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EK_MD_FORMAT` | `1`（开启） | 设为 `0` 可关闭 mdformat 自动格式化；用于应急排查或调用方自行保证格式合规。 |

## 参考

- [error-types.md](../reference/error-types.md)
