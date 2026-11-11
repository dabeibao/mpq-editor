from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtGui import QColor, QImage

from dc6.dc6_viewer import (
    Dc6File,
    FrameInfo,
    build_dc6_bytes,
    compress_frame,
    floyd_steinberg_dither,
    list_palettes,
    load_palette,
    split_image_tiles,
)


def make_simple_dc6(
    width: int,
    height: int,
    pixels: bytes | None = None,
    x_offset: int = 0,
    y_offset: int = 0,
) -> bytes:
    """Build a minimal valid DC6 file with 1 frame."""
    if pixels is None:
        pixels = bytes([0] * width * height)

    compressed = compress_frame(pixels, width, height)
    # Block header: flags, width, height, nXOffset, nYOffset, unk, next, length
    header = bytearray(struct.pack("<IIIiiIII", 0, width, height, x_offset, y_offset, 0, 0, 0))
    struct.pack_into("<I", header, 28, len(compressed))
    block_data = bytes(header) + compressed + b"\xee\xee\xee"

    buf = io.BytesIO()
    buf.write(struct.pack("<III", 6, 1, 0))
    buf.write(b"\xee\xee\xee\xee")
    buf.write(struct.pack("<II", 1, 1))
    buf.write(struct.pack("<I", 24 + 4))
    buf.write(block_data)
    return buf.getvalue()


class TestDc6File:
    def test_parse_valid(self):
        data = make_simple_dc6(16, 16)
        dc6 = Dc6File(data)
        assert dc6.version == 6
        assert dc6.total_frames() == 1
        assert dc6.frames[0].width == 16
        assert dc6.frames[0].height == 16

    def test_parse_invalid_version(self):
        data = make_simple_dc6(16, 16)
        data = bytearray(data)
        struct.pack_into("<I", data, 0, 5)
        with pytest.raises(ValueError, match="Not a DC6 file"):
            Dc6File(bytes(data))

    def test_parse_empty_data(self):
        with pytest.raises(struct.error):
            Dc6File(b"")

    def test_parse_invalid_blockcount(self):
        buf = io.BytesIO()
        buf.write(struct.pack("<III", 6, 1, 0))
        buf.write(b"\xee\xee\xee\xee")
        buf.write(struct.pack("<II", 1, 0))
        with pytest.raises(ValueError, match="Invalid block count"):
            Dc6File(buf.getvalue())

    def test_multi_frame(self):
        frame_pixels = bytes([1] * 64)
        compressed = compress_frame(frame_pixels, 8, 8)
        block_data = struct.pack("<IIIIIIII", 0, 8, 8, 0, 0, 0, 0, 0)
        block_data += compressed
        block_data += b"\xee\xee\xee"

        block_size = len(block_data)

        buf = io.BytesIO()
        buf.write(struct.pack("<III", 6, 1, 0))
        buf.write(b"\xee\xee\xee\xee")
        buf.write(struct.pack("<II", 1, 2))

        first_ptr = 24 + 2 * 4
        second_ptr = first_ptr + block_size
        buf.write(struct.pack("<II", first_ptr, second_ptr))

        buf.write(block_data)
        buf.write(block_data)

        data = buf.getvalue()
        dc6 = Dc6File(data)
        assert dc6.total_frames() == 2
        assert dc6.frames[0].width == 8
        assert dc6.frames[0].height == 8
        assert dc6.frames[1].width == 8
        assert dc6.frames[1].height == 8

    def test_pixel_data_decompressed(self):
        w, h = 4, 4
        original = bytes(range(w * h))
        data = make_simple_dc6(w, h, original)
        dc6 = Dc6File(data)
        frame = dc6.frames[0]
        assert frame.width == w
        assert frame.height == h

    def test_decompress_various_widths(self):
        for w, h in [(1, 1), (2, 2), (10, 5), (31, 17), (64, 64), (100, 30)]:
            original = bytes([i % 256 for i in range(w * h)])
            data = make_simple_dc6(w, h, original)
            dc6 = Dc6File(data)
            assert dc6.frames[0].width == w
            assert dc6.frames[0].height == h

    def test_decompress_transparent_runs(self):
        w, h = 10, 5
        pixels = bytearray(w * h)
        for i in range(len(pixels)):
            pixels[i] = 0 if (i % 3 == 0) else 1
        data = make_simple_dc6(w, h, bytes(pixels))
        dc6 = Dc6File(data)
        frame = dc6.frames[0]
        assert frame.width == w
        assert frame.height == h


