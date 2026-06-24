from __future__ import annotations

import datetime as _dt
import json
import unicodedata
from pathlib import Path

from ego_knowledge.doctor import (
    _CHECK_REGISTRY,
    Severity,
    _check_alias_conflicts,
    _check_fullwidth_chars,
    _check_id_uniqueness,
    _check_jieba_fallback_summary,
    _check_metrics_stale,
    _check_nfc_residuals,
    _check_orphan_files,
    _check_orphan_relation_type,
    _check_terminology_audit,
    doctor,
)
from ego_knowledge.frontmatter import _fm_to_entry, read_file, write_file

from ._doctor_helpers import write_entry_with_body
from .support import (
    concept_payload,
    dossier_payload,
    source_payload,
)


def _rewrite_frontmatter(
    ek: object, ek_root: Path, relative_path: str, overrides: dict[str, object]
) -> None:
    """修改条目文件的 frontmatter 并同步 registry（绕过 schema 校验）。"""
    abs_path = ek_root / relative_path
    frontmatter, body = read_file(str(abs_path))
    frontmatter.update(overrides)
    write_file(str(abs_path), frontmatter, body)
    entry = _fm_to_entry(frontmatter, file_path=str(abs_path), body=body)
    ek._registry.upsert_entry(entry, abs_path, body)  # type: ignore[union-attr]
    ek._registry.commit()  # type: ignore[union-attr]


def test_doctor_checked_rules_cover_all_phase5_rules(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest(
        "source",
        source_payload(
            title="健康来源",
            search_terms=["健康来源", "health-source", "origin-doc", "核验材料", "alpha-proof"],
        ),
    )
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="稳定概念",
            search_terms=["稳定概念", "stable-concept", "insight-map", "结构结论", "beta-proof"],
        ),
    )

    report = doctor(fresh_ek._registry, ek_root)

    assert report.checked_rules == [rule_id for rule_id, _ in _CHECK_REGISTRY]
    assert report.findings == []
    assert Path(report.report_path).exists()


def test_terminology_audit_emits_rule_specific_findings(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="术语来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="Graph Mode",
            aliases=["shared", "信任", "trust"],
        ),
    )
    dossier = fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="Graph Node",
            aliases=["bridge"],
        ),
    )

    _rewrite_frontmatter(
        fresh_ek,
        ek_root,
        dossier.file_path or "",
        {"title": "Graph M0de", "aliases": ["shared"]},
    )

    findings = _check_terminology_audit(fresh_ek._registry, ek_root)
    rule_ids = {finding.rule_id for finding in findings}

    assert "terminology_near_duplicate" in rule_ids
    assert "terminology_cross_language_alias" in rule_ids
    assert any(concept.id in (finding.target_id or "") for finding in findings)


def test_nfc_residuals_emit_all_four_rule_ids(fresh_ek, ek_root: Path) -> None:
    decomposed = unicodedata.normalize("NFD", "café")
    source_dir = ek_root / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    path = source_dir / f"{decomposed}.md"
    path.write_text(
        (
            "---\n"
            "id: ek_src_01HXYZ1234ABCDEFGHJKMNPQRS\n"
            "kind: source\n"
            f"title: {decomposed}\n"
            f"slug: {decomposed}\n"
            "status: authoritative\n"
            "freshness: watch\n"
            "schema_version: '1.0'\n"
            "created_at: 2026-04-16\n"
            "updated_at: 2026-04-17\n"
            "source_type: web\n"
            f"source_url: https://example.com/{decomposed}\n"
            "content_hash: hash-nfd\n"
            f"search_terms: [{decomposed}, fallback, alias, token, demo]\n"
            "tags: [测试]\n"
            "---\n"
            f"[原文](notes/{decomposed}.md)\n"
        ),
        encoding="utf-8",
    )

    findings = _check_nfc_residuals(fresh_ek._registry, ek_root)
    rule_ids = {finding.rule_id for finding in findings}

    assert rule_ids == {
        "nfc_residual_in_frontmatter",
        "nfc_residual_in_path",
        "nfc_residual_in_markdown_link",
        "nfc_residual_in_source_url",
    }


