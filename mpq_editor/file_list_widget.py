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
from mpq_editor.path_utils import fold, is_under, relative_to, split_parts
from mpq_editor.special import special_file_info


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
        cur_fold = fold(self._current_dir)
        cur_parts = split_parts(self._current_dir)

        # 子目录行：目录不区分大小写，dir_map 已合并为规范形式，行数据存完整路径
        dir_map = self.mpq_model.dir_map()
        dirs_in_dir: dict[str, str] = {}
        for d in dir_map.values():
            dparts = d.split("/")
            if len(dparts) == len(cur_parts) + 1 and fold("/".join(dparts[:-1])) == cur_fold:
                dirs_in_dir[fold(dparts[-1])] = d

        # 文件行：按折叠大小写匹配当前目录
        files_in_dir: list[str] = []
        for f in files:
            fparts = split_parts(f)
            if len(fparts) != len(cur_parts) + 1:
                continue
            if all(p.casefold() == q.casefold() for p, q in zip(fparts, cur_parts, strict=False)):
                files_in_dir.append(f)

        for d in sorted(dirs_in_dir.values(), key=fold):
            name = d.split("/")[-1]
            item = QTreeWidgetItem([name, "目录", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, "dir:" + d)
            item.setIcon(0, self._dir_icon)
            self.addTopLevelItem(item)

        for f in sorted(files_in_dir, key=fold):
            norm_f = f.replace("\\", "/")
            name = norm_f.split("/")[-1]
            info = special_file_info(f)
            if info is not None:
                type_text = info.label
            else:
                ext = Path(name).suffix.lower() if "." in name else ""
                type_text = f"文件 {ext}".rstrip()
            size = self._get_file_size(f)
            item = QTreeWidgetItem([name, type_text, str(size)])
            item.setData(0, Qt.ItemDataRole.UserRole, "file:" + f)
            item.setIcon(0, self._file_icon)
            if info is not None:
                item.setToolTip(1, info.description)
            self.addTopLevelItem(item)
        self.setUpdatesEnabled(True)

    def _get_file_size(self, mpq_path: str) -> int:
        return self.mpq_model.get_file_size(mpq_path)

    def _ensure_current_dir_valid(self):
        if not self._current_dir:
            return
        while self._current_dir and not self.mpq_model.has_dir(self._current_dir):
            parts = self._current_dir.split("/")
            self._current_dir = "/".join(parts[:-1])

    def _on_double_click(self, item: QTreeWidgetItem, _column: int):
        user_data = item.data(0, Qt.ItemDataRole.UserRole)
        if user_data and user_data.startswith("dir:"):
            dir_path = user_data[4:]
            self.current_dir = dir_path
            self.file_activated.emit(dir_path)
        elif user_data and user_data.startswith("file:"):
            file_path = user_data[5:]
            info = special_file_info(file_path)
            if info is not None and info.is_binary:
                self._explain_binary_special(file_path, info)
                return
            if file_path.lower().endswith(".dc6"):
                self.dc6_file_activated.emit(file_path)
            else:
                self._extract_single_file(file_path)

    def _explain_binary_special(self, file_path: str, info) -> None:
        """解释二进制内部文件为何显示乱码，并允许用户继续导出。"""
        name = file_path.replace("\\", "/").split("/")[-1]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(name)
        box.setText(f"{name} 是 {info.label}。")
        box.setInformativeText(f"{info.description}\n\n如需原始数据，可选择“仍要导出”。")
        extract_btn = box.addButton("仍要导出", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is extract_btn:
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
            if not user_data:
                continue
            if user_data.startswith("file:"):
                mpq_path = user_data[5:]
                try:
                    self.mpq_model.extract_file(mpq_path, dest)
                except Exception as e:
                    action = handle_batch_error(self, mpq_path, e, skip_all)
                    if action == ErrorAction.ABORT:
                        return
                    skip_all = action == ErrorAction.SKIP_ALL
            elif user_data.startswith("dir:"):
                dir_path = user_data[4:]
                for f in self.mpq_model.list_files():
                    if is_under(f, dir_path):
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
                if not user_data:
                    continue
                if user_data.startswith("file:"):
                    to_delete.append(user_data[5:])
                elif user_data.startswith("dir:"):
                    dir_path = user_data[4:]
                    empty_dirs_to_remove.append(dir_path)
                    to_delete.extend(f for f in self.mpq_model.list_files() if is_under(f, dir_path))

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
                self.mpq_model.remove_empty_dirs_under(dir_path)
                if self._current_dir and (
                    fold(self._current_dir) == fold(dir_path)
                    or is_under(self._current_dir, dir_path)
                ):
                    nav_up = True

            if nav_up:
                self._current_dir = "/".join(self._current_dir.split("/")[:-1])
            self.refresh()

    def _rename_item(self, item):
        user_data = item.data(0, Qt.ItemDataRole.UserRole)
        if not user_data:
            return
        old_name = item.text(0)
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=old_name)
        if not (ok and new_name and new_name != old_name):
            return
        if user_data.startswith("file:"):
            old_path = user_data[5:]
            sep = "\\" if "\\" in old_path else "/"
            parts = old_path.replace("\\", "/").split("/")
            parts[-1] = new_name
            new_path = sep.join(parts)
            self.mpq_model.rename_file(old_path, new_path)
        elif user_data.startswith("dir:"):
            old_path = user_data[4:]
            parts = old_path.split("/")
            parts[-1] = new_name
            new_path = "/".join(parts)
            for f in self.mpq_model.list_files():
                rel = relative_to(f, old_path)
                if not rel:
                    continue
                sep = "\\" if "\\" in f else "/"
                self.mpq_model.rename_file(f, new_path.replace("/", sep) + sep + rel.replace("/", sep))
            self.mpq_model.rename_empty_dir(old_path, new_path)
            rel = relative_to(self._current_dir, old_path)
            if fold(self._current_dir) == fold(old_path):
                self._current_dir = new_path
            elif rel is not None:
                self._current_dir = new_path + "/" + rel
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
                dir_path = user_data[4:]
                extract_dir = Path(tmp_dir) / dir_path.split("/")[-1]
                extract_dir.mkdir(parents=True, exist_ok=True)
                for f in self.mpq_model.list_files():
                    rel = relative_to(f, dir_path)
                    if not rel:
                        continue
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
