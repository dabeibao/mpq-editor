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
    COMMON_COLOR_INDICES,
    COMMON_COLOR_LAST,
    MAX_FRAME_SIZE,
    OFFSET_MAX,
    OFFSET_MIN,
    PALETTE_ENTRIES,
    TRANSPARENT_INDEX,
    Dc6File,
    FrameInfo,
    build_dc6_bytes,
    list_palettes,
    load_palette,
    make_bmp_data,
    nearest_palette_index,
    palette_rgb,
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
            for i in range(COMMON_COLOR_LAST + 1):
                pr, pg, pb = palette[i * 3 + 2], palette[i * 3 + 1], palette[i * 3]
                dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
                if dist < best_dist:
                    best_dist, best_idx = dist, i
            assert nearest_palette_index(r, g, b, palette) == best_idx


# ---------------------------------------------------------------------------
# 调色板颜色范围：所有导入分支的索引都不超过 224
# ---------------------------------------------------------------------------
def _color_distance(palette: bytes, index: int, r: int, g: int, b: int) -> int:
    pr, pg, pb = palette_rgb(palette, index)
    return (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2


class TestPaletteRange:
    def test_bundled_palettes_have_256_entries(self):
        for name in list_palettes():
            assert len(load_palette(name)) == PALETTE_ENTRIES * 3

    def test_common_colors_are_exactly_224(self):
        assert len(COMMON_COLOR_INDICES) == 224
        assert COMMON_COLOR_INDICES[0] == 1
        assert COMMON_COLOR_INDICES[-1] == 224
        assert TRANSPARENT_INDEX not in COMMON_COLOR_INDICES

    def test_units_palette_leaves_act_colors_unused(self):
        units = load_palette("units")
        assert all(palette_rgb(units, i) == (0, 0, 0) for i in range(225, 255))
        assert palette_rgb(units, 255) == (255, 255, 255)  # 文件里有白，但导入不用
        # 同样的索引在 Act 调色板里是彩色，故不能用于跨 Act 的图标。
        act1 = load_palette("act1")
        assert any(palette_rgb(act1, i) != (0, 0, 0) for i in range(225, 255))

    def test_quantization_never_exceeds_224(self):
        """每个内置调色板、每种开关组合、每种颜色都不得返回 >224 的索引。"""
        colors = ((0, 0, 0), (255, 255, 255), (128, 64, 32), (200, 10, 240), (8, 20, 16))
        for name in list_palettes():
            palette = load_palette(name)
            for r, g, b in colors:
                for skip in (False, True):
                    idx = nearest_palette_index(r, g, b, palette, skip_transparent=skip)
                    assert idx <= COMMON_COLOR_LAST
                    assert idx >= (1 if skip else 0)

    def test_quantization_still_picks_nearest_allowed_color(self):
        palette = load_palette("act1")
        for skip in (False, True):
            allowed = range(1 if skip else 0, COMMON_COLOR_LAST + 1)
            for r, g, b in ((0, 0, 0), (255, 255, 255), (100, 80, 60), (8, 20, 16)):
                best = min(allowed, key=lambda i, r=r, g=g, b=b: _color_distance(palette, i, r, g, b))
                assert nearest_palette_index(r, g, b, palette, skip_transparent=skip) == best

    def test_white_uses_common_color_not_255(self):
        palette = load_palette("act1")
        idx = nearest_palette_index(255, 255, 255, palette)
        assert idx <= COMMON_COLOR_LAST
        assert idx == min(COMMON_COLOR_INDICES, key=lambda i: _color_distance(palette, i, 255, 255, 255))

    def test_quantization_handles_short_palette(self):
        assert nearest_palette_index(0, 0, 0, bytes([0, 0, 0])) == 0
        short = bytes([0, 0, 0] * 10)
        assert nearest_palette_index(0, 0, 0, short, skip_transparent=True) in range(1, 10)

    @pytest.mark.parametrize("palette_name", ["units", "act1", "loading", "menu2"])
    @pytest.mark.parametrize("preserve_black", [False, True])
    def test_insert_and_split_branches_stay_within_224(self, palette_name: str, preserve_black: bool):
        """白色底最能诱使量化落到 255；插入 + 大图拆帧也必须 <=224。"""
        img = QImage(300, 20, QImage.Format.Format_ARGB32)
        img.fill(QColor(255, 255, 255, 255).rgba())
        for x in range(0, 300, 3):
            img.setPixel(x, 0, QColor(0, 0, 0, 0).rgba())
        frames = frames_from_image(
            img, load_palette(palette_name), 100.0, 2, preserve_black=preserve_black
        )
        assert len(frames) == 2
        assert all(max(f.pixels) <= COMMON_COLOR_LAST for f in frames)

    @pytest.mark.parametrize("preserve_black", [False, True])
    def test_replace_branch_stays_within_224(self, preserve_black: bool):
        img = QImage(32, 32, QImage.Format.Format_ARGB32)
        img.fill(QColor(255, 255, 255, 255).rgba())
        frames = frames_from_image(
            img, load_palette("act1"), allow_split=False, preserve_black=preserve_black
        )
        assert max(frames[0].pixels) <= COMMON_COLOR_LAST

    def test_rgb32_default_branch_stays_within_224(self):
        """没有 alpha 通道的普通 RGB 图片（默认路径）同样受限。"""
        pixels = frames_from_image(rgb_image(64, 8, (250, 250, 250)), load_palette("act1"))[0].pixels
        assert max(pixels) <= COMMON_COLOR_LAST

    def test_out_of_range_indices_are_rejected(self, monkeypatch: pytest.MonkeyPatch):
        """即使量化层被替换/放宽，也不允许产出含 >224 索引的帧。"""
        bad = QImage(2, 1, QImage.Format.Format_Indexed8)
        bad.setColorCount(256)
        bad.fill(255)

        def fake_dither(*_args: object, **_kwargs: object) -> QImage:
            return bad

        monkeypatch.setattr("dc6.frame_ops.floyd_steinberg_dither", fake_dither)
        with pytest.raises(ValueError, match="224"):
            frames_from_image(rgb_image(2, 1, (255, 255, 255)), load_palette("act1"))

    def test_preserve_black_uses_common_color_for_black_icon(self):
        img = QImage(2, 1, QImage.Format.Format_ARGB32)
        img.setPixel(0, 0, QColor(0, 0, 0, 0).rgba())
        img.setPixel(1, 0, QColor(0, 0, 0, 255).rgba())
        pixels = frames_from_image(img, load_palette("units"), preserve_black=True)[0].pixels
        assert pixels[0] == TRANSPARENT_INDEX
        assert pixels[1] in COMMON_COLOR_INDICES

    def test_preserve_black_avoids_act_colors_with_dithering(self):
        img = QImage(16, 16, QImage.Format.Format_ARGB32)
        for y in range(16):
            for x in range(16):
                img.setPixel(x, y, QColor(x * 16, y * 16, (x + y) * 8).rgba())
        pixels = frames_from_image(img, load_palette("act1"), preserve_black=True)[0].pixels
        assert all(p in COMMON_COLOR_INDICES for p in pixels)


# ---------------------------------------------------------------------------
# 「保留黑色」导入选项（仅 alpha=0 的像素使用 0 号透明色）
# ---------------------------------------------------------------------------
class TestPreserveBlack:
    @staticmethod
    def _icon() -> QImage:
        """一行四像素：透明黑 / 不透明黑 / 半透明黑 / 不透明白。"""
        img = QImage(4, 1, QImage.Format.Format_ARGB32)
        colors = [
            QColor(0, 0, 0, 0),
            QColor(0, 0, 0, 255),
            QColor(0, 0, 0, 128),
            QColor(255, 255, 255, 255),
        ]
        for x, color in enumerate(colors):
            img.setPixel(x, 0, color.rgba())
        return img

    def test_default_maps_opaque_black_to_pal_zero(self):
        """历史行为：只看 RGB，黑色与透明点都会落到 0 号色。"""
        pixels = frames_from_image(self._icon(), load_palette("act1"))[0].pixels
        assert list(pixels[:3]) == [0, 0, 0]

    def test_preserve_black_keeps_black_visible(self):
        pixels = frames_from_image(self._icon(), load_palette("act1"), preserve_black=True)[0].pixels
        assert pixels[0] == 0  # 完全透明
        assert pixels[1] != 0  # 不透明黑：不再被挖空
        assert pixels[2] != 0  # 半透明按可见处理
        assert pixels[3] != 0  # 白色

    def test_transparent_pixel_is_zero_even_with_white_rgb(self):
        img = QImage(2, 1, QImage.Format.Format_ARGB32)
        img.setPixel(0, 0, QColor(255, 255, 255, 0).rgba())
        img.setPixel(1, 0, QColor(255, 255, 255, 255).rgba())
        palette = load_palette("act1")

        default = frames_from_image(img, palette)[0].pixels
        preserved = frames_from_image(img, palette, preserve_black=True)[0].pixels

        assert default[0] != 0  # 默认忽略 alpha，透明白点被当成白色
        assert preserved[0] == 0
        assert preserved[1] != 0

    def test_rgb_image_without_alpha_keeps_black_visible(self):
        img = rgb_image(3, 1, (0, 0, 0))
        pixels = frames_from_image(img, load_palette("act1"), preserve_black=True)[0].pixels
        assert all(p != 0 for p in pixels)

    def test_option_works_when_splitting_large_image(self):
        img = QImage(300, 2, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 255).rgba())
        for x in range(300):
            if x % 2 == 0:
                img.setPixel(x, 0, QColor(0, 0, 0, 0).rgba())

        frames = frames_from_image(img, load_palette("act1"), 100.0, 2, preserve_black=True)

        assert len(frames) == 2
        for frame in frames:
            top_row = list(frame.pixels[: frame.width])
            assert all(p == 0 for p in top_row[::2])  # 透明点
            assert all(p != 0 for p in top_row[1::2])  # 黑色可见

    def test_option_works_for_replace_mode(self):
        frames = frames_from_image(
            self._icon(), load_palette("act1"), allow_split=False, preserve_black=True
        )
        assert len(frames) == 1
        assert frames[0].pixels[0] == 0
        assert frames[0].pixels[1] != 0

    def test_real_png_icon_keeps_black_and_transparency(self, tmp_path: Path):
        src = QImage(8, 8, QImage.Format.Format_ARGB32)
        src.fill(QColor(0, 0, 0, 0).rgba())  # 透明背景
        for y in range(2, 6):
            for x in range(2, 6):
                src.setPixel(x, y, QColor(0, 0, 0, 255).rgba())  # 黑色图标
        path = tmp_path / "icon.png"
        assert src.save(str(path))

        loaded = QImage(str(path))
        assert loaded.hasAlphaChannel()
        palette = load_palette("act1")

        default = frames_from_image(loaded, palette)[0].pixels
        assert default[0] == 0  # 背景
        assert default[3 * 8 + 3] == 0  # 历史行为：黑色图标被挖空

        preserved = frames_from_image(loaded, palette, preserve_black=True)[0].pixels
        assert preserved[0] == 0
        assert all(preserved[y * 8 + x] != 0 for y in range(2, 6) for x in range(2, 6))

    def test_nearest_palette_index_can_skip_transparent_color(self):
        palette = load_palette("act1")
        assert nearest_palette_index(0, 0, 0, palette) == 0

        idx = nearest_palette_index(0, 0, 0, palette, skip_transparent=True)
        assert idx != 0
        best = min(
            range(1, COMMON_COLOR_LAST + 1),
            key=lambda i: sum(c * c for c in palette_rgb(palette, i)),
        )
        assert idx == best

    def test_skip_transparent_falls_back_for_single_entry_palette(self):
        assert nearest_palette_index(0, 0, 0, bytes([0, 0, 0]), skip_transparent=True) == 0


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
