from __future__ import annotations

import inspect

from ego_knowledge.core import EgoKnowledge

EXPOSED_TOOLS = {
    "search",
    "get",
    "related",
    "review_queue",
    "ingest",
    "update",
    "promote",
    "link",
    "unlink",
    "doctor",
    "diagnose",
    "stats",
    "domains_list",
    "domains_add",
    "domains_migrate",
}
CLI_ONLY_METHODS = {
    "add_external_watch",
    "build_registry",
    "close",
    "dense_embedder_available",
    "dense_index_populated",
    "establish_diagnose_baseline",
    "list_external_watches",
    "list_sources_by_target",
    "related_basic",
    "poll_external_watches",
    "rebuild_dense_index",
    "recompute_authority",
    "rename",
    "maintenance_queue_review",
    "source_exists_by_hash",
    "touch",
    "write_stats_snapshot",
}


def test_all_core_methods_have_mcp_tool_or_skip() -> None:
    public_methods = {
        name
        for name, member in inspect.getmembers(EgoKnowledge, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    assert len(EXPOSED_TOOLS) + len(CLI_ONLY_METHODS) == 32
    assert len(public_methods) == 32, f"Core 公开方法数异动：{sorted(public_methods)}"

    union = EXPOSED_TOOLS | CLI_ONLY_METHODS
    missing = public_methods - union
    extra = union - public_methods

    assert not missing, f"Core 新增方法未在 EXPOSED_TOOLS 或 CLI_ONLY_METHODS 中：{missing}"
    assert not extra, f"EXPOSED_TOOLS/CLI_ONLY_METHODS 引用了不存在的 Core 方法：{extra}"
