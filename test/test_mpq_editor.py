"""MPQ 编辑器的单元测试。

覆盖：路径大小写工具、文件名编解码（乱码修复）、特殊文件信息、
列表文件扫描、模型与 StormLib 的集成，以及树/列表控件的大小写
不敏感合并。
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parent.parent))

from mpq_editor.listfiles import candidate_dirs, scan_listfiles
from mpq_editor.mpq_model import MPQModel
from mpq_editor.name_codec import MpqNameCodec
from mpq_editor.path_utils import (
    build_dir_map,
    canonical_by_fold,
    fold,
    is_under,
    is_under_or_eq,
    normalize_sep,
    relative_to,
)
from mpq_editor.special import special_file_info

# ---------------------------------------------------------------------------
# 测试用 MPQ 构造工具
# ---------------------------------------------------------------------------

MPQ_CREATE_LISTFILE = 0x00100000
MPQ_CREATE_ATTRIBUTES = 0x00200000
MPQ_CREATE_ARCHIVE_V3 = 0x00030000


def make_mpq(
    path: Path,
    entries: dict[bytes, bytes],
    *,
    attributes: bool = False,
    write_listfile: bool = True,
) -> None:
    """创建一个测试用 MPQ，entries 的键是原始字节文件名。"""
    from stormlibpy._stormlib import MPQFileFlags, stormlib

    flags = MPQ_CREATE_ARCHIVE_V3 | (MPQ_CREATE_ATTRIBUTES if attributes else 0)
    hMpq = ctypes.c_void_p()
    assert stormlib.SFileCreateArchive(str(path), flags, 128, ctypes.byref(hMpq))
    for raw_name, data in entries.items():
        hFile = ctypes.c_void_p()
        assert stormlib.SFileCreateFile(
            hMpq,
            ctypes.c_char_p(raw_name),
            0,
            len(data),
            0,
            MPQFileFlags.MPQ_COMPRESSION_ZLIB,
            ctypes.byref(hFile),
        )
        buf = ctypes.create_string_buffer(data)
        assert stormlib.SFileWriteFile(hFile, buf, len(data), MPQFileFlags.MPQ_COMPRESSION_ZLIB)
        stormlib.SFileCloseFile(hFile)
    if write_listfile and entries:
        lf = path.parent / (path.name + ".listfile.txt")
        lf.write_bytes(b"\r\n".join(entries) + b"\r\n")
        assert stormlib.SFileAddListFile(hMpq, str(lf)) == 0
        lf.unlink()
    stormlib.SFileCloseArchive(hMpq)


def read_file(model: MPQModel, name: str) -> bytes:
    """读取 MPQ 内文件（测试辅助，保证 _mpq 非空）。"""
    assert model._mpq is not None
    return model._mpq.read_file(name)


# ---------------------------------------------------------------------------
# path_utils
# ---------------------------------------------------------------------------


class TestPathUtils:
    def test_normalize_sep(self):
        assert normalize_sep("a\\b\\c") == "a/b/c"
        assert normalize_sep("a/b") == "a/b"

    def test_fold(self):
        assert fold("DaTa\\Global") == "data/global"
        assert fold("a/b") == "a/b"

    def test_relative_to(self):
        assert relative_to("data/global/a.txt", "data") == "global/a.txt"
        assert relative_to("DATA/GLOBAL/A.TXT", "data/global") == "A.TXT"
        assert relative_to("data\\global\\a.txt", "Data/Global") == "a.txt"
        # 相等或不在目录下时返回 None
        assert relative_to("data/global", "data/global") is None
        assert relative_to("data/globalx/a", "data/global") is None
        assert relative_to("other/a", "data") is None
        # 根目录
        assert relative_to("a.txt", "") == "a.txt"
        assert relative_to("data/a.txt", "") == "data/a.txt"

    def test_is_under(self):
        assert is_under("DATA/GLOBAL/A.TXT", "data/global")
        assert is_under("data\\global\\a.txt", "data")
        assert not is_under("data/global", "data/global")
        assert not is_under("data/globalx", "data/global")
        assert is_under("anything", "")

    def test_is_under_or_eq(self):
        assert is_under_or_eq("Data/Global", "data/global")
        assert is_under_or_eq("data/global/a", "DATA/GLOBAL")
        assert not is_under_or_eq("data/globalx", "data/global")

    def test_canonical_by_fold_majority(self):
        files = ["data/Excel/a.txt", "data/Excel/b.txt", "data/excel/c.txt"]
        m = canonical_by_fold(files)
        assert len(m) == 3
        # data/excel 只出现 1 次，data/Excel 出现 2 次 -> 后者为规范形式
        assert m[fold("data/excel/a.txt")] == "data/Excel/a.txt"
        assert m[fold("data/excel/c.txt")] == "data/excel/c.txt"

    def test_canonical_by_fold_tie_is_deterministic(self):
        m = canonical_by_fold(["B.txt", "a.txt", "A.TXT", "b.txt"])
        assert m[fold("a.txt")] == "A.TXT"
        assert m[fold("b.txt")] == "B.txt"

    def test_canonical_by_fold_keeps_original_string(self):
        # 规范形式保留原始字符串（含分隔符），与编码往返映射一致
        m = canonical_by_fold(["data\\global\\a.txt"])
        assert m[fold("data/global/a.txt")] == "data\\global\\a.txt"

    def test_build_dir_map_merges_case(self):
        files = [
            "data/global/Excel/a.txt",
            "data/global/Excel/b.txt",
            "data/global/excel/c.txt",
            "readme.txt",
        ]
        m = build_dir_map(files)
        assert m[fold("data")] == "data"
        assert m[fold("data/global")] == "data/global"
        # Excel 出现 2 次，excel 出现 1 次 -> 合并为 Excel
        assert m[fold("data/global/excel")] == "data/global/Excel"
        assert len(m) == 3

    def test_build_dir_map_includes_empty_dirs(self):
        m = build_dir_map(["data/a.txt"], ["New/Empty/Dir"])
        assert m[fold("new")] == "New"
        assert m[fold("new/empty")] == "New/Empty"
        assert m[fold("new/empty/dir")] == "New/Empty/Dir"


# ---------------------------------------------------------------------------
# name_codec
# ---------------------------------------------------------------------------


class TestNameCodec:
    def test_ascii_roundtrip(self):
        codec = MpqNameCodec("utf-8")
        name = codec.decode(b"data\\global\\a.txt")
        assert name == "data\\global\\a.txt"
        assert codec.encode(name) == b"data\\global\\a.txt"

    def test_gbk_name_decoded_correctly(self):
        raw = "数据\\中文文件名.txt".encode("gbk")
        codec = MpqNameCodec("utf-8")
        name = codec.decode(raw)
        assert name == "数据\\中文文件名.txt"
        assert codec.encode(name) == raw

    def test_utf8_name_decoded_correctly(self):
        raw = "数据.txt".encode()
        codec = MpqNameCodec("utf-8")
        assert codec.decode(raw) == "数据.txt"
        assert codec.encode("数据.txt") == raw

    def test_mixed_names_independent(self):
        gbk_raw = "中文.dat".encode("gbk")
        utf8_raw = "文件.txt".encode()
        codec = MpqNameCodec("utf-8")
        gbk_name = codec.decode(gbk_raw)
        utf8_name = codec.decode(utf8_raw)
        assert codec.encode(gbk_name) == gbk_raw
        assert codec.encode(utf8_name) == utf8_raw

    def test_same_display_string_last_decode_wins(self):
        # 极端情况：不同字节形式解码为同一显示串（无法区分），后解码的编码生效
        gbk_raw = "中文.dat".encode("gbk")
        utf8_raw = "中文.dat".encode()
        codec = MpqNameCodec("utf-8")
        codec.decode(gbk_raw)
        name = codec.decode(utf8_raw)
        assert codec.encode(name) == utf8_raw

    def test_selected_encoding_tried_first(self):
        raw = "中文.txt".encode("gbk")
        codec = MpqNameCodec("gbk")
        assert codec.decode(raw) == "中文.txt"
        assert codec.encode("中文.txt") == raw

    def test_surrogateescape_fallback_roundtrip(self):
        raw = b"\xff\xfe\xfa.txt"  # 任何严格编码都无法解码
        codec = MpqNameCodec("utf-8")
        name = codec.decode(raw)
        assert codec.encode(name) == raw

    def test_set_encoding_keeps_known_names(self):
        codec = MpqNameCodec("gbk")
        raw = "中文.txt".encode("gbk")
        name = codec.decode(raw)
        codec.set_encoding("utf-8")
        # 已记录的名字仍按原编码往返
        assert codec.encode(name) == raw
        # 新名字用新默认编码
        assert codec.decode(b"abc.txt") == "abc.txt"


# ---------------------------------------------------------------------------
# special
# ---------------------------------------------------------------------------


class TestSpecial:
    def test_known_specials(self):
        for name in ("(listfile)", "(LISTFILE)", "(attributes)", "(Signature)"):
            info = special_file_info(name)
            assert info is not None
        listfile_info = special_file_info("(listfile)")
        assert listfile_info is not None and listfile_info.is_binary is False
        attributes_info = special_file_info("(attributes)")
        assert attributes_info is not None and attributes_info.is_binary is True
        signature_info = special_file_info("(signature)")
        assert signature_info is not None and signature_info.is_binary is True

    def test_special_with_path(self):
        info = special_file_info("data\\global\\(attributes)")
        assert info is not None and info.is_binary

    def test_normal_file_is_not_special(self):
        assert special_file_info("data/global/a.txt") is None
        assert special_file_info("listfile.txt") is None


# ---------------------------------------------------------------------------
# listfiles
# ---------------------------------------------------------------------------


class TestListfiles:
    def test_scan_dev_mode_finds_repo_listfiles(self):
        files = scan_listfiles()
        names = {f.name.casefold() for f in files}
        assert "diablo ii.txt" in names
        assert all(f.suffix == ".txt" for f in files)

    def test_frozen_mode_priority_and_dedup(self, tmp_path, monkeypatch):
        exe_dir = tmp_path / "app"
        meipass_dir = tmp_path / "internal"
        exe_dir.mkdir()
        meipass_dir.mkdir()
        (exe_dir / "Diablo II.txt").write_bytes(b"a\r\n")
        (exe_dir / "common.txt").write_bytes(b"exe version\r\n")
        (meipass_dir / "Common.txt").write_bytes(b"meipass version\r\n")
        (meipass_dir / "Extra.txt").write_bytes(b"x\r\n")

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "MPQEditor.exe"), raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass_dir), raising=False)
        try:
            dirs = candidate_dirs()
            assert exe_dir / "Listfiles" not in dirs  # 无 Listfiles 子目录
            scanned = scan_listfiles()
        finally:
            monkeypatch.delattr(sys, "frozen", raising=False)
            monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        # 无 Listfiles 子目录时扫描不到
        assert scanned == []

        (exe_dir / "Listfiles").mkdir()
        (exe_dir / "Listfiles" / "Zeta.txt").write_text("z\r\n")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass_dir), raising=False)
        try:
            scanned = scan_listfiles()
        finally:
            monkeypatch.delattr(sys, "frozen", raising=False)
            monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        names = [f.name.casefold() for f in scanned]
        assert "zeta.txt" in names
        assert names == sorted(names)

    def test_frozen_prefers_exe_dir_on_conflict(self, tmp_path, monkeypatch):
        exe_dir = tmp_path / "app"
        meipass_dir = tmp_path / "internal"
        (exe_dir / "Listfiles").mkdir(parents=True)
        (meipass_dir / "Listfiles").mkdir(parents=True)
        (exe_dir / "Listfiles" / "Dup.txt").write_bytes(b"exe\r\n")
        (meipass_dir / "Listfiles" / "Dup.txt").write_bytes(b"meipass\r\n")

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe_dir / "MPQEditor.exe"), raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass_dir), raising=False)
        try:
            scanned = scan_listfiles()
        finally:
            monkeypatch.delattr(sys, "frozen", raising=False)
            monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        assert [f.name for f in scanned] == ["Dup.txt"]
        assert scanned[0].read_bytes() == b"exe\r\n"


# ---------------------------------------------------------------------------
# MPQModel + StormLib 集成
# ---------------------------------------------------------------------------


class TestMPQModelIntegration:
    def test_open_and_list(self, tmp_path):
        mpq = tmp_path / "t.mpq"
        make_mpq(
            mpq,
            {
                b"data\\global\\Excel\\a.txt": b"A",
                b"data\\global\\excel\\b.txt": b"B",
                b"root.txt": b"R",
            },
        )
        model = MPQModel()
        model.open(mpq)
        files = model.list_files()
        folds = [fold(f) for f in files]
        assert "data/global/excel/a.txt" in folds
        assert "data/global/excel/b.txt" in folds
        assert "root.txt" in folds
        assert "(listfile)" in folds

        # 目录大小写合并：data/global/excel 只有一个规范形式
        dmap = model.dir_map()
        assert dmap[fold("data/global/excel")] == "data/global/Excel"
        assert model.has_dir("DATA/GLOBAL/EXCEL")
        assert not model.has_dir("nonexistent")

        # 读取（大小写不同的名字也能读）
        assert read_file(model, "data\\global\\Excel\\a.txt") == b"A"
        assert read_file(model, "DATA\\GLOBAL\\EXCEL\\B.TXT") == b"B"
        model.close()

    def test_dir_map_case_insensitive_lookup(self, tmp_path):
        mpq = tmp_path / "t.mpq"
        make_mpq(mpq, {b"aaa\\Bbb\\c.txt": b"x", b"aaa\\bbb\\d.txt": b"y"})
        model = MPQModel()
        model.open(mpq)
        dmap = model.dir_map()
        # 两个文件各投一票，并列时取字典序最小（B < b）
        assert dmap[fold("aaa/bbb")] == "aaa/Bbb"
        assert dmap[fold("AAA/BBB")] == "aaa/Bbb"
        model.close()

    def test_apply_listfile_names_unnamed_files(self):
        # 2.mpq 内含未命名的 FileXXXXXXX 占位文件，应用列表文件后应减少
        repo = Path(__file__).parent.parent
        mpq = repo / "2.mpq"
        if not mpq.exists():
            pytest.skip("sample 2.mpq not available")
        lf = repo / "Listfiles" / "Diablo II.txt"

        model = MPQModel()
        model.open(mpq)
        before = model.list_files()
        ph_before = [f for f in before if fold(f).startswith("file")]
        assert ph_before, "样本应包含未命名文件"
        model.close()

        model.open(mpq, [str(lf)])
        after = model.list_files()
        ph_after = [f for f in after if fold(f).startswith("file")]
        assert model.listfile_errors == []
        assert len(ph_after) < len(ph_before)
        # 新识别出的名字使用真实路径
        assert any(fold(f).startswith("data/") for f in after)
        model.close()

    def test_apply_listfile_missing_collects_error(self, tmp_path):
        mpq = tmp_path / "t.mpq"
        make_mpq(mpq, {b"a.txt": b"a"})
        model = MPQModel()
        model.open(mpq, [str(tmp_path / "no-such-listfile.txt")])
        assert model.listfile_errors
        # 档案仍可正常列出
        assert fold("a.txt") in [fold(f) for f in model.list_files()]
        model.close()

    def test_gbk_names_display_and_roundtrip(self, tmp_path):
        gbk_name = "数据\\中文文件.txt".encode("gbk")
        mpq = tmp_path / "gbk.mpq"
        make_mpq(mpq, {gbk_name: b"content", b"plain.txt": b"p"})
        model = MPQModel()
        model.open(mpq)  # 默认 utf-8，应自动回退到 gbk 解码
        files = model.list_files()
        assert "数据\\中文文件.txt" in files
        assert read_file(model, "数据\\中文文件.txt") == b"content"

        # 切换编码后仍然可以往返
        model.encoding = "gbk"
        assert "数据\\中文文件.txt" in model.list_files()
        assert read_file(model, "数据\\中文文件.txt") == b"content"
        model.close()

    def test_attributes_binary_special(self, tmp_path):
        mpq = tmp_path / "attr.mpq"
        make_mpq(mpq, {b"data\\hello.txt": b"hi"}, attributes=True)
        model = MPQModel()
        model.open(mpq)
        folds = [fold(f) for f in model.list_files()]
        assert "(attributes)" in folds
        info = special_file_info("(attributes)")
        assert info is not None and info.is_binary
        data = read_file(model, "(attributes)")
        assert data  # 二进制内容
        model.close()

    def test_create_directory_and_empty_dir_ops(self, tmp_path):
        mpq = tmp_path / "t.mpq"
        make_mpq(mpq, {b"a.txt": b"a"})
        model = MPQModel()
        model.open(mpq)
        model.create_directory("New/Dir")
        assert model.has_dir("new/dir")
        model.rename_empty_dir("New/Dir", "Renamed")
        assert model.has_dir("renamed")
        assert not model.has_dir("new/dir")
        model.remove_empty_dirs_under("renamed")
        assert not model.has_dir("renamed")
        model.close()

    def test_save_and_reopen(self, tmp_path):
        mpq = tmp_path / "t.mpq"
        make_mpq(mpq, {b"old.txt": b"old"})
        model = MPQModel()
        model.open(mpq)
        model.add_file_data(b"new", "added\\new.txt")
        model.save()
        model.open(mpq)
        folds = [fold(f) for f in model.list_files()]
        assert "added/new.txt" in folds
        assert "old.txt" in folds
        assert read_file(model, "added\\new.txt") == b"new"
        model.close()

    def test_extract_file(self, tmp_path):
        mpq = tmp_path / "t.mpq"
        make_mpq(mpq, {b"data\\sub\\a.txt": b"AAA"})
        model = MPQModel()
        model.open(mpq)
        out = tmp_path / "out"
        model.extract_file("data\\sub\\a.txt", out)
        assert (out / "data/sub/a.txt").read_bytes() == b"AAA"
        model.close()

    def test_delete_and_rename(self, tmp_path):
        mpq = tmp_path / "t.mpq"
        make_mpq(mpq, {b"data\\x\\a.txt": b"A", b"b.txt": b"B"})
        model = MPQModel()
        model.open(mpq)
        model.rename_file("b.txt", "renamed.txt")
        model.delete_file("data\\x\\a.txt")
        folds = [fold(f) for f in model.list_files()]
        assert "renamed.txt" in folds
        assert "b.txt" not in folds
        assert "data/x/a.txt" not in folds
        model.close()


# ---------------------------------------------------------------------------
# 控件（树 / 文件列表）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _find_child(item, text: str):
    for i in range(item.childCount()):
        child = item.child(i)
        if child.text(0).casefold() == text.casefold():
            return child
    return None


class TestWidgets:
    @pytest.fixture()
    def mixed_model(self, tmp_path):
        mpq = tmp_path / "mixed.mpq"
        # excel 写法出现 2 次（b.txt、sub/c.txt），Excel 出现 1 次 -> 规范形式为 excel
        make_mpq(
            mpq,
            {
                b"data\\global\\Excel\\a.txt": b"A",
                b"data\\global\\excel\\b.txt": b"B",
                b"data\\global\\excel\\sub\\c.txt": b"C",
            },
        )
        model = MPQModel()
        model.open(mpq)
        yield model
        model.close()

    @pytest.mark.usefixtures("qapp")
    def test_tree_merges_case_dirs(self, mixed_model):
        from PySide6.QtCore import Qt

        from mpq_editor.tree_widget import TreeWidget

        tree = TreeWidget(mixed_model)
        tree.refresh()
        root = tree.topLevelItem(0)
        assert root is not None
        data = _find_child(root, "data")
        assert data is not None
        assert data.data(0, Qt.ItemDataRole.UserRole) == "data"
        glob = _find_child(data, "global")
        assert glob is not None
        excel_nodes = [
            glob.child(i) for i in range(glob.childCount()) if glob.child(i).text(0).casefold() == "excel"
        ]
        assert len(excel_nodes) == 1, "仅大小写不同的目录应合并为一个节点"
        assert excel_nodes[0].data(0, Qt.ItemDataRole.UserRole) == "data/global/excel"

    @pytest.mark.usefixtures("qapp")
    def test_tree_select_path_case_insensitive(self, mixed_model):
        from PySide6.QtCore import Qt

        from mpq_editor.tree_widget import TreeWidget

        tree = TreeWidget(mixed_model)
        tree.refresh()
        tree.select_path("DATA/GLOBAL/EXCEL")
        current = tree.currentItem()
        assert current is not None
        assert current.data(0, Qt.ItemDataRole.UserRole) == "data/global/excel"

    @pytest.mark.usefixtures("qapp")
    def test_file_list_case_insensitive_browsing(self, mixed_model):
        from PySide6.QtCore import Qt

        from mpq_editor.file_list_widget import FileListWidget

        w = FileListWidget(mixed_model)

        def row_data(kind: str) -> list[str]:
            rows = []
            for i in range(w.topLevelItemCount()):
                item = w.topLevelItem(i)
                if item is None:
                    continue
                ud = item.data(0, Qt.ItemDataRole.UserRole) or ""
                if ud.startswith(kind):
                    rows.append(ud)
            return rows

        # 根目录：一个 data 目录
        w.refresh()
        assert row_data("dir:") == ["dir:data"]

        # 用不同大小写浏览 data/global：Excel/excel 合并为一个目录行
        w.current_dir = "DATA/GLOBAL"
        assert row_data("dir:") == ["dir:data/global/excel"]
        assert row_data("file:") == []

        # data/global/Excel 下能看到两种大小写路径的文件
        w.current_dir = "data/global/Excel"
        files = sorted(row_data("file:"))
        assert files == ["file:data\\global\\Excel\\a.txt", "file:data\\global\\excel\\b.txt"]

        # 子目录 sub 正常显示
        assert "dir:data/global/excel/sub" in row_data("dir:")

        # 无效目录向上回退
        w.current_dir = "no/such/dir"
        assert w.current_dir == ""

    @pytest.mark.usefixtures("qapp")
    def test_file_list_special_labels(self, tmp_path):
        from mpq_editor.file_list_widget import FileListWidget

        mpq = tmp_path / "attr.mpq"
        make_mpq(mpq, {b"a.txt": b"a"}, attributes=True)
        model = MPQModel()
        model.open(mpq)
        w = FileListWidget(model)
        w.refresh()
        labels = {}
        for i in range(w.topLevelItemCount()):
            item = w.topLevelItem(i)
            if item is not None:
                labels[item.text(0)] = item.text(1)
        assert labels.get("(attributes)") == "MPQ 校验数据"
        assert labels.get("(listfile)") == "MPQ 列表文件"
        assert labels.get("a.txt", "").startswith("文件")
        model.close()
