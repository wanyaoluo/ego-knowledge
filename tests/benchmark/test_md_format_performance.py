"""``format_body`` 性能基准:40KB body 格式化耗时。

保护契约(spec 待决点 / Phase 2 任务 2.2):
- 单 body(≤ ``MAX_UPDATE_BODY_BYTES`` = 40960B)格式化耗时 < 100ms(软目标)
- 硬阈 1000ms 仅防死循环/异常回归,不是性能 SLA

与单元测试的边界:
- ``tests/unit/test_md_format.py`` 锁行为契约(输入 → 输出 + 可观测信号)
- 本文件锁性能契约(耗时随 body 规模线性,40KB 内毫秒级)

不引 pytest-benchmark;用 ``time.perf_counter`` + 多次取样,与 ``test_l3_latency.py`` 一致。
默认被 ``-m 'not slow'`` 排除;需显式 ``-m slow`` 或 ``-m ''`` 触发。
"""

from __future__ import annotations

import time

import pytest

from ego_knowledge._md_format import format_body
from ego_knowledge._validation import MAX_UPDATE_BODY_BYTES

pytestmark = pytest.mark.slow

# 取接近 MAX_UPDATE_BODY_BYTES(40960B)的中文 body:
# - "正文内容测试填充。\n" 每行 UTF-8 = 9 中文字符(3B/字) + 中文句号(3B) + LF(1B) = 28B
# - 1462 行 × 28B = 40936B,贴近上限且不越界
# 数字与字节计算同步到本注释,避免后续误改后失同步。
_ROWS = 1462
_LINE_TEMPLATE = "正文内容测试填充。\n"


def _build_body() -> str:
    body = "# 标题\n\n" + _LINE_TEMPLATE * _ROWS
    byte_len = len(body.encode("utf-8"))
    assert byte_len <= MAX_UPDATE_BODY_BYTES, (
        f"基准 body 字节 {byte_len} 超过 MAX_UPDATE_BODY_BYTES {MAX_UPDATE_BODY_BYTES};"
        " 调整 _ROWS 保持贴近上限且不越界"
    )
    return body


def test_format_performance_records_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    """40KB body 单次格式化:记录基线,<1000ms 硬阈防回归。

    通过条件(spec 口径):
    - <100ms:PASS,记入交付报告
    - ≥100ms 且 <1000ms:仍 PASS(本测试不硬阻断),但断言会触发交付报告降级决策记录
    - ≥1000ms:FAIL,视为性能回归或死循环
    """
    body = _build_body()

    # warmup:避免首次 import/JIT 抖动污染首测;mdformat 是纯 Python 但仍有 import 缓存效应
    format_body(body)

    # 取 3 次最小,降抖动;不取均值(性能基线看下界更稳)
    samples: list[float] = []
    for _ in range(3):
        start = time.perf_counter()
        format_body(body)
        samples.append((time.perf_counter() - start) * 1000)
    elapsed_ms = min(samples)

    # 硬阈:1000ms,只拦异常回归或死循环
    assert elapsed_ms < 1000, (
        f"format_body 40KB 耗时 {elapsed_ms:.1f}ms 超过硬阈 1000ms,疑似性能回归"
    )

    # 软目标:100ms。≥100ms 不阻断,但用 print 把降级决策证据暴露给 -s 输出与交付报告。
    verdict = "PASS(<100ms 软目标)" if elapsed_ms < 100 else "DEGRADE(≥100ms 触发降级决策)"
    msg = (
        f"\n[format_body 40KB baseline] "
        f"min={elapsed_ms:.1f}ms samples={[f'{s:.1f}' for s in samples]}ms "
        f"body_bytes={len(body.encode('utf-8'))} verdict={verdict}\n"
    )
    print(msg, flush=True)

    # 软目标失败时,显式把降级标记写到 stdout,供交付报告抄录;不靠隐式通过掩盖
    if elapsed_ms >= 100:
        print(
            "[DEGRADE_NOTICE] 性能 ≥100ms,需在交付报告中记录降级决策"
            "(开关默认关或保留开启由用户裁决)。",
            flush=True,
        )
