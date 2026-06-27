from __future__ import annotations

from pathlib import Path
from typing import override

from PySide6.QtCore import QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QInputDialog,
    QMenu,
    QMessageBox,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
)

from mpq_editor.drag_drop import ErrorAction, handle_batch_error
from mpq_editor.mpq_model import MPQModel


class FileListWidget(QTreeWidget):
    file_activated = Signal(str)
    dc6_file_activated = Signal(str)

    mpq_model: MPQModel

    def __init__(self, mpq_model: MPQModel, parent: QTreeWidget | None = None):
        super().__init__(parent)
        self.mpq_model = mpq_model
        self.mpq_model.data_changed.connect(self.refresh)
        self._current_dir = ""
        self.setHeaderLabels(["名称", "类型", "大小"])
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.resizeSection(0, 250)
        header.resizeSection(1, 80)
        header.resizeSection(2, 80)

        self._dir_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    @property
    def current_dir(self) -> str:
        return self._current_dir

    @current_dir.setter
    def current_dir(self, value: str):
        self._current_dir = value
        self.refresh()

    def refresh(self):
        self._ensure_current_dir_valid()
        self.setUpdatesEnabled(False)
        self.clear()
        if not self.mpq_model.is_open:
            self.setUpdatesEnabled(True)
            return
        files = self.mpq_model.list_files()
        prefix = self._current_dir.replace("/", "\\")
        if prefix and not prefix.endswith("\\"):
            prefix += "\\"

        dirs_in_dir: set[str] = set()
        files_in_dir: list[str] = []

        for f in files:
            norm_f = f.replace("\\", "/")
            if prefix:
                if not norm_f.startswith(self._current_dir.replace("\\", "/") + "/"):
                    continue
                rel = norm_f[len(self._current_dir) + 1 :]
            else:
                rel = norm_f
            if "/" in rel:
                sub_dir = rel.split("/")[0]
                dirs_in_dir.add(sub_dir)
            elif rel:
                files_in_dir.append(f)

        for d in self.mpq_model._empty_dirs:
            norm_d = d.replace("\\", "/")
            cur = self._current_dir.replace("\\", "/")
            if cur:
                if norm_d.startswith(cur + "/"):
                    rel = norm_d[len(cur) + 1 :]
                    if "/" in rel:
                        dirs_in_dir.add(rel.split("/")[0])
                    elif rel:
                        dirs_in_dir.add(rel)
            else:
                if "/" in norm_d:
                    dirs_in_dir.add(norm_d.split("/")[0])
                elif norm_d:
                    dirs_in_dir.add(norm_d)

        for d in sorted(dirs_in_dir):
            item = QTreeWidgetItem([d, "目录", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, "dir:" + d)
            item.setIcon(0, self._dir_icon)
            self.addTopLevelItem(item)

        for f in sorted(files_in_dir):
            norm_f = f.replace("\\", "/")
            name = norm_f.split("/")[-1]
            ext = Path(name).suffix.lower() if "." in name else ""
            size = self._get_file_size(f)
            item = QTreeWidgetItem([name, f"文件 {ext}", str(size)])
            item.setData(0, Qt.ItemDataRole.UserRole, "file:" + f)
            item.setIcon(0, self._file_icon)
            self.addTopLevelItem(item)
        self.setUpdatesEnabled(True)

    def _get_file_size(self, mpq_path: str) -> int:
        return self.mpq_model.get_file_size(mpq_path)

    def _ensure_current_dir_valid(self):
        if not self._current_dir:
            return
        prefix = self._current_dir.replace("\\", "/") + "/"
        files = self.mpq_model.list_files()
        if (
            not any(
                f.replace("\\", "/").startswith(prefix)
                or f.replace("\\", "/") == self._current_dir.replace("\\", "/")
                for f in files
            )
            and self._current_dir not in self.mpq_model._empty_dirs
        ):
            self._current_dir = "/".join(self._current_dir.split("/")[:-1])

    def _on_double_click(self, item: QTreeWidgetItem, _column: int):
        user_data = item.data(0, Qt.ItemDataRole.UserRole)
        if user_data and user_data.startswith("dir:"):
            dir_name = user_data[4:]
            new_path = f"{self._current_dir}/{dir_name}" if self._current_dir else dir_name
            self.current_dir = new_path
            self.file_activated.emit(new_path)
        elif user_data and user_data.startswith("file:"):
            file_path = user_data[5:]
            if file_path.lower().endswith(".dc6"):
                self.dc6_file_activated.emit(file_path)
            else:
                self._extract_single_file(file_path)

    def _extract_single_file(self, mpq_path: str):
        from PySide6.QtWidgets import QFileDialog

        default_name = mpq_path.replace("\\", "/").split("/")[-1]
        dest, _ = QFileDialog.getSaveFileName(self, "解压文件", default_name, "所有文件 (*)")
        if dest:
            assert self.mpq_model._mpq is not None
            data = self.mpq_model._mpq.read_file(mpq_path)
            with Path(dest).open("wb") as f:
                f.write(data)

    def _show_context_menu(self, pos):
        items = self.selectedItems()
        menu = QMenu(self)

        extract_act = menu.addAction("解压到...")
        delete_act = menu.addAction("删除")
        rename_act = menu.addAction("重命名")
        menu.addSeparator()
        new_dir_act = menu.addAction("新建目录")

        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == extract_act:
            self._extract_selected(items)
        elif action == delete_act:
            self._delete_selected(items)
        elif action == rename_act and len(items) == 1:
            self._rename_item(items[0])
        elif action == new_dir_act:
            self._new_dir()

    def _extract_selected(self, items):
        from PySide6.QtWidgets import QFileDialog

        dest = QFileDialog.getExistingDirectory(self, "选择解压目标文件夹")
        if not dest:
            return
        skip_all = False
        for item in items:
            user_data = item.data(0, Qt.ItemDataRole.UserRole)
            if user_data and user_data.startswith("file:"):
                mpq_path = user_data[5:]
                try:
                    self.mpq_model.extract_file(mpq_path, dest)
                except Exception as e:
                    action = handle_batch_error(self, mpq_path, e, skip_all)
                    if action == ErrorAction.ABORT:
                        return
                    skip_all = action == ErrorAction.SKIP_ALL
            elif user_data and user_data.startswith("dir:"):
                dir_name = user_data[4:]
                dir_path = (
                    f"{self._current_dir}/{dir_name}".strip("/") if self._current_dir else dir_name
                )
                for f in self.mpq_model.list_files():
                    norm_f = f.replace("\\", "/")
                    if norm_f.startswith(dir_path + "/") or norm_f == dir_path:
                        try:
                            self.mpq_model.extract_file(f, dest)
                        except Exception as e:
                            action = handle_batch_error(self, f, e, skip_all)
                            if action == ErrorAction.ABORT:
                                return
                            skip_all = action == ErrorAction.SKIP_ALL

    def _delete_selected(self, items):
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除选中的 {len(items)} 个项目？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            skip_all = False

            to_delete: list[str] = []
            empty_dirs_to_remove: list[str] = []
            for item in items:
                user_data = item.data(0, Qt.ItemDataRole.UserRole)
                if user_data and user_data.startswith("file:"):
                    to_delete.append(user_data[5:])
                elif user_data and user_data.startswith("dir:"):
                    dir_name = user_data[4:]
                    dir_path = (
                        f"{self._current_dir}/{dir_name}".strip("/")
                        if self._current_dir
                        else dir_name
                    )
                    empty_dirs_to_remove.append(dir_path)
                    for f in self.mpq_model.list_files():
                        norm_f = f.replace("\\", "/")
                        if norm_f.startswith(dir_path + "/") or norm_f == dir_path:
                            to_delete.append(f)

            for mpq_path in to_delete:
                try:
                    self.mpq_model.delete_file(mpq_path)
                except Exception as e:
                    action = handle_batch_error(self, mpq_path, e, skip_all)
                    if action == ErrorAction.ABORT:
                        return
                    skip_all = action == ErrorAction.SKIP_ALL

            nav_up = False
            for dir_path in empty_dirs_to_remove:
                self.mpq_model.remove_empty_dir(dir_path)
                self.mpq_model.remove_empty_dir(self._current_dir)
                if self._current_dir and (
                    self._current_dir == dir_path or self._current_dir.startswith(dir_path + "/")
                ):
                    nav_up = True

            if nav_up:
                self._current_dir = "/".join(self._current_dir.split("/")[:-1])
            self.refresh()

    def _rename_item(self, item):
        user_data = item.data(0, Qt.ItemDataRole.UserRole)
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=old_name)
        if ok and new_name and new_name != old_name:
            if user_data.startswith("file:"):
                old_path = user_data[5:]
                parts = old_path.replace("\\", "/").split("/")
                parts[-1] = new_name
                new_path = "/".join(parts)
                self.mpq_model.rename_file(old_path, new_path)
            elif user_data.startswith("dir:"):
                old_dir = user_data[4:]
                old_path = (
                    f"{self._current_dir}/{old_dir}".strip("/") if self._current_dir else old_dir
                )
                new_path = (
                    f"{self._current_dir}/{new_name}".strip("/") if self._current_dir else new_name
                )
                for f in self.mpq_model.list_files():
                    if f.startswith(old_path.replace("/", "\\")) or f.startswith(old_path):
                        rel = f[len(old_path) :]
                        self.mpq_model.rename_file(f, new_path + rel)
                if old_path in self.mpq_model._empty_dirs:
                    self.mpq_model.rename_empty_dir(old_path, new_path)
                self.refresh()

    def _new_dir(self):
        from mpq_editor.dialogs import NewDirDialog

        dlg = NewDirDialog(self, self._current_dir)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.dir_name()
            if name:
                full_path = f"{self._current_dir}/{name}".strip("/")
                self.mpq_model.create_directory(full_path)

    @override
    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return

        import shutil
        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="mpq_extract_")
        urls = []
        skip_all = False

        for item in items:
            user_data = item.data(0, Qt.ItemDataRole.UserRole)
            if not user_data:
                continue
            if user_data.startswith("file:"):
                mpq_path = user_data[5:]
                target = Path(tmp_dir) / mpq_path.replace("\\", "/").split("/")[-1]
                try:
                    assert self.mpq_model._mpq is not None
                    data = self.mpq_model._mpq.read_file(mpq_path)
                    target.write_bytes(data)
                    urls.append(QUrl.fromLocalFile(str(target)))
                except Exception as e:
                    action = handle_batch_error(self, mpq_path, e, skip_all)
                    if action == ErrorAction.ABORT:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        return
                    skip_all = action == ErrorAction.SKIP_ALL
            elif user_data.startswith("dir:"):
                dir_name = user_data[4:]
                extract_dir = Path(tmp_dir) / dir_name
                extract_dir.mkdir(parents=True, exist_ok=True)
                prefix = (
                    (self._current_dir + "/" + dir_name).replace("\\", "/")
                    if self._current_dir
                    else dir_name
                )
                for f in self.mpq_model.list_files():
                    norm_f = f.replace("\\", "/")
                    if norm_f.startswith(prefix + "/") or norm_f == prefix:
                        rel = norm_f[len(prefix) + 1 :] if norm_f != prefix else ""
                        if rel:
                            try:
                                target = extract_dir / rel
                                target.parent.mkdir(parents=True, exist_ok=True)
                                assert self.mpq_model._mpq is not None
                                data = self.mpq_model._mpq.read_file(f)
                                target.write_bytes(data)
                            except Exception as e:
                                action = handle_batch_error(self, f, e, skip_all)
                                if action == ErrorAction.ABORT:
                                    shutil.rmtree(tmp_dir, ignore_errors=True)
                                    return
                                skip_all = action == ErrorAction.SKIP_ALL
                urls.append(QUrl.fromLocalFile(str(extract_dir)))

        if not urls:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setUrls(urls)
        marker_lines = []
        for item in items:
            ud = item.data(0, Qt.ItemDataRole.UserRole) or ""
            marker_lines.append(f"mpq_file:{ud}")
        mime_data.setText("\n".join(marker_lines))
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    @override
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    @override
    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    @override
    def dropEvent(self, event):
        mime = event.mimeData()

        if mime.hasText():
            text = mime.text().strip()
            for line in text.split("\n"):
                if line.strip().startswith("mpq_dir:") or line.strip().startswith("mpq_file:"):
                    event.ignore()
                    return

        if mime.hasUrls():
            candidates = []
            for url in mime.urls():
                local_path = url.toLocalFile()
                if not local_path:
                    continue
                p = Path(local_path)
                if p.is_dir():
                    dir_name = p.name
                    for file_path in sorted(p.rglob("*")):
                        if file_path.is_file():
                            rel = file_path.relative_to(p).as_posix()
                            mpq_target = (
                                f"{self._current_dir}/{dir_name}/{rel}".strip("/")
                                if self._current_dir
                                else f"{dir_name}/{rel}"
                            )
                            candidates.append((str(file_path), mpq_target))
                else:
                    mpq_target = (
                        f"{self._current_dir}/{p.name}".strip("/") if self._current_dir else p.name
                    )
                    candidates.append((local_path, mpq_target))
            if candidates:
                from mpq_editor.dialogs import DropConfirmDialog

                dlg = DropConfirmDialog([c[1] for c in candidates], self)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    for local_path, mpq_target in candidates:
                        self.mpq_model.add_file(local_path, mpq_target)
                    self.refresh()
                    event.acceptProposedAction()
                    return
        event.ignore()
