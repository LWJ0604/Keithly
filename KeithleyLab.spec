# Relative paths only; invoke from the repository root.
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root / "launch.py")], pathex=[str(root)], binaries=[], datas=[],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
             win_no_prefer_redirects=False, win_private_assemblies=False, cipher=None, noarchive=False)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="KeithleyLab", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, target_arch=None, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="KeithleyLab")
