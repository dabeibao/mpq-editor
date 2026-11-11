"""DC6 数据模型与图像处理算法（不依赖任何 QWidget）。

本模块只依赖 PySide6.QtGui 与标准库，界面层、命令行工具、单元测试都可以
独立导入，避免把业务逻辑绑在窗口对象上。
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QColor, QImage

DC6_SIGNATURE = 6
PAL_DIR = Path(__file__).parent / "pal"

# DC6 单帧的尺寸上限：D2 的 cell 文件把宽高写进块头，超过 256 就无法作为一帧。
MAX_FRAME_SIZE = 256
# 块头里的 nXOffset / nYOffset 是有符号 32 位整数。
OFFSET_MIN = -(2**31)
OFFSET_MAX = 2**31 - 1


@dataclass
class FrameInfo:
    width: int
    height: int
    x_offset: int = 0
    y_offset: int = 0
    pixels: bytearray = field(default_factory=bytearray)


def palette_rgb(palette: bytes, index: int) -> tuple[int, int, int]:
    """返回调色板第 index 项的 (r, g, b)；越界时返回黑色。"""
    off = index * 3
    if off + 2 < len(palette):
        return palette[off + 2], palette[off + 1], palette[off]
    return 0, 0, 0


class Dc6File:
    def __init__(self, data: bytes):
        self.version: int = 0
        self.termination: bytes = b""
        self.frames: list[FrameInfo] = []
        self.palette: bytes = b""
        self._parse(data)

    @classmethod
    def blank(cls) -> Dc6File:
        """创建一个空白 DC6（尚未保存过，帧列表为空）。"""
        dc6 = cls.__new__(cls)
        dc6.version = DC6_SIGNATURE
        dc6.termination = b"\xee\xee\xee\xee"
        dc6.frames = []
        dc6.palette = b""
        return dc6

    def _parse(self, data: bytes):
        (
            self.version,
            _unknown01,
            _unknown02,
        ) = struct.unpack_from("<III", data, 0)
        if self.version != DC6_SIGNATURE:
            raise ValueError(f"Not a DC6 file (version={self.version})")

        term = struct.unpack_from("4B", data, 12)
        self.termination = bytes(term)

        (_unknown03, blockcount) = struct.unpack_from("<II", data, 16)

        if blockcount < 1 or blockcount > 1024:
            raise ValueError(f"Invalid block count: {blockcount}")

        pointers = struct.unpack_from(f"<{blockcount}I", data, 24)

        self.frames = []
        for i in range(blockcount):
            offset = pointers[i]
            (
                _bh_flags,
                width,
                height,
                x_offset,
                y_offset,
                _bh_unknown04,
                _next_block,
                length,
            ) = struct.unpack_from("<IIIIIIII", data, offset)
            raw = data[offset + 32 : offset + 32 + length]
            pixels = self._decompress(raw, width, height)
            self.frames.append(
                FrameInfo(
                    width=width,
                    height=height,
                    x_offset=self._to_signed(x_offset),
                    y_offset=self._to_signed(y_offset),
                    pixels=pixels,
                )
            )

    @staticmethod
    def _to_signed(value: int) -> int:
        # Block header offsets (nXOffset/nYOffset) are signed 32-bit in the game
        # (see D2CMP D2GfxCellStrc), e.g. -2 for the grasp cursor hotspot.
        return value - 0x100000000 if value >= 0x80000000 else value

    @staticmethod
    def _decompress(data: bytes, width: int, height: int) -> bytearray:
        result = bytearray(width * height)
        in_pos = 0
        row = height - 1
        col = 0

        while in_pos < len(data) and row >= 0:
            c1 = data[in_pos]
            in_pos += 1
            if c1 == 0x80:
                if col < width:
                    pass
                row -= 1
                col = 0
            elif c1 > 0x80:
                skip = c1 - 0x80
                col += skip
            else:
                for _ in range(c1):
                    if col < width and row >= 0:
                        result[row * width + col] = data[in_pos]
                        in_pos += 1
                        col += 1
                    else:
                        in_pos += 1

        return result

    def get_rgba_image(self, frame_index: int) -> QImage:
        """返回 Indexed8 图像（沿用历史命名：颜色由当前调色板决定）。"""
        frame = self.frames[frame_index]
        w, h = frame.width, frame.height
        img = QImage(w, h, QImage.Format.Format_Indexed8)
        img.setColorCount(256)
        for i in range(256):
            r, g, b = palette_rgb(self.palette, i)
            img.setColor(i, QColor(r, g, b).rgb())
        for y in range(h):
            for x in range(w):
                img.setPixel(x, y, frame.pixels[y * w + x])
        return img

    def get_argb_image(self, frame_index: int) -> QImage:
        """返回带透明通道的 ARGB32 图像（调色板 0 号色视为透明），用于 PNG 导出。"""
        frame = self.frames[frame_index]
        w, h = frame.width, frame.height
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        colors = [QColor(*palette_rgb(self.palette, i), 255).rgba() for i in range(256)]
        for y in range(h):
            for x in range(w):
                index = frame.pixels[y * w + x]
                if index:
                    img.setPixel(x, y, colors[index])
        return img

    def total_frames(self) -> int:
        return len(self.frames)


def compress_frame(pixels: bytes | bytearray, width: int, height: int) -> bytes:
    buf = io.BytesIO()
    transcol = 0

    for line in range(height - 1, -1, -1):
        col = 0
        row_data = pixels[line * width : (line + 1) * width]
        while col < width:
            run_start = col
            while col < width and row_data[col] == transcol:
                col += 1
            skip = col - run_start
            if skip:
                while skip > 0x7F:
                    buf.write(bytes([0xFF]))
                    skip -= 0x7F
                if skip:
                    buf.write(bytes([0x80 | skip]))

            if col >= width:
                break

            run_start = col
            while col < width and row_data[col] != transcol:
                col += 1
            literal = row_data[run_start:col]
            while literal:
                chunk = literal[:0x7F]
                buf.write(bytes([len(chunk)]))
                buf.write(chunk)
                literal = literal[0x7F:]

        buf.write(b"\x80")

    return buf.getvalue()


def load_palette(name_or_path: str) -> bytes:
    p = Path(name_or_path)
    if not p.is_file():
        p = PAL_DIR / f"{name_or_path}.pal"
    if not p.is_file():
        p = PAL_DIR / name_or_path
    if not p.is_file():
        raise FileNotFoundError(f"Cannot find palette: {name_or_path}")
    data = p.read_bytes()
    if len(data) != 768:
        raise ValueError(f"Invalid palette file size: {len(data)} (expected 768)")
    return data


def list_palettes() -> list[str]:
    if not PAL_DIR.is_dir():
        return []
    return sorted(f.stem for f in PAL_DIR.iterdir() if f.suffix.lower() == ".pal")


def build_dc6_bytes(dc6: Dc6File) -> bytes:
    """Serialize a Dc6File back to the DC6 on-disk format."""
    buf = io.BytesIO()

    buf.write(struct.pack("<III", dc6.version, 1, 0))
    buf.write(dc6.termination[:4].ljust(4, b"\xee"))
    buf.write(struct.pack("<II", 1, dc6.total_frames()))

    pointer_table_offset = buf.tell()
    for _ in range(dc6.total_frames()):
        buf.write(struct.pack("<I", 0))

    pointers: list[int] = []
    for frame in dc6.frames:
        pointers.append(buf.tell())
        block_start = buf.tell()
        # Block header: flags, width, height, nXOffset, nYOffset, unk, next, length
        buf.write(
            struct.pack(
                "<IIIiiIII",
                0,
                frame.width,
                frame.height,
                frame.x_offset,
                frame.y_offset,
                0,
                0,
                0,
            )
        )

        data_start = buf.tell()
        compressed = compress_frame(bytes(frame.pixels), frame.width, frame.height)
        buf.write(compressed)
        data_end = buf.tell()

        buf.write(b"\xee\xee\xee")
        block_end = buf.tell()

        buf.seek(block_start + 24)
        buf.write(struct.pack("<II", block_end, data_end - data_start))
        buf.seek(block_end)

    buf.seek(pointer_table_offset)
    for p in pointers:
        buf.write(struct.pack("<I", p))

    return buf.getvalue()


def make_bmp_data(frame: FrameInfo, palette: bytes) -> bytes:
    """把单帧编码成 8 位索引色 BMP（保留调色板索引，不丢色）。

    布局：BITMAPFILEHEADER(14) + BITMAPINFOHEADER(40) + 256*4 调色板 + 像素数据。
    """
    w, h = frame.width, frame.height
    if w <= 0 or h <= 0:
        raise ValueError(f"无效的帧尺寸: {w}x{h}")
    if len(frame.pixels) < w * h:
        raise ValueError(f"帧像素数据不足: {len(frame.pixels)} < {w * h}")
    row_size = ((w * 8 + 31) // 32) * 4
    palette_size = 256 * 4
    pixel_offset = 14 + 40 + palette_size
    pixel_data_size = row_size * h
    file_size = pixel_offset + pixel_data_size

    out = bytearray(file_size)

    struct.pack_into("<2sIHHI", out, 0, b"BM", file_size, 0, 0, pixel_offset)
    struct.pack_into(
        "<IiiHHIIiiII",
        out,
        14,
        40,
        w,
        h,
        1,
        8,
        0,
        pixel_data_size,
        0,
        0,
        256,
        0,
    )

    for i in range(256):
        r, g, b = palette_rgb(palette, i)
        off = 14 + 40 + i * 4
        out[off] = b
        out[off + 1] = g
        out[off + 2] = r
        out[off + 3] = 0

    for y in range(h):
        dst = pixel_offset + (h - 1 - y) * row_size
        src = y * w
        out[dst : dst + w] = frame.pixels[src : src + w]

    return bytes(out)


@lru_cache(maxsize=65536)
def nearest_palette_index(r: int, g: int, b: int, palette: bytes) -> int:
    """在调色板里找欧氏距离最近的颜色索引。

    结果与逐像素暴力搜索完全一致；缓存让大面积同色图片（精灵图）避免重复计算。
    """
    best_idx = 0
    best_dist = 3 * 256 * 256
    for i in range(min(len(palette) // 3, 256)):
        pr, pg, pb = palette_rgb(palette, i)
        dr = r - pr
        dg = g - pg
        db = b - pb
        dist = dr * dr + dg * dg + db * db
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def floyd_steinberg_dither(img: QImage, palette: bytes, ratio: float) -> QImage:
    w, h = img.width(), img.height()
    ratio = max(0.0, min(2.0, ratio / 100.0))

    img_rgba = img.convertToFormat(QImage.Format.Format_ARGB32)

    err_cur = [[0.0, 0.0, 0.0] for _ in range(w + 1)]
    err_next = [[0.0, 0.0, 0.0] for _ in range(w + 1)]

    out = QImage(w, h, QImage.Format.Format_Indexed8)
    out.setColorCount(256)
    for i in range(256):
        r, g, b = palette_rgb(palette, i)
        out.setColor(i, QColor(r, g, b).rgb())

    for y in range(h):
        for x in range(w):
            pixel = img_rgba.pixel(x, y)
            orig_r = (pixel >> 16) & 0xFF
            orig_g = (pixel >> 8) & 0xFF
            orig_b = pixel & 0xFF

            r = orig_r + err_cur[x][0]
            g = orig_g + err_cur[x][1]
            b = orig_b + err_cur[x][2]

            r = max(0, min(255, int(r + 0.5)))
            g = max(0, min(255, int(g + 0.5)))
            b = max(0, min(255, int(b + 0.5)))

            best_idx = nearest_palette_index(r, g, b, palette)
            out.setPixel(x, y, best_idx)

            pr, pg, pb = palette_rgb(palette, best_idx)
            qr = (r - pr) * ratio
            qg = (g - pg) * ratio
            qb = (b - pb) * ratio

            if x + 1 < w:
                err_cur[x + 1][0] += qr * 7.0 / 16.0
                err_cur[x + 1][1] += qg * 7.0 / 16.0
                err_cur[x + 1][2] += qb * 7.0 / 16.0

            if x > 0 and y + 1 < h:
                err_next[x - 1][0] += qr * 3.0 / 16.0
                err_next[x - 1][1] += qg * 3.0 / 16.0
                err_next[x - 1][2] += qb * 3.0 / 16.0

            if y + 1 < h:
                err_next[x][0] += qr * 5.0 / 16.0
                err_next[x][1] += qg * 5.0 / 16.0
                err_next[x][2] += qb * 5.0 / 16.0

            if x + 1 < w and y + 1 < h:
                err_next[x + 1][0] += qr * 1.0 / 16.0
                err_next[x + 1][1] += qg * 1.0 / 16.0
                err_next[x + 1][2] += qb * 1.0 / 16.0

        err_cur, err_next = err_next, [[0.0, 0.0, 0.0] for _ in range(w + 1)]

    return out


def plan_tiles(width: int, height: int, frames_per_row: int) -> list[tuple[int, int, int, int]]:
    """计算拆分方案，返回 [(x, y, w, h)]。

    按「每组 frames_per_row 列、每列 256 宽、每条 256 高」从左到右、从上到下切。
    例如 1024x384、frames_per_row=2 得到 8 块（见 design.md）。
    """
    fpr = max(1, frames_per_row)
    group_width = fpr * MAX_FRAME_SIZE
    tiles: list[tuple[int, int, int, int]] = []

    for group_x in range(0, width, group_width):
        for strip_y in range(0, height, MAX_FRAME_SIZE):
            strip_h = min(MAX_FRAME_SIZE, height - strip_y)
            for i in range(fpr):
                tile_x = group_x + i * MAX_FRAME_SIZE
                if tile_x >= width:
                    break
                tiles.append((tile_x, strip_y, min(MAX_FRAME_SIZE, width - tile_x), strip_h))

    return tiles


def split_image_tiles(img: QImage, frames_per_row: int) -> list[tuple[int, int, int, int, QImage]]:
    return [
        (x, y, w, h, img.copy(x, y, w, h))
        for x, y, w, h in plan_tiles(img.width(), img.height(), frames_per_row)
    ]
