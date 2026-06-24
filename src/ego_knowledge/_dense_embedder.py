"""SiliconFlow BGE-M3 dense embedding client with local cache."""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import jieba  # type: ignore[import-untyped]

API_URL = "https://api.siliconflow.cn/v1/embeddings"
MODEL = "BAAI/bge-m3"
EMBEDDING_DIM = 1024
MAX_BATCH = 16
MAX_TOKENS_PER_ITEM = 512
MAX_RETRIES = 3
RETRYABLE_CODES = {429, 503, 504}
NON_RETRYABLE_CODES = {400, 401, 403, 404}


@dataclass(frozen=True)
class EmbedResult:
    embeddings: list[list[float]]
    model_revision: str
    tokens_used: int


class DenseEmbedder:
    """Small stdlib HTTP wrapper for SiliconFlow embeddings.

    The cache key includes ``entry_id`` and ``embedding_content_hash`` so callers
    can treat cache hits as safe only for the exact indexed content revision.
    """

    def __init__(
        self,
        api_key: str,
        *,
        cache_dir: Path,
        log_dir: Path,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("SiliconFlow api_key 未配置")
        self._api_key = api_key
        self._cache_dir = cache_dir
        self._log_dir = log_dir
        self._timeout = timeout
        self.last_model_revision: str | None = None
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def embed_cached(
        self,
        entry_id: str,
        embedding_content_hash: str,
        text: str,
    ) -> list[float]:
        """Return one embedding from cache or API, then persist it atomically."""

        cache_file = cache_path_for(entry_id, embedding_content_hash, self._cache_dir)
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                embedding = data["embedding"]
                model_revision = data.get("model_revision")
                if isinstance(model_revision, str) and model_revision:
                    self.last_model_revision = model_revision
                if isinstance(embedding, list):
                    return [float(value) for value in embedding]
                raise TypeError("embedding is not a list")
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                self._log_error("cache_corrupt", 0, f"{cache_file.name}: {exc}", 0.0)
                cache_file.unlink(missing_ok=True)

        result = self._api_embed([text])
        self.last_model_revision = result.model_revision
        embedding = result.embeddings[0]
        payload = {
            "entry_id": entry_id,
            "embedding_content_hash": embedding_content_hash,
            "embedding": embedding,
            "model_revision": result.model_revision,
            "cached_at": _utc_now_text(),
        }
        self._write_cache(cache_file, payload)
        return embedding

    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        """Embed a bounded batch. Callers own chunking beyond ``MAX_BATCH``."""

        if len(texts) > MAX_BATCH:
            raise ValueError(f"batch 最多 {MAX_BATCH} 条,收到 {len(texts)}")
        result = self._api_embed(list(texts))
        self.last_model_revision = result.model_revision
        return result

    def _api_embed(self, texts: list[str]) -> EmbedResult:
        if not texts:
            raise ValueError("batch 不能为空")
        if len(texts) > MAX_BATCH:
            raise ValueError(f"batch 最多 {MAX_BATCH} 条,收到 {len(texts)}")

        prepared_texts = [
            self._truncate_text(text, index=index) for index, text in enumerate(texts)
        ]
        body = json.dumps(
            {
                "model": MODEL,
                "input": prepared_texts if len(prepared_texts) > 1 else prepared_texts[0],
                "encoding_format": "float",
            },
            ensure_ascii=False,
        ).encode("utf-8")

        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(
                    API_URL,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                return self._parse_response(payload, expected_count=len(prepared_texts))
            except urllib.error.HTTPError as exc:
                if exc.code in NON_RETRYABLE_CODES:
                    self._log_error(
                        _non_retryable_phase(exc.code),
                        exc.code,
                        _read_error_body(exc),
                        0.0,
                    )
                    raise
                if exc.code in RETRYABLE_CODES and attempt < MAX_RETRIES - 1:
                    wait = _retry_wait(exc, attempt)
                    self._log_error("retryable", exc.code, str(exc), wait)
                    time.sleep(wait)
                    continue
                self._log_error("http_error_final", exc.code, _read_error_body(exc), 0.0)
                raise
            except urllib.error.URLError as exc:
                if attempt < MAX_RETRIES - 1:
                    wait = float(2**attempt)
                    self._log_error("network", -1, str(exc), wait)
                    time.sleep(wait)
                    continue
                self._log_error("network_final", -1, str(exc), 0.0)
                raise
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                self._log_error("bad_response", 0, str(exc), 0.0)
                raise

        raise RuntimeError(f"embed 重试 {MAX_RETRIES} 次仍失败")

    def _parse_response(self, payload: Any, *, expected_count: int) -> EmbedResult:
        if not isinstance(payload, dict):
            raise TypeError("response is not an object")
        raw_items = payload["data"]
        if not isinstance(raw_items, list):
            raise TypeError("response.data is not a list")
        for item in raw_items:
            if not isinstance(item, dict):
                raise TypeError("response.data[] is not an object")
        rows = cast(list[dict[str, Any]], raw_items)
        items = sorted(rows, key=lambda item: int(item.get("index", 0)))
        embeddings: list[list[float]] = []
        for item in items:
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise TypeError("response.data[].embedding is invalid")
            embeddings.append([float(value) for value in embedding])
        if len(embeddings) != expected_count:
            raise ValueError(
                f"embedding 数量不匹配: expected {expected_count}, got {len(embeddings)}"
            )

        usage = payload.get("usage", {})
        tokens_used = _tokens_used(usage)
        return EmbedResult(
            embeddings=embeddings,
            model_revision=time.strftime("%Y-%m-%d"),
            tokens_used=tokens_used,
        )

    def _truncate_text(self, text: str, *, index: int) -> str:
        tokens = _estimate_tokens(text)
        if len(tokens) <= MAX_TOKENS_PER_ITEM:
            return text
        truncated = _join_estimated_tokens(tokens[:MAX_TOKENS_PER_ITEM])
        self._log_error(
            "text_truncated",
            0,
            f"item={index} tokens={len(tokens)}->{MAX_TOKENS_PER_ITEM}",
            0.0,
        )
        return truncated

    def _write_cache(self, cache_file: Path, payload: Mapping[str, object]) -> None:
        tmp_file = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.tmp")
        tmp_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_file.replace(cache_file)

    def _log_error(self, phase: str, code: int, message: str, wait: float) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _utc_now_text(),
            "phase": phase,
            "code": code,
            "message": message[:500],
            "wait": wait,
        }
        log_file = self._log_dir / "dense-errors.jsonl"
        with log_file.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _estimate_tokens(text: str) -> list[str]:
    if not text:
        return []
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        tokens = [token.strip() for token in jieba.lcut(text) if token.strip()]
        return tokens or [text]
    return text.split()


def cache_path_for(entry_id: str, embedding_content_hash: str, cache_dir: Path) -> Path:
    safe_entry_id = _safe_cache_part(entry_id, fallback="entry")
    safe_hash = _safe_cache_part(embedding_content_hash, fallback="hash")
    return cache_dir / f"{safe_entry_id}-{safe_hash}.json"


def _safe_cache_part(value: str, *, fallback: str) -> str:
    parts: list[str] = []
    for raw_part in re.split(r"[^A-Za-z0-9_.-]+", value):
        part = raw_part.strip("._")
        if part:
            parts.append(part)
    safe = "_".join(parts)
    return safe or fallback


def _join_estimated_tokens(tokens: list[str]) -> str:
    if not tokens:
        return ""
    if any("\u4e00" <= char <= "\u9fff" for token in tokens for char in token):
        return "".join(tokens)
    return " ".join(tokens)


def _retry_wait(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return float(2**attempt)


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return str(exc)
    if not body:
        return str(exc)
    return body.decode("utf-8", errors="replace")


def _non_retryable_phase(code: int) -> str:
    return {
        401: "auth_error",
        403: "quota_exhausted",
        404: "endpoint_error",
    }.get(code, "http_error")


def _tokens_used(usage: object) -> int:
    if not isinstance(usage, dict):
        return 0
    value = usage.get("total_tokens", 0)
    if isinstance(value, int | float | str) and not isinstance(value, bool):
        return int(value)
    return 0


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
