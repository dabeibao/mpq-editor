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


class OpenOptionsDialog(QDialog):
    encoding_combo: QComboBox
    list_path_edit: QLineEdit

    def __init__(self, mpq_path: str, current_encoding: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"打开 MPQ - {mpq_path.split('/')[-1].split('\\\\')[-1]}")
        self.resize(500, 300)
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

        list_path_layout = QHBoxLayout()
        list_path_layout.addWidget(QLabel("文件列表（可选）："))
        self.list_path_edit = QLineEdit()
        self.list_path_edit.setPlaceholderText("选择 .txt 文件，每行一个文件名...")
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_list)
        list_path_layout.addWidget(self.list_path_edit)
        list_path_layout.addWidget(browse_btn)
        layout.addLayout(list_path_layout)

        list_info = QLabel("留空则显示 MPQ 中所有文件")
        list_info.setStyleSheet("color: gray;")
        layout.addWidget(list_info)

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
            self, "选择文件列表", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if path:
            self.list_path_edit.setText(path)

    def encoding(self) -> str:
        return self.encoding_combo.currentText()

    def file_list_data(self) -> list[str]:
        path = self.list_path_edit.text().strip()
        if not path:
            return []
        try:
            with Path(path).open(encoding="utf-8", errors="ignore") as f:
                return [line.strip() for line in f if line.strip()]
        except Exception:
            return []


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