def test_jieba_fallback_summary_emits_when_threshold_exceeded(fresh_ek, ek_root: Path) -> None:
    log_path = ek_root / "logs" / "refresh" / "jieba-fallback.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        json.dumps({"ts_epoch": 2_000_000_000, "token": f"词{i}"}, ensure_ascii=False)
        for i in range(50)
    ]
    log_path.write_text("\n".join(records) + "\n", encoding="utf-8")

    findings = _check_jieba_fallback_summary(fresh_ek._registry, ek_root)

    assert len(findings) == 1
    assert findings[0].rule_id == "jieba_fallback_summary"


# ---------------------------------------------------------------------------
# 2.5  metrics_stale 检查
# ---------------------------------------------------------------------------


def test_metrics_stale_reports_entry_with_old_metrics(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="陈旧指标来源"))

    # entry_metrics.updated_at 设为 2 天前
    stale_time = (
        (_dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=2)).replace(microsecond=0).isoformat()
    )
    fresh_ek._registry.conn.execute(
        "UPDATE entry_metrics SET updated_at = ? WHERE entry_id = ?",
        (stale_time, source.id),
    )
    fresh_ek._registry.commit()

    findings = _check_metrics_stale(fresh_ek._registry, ek_root)

    assert len(findings) >= 1
    stale = [f for f in findings if f.rule_id == "metrics_stale"]
    assert len(stale) == 1
    assert stale[0].severity.value == "medium"
    assert stale[0].target_id == source.id


def test_metrics_stale_does_not_report_fresh_metrics(fresh_ek, ek_root: Path) -> None:
    fresh_ek.ingest("source", source_payload(title="新鲜指标来源"))

    findings = _check_metrics_stale(fresh_ek._registry, ek_root)
    stale = [f for f in findings if f.rule_id == "metrics_stale"]
    assert len(stale) == 0


# ---------------------------------------------------------------------------
# Phase 1 · 术语审计细化测试
# ---------------------------------------------------------------------------


def test_terminology_near_duplicate_triggered_by_edit_distance(fresh_ek, ek_root: Path) -> None:
    """title 编辑距离 ≤ 2 且来自不同条目时触发 terminology_near_duplicate。"""
    source = fresh_ek.ingest("source", source_payload(title="近义词来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="知识抽取",
            search_terms=["知识抽取", "concept-ext", "抽取方法", "理论体系", "alias-ext-a"],
        ),
    )
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="知识抽词",
            search_terms=["知识抽词", "extract", "抽词", "检索抽取", "alias-extract"],
        ),
        conflict_policy="allow",
    )

    findings = _check_terminology_audit(fresh_ek._registry, ek_root)
    near_dups = [f for f in findings if f.rule_id == "terminology_near_duplicate"]

    assert len(near_dups) >= 1
    dup = near_dups[0]
    assert "知识抽取" in dup.message or "知识抽词" in dup.message
    assert dup.target_id is not None
    assert dup.severity.value == "medium"


def test_terminology_cross_language_alias_across_kinds(fresh_ek, ek_root: Path) -> None:
    """不同 kind 共享含中英混合 alias 触发 terminology_cross_language_alias。"""
    source = fresh_ek.ingest("source", source_payload(title="跨语种来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="统一概念",
            aliases=["统一概念", "unified-concept"],
        ),
    )
    fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="独立档案",
            aliases=["unified-concept", "独立档案"],
        ),
        conflict_policy="allow",
    )

    findings = _check_terminology_audit(fresh_ek._registry, ek_root)
    cross = [f for f in findings if f.rule_id == "terminology_cross_language_alias"]

    assert len(cross) >= 1
    finding = cross[0]
    assert "unified-concept" in finding.message
    assert finding.target_id is not None


