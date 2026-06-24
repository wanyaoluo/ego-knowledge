#!/usr/bin/env python3
"""Threshold dry-run audit script.

Iterates over all active entries (excluding archived), computes body / search_terms
length distributions grouped by kind, and writes a markdown report.

Usage:
    uv run python scripts/threshold_dryrun.py \
        --data-root <your-data-root> \
        --output threshold-dryrun-report.md

Exit codes:
    0  success
    1  missing arguments / data-root not found
    2  database error
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BODY_GUARDED_KINDS = ["note", "dossier", "concept", "decision", "view"]

# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Threshold dry-run audit for ego-knowledge")
    p.add_argument(
        "--data-root",
        required=True,
        type=Path,
        help="Root directory of EgoKnowledge data (contains registry/)",
    )
    p.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output markdown file path",
    )
    return p


# ── Database access ────────────────────────────────────────────────────────────


def open_registry(data_root: Path) -> sqlite3.Connection:
    db_path = data_root / "registry" / "catalog.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"catalog.sqlite not found at {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def fetch_active_entries(conn: sqlite3.Connection) -> list[dict]:
    """Return all non-archived entries as dicts with kind, body, search_terms."""
    rows = conn.execute(
        """
        SELECT id, kind, title, body, status
          FROM entries
         WHERE status != 'archived'
         ORDER BY kind, id
        """
    ).fetchall()

    # Also fetch search_terms for each entry
    entries = []
    for row in rows:
        entry_id = row["id"]
        terms_rows = conn.execute(
            "SELECT term FROM entry_search_terms WHERE entry_id = ?",
            (entry_id,),
        ).fetchall()
        search_terms = [r["term"] for r in terms_rows]
        entries.append(
            {
                "id": entry_id,
                "kind": row["kind"],
                "title": row["title"],
                "body": row["body"] or "",
                "status": row["status"],
                "search_terms": search_terms,
            }
        )
    return entries


# ── Statistics helpers ──────────────────────────────────────────────────────────


def pct(data: list[float], p: float) -> float:
    """Percentile using linear interpolation (same as numpy)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = lo + 1
    if hi >= n:
        return sorted_data[-1]
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


def build_distribution(lengths: list[int]) -> dict:
    if not lengths:
        return {"count": 0, "min": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0}
    fl = [float(x) for x in lengths]
    return {
        "count": len(lengths),
        "min": min(lengths),
        "p50": pct(fl, 50),
        "p90": pct(fl, 90),
        "p99": pct(fl, 99),
        "max": max(lengths),
    }


# ── Main logic ─────────────────────────────────────────────────────────────────


