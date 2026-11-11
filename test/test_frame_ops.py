"""DC6 帧编辑能力的单元测试。

覆盖：插入（含大图自动拆帧）、删除、替换（含超尺寸报错）、
X/Y 偏移修改、图片转帧、单帧导出，以及模型层的 BMP 编码与拆分方案。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtGui import QColor, QImage

from dc6.frame_ops import (
    FrameIndexError,
    FrameSizeError,
    delete_frame,
    export_frame,
    frame_export_name,
    frames_from_image,
    frames_from_tiles,
    insert_frames,
    replace_frame,
    set_frame_offset,
    validate_frame_size,
    validate_offset,
)
from dc6.model import (
    MAX_FRAME_SIZE,
    OFFSET_MAX,
    OFFSET_MIN,
    Dc6File,
    FrameInfo,
    build_dc6_bytes,
    load_palette,
    make_bmp_data,
    nearest_palette_index,
    plan_tiles,
)


def make_frame(width: int = 8, height: int = 8, fill: int = 1, x: int = 0, y: int = 0) -> FrameInfo:
    return FrameInfo(
        width=width,
        height=height,
        x_offset=x,
        y_offset=y,
        pixels=bytearray([fill] * (width * height)),
    )


def blank_dc6() -> Dc6File:
    dc6 = Dc6File.blank()
    dc6.palette = load_palette("act1")
    return dc6


def rgb_image(width: int, height: int, color: tuple[int, int, int] = (100, 80, 60)) -> QImage:
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(QColor(*color).rgb())
    return img


# ---------------------------------------------------------------------------
# 插入
# ---------------------------------------------------------------------------
class TestInsertFrames:
    def test_insert_into_empty(self):
        dc6 = blank_dc6()
        assert insert_frames(dc6, 0, [make_frame()]) == 0
        assert dc6.total_frames() == 1
        assert dc6.frames[0].width == 8

    def test_insert_before_middle_and_end(self):
        dc6 = blank_dc6()
        first = make_frame(4, 4, 1)
        middle = make_frame(5, 5, 2)
        last = make_frame(6, 6, 3)
        insert_frames(dc6, 0, [first])
        insert_frames(dc6, 0, [middle])
        insert_frames(dc6, 2, [last])
        assert [f.width for f in dc6.frames] == [5, 4, 6]

    def test_insert_multiple_frames_keeps_order(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame(2, 2), make_frame(3, 3)])
        insert_frames(dc6, 1, [make_frame(4, 4), make_frame(5, 5)])
        assert [f.width for f in dc6.frames] == [2, 4, 5, 3]

    def test_clamps_out_of_range_position(self):
        dc6 = blank_dc6()
        insert_frames(dc6, -99, [make_frame(2, 2)])
        insert_frames(dc6, 99, [make_frame(3, 3)])
        assert [f.width for f in dc6.frames] == [2, 3]

    def test_rejects_empty_frame_list(self):
        with pytest.raises(FrameSizeError, match="没有可插入的帧"):
            insert_frames(blank_dc6(), 0, [])

    def test_rejects_oversized_frame(self):
        with pytest.raises(FrameSizeError, match="超过单帧上限"):
            insert_frames(blank_dc6(), 0, [make_frame(MAX_FRAME_SIZE + 1, 10)])

    def test_large_image_becomes_multiple_frames(self):
        dc6 = blank_dc6()
        frames = frames_from_image(rgb_image(512, 300), dc6.palette, 100.0, 2)
        assert [(f.width, f.height) for f in frames] == [
            (256, 256),
            (256, 256),
            (256, 44),
            (256, 44),
        ]
        pos = insert_frames(dc6, 0, frames)
        assert pos == 0
        assert dc6.total_frames() == len(frames)
        assert all(f.width <= MAX_FRAME_SIZE and f.height <= MAX_FRAME_SIZE for f in dc6.frames)

    def test_inserted_frames_survive_dc6_roundtrip(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame(7, 5, 9, x=-3, y=42), make_frame(3, 3, 2)])
        restored = Dc6File(build_dc6_bytes(dc6))
        assert restored.total_frames() == 2
        assert (restored.frames[0].width, restored.frames[0].height) == (7, 5)
        assert (restored.frames[0].x_offset, restored.frames[0].y_offset) == (-3, 42)
        assert bytes(restored.frames[0].pixels) == bytes(dc6.frames[0].pixels)


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------
class TestDeleteFrame:
    def test_delete_returns_removed_frame(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame(4, 4), make_frame(6, 6)])
        removed = delete_frame(dc6, 0)
        assert removed.width == 4
        assert [f.width for f in dc6.frames] == [6]

    def test_delete_last_frame(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame()])
        delete_frame(dc6, 0)
        assert dc6.total_frames() == 0

    @pytest.mark.parametrize("index", [-1, 1, 99])
    def test_delete_invalid_index(self, index: int):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame()])
        with pytest.raises(FrameIndexError):
            delete_frame(dc6, index)


# ---------------------------------------------------------------------------
# 替换
# ---------------------------------------------------------------------------
class TestReplaceFrame:
    def test_keeps_existing_offsets(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame(8, 8, 1, x=-17, y=33)])
        replace_frame(dc6, 0, make_frame(16, 12, 7))
        assert (dc6.frames[0].width, dc6.frames[0].height) == (16, 12)
        assert (dc6.frames[0].x_offset, dc6.frames[0].y_offset) == (-17, 33)

    def test_can_discard_existing_offsets(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame(8, 8, 1, x=-17, y=33)])
        replace_frame(dc6, 0, make_frame(4, 4, 7, x=1, y=2), keep_offset=False)
        assert (dc6.frames[0].x_offset, dc6.frames[0].y_offset) == (1, 2)

    def test_replace_invalid_index(self):
        with pytest.raises(FrameIndexError):
            replace_frame(blank_dc6(), 0, make_frame())

    def test_replace_rejects_oversized_frame(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame()])
        with pytest.raises(FrameSizeError, match="超过单帧上限"):
            replace_frame(dc6, 0, make_frame(10, MAX_FRAME_SIZE + 1))

    def test_replace_mode_rejects_large_source_image(self):
        img = rgb_image(MAX_FRAME_SIZE + 1, 10)
        with pytest.raises(FrameSizeError, match="替换只支持"):
            frames_from_image(img, load_palette("act1"), allow_split=False)
        with pytest.raises(FrameSizeError, match="替换只支持"):
            frames_from_image(rgb_image(10, MAX_FRAME_SIZE + 1), load_palette("act1"), allow_split=False)

    def test_replace_accepts_boundary_size(self):
        frames = frames_from_image(rgb_image(256, 256), load_palette("act1"), allow_split=False)
        assert len(frames) == 1
        assert (frames[0].width, frames[0].height) == (256, 256)


# ---------------------------------------------------------------------------
# 偏移
# ---------------------------------------------------------------------------
class TestFrameOffsets:
    def test_set_negative_offsets(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame()])
        set_frame_offset(dc6, 0, x=-123, y=-456)
        assert (dc6.frames[0].x_offset, dc6.frames[0].y_offset) == (-123, -456)

    def test_partial_update(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame(x=7, y=9)])
        set_frame_offset(dc6, 0, x=-1)
        assert (dc6.frames[0].x_offset, dc6.frames[0].y_offset) == (-1, 9)
        set_frame_offset(dc6, 0, y=11)
        assert (dc6.frames[0].x_offset, dc6.frames[0].y_offset) == (-1, 11)

    @pytest.mark.parametrize("value", [OFFSET_MIN, OFFSET_MAX, 0, -2])
    def test_validate_offset_bounds(self, value: int):
        assert validate_offset(value) == value

    @pytest.mark.parametrize("value", [OFFSET_MIN - 1, OFFSET_MAX + 1])
    def test_validate_offset_rejects_out_of_range(self, value: int):
        with pytest.raises(ValueError, match="超出有符号 32 位范围"):
            validate_offset(value)

    def test_offset_survives_dc6_roundtrip(self):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [make_frame()])
        set_frame_offset(dc6, 0, x=-2, y=24)
        restored = Dc6File(build_dc6_bytes(dc6))
        assert (restored.frames[0].x_offset, restored.frames[0].y_offset) == (-2, 24)


# ---------------------------------------------------------------------------
# 图片 → 帧
# ---------------------------------------------------------------------------
class TestImageConversion:
    def test_small_image_is_single_frame(self):
        frames = frames_from_image(rgb_image(30, 20), load_palette("act1"))
        assert len(frames) == 1
        assert (frames[0].width, frames[0].height) == (30, 20)
        assert len(frames[0].pixels) == 600
        assert all(0 <= p < 256 for p in frames[0].pixels)

    def test_split_plan_matches_design_example(self):
        assert plan_tiles(1024, 384, 2) == [
            (0, 0, 256, 256),
            (256, 0, 256, 256),
            (0, 256, 256, 128),
            (256, 256, 256, 128),
            (512, 0, 256, 256),
            (768, 0, 256, 256),
            (512, 256, 256, 128),
            (768, 256, 256, 128),
        ]

    def test_frames_from_tiles_copies_pixels(self):
        tile = QImage(3, 2, QImage.Format.Format_Indexed8)
        tile.setColorCount(256)
        tile.fill(0)
        for x in range(3):
            tile.setPixel(x, 0, x + 1)
            tile.setPixel(x, 1, x + 10)
        frames = frames_from_tiles([(0, 0, 3, 2, tile)])
        assert list(frames[0].pixels) == [1, 2, 3, 10, 11, 12]

    def test_null_image_rejected(self):
        with pytest.raises(FrameSizeError, match="无法读取图片"):
            frames_from_image(QImage(), load_palette("act1"))

    @pytest.mark.parametrize(
        "width,height",
        [(0, 10), (10, 0), (257, 10), (10, 257)],
    )
    def test_validate_frame_size(self, width: int, height: int):
        with pytest.raises(FrameSizeError):
            validate_frame_size(width, height)

    def test_nearest_palette_index_matches_naive_search(self):
        palette = load_palette("act1")
        for r, g, b in [(0, 0, 0), (255, 255, 255), (100, 80, 60)]:
            best_idx, best_dist = 0, 3 * 256 * 256
            for i in range(256):
                pr, pg, pb = palette[i * 3 + 2], palette[i * 3 + 1], palette[i * 3]
                dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
                if dist < best_dist:
                    best_dist, best_idx = dist, i
            assert nearest_palette_index(r, g, b, palette) == best_idx


# ---------------------------------------------------------------------------
# 单帧导出
# ---------------------------------------------------------------------------
class TestExportFrame:
    @staticmethod
    def _frame() -> FrameInfo:
        pixels = bytearray(range(1, 16))
        return FrameInfo(width=5, height=3, pixels=pixels)

    def test_bmp_header_palette_and_bottom_up_rows(self, tmp_path: Path):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [self._frame()])
        dest = export_frame(dc6, 0, tmp_path / "frame.bmp")
        data = dest.read_bytes()

        row_size = ((5 * 8 + 31) // 32) * 4  # 8，含 3 字节行填充
        pixel_offset = 14 + 40 + 256 * 4
        assert len(data) == pixel_offset + row_size * 3

        signature, file_size, _r1, _r2, offset = struct.unpack_from("<2sIHHI", data, 0)
        assert signature == b"BM"
        assert file_size == len(data)
        assert offset == pixel_offset

        info = struct.unpack_from("<IiiHHIIiiII", data, 14)
        assert info[0] == 40
        assert (info[1], info[2], info[4]) == (5, 3, 8)
        assert info[7] == 0  # biXPelsPerMeter
        assert info[6] == row_size * 3  # biSizeImage
        assert info[9] == 256  # biClrUsed

        # act1.pal 第 0 项黑色、第 1 项 BGR=(0,0,36)
        assert data[54 : 54 + 4] == b"\x00\x00\x00\x00"
        assert data[58 : 58 + 4] == b"\x00\x00\x24\x00"

        # BMP 行自下而上；最后一行是源图 y=2
        bottom = pixel_offset
        assert data[bottom : bottom + 5] == bytes(dc6.frames[0].pixels[10:15])
        assert data[bottom + 5 : bottom + row_size] == b"\x00" * (row_size - 5)

    def test_bmp_matches_make_bmp_data(self, tmp_path: Path):
        dc6 = blank_dc6()
        insert_frames(dc6, 0, [self._frame()])
        assert export_frame(dc6, 0, tmp_path / "a.bmp").read_bytes() == make_bmp_data(
            dc6.frames[0], dc6.palette
        )

    def test_png_export_keeps_transparent_index_zero(self, tmp_path: Path):
        dc6 = blank_dc6()
        frame = FrameInfo(width=2, height=1, pixels=bytearray([0, 1]))
        insert_frames(dc6, 0, [frame])
        dest = export_frame(dc6, 0, tmp_path / "frame.png")
        assert dest.is_file()

        loaded = QImage(str(dest))
        assert not loaded.isNull()
        # QColor(int) 会忽略 alpha，这里直接读 QRgb 的高 8 位。
        assert (loaded.pixel(0, 0) >> 24) & 0xFF == 0
        assert (loaded.pixel(1, 0) >> 24) & 0xFF == 255

    def test_bmp_rejects_malformed_frame(self):
        with pytest.raises(ValueError, match="帧像素数据不足"):
            make_bmp_data(FrameInfo(width=8, height=8, pixels=bytearray(3)), load_palette("act1"))

    def test_invalid_index(self, tmp_path: Path):
        with pytest.raises(FrameIndexError):
            export_frame(blank_dc6(), 0, tmp_path / "x.bmp")

    @pytest.mark.parametrize(
        "source,index,expected",
        [(None, 3, "frame-003.bmp"), ("data/items/arrow.dc6", 12, "arrow-012.bmp")],
    )
    def test_default_export_name(self, source: str | None, index: int, expected: str):
        assert frame_export_name(source, index) == expected


# ---------------------------------------------------------------------------
# 兼容旧 import 路径
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    def test_dc6_viewer_still_reexports_model_api(self):
        import dc6.dc6_viewer as viewer

        assert viewer.Dc6File is Dc6File
        assert viewer.FrameInfo is FrameInfo
        assert viewer.build_dc6_bytes is build_dc6_bytes
        assert viewer.load_palette is load_palette
        assert viewer.split_image_tiles is not None
        assert viewer.compress_frame is not None
        assert viewer.floyd_steinberg_dither is not None
