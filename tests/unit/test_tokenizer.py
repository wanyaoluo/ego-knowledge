from __future__ import annotations

import json
from pathlib import Path

from ego_knowledge import tokenizer
from ego_knowledge.tokenizer import rebuild_custom_dict, tokenize

from .support import concept_payload, source_payload


def test_tokenize_fallback_logs_jsonl(tmp_path: Path, monkeypatch) -> None:
    def _boom(_: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(tokenizer, "init_jieba", lambda custom_dict_dir=None: None)
    monkeypatch.setattr(tokenizer.jieba, "cut", _boom)

    log_path = tmp_path / "logs" / "refresh" / "jieba-fallback.log"
    tokens = tokenize("罕见术语", fallback_log_path=log_path)

    assert tokens == ["罕见术", "见术语"]
    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert set(record) == {"ts_epoch", "token"}
    assert record["token"] == "罕见术语"


def test_rebuild_custom_dict_collects_aliases_and_tags(fresh_ek, tmp_path: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="分词来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="图检索",
            aliases=["知识图谱", "图检索"],
            tags=["检索", "图谱"],
        ),
    )

    output_dir = tmp_path / "registry" / "jieba"
    rebuild_custom_dict(fresh_ek._registry, output_dir)

    content = (output_dir / "ek-auto.txt").read_text(encoding="utf-8")
    assert "知识图谱 5" in content
    assert "检索 5" in content
