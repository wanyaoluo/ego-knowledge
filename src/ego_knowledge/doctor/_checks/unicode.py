"""Unicode checks: NFC residuals and fullwidth characters in frontmatter/body."""

from __future__ import annotations

from pathlib import Path

from ...frontmatter import (
    _CODE_BLOCK_RE,
    FRONTMATTER_BOUNDARY,
    FULLWIDTH_TO_HALFWIDTH,
)
from ...registry import Registry
from ...unicode_utils import has_nfd_residual
from .._helpers import (
    _FRONTMATTER_FIELDS,
    _MD_LINK_RE,
    _coerce_string_values,
    _iter_all_entry_files,
    _read_raw_markdown,
)
from .._types import Finding, Severity


def _check_nfc_residuals(registry: Registry, data_root: Path) -> list[Finding]:
    del registry
    findings: list[Finding] = []

    for path in _iter_all_entry_files(data_root):
        raw_frontmatter, body = _read_raw_markdown(path)
        target_id = raw_frontmatter.get("id")
        entry_id = target_id if isinstance(target_id, str) else None
        path_text = str(path)

        for field in _FRONTMATTER_FIELDS:
            for value in _coerce_string_values(raw_frontmatter.get(field)):
                if not has_nfd_residual(value):
                    continue
                findings.append(
                    Finding(
                        rule_id="nfc_residual_in_frontmatter",
                        severity=Severity.HIGH,
                        target_id=entry_id,
                        target_path=path_text,
                        message=f"字段 {field} 含 NFD 残留",
                    )
                )

        if has_nfd_residual(path_text):
            findings.append(
                Finding(
                    rule_id="nfc_residual_in_path",
                    severity=Severity.HIGH,
                    target_id=entry_id,
                    target_path=path_text,
                    message="文件路径含 NFD 残留",
                )
            )

        for link_target in _MD_LINK_RE.findall(body):
            if link_target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not has_nfd_residual(link_target):
                continue
            findings.append(
                Finding(
                    rule_id="nfc_residual_in_markdown_link",
                    severity=Severity.HIGH,
                    target_id=entry_id,
                    target_path=path_text,
                    message=f"正文 Markdown 链接含 NFD 残留: {link_target}",
                )
            )

        for source_url in _coerce_string_values(raw_frontmatter.get("source_url")):
            if not has_nfd_residual(source_url):
                continue
            findings.append(
                Finding(
                    rule_id="nfc_residual_in_source_url",
                    severity=Severity.HIGH,
                    target_id=entry_id,
                    target_path=path_text,
                    message=f"source_url 含 NFD 残留: {source_url}",
                )
            )

    return findings


def _read_frontmatter_raw_text(path: Path) -> str | None:
    """读取原始 frontmatter 文本（FRONTMATTER_BOUNDARY 之间），不做 YAML 解析。

    用于全角结构标点检测：破坏 YAML 结构的全角字符（如全角冒号 ``：``）
    会让 ``_read_raw_markdown`` 的 YAML 解析失败而丢失，必须扫描原始文本。
    """

    raw = path.read_text(encoding="utf-8")
    if not raw.startswith(FRONTMATTER_BOUNDARY):
        return None
    parts = raw.split(FRONTMATTER_BOUNDARY, 2)
    if len(parts) != 3:
        return None
    return parts[1]


def _scan_frontmatter_fullwidth(
    path: Path, entry_id: str | None, path_text: str
) -> Finding | None:
    """检测 frontmatter 原始文本中残留的全角结构标点。

    复用写入通道 ``FULLWIDTH_TO_HALFWIDTH`` 真源（spec 决策 1）：
    U+3000、：，""'' 应已半角化，残留即问题。
    """

    fm_raw = _read_frontmatter_raw_text(path)
    if fm_raw is None:
        return None

    for line_no, line in enumerate(fm_raw.splitlines(), start=1):
        for char in line:
            if char in FULLWIDTH_TO_HALFWIDTH:
                codepoint = ord(char)
                halfwidth = FULLWIDTH_TO_HALFWIDTH[char]
                return Finding(
                    rule_id="fullwidth_in_body",
                    severity=Severity.LOW,
                    target_id=entry_id,
                    target_path=path_text,
                    message=(
                        f"[fm] frontmatter 第 {line_no} 行含全角结构标点 "
                        f"{char!r} (U+{codepoint:04X}) → 应为 {halfwidth!r}"
                    ),
                )
    return None