def test_terminology_audit_healthy_data_no_findings(fresh_ek, ek_root: Path) -> None:
    """术语完全不同且无别名重叠时不触发术语审计 finding。"""
    source_a = fresh_ek.ingest(
        "source",
        source_payload(
            title="健康来源",
            search_terms=["健康来源", "health-source", "origin-doc", "核验材料", "alpha-proof"],
        ),
    )
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source_a.id,
            title="稳定概念",
            aliases=["stable-concept-alias"],
            search_terms=["稳定概念", "stable-concept", "insight-map", "结构结论", "concept-ref"],
        ),
    )
    source_b = fresh_ek.ingest(
        "source",
        source_payload(
            title="天文观测",
            search_terms=["天文观测", "astro-obs", "star-map", "射电望远镜", "gamma-proof"],
        ),
    )
    fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source_b.id,
            title="海洋生态",
            aliases=["marine-eco-alias"],
            search_terms=["海洋生态", "marine-eco", "coral-reef", "独立档案", "dossier-fact"],
        ),
    )

    findings = _check_terminology_audit(fresh_ek._registry, ek_root)

    assert findings == []


def test_terminology_findings_contain_required_fields(fresh_ek, ek_root: Path) -> None:
    """每条 finding 必须包含 rule_id、target_id、message。"""
    source = fresh_ek.ingest("source", source_payload(title="字段验证来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="Graph Mode",
            aliases=["shared", "信任"],
        ),
    )
    fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="Graph M0de",
            aliases=["shared"],
        ),
        conflict_policy="allow",
    )

    findings = _check_terminology_audit(fresh_ek._registry, ek_root)

    for finding in findings:
        assert finding.rule_id, "finding 缺少 rule_id"
        assert finding.target_id is not None, "finding 缺少 target_id"
        assert finding.message, "finding 缺少 message"


# ---------------------------------------------------------------------------
# orphan_relation_type 检查
# ---------------------------------------------------------------------------


def test_orphan_relation_type_detects_invalid_type(fresh_ek, ek_root: Path) -> None:
    """插入一条非法 type 的 relation，doctor 应报出 finding。"""
    source_a = fresh_ek.ingest("source", source_payload(title="关系来源甲"))
    source_b = fresh_ek.ingest("source", source_payload(title="乙号独立数据源"))

    # 直接在 relations 表中插入一条非法 type
    fresh_ek._registry.conn.execute(
        "INSERT INTO relations(source_id, target_id, type, origin) VALUES (?, ?, ?, ?)",
        (source_a.id, source_b.id, "invalid_magic_type", "confirmed"),
    )
    fresh_ek._registry.commit()

    findings = _check_orphan_relation_type(fresh_ek._registry, ek_root)

    assert len(findings) == 1
    assert findings[0].rule_id == "orphan_relation_type"
    assert findings[0].severity.value == "warning"
    assert "invalid_magic_type" in findings[0].message


def test_orphan_relation_type_clean_db_no_findings(fresh_ek, ek_root: Path) -> None:
    """所有 relation type 合法时不应报出 finding。"""
    source = fresh_ek.ingest("source", source_payload(title="合法关系来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="合法概念"),
    )

    findings = _check_orphan_relation_type(fresh_ek._registry, ek_root)
    assert findings == []


# ---------------------------------------------------------------------------
# Phase 3 · _check_alias_conflicts 测试
# ---------------------------------------------------------------------------


def test_alias_conflict_active_entries(fresh_ek, ek_root: Path) -> None:
    """两个 active entry 共享 alias → 应报告 alias_conflicts。"""
    source = fresh_ek.ingest("source", source_payload(title="别名冲突来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="概念甲",
            aliases=["shared-alias-conflict"],
            search_terms=["概念甲", "con-a", "alias-a", "甲号", "alpha-a"],
        ),
    )
    dossier = fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="档案乙",
            aliases=["shared-alias-conflict"],
            search_terms=["档案乙", "dos-b", "alias-b", "乙号", "alpha-b"],
        ),
        conflict_policy="allow",
    )

    findings = _check_alias_conflicts(fresh_ek._registry, ek_root)

    assert len(findings) >= 1
    finding = findings[0]
    assert finding.rule_id == "alias_conflicts"
    assert finding.severity == Severity.MEDIUM
    assert "shared-alias-conflict" in finding.message
    assert concept.id in (finding.target_id or "")
    assert dossier.id in (finding.target_id or "")


