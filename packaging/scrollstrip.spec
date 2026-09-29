# packaging/scrollstrip.spec
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['launch.py'],
    pathex=['..'],
    binaries=[],
    datas=[('..\\scrollstrip\\web_dist', 'scrollstrip/web_dist')]
          + collect_data_files('ultralytics'),
    hiddenimports=collect_submodules('uvicorn') + ['pymupdf', 'cv2'],
    excludes=['matplotlib', 'tkinter', 'pandas', 'polars', 'IPython', 'notebook'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Scrollstrip',
          console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False,
               upx=False, name='Scrollstrip')
