"""After a real served chat, expanding the operator family still offers operator.save_note.

A served /api/chat turn projects the command registry's model-offerable reads
(operator.command.status.show, operator.command.capabilities.list) into the tool registry, as
the daemon does on boot. Those two reads joined the operator family, and under the 24-seat cap
they pushed operator.save_note (and both reminder tools) out of an operator expansion beside
web and a token ask. Saving a note is an everyday action; the diagnostic reads now step down
first and seat only where room is left.
"""
from __future__ import annotations

import json

import pytest

from core import capability_graph as cg
from core.capability_graph import model_visible_specs
from core.tool_offer_state import begin_turn_navigation, end_turn_navigation, record_family_expansion

_PLAN = "MONTHS = {'March': ['peas'], 'April': ['carrots', 'beets']}\n"
COMMAND_READS = {"operator.command.status.show", "operator.command.capabilities.list"}


@pytest.fixture
def say(tmp_path, monkeypatch):
    from tests.gauntlet import harness

    home = tmp_path / "person"
    project = home / "Projects" / "garden-planner"
    project.mkdir(parents=True)
    (project / "plan.py").write_text(_PLAN)
    (home / "Documents").mkdir()
    monkeypatch.setenv("HOME", str(home))
    harness.install(monkeypatch)
    default_workspace = tmp_path / "default-workspace"
    default_workspace.mkdir()

    def _say(text: str) -> str:
        from core.web.api.service import dispatch_post

        res = dispatch_post(
            path="/api/chat",
            body={"stream": False, "session_id": "garden-chat", "turn_id": f"t-{abs(hash(text))}",
                  "messages": [{"role": "user", "content": text}]},
            headers={},
            runtime=harness.runtime(),
            model_name="gauntlet",
            workspace_root_provider=lambda: str(default_workspace),
            client_host="127.0.0.1",
        )
        assert res.status == 200, res.body[:400]
        return str((json.loads(res.body.decode("utf-8")).get("message") or {}).get("content") or "")

    yield _say
    cg.reset()


def _operator_expansion(user_text: str, *, also: tuple[str, ...] = (), hints: tuple[str, ...] = ()) -> list[str]:
    context: dict = {"_owner_local": True, "turn_id": "after-served", "runtime_session_id": "s-after-served"}
    scope = begin_turn_navigation(context)
    try:
        record_family_expansion(context, "operator")
        for family in also:
            record_family_expansion(context, family)
        specs = model_visible_specs(family_hints=hints, user_text=user_text, source_context=context)
        return [str(spec.get("intent") or "") for spec in specs]
    finally:
        end_turn_navigation(context, scope)


def test_a_project_chat_then_an_operator_expansion_still_offers_save_note(say) -> None:
    from core.tool_registry import registry_map

    answer = say("show me what's in plan.py in the garden planner project")
    assert "MONTHS" in answer and "peas" in answer, answer
    # The served turn projected the command reads: this is the world the daemon runs in.
    assert set(registry_map()) >= COMMAND_READS, sorted(i for i in registry_map() if "command" in i)

    cg.reset()
    for text, also, hints in (
        ("what launched on solana today", ("web",), ("token",)),
        ("summarize my notes from this week", (), ("web",)),
        ("save a note that the carrots go in after the peas", (), ()),
    ):
        intents = _operator_expansion(text, also=also, hints=hints)
        assert len(intents) <= cg._HARD_MAX_CANDIDATES, intents
        assert "operator.save_note" in intents, (text, intents)
