# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['gedcom-dna-finder-gui.py'],
    pathex=[],
    binaries=[],
    datas=[('HELP.md', '.'), ('LICENSE', '.'), ('./icons/family_tree.ico','.'), ('./icons/family_tree.png','.')],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name='gedcom-dna-finder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['family_tree.ico'],
)

if sys.platform == 'darwin':
    exe = EXE(pyz,
              a.scripts,
              exclude_binaries=True, 
              name='gedcom-dna-finder',
              console=False)

    coll = COLLECT(exe,
                   a.binaries,
                   a.zipfiles,
                   a.datas,
                   strip=False,
                   upx=True,
                   name='gedcom-dna-finder.app')

    app = BUNDLE(coll,
                 name='gedcom-dna-finder.app',
                 icon='./icons/family_tree.icns',
                 bundle_identifier='com.ajkessel.gedcom-dna-finder')