def _scan_body_fullwidth(
    body: str, entry_id: str | None, path_text: str
) -> Finding | None:
    """检测 body 非代码区域中的全角空格与全角 ASCII 字母数字。

    分层口径（spec 决策 1）：
    - U+3000 全角空格：Markdown 中无稳定语义，应转半角 → 报。
    - 全角 ASCII 字母数字（U+FF01–U+FF5E 且半角为 alnum）：保留既有检测 → 报。
    - 中文正文全角标点（，。：；！？""''）：不在检测范围 → 不报。

    代码块/行内代码内容跳过（spec 决策 1：代码块不改动），避免误报
    合法代码内容。复用写入通道 ``_CODE_BLOCK_RE`` 真源保持口径一致；
    替换代码块为等长度空格占位符（保留换行符）以同步原始行号。
    """

    sanitized = _CODE_BLOCK_RE.sub(
        lambda m: "".join("\n" if c == "\n" else " " for c in m.group()),
        body,
    )

    for line_no, line in enumerate(sanitized.splitlines(), start=1):
        for char in line:
            codepoint = ord(char)
            if codepoint == 0x3000:
                return Finding(
                    rule_id="fullwidth_in_body",
                    severity=Severity.LOW,
                    target_id=entry_id,
                    target_path=path_text,
                    message=(
                        f"[body] 正文第 {line_no} 行含全角空格 "
                        f"{char!r} (U+{codepoint:04X}) → 应为 ' '"
                    ),
                )
            if 0xFF01 <= codepoint <= 0xFF5E:
                halfwidth = chr(codepoint - 0xFEE0)
                if halfwidth.isalnum():
                    return Finding(
                        rule_id="fullwidth_in_body",
                        severity=Severity.LOW,
                        target_id=entry_id,
                        target_path=path_text,
                        message=(
                            f"[body] 正文第 {line_no} 行含全角字符 "
                            f"{char!r} (U+{codepoint:04X}) → 应为 {halfwidth!r}"
                        ),
                    )
    return None


def _check_fullwidth_chars(registry: Registry, data_root: Path) -> list[Finding]:
    """Detect fullwidth characters in entry frontmatter structure and body.

    分层口径（spec 决策 1）：
    - frontmatter：全角结构标点（U+3000、：，""''）应已半角化；残留即问题。
    - body：全角空格 U+3000 与全角 ASCII 字母数字仍报；中文正文全角标点不报。

    函数名 ``_check_fullwidth_chars`` 反映实际职责：扫描 frontmatter 全角结构
    标点 + body 全角字符两类残留。``rule_id`` 复用 ``fullwidth_in_body`` 是
    任务 1.1 硬约束（doctor 框架不变 → rule_id 集合不变），下游按 finding
    message 头部 ``[fm]``/``[body]`` 标签区分两类来源。
    """

    del registry
    findings: list[Finding] = []

    for path in _iter_all_entry_files(data_root):
        raw_frontmatter, body = _read_raw_markdown(path)
        target_id = raw_frontmatter.get("id")
        entry_id = target_id if isinstance(target_id, str) else None
        path_text = str(path)

        fm_finding = _scan_frontmatter_fullwidth(path, entry_id, path_text)
        if fm_finding is not None:
            findings.append(fm_finding)

        body_finding = _scan_body_fullwidth(body, entry_id, path_text)
        if body_finding is not None:
            findings.append(body_finding)

    return findings
