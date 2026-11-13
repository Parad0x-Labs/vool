"""A lent job's worker starts under a framework Python without widening its read scope.

On the GitHub macOS runner (python.org framework build) every lent job failed: the worker aborted
before ``main`` with dyld "Library not loaded: .../Python.framework/Versions/3.12/Python", because
that shared library sits directly in the version directory, outside the bin/lib trees the worker is
granted. The fix names that one file; it never grants the version directory (the whole prefix).
"""

from __future__ import annotations

from pathlib import Path

from core.artifact_readers import _sandbox
from core.lend import worker


def _framework(tmp_path, monkeypatch):
    prefix = tmp_path / "Frameworks"
    version = prefix / "Python.framework" / "Versions" / "3.12"
    (version / "lib").mkdir(parents=True)
    (version / "Resources").mkdir()
    (version / "Python").write_bytes(b"\xcf\xfa\xed\xfe")
    values = {
        "PYTHONFRAMEWORK": "Python",
        "PYTHONFRAMEWORKPREFIX": str(prefix),
        "LDLIBRARY": "Python.framework/Versions/3.12/Python",
    }
    monkeypatch.setattr(worker.sysconfig, "get_config_var", lambda name: values.get(name))
    return version


def test_a_framework_python_names_only_its_shared_library(tmp_path, monkeypatch):
    version = _framework(tmp_path, monkeypatch)
    assert worker.runtime_files() == [str((version / "Python").resolve())]


def test_a_non_framework_python_names_no_file(monkeypatch):
    monkeypatch.setattr(worker.sysconfig, "get_config_var", lambda name: "" if name == "PYTHONFRAMEWORK" else None)
    assert worker.runtime_files() == []


def test_a_missing_framework_library_names_nothing(tmp_path, monkeypatch):
    version = _framework(tmp_path, monkeypatch)
    (version / "Python").unlink()
    assert worker.runtime_files() == []


def test_the_profile_grants_the_file_as_a_literal_never_its_directory(tmp_path, monkeypatch):
    version = _framework(tmp_path, monkeypatch)
    library = worker.runtime_files()
    profile = _sandbox._profile(scratch=str(tmp_path / "scratch"), extra_read=[], allow_gpu=False,
                                extra_read_files=_sandbox._resolved_files(library))
    assert f'(literal "{library[0]}")' in profile
    assert f'(subpath "{version.resolve()}")' not in profile
    assert f'"{(version / "Resources").resolve()}"' not in profile


def test_single_file_grants_skip_directories_missing_files_and_vool_data(tmp_path, monkeypatch):
    data = tmp_path / "vool-home"
    data.mkdir()
    secret = data / "wallet.json"
    secret.write_text("{}")
    real = tmp_path / "lib.dylib"
    real.write_bytes(b"x")
    monkeypatch.setattr("core.runtime_paths.active_vool_home", lambda: data)
    assert _sandbox._resolved_files([str(secret), str(tmp_path), str(tmp_path / "missing"), str(real), str(real)]) == [
        str(real.resolve())
    ]


def test_the_worker_passes_the_file_grant_to_the_confined_run():
    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert "extra_read_files=runtime_files()" in source
