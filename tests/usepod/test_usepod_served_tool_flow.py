"""The served UsePod TOOL flow: what the architecture actually does, proven end to end.

The runtime's own law (core/paid_call_reservation.py, "internal_tool_intent_call", present at the
base) keeps an explicit PAID pin off a turn's internal tool-selection rounds: the pick authorizes
the answering lane, not the loop machinery. This suite proves BOTH sides of that law through the
served daemon rather than bypassing it:

* a PINNED UsePod turn with a tool-shaped request refuses the selection round visibly, answers
  plainly on the paid lane with exactly one dispatch, one reservation and a correlated receipt --
  in BOTH wire protocols, on the original and a novel model;
* an UNPINNED turn drives a real tool round trip: the served tool loop offers the catalog, a
  labelled free loopback lane picks the harmless disposable local tool, the daemon EXECUTES it for
  real, and the tool result is returned to the provider in the next request on the wire;
* a provider tool call that names a tool never offered, or arguments that are not JSON, is refused
  closed with a typed error and executes nothing;
* without the monetary TEST DOUBLE a prepaid turn visibly refuses before any dispatch, and an
  x402 turn refuses before any reservation or payment with exactly one quote request -- the
  missing dependency is the reason shown;
* under the ACTUAL streaming mode of this served path (the provider call is buffered, the client
  stream is replayed from it -- labelled honestly, no upstream SSE is claimed), an unparsable
  provider body retains the liability (reserve -> mark_dispatched -> retain_unknown) and a
  mid-stream cancellation stops the turn with the already-completed provider call settled.

The monetary authority in the main fixture is the labelled TEST DOUBLE from
``_served_usepod_launcher`` (every reservation granted, journaled); nothing here is paid and no
wallet exists. The free tool-selection lane (``_free_tool_lane``) is a SYNTHETIC loopback service,
not UsePod. UsePod's own answers come from the SYNTHETIC strict local service.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.usepod._free_tool_lane import FreeToolLaneService, TOOL_NATIVE_NAME
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_served_flow import (
    DOUBLE_LABEL,
    INFERENCE_PATHS,
    MARKET,
    MARKET_ID,
    MODEL,
    UsePodServedDaemon,
    _free_port,
    _session,
)
from tests.usepod.test_usepod_settings_ui import CENTRAL, PlainServedDaemon

REPO_ROOT = Path(__file__).resolve().parents[2]
#: A second, NOVEL model with its own marketplace listing at different prices.
NOVEL_MODEL = "meridian-synth-chat-mini"
NOVEL_MARKET_ID = "7e8f9a0b-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
NOVEL_MARKET = (330_000, 880_000)
FREE_LANE_ID = "freelane-tools:freelane-tool-picker"


class ToolFlowDaemon(UsePodServedDaemon):
    """The monetary-double daemon from the served flow, plus the free tool-selection lane
    registered through the production registry, and a DISPOSABLE scratch cwd so the executed
    workspace tool reads files this suite planted, not the repository."""

    def __init__(self, home: Path, scratch: Path, free_base_url: str) -> None:
        super().__init__(home)
        self.scratch = scratch
        self.free_base_url = free_base_url

    def env(self) -> dict[str, str]:
        env = super().env()
        # The executed workspace tool must read the files this suite planted. An inherited
        # VOOL_WORKSPACE_ROOT (a test runner's isolated root, an operator shell) outranks the
        # daemon's cwd, so the scratch root is named explicitly rather than implied by the cwd.
        env["VOOL_WORKSPACE_ROOT"] = str(self.scratch)
        return env

    def start(self, timeout: float = 240.0) -> "ToolFlowDaemon":
        self.scratch.mkdir(parents=True, exist_ok=True)
        script = (
            "from core.model_registry import ModelRegistry\n"
            "from storage.model_provider_manifest import ModelProviderManifest\n"
            "ModelRegistry().register_manifest(ModelProviderManifest(\n"
            "    provider_name='freelane-tools', model_name='freelane-tool-picker',\n"
            "    source_type='http', adapter_type='openai_compatible',\n"
            "    license_name='synthetic-test-lane', license_reference='loopback', license_url_or_reference='loopback',\n"
            "    weight_location='external', weights_bundled=False, redistribution_allowed=False,\n"
            "    runtime_dependency='loopback http',\n"
            "    capabilities=['summarize', 'tool_intent'],\n"
            f"    runtime_config={{'base_url': {self.free_base_url!r}, 'timeout_seconds': 60, "
            "'health_timeout_seconds': 5, 'temperature': 0.0, 'supports_json_mode': False, 'tool_dialect': 'native'},\n"
            "    metadata={'cost_class': 'free'},\n"
            "    enabled=True,\n"
            "))\n"
            "print('registered')\n"
        )
        completed = subprocess.run([sys.executable, "-B", "-c", script], cwd=str(REPO_ROOT), env=self.env(), capture_output=True, text=True, timeout=180)
        if completed.returncode != 0:
            raise RuntimeError(f"free-lane registration failed:\n{completed.stdout}\n{completed.stderr}")
        self.home.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-B", str(Path(REPO_ROOT / "tests/usepod/_served_usepod_launcher.py")), "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(self.scratch),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        import urllib.error
        from urllib.request import urlopen

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except Exception:
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")


def _chat(daemon, text: str, *, session_id: str, model: str | None, stream: bool = False, turn_id: str = "") -> tuple[int, object]:
    payload: dict = {"messages": [{"role": "user", "content": text}], "stream": stream, "session_id": session_id, "mode": "auto"}
    if model:
        payload["model"] = model
    if turn_id:
        payload["turn_id"] = turn_id
    return daemon.call("POST", "/api/chat", payload, timeout=300.0)


def _answer_text(answer: object) -> str:
    if isinstance(answer, dict):
        message = answer.get("message") if isinstance(answer.get("message"), dict) else {}
        return str(message.get("content") or "")
    parts = []
    for line in str(answer or "").splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        message = frame.get("message") if isinstance(frame.get("message"), dict) else {}
        if isinstance(message.get("content"), str):
            parts.append(message["content"])
    return "".join(parts)


def _receipts(events: list[dict]) -> list[dict]:
    out = []
    for event in events:
        details = event.get("details") if isinstance(event.get("details"), dict) else event
        if str(event.get("event_type") or "") == "model.call_completed" and isinstance(details.get("provider_receipt"), dict):
            out.append(details["provider_receipt"])
    return out


def _keep(name: str, payload: object) -> None:
    keep = os.environ.get("USEPOD_SERVED_ARTIFACT_DIR")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        (Path(keep) / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("usepod-tool-flow")
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={
            MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)],
            NOVEL_MODEL: [Listing("marketplace", NOVEL_MARKET_ID, *NOVEL_MARKET), Listing("centralized", *CENTRAL)],
        },
    ).start()
    free = FreeToolLaneService(mode="tool_round_trip").start()
    daemon = ToolFlowDaemon(root / "home", root / "scratch", free.base_url)
    try:
        daemon.start()
        status, saved = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1", "base_url": service.origin})
        assert status == 200, saved
        status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
        assert status == 200 and refreshed["marketplace"]["state"] == "fetched", refreshed
        status, policy = daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
        assert status == 200, policy
        yield SimpleNamespace(service=service, daemon=daemon, free=free, token=token)
    finally:
        daemon.stop()
        service.stop()
        free.stop()


def _pin_and_approve(served, model_id: str) -> None:
    daemon = served.daemon
    status, pinned = daemon.call("POST", "/api/cloud/model", {"model": model_id, "provider": "usepod", "confirm_paid": True})
    assert status == 200 and pinned.get("ok") is True, pinned
    status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": model_id})
    assert status == 200, approved


def _unpin(served) -> None:
    """Clear the persisted pin the way the authority accepts for a paid provider: `reset`
    (`auto` is refused for a provider with no free catalogue, by design)."""
    status, cleared = served.daemon.call("POST", "/api/cloud/model", {"model": "reset"})
    assert status == 200, cleared


# --- the paid-pin law, both protocols, original and novel model ---------------------------------------


def test_a_pinned_paid_turn_refuses_tool_selection_and_answers_with_exactly_one_dispatch(served) -> None:
    """Original model, OpenAI dialect. The refusal is visible, nothing is executed, and the
    answering lane carries exactly one dispatch, one reservation cycle and a receipt."""
    daemon, service, free = served.daemon, served.service, served.free
    _pin_and_approve(served, MODEL)
    before_journal = len(daemon.journal_lines())
    session_id = _session(f"pin-openai-{uuid.uuid4()}")
    status, answer = _chat(
        daemon,
        "List the files in the workspace root, then read the smallest text file and quote its first line.",
        session_id=session_id,
        model=f"usepod-byok:{MODEL}",
    )
    events = daemon.events(session_id)
    _keep("pinned_openai_tool_refusal.json", {"status": status, "answer": answer, "events": events})
    assert status == 200, answer
    blob = json.dumps(events)
    assert "internal_tool_intent_call" in blob, "the tool-selection refusal reason must be visible"
    # The pin invariant: the refused selection round substitutes nothing (no free-lane round).
    assert free.requests == []
    # No tool ran: the paid pin answered plainly.
    assert not [e for e in events if e.get("event_type") == "tool_executed"]
    # Exactly one paid dispatch: one inference request, one reserve->dispatch->settle cycle.
    arrived = service.requests_to(INFERENCE_PATHS["openai"]) + service.requests_to(INFERENCE_PATHS["anthropic"])
    assert len(arrived) == 1, [r["path"] for r in arrived]
    new_journal = daemon.journal_lines()[before_journal:]
    assert [line["call"] for line in new_journal] == ["reserve", "mark_dispatched", "settle"]
    receipts = [r for r in _receipts(events) if r.get("provider") == "usepod"]
    assert receipts and receipts[-1]["monetary_authority"] == DOUBLE_LABEL
    assert receipts[-1]["settlement"] == {"outcome": "completed", "recording": "settled_with_evidence"}
    assert "synthetic reply" in _answer_text(answer)


def test_the_same_law_holds_on_the_anthropic_lane_with_a_novel_model(served) -> None:
    """Novel model, novel request wording, Anthropic dialect. The refusal, the single dispatch and
    the receipt all name the novel model's own marketplace route."""
    daemon, service = served.daemon, served.service
    status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "anthropic", "transport_mode": "prepaid_token"})
    assert status == 200, lane
    _pin_and_approve(served, NOVEL_MODEL)
    before_journal = len(daemon.journal_lines())
    before = len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    session_id = _session(f"pin-anthropic-{uuid.uuid4()}")
    status, answer = _chat(
        daemon,
        "List the files in the workspace folder, then open the newest text file and quote its first line.",
        session_id=session_id,
        model=f"usepod-byok:{NOVEL_MODEL}",
    )
    events = daemon.events(session_id)
    _keep("pinned_anthropic_tool_refusal.json", {"status": status, "answer": answer, "events": events})
    assert status == 200, answer
    assert "internal_tool_intent_call" in json.dumps(events)
    assert not [e for e in events if e.get("event_type") == "tool_executed"]
    arrived = service.requests_to(INFERENCE_PATHS["anthropic"])[before:]
    assert len(arrived) == 1
    body = json.loads(arrived[0]["body"])
    assert body["model"] == NOVEL_MODEL and isinstance(body.get("max_tokens"), int)
    new_journal = daemon.journal_lines()[before_journal:]
    assert [line["call"] for line in new_journal] == ["reserve", "mark_dispatched", "settle"]
    receipts = [r for r in _receipts(events) if r.get("provider") == "usepod"]
    receipt = receipts[-1]
    assert (receipt["protocol"], receipt["model"]) == ("anthropic", NOVEL_MODEL)
    assert (receipt["route"]["class"], receipt["route"]["provider_id"]) == ("marketplace", NOVEL_MARKET_ID)
    assert "synthetic reply" in _answer_text(answer)
    # Back to the OpenAI lane for the tests that follow.
    status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "prepaid_token"})
    assert status == 200, lane