def test_alias_conflict_filters_archived(fresh_ek, ek_root: Path) -> None:
    """一个 archived + 一个 active 共享 alias → 不应报告（F4）。"""
    source = fresh_ek.ingest("source", source_payload(title="归档过滤来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="活跃概念",
            aliases=["archived-shared-alias"],
            search_terms=["活跃概念", "con-active", "alias-act", "活跃", "alpha-act"],
        ),
    )
    dossier = fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="归档档案",
            aliases=["archived-shared-alias"],
            search_terms=["归档档案", "dos-arch", "alias-arch", "归档", "alpha-arch"],
        ),
        conflict_policy="allow",
    )

    # 归档其中一个
    fresh_ek._registry.conn.execute(
        "UPDATE entries SET status = 'archived' WHERE id = ?",
        (dossier.id,),
    )
    fresh_ek._registry.commit()

    findings = _check_alias_conflicts(fresh_ek._registry, ek_root)

    assert findings == []


# ---------------------------------------------------------------------------
# Phase 3 · _check_id_uniqueness 测试
# ---------------------------------------------------------------------------


def test_duplicate_id_detected(fresh_ek, ek_root: Path) -> None:
    """模拟 entries 表中有重复 id（绕过 PRIMARY KEY 约束），应报告 id_uniqueness。"""
    source = fresh_ek.ingest("source", source_payload(title="重复ID来源"))

    conn = fresh_ek._registry.conn
    # 临时关闭外键约束，重建 entries 表去掉 PRIMARY KEY
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE entries RENAME TO _entries_orig")
    conn.execute(
        """
        CREATE TABLE entries (
            id TEXT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            slug TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL,
            freshness TEXT NOT NULL,
            confidence TEXT,
            schema_version TEXT NOT NULL,
            domain TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            frontmatter_json TEXT NOT NULL,
            body TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT INTO entries SELECT * FROM _entries_orig")
    conn.execute("INSERT INTO entries SELECT * FROM _entries_orig")
    conn.execute("DROP TABLE _entries_orig")
    conn.execute("PRAGMA foreign_keys = ON")

    findings = _check_id_uniqueness(fresh_ek._registry, ek_root)

    assert len(findings) >= 1
    finding = findings[0]
    assert finding.rule_id == "id_uniqueness"
    assert finding.severity == Severity.HIGH
    assert finding.target_id == source.id
    assert "数据完整性" in finding.message


def test_no_finding_when_all_unique(fresh_ek, ek_root: Path) -> None:
    """正常数据 entries 表 id 唯一，不应报告 id_uniqueness。"""
    source = fresh_ek.ingest("source", source_payload(title="唯一ID来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="唯一ID概念"),
    )

    findings = _check_id_uniqueness(fresh_ek._registry, ek_root)

    assert findings == []


# ---------------------------------------------------------------------------
# Phase 3 · _check_orphan_files 测试
# ---------------------------------------------------------------------------


def test_orphan_file_detected(fresh_ek, ek_root: Path) -> None:
    """文件系统有 .md 但 registry 无记录 → 应报告 orphan_files。"""
    fresh_ek.ingest("source", source_payload(title="正常条目来源"))

    # 创建一个不在 registry 的孤儿文件
    orphan_dir = ek_root / "entries" / "concepts" / "_unsorted"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan_path = orphan_dir / "orphan-ghost-entry.md"
    orphan_path.write_text(
        "---\nid: orphan-ghost-001\nkind: concept\n---\nghost body",
        encoding="utf-8",
    )

    findings = _check_orphan_files(fresh_ek._registry, ek_root)

    orphan_findings = [f for f in findings if f.rule_id == "orphan_files"]
    assert len(orphan_findings) >= 1
    finding = orphan_findings[0]
    assert finding.severity == Severity.MEDIUM
    assert "orphan-ghost-entry.md" in (finding.target_path or "")


def test_views_indexes_not_scanned(fresh_ek, ek_root: Path) -> None:
    """views/indexes/ 下的孤儿文件不应报出（W6）。"""
    fresh_ek.ingest("source", source_payload(title="视图排除来源"))

    # views/ 下放文件
    views_dir = ek_root / "views" / "indexes"
    views_dir.mkdir(parents=True, exist_ok=True)
    views_file = views_dir / "orphan-view.md"
    views_file.write_text(
        "---\nid: orphan-view-001\n---\nview body",
        encoding="utf-8",
    )

    findings = _check_orphan_files(fresh_ek._registry, ek_root)

    assert findings == []


# ---------------------------------------------------------------------------
# _check_fullwidth_chars 测试
# ---------------------------------------------------------------------------


def test_fullwidth_in_body_detects_fullwidth_letters(fresh_ek, ek_root: Path) -> None:
    """正文含全角字母 → 应报告 fullwidth_in_body finding。"""
    write_entry_with_body(ek_root, "fw-letters-001", "这是ＡＩ编程")

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "fullwidth_in_body"
    assert f.severity == Severity.LOW
    assert f.target_id == "fw-letters-001"
    assert "Ａ" in f.message or "Ｉ" in f.message
    assert "U+FF21" in f.message or "U+FF29" in f.message


def test_fullwidth_in_body_detects_fullwidth_digits(fresh_ek, ek_root: Path) -> None:
    """正文含全角数字 → 应报告 fullwidth_in_body finding。"""
    write_entry_with_body(ek_root, "fw-digits-001", "版本１２３发布")

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert len(findings) == 1
    assert findings[0].rule_id == "fullwidth_in_body"
    assert findings[0].severity == Severity.LOW
    assert "１" in findings[0].message


def test_fullwidth_in_body_no_findings_for_clean_body(fresh_ek, ek_root: Path) -> None:
    """正文无全角字符 → 不应报告 finding。"""
    write_entry_with_body(ek_root, "clean-001", "这是正常的AI编程版本123")

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert findings == []


def test_fullwidth_in_body_fullwidth_punctuation_not_triggered(fresh_ek, ek_root: Path) -> None:
    """全角标点（如 U+FF01 ！」）对应的半角不是字母数字 → 不触发。"""
    # U+FF01 = ！, halfwidth = '!' (not alnum)
    write_entry_with_body(ek_root, "fw-punct-001", "注意\uff01这是重点")

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert findings == []


def test_fullwidth_in_body_one_finding_per_file(fresh_ek, ek_root: Path) -> None:
    """一行内多个全角字母 + 多行全角 → 一文件仅一条 finding。"""
    body = "第一行有Ａ和Ｂ\n第二行有Ｃ和Ｄ"
    write_entry_with_body(ek_root, "fw-multi-001", body)

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert len(findings) == 1
    assert findings[0].rule_id == "fullwidth_in_body"


def test_fullwidth_in_body_correct_line_number(fresh_ek, ek_root: Path) -> None:
    """全角字符出现在第 3 行 → finding 消息应含正确的行号。"""
    body = "第一行正常\n第二行也正常\n第三行有Ａ字母"
    write_entry_with_body(ek_root, "fw-line-001", body)

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert len(findings) == 1
    assert "第 3 行" in findings[0].message


def test_fullwidth_in_body_cjk_not_triggered(fresh_ek, ek_root: Path) -> None:
    """中文正文（CJK 字符在 0xFF01-0xFF5E 范围外）→ 不触发。"""
    write_entry_with_body(ek_root, "cjk-001", "这是纯中文内容没有任何全角ASCII")

    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert findings == []


def test_fullwidth_in_body_empty_data_root(fresh_ek, ek_root: Path) -> None:
    """空 data_root 无条目文件 → 不报 finding。"""
    findings = _check_fullwidth_chars(fresh_ek._registry, ek_root)

    assert findings == []


# ---------------------------------------------------------------------------
# Phase 1 · 1.1 doctor unicode 检查口径调整测试已拆出至：
#   tests/unit/test_doctor_unicode.py（聚焦 spec 决策 1 分层口径）
# Phase 1 · 1.2 doctor integrity 断裂关系测试已拆出至：
#   tests/unit/test_doctor_broken_relations.py（聚焦 spec 决策 3 origin 分类）
# 共享辅助（write_entry_with_body / insert_broken_relation）见：
#   tests/unit/_doctor_helpers.py
# ---------------------------------------------------------------------------
