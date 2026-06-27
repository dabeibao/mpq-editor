from __future__ import annotations

import ctypes
import ctypes.wintypes
import io
import os
import shutil
import tempfile
from pathlib import Path

from stormlibpy._stormlib import MPQFileFlags, SFileFindDataStrc, stormlib

from PySide6.QtCore import QObject, Signal

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
        self.encoding = encoding

    def _encode_name(self, name: str) -> bytes:
        try:
            return name.encode(self.encoding, errors="surrogateescape")
        except UnicodeEncodeError:
            return name.encode("utf-8", errors="surrogateescape")

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
        archive_files = []

        filename = ctypes.c_char_p(b"(listfile)")
        if stormlib.SFileHasFile(self.hMpq, filename):
            try:
                data = self.read_file("(listfile)")
                archive_files = data.decode(self.encoding, errors="surrogateescape").splitlines()
            except Exception:
                pass

        find_data = SFileFindDataStrc()
        hFind = stormlib.SFileFindFirstFile(self.hMpq, b"*", ctypes.byref(find_data), None)
        if hFind:
            while True:
                name = find_data.cFileName.decode(self.encoding, errors="surrogateescape")
                if name and name != "(listfile)":
                    archive_files.append(name)
                if not stormlib.SFileFindNextFile(hFind, ctypes.byref(find_data)):
                    break
            stormlib.SFileFindClose(hFind)

        return sorted(set(archive_files))

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
        content = b"\r\n".join(encoded_lines)
        import tempfile

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
        self._empty_dirs: set[str] = set()
        self.is_modified = False
        self._encoding = "utf-8"

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

    def open(self, path: str | Path, file_list: list[str] | None = None) -> None:
        self.file_path = Path(path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        self._mpq = MPQHandle(str(self.file_path), self._encoding)
        self._mpq.open_existing()
        self._file_list = file_list
        self._empty_dirs.clear()
        self.is_modified = False
        self.data_changed.emit()

    def create(self, path: str | Path) -> None:
        self.file_path = Path(path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._mpq = MPQHandle(str(self.file_path), self._encoding)
        self._mpq.create_new()
        self._empty_dirs.clear()
        self.is_modified = True
        self.data_changed.emit()

    def close(self) -> None:
        if self._mpq:
            self._mpq.close()
        self._mpq = None
        self.file_path = None
        self._file_list = None
        self._empty_dirs.clear()
        self.is_modified = False
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
        if not self._mpq:
            return []
        if self._file_list is not None:
            return self._file_list
        try:
            self._file_list = self._mpq.list_files()
            return self._file_list
        except Exception:
            return []

    def _invalidate_cache(self):
        self._file_list = None

    def get_file_size(self, mpq_path: str) -> int:
        if not self._mpq:
            return 0
        return self._mpq.get_file_size(mpq_path)

    def extract_file(self, mpq_path: str, dest_dir: str | Path) -> None:
        if not self._mpq:
            return
        dest = Path(dest_dir)
        data = self._mpq.read_file(mpq_path)
        out_path = dest / mpq_path
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
        self._empty_dirs.add(mpq_path)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def has_listfile(self) -> bool:
        return self._mpq is not None and self._mpq.has_listfile()

    def add_listfile(self) -> None:
        if not self._mpq:
            return
        files = self.list_files()
        files = [f for f in files if f != "(listfile)"]
        self._mpq.write_listfile(files)
        self.is_modified = True
        self._invalidate_cache()
        self.data_changed.emit()

    def remove_empty_dir(self, mpq_path: str) -> None:
        self._empty_dirs.discard(mpq_path)
        self.data_changed.emit()

    def rename_empty_dir(self, old_path: str, new_path: str) -> None:
        self._empty_dirs.discard(old_path)
        self._empty_dirs.add(new_path)
        self.data_changed.emit()

    def compress(self) -> None:
        if not self._mpq:
            return
        self._mpq.compact()
        self.is_modified = True

    @property
    def is_open(self) -> bool:
        return self._mpq is not None and self._mpq.hMpq is not None
