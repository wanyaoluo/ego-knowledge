from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from ego_knowledge._dense_embedder import (
    MAX_BATCH,
    MAX_TOKENS_PER_ITEM,
    DenseEmbedder,
    cache_path_for,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _payload(count: int, *, dim: int = 3) -> dict[str, object]:
    return {
        "object": "list",
        "model": "BAAI/bge-m3",
        "data": [
            {
                "index": index,
                "object": "embedding",
                "embedding": [float(index), 0.1, 0.2][:dim],
            }
            for index in range(count)
        ],
        "usage": {"prompt_tokens": count, "total_tokens": count * 2},
    }


def _http_error(
    code: int,
    body: str = "error",
    headers: dict[str, str] | None = None,
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.siliconflow.cn/v1/embeddings",
        code,
        "boom",
        headers or {},
        io.BytesIO(body.encode("utf-8")),
    )


@pytest.fixture()
def embedder(tmp_path: Path) -> DenseEmbedder:
    return DenseEmbedder(
        "example-siliconflow-key",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        timeout=1.0,
    )


def _read_log(log_dir: Path) -> list[dict[str, object]]:
    log_file = log_dir / "dense-errors.jsonl"
    if not log_file.exists():
        return []
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]


def test_embed_cached_hit(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "entry-hash.json").write_text(
        json.dumps({"embedding": [1, 2, 3]}),
        encoding="utf-8",
    )
    embedder = DenseEmbedder(
        "example-siliconflow-key",
        cache_dir=cache_dir,
        log_dir=tmp_path / "logs",
    )

    assert embedder.embed_cached("entry", "hash", "不会调用 API") == [
        1.0,
        2.0,
        3.0,
    ]


def test_cache_path_sanitizes_entry_id(tmp_path: Path) -> None:
    assert cache_path_for("../奇怪/id", "hash:01", tmp_path).name == "id-hash_01.json"


def test_embed_cached_miss_calls_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fake_urlopen(req: object, timeout: float) -> _FakeResponse:
        calls.append((req, timeout))
        return _FakeResponse(_payload(1))

    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        fake_urlopen,
    )
    embedder = DenseEmbedder(
        "example-siliconflow-key",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
    )

    assert embedder.embed_cached("entry", "hash", "hello") == [0.0, 0.1, 0.2]
    assert len(calls) == 1
    assert (tmp_path / "cache" / "entry-hash.json").exists()


def test_embed_batch_happy_path(
    embedder: DenseEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        lambda req, timeout: _FakeResponse(_payload(2)),
    )

    result = embedder.embed_batch(["one", "two"])

    assert result.embeddings == [[0.0, 0.1, 0.2], [1.0, 0.1, 0.2]]
    assert result.tokens_used == 4


def test_embed_batch_too_large(embedder: DenseEmbedder) -> None:
    with pytest.raises(ValueError, match="batch 最多"):
        embedder.embed_batch(["x"] * (MAX_BATCH + 1))


def test_retry_on_429(
    embedder: DenseEmbedder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(req: object, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429)
        return _FakeResponse(_payload(1))

    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.time.sleep",
        sleeps.append,
    )

    assert embedder.embed_batch(["one"]).embeddings[0] == [0.0, 0.1, 0.2]
    assert sleeps == [1.0]
    assert _read_log(tmp_path / "logs")[0]["phase"] == "retryable"


def test_retry_on_429_with_retry_after_header(
    embedder: DenseEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(req: object, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _http_error(429, headers={"Retry-After": "3.5"})
        return _FakeResponse(_payload(1))

    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.time.sleep",
        sleeps.append,
    )

    embedder.embed_batch(["one"])

    assert sleeps == [3.5]


@pytest.mark.parametrize("code", [503, 504])
def test_retry_on_503_or_504(
    embedder: DenseEmbedder,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(req: object, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _http_error(code)
        return _FakeResponse(_payload(1))

    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        fake_urlopen,
    )
    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.time.sleep",
        sleeps.append,
    )

    embedder.embed_batch(["one"])

    assert sleeps == [1.0, 2.0]


def test_retry_exhaustion_logs_final_error(
    embedder: DenseEmbedder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(_http_error(429, "busy")),
    )
    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.time.sleep",
        sleeps.append,
    )

    with pytest.raises(urllib.error.HTTPError):
        embedder.embed_batch(["one"])

    records = _read_log(tmp_path / "logs")
    assert sleeps == [1.0, 2.0]
    assert records[-1]["phase"] == "http_error_final"
    assert records[-1]["message"] == "busy"


@pytest.mark.parametrize("code,phase", [(401, "auth_error"), (404, "endpoint_error")])
def test_no_retry_on_non_retryable_http(
    embedder: DenseEmbedder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    phase: str,
) -> None:
    calls = 0

    def fake_urlopen(req: object, timeout: float) -> _FakeResponse:
        nonlocal calls
        calls += 1
        raise _http_error(code, f"code={code}")

    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        fake_urlopen,
    )

    with pytest.raises(urllib.error.HTTPError):
        embedder.embed_batch(["one"])

    assert calls == 1
    assert _read_log(tmp_path / "logs")[0]["phase"] == phase


def test_corrupted_cache_refetches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "entry-hash.json").write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        lambda req, timeout: _FakeResponse(_payload(1)),
    )
    embedder = DenseEmbedder(
        "example-siliconflow-key",
        cache_dir=cache_dir,
        log_dir=tmp_path / "logs",
    )

    assert embedder.embed_cached("entry", "hash", "hello") == [0.0, 0.1, 0.2]
    assert _read_log(tmp_path / "logs")[0]["phase"] == "cache_corrupt"


def test_text_truncation_logged(
    embedder: DenseEmbedder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_body: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> _FakeResponse:
        captured_body.update(json.loads(req.data.decode("utf-8")))
        return _FakeResponse(_payload(1))

    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        fake_urlopen,
    )
    embedder.embed_batch([" ".join(f"token{i}" for i in range(MAX_TOKENS_PER_ITEM + 10))])

    request_input = captured_body["input"]
    assert isinstance(request_input, str)
    assert len(request_input.split()) == MAX_TOKENS_PER_ITEM
    assert _read_log(tmp_path / "logs")[0]["phase"] == "text_truncated"


def test_error_log_jsonl(
    embedder: DenseEmbedder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ego_knowledge._dense_embedder.urllib.request.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(_http_error(403, "quota")),
    )

    with pytest.raises(urllib.error.HTTPError):
        embedder.embed_batch(["one"])

    records = _read_log(tmp_path / "logs")
    assert records
    assert records == [
        {
            "ts": records[0]["ts"],
            "phase": "quota_exhausted",
            "code": 403,
            "message": "quota",
            "wait": 0.0,
        }
    ]


def test_api_key_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="api_key"):
        DenseEmbedder("", cache_dir=tmp_path / "cache", log_dir=tmp_path / "logs")