# --- the real tool round trip through the served daemon ------------------------------------------------


def test_a_tool_round_trip_executes_a_real_local_tool_and_returns_the_result_to_the_provider(served) -> None:
    """Unpinned. The harmless disposable tool is workspace.list_files over a scratch workspace
    this suite owns; the provider that asked for it receives the executed result on the wire."""
    daemon, service, free = served.daemon, served.service, served.free
    _unpin(served)
    (daemon.scratch / "alpha-notes.md").write_text("alpha marker one\n", encoding="utf-8")
    (daemon.scratch / "beta-readme.txt").write_text("beta marker two\n", encoding="utf-8")
    free.mode = "tool_round_trip"
    free.tool_name = TOOL_NATIVE_NAME
    before_free = len(free.requests)
    before_journal = len(daemon.journal_lines())
    before_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    session_id = _session(f"tools-openai-{uuid.uuid4()}")
    status, answer = _chat(
        daemon,
        "List the files in the workspace root, then read the smallest text file and quote its first line.",
        session_id=session_id,
        model=None,
    )
    events = daemon.events(session_id)
    _keep("tool_round_trip.json", {"status": status, "answer": answer, "events": events, "free_requests": free.requests[before_free:]})
    assert status == 200, answer
    # The offer carried the harmless tool on the wire.
    rounds = free.requests[before_free:]
    assert rounds, "the tool-selection round never reached the free lane"
    offered = {t["function"]["name"] for t in rounds[0]["body"].get("tools", [])}
    assert TOOL_NATIVE_NAME in offered, sorted(offered)
    # The tool EXECUTED for real: the runtime ran workspace.list_files over the scratch cwd.
    executed = [e for e in events if e.get("event_type") == "tool_executed" and e.get("tool_name") == "workspace.list_files"]
    assert executed, [e.get("event_type") for e in events]
    assert "alpha-notes.md" in _answer_text(answer) and "beta-readme.txt" in _answer_text(answer)
    # The executed result was RETURNED to the provider in the next round.
    later = rounds[1:]
    assert later, "no second round carried the tool result back to the provider"
    joined = json.dumps(later[0]["body"].get("messages", []))
    assert "Real tool observations from" in joined and "alpha-notes.md" in joined
    # No paid dispatch for this turn: the tool loop is free-lane work, nothing reserved.
    assert daemon.journal_lines()[before_journal:] == []
    after_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    assert after_paid == before_paid


