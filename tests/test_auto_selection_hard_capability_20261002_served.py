"""SERVED hard-capability selection: what a real Auto chat dispatches, and to whom.

A real ``apps.vool_api_server`` daemon in its own VOOL_HOME, a scripted Ollama-dialect provider
for every local lane, a second scripted endpoint standing in for a paid cloud provider, and a
sealed fixture transport that refuses every other host. Ingress, the turn routing plan,
``rank_provider_candidates``, the autopilot, the media review lane, permissions and dispatch
are production code from this checkout; only the provider transport is synthetic, so these
prove routing and dispatch truth, never model answer quality.

Each case re-seeds the manifest table and reads two independent records of what happened:
the wire (every request the scripted endpoints received) and the canonical per-request
routing receipts (``model_lane_started`` names the task kind and model of each dispatch).

The external-image review lane (``MediaAnalysisPipeline``) is not reachable from this chat door:
an image-URL question is answered by the front door's web fetch, and chat attachments ride the
attachment carrier. Its hard-capability law is proven against the real owner in
``test_auto_selection_hard_capability_20261002.py``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from urllib.request import Request, urlopen

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, _chat_with_operator, run_in_home
from tests._reader_served_rig import _probe_reply

pytestmark = [pytest.mark.served]

SHIM = REPO_ROOT / "tests" / "fixture_transport"

# Stub tags read as 8B ACTIVE parameters (clearing the tool-selection size floor) but a ~1 GB
# load footprint, so the host's real memory gate (core.resource_governor, live free RAM) never
# refuses a lane this rig does not actually load.
REASONING = "reasonix-1b-a8b:q4"     # VOOL's reasoning-lane profile: structured_json, no tool_intent
GENERAL = "generalist-1b-a8b:q4"     # general-lane profile: structured_json + tool_intent
SPARE = "spareist-1b-a8b:q4"         # a second general-lane model
CLOUD = "cloudy-model-x"    # paid cloud, never authorized here

REASONING_CAPS = ["summarize", "classify", "format", "extract", "structured_json", "code_basic", "code_complex", "long_context"]
GENERAL_CAPS = ["summarize", "classify", "format", "extract", "structured_json", "code_basic", "tool_intent"]

PROBE = "hard-capability-probe"

# Result-carry cases. Each command's real output holds a marker that appears nowhere in the
# operator's request or the command's arguments (the data lives only in a workspace file), so the
# marker can reach a later provider payload or the shipped answer only by the command having run
# and its sealed result having been carried forward. Two different sandbox-whitelisted commands
# over two different files, both in the model-routed "run the command" wording: a bare file read
# is served by a deterministic fast path with no model, so it cannot exercise selection.
CARRY_CASES = {
    "head": {
        "command": "head -n 1 ledger/carry-note.txt",
        "file": "ledger/carry-note.txt",
        "content": "ledger-total-4817-teal\nsecond line stays unread\n",
        "marker": "ledger-total-4817-teal",
        "answer": "The first line is",
    },
    "grep": {
        "command": "grep -h quartz inbox/notes.txt",
        "file": "inbox/notes.txt",
        "content": "amber line\ninvoice-7c2e-quartz paid\nslate line\n",
        "marker": "invoice-7c2e-quartz",
        "answer": "The matching line names",
    },
}


SEED = '''
import sys
sys.path.insert(0, {root!r})
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

run_migrations()

def local(name, caps):
    return ModelProviderManifest(
        provider_name="ollama-local", model_name=name, source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3", weight_location="external",
        runtime_dependency="ollama", capabilities=caps,
        runtime_config={{"base_url": {base!r}, "timeout_seconds": 30}},
        metadata={{"runtime_family": "ollama", "cost_class": "free_local", "model_digest": "sha256:stub",
                   "chat_template_hash": "tmpl", "quantization": "q4_K_M"}},
    )

def cloud(name):
    return ModelProviderManifest(
        provider_name="cloudy-remote", model_name=name, source_type="http",
        adapter_type="openai_compatible", license_name="Provider", license_reference="user-managed",
        weight_location="external", runtime_dependency="remote-openai-compatible-provider",
        capabilities=["summarize", "classify", "format", "long_context", "structured_json", "tool_intent"],
        runtime_config={{"base_url": {cloud_base!r}, "timeout_seconds": 30}},
        metadata={{"deployment_class": "cloud", "cost_class": "paid_cloud"}},
    )

conn = get_connection()
conn.execute("DELETE FROM model_provider_manifests")
conn.commit()
conn.close()
for name, caps in {locals_!r}:
    upsert_provider_manifest(local(name, caps))
for name in {clouds!r}:
    upsert_provider_manifest(cloud(name))
print(sorted(m.provider_id for m in list_provider_manifests()))
'''


def _is_tool_step(prompt: str) -> bool:
    return "respond.direct" in prompt and "sandbox.run_command" in prompt


def _carry_reply(body: dict[str, Any], prompt: str) -> str | None:
    """A carry case's tool step: run its command, then answer ONLY from what came back.

    The closing answer quotes the marker as the provider found it in the payload, so a lost or
    unexecuted result cannot produce a grounded answer: the step would keep asking to run."""
    for case in CARRY_CASES.values():
        if f"run the command: {case['command']}" not in prompt:
            continue
        if case["marker"] in prompt:
            return json.dumps(
                {"intent": "respond.direct", "arguments": {"message": f"{case['answer']} {case['marker']}."}}
            )
        return json.dumps({"intent": "sandbox.run_command", "arguments": {"command": case["command"]}})
    return None


def _reply(body: dict[str, Any]) -> Any:
    """Answer a tool-selection step with the probe command once, then close with respond.direct
    when the command's own output is back in the prompt; everything else gets plain text."""
    # The production local-model certification probe (a stub cannot author a final answer
    # until it passes it): really emit the parallel calls, carry the sealed result, repair.
    probe = _probe_reply(body)
    if probe is not None:
        return list(probe["tool_calls"]) if probe.get("tool_calls") else str(probe.get("content") or "")
    messages = body.get("messages") or []
    prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
    prompt += " " + str(body.get("system") or "")
    if "turns on an entity that has several" in prompt:
        # The entity-ambiguity pre-check (core.entity_ambiguity): these questions name no entity.
        return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
    if not _is_tool_step(prompt):
        return None
    carry = _carry_reply(body, prompt)
    if carry is not None:
        return carry
    if prompt.count(PROBE) >= 3:
        return json.dumps({"intent": "respond.direct", "arguments": {"message": f"The command printed {PROBE}."}})
    return json.dumps({"intent": "sandbox.run_command", "arguments": {"command": f"echo {PROBE}"}})


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("hard-capability-served") / "home"
    home.mkdir(parents=True)
    transport = home.parent / "sealed_transport.json"
    transport.write_text(json.dumps({"rules": []}), encoding="utf-8")
    # A disposable, bounded workspace for the command: the sandbox's reversible-capture gate
    # refuses a command whose preimage it cannot scan.
    workspace = home.parent / "workspace"
    workspace.mkdir()
    for case in CARRY_CASES.values():
        target = workspace / case["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(case["content"], encoding="utf-8")
    local = ScriptedProvider({}, default="Plain scripted answer.", reply_fn=_reply)
    cloud = ScriptedProvider({}, default="cloud reply")
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "OLLAMA_HOST": local.base_url,
            "VOOL_OLLAMA_URL": local.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{local.base_url}/api/chat",
            # Sealed: every non-loopback host is refused, so no drive can reach the internet.
            "PYTHONPATH": os.pathsep.join([str(SHIM), str(REPO_ROOT)]),
            "VOOL_FIXTURE_TRANSPORT_MANIFEST": str(transport),
            "VOOL_FIXTURE_TRANSPORT_LOG": str(home.parent / "transport.log"),
        },
    )
    local.__enter__()
    cloud.__enter__()
    try:
        # A daemon that cannot boot is a failed acceptance run, never a skip.
        daemon.start(timeout=240)
        receipts = home.parent / "receipts"
        receipts.mkdir()
        yield {
            "home": home, "daemon": daemon, "local": local, "cloud": cloud, "workspace": workspace,
            "certified": set(), "receipts": receipts,
        }
    finally:
        daemon.stop()
        local.__exit__(None, None, None)
        cloud.__exit__(None, None, None)


