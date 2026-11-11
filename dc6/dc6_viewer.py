"""DC6 Viewer 界面层。

数据模型与图像处理在 ``dc6.model``，帧编辑操作在 ``dc6.frame_ops``；
本文件只负责窗口、菜单、对话框与用户交互。为兼容历史调用，
这里仍然再导出模型层的公开符号。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import NamedTuple, override

# 兼容直接执行 `python dc6/dc6_viewer.py` 的情况。
if not __package__:  # pragma: no cover - 仅脚本直跑时生效
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QSettings,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dc6.frame_ops import (
    FrameIndexError,
    FrameSizeError,
    delete_frame,
    export_frame,
    frame_export_name,
    frames_from_image,
    insert_frames,
    replace_frame,
    set_frame_offset,
    validate_frame_size,
    validate_offset,
)
from dc6.model import (
    DC6_SIGNATURE,
    MAX_FRAME_SIZE,
    OFFSET_MAX,
    OFFSET_MIN,
    PAL_DIR,
    Dc6File,
    FrameInfo,
    build_dc6_bytes,
    compress_frame,
    floyd_steinberg_dither,
    list_palettes,
    load_palette,
    make_bmp_data,
    nearest_palette_index,
    palette_rgb,
    plan_tiles,
    split_image_tiles,
)

# 历史版本的数据模型 / 算法都定义在本文件，这里保留同名再导出以免破坏既有调用。
__all__ = [
    "DC6_SIGNATURE",
    "MAX_FRAME_SIZE",
    "OFFSET_MAX",
    "OFFSET_MIN",
    "PAL_DIR",
    "Dc6File",
    "Dc6PreviewPanel",
    "Dc6Viewer",
    "ExportDialog",
    "FrameIndexError",
    "FrameInfo",
    "FrameSizeError",
    "ImagePanel",
    "ImportDialog",
    "OffsetDelegate",
    "OffsetDialog",
    "ThumbnailBar",
    "build_dc6_bytes",
    "compress_frame",
    "delete_frame",
    "export_frame",
    "floyd_steinberg_dither",
    "frame_export_name",
    "frames_from_image",
    "insert_frames",
    "list_palettes",
    "load_palette",
    "main",
    "make_bmp_data",
    "nearest_palette_index",
    "palette_rgb",
    "plan_tiles",
    "replace_frame",
    "set_frame_offset",
    "show_dc6_window",
    "split_image_tiles",
    "validate_frame_size",
    "validate_offset",
]

THUMB_HEIGHT = 40
THUMB_WIDTH = 60
# 导入选项存到 QSettings；用字符串而非 bool，避免不同平台把 "false" 读成真值。
PRESERVE_BLACK_KEY = "import/preserve_black"


class OffsetDelegate(QStyledItemDelegate):
    """X / Y 偏移列的编辑器：只能输入有符号 32 位整数。"""

    @override
    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QWidget:
        editor = QSpinBox(parent)
        editor.setRange(OFFSET_MIN, OFFSET_MAX)
        editor.setSingleStep(1)
        editor.setFrame(False)
        return editor

    @override
    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:
        if not isinstance(editor, QSpinBox):
            return
        try:
            value = int(index.data(Qt.ItemDataRole.EditRole))
        except (TypeError, ValueError):
            value = 0
        editor.setValue(max(OFFSET_MIN, min(OFFSET_MAX, value)))

    @override
    def setModelData(
        self,
        editor: QWidget,
        model: QAbstractItemModel,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if isinstance(editor, QSpinBox):
            editor.interpretText()
            model.setData(index, editor.value(), Qt.ItemDataRole.EditRole)


class _ThumbWidget(QWidget):
    """单个缩略图：左键选中，右键弹出帧菜单。"""

    _index: int
    _on_click: Callable[[int], None]
    _on_context: Callable[[int, QPoint], None]

    def __init__(
        self,
        index: int,
        pixmap: QPixmap,
        scale: float,
        selected: bool,
        on_click: Callable[[int], None],
        on_context: Callable[[int, QPoint], None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._index = index
        self._on_click = on_click
        self._on_context = on_context
        self.setFixedSize(THUMB_WIDTH + 8, THUMB_HEIGHT + 22)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_requested)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        img_label = QLabel()
        scaled = pixmap.scaled(
            int(pixmap.width() * scale),
            int(pixmap.height() * scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        img_label.setPixmap(scaled)
        img_label.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_label.setStyleSheet("border: 2px solid #409EFF;" if selected else "border: 1px solid #ccc;")

        idx_label = QLabel(str(index))
        idx_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idx_label.setStyleSheet("font-size: 10px; color: gray;")
        idx_label.setFixedWidth(THUMB_WIDTH)

        layout.addWidget(img_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(idx_label, 0, Qt.AlignmentFlag.AlignCenter)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self._index)
        super().mousePressEvent(event)

    def _context_requested(self, pos: QPoint) -> None:
        self._on_context(self._index, self.mapToGlobal(pos))


class ThumbnailBar(QScrollArea):
    _container: QWidget
    _layout: QHBoxLayout
    thumb_clicked: Callable[[int], None] | None
    context_menu_requested: Callable[[int | None, QPoint], None] | None

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(THUMB_HEIGHT + 34)

        self._container = QWidget()
        self._container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._container.customContextMenuRequested.connect(self._container_context_requested)
        self._layout = QHBoxLayout(self._container)
        self._layout.setContentsMargins(4, 2, 4, 2)
        self._layout.setSpacing(6)
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.setWidget(self._container)

        self.thumb_clicked = None
        self.context_menu_requested = None

    def clear_thumbs(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def add_thumb(self, pixmap: QPixmap, index: int, scale: float, selected: bool = False):
        widget = _ThumbWidget(
            index,
            pixmap,
            scale,
            selected,
            self._on_thumb_clicked,
            self._on_thumb_context,
        )
        self._layout.addWidget(widget)

    def _on_thumb_clicked(self, index: int) -> None:
        if self.thumb_clicked is not None:
            self.thumb_clicked(index)

    def _on_thumb_context(self, index: int, global_pos: QPoint) -> None:
        if self.context_menu_requested is not None:
            self.context_menu_requested(index, global_pos)

    def _container_context_requested(self, pos: QPoint) -> None:
        # 缩略图栏的空白处：等价于「在末尾插入」。
        if self.context_menu_requested is not None:
            self.context_menu_requested(None, self._container.mapToGlobal(pos))


class ExportDialog(QDialog):
    start_spin: QSpinBox
    count_spin: QSpinBox
    cols_spin: QSpinBox
    rows_spin: QSpinBox
    merge_cb: QCheckBox
    name_edit: QLineEdit

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

        self.merge_cb = QCheckBox("合并多张图为一张（从左到右）")

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
        form.addWidget(self.merge_cb)
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

    def settings(self) -> tuple[int, int, int, int, int, str]:
        return (
            self.start_spin.value(),
            self.count_spin.value(),
            self.cols_spin.value(),
            self.rows_spin.value(),
            1 if self.merge_cb.isChecked() else 0,
            self.name_edit.text(),
        )


class ImportSettings(NamedTuple):
    """导入对话框的参数集合。

    用具名字段承载选项，避免以后每加一个开关就多一个靠位置区分的 tuple 元素。
    同时保留 tuple 行为（可解包、可按下标访问），兼容既有调用。
    """

    palette: str
    ratio: float
    frames_per_row: int
    preserve_black: bool = False


class _SwitchDecision(Enum):
    """切换文档前，用户对「未保存的修改」所作的选择。"""

    PROCEED = "proceed"  # 没有修改，或已经保存成功
    DISCARD = "discard"  # 放弃修改，继续切换
    CANCEL = "cancel"  # 留在当前文档


class ImportDialog(QDialog):
    """选择图片、调色板、抖动误差比例、透明处理方式；大图时配置每行帧数。"""

    allow_split: bool
    need_split: bool
    palette_combo: QComboBox
    ratio_spin: QSpinBox
    black_cb: QCheckBox
    fpr_widget: QWidget
    fpr_spin: QSpinBox
    fpr_info: QLabel

    def __init__(
        self,
        img_path: str,
        img_size: tuple[int, int],
        palettes: list[str],
        current_palette: str,
        parent: QWidget | None = None,
        *,
        allow_split: bool = True,
        title: str = "导入图片",
        preserve_black: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)

        img_w, img_h = img_size
        self.allow_split = allow_split
        self.need_split = img_w > MAX_FRAME_SIZE or img_h > MAX_FRAME_SIZE

        layout.addWidget(QLabel(f"图片: {img_path}"))
        layout.addWidget(QLabel(f"尺寸: {img_w} x {img_h}"))
        hint = (
            f"超过 {MAX_FRAME_SIZE}x{MAX_FRAME_SIZE} 的图片会按下方配置拆成多帧。"
            if allow_split
            else f"替换只允许不超过 {MAX_FRAME_SIZE}x{MAX_FRAME_SIZE} 的单帧图片。"
        )
        hint_label = QLabel(hint)
        hint_label.setWordWrap(True)
        hint_label.setStyleSheet("color: gray;")
        layout.addWidget(hint_label)

        palette_layout = QHBoxLayout()
        palette_layout.addWidget(QLabel("调色板:"))
        self.palette_combo = QComboBox()
        self.palette_combo.addItems(palettes)
        if current_palette in palettes:
            self.palette_combo.setCurrentText(current_palette)
        palette_layout.addWidget(self.palette_combo)
        layout.addLayout(palette_layout)

        ratio_layout = QHBoxLayout()
        ratio_layout.addWidget(QLabel("误差传递:"))
        self.ratio_spin = QSpinBox()
        self.ratio_spin.setRange(0, 200)
        self.ratio_spin.setValue(100)
        self.ratio_spin.setSuffix(" %")
        ratio_layout.addWidget(self.ratio_spin)
        layout.addLayout(ratio_layout)

        self.black_cb = QCheckBox("保留黑色：仅透明像素使用透明色 (pal 0)")
        self.black_cb.setToolTip(
            "勾选后，调色板 0 号色只用于源图完全透明 (alpha=0) 的像素；"
            "黑色等可见颜色改用 1..224 的 224 个跨 Act 通用色，不会被当成背景挖空。"
            "所有导入都只生成不超过 224 的索引，避免换 Act 显示成杂色。"
            "此选项会记住上次的选择。"
        )
        self.black_cb.setChecked(preserve_black)
        layout.addWidget(self.black_cb)

        self.fpr_widget = QWidget()
        fpr_layout = QHBoxLayout(self.fpr_widget)
        fpr_layout.setContentsMargins(0, 0, 0, 0)
        fpr_layout.addWidget(QLabel("每行帧数:"))
        self.fpr_spin = QSpinBox()
        self.fpr_spin.setRange(1, 64)
        self.fpr_spin.setValue(4)
        fpr_layout.addWidget(self.fpr_spin)
        self.fpr_info = QLabel()
        fpr_layout.addWidget(self.fpr_info)
        layout.addWidget(self.fpr_widget)
        self.fpr_widget.setVisible(self.need_split and self.allow_split)

        def _update_fpr_info():
            self.fpr_info.setText(f"将生成 {len(plan_tiles(img_w, img_h, self.fpr_spin.value()))} 帧")

        self.fpr_spin.valueChanged.connect(_update_fpr_info)
        if self.need_split and self.allow_split:
            _update_fpr_info()

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def settings(self) -> ImportSettings:
        return ImportSettings(
            palette=self.palette_combo.currentText(),
            ratio=float(self.ratio_spin.value()),
            frames_per_row=self.fpr_spin.value() if self.need_split and self.allow_split else 0,
            preserve_black=self.black_cb.isChecked(),
        )


class OffsetDialog(QDialog):
    """右键菜单里批量设置某一帧的 X / Y 偏移。"""

    x_spin: QSpinBox
    y_spin: QSpinBox

    def __init__(self, frame_index: int, x_offset: int, y_offset: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"帧 {frame_index} 的偏移")
        layout = QVBoxLayout(self)

        self.x_spin = QSpinBox()
        self.x_spin.setRange(OFFSET_MIN, OFFSET_MAX)
        self.x_spin.setValue(x_offset)
        self.y_spin = QSpinBox()
        self.y_spin.setRange(OFFSET_MIN, OFFSET_MAX)
        self.y_spin.setValue(y_offset)

        form = QFormLayout()
        form.addRow("X 偏移:", self.x_spin)
        form.addRow("Y 偏移:", self.y_spin)
        layout.addLayout(form)

        note = QLabel("偏移写入 DC6 块头，决定帧在动画中的摆放位置。也可以直接双击表格里的 X/Y 列修改。")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        layout.addWidget(note)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def values(self) -> tuple[int, int]:
        return self.x_spin.value(), self.y_spin.value()


class ImagePanel(QScrollArea):
    _original_pixmap: QPixmap | None
    _zoom: float
    _image_label: QLabel

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._original_pixmap = None
        self._zoom = 1.0

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #2d2d2d;")
        self.setWidget(self._image_label)

        self.viewport().installEventFilter(self)

    def set_image(self, pixmap: QPixmap):
        self._original_pixmap = pixmap
        self._update_zoomed()

    def clear_image(self):
        self._original_pixmap = None
        self._image_label.clear()
        self._image_label.resize(0, 0)

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
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            obj == self.viewport()
            and isinstance(event, QWheelEvent)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            return True
        return super().eventFilter(obj, event)


class Dc6Viewer(QMainWindow):
    # 类级注解让静态检查器明确这些属性的类型（均由 __init__ / _setup_* 赋值）。
    _dc6: Dc6File | None
    _source_path: str | None
    # 从 MPQ 内部打开时 source_path 是归档里的虚拟路径，不能直接写盘。
    _source_is_virtual: bool
    _current_frame: int
    _modified: bool
    _is_dark: bool
    _settings: QSettings
    frame_table: QTableWidget
    file_list: QListWidget
    palette_combo: QComboBox
    image_panel: ImagePanel
    thumb_bar: ThumbnailBar
    _zoom_label: QLabel
    new_act: QAction
    open_act: QAction
    open_dir_act: QAction
    save_act: QAction
    save_as_act: QAction
    close_act: QAction
    delete_act: QAction
    import_act: QAction
    export_act: QAction
    replace_act: QAction
    export_frame_act: QAction
    offset_act: QAction
    zoom_in_act: QAction
    zoom_out_act: QAction
    zoom_reset_act: QAction

    def __init__(
        self,
        parent: QWidget | None = None,
        data: bytes | None = None,
        source_path: str | None = None,
        is_dark: bool = False,
    ):
        super().__init__(parent)
        self._is_dark = is_dark
        self._dc6 = None
        self._source_path = source_path
        self._source_is_virtual = False
        self._current_frame = -1
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

    # ------------------------------------------------------------------ UI
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
        self.frame_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.frame_table.setItemDelegateForColumn(2, OffsetDelegate(self.frame_table))
        self.frame_table.setItemDelegateForColumn(3, OffsetDelegate(self.frame_table))
        self.frame_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.frame_table.customContextMenuRequested.connect(self._on_frame_table_context_menu)
        self.frame_table.itemChanged.connect(self._on_frame_item_changed)
        self.frame_table.currentCellChanged.connect(self._on_frame_selected)
        self.frame_table.setToolTip("双击 X/Y 偏移列可编辑；右键可插入、删除、替换或导出帧")
        hh = self.frame_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.resizeSection(0, 55)
        hh.resizeSection(1, 55)
        hh.resizeSection(2, 70)
        hh.resizeSection(3, 70)

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
        self.thumb_bar.context_menu_requested = self._show_frame_menu
        right_layout.addWidget(self.thumb_bar)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 740])

        self.setCentralWidget(splitter)

        self.statusBar().showMessage("就绪")
        self.apply_theme(self._is_dark)

    def apply_theme(self, is_dark: bool):
        self._is_dark = is_dark
        self.frame_table.setStyleSheet(self._get_style_sheet())

    def _get_style_sheet(self) -> str:
        common = """
        QTableWidget {
            outline: 0;
        }
        """
        if self._is_dark:
            return common + """
            QTableWidget::item:selected,
            QTableWidget::item:focus {
                background-color: #303030;
                border: none;
            }
            """
        return common + """
        QTableWidget::item:selected,
        QTableWidget::item:focus {
            background-color: #f5f5f5;
            color: black;
        }
        """

    def _setup_actions(self):
        style = self.style()
        i = QStyle.StandardPixmap

        self.new_act = QAction(style.standardIcon(i.SP_FileIcon), "新建", self)
        self.new_act.setShortcut(QKeySequence("Ctrl+N"))
        self.new_act.triggered.connect(self._new_dc6)

        self.open_act = QAction(style.standardIcon(i.SP_DialogOpenButton), "打开...", self)
        self.open_act.setShortcut(QKeySequence("Ctrl+O"))
        self.open_act.triggered.connect(self._open_file)

        self.open_dir_act = QAction(style.standardIcon(i.SP_DirOpenIcon), "打开目录...", self)
        self.open_dir_act.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.open_dir_act.triggered.connect(self._open_directory)

        self.save_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "保存", self)
        self.save_act.setShortcut(QKeySequence("Ctrl+S"))
        self.save_act.triggered.connect(self._save)

        self.save_as_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "另存为...", self)
        self.save_as_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_as_act.triggered.connect(self._save_as)

        self.close_act = QAction(style.standardIcon(i.SP_DialogCloseButton), "关闭", self)
        self.close_act.triggered.connect(self.close)

        self.import_act = QAction(style.standardIcon(i.SP_FileDialogStart), "插入...", self)
        self.import_act.setShortcut(QKeySequence("Ctrl+I"))
        self.import_act.setToolTip("插入图像：在当前帧之前插入；超过 256x256 自动拆成多帧")
        self.import_act.triggered.connect(self._import_image)

        self.replace_act = QAction(style.standardIcon(i.SP_FileDialogContentsView), "替换当前帧...", self)
        self.replace_act.setShortcut(QKeySequence("Ctrl+R"))
        self.replace_act.setToolTip("只接受不超过 256x256 的单帧图像")
        self.replace_act.triggered.connect(self._replace_current_frame)

        self.delete_act = QAction(style.standardIcon(i.SP_TrashIcon), "删除帧", self)
        self.delete_act.setShortcut(QKeySequence("Delete"))
        self.delete_act.setToolTip("删除当前选中的帧")
        self.delete_act.triggered.connect(self._delete_frame)

        self.export_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "导出 BMP...", self)
        self.export_act.setShortcut(QKeySequence("Ctrl+E"))
        self.export_act.setToolTip("把全部帧批量导出为 BMP")
        self.export_act.triggered.connect(self._export_bmp)

        self.export_frame_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "导出当前帧...", self)
        self.export_frame_act.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.export_frame_act.triggered.connect(self._export_current_frame)

        self.offset_act = QAction(style.standardIcon(i.SP_ArrowRight), "设置 X/Y 偏移...", self)
        self.offset_act.setShortcut(QKeySequence("Ctrl+Shift+X"))
        self.offset_act.triggered.connect(self._edit_current_offset)

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
        file_menu.addAction(self.new_act)
        file_menu.addSeparator()
        file_menu.addAction(self.open_act)
        file_menu.addAction(self.open_dir_act)
        file_menu.addSeparator()
        file_menu.addAction(self.save_act)
        file_menu.addAction(self.save_as_act)
        file_menu.addSeparator()
        file_menu.addAction(self.export_act)
        file_menu.addSeparator()
        file_menu.addAction(self.close_act)

        frame_menu = menubar.addMenu("帧")
        frame_menu.addAction(self.import_act)
        frame_menu.addAction(self.replace_act)
        frame_menu.addAction(self.delete_act)
        frame_menu.addSeparator()
        frame_menu.addAction(self.export_frame_act)
        frame_menu.addSeparator()
        frame_menu.addAction(self.offset_act)
        frame_menu.setToolTipsVisible(True)

        view_menu = menubar.addMenu("查看")
        view_menu.addAction(self.zoom_in_act)
        view_menu.addAction(self.zoom_out_act)
        view_menu.addAction(self.zoom_reset_act)

    def _setup_toolbar(self):
        toolbar = self.addToolBar("工具栏")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # 保持改造前的按钮集合，避免用户习惯的工具栏入口消失。
        # 新增的帧编辑功能走「帧」菜单与帧列表右键菜单，不挤占工具栏宽度。
        toolbar.addAction(self.new_act)
        toolbar.addAction(self.open_act)
        toolbar.addAction(self.open_dir_act)
        toolbar.addAction(self.save_act)
        toolbar.addAction(self.save_as_act)
        toolbar.addSeparator()
        toolbar.addAction(self.import_act)
        toolbar.addAction(self.export_act)
        toolbar.addSeparator()
        toolbar.addAction(self.delete_act)
        toolbar.addSeparator()
        toolbar.addAction(self.zoom_in_act)
        toolbar.addAction(self.zoom_out_act)
        toolbar.addAction(self.zoom_reset_act)

        # 缩放按钮的图标本身已能表意，改为纯图标以给左侧带文字的按钮留足空间，
        # 避免它们被折叠进 ">>" 溢出菜单。
        for act in (self.zoom_in_act, self.zoom_out_act, self.zoom_reset_act):
            button = toolbar.widgetForAction(act)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                button.setToolTip(act.text())

        # 缩放百分比放在状态栏，避免工具栏被撑开导致按钮折叠到 ">>" 里。
        self._zoom_label = QLabel("100%")
        self._zoom_label.setStyleSheet("padding: 0 8px;")
        self.statusBar().addPermanentWidget(self._zoom_label)

    def _update_actions(self):
        dc6 = self._dc6
        has_file = dc6 is not None
        n = dc6.total_frames() if dc6 is not None else 0
        has_frame = has_file and n > 0 and 0 <= self._current_frame < n
        self.save_act.setEnabled(has_file and n > 0)
        self.save_as_act.setEnabled(has_file and n > 0)
        self.delete_act.setEnabled(has_frame)
        self.import_act.setEnabled(has_file)
        self.replace_act.setEnabled(has_frame)
        self.export_act.setEnabled(has_file and n > 0)
        self.export_frame_act.setEnabled(has_frame)
        self.offset_act.setEnabled(has_frame)
        self.zoom_in_act.setEnabled(has_file and n > 0)
        self.zoom_out_act.setEnabled(has_file and n > 0)
        self.zoom_reset_act.setEnabled(has_file and n > 0)

    def set_directory_enabled(self, enabled: bool) -> None:
        """由主程序调用，控制是否允许浏览同目录下的其它 DC6 文件。"""
        self.open_dir_act.setEnabled(enabled)
        self.file_list.setVisible(enabled)

    def _last_dir(self, key: str) -> str:
        """QSettings.value 的静态类型是 object，这里统一收敛成 str。"""
        return str(self._settings.value(key, ""))

    def _remembered_preserve_black(self) -> bool:
        """读取上次导入时确定的「保留黑色」选项。"""
        return str(self._settings.value(PRESERVE_BLACK_KEY, "false")) == "true"

    # ------------------------------------------------------------- 状态刷新
    def _refresh_title(self):
        name = Path(self._source_path).name if self._source_path else "未命名"
        mark = " *" if self._modified else ""
        self.setWindowTitle(f"DC6 Viewer - {name}{mark}")

    def _populate_frame_table(self, select_row: int | None = None):
        dc6 = self._dc6
        if dc6 is None:
            return
        total = dc6.total_frames()
        row = -1
        if total > 0:
            row = self._current_frame if select_row is None else select_row
            row = max(0, min(row, total - 1))

        self.frame_table.blockSignals(True)
        self.frame_table.setRowCount(total)
        for i, frame in enumerate(dc6.frames):
            for col, value in enumerate((frame.width, frame.height, frame.x_offset, frame.y_offset)):
                item = QTableWidgetItem(str(value))
                if col < 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.frame_table.setItem(i, col, item)
        if row >= 0:
            self.frame_table.selectRow(row)
        self.frame_table.blockSignals(False)
        self._current_frame = row

    def _populate_thumbnails(self):
        scroll_pos = self.thumb_bar.horizontalScrollBar().value()
        self.thumb_bar.clear_thumbs()
        dc6 = self._dc6
        if dc6 is None or not dc6.frames:
            return
        max_w = max(f.width for f in dc6.frames) or 1
        max_h = max(f.height for f in dc6.frames) or 1
        scale = min(THUMB_WIDTH / max_w, THUMB_HEIGHT / max_h, 1.0)
        for i in range(dc6.total_frames()):
            pix = QPixmap.fromImage(dc6.get_rgba_image(i))
            self.thumb_bar.add_thumb(pix, i, scale, selected=(i == self._current_frame))
        if scroll_pos > 0:
            QTimer.singleShot(0, lambda: self.thumb_bar.horizontalScrollBar().setValue(scroll_pos))

    def _reload_frames(self, select_row: int | None = None, message: str | None = None):
        """数据变动后的统一刷新入口。"""
        dc6 = self._dc6
        if dc6 is None:
            return
        total = dc6.total_frames()
        if total == 0:
            self._current_frame = -1
        else:
            row = self._current_frame if select_row is None else select_row
            self._current_frame = max(0, min(row, total - 1))
        self._populate_frame_table(self._current_frame)
        self._populate_thumbnails()
        self._show_frame(self._current_frame)
        self._update_actions()
        self._refresh_title()
        if message:
            self.statusBar().showMessage(message)

    def _apply_import_palette(self, name: str) -> None:
        """导入时用的调色板同时切换为显示调色板，颜色才对得上。"""
        idx = self.palette_combo.findText(name)
        if idx >= 0 and idx != self.palette_combo.currentIndex():
            self.palette_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------- 文件操作
    def _confirm_before_switch(self) -> _SwitchDecision:
        """新建 / 打开 / 切换文件前，确认当前文档未保存的修改。

        DISCARD 时由调用方在真正替换文档时清掉修改标记；CANCEL 时必须保持现状。
        选择「是」但保存失败（例如另存为被取消）同样视为 CANCEL，避免丢失修改。
        """
        if not self._modified:
            return _SwitchDecision.PROCEED
        name = Path(self._source_path).name if self._source_path else "未命名"
        buttons = (
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel
        )
        choice = QMessageBox.question(
            self,
            "未保存的修改",
            f"{name} 有未保存的修改，切换前是否保存？",
            buttons,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Yes:
            return _SwitchDecision.PROCEED if self._save() else _SwitchDecision.CANCEL
        if choice == QMessageBox.StandardButton.No:
            return _SwitchDecision.DISCARD
        return _SwitchDecision.CANCEL

    def _new_dc6(self):
        if self._confirm_before_switch() is _SwitchDecision.CANCEL:
            return
        self._dc6 = Dc6File.blank()
        self._source_path = None
        self._source_is_virtual = False
        self._current_frame = -1
        self._modified = False
        self._populate_palettes()
        self._reload_frames(message="已创建空白 DC6，右键帧列表可插入图像")

    def _save(self) -> bool:
        """保存当前文档；返回是否真的写入成功（切换文件时需要据此决定是否放行）。"""
        if not self._dc6:
            return False
        if not self._dc6.frames:
            QMessageBox.warning(self, "保存失败", "无法保存空白 DC6，请先插入帧")
            return False
        if not self._source_path or self._source_is_virtual:
            return self._save_as()
        try:
            self._write_dc6(self._source_path)
        except OSError as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return False
        self._modified = False
        self._refresh_title()
        self.statusBar().showMessage(f"已保存: {self._source_path}")
        return True

    def _save_as(self) -> bool:
        if not self._dc6:
            return False
        if not self._dc6.frames:
            QMessageBox.warning(self, "保存失败", "无法保存空白 DC6，请先插入帧")
            return False
        path, _ = QFileDialog.getSaveFileName(self, "另存为 DC6", "", "DC6 文件 (*.dc6);;所有文件 (*)")
        if not path:
            return False
        try:
            self._write_dc6(path)
        except OSError as e:
            QMessageBox.critical(self, "保存失败", str(e))
            return False
        self._source_path = path
        self._source_is_virtual = False
        self._modified = False
        self._refresh_title()
        self.statusBar().showMessage(f"已保存: {path}")
        return True

    def _write_dc6(self, path: str):
        if not self._dc6:
            return
        Path(path).write_bytes(build_dc6_bytes(self._dc6))

    def load_data(self, data: bytes, source_path: str | None = None) -> bool:
        """加载并显示另一个 DC6；返回是否真的切换了文档。

        所有会替换当前文档的入口（打开文件、文件列表、MPQ 编辑器双击）都经过这里，
        因此未保存修改的确认只需在这一处收口。
        """
        if self._confirm_before_switch() is _SwitchDecision.CANCEL:
            return False
        try:
            dc6 = Dc6File(data)
            self._dc6 = dc6
            self._source_path = source_path
            self._source_is_virtual = source_path is not None and not Path(source_path).is_file()
            self._current_frame = 0
            self._modified = False
            self._populate_palettes()
            self._reload_frames(message=f"已加载: {Path(source_path).name if source_path else 'untitled'} "
                                        f"({dc6.total_frames()} 帧)")
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return False
        return True

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

    def _populate_file_list(self, directory: str):
        scroll_pos = self.file_list.verticalScrollBar().value()
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for f in sorted(Path(directory).glob("*.dc6")):
            item = QListWidgetItem(f.name)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self.file_list.addItem(item)
        if self._source_path:
            items = self.file_list.findItems(Path(self._source_path).name, Qt.MatchFlag.MatchExactly)
            if items:
                self.file_list.setCurrentItem(items[0])
                self.file_list.scrollToItem(items[0])
        self.file_list.blockSignals(False)
        self.file_list.verticalScrollBar().setValue(scroll_pos)

    def _on_file_list_selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ):
        if current is None:
            return
        path = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if not path or not Path(path).is_file():
            return
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            self.statusBar().showMessage(f"打开失败: {e}")
            return
        if self.load_data(data, path):
            return
        # 取消切换或打开失败时还原高亮，避免左侧列表与实际显示的文档不一致。
        self.file_list.blockSignals(True)
        if previous is None:
            self.file_list.setCurrentRow(-1)
        else:
            self.file_list.setCurrentItem(previous)
        self.file_list.blockSignals(False)

    def _open_directory(self):
        last_dir = self._last_dir("last_open_dir")
        directory = QFileDialog.getExistingDirectory(self, "打开 DC6 目录", last_dir)
        if not directory:
            return
        # 刷新列表后会自动选中第一个文件（等于切换文档），先确认未保存的修改。
        decision = self._confirm_before_switch()
        if decision is _SwitchDecision.CANCEL:
            return
        self._settings.setValue("last_open_dir", directory)
        self._populate_file_list(directory)
        first_item = self.file_list.item(0)
        if first_item is None:
            return
        # 显式加载第一个文件：若当前文件正好就在目录里，setCurrentRow(0) 不会发出
        # 切换信号，靠信号加载会让「放弃修改」只改了标记、内存里仍留着旧改动。
        self.file_list.blockSignals(True)
        self.file_list.setCurrentRow(0)
        self.file_list.blockSignals(False)
        if decision is _SwitchDecision.DISCARD:
            self._modified = False
            self._refresh_title()
        path = str(first_item.data(Qt.ItemDataRole.UserRole) or "")
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            self.statusBar().showMessage(f"打开失败: {e}")
            return
        self.load_data(data, path)

    def _open_file(self):
        last_dir = self._last_dir("last_open_dir")
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 DC6 文件", last_dir, "DC6 文件 (*.dc6);;所有文件 (*)"
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "打开失败", str(e))
            return
        if self.load_data(data, path):
            self._settings.setValue("last_open_dir", str(Path(path).parent))
            self.file_list.clearSelection()

    # ------------------------------------------------------------- 帧编辑
    def _choose_image_frames(self, allow_split: bool) -> tuple[list[FrameInfo], str] | None:
        """选图片 → 抖动 → 切片 → 帧数据；返回 (帧列表, 调色板名) 或取消。"""
        last_dir = self._last_dir("last_import_dir")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "替换图像" if not allow_split else "插入图像",
            last_dir,
            "图片文件 (*.png *.bmp *.jpg *.jpeg *.gif *.tga);;所有文件 (*)",
        )
        if not path:
            return None
        self._settings.setValue("last_import_dir", str(Path(path).parent))

        img = QImage(path)
        if img.isNull():
            QMessageBox.critical(self, "读取失败", "无法加载图片")
            return None
        if not allow_split:
            try:
                validate_frame_size(img.width(), img.height())
            except FrameSizeError as e:
                QMessageBox.critical(self, "替换失败", str(e))
                return None

        palettes = list_palettes()
        title = "替换图像" if not allow_split else "插入图像"
        dlg = ImportDialog(
            path,
            (img.width(), img.height()),
            palettes,
            self.palette_combo.currentText(),
            self,
            allow_split=allow_split,
            title=title,
            preserve_black=self._remembered_preserve_black(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        opts = dlg.settings()
        # 只在确认导入后记住，点「取消」不应改变用户的长期偏好。
        self._settings.setValue(PRESERVE_BLACK_KEY, "true" if opts.preserve_black else "false")
        try:
            palette_data = load_palette(opts.palette)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "调色板错误", str(e))
            return None

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage("正在转换图像，请稍候...")
        try:
            frames = frames_from_image(
                img,
                palette_data,
                opts.ratio,
                opts.frames_per_row,
                allow_split=allow_split,
                preserve_black=opts.preserve_black,
            )
        except ValueError as e:
            QMessageBox.critical(self, "图像错误", str(e))
            return None
        finally:
            QApplication.restoreOverrideCursor()

        return frames, opts.palette

    def _insert_frames_at(self, position: int) -> None:
        dc6 = self._dc6
        if dc6 is None:
            return
        chosen = self._choose_image_frames(allow_split=True)
        if chosen is None:
            return
        frames, pal_name = chosen
        pos = insert_frames(dc6, position, frames)
        self._apply_import_palette(pal_name)
        self._modified = True
        self._reload_frames(select_row=pos, message=f"已在位置 {pos} 插入 {len(frames)} 帧")

    def _import_image(self):
        self._insert_frames_at(max(0, self._current_frame))

    def _replace_frame_at(self, index: int) -> None:
        dc6 = self._dc6
        if dc6 is None:
            return
        chosen = self._choose_image_frames(allow_split=False)
        if chosen is None:
            return
        frames, pal_name = chosen
        try:
            replace_frame(dc6, index, frames[0])
        except (FrameIndexError, FrameSizeError) as e:
            QMessageBox.critical(self, "替换失败", str(e))
            return
        self._apply_import_palette(pal_name)
        self._modified = True
        self._reload_frames(select_row=index, message=f"已替换帧 {index}（保留原 X/Y 偏移）")

    def _replace_current_frame(self):
        self._replace_frame_at(self._current_frame)

    def _delete_frame_at(self, index: int) -> None:
        dc6 = self._dc6
        if dc6 is None:
            return
        try:
            delete_frame(dc6, index)
        except FrameIndexError:
            return
        self._modified = True
        next_row = min(index, dc6.total_frames() - 1)
        self._reload_frames(select_row=next_row, message=f"已删除帧 {index}")

    def _delete_frame(self):
        self._delete_frame_at(self._current_frame)

    def _apply_offsets(self, index: int, x: int, y: int) -> bool:
        dc6 = self._dc6
        if dc6 is None:
            return False
        try:
            set_frame_offset(dc6, index, x=x, y=y)
        except (FrameIndexError, ValueError) as e:
            QMessageBox.warning(self, "偏移错误", str(e))
            return False
        item_x = self.frame_table.item(index, 2)
        item_y = self.frame_table.item(index, 3)
        self.frame_table.blockSignals(True)
        if item_x is not None:
            item_x.setText(str(x))
        if item_y is not None:
            item_y.setText(str(y))
        self.frame_table.blockSignals(False)
        self._modified = True
        self._refresh_title()
        self.statusBar().showMessage(f"帧 {index} 偏移已设为 ({x}, {y})")
        return True

    def _edit_offset_at(self, index: int) -> None:
        dc6 = self._dc6
        if dc6 is None or not 0 <= index < dc6.total_frames():
            return
        frame = dc6.frames[index]
        dlg = OffsetDialog(index, frame.x_offset, frame.y_offset, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            x, y = dlg.values()
            self._apply_offsets(index, x, y)

    def _edit_current_offset(self):
        self._edit_offset_at(self._current_frame)

    def _on_frame_item_changed(self, item: QTableWidgetItem) -> None:
        """双击 X/Y 单元格内联编辑后的提交处理。"""
        dc6 = self._dc6
        if dc6 is None:
            return
        col = item.column()
        row = item.row()
        if col not in (2, 3) or row >= dc6.total_frames():
            return
        try:
            value = int(item.text().strip())
            validate_offset(value)
        except ValueError as e:
            self._restore_offset_cell(row, col)
            QMessageBox.warning(self, "偏移错误", str(e))
            return
        frame = dc6.frames[row]
        x = value if col == 2 else frame.x_offset
        y = value if col == 3 else frame.y_offset
        if not self._apply_offsets(row, x, y):
            self._restore_offset_cell(row, col)

    def _restore_offset_cell(self, row: int, col: int) -> None:
        dc6 = self._dc6
        if dc6 is None or row >= dc6.total_frames():
            return
        frame = dc6.frames[row]
        value = frame.x_offset if col == 2 else frame.y_offset
        item = self.frame_table.item(row, col)
        if item is None:
            return
        self.frame_table.blockSignals(True)
        item.setText(str(value))
        self.frame_table.blockSignals(False)

    # ------------------------------------------------------------- 右键菜单
    def _on_frame_table_context_menu(self, pos: QPoint) -> None:
        dc6 = self._dc6
        if dc6 is None:
            self._show_frame_menu(None, self.frame_table.viewport().mapToGlobal(pos))
            return
        row = self.frame_table.rowAt(pos.y())
        index = row if 0 <= row < dc6.total_frames() else None
        self._show_frame_menu(index, self.frame_table.viewport().mapToGlobal(pos))

    def _show_frame_menu(self, index: int | None, global_pos: QPoint) -> None:
        """帧列表 / 缩略图共用的右键菜单；index=None 表示点在空白处。"""
        dc6 = self._dc6
        menu = QMenu(self)
        handlers: dict[QAction, Callable[[], None]] = {}

        if dc6 is None:
            handlers[menu.addAction("新建空白 DC6")] = self._new_dc6
        elif index is None:
            handlers[menu.addAction("在末尾插入图像...")] = lambda: self._insert_frames_at(dc6.total_frames())
        else:
            i = index
            handlers[menu.addAction(f"在帧 {i} 之前插入图像...")] = lambda i=i: self._insert_frames_at(i)
            handlers[menu.addAction(f"在帧 {i} 之后插入图像...")] = lambda i=i: self._insert_frames_at(i + 1)
            menu.addSeparator()
            handlers[menu.addAction("替换此帧图像...")] = lambda i=i: self._replace_frame_at(i)
            handlers[menu.addAction("删除此帧")] = lambda i=i: self._delete_frame_at(i)
            handlers[menu.addAction("导出此帧...")] = lambda i=i: self._export_frame_at(i)
            menu.addSeparator()
            handlers[menu.addAction("设置 X/Y 偏移...")] = lambda i=i: self._edit_offset_at(i)

        chosen = menu.exec(global_pos)
        if chosen is not None and chosen in handlers:
            handlers[chosen]()

    # ------------------------------------------------------------- 显示 / 缩放
    def _show_frame(self, index: int):
        dc6 = self._dc6
        if dc6 is None or index < 0 or index >= dc6.total_frames():
            self.image_panel.clear_image()
            return
        self.image_panel.set_image(QPixmap.fromImage(dc6.get_rgba_image(index)))
        self._zoom_label.setText(self.image_panel.zoom_text())

    def _select_frame(self, index: int):
        self._reload_frames(select_row=index)

    def _on_frame_selected(self, row: int, _col: int, _prev_row: int, _prev_col: int):
        if row >= 0 and row != self._current_frame:
            self._current_frame = row
            self._show_frame(row)
            self._populate_thumbnails()
            self._update_actions()

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
        except (OSError, ValueError) as e:
            self.statusBar().showMessage(f"调色板加载失败: {e}")

    # ------------------------------------------------------------- 导出
    def _export_frame_at(self, index: int) -> None:
        dc6 = self._dc6
        if dc6 is None:
            return
        last_dir = self._last_dir("last_export_dir")
        default = str(Path(last_dir) / frame_export_name(self._source_path, index)) if last_dir else frame_export_name(
            self._source_path, index
        )
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            f"导出帧 {index}",
            default,
            "BMP 索引色 (*.bmp);;PNG 透明 (*.png);;JPEG (*.jpg);;所有文件 (*)",
        )
        if not path:
            return
        dest = Path(path)
        if not dest.suffix:
            ext = ".png" if selected_filter.startswith("PNG") else ".jpg" if selected_filter.startswith("JPEG") else ".bmp"
            dest = dest.with_suffix(ext)
        try:
            export_frame(dc6, index, dest)
        except (OSError, ValueError, FrameIndexError) as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        self._settings.setValue("last_export_dir", str(dest.parent))
        self.statusBar().showMessage(f"已导出: {dest}")
        QMessageBox.information(self, "导出完成", f"已导出帧 {index}:\n{dest}")

    def _export_current_frame(self):
        self._export_frame_at(self._current_frame)

    def _build_grid_image(self, base_frame: int, count: int, cols: int, _rows: int) -> QImage | None:
        dc6 = self._dc6
        if dc6 is None:
            return None
        rows = (count + cols - 1) // cols
        total_w = 0
        total_h = 0
        row_heights: list[int] = []
        for i in range(rows):
            row_width = 0
            max_row_height = 0
            for j in range(cols):
                index = i * cols + j
                if index >= count:
                    break
                f = dc6.frames[base_frame + index]
                row_width += f.width
                max_row_height = max(max_row_height, f.height)
            total_w = max(total_w, row_width)
            row_heights.append(max_row_height)
        total_h = sum(row_heights)
        if total_w == 0 or total_h == 0:
            return None

        composite = QImage(total_w, total_h, QImage.Format.Format_Indexed8)
        composite.setColorCount(256)
        for i in range(256):
            r, g, b = palette_rgb(dc6.palette, i)
            composite.setColor(i, QColor(r, g, b).rgb())
        composite.fill(0)

        fy = 0
        for i in range(rows):
            fx = 0
            for j in range(cols):
                index = i * cols + j
                if index >= count:
                    break
                f = dc6.frames[base_frame + index]
                for y in range(f.height):
                    for x in range(f.width):
                        composite.setPixel(fx + x, fy + y, f.pixels[y * f.width + x])
                fx += f.width
            fy += row_heights[i]
        return composite

    def _export_bmp(self):
        dc6 = self._dc6
        if dc6 is None:
            return
        default_name = Path(self._source_path).stem if self._source_path else "export"
        dlg = ExportDialog(dc6.total_frames(), default_name, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        start_frame, count, cols, rows, merge_mode, base_name = dlg.settings()

        last_export = self._last_dir("last_export_dir")
        dest_dir = QFileDialog.getExistingDirectory(self, "选择导出目录", last_export)
        if not dest_dir:
            return
        self._settings.setValue("last_export_dir", dest_dir)

        frames_per_image = cols * rows
        num_images = (count + frames_per_image - 1) // frames_per_image

        grid_images: list[QImage] = []
        for img_idx in range(num_images):
            base_frame = start_frame + img_idx * frames_per_image
            actual_frames = min(frames_per_image, count - img_idx * frames_per_image)
            if actual_frames <= 0:
                break
            grid = self._build_grid_image(base_frame, actual_frames, cols, rows)
            if grid is not None:
                grid_images.append(grid)

        if not grid_images:
            return

        if merge_mode:
            total_w = sum(img.width() for img in grid_images)
            total_h = max(img.height() for img in grid_images)

            merged = QImage(total_w, total_h, QImage.Format.Format_Indexed8)
            merged.setColorCount(256)
            for i in range(256):
                r, g, b = palette_rgb(dc6.palette, i)
                merged.setColor(i, QColor(r, g, b).rgb())
            merged.fill(0)

            x_offset = 0
            for grid in grid_images:
                for y in range(grid.height()):
                    for x in range(grid.width()):
                        merged.setPixel(x_offset + x, y, grid.pixelIndex(x, y))
                x_offset += grid.width()

            bmp_path = str(Path(dest_dir) / f"{base_name}.bmp")
            merged.save(bmp_path)
            self.statusBar().showMessage("已导出 1 个合并文件")
            QMessageBox.information(self, "导出完成", f"已导出 1 个合并 BMP 文件: {base_name}.bmp")
        else:
            exported: list[str] = []
            for img_idx, grid in enumerate(grid_images):
                bmp_name = f"{base_name}.bmp" if num_images == 1 else f"{base_name}-{img_idx:02d}.bmp"
                bmp_path = str(Path(dest_dir) / bmp_name)
                grid.save(bmp_path)
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
        p.setPen(QPen(fg, 2))
        p.drawEllipse(2, 2, 20, 20)
        p.setFont(QFont("sans-serif", 14, QFont.Weight.Bold))
        p.drawText(0, 1, size, size, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
        return QIcon(pix)

    @override
    def closeEvent(self, event: QCloseEvent):
        super().closeEvent(event)

    def dc6_file(self) -> Dc6File | None:
        return self._dc6


class Dc6PreviewPanel(QWidget):
    _dc6: Dc6File | None
    _current_palette: str
    _pixmap: QPixmap | None
    palette_combo: QComboBox
    info_label: QLabel
    image_label: QLabel

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._dc6 = None
        self._current_palette = "act1"
        self._pixmap = None

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
        except (OSError, ValueError):
            pass

    def _on_palette_changed(self, name: str):
        if name and self._dc6:
            self._current_palette = name
            self._apply_palette()

    def _show_preview(self):
        dc6 = self._dc6
        if dc6 is None or not dc6.frames:
            self.image_label.clear()
            return

        best_idx = max(range(len(dc6.frames)), key=lambda i: dc6.frames[i].width * dc6.frames[i].height)

        img = dc6.get_rgba_image(best_idx)
        self._pixmap = QPixmap.fromImage(img)
        self._update_scaled()

        self.info_label.setText(f"{dc6.total_frames()} 帧 | 最大帧: {best_idx}")

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
    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self._update_scaled()


def show_dc6_window(
    data: bytes,
    source_path: str | None = None,
    parent: QWidget | None = None,
    is_dark: bool = False,
    enable_directory: bool = True,
) -> Dc6Viewer:
    viewer = Dc6Viewer(parent, data, source_path, is_dark=is_dark)
    viewer.set_directory_enabled(enable_directory)
    viewer.show()
    return viewer


def main():
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
        except OSError as e:
            print(f"Error: {e}")
            sys.exit(1)

    viewer = Dc6Viewer(data=data)
    if data:
        viewer._source_path = sys.argv[1]
        viewer._refresh_title()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
