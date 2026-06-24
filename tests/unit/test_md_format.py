"""``ego_knowledge._md_format.format_body`` 单测。

覆盖 acceptance：混乱 body 规范化、CJK 保留、GFM 表格规范化、解析失败 best-effort
fallback + 告警、开关关闭、严格模式重抛、边界输入（空/空白/单字符）。

测试只锁行为契约（输入 → 输出 + 可观测信号），不锁 mdformat 内部实现细节。
"""

from __future__ import annotations

import logging

import pytest

from ego_knowledge._md_format import format_body

# ---------------------------------------------------------------------------
# 正常路径：规范化
# ---------------------------------------------------------------------------


class TestNormalization:
    """mdformat + GFM 对混乱 body 的规范化行为。"""

    def test_formats_messy_body(self) -> None:
        """标题多余空格、连续空行、句末多空格被规范化。"""
        messy = "#   标题\n\n\n正文   。\n"
        assert format_body(messy) == "# 标题\n\n正文 。\n"

    def test_cjk_preserved(self) -> None:
        """中文字符不被破坏（不丢字、不转 ASCII 转义）。"""
        out = format_body("# 标题\n\n中文内容 test\n")
        assert "中文" in out
        assert "test" in out

    def test_gfm_table_normalized(self) -> None:
        """GFM 表格：单元格空格 + 列分隔符列宽被 mdformat-gfm 对齐。"""
        messy = "|名字|年龄|\n|---|---|\n|张三|25|\n|李四|30|\n"
        out = format_body(messy)
        # 表头被规范化为带空格分隔
        assert "| 名字 | 年龄 |" in out
        # 数据行也对齐
        assert "| 张三 | 25" in out
        assert "| 李四 | 30" in out

    def test_gfm_task_list_preserved(self) -> None:
        """GFM 任务列表语法不被破坏（- [x] / - [ ]）。"""
        out = format_body("- [x] 完成\n- [ ] 待办\n")
        assert "- [x] 完成" in out
        assert "- [ ] 待办" in out


# ---------------------------------------------------------------------------
# 边界输入：mdformat 必须安全处理（不抛错）
# ---------------------------------------------------------------------------


class TestEdgeInputs:
    """空串 / 纯空白 / 单字符 / 多空行：mdformat 安全处理（safety_check_output.log 实测无异常）。"""

    @pytest.mark.parametrize(
        ("body", "desc"),
        [
            ("", "空串"),
            (" ", "纯空白"),
            ("   \n\t  ", "混合空白"),
            ("x", "单字符"),
            ("\n\n\n", "纯空行"),
        ],
    )
    def test_safe_on_edge_input(self, body: str, desc: str) -> None:
        """边界输入不抛错；返回值是合法字符串（具体形态由 mdformat 决定，不锁实现）。"""
        try:
            out = format_body(body)
        except Exception as exc:  # noqa: PT011 — 边界安全：任何抛错都是回归
            pytest.fail(f"边界输入({desc})不应抛错，但抛了 {type(exc).__name__}: {exc}")
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# 异常路径：best-effort fallback + 告警 + 严格模式
# ---------------------------------------------------------------------------


def _force_mdformat_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 mdformat.text 替换为必抛 ValueError 的桩，模拟解析失败。"""
    import ego_knowledge._md_format as m

    def _raise(*_args: object, **_kwargs: object) -> str:
        raise ValueError("simulated parse failure")

    monkeypatch.setattr(m.mdformat, "text", _raise)


class TestFallback:
    """mdformat 解析异常时的 best-effort 行为（spec 决策 3）。"""

    def test_fallback_returns_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """strict=False（默认）：解析失败 fallback 原文，不阻断调用方。"""
        _force_mdformat_raise(monkeypatch)
        assert format_body("x") == "x"

    def test_fallback_logs_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """解析失败时必须记录 WARNING 日志（spec 决策 3：可观测告警，不静默 fallback）。"""
        _force_mdformat_raise(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="ego_knowledge._md_format"):
            format_body("some body")
        # 至少一条 WARNING 提到 fallback
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "fallback 必须记录 WARNING 日志"
        # exc_info=True 让日志带堆栈（玻璃盒：可复现可定位）
        assert any(r.exc_info is not None for r in warnings), "fallback 日志必须带堆栈"

    def test_strict_mode_reraises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """strict=True：解析失败重抛原异常，让调用方决定处理策略。"""
        _force_mdformat_raise(monkeypatch)
        with pytest.raises(ValueError, match="simulated parse failure"):
            format_body("x", strict=True)


# ---------------------------------------------------------------------------
# 开关：EK_MD_FORMAT 环境变量
# ---------------------------------------------------------------------------


class TestSwitch:
    """``EK_MD_FORMAT`` 环境变量开关行为。"""

    def test_disabled_returns_original_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EK_MD_FORMAT=0：跳过格式化，原样返回（含原始格式问题）。"""
        monkeypatch.setenv("EK_MD_FORMAT", "0")
        messy = "#   x\n"
        assert format_body(messy) == messy

    def test_disabled_does_not_call_mdformat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """开关关闭时不应调用 mdformat.text（避免无谓计算 + 应急关闭语义）。"""
        import ego_knowledge._md_format as m

        call_count = 0

        def _spy(*_args: object, **_kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            return "should not reach"

        monkeypatch.setattr(m.mdformat, "text", _spy)
        monkeypatch.setenv("EK_MD_FORMAT", "0")
        format_body("# messy x\n")
        assert call_count == 0, "开关关闭时不应调用 mdformat.text"

    def test_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认（未设/非 "0"）开启格式化：混乱 body 被规范化。"""
        monkeypatch.delenv("EK_MD_FORMAT", raising=False)
        assert format_body("#   x\n") == "# x\n"