def test_a_novel_tool_request_round_trip_with_different_data(served) -> None:
    """Genuinely new case for the same flow class: different wording, a DIFFERENT tool
    (workspace.list_tree over the whole tree), different planted data and a different answer."""
    daemon, service, free = served.daemon, served.service, served.free
    _unpin(served)
    (daemon.scratch / "gamma-zephyr-log.txt").write_text("the zephyr ledger line\n", encoding="utf-8")
    free.mode = "tool_round_trip"
    free.tool_name = "workspace__list_tree"
    before_free = len(free.requests)
    before_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    session_id = _session(f"tools-novel-{uuid.uuid4()}")
    status, answer = _chat(
        daemon,
        "List the files in the workspace root, then read the newest text file and quote its first line.",
        session_id=session_id,
        model=None,
    )
    events = daemon.events(session_id)
    _keep("tool_round_trip_novel.json", {"status": status, "answer": answer, "events": events, "free_requests": free.requests[before_free:]})
    assert status == 200, answer
    rounds = free.requests[before_free:]
    offered = {t["function"]["name"] for t in rounds[0]["body"].get("tools", [])}
    assert "workspace__list_tree" in offered, sorted(offered)
    executed = [e for e in events if e.get("event_type") == "tool_executed" and e.get("tool_name") == "workspace.list_tree"]
    assert executed, [e.get("event_type") for e in events]
    assert "gamma-zephyr-log.txt" in _answer_text(answer)
    later = rounds[1:]
    assert later and "gamma-zephyr-log.txt" in json.dumps(later[0]["body"].get("messages", []))
    # The free-lane tool loop paid nothing.
    after_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    assert after_paid == before_paid


