from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from stormlibpy._stormlib import MPQFileFlags, SFileFindDataStrc, stormlib

from mpq_editor.name_codec import MpqNameCodec
from mpq_editor.path_utils import build_dir_map, canonical_by_fold, fold, is_under_or_eq, normalize_sep

MPQ_OPEN_CREATE = 0x00002000
MPQ_CREATE_LISTFILE = 0x00100000
MPQ_CREATE_ATTRIBUTES = 0x00200000
MPQ_CREATE_ARCHIVE_V3 = 0x00030000

stormlib.SFileCreateArchive.argtypes = [
    ctypes.c_wchar_p,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_void_p),
]
stormlib.SFileCreateArchive.restype = ctypes.c_bool

stormlib.SFileCompactArchive.argtypes = [
    ctypes.c_void_p,
    ctypes.c_wchar_p,
    ctypes.c_bool,
]
stormlib.SFileCompactArchive.restype = ctypes.c_bool

stormlib.SFileFlushArchive.argtypes = [ctypes.c_void_p]
stormlib.SFileFlushArchive.restype = ctypes.c_bool

stormlib.SFileAddListFile.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
stormlib.SFileAddListFile.restype = ctypes.c_uint32


def _check(result, msg=""):
    if not result:
        err = stormlib.GetLastError()
        raise RuntimeError(f"{msg} (错误 {err})")
    return result


