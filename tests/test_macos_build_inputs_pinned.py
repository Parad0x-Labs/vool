from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from ops import macos_build_inputs as inputs
from ops import proxy_tools_wheel as recipe


def test_download_checks_bytes_before_publishing_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(inputs.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'release bytes'))
    target = tmp_path / 'archive.tgz'
    with pytest.raises(ValueError, match='sha256 mismatch'):
        inputs.verified_download('https://example.invalid/archive', '0' * 64, target)
    assert not target.exists()
    assert not target.with_suffix('.tgz.part').exists()
    digest = hashlib.sha256(b'release bytes').hexdigest()
    assert inputs.verified_download('https://example.invalid/archive', digest, target).read_bytes() == b'release bytes'
    target.write_bytes(b'changed cached bytes')
    with pytest.raises(ValueError, match='sha256 mismatch'):
        inputs.verified_download('https://example.invalid/archive', digest, target)


def test_ollama_uses_only_the_verified_archive(monkeypatch, tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    archive = cache / 'ollama-darwin.tgz'
    with tarfile.open(archive, 'w:gz') as out:
        info = tarfile.TarInfo('ollama')
        info.size = len(b'pinned executable')
        info.mode = 0o755
        out.addfile(info, io.BytesIO(b'pinned executable'))
    monkeypatch.setattr(inputs, 'OLLAMA_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(inputs.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('cache must avoid a download'))
    inputs.ollama(cache, tmp_path / 'release')
    assert (tmp_path / 'release/ollama').read_bytes() == b'pinned executable'
    archive.write_bytes(b'wrong archive')
    with pytest.raises(ValueError, match='sha256 mismatch'):
        inputs.ollama(cache, tmp_path / 'bad-release')
    assert not (tmp_path / 'bad-release').exists()


def test_archive_traversal_is_refused(tmp_path):
    archive = tmp_path / 'bad.tar'
    with tarfile.open(archive, 'w') as out:
        info = tarfile.TarInfo('../escaped')
        info.size = 1
        out.addfile(info, io.BytesIO(b'x'))
    with pytest.raises(tarfile.FilterError):
        inputs.extract(archive, tmp_path / 'extract')
    assert not (tmp_path / 'escaped').exists()


def test_proxy_wheel_is_built_twice_and_validated_without_executing_setup(monkeypatch, tmp_path):
    source = tmp_path / 'proxy_tools-0.1.0'
    (source / 'proxy_tools').mkdir(parents=True)
    (source / 'proxy_tools/__init__.py').write_text('value = 7\n')
    (source / 'PKG-INFO').write_text('Metadata-Version: 1.1\nName: proxy_tools\nVersion: 0.1.0\n')
    (source / 'setup.py').write_text('raise RuntimeError("must never execute")\n')
    reference = tmp_path / 'reference'
    recipe.main(['recipe', str(source), str(reference)])
    wheel_name = 'proxy_tools-0.1.0-py3-none-any.whl'
    wheel_digest = hashlib.sha256((reference / wheel_name).read_bytes()).hexdigest()
    archive = tmp_path / 'proxy.tar.gz'
    with tarfile.open(archive, 'w:gz') as out:
        out.add(source, arcname=source.name)
    root = tmp_path / 'root'
    (root / 'installer/bundle').mkdir(parents=True)
    (root / 'ops').mkdir()
    (root / 'ops/proxy_tools_wheel.py').write_bytes(Path(recipe.__file__).read_bytes())
    entry = {'package': 'proxy-tools', 'file': wheel_name, 'source_sdist': archive.name,
             'source_sdist_url': 'https://example.invalid/source',
             'source_sdist_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'sha256': wheel_digest}
    record = root / 'installer/bundle/built_wheels.json'
    record.write_text(json.dumps({'wheels': [entry]}))
    monkeypatch.setattr(inputs, 'ROOT', root)
    monkeypatch.setattr(inputs.urllib.request, 'urlopen', lambda *a, **k: archive.open('rb'))
    built = inputs.proxy_wheel(tmp_path / 'cache', tmp_path / 'wheels')
    assert built.read_bytes() == (reference / wheel_name).read_bytes()
    entry['sha256'] = '0' * 64
    record.write_text(json.dumps({'wheels': [entry]}))
    with pytest.raises(ValueError, match='differs from the recorded recipe'):
        inputs.proxy_wheel(tmp_path / 'cache', tmp_path / 'refused')
    assert not (tmp_path / 'refused' / wheel_name).exists()


def test_self_contained_build_uses_pinned_inputs_and_no_ambient_ollama():
    script = (inputs.ROOT / 'installer/bundle/build_macos_app.sh').read_text()
    assert 'macos_build_inputs.py" proxy-wheel' in script
    assert 'macos_build_inputs.py" ollama' in script
    assert 'command -v ollama' not in script
    assert 'WARNING: no ollama binary bundled' not in script
    assert '--find-links "${input_scratch}/wheels"' in script
