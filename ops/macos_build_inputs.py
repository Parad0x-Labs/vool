"""Pinned, verified inputs for the self-contained macOS build."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

OLLAMA_VERSION = '0.40.3'
OLLAMA_URL = f'https://github.com/ollama/ollama/releases/download/v{OLLAMA_VERSION}/ollama-darwin.tgz'
# Digest published on the official v0.40.3 release asset.
OLLAMA_SHA256 = 'a2d1d906a2c8febd9cb18f5c183854d0a6db5dc522940d68b0c0ed048e8dfdea'
ROOT = Path(__file__).resolve().parents[1]


def verified_download(url: str, digest: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        part = target.with_suffix(target.suffix + '.part')
        try:
            with urllib.request.urlopen(url, timeout=120) as source, part.open('xb') as out:
                shutil.copyfileobj(source, out)
            if hashlib.sha256(part.read_bytes()).hexdigest() != digest:
                raise ValueError('download sha256 mismatch')
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    if not target.is_file() or target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError('cached input sha256 mismatch')
    return target


def extract(archive: Path, destination: Path) -> None:
    # Python's data filter confines links and member paths and refuses device nodes.
    if not hasattr(tarfile, 'data_filter'):
        raise RuntimeError('a security-patched Python with tarfile.data_filter is required')
    with tarfile.open(archive) as source:
        source.extractall(destination, filter='data')


def ollama(cache: Path, destination: Path) -> None:
    archive = verified_download(OLLAMA_URL, OLLAMA_SHA256, cache / 'ollama-darwin.tgz')
    destination.mkdir(parents=True, exist_ok=False)
    extract(archive, destination)
    if not (destination / 'ollama').is_file():
        raise ValueError('official archive has no ollama executable')


def proxy_wheel(cache: Path, destination: Path) -> Path:
    entries = json.loads((ROOT / 'installer/bundle/built_wheels.json').read_text())['wheels']
    record = next(entry for entry in entries if entry['package'] == 'proxy-tools')
    archive = verified_download(record['source_sdist_url'], record['source_sdist_sha256'], cache / record['source_sdist'])
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='proxy-tools-') as scratch:
        source = Path(scratch) / 'source'
        extract(archive, source)
        wheels = []
        for name in ('first', 'second'):
            out = Path(scratch) / name
            subprocess.run([sys.executable, '-I', str(ROOT / 'ops/proxy_tools_wheel.py'),
                            str(source / 'proxy_tools-0.1.0'), str(out)], check=True)
            wheels.append(out / record['file'])
        if wheels[0].read_bytes() != wheels[1].read_bytes():
            raise ValueError('proxy-tools wheel build is not reproducible')
        if hashlib.sha256(wheels[0].read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('proxy-tools wheel differs from the recorded recipe')
        target = destination / record['file']
        if target.exists() and target.read_bytes() != wheels[0].read_bytes():
            raise ValueError('wheelhouse contains a conflicting proxy-tools wheel')
        shutil.copyfile(wheels[0], target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('ollama', 'proxy-wheel'))
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.kind == 'ollama':
        ollama(args.cache, args.out)
    else:
        proxy_wheel(args.cache, args.out)


if __name__ == '__main__':
    main()