class TestDc6FileRoundtrip:
    def test_write_then_read(self):
        w, h = 8, 8
        original = make_simple_dc6(w, h)
        dc6 = Dc6File(original)

        written = build_dc6_bytes(dc6)
        dc6_2 = Dc6File(written)
        assert dc6_2.total_frames() == 1
        assert dc6_2.frames[0].width == w
        assert dc6_2.frames[0].height == h
        assert dc6_2.version == 6


class TestFrameOffsets:
    def test_parse_positive_offsets(self):
        data = make_simple_dc6(8, 8, x_offset=5, y_offset=23)
        dc6 = Dc6File(data)
        assert dc6.frames[0].x_offset == 5
        assert dc6.frames[0].y_offset == 23

    def test_parse_negative_offsets(self):
        # Offsets are signed 32-bit on disk (real data: grasp cursor x=-2, y=24)
        data = make_simple_dc6(8, 8, x_offset=-2, y_offset=24)
        dc6 = Dc6File(data)
        assert dc6.frames[0].x_offset == -2
        assert dc6.frames[0].y_offset == 24

    def test_parse_zero_offsets(self):
        data = make_simple_dc6(8, 8)
        dc6 = Dc6File(data)
        assert dc6.frames[0].x_offset == 0
        assert dc6.frames[0].y_offset == 0

    def test_offsets_preserved_on_roundtrip(self):
        f1 = FrameInfo(width=8, height=8, pixels=bytearray(64), x_offset=10, y_offset=-7)
        f2 = FrameInfo(width=4, height=4, pixels=bytearray(16), x_offset=0, y_offset=24)
        dc6 = Dc6File.__new__(Dc6File)
        dc6.version = 6
        dc6.termination = b"\xee\xee\xee\xee"
        dc6.frames = [f1, f2]
        dc6.palette = b""

        dc6_2 = Dc6File(build_dc6_bytes(dc6))
        assert dc6_2.total_frames() == 2
        assert dc6_2.frames[0].x_offset == 10
        assert dc6_2.frames[0].y_offset == -7
        assert dc6_2.frames[1].x_offset == 0
        assert dc6_2.frames[1].y_offset == 24
        # pixel payload must survive the roundtrip too
        assert bytes(dc6_2.frames[0].pixels) == bytes(f1.pixels)
        assert bytes(dc6_2.frames[1].pixels) == bytes(f2.pixels)

    def test_real_file_header_layout(self):
        # Mirror the layout of D2CMP.dll's D2GfxCellStrc block header:
        # 0x00 flags, 0x04 width, 0x08 height, 0x0C nXOffset, 0x10 nYOffset
        w, h = 4, 2
        compressed = compress_frame(bytes([0] * (w * h)), w, h)
        header = bytearray(32)
        struct.pack_into("<I", header, 0x04, w)
        struct.pack_into("<I", header, 0x08, h)
        struct.pack_into("<i", header, 0x0C, 1)
        struct.pack_into("<i", header, 0x10, -15)
        struct.pack_into("<I", header, 0x1C, len(compressed))
        buf = io.BytesIO()
        buf.write(struct.pack("<III", 6, 1, 0))
        buf.write(b"\xee\xee\xee\xee")
        buf.write(struct.pack("<II", 1, 1))
        buf.write(struct.pack("<I", 24 + 4))  # pointer table at 0x18, block after it
        buf.write(bytes(header))
        buf.write(compressed)
        buf.write(b"\xee\xee\xee")
        dc6 = Dc6File(buf.getvalue())
        assert dc6.frames[0].x_offset == 1
        assert dc6.frames[0].y_offset == -15


class TestPalette:
    def test_load_palette_by_name(self):
        pal = load_palette("act1")
        assert len(pal) == 768

    def test_load_palette_full_path(self):
        pal_path = Path(__file__).parent.parent / "dc6" / "pal" / "act2.pal"
        pal = load_palette(str(pal_path))
        assert len(pal) == 768

    def test_load_palette_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_palette("nonexistent_pal")

    def test_list_palettes(self):
        palettes = list_palettes()
        assert len(palettes) > 0
        assert "act1" in palettes
        assert "act2" in palettes
        assert "units" in palettes

    def test_palette_format(self):
        pal = load_palette("act1")
        assert len(pal) == 768
        assert pal[0:3] == b"\x00\x00\x00"
        assert pal[3:6] == b"\x00\x00$"


