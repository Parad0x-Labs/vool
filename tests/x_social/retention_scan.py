"""Read every owned HOME file, live SQLite/FTS row and decrypted recorder blob."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def scan_home(home: Path, needle: str) -> dict:
    home = home.resolve()
    raw = needle.encode()
    matches = []
    databases = []
    files = 0
    for path in sorted(home.rglob('*')):
        if not path.is_file():
            continue
        assert path.resolve().is_relative_to(home), path
        files += 1
        data = path.read_bytes()
        relative = str(path.relative_to(home))
        if raw in data:
            matches.append({'kind': 'file', 'path': relative})
        if not data.startswith(b'SQLite format 3\x00'):
            continue
        database = {'path': relative, 'tables': 0, 'rows': 0}
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                identifier = '"' + table.replace('"', '""') + '"'
                database['tables'] += 1
                for row in conn.execute('SELECT * FROM ' + identifier):
                    database['rows'] += 1
                    if any(needle in str(value) or (isinstance(value, bytes) and raw in value) for value in row):
                        matches.append({'kind': 'sqlite', 'path': relative, 'table': table})
        databases.append(database)
    from core.blackbox.store import BlackboxStore

    recorder = BlackboxStore(home / 'data/blackbox')
    blobs = 0
    for reference in recorder.blobs.iter_sha256():
        blobs += 1
        if raw in recorder.blobs.get(reference):
            matches.append({'kind': 'recorder_cas', 'reference': reference})
    return {'files': files, 'databases': databases, 'recorder_blobs_decrypted': blobs, 'matches': matches}