def test_a_forbidden_tool_call_is_refused_closed_and_executes_nothing(served) -> None:
    """The provider names a tool that was never offered this round (a sandbox command). The whole
    batch fails closed with the typed unknown-tool error; no tool runs; nothing is paid."""
    daemon, service, free = served.daemon, served.service, served.free
    _unpin(served)
    free.mode = "forbidden_tool"
    free.tool_name = None   # the unoffered name must be the default, never a leaked earlier pick
    before_free = len(free.requests)
    before_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    session_id = _session(f"forbidden-{uuid.uuid4()}")
    status, answer = _chat(daemon, "List the files in the workspace root for me now.", session_id=session_id, model=None)
    events = daemon.events(session_id)
    _keep("forbidden_tool.json", {"status": status, "answer": answer, "events": events})
    blob = json.dumps(events)
    # The typed refusal is visible, and NOTHING executed.
    assert "unknown_tool_name" in blob
    assert not [e for e in events if e.get("event_type") == "tool_executed"]
    # Bounded: exactly one selection round, no retry storm.
    assert len(free.requests) - before_free == 1
    # No paid dispatch happened for a refused tool turn (deltas: earlier pinned turns are not this turn).
    after_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    assert after_paid == before_paid


def test_a_malformed_tool_call_is_refused_closed_and_executes_nothing(served) -> None:
    """Arguments that are not JSON: the typed malformed-arguments error, no execution."""
    daemon, service, free = served.daemon, served.service, served.free
    _unpin(served)
    free.mode = "malformed_arguments"
    before_free = len(free.requests)
    before_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    session_id = _session(f"malformed-{uuid.uuid4()}")
    status, answer = _chat(daemon, "List the files in the workspace root for me now.", session_id=session_id, model=None)
    events = daemon.events(session_id)
    _keep("malformed_tool.json", {"status": status, "answer": answer, "events": events})
    blob = json.dumps(events)
    assert "malformed_tool_arguments" in blob or "malformed" in blob.lower()
    assert not [e for e in events if e.get("event_type") == "tool_executed"]
    assert len(free.requests) - before_free == 1
    after_paid = len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    assert after_paid == before_paid


