# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_root = Path(SPEC).resolve().parent.parent
qml_file = project_root / 'qtx_app' / 'qml' / 'Lab3DView.qml'

a = Analysis(
    [str(project_root / 'main.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(qml_file), 'qtx_app/qml')],
    hiddenimports=['PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ChromaticAnalysis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ChromaticAnalysis',
)
