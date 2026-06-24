"""Markdown body 格式化（写入通道自动修复，best-effort）。

纯函数模块：只做 body 字符串 → 规范 body 字符串，不碰 frontmatter、不落盘、不校验。
- mdformat + GFM 扩展（表格/任务列表/删除线）做 CommonMark + GFM 规范化。
- 解析失败走 best-effort：fallback 原文 + 告警日志；严格模式（``strict=True``）重抛。
- 受 ``EK_MD_FORMAT`` 环境变量开关控制（默认开启；显式置 ``"0"`` 关闭）。

接入边界（ingest 必走 / update 仅 body_changed / touch 不接入）由 ``_entry_store.py`` 负责，
本模块不感知调用方语义。
"""

from __future__ import annotations

import logging
import os

import mdformat

logger = logging.getLogger(__name__)


def _is_format_enabled() -> bool:
    """读 ``EK_MD_FORMAT`` 环境变量；默认开启，仅在显式置 ``"0"`` 时关闭。"""
    return os.environ.get("EK_MD_FORMAT", "1") != "0"


def format_body(body: str, *, strict: bool = False) -> str:
    """格式化 markdown body 字符串。

    接收任意 body 字符串，调用 mdformat + GFM 扩展做规范化（标题空格、列表缩进、
    表格列宽对齐、尾随空格、空行压缩等），返回规范化后的字符串。

    失败路径（best-effort，调和"格式规范"承诺与"写入可用性"优先级）：

    - ``strict=False``（默认，写入通道使用）：捕获 mdformat 解析异常 → 记录告警日志
      （含原因与堆栈）→ 返回原文，不阻断写入。罕见极端内容会走此路径；CommonMark + GFM
      已覆盖常规 markdown 全集。
    - ``strict=True``：捕获后重抛，让调用方按严格策略处理（如人工审核流入）。

    开关：``EK_MD_FORMAT=0`` 跳过格式化，原样返回 body；用于应急关闭或排查对比。

    .. note::
       GFM 扩展（表格/任务列表/删除线）**必须**在此处显式传 ``extensions={"gfm"}``：
       ``pyproject.toml [tool.mdformat]`` 配置只对 mdformat CLI 生效，对
       ``mdformat.text()`` 字符串 API 调用无效（Phase 0 任务 0.1 WARN-2 已确认）。

    :param body:    待格式化的 markdown 字符串（仅 body，不含 frontmatter）。
    :param strict:  ``True`` 时解析失败重抛原异常；``False`` 时 fallback 原文 + 告警。
    :returns:       格式化后的 markdown 字符串；开关关闭或 best-effort fallback 时返回原文。
    """
    if not _is_format_enabled():
        return body

    try:
        return mdformat.text(body, extensions={"gfm"})
    except Exception as exc:
        # 故意捕宽 Exception：mdformat 的解析错误类型不在公开 API 的稳定承诺里，
        # 任何抛错都按 best-effort fallback 处理；strict 模式下裸 raise 保留原异常类型与链。
        # 不是静默吞错——告警日志 + exc_info=True 已留下可观测痕迹（spec 决策 3）。
        logger.warning(
            "mdformat 格式化失败，fallback 原文",
            extra={"error": str(exc), "body_len": len(body)},
            exc_info=True,
        )
        if strict:
            raise
        return body
