from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("barcode")
    + collect_submodules("qrcode")
    + collect_submodules("bleak")
)

a = Analysis(
    ["run_printpal.py"],
    pathex=[],
    binaries=[],
    datas=[("web", "web")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PrintPal",
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
    icon="assets/printpal.ico",
    version="packaging/version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PrintPal",
)