class TestDc6FileEdgeCases:
    def test_frame_with_varying_data(self):
        w, h = 16, 16
        pixels = bytes([0xAA] * (w * h))
        data = make_simple_dc6(w, h, pixels)
        dc6 = Dc6File(data)
        assert dc6.frames[0].width == w
        assert dc6.frames[0].height == h

    def test_zero_pixels_frame(self):
        w, h = 8, 8
        pixels = bytes([0] * (w * h))
        data = make_simple_dc6(w, h, pixels)
        dc6 = Dc6File(data)
        assert dc6.frames[0].width == w
        assert dc6.frames[0].height == h


class TestCompressRoundtrip:
    @staticmethod
    def _decompress(data: bytes, width: int, height: int) -> bytearray:
        return Dc6File._decompress(data, width, height)

    def test_roundtrip_simple(self):
        w, h = 16, 16
        original = bytes([i % 256 for i in range(w * h)])
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original

    def test_roundtrip_all_transparent(self):
        w, h = 32, 16
        original = bytes(w * h)
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original

    def test_roundtrip_all_solid(self):
        w, h = 8, 8
        original = bytes([0x42] * (w * h))
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original

    def test_roundtrip_mixed(self):
        w, h = 10, 5
        pixels = bytearray(w * h)
        for i in range(len(pixels)):
            pixels[i] = 0 if (i % 3 == 0) else 0xAA
        original = bytes(pixels)
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original

    def test_roundtrip_single_row(self):
        w, h = 100, 1
        original = bytes([i % 256 for i in range(w)])
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original

    def test_roundtrip_single_column(self):
        w, h = 1, 100
        original = bytes([i % 256 for i in range(w * h)])
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original

    def test_roundtrip_large_transparent_runs(self):
        w, h = 200, 1
        original = bytes([0x80] * 50 + [0] * 100 + [0x80] * 50)
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original

    def test_roundtrip_frame_from_dc6(self):
        w, h = 8, 8
        data = make_simple_dc6(w, h)
        dc6 = Dc6File(data)
        frame = dc6.frames[0]
        original = bytes(frame.pixels)
        compressed = compress_frame(original, w, h)
        decompressed = self._decompress(compressed, w, h)
        assert bytes(decompressed) == original


class TestDither:
    def test_dither_output_format(self):
        palette = load_palette("act1")
        img = QImage(4, 4, QImage.Format.Format_RGB32)
        for y in range(4):
            for x in range(4):
                c = QColor(x * 60, y * 60, 128)
                img.setPixel(x, y, c.rgb())
        dithered = floyd_steinberg_dither(img, palette, 100.0)
        assert dithered.width() == 4
        assert dithered.height() == 4
        assert dithered.format() == QImage.Format.Format_Indexed8
        assert dithered.colorCount() == 256

    def test_dither_zero_ratio(self):
        palette = load_palette("act1")
        img = QImage(8, 8, QImage.Format.Format_RGB32)
        img.fill(QColor(128, 128, 128).rgb())
        dithered = floyd_steinberg_dither(img, palette, 0.0)
        assert dithered.width() == 8
        assert dithered.height() == 8
        for y in range(8):
            for x in range(8):
                idx = dithered.pixelIndex(x, y)
                assert 0 <= idx <= 224  # 导入量化的索引上限，见 dc6/model.py

    def test_dither_full_ratio(self):
        palette = load_palette("act1")
        img = QImage(8, 8, QImage.Format.Format_RGB32)
        img.fill(QColor(128, 128, 128).rgb())
        dithered = floyd_steinberg_dither(img, palette, 100.0)
        assert dithered.format() == QImage.Format.Format_Indexed8

    def test_dither_max_ratio(self):
        palette = load_palette("act1")
        img = QImage(4, 4, QImage.Format.Format_RGB32)
        for y in range(4):
            for x in range(4):
                img.setPixel(x, y, QColor(x * 60, y * 60, 128).rgb())
        dithered = floyd_steinberg_dither(img, palette, 200.0)
        assert dithered.width() == 4
        assert dithered.height() == 4

    @pytest.mark.parametrize("ratio", [0.0, 100.0, 200.0])
    @pytest.mark.parametrize("preserve_black", [False, True])
    def test_dither_never_exceeds_224(self, ratio: float, preserve_black: bool):
        """白色底最容易诱出 255 号，任何误差比例/开关组合都不能越界。"""
        img = QImage(8, 8, QImage.Format.Format_ARGB32)
        img.fill(QColor(255, 255, 255, 255).rgba())
        dithered = floyd_steinberg_dither(
            img, load_palette("act1"), ratio, preserve_black=preserve_black
        )
        indices = [dithered.pixelIndex(x, y) for y in range(8) for x in range(8)]
        assert max(indices) <= 224