# --- the actual streaming mode's error and cancellation accounting ------------------------------------


def test_an_unparsable_provider_body_retains_the_liability_on_a_client_streamed_turn(served) -> None:
    """The served path buffers the provider call and replays the client stream from it; this is
    that mode's error accounting. A 200 whose body is not JSON is an outcome AFTER send: the
    reservation is retained, and the client stream ends in a readable failure, not a hang."""
    daemon, service = served.daemon, served.service
    _pin_and_approve(served, MODEL)
    service.faults["malformed_body"] = True
    before_journal = len(daemon.journal_lines())
    try:
        session_id = _session(f"malformed-body-{uuid.uuid4()}")
        status, answer = _chat(
            daemon, "Write a short reminder asking my roommate to take out the recycling.",
            session_id=session_id, model=f"usepod-byok:{MODEL}", stream=True,
        )
        events = daemon.events(session_id)
        _keep("malformed_provider_body_stream.json", {"status": status, "answer": answer, "events": events})
        new_journal = daemon.journal_lines()[before_journal:]
        assert [line["call"] for line in new_journal] == ["reserve", "mark_dispatched", "retain_unknown"], new_journal
        receipts = [r for r in _receipts(events) if r.get("provider") == "usepod"]
        if receipts:
            assert receipts[-1]["settlement"]["recording"] == "retained_unknown"
    finally:
        service.faults.pop("malformed_body", None)


def test_a_mid_stream_cancellation_stops_the_turn_and_settles_the_completed_call(served) -> None:
    """Cancellation under the ACTUAL mode: the provider call runs buffered to completion before
    the client stream starts, so a mid-stream cancel lands after the call answered. The turn ends
    cancelled for the client and the completed call settles -- nothing is retained and nothing is
    re-dispatched."""
    daemon, service = served.daemon, served.service
    _pin_and_approve(served, MODEL)
    service.faults["delay_seconds"] = 6
    before_journal = len(daemon.journal_lines())
    session_id = _session(f"cancel-{uuid.uuid4()}")
    turn_id = "turn-" + uuid.uuid4().hex
    result: dict = {}

    def run() -> None:
        result["r"] = _chat(
            daemon, "Write a short welcome message for a new member of a book club.",
            session_id=session_id, model=f"usepod-byok:{MODEL}", stream=True, turn_id=turn_id,
        )

    def answer_requests() -> int:
        return len(service.requests_to(INFERENCE_PATHS["openai"])) + len(service.requests_to(INFERENCE_PATHS["anthropic"]))

    requests_before = answer_requests()
    worker = threading.Thread(target=run)
    worker.start()
    # Cancel only once the provider holds the (delayed) answer request: a cancel that lands before
    # dispatch correctly sends nothing, which is a different case from the one this test names.
    deadline = time.monotonic() + 120.0
    while answer_requests() == requests_before and worker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert answer_requests() > requests_before, "the pinned answer request never reached the provider"
    status, cancel = daemon.call("POST", "/api/chat/cancel", {"session_id": session_id, "turn_id": turn_id}, timeout=30.0)
    worker.join(timeout=120.0)
    service.faults.pop("delay_seconds", None)
    events = daemon.events(session_id)
    _keep("cancelled_stream.json", {"cancel": [status, cancel], "result": result.get("r"), "events": events})
    assert status == 200 and cancel.get("state") == "cancelled", cancel
    streamed = result["r"][1] if isinstance(result.get("r"), tuple) else result.get("r")
    assert "Cancelled" in _answer_text(streamed)
    # The one provider call had answered by the time the cancel landed: it settles exactly once.
    new_journal = daemon.journal_lines()[before_journal:]
    assert [line["call"] for line in new_journal] == ["reserve", "mark_dispatched", "settle"], new_journal


