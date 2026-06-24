"""Integration tests for ek review command.

Coverage spans Phase 3 Task 3.7 maintenance queue basics and Phase 8.3
AI approval CLI additions: --origin, --approve, --reject and --reason.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from ego_knowledge._autonomous import ingest_autonomous
from ego_knowledge.cli import main
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.doctor import Finding, Severity
from ego_knowledge.maintenance_queue_store import enqueue
from tests.unit.support import concept_payload, source_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    rule_id: str = "decay_note_stagnant",
    *,
    severity: Severity = Severity.MEDIUM,
    entry_id: str = "ek_not_01HABC",
    message: str = "笔记停滞超过 30 天",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        target_id=entry_id,
        target_path="/entries/note.md",
        message=message,
    )


def _seed_queue(ek: EgoKnowledge, n: int = 3) -> list[str]:
    """Ingest real entries, then enqueue n medium findings and return their queue ids."""
    ids = []
    for i in range(n):
        source = ek.ingest(
            "source",
            {
                "title": f"队列来源 {i}",
                "source_type": "web",
                "source_url": f"https://example.com/queue-{i}",
                "content_hash": f"hash-queue-{i}",
                "search_terms": [f"队列来源 {i}", "source", "src", "队列来源", f"alias-queue-{i}"],
                "tags": ["测试"],
            },
        )
        finding = _make_finding(
            rule_id=f"decay_note_stagnant_{i}",
            entry_id=source.id,
        )
        qid = enqueue(ek._registry, finding)
        ids.append(qid)
    return ids


def _cli_env(data_root: Path) -> dict[str, str]:
    """Build env dict for CliRunner that points EK_DATA_ROOT at *data_root*."""
    return {**os.environ, "EK_DATA_ROOT": str(data_root)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReviewDefaultMode:
    """Default `ek review` lists maintenance_queue pending items."""

    def test_default_lists_pending(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        _seed_queue(fresh_ek, 3)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review"])
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert data["total"] == 3
        assert len(data["grouped"]["human"]) == 3

    def test_default_updates_last_reviewed_at(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        _seed_queue(fresh_ek, 1)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review"])
        assert result.exit_code == 0

        # Verify registry_meta was updated
        row = fresh_ek._registry.conn.execute(
            "SELECT value FROM registry_meta WHERE key = 'last_reviewed_at'"
        ).fetchone()
        assert row is not None
        assert row["value"] > "1970-01-01"

    def test_empty_queue_shows_zero(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert data["total"] == 0
        assert data["grouped"] == {}

    def test_first_review_all_new(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        """First review (no last_reviewed_at) marks all as new."""
        ek_root.mkdir(parents=True, exist_ok=True)
        _seed_queue(fresh_ek, 2)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert data["new_count"] == 2

    def test_second_review_only_new(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        """After first review, only items created after are new."""
        ek_root.mkdir(parents=True, exist_ok=True)
        _seed_queue(fresh_ek, 2)

        # First review
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review"])
        assert result.exit_code == 0

        # Add one more item (ingest a real entry first)
        new_source = fresh_ek.ingest(
            "source",
            {
                "title": "新增队列来源",
                "source_type": "web",
                "source_url": "https://example.com/queue-new",
                "content_hash": "hash-queue-new",
                "search_terms": ["新增队列来源", "source", "src", "队列来源", "alias-queue-new"],
                "tags": ["测试"],
            },
        )
        enqueue(
            fresh_ek._registry,
            _make_finding(rule_id="decay_new_item", entry_id=new_source.id),
        )

        # Second review
        result = runner.invoke(main, ["review"])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert data["total"] == 3
        assert data["new_count"] == 1


class TestReviewIdDetail:
    """`ek review --id <mq_id>` shows item detail."""

    def test_id_shows_detail(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        ids = _seed_queue(fresh_ek, 1)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review", "--id", ids[0]])
        assert result.exit_code == 0, result.output

        data = json.loads(result.output)
        assert data["id"] == ids[0]
        assert data["rule_id"] == "decay_note_stagnant_0"

    def test_id_not_found(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review", "--id", "mq_nonexistent"])
        assert result.exit_code != 0


class TestReviewResolve:
    """`ek review --resolve <mq_id>` marks item resolved."""

    def test_resolve_success(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        ids = _seed_queue(fresh_ek, 1)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review", "--resolve", ids[0]])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["action"] == "resolved"

        # Verify it's no longer pending
        result2 = runner.invoke(main, ["review"])
        data2 = json.loads(result2.output)
        assert data2["total"] == 0


class TestReviewDismiss:
    """`ek review --dismiss <mq_id>` marks item dismissed."""

    def test_dismiss_success(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        ids = _seed_queue(fresh_ek, 1)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review", "--dismiss", ids[0]])
        assert result.exit_code == 0

        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["action"] == "dismissed"


class TestReviewAiApproval:
    def test_origin_filter_returns_flat_ai_proposed_items(
        self,
        ek_root: Path,
        fresh_ek: EgoKnowledge,
    ) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        source = fresh_ek.ingest("source", source_payload(title="origin 过滤来源"))
        result = ingest_autonomous(
            fresh_ek,
            "rename",
            {"id": source.id, "new_slug": "origin-filter-source"},
            agent_id="agent-a",
        )
        assert result.queue_id is not None

        runner = CliRunner(env=_cli_env(ek_root))
        cli_result = runner.invoke(main, ["review", "--origin", "ai_proposed"])

        assert cli_result.exit_code == 0, cli_result.output
        data = json.loads(cli_result.output)
        assert data["total"] == 1
        assert data["items"][0]["id"] == result.queue_id
        assert data["items"][0]["origin"] == "ai_proposed"
        assert "grouped" not in data

    def test_approve_executes_ai_proposal(
        self,
        ek_root: Path,
        fresh_ek: EgoKnowledge,
    ) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        source = fresh_ek.ingest("source", source_payload(title="CLI 批准来源"))
        concept = fresh_ek.ingest("concept", concept_payload(source.id, title="CLI 批准概念"))
        result = ingest_autonomous(
            fresh_ek,
            "rename",
            {"id": concept.id, "new_slug": "cli-approved-concept"},
            agent_id="agent-a",
        )
        assert result.queue_id is not None

        runner = CliRunner(env=_cli_env(ek_root))
        cli_result = runner.invoke(main, ["review", "--id", result.queue_id, "--approve"])

        assert cli_result.exit_code == 0, cli_result.output
        data = json.loads(cli_result.output)
        assert data["ok"] is True
        assert data["executed_op"] == "rename"
        assert fresh_ek.get(concept.id).slug == "cli-approved-concept"

    def test_reject_dismisses_ai_proposal(
        self,
        ek_root: Path,
        fresh_ek: EgoKnowledge,
    ) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        source = fresh_ek.ingest("source", source_payload(title="CLI 拒绝来源"))
        result = ingest_autonomous(
            fresh_ek,
            "rename",
            {"id": source.id, "new_slug": "cli-rejected-source"},
            agent_id="agent-a",
        )
        assert result.queue_id is not None

        runner = CliRunner(env=_cli_env(ek_root))
        cli_result = runner.invoke(
            main,
            ["review", "--id", result.queue_id, "--reject", "--reason", "不需要"],
        )

        assert cli_result.exit_code == 0, cli_result.output
        data = json.loads(cli_result.output)
        assert data["ok"] is True
        assert data["rejected_reason"] == "不需要"
        dismissed = fresh_ek.maintenance_queue_review(dismiss_id=None, origin="ai_proposed")
        assert dismissed["total"] == 0

    def test_approve_requires_id(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))

        result = runner.invoke(main, ["review", "--approve"])

        assert result.exit_code != 0
        assert "需要配合 --id" in result.output or "Usage" in result.output

    def test_approve_and_reject_mutually_exclusive(
        self,
        ek_root: Path,
        fresh_ek: EgoKnowledge,
    ) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))

        result = runner.invoke(main, ["review", "--id", "mq_x", "--approve", "--reject"])

        assert result.exit_code != 0
        assert "--approve 和 --reject 互斥" in result.output or "Usage" in result.output

    def test_approve_and_resolve_mutually_exclusive(
        self,
        ek_root: Path,
        fresh_ek: EgoKnowledge,
    ) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))

        result = runner.invoke(
            main,
            ["review", "--id", "mq_x", "--approve", "--resolve", "mq_y"],
        )

        assert result.exit_code != 0
        assert "--resolve" in result.output or "Usage" in result.output


class TestReviewMutualExclusivity:
    """--id / --resolve / --dismiss are mutually exclusive."""

    def test_id_and_resolve_mutually_exclusive(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review", "--id", "mq_x", "--resolve", "mq_y"])
        assert result.exit_code != 0
        assert "互斥" in result.output or "Usage" in result.output


class TestReviewDueCompat:
    """`ek review --due` (and --overdue) use legacy review_due_at mode."""

    def test_due_returns_entries(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        """--due triggers legacy review_queue path, returns entry list."""
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review", "--due"])
        # No dossier entries → empty list
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)

    def test_overdue_backward_compat(self, ek_root: Path, fresh_ek: EgoKnowledge) -> None:
        """--overdue still works as alias for --due."""
        ek_root.mkdir(parents=True, exist_ok=True)
        runner = CliRunner(env=_cli_env(ek_root))
        result = runner.invoke(main, ["review", "--overdue"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
