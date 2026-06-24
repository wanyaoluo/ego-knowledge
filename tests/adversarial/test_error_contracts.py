from __future__ import annotations

import importlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from ego_knowledge.mcp_server import mcp
from tests.unit.support import concept_payload, note_payload, source_payload

mcp_server_mod = importlib.import_module("ego_knowledge.mcp_server.mcp_server")


@pytest.fixture()
def bound_core(monkeypatch: pytest.MonkeyPatch, fresh_ek):
    monkeypatch.setattr(mcp_server_mod, "_core", fresh_ek)
    return fresh_ek


@pytest.fixture()
def preseed_source(bound_core):
    return bound_core.ingest(
        "source",
        source_payload(
            title="RAG Survey 2026 参考资料",
            source_type="doc",
            source_url="https://example.com/rag-survey-2026",
            captured_at="2026-04-10",
            content_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            body="# RAG Survey\n",
            search_terms=[
                "RAG survey",
                "检索增强生成综述",
                "retrieval-augmented-generation",
                "综述",
                "paper",
            ],
        ),
    )


@pytest.fixture()
def preseed_conflict(bound_core, preseed_source):
    return bound_core.ingest(
        "note",
        note_payload(
            preseed_source.id,
            title="RAG 方案横评",
            aliases=["RAG 横评"],
            extracted_at="2026-04-17",
            promotion_targets=[],
            body="# 预置冲突条目\n\n" + "x" * 50,
            search_terms=[
                "RAG",
                "检索增强生成",
                "retrieval-augmented",
                "RAG 横评",
                "rag-comparison",
            ],
        ),
    )


@pytest.fixture()
def preseed_concept(bound_core, preseed_source):
    return bound_core.ingest(
        "concept",
        concept_payload(
            preseed_source.id,
            title="检索增强生成",
            aliases=["RAG 概念"],
            body="# 概念\n\n" + "x" * 60,
            search_terms=[
                "检索增强生成",
                "retrieval augmented generation",
                "RAG",
                "检索增强",
                "rag-concept",
            ],
        ),
    )


def call_tool(name: str, **kwargs: object) -> object:
    return mcp._tool_manager.get_tool(name).fn(**kwargs)


def parse_tool_error(excinfo: pytest.ExceptionInfo[ToolError]) -> dict[str, object]:
    payload = json.loads(excinfo.value.args[0])
    assert set(payload) == {"error_type", "message", "details"}
    return payload


def assert_tool_error(
    excinfo: pytest.ExceptionInfo[ToolError],
    *,
    error_type: str,
    message_fragment: str,
    details: object,
) -> dict[str, object]:
    payload = parse_tool_error(excinfo)
    assert payload["error_type"] == error_type
    assert message_fragment in payload["message"]
    assert payload["details"] == details
    return payload


def test_ingest_missing_title_contract(bound_core) -> None:
    payload = source_payload(
        title="临时来源",
        source_type="doc",
        source_url="https://example.com/missing-title",
        captured_at="2026-04-10",
        content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    payload.pop("title")

    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_ingest", kind="source", payload=payload, conflict_policy="strict")

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="ingest payload 缺少非空 title",
        details={},
    )


def test_ingest_schema_failure_contract(bound_core) -> None:
    payload = source_payload(
        title="Schema 缺字段来源",
        source_type="doc",
        captured_at="2026-04-10",
        content_hash="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    payload.pop("source_url")

    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_ingest", kind="source", payload=payload, conflict_policy="strict")

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="schema 校验失败",
        details={},
    )


def test_ingest_conflict_contract_has_candidates(
    bound_core,
    preseed_source,
    preseed_conflict,
) -> None:
    del bound_core, preseed_conflict
    payload = note_payload(
        preseed_source.id,
        title="RAG 方案横评 2",
        aliases=["RAG 横评"],
        slug="rag-方案横评-2",
        extracted_at="2026-04-17",
        promotion_targets=[],
        body="# 测试相近标题\n\n" + "x" * 50,
        search_terms=[
            "RAG",
            "检索增强生成",
            "retrieval-augmented",
            "RAG 横评 2",
            "rag-comparison-2",
        ],
    )

    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_ingest", kind="note", payload=payload, conflict_policy="strict")

    payload = parse_tool_error(tool_err)
    assert payload["error_type"] == "conflict_error"
    assert "冲突：检测到候选重复条目" in payload["message"]
    assert payload["details"]["candidates"]
    assert all(set(candidate) == {"id", "title"} for candidate in payload["details"]["candidates"])


