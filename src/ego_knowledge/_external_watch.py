"""GitHub release watch: poll external_watch entries and process new releases.

L1 automation layer — watches GitHub repos for new releases via the REST API,
ingests them as source entries, and manages superseded_by chains.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeGuard, cast

import ulid

from .errors import ConflictError, StorageError, ValidationError
from .models import Entry, SourceEntry
from .registry import Registry

if TYPE_CHECKING:
    from .core import EgoKnowledge

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_WATCH_ID_PREFIX = "ew_"

_LOG_DIR_NAME = "watch"
_ERRORS_LOG_NAME = "errors.jsonl"


class _HeaderGetter(Protocol):
    def get(self, key: str, default: str = ...) -> object:
        """Return a header value by key."""


def _has_header_getter(headers: object) -> TypeGuard[_HeaderGetter]:
    return callable(getattr(headers, "get", None))


@dataclass(slots=True)
class PollResult:
    """Summary of a poll_all run."""

    processed: int = 0
    new: int = 0
    errors: list[dict[str, object]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


def add_watch(registry: Registry, target: str) -> str:
    """Register owner/repo to external_watch; returns the watch id."""
    if "/" not in target or len(target.split("/")) != 2:
        raise ValidationError("格式应为 owner/repo")

    owner, repo = target.split("/", 1)
    normalized = f"{owner}/{repo}"

    # Check for duplicates
    row = registry.conn.execute(
        "SELECT id FROM external_watch WHERE target = ?",
        (normalized,),
    ).fetchone()
    if row is not None:
        raise ConflictError(f"watch 已存在: {normalized}")

    watch_id = f"{_WATCH_ID_PREFIX}{ulid.new().str}"
    now = _utc_now_text()
    try:
        registry.conn.execute(
            """
            INSERT INTO external_watch(id, source_type, target, cursor,
                                       last_checked_at, linked_dossiers_json,
                                       created_at, updated_at)
            VALUES(?, 'github_release', ?, NULL, NULL, '[]', ?, ?)
            """,
            (watch_id, normalized, now, now),
        )
        registry.commit()
    except Exception as exc:
        raise StorageError(f"写入 external_watch 失败: {exc}") from exc

    return watch_id


def list_watches(registry: Registry) -> list[dict[str, object]]:
    """Read all rows from external_watch."""
    rows = registry.conn.execute(
        """
        SELECT id, source_type, target, cursor,
               last_checked_at, linked_dossiers_json,
               consecutive_404_count, last_404_at,
               created_at, updated_at
          FROM external_watch
         ORDER BY created_at
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_watch_by_target(registry: Registry, target: str) -> dict[str, object] | None:
    """Find a watch entry by target (owner/repo)."""
    row = registry.conn.execute(
        """
        SELECT id, source_type, target, cursor,
               last_checked_at, linked_dossiers_json,
               created_at, updated_at
          FROM external_watch
         WHERE target = ?
        """,
        (target,),
    ).fetchone()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Poll orchestration
# ---------------------------------------------------------------------------


def poll_all(
    registry: Registry,
    *,
    data_root: Path,
    token: str | None = None,
) -> PollResult:
    """Process all watch entries; returns PollResult summary."""
    watches = list_watches(registry)
    if not watches:
        return PollResult()

    result = PollResult(processed=len(watches))

    for record in watches:
        target = cast(str, record["target"])
        watch_id = cast(str, record["id"])
        try:
            # Fix-6: skip repos with excessive consecutive 404s
            consecutive_404 = int(cast(int | None, record.get("consecutive_404_count")) or 0)
            if consecutive_404 >= _404_COOLDOWN_THRESHOLD:
                _update_last_checked(registry, watch_id)
                logger.info("跳过 %s: 连续 %d 次 404, 已降频", target, consecutive_404)
                continue

            latest = _fetch_latest_release(target, token=token)
            if latest is None:
                # 404: increment counter (also updates last_checked_at)
                _increment_404_counter(registry, watch_id)
                continue

            # Success: reset 404 counter
            _reset_404_counter(registry, watch_id)

            if _is_new_release(record, latest):
                _on_new_release(registry, record, latest, data_root=data_root)
                result.new += 1
            else:
                _update_last_checked(registry, watch_id)
        except Exception as exc:
            error_entry: dict[str, object] = {
                "ts": _utc_now_text(),
                "target": target,
                "phase": "poll",
                "error": str(exc),
            }
            result.errors.append(error_entry)
            _append_error_log(data_root, error_entry)
            logger.warning("poll %s 失败: %s", target, exc)

    return result


