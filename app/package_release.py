#!/usr/bin/env python3
"""Create a reproducible app ZIP and SHA-256 sidecar for a GitHub release."""
import hashlib
import os
import sys
import zipfile
from pathlib import Path

app = Path(sys.argv[1]).resolve(strict=True)
out = Path(sys.argv[2]).resolve()
version = sys.argv[3]
if app.name != 'Mavi.app' or not (app / 'Contents/Info.plist').is_file():
    raise SystemExit('Expected a built Mavi.app bundle')
out.mkdir(parents=True, exist_ok=True)
archive = out / f'Mavi-{version}.zip'
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    paths = [app] + sorted(app.rglob('*'))
    for path in paths:
        relative = Path('Mavi.app') if path == app else Path('Mavi.app') / path.relative_to(app)
        info = zipfile.ZipInfo(str(relative) + ('/' if path.is_dir() else ''), (2020, 1, 1, 0, 0, 0))
        info.create_system = 3
        mode = path.lstat().st_mode
        info.external_attr = (mode & 0xFFFF) << 16
        if path.is_symlink():
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, os.readlink(path).encode())
        elif path.is_dir():
            z.writestr(info, b'')
        else:
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(archive.with_suffix(archive.suffix + '.sha256')).write_text(f'{digest}  {archive.name}\n')
print(f'{archive}\n{archive.with_suffix(archive.suffix + ".sha256")}')
