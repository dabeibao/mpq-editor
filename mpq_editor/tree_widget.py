from __future__ import annotations

from pathlib import Path
from typing import override

from PySide6.QtCore import QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QInputDialog,
    QMenu,
    QMessageBox,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
)

from mpq_editor.dialogs import NewDirDialog
from mpq_editor.drag_drop import ErrorAction, handle_batch_error
from mpq_editor.mpq_model import MPQModel
from mpq_editor.path_utils import fold, is_under, relative_to, sort_key


class TreeWidget(QTreeWidget):
    file_selected = Signal(str)
    files_dropped = Signal(list)

    mpq_model: MPQModel

    def __init__(self, mpq_model: MPQModel, parent: QTreeWidget | None = None):
        super().__init__(parent)
        self.mpq_model = mpq_model
        self.mpq_model.data_changed.connect(self.refresh)
        self.setHeaderLabel("MPQ 目录")
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemClicked.connect(self._on_item_clicked)
        self._dir_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self.header()
        header.setStretchLastSection(True)

    def refresh(self):
        expanded_paths = self._get_expanded_paths()
        selected_path = self._get_selected_path()

        self.setUpdatesEnabled(False)
        self.clear()
        if not self.mpq_model.is_open:
            self.setUpdatesEnabled(True)
            return

        # 目录不区分大小写：dir_map 已把仅大小写不同的目录合并为一个规范形式
        dirs = sorted(self.mpq_model.dir_map().values(), key=sort_key)

        mpq_name = self.mpq_model.file_path.name if self.mpq_model.file_path else "MPQ"
        root_item = QTreeWidgetItem([mpq_name])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        root_item.setIcon(0, self._dir_icon)
        self.addTopLevelItem(root_item)

        added: dict[str, QTreeWidgetItem] = {fold(""): root_item}
        for d in dirs:
            parts = d.split("/")
            parent_fold = fold("/".join(parts[:-1]))
            parent_item = added.get(parent_fold, root_item)
            item = QTreeWidgetItem([parts[-1]])
            item.setData(0, Qt.ItemDataRole.UserRole, d)
            item.setIcon(0, self._dir_icon)
            parent_item.addChild(item)
            added[fold(d)] = item

        self._restore_expanded_state(expanded_paths, added)
        root_item.setExpanded(True)
        if selected_path is not None:
            self.select_path(selected_path)
        self.setUpdatesEnabled(True)

    def _get_expanded_paths(self) -> set[str]:
        paths: set[str] = set()
        root = self.topLevelItem(0)
        if not root:
            return paths

        def walk(item):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path is not None and item.isExpanded():
                paths.add(fold(path))
            for i in range(item.childCount()):
                walk(item.child(i))

        walk(root)
        return paths

    def _get_selected_path(self) -> str | None:
        item = self.currentItem()
        if item:
            return item.data(0, Qt.ItemDataRole.UserRole)
        return None

    def _restore_expanded_state(self, expanded_paths: set[str], added: dict[str, QTreeWidgetItem]):
        for path, item in added.items():
            if path in expanded_paths:
                item.setExpanded(True)

    def select_path(self, path: str):
        root = self.topLevelItem(0)
        if not root:
            return

        def find(parent_item, parts, idx=0):
            if idx >= len(parts):
                return parent_item
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if child.text(0).casefold() == parts[idx].casefold():
                    return find(child, parts, idx + 1)
            return parent_item

        if not path:
            self.setCurrentItem(root)
            return
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        item = find(root, parts)
        if item and item != root:
            self.setCurrentItem(item)
            self.scrollToItem(item)
            p = item.parent()
            while p:
                p.setExpanded(True)
                p = p.parent()

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path is not None:
            self.file_selected.emit(path)

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        menu = QMenu(self)
        if item:
            self.setCurrentItem(item)
            dir_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
            extract_act = menu.addAction("解压到...")
            delete_act = menu.addAction("删除")
            rename_act = menu.addAction("重命名")
            menu.addSeparator()
            new_dir_act = menu.addAction("新建目录")
            action = menu.exec(self.viewport().mapToGlobal(pos))
            if action == extract_act:
                self._extract_dir(dir_path)
            elif action == delete_act:
                self._delete_dir(dir_path)
            elif action == rename_act:
                self._rename_dir(dir_path)
            elif action == new_dir_act:
                self._new_dir(dir_path)
        else:
            new_dir_act = menu.addAction("新建目录")
            action = menu.exec(self.viewport().mapToGlobal(pos))
            if action == new_dir_act:
                self._new_dir("")

    def _files_under(self, dir_path: str) -> list[str]:
        return [f for f in self.mpq_model.list_files() if is_under(f, dir_path)]

    def _extract_dir(self, dir_path: str):
        from PySide6.QtWidgets import QFileDialog

        dest = QFileDialog.getExistingDirectory(self, "选择解压目标文件夹")
        if not dest:
            return
        skip_all = False
        for f in self._files_under(dir_path):
            try:
                self.mpq_model.extract_file(f, dest)
            except Exception as e:
                action = handle_batch_error(self, f, e, skip_all)
                if action == ErrorAction.ABORT:
                    return
                skip_all = action == ErrorAction.SKIP_ALL

    def _delete_dir(self, dir_path: str):
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除目录 '{dir_path or '/'}' 及其所有文件？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            skip_all = False
            for f in self._files_under(dir_path):
                try:
                    self.mpq_model.delete_file(f)
                except Exception as e:
                    action = handle_batch_error(self, f, e, skip_all)
                    if action == ErrorAction.ABORT:
                        return
                    skip_all = action == ErrorAction.SKIP_ALL
            self.mpq_model.remove_empty_dirs_under(dir_path)
            self.refresh()

    def _rename_dir(self, dir_path: str):
        if not dir_path:
            QMessageBox.information(self, "重命名", "不能重命名档案根目录。")
            return
        new_name, ok = QInputDialog.getText(self, "重命名", "新目录名:", text=dir_path.split("/")[-1])
        if not (ok and new_name):
            return
        parts = dir_path.replace("\\", "/").split("/")
        parts[-1] = new_name
        new_path = "/".join(parts)
        for f in self._files_under(dir_path):
            rel = relative_to(f, dir_path)
            if not rel:
                continue
            sep = "\\" if "\\" in f else "/"
            self.mpq_model.rename_file(f, new_path.replace("/", sep) + sep + rel.replace("/", sep))
        self.mpq_model.rename_empty_dir(dir_path, new_path)
        self.refresh()

    def _new_dir(self, base_path: str):
        dlg = NewDirDialog(self, base_path)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.dir_name()
            if name:
                full_path = f"{base_path}/{name}".strip("/")
                self.mpq_model.create_directory(full_path)

    @override
    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        dir_path = item.data(0, Qt.ItemDataRole.UserRole) or ""

        import shutil
        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="mpq_extract_")

        skip_all = False
        targets = []
        for f in self._files_under(dir_path):
            rel = relative_to(f, dir_path)
            if rel:
                targets.append((f, rel))

        assert self.mpq_model.file_path is not None
        extract_dir = Path(tmp_dir) / (
            self.mpq_model.file_path.stem if dir_path == "" else dir_path.split("/")[-1]
        )
        extract_dir.mkdir(parents=True, exist_ok=True)
        for f, rel in targets:
            target = extract_dir / rel
            try:
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

        urls = [QUrl.fromLocalFile(str(extract_dir))]
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setUrls(urls)
        mime_data.setText(f"mpq_dir:{dir_path}")
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)
        shutil.rmtree(tmp_dir, ignore_errors=True)
