"""Served proof: reporting a bad answer after VOOL remembered something, on a running VOOL.

The person asks VOOL to remember a preference in fresh words (a scripted model calls the real memory
tool), then reports the answer as "remembered something wrong". The report is built by the real
bug-report lane from the turn's own records:

- it names the reason, the versions and the model, and carries a content-free trace: where the model
  call went and what kinds of data it carried, the turn's stages, and each action's checked result (here the
  save, read back from the memory store);
- what was remembered, and the person's question, are not in it (the question only when ticked);
- the local copy holds exactly the previewed bytes, and nothing was sent anywhere.
"""
from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import MODEL, _certify
from tests.test_setup_first_tries_served import _AskProvider

REPO = Path(__file__).resolve().parents[1]
_RNG = random.Random()


class _KeepProvider(_AskProvider):
    """The setup rig's model, which also reads a preference told to it as a fact worth keeping (the keep step's own
    yes/no questions), as the loaded local model does."""

    def reply(self, model, body):
        said = "\n".join(str((m or {}).get("content") or "") for m in body.get("messages") or [])
        if '{"answer": "yes"} or {"answer": "no"}' in said and self.tool == "remember":
            # The keep question asks whether the person said NOT to keep it (core.intent_arbiter, 0c3d44046): a
            # preference told to be kept is a no there, and a yes to every other question.
            return '{"answer": "no"}' if "NOT to remember" in said else '{"answer": "yes"}'
        return super().reply(model, body)


def _post(daemon: ServedDaemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(f"{daemon.base_url}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as exc:
        return int(getattr(exc, "code", 0)), json.loads(exc.read().decode() or "{}") if hasattr(exc, "read") else {}


def _turn(daemon: ServedDaemon, text: str, session: str, **extra) -> tuple[list[dict], dict]:
    body = {"messages": [{"role": "user", "content": text}], "stream": True, "stream_task_events": True, "session_id": session,
            "model": MODEL, "model_selection": "sticky", "mode": "manual", "surface": "desktop", **extra}
    body.setdefault("turn_id", uuid.uuid4().hex)
    events, commit = [], {}
    request = Request(f"{daemon.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=600) as response:
        for raw in response:
            try:
                frame = json.loads(raw.decode("utf-8", "replace").strip() or "null")
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict) and frame.get("vool_event"):
                events.append(frame["vool_event"])
            if isinstance(frame, dict) and isinstance(frame.get("vool_response_commit"), dict):
                commit = frame["vool_response_commit"]
    return events, commit


def _ask(daemon: ServedDaemon, text: str, session: str) -> dict:
    turn_id = uuid.uuid4().hex
    events, commit = _turn(daemon, text, session, turn_id=turn_id)
    held = [e.get("approval") or {} for e in events if e.get("type") == "permission.required" and e.get("approval")]
    if held:
        approval = held[0]
        status, _ = _post(daemon, "/api/mode", {"op": "resolve_approval", "approval_id": approval["approval_id"], "decision": "allow",
                                                "scope": "once", "session_id": session, "turn_id": turn_id})
        assert status == 200
        _events, commit = _turn(daemon, text, session, turn_id=turn_id, approval_token=str(approval["approval_id"]))
    return commit


def test_a_wrong_memory_report_carries_a_content_free_trace_and_saves_locally(tmp_path):
    home = tmp_path / "home"
    provider = _KeepProvider()
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        env = {"VOOL_MODEL_LOAD_FLOOR_GB": "0", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "person")}
        (tmp_path / "person").mkdir()
        with ServedDaemon(home, env_extra=env) as daemon:
            assert _certify(daemon).get("state") == "verified"
            session = "openclaw:" + uuid.uuid4().hex[:20]
            secret_style = _RNG.choice(("tiny haiku replies", "answers in pirate voice", "one emoji-free line")) + " " + uuid.uuid4().hex[:5]
            provider.words, provider.tool = ("memory, keep",), "remember"
            provider.arguments = {"category": "response_style", "value": secret_style, "scope": "global"}
            question = _RNG.choice(("pls remember that I like {v}", "from now on I want {v}", "note that I like {v}",
                                    "keep in mind I prefer {v}")).format(v=secret_style)
            commit = _ask(daemon, question, session)
            request_id = str(commit.get("request_id") or "")
            assert request_id, commit

            status, refused = _post(daemon, "/api/bug-report/answer-draft", {"session_id": session, "request_id": request_id, "reason": "made_up_reason"})
            assert status == 400, refused
            status, made = _post(daemon, "/api/bug-report/answer-draft", {
                "session_id": session, "request_id": request_id, "reason": "wrong_memory",
                "what_was_wrong": "it kept the wrong thing", "include_receipt": True})
            assert status == 200, made
            report_id = made["report_id"]
            status, preview = _post(daemon, "/api/bug-report/preview", {"report_id": report_id})
            assert status == 200, preview
            body = preview["issue"]["body"] if "issue" in preview else json.dumps(preview)
            assert "Remembered or recalled something wrong" in body, body
            assert "## Trace" in body, body
            # The trace says what ran. Some wordings ("note that I like ...") are the memory lane's own
            # command and save with no model call (CI on 882ba9a05 drew one); the report must then
            # name no model call and no model, and a model-path save must name both.
            if "provider: runtime-fast-path" in body:
                assert "model call:" not in body and MODEL not in body, body
            else:
                assert "model call: this_computer" in body, body
                assert MODEL in body, body
            assert "## Environment" in body or "VOOL" in body, body
            # The save is an action with a checked result: the memory store holds the row the turn wrote.
            assert "action result: approval.action.profile.remember -> checked" in body, body
            assert secret_style not in body, "what was remembered never enters a report"
            assert question not in body, "the question is left out unless the person ticks it"

            status, saved = _post(daemon, "/api/bug-report/export", {"report_id": report_id})
            assert status == 200, saved
            on_disk = Path(saved["path"]).read_bytes()
            assert json.loads(on_disk)["body"] == body
