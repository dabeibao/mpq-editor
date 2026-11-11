"""MPQ 内部路径的工具函数。

MPQ 的名字哈希不区分大小写，同一个文件/目录可能以不同大小写出现在
(listfile) 与枚举结果中。本模块提供大小写不敏感的比较、以及把混合
大小写的路径合并为单一规范形式的工具。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import TypeVar

T = TypeVar("T")


def normalize_sep(path: str) -> str:
    """把反斜杠分隔符统一为正斜杠。"""
    return path.replace("\\", "/")


def fold(path: str) -> str:
    """归一化分隔符并折叠大小写，用于大小写不敏感比较。"""
    return normalize_sep(path).casefold()


def split_parts(path: str) -> list[str]:
    """按两种分隔符拆分路径（不含空组件）。"""
    return [p for p in normalize_sep(path).split("/") if p]


def relative_to(file: str, dir_path: str) -> str | None:
    """返回 file 中位于 dir_path 之后的相对部分（大小写不敏感）。

    file 必须严格位于 dir_path 之下（相等时返回 None）。
    """
    parts = split_parts(file)
    dparts = split_parts(dir_path)
    if len(parts) <= len(dparts):
        return None
    for p, dp in zip(parts, dparts, strict=False):
        if p.casefold() != dp.casefold():
            return None
    return "/".join(parts[len(dparts) :])


def is_under(path: str, dir_path: str) -> bool:
    """path 是否严格位于 dir_path 之下（大小写不敏感）。"""
    return relative_to(path, dir_path) is not None


def is_under_or_eq(path: str, dir_path: str) -> bool:
    """path 是否位于 dir_path 之下或与之相等（大小写不敏感）。"""
    if fold(path) == fold(dir_path):
        return True
    return is_under(path, dir_path)


def _pick_canonical(counter: Counter[str]) -> str:
    """选出规范形式：出现次数最多，其次按字典序保证确定性。"""
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def canonical_by_fold(paths: Iterable[str]) -> dict[str, str]:
    """对一组路径按折叠大小写分组，返回 fold -> 规范路径。

    每组内出现次数最多的形式作为规范形式（并列时取字典序最小），
    保证同一文件只保留一个名字。规范形式保留原始字符串（含原始
    分隔符），以便与按名字记忆的编码映射保持一致。
    """
    counts: dict[str, Counter[str]] = {}
    for p in paths:
        counts.setdefault(fold(p), Counter())[p] += 1
    return {k: _pick_canonical(c) for k, c in counts.items()}


def build_dir_map(files: Iterable[str], empty_dirs: Iterable[str] = ()) -> dict[str, str]:
    """由文件路径（及空目录）推导所有目录的规范形式。

    返回 fold(目录) -> 规范目录路径。每个目录取包含它的文件中
    出现最多的写法，空目录各贡献一票。
    """
    counts: dict[str, Counter[str]] = {}
    for f in files:
        parts = split_parts(f)
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            counts.setdefault(d.casefold(), Counter())[d] += 1
    for d in empty_dirs:
        parts = split_parts(d)
        for i in range(1, len(parts) + 1):
            dd = "/".join(parts[:i])
            counts.setdefault(dd.casefold(), Counter())[dd] += 1
    return {k: _pick_canonical(c) for k, c in counts.items()}


def sort_key(path: str) -> tuple[int, str]:
    """目录树排序键：先按深度，再按折叠大小写。"""
    return (path.count("/"), fold(path))
