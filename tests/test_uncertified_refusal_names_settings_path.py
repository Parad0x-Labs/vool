"""The uncertified-local-model refusal must say WHERE certification lives.

A user told to "run the local model tool certification for that model" has no way to find it:
the only surface that runs it is Settings > Models & Providers > Local models > Certify
(core/vool_settings_page.py; README.md points owners there the same way). The refusal keeps
every phrase it always had and now names that path with the words the Settings page shows.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import local_model_presence

SETTINGS_CERTIFY_PATH = "Settings → Models & Providers → Local models → Certify"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "uncertified-refusal-home"
    (home / "data").mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    # An Ollama endpoint that refuses connections; presence is forced on per-test instead.
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", "http://127.0.0.1:9")
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    import storage.db as sdb

    sdb.configure_default_db_path(home / "data" / "vool_web0_v2.db")
    local_model_presence.reset_cache()
    yield home
    local_model_presence.reset_cache()
    configure_runtime_home(None)
    sdb.configure_default_db_path(None)


def _register_local_model() -> str:
    from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest

    upsert_provider_manifest(
        ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen3:4b",
            source_type="http",
            capabilities=["summarize"],
            runtime_config={"base_url": "http://127.0.0.1:11434"},
        )
    )
    return "ollama-local:qwen3:4b"


def _decision(home, reason: str, *, attempted_failed=None):
    from core.final_answer_authorship import AuthorshipDecision

    return AuthorshipDecision(
        eligible=False,
        author_role="final_answer",
        task_class="general",
        selected_model=_register_local_model(),
        reason=reason,
    ).refusal_text(attempted_failed=attempted_failed)


def test_the_refusal_for_an_uncertified_local_model_names_the_settings_path(home, monkeypatch) -> None:
    from core.final_answer_authorship import REASON_UNCERTIFIED_AUTHOR

    monkeypatch.setattr("core.local_model_presence.local_model_running", lambda **_: True)
    text = _decision(home, REASON_UNCERTIFIED_AUTHOR)
    assert SETTINGS_CERTIFY_PATH in text, "the refusal must say where Certify lives"
    # It still names the model and the historical remedy phrases, not a rewording.
    assert "`ollama-local:qwen3:4b`" in text
    assert "run the local model tool certification for that model" in text


def test_every_refusal_that_recommends_certification_names_the_settings_path(home, monkeypatch) -> None:
    from core.final_answer_authorship import REASON_BLOCKED_BEFORE_GENERATION

    monkeypatch.setattr("core.local_model_presence.local_model_running", lambda **_: True)
    # The blocked-before-generation shape and the attempted-failed variant ship the same remedy.
    for attempted in (None, ["openrouter-byok:fake/free"]):
        text = _decision(home, REASON_BLOCKED_BEFORE_GENERATION, attempted_failed=attempted)
        assert SETTINGS_CERTIFY_PATH in text
