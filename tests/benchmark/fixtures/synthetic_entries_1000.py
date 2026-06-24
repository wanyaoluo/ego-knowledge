"""Synthetic entry generator for Phase 8.4 stress and red-team tests.

R1 M6: deterministic seed ensures reproducible runs.
Distribution:
- 85% unique independent entries
- 10% semantically near-duplicate (same meaning, different wording)
- 5% literal duplicate candidates (same alias, different meaning)
"""

from __future__ import annotations

import hashlib
import random
from itertools import count

_SEQ = count(0)

# Fixed seed for reproducibility (R1 M6)
_SEED = 42


def _reset_seed() -> None:
    """Reset global RNG to deterministic state."""
    random.seed(_SEED)


def _idx() -> int:
    return next(_SEQ)


def _hash(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()[:8]


def semantic_group_anchor(group_index: int) -> str:
    return f"语义组-{_hash(group_index)}"


# ---------------------------------------------------------------------------
# 语义近似组：同义改写但字面完全不同（测 semantic_duplicate_candidate 召回）
# ---------------------------------------------------------------------------

_SEMANTIC_GROUPS: list[list[str]] = [
    ["机器学习基础入门", "ML入门指南", "人工智能学习起步", "machine learning fundamentals"],
    ["深度学习框架对比", "DL框架评测", "神经网络工具比较", "deep learning frameworks"],
    ["自然语言处理综述", "NLP技术总览", "文本理解技术汇总", "NLP overview survey"],
    ["Python并发编程", "Python多线程与异步", "Python parallel programming"],
    ["微服务架构设计", "service mesh实践", "分布式系统拆分策略"],
    ["数据仓库建模", "DW dimensional modeling", "数据仓库维度设计"],
    ["容器编排最佳实践", "K8s生产部署指南", "container orchestration guide"],
    ["知识图谱构建方法", "KG construction techniques", "图谱建模与推理"],
    ["推荐系统算法原理", "recommendation engine design", "个性化推荐技术"],
    ["时序数据库选型", "time series DB comparison", "时间序列存储方案"],
    ["Git分支管理策略", "版本控制工作流对比", "Git workflow comparison"],
    ["CI/CD流水线设计", "持续集成与部署实践", "devops pipeline design"],
    ["图数据库应用场景", "graph DB use cases", "图存储与查询优化"],
    ["消息队列对比分析", "MQ performance comparison", "异步通信中间件选型"],
    ["API网关设计模式", "gateway pattern design", "统一入口架构方案"],
]


def _build_search_terms(title: str, idx: int) -> list[str]:
    """Generate ≥5 search_terms: 中文≥1, 英文≥1, alias≥1."""
    h = _hash(idx)
    return [
        title,
        "压力测试中文主术语",
        f"syn-{h}",
        "benchmark",
        f"synthetic-stress-test-{idx:04d}",
        f"alias-bench-{h}",
    ]


def stress_source_payload(title: str, idx: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "source_type": "web",
        "source_url": f"https://stress.example.com/{idx:04d}",
        "content_hash": f"stress-{idx:04d}-{_hash(idx)}",
        "search_terms": _build_search_terms(title, idx),
        "tags": ["压力测试"],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 主生成器：产出 1000+ 合成 payload
# ---------------------------------------------------------------------------


def generate_synthetic_batch(total: int = 1000) -> list[dict[str, object]]:
    """Generate *total* ingest-ready payloads (source + note pairs).

    Distribution:
    - 85% unique entries
    - 10% semantic near-duplicates (grouped)
    - 5% literal overlap candidates (shared aliases)

    Returns list of dicts with keys: kind, payload, group_tag
    """
    _reset_seed()
    batch: list[dict[str, object]] = []

    n_semantic = int(total * 0.10)
    n_literal = int(total * 0.05)
    n_unique = total - n_semantic - n_literal

    # --- 85% unique ---
    for i in range(n_unique):
        idx = _idx()
        title = f"独立条目{_hash(idx)}压力测试"
        batch.append(
            {
                "kind": "source",
                "payload": stress_source_payload(title, idx),
                "group_tag": "unique",
            }
        )

    # --- 10% semantic near-duplicates ---
    semantic_created = 0
    group_index = 0
    while semantic_created < n_semantic:
        group = _SEMANTIC_GROUPS[group_index % len(_SEMANTIC_GROUPS)]
        title = f"{group[semantic_created % len(group)]} 样本{semantic_created:03d}"
        idx = _idx()
        payload = stress_source_payload(title, idx)
        terms = payload.get("search_terms", [])
        assert isinstance(terms, list)
        terms.append(semantic_group_anchor(group_index % len(_SEMANTIC_GROUPS)))
        batch.append(
            {
                "kind": "source",
                "payload": payload,
                "group_tag": "semantic_dup",
            }
        )
        semantic_created += 1
        group_index += 1

    # --- 5% literal overlap (shared alias to trigger duplicate_candidate) ---
    shared_alias = "共享别名压力测试"
    for i in range(n_literal):
        idx = _idx()
        title = f"字面重叠条目{_hash(idx)}"
        payload = stress_source_payload(title, idx)
        terms = payload.get("search_terms", [])
        assert isinstance(terms, list)
        terms.append(shared_alias)
        batch.append(
            {
                "kind": "source",
                "payload": payload,
                "group_tag": "literal_overlap",
            }
        )

    # Shuffle for realistic ingestion order
    random.shuffle(batch)
    return batch
