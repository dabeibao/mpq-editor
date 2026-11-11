"""MPQ 内部特殊文件的信息。

MPQ 档案可能包含若干内部维护文件，它们不是普通的数据文件：

- ``(listfile)``   文本格式，档案内所有文件名的清单。
- ``(attributes)`` 二进制格式，每个文件的 CRC32/文件时间/MD5 校验数据。
- ``(signature)`` 二进制格式，Blizzard 数字签名。

二进制文件用文本方式查看时显示“乱码”属于正常现象，并非数据损坏。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecialFileInfo:
    label: str
    description: str
    is_binary: bool


_SPECIAL_FILES: dict[str, SpecialFileInfo] = {
    "(listfile)": SpecialFileInfo(
        label="MPQ 列表文件",
        description="档案内所有文件名的文本清单，可用于识别未命名的文件。",
        is_binary=False,
    ),
    "(attributes)": SpecialFileInfo(
        label="MPQ 校验数据",
        description=(
            "二进制格式的校验数据（每个文件的 CRC32 / 文件时间 / MD5），"
            "不是文本文件，用文本编辑器打开显示乱码属正常现象。"
        ),
        is_binary=True,
    ),
    "(signature)": SpecialFileInfo(
        label="MPQ 数字签名",
        description=(
            "二进制格式的 Blizzard 数字签名，不是文本文件，"
            "用文本编辑器打开显示乱码属正常现象。"
        ),
        is_binary=True,
    ),
}


def special_file_info(mpq_path: str) -> SpecialFileInfo | None:
    """返回内部特殊文件的信息；普通文件返回 None。"""
    base = mpq_path.replace("\\", "/").split("/")[-1]
    return _SPECIAL_FILES.get(base.casefold())
