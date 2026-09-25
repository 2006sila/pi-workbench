# -*- mode: python ; coding: utf-8 -*-
"""
pi用学习工作台 —— PyInstaller 打包配置（单文件）

构建：
    pyinstaller --clean --noconfirm bj_tool.spec
产物：
    dist/pi用学习工作台.exe
"""

import os

ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (PyInstaller 注入)

DATAS = [
    ('inject.ps1', '.'),
    ('inject-pideck.ps1', '.'),
    ('inject-dsh.ps1', '.'),
    ('app.ico', '.'),
    ('prompts', 'prompts'),
    ('skills-v4', 'skills-v4'),
]

EXCLUDES = [
    'tkinter', 'unittest', 'pydoc_data', 'lib2to3', 'test', 'distutils',
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtQuick', 'PySide6.QtQuick3D', 'PySide6.QtQml', 'PySide6.QtQuickWidgets',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtBluetooth',
    'PySide6.QtNfc', 'PySide6.QtPositioning', 'PySide6.QtSerialPort', 'PySide6.QtSql',
    'PySide6.QtTest', 'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtPdf',
    'PySide6.QtPdfWidgets', 'PySide6.QtSpatialAudio', 'PySide6.QtSensors',
    'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtStateMachine',
    'PySide6.QtHttpServer', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtUiTools',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtSvgWidgets',
]

a = Analysis(  # noqa: F821
    ['bj_tool.py'],
    pathex=[ROOT],
    binaries=[],
    datas=DATAS,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='pi用学习工作台',
    icon=os.path.join(ROOT, 'app.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
