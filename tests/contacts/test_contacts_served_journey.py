"""PA Contacts, served: one daemon, the real chat door and the real Contacts, email draft and wallet doors.

SIMULATED MODEL, SIMULATED CHAIN, real product. The daemon is ``apps.vool_api_server`` in its own process, home and port,
with SHIPPED routing (``VOOL_ALWAYS_ON_CATALOG`` is removed, so each request offers only the tools the runtime chose). The
model is ScriptedContactsModel behind the production certification door: every tool call it makes is chosen only from what
that request carried (the user's words, any attachment text the runtime delivered, the offered tool names and the same-turn
tool observations). The chain is the loopback Solana-dialect node answering as Devnet. The served home's email policy is
switched on in its own config, as an owner does; no mailbox is configured, so nothing can be sent.

Proved: contacts saved from the user's words; two saved Alexes turn "email Alex" into a question that saves nothing; "email
Alex Chen" drafts to the exact saved address and shows who it is; "prepare 0.1 SOL to Alex Chen" becomes a pending proposal to
the saved Devnet address with the contact bound; a changed saved address refuses that approval before any credential or send;
an address read from an attached invoice stays a suggestion; a Telegram lookup shows the identity and says nothing can send to
it; a Unicode name resolves; everything survives a daemon restart; no lookup signs or sends. The browser part opens the served
page: Home -> Contacts shows the waiting suggestion, and the approval sheet names the contact.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
from tests import _reader_served_rig as rig
from tests import served_browser
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc

MODEL = "contacts-journey:scripted"
PROVIDER_NAME = "contacts-journey"
PIN = "482913"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
OBSERVATION_HEADER = "Real tool observations from this same turn follow."
PLANNER_WIRE_FORMAT = 'Wire format: return ONLY {"requests": [...]}'
FACT_EXTRACTION_REQUEST = "Extract stable persistent facts"
ENTITY_AMBIGUITY_JUDGE = AMBIGUITY_SYSTEM_PROMPT.splitlines()[0]
_BASE58 = r"[1-9A-HJ-NP-Za-km-z]{32,44}"

Action = tuple[str, dict[str, Any]]


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


# --- the scripted model --------------------------------------------------------------------------------------------

def _observations(prompt: str) -> list[dict[str, Any]]:
    if OBSERVATION_HEADER not in prompt:
        return []
    found: list[dict[str, Any]] = []
    for line in prompt.split(OBSERVATION_HEADER, 1)[1].splitlines():
        line = line.strip()
        if line.startswith("- {"):
            with contextlib.suppress(ValueError):
                found.append(json.loads(line[2:]))
    return found


@dataclass(frozen=True)
class ModelView:
    """Everything ONE model request carried: the only input a scripted choice may use."""

    prompt: str
    offered: frozenset[str]
    observed: tuple[dict[str, Any], ...]

    def results(self, intent: str) -> list[dict[str, Any]]:
        return [item for item in self.observed if item.get("intent") == intent]


def call(intent: str, **arguments: Any) -> Action:
    return intent.replace(".", "__"), arguments


def say(message: str) -> Action:
    return "respond__direct", {"message": message}


def _narration(item: dict[str, Any]) -> str:
    return str(item.get("response_preview") or item.get("response_text") or json.dumps({k: item.get(k) for k in ("intent", "ok", "status")}))


def plan_calls(*steps: tuple[str, Any]) -> Callable[[ModelView], Action]:
    """Make each call once, in order, after the previous one was observed; then report the last observation.

    A step's arguments may be a function of the view (a later call that uses an earlier observation, or text the runtime
    delivered). A tool the runtime did not offer is asked for through capability.expand_family, as a model would."""

    def plan(view: ModelView) -> Action:
        for intent, arguments in steps:
            if view.results(intent):
                continue
            args = arguments(view) if callable(arguments) else dict(arguments)
            if args is None:
                return say(f"SCRIPT: nothing in this request gives the arguments for {intent}.")
            wire = intent.replace(".", "__")
            if wire not in view.offered and "capability__expand_family" in view.offered and not view.results("capability.expand_family"):
                return call("capability.expand_family", family=intent.split(".", 1)[0])
            return call(intent, **args)
        return say(_narration(view.results(steps[-1][0])[-1]))

    return plan


class ScriptedContactsModel:
    def __init__(self) -> None:
        self.plan: Callable[[ModelView], Action] | None = None
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def answer(self, path: str, body: dict[str, Any]) -> dict[str, Any] | str:
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        prompt = rig._all_text(messages)
        offered = frozenset(rig._tool_names(body))
        view = ModelView(prompt=prompt, offered=offered, observed=tuple(_observations(prompt)))
        chosen: Action | None = None
        if PLANNER_WIRE_FORMAT in prompt:
            kind = "planner"
            last_user = next((rig._all_text([m]) for m in reversed(messages) if m.get("role") == "user"), "")
            reply: dict[str, Any] | str = json.dumps({"requests": [{"request": last_user, "operation": "contacts_conversation", "depends_on": []}]})
        elif FACT_EXTRACTION_REQUEST in prompt:
            kind, reply = "fact_extraction", json.dumps({"facts": [{"action": "NOOP"}]})
        elif ENTITY_AMBIGUITY_JUDGE in prompt:
            kind, reply = "entity_ambiguity", json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        elif self.plan is None:
            kind, reply = "unplanned", "SCRIPT: no plan is active for this request."
        else:
            chosen = self.plan(view)
            name, arguments = chosen
            if offered:
                kind = "tool_round"
                reply = (f"SCRIPT: the runtime did not offer {name}." if name not in offered else {
                    "role": "assistant", "content": "", "tool_calls": [{
                        "id": f"call_{uuid.uuid4().hex[:10]}", "type": "function", "function": {"name": name, "arguments": arguments}}]})
            else:
                kind = "plain_text"
                reply = str(arguments.get("message") or "") if name == "respond__direct" else f"SCRIPT: no tools were offered and this step needed {name}."
        with self._lock:
            self.requests.append({"path": path, "kind": kind, "offered": sorted(offered), "observed": list(view.observed),
                                  "choice": list(chosen) if chosen else None, "reply": reply if isinstance(reply, str) else "tool_call",
                                  "prompt_tail": prompt[-1500:]})
        return reply


def _install(provider: rig.CapturingProvider, model: ScriptedContactsModel) -> None:
    handler = provider._server.RequestHandlerClass
    original_get = handler.do_GET

    def do_GET(self) -> None:
        if self.path.startswith("/api/ps"):
            # the scripted model occupies no memory: reported resident, so no model load is planned
            return self._send({"models": [{"name": MODEL, "size": 0, "size_vram": 0}]})
        return original_get(self)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        probe = rig._probe_reply(body)
        reply = probe if probe is not None else model.answer(self.path, body)
        name = body.get("model")
        if isinstance(reply, dict):
            message = {"role": "assistant", "content": reply.get("content", ""), "tool_calls": reply.get("tool_calls") or []}
            if self.path.startswith("/v1/"):
                return self._send({"model": name, "choices": [{"index": 0, "finish_reason": "tool_calls" if message["tool_calls"] else "stop", "message": message}]})
            return self._send({"model": name, "done": True, "done_reason": "stop", "message": message})
        if self.path.startswith("/v1/"):
            return self._send({"model": name, "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": reply}}],
                               "usage": {"prompt_tokens": 40, "completion_tokens": 30}})
        return self._send({"model": name, "done": True, "done_reason": "stop", "message": {"role": "assistant", "content": reply},
                           "prompt_eval_count": 40, "eval_count": 30})

    handler.do_GET = do_GET
    handler.do_POST = do_POST


# --- the served journey --------------------------------------------------------------------------------------------

class Journey:
    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.chain = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
        self.model = ScriptedContactsModel()
        self.provider = rig.CapturingProvider(default="SCRIPT: default reply")
        _install(self.provider, self.model)
        self.session = rig.canonical_session(f"contacts-journey-{uuid.uuid4().hex}")
        self.turns: list[dict[str, Any]] = []
        self.daemon: rig.ServedDaemon | None = None
        self.certification: dict[str, Any] = {}

    def open(self) -> Journey:
        self.chain.__enter__()
        self.provider.__enter__()
        daemon = rig.ServedDaemon(self.home, provider=self.provider, model=MODEL, provider_name=PROVIDER_NAME, env_extra={
            "VOOL_MODEL_LOAD_FLOOR_GB": "0", "VOOL_CREDENTIAL_STORE": "vault", "VOOL_ALLOW_LIVE_OLLAMA_TESTS": "0",
            "VOOL_RAW_OLLAMA_API_URL": self.provider.base_url,
            "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "VOOL_WALLET_TESTNET_RPC_URL": self.chain.url,
            "VOOL_WALLET_SKIP_CONSENT_GATE": "yes",
        })
        base_env = daemon.env

        def shipped_routing_env() -> dict[str, str]:
            env = base_env()
            env.pop("VOOL_ALWAYS_ON_CATALOG", None)  # shipped default: off (core/runtime_flags.py)
            return env

        daemon.env = shipped_routing_env  # type: ignore[method-assign]
        config = self.home / "config"
        config.mkdir(parents=True, exist_ok=True)
        policy = (rig.REPO_ROOT / "config" / "default_policy.yaml").read_text(encoding="utf-8")
        (config / "default_policy.yaml").write_text(policy.rstrip("\n") + "\nemail:\n  read_enabled: true\n  send_enabled: true\n", encoding="utf-8")
        self.daemon = daemon
        daemon.start(timeout=240)
        self.certification = daemon.certify(timeout=240)
        return self

    def restart(self) -> None:
        assert self.daemon is not None
        self.daemon.stop()
        self.daemon.start(timeout=240)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self.daemon is not None:
                self.daemon.stop()
        with contextlib.suppress(Exception):
            self.provider.__exit__(None, None, None)
        with contextlib.suppress(Exception):
            self.chain.__exit__(None, None, None)

    # doors
    def get(self, path: str) -> dict[str, Any]:
        assert self.daemon is not None
        with urlopen(self.daemon.base_url + path, timeout=60) as response:
            return json.load(response)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        assert self.daemon is not None
        request = Request(self.daemon.base_url + path, data=json.dumps(body).encode(), method="POST",
                          headers={"Origin": self.daemon.base_url, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=90) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def turn(self, name: str, text: str, plan: Callable[[ModelView], Action], *, attachments: list[str] | None = None) -> dict[str, Any]:
        assert self.daemon is not None
        self.model.plan = plan
        mark = len(self.model.requests)
        try:
            reply = self.daemon.chat(text, session_id=self.session, attachments=attachments, timeout=300)
        finally:
            self.model.plan = None
        message = reply.get("message") if isinstance(reply.get("message"), dict) else {}
        record = {"name": name, "text": text, "reply": str(message.get("content") or reply.get("response") or ""),
                  "requests": self.model.requests[mark:]}
        record["observed"] = {}
        for request in record["requests"]:
            for item in request["observed"]:
                record["observed"].setdefault(str(item.get("intent")), item)
        self.turns.append(record)
        return record

    def contact(self, name: str) -> dict[str, Any]:
        listed = [c for c in self.get("/api/contacts?q=" + quote(name))["contacts"] if c["display_name"] == name]
        assert len(listed) == 1, (name, listed)
        return self.get("/api/contacts/detail?id=" + quote(listed[0]["contact_id"]))["contact"]

    def pending(self) -> dict[str, dict[str, Any]]:
        return {row["proposal_id"]: row for row in self.get("/api/wallet/status")["status"]["pending"]}

    def record(self) -> None:
        path = os.environ.get("CONTACTS_SERVED_RECORD")
        if path:
            Path(path).write_text(json.dumps({"certification": self.certification, "turns": self.turns}, indent=1, ensure_ascii=False, default=str),
                                  encoding="utf-8")


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    opened = Journey(tmp_path_factory.mktemp("contacts-served"))
    try:
        try:
            opened.open()
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        yield opened
    finally:
        opened.record()
        opened.close()


def _ready_pilot_wallet(journey: Journey) -> str:
    status, created = journey.post("/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
                                                                "creation_key": f"contacts-{uuid.uuid4().hex}", "label": "Contacts journey"})
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = journey.post("/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    ack_token = (revealed.get("backup") or {}).get("ack_token")
    assert status == 200 and ack_token, (status, sorted(revealed.get("backup") or {}))  # key names only, never the value
    revealed = None
    status, ready = journey.post("/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    # the owner's own cap for this pilot: 0.1 SOL plus fees is above the shipped per-transfer default
    status, limits = journey.post("/api/wallet/limits", {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": 1_000_000_000,
                                                         "daily_minor": 5_000_000_000, "per_destination_daily_minor": 2_500_000_000})
    assert status == 200, limits
    return wallet_id


def test_saved_contacts_drive_drafts_and_proposals_through_the_served_product(journey: Journey) -> None:
    sol_alex, sol_owner_new, sol_invoice, sol_zoe = _sol_key(), _sol_key(), _sol_key(), _sol_key()
    assert journey.certification.get("state") in {"verified", "certified"} or journey.certification.get("ok"), journey.certification
    _ready_pilot_wallet(journey)

    # -- save from the user's own words ------------------------------------------------------------------------------
    saved = journey.turn("save-alex-chen", f"Save Alex Chen as a contact: work email alex.chen@example.test, Solana devnet wallet {sol_alex}, Telegram alexchen_kiln.",
                         plan_calls(("contacts.save", {"name": "Alex Chen", "endpoints": [
                             {"kind": "email", "value": "alex.chen@example.test", "label": "work"},
                             {"kind": "wallet", "value": sol_alex, "network": "solana-devnet"},
                             {"kind": "messaging", "value": "alexchen_kiln", "channel": "telegram"}]})))
    alex = journey.contact("Alex Chen")
    assert {(e["kind"], e["value"]) for e in alex["endpoints"]} == {("email", "alex.chen@example.test"), ("wallet", sol_alex), ("messaging", "@alexchen_kiln")}, saved  # a Telegram username is kept in its @ form
    assert journey.get("/api/contacts/suggestions")["suggestions"] == []
    journey.turn("save-alex-rivera", "Save Alex Rivera as a contact with email alex.rivera@example.test.",
                 plan_calls(("contacts.save", {"name": "Alex Rivera", "email": "alex.rivera@example.test"})))
    assert journey.contact("Alex Rivera")["endpoints"][0]["value"] == "alex.rivera@example.test"

    # -- ambiguity asks and saves nothing ------------------------------------------------------------------------------
    # (this base has no email-draft lane; the ambiguity door is proven on the wallet proposal, the consumer it owns)
    before_ambiguous = set(journey.pending())
    asked = journey.turn("pay-alex-ambiguous", "Prepare 0.1 SOL to Alex on Solana devnet.",
                         plan_calls(("wallet.propose", {"destination": "Alex", "amount": "0.1", "asset": "SOL", "chain": "solana"})))
    assert set(journey.pending()) == before_ambiguous, "an ambiguous name proposes nothing"
    assert "Alex Chen" in asked["reply"] and "Alex Rivera" in asked["reply"] and "Nothing was proposed" in asked["reply"], asked

    # -- a transfer proposal to the saved Devnet address; nothing signed or sent ---------------------------------------
    before = set(journey.pending())
    proposed = journey.turn("prepare-sol-alex", "Prepare 0.1 SOL to Alex Chen on Solana devnet for the glaze order.",
                            plan_calls(("wallet.propose", {"destination": "Alex Chen", "amount": "0.1", "asset": "SOL", "chain": "solana", "memo": "glaze order"})))
    [proposal_id] = set(journey.pending()) - before
    row = journey.pending()[proposal_id]
    assert row["recipient"]["saved"] is True and row["recipient"]["display_name"] == "Alex Chen", row
    assert sol_alex in proposed["reply"] and "Alex Chen" in proposed["reply"], proposed
    assert journey.chain.send_count() == 0
    status, minted = journey.post("/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200, minted
    quote_fields = minted["quote"]["fields"]
    assert quote_fields["to_address"] == sol_alex and "Alex Chen" in str(quote_fields.get("recipient_label")), quote_fields

    # -- the owner protects contacts, then changes the saved address behind the PIN: the reviewed approval is refused ----
    status, enrolled = journey.post("/api/contacts/credential/enroll", {"kind": "pin", "secret": PIN})
    assert status == 200 and enrolled["status"] == "enrolled" and len(enrolled["recovery_code"]) >= 20, enrolled
    wallet_endpoint = next(e for e in alex["endpoints"] if e["kind"] == "wallet")
    status, proposed = journey.post("/api/contacts/update", {"contact_id": alex["contact_id"], "expected_revision": alex["revision"],
                                                            "change_endpoints": [{"endpoint_id": wallet_endpoint["endpoint_id"], "value": sol_owner_new, "network": "solana-devnet"}]})
    assert status == 200 and proposed["status"] == "pending_authentication" and proposed["applied"] is False, proposed
    assert [e["value"] for e in journey.contact("Alex Chen")["endpoints"] if e["kind"] == "wallet"] == [sol_alex], "the change waits for the PIN"
    operation = proposed["operation"]
    status, wrong = journey.post("/api/contacts/operations/confirm", {"operation_id": operation["operation_id"], "digest": operation["digest"], "secret": "000000"})
    assert status == 403 and [e["value"] for e in journey.contact("Alex Chen")["endpoints"] if e["kind"] == "wallet"] == [sol_alex], (status, wrong)
    status, changed = journey.post("/api/contacts/operations/confirm", {"operation_id": operation["operation_id"], "digest": operation["digest"], "secret": PIN})
    assert status == 200 and changed["status"] == "committed", changed
    alex = journey.contact("Alex Chen")
    assert [e["value"] for e in alex["endpoints"] if e["kind"] == "wallet"] == [sol_owner_new], "the PIN applied the change once"
    status, refused = journey.post("/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": minted["quote"]["quote_id"],
                                                           "quote_digest": minted["quote"]["digest"], "pin": PIN})
    assert status >= 400 and refused.get("error") == "wallet_recipient_refused", (status, refused)
    assert journey.chain.send_count() == 0 and journey.get("/api/wallet/transfers")["transfers"] == []

    # -- an address the user did not type never overwrites the saved one ------------------------------------------------
    # (this base's attachment lane routes a document turn to a tools-less path, so the untrusted value is served
    # through the model's own update call: the address below appears in no user message of this session)
    def invoice_change(view: ModelView) -> dict[str, Any] | None:
        destination = next((item.get("destination") or {} for item in view.results("contacts.resolve")), {})
        if destination.get("endpoint_id"):
            return {"target": "Alex Chen", "change_endpoints": [{"endpoint_id": destination["endpoint_id"], "value": sol_invoice}]}
        return {"target": "Alex Chen", "wallet_address": sol_invoice, "network": "solana-devnet"}

    replaced = journey.turn("invoice-replacement", "Update Alex Chen's Solana devnet wallet with the new one.",
                            plan_calls(("contacts.resolve", {"name": "Alex Chen", "kind": "wallet", "network": "solana-devnet"}), ("contacts.update", invoice_change)))
    assert any(request.get("choice") and sol_invoice in json.dumps(request["choice"]) for request in replaced["requests"]), \
        "the model asked to write an address the user never typed"
    alex_now = journey.contact("Alex Chen")
    assert [e["value"] for e in alex_now["endpoints"] if e["kind"] == "wallet"] == [sol_owner_new], "the saved address was not overwritten"
    waiting = [s for s in journey.get("/api/contacts/suggestions")["suggestions"] if s["value"] == sol_invoice]
    assert len(waiting) == 1 and waiting[0]["origin"] == "chat_unconfirmed", journey.get("/api/contacts/suggestions")
    if waiting[0].get("replaces"):
        assert waiting[0]["replaces"]["value"] == sol_owner_new

    # -- a messaging identity is shown with no delivery claimed --------------------------------------------------------
    looked = journey.turn("telegram-lookup", "What is Alex Chen's Telegram handle?",
                          plan_calls(("contacts.resolve", {"name": "Alex Chen", "kind": "messaging", "channel": "telegram"})))
    assert "alexchen_kiln" in looked["reply"] and "no Telegram transport" in looked["reply"], looked

    # -- a Unicode name, saved and paid to by its first name -----------------------------------------------------------
    journey.turn("save-zoe", f"Save Zoë Ng as a contact with Solana devnet wallet {sol_zoe}.",
                 plan_calls(("contacts.save", {"name": "Zoë Ng", "wallet_address": sol_zoe, "network": "solana-devnet"})))
    before = set(journey.pending())
    zoe_turn = journey.turn("prepare-sol-zoe", "Prepare 0.05 SOL to Zoë on Solana devnet.",
                            plan_calls(("wallet.propose", {"destination": "Zoë", "amount": "0.05", "asset": "SOL", "chain": "solana"})))
    [zoe_proposal] = set(journey.pending()) - before
    assert journey.pending()[zoe_proposal]["recipient"]["display_name"] == "Zoë Ng" and sol_zoe in zoe_turn["reply"], zoe_turn
    assert journey.chain.send_count() == 0

    # -- a daemon restart keeps contacts, the waiting suggestion and the bound proposals ---------------------------------
    journey.restart()
    assert sorted(c["display_name"] for c in journey.get("/api/contacts")["contacts"]) == ["Alex Chen", "Alex Rivera", "Zoë Ng"]
    assert [s["value"] for s in journey.get("/api/contacts/suggestions")["suggestions"]] == [sol_invoice]
    pending = journey.pending()
    assert pending[zoe_proposal]["recipient"]["display_name"] == "Zoë Ng" and proposal_id in pending
    after_restart = journey.turn("telegram-lookup-after-restart", "What is Alex Chen's Telegram handle?",
                                 plan_calls(("contacts.resolve", {"name": "Alex Chen", "kind": "messaging", "channel": "telegram"})))
    assert "alexchen_kiln" in after_restart["reply"], after_restart

    # -- the served page: the waiting suggestion in Contacts, the contact on the approval sheet --------------------------
    manager, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(journey.daemon.base_url) else route.abort())
        page.goto(journey.daemon.base_url + "/chat", wait_until="domcontentloaded")
        page.wait_for_selector("#input", timeout=20_000)
        page.locator("#homeToggle").click()
        page.locator("#contactsBtn").click()
        suggestion = page.locator("#vcSuggestions .vc-suggestion", has_text=sol_invoice)
        suggestion.wait_for(timeout=20_000)
        assert "Replace the saved wallet address" in suggestion.inner_text() or "Add wallet address" in suggestion.inner_text()
        page.locator("#vcList .vc-row", has_text="Zoë Ng").click()
        page.locator("#vcDetail .vc-endpoint", has_text=sol_zoe).wait_for(timeout=10_000)
        page.locator("#vcOverlay .modal-x").click()

        review = page.locator(f'.vw-card[data-proposal="{zoe_proposal}"] .vw-review')
        review.wait_for(timeout=30_000)
        review.click()
        page.wait_for_function("(document.getElementById('vwSheetTitle') || {}).textContent && document.getElementById('vwSheetTitle').textContent.indexOf('Send ') === 0")
        sheet = page.locator(f'[role="dialog"][data-proposal="{zoe_proposal}"]')
        assert "Zoë Ng" in sheet.inner_text()
        page.locator("#vwSheetDetailsToggle").click()
        assert sol_zoe in sheet.inner_text()
        assert [e for e in errors if "vc" in e or "Contacts" in e] == [], errors
    finally:
        with contextlib.suppress(Exception):
            browser.close()
        manager.stop()

    assert journey.chain.send_count() == 0 and journey.get("/api/wallet/transfers")["transfers"] == [], "no lookup or proposal signed or sent"
