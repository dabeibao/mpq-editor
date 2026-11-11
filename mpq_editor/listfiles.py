"""列表文件（listfile）的目录扫描。

程序按以下顺序扫描 ``Listfiles`` 目录，作为打开 MPQ 时可用的默认列表：

1. 可执行文件所在目录下的 ``Listfiles``（编译后的程序目录）；
2. PyInstaller 解包目录 ``sys._MEIPASS`` 下的 ``Listfiles``；
3. 开发模式下仓库根目录的 ``Listfiles``。

同名文件先扫描到的目录优先。
"""

from __future__ import annotations

import sys
from pathlib import Path

LISTFILES_DIRNAME = "Listfiles"


def candidate_dirs() -> list[Path]:
    """返回所有可能包含列表文件的目录（仅返回存在的目录）。"""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).parent / LISTFILES_DIRNAME)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass) / LISTFILES_DIRNAME)
    else:
        dirs.append(Path(__file__).resolve().parent.parent / LISTFILES_DIRNAME)
    return [d for d in dirs if d.is_dir()]


def scan_listfiles() -> list[Path]:
    """扫描所有候选目录，返回去重后的 .txt 列表文件（按文件名排序）。"""
    found: dict[str, Path] = {}
    for d in candidate_dirs():
        for p in sorted(d.glob("*.txt")):
            found.setdefault(p.name.casefold(), p)
    return [found[k] for k in sorted(found)]
