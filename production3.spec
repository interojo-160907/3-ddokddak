# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


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
        "collectors.aps_update_monitor",
        "collectors.bom_snapshot_collector",
        "collectors.data_retention_cleanup",
        "collectors.process_status_collector",
        "collectors.production_performance_collector",
        "collectors.refresh_all",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
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