class MPQHandle:
    def __init__(self, path: str, encoding: str = "utf-8"):
        self.original_path = path
        self.path = path
        self.temp_path: str | None = None
        self.hMpq: ctypes.wintypes.HANDLE | None = None
        self.codec = MpqNameCodec(encoding)

    @property
    def encoding(self) -> str:
        return self.codec.default_encoding

    @encoding.setter
    def encoding(self, name: str) -> None:
        self.codec.set_encoding(name)

    def _encode_name(self, name: str) -> bytes:
        return self.codec.encode(name)

    def _decode_name(self, raw: bytes) -> str:
        return self.codec.decode(raw)

    def _open(self, filepath: str) -> None:
        self.hMpq = ctypes.c_void_p()
        _check(
            stormlib.SFileOpenArchive(filepath, 0, 0, ctypes.byref(self.hMpq)),
            "打开 MPQ 失败",
        )

    def open_existing(self) -> None:
        self.temp_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mpq").name
        shutil.copy2(self.original_path, self.temp_path)
        self.path = self.temp_path
        self._open(self.temp_path)

    def create_new(self) -> None:
        self.hMpq = ctypes.c_void_p()
        _check(
            stormlib.SFileCreateArchive(
                self.original_path,
                MPQ_CREATE_LISTFILE | MPQ_CREATE_ATTRIBUTES | MPQ_CREATE_ARCHIVE_V3,
                128,
                ctypes.byref(self.hMpq),
            ),
            "创建 MPQ 失败",
        )
        self.path = self.original_path

    def _close_handle(self) -> None:
        if self.hMpq:
            stormlib.SFileCloseArchive(self.hMpq)
            self.hMpq = None

    def close(self) -> None:
        self._close_handle()
        if self.temp_path and os.path.exists(self.temp_path):
            os.unlink(self.temp_path)
            self.temp_path = None

    def list_files(self) -> list[str]:
        """枚举档案内所有已命名的文件。

        StormLib 会自动解析档案内 (listfile) 并为未命名的文件生成
        FileXXXXXXX.xxx 形式的占位名。
        """
        names: list[str] = []
        find_data = SFileFindDataStrc()
        hFind = stormlib.SFileFindFirstFile(self.hMpq, b"*", ctypes.byref(find_data), None)
        if hFind:
            while True:
                raw = bytes(find_data.cFileName)
                if raw:
                    names.append(self._decode_name(raw))
                if not stormlib.SFileFindNextFile(hFind, ctypes.byref(find_data)):
                    break
            stormlib.SFileFindClose(hFind)
        return names

    def apply_listfile(self, listfile_path: str | Path) -> None:
        """应用外部列表文件，识别档案内未命名的文件。"""
        result = stormlib.SFileAddListFile(self.hMpq, str(listfile_path))
        if result != 0:
            raise RuntimeError(f"应用列表文件 {listfile_path} 失败 (错误 {result})")

    def get_file_size(self, filename: str) -> int:
        c_filename = ctypes.c_char_p(self._encode_name(filename))
        hFile = ctypes.c_void_p()
        if not stormlib.SFileOpenFileEx(self.hMpq, c_filename, 0, ctypes.byref(hFile)):
            return 0
        dwFileSize = stormlib.SFileGetFileSize(hFile, None)
        stormlib.SFileCloseFile(hFile)
        return dwFileSize

    def read_file(self, filename: str) -> bytes:
        hFile = ctypes.c_void_p()
        c_filename = ctypes.c_char_p(self._encode_name(filename))
        _check(
            stormlib.SFileOpenFileEx(self.hMpq, c_filename, 0, ctypes.byref(hFile)),
            f"读取文件 {filename} 失败",
        )
        dwFileSize = stormlib.SFileGetFileSize(hFile, None)

        bio = io.BytesIO()
        buf = (ctypes.c_ubyte * dwFileSize)()
        bytes_read = ctypes.wintypes.DWORD(1)
        bytes_requested = dwFileSize
        while bytes_read.value:
            if stormlib.SFileReadFile(
                hFile,
                ctypes.byref(buf),
                bytes_requested,
                ctypes.byref(bytes_read),
                None,
            ):
                bytes_requested -= bytes_read.value
                bio.write(bytes(buf[: bytes_read.value]))

        stormlib.SFileCloseFile(hFile)
        bio.seek(0)
        return bio.read()

    def add_file(self, local_path: str, archived_name: str) -> None:
        file_flags = MPQFileFlags.MPQ_FILE_COMPRESS | MPQFileFlags.MPQ_FILE_REPLACEEXISTING
        compression = MPQFileFlags.MPQ_COMPRESSION_ZLIB
        _check(
            stormlib.SFileAddFileEx(
                self.hMpq,
                local_path,
                self._encode_name(archived_name),
                file_flags,
                compression,
                MPQFileFlags.MPQ_COMPRESSION_NEXT_SAME,
            ),
            f"添加文件 {archived_name} 失败",
        )

    def write_file(self, filename: str, data: bytes) -> None:
        c_filename = ctypes.c_char_p(self._encode_name(filename))

        if stormlib.SFileHasFile(self.hMpq, c_filename):
            _check(stormlib.SFileRemoveFile(self.hMpq, c_filename, 0))

        hFile = ctypes.c_void_p()
        _check(
            stormlib.SFileCreateFile(
                self.hMpq,
                c_filename,
                0,
                len(data),
                0,
                MPQFileFlags.MPQ_COMPRESSION_ZLIB,
                ctypes.byref(hFile),
            ),
            f"创建文件 {filename} 失败",
        )

        pData = ctypes.c_char_p(data)
        _check(
            stormlib.SFileWriteFile(
                hFile,
                pData,
                len(data),
                MPQFileFlags.MPQ_COMPRESSION_ZLIB,
            ),
            f"写入文件 {filename} 失败",
        )
        stormlib.SFileCloseFile(hFile)

    def remove_file(self, filename: str) -> None:
        c_filename = ctypes.c_char_p(self._encode_name(filename))
        _check(
            stormlib.SFileRemoveFile(self.hMpq, c_filename, 0),
            f"删除文件 {filename} 失败",
        )

    def has_listfile(self) -> bool:
        filename = ctypes.c_char_p(b"(listfile)")
        return bool(stormlib.SFileHasFile(self.hMpq, filename))

    def write_listfile(self, files: list[str]) -> None:
        if not files:
            return
        encoded_lines = [self._encode_name(f) for f in files]
        content = b"\r\n".join(encoded_lines) + b"\r\n"

        tmp_path = tempfile.mktemp(suffix=".txt")
        with Path(tmp_path).open("wb") as tmp:
            tmp.write(content)
        result = stormlib.SFileAddListFile(self.hMpq, tmp_path)
        os.unlink(tmp_path)
        if result != 0:
            err = stormlib.GetLastError()
            raise RuntimeError(f"添加 (listfile) 失败 (错误 {err})")

    def compact(self) -> None:
        _check(stormlib.SFileFlushArchive(self.hMpq), "刷新失败")
        result = stormlib.SFileCompactArchive(self.hMpq, None, False)
        if not result:
            err = stormlib.GetLastError()
            if err == 10007:
                return
            raise RuntimeError(f"压缩失败 (错误 {err})")


ENCODINGS = ["utf-8", "gbk", "shift-jis", "euc-kr", "latin-1", "cp1252"]


