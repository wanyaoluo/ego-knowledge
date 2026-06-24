"""扫描 ``data_root/entries/**/*.md`` 并报告 frontmatter 全角结构标点修复清单。

dry-run 不修改任何文件；apply 在本模块外（``_apply``）单独执行，确保扫描
逻辑可被两端复用、且 dry-run 严格只读。

修复规则复用 Phase 0.2 的 ``ego_knowledge.frontmatter._fix_fullwidth_punctuation``
（spec 决策：frontmatter 结构标点全角→半角；body 中文标点保留不动）。

扫描边界（spec.md 真源边界 + Phase 2 遗留债务扩展）：

- 默认只扫 ``entries/``（spec.md:207 真源「唯一可修复对象 = entries/**/*.md」）。
- 显式 ``scan_sources=True`` 时追加扫描 ``sources/`` 下 ``docs/`` 与 ``imports/``
  子树（Phase 2 遗留债务：sources/ 9 个 frontmatter 全角结构标点）。
  ``sources/`` 是导入素材原始快照，扩展扫描默认关闭，调用方需知情同意。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

import yaml  # type: ignore[import-untyped]

from ego_knowledge.frontmatter import (
    FRONTMATTER_BOUNDARY,
    _fix_fullwidth_punctuation,
    split_frontmatter,
)


class NormalizeLegacyError(Exception):
    """normalize_legacy 脚本对外统一错误类型。

    将校验失败、备份/写回/恢复失败统一收口为这一类，便于 CLI 入口转
    stderr JSON，也便于测试与外部调用方按单一类型捕获。
    """


# 扫描子目录：spec.md 真源边界默认 ``entries/``，``--scan-sources`` 扩展到
# ``sources/`` 下 ``docs/`` 与 ``imports/``（Phase 2 遗留 sources 9 个债务）。
# sources/github 与 sources/web 是外部导入的不可逆快照，不纳入扫描。
_SCAN_ENTRIES_SUBDIR = "entries"
_SCAN_SOURCES_SUBDIR = "sources"
# sources/ 下只扫 docs/ 与 imports/（Phase 2 实测债务集中在两处）。
# github/ 与 web/ 是外部抓取的不可逆素材，结构标点修复风险高，不纳入。
# 子树根由本模块 ``_allowed_subtree_roots`` 统一定义，``_apply`` / ``_restore``
# 反向 import 复用（W1+W2 单源：扫描/写回/restore 共用同一允许根清单）。
_SCAN_SOURCES_ALLOWED = ("docs", "imports")

# frontmatter 字段名提取正则：匹配行首 ``key:`` 或 ``key：``（覆盖半角/全角冒号）。
_FRONTMATTER_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[：:]")

# 顶层 scalar 字段正则：行首无缩进的 ``key: value``，用于 YAML 兜底时给 value
# 加单引号。仅识别顶层（避免误改 list 内嵌 dict）。
_TOPLEVEL_SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*):(\s*)(\S.*)$")

# 顶层 ``key:value``（冒号后无空格）正则：用于 YAML 兜底第一层规范化。
# PyYAML 把 ``kind:note`` 当作非法（mapping 后必须有空白），规范化为
# ``kind: note`` 即可让 yaml.safe_load 成功。仅识别顶层避免动 list 项。
_TOPLEVEL_KEY_NOSPACE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*):(\S.*)$")


@dataclass(frozen=True)
class FileChange:
    """单个待修复文件的变更记录（dry-run/apply 报告对外结构）。"""

    path: str
    """相对 ``data_root`` 的 posix 路径，便于跨平台对比与日志输出。"""

    changed_fields: list[str]
    """frontmatter 中实际被改的字段名清单；无法归属字段时填 ``<line>``。"""

    diff_summary: str
    """人类可读的 diff 摘要：列出每个被替换的全角字符及其出现次数。"""


@dataclass(frozen=True)
class NormalizeReport:
    """dry-run / apply 共用的扫描报告结构。"""

    data_root: Path
    scanned: int
    """实际尝试解析 frontmatter 的 .md 文件数（不含无 frontmatter 的纯文本）。"""

    would_change: int
    changes: list[FileChange] = field(default_factory=list)


@dataclass(frozen=True)
class FileScan:
    """单文件扫描内部结果，供 apply/restore 跨模块复用。

    公开类型（无下划线前缀），让 apply 阶段以强类型签名消费，避免
    ``type: ignore[attr-defined]`` 绕过 mypy strict。
    """

    abs_path: Path
    relative_path: str
    original_text: str
    fixed_text: str
    changed_fields: list[str]
    diff_summary: str


def normalize_legacy_dry_run(
    data_root: Path, *, scan_sources: bool = False
) -> NormalizeReport:
    """扫描 ``data_root/entries/**/*.md``，报告 frontmatter 全角结构标点修复清单。

    - 只读，不修改任何文件。
    - 扫描范围默认硬约束为 ``entries/`` 子目录；``scan_sources=True`` 时追加
      扫描 ``sources/docs/`` 与 ``sources/imports/``（Phase 2 遗留债务）。
    - 幂等：对已修复数据再跑，``would_change == 0``。
    """

    _validate_data_root(data_root)
    scanned, changes = _scan_entries(data_root, scan_sources=scan_sources)
    return NormalizeReport(
        data_root=data_root,
        scanned=scanned,
        would_change=len(changes),
        changes=[
            FileChange(
                path=c.relative_path,
                changed_fields=c.changed_fields,
                diff_summary=c.diff_summary,
            )
            for c in changes
        ],
    )


def scan_for_changes(
    data_root: Path, *, scan_sources: bool = False
) -> tuple[int, list[FileScan]]:
    """供 apply 复用的扫描入口，返回原始文本与修复后文本便于备份/写回。"""

    _validate_data_root(data_root)
    return _scan_entries(data_root, scan_sources=scan_sources)


def _scan_entries(
    data_root: Path, *, scan_sources: bool = False
) -> tuple[int, list[FileScan]]:
    """遍历扫描子树下的 ``*.md``，返回 (扫描数, 待修复清单)。

    扫描边界：

    - 始终扫描 ``entries/**/*.md``。
    - ``scan_sources=True`` 时追加扫描 ``sources/{docs,imports}/**/*.md``。

    qa-strict R1（symlink/resolve allowed-root 校验）：扫描到的每个 ``md_path``
    必须不是 symlink 且 ``resolve(strict=False)`` 后仍位于对应允许子树根内，
    防止 ``entries/link.md`` 或 ``sources/docs/link.md`` 通过 symlink 把
    apply 写回导向 data_root 外部（与 restore 端 ``_assert_target_within_allowed_roots``
    构成对称护栏）。
    """

    data_root_resolved = data_root.resolve(strict=False)
    scanned = 0
    changes: list[FileScan] = []
    for allowed_root in _allowed_subtree_roots(
        data_root_resolved, scan_sources=scan_sources
    ):
        if not allowed_root.is_dir():
            continue
        for md_path in sorted(allowed_root.rglob("*.md")):
            if not md_path.is_file():
                continue
            _assert_path_within_allowed_roots(md_path, [allowed_root])
            scanned += _scan_one(md_path, data_root, changes)
    return scanned, changes


def _allowed_subtree_roots(
    data_root_resolved: Path, *, scan_sources: bool = False
) -> list[Path]:
    """返回扫描/写回/restore 共用的允许子树根（已 resolve）。

    单一真源（W1+W2 修复）：apply 写回阶段、restore 落点校验、dry-run 扫描
    全部通过本 helper 拿允许子树根，避免在多模块各自重复定义导致漂移。

    - 默认: ``[data_root/entries]``（spec.md:207 真源边界）。
    - ``scan_sources=True``: 追加 ``data_root/sources/{docs,imports}``。
    """

    roots = [data_root_resolved / _SCAN_ENTRIES_SUBDIR]
    if scan_sources:
        sources_root = data_root_resolved / _SCAN_SOURCES_SUBDIR
        for sub in _SCAN_SOURCES_ALLOWED:
            roots.append(sources_root / sub)
    return roots


def _assert_path_within_allowed_roots(
    path: Path, allowed_roots: list[Path]
) -> None:
    """护栏：``path`` 必须不是 symlink 且 resolve 后位于 ``allowed_roots`` 之一内。

    qa-strict R1：扫描与写回阶段共用此 helper，防符号链接绕过 + 越界写。

    双重校验：

    - 拒绝 ``path.is_symlink()``：``rglob`` / ``is_file`` / ``read_text`` /
      ``write_text`` 都跟随 symlink，单纯 resolve 校验不足以阻断通过 symlink
      读外部内容或写入外部文件，扫描阶段直接拒绝最干净。
    - ``path.resolve().relative_to(root)``：即使 path 自身不是 symlink，
      父目录是 symlink 时 resolve 后落点会跳出 allowed_root；此校验兜底。
    """

    if path.is_symlink():
        raise NormalizeLegacyError(
            f"扫描/写回目标 .md 是符号链接，拒绝处理 (防越界写): {path}"
        )
    resolved = path.resolve(strict=False)
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue
    raise NormalizeLegacyError(
        f"扫描/写回目标 resolve 后落在允许子树之外 (越界写风险): "
        f"{path} -> {resolved} (允许根: {[str(r) for r in allowed_roots]})"
    )


def _scan_one(md_path: Path, data_root: Path, changes: list[FileScan]) -> int:
    """扫描单文件，命中则 append 到 ``changes``；返回是否计入 scanned（0/1）。"""

    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NormalizeLegacyError(f"读取条目失败 {md_path}: {exc}") from exc
    if not text.startswith(FRONTMATTER_BOUNDARY):
        # 无 frontmatter 边界的 .md 不算知识条目，跳过不抛错
        return 0
    parsed = split_frontmatter(text)
    if parsed is None:
        # 已通过开始标记；此处仅结束标记缺失导致拆分失败，doctor 应已捕获，
        # 跳过避免误伤
        return 0
    fm_raw, body_part = parsed
    fixed_fm = _fix_fullwidth_punctuation(fm_raw)
    # spec.md:107-108 异常路径兜底：全角→半角修复后若 YAML 仍不可解析，
    # 对受影响顶层 scalar 加单引号转义；仍不可解析则抛错（停止写入）。
    fixed_fm = _ensure_yaml_parseable_after_fix(fm_raw, fixed_fm, md_path)
    if fixed_fm == fm_raw:
        return 1
    diff_summary = _summarize_diff(fm_raw, fixed_fm)
    changed_fields = _extract_changed_fields(fm_raw, fixed_fm)
    changes.append(
        FileScan(
            abs_path=md_path,
            relative_path=_relative_posix(data_root, md_path),
            original_text=text,
            fixed_text=f"{FRONTMATTER_BOUNDARY}{fixed_fm}{FRONTMATTER_BOUNDARY}{body_part}",
            changed_fields=changed_fields,
            diff_summary=diff_summary,
        )
    )
    return 1


def _extract_changed_fields(before: str, after: str) -> list[str]:
    """从 frontmatter 行级 diff 提取被改字段名。

    全角→半角只替换字符不增删行，按行 diff 即可定位被改行；再用
    ``_FRONTMATTER_KEY_RE`` 从行首解析字段名，解析失败填 ``<line>``。
    plan 验收口径要求报告含「变更字段」，便于 Phase 2.2 抽样复核。
    """

    before_lines = before.splitlines()
    after_lines = after.splitlines()
    fields: list[str] = []
    upper = min(len(before_lines), len(after_lines))
    for i in range(upper):
        if before_lines[i] != after_lines[i]:
            match = _FRONTMATTER_KEY_RE.match(before_lines[i])
            fields.append(match.group(1) if match else "<line>")
    # 行数差异（理论不应发生，但保留兜底标记便于排查）
    if len(before_lines) != len(after_lines):
        fields.append("<line>")
    return fields


def _summarize_diff(before: str, after: str) -> str:
    """生成紧凑可定位的 diff 摘要：列出每个全角字符及其替换次数。

    例：``"'：'x2, '\\u3000'x1"`` 表示两个全角冒号 + 一个全角空格被替换。
    用字符级统计而非整段 diff，保证摘要稳定（不依赖行序）且人类可读。
    """

    # before 中每个字符出现次数减去 after 中同字符次数 > 0 → 被替换掉
    before_counts = Counter(before)
    after_counts = Counter(after)
    removed = {ch: count for ch, count in (before_counts - after_counts).items() if count > 0}
    if not removed:
        return "(no removable chars detected)"
    parts = [f"{ch!r}x{count}" for ch, count in sorted(removed.items())]
    return ", ".join(parts)


def _relative_posix(data_root: Path, path: Path) -> str:
    """返回 ``path`` 相对 ``data_root`` 的 posix 路径；越界时回落为绝对路径。"""

    try:
        return path.relative_to(data_root).as_posix()
    except ValueError:
        return path.as_posix()


def _validate_data_root(data_root: Path) -> None:
    """护栏：拒绝不安全的 data_root 与非 canonical 数据根目录。

    canonical 校验（``resolve(strict=False)`` 后比对），覆盖以下不合法形态：

    - 文件系统根及其等价路径（如 ``Path('/tmp/..')``）：
      ``resolved == resolved.parent`` 是根的唯一判据；
    - 不存在 / 非目录两种错误配置；
    - 仓库根/项目根：自身含 ``.git`` 目录（生产部署 data_root 是
      用户配置的数据目录，本身不含 ``.git``）；
    - **非 canonical 数据根**：必须含 ``entries/`` 子目录才算合法
      EgoKnowledge 数据根（spec 真源边界：唯一可修复对象 =
      数据根下的 ``entries/**/*.md``）。仓库内非数据根目录
      （如 ``<repo>/tools``）、data_root 祖先目录、普通 tmp 目录
      一律不含 ``entries/``，应被拒绝避免静默返回空报告。

    plan 边界条件「禁止隐式扫描仓库根」与 Phase 2.2 批量修复停点信号
    （依赖 dry-run 数量判定是否需要修复）都靠这条护栏强制。
    """

    resolved = data_root.resolve(strict=False)
    if resolved == resolved.parent:
        raise NormalizeLegacyError(f"拒绝不安全的 data_root (文件系统根): {data_root}")
    if not resolved.exists():
        raise NormalizeLegacyError(f"data_root 不存在: {data_root}")
    if not resolved.is_dir():
        raise NormalizeLegacyError(f"data_root 不是目录: {data_root}")
    if (resolved / ".git").exists():
        raise NormalizeLegacyError(f"拒绝不安全的 data_root (仓库根，含 .git): {data_root}")
    # canonical 形态：必须含 entries/ 子目录。生产部署真源为
    # 用户配置数据根，其下必有 ``entries/``；祖先目录
    # （如 ``<repo>/data`` 或 ``<repo>``）、仓库内非数据根目录（如
    # ``<repo>/tools``）与普通 tmp 目录都没有 ``entries/``，应被拒绝。
    # 允许 ``entries/`` 为空目录（新装且未 ingest 任何条目的合法状态），
    # 但不允许缺失（缺失即说明传错了路径）。
    entries_dir = resolved / _SCAN_ENTRIES_SUBDIR
    if not entries_dir.is_dir():
        raise NormalizeLegacyError(
            f"data_root 不是 canonical EgoKnowledge 数据根 (缺少 entries/ 子目录): {data_root}"
        )


def _try_parse_yaml(text: str) -> str | None:
    """尝试 ``yaml.safe_load(text)``，合法返回原字符串，非法返回 None。

    抽取 yaml.safe_load + try/except YAMLError pass 模式，避免在三层兜底中
    重复写 4 次相同模式（W3 修复：让主函数变成线性 if 链）。
    """

    try:
        yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return text


def _ensure_yaml_parseable_after_fix(
    fm_raw: str, fixed_fm: str, abs_path: Path
) -> str:
    """spec.md:107-108 异常路径兜底：全角→半角修复后 YAML 仍不可解析时转义。

    场景：``_fix_fullwidth_punctuation`` 把全角冒号 ``：`` 替换为半角 ``:`` 后，
    PyYAML 把 ``kind:note``（冒号后无空格）解析为非法（YAML 1.1 要求 mapping
    后必须有空白）。spec.md:107-108 要求此时「停止写入，错误信息指向文件与
    字段；写入必须在替换原文件前失败」。本函数用两层兜底让多数场景恢复合法。

    兜底边界（最小侵入，三层递进）：

    - **不动无全角字符的 frontmatter**：``fixed_fm == fm_raw`` 时直接返回
      （原本就非法且与全角无关的不归 normalize_legacy 管，应被 doctor 跳过）。
    - 第一层 ``_normalize_toplevel_colon_spacing``：对顶层 ``key:value``
      补一个空格 → ``key: value``（YAML 规范推荐写法，diff 最小）。
    - 第二层 ``_quote_toplevel_scalars``：对顶层 scalar value 加单引号包裹
      （YAML 单引号转义：``'`` → ``''``）。处理 value 内含 ``: `` 的边界。
    - 第三层：兜底后仍非法 → 抛 ``NormalizeLegacyError``，错误信息含
      spec.md:107-108 + 文件路径 + 候选字段名（spec.md:108「文件与字段」要求）。

    返回值：可能规范化/转义后的 frontmatter 字符串（与 ``fixed_fm`` 同语义）。
    """

    # 无全角字符被替换 → 不在 normalize_legacy 范围（不兜底）
    if fixed_fm == fm_raw:
        return fixed_fm
    # 修复后合法，无需兜底（最小 diff）
    if _try_parse_yaml(fixed_fm) is not None:
        return fixed_fm
    # 第一层：顶层冒号后补空格（``kind:note`` → ``kind: note``）
    normalized = _normalize_toplevel_colon_spacing(fixed_fm)
    if normalized != fixed_fm and _try_parse_yaml(normalized) is not None:
        return normalized
    # 第二层：顶层 scalar 加单引号（处理 value 内 ``: ``）
    quoted = _quote_toplevel_scalars(normalized)
    if quoted != normalized and _try_parse_yaml(quoted) is not None:
        return quoted
    # 第三层：兜底失败 → 停止写入（spec.md:108），抛错逻辑抽到独立 helper
    # 让主函数保持线性 if 链（W3）+ 字段定位逻辑内聚（_raise_yaml_fallback_failure）。
    _raise_yaml_fallback_failure(fm_raw, fixed_fm, quoted, abs_path)


def _raise_yaml_fallback_failure(
    fm_raw: str, fixed_fm: str, quoted: str, abs_path: Path
) -> NoReturn:
    """spec.md:108 第三层兜底抛错：错误信息含文件 + 字段定位。

    抛错前从 PyYAML ``mark.line`` 反查受影响顶层字段名（``_locate_failing_field``）；
    mark 缺失或定位失败时回退到 fm_raw 与 fixed_fm 行级 diff 提取候选字段列表
    （``_extract_changed_fields``）。spec.md:108 字面要求「错误信息指向文件与字段」，
    本函数是该要求的最终落点。

    返回类型 ``NoReturn`` 让调用方 ``_ensure_yaml_parseable_after_fix`` 的线性
    if 链不需补冗余 return / raise，mypy strict 也能识别终态。
    """

    field_hint, exc = _locate_failing_field(quoted)
    candidate_fields = _extract_changed_fields(fm_raw, fixed_fm)
    raise NormalizeLegacyError(
        f"frontmatter 全角→半角修复后 YAML 仍不可解析 "
        f"(spec.md:107-108 兜底失败) {abs_path}: "
        f"field={field_hint or '<unknown>'}, "
        f"候选字段={candidate_fields or '<unknown>'}: {exc}"
    ) from exc


def _locate_failing_field(text: str) -> tuple[str | None, yaml.YAMLError]:
    """对 ``text`` 跑 ``yaml.safe_load`` 拿 YAMLError，返回 (字段名, 异常实例)。

    spec.md:108 要求错误信息「指向文件与字段」。PyYAML ``MarkedYAMLError``
    含 ``problem_mark`` / ``context_mark``，``.line`` 是 0-indexed 行号。
    本函数据此定位 frontmatter 行并解析顶层字段名（``_FRONTMATTER_KEY_RE``）；
    mark 缺失或行越界时字段名为 None，调用方再走候选字段列表回退路径。

    调用方已知 ``text`` 解析必失败（``_ensure_yaml_parseable_after_fix``
    前三层 ``_try_parse_yaml`` 已各跑一次 ``yaml.safe_load`` 但吞掉异常返回
    None）；本函数对 ``text`` 再跑一次 ``yaml.safe_load`` 拿 ``YAMLError``
    实例（``_try_parse_yaml`` 为保持线性 if 链简洁未保留异常对象）。复跑代价
    有意接受：单文件兜底路径触发频率极低（仅 frontmatter 修复后仍非法时），
    用一次冗余解析换取主流程的可读性。
    """

    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(
            exc, "context_mark", None
        )
        if mark is None:
            return None, exc
        lines = text.splitlines()
        # PyYAML mark.line 是 0-indexed，与 splitlines 索引对齐。
        if 0 <= mark.line < len(lines):
            match = _FRONTMATTER_KEY_RE.match(lines[mark.line])
            if match:
                return match.group(1), exc
        return None, exc
    # 理论不会走到（调用前 _try_parse_yaml 已返回 None）；
    # 兜底构造一个无 mark 的 YAMLError，保证消息可拼接且不掩盖逻辑错误。
    return None, yaml.YAMLError("unexpected: yaml.safe_load unexpectedly succeeded")


def _normalize_toplevel_colon_spacing(fm_raw: str) -> str:
    """对顶层 ``key:value``（冒号后无空格）补一个空格 → ``key: value``。

    PyYAML 解析 ``kind:note`` 会抛 YAMLError（mapping 后必须有空白）。
    本函数对行首无缩进的顶层 ``key:value`` 形式补一个空格，让其合法化。
    跳过：

    - 已有 ``key: value`` 形式（``\\s`` 已在 ``:`` 后）；
    - ``key:`` 后为空（value 在后续行，如 list/block scalar）；
    - 行首有缩进（list 项、嵌套 dict 子项）。
    """

    lines: list[str] = []
    for line in fm_raw.splitlines(keepends=True):
        body = line.rstrip("\n")
        newline = "\n" if line.endswith("\n") else ""
        match = _TOPLEVEL_KEY_NOSPACE_RE.match(body)
        if match:
            key, value = match.group(1), match.group(2)
            lines.append(f"{key}: {value}{newline}")
        else:
            lines.append(line)
    return "".join(lines)


def _quote_toplevel_scalars(fm_raw: str) -> str:
    """对顶层 ``key: value`` 行的 scalar value 加单引号（YAML 转义）。

    仅识别行首无缩进的顶层字段，避免误改 list 项内嵌 dict 或 block scalar。
    跳过：

    - 已是单/双引号包裹的 value；
    - 以 YAML 特殊起始字符开头的 value（``|`` block、``[`` flow seq、
      ``{`` flow map、``&`` anchor、``*`` alias、``!`` tag 等）；
    - 不含 ``: `` 且不以 ``:`` 结尾的普通 plain scalar（YAML 合法，无需 quote）。
    """

    lines: list[str] = []
    for line in fm_raw.splitlines(keepends=True):
        body = line.rstrip("\n")
        newline = "\n" if line.endswith("\n") else ""
        match = _TOPLEVEL_SCALAR_RE.match(body)
        if match:
            key, sep, value = match.group(1), match.group(2), match.group(3)
            if _needs_yaml_quote(value):
                escaped = value.replace("'", "''")
                # 保证 ``key:`` 后冒号有空格（YAML mapping 后必须有空白）；
                # 端到端路径里 _normalize_toplevel_colon_spacing 已补空格，
                # 这里对 sep="" 的边界（直接调用本函数）补一个空格防误用。
                effective_sep = sep if sep else " "
                lines.append(f"{key}:{effective_sep}'{escaped}'{newline}")
                continue
        lines.append(line)
    return "".join(lines)


def _needs_yaml_quote(value: str) -> bool:
    """判断顶层 scalar value 是否需要加单引号才能保证 YAML 合法。

    覆盖 spec.md:107-108 的兜底目标场景：全角 ``：`` 转半角 ``:`` 后
    与后续空格组合形成 mapping 解析歧义。其他 plain scalar 合法场景
    不动，保持最小 diff。
    """

    if not value:
        return False
    # 已是引号包裹
    if (value[0] == "'" and value[-1] == "'") or (
        value[0] == '"' and value[-1] == '"'
    ):
        return False
    # YAML 特殊起始字符（block/flow/anchor/alias/tag 等）
    if value[0] in "|>&*!%@`#-,{[":
        return False
    # 含 ``: ``（冒号+空格）或以 ``:`` 结尾 → 会被解析为 mapping，需 quote
    if ": " in value or value.endswith(":"):
        return True
    return False
