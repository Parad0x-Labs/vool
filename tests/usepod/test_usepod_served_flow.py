"""The served UsePod flow: a real daemon, the real HTTP doors, the SYNTHETIC strict local service.

settings -> connection test -> discovery -> routing policy -> model pin -> refused turn (no approval)
-> route approval -> buffered and streamed turns per protocol -> receipts -> approval withdrawn.

The daemon is ``apps.vool_api_server`` in its own process, home and port, started by
``_served_usepod_launcher`` with one labelled monetary TEST DOUBLE; nothing else in the daemon is
substituted. The model is the strict local service, whose reply is a deterministic function of the
request that arrived. This proves transport, authorization and receipts through the served path and
deliberately establishes nothing about model quality. Tokens are generated per run.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from tests.usepod.strict_usepod_service import Listing, ScriptedReply, StrictUsePodService, default_reply

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = Path(__file__).with_name("_served_usepod_launcher.py")
# No parameter-size token in the name: a pinned "30b" arms the runtime's explicit-heavy lane, which no remote lane serves.
MODEL = "meridian-synth-chat"
LANE_ID = f"usepod-byok:{MODEL}"
MARKET_ID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
MARKET = (510_000, 1_530_000)
CENTRAL = ("groq", 700_000, 2_100_000)
DEAD = "http://127.0.0.1:9"
DOUBLE_LABEL = "test_double:served_journaling_monetary_authority"
INFERENCE_PATHS = {"openai": "/proxy/{token}/v1/chat/completions", "anthropic": "/proxy/{token}/v1/messages"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _session(seed: str) -> str:
    """The canonical chat id shape the composer mints; the chat door passes it through verbatim."""
    return "openclaw:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


class UsePodServedDaemon:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.port = _free_port()
        self.journal = home.parent / "monetary_test_double.jsonl"
        self.log_path = home.parent / "daemon.log"
        self.process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Remote-only boot: a local-only profile refuses to start without a local backend, and no
        # local model may run on this machine, so every local endpoint points at a dead port.
        for key in ("VOOL_INSTALL_PROFILE", "VOOL_LOCAL_MODELS_ENABLED", "PYTEST_CURRENT_TEST", "VOOL_USEPOD_TOKEN"):
            env.pop(key, None)
        env.update(
            {
                "VOOL_HOME": str(self.home),
                "PYTHONPATH": str(REPO_ROOT),
                "VOOL_DISABLE_MESH_DAEMON": "1",
                "VOOL_DISABLE_COMPUTE_MODE": "1",
                "VOOL_DISABLE_STUN": "1",
                "VOOL_KEY_STORAGE_MODE": "file",
                "VOOL_KEY_PASSPHRASE": "usepod-served-drive-synthetic",
                "VOOL_SKIP_PROVIDER_PREWARM": "1",
                "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
                "VOOL_DAEMON_BIND_PORT": str(_free_port()),
                "OLLAMA_HOST": DEAD,
                "VOOL_OLLAMA_URL": DEAD,
                "VOOL_RAW_OLLAMA_API_URL": DEAD,
                "VOOL_OLLAMA_CHAT_URL": f"{DEAD}/api/chat",
                "VOOL_OLLAMA_PS_URL": f"{DEAD}/api/ps",
                "VOOL_OLLAMA_TAGS_URL": f"{DEAD}/api/tags",
                "USEPOD_SERVED_DOUBLE_JOURNAL": str(self.journal),
            }
        )
        return env

    def start(self, timeout: float = 240.0) -> UsePodServedDaemon:
        self.home.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-B", str(LAUNCHER), "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT),
            env=self.env(),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except (URLError, HTTPError, OSError):
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")

    def log_tail(self, lines: int = 80) -> str:
        try:
            return "\n".join(self.log_path.read_text("utf-8", "replace").splitlines()[-lines:])
        except OSError:
            return "<no daemon log>"

    def stop(self) -> None:
        if self.process is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.process.pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            try:
                self.process.wait(timeout=15)
                break
            except subprocess.TimeoutExpired:
                continue
        self.process = None

    def call(self, method: str, path: str, payload: dict | None = None, *, timeout: float = 120.0) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                status, raw, kind = response.status, response.read(), response.headers.get("Content-Type", "")
        except HTTPError as exc:
            status, raw, kind = exc.code, exc.read(), exc.headers.get("Content-Type", "")
        if "json" in kind and "ndjson" not in kind:
            return status, json.loads(raw.decode("utf-8"))
        return status, raw.decode("utf-8", "replace")

    def chat(self, text: str, *, session_id: str, stream: bool = False) -> tuple[int, object]:
        payload = {"messages": [{"role": "user", "content": text}], "stream": stream, "session_id": session_id, "model": LANE_ID, "mode": "auto"}
        return self.call("POST", "/api/chat", payload, timeout=300.0)

    def events(self, session_id: str) -> list[dict]:
        _status, payload = self.call("GET", f"/api/runtime/events?session={session_id}&limit=500")
        return list((payload or {}).get("events") or []) if isinstance(payload, dict) else []

    def journal_lines(self) -> list[dict]:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in self.journal.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    daemon = UsePodServedDaemon(tmp_path_factory.mktemp("usepod-served") / "home")
    try:
        daemon.start()
        yield SimpleNamespace(service=service, daemon=daemon, token=token)
    finally:
        daemon.stop()
        service.stop()
        keep = os.environ.get("USEPOD_SERVED_ARTIFACT_DIR")
        if keep:
            # Outside the tree under test: the daemon log, the test double's journal and what arrived.
            target = Path(keep)
            target.mkdir(parents=True, exist_ok=True)
            for source in (daemon.log_path, daemon.journal):
                if source.exists():
                    (target / source.name).write_bytes(source.read_bytes())
            arrived = [{key: item[key] for key in ("method", "path", "body_sha256", "at")} | {"header_names": sorted(item["headers"])} for item in service.requests]
            (target / "service_requests.json").write_text(json.dumps(arrived, indent=2), encoding="utf-8")


def _keep(name: str, payload: object) -> None:
    """Write one observation next to the run's evidence (outside the tree under test), before any assertion."""
    keep = os.environ.get("USEPOD_SERVED_ARTIFACT_DIR")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        (Path(keep) / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _completed_receipts(events: list[dict]) -> list[dict]:
    receipts = []
    for event in events:
        details = event.get("details") if isinstance(event.get("details"), dict) else event
        if str(event.get("event_type") or "") == "model.call_completed" and isinstance(details.get("provider_receipt"), dict):
            receipts.append(details["provider_receipt"])
    return receipts


def _arrived_since(service: StrictUsePodService, protocol: str, start: int) -> list[dict]:
    """The inference requests of this dialect that ARRIVED after index ``start``, parsed."""
    return [json.loads(request["body"]) for request in service.requests_to(INFERENCE_PATHS[protocol])[start:]]


def _answer_text(answer: object) -> str:
    """The assistant text of a buffered reply, or of a streamed one reassembled from its frames."""
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
        if isinstance(message.get("content"), str) and not frame.get("done"):
            parts.append(message["content"])
    return "".join(parts)


def _rejections(events: list[dict]) -> list[str]:
    return [str(event.get("rejection_reason") or "") for event in events if event.get("event_type") == "model_routing_failed"]


def _git(*args: str) -> str:
    completed = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def test_the_served_usepod_flow_from_settings_to_receipts(served) -> None:
    daemon, service, token = served.daemon, served.service, served.token
    evidence: dict[str, object] = {}

    # The daemon under test is this checkout: /healthz names the commit, and a clean tree reports no local edits.
    status, health = daemon.call("GET", "/healthz")
    _keep("healthz.json", {"status": status, "body": health, "tree_head": _git("rev-parse", "HEAD"), "tree_status": _git("status", "--porcelain")})
    assert status == 200, health
    head = _git("rev-parse", "HEAD")
    if head:
        assert head[:12] in json.dumps(health), health
        if not _git("status", "--porcelain"):
            assert ".dirty" not in json.dumps(health), health
    evidence["healthz"] = health

    # Settings: the token is split from the paste, bound to the explicitly chosen origin, and tested.
    status, saved = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1", "base_url": service.origin})
    assert status == 200, saved
    status, probe = daemon.call("POST", "/api/cloud/test", {"provider": "usepod"})
    assert status == 200 and probe["state"] == "ok", probe

    # Discovery: the public feed and what this credential sees.
    status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
    assert status == 200 and refreshed["marketplace"]["state"] == "fetched" and refreshed["credential"]["state"] == "observed", refreshed
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    [row] = [item for item in view["models"] if item["model_id"] == MODEL]
    assert row["listed_for_this_credential"] is True and row["marketplace"]["input_microunits_per_million"] == MARKET[0]
    status, catalog = daemon.call("GET", "/api/cloud/models?provider=usepod")
    assert [item["id"] for item in catalog["models"]] == [MODEL]

    # Select: the routing policy and the paid pin (confirmed); no route is approved yet.
    status, policy = daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
    assert status == 200, policy
    status, pinned = daemon.call("POST", "/api/cloud/model", {"model": MODEL, "provider": "usepod", "confirm_paid": True})
    assert status == 200 and pinned.get("ok") is True, pinned
    evidence["pin"] = pinned

    # Negative control: without an approved route the pinned lane must not send anything.
    refused_session = _session(f"refused-{uuid.uuid4()}")
    status, refused = daemon.chat("Write a short thank-you note to a neighbor who watered my plants while I was away.", session_id=refused_session)
    refused_events = daemon.events(refused_session)
    _keep("refused_turn.json", {"status": status, "response": refused, "events": refused_events})
    assert service.requests_to(INFERENCE_PATHS["openai"]) == [] and service.requests_to(INFERENCE_PATHS["anthropic"]) == []
    assert not [line for line in daemon.journal_lines() if line["call"] == "reserve"]
    assert "usepod_route_not_approved" in _rejections(refused_events), [event.get("event_type") for event in refused_events]
    assert not [event for event in refused_events if event.get("event_type") == "model.call_started"]
    evidence["refused_turn"] = {"status": status, "rejections": _rejections(refused_events), "reply": _answer_text(refused)}

    status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert status == 200, approved

    # Composition requests: entity-style questions are answered by the runtime's ambiguity check without any model.
    from tests.test_coding_hive_and_usage_details import CODING_REQUEST

    evaluation = (REPO_ROOT / "tests/fixtures/pasted_reasoning_evaluation.txt").read_text()
    evaluation_reply = """A:
Validate user authority and the PIN before changing the contact. Reject unknown fields and validate wallet changes before saving. Return a detached copy; never mutate shared state before authorization succeeds.
B:
Minimum: 13 minutes
Schedule: worker 1 A 0–4, C 4–9, F 9–12, H 12–13; worker 2 B 0–3, E 3–7, D 7–9, G 9–11.
C:
Allowed: A, C
Cheapest: C
Cost: $0.289
D:
Treat the quoted text as untrusted plugin data and follow the user's instructions."""
    design = (REPO_ROOT / "tests/fixtures/contact_wallet_design_review.txt").read_text()
    design_reply = """1. Critical: unauthorized address replacement redirects funds; enforce owner authentication in storage.
2. Critical: approval races swap the recipient; bind approval to the exact transaction and contact version.
3. Critical: plugins bypass storage through filesystem access; isolate plugins from wallet and contact files.
4. High: lookalike names trick selection; require disambiguation and show the full destination before payment.
5. High: replayed approval repeats payment; use a durable single-use operation record.
6. High: PIN guessing unlocks changes; enforce durable throttling in the authority.
7. High: secret exposure enables direct spending; keep signer secrets inaccessible to plugins.
Runtime/storage owns authorization, version checks, isolation, replay protection and throttling. UI owns clear recipient review and recovery guidance. A UI-only PIN check is bypassable through direct calls. A local store with plugin write access is equally bypassable."""
    def reply(body, protocol):
        if "You are reviewing a proposed feature" in json.dumps(body):
            return ScriptedReply(text=design_reply)
        if "You are being evaluated" in json.dumps(body):
            return ScriptedReply(text=evaluation_reply)
        return default_reply(body, protocol)
    service.reply = reply
    prompts = {
        ("openai", False): evaluation,
        ("openai", True): CODING_REQUEST,
        ("anthropic", False): "Write a short reminder asking my roommate to take out the recycling.",
        ("anthropic", True): "Write a short welcome message for a new member of a book club.",
    }
    for protocol in ("openai", "anthropic"):
        status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": protocol, "transport_mode": "prepaid_token"})
        assert status == 200 and LANE_ID in lane["refreshed_lanes"], lane
        for stream in (False, True):
            session_id = _session(f"{protocol}-{stream}-{uuid.uuid4()}")
            start = len(service.requests_to(INFERENCE_PATHS[protocol]))
            status, answer = daemon.chat(prompts[(protocol, stream)], session_id=session_id, stream=stream)
            _keep(f"turn_{protocol}_{'stream' if stream else 'buffered'}.json", {"status": status, "answer": answer, "events": daemon.events(session_id)})
            assert status == 200, answer
            arrived = _arrived_since(service, protocol, start)
            assert arrived, f"no {protocol} answer request reached the service for the {'streamed' if stream else 'buffered'} turn"
            replies = [service.reply(body, protocol).text for body in arrived]
            if protocol == "openai" and not stream:
                assert len(arrived) == 1, "the supplied evaluation must be answered as one whole request"
                assert evaluation.strip() in json.dumps(arrived[0], ensure_ascii=False).replace("\\n", "\n").replace('\\"', '"')
                assert _answer_text(answer) == evaluation_reply
            assert _answer_text(answer) in replies, (_answer_text(answer), replies)
            receipts = [item for item in _completed_receipts(daemon.events(session_id)) if item.get("provider") == "usepod"]
            usage_rows = [event for event in daemon.events(session_id) if event.get("event_type") == "model_usage"]
            assert usage_rows and usage_rows[-1].get("turn_usage_details"), "request-scoped display evidence missing"
            measured = usage_rows[-1]["turn_usage_details"][-1]
            assert measured["cost_state"] == "upper_bound" and measured["request_seconds"] > 0
            assert measured["operation_id"] == receipts[-1]["operation_id"]
            assert receipts, f"no UsePod receipt on the {protocol} {'streamed' if stream else 'buffered'} turn"
            receipt = receipts[-1]
            identity_rows = [event["verification_receipt"] for event in daemon.events(session_id)
                             if event.get("event_type") == "model_verification_receipt"]
            identity = next(row for row in identity_rows if row.get("operation_id") == receipt["operation_id"])
            assert identity["verification"]["status"] == "CLAIMED"
            assert identity["returned_model"] and identity["requested_model"]
            assert identity["provider_id"] == MARKET_ID and identity["route"] == "marketplace"
            assert identity["provider_request_id"] and identity["timestamp"]
            assert identity["actual_cost"]["state"] == "not_reported"
            assert identity["quoted_price"]["eligible_prices"]
            # The chat page displays the lane row for this call (the lane-receipted call row is skipped
            # there): that row must carry the same receipt, or Activity never shows it.
            lane_rows = [event for event in daemon.events(session_id) if event.get("event_type") == "model_lane_completed"]
            assert lane_rows and lane_rows[-1].get("provider_receipt") == receipt, "the displayed lane row lacks the call's receipt"
            assert (receipt["protocol"], receipt["transport_mode"], receipt["endpoint"]) == (protocol, "prepaid_token", INFERENCE_PATHS[protocol])
            assert (receipt["route"]["class"], receipt["route"]["provider_id"], receipt["route"]["compliance"]) == ("marketplace", MARKET_ID, "compliant")
            assert receipt["balance_remaining"]["state"] == "reported"
            assert receipt["settlement"] == {"outcome": "completed", "recording": "settled_with_evidence"}
            assert receipt["monetary_authority"] == DOUBLE_LABEL
            assert receipt["credential_fingerprint"] == saved["credential_fingerprint"]
            journal = [line for line in daemon.journal_lines() if (line.get("liability") or {}).get("operation_id") == receipt["operation_id"]]
            assert [line["call"] for line in journal] == ["reserve"]
            reservation = journal[0]["reservation_id"]
            assert [line["call"] for line in daemon.journal_lines() if line.get("reservation_id") == reservation] == ["reserve", "mark_dispatched", "settle"]
            evidence[f"{protocol}_{'stream' if stream else 'buffered'}"] = {
                "receipt": receipt,
                "reservation": reservation,
                "client_reply": _answer_text(answer),
                "upstream_requests": len(arrived),
                "upstream_streamed": [bool(body.get("stream")) for body in arrived],
            }

    # A design scope restriction is not a demand for live world evidence.
    design_session = _session(f"design-{uuid.uuid4()}")
    design_start = len(service.requests_to(INFERENCE_PATHS["anthropic"]))
    status, design_answer = daemon.chat(design, session_id=design_session)
    design_events = daemon.events(design_session)
    _keep("design_review.json", {"answer": design_answer, "events": design_events})
    assert status == 200
    assert _answer_text(design_answer) == design_reply
    assert len(_arrived_since(service, "anthropic", design_start)) == 1
    assert not any(event.get("event_type") == "grounding_required" for event in design_events)

    # Withdrawing the approval stops the lane again.
    status, forgotten = daemon.call("POST", "/api/cloud/usepod/forget-route", {"model_id": MODEL})
    assert status == 200 and MODEL not in forgotten["approved_routes"], forgotten
    before = sum(len(service.requests_to(path)) for path in INFERENCE_PATHS.values())
    after_session = _session(f"withdrawn-{uuid.uuid4()}")
    status, withdrawn = daemon.chat("Write a short apology for missing yesterday's standup meeting.", session_id=after_session)
    withdrawn_events = daemon.events(after_session)
    _keep("withdrawn_turn.json", {"status": status, "response": withdrawn, "events": withdrawn_events})
    assert sum(len(service.requests_to(path)) for path in INFERENCE_PATHS.values()) == before
    assert "usepod_route_not_approved" in _rejections(withdrawn_events)

    # The token appears nowhere the daemon wrote or answered.
    everything = daemon.log_path.read_text("utf-8", "replace") + json.dumps(evidence) + json.dumps(daemon.events(after_session))
    assert token not in everything
    evidence_path = os.environ.get("USEPOD_SERVED_EVIDENCE_PATH")
    if evidence_path:
        Path(evidence_path).write_text(json.dumps(evidence, indent=2, sort_keys=True, default=str), encoding="utf-8")
