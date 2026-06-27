from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import override

from PySide6.QtCore import QEvent, QSettings, Qt, QSize, QTimer
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QStyle,
)

DC6_SIGNATURE = 6
PAL_DIR = Path(__file__).parent / "pal"


@dataclass
class FrameInfo:
    width: int
    height: int
    x_offset: int = 0
    y_offset: int = 0
    pixels: bytearray = field(default_factory=bytearray)


class Dc6File:
    def __init__(self, data: bytes):
        self.version: int = 0
        self.termination: bytes = b""
        self.frames: list[FrameInfo] = []
        self.palette: bytes = b""
        self._parse(data)

    def _parse(self, data: bytes):
        (
            self.version,
            unknown01,
            unknown02,
        ) = struct.unpack_from("<III", data, 0)
        if self.version != DC6_SIGNATURE:
            raise ValueError(f"Not a DC6 file (version={self.version})")

        term = struct.unpack_from("4B", data, 12)
        self.termination = bytes(term)

        (unknown03, blockcount) = struct.unpack_from("<II", data, 16)

        if blockcount < 1 or blockcount > 1024:
            raise ValueError(f"Invalid block count: {blockcount}")

        pointers = struct.unpack_from(f"<{blockcount}I", data, 24)

        self.frames = []
        for i in range(blockcount):
            offset = pointers[i]
            (
                bh_unknown01,
                width,
                height,
                bh_unknown02,
                bh_unknown03,
                bh_unknown04,
                next_block,
                length,
            ) = struct.unpack_from("<IIIIIIII", data, offset)
            raw = data[offset + 32 : offset + 32 + length]
            pixels = self._decompress(raw, width, height)
            self.frames.append(FrameInfo(width=width, height=height, pixels=pixels))

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
        frame = self.frames[frame_index]
        w, h = frame.width, frame.height
        img = QImage(w, h, QImage.Format.Format_Indexed8)
        img.setColorCount(256)
        for i in range(256):
            if i * 3 + 2 < len(self.palette):
                b = self.palette[i * 3]
                g = self.palette[i * 3 + 1]
                r = self.palette[i * 3 + 2]
            else:
                r = g = b = 0
            img.setColor(i, QColor(r, g, b).rgb())
        for y in range(h):
            for x in range(w):
                val = frame.pixels[y * w + x]
                img.setPixel(x, y, val)
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


