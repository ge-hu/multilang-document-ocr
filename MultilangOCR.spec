# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH)
datas = []
datas += collect_data_files("langdetect")

assets_dir = project_root / "assets"
if assets_dir.is_dir():
    datas.append((str(assets_dir), "assets"))

tesseract_dir = project_root / "vendor" / "tesseract"
if tesseract_dir.is_dir():
    datas.append((str(tesseract_dir), "tesseract"))

for document_name in ("使用说明.txt", "THIRD_PARTY_NOTICES.md"):
    document_path = project_root / document_name
    if document_path.is_file():
        datas.append((str(document_path), "."))

hiddenimports = collect_submodules("tkinterdnd2")

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MultilangOCR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MultilangOCR-Portable",
)
