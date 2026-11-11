#!/usr/bin/env python3

import sys

SKIP = {
    0x00,
    0x4D, 0xB3,   # M
    0x50, 0xB0,   # P
    0x51, 0xAF,   # Q
    0x1A, 0xE6,
    0x20, 0xE0,
}


def decrypt_mpq(src, dst):
    with open(src, "rb") as f:
        data = bytearray(f.read())

    # 必须满足 magic=0x41
    if len(data) >= 8 and data[4] == 0x41:
        for i in range(24):
            pos = 8 + i
            if pos >= len(data):
                break

            b = data[pos]

            if b not in SKIP:
                data[pos] = (-b) & 0xFF

    with open(dst, "wb") as f:
        f.write(data)

    print(f"saved: {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} input.mpq output.mpq")
        sys.exit(1)

    decrypt_mpq(sys.argv[1], sys.argv[2])
