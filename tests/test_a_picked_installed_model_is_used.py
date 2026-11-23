"""A model the person picks that Ollama has installed is used, not refused as unavailable.

Mac gate on 91e898675 (2026-11-23, installed app): the chat was switched to qwen3:4b, which `ollama list` showed
installed, and the turn answered "Requested model 'qwen3:4b' is not available on this runtime." VOOL had never
registered it. A pick of an installed text model is registered the way Settings -> Models -> Use this model does it.
Model names are drawn at random; one case runs the served daemon end to end with a stub Ollama.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

TAGS = ("qwen3:4b", "qwen3:14b", "gemma3:4b", "llama3.2:3b", "mistral:7b", "qwen2.5:7b")


class _Registry:
    def __init__(self, names=()):
        self.rows = [SimpleNamespace(provider_id=f"ollama-local:{n}", model_name=n) for n in names]

    def list_manifests(self, *, enabled_only=False, limit=256):
        return list(self.rows)


@pytest.fixture
def adopt(monkeypatch):
    from core import runtime_provider_defaults as rpd

    registered: list[str] = []
    state = {"installed": (), "disabled": False}
    monkeypatch.setattr(rpd, "installed_ollama_model_names", lambda **_kw: tuple(state["installed"]))
    monkeypatch.setattr(rpd, "resolve_local_model_policy", lambda: SimpleNamespace(local_models_disabled=state["disabled"]))

    def register(registry, *, model_tag, env=None):
        registered.append(model_tag)
        return SimpleNamespace(provider_id=f"ollama-local:{model_tag}", model_name=model_tag)

    monkeypatch.setattr(rpd, "register_installed_local_model", register)
    return rpd.adopt_installed_local_pick, state, registered


@pytest.mark.parametrize("seed", range(5))
def test_an_installed_pick_with_no_manifest_is_registered(seed, adopt) -> None:
    pick, state, registered = adopt
    rng = random.Random(f"adopt-{seed}")
    tag = rng.choice(TAGS)
    state["installed"] = tuple(rng.sample([t for t in TAGS if t != tag], 2)) + (tag, "nomic-embed-text:latest")

    adopted = pick(tag, registry=_Registry(n for n in TAGS if n not in state["installed"]))

    assert adopted is not None and adopted.model_name == tag
    assert registered == [tag]


def test_nothing_is_adopted_that_is_not_exactly_an_installed_text_model(adopt) -> None:
    pick, state, registered = adopt
    state["installed"] = ("qwen3:4b-instruct", "nomic-embed-text:latest", "qwen3:8b")

    assert pick("qwen3:4b", registry=_Registry()) is None             # a longer tag is not this one
    assert pick("nomic-embed-text:latest", registry=_Registry()) is None  # no text
    assert pick("qwen3:8b", registry=_Registry(["qwen3:8b"])) is None   # already registered
    assert pick("auto", registry=_Registry()) is None
    assert pick("", registry=_Registry()) is None
    state["disabled"] = True
    assert pick("qwen3:4b-instruct", registry=_Registry()) is None
    assert registered == []


# ---- served, end to end -----------------------------------------------------------------------------------------

def test_the_served_chat_answers_on_the_picked_installed_model(tmp_path) -> None:
    from datetime import datetime, timezone

    from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

    rng = random.Random(f"served-adopt-{datetime.now(timezone.utc):%Y%m%d}")
    registered, picked = "qwen3-stub:2b", rng.choice(("qwen3:4b", "gemma3:4b", "llama3.2:3b"))
    said = f"Fourteen nines are {9 * 14} ({rng.getrandbits(16):04x})."
    provider = ScriptedProvider({registered: "from the other model", picked: said})
    home = tmp_path / "home"
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the pick
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[registered],
                                               model=registered))
        provider.reset()
        payload = daemon.chat("what is 9 times 14?", session_id="served-adopt", model=picked, mode="auto")
        asked = [str((c.get("body") or c).get("model") or "") for c in provider.calls]
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    text = str(commit.get("canonical_content") or message.get("content") or payload.get("response") or "")

    assert "not available on this runtime" not in text, text
    assert picked in asked, asked