def _seed(served: dict[str, Any], locals_: list[tuple[str, list[str]]], clouds: tuple[str, ...] = ()) -> None:
    run_in_home(
        served["home"],
        SEED.format(
            root=str(REPO_ROOT),
            base=served["local"].base_url,
            cloud_base=served["cloud"].base_url,
            locals_=locals_,
            clouds=list(clouds),
        ),
    )
    for name, _caps in locals_:
        if name not in served["certified"]:
            _certify(served, name)
            served["certified"].add(name)
    served["local"].reset()
    served["cloud"].reset()


def _certify(served: dict[str, Any], model: str) -> None:
    """Certify a stub as a final-answer author through the real certification door and prober."""
    request = Request(
        f"{served['daemon'].base_url}/api/model-tool-certification/run",
        data=json.dumps({"provider_name": "ollama-local", "model_name": model, "timeout_seconds": 120}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=240) as response:
        result = json.loads(response.read().decode("utf-8"))
    assert str(result.get("state") or "").lower() == "verified", result


def _event_count(home: Path) -> int:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM runtime_session_events").fetchone()[0])
    finally:
        conn.close()


def _events_since(home: Path, start: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT event_type, message, COALESCE(details_json, '') FROM runtime_session_events ORDER BY rowid"
        ).fetchall()[start:]
    finally:
        conn.close()
    events = []
    for event_type, message, details in rows:
        try:
            parsed = json.loads(details) if details else {}
        except json.JSONDecodeError:
            parsed = {}
        events.append({"type": str(event_type), "message": str(message or ""), "details": parsed if isinstance(parsed, dict) else {}})
    return events


def _dispatches(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(task_kind, model) of every provider dispatch the live router started."""
    return [
        (str(e["details"].get("task_kind") or ""), str(e["details"].get("model_id") or ""))
        for e in events
        if e["type"] == "model_lane_started"
    ]


def _tool_runs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] in {"tool_executed", "tool_result_published"}]


def _wire_tool_steps(provider: ScriptedProvider, model: str) -> int:
    return sum(
        1
        for call in provider.calls
        if call["model"] == model and _is_tool_step(f"{call['full_prompt']} {call['system']}")
    )


def _generated_by(provider: ScriptedProvider) -> set[str]:
    """Models that were asked to GENERATE (chat calls with a prompt), not metadata probes."""
    return {
        call["model"]
        for call in provider.calls
        if call["path"].rstrip("/").endswith(("/api/chat", "/chat/completions")) and str(call["full_prompt"]).strip()
    }


def _diag(events: list[dict[str, Any]], provider: ScriptedProvider) -> str:
    rows = [f"{e['type']}: {e['message'][:140]}" for e in events]
    wire = [f"{c['model']} {c['path']} :: {(c['system'] or c['prompt'])[:90]!r}" for c in provider.calls]
    return "\n".join(rows[-60:]) + "\nwire:\n" + "\n".join(wire)


def _text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or "")


def _keep_receipt(served: dict[str, Any], name: str, **record: Any) -> None:
    """Retain the case's raw evidence (wire calls, canonical events, shipped text) as JSON."""
    path = served["receipts"] / f"{name}.json"
    path.write_text(json.dumps(record, indent=1, sort_keys=True, default=str), encoding="utf-8")


def _tool_step_calls(provider: ScriptedProvider) -> list[dict[str, Any]]:
    return [call for call in provider.calls if _is_tool_step(f"{call['full_prompt']} {call['system']}")]


# --------------------------------------------------------------------------------------------


def test_served_tool_turn_without_a_tool_capable_model_dispatches_no_tool_step(served) -> None:
    _seed(served, [(REASONING, REASONING_CAPS)])
    start = _event_count(served["home"])

    payload = _chat_with_operator(served["daemon"], served["home"], "hc-tool-none", f"run the command: echo {PROBE}", model="vool")
    events = _events_since(served["home"], start)

    assert ("tool_intent", REASONING) not in _dispatches(events), _diag(events, served["local"])
    assert _wire_tool_steps(served["local"], REASONING) == 0, _diag(events, served["local"])
    # Nothing ran, and the turn did not answer as if it had.
    assert not any("sandbox.run_command" in json.dumps(e["details"]) for e in _tool_runs(events)), _diag(events, served["local"])
    assert f"printed {PROBE}" not in _text(payload)


def test_served_tool_turn_runs_on_the_capable_model_and_executes_once(served) -> None:
    _seed(served, [(REASONING, REASONING_CAPS), (GENERAL, GENERAL_CAPS)])
    start = _event_count(served["home"])

    payload = _chat_with_operator(
        served["daemon"], served["home"], "hc-tool-capable", f"run the command: echo {PROBE}", model="vool",
        workspace=str(served["workspace"]),
    )
    events = _events_since(served["home"], start)
    dispatches = _dispatches(events)

    tool_models = {model for kind, model in dispatches if kind == "tool_intent"}
    assert tool_models == {GENERAL}, _diag(events, served["local"])
    assert _wire_tool_steps(served["local"], REASONING) == 0, _diag(events, served["local"])
    executed = [e for e in _tool_runs(events) if e["type"] == "tool_executed" and "sandbox.run_command" in json.dumps(e["details"])]
    assert len(executed) == 1, _diag(events, served["local"])
    assert _text(payload).strip(), payload


@pytest.mark.parametrize("case_id", sorted(CARRY_CASES))
def test_served_tool_result_is_carried_forward_and_grounds_the_answer(served, case_id) -> None:
    """The decisive positive route: the capable model chooses the command, the real sandbox runs
    it with exactly those arguments, the sealed result rides the next provider round, and the
    shipped answer is grounded in it."""
    case = CARRY_CASES[case_id]
    _seed(served, [(REASONING, REASONING_CAPS), (GENERAL, GENERAL_CAPS)])
    start = _event_count(served["home"])

    payload = _chat_with_operator(
        served["daemon"], served["home"], f"hc-carry-{case_id}", f"run the command: {case['command']}", model="vool",
        workspace=str(served["workspace"]),
    )
    events = _events_since(served["home"], start)
    calls = list(served["local"].calls)
    executed = [
        e for e in events
        if e["type"] == "tool_executed" and e["details"].get("tool_name") == "sandbox.run_command"
    ]
    _keep_receipt(
        served, f"carry-{case_id}", case=case, dispatches=_dispatches(events), executed=executed,
        events=events, wire=calls, shipped=_text(payload),
    )
    diag = _diag(events, served["local"])

    # The chosen provider: every tool-selection round went to the tool-capable model only.
    assert {model for kind, model in _dispatches(events) if kind == "tool_intent"} == {GENERAL}, diag
    tool_calls = _tool_step_calls(served["local"])
    assert len(tool_calls) >= 2 and {call["model"] for call in tool_calls} == {GENERAL}, diag

    # Canonical execution: exactly one real run, with exactly the arguments the model chose.
    assert len(executed) == 1, diag
    assert executed[0]["details"].get("arguments") == {"command": case["command"]}, executed[0]
    assert executed[0]["details"].get("ok") is True, executed[0]
    assert case["marker"] not in json.dumps(executed[0]["details"].get("arguments")), executed[0]

    # The result came from the run: absent from the round that chose the command, present in a
    # message of the next round's payload to the same model.
    first = tool_calls[0]
    assert case["marker"] not in f"{first['full_prompt']} {first['system']}", diag
    carriers = [
        message for call in tool_calls[1:] for message in call["messages"] if case["marker"] in message["content"]
    ]
    assert carriers, diag

    # The shipped answer is grounded in the carried result.
    assert f"{case['answer']} {case['marker']}" in _text(payload), payload


def test_served_incapable_routes_never_run_the_command_or_see_its_result(served) -> None:
    """Zero forbidden execution: with only a tool-incapable model, in Auto and pinned, the
    command never runs, its output never reaches any provider, and no answer claims it."""
    case = CARRY_CASES["head"]
    _seed(served, [(REASONING, REASONING_CAPS)])
    results = {}
    for session, model in (("hc-carry-none-auto", "vool"), ("hc-carry-none-pin", REASONING)):
        served["local"].reset()
        start = _event_count(served["home"])
        payload = _chat_with_operator(
            served["daemon"], served["home"], session, f"run the command: {case['command']}", model=model,
            workspace=str(served["workspace"]),
        )
        events = _events_since(served["home"], start)
        calls = list(served["local"].calls)
        _keep_receipt(served, session, events=events, wire=calls, shipped=_text(payload))
        results[session] = (payload, events, calls)

    for session, (payload, events, calls) in results.items():
        diag = f"{session}\n" + _diag(events, served["local"])
        assert ("tool_intent", REASONING) not in _dispatches(events), diag
        assert not any("sandbox.run_command" in json.dumps(e["details"]) for e in _tool_runs(events)), diag
        assert not any(case["marker"] in json.dumps(call) for call in calls), diag
        assert case["marker"] not in _text(payload), payload


def test_served_plain_pin_is_exact_and_an_incapable_pin_is_never_substituted(served) -> None:
    _seed(served, [(REASONING, REASONING_CAPS), (GENERAL, GENERAL_CAPS)])

    start = _event_count(served["home"])
    plain = served["daemon"].chat("answer with exactly: pinned-ok", session_id="hc-pin", mode="auto", model=REASONING, timeout=240)
    plain_events = _events_since(served["home"], start)
    assert _text(plain).strip(), plain
    assert {model for _kind, model in _dispatches(plain_events)} == {REASONING}, _diag(plain_events, served["local"])
    assert _generated_by(served["local"]) == {REASONING}, _diag(plain_events, served["local"])

    served["local"].reset()
    start = _event_count(served["home"])
    _chat_with_operator(served["daemon"], served["home"], "hc-pin", f"run the command: echo {PROBE}", model=REASONING)
    tool_events = _events_since(served["home"], start)
    # The pinned model declares no tool selection: it is not handed the tool step, and no
    # other model silently answers or acts in its place.
    assert GENERAL not in _generated_by(served["local"]), _diag(tool_events, served["local"])
    assert ("tool_intent", REASONING) not in _dispatches(tool_events), _diag(tool_events, served["local"])
    assert not any("sandbox.run_command" in json.dumps(e["details"]) for e in _tool_runs(tool_events))


def test_served_chats_keep_their_own_policy_and_auto_never_dispatches_paid(served) -> None:
    _seed(served, [(GENERAL, GENERAL_CAPS)], clouds=(CLOUD,))

    local_only = served["daemon"].chat("answer with exactly: local-only-ok", session_id="hc-chat-a", mode="auto", model="vool-local-only", timeout=240)
    # An explicit paid pick IS the owner's permission under the existing reservation law (it
    # reserves within the per-call cap). Here it is still refused before the wire, because this
    # rig's loopback "cloud" endpoint is not a certified final-answer author; what this chat
    # proves is that no other model answers in the pin's place and nothing leaks to the others.
    served["local"].reset()
    pinned_paid = served["daemon"].chat("answer with exactly: paid-pin", session_id="hc-chat-b", mode="auto", model=CLOUD, timeout=240)
    answered_for_the_pin = _generated_by(served["local"])
    auto = served["daemon"].chat("answer with exactly: auto-ok", session_id="hc-chat-c", mode="auto", model="vool", timeout=240)
    local_again = served["daemon"].chat("answer with exactly: local-only-again", session_id="hc-chat-a", mode="auto", model="vool-local-only", timeout=240)

    # Auto and Local Only carry no paid reservation: the registered paid lane is never dispatched.
    assert served["cloud"].calls == [], f"paid cloud received {len(served['cloud'].calls)} dispatches"
    assert _text(local_only).strip() and _text(auto).strip() and _text(local_again).strip()
    assert answered_for_the_pin == set(), f"a local model generated in the paid pin's place: {answered_for_the_pin}"
    assert "Plain scripted answer." not in _text(pinned_paid), pinned_paid


def test_served_a_capability_change_moves_the_next_tool_step(served) -> None:
    _seed(served, [(GENERAL, GENERAL_CAPS), (SPARE, [c for c in GENERAL_CAPS if c != "tool_intent"])])
    start = _event_count(served["home"])
    _chat_with_operator(served["daemon"], served["home"], "hc-switch", f"run the command: echo {PROBE}", model="vool")
    first = {model for kind, model in _dispatches(_events_since(served["home"], start)) if kind == "tool_intent"}
    assert first == {GENERAL}

    # The operator's catalog changes between turns: GENERAL no longer selects tools, SPARE does.
    _seed(served, [(GENERAL, [c for c in GENERAL_CAPS if c != "tool_intent"]), (SPARE, GENERAL_CAPS)])
    start = _event_count(served["home"])
    _chat_with_operator(served["daemon"], served["home"], "hc-switch", f"run the command: echo {PROBE}", model="vool")
    events = _events_since(served["home"], start)
    second = {model for kind, model in _dispatches(events) if kind == "tool_intent"}

    assert second == {SPARE}, _diag(events, served["local"])
    assert _wire_tool_steps(served["local"], GENERAL) == 0, _diag(events, served["local"])
