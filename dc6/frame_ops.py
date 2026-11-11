"""DC6 帧编辑操作（纯逻辑，无 QWidget 依赖）。

界面层只负责取参数 / 弹提示，所有对 Dc6File 的修改都收敛到这里，
便于单元测试覆盖插入、删除、替换、偏移、导出与图片拆帧。
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtGui import QImage

from dc6.model import (
    MAX_FRAME_SIZE,
    OFFSET_MAX,
    OFFSET_MIN,
    Dc6File,
    FrameInfo,
    floyd_steinberg_dither,
    make_bmp_data,
    split_image_tiles,
)


class FrameSizeError(ValueError):
    """帧尺寸非法，或替换时收到超过单帧上限的图片。"""


class FrameIndexError(IndexError):
    """帧下标越界。"""


def validate_frame_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise FrameSizeError(f"无效的图像尺寸: {width}x{height}")
    if width > MAX_FRAME_SIZE or height > MAX_FRAME_SIZE:
        raise FrameSizeError(f"图像尺寸 {width}x{height} 超过单帧上限 {MAX_FRAME_SIZE}x{MAX_FRAME_SIZE}")


def validate_offset(value: int) -> int:
    if not OFFSET_MIN <= value <= OFFSET_MAX:
        raise ValueError(f"偏移量 {value} 超出有符号 32 位范围 [{OFFSET_MIN}, {OFFSET_MAX}]")
    return value


def _check_index(dc6: Dc6File, index: int) -> None:
    if index < 0 or index >= dc6.total_frames():
        raise FrameIndexError(f"帧下标越界: {index} (共 {dc6.total_frames()} 帧)")


def insert_frames(dc6: Dc6File, position: int, frames: list[FrameInfo]) -> int:
    """在 position 处批量插入帧，返回实际插入位置（越界会被夹到合法范围）。"""
    if not frames:
        raise FrameSizeError("没有可插入的帧")
    for frame in frames:
        validate_frame_size(frame.width, frame.height)
    pos = max(0, min(int(position), dc6.total_frames()))
    dc6.frames[pos:pos] = list(frames)
    return pos


def delete_frame(dc6: Dc6File, index: int) -> FrameInfo:
    """删除第 index 帧并返回被删除的帧。"""
    _check_index(dc6, index)
    return dc6.frames.pop(index)


def replace_frame(dc6: Dc6File, index: int, frame: FrameInfo, keep_offset: bool = True) -> None:
    """替换第 index 帧的图像内容。

    偏移属于帧在动画里的摆放信息，默认沿用旧帧的 X/Y 偏移，替换图片不改变它。
    """
    _check_index(dc6, index)
    validate_frame_size(frame.width, frame.height)
    if keep_offset:
        old = dc6.frames[index]
        frame.x_offset = old.x_offset
        frame.y_offset = old.y_offset
    dc6.frames[index] = frame


def set_frame_offset(dc6: Dc6File, index: int, x: int | None = None, y: int | None = None) -> None:
    """修改某一帧的 X / Y 偏移；传 None 表示该轴保持不变。"""
    _check_index(dc6, index)
    frame = dc6.frames[index]
    if x is not None:
        frame.x_offset = validate_offset(int(x))
    if y is not None:
        frame.y_offset = validate_offset(int(y))


def frames_from_tiles(tiles: list[tuple[int, int, int, int, QImage]]) -> list[FrameInfo]:
    """把 Indexed8 的切片转成 DC6 帧（像素按行拷贝成调色板索引）。"""
    frames: list[FrameInfo] = []
    for _tx, _ty, tw, th, tile_img in tiles:
        pixels = bytearray(tw * th)
        for y in range(th):
            scan = cast("memoryview", tile_img.constScanLine(y))
            pixels[y * tw : (y + 1) * tw] = scan[:tw]
        frames.append(FrameInfo(width=tw, height=th, pixels=pixels))
    return frames


def frames_from_image(
    img: QImage,
    palette: bytes,
    ratio: float = 100.0,
    frames_per_row: int = 1,
    *,
    allow_split: bool = True,
) -> list[FrameInfo]:
    """把一张普通图片转成一帧或多帧 DC6 数据。

    - allow_split=True：宽或高超过 256 时按 frames_per_row 拆成多帧。
    - allow_split=False：用于「替换」，超过 256 直接抛 FrameSizeError。
    """
    if img.isNull():
        raise FrameSizeError("无法读取图片")
    need_split = img.width() > MAX_FRAME_SIZE or img.height() > MAX_FRAME_SIZE
    if need_split and not allow_split:
        raise FrameSizeError(
            f"替换只支持不超过 {MAX_FRAME_SIZE}x{MAX_FRAME_SIZE} 的图片，"
            f"当前图片为 {img.width()}x{img.height()}"
        )

    dithered = floyd_steinberg_dither(img, palette, ratio)
    if need_split:
        tiles = split_image_tiles(dithered, frames_per_row)
    else:
        tiles = [(0, 0, dithered.width(), dithered.height(), dithered)]

    frames = frames_from_tiles(tiles)
    for frame in frames:
        validate_frame_size(frame.width, frame.height)
    return frames


def frame_export_name(source_path: str | None, index: int) -> str:
    """右键「导出此帧」时的默认文件名。"""
    stem = Path(source_path).stem if source_path else "frame"
    return f"{stem}-{index:03d}.bmp"


def export_frame(dc6: Dc6File, index: int, path: str | Path) -> Path:
    """导出单帧到文件：BMP 走索引色编码，其它格式走带透明的 ARGB 图像。"""
    _check_index(dc6, index)
    dest = Path(path)
    if dest.suffix.lower() == ".bmp":
        dest.write_bytes(make_bmp_data(dc6.frames[index], dc6.palette))
    else:
        image = dc6.get_argb_image(index)
        if not image.save(str(dest)):
            raise OSError(f"无法写入图片: {dest}")
    return dest
