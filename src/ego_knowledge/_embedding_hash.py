"""Generate embedding_content_hash for dense index stale detection.

The hash covers title + slug + selected frontmatter fields + body (first 2000
chars) so that any content change triggers a re-embed.  The hash is stored in
``dense_embeddings.embedding_content_hash`` and compared on each rebuild cycle.

Design notes (Phase 7.2a / R1 A-content-hash):
  - ``entries`` table has no ``content_hash`` column (that lives in
    ``source_fields``), so the dense layer needs its own hash mechanism that
    works for *all* kinds, not just sources.
  - For ``source`` kind, ``source_url`` and ``content_hash`` from the entry
    model are included so that watched-source content drift is detected.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Entry


def compute_embedding_content_hash(entry: Entry) -> str:
    """Compute a stable SHA-256 hex digest (first 16 chars) for *entry*.

    The hash input is built from a deterministic sequence of parts so that
    identical content always produces the same hash.
    """
    from .models import SourceEntry

    parts: list[str] = [
        entry.title,
        entry.slug,
        _normalize_list(entry.tags),
        _normalize_list(entry.aliases),
        _normalize_list(entry.search_terms),
        (entry.body or "")[:2000],
    ]

    # Source kind: include source_url + content_hash to track watch drift
    if isinstance(entry, SourceEntry):
        parts.append(entry.source_url)
        parts.append(entry.content_hash)

    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_list(value: list[str]) -> str:
    """Sort and join a list for deterministic ordering."""
    return ",".join(str(v) for v in sorted(value))