def make_bmp_data(frame: FrameInfo, palette: bytes) -> bytes:
    w, h = frame.width, frame.height
    row_size = ((w * 8 + 31) // 32) * 4

    bmp_header = bytearray(14 + 40 + row_size * h)
    file_size = 14 + 40 + row_size * h

    struct.pack_into("<2sI4xI", bmp_header, 0, b"BM", file_size, 14 + 40)
    struct.pack_into("<IiiHHIIiiII", bmp_header, 14, 40, w, h, 1, 8, 0, row_size * h, 0, 0, 256, 0)

    for i in range(256):
        off = 14 + 40 + i * 4
        if i * 3 + 2 < len(palette):
            bmp_header[off] = palette[i * 3]
            bmp_header[off + 1] = palette[i * 3 + 1]
            bmp_header[off + 2] = palette[i * 3 + 2]
        bmp_header[off + 3] = 0

    pixel_start = 14 + 40 + 256 * 4
    for y in range(h):
        dst = pixel_start + (h - 1 - y) * row_size
        src_start = y * w
        for x in range(w):
            bmp_header[dst + x] = frame.pixels[src_start + x]

    return bytes(bmp_header)


THUMB_HEIGHT = 40
THUMB_WIDTH = 60


class ThumbnailBar(QScrollArea):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(THUMB_HEIGHT + 34)

        self._container = QWidget()
        self._layout = QHBoxLayout(self._container)
        self._layout.setContentsMargins(4, 2, 4, 2)
        self._layout.setSpacing(6)
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.setWidget(self._container)

    def clear_thumbs(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def add_thumb(self, pixmap: QPixmap, index: int, scale: float, selected: bool = False):
        pw = int(pixmap.width() * scale)
        ph = int(pixmap.height() * scale)
        container = QWidget()
        container.setFixedSize(THUMB_WIDTH + 8, THUMB_HEIGHT + 22)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        img_label = QLabel()
        scaled = pixmap.scaled(
            pw,
            ph,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        img_label.setPixmap(scaled)
        img_label.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if selected:
            img_label.setStyleSheet("border: 2px solid #409EFF;")
        else:
            img_label.setStyleSheet("border: 1px solid #ccc;")

        idx_label = QLabel(str(index))
        idx_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idx_label.setStyleSheet("font-size: 10px; color: gray;")
        idx_label.setFixedWidth(THUMB_WIDTH)

        layout.addWidget(img_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(idx_label, 0, Qt.AlignmentFlag.AlignCenter)

        container.mousePressEvent = lambda _e, idx=index: self._on_thumb_clicked(idx)
        self._layout.addWidget(container)

    thumb_clicked = None

    def _on_thumb_clicked(self, index: int):
        if self.thumb_clicked:
            self.thumb_clicked(index)


class ExportDialog(QDialog):
    def __init__(self, total_frames: int, default_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("导出 BMP")
        layout = QVBoxLayout(self)

        self.start_spin = QSpinBox()
        self.start_spin.setRange(0, total_frames - 1)
        self.start_spin.setValue(0)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, total_frames)
        self.count_spin.setValue(total_frames)

        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, total_frames)
        self.cols_spin.setValue(1)

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, total_frames)
        self.rows_spin.setValue(1)

        def _update_max_rows():
            total_selected = self.count_spin.value()
            max_rows = (total_selected + self.cols_spin.value() - 1) // self.cols_spin.value()
            self.rows_spin.setRange(1, max_rows)

        self.count_spin.valueChanged.connect(_update_max_rows)
        self.cols_spin.valueChanged.connect(_update_max_rows)

        form = QVBoxLayout()
        form.addWidget(QLabel("起始帧:"))
        form.addWidget(self.start_spin)
        form.addWidget(QLabel("帧数:"))
        form.addWidget(self.count_spin)
        form.addWidget(QLabel("每行帧数:"))
        form.addWidget(self.cols_spin)
        form.addWidget(QLabel("行数:"))
        form.addWidget(self.rows_spin)

        self.name_edit = QLineEdit(default_name)
        form.addWidget(QLabel("图片名字:"))
        form.addWidget(self.name_edit)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def settings(self) -> tuple[int, int, int, int, str]:
        return (
            self.start_spin.value(),
            self.count_spin.value(),
            self.cols_spin.value(),
            self.rows_spin.value(),
            self.name_edit.text(),
        )


class ImagePanel(QScrollArea):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._original_pixmap: QPixmap | None = None
        self._zoom = 1.0

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #2d2d2d;")
        self.setWidget(self._image_label)

        self.viewport().installEventFilter(self)

    def set_image(self, pixmap: QPixmap):
        self._original_pixmap = pixmap
        self._update_zoomed()

    def _update_zoomed(self):
        if self._original_pixmap is None:
            return
        w = int(self._original_pixmap.width() * self._zoom)
        h = int(self._original_pixmap.height() * self._zoom)
        scaled = self._original_pixmap.scaled(
            w,
            h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._image_label.resize(scaled.size())

    def zoom_in(self):
        self._zoom = min(10.0, self._zoom * 1.25)
        self._update_zoomed()

    def zoom_out(self):
        self._zoom = max(0.1, self._zoom / 1.25)
        self._update_zoomed()

    def zoom_reset(self):
        self._zoom = 1.0
        self._update_zoomed()

    def zoom_text(self) -> str:
        return f"{int(self._zoom * 100)}%"

    @override
    def eventFilter(self, obj, event):
        if obj == self.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.zoom_in()
                elif delta < 0:
                    self.zoom_out()
                return True
        return super().eventFilter(obj, event)


class Dc6Viewer(QMainWindow):
    def __init__(
        self,
        parent: QWidget | None = None,
        data: bytes | None = None,
        source_path: str | None = None,
        enable_directory: bool = True,
    ):
        super().__init__(parent)
        self._dc6: Dc6File | None = None
        self._source_path = source_path
        self._current_frame = 0
        self._modified = False
        self._settings = QSettings("MPQEditor", "DC6Viewer")
        self.setWindowTitle("DC6 Viewer")
        self.resize(1000, 700)

        self._setup_ui()
        self._setup_actions()
        self._setup_menu()
        self._setup_toolbar()

        if data is not None:
            self.load_data(data, self._source_path)
            if self._source_path:
                file_dir = Path(self._source_path).parent
                if file_dir.is_dir():
                    self._populate_file_list(str(file_dir))

    def _setup_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        self.frame_table = QTableWidget()
        self.frame_table.setColumnCount(4)
        self.frame_table.setHorizontalHeaderLabels(["宽度", "高度", "X偏移", "Y偏移"])
        self.frame_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.frame_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.frame_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hh = self.frame_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.resizeSection(0, 55)
        hh.resizeSection(1, 55)
        hh.resizeSection(2, 55)
        hh.resizeSection(3, 55)
        self.frame_table.currentCellChanged.connect(self._on_frame_selected)
        self.frame_table.setStyleSheet("""
            QTableWidget::item:selected {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """)
        self.frame_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(self._on_file_list_selected)
        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(self.file_list)
        left_splitter.addWidget(self.frame_table)
        left_splitter.setSizes([400, 200])
        left_layout.addWidget(left_splitter, 1)

        palette_layout = QHBoxLayout()
        palette_layout.addWidget(QLabel("调色板:"))
        self.palette_combo = QComboBox()
        self.palette_combo.currentTextChanged.connect(self._on_palette_changed)
        palette_layout.addWidget(self.palette_combo)
        left_layout.addLayout(palette_layout)

        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.image_panel = ImagePanel()
        right_layout.addWidget(self.image_panel, 1)

        self.thumb_bar = ThumbnailBar()
        self.thumb_bar.thumb_clicked = self._select_frame
        right_layout.addWidget(self.thumb_bar)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 740])

        self.setCentralWidget(splitter)

        self.statusBar().showMessage("就绪")

    def _setup_actions(self):
        style = self.style()
        i = QStyle.StandardPixmap

        self.open_act = QAction(style.standardIcon(i.SP_DialogOpenButton), "打开...", self)
        self.open_act.setShortcut(QKeySequence("Ctrl+O"))
        self.open_act.triggered.connect(self._open_file)

        self.open_dir_act = QAction(style.standardIcon(i.SP_DirOpenIcon), "打开目录...", self)
        self.open_dir_act.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.open_dir_act.triggered.connect(self._open_directory)
        self.open_dir_act.setEnabled(True)

        self.close_act = QAction(style.standardIcon(i.SP_DialogCloseButton), "关闭", self)
        self.close_act.triggered.connect(self.close)

        self.save_as_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "另存为...", self)
        self.save_as_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_as_act.triggered.connect(self._save_as)

        self.export_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "导出 BMP...", self)
        self.export_act.setShortcut(QKeySequence("Ctrl+E"))
        self.export_act.triggered.connect(self._export_bmp)

        self.zoom_in_act = QAction(self._make_zoom_icon("+"), "放大", self)
        self.zoom_in_act.setShortcut(QKeySequence("Ctrl+="))
        self.zoom_in_act.triggered.connect(self._zoom_in)

        self.zoom_out_act = QAction(self._make_zoom_icon("-"), "缩小", self)
        self.zoom_out_act.setShortcut(QKeySequence("Ctrl+-"))
        self.zoom_out_act.triggered.connect(self._zoom_out)

        self.zoom_reset_act = QAction(style.standardIcon(i.SP_BrowserReload), "原始大小", self)
        self.zoom_reset_act.setShortcut(QKeySequence("Ctrl+0"))
        self.zoom_reset_act.triggered.connect(self._zoom_reset)

        self._update_actions()

    def _setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        file_menu.addAction(self.open_act)
        file_menu.addAction(self.open_dir_act)
        file_menu.addAction(self.save_as_act)
        file_menu.addSeparator()
        file_menu.addAction(self.export_act)
        file_menu.addSeparator()
        file_menu.addAction(self.close_act)

        view_menu = menubar.addMenu("查看")
        view_menu.addAction(self.zoom_in_act)
        view_menu.addAction(self.zoom_out_act)
        view_menu.addAction(self.zoom_reset_act)

    def _setup_toolbar(self):
        toolbar = self.addToolBar("工具栏")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addAction(self.open_act)
        toolbar.addAction(self.open_dir_act)
        toolbar.addAction(self.save_as_act)
        toolbar.addSeparator()
        toolbar.addAction(self.export_act)
        toolbar.addSeparator()
        toolbar.addAction(self.zoom_in_act)
        toolbar.addAction(self.zoom_out_act)
        toolbar.addAction(self.zoom_reset_act)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet("padding: 0 8px;")
        toolbar.addWidget(self._zoom_label)

    def _update_actions(self):
        has_file = self._dc6 is not None
        self.save_as_act.setEnabled(has_file)
        self.export_act.setEnabled(has_file)
        self.zoom_in_act.setEnabled(has_file)
        self.zoom_out_act.setEnabled(has_file)
        self.zoom_reset_act.setEnabled(has_file)

    def load_data(self, data: bytes, source_path: str | None = None):
        try:
            self._dc6 = Dc6File(data)
            self._source_path = source_path
            self._current_frame = 0
            self._modified = False
            self._populate_palettes()
            self._populate_frame_table()
            self._populate_thumbnails()
            self._show_frame(0)
            self._update_actions()
            name = Path(source_path).name if source_path else "untitled"
            self.setWindowTitle(f"DC6 Viewer - {name}")
            self.statusBar().showMessage(f"已加载: {name} ({self._dc6.total_frames()} 帧)")
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))

    def _populate_palettes(self):
        self.palette_combo.blockSignals(True)
        self.palette_combo.clear()
        palettes = list_palettes()
        self.palette_combo.addItems(palettes)
        if "act1" in palettes:
            self.palette_combo.setCurrentText("act1")
        elif palettes:
            self.palette_combo.setCurrentText(palettes[0])
        self.palette_combo.blockSignals(False)
        self._on_palette_changed(self.palette_combo.currentText())

    def _populate_frame_table(self):
        if not self._dc6:
            return
        self.frame_table.blockSignals(True)
        self.frame_table.setRowCount(self._dc6.total_frames())
        for i, frame in enumerate(self._dc6.frames):
            self.frame_table.setItem(i, 0, QTableWidgetItem(str(frame.width)))
            self.frame_table.setItem(i, 1, QTableWidgetItem(str(frame.height)))
            self.frame_table.setItem(i, 2, QTableWidgetItem(str(frame.x_offset)))
            self.frame_table.setItem(i, 3, QTableWidgetItem(str(frame.y_offset)))
        self.frame_table.blockSignals(False)
        if self._dc6.total_frames() > 0:
            self.frame_table.selectRow(0)

    def _populate_thumbnails(self):
        scroll_pos = self.thumb_bar.horizontalScrollBar().value()
        self.thumb_bar.clear_thumbs()
        if not self._dc6:
            return
        max_w = max(f.width for f in self._dc6.frames) or 1
        max_h = max(f.height for f in self._dc6.frames) or 1
        scale = min(THUMB_WIDTH / max_w, THUMB_HEIGHT / max_h, 1.0)
        for i in range(self._dc6.total_frames()):
            img = self._dc6.get_rgba_image(i)
            pix = QPixmap.fromImage(img)
            self.thumb_bar.add_thumb(pix, i, scale, selected=(i == self._current_frame))
        if scroll_pos > 0:
            QTimer.singleShot(0, lambda: self.thumb_bar.horizontalScrollBar().setValue(scroll_pos))

    def _populate_file_list(self, directory: str):
        scroll_pos = self.file_list.verticalScrollBar().value()
        self.file_list.blockSignals(True)
        self.file_list.clear()
        dc6_files = sorted(Path(directory).glob("*.dc6"))
        for f in dc6_files:
            item = QListWidgetItem(f.name)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self.file_list.addItem(item)
        if self._source_path:
            current_name = Path(self._source_path).name
            items = self.file_list.findItems(current_name, Qt.MatchFlag.MatchExactly)
            if items:
                self.file_list.setCurrentItem(items[0])
                self.file_list.scrollToItem(items[0])
        self.file_list.blockSignals(False)
        self.file_list.verticalScrollBar().setValue(scroll_pos)

    def _on_file_list_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ):
        if current is None:
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).is_file():
            try:
                data = Path(path).read_bytes()
                self.load_data(data, path)
            except Exception as e:
                self.statusBar().showMessage(f"打开失败: {e}")

    def _open_directory(self):
        last_dir = self._settings.value("last_open_dir", "")
        directory = QFileDialog.getExistingDirectory(self, "打开 DC6 目录", last_dir)
        if directory:
            self._settings.setValue("last_open_dir", directory)
            self._populate_file_list(str(directory))
            if self.file_list.count() > 0:
                self.file_list.setCurrentRow(0)

    def _show_frame(self, index: int):
        if not self._dc6 or index < 0 or index >= self._dc6.total_frames():
            return
        img = self._dc6.get_rgba_image(index)
        pix = QPixmap.fromImage(img)
        self.image_panel.set_image(pix)
        self._zoom_label.setText(self.image_panel.zoom_text())

    def _select_frame(self, index: int):
        self._current_frame = index
        self.frame_table.selectRow(index)
        self._show_frame(index)
        self._populate_thumbnails()

    def _on_frame_selected(self, row: int, _col: int, _prev_row: int, _prev_col: int):
        if row >= 0 and row != self._current_frame:
            self._current_frame = row
            self._show_frame(row)
            self._populate_thumbnails()

    def _zoom_in(self):
        self.image_panel.zoom_in()
        self._zoom_label.setText(self.image_panel.zoom_text())

    def _zoom_out(self):
        self.image_panel.zoom_out()
        self._zoom_label.setText(self.image_panel.zoom_text())

    def _zoom_reset(self):
        self.image_panel.zoom_reset()
        self._zoom_label.setText(self.image_panel.zoom_text())

    def _on_palette_changed(self, name: str):
        if not name or not self._dc6:
            return
        try:
            self._dc6.palette = load_palette(name)
            self._show_frame(self._current_frame)
            self._populate_thumbnails()
        except Exception as e:
            self.statusBar().showMessage(f"调色板加载失败: {e}")

    def _open_file(self):
        last_dir = self._settings.value("last_open_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 DC6 文件", last_dir, "DC6 文件 (*.dc6);;所有文件 (*)"
        )
        if path:
            self._settings.setValue("last_open_dir", str(Path(path).parent))
            with Path(path).open("rb") as f:
                data = f.read()
            self.load_data(data, path)
            self.file_list.clearSelection()

    def _save_as(self):
        if not self._dc6:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为 DC6", "", "DC6 文件 (*.dc6);;所有文件 (*)"
        )
        if path:
            try:
                self._write_dc6(path)
                self.statusBar().showMessage(f"已保存: {path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))

    def _write_dc6(self, path: str):
        buf = io.BytesIO()
        dc6 = self._dc6

        buf.write(struct.pack("<III", dc6.version, 1, 0))
        buf.write(dc6.termination[:4].ljust(4, b"\xee"))
        buf.write(struct.pack("<II", 1, dc6.total_frames()))

        pointer_table_offset = buf.tell()
        for _ in range(dc6.total_frames()):
            buf.write(struct.pack("<I", 0))

        pointers: list[int] = []
        for _ in range(dc6.total_frames()):
            pointers.append(buf.tell())
            frame = dc6.frames[_]

            block_start = buf.tell()
            buf.write(struct.pack("<IIIIIIII", 0, frame.width, frame.height, 0, 0, 0, 0, 0))

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

        with Path(path).open("wb") as f:
            f.write(buf.getvalue())

    def _export_bmp(self):
        if not self._dc6:
            return
        default_name = Path(self._source_path).stem if self._source_path else "export"
        dlg = ExportDialog(self._dc6.total_frames(), default_name, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        start_frame, count, cols, rows, base_name = dlg.settings()
        frames_per_image = cols * rows
        num_images = (count + frames_per_image - 1) // frames_per_image

        last_export = self._settings.value("last_export_dir", "")
        dest_dir = QFileDialog.getExistingDirectory(self, "选择导出目录", last_export)
        if not dest_dir:
            return
        self._settings.setValue("last_export_dir", dest_dir)

        exported = []
        for img_idx in range(num_images):
            base_frame = start_frame + img_idx * frames_per_image
            actual_frames = min(frames_per_image, count - img_idx * frames_per_image)

            if actual_frames <= 0:
                break

            total_w = cols * max(
                (
                    self._dc6.frames[base_frame + f].width
                    if base_frame + f < len(self._dc6.frames)
                    else 0
                )
                for f in range(actual_frames)
            )
            total_h = rows * max(
                self._dc6.frames[base_frame + f].height for f in range(actual_frames)
            )
            if total_w == 0 or total_h == 0:
                continue

            composite = QImage(total_w, total_h, QImage.Format.Format_Indexed8)
            composite.setColorCount(256)
            for i in range(256):
                if i * 3 + 2 < len(self._dc6.palette):
                    b = self._dc6.palette[i * 3]
                    g = self._dc6.palette[i * 3 + 1]
                    r = self._dc6.palette[i * 3 + 2]
                else:
                    r = g = b = 0
                composite.setColor(i, QColor(r, g, b).rgb())
            composite.fill(0)

            for f in range(actual_frames):
                fi = base_frame + f
                if fi >= len(self._dc6.frames):
                    break
                frame = self._dc6.frames[fi]
                fx = (f % cols) * frame.width
                fy = (f // cols) * frame.height
                for y in range(frame.height):
                    for x in range(frame.width):
                        val = frame.pixels[y * frame.width + x]
                        composite.setPixel(fx + x, fy + y, val)

            if num_images == 1:
                bmp_name = f"{base_name}.bmp"
            else:
                bmp_name = f"{base_name}-{img_idx:02d}.bmp"
            bmp_path = str(Path(dest_dir) / bmp_name)
            composite.save(bmp_path)
            exported.append(bmp_path)

        self.statusBar().showMessage(f"已导出 {len(exported)} 个文件")
        QMessageBox.information(self, "导出完成", f"已导出 {len(exported)} 个 BMP 文件")

    @staticmethod
    def _make_zoom_icon(text: str, size: int = 24) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = QApplication.palette()
        fg = pal.color(QPalette.ColorRole.ButtonText)
        pen = QPen(fg, 2)
        p.setPen(pen)
        p.drawEllipse(2, 2, 20, 20)
        font = QFont("sans-serif", 14, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(0, 1, size, size, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
        return QIcon(pix)

    @override
    def closeEvent(self, event: QCloseEvent):
        super().closeEvent(event)

    def dc6_file(self) -> Dc6File | None:
        return self._dc6


class Dc6PreviewPanel(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._dc6: Dc6File | None = None
        self._current_palette = "act1"
        self._pixmap: QPixmap | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.setMinimumSize(0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("调色板:"))
        self.palette_combo = QComboBox()
        self.palette_combo.currentTextChanged.connect(self._on_palette_changed)
        header.addWidget(self.palette_combo)
        self.info_label = QLabel("未选择 DC6")
        self.info_label.setStyleSheet("color: gray;")
        header.addWidget(self.info_label, 1)
        layout.addLayout(header)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background-color: #2d2d2d;")
        self.image_label.setMinimumSize(0, 0)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout.addWidget(self.image_label, 1)

    def load_dc6(self, data: bytes):
        try:
            self._dc6 = Dc6File(data)
            self._populate_palettes()
            self._show_preview()
        except Exception:
            self._dc6 = None
            self.image_label.clear()
            self.info_label.setText("无法解析 DC6")

    def clear(self):
        self._dc6 = None
        self.image_label.clear()
        self.info_label.setText("未选择 DC6")

    def _populate_palettes(self):
        self.palette_combo.blockSignals(True)
        self.palette_combo.clear()
        palettes = list_palettes()
        self.palette_combo.addItems(palettes)
        if self._current_palette in palettes:
            self.palette_combo.setCurrentText(self._current_palette)
        elif palettes:
            self.palette_combo.setCurrentText(palettes[0])
        self.palette_combo.blockSignals(False)
        self._apply_palette()

    def _apply_palette(self):
        if not self._dc6 or not self.palette_combo.currentText():
            return
        try:
            self._dc6.palette = load_palette(self.palette_combo.currentText())
            self._show_preview()
        except Exception:
            pass

    def _on_palette_changed(self, name: str):
        if name and self._dc6:
            self._current_palette = name
            self._apply_palette()

    def _show_preview(self):
        if not self._dc6 or not self._dc6.frames:
            self.image_label.clear()
            return

        best_idx = max(
            range(len(self._dc6.frames)),
            key=lambda i: self._dc6.frames[i].width * self._dc6.frames[i].height,
        )

        img = self._dc6.get_rgba_image(best_idx)
        self._pixmap = QPixmap.fromImage(img)
        self._update_scaled()

        self.info_label.setText(f"{self._dc6.total_frames()} 帧 | 最大帧: {best_idx}")

    def _update_scaled(self):
        if self._pixmap is None:
            return
        avail = self.image_label.size()
        if avail.width() <= 0 or avail.height() <= 0:
            self.image_label.setPixmap(self._pixmap)
            return
        scaled = self._pixmap.scaled(
            avail.width(),
            avail.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    @override
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scaled()


def show_dc6_window(
    data: bytes,
    source_path: str | None = None,
    parent: QWidget | None = None,
    enable_directory: bool = True,
) -> Dc6Viewer:
    viewer = Dc6Viewer(parent, data, source_path, enable_directory=enable_directory)
    viewer.show()
    return viewer


def main():
    import sys

    app = QApplication(sys.argv)
    app.setApplicationName("DC6 Viewer")

    icon_path = Path(__file__).parent.parent / "resources" / "dc6viewer.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    data = None
    if len(sys.argv) > 1:
        path = sys.argv[1]
        try:
            data = Path(path).read_bytes()
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)

    viewer = Dc6Viewer(data=data)
    if data:
        viewer._source_path = sys.argv[1]
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
