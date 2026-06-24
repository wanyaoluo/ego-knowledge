"""L3 latency benchmark: ingest / update / link / promote under 100-entry baseline.

Measures p95 < 200ms and max < 500ms for each of 4 mutating entry points,
using time.perf_counter. No pytest-benchmark dependency.
"""

from __future__ import annotations

import hashlib
import statistics
import time
from itertools import count

import pytest

from ego_knowledge.core import EgoKnowledge
from tests.unit.support import note_payload, source_payload

pytestmark = pytest.mark.slow

# Global sequence counter for unique titles across test runs.
_seq = count(10_000)


def _idx() -> int:
    return next(_seq)


def _short_hash(seed: int) -> str:
    """8-char hex hash from int seed; guarantees Levenshtein distance >> 2."""
    return hashlib.sha256(str(seed).encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Payload builders (Levenshtein distance >> 2 between any two entries)
# ---------------------------------------------------------------------------


def _bench_source(idx: int) -> dict[str, object]:
    """Source payload with ≥5 search_terms (中文≥1, 英文≥1, alias≥1)."""
    h = _short_hash(idx)
    return source_payload(
        title=f"延迟基准来源#{h}",
        source_url=f"https://bench.example/{idx:04d}",
        content_hash=f"bench-{idx:04d}",
        search_terms=[
            f"延迟基准来源#{h}",
            f"bench-src-{idx:04d}",
            "benchmark",
            "来源基准",
            f"alias-bs-{idx:04d}",
        ],
        tags=["基准测试"],
    )


def _bench_note(source_id: str, idx: int) -> dict[str, object]:
    h = _short_hash(idx)
    return note_payload(
        source_id,
        title=f"延迟基准笔记#{h}",
        search_terms=[
            f"延迟基准笔记#{h}",
            f"bench-nt-{idx:04d}",
            "benchmark",
            "笔记基准",
            f"alias-bn-{idx:04d}",
        ],
        tags=["基准测试"],
    )


# ---------------------------------------------------------------------------
# Seed + measurement helpers
# ---------------------------------------------------------------------------


def _seed(ek: EgoKnowledge, n: int = 100) -> list[str]:
    """Create *n* entries (half source, half note); return all IDs."""
    ids: list[str] = []
    for i in range(n // 2):
        src = ek.ingest("source", _bench_source(i))
        ids.append(src.id)
        note = ek.ingest("note", _bench_note(src.id, i))
        ids.append(note.id)
    return ids


def _measure(action, n: int = 25, warmup: int = 2) -> tuple[float, float]:
    """Run *warmup* + *n* iterations; return (p95_ms, max_ms)."""
    for _ in range(warmup):
        action()
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        action()
        samples.append((time.perf_counter() - t0) * 1000)
    p95 = statistics.quantiles(sorted(samples), n=20)[18]
    return p95, max(samples)


# ---------------------------------------------------------------------------
# 4 latency tests
# ---------------------------------------------------------------------------


def test_l3_ingest_latency(fresh_ek: EgoKnowledge) -> None:
    """Ingest single source with 100 pre-existing entries: p95 < 200ms."""
    _seed(fresh_ek, 100)
    p95, mx = _measure(lambda: fresh_ek.ingest("source", _bench_source(_idx())))
    assert p95 < 200, f"ingest p95={p95:.1f}ms > 200ms"
    assert mx < 500, f"ingest max={mx:.1f}ms > 500ms"


def test_l3_update_latency(fresh_ek: EgoKnowledge) -> None:
    """Update note tags with 100 pre-existing entries: p95 < 200ms."""
    ids = _seed(fresh_ek, 100)
    target = ids[1]  # first note
    c = count()
    p95, mx = _measure(
        lambda: fresh_ek.update(target, {"tags": [f"tag-{next(c)}"]}),
        n=25,
    )
    assert p95 < 200, f"update p95={p95:.1f}ms > 200ms"
    assert mx < 500, f"update max={mx:.1f}ms > 500ms"


def test_l3_link_latency(fresh_ek: EgoKnowledge) -> None:
    """Link new source to anchor with 100 pre-existing entries: p95 < 200ms."""
    ids = _seed(fresh_ek, 100)
    anchor = ids[0]  # first source

    def _do_link():
        src = fresh_ek.ingest("source", _bench_source(_idx()))
        fresh_ek.link(anchor, src.id, "related")

    p95, mx = _measure(_do_link, n=25)
    assert p95 < 200, f"link p95={p95:.1f}ms > 200ms"
    assert mx < 500, f"link max={mx:.1f}ms > 500ms"


def test_l3_promote_latency(fresh_ek: EgoKnowledge) -> None:
    """Promote note → concept with 100 pre-existing entries: p95 < 200ms."""
    _seed(fresh_ek, 100)

    def _do_promote():
        src = fresh_ek.ingest("source", _bench_source(_idx()))
        note = fresh_ek.ingest("note", _bench_note(src.id, _idx()))
        fresh_ek.promote(note.id, "concept")

    p95, mx = _measure(_do_promote, n=25)
    assert p95 < 200, f"promote p95={p95:.1f}ms > 200ms"
    assert mx < 500, f"promote max={mx:.1f}ms > 500ms"
