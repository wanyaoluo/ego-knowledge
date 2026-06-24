"""Tests for fullwidth-to-halfwidth normalization."""

from __future__ import annotations

import pytest

from ego_knowledge.frontmatter import _extract_body
from ego_knowledge.search import parse_query
from ego_knowledge.unicode_utils import normalize_fullwidth


class TestNormalizeFullwidth:
    """Unit tests for normalize_fullwidth."""

    def test_fullwidth_letters(self) -> None:
        assert normalize_fullwidth("ＡＢＣ") == "ABC"

    def test_fullwidth_digits(self) -> None:
        assert normalize_fullwidth("１２３") == "123"

    def test_fullwidth_mixed_cjk(self) -> None:
        assert normalize_fullwidth("ＡＩ编程") == "AI编程"

    def test_already_halfwidth(self) -> None:
        assert normalize_fullwidth("ABC123") == "ABC123"

    def test_pure_cjk_unchanged(self) -> None:
        assert normalize_fullwidth("知识库管理") == "知识库管理"

    def test_empty_string(self) -> None:
        assert normalize_fullwidth("") == ""

    def test_fullwidth_sentence(self) -> None:
        assert normalize_fullwidth("版本Ｖ１．２") == "版本V1.2"

    def test_idempotent(self) -> None:
        text = "ＡＩ编程１２３"
        first = normalize_fullwidth(text)
        assert normalize_fullwidth(first) == first


class TestExtractBodyFullwidth:
    """Verify body extraction layering (spec 决策 1).

    Replaces the former NFKC-everywhere behavior: body now keeps CJK
    fullwidth punctuation and fullwidth ASCII letters/digits, only
    mapping U+3000 to halfwidth space outside code spans.
    """

    def test_body_fullwidth_ascii_preserved(self) -> None:
        # spec 决策 1：body 不再做全量 NFKC，全角 ASCII 字母/数字保留
        payload = {"body": "这是ＡＩ编程的１２３种方法"}
        result = _extract_body(payload)
        assert result == "这是ＡＩ编程的１２３种方法"

    def test_body_already_clean(self) -> None:
        payload = {"body": "正常文本无全角"}
        result = _extract_body(payload)
        assert result == "正常文本无全角"

    def test_body_none_returns_empty(self) -> None:
        payload: dict[str, object] = {"body": None}
        result = _extract_body(payload)
        assert result == ""

    def test_extract_body_preserves_cjk_punctuation(self) -> None:
        raw = "正文，含全角标点：测试。"
        body = _extract_body({"body": raw})
        assert "，" in body and "：" in body and "。" in body

    def test_extract_body_converts_fullwidth_space_in_prose(self) -> None:
        raw = "全角\u3000空格"
        body = _extract_body({"body": raw})
        assert "\u3000" not in body and " " in body

    def test_extract_body_preserves_fullwidth_ascii(self) -> None:
        raw = "全角字母ＡＢＣ数字１２３"
        body = _extract_body({"body": raw})
        assert "Ａ" in body and "１" in body

    def test_extract_body_preserves_fenced_code_block(self) -> None:
        raw = "正文\u3000前\n```python\n代码\u3000块内\n```\n正文\u3000后"
        body = _extract_body({"body": raw})
        assert "代码\u3000块内" in body  # 代码块内 U+3000 保留
        assert "正文 前" in body and "正文 后" in body  # 代码块外转半角

    def test_extract_body_preserves_inline_code(self) -> None:
        raw = "前文\u3000前`代码\u3000内联`后文\u3000后"
        body = _extract_body({"body": raw})
        assert "代码\u3000内联" in body  # 行内代码内保留


# ---------------------------------------------------------------------------
# 任务 0.4 / Scenario 2-4: 边界用例（混合代码 / 表格 / 退化输入）
# ---------------------------------------------------------------------------


