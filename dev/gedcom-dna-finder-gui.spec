# -*- mode: python ; coding: utf-8 -*-

import glob
import os
import sys
import subprocess
import re
from PyInstaller.utils.hooks import collect_data_files

def check_codesigning_key():
    """
    Checks if a codesigning key exists in the user keychain; if so, use to sign the package.
    """
    identity_name = "Developer ID Application"
    try:
        result = subprocess.run(
            ['security', 'find-identity', '-v', '-p', 'codesigning'],
            capture_output=True,
            text=True,
            check=True
        )
        for line in result.stdout.splitlines():
            if identity_name in line:
                key = re.search(r'[0-9A-Z]{40}',line)
                return(key[0])
            
    except subprocess.CalledProcessError as e:
        print(f"Error checking keychain: {e}")
        return False

# ffi-8.dll / libffi-8.dll is required by _ctypes.pyd on Windows but is not
# auto-detected by PyInstaller. Conda names it ffi-8.dll (no lib prefix) and
# places it under Library\bin; standard CPython uses libffi-8.dll in the
# executable directory or DLLs\. Search all four combinations.
_extra_binaries = []
if sys.platform == 'win32':
    _base = os.path.dirname(sys.executable)
    for _pat in [
        os.path.join(_base, 'libffi*.dll'),
        os.path.join(_base, 'ffi*.dll'),
        os.path.join(_base, 'DLLs', 'libffi*.dll'),
        os.path.join(_base, 'DLLs', 'ffi*.dll'),
        os.path.join(_base, 'Library', 'bin', 'libffi*.dll'),
        os.path.join(_base, 'Library', 'bin', 'ffi*.dll'),
    ]:
        _extra_binaries += [(p, '.') for p in glob.glob(_pat)]

a = Analysis(
    ['../src/gedcom-dna-finder-gui.py'],
    pathex=[],
    binaries=_extra_binaries,
    datas=[('../docs/HELP.md', './docs'), ('../docs/LICENSE.md', '.'), ('../icons/family_tree.ico','./icons'), ('../icons/family_tree.png','./icons')],
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
    icon=['../icons/family_tree.ico'],
)

if sys.platform == 'darwin':
    exe = EXE(pyz,
              a.scripts,
              exclude_binaries=True, 
              name='gedcom-dna-finder',
              codesign_identity=check_codesigning_key(),
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
                 icon='../icons/family_tree.icns',
                 bundle_identifier='com.ajkessel.gedcom-dna-finder')
