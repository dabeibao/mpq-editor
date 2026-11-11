#!/usr/bin/env python3
"""还原 MPQ 样本头部被按字节取反的字段，输出到新文件（不改动原文件）。"""

from pathlib import Path

MAGIC_OFFSET = 4
MAGIC_VALUE = 0x41
HEADER_START = 8
HEADER_SIZE = 24

# 取反后会落回自身或变成保留值的字节，保持原样，避免二次破坏。
# 成对出现的值互为取反结果（例如 0xB3 取反即 0x4D，也就是 'M'）。
SKIP = {
    0x00,
    0x4D,
    0xB3,  # M
    0x50,
    0xB0,  # P
    0x51,
    0xAF,  # Q
    0x1A,
    0xE6,
    0x20,
    0xE0,
}


def decrypt_mpq(src: str, dst: str) -> None:
    data = bytearray(Path(src).read_bytes())

    # 只有 magic 位匹配的样本才需要处理头部字段。
    if len(data) >= HEADER_START and data[MAGIC_OFFSET] == MAGIC_VALUE:
        end = min(HEADER_START + HEADER_SIZE, len(data))
        for pos in range(HEADER_START, end):
            byte = data[pos]
            if byte not in SKIP:
                data[pos] = (-byte) & 0xFF

    Path(dst).write_bytes(data)
    print(f"saved: {dst}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} input.mpq output.mpq")
        sys.exit(1)

    decrypt_mpq(sys.argv[1], sys.argv[2])