class TestSplitImageTiles:
    def test_split_small_image(self):
        img = QImage(100, 100, QImage.Format.Format_Indexed8)
        img.setColorCount(256)
        img.fill(0)
        tiles = split_image_tiles(img, 4)
        assert len(tiles) == 1
        assert tiles[0][2] == 100
        assert tiles[0][3] == 100

    def test_split_large_image(self):
        img = QImage(512, 256, QImage.Format.Format_Indexed8)
        img.setColorCount(256)
        img.fill(0)
        tiles = split_image_tiles(img, 2)
        assert len(tiles) == 2
        assert tiles[0][:4] == (0, 0, 256, 256)
        assert tiles[1][:4] == (256, 0, 256, 256)

    def test_split_uneven_height(self):
        img = QImage(512, 384, QImage.Format.Format_Indexed8)
        img.setColorCount(256)
        img.fill(0)
        tiles = split_image_tiles(img, 2)
        assert len(tiles) == 4
        assert tiles[0][:4] == (0, 0, 256, 256)
        assert tiles[1][:4] == (256, 0, 256, 256)
        assert tiles[2][:4] == (0, 256, 256, 128)
        assert tiles[3][:4] == (256, 256, 256, 128)

    def test_split_1024x384_fpr2(self):
        img = QImage(1024, 384, QImage.Format.Format_Indexed8)
        img.setColorCount(256)
        img.fill(0)
        tiles = split_image_tiles(img, 2)
        assert len(tiles) == 8
        expected = [
            (0, 0, 256, 256),
            (256, 0, 256, 256),
            (0, 256, 256, 128),
            (256, 256, 256, 128),
            (512, 0, 256, 256),
            (768, 0, 256, 256),
            (512, 256, 256, 128),
            (768, 256, 256, 128),
        ]
        for i, (ex, ey, ew, eh) in enumerate(expected):
            assert tiles[i][:4] == (ex, ey, ew, eh), f"Tile {i}: expected {(ex, ey, ew, eh)}, got {tiles[i][:4]}"


class TestDc6FileCreation:
    def test_create_blank(self):
        dc6 = Dc6File.__new__(Dc6File)
        dc6.version = 6
        dc6.termination = b"\xee\xee\xee\xee"
        dc6.frames = []
        dc6.palette = b""
        assert dc6.version == 6
        assert dc6.total_frames() == 0

    def test_create_blank_then_add_frame(self):
        dc6 = Dc6File.__new__(Dc6File)
        dc6.version = 6
        dc6.termination = b"\xee\xee\xee\xee"
        dc6.frames = []
        dc6.palette = b""
        assert dc6.total_frames() == 0
        dc6.frames.append(FrameInfo(width=8, height=8, pixels=bytearray(64)))
        assert dc6.total_frames() == 1


class TestDc6FileDeleteFrame:
    def test_delete_frame(self):
        data = make_simple_dc6(8, 8)
        dc6 = Dc6File(data)
        assert dc6.total_frames() == 1
        dc6.frames.append(FrameInfo(width=4, height=4, pixels=bytearray(16)))
        assert dc6.total_frames() == 2
        del dc6.frames[0]
        assert dc6.total_frames() == 1
        assert dc6.frames[0].width == 4

    def test_delete_all_frames(self):
        f1 = FrameInfo(width=8, height=8, pixels=bytearray(64))
        f2 = FrameInfo(width=4, height=4, pixels=bytearray(16))
        dc6 = Dc6File.__new__(Dc6File)
        dc6.version = 6
        dc6.termination = b"\xee\xee\xee\xee"
        dc6.frames = [f1, f2]
        dc6.palette = b""
        del dc6.frames[0]
        assert dc6.total_frames() == 1
        del dc6.frames[0]
        assert dc6.total_frames() == 0