# ---------------------------------------------------------------------------
# GitHub API interaction
# ---------------------------------------------------------------------------


def _fetch_latest_release(
    target: str,
    *,
    token: str | None = None,
) -> dict[str, object] | None:
    """Call GitHub API GET /repos/{owner}/{repo}/releases/latest.

    Returns None on 404 (no releases yet or repo not found).
    Raises on other HTTP errors after one retry.
    """
    url = f"{GITHUB_API}/repos/{target}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ego-knowledge-watch/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers, method="GET")

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return cast(dict[str, object], data)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # No releases or repo not found (decision B)
            if exc.code == 403:
                # Rate limit
                remaining = _resp_headers_get(exc.headers, "X-RateLimit-Remaining", "?")
                raise RuntimeError(f"GitHub API 限流: remaining={remaining}") from exc
            if attempt == 0 and exc.code >= 500:
                time.sleep(2**attempt)
                continue
            raise
        except urllib.error.URLError as exc:
            if attempt == 0:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"网络错误: {exc}") from exc

    return None


def _resp_headers_get(headers: object, key: str, default: str) -> str:
    """Safely get a header value."""
    if _has_header_getter(headers):
        return str(headers.get(key, default))
    return default


def _is_new_release(record: dict[str, object], latest: dict[str, object]) -> bool:
    """Compare cursor vs latest tag_name."""
    cursor = record.get("cursor")
    if cursor is None:
        return True
    tag_name = latest.get("tag_name")
    return tag_name is not None and tag_name != cursor


# ---------------------------------------------------------------------------
# New release processing (Task 5.4 main logic)
# ---------------------------------------------------------------------------


def _on_new_release(
    registry: Registry,
    record: dict[str, object],
    latest: dict[str, object],
    *,
    data_root: Path,
) -> None:
    """Process a new release: ingest source, supersede old, notify dossiers.

    Dossier notification failures prevent cursor advancement (W1b),
    ensuring the next poll will retry the notification.
    """
    from .core import EgoKnowledge

    ek = EgoKnowledge(data_root)
    try:
        target = cast(str, record["target"])
        tag = cast(str, latest.get("tag_name", "unknown"))

        # 1. Build source payload
        html_url = cast(str, latest.get("html_url", ""))
        body_text = cast(str, latest.get("body") or "")
        if len(body_text) > 5000:
            body_text = body_text[:5000] + "...(truncated)"

        content_hash = _hash_url_tag(html_url, tag)

        # 2. Check if source already exists (e.g. from a previous failed attempt)
        source_already_exists = _source_exists_by_hash(ek, content_hash)

        new_entry: Entry | None = None
        if not source_already_exists:
            source_payload: dict[str, object] = {
                "title": f"{target} {tag}",
                "source_url": html_url,
                "source_type": "github_release",
                "content_hash": content_hash,
                "captured_at": _extract_date(latest.get("published_at")),
                "tags": ["github_release"],
                "search_terms": _build_search_terms(target, tag, latest),
                "watch_target": target,
                "body": body_text,
            }

            try:
                new_entry = ek.ingest("source", source_payload, conflict_policy="strict")
            except ConflictError:
                source_already_exists = True

        # 3. Supersede old versions (nice-to-have, failures just warn)
        if new_entry is not None:
            old_sources = _find_old_versions(ek, target)
            for old in old_sources:
                if old.id == new_entry.id:
                    continue
                try:
                    ek.update(old.id, {"superseded_by": [new_entry.id]})
                except Exception:
                    logger.warning("superseded_by 写入失败: %s → %s", old.id, new_entry.id)

        # 4. Notify linked dossiers via Core write protocol (Fix-5/W2).
        #    Failure here propagates as exception → cursor won't advance (W1b).
        linked_ids_raw = record.get("linked_dossiers_json") or "[]"
        linked_ids: list[str] = (
            json.loads(linked_ids_raw) if isinstance(linked_ids_raw, str) else []
        )
        for dossier_id in linked_ids:
            ek.touch(dossier_id)

        # 5. Update cursor (only after all required actions succeed)
        _update_cursor(registry, cast(str, record["id"]), tag)
    finally:
        ek.close()


