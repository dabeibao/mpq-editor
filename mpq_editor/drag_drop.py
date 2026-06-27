from __future__ import annotations

from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QMessageBox, QWidget


class ErrorAction(Enum):
    SKIP = auto()
    SKIP_ALL = auto()
    ABORT = auto()


def handle_batch_error(parent: QWidget, filepath: str, error: Exception, skip_all: bool = False) -> ErrorAction:
    if skip_all:
        return ErrorAction.SKIP
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Warning)
    msg.setWindowTitle("操作出错")
    msg.setText(f"文件: {filepath}")
    msg.setInformativeText(str(error))
    skip_btn = msg.addButton("跳过", QMessageBox.ButtonRole.ActionRole)
    skip_all_btn = msg.addButton("全部跳过", QMessageBox.ButtonRole.ActionRole)
    abort_btn = msg.addButton("中止", QMessageBox.ButtonRole.ActionRole)
    msg.setDefaultButton(skip_btn)
    msg.exec()
    clicked = msg.clickedButton()
    if clicked == skip_all_btn:
        return ErrorAction.SKIP_ALL
    elif clicked == abort_btn:
        return ErrorAction.ABORT
    return ErrorAction.SKIP


def start_drag(widget: QWidget, text: str) -> None:
    drag = QDrag(widget)
    mime = QMimeData()
    mime.setText(text)
    drag.setMimeData(mime)
    drag.exec(Qt.DropAction.CopyAction)


def extract_mpq_paths(mime_text: str) -> list[tuple[str, str]]:
    paths = []
    for line in mime_text.strip().split("\n"):
        line = line.strip()
        if line.startswith("mpq_dir:"):
            paths.append(("dir", line[8:]))
        elif line.startswith("mpq_file:"):
            paths.append(("file", line[9:]))
    return paths


def extract_local_paths(mime: QMimeData, _parent: QWidget | None = None) -> list[Path]:
    paths = []
    if mime.hasUrls():
        for url in mime.urls():
            local_path = url.toLocalFile()
            if local_path:
                paths.append(Path(local_path))
    return paths
