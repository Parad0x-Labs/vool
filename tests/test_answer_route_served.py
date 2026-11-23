"""Served proof: every answer's chip says where it was processed, from the turn's own send receipts.

One isolated daemon with a certified scripted model on 127.0.0.1. Turns run through the real front
door, the real invocation gateway and the real proof route: the route is read from the
`provider_dispatch` receipts the gateway files when a sealed payload is handed to transport, never
from the answer's words.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED, BodyAwareScriptedProvider

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-route:4b"


class _Provider(BodyAwareScriptedProvider):
    def reply(self, model: str, body: dict[str, Any]) -> Any:
        if "well-known referents" in json.dumps(body):
            return '{"ambiguous": false, "referents": [], "clarification": ""}'
        return super().reply(model, body)


def _certify(daemon: ServedDaemon) -> dict[str, Any]:
    request = Request(f"{daemon.base_url}/api/model-tool-certification/run", method="POST",
                      data=json.dumps({"provider_name": "ollama-local", "model_name": MODEL,
                                       "timeout_seconds": 120}).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=400) as response:
        return json.loads(response.read().decode())


def _turn(daemon: ServedDaemon, text: str, session: str) -> tuple[str, dict[str, Any]]:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "session_id": session, "model": MODEL}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    answer, commit = "", {}
    with urlopen(request, timeout=600) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            try:
                frame = json.loads(line) if line else {}
            except json.JSONDecodeError:
                continue
            if isinstance(frame.get("vool_response_commit"), dict):
                commit = frame["vool_response_commit"]
            answer += str((frame.get("message") or {}).get("content") or "")
    request_id = str(commit.get("request_id") or "")
    assert request_id, "the served turn committed no request id"
    query = urlencode({"session": session, "request_id": request_id})
    with urlopen(f"{daemon.base_url}/api/chat/proof?{query}", timeout=60) as r:
        return str(commit.get("canonical_content") or answer), json.loads(r.read().decode())


def test_a_local_answer_names_this_computer_for_every_call_it_sent(tmp_path):
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Small crabs and sea anemones live there."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert _certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "4e" * 10

            before = len(provider.calls)
            answer, proof = _turn(daemon, "name one creature you'd find in a rock pool", chat)
            assert len(provider.calls) > before, "control: the model must have been asked"
            route = proof["expanded"]["route"]
            assert proof["compact"]["route"]["summary"] == "local", route
            assert route["calls"] and all(row["where"] == "this_computer" for row in route["calls"]), route
            assert any(row["model"] == MODEL and row["bytes"] > 0 for row in route["calls"]), route
            assert route["online_bytes"] == 0 and proof["compact"]["route"]["carried_online"] == [], route
