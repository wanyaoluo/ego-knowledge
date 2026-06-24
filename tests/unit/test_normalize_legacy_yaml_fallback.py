"""task_411da53a4e03：spec.md:107-108 异常路径 YAML 兜底测试。

``_fix_fullwidth_punctuation`` 全角→半角后，PyYAML 对 ``key:value``（冒号后
无空格）会抛 YAMLError（YAML 1.1 要求 mapping 后必须有空白）。
``_ensure_yaml_parseable_after_fix`` 三层兜底：冒号空格规范化 → scalar 单引号
转义 → 抛 ``NormalizeLegacyError``（spec.md:108 停止写入）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from ego_knowledge.frontmatter import _fix_fullwidth_punctuation
from ego_knowledge.scripts.normalize_legacy import (
    NormalizeLegacyError,
    normalize_legacy_apply,
    normalize_legacy_dry_run,
)
from ego_knowledge.scripts.normalize_legacy._dry_run import (
    _ensure_yaml_parseable_after_fix,
    _needs_yaml_quote,
    _normalize_toplevel_colon_spacing,
    _quote_toplevel_scalars,
)

from ._normalize_legacy_helpers import _dirty_fm, _write_entry


class TestEnsureYamlParseableAfterFix:
    """spec.md:107-108 三层兜底契约。"""

    def test_no_fallback_when_fixed_fm_still_valid(self) -> None:
        """修复后 yaml.safe_load 成功 → 不做兜底（最小 diff）。

        sources 实测场景：title 内全角冒号转半角后无 ``: ``，仍合法。
        """

        fm_raw = "id: x\ntitle: 文体学：子标题\nkind: note\n"
        fixed = _fix_fullwidth_punctuation(fm_raw)

        result = _ensure_yaml_parseable_after_fix(fm_raw, fixed, Path("x.md"))

        assert result == fixed
        assert isinstance(yaml.safe_load(result), dict)

    def test_normalize_colon_spacing_when_no_space_after_colon(self) -> None:
        """全角 ``：`` 转半角 ``:`` 后 ``key:value`` 触发 YAMLError。

        场景：``kind：note`` 修复后 ``kind:note``，PyYAML 把 mapping 后无空白
        当非法。第一层兜底补一个空格 → ``kind: note`` 即合法。
        """

        # 用半角冒号的结构 + 全角冒号的内容（修复前后都合法，验证不误触兜底）
        fm_raw_valid = "id: x\ntitle: foo：bar\nkind: note\n"
        fixed_valid = _fix_fullwidth_punctuation(fm_raw_valid)
        result_valid = _ensure_yaml_parseable_after_fix(
            fm_raw_valid, fixed_valid, Path("x.md")
        )
        assert result_valid == fixed_valid  # 不动

        # 全角结构冒号 → 修复后无空格 → 兜底补空格
        fm_raw = "id: x\nkind：note\ntitle: foo\n"
        fixed = _fix_fullwidth_punctuation(fm_raw)
        # 修复后非法
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(fixed)

        result = _ensure_yaml_parseable_after_fix(fm_raw, fixed, Path("x.md"))

        loaded = yaml.safe_load(result)
        assert isinstance(loaded, dict)
        assert loaded["kind"] == "note"
        # 兜底动作可定位
        assert "kind: note" in result

    def test_quote_fallback_when_value_contains_colon_space(self) -> None:
        """value 含 ``: ``（冒号+空格）时第二层 quote 兜底。

        场景：``note：a： b`` 修复后 ``note:a: b``。第一层规范化得
        ``note: a: b``，value ``a: b`` 含 ``: `` 仍触发 mapping 解析歧义。
        第二层给 value 加单引号 → ``note: 'a: b'``。
        """

        fm_raw = "id: x\nnote：a： b\nkind：note\n"
        fixed = _fix_fullwidth_punctuation(fm_raw)
        # 修复后非法
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(fixed)

        result = _ensure_yaml_parseable_after_fix(fm_raw, fixed, Path("x.md"))

        loaded = yaml.safe_load(result)
        assert isinstance(loaded, dict)
        assert loaded["note"] == "a: b"
        assert loaded["kind"] == "note"

    def test_no_fallback_when_no_fullwidth_chars(self) -> None:
        """无全角字符（fixed_fm == fm_raw）→ 不做兜底（不归本工具管）。

        即使原本非法，若无全角字符需要修复，normalize_legacy 不应越界改写。
        """

        # 半角冒号但缺空格（原本就非法，与全角无关）
        fm_raw = "id: x\nkind:note\n"
        fixed = _fix_fullwidth_punctuation(fm_raw)  # 无全角字符，与 fm_raw 相同

        result = _ensure_yaml_parseable_after_fix(fm_raw, fixed, Path("x.md"))

        assert result == fixed  # 不兜底

    def test_raises_when_all_fallbacks_fail(self) -> None:
        """三层兜底全失败 → 抛 NormalizeLegacyError（spec.md:108 停止写入）。

        构造一个 yaml.safe_load 始终失败的场景（monkeypatch 替换兜底函数
        为 no-op），验证 spec.md:108 错误信息可定位（指向文件 + 字段）。

        qa-strict R1（issue 1）+ qa-consistency R1（issue-2 info）：
        spec.md:108 字面要求「错误信息指向文件与字段」。第三层抛错必须含
        ``field=`` 字段名（从 PyYAML mark.line 反查顶层字段）+ ``候选字段=``
        列表（fm_raw 与 fixed_fm 行级 diff 提取）。
        """

        # 多字段 frontmatter，让 mark.line 反查有明确字段可定位。
        fm_raw = (
            "id: x\n"
            "kind：note\n"  # 全角冒号触发兜底
            "title: 测试\n"
        )
        fixed = _fix_fullwidth_punctuation(fm_raw)

        # 模拟兜底全失败：替换两个兜底函数为 identity
        import ego_knowledge.scripts.normalize_legacy._dry_run as dry_run_mod

        original_norm = dry_run_mod._normalize_toplevel_colon_spacing
        original_quote = dry_run_mod._quote_toplevel_scalars
        dry_run_mod._normalize_toplevel_colon_spacing = lambda s: s
        dry_run_mod._quote_toplevel_scalars = lambda s: s

        try:
            with pytest.raises(NormalizeLegacyError) as excinfo:
                _ensure_yaml_parseable_after_fix(
                    fm_raw, fixed, Path("/tmp/yaml-fail.md")
                )
        finally:
            dry_run_mod._normalize_toplevel_colon_spacing = original_norm
            dry_run_mod._quote_toplevel_scalars = original_quote

        msg = str(excinfo.value)
        assert "spec.md:107-108" in msg
        assert "/tmp/yaml-fail.md" in msg
        # qa-strict R1：必须含字段定位（spec.md:108「文件与字段」要求）。
        # field= 来自 PyYAML mark.line 反查；候选字段= 来自行级 diff（fm_raw
        # 与 fixed_fm 在 kind 行上差异，故候选字段列表必含 "kind"）。
        assert "field=" in msg
        assert "候选字段=" in msg
        # mark.line 应能定位到 ``kind`` 行（fixed 中 ``kind:note`` 触发解析失败）
        assert "kind" in msg


class TestNormalizeToplevelColonSpacing:
    """第一层兜底：``key:value`` → ``key: value`` 单元测试。"""

    def test_inserts_space_after_colon(self) -> None:
        out = _normalize_toplevel_colon_spacing("kind:note\n")
        assert out == "kind: note\n"

    def test_preserves_already_spaced(self) -> None:
        out = _normalize_toplevel_colon_spacing("kind: note\n")
        assert out == "kind: note\n"

    def test_preserves_key_only_line(self) -> None:
        """``key:`` 后为空（value 在后续行）不动。"""

        fm = "tags:\n- a\n- b\n"
        out = _normalize_toplevel_colon_spacing(fm)
        assert out == fm

    def test_preserves_indented_child_lines(self) -> None:
        """行首有缩进的行（list/dict 子项）不被误改。"""

        fm = "tags:\n- foo:bar\nnote: x: y\n"
        out = _normalize_toplevel_colon_spacing(fm)
        # list 项 ``- foo:bar`` 不动
        assert "- foo:bar" in out
        # 顶层 ``note: x: y`` 已有空格，不动
        assert "note: x: y" in out

    def test_preserves_value_with_leading_special_chars(self) -> None:
        """value 以特殊字符（``[``、``{``、``|``）开头时不被误改。

        这些是 YAML flow seq / flow map / block scalar，本就有合法语法。
        """

        fm = "tags: [a, b]\nnote: |block\n"
        out = _normalize_toplevel_colon_spacing(fm)
        assert out == fm  # 已有空格，不动


class TestQuoteToplevelScalars:
    """第二层兜底：顶层 scalar 单引号转义单元测试。"""

    def test_quotes_value_with_colon_space(self) -> None:
        out = _quote_toplevel_scalars("note: a: b\n")
        assert out == "note: 'a: b'\n"

    def test_preserves_value_without_colon_space(self) -> None:
        out = _quote_toplevel_scalars("title: 普通文本\n")
        assert out == "title: 普通文本\n"

    def test_skips_already_quoted_value(self) -> None:
        out = _quote_toplevel_scalars("note: 'already: quoted'\n")
        assert out == "note: 'already: quoted'\n"

    def test_escapes_single_quote_inside_value(self) -> None:
        # YAML 单引号转义：' -> ''
        out = _quote_toplevel_scalars("note: a: b'c\n")
        assert out == "note: 'a: b''c'\n"

    def test_skips_indented_child_lines(self) -> None:
        """行首有缩进的行（list/dict 子项）不被误改。"""

        fm = "tags:\n- foo: bar\nnote: x: y\n"
        out = _quote_toplevel_scalars(fm)
        assert "- foo: bar" in out
        assert "note: 'x: y'" in out

    def test_inserts_space_when_called_without_normalized_input(self) -> None:
        """直接调用且输入 ``key:value``（无空格）时仍补空格防误用。

        端到端路径里 _normalize_toplevel_colon_spacing 已补空格，但本函数
        单独被调用时也要保证输出合法（spec.md:108 不留半成功）。
        """

        out = _quote_toplevel_scalars("note:a: b\n")
        # 输出必须是合法 YAML（key 后冒号有空格）
        assert out == "note: 'a: b'\n"


class TestNeedsYamlQuote:
    """``_needs_yaml_quote`` 决策表。"""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("普通文本", False),
            ("a:b", False),  # 半角冒号无空格，YAML 合法
            ("a: b", True),  # 冒号+空格触发 mapping
            ("结尾:", True),  # 以冒号结尾
            ("'already quoted'", False),
            ('"already quoted"', False),
            ("|block", False),  # YAML 特殊起始
            ("[flow]", False),
            ("-list-item", False),
            ("", False),
        ],
    )
    def test_decision_table(self, value: str, expected: bool) -> None:
        assert _needs_yaml_quote(value) is expected


class TestEndToEndYamlFallbackInScan:
    """端到端：扫描时 YAML 兜底触发，apply 后文件仍可被 yaml.safe_load 解析。"""

    def test_dirty_fm_apply_produces_valid_yaml(self, tmp_path: Path) -> None:
        """``_dirty_fm`` fixture（全角结构冒号）修复后必须仍 YAML 合法。

        回归保护：当前 normalize_legacy 写回的 frontmatter 若不能被
        yaml.safe_load 解析，会让 build-registry 跳过该条目（spec.md:108
        红线：不留下半写入文件）。
        """

        entry = _write_entry(
            tmp_path / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(tmp_path)

        assert report.would_change == 1

        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-yaml-e2e"
        normalize_legacy_apply(tmp_path, backup_dir)

        applied = entry.read_text(encoding="utf-8")
        parts = applied.split("---\n", 2)
        assert len(parts) == 3
        loaded = yaml.safe_load(parts[1])
        assert isinstance(loaded, dict)
        # 字段语义正确（key 不含 ``:``）
        assert loaded["kind"] == "note"
        assert loaded["tags"] == ["测试"]

    def test_dirty_fm_idempotent_after_apply(self, tmp_path: Path) -> None:
        """apply 后复跑 dry-run 幂等（spec.md 验收标准 6）。"""

        _write_entry(
            tmp_path / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
        )
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-idem"
        normalize_legacy_apply(tmp_path, backup_dir)

        report = normalize_legacy_dry_run(tmp_path)
        assert report.would_change == 0

    def test_value_with_colon_space_apply_produces_valid_yaml(
        self, tmp_path: Path
    ) -> None:
        """note 字段含 ``：`` + 空格（修复后产生 ``: ``）：兜底加单引号。"""

        dirty_fm = (
            "id: ek_note_yaml\n"
            "note：a： b\n"  # 全角冒号 + 半角空格 → 修复后 ``a: b``
            "kind：note\n"
        )
        entry = _write_entry(
            tmp_path / "entries" / "notes" / "yaml-note.md",
            fm_text=dirty_fm,
        )

        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-note-colon"
        normalize_legacy_apply(tmp_path, backup_dir)

        applied = entry.read_text(encoding="utf-8")
        parts = applied.split("---\n", 2)
        loaded = yaml.safe_load(parts[1])
        assert isinstance(loaded, dict)
        assert loaded["note"] == "a: b"
        assert loaded["kind"] == "note"

        # 复跑 dry-run 幂等
        report = normalize_legacy_dry_run(tmp_path)
        assert report.would_change == 0