# --- availability without a monetary double or a wallet network ---------------------------------------------------


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    """A second daemon WITHOUT the monetary test double: the money law and the wallet payment
    authority are the production ones, and Crypto is off, so the wallet pays on no network.
    These refusals must name exactly that."""
    root = tmp_path_factory.mktemp("usepod-tool-plain")
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    daemon = PlainServedDaemon(root / "home")
    try:
        daemon.start()
        status, saved = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1", "base_url": service.origin})
        assert status == 200, saved
        daemon.call("POST", "/api/cloud/usepod/refresh", {})
        daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": MODEL, "provider": "usepod", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
        assert status == 200, approved
        yield SimpleNamespace(service=service, daemon=daemon, token=token)
    finally:
        daemon.stop()
        service.stop()


def test_without_a_spend_grant_a_prepaid_turn_refuses_before_any_dispatch(plain) -> None:
    """The production monetary authority IS the money law now; with no operator-minted spend
    grant, the refusal is the law's own code -- still before any byte is sent."""
    daemon, service = plain.daemon, plain.service
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert view["authorities"]["monetary"]["installed"] is True, view["authorities"]
    assert view["authorities"]["monetary"]["label"] == "effect_budget_money:v1"
    assert view["authorities"]["monetary"]["grants"] == [], "no grant exists in this home"
    session_id = _session(f"no-money-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a short thank-you note to a neighbor who watered my plants.", session_id=session_id, model=f"usepod-byok:{MODEL}")
    events = daemon.events(session_id)
    _keep("plain_prepaid_refusal.json", {"status": status, "answer": answer, "events": events})
    blob = json.dumps(events)
    assert "MONEY_AUTHORITY_INVALID" in blob, blob[:400]
    assert service.requests_to(INFERENCE_PATHS["openai"]) == [] and service.requests_to(INFERENCE_PATHS["anthropic"]) == []
    # The refusal the person reads names the pinned model failing, not a silent substitute.
    assert "meridian-synth-chat" in _answer_text(answer)


def test_without_a_verified_wallet_network_x402_refuses_before_any_reservation(plain) -> None:
    daemon, service = plain.daemon, plain.service
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    payment = view["authorities"]["payment"]
    # installed at boot under its own label; with Crypto off it verifies no network
    assert (payment["installed"], payment["label"], payment["verified_networks"]) == (True, "core.wallet.usepod_x402:v1", []), payment
    status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "x402"})
    assert status == 200, lane
    try:
        session_id = _session(f"no-wallet-{uuid.uuid4()}")
        status, answer = _chat(daemon, "Write a short note congratulating a colleague on finishing a marathon.", session_id=session_id, model=f"usepod-byok:{MODEL}")
        events = daemon.events(session_id)
        _keep("plain_x402_refusal.json", {"status": status, "answer": answer, "events": events})
        blob = json.dumps(events)
        assert "wallet_payment_network_unverified" in blob
        # The refusal bites at the PICK, before the lane is attempted at all: no quote request,
        # no prepaid traffic, no payment of any kind. (The transport-level refusals with
        # dispatch=not_sent stay proven in test_usepod_transport_x402.py.)
        assert len(service.requests_to("/proxy/x402/v1/chat/completions")) == 0
        assert service.requests_to(INFERENCE_PATHS["openai"]) == []
    finally:
        daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "prepaid_token"})
