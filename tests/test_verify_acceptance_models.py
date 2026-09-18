from __future__ import annotations

import json

import pytest

from ops.verify_acceptance_models import required_models, served_models, verify


def test_required_models_are_unique_and_primary_first(tmp_path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"model": "qwen3:8b", "selected_models": ["qwen3:8b", "deepseek-r1:8b"]}),
        encoding="utf-8",
    )

    assert required_models(profile) == ("qwen3:8b", "deepseek-r1:8b")


def test_served_models_reads_ollama_tags() -> None:
    assert served_models({"models": [{"name": "qwen3:8b"}, {"name": "deepseek-r1:8b"}]}) == {
        "qwen3:8b",
        "deepseek-r1:8b",
    }


def test_verify_fails_when_any_profile_model_is_missing(monkeypatch, tmp_path) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"model": "qwen3:8b", "selected_models": ["qwen3:8b", "deepseek-r1:8b"]}),
        encoding="utf-8",
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return json.dumps({"models": [{"name": "qwen3:8b"}]}).encode()

    monkeypatch.setattr("ops.verify_acceptance_models.request.urlopen", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError, match="deepseek-r1:8b"):
        verify(profile)
