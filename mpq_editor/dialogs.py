from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mpq_editor.listfiles import scan_listfiles

_AUTO = "auto"
_NONE = "none"


class OpenOptionsDialog(QDialog):
    encoding_combo: QComboBox
    listfile_combo: QComboBox

    def __init__(
        self,
        mpq_path: str,
        current_encoding: str,
        parent: QWidget | None = None,
        listfiles: list[Path] | None = None,
    ):
        super().__init__(parent)
        self._listfiles = listfiles if listfiles is not None else scan_listfiles()
        self.setWindowTitle(f"打开 MPQ - {Path(mpq_path).name}")
        self.resize(520, 320)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"文件: {mpq_path}"))

        enc_layout = QHBoxLayout()
        enc_layout.addWidget(QLabel("文件名编码："))
        from mpq_editor.mpq_model import ENCODINGS

        self.encoding_combo = QComboBox()
        self.encoding_combo.addItems(ENCODINGS)
        if current_encoding in ENCODINGS:
            self.encoding_combo.setCurrentText(current_encoding)
        enc_layout.addWidget(self.encoding_combo)
        enc_layout.addStretch()
        layout.addLayout(enc_layout)

        list_layout = QHBoxLayout()
        list_layout.addWidget(QLabel("列表文件："))
        self.listfile_combo = QComboBox()
        self.listfile_combo.addItem(
            "自动匹配（使用程序目录下全部列表文件）", _AUTO
        )
        self.listfile_combo.addItem("（不使用列表文件）", _NONE)
        for p in self._listfiles:
            self.listfile_combo.addItem(p.name, str(p))
        self.listfile_combo.setCurrentIndex(0)
        list_layout.addWidget(self.listfile_combo, 1)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_list)
        list_layout.addWidget(browse_btn)
        layout.addLayout(list_layout)

        info = QLabel(
            "列表文件用于识别 MPQ 中未命名的文件（如 File00000001.xxx）。\n"
            "默认扫描程序目录下的 Listfiles 文件夹，也可以选择其他文件。"
        )
        info.setStyleSheet("color: gray;")
        layout.addWidget(info)

        layout.addStretch()

        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        bottom_layout.addWidget(ok_btn)
        bottom_layout.addWidget(cancel_btn)
        layout.addLayout(bottom_layout)

    def _browse_list(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择列表文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            label = f"自定义: {Path(path).name}"
            for i in range(self.listfile_combo.count()):
                if self.listfile_combo.itemData(i) == path:
                    self.listfile_combo.setCurrentIndex(i)
                    return
            self.listfile_combo.addItem(label, path)
            self.listfile_combo.setCurrentIndex(self.listfile_combo.count() - 1)

    def encoding(self) -> str:
        return self.encoding_combo.currentText()

    def selected_listfile_paths(self) -> list[str]:
        """返回要应用的列表文件路径（自动匹配时为全部，未使用时为空）。"""
        data = self.listfile_combo.currentData()
        if data == _AUTO:
            return [str(p) for p in self._listfiles]
        if data == _NONE or data is None:
            return []
        return [str(data)]


class NewDirDialog(QDialog):
    name_edit: QLineEdit

    def __init__(self, parent: QWidget | None = None, current_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("新建目录")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"在 {current_path or '/'} 下新建目录："))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("目录名")
        layout.addWidget(self.name_edit)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def dir_name(self) -> str:
        return self.name_edit.text().strip()


class DropConfirmDialog(QDialog):
    list_widget: QListWidget

    def __init__(self, files: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("确认添加文件")
        self.resize(450, 350)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("以下文件将被添加到 MPQ："))
        self.list_widget = QListWidget()
        for f in files:
            self.list_widget.addItem(f)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定添加")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)


class EncodingDialog(QDialog):
    combo: QComboBox

    def __init__(self, current_encoding: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("选择文件名编码")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("MPQ 文件名编码："))

        from mpq_editor.mpq_model import ENCODINGS

        self.combo = QComboBox()
        self.combo.addItems(ENCODINGS)
        if current_encoding in ENCODINGS:
            self.combo.setCurrentText(current_encoding)
        layout.addWidget(self.combo)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def encoding(self) -> str:
        return self.combo.currentText()
