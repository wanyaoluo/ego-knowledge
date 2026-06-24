"""Integration tests for ek search --semantic / --no-semantic CLI flags (Phase 7.6).

Tests CLI flag behavior: mutual exclusivity, backend selection, and error messages.
SearchRouter-level dense behavior is covered by test_dense_search.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

import ego_knowledge.cli as cli_module
from ego_knowledge._dense_embedder import EMBEDDING_DIM, EmbedResult
from ego_knowledge._dense_index import store_embedding
from ego_knowledge._embedding_hash import compute_embedding_content_hash
from ego_knowledge.cli import main
from ego_knowledge.core import EgoKnowledge
from tests.unit.support import concept_payload, source_payload


def _embedding(first_value: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = first_value
    return vector


class FakeEmbedder:
    last_model_revision = "fake-rev"

    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        return EmbedResult(
            embeddings=[_embedding(1.0) for _ in texts],
            model_revision="fake-rev",
            tokens_used=len(texts),
        )

    def embed_cached(self, entry_id: str, embedding_content_hash: str, text: str) -> list[float]:
        del entry_id
        assert embedding_content_hash
        assert text
        return _embedding(1.0)


@pytest.fixture()
def _seeded_env(ek_root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Seed entries with dense embeddings and set EK_DATA_ROOT."""
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    source = ek.ingest("source", source_payload(title="semantic cli source"))
    target = ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="语义检索测试目标",
            body="这是一个用于 CLI --semantic flag 测试的条目。" + "y" * 20,
        ),
    )
    target_entry = ek.get(target.id)
    store_embedding(
        ek._registry,
        target.id,
        _embedding(1.0),
        compute_embedding_content_hash(target_entry),
        "test-rev",
    )
    ek.close()

    monkeypatch.setenv("EK_DATA_ROOT", str(ek_root))
    monkeypatch.setattr(
        "ego_knowledge._secrets.get_siliconflow_api_key",
        lambda: "example-siliconflow-key",
    )

    def fake_get_ek(*, dense_disabled: bool = False) -> EgoKnowledge:
        return EgoKnowledge(
            ek_root,
            dense_disabled=dense_disabled,
            dense_embedder=None if dense_disabled else FakeEmbedder(),
        )

    monkeypatch.setattr(cli_module, "_get_ek", fake_get_ek)
    yield ek_root


def _invoke(*args: str) -> Result:
    runner = CliRunner()
    return runner.invoke(main, ["search", *args])


def test_search_default_includes_dense(_seeded_env: Path) -> None:
    """默认模式下，如果有 api_key + dense 索引，结果中包含 dense backend."""
    result = _invoke("语义检索")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "results" in data
    # At least one result should have "dense" in backends
    has_dense = any("dense" in r.get("backends", []) for r in data["results"])
    assert has_dense, f"Expected dense in backends, got: {data['results']}"


def test_search_no_semantic_excludes_dense(_seeded_env: Path) -> None:
    """--no-semantic 时结果中不包含 dense backend."""
    result = _invoke("语义检索", "--no-semantic")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    for r in data.get("results", []):
        assert "dense" not in r.get("backends", []), (
            f"--no-semantic should exclude dense, but got: {r['backends']}"
        )


def test_search_backend_dense_only(_seeded_env: Path) -> None:
    """--backend dense 时只有 dense backend."""
    result = _invoke("语义检索", "--backend", "dense")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["results"], "Expected results from dense-only search"
    for r in data["results"]:
        assert r["backends"] == ["dense"], f"Expected only dense backend, got: {r['backends']}"


def test_semantic_and_no_semantic_mutual_exclusive(_seeded_env: Path) -> None:
    """--semantic + --no-semantic 同时传入应报错."""
    result = _invoke("测试", "--semantic", "--no-semantic")
    assert result.exit_code != 0
    assert "不能同时使用" in result.output or "不能同时使用" in (result.stderr or "")


def test_no_semantic_with_backend_dense_conflict(_seeded_env: Path) -> None:
    """--no-semantic + --backend dense 应报冲突."""
    result = _invoke("测试", "--no-semantic", "--backend", "dense")
    assert result.exit_code != 0


def test_backend_dense_empty_index(ek_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--backend dense 但 dense 索引为空时应报 ValidationError."""
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    ek.ingest("source", source_payload(title="no dense index"))
    ek.close()

    monkeypatch.setenv("EK_DATA_ROOT", str(ek_root))
    monkeypatch.setattr(
        "ego_knowledge._secrets.get_siliconflow_api_key",
        lambda: "example-siliconflow-key",
    )
    result = _invoke("测试", "--backend", "dense")
    assert result.exit_code != 0
    output = result.output or ""
    assert "索引为空" in output or "EK_VALIDATION" in output


def test_semantic_without_api_key(ek_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--semantic 但 api_key 未配置应报错."""
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    ek.ingest("source", source_payload(title="no api key"))
    ek.close()

    monkeypatch.setenv("EK_DATA_ROOT", str(ek_root))
    monkeypatch.setattr("ego_knowledge._secrets.get_siliconflow_api_key", lambda: None)
    result = _invoke("测试", "--semantic")
    assert result.exit_code != 0


def test_backend_dense_without_api_key(
    ek_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--backend dense 但 api_key 未配置应报错."""
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    ek.ingest("source", source_payload(title="backend dense no api key"))
    ek.close()

    monkeypatch.setenv("EK_DATA_ROOT", str(ek_root))
    monkeypatch.setattr("ego_knowledge._secrets.get_siliconflow_api_key", lambda: None)
    result = _invoke("测试", "--backend", "dense")
    assert result.exit_code != 0
