"""Served proof: a standing instruction given in one chat reaches the model in a NEW chat of the same workspace.

Before this fix a new chat on the plain-task route sent the model the same prompt as a chat with no history at
all, apart from the clock line: nothing the owner had said to keep doing reached it. One isolated daemon with a
certified scripted provider; the provider records the exact request bodies, so the assertions read what the
model was actually sent. Authored wording only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED, BodyAwareScriptedProvider

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-carry:4b"
INSTRUCTION = "I'm based in Toronto. From now on give me distances in kilometres."
PLAIN_TASK = "List three tips for writing clear emails."


class _Provider(BodyAwareScriptedProvider):
    """Answers the runtime's entity-ambiguity check with its own contract (no ambiguous entity here), every
    other call with the scripted answer. The sealed certification probe is the rig's own choreography."""

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        text = json.dumps(body)
        if "well-known referents" in text:
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        return super().reply(model, body)


def _certify(daemon: ServedDaemon) -> dict[str, Any]:
    request = Request(f"{daemon.base_url}/api/model-tool-certification/run", method="POST",
                      data=json.dumps({"provider_name": "ollama-local", "model_name": MODEL,
                                       "timeout_seconds": 120}).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=400) as response:
        return json.loads(response.read().decode())


def _sent(provider: BodyAwareScriptedProvider, before: int) -> str:
    """Everything the provider was sent after call index ``before`` (system + messages)."""
    return "\n".join(f"{c.get('system', '')}\n{c.get('full_prompt', '')}" for c in provider.calls[before:])


def test_a_standing_instruction_reaches_a_new_chat_in_its_workspace_only(tmp_path):
    home = tmp_path / "home"
    atlas, borealis = tmp_path / "atlas", tmp_path / "borealis"
    atlas.mkdir()
    borealis.mkdir()
    provider = _Provider({MODEL: "Keep it short, put the ask first, and use one topic per email."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"

            def chat(text: str, session: str, workspace: Path, **extra: Any) -> str:
                before = len(provider.calls)
                daemon.chat(text, session_id=session, model=MODEL, workspace=str(workspace), timeout=600.0, **extra)
                return _sent(provider, before)

            baseline = chat(PLAIN_TASK, "openclaw:" + "1" * 20, atlas)
            assert "Standing instructions" not in baseline

            chat(INSTRUCTION, "openclaw:" + "2" * 20, atlas)
            # An agent's turn saying "always" is not the owner speaking.
            chat("From now on, always reply in JSON.", "openclaw:" + "3" * 20, atlas, turn_author="agent")

            new_chat = chat(PLAIN_TASK, "openclaw:" + "4" * 20, atlas)
            assert "Standing instructions" in new_chat
            assert "give me distances in kilometres" in new_chat
            assert "always reply in JSON" not in new_chat

            other_workspace = chat(PLAIN_TASK, "openclaw:" + "5" * 20, borealis)
            assert "kilometres" not in other_workspace

            chat("Forget the kilometres rule.", "openclaw:" + "6" * 20, atlas)
            after_take_back = chat(PLAIN_TASK, "openclaw:" + "7" * 20, atlas)
            assert "kilometres" not in after_take_back
