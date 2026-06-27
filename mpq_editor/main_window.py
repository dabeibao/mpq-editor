from __future__ import annotations

from pathlib import Path
from typing import override
import sys

from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

SETTINGS_PATH = str(Path(__file__).parent / "settings.ini")

from mpq_editor.dialogs import EncodingDialog, OpenOptionsDialog
from mpq_editor.drag_drop import ErrorAction, handle_batch_error
from mpq_editor.file_list_widget import FileListWidget
from mpq_editor.mpq_model import MPQModel
from mpq_editor.tree_widget import TreeWidget

from dc6.dc6_viewer import Dc6PreviewPanel, Dc6Viewer


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.model = MPQModel()
        self.settings = QSettings(SETTINGS_PATH, QSettings.Format.IniFormat)
        self.setWindowTitle("MPQ 编辑器")
        self.resize(1000, 700)
        self._dc6_viewer: Dc6Viewer | None = None

        self._setup_ui()
        self._setup_actions()
        self._setup_toolbar()
        self._setup_menu()
        self._restore_settings()
        self._init_theme()
        self._update_actions()
        self.model.data_changed.connect(self._update_actions)
        self.statusBar().showMessage("就绪")

    def _setup_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.tree = TreeWidget(self.model)
        self.tree.file_selected.connect(self._on_tree_file_selected)
        left_layout.addWidget(self.tree, 2)

        self.dc6_preview = Dc6PreviewPanel()
        left_layout.addWidget(self.dc6_preview, 1)
        splitter.addWidget(left_panel)

        self.file_list = FileListWidget(self.model)
        self.file_list.file_activated.connect(self._on_list_file_activated)
        self.file_list.dc6_file_activated.connect(self._on_dc6_file_activated)
        self.file_list.itemSelectionChanged.connect(self._on_list_selection_changed)
        splitter.addWidget(self.file_list)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([300, 700])
        self.setCentralWidget(splitter)

    def _setup_actions(self):
        style = self.style()
        i = QStyle.StandardPixmap

        self.new_act = QAction(style.standardIcon(i.SP_FileIcon), "新建 MPQ...", self)
        self.new_act.setShortcut(QKeySequence("Ctrl+N"))
        self.new_act.triggered.connect(self._new_mpq)

        self.open_act = QAction(style.standardIcon(i.SP_DialogOpenButton), "打开...", self)
        self.open_act.setShortcut(QKeySequence.StandardKey.Open)
        self.open_act.triggered.connect(self._open_mpq)

        self.save_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "保存", self)
        self.save_act.setShortcut(QKeySequence.StandardKey.Save)
        self.save_act.triggered.connect(self._save_mpq)

        self.save_as_act = QAction(style.standardIcon(i.SP_DialogSaveButton), "另存为...", self)
        self.save_as_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_as_act.triggered.connect(self._save_as_mpq)

        self.close_act = QAction(style.standardIcon(i.SP_DialogCloseButton), "关闭", self)
        self.close_act.triggered.connect(self._close_mpq)

        self.add_files_act = QAction(style.standardIcon(i.SP_FileDialogNewFolder), "添加文件", self)
        self.add_files_act.triggered.connect(self._add_files)

        self.extract_act = QAction(style.standardIcon(i.SP_ArrowRight), "解压到...", self)
        self.extract_act.triggered.connect(self._edit_extract)

        self.delete_act = QAction(style.standardIcon(i.SP_DialogCloseButton), "删除", self)
        self.delete_act.triggered.connect(self._edit_delete)

        self.rename_act = QAction(style.standardIcon(i.SP_FileIcon), "重命名", self)
        self.rename_act.triggered.connect(self._edit_rename)

        self.extract_all_act = QAction(style.standardIcon(i.SP_ArrowRight), "解压全部", self)
        self.extract_all_act.triggered.connect(self._extract_all)

        self.new_dir_act = QAction(style.standardIcon(i.SP_FileDialogNewFolder), "新建目录", self)
        self.new_dir_act.triggered.connect(self._tree_new_dir)

        self.compress_act = QAction(style.standardIcon(i.SP_BrowserReload), "压缩 MPQ...", self)
        self.compress_act.triggered.connect(self._compress_mpq)

        self.encoding_act = QAction(
            style.standardIcon(i.SP_FileDialogInfoView), "文件名编码...", self
        )
        self.encoding_act.triggered.connect(self._choose_encoding)

        self._open_actions = [
            self.save_act,
            self.save_as_act,
            self.close_act,
            self.add_files_act,
            self.extract_act,
            self.delete_act,
            self.rename_act,
            self.extract_all_act,
            self.new_dir_act,
            self.compress_act,
        ]

    def _setup_toolbar(self):
        toolbar = self.addToolBar("主工具栏")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        toolbar.addAction(self.open_act)
        toolbar.addAction(self.save_act)

        toolbar.addSeparator()

        toolbar.addAction(self.add_files_act)
        toolbar.addAction(self.extract_all_act)

        toolbar.addSeparator()

        toolbar.addAction(self.compress_act)

        toolbar.addSeparator()

        toolbar.addAction(self.encoding_act)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self._theme_btn = QToolButton()
        self._theme_btn.setToolTip("切换主题")
        self._theme_btn.clicked.connect(self._toggle_theme)
        toolbar.addWidget(self._theme_btn)

    def _setup_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件")
        file_menu.addAction(self.new_act)
        file_menu.addAction(self.open_act)
        file_menu.addAction(self.save_act)
        file_menu.addAction(self.save_as_act)
        file_menu.addSeparator()
        file_menu.addAction(self.close_act)

        edit_menu = menubar.addMenu("编辑")
        edit_menu.addAction(self.extract_act)
        edit_menu.addAction(self.delete_act)
        edit_menu.addAction(self.rename_act)
        edit_menu.addSeparator()
        edit_menu.addAction(self.extract_all_act)
        edit_menu.addAction(self.new_dir_act)

        tool_menu = menubar.addMenu("工具")
        tool_menu.addAction(self.compress_act)
        tool_menu.addSeparator()
        tool_menu.addAction(self.encoding_act)

    def _update_actions(self):
        is_open = self.model.is_open
        is_modified = self.model.is_modified
        for act in self._open_actions:
            act.setEnabled(is_open)
        self.save_act.setEnabled(is_open and is_modified)

        name = self.model.file_path.name if self.model.file_path else ""
        base = f"MPQ 编辑器 - {name}" if name else "MPQ 编辑器"
        title = f"* {base}" if is_open and is_modified else base
        self.setWindowTitle(title)

    def _restore_settings(self):
        last_mpq_dir = self.settings.value("last_mpq_dir", "")
        saved_encoding = self.settings.value("encoding", "")
        if saved_encoding:
            self.model.encoding = saved_encoding
        self._theme = self.settings.value("theme", "light")

    def _save_settings(self):
        if self.model.file_path:
            self.settings.setValue("last_mpq_dir", str(self.model.file_path.parent))
        last_extract = self.settings.value("last_extract_dir", "")
        if last_extract:
            self.settings.setValue("last_extract_dir", last_extract)
        self.settings.setValue("encoding", self.model.encoding)
        self.settings.setValue("theme", self._theme)

    @override
    def showEvent(self, event):
        super().showEvent(event)
        self._set_title_bar_theme(self._theme == "dark")

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.model.is_open and self.model.is_modified:
            reply = QMessageBox.question(
                self,
                "保存更改",
                "当前文件已修改，是否保存？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._save_mpq()
            elif reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
        self._save_settings()
        super().closeEvent(event)

    def _open_mpq(self):
        if self.model.is_open and self.model.is_modified:
            reply = QMessageBox.question(
                self,
                "保存更改",
                "当前文件已修改，是否保存？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._save_mpq()
            elif reply == QMessageBox.StandardButton.Cancel:
                return

        last_dir: str = self.settings.value("last_mpq_dir", "")  # type: ignore
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 MPQ 文件", last_dir, "MPQ 文件 (*.mpq);;所有文件 (*)"
        )
        if not path:
            return

        dlg = OpenOptionsDialog(path, self.model.encoding, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self.model.encoding = dlg.encoding()
        file_list = dlg.file_list_data() or None
        try:
            self.model.open(path, file_list)
            self.file_list.current_dir = ""
            self.setWindowTitle(f"MPQ 编辑器 - {Path(path).name}")
            self.statusBar().showMessage(f"已打开: {path}")
            self.settings.setValue("last_mpq_dir", str(Path(path).parent))
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法打开 MPQ 文件:\n{e}")

    def _new_mpq(self):
        last_dir: str = self.settings.value("last_mpq_dir", "")  # type: ignore
        path, _ = QFileDialog.getSaveFileName(
            self, "新建 MPQ 文件", last_dir, "MPQ 文件 (*.mpq);;所有文件 (*)"
        )
        if path:
            try:
                self.model.create(path)
                self.file_list.current_dir = ""
                self.setWindowTitle(f"MPQ 编辑器 - {Path(path).name}")
                self.statusBar().showMessage(f"已新建: {path}")
                self.settings.setValue("last_mpq_dir", str(Path(path).parent))
            except Exception as e:
                QMessageBox.critical(self, "新建失败", f"无法创建 MPQ 文件:\n{e}")

    def _save_mpq(self):
        if not self.model.is_open:
            return
        try:
            self.model.save()
            self.model.is_modified = False
            self.statusBar().showMessage("已保存")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _save_as_mpq(self):
        if not self.model.is_open:
            return
        last_dir: str = self.settings.value("last_mpq_dir", "")  # type: ignore
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", last_dir, "MPQ 文件 (*.mpq);;所有文件 (*)"
        )
        if path:
            try:
                self.model.save(path)
                self.setWindowTitle(f"MPQ 编辑器 - {Path(path).name}")
                self.settings.setValue("last_mpq_dir", str(Path(path).parent))
                self.statusBar().showMessage(f"已保存到: {path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))

    def _close_mpq(self):
        if self.model.is_open and self.model.is_modified:
            reply = QMessageBox.question(
                self,
                "保存更改",
                "当前文件已修改，是否保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._save_mpq()
        self._save_settings()
        self.model.close()
        self.setWindowTitle("MPQ 编辑器")
        self.statusBar().showMessage("已关闭")

    def _add_files(self):
        if not self.model.is_open:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "选择要添加的文件", "", "所有文件 (*)")
        for local_path in files:
            mpq_name = Path(local_path).name
            mpq_target = (
                f"{self.file_list.current_dir}/{mpq_name}".strip("/")
                if self.file_list.current_dir
                else mpq_name
            )
            try:
                self.model.add_file(local_path, mpq_target)
            except Exception as e:
                QMessageBox.warning(self, "添加失败", f"{mpq_name}: {e}")

    def _extract_all(self):
        if not self.model.is_open:
            return
        last_dir: str = self.settings.value("last_extract_dir", "")  # type: ignore
        dest = QFileDialog.getExistingDirectory(self, "选择解压目标文件夹", last_dir)
        if not dest:
            return
        self.settings.setValue("last_extract_dir", dest)
        skip_all = False
        for f in self.model.list_files():
            try:
                self.model.extract_file(f, dest)
            except Exception as e:
                action = handle_batch_error(self, f, e, skip_all)
                if action == ErrorAction.ABORT:
                    return
                skip_all = action == ErrorAction.SKIP_ALL
        self.statusBar().showMessage(f"已解压到: {dest}")

    def _compress_mpq(self):
        if not self.model.is_open:
            return
        last_dir: str = self.settings.value("last_mpq_dir", "")  # type: ignore
        path, _ = QFileDialog.getSaveFileName(
            self, "压缩保存为", last_dir, "MPQ 文件 (*.mpq);;所有文件 (*)"
        )
        if path:
            try:
                self.model.save(path, compress=True)
                self.setWindowTitle(f"MPQ 编辑器 - {Path(path).name}")
                self.statusBar().showMessage("压缩完成")
            except Exception as e:
                QMessageBox.critical(self, "压缩失败", str(e))

    def _tree_new_dir(self):
        if self.tree.currentItem():
            path = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) or ""
            self.tree._new_dir(path)
        else:
            self.tree._new_dir(self.file_list.current_dir)

    def _on_tree_file_selected(self, path: str):
        self.file_list.current_dir = path

    def _edit_extract(self):
        if not self.model.is_open:
            return
        items = self.file_list.selectedItems()
        if items:
            self.file_list._extract_selected(items)
        elif self.tree.currentItem():
            path = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) or ""
            self.tree._extract_dir(path)

    def _edit_delete(self):
        if not self.model.is_open:
            return
        items = self.file_list.selectedItems()
        if items:
            self.file_list._delete_selected(items)
        elif self.tree.currentItem():
            path = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) or ""
            self.tree._delete_dir(path)

    def _edit_rename(self):
        if not self.model.is_open:
            return
        items = self.file_list.selectedItems()
        if len(items) == 1:
            self.file_list._rename_item(items[0])
        elif self.tree.currentItem():
            path = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) or ""
            self.tree._rename_dir(path)

    def _init_theme(self):
        app = QApplication.instance()
        app.setStyle("Fusion")
        self._light_palette = app.palette()
        self._apply_theme(self._theme)

    def _set_title_bar_theme(self, dark: bool):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = wintypes.HWND(int(self.winId()))
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass

    def _apply_theme(self, theme: str):
        app = QApplication.instance()
        if not app:
            return
        if theme == "dark":
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
            palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(220, 220, 220))
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(128, 128, 128)
            )
            palette.setColor(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(128, 128, 128)
            )
            app.setPalette(palette)
            self.setStyleSheet(
                """
                QMenuBar { color: #DCDCDC; }
                QMenuBar::item { color: #DCDCDC; }
                QToolBar { color: #DCDCDC; }
                QToolButton { color: #DCDCDC; }
                QMenu { color: #DCDCDC; }
                QMenu::item { color: #DCDCDC; }
                QStatusBar { color: #DCDCDC; }
            """
            )
            self._theme_btn.setText("☀")
            self._theme_btn.setToolTip("浅色主题")
        else:
            app.setPalette(self._light_palette)
            self.setStyleSheet("")
            self._theme_btn.setText("☾")
            self._theme_btn.setToolTip("深色主题")
        self._set_title_bar_theme(theme == "dark")

    def _toggle_theme(self):
        self._theme = "light" if self._theme == "dark" else "dark"
        self._apply_theme(self._theme)
        self.statusBar().showMessage(f"主题: {'深色' if self._theme == 'dark' else '浅色'}")

    def _choose_encoding(self):
        dlg = EncodingDialog(self.model.encoding, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.model.encoding = dlg.encoding()
            self.statusBar().showMessage(f"文件名编码: {self.model.encoding}")

    def _on_list_file_activated(self, path: str):
        self.tree.select_path(path)

    def _on_list_selection_changed(self):
        if not self.model.is_open or self.model._mpq is None:
            self.dc6_preview.clear()
            return
        items = self.file_list.selectedItems()
        dc6_item = None
        for item in items:
            ud = item.data(0, Qt.ItemDataRole.UserRole)
            if ud and ud.startswith("file:") and ud[5:].lower().endswith(".dc6"):
                dc6_item = ud[5:]
                break
        if dc6_item and len(items) == 1:
            try:
                data = self.model._mpq.read_file(dc6_item)
                self.dc6_preview.load_dc6(data)
            except Exception:
                self.dc6_preview.clear()
        else:
            self.dc6_preview.clear()

    def _on_dc6_file_activated(self, mpq_path: str):
        if self.model._mpq is None:
            return
        data = self.model._mpq.read_file(mpq_path)
        viewer = self._dc6_viewer
        if viewer is not None and viewer.isVisible():
            viewer.load_data(data, mpq_path)
            viewer.raise_()
            viewer.activateWindow()
        else:
            if viewer is not None:
                viewer.deleteLater()
            from dc6.dc6_viewer import show_dc6_window

            self._dc6_viewer = show_dc6_window(data, mpq_path, self, enable_directory=False)