class TestExtractBodyEdgeCases:
    """``_extract_body`` 边界用例（plan 任务 0.4 Scenario 2-4）。

    覆盖三类边界：
    - Scenario 2：fenced + inline + 中文标点 共存的混合结构；多块共存；
      代码块内中文标点保留；未闭合反引号的保守处理。
    - Scenario 3：表格单元格按正文非代码区处理（spec 决策 1）。
    - Scenario 4：空串 / 纯空白 / 纯全角空格 / 纯代码块 等退化输入。
    """

    # --- Scenario 2: 混合代码结构 -----------------------------------------

    def test_mixed_fenced_inline_cjk_punctuation(self) -> None:
        """fenced + inline + 中文标点 + 全角空格 共存的混合 body。

        锁定 spec 决策 1 在多结构共存时的分层独立性：代码区不动、非代码
        区窄映射、中文标点全程保留。
        """
        raw = (
            "正文\u3000前，含标点。\n"
            "```python\n"
            "code\u3000block with ，\n"
            "```\n"
            "行内`inline\u3000code`后文\u3000尾。"
        )
        body = _extract_body({"body": raw})

        # 非代码区全角空格 → 半角
        assert "正文 前，含标点。" in body
        assert "后文 尾。" in body
        # fenced code 内全角空格 + 中文标点保留
        assert "code\u3000block with ，" in body
        # inline code 内全角空格保留
        assert "inline\u3000code" in body

    def test_multiple_fenced_blocks_coexist(self) -> None:
        """多个 fenced code block 交替出现，每个块内 U+3000 都保留。"""
        raw = (
            "段\u30001\n"
            "```py\na\u3000\n```\n"
            "段\u30002\n"
            "```js\nb\u3000\n```\n"
            "段\u30003"
        )
        body = _extract_body({"body": raw})

        assert "段 1" in body and "段 2" in body and "段 3" in body
        assert "a\u3000" in body and "b\u3000" in body

    def test_multiple_inline_codes_coexist(self) -> None:
        """多个 inline code 交替出现，每个块内 U+3000 都保留。"""
        raw = "前\u3000`a\u3000`中\u3000`b\u3000`后\u3000"
        body = _extract_body({"body": raw})

        # inline code 内 U+3000 保留
        assert "a\u3000" in body and "b\u3000" in body
        # 非代码区 U+3000 → 半角空格（紧跟反引号或行尾）
        assert "前 `" in body  # 前\u3000`a → 前 `a
        assert "中 `" in body  # `中\u3000`b → `中 `b
        assert body.endswith("后 ")  # `后\u3000 → `后 （行尾半角空格）
        # 非代码区无残留 U+3000
        non_code = body.replace("a\u3000", "").replace("b\u3000", "")
        assert "\u3000" not in non_code

    def test_punctuation_inside_code_preserved(self) -> None:
        """代码块内出现的中文标点（，。：）保留不动。

        spec 决策 1 代码块规则：不改动内容。中文标点出现在代码块里时
        也不应被转换（与 body 正文段落保留中文标点一致，但路径不同：
        正文是"窄映射不覆盖中文标点"，代码块是"完全不动"）。
        """
        raw = "```text\n标点，：。在内\n```\n正文，：。在外"
        body = _extract_body({"body": raw})

        assert "标点，：。在内" in body  # 代码块内原样
        assert "正文，：。在外" in body  # 正文保留

    def test_unclosed_backtick_at_start_preserved(self) -> None:
        """整段以反引号开头但未闭合 → 保守不转换（plan 0.3 已知限制）。

        ``_CODE_BLOCK_RE.split`` 把整段切为单 part `` `abc\u3000def ``，
        ``startswith('`')`` 启发式判定为代码块 → U+3000 保留。

        这是 spec 异常路径"无法安全识别代码边界时不做高风险转换"的
        保守失败模式：宁可漏转换，不误改可能存在的代码内容。
        """
        raw = "`abc\u3000def"
        body = _extract_body({"body": raw})

        assert body == "`abc\u3000def"  # 原样保留
        assert "\u3000" in body

    def test_unclosed_backtick_mid_text_normalized(self) -> None:
        """中段未闭合反引号 → 整段按普通文本归一（不误判为代码块）。

        ``_CODE_BLOCK_RE`` 的 inline 模式 `` `[^`\n]+` `` 要求同行闭合；
        未闭合时不匹配，整段作为普通文本走 ``normalize_body_spacing``，
        U+3000 → 半角。与上一用例对照：闭合性决定是否进入代码块保护。
        """
        raw = "前文\u3000前`未闭合 inline\n第二行\u3000后"
        body = _extract_body({"body": raw})

        assert "前文 前" in body and "第二行 后" in body
        assert "\u3000" not in body

    # --- Scenario 3: 表格单元格（spec 决策 1 表格规则）-------------------

    def test_table_cell_fullwidth_space_converted(self) -> None:
        """表格单元格内全角空格 → 半角（按正文非代码区处理）。

        spec 决策 1：表格单元格按正文非代码区处理，U+3000 → 半角空格，
        消除无语义空白。结构（| 分隔符、对齐）由 mdformat 处理，不在
        ``_extract_body`` 职责内。
        """
        raw = "| 列1\u3000内容 | 列2 |\n|---|---|\n| 数据\u30001 | 数据2 |"
        body = _extract_body({"body": raw})

        assert "\u3000" not in body
        assert "| 列1 内容 |" in body
        assert "| 数据 1 |" in body

    def test_table_cell_cjk_punctuation_preserved(self) -> None:
        """表格单元格内中文标点保留（与正文段落规则一致）。"""
        raw = "| 名字，含逗号 | 描述：含冒号 |\n|---|---|\n| 张三，| 备注。|"
        body = _extract_body({"body": raw})

        assert "名字，含逗号" in body
        assert "描述：含冒号" in body
        assert "张三，" in body
        assert "备注。" in body

    # --- Scenario 4: 退化输入 --------------------------------------------

    @pytest.mark.parametrize(
        ("body_input", "desc"),
        [
            ("", "空字符串"),
            ("   \n\t  ", "纯空白（空格+tab+换行）"),
            ("\u3000\u3000\u3000", "纯全角空格"),
            ("```python\ncode\u3000block\n```", "纯 fenced code block"),
            ("`inline\u3000code`", "纯 inline code"),
        ],
    )
    def test_safe_on_degenerate_input(self, body_input: str, desc: str) -> None:
        """退化输入安全处理：不抛错、返回合法字符串。

        - 空串/纯空白：原样返回（normalize_body_spacing 空串安全）。
        - 纯全角空格：全部转半角。
        - 纯代码块/纯 inline code：整段被代码块保护，原样返回。
        """
        try:
            result = _extract_body({"body": body_input})
        except Exception as exc:
            pytest.fail(f"退化输入({desc})不应抛错，但抛了 {type(exc).__name__}: {exc}")
        assert isinstance(result, str)

    def test_empty_string_returns_empty(self) -> None:
        """空字符串 body → 返回空字符串（精确锚定，不用宽松断言）。"""
        assert _extract_body({"body": ""}) == ""

    def test_pure_fullwidth_space_all_converted(self) -> None:
        """纯全角空格 body → 全部转半角空格（数量守恒）。"""
        assert _extract_body({"body": "\u3000\u3000\u3000"}) == "   "

    def test_pure_fenced_code_preserved(self) -> None:
        """纯 fenced code block body → 原样返回（含 U+3000）。"""
        raw = "```python\ncode\u3000block\n```"
        assert _extract_body({"body": raw}) == raw

    def test_pure_inline_code_preserved(self) -> None:
        """纯 inline code body → 原样返回（含 U+3000）。"""
        raw = "`inline\u3000code`"
        assert _extract_body({"body": raw}) == raw

    def test_body_key_missing_returns_empty(self) -> None:
        """payload 不含 body key → 返回空字符串（payload.get 默认值）。"""
        assert _extract_body({}) == ""


class TestParseQueryFullwidth:
    """Verify that query parsing handles fullwidth input."""

    def test_fullwidth_query_normalized(self) -> None:
        segments = parse_query("ＡＩ编程")
        texts = [s.text for s in segments]
        # After normalization, "ＡＩ" becomes "AI" and is classified as ASCII,
        # "编程" is classified as CJK. Both must be present (independent asserts,
        # not joined by `or` — otherwise one side could silently regress).
        assert any("AI" in t for t in texts)
        assert any("编程" in t for t in texts)

    def test_fullwidth_digit_query(self) -> None:
        segments = parse_query("版本１２３")
        texts = [s.text for s in segments]
        # "123" should appear after normalization
        assert any("123" in t for t in texts)

    def test_pure_fullwidth_english(self) -> None:
        segments = parse_query("Ｃｌａｕｄｅ")
        texts = [s.text for s in segments]
        assert any("Claude" in t for t in texts)
