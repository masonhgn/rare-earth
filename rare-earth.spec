# PyInstaller spec for the rare-earth game client (src/main.py).
#
# build:   pyinstaller rare-earth.spec --noconfirm
# output:  dist/rare-earth/rare-earth.exe   (onedir)
#
# the whole src/data tree is bundled as data at the same 'src/data' path the
# code loads it from; respath.init() chdir's into the extraction root at
# startup so those cwd-relative loads resolve. saves + settings are written
# next to the exe (see respath.writable_base), not into the bundle.

block_cipher = None

a = Analysis(
    ['src/main.py'],
    pathex=['src'],                    # flat imports: `from config import ...`
    binaries=[],
    datas=[('src/data', 'src/data')],  # entire asset tree, dest mirrors source
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='rare-earth',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # console left on so any runtime/asset error is visible while stabilizing;
    # flip to False for a clean windowed game once it launches cleanly.
    console=True,
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
    upx=True,
    upx_exclude=[],
    name='rare-earth',
)
