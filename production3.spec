# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH)

a = Analysis(
    [str(project_root / "gui_app_pyside6.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "assets"), "assets"),
        (str(project_root / "styles"), "styles"),
    ],
    hiddenimports=[
        *collect_submodules("xlsxwriter"),
        *collect_submodules("openpyxl"),
        "services.safe_mode",
        "numpy",
        "services.windows_icon",
        "collectors.aps_update_monitor",
        "collectors.bom_snapshot_collector",
        "collectors.data_retention_cleanup",
        "collectors.process_status_collector",
        "collectors.production_performance_collector",
        "collectors.live_production_need_collector",
        "collectors.inventory_live_refresh",
        "collectors.refresh_all",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Codex/workspace helper runtimes can prepend Poppler DLLs to PATH while
# packaging.  Their ICU runtime is not an application dependency and shadows
# Qt's Windows ICU integration at startup, which makes QtCore fail to load.
# Keep those foreign helper DLLs out of the distributable.
_foreign_runtime_markers = ("codex-runtimes", "poppler")
_foreign_runtime_names = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry
    for entry in a.binaries
    if not (
        Path(entry[0]).name.lower() in _foreign_runtime_names
        and all(marker in str(entry[1]).lower() for marker in _foreign_runtime_markers)
    )
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="gui_app_pyside6",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(project_root / "assets" / "ddokddak_app_icon.ico"),
    version=str(project_root / "installer" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="production3",
)
