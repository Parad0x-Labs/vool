"""An installed chat or project skill reaches the prompt the provider actually receives.

`skill_guidance_for` found a chat-scoped skill, but both normalized provider paths (the structured
action-plan emitter and the API catalog emitter) asked it without the turn's source context, so the
skill vanished before the provider request was built. Each case below builds the real normalized
request on both paths.
"""
from __future__ import annotations

from types import SimpleNamespace as N

import pytest

from core import runtime_paths, skill_tools
from core.context_namespace import ensure_chat_namespace
from core.prompt_normalizer import normalize_prompt
from core.skill_scopes import scope_keys

ASK = "here are my meeting notes, give me the action items"
CHAT_MARKER = "SCOPED_GUIDANCE_CHAT_7B93"
GLOBAL_MARKER = "SCOPED_GUIDANCE_GLOBAL_41C2"
SURFACES = [("api", "plain_text"), ("cli", "action_plan")]


def _skill(name: str, marker: str) -> str:
    return (
        f"---\nname: {name}\ndescription: Use when meeting notes need action items\n"
        f"triggers: [meeting, notes, action items]\n---\n{marker} Format meeting notes as action items.\n"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    from core.plugin_catalog import invalidate_storage_listing

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins"))
    (tmp_path / "plugins" / "plugins").mkdir(parents=True)
    invalidate_storage_listing()
    yield tmp_path
    invalidate_storage_listing()
    runtime_paths.configure_runtime_home(None)


def _install_chat(chat: str, name: str, marker: str) -> dict:
    ensure_chat_namespace(chat)
    keys = scope_keys({"session_id": chat})
    draft = skill_tools._scoped_dirs("chat", keys)[0] / name
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text(_skill(name, marker), encoding="utf-8")
    assert skill_tools.install_skill(name, scope="chat", keys=keys)["status"] == "ok"
    return keys


def _instructions(chat: str, surface: str, mode: str) -> str:
    ensure_chat_namespace(chat)
    request = normalize_prompt(
        task=N(task_id="t", task_summary=ASK),
        classification={"task_class": "unknown"},
        interpretation=N(normalized_text=ASK, raw_text=ASK, understanding_confidence=0.9),
        context_result=N(
            local_candidates=[], swarm_metadata=[], retrieval_confidence_score=0.0, assembled_context=lambda: "",
            report=N(retrieval_confidence=0.0, to_dict=lambda: {}),
        ),
        persona=N(name="VOOL", tone="clear"),
        output_mode=mode,
        task_kind=mode,
        trace_id="scoped-skill-prompt",
        surface=surface,
        source_context={"session_id": chat},
    )
    return request.instructions()


@pytest.mark.parametrize(("surface", "mode"), SURFACES)
def test_a_chat_skill_reaches_its_own_chats_provider_prompt_only(home, surface, mode):
    keys = _install_chat("openclaw:scopedpromptchata0001", "meeting-actions", CHAT_MARKER)
    assert CHAT_MARKER in _instructions("openclaw:scopedpromptchata0001", surface, mode)
    assert CHAT_MARKER not in _instructions("openclaw:scopedpromptchatb0002", surface, mode)
    # removed, it is gone from the prompt too
    assert skill_tools.remove_skill("meeting-actions", scope="chat", keys=keys)["status"] == "ok"
    assert CHAT_MARKER not in _instructions("openclaw:scopedpromptchata0001", surface, mode)


@pytest.mark.parametrize(("surface", "mode"), SURFACES)
def test_a_global_skill_still_reaches_every_chat(home, surface, mode):
    staged = skill_tools.staging_root() / "meeting-digest"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text(_skill("meeting-digest", GLOBAL_MARKER), encoding="utf-8")
    assert skill_tools.install_skill("meeting-digest")["status"] == "ok"
    for chat in ("openclaw:scopedpromptchata0001", "openclaw:scopedpromptchatb0002"):
        assert GLOBAL_MARKER in _instructions(chat, surface, mode), chat
