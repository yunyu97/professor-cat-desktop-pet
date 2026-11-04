# -*- mode: python ; coding: utf-8 -*-
# Folder ("onedir") build: starts in a couple of seconds because nothing has to be
# unpacked to a temp folder on every launch (the old single-file .exe unpacked
# ~220 MB of Qt WebEngine each time).
#
#   pyinstaller ProfessorCatLife.spec
#   -> dist/ProfessorCatLife/ProfessorCatLife.exe

a = Analysis(
    ['ProfessorCatLife.py'],
    pathex=[],
    binaries=[],
    datas=[('cat_sprites_life', 'cat_sprites_life')],
    hiddenimports=['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'numpy', 'scipy', 'PIL', 'rembg', 'onnxruntime',
              'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
              'PySide6.QtMultimedia', 'PySide6.QtQuick3D', 'PySide6.QtPdf',
              'PySide6.QtBluetooth', 'PySide6.QtSensors', 'PySide6.QtSql',
              'PySide6.QtTest', 'PySide6.QtDesigner'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ProfessorCatLife',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name='ProfessorCatLife',
)
