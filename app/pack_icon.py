import struct
import sys
from pathlib import Path

root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('/tmp/mavi.iconset')
entries = [('icp4','16x16'),('icp5','32x32'),('icp6','32x32@2x'),('ic07','128x128'),('ic08','256x256'),('ic09','512x512'),('ic10','512x512@2x')]
chunks = []
for kind, name in entries:
    png = (root / f'icon_{name}.png').read_bytes()
    chunks.append(kind.encode() + struct.pack('>I',len(png)+8) + png)
body = b''.join(chunks)
Path(sys.argv[1]).write_bytes(b'icns'+struct.pack('>I',len(body)+8)+body)
