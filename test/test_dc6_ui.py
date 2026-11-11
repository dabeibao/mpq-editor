"""DC6 Viewer 界面交互的单元测试（offscreen，无真实弹窗）。

覆盖：帧表格偏移列的内联编辑、右键菜单的项与分发、插入/替换/删除/导出的
界面编排、大图替换报错、导入对话框的拆帧开关。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
)

from dc6 import dc6_viewer as viewer_mod
from dc6.dc6_viewer import (
    Dc6Viewer,
    ImportDialog,
    OffsetDelegate,
    OffsetDialog,
    build_dc6_bytes,
    show_dc6_window,
)
from dc6.frame_ops import frames_from_image, insert_frames
from dc6.model import Dc6File, FrameInfo, load_palette, plan_tiles


class _FakeAction:
    _text: str

    def __init__(self, text: str):
        self._text = text

    def text(self) -> str:
        return self._text


class _FakeMenu:
    """替代 QMenu：记录菜单项，并返回指定下标的动作。"""

    pick: int = 0
    last: _FakeMenu | None = None
    actions: list[_FakeAction]

    def __init__(self, _parent: object | None = None):
        self.actions = []
        _FakeMenu.last = self

    def addAction(self, text: str) -> _FakeAction:  # noqa: N802 - 模仿 Qt API
        action = _FakeAction(text)
        self.actions.append(action)
        return action

    def addSeparator(self) -> None:  # noqa: N802 - 模仿 Qt API
        return None

    def exec(self, _pos: QPoint) -> _FakeAction:
        return self.actions[_FakeMenu.pick]


class _FakeMessageBox:
    calls: ClassVar[list[tuple[str, str]]] = []

    @classmethod
    def critical(cls, _parent: object, title: str, message: str) -> None:
        cls.calls.append((title, message))

    @classmethod
    def warning(cls, _parent: object, title: str, message: str) -> None:
        cls.calls.append((title, message))

    @classmethod
    def information(cls, _parent: object, title: str, message: str) -> None:
        cls.calls.append((title, message))


class _FakeFileDialog:
    path: str = ""
    selected_filter: str = ""

    @classmethod
    def getOpenFileName(cls, *_args: object, **_kwargs: object) -> tuple[str, str]:  # noqa: N802
        return cls.path, ""

    @classmethod
    def getSaveFileName(cls, *_args: object, **_kwargs: object) -> tuple[str, str]:  # noqa: N802
        return cls.path, cls.selected_filter


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    yield app


@pytest.fixture
def viewer(qapp: QApplication) -> Iterator[Dc6Viewer]:
    assert qapp is not None
    window = Dc6Viewer()
    window._new_dc6()
    yield window
    window.close()


def _model(window: Dc6Viewer) -> Dc6File:
    result = window._dc6
    assert result is not None
    return result


def _cell(window: Dc6Viewer, row: int, col: int) -> QTableWidgetItem:
    item = window.frame_table.item(row, col)
    assert item is not None
    return item


def _last_menu() -> _FakeMenu:
    menu = _FakeMenu.last
    assert menu is not None
    return menu


def _add_frames(window: Dc6Viewer, count: int = 3) -> None:
    frames = [
        FrameInfo(width=4 + i, height=3, pixels=bytearray([1] * ((4 + i) * 3)))
        for i in range(count)
    ]
    insert_frames(_model(window), 0, frames)
    window._reload_frames(select_row=0)


def _record_frame_actions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[object, ...]]]:
    calls: list[tuple[str, tuple[object, ...]]] = []
    for name in (
        "_insert_frames_at",
        "_replace_frame_at",
        "_delete_frame_at",
        "_export_frame_at",
        "_edit_offset_at",
        "_new_dc6",
    ):
        monkeypatch.setattr(
            Dc6Viewer,
            name,
            lambda _self, *args, _n=name: calls.append((_n, args)),
        )
    return calls


class TestToolbar:
    """改造前的工具栏入口必须保留，不能被新增功能挤掉。"""

    _ORIGINAL_ACTIONS: tuple[str, ...] = (
        "new_act",
        "open_act",
        "open_dir_act",
        "save_act",
        "save_as_act",
        "import_act",
        "export_act",
        "delete_act",
        "zoom_in_act",
        "zoom_out_act",
        "zoom_reset_act",
    )

    def test_original_actions_are_kept(self, viewer: Dc6Viewer):
        toolbar = viewer.findChild(QToolBar)
        assert toolbar is not None
        actions = set(toolbar.actions())
        for name in self._ORIGINAL_ACTIONS:
            action = getattr(viewer, name)
            assert action in actions, f"{action.text()} 从工具栏丢失"

    def test_all_original_buttons_are_visible_at_default_width(
        self, viewer: Dc6Viewer, qapp: QApplication
    ):
        viewer.resize(1000, 700)
        viewer.show()
        qapp.processEvents()
        toolbar = viewer.findChild(QToolBar)
        assert toolbar is not None
        for name in self._ORIGINAL_ACTIONS:
            action = getattr(viewer, name)
            button = toolbar.widgetForAction(action)
            assert isinstance(button, QToolButton), f"{action.text()} 没有工具栏按钮"
            assert button.isVisible(), f"{action.text()} 被折叠进 >> 溢出菜单"
            assert button.geometry().right() <= toolbar.width()


class TestOffsetEditing:
    def test_only_offset_columns_are_editable(self, viewer: Dc6Viewer):
        _add_frames(viewer, 1)
        for col in (0, 1):
            assert not (_cell(viewer, 0, col).flags() & Qt.ItemFlag.ItemIsEditable)
        for col in (2, 3):
            assert _cell(viewer, 0, col).flags() & Qt.ItemFlag.ItemIsEditable
            assert isinstance(viewer.frame_table.itemDelegateForColumn(col), OffsetDelegate)

    def test_inline_edit_updates_model_and_marks_modified(self, viewer: Dc6Viewer):
        _add_frames(viewer, 2)
        _cell(viewer, 1, 2).setText("-33")
        _cell(viewer, 1, 3).setText("47")
        model = _model(viewer)
        assert (model.frames[1].x_offset, model.frames[1].y_offset) == (-33, 47)
        assert viewer.windowTitle().endswith("*")

    def test_invalid_inline_value_is_restored_and_reported(
        self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(viewer_mod, "QMessageBox", _FakeMessageBox)
        _FakeMessageBox.calls.clear()
        _add_frames(viewer, 1)
        _cell(viewer, 0, 2).setText("not-a-number")
        assert _model(viewer).frames[0].x_offset == 0
        assert _cell(viewer, 0, 2).text() == "0"
        assert any(title == "偏移错误" for title, _ in _FakeMessageBox.calls)

    def test_offset_edit_survives_save_and_reload(self, viewer: Dc6Viewer, tmp_path: Path):
        _add_frames(viewer, 1)
        _cell(viewer, 0, 2).setText("-5")
        _cell(viewer, 0, 3).setText("19")
        dest = tmp_path / "edited.dc6"
        viewer._source_path = str(dest)

        viewer._save()

        assert dest.is_file()
        restored = Dc6File(dest.read_bytes())
        assert (restored.frames[0].x_offset, restored.frames[0].y_offset) == (-5, 19)
        assert not viewer.windowTitle().endswith("*")

    def test_offset_dialog_values(self):
        dlg = OffsetDialog(2, -5, 17)
        dlg.x_spin.setValue(100)
        dlg.y_spin.setValue(-200)
        assert dlg.values() == (100, -200)


class TestImportDialog:
    def test_insert_mode_shows_split_setting(self):
        dlg = ImportDialog("big.png", (512, 300), ["act1"], "act1", allow_split=True)
        assert not dlg.fpr_widget.isHidden()
        dlg.fpr_spin.setValue(2)
        assert dlg.settings()[2] == 2
        assert str(len(plan_tiles(512, 300, 2))) in dlg.fpr_info.text()

    def test_replace_mode_hides_split_setting(self):
        dlg = ImportDialog("small.png", (100, 100), ["act1"], "act1", allow_split=False, title="替换图像")
        assert dlg.fpr_widget.isHidden()
        assert dlg.settings()[2] == 0
        assert dlg.windowTitle() == "替换图像"


class TestContextMenu:
    def test_frame_menu_items_and_dispatch(self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(viewer_mod, "QMenu", _FakeMenu)
        calls = _record_frame_actions(monkeypatch)
        _add_frames(viewer, 3)

        for pick in range(6):
            _FakeMenu.pick = pick
            viewer._show_frame_menu(1, QPoint(0, 0))

        texts = [a.text() for a in _last_menu().actions]
        assert any("之前插入" in t for t in texts)
        assert any("之后插入" in t for t in texts)
        assert any("替换" in t for t in texts)
        assert any("删除" in t for t in texts)
        assert any("导出" in t for t in texts)
        assert any("偏移" in t for t in texts)
        assert calls == [
            ("_insert_frames_at", (1,)),
            ("_insert_frames_at", (2,)),
            ("_replace_frame_at", (1,)),
            ("_delete_frame_at", (1,)),
            ("_export_frame_at", (1,)),
            ("_edit_offset_at", (1,)),
        ]

    def test_blank_area_appends(self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(viewer_mod, "QMenu", _FakeMenu)
        calls = _record_frame_actions(monkeypatch)
        _add_frames(viewer, 3)
        _FakeMenu.pick = 0
        viewer._on_frame_table_context_menu(QPoint(0, 10000))
        assert calls[-1] == ("_insert_frames_at", (3,))
        assert "末尾插入" in _last_menu().actions[0].text()

    def test_right_click_on_row_targets_that_frame(
        self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch, qapp: QApplication
    ):
        monkeypatch.setattr(viewer_mod, "QMenu", _FakeMenu)
        calls = _record_frame_actions(monkeypatch)
        _add_frames(viewer, 3)
        viewer.resize(1000, 700)
        viewer.show()
        qapp.processEvents()
        y = viewer.frame_table.rowViewportPosition(1) + 5
        _FakeMenu.pick = 2  # 「替换此帧图像」
        try:
            viewer._on_frame_table_context_menu(QPoint(5, y))
            assert calls[-1] == ("_replace_frame_at", (1,))
        finally:
            viewer.hide()

    def test_no_document_offers_new(
        self, monkeypatch: pytest.MonkeyPatch, qapp: QApplication
    ):
        assert qapp is not None
        monkeypatch.setattr(viewer_mod, "QMenu", _FakeMenu)
        calls = _record_frame_actions(monkeypatch)
        window = Dc6Viewer()
        _FakeMenu.pick = 0
        window._show_frame_menu(None, QPoint(0, 0))
        assert calls[-1] == ("_new_dc6", ())
        window.close()

    def test_thumbnail_bar_is_wired_to_same_menu(self, viewer: Dc6Viewer):
        assert viewer.thumb_bar.context_menu_requested == viewer._show_frame_menu


class TestFrameActionsThroughViewer:
    def test_large_image_insert_becomes_multiple_frames(
        self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch
    ):
        img = QImage(300, 20, QImage.Format.Format_RGB32)
        img.fill(QColor(120, 70, 30).rgb())
        frames = frames_from_image(img, load_palette("act1"), 100.0, 2)
        monkeypatch.setattr(viewer, "_choose_image_frames", lambda **_kwargs: (frames, "act1"))
        _add_frames(viewer, 2)

        viewer._insert_frames_at(1)

        model = _model(viewer)
        assert model.total_frames() == 4
        assert [f.width for f in model.frames][1:3] == [256, 44]
        assert viewer.frame_table.rowCount() == 4
        assert viewer.windowTitle().endswith("*")

    def test_insert_real_large_image_end_to_end(
        self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(viewer_mod, "QFileDialog", _FakeFileDialog)
        monkeypatch.setattr(viewer_mod.ImportDialog, "exec", lambda _self: QDialog.DialogCode.Accepted)
        _add_frames(viewer, 2)

        img = QImage(300, 20, QImage.Format.Format_RGB32)
        img.fill(QColor(120, 70, 30).rgb())
        path = tmp_path / "wide.png"
        assert img.save(str(path))
        _FakeFileDialog.path = str(path)

        viewer._insert_frames_at(1)

        model = _model(viewer)
        assert model.total_frames() == 4  # 原有 2 帧 + 300x20 拆出的 2 帧
        assert [f.width for f in model.frames][1:3] == [256, 44]
        assert viewer.frame_table.rowCount() == 4

    def test_replace_then_delete(self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch):
        _add_frames(viewer, 2)
        replacement = FrameInfo(width=6, height=6, pixels=bytearray(36))
        monkeypatch.setattr(viewer, "_choose_image_frames", lambda **_kwargs: ([replacement], "act1"))

        viewer._replace_frame_at(0)
        assert _model(viewer).frames[0].width == 6

        viewer._delete_frame_at(0)
        assert _model(viewer).total_frames() == 1
        assert viewer.frame_table.rowCount() == 1

    def test_replace_rejects_oversized_image(
        self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(viewer_mod, "QMessageBox", _FakeMessageBox)
        monkeypatch.setattr(viewer_mod, "QFileDialog", _FakeFileDialog)
        _FakeMessageBox.calls.clear()

        large = tmp_path / "large.png"
        img = QImage(300, 20, QImage.Format.Format_RGB32)
        img.fill(QColor(10, 20, 30).rgb())
        assert img.save(str(large))
        _FakeFileDialog.path = str(large)

        assert viewer._choose_image_frames(allow_split=False) is None
        assert any(title == "替换失败" for title, _ in _FakeMessageBox.calls)

    def test_export_single_frame(
        self, viewer: Dc6Viewer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(viewer_mod, "QMessageBox", _FakeMessageBox)
        monkeypatch.setattr(viewer_mod, "QFileDialog", _FakeFileDialog)
        _add_frames(viewer, 1)
        dest = tmp_path / "one.bmp"
        _FakeFileDialog.path = str(dest)
        _FakeFileDialog.selected_filter = "BMP 索引色 (*.bmp)"

        viewer._export_frame_at(0)

        assert dest.is_file()
        assert dest.read_bytes()[:2] == b"BM"

    def test_directory_flag_disables_action(self, qapp: QApplication):
        assert qapp is not None
        dc6 = Dc6File.blank()
        dc6.frames.append(FrameInfo(width=2, height=2, pixels=bytearray(4)))
        window = show_dc6_window(build_dc6_bytes(dc6), enable_directory=False)
        try:
            assert not window.open_dir_act.isEnabled()
        finally:
            window.close()
