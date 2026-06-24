"""``_extract_body`` 归一化性能基准：40KB body 处理耗时。

保护契约（plan 任务 0.4 acceptance + spec 测试策略性能测试行）：
- ``_extract_body`` 40KB body 归一化耗时 < 100ms（硬目标，plan acceptance）
- ``_extract_body`` + ``format_body`` 全管道 40KB 耗时 < 100ms 软目标
  （spec「叠加归一化后仍接近 <100ms 软目标」），硬阈 1000ms 防回归

与 ``test_md_format_performance.py`` 的边界：
- ``test_md_format_performance.py`` 锁 ``format_body``（mdformat）单独性能
- 本文件锁 ``_extract_body``（归一化层）单独性能 + 全管道叠加性能
- 两者共同验证 spec 风险「性能超过写入预算」的缓解（线性映射，无 NFKC 开销）

不引 pytest-benchmark；用 ``time.perf_counter`` + 多次取样取最小值，与
``test_md_format_performance.py`` / ``test_l3_latency.py`` 一致。
默认被 ``-m 'not slow'`` 排除；需显式 ``-m slow`` 触发。
"""

from __future__ import annotations

import time

import pytest

from ego_knowledge._md_format import format_body
from ego_knowledge._validation import MAX_UPDATE_BODY_BYTES
from ego_knowledge.frontmatter import _extract_body

pytestmark = pytest.mark.slow

# 构造贴近 MAX_UPDATE_BODY_BYTES(40960B) 的混合 body：
# - _LINE = "正文内容测试填充词\u3000结尾。\n" = 9 中文字 + 1 全角空格 + 2 中文字
#   + 中文句号 + LF = 27+3+6+3+1 = 40 bytes/行（覆盖 normalize_body_spacing 路径）
# - 末尾追加 fenced code block（含 U+3000，覆盖 _CODE_BLOCK_RE 保护路径）
# - 数字与字节计算同步到本注释，避免后续误改后失同步。
_LINE = "正文内容测试填充词\u3000结尾。\n"
_LINE_BYTES = len(_LINE.encode("utf-8"))  # 40
_CODE_BLOCK = "```py\ncode\u3000with space\n```\n"
_CODE_BLOCK_BYTES = len(_CODE_BLOCK.encode("utf-8"))

# 预留 _CODE_BLOCK 字节空间，剩余用 _LINE 填充
_ROWS = (MAX_UPDATE_BODY_BYTES - _CODE_BLOCK_BYTES) // _LINE_BYTES


def _build_body() -> str:
    """构造贴近 40KB 上限的混合 body（段落含全角空格 + fenced code block）。"""

    body = _LINE * _ROWS + _CODE_BLOCK
    byte_len = len(body.encode("utf-8"))
    assert byte_len <= MAX_UPDATE_BODY_BYTES, (
        f"基准 body 字节 {byte_len} 超过 MAX_UPDATE_BODY_BYTES {MAX_UPDATE_BODY_BYTES};"
        " 调整 _ROWS 保持贴近上限且不越界"
    )
    return body


def _bench(fn, body: str, rounds: int = 5) -> float:
    """取样 rounds 次，返回最小耗时（ms）。含 warmup 防首次 import 抖动。"""

    fn(body)  # warmup
    samples: list[float] = []
    for _ in range(rounds):
        start = time.perf_counter()
        fn(body)
        samples.append((time.perf_counter() - start) * 1000)
    return min(samples)


def test_extract_body_40kb_under_100ms(capsys: pytest.CaptureFixture[str]) -> None:
    """``_extract_body`` 40KB body 归一化耗时 < 100ms（plan acceptance 硬目标）。

    通过条件（plan acceptance 口径）：
    - <100ms：PASS，记入交付报告
    - ≥1000ms：FAIL，视为性能回归（线性映射不应有此开销）

    归一化层是线性字符映射（``_BODY_SPACING_MAP.get``）+ 一次正则 split，
    无 NFKC 兼容分解开销，40KB 量级预期在 1ms 量级。
    """
    body = _build_body()
    elapsed_ms = _bench(lambda b: _extract_body({"body": b}), body)

    # 硬阈：1000ms，只拦异常回归或死循环
    assert elapsed_ms < 1000, (
        f"_extract_body 40KB 耗时 {elapsed_ms:.1f}ms 超过硬阈 1000ms，疑似性能回归"
    )

    # plan acceptance 硬目标：100ms
    assert elapsed_ms < 100, (
        f"_extract_body 40KB 耗时 {elapsed_ms:.1f}ms 超过 plan acceptance 100ms"
    )

    verdict = "PASS(<100ms plan acceptance)"
    msg = (
        f"\n[_extract_body 40KB baseline] "
        f"min={elapsed_ms:.3f}ms body_bytes={len(body.encode('utf-8'))} "
        f"verdict={verdict}\n"
    )
    print(msg, flush=True)


def test_extract_body_with_format_body_40kb_pipelined(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``_extract_body`` + ``format_body`` 全管道 40KB 耗时（spec 软目标）。

    通过条件（spec 测试策略性能测试行口径）：
    - <100ms：PASS，叠加归一化后仍接近软目标
    - ≥100ms 且 <1000ms：仍 PASS（软目标不阻断），但 print DEGRADE_NOTICE
    - ≥1000ms：FAIL，视为性能回归

    mdformat 是主要开销（spec 风险「性能超过写入预算」的瓶颈在 mdformat，
    不在归一化层）；本测试验证叠加归一化不显著推高全管道耗时。
    """
    body = _build_body()

    def _pipeline(b: str) -> None:
        normalized = _extract_body({"body": b})
        format_body(normalized)

    elapsed_ms = _bench(_pipeline, body)

    # 硬阈：1000ms
    assert elapsed_ms < 1000, (
        f"全管道 40KB 耗时 {elapsed_ms:.1f}ms 超过硬阈 1000ms，疑似性能回归"
    )

    # 软目标：100ms（spec「叠加归一化后仍接近 <100ms 软目标」）
    verdict = "PASS(<100ms 软目标)" if elapsed_ms < 100 else "DEGRADE(≥100ms 触发降级决策)"
    msg = (
        f"\n[extract_body+format_body 40KB pipeline] "
        f"min={elapsed_ms:.1f}ms body_bytes={len(body.encode('utf-8'))} "
        f"verdict={verdict}\n"
    )
    print(msg, flush=True)

    if elapsed_ms >= 100:
        print(
            "[DEGRADE_NOTICE] 全管道性能 ≥100ms，需在交付报告中记录降级决策"
            "（开关默认关或保留开启由用户裁决）。",
            flush=True,
        )