def _find_old_versions(ek: EgoKnowledge, target: str) -> list[SourceEntry]:
    """Find source entries with matching watch_target field (D1).

    Uses the frontmatter_json column to search for watch_target.
    """
    return ek.list_sources_by_target(target)


def _source_exists_by_hash(ek: EgoKnowledge, content_hash: str) -> bool:
    """Check if a source with the given content_hash already exists.

    Uses source_fields table for exact match (Fix-1: avoids LIKE false positives).
    """
    return ek.source_exists_by_hash(content_hash)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_url_tag(html_url: str, tag: str) -> str:
    """content_hash = sha256(html_url + tag_name) (decision B2).

    Assumption: GitHub release html_url and tag_name are immutable after publish.
    If a maintainer force-pushes the same tag name to a different commit,
    the hash stays the same and the new release content will be silently skipped.
    """
    return hashlib.sha256(f"{html_url}{tag}".encode()).hexdigest()[:16]


def _build_search_terms(
    target: str,
    tag: str,
    latest: dict[str, object],
) -> list[str]:
    """Auto-generate ≥5 search terms satisfying three-bucket validation (C1).

    Three buckets:
    - Chinese term (use a descriptive Chinese marker)
    - English term / abbreviation
    - Alias-like term (different from title, not contained in title)
    """
    terms: list[str] = []
    owner, repo = target.split("/", 1)

    # 1. Full owner/repo — English
    terms.append(target)
    # 2. Repo name only — English
    terms.append(repo)
    # 3. Owner — English
    terms.append(owner)
    # 4. Tag name — English/version
    if tag:
        terms.append(tag)
    # 5. Release name (if different from tag) — English
    name = latest.get("name")
    if isinstance(name, str) and name and name != tag:
        terms.append(name)
    # 6. Chinese marker for three-bucket coverage
    terms.append(f"{repo} 释出")
    # 7. github_release marker — alias-like
    terms.append("github_release")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    return unique


def _extract_date(published_at: object) -> str:
    """Extract ISO date from published_at string."""
    if isinstance(published_at, str) and published_at:
        return published_at[:10]
    return date.today().isoformat()


def _update_cursor(registry: Registry, watch_id: str, tag: str) -> None:
    """Update cursor and last_checked_at for a watch entry."""
    now = _utc_now_text()
    registry.conn.execute(
        """
        UPDATE external_watch
           SET cursor = ?, last_checked_at = ?, updated_at = ?
         WHERE id = ?
        """,
        (tag, now, now, watch_id),
    )
    registry.commit()


def _update_last_checked(registry: Registry, watch_id: str) -> None:
    """Update only last_checked_at (no new release)."""
    now = _utc_now_text()
    registry.conn.execute(
        """
        UPDATE external_watch
           SET last_checked_at = ?, updated_at = ?
         WHERE id = ?
        """,
        (now, now, watch_id),
    )
    registry.commit()


# ---------------------------------------------------------------------------
# 404 cooldown counter (Fix-6)
# ---------------------------------------------------------------------------

_404_COOLDOWN_THRESHOLD = 3


def _increment_404_counter(registry: Registry, watch_id: str) -> None:
    """Increment consecutive_404_count for a watch entry."""
    now = _utc_now_text()
    registry.conn.execute(
        """
        UPDATE external_watch
           SET consecutive_404_count = COALESCE(consecutive_404_count, 0) + 1,
               last_404_at = ?,
               last_checked_at = ?,
               updated_at = ?
         WHERE id = ?
        """,
        (now, now, now, watch_id),
    )
    registry.commit()


def _reset_404_counter(registry: Registry, watch_id: str) -> None:
    """Reset consecutive_404_count to 0 (on successful response)."""
    now = _utc_now_text()
    registry.conn.execute(
        """
        UPDATE external_watch
           SET consecutive_404_count = 0,
               updated_at = ?
         WHERE id = ?
        """,
        (now, watch_id),
    )
    registry.commit()


def _append_error_log(data_root: Path, entry: dict[str, object]) -> None:
    """Append an error entry to logs/watch/errors.jsonl."""
    log_dir = data_root / "logs" / _LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / _ERRORS_LOG_NAME
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _utc_now_text() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
