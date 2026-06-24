from __future__ import annotations

from datetime import date

import pytest

from ego_knowledge.errors import ValidationError
from ego_knowledge.frontmatter import (
    FRONTMATTER_BOUNDARY,
    _extract_body,
    _fix_fullwidth_punctuation,
    _load_frontmatter,
    read_file,
    split_frontmatter,
    write_file,
)


def test_read_write_roundtrip(tmp_path: pytest.TempPathFactory) -> None:
    path = tmp_path / "test.md"
    frontmatter = {
        "id": "ek_con_01HXYZ1234ABCDEFGHJKMNPQRS",
        "title": "Cafe\u0301",
        "created_at": date(2026, 4, 16),
        "file_path": "/tmp/should-not-persist.md",
        "metrics": {"tokens": 4},
    }
    body = "Cafe\u0301 正文\n"

    write_file(str(path), frontmatter, body)
    loaded_frontmatter, loaded_body = read_file(str(path))

    assert loaded_frontmatter["title"] == "Café"
    assert loaded_frontmatter["created_at"] == date(2026, 4, 16)
    assert "file_path" not in loaded_frontmatter
    assert "metrics" not in loaded_frontmatter
    assert loaded_body == "Café 正文\n"


def test_fullwidth_colon_auto_fixed(tmp_path: pytest.TempPathFactory) -> None:
    """frontmatter 值里含全角冒号 → 写入通道自动修复为半角，YAML 正常解析。

    旧逻辑会在预检查阶段直接拒绝（因含 ``：``）；新逻辑映射后半角冒号
    出现在值里、不破坏 key/value 结构，YAML 可正常解析为 dict。
    """

    path = tmp_path / "auto-fix.md"
    path.write_text("---\ntitle: 标题：含全角冒号\n---\n正文\n", encoding="utf-8")

    frontmatter, body = read_file(str(path))
    assert frontmatter["title"] == "标题:含全角冒号"
    assert body == "正文\n"


