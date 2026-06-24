"""normalize_legacy 测试共享 fixture 与 helper。"""

from __future__ import annotations

import json
from pathlib import Path


def _write_entry(
    path: Path,
    *,
    fm_text: str,
    body: str = "正文，含合法中文标点：保留。",
) -> Path:
    """构造一个 frontmatter + body 的 .md 条目，返回路径。

    frontmatter 与 body 都按生产格式 ``---\\n...\\n---\\n...`` 拼接，
    让被测代码走与真实条目一致的解析路径。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fm_text}\n---\n{body}", encoding="utf-8")
    return path


def _dirty_fm(*, title: str = "测试：标题　含全角") -> str:
    """含全角冒号/逗号/全角空格的 frontmatter 段（待修复）。"""

    return (
        "id: ek_note_dirty\n"
        f"title: {title}\n"
        "kind：note\n"  # 全角冒号 — YAML 解析会出问题，正是修复目标
        "tags：\n- 测试\n"
    )


def _clean_fm(*, title: str = "测试: 标题 含半角") -> str:
    """已修复（半角结构标点）的 frontmatter 段。"""

    return f"id: ek_note_clean\ntitle: {title}\nkind: note\ntags:\n- 测试\n"


def _frontmatter_of(md_text: str) -> str:
    """从 .md 全文提取 frontmatter 段（不含 ``---`` 边界）。"""

    parts = md_text.split("---\n", 2)
    assert len(parts) == 3, f"bad md: {md_text!r}"
    return parts[1]


def _write_fake_manifest(
    backup_dir: Path,
    *,
    entries: list[object],
    entry_count: int | None = None,
    record_type: str = "normalize_legacy.backup.manifest/v1",
    scan_sources: object = "missing",
) -> Path:
    """构造一份畸形/合法的 manifest 用于 read_manifest / restore 拒绝路径测试。

    - ``entries`` 直接写入 manifest.entries（不做契约校验，用于注入畸形条目）；
    - ``entry_count`` 默认等于 ``len(entries)``；测试可显式覆盖制造不一致；
    - ``record_type`` 默认合法；测试可注入非法值；
    - ``scan_sources`` 默认 ``"missing"``（不写该字段，模拟 Phase 2.2 旧 manifest）；
      传 True/False 写入字段，传其他类型用于注入畸形。
    """

    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = backup_dir / "normalize-legacy-manifest.json"
    payload: dict[str, object] = {
        "record_type": record_type,
        "entry_count": len(entries) if entry_count is None else entry_count,
        "entries": entries,
    }
    if scan_sources != "missing":
        payload["scan_sources"] = scan_sources
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path
