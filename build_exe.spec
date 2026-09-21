# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

打包：
    .venv\\Scripts\\python.exe -m PyInstaller build_exe.spec --noconfirm

产物：
    dist\\断面计算工具\\断面计算工具.exe   （整个目录拷走即可运行）

说明：
  - 采用 onedir 模式（不是单文件）。单文件每次启动要解压约 200 MB，
    冷启动十几秒；onedir 秒开，分发时把整个文件夹打包成 zip 即可。
    若确实要单文件，把下面的 COLLECT 段删除，改用 EXE(..., a.binaries, a.datas)。
  - PySide6-Addons 没有安装（见 requirements.txt），所以不会把
    QtWebEngine / Qt3D 那些几百 MB 的东西打进去。
"""

import os
import sys

block_cipher = None

ROOT = os.path.abspath(os.getcwd())

# 打包前把当前 git 提交与时间戳烘焙进 src/core/_build_info.py。
# exe 里没有 git 可查，不烘焙的话「关于」就只能显示版本号、看不出是哪次提交的产物。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from tools.write_build_info import write_build_info          # noqa: E402

_HASH, _TIME = write_build_info(ROOT)
print(f"[build] 构建信息已烘焙：GIT_HASH={_HASH}  BUILD_TIME={_TIME}")

a = Analysis(
    [os.path.join("src", "app", "main.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=[
        # 数据模板目录随包分发（没有数据也能启动，用户自己往里放 xlsx）
        (os.path.join("data"), "data"),
        # 图标等静态资源，运行时通过 sys._MEIPASS/assets 找到
        (os.path.join("src", "app", "assets"), "assets"),
    ],
    hiddenimports=[
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qt",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 明确排除用不到的大块头，进一步瘦身。
    #
    # ⚠ 千万不要把 unittest 加进来！pyparsing.testing 会 import unittest，
    #   而 matplotlib -> rcsetup -> _fontconfig_pattern -> pyparsing 链式依赖它，
    #   排掉会导致 exe 启动即崩且（console=False 时）毫无提示。
    #   这个坑是 exe 的 --selftest 抓出来的。
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtQuick3D",
        "PySide6.QtBluetooth", "PySide6.QtDataVisualization",
        "tkinter", "pydoc_data",
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends.backend_wxagg",
        "matplotlib.backends.backend_gtk3agg",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="断面计算工具",
    icon=os.path.join("src", "app", "assets", "icon.ico"),   # exe 图标
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 不要黑窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="断面计算工具",
)
