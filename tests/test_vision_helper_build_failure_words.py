import subprocess

import pytest

from core.artifact_readers import vision_tool
from core.artifact_readers._types import ReaderUnavailable


@pytest.fixture
def build_here(monkeypatch, tmp_path):
    monkeypatch.setattr(vision_tool, "toolchain_available", lambda: True)
    monkeypatch.setattr(vision_tool, "tools_dir", lambda: tmp_path)


def _assert_plain_words(exc):
    message, remediation = vision_tool._build_failed_words()
    assert exc.code == "vision_build_failed"
    assert exc.message == message
    assert exc.remediation == remediation


def test_a_compiler_that_cannot_start_gives_the_plain_words(build_here, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError("no compiler")

    monkeypatch.setattr(vision_tool.subprocess, "run", refuse)
    with pytest.raises(ReaderUnavailable) as caught:
        vision_tool.ensure_tool()
    _assert_plain_words(caught.value)


def test_a_failed_compile_gives_the_plain_words(build_here, monkeypatch):
    def fail(args, **_kwargs):
        return subprocess.CompletedProcess(args, 1, b"", b"error: something")

    monkeypatch.setattr(vision_tool.subprocess, "run", fail)
    with pytest.raises(ReaderUnavailable) as caught:
        vision_tool.ensure_tool()
    _assert_plain_words(caught.value)
