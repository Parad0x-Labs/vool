"""Pay-per-answer AI (core.x402_ai): the owner's endpoint list, the model check, and every refusal that must happen
before anything is sent or parked. The paid path itself is proven end to end in test_paid_ai_answers_served.py."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = [pytest.mark.safety]


@pytest.fixture
def ai(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core import x402_ai
    from core.blackbox import store as store_module

    monkeypatch.setattr(x402_ai, "_path", lambda: tmp_path / "x402_ai.json")
    store_module.reset_default_store()
    yield x402_ai
    store_module.reset_default_store()


class _Service:
    """A loopback chat-completions service: answers 402 with no offer VOOL pays, or 200 free, and records requests."""

    def __init__(self, *, status: int, headers: dict[str, str] | None = None, body: bytes = b"{}") -> None:
        self.requests: list[dict] = []
        rig = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                rig.requests.append({"path": self.path, "body": self.rfile.read(length)})
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.mark.parametrize(("asked", "reported", "verdict"), [
    ("open-reasoner-70b", "open-reasoner-70b", "match"),
    ("open-reasoner-70b", "Open-Reasoner-70B", "match"),
    ("open-reasoner-70b", "acme/open-reasoner-70b", "match"),
    ("acme/open-reasoner-70b", "open-reasoner-70b", "match"),
    ("open-reasoner-70b", "open-reasoner-70b-2026-09-30", "match"),
    ("open-reasoner-70b", "open-reasoner-7b", "mismatch"),
    ("open-reasoner-70b", "open-reasoner-70b-mini", "mismatch"),
    ("open-reasoner-70b", "tiny-cheap-8b", "mismatch"),
    ("open-reasoner-70b", "", "unnamed"),
    ("open-reasoner-70b", None, "unnamed"),
    ("open-reasoner-70b", 70, "unnamed"),
])
def test_the_model_check_accepts_only_spellings_of_the_paid_model(ai, asked, reported, verdict):
    assert ai.model_verdict(asked, reported) == verdict


@pytest.mark.parametrize(("fields", "code"), [
    ({"base_url": "http://example.com/v1"}, "endpoint_url_invalid"),
    ({"base_url": "https://user:pw@example.com/v1"}, "endpoint_url_invalid"),
    ({"base_url": "https://example.com/v1?k=1"}, "endpoint_url_invalid"),
    ({"model": "two words"}, "endpoint_model_invalid"),
    ({"max_price": "0"}, "endpoint_price_invalid"),
    ({"max_price": "-1"}, "endpoint_price_invalid"),
    ({"max_price": "0.0000001"}, "endpoint_price_invalid"),
    ({"label": ""}, "endpoint_label_invalid"),
])
def test_a_bad_endpoint_is_refused_and_nothing_is_saved(ai, fields, code):
    good = {"label": "Reasoner", "base_url": "https://example.com/v1", "model": "open-reasoner-70b", "max_price": "0.05"}
    with pytest.raises(ai.PaidAskError) as refused:
        ai.add_endpoint(**{**good, **fields})
    assert refused.value.code == code
    assert ai.endpoints() == []


def test_price_is_kept_exactly_in_usdc_minor_units_and_names_are_unique(ai):
    added = ai.add_endpoint(label="Reasoner", base_url="https://example.com/v1/", model="open-reasoner-70b", max_price="0.05")
    assert (added["max_price_minor"], added["base_url"], added["on"]) == (50_000, "https://example.com/v1", True)
    with pytest.raises(ai.PaidAskError) as refused:
        ai.add_endpoint(label="reasoner", base_url="https://example.org/v1", model="m", max_price="1")
    assert refused.value.code == "endpoint_label_taken"


def test_asking_with_no_endpoint_or_crypto_off_sends_nothing(ai, monkeypatch):
    with pytest.raises(ai.PaidAskError) as refused:
        ai.ask("", "hello")
    assert refused.value.code == "no_endpoint"
    service = _Service(status=200)
    try:
        ai.add_endpoint(label="Reasoner", base_url=service.base_url, model="open-reasoner-70b", max_price="0.05")
        monkeypatch.setenv("VOOL_WALLET_ENABLED", "0")
        from core.wallet.errors import WalletFault

        with pytest.raises(WalletFault) as off:
            ai.ask("", "hello")
        assert off.value.code == "wallet_disabled" and service.requests == []
    finally:
        service.close()


def test_two_endpoints_need_a_name_and_a_paused_one_sends_nothing(ai):
    service = _Service(status=200)
    try:
        first = ai.add_endpoint(label="Reasoner", base_url=service.base_url, model="open-reasoner-70b", max_price="0.05")
        ai.add_endpoint(label="Coder", base_url=service.base_url, model="open-coder-34b", max_price="0.05")
        with pytest.raises(ai.PaidAskError) as ambiguous:
            ai.ask("", "hello")
        assert ambiguous.value.code == "endpoint_ambiguous" and "Reasoner" in ambiguous.value.message
        ai.set_endpoint_on(first["id"], False)
        with pytest.raises(ai.PaidAskError) as paused:
            ai.ask("Reasoner", "hello")
        assert paused.value.code == "endpoint_paused" and service.requests == []
    finally:
        service.close()


def test_a_payment_offer_vool_cannot_pay_is_refused_with_nothing_parked(ai):
    # a 402 in a shape the Base USDC lane does not read (and no ready Pilot wallet on Base): refused, nothing parked
    service = _Service(status=402, headers={"Content-Type": "application/json"}, body=json.dumps({"x402Version": 1, "accepts": []}).encode())
    try:
        ai.add_endpoint(label="Reasoner", base_url=service.base_url, model="open-reasoner-70b", max_price="0.05")
        with pytest.raises(ai.PaidAskError) as refused:
            ai.ask("Reasoner", "what is seven plus five?")
        assert refused.value.code == "offer_not_payable" and "Nothing was paid" in refused.value.message
        assert len(service.requests) == 1
        sent = json.loads(service.requests[0]["body"])
        assert sent["messages"] == [{"role": "user", "content": "what is seven plus five?"}], "only the question leaves"
        from core.wallet import proposals

        assert proposals.list_proposals() == []
    finally:
        service.close()


def test_a_free_answer_is_checked_and_a_wrong_model_pauses_the_endpoint(ai):
    body = json.dumps({"model": "tiny-cheap-8b", "choices": [{"message": {"role": "assistant", "content": "Twelve."}}]}).encode()
    service = _Service(status=200, headers={"Content-Type": "application/json"}, body=body)
    try:
        ai.add_endpoint(label="Reasoner", base_url=service.base_url, model="open-reasoner-70b", max_price="0.05")
        out = ai.ask("Reasoner", "what is seven plus five?")
        assert out["status"] == "answered_free" and out["verdict"] == "mismatch" and "Twelve." in out["text"]
        assert "Not the model you asked for" in out["text"] and "No payment was needed" in out["text"]
        listed = ai.endpoints()[0]
        assert listed["on"] is False and "tiny-cheap-8b" in listed["paused_reason"]
    finally:
        service.close()


def test_an_error_from_the_service_is_reported_and_nothing_is_paid(ai):
    service = _Service(status=500)
    try:
        ai.add_endpoint(label="Reasoner", base_url=service.base_url, model="open-reasoner-70b", max_price="0.05")
        with pytest.raises(ai.PaidAskError) as refused:
            ai.ask("Reasoner", "hello")
        assert refused.value.code == "endpoint_error" and "HTTP 500" in refused.value.message
    finally:
        service.close()


def test_the_chat_offers_the_tool_only_when_the_owner_asks_a_paid_model(ai):
    from core.tool_demand_signals import resolve_demand_signals

    assert "ai.paid_ask" in resolve_demand_signals("ask my paid model: what is seven plus five?").explicit_intents
    assert "ai.paid_ask" in resolve_demand_signals("ask Reasoner via x402 what the weather word means").explicit_intents
    assert "ai.paid_ask" not in resolve_demand_signals("what is a paid model?").explicit_intents
    assert "ai.paid_ask" not in resolve_demand_signals("fetch the x402 resource at https://example.com/paid").explicit_intents
