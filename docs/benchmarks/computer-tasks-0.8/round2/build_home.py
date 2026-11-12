#!/usr/bin/env python3
"""Round 2: materialize the independent author's home spec (AUTHOR-REPLY.json "home") into a fake home folder, with the
same file encoders as audit-files-20261027/harness/make_fakehome.py (png, jpeg_like, pdf). File mtimes are set from
age_days relative to now. Writes <dest>/MANIFEST.json (path, age_days, bytes, sha256) and PACK.txt (every path, size,
age and readable text, the material the answer keys are checked against). Refuses a non-empty dest.
usage: python3 -I build_home.py <dest>"""
import hashlib, json, os, sys, time
from pathlib import Path

sys.path.insert(0, '<work>/missions/audit-files-20261027/harness')
from make_fakehome import png, jpeg_like, pdf  # noqa: E402

DAY = 86400
dest = Path(sys.argv[1])
if dest.exists() and any(dest.iterdir()):
    sys.exit(f'{dest} exists and is not empty')
s = open(Path(__file__).parent / 'AUTHOR-REPLY.json').read()
spec = json.loads(s[s.find('{'):s.rfind('}') + 1])
now = time.time()
manifest, pack = [], [f"persona: {spec.get('persona', '')}", '']
for f in spec['home']:
    p = dest / f['path']
    p.parent.mkdir(parents=True, exist_ok=True)
    k = f['kind']
    if k == 'text':
        data = f['text'].encode()
    elif k == 'pdf':
        data = pdf([str(x) for x in f['lines']])
    elif k == 'jpg':
        data = jpeg_like(f['path'])
    elif k == 'png':
        data = png(int(f.get('width', 40)), int(f.get('height', 30)), (120, 140, 160))
    elif k == 'bytes':
        ext = p.suffix.lower()
        magic = {'.mp4': b'\x00\x00\x00\x18ftypmp42', '.mov': b'\x00\x00\x00\x14ftypqt  ', '.zip': b'PK\x03\x04',
                 '.heic': b'\x00\x00\x00\x18ftypheic', '.gif': b'GIF89a', '.m4a': b'\x00\x00\x00\x18ftypM4A '}.get(ext, b'')
        data = magic + b'\x00' * max(0, int(f['size_bytes']) - len(magic))
    else:
        sys.exit(f'unknown kind {k} for {f["path"]}')
    p.write_bytes(data)
    t = now - float(f.get('age_days', 0)) * DAY
    os.utime(p, (t, t))
    manifest.append({'path': f['path'], 'age_days': f.get('age_days', 0), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    pack.append(f"== {f['path']}  ({len(data)} bytes, {f.get('age_days', 0)} days old)")
    if k == 'text':
        pack.append(f['text'])
    elif k == 'pdf':
        pack.extend(str(x) for x in f['lines'])
(dest / 'MANIFEST.json').write_text(json.dumps({'built_at_epoch': int(now), 'files': manifest}, indent=1))
(Path(__file__).parent / 'PACK.txt').write_text('\n'.join(pack) + '\n')
print(len(manifest), 'files')
