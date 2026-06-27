from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"

EXCLUDE_MODULES = [
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQmlModels",
    "PySide6.QtQmlMeta",
    "PySide6.QtQmlWorkerScript",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtPdf",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtHelp",
    "PySide6.QtXml",
    "PySide6.QtXmlPatterns",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtPrintSupport",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtHttpServer",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "tornado",
    "PIL",
    "matplotlib",
    "scipy",
    "numpy",
    "pandas",
]


def _rel(path: Path) -> str:
    """Return path relative to build/ directory."""
    build = ROOT / "build"
    return Path(os.path.relpath(path, build)).as_posix()


def _make_spec(
    name: str, script: str, icon_name: str, extra_binaries: list[str] | None = None
) -> str:
    script_rel = _rel(ROOT / script)
    icon_path = ROOT / "resources" / icon_name
    icon_rel = _rel(icon_path)
    pal_rel = _rel(ROOT / "dc6" / "pal")
    resources_rel = _rel(ROOT / "resources")

    datas = [f"('{pal_rel}', 'dc6/pal')", f"('{resources_rel}', 'resources')"]
    if extra_binaries:
        datas.extend(f"('{b}', 'stormlibpy/lib')" for b in extra_binaries)

    excludes = ",\n    ".join(repr(m) for m in EXCLUDE_MODULES)
    icon_arg = f"icon='{icon_rel}',"

    return f"""# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['{script_rel}'],
    pathex=[],
    binaries=[],
    datas=[{", ".join(datas)}],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[{excludes}],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [('O', None, 'OPTION'), ('O', None, 'OPTION')],
    exclude_binaries=True,
    name='{name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    {icon_arg}
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name='{name}',
)
"""


def _stormlib_dll() -> str:
    import stormlibpy

    p = Path(stormlibpy.__file__).parent / "lib" / "StormLib.dll"
    if p.exists():
        return p.as_posix()
    return ""


def _build_mpq_editor():
    name = "MPQEditor"
    print(f"Building {name}...")
    stormlib = _stormlib_dll()
    binaries = [stormlib] if stormlib else None
    spec = _make_spec(name, "main.py", "mpqeditor.ico", binaries)

    spec_path = ROOT / "build" / f"{name}.spec"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec, encoding="utf-8")

    args = [
        sys.executable,
        "-OO",
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        str(spec_path),
    ]
    subprocess.check_call(args)

    out_dir = DIST / name
    if out_dir.exists():
        _cleanup(out_dir)
        print(f"\nPackage ready: {out_dir}")


def _build_dc6_viewer():
    name = "DC6Viewer"
    print(f"Building {name}...")
    spec = _make_spec(name, "DC6Viewer.py", "dc6viewer.ico")

    spec_path = ROOT / "build" / f"{name}.spec"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(spec, encoding="utf-8")

    args = [
        sys.executable,
        "-OO",
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        str(spec_path),
    ]
    subprocess.check_call(args)

    out_dir = DIST / name
    if out_dir.exists():
        _cleanup(out_dir)
        print(f"\nPackage ready: {out_dir}")


def _rmtree_force(p: Path):
    import stat

    def onerror(func, path, exc_info):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    shutil.rmtree(str(p), onerror=onerror)


def _merge_internal(built: list[str]):
    """Merge per-app dirs into dist/ with shared _internal."""
    shared = DIST / "_internal"
    shared.mkdir(parents=True, exist_ok=True)

    for name in built:
        app_dir = DIST / name
        src_internal = app_dir / "_internal"
        if src_internal.is_dir():
            for item in src_internal.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(src_internal)
                    dst = shared / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        shutil.copy2(item, dst)

        exe_src = app_dir / f"{name}.exe"
        if exe_src.exists():
            shutil.copy2(exe_src, DIST / f"{name}.exe")

        _rmtree_force(app_dir)

    total = sum(f.stat().st_size for f in shared.rglob("*") if f.is_file())
    for unit in ["B", "KB", "MB", "GB"]:
        if total < 1024:
            print(f"Shared _internal: {total:.1f} {unit}")
            break
        total /= 1024


def main():
    if not shutil.which("pyinstaller") and not shutil.which("pyinstaller.exe"):
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    targets = sys.argv[1:] if len(sys.argv) > 1 else ["mpq", "dc6"]

    if DIST.exists():
        try:
            _rmtree_force(DIST)
        except PermissionError:
            import uuid

            backup = ROOT / f"dist-{uuid.uuid4().hex[:8]}"
            os.rename(str(DIST), str(backup))
            print(f"Renamed old dist to {backup.name}")

    built: list[str] = []
    for t in targets:
        if t == "mpq":
            _build_mpq_editor()
            built.append("MPQEditor")
        elif t == "dc6":
            _build_dc6_viewer()
            built.append("DC6Viewer")
        else:
            print(f"Unknown target: {t} (use 'mpq' or 'dc6')")

    if len(built) > 1:
        _merge_internal(built)


def _cleanup(out_dir: Path):
    internal = out_dir / "_internal"
    if not internal.is_dir():
        return

    rm_dlls = {
        "Qt6Qml",
        "Qt6Quick",
        "Qt6QmlModels",
        "Qt6QmlMeta",
        "Qt6QmlWorkerScript",
        "Qt6VirtualKeyboard",
        "Qt6Pdf",
        "Qt6OpenGL",
        "opengl32sw",
        "libcrypto-3-x64",
        "libssl-3-x64",
    }

    rm_plugins = {
        "platforms/qdirect2d.dll",
        "platforms/qoffscreen.dll",
        "platforms/qminimal.dll",
        "generic/qtuiotouchplugin.dll",
        "platforminputcontexts/qtvirtualkeyboardplugin.dll",
    }

    for p in internal.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix == ".dll" and p.stem in rm_dlls:
            p.unlink()
            continue
        rel = p.relative_to(internal).as_posix()
        if rel in rm_plugins:
            p.unlink()


if __name__ == "__main__":
    main()
