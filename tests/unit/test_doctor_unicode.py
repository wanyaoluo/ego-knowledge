"""Phase 1 / 1.1 — doctor unicode 检查口径调整测试。

对应被测模块：``ego_knowledge.doctor._checks.unicode._check_fullwidth_chars``。

从 ``test_doctor_checks.py`` 拆出，聚焦 spec 决策 1 引入的四条 acceptance：
 - body 中文正文全角标点不报（口径放宽）
 - body 全角空格 U+3000 仍报
 - frontmatter 全角结构标点（：，""''）仍报
 - body 代码块 / 行内代码内容受保护不误报
 - 已合规条目不产生噪音

注：``_check_fullwidth_chars`` 在 Phase 0 之前名为 ``_check_fullwidth_in_body``；
既有 body 全角 ASCII 字母数字检测、one-finding-per-file、行号定位等基线行为
仍在 ``test_doctor_checks.py`` 中验证，本文件只覆盖 Phase 1 新增口径。
"""

from __future__ import annotations

from pathlib import Path

from ego_knowledge.doctor import _check_fullwidth_chars

from ._doctor_helpers import write_entry_with_body

# ---------------------------------------------------------------------------
# spec 决策 1：分层口径（body 中文标点 / 全角空格 / frontmatter 结构标点 / 代码块保护）
# ---------------------------------------------------------------------------


def test_fullwidth_in_body_passes_cjk_punctuation(fresh_ek, ek_root: Path) -> None:
    """body 中文正文全角标点（，。：；！？""''）不应报为问题（spec 决策 1）。

    覆盖 acceptance：body 中文标点不报错。
    """
    body = "正文，含标点：测试。还有；分号！问号？引号""''"
    write_entry_with_body(ek_root, "cjk-punct-001", body)

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    # frontmatter 由 write_entry_with_body 写入半角结构，不会触发；
    # body 中文标点按新口径不应触发任何 finding。
    assert findings == []


def test_fullwidth_in_body_flags_fullwidth_space(fresh_ek, ek_root: Path) -> None:
    """body 含全角空格 U+3000 → 应报 finding。

    覆盖 acceptance：body 全角空格仍报。
    """
    write_entry_with_body(ek_root, "fw-space-001", "残留\u3000全角空格")

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)
    fullwidth_findings = [f for f in findings if f.rule_id == "fullwidth_in_body"]

    assert len(fullwidth_findings) >= 1
    f = fullwidth_findings[0]
    assert "U+3000" in f.message
    assert "正文" in f.message
    # W2：[body] 标签锁定下游可机器分类契约（区分 fm/body 来源）
    assert f.message.startswith("[body]")


def test_fullwidth_in_body_flags_frontmatter_structure_punctuation(
    fresh_ek, ek_root: Path
) -> None:
    """frontmatter 含全角结构标点（：，""''）→ 应报 finding。

    覆盖 acceptance：frontmatter 全角结构标点仍报。
    全角冒号 U+FF1A 会破坏 YAML 结构，必须扫描原始文本才能稳定检测。
    """
    source_dir = ek_root / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "fw-fm-001.md").write_text(
        "---\n"
        "id：fw-fm-001\n"  # 全角冒号 U+FF1A
        "kind: source\n"
        "title: 全角结构标点测试\n"
        "---\n"
        "正文不含全角字符\n",
        encoding="utf-8",
    )

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)
    fullwidth_findings = [f for f in findings if f.rule_id == "fullwidth_in_body"]

    assert len(fullwidth_findings) >= 1
    f = fullwidth_findings[0]
    assert "frontmatter" in f.message
    assert "U+FF1A" in f.message
    # W2：[fm] 标签锁定下游可机器分类契约（区分 fm/body 来源）
    assert f.message.startswith("[fm]")


def test_fullwidth_in_body_clean_entry_no_findings(fresh_ek, ek_root: Path) -> None:
    """合规条目（frontmatter 半角 + body 无全角残留）→ 不产生噪音。

    覆盖 acceptance：已合规条目不产生噪音。
    """
    write_entry_with_body(
        ek_root, "clean-all-001", "这是正常中文正文，含标点。"
    )

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert findings == []


def test_fullwidth_in_body_skips_code_blocks(fresh_ek, ek_root: Path) -> None:
    """body 代码块/行内代码内的全角空格 U+3000 不应报（spec 决策 1）。

    写入通道 ``_extract_body`` 已对 fenced/inline code 做保护；
    doctor 必须与写入通道口径一致，否则会对合法代码内容产生噪音。
    """
    body = "正文\n```\n代码块\u3000内容\n```\n行内 `code\u3000` 测试"
    write_entry_with_body(ek_root, "fw-code-001", body)

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    # frontmatter 由 write_entry_with_body 写入半角结构；body 中所有 U+3000
    # 都在代码块/行内代码内，按 spec 决策 1 不应触发任何 finding。
    assert findings == []


def test_fullwidth_in_body_flags_fullwidth_space_outside_code(
    fresh_ek, ek_root: Path
) -> None:
    """body 非代码区域的全角空格 U+3000 仍报（与代码块保护正交）。

    保证代码块保护不会把合法的报错场景也吞掉。
    """
    body = "正常段落\u3000含全角空格\n```\ncode\n```"
    write_entry_with_body(ek_root, "fw-space-outside-001", body)

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)
    fullwidth_findings = [f for f in findings if f.rule_id == "fullwidth_in_body"]

    assert len(fullwidth_findings) == 1
    assert "U+3000" in fullwidth_findings[0].message
    assert "第 1 行" in fullwidth_findings[0].message