def test_ingest_search_terms_bucket_contract(bound_core) -> None:
    payload = source_payload(
        title="别称校验",
        source_type="doc",
        source_url="https://example.com/search-terms-bucket",
        captured_at="2026-04-10",
        content_hash="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        search_terms=["别称校验", "别称", "校验", "误写", "错写"],
    )

    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_ingest", kind="source", payload=payload, conflict_policy="strict")

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="search_terms 三桶未覆盖",
        details={},
    )


def test_ingest_rejects_invalid_conflict_policy_contract(bound_core) -> None:
    payload = source_payload(
        title="非法策略来源",
        source_url="https://example.com/invalid-policy",
        captured_at="2026-04-10",
        content_hash="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    )

    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_ingest", kind="source", payload=payload, conflict_policy="merge")

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="不支持的 conflict_policy: merge",
        details={},
    )


def test_update_title_conflict_contract_has_candidates(
    bound_core,
    preseed_source,
    preseed_conflict,
) -> None:
    del bound_core, preseed_conflict
    candidate = call_tool(
        "ek_ingest",
        kind="note",
        payload=note_payload(
            preseed_source.id,
            title="另一条候选记录",
            aliases=["第二候选"],
            slug="note-second-candidate",
            extracted_at="2026-04-17",
            promotion_targets=[],
            body="# 第二条\n\n" + "x" * 50,
            search_terms=[
                "另一条候选记录",
                "second note",
                "候选记录",
                "第二候选",
                "second-candidate",
            ],
        ),
        conflict_policy="strict",
    )

    with pytest.raises(ToolError) as tool_err:
        call_tool(
            "ek_update",
            id=candidate["id"],
            changes={"title": "RAG 方案横评", "aliases": ["RAG 横评"]},
        )

    payload = parse_tool_error(tool_err)
    assert payload["error_type"] == "conflict_error"
    assert "冲突：检测到候选重复条目" in payload["message"]
    assert payload["details"]["candidates"]
    assert all(set(candidate) == {"id", "title"} for candidate in payload["details"]["candidates"])


def test_get_missing_entry_contract(bound_core) -> None:
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_get", id="ek_src_missing")

    assert_tool_error(
        tool_err,
        error_type="not_found_error",
        message_fragment="条目不存在: ek_src_missing",
        details={},
    )


def test_get_path_like_missing_entry_contract(bound_core) -> None:
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_get", id="../../etc/passwd")

    assert_tool_error(
        tool_err,
        error_type="not_found_error",
        message_fragment="条目不存在: ../../etc/passwd",
        details={},
    )


def test_link_missing_target_contract(bound_core, preseed_source) -> None:
    with pytest.raises(ToolError) as tool_err:
        call_tool(
            "ek_link",
            source_id=preseed_source.id,
            target_id="ek_con_missing",
            rel_type="related",
        )

    assert_tool_error(
        tool_err,
        error_type="not_found_error",
        message_fragment="条目不存在: ek_con_missing",
        details={},
    )


def test_link_invalid_rel_type_contract(bound_core, preseed_source) -> None:
    target = bound_core.ingest(
        "source",
        source_payload(
            title="关系目标来源",
            source_url="https://example.com/relation-target",
            content_hash="sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        ),
    )

    with pytest.raises(ToolError) as tool_err:
        call_tool(
            "ek_link",
            source_id=preseed_source.id,
            target_id=target.id,
            rel_type="friend_of",
        )

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="rel_type 'friend_of' 不在 RelationType 枚举中",
        details={},
    )


def test_maintain_invalid_action_contract(bound_core) -> None:
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_maintain", action="doctors")

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="doctors",
        details={"valid_actions": ["diagnose", "doctor", "stats"]},
    )


def test_domains_invalid_action_contract(bound_core) -> None:
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_domains", action="rename")

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="rename",
        details={"valid_actions": ["add", "list", "migrate"]},
    )


def test_domains_add_missing_name_contract(bound_core) -> None:
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_domains", action="add")

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="requires 'name'",
        details={"missing_fields": ["name"]},
    )


