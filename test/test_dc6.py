from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dc6.dc6_viewer import Dc6File, load_palette, list_palettes, compress_frame  # noqa: E402


def make_simple_dc6(width: int, height: int, pixels: bytes | None = None) -> bytes:
    """Build a minimal valid DC6 file with 1 frame."""
    if pixels is None:
        pixels = bytes([0] * width * height)

    compressed = compress_frame(pixels, width, height)
    header = struct.pack("<IIIIIIII", 0, width, height, 0, 0, 0, 0, 0)
    block_data = header + compressed + b"\xee\xee\xee"

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
        with pytest.raises(Exception):
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

        out = io.BytesIO()
        out.write(struct.pack("<III", dc6.version, 1, 0))
        out.write(dc6.termination[:4].ljust(4, b"\xee"))
        out.write(struct.pack("<II", 1, dc6.total_frames()))

        pointer_table_offset = out.tell()
        for _ in range(dc6.total_frames()):
            out.write(struct.pack("<I", 0))

        pointers: list[int] = []
        for i in range(dc6.total_frames()):
            pointers.append(out.tell())
            frame = dc6.frames[i]
            block_start = out.tell()
            out.write(struct.pack("<IIIIIIII", 0, frame.width, frame.height, 0, 0, 0, 0, 0))
            data_start = out.tell()
            compressed = compress_frame(bytes(frame.pixels), frame.width, frame.height)
            out.write(compressed)
            data_end = out.tell()
            out.write(b"\xee\xee\xee")
            block_end = out.tell()
            out.seek(block_start + 24)
            out.write(struct.pack("<II", block_end, data_end - data_start))
            out.seek(block_end)

        out.seek(pointer_table_offset)
        for p in pointers:
            out.write(struct.pack("<I", p))

        written = out.getvalue()
        dc6_2 = Dc6File(written)
        assert dc6_2.total_frames() == 1
        assert dc6_2.frames[0].width == w
        assert dc6_2.frames[0].height == h
        assert dc6_2.version == 6


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