def run(data_root: Path, output: Path) -> None:
    conn = open_registry(data_root)
    try:
        entries = fetch_active_entries(conn)
    finally:
        conn.close()

    if not entries:
        print("ERROR: no active entries found", file=sys.stderr)
        sys.exit(1)

    # ── Per-kind grouping ──────────────────────────────────────────────────────
    kind_bodies: dict[str, list[int]] = {k: [] for k in BODY_GUARDED_KINDS}
    kind_rejected_body: dict[str, list[dict]] = {k: [] for k in BODY_GUARDED_KINDS}  # body < 50
    all_terms_lengths: list[int] = []
    all_terms_rejected: list[dict] = []  # term > 40

    for entry in entries:
        kind = entry["kind"]
        if kind not in BODY_GUARDED_KINDS:
            continue  # skip 'source' — exempt from body-length guard

        # body length = strip() then char count
        body_len = len(entry["body"].strip())

        # per-kind body lengths
        kind_bodies[kind].append(body_len)

        # boundary: body < 50 chars
        if body_len < 50:
            kind_rejected_body[kind].append(
                {
                    "id": entry["id"],
                    "title": entry["title"],
                    "body_preview": entry["body"].strip()[:100],
                    "body_len": body_len,
                }
            )

        # per-term lengths
        for term in entry["search_terms"]:
            term_len = len(term)
            all_terms_lengths.append(term_len)
            if term_len > 40:
                all_terms_rejected.append(
                    {
                        "entry_id": entry["id"],
                        "entry_title": entry["title"],
                        "term": term,
                        "term_len": term_len,
                    }
                )

    # ── Build distributions ─────────────────────────────────────────────────────
    lines: list[str] = []
    lines.append("# Threshold Dry-run Report")
    lines.append("")
    lines.append("> Generated by `threshold_dryrun.py` · Phase 0前置审计")
    lines.append(f"> Data root: `{data_root}` · Total active entries: **{len(entries)}**")
    lines.append("")

    # ── 1. Body length distribution per kind ───────────────────────────────────
    lines.append("## 1. Body Length Distribution (by Kind)")
    lines.append("")
    lines.append("Thresholds: body ≥ 50 chars after `strip()`.")
    lines.append("")

    header = "| kind | count | min | p50 | p90 | p99 | max | rejected (<50) | rejection rate |"
    sep = "|------|-------|-----|-----|-----|-----|-----|----------------|---------------|"
    lines.append(header)
    lines.append(sep)

    for kind in BODY_GUARDED_KINDS:
        dist = build_distribution(kind_bodies[kind])
        rejected = kind_rejected_body[kind]
        total_kind = dist["count"]
        rate = (len(rejected) / total_kind * 100) if total_kind > 0 else 0.0
        lines.append(
            f"| {kind} | {dist['count']} | "
            f"{dist['min']} | {dist['p50']:.0f} | {dist['p90']:.0f} | {dist['p99']:.0f} | "
            f"{dist['max']} | {len(rejected)} | {rate:.2f}% |"
        )

    lines.append("")

    # ── 2. search_terms individual length distribution ──────────────────────────
    lines.append("## 2. search_terms 单项长度分布（整体）")
    lines.append("")
    lines.append("阈值：单项 > 40 字符 → 拒绝")
    lines.append("")

    term_dist = build_distribution(all_terms_lengths)
    total_terms = len(all_terms_lengths)
    terms_rejected = len(all_terms_rejected)
    term_rate = (terms_rejected / total_terms * 100) if total_terms > 0 else 0.0

    lines.append(
        f"**Total terms: {total_terms}** · Rejected (>40): **{terms_rejected}** ({term_rate:.2f}%)"
    )
    lines.append("")
    lines.append("| min | p50 | p90 | p99 | max |")
    lines.append("|-----|-----|-----|-----|-----|")
    lines.append(
        f"| {term_dist['min']} | {term_dist['p50']:.0f} | "
        f"{term_dist['p90']:.0f} | {term_dist['p99']:.0f} | {term_dist['max']} |"
    )
    lines.append("")

    # ── 3. Boundary samples: body < 50 ────────────────────────────────────────
    lines.append("## 3. 边界值命中样本：body < 50 字符的 active 条目")
    lines.append("")
    total_short_body = sum(len(v) for v in kind_rejected_body.values())
    lines.append(f"共 **{total_short_body}** 条（所有 kind 合计）")
    lines.append("")

    if total_short_body == 0:
        lines.append("*无条目命中此边界。*")
    else:
        for kind in BODY_GUARDED_KINDS:
            hits = kind_rejected_body[kind]
            if not hits:
                continue
            lines.append(f"### {kind} ({len(hits)} 条)")
            lines.append("")
            lines.append("| id | title | body_len | body 前 100 字符 |")
            lines.append("|----|-------|----------|------------------|")
            for h in hits:
                preview = h["body_preview"].replace("|", "\\|").replace("\n", " ")
                lines.append(f"| `{h['id']}` | {h['title']} | {h['body_len']} | {preview} |")
            lines.append("")

    # ── 4. Boundary samples: term > 40 ────────────────────────────────────────
    lines.append("## 4. 边界值命中样本：search_terms 单项 > 40 字符")
    lines.append("")
    lines.append(f"共 **{terms_rejected}** 项")
    lines.append("")

    if not all_terms_rejected:
        lines.append("*无 term 命中此边界。*")
    else:
        lines.append("| entry_id | title | term | term_len |")
        lines.append("|----------|-------|------|----------|")
        for h in all_terms_rejected[:100]:  # cap at 100 for readability
            term_display = h["term"].replace("|", "\\|").replace("\n", " ")[:60]
            lines.append(
                f"| `{h['entry_id']}` | {h['entry_title']} | `{term_display}` | {h['term_len']} |"
            )
        if len(all_terms_rejected) > 100:
            lines.append("")
            lines.append(f"_...还有 {len(all_terms_rejected) - 100} 条未展示。_")
    lines.append("")

    # ── 5. Per-kind rejection rate summary + verdict ───────────────────────────
    lines.append("## 5. 拒绝率预估与判定")
    lines.append("")
    lines.append("| kind | total | rejected | rejection rate | verdict |")
    lines.append("|------|-------|----------|-----------------|---------|")

    stop_point = False
    for kind in BODY_GUARDED_KINDS:
        total_kind = len(kind_bodies[kind])
        rejected = len(kind_rejected_body[kind])
        rate = (rejected / total_kind * 100) if total_kind > 0 else 0.0
        if rate == 0:
            verdict = "✅ 直接放行"
        elif rate <= 2.0:
            verdict = "⚠️ ≤2% 列豁免"
        else:
            verdict = "🚨 >2% 停点决策"
            stop_point = True
        lines.append(f"| {kind} | {total_kind} | {rejected} | {rate:.2f}% | {verdict} |")

    lines.append("")
    if stop_point:
        lines.append("**判定：存在 kind 拒绝率 > 2%，需按 spec §7.5 停点决策。**")
    else:
        lines.append("**判定：所有 kind 拒绝率 ≤ 2%，可按 spec §7.0 继续。**")

    lines.append("")
    lines.append("---")
    lines.append("*Report generated by threshold_dryrun.py*")

    # ── Write output ───────────────────────────────────────────────────────────
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {output}")
    print(f"Total active entries: {len(entries)}")
    print(f"Total search_terms:   {total_terms}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    if not args.data_root.exists():
        print(f"ERROR: data-root not found: {args.data_root}", file=sys.stderr)
        sys.exit(1)

    try:
        run(args.data_root, args.output)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(2)