def test_missing_frontmatter_marker(tmp_path: pytest.TempPathFactory) -> None:
    path = tmp_path / "nomarker.md"
    path.write_text("title: test\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="缺少 frontmatter"):
        read_file(str(path))


def test_non_mapping_frontmatter_rejected(tmp_path: pytest.TempPathFactory) -> None:
    path = tmp_path / "bad-frontmatter.md"
    path.write_text("---\n- item\n---\n正文\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="不是字典类型"):
        read_file(str(path))


def test_write_file_handles_strenum(tmp_path):
    """StrEnum (Kind/Status/Freshness) 应被自动 unwrap 成裸 str 写入 YAML."""
    from ego_knowledge.frontmatter import read_file, write_file
    from ego_knowledge.models import Freshness, Kind, Status

    path = tmp_path / "enum.md"
    fm = {
        "kind": Kind.CONCEPT,
        "status": Status.ACTIVE,
        "freshness": Freshness.STABLE,
        "aliases": [Kind.SOURCE, "text"],
    }
    write_file(str(path), fm, "body")
    fm_loaded, _ = read_file(str(path))
    assert fm_loaded["kind"] == "concept"
    assert fm_loaded["status"] == "active"
    assert fm_loaded["freshness"] == "stable"
    assert fm_loaded["aliases"] == ["source", "text"]


def test_fix_fullwidth_punctuation_converts_to_halfwidth():
    raw = "title：含全角逗号，测试"
    fixed = _fix_fullwidth_punctuation(raw)
    assert "：" not in fixed and "," in fixed
    assert fixed == "title:含全角逗号,测试"


def test_fix_fullwidth_punctuation_idempotent():
    raw = "title:already clean"
    assert _fix_fullwidth_punctuation(raw) == raw


def test_fix_fullwidth_punctuation_all_seven_mappings():
    raw = "a\uff1ab\uff0c\u201cc\u201d\u2018d\u2019\u3000e"
    fixed = _fix_fullwidth_punctuation(raw)
    assert fixed == "a:b,\"c\"'d' e"


# ---------------------------------------------------------------------------
# 任务 0.4 / Scenario 1: frontmatter 全角结构标点 + body 中文标点 + 代码块 端到端
# ---------------------------------------------------------------------------


class TestWriteChannelNormalization:
    """写入通道归一化层的端到端组合测试（spec 决策 1 + 数据流）。

    覆盖两层归一化的串接，对应 ``_entry_store.ingest`` 中归一化管道的
    两个关键步骤：

    - frontmatter 层：外部 raw yaml 含全角结构标点 → ``_load_frontmatter``
      触发 ``_fix_fullwidth_punctuation``（七项映射）→ YAML 解析为 dict。
      这是**存量救援**路径（用户/AI 编辑器写入含全角的 frontmatter）。
    - body 层：``_extract_body`` 触发 NFC + 代码块保护 + 非代码区窄映射。

    与单函数测试的边界：``_fix_fullwidth_punctuation`` / ``_extract_body``
    各自的单元测试锁函数契约；本类锁两层在写入通道中正确串接，且
    frontmatter 半角化与 body 窄映射**互不干扰**（spec 决策 1 分层独立性）。
    """

    def test_fullwidth_frontmatter_with_cjk_body_and_code(self) -> None:
        """混合场景：frontmatter 全角七项 + body 中文标点 + 全角空格 + 代码块。

        YAML 语法要求 ``key: value`` 冒号后跟空格作为分隔符；全角字符
        故意只出现在 value 中，七项映射后不破坏 key/value 结构。
        """
        fm_raw = "title: 标题，含全角：与逗号\ntags:\n  - ＡＩ\n  - 编程，语言\n"
        body_raw = (
            "正文，含中文标点：测试。\n"
            "全角\u3000空格在正文。\n"
            "```python\n"
            "code\u3000block with ，\n"
            "```\n"
            "行内`inline\u3000code`后文。\n"
        )

        # 写入通道归一化层（模拟 ingest 中的两步归一化）
        frontmatter = _load_frontmatter(fm_raw, "<test>")
        body = _extract_body({"body": body_raw})

        # frontmatter 七项映射：值里的全角 ，：→ 半角 ,:
        assert frontmatter["title"] == "标题,含全角:与逗号"
        # 全角 ASCII 字母不在七项映射里，保留不动（spec 决策 1 frontmatter 只映射结构标点）
        assert frontmatter["tags"] == ["ＡＩ", "编程,语言"]

        # body 中文标点保留（spec 决策 1 body 正文段落）
        assert "，含中文标点：测试。" in body
        # body 非代码区全角空格 → 半角
        assert "全角 空格在正文。" in body
        # fenced code 块内全角空格 + 中文标点保留（spec 决策 1 代码块不动）
        assert "code\u3000block with ，" in body
        # inline code 内全角空格保留
        assert "inline\u3000code" in body
        # body 非代码区无残留 U+3000（剔除两个代码段后应无全角空格）
        non_code = body.replace("code\u3000block with ，", "").replace("inline\u3000code", "")
        assert "\u3000" not in non_code

    def test_read_file_idempotent_on_rewrite(self, tmp_path: pytest.TempPathFactory) -> None:
        """frontmatter 半角化幂等：read → write → read 二次读不产生新变化。

        验证 spec 成功标准 6「dry-run 幂等」的前置条件——半角化输出
        已是固定点，复跑不产生新变化（无全角字符可再映射）。
        """
        raw_md = "---\ntitle: 标题，含全角\n---\n正文。\n"
        path = tmp_path / "idempotent.md"
        path.write_text(raw_md, encoding="utf-8")

        fm1, body1 = read_file(str(path))
        write_file(str(path), fm1, body1)
        fm2, body2 = read_file(str(path))

        assert fm1 == fm2
        assert body1 == body2


# ---------------------------------------------------------------------------
# split_frontmatter: 公共 helper 契约（frontmatter.py 下沉后供三方复用）
# ---------------------------------------------------------------------------


class TestSplitFrontmatter:
    """锁 ``split_frontmatter`` 对外契约。

    helper 被 ``read_file`` / ``normalize_legacy._dry_run`` /
    ``cleanup_broken_relations._scan`` 三方复用，契约漂移会同时影响：
    读取通道的结构化错误（read_file）、normalize 的扫描口径与写回原文
    （_dry_run）、cleanup 的 frontmatter 解析与 body 透传（_scan）。
    """

    def test_standard_split_returns_fm_and_body(self) -> None:
        text = f"{FRONTMATTER_BOUNDARY}title: hello\n{FRONTMATTER_BOUNDARY}body line\n"

        result = split_frontmatter(text)

        assert result == ("title: hello\n", "body line\n")

    def test_body_keeps_leading_newline(self) -> None:
        """body 不做 lstrip：read_file 自己 lstrip，扫描方依赖原文写回。

        若 helper 自作主张 lstrip，_dry_run 写回时会丢掉结束标记后的换行，
        破坏文件 round-trip 一致性。
        """

        text = f"{FRONTMATTER_BOUNDARY}title: hello\n{FRONTMATTER_BOUNDARY}\nbody\n"

        result = split_frontmatter(text)

        assert result is not None
        fm_raw, body = result
        assert fm_raw == "title: hello\n"
        assert body == "\nbody\n"

    def test_body_with_extra_boundary_preserved(self) -> None:
        """split 限制 2 刀：body 内再出现的 ``---\\n`` 必须原样保留。

        cleanup_broken_relations 扫描真实条目时 body 可能含 ```---``` 分隔线
        或 YAML 风格标记，若被贪婪切掉会破坏写回内容。
        """

        text = f"{FRONTMATTER_BOUNDARY}title: hello\n{FRONTMATTER_BOUNDARY}para1\n---\npara2\n"

        result = split_frontmatter(text)

        assert result is not None
        _, body = result
        assert body == "para1\n---\npara2\n"

    def test_empty_body(self) -> None:
        text = f"{FRONTMATTER_BOUNDARY}title: hello\n{FRONTMATTER_BOUNDARY}"

        result = split_frontmatter(text)

        assert result == ("title: hello\n", "")

    @pytest.mark.parametrize(
        "text",
        [
            "no frontmatter at all\n",
            "title: no leading marker\n---\nbody\n",
            "",
        ],
        ids=["plain_text", "marker_not_at_start", "empty"],
    )
    def test_no_opening_marker_returns_none(self, text: str) -> None:
        assert split_frontmatter(text) is None

    def test_missing_closing_marker_returns_none(self) -> None:
        text = f"{FRONTMATTER_BOUNDARY}title: hello\n"

        assert split_frontmatter(text) is None