class MPQModel(QObject):
    data_changed = Signal()

    def __init__(self):
        super().__init__()
        self._mpq: MPQHandle | None = None
        self.file_path: Path | None = None
        self._file_list: list[str] | None = None
        self._dir_map: dict[str, str] | None = None
        self._empty_dirs: set[str] = set()
        self.is_modified = False
        self._encoding = "utf-8"
        self.listfile_errors: list[str] = []

    @property
    def encoding(self) -> str:
        return self._encoding

    @encoding.setter
    def encoding(self, name: str):
        self._encoding = name
        if self._mpq:
            self._mpq.encoding = name
        self._invalidate_cache()
        self.data_changed.emit()

    def open(self, path: str | Path, listfile_paths: list[str] | None = None) -> None:
        """打开 MPQ；listfile_paths 中的列表文件用于识别未命名的文件。"""
        self.file_path = Path(path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        self._mpq = MPQHandle(str(self.file_path), self._encoding)
        self._mpq.open_existing()
        self.listfile_errors = []
        for lf in listfile_paths or []:
            try:
                self._mpq.apply_listfile(lf)
            except Exception as e:  # 单个列表文件失败不应中断打开
                self.listfile_errors.append(str(e))
        self._empty_dirs.clear()
        self.is_modified = False
        self._invalidate_cache()
        self.data_changed.emit()

    def create(self, path: str | Path) -> None:
        self.file_path = Path(path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._mpq = MPQHandle(str(self.file_path), self._encoding)
        self._mpq.create_new()
        self._empty_dirs.clear()
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def close(self) -> None:
        if self._mpq:
            self._mpq.close()
        self._mpq = None
        self.file_path = None
        self._empty_dirs.clear()
        self.is_modified = False
        self.listfile_errors = []
        self._invalidate_cache()
        self.data_changed.emit()

    def save(self, path: str | Path | None = None, compress: bool = False) -> None:
        if not self._mpq or not self.file_path:
            return
        try:
            self.add_listfile()
        except Exception:
            pass
        if compress:
            self._mpq.compact()
        temp_src = self._mpq.temp_path or self._mpq.path
        self._mpq._close_handle()

        save_path = Path(path) if path else self.file_path
        save_path.parent.mkdir(parents=True, exist_ok=True)
        if Path(temp_src).resolve() != save_path.resolve():
            shutil.copy2(temp_src, save_path)

        self.file_path = save_path
        self._mpq = MPQHandle(str(self.file_path), self._encoding)
        self._mpq.open_existing()
        self._invalidate_cache()
        self.is_modified = False
        self.data_changed.emit()

    def list_files(self) -> list[str]:
        """规范化的文件列表：合并仅大小写不同的名字，按忽略大小写排序。"""
        if self._file_list is None:
            raw: list[str] = []
            if self._mpq:
                try:
                    raw = self._mpq.list_files()
                except Exception:
                    raw = []
            canon = canonical_by_fold(raw)
            self._file_list = sorted(canon.values(), key=fold)
            self._dir_map = build_dir_map(self._file_list, self._empty_dirs)
        return self._file_list

    def dir_map(self) -> dict[str, str]:
        """返回 fold(目录) -> 规范目录路径 的映射（含空目录）。"""
        if self._dir_map is None:
            self.list_files()
        assert self._dir_map is not None
        return self._dir_map

    def has_dir(self, dir_path: str) -> bool:
        return fold(dir_path) in self.dir_map()

    def _invalidate_cache(self):
        self._file_list = None
        self._dir_map = None

    def get_file_size(self, mpq_path: str) -> int:
        if not self._mpq:
            return 0
        return self._mpq.get_file_size(mpq_path)

    def extract_file(self, mpq_path: str, dest_dir: str | Path) -> None:
        if not self._mpq:
            return
        dest = Path(dest_dir)
        data = self._mpq.read_file(mpq_path)
        out_path = dest / normalize_sep(mpq_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

    def add_file(self, local_path: str | Path, mpq_path: str) -> None:
        if not self._mpq:
            return
        local = Path(local_path)
        self._mpq.add_file(str(local.resolve()), mpq_path)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def add_file_data(self, data: bytes, mpq_path: str) -> None:
        if not self._mpq:
            return
        self._mpq.write_file(mpq_path, data)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def delete_file(self, mpq_path: str) -> None:
        if not self._mpq:
            return
        self._mpq.remove_file(mpq_path)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def rename_file(self, old_path: str, new_path: str) -> None:
        if not self._mpq:
            return
        data = self._mpq.read_file(old_path)
        self._mpq.remove_file(old_path)
        self._mpq.write_file(new_path, data)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def create_directory(self, mpq_path: str) -> None:
        if not self._mpq:
            return
        full = normalize_sep(mpq_path).strip("/")
        if not full or self.has_dir(full):
            return
        self._empty_dirs.add(full)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def has_listfile(self) -> bool:
        return self._mpq is not None and self._mpq.has_listfile()

    def add_listfile(self) -> None:
        if not self._mpq:
            return
        files = self.list_files()
        files = [f for f in files if fold(f) != "(listfile)"]
        self._mpq.write_listfile(files)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def remove_empty_dir(self, mpq_path: str) -> None:
        f = fold(mpq_path)
        self._empty_dirs = {d for d in self._empty_dirs if fold(d) != f}
        self._invalidate_cache()
        self.data_changed.emit()

    def remove_empty_dirs_under(self, dir_path: str) -> None:
        """删除 dir_path 之下（含自身）的所有空目录记录。"""
        self._empty_dirs = {d for d in self._empty_dirs if not is_under_or_eq(d, dir_path)}
        self._invalidate_cache()
        self.data_changed.emit()

    def rename_empty_dir(self, old_path: str, new_path: str) -> None:
        self._empty_dirs = {normalize_sep(new_path) if fold(d) == fold(old_path) else d for d in self._empty_dirs}
        self._invalidate_cache()
        self.data_changed.emit()

    def compress(self) -> None:
        if not self._mpq:
            return
        self._mpq.compact()
        self.is_modified = True

    @property
    def is_open(self) -> bool:
        return self._mpq is not None and self._mpq.hMpq is not None
