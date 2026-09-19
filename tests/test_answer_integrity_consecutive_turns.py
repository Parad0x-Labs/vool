"""Answer integrity across consecutive chat turns -- the 2026-09-15/16 incident, repaired.

Three runtime-owned defects, one incident:

1. ANSWER REPLACEMENT. `_enforce_wallet_secret_safety` matched educational security-review prose
   (a description of what a malicious plugin COULD ask the user to paste; a risk clause about
   funds being moved without consent) and replaced the entire answer with a stale capability
   paragraph (".null", "Windows Hello", "USDC x402 spend lane is disabled in this build").
2. CONSECUTIVE-TURN IDENTITY. A turn that ended in a runtime NO-ANSWER notice left that notice in
   the next prompt's history still holding the failed request open, so the next, unrelated
   question was answered AS the failed task ("Thank you for the detailed task description ...
   1. Identify the 7 most important security or abuse risks ...").
3. OUTPUT BUDGET. A plain chat turn on a pinned cloud lane carried the prompt layer's chat table
   (240-520 tokens, sized for small local models) to the wire, and the answer came back cut
   mid-sentence; a multi-part turn then dropped its usable partial entirely.

Unit cases prove the predicates and the lane budget; the served case at the bottom drives the
logged turn sequence through the real HTTP door with a scripted provider.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from core.agent_runtime.action_honesty_validator import (
    _claims_false_payment,
    _solicits_wallet_secret,
    enforce_final_action_honesty,
)
from core.context_history_authority import (
    FAILED_EXCHANGE_NOTICE,
    assistant_text_is_runtime_failure_notice,
    failed_exchange_marker,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

WALLET_QUESTION = (
    "You are reviewing a proposed feature for a local AI agent runtime.\n\n"
    "Feature:\nUsers can save a wallet address inside a contact card and later say:\n"
    "\"Send $25 to Tom.\"\n\n"
    "System properties:\n- Skills/plugins can access contacts.\n- Agents can call tools.\n"
    "- Wallet payments exist.\n- Contacts are stored locally.\n"
    "- Users may install third-party skills/plugins.\n"
    "- The runtime must protect users even if a plugin is malicious.\n"
    "- We want the smallest practical design that is safe enough for beta.\n\n"
    "Task:\n\n"
    "1. Identify the 7 most important security or abuse risks introduced by adding wallet "
    "addresses to contacts.\n2. Rank them by severity and explain why.\n"
    "3. Propose the minimum architecture needed to mitigate them.\n"
    "4. Clearly separate protections that must be enforced in the runtime/storage layer from "
    "protections that can safely live in UI/UX.\n5. Identify any design that looks safe at first "
    "but is actually bypassable.\n6. Do not invent unnecessary infrastructure.\n\n"
    "Be concise but technically specific."
)

DOG_QUESTION = (
    "I'm adopting a rescue dog next week and I have a few questions. What supplies do I need for "
    "the first week? Also, how should I introduce him to my cat, and what training should I start "
    "with? Finally, any advice on helping him settle in at night?"
)

WOW_QUESTION = (
    "I want to build a small community bot system with a World of Warcraft theme.\n\n"
    "The Telegram bot is used by members. Users can earn points from activity, choose Alliance "
    "or Horde, check balances, transfer points, and view faction rankings.\n\n"
    "I also want a private Discord server for admins where trusted staff can control the Telegram "
    "bot remotely.\n\n"
    "How would you design this system?\n\n"
    "Explain the architecture, how Discord talks to the Telegram bot, how permissions should work, "
    "how you would store the shared state, and what you would do to avoid duplicate commands.\n\n"
    "Keep it practical."
)

# The answer claude-3-haiku actually returned on the WoW turn in the incident: an answer to the
# PRECEDING wallet-security question (its opening is quoted verbatim by the turn's
# task_completed event in the failure log).
EDUCATIONAL_WALLET_REVIEW = (
    "Thank you for the detailed task description. I will provide a thoughtful and thorough "
    "response while adhering to the principles and constraints you have outlined.\n\n"
    "1. Identify the 7 most important security or abuse risks:\n\n"
    "Risk 1 - Seed phrase exfiltration. A malicious plugin could instruct the user to paste their "
    "seed phrase into chat, then read and exfiltrate it.\n\n"
    "Risk 2 - Unauthorized payment execution. When the user says 'Send $25 to Tom', a compromised "
    "plugin could trigger the payment with no preview, so funds are moved without informed "
    "consent.\n\n"
    "Risk 3 - Contact poisoning. A plugin rewrites Tom's saved wallet address so the approved "
    "payment goes to the attacker.\n\n"
    "Risk 4 - Quote manipulation. The user approves $25 but $250 is transferred.\n\n"
    "Risk 5 - Persistence bypass. Direct storage writes change a protected wallet address with no "
    "PIN and no audit receipt.\n\n"
    "Risk 6 - Rpc endpoint abuse. The spend lane is redirected to an attacker-controlled "
    "endpoint.\n\n"
    "Risk 7 - Receipt forgery. A fake audit receipt claims a protected change was authorized.\n"
)

INCIDENT_FAILURE_NOTICE = (
    "`claude-3-haiku` hit its output limit before it completed the answer. No cached or "
    "remembered text was substituted. Retry the turn."
)

GENUINE_SOLICITATION = (
    "To complete the x402 payment, enter your seed phrase and private key into the dialog below."
)

GENUINE_FALSE_PAYMENT = (
    "Done! I sent the payment to Tom -- the $25 transaction was submitted and is complete."
)


# ---------------------------------------------------------------- 1. answer replacement


def test_educational_security_review_is_not_a_solicitation() -> None:
    assert not _solicits_wallet_secret(EDUCATIONAL_WALLET_REVIEW)
    assert not _claims_false_payment(EDUCATIONAL_WALLET_REVIEW)


@pytest.mark.parametrize(
    "text",
    [
        # Design prose: how the proposed system behaves.
        "The agent resolves the wallet address and the payment is sent after the user approves it.",
        "Funds are moved without informed consent when there is no preview step.",
        "Once the transaction is settled you get a receipt.",
        # Warnings and self-custody advice.
        "If a site tells you to enter your recovery phrase to verify ownership, it is phishing.",
        "Back up your seed phrase offline and never share it with anyone.",
        "VOOL never asks for your private key or seed phrase, and it cannot move money on its own.",
        # Hypothetical attacker behaviour with the READER'S secret.
        "A malicious plugin could ask you to enter your seed phrase into a chat field.",
        "Anyone who asks you to paste your private key into chat is attacking you.",
    ],
)
def test_benign_wallet_prose_passes_untouched(text: str) -> None:
    out = enforce_final_action_honesty({"response": text}, user_input="Review this design.")
    assert out["response"] == text


@pytest.mark.parametrize(
    "text",
    [
        GENUINE_SOLICITATION,
        "Could you paste your seed phrase below so I can restore the wallet?",
        "Please share your private key with me to verify ownership.",
    ],
)
def test_genuine_secret_solicitation_is_still_blocked(text: str) -> None:
    out = enforce_final_action_honesty({"response": text}, user_input="Help me pay.")
    verdict = out.get("action_honesty_validator") or {}
    assert verdict.get("reason") == "wallet_secret_safety"
    assert verdict.get("trigger") == "wallet_secret_solicitation"
    assert "never asks for" in out["response"].lower()


@pytest.mark.parametrize(
    "text",
    [
        GENUINE_FALSE_PAYMENT,
        "The funds have been sent to the destination address.",
        "I processed the transaction and it cleared on chain.",
    ],
)
def test_genuine_false_payment_claims_are_still_blocked(text: str) -> None:
    out = enforce_final_action_honesty({"response": text}, user_input="Send $25 to Tom.")
    verdict = out.get("action_honesty_validator") or {}
    assert verdict.get("reason") == "wallet_secret_safety"
    assert verdict.get("trigger") == "false_payment_claim"
    lowered = out["response"].lower()
    assert "no funds were sent" in lowered and "no spend receipt" in lowered


def test_the_correction_carries_runtime_facts_not_stale_capabilities(monkeypatch) -> None:
    from core.agent_runtime import action_honesty_validator as validator

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "0")
    out = enforce_final_action_honesty(
        {"response": GENUINE_SOLICITATION}, user_input="x"
    )
    text = out["response"]
    # The obsolete capability assertions are gone.
    for stale in (".null", "Windows Hello", "spend lane is disabled", "x402 itself is the"):
        assert stale not in text, stale
    # The wallet's own config decided the capability clause.
    assert "disabled in this session" in text

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    out = enforce_final_action_honesty(
        {"response": GENUINE_SOLICITATION}, user_input="x"
    )
    assert "previewed" in out["response"] and "explicit approval" in out["response"]


def test_a_receipt_backed_payment_claim_is_not_replaced(monkeypatch) -> None:
    """A turn whose execution record shows a wallet lane ran keeps its completion claim.

    The claim may be backed by that lane's receipt; judging tool-backed claims belongs to the
    evidence gates, not to this safety replacement."""
    import core.agent_runtime.action_honesty_validator as validator

    source_context = {
        "tool_receipts": [
            {"tool_name": "wallet.payment_status", "status": "tool_executed", "execution": {"executed": True}}
        ]
    }
    out = validator._enforce_wallet_secret_safety(
        {"response": GENUINE_FALSE_PAYMENT},
        session_id="s",
        source_context=source_context,
    )
    assert out["response"] == GENUINE_FALSE_PAYMENT


# ---------------------------------------------------------------- 2. consecutive-turn identity


@pytest.mark.parametrize(
    "notice",
    [
        INCIDENT_FAILURE_NOTICE,
        "I couldn't get a usable model response in this run, so I'm not going to recycle cached or "
        "remembered text as if it were fresh.",
        "I couldn't get a live model response in this run, so I'm not going to recycle cached or "
        "remembered text as if it were fresh.",
        "The answer came back cut off -- the last sentence ends mid-clause -- so there is nothing "
        "complete to show you. Ask again and I'll run it fresh.",
        "`stub:2b` was refused by its provider with HTTP 429 (rate limit). Wait and retry, or "
        "select another model. No cached or remembered text was substituted. Retry the turn.",
    ],
)
def test_runtime_no_answer_notices_are_recognized(notice: str) -> None:
    assert assistant_text_is_runtime_failure_notice(notice)


@pytest.mark.parametrize(
    "answer",
    [
        EDUCATIONAL_WALLET_REVIEW,
        # A REAL partial answer keeps its own bytes; only the suffix is the notice.
        "Central Service: one backend owns all state.\n\n(Incomplete: this answer stopped before "
        "it finished. Ask again for the rest.)",
        "I did not create those files and I did not run any tests.",
        "On wallet safety: VOOL never asks for your private key or seed phrase.",
    ],
)
def test_real_answers_are_not_failure_notices(answer: str) -> None:
    assert not assistant_text_is_runtime_failure_notice(answer)


def test_client_history_closes_failed_exchanges(tmp_path, monkeypatch) -> None:
    from core.persistent_memory import augment_history_from_session_log
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(tmp_path)
    try:
        history = [
            {"role": "user", "content": "yo"},
            {"role": "assistant", "content": "Hey there."},
            {"role": "user", "content": WALLET_QUESTION},
            {"role": "assistant", "content": INCIDENT_FAILURE_NOTICE},
        ]
        out = augment_history_from_session_log(
            history, session_id="chat-closed", user_text=WOW_QUESTION
        )
        contents = [item["content"] for item in out]
        assert INCIDENT_FAILURE_NOTICE not in contents
        assert contents.count(FAILED_EXCHANGE_NOTICE) == 1
        # The pair survives: the request is still there, closed by the marker.
        wallet_index = contents.index(WALLET_QUESTION)
        assert out[wallet_index + 1]["content"] == FAILED_EXCHANGE_NOTICE
        assert out[0]["content"] == "yo" and out[1]["content"] == "Hey there."
    finally:
        configure_runtime_home(None)


def test_the_failed_marker_is_not_answer_content() -> None:
    marker = failed_exchange_marker()
    assert marker["role"] == "assistant"
    assert not _solicits_wallet_secret(marker["content"])
    assert not _claims_false_payment(marker["content"])
    from core.agent_runtime.action_honesty_validator import completion_claim_kind

    assert completion_claim_kind(marker["content"]) == ""


# ---------------------------------------------------------------- 3. output budget


def _manifest(metadata: dict[str, Any], base_url: str = "https://remote.example/v1"):
    from storage.model_provider_manifest import ModelProviderManifest

    return ModelProviderManifest(
        provider_name="remote-lane",
        model_name="lane-model",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Proprietary",
        license_reference="https://example.com",
        weight_location="external",
        runtime_dependency="http",
        capabilities=["summarize"],
        runtime_config={"base_url": base_url, "timeout_seconds": 30},
        metadata=metadata,
    )


def _budget_request(max_output_tokens: int, output_mode: str = "plain_text"):
    from types import SimpleNamespace

    return SimpleNamespace(
        max_output_tokens=max_output_tokens,
        output_mode=output_mode,
        tools=None,
        metadata={},
        temperature=0.5,
        system_prompt="",
        prompt="",
        messages=[],
        context={},
    )


def test_a_cost_classed_chat_lane_gets_the_lane_policy_budget() -> None:
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    adapter = OpenAICompatibleAdapter(
        _manifest({"cost_class": "paid_cloud", "context_window": 128000, "max_output_tokens": 8192})
    )
    # The incident's shape: the prompt layer's chat table (a few hundred tokens) on a paid lane.
    assert adapter._cloud_tool_output_budget(_budget_request(484), 484) == 760
    # A verified-free cloud lane carries the same policy's larger target. Only the CAPABILITY
    # resolution is pinned (that lane's verdict comes from the pricing catalog, which no unit
    # fixture can honestly stand in for); the budget resolution under test stays real.
    from unittest.mock import patch

    import core.output_budget_policy as budget_policy

    free_capability = budget_policy.LaneCapability(
        context_window=64000, max_output_tokens=8192, cost_class="free_cloud"
    )
    with patch.object(budget_policy, "manifest_lane_capability", return_value=free_capability):
        free = OpenAICompatibleAdapter(_manifest({"context_window": 64000, "max_output_tokens": 8192}))
        assert free._cloud_tool_output_budget(_budget_request(484), 484) == 1800


def test_an_unclassified_or_local_lane_keeps_the_asked_budget() -> None:
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    unknown = OpenAICompatibleAdapter(_manifest({}))
    assert unknown._cloud_tool_output_budget(_budget_request(484), 484) == 484
    # Physical caps still bind: a 1024-completion lane cannot carry the paid target.
    capped = OpenAICompatibleAdapter(
        _manifest({"cost_class": "paid_cloud", "context_window": 128000, "max_output_tokens": 600})
    )
    assert capped._cloud_tool_output_budget(_budget_request(484), 484) == 600


# ---------------------------------------------------------------- 4. review reason


def test_the_execution_history_carries_the_reviewers_actual_reason() -> None:
    from core.runtime_execution_history import build_runtime_execution_history

    history = build_runtime_execution_history(
        session={"session_id": "s1", "status": "completed", "request_preview": "design q"},
        checkpoint={},
        events=[
            {
                "event_type": "model_lane_verifier_blocked",
                "message": "Verifier was required, but no verifier lane was available.",
                "details": {"verifier_status": "blocked"},
            }
        ],
        receipts=[],
    )
    bounded = history["bounded_execution"]
    assert bounded["model_review_state"] == "blocked"
    assert (
        bounded["model_review_reason"]
        == "Verifier was required, but no verifier lane was available."
    )
    review_row = next(row for row in history["timeline"] if row["key"] == "model_review")
    assert "no verifier lane was available" in review_row["detail"]


# ---------------------------------------------------------------- served: the logged sequence

try:  # the served rig imports the daemon stack; keep collection light when absent
    from tests._blackbox_served_rig import SEED_MANIFEST, ServedDaemon, free_port, run_in_home

    _SERVED_AVAILABLE = True
except Exception:  # pragma: no cover
    _SERVED_AVAILABLE = False


class _RecordingProvider:
    """Ollama-dialect stub that records every request body and answers the certification probe."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.wallet_calls = 0
        self._lock = threading.Lock()
        self.port = free_port()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                return self._send({"ok": True})

            def do_POST(self) -> None:
                import re

                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                with rig._lock:
                    rig.calls.append(body)
                messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
                system = " ".join(
                    str(m.get("content") or "") for m in messages if m.get("role") == "system"
                )
                joined = " ".join(str(m.get("content") or "") for m in messages)
                options = body.get("options") or {}
                ceiling = int(body.get("max_tokens") or options.get("num_predict") or 0)
                user_rows = [
                    str(m.get("content") or "")
                    for m in messages
                    if m.get("role") == "user"
                ]
                current = next(
                    (row for row in reversed(user_rows) if not row.startswith("The previous answer")),
                    "",
                )
                tool_names = [
                    str(((t or {}).get("function") or {}).get("name") or "")
                    for t in (body.get("tools") or [])
                    if isinstance(t, dict)
                ]
                has_tool_result = any(m.get("role") == "tool" for m in messages)
                finish = "stop"
                if "vool_probe_add" in tool_names:
                    message = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "p1", "type": "function", "function": {"name": "vool_probe_add", "arguments": {"left": 19, "right": 23}}},
                            {"id": "p2", "type": "function", "function": {"name": "vool_probe_lookup_nonce", "arguments": {"key": "alpha"}}},
                        ],
                    }
                    return self._send({"model": "stub", "done": True, "done_reason": "stop", "message": message})
                if "vool_probe_echo" in tool_names:
                    token = "recovered" if "recovered" in joined else "repair-me"
                    message = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "pe", "type": "function", "function": {"name": "vool_probe_echo", "arguments": {"token": token}}}],
                    }
                    return self._send({"model": "stub", "done": True, "done_reason": "stop", "message": message})
                if not tool_names and has_tool_result:
                    nonces = sorted(set(re.findall(r"\b[0-9a-f]{12}\b", joined)))
                    reply = f"The sum is 42 and the sealed nonce is {nonces[0]}." if nonces else "The sum is 42."
                    return self._send(
                        {"model": "stub", "done": True, "done_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}
                    )
                if system.startswith("You split a user's message"):
                    user_text = " ".join(
                        str(m.get("content") or "") for m in messages if m.get("role") == "user"
                    )
                    reply = json.dumps([{"request": user_text, "depends_on": []}])
                elif DOG_QUESTION[:60] in current:
                    # The plain-chat truncation case: always cut hard at the ceiling.
                    reply = (
                        "Supplies for the first week: a crate, a flat collar with an ID tag, "
                        "poop bags, and the same food the shelter used -- switch foods slowly "
                        "over a week. Introducing him to the cat: keep them fully separated at "
                        "first and let them smell each other through"
                    )[: max(1, (ceiling or 120) * 3)]
                    finish = "length"
                elif WALLET_QUESTION[:80] in current:
                    # First model call answers in full; later ones truncate at the ceiling.
                    with rig._lock:
                        rig.wallet_calls += 1
                        call_index = rig.wallet_calls
                    if call_index == 1:
                        reply = EDUCATIONAL_WALLET_REVIEW
                    else:
                        reply = EDUCATIONAL_WALLET_REVIEW[: max(1, (ceiling or 120) * 3)]
                        finish = "length"
                elif "Agent Trace Viewer" in joined:
                    # A single-file app sized past the old chat ceiling but inside the
                    # authoring room: real HTML/CSS/JS bytes for the build lane to publish.
                    app = ["<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\">",
                           "<title>Agent Trace Viewer</title></head><body>"]
                    for i in range(60):
                        app.append(f"<style>.cls-{i} {{ color: #{i:06x}; background: #{i*37 % 0xffffff:06x}; "
                                   f"padding: {i}px; border-radius: {i % 12}px; }}</style>")
                    for i in range(60):
                        app.append(f"<div class=\"cls-{i}\" data-event=\"{i}\">Event {i} row with name, duration and status.</div>")
                    for i in range(50):
                        app.append(f"function render{i}() {{ bind('.cls-{i}', {i}); }}")
                    app.append("</body></html>")
                    reply = "\n".join(app)
                elif "USD allocation to each asset" in current or "cost per 1 million total tokens" in current:
                    # A compute turn: answer in the table shape its request demands.
                    reply = (
                        "| Asset | Amount |\n| --- | --- |\n| EUR | $5,520 |\n| Gold | $4,600 |\n"
                        "| Silver | $3,680 |\n| Cash | $4,600 |\n| Total | $18,400 |\n\n"
                        "The four allocations sum to exactly $18,400."
                    )
                elif WOW_QUESTION[:80] in current:
                    reply = (
                        "Central Service: one backend owns all state (points, factions, audit "
                        "log) behind an API. Telegram and Discord are thin clients. Permissions: "
                        "server-side role checks with an append-only audit log. Idempotency: every "
                        "command carries a command id so a replay cannot execute twice."
                    )
                else:
                    reply = "Sure -- what would you like to know?"
                self._send(
                    {
                        "model": str(body.get("model") or "stub"),
                        "done": True,
                        "done_reason": finish,
                        "message": {"role": "assistant", "content": reply},
                        "prompt_eval_count": 300,
                        "eval_count": max(1, ceiling or 40),
                    }
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _RecordingProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.mark.served
@pytest.mark.skipif(not _SERVED_AVAILABLE, reason="served rig unavailable")
def test_the_logged_consecutive_turn_sequence_through_the_served_door(tmp_path) -> None:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    def _answer(frame: dict[str, Any]) -> str:
        message = frame.get("message")
        text = str((message or {}).get("content") or "") if isinstance(message, dict) else ""
        if not text.strip():
            commit = frame.get("vool_response_commit")
            if isinstance(commit, dict):
                text = str(commit.get("canonical_content") or "")
        return text

    home = tmp_path / "home"
    with _RecordingProvider() as provider:
        seed = SEED_MANIFEST.format(
            root=REPO_ROOT, base_url=provider.base_url, registered=["stub-chat:2b"]
        )
        run_in_home(home, seed)
        with ServedDaemon(home) as daemon:
            request = Request(
                f"{daemon.base_url}/api/model-tool-certification/run",
                data=json.dumps(
                    {"provider_name": "ollama-local", "model_name": "stub-chat:2b", "timeout_seconds": 60}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=180) as response:
                assert json.loads(response.read().decode("utf-8")).get("state") == "verified"

            session = "integrity-session"

            # T1 -- greeting: the smalltalk fast path, NO model.
            provider.calls.clear()
            f1 = daemon.chat_stream("yo", session_id=session, model="stub-chat:2b")
            assert _answer(f1).strip()
            assert provider.calls == []

            # T2 -- the wallet-security question, answered with the incident's educational review.
            f2 = daemon.chat_stream(WALLET_QUESTION, session_id=session, model="stub-chat:2b")
            answer2 = _answer(f2)
            assert "Risk 1" in answer2, answer2[:200]
            assert "never asks for" not in answer2.lower()

            # T3 -- the same question again; the provider truncates at the ceiling both times.
            # This is the incident's turn 3: a failed turn whose published reply is the runtime's
            # own no-answer notice.
            f3 = daemon.chat_stream(WALLET_QUESTION, session_id=session, model="stub-chat:2b")
            answer3 = _answer(f3)
            assert "output limit" in answer3, answer3[:300]
            assert "Risk 1" not in answer3

            # T3b -- a plain conversational multi-part question that truncates: the partial is
            # usable, so it ships with its honest incomplete notice instead of being dropped.
            f3b = daemon.chat_stream(DOG_QUESTION, session_id=session, model="stub-chat:2b")
            answer3b = _answer(f3b)
            assert "Supplies for the first week" in answer3b, answer3b[:200]
            assert "Incomplete" in answer3b, answer3b[:400]

            # T4 -- the unrelated design question. The provider request must close the failed
            # exchange instead of carrying its notice, and the current question must be the live
            # user turn.
            before = len(provider.calls)
            f4 = daemon.chat_stream(WOW_QUESTION, session_id=session, model="stub-chat:2b")
            answer4 = _answer(f4)
            model_calls = [
                call
                for call in provider.calls[before:]
                if any(
                    str(m.get("role")) == "user" and WOW_QUESTION[:60] in str(m.get("content") or "")
                    for m in (call.get("messages") or [])
                )
                and not str(
                    next((m.get("content") for m in reversed(call.get("messages") or []) if m.get("role") == "system"), "")
                ).startswith("You split")
            ]
            assert model_calls, "the model lane never received the current question"
            chat_call = model_calls[-1]
            joined = " ".join(str(m.get("content") or "") for m in chat_call.get("messages") or [])
            assert "No cached or remembered text was substituted" not in joined
            assert FAILED_EXCHANGE_NOTICE in joined
            # The truncated-but-real partial stays history (its own bytes, not a marker).
            assert "Supplies for the first week" in joined
            user_rows = [
                str(m.get("content") or "")
                for m in (chat_call.get("messages") or [])
                if str(m.get("role")) == "user"
            ]
            assert any(row.startswith(WOW_QUESTION[:60]) for row in user_rows)
            # The incident's identity failure: the trailing ask was the PREVIOUS turn's question.
            # (A trailing bounded-repair instruction about the CURRENT answer is legitimate.)
            assert not user_rows[-1].startswith(WALLET_QUESTION[:60])
            assert "Central Service" in answer4, answer4[:200]

            # Fresh-chat control: no wallet content anywhere in the request.
            before = len(provider.calls)
            f5 = daemon.chat_stream(WOW_QUESTION, session_id="integrity-fresh", model="stub-chat:2b")
            answer5 = _answer(f5)
            fresh_calls = provider.calls[before:]
            assert fresh_calls
            assert all(
                "wallet address inside a contact card" not in " ".join(
                    str(m.get("content") or "") for m in (call.get("messages") or [])
                )
                for call in fresh_calls
            )
            assert "Central Service" in answer5, answer5[:200]

            # The reviewer's actual blocked reason is in the durable events for these turns.
            canonical = run_in_home(
                home,
                f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
                "from storage.dialogue_memory import recent_dialogue_turns_any\n"
                "rows = [r for r in recent_dialogue_turns_any(limit=40, speaker_roles=('user',)) "
                "if 'proposed feature for a local AI agent runtime' in str(r.get('raw_input'))]\n"
                "print(rows[-1]['session_id'] if rows else '')",
            ).strip()
            assert canonical.startswith("openclaw:"), canonical
            url = f"{daemon.base_url}/api/runtime/events?{urlencode({'session': canonical, 'limit': 200})}"
            with urlopen(url, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            messages = [str(e.get("message") or "") for e in payload.get("events") or []]
            assert any("no verifier lane was available" in m for m in messages)