@pytest.mark.parametrize(
    ("kwargs", "missing_fields"),
    [
        ({}, ["entries", "target_domain"]),
        ({"entries": ["ek_con_1"]}, ["target_domain"]),
        ({"target_domain": "rag"}, ["entries"]),
    ],
)
def test_domains_migrate_missing_fields_contract(
    bound_core,
    kwargs: dict[str, object],
    missing_fields: list[str],
) -> None:
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_domains", action="migrate", **kwargs)

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="requires 'entries' + 'target_domain'",
        details={"missing_fields": missing_fields},
    )


def test_runtime_error_falls_back_to_internal_error_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenCore:
        def search(self, **_: object) -> object:
            raise RuntimeError("boom")

    monkeypatch.setattr(mcp_server_mod, "_core", BrokenCore())

    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_search", query="runtime")

    assert_tool_error(
        tool_err,
        error_type="internal_error",
        message_fragment="boom",
        details={"tool": "ek_search"},
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("id", "ek_con_override"),
        ("kind", "source"),
        ("file_path", "override/path.md"),
        ("metrics", {"freshness_score": 1.0}),
    ],
)
def test_update_forbidden_fields_contract(
    bound_core,
    preseed_source,
    field_name: str,
    value: object,
) -> None:
    del bound_core
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_update", id=preseed_source.id, changes={field_name: value})

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment=f"字段 {field_name} 不允许通过 update() 修改",
        details={},
    )


def test_update_concept_slug_requires_rename(bound_core, preseed_concept) -> None:
    del bound_core
    with pytest.raises(ToolError) as tool_err:
        call_tool("ek_update", id=preseed_concept.id, changes={"slug": "new-concept-slug"})

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="concept/dossier/decision 改 slug 请走 rename()",
        details={},
    )


# ---------------------------------------------------------------------------
# Phase 5.5 · allow 不绕四道护栏——对抗用例
# ---------------------------------------------------------------------------


def test_allow_same_slug_still_rejected_by_slug_gate(
    bound_core, preseed_source, preseed_concept
) -> None:
    """allow + 同名 slug → ConflictError（allow 不绕 slug 护栏）。"""
    del bound_core
    with pytest.raises(ToolError) as tool_err:
        call_tool(
            "ek_ingest",
            kind="concept",
            payload=concept_payload(
                preseed_source.id,
                title=preseed_concept.title,
                body="# 同 slug 副本\n\n" + "x" * 60,
                search_terms=[
                    preseed_concept.title,
                    "allow-slug-dup",
                    "slug对抗",
                    "allow-gate",
                    "adversarial-slug",
                ],
            ),
            conflict_policy="allow",
        )

    assert_tool_error(
        tool_err,
        error_type="conflict_error",
        message_fragment="slug",
        details={},
    )


def test_allow_short_body_still_rejected_by_body_gate(bound_core, preseed_source) -> None:
    """allow + 空 body → ValidationError（allow 不绕 body 护栏）。"""
    del bound_core
    with pytest.raises(ToolError) as tool_err:
        call_tool(
            "ek_ingest",
            kind="concept",
            payload={
                **concept_payload(
                    preseed_source.id,
                    title="allow-短body对抗",
                    search_terms=[
                        "allow-短body对抗",
                        "allow-body-short",
                        "短body对抗",
                        "body-gate",
                        "adversarial-body",
                    ],
                ),
                "body": "短",
            },
            conflict_policy="allow",
        )

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="body",
        details={},
    )


def test_allow_oversize_terms_still_rejected_by_terms_gate(bound_core, preseed_source) -> None:
    """allow + 超长 terms → ValidationError（allow 不绕 terms 护栏）。"""
    del bound_core
    oversize_term = "这是一个故意超过四十个字符长度的搜索词条用来验证allow不绕过search-terms护栏"
    with pytest.raises(ToolError) as tool_err:
        call_tool(
            "ek_ingest",
            kind="note",
            payload=note_payload(
                preseed_source.id,
                title="allow-超长terms对抗",
                body="# allow 超长 terms 对抗\n\n" + "x" * 50,
                search_terms=[
                    oversize_term,
                    "allow-terms-long",
                    "超长terms",
                    "terms-gate",
                    "adversarial-terms",
                ],
            ),
            conflict_policy="allow",
        )

    assert_tool_error(
        tool_err,
        error_type="validation_error",
        message_fragment="search_terms",
        details={},
    )
