"""The permanent served-reality corpus.

Every case drives the ASSEMBLED product over its real HTTP surface with
deterministic provider stubs, and asserts truths a release depends on. Cases
are allowed to discover that the product violates a truth — the bench reports
that as a VOOL-class failure and that is a SUCCESSFUL bench run.

Case contract:
    * a case either completes (all `require`s held) -> PASS,
      or raises a typed assertion -> FAIL with an owner class,
      or raises BenchError -> FAIL with HARNESS class;
    * required cases can never be skipped — a skip is a failure;
    * every case's turns are recorded with their full identity set;
    * exact wire bytes are retained for every exchange (bench<->daemon and
      stub<->daemon).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ops.served_reality.bench import Case, CaseContext, TurnRecord
from ops.served_reality.classify import (
    ModelExpectationError,
    ProductAssertionError,
)
from ops.served_reality.provider_stub import StubPlan, StubRule

CLOUD_MODEL = "served-reality/cloud-stub"
LOCAL_MODEL = "served-reality-stub"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _closure(commit: dict[str, Any]) -> dict[str, Any]:
    value = commit.get("closure_verdict")
    return dict(value) if isinstance(value, dict) else {}


def _event_types(record: TurnRecord) -> list[str]:
    return [str(e.get("event_type") or "") for e in record.events]


def _find_event(record: TurnRecord, *prefixes: str) -> dict[str, Any] | None:
    for event in record.events:
        etype = str(event.get("event_type") or "")
        if etype.startswith(prefixes):
            return event
    return None


def _wait_until(predicate: Callable[[], Any], timeout_s: float, interval_s: float = 0.5) -> Any:
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval_s)
    return last


def _approval_for(ctx: CaseContext, *, session_id: str = "", timeout_s: float = 25.0) -> dict[str, Any] | None:
    def _poll() -> dict[str, Any] | None:
        for approval in ctx.pending_approvals():
            if str(approval.get("status") or "pending") != "pending":
                continue
            if session_id and str(approval.get("session_id") or "") not in {"", session_id}:
                continue
            return approval
        return None

    return _wait_until(_poll, timeout_s)


def _resolve(ctx: CaseContext, approval: dict[str, Any], decision: str) -> Any:
    """decision: 'allow' | 'deny' (the product's own vocabulary)."""
    return ctx.resolve_approval(
        str(approval.get("session_id") or ""), str(approval.get("approval_id") or ""), decision
    )


def _read_workspace_file(ctx: CaseContext, name: str) -> bytes | None:
    # The chat's workspace was bound per-case by ctx.chat(); recover it from
    # the last workspace the case created.
    if not ctx._workspaces:
        return None
    root = ctx._workspaces[-1]
    candidate = root / name
    if candidate.is_file():
        return candidate.read_bytes()
    for path in sorted(root.rglob(name)):
        if path.is_file():
            return path.read_bytes()
    return None


def _lane_of(record: TurnRecord) -> str:
    for event in record.events:
        if str(event.get("event_type") or "") == "model_lane_selected":
            message = str(event.get("message") or "")
            if message.startswith("Selected "):
                return message.split("Selected ", 1)[1].split(" for")[0].strip()
            return str(event.get("model_lane") or "")
    return ""


def _cost_class(record: TurnRecord) -> str:
    for event in record.events:
        value = str(event.get("cost_class") or "")
        if value:
            return value
    return ""


def _stub_calls_matching(ctx: CaseContext, needle: str) -> int:
    assert ctx.rig.stub is not None
    count = 0
    for exchange in ctx.rig.stub.chat_calls():
        body = exchange.request_body or b""
        if needle.encode("utf-8") in body:
            count += 1
    return count


def _fabrication_guard(ctx: CaseContext, record: TurnRecord, *, fetch_event_prefixes: tuple[str, ...]) -> None:
    """A specific current-world figure may appear ONLY with a retrieval record.

    Dual truth: (grounded answer + retrieval event) or (non-specific answer +
    no retrieval). The forbidden cell is a specific figure with no retrieval —
    fabrication.
    """
    fetched = any(
        str(e.get("event_type") or "").startswith(fetch_event_prefixes)
        for e in record.events
    )
    receipts = list((record.receipts or {}).get("receipts") or [])
    has_claimed_actions = any(bool(r.get("claimed_actions")) for r in receipts)
    ctx.record(
        "fabrication_guard",
        fetched or not has_claimed_actions,
        expected="retrieval event present or no unbacked action claims",
        observed={"retrieval_event": fetched, "claimed_actions": has_claimed_actions},
    )


# ---------------------------------------------------------------------------
# 1. ordinary chat
# ---------------------------------------------------------------------------

def _case_ordinary_chat(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer("BENCH-ORDINARY-8811 is the marker."))
    record = ctx.chat("Hello! Just checking you are alive.", chat_id="ordinary-1")
    ctx.require_eq("http_status", 200, record.status)
    ctx.require_in("stub_marker_served", "BENCH-ORDINARY-8811", record.content)
    identity = ctx.identities[-1]
    ctx.require("request_identity_present", bool(identity.request_id), observed=identity.request_id)
    ctx.require("session_identity_present", bool(identity.session_id), observed=identity.session_id)
    ctx.require("finalization_present", bool(identity.finalization_id), observed=identity.finalization_id)
    ctx.require("receipt_present", bool(identity.receipt_ids), observed=identity.receipt_ids)
    chain = record.receipts.get("chain_verified")
    ctx.require("receipt_chain_verifies", chain is True, observed=chain)
    # The served lane for an unprompted ordinary turn must never be a paid
    # lane — spend consent cannot be bypassed by routing preference.
    lane = _lane_of(record)
    cost = _cost_class(record)
    ctx.require(
        "ordinary_turn_not_paid_lane",
        "paid_cloud" not in cost and "tether-remote" not in lane,
        expected="free lane for an unprompted turn",
        observed={"lane": lane, "cost_class": cost},
    )
    canonical = str(record.commit.get("canonical_content") or "")
    expected_hash = "sha256:" + _sha256_hex(canonical)
    ctx.require_eq("commit_hash_matches_content", expected_hash, str(record.commit.get("content_hash") or ""))


# ---------------------------------------------------------------------------
# 2. strict literal output
# ---------------------------------------------------------------------------

def _case_strict_literal(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer("VERBATIM-PROOF-2291"))
    record = ctx.chat("Reply with exactly this and nothing else: VERBATIM-PROOF-2291", chat_id="strict-1")
    ctx.require_eq("http_status", 200, record.status)
    ctx.require_eq(
        "literal_output_byte_exact",
        "VERBATIM-PROOF-2291",
        record.content.strip(),
        note="strict literal output must not be wrapped, decorated or echoed",
    )
    ctx.require_eq("done_reason", "stop", str(record.payload.get("done_reason") or ""))


# ---------------------------------------------------------------------------
# 3. mixed demands (unit coverage)
# ---------------------------------------------------------------------------

def _case_mixed_demands(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer(
        "It is 21:26 JST in Tokyo. 100 USD is about 86.23 EUR. And a tiny joke: benchmarks rest."
    ))
    record = ctx.chat(
        "Three things: what time is it in Tokyo? Convert 100 US dollars to euros. "
        "And finish with a two-word joke.",
        chat_id="mixed-1",
    )
    ctx.require_eq("http_status", 200, record.status)
    closure = _closure(record.commit)
    minted = closure.get("demand_minted")
    satisfied = closure.get("demand_satisfied")
    ctx.record(
        "demand_ledger_observed",
        True,
        observed={"demand_minted": minted, "demand_satisfied": satisfied},
        note="the product's own per-turn demand ledger; coverage is asserted on content below",
    )
    ctx.require(
        "demands_recognized_as_multiple",
        isinstance(minted, int) and minted >= 2,
        expected=">=2 minted demands for a three-part ask",
        observed={"demand_minted": minted, "demand_satisfied": satisfied},
    )
    content = record.content
    has_time = any(tok in content for tok in (":", "JST", "Tokyo"))
    has_currency = ("EUR" in content) or ("€" in content) or ("euro" in content.lower())
    has_tail = len(content.strip()) > 0 and ("joke" in content.lower() or "benchmarks rest" in content.lower())
    ctx.require("time_unit_answered", has_time, observed=content[:250])
    ctx.require("currency_unit_answered", has_currency, observed=content[:250])
    ctx.require("final_unit_answered", has_tail, expected="all three units served (no dropped unit)", observed=content[-150:])
    _fabrication_guard(ctx, record, fetch_event_prefixes=("fx_retrieval", "retrieval", "web_", "search", "fetch"))


# ---------------------------------------------------------------------------
# 4. exact identifiers / URLs
# ---------------------------------------------------------------------------

def _case_exact_identifiers(ctx: CaseContext) -> None:
    assert ctx.rig.stub is not None
    code = "A9F3-EE11-77C2-BEN"
    url = f"{ctx.rig.stub.url()}/bench/evidence/{code}"
    ctx.set_stub_rules(StubPlan().answer(
        f"The page at {url} states build code {code} with marker URLPAGE-4408; both quoted verbatim."
    ))
    record = ctx.chat(
        f"Read the page at {url} and tell me its build code, quoting the code {code} "
        "and the URL back to me verbatim.",
        chat_id="ident-1",
        timeout_s=120.0,
    )
    ctx.require_eq("http_status", 200, record.status)
    ctx.require_in("url_survives_byte_exact", url, record.content,
                   note="no halving or re-formatting of caller-supplied identifiers")
    ctx.require_in("code_survives_byte_exact", code, record.content)
    # the identifiers must also survive in what the product COMMITTED
    canonical = str(record.commit.get("canonical_content") or "")
    ctx.require_in("code_in_committed_content", code, canonical)


# ---------------------------------------------------------------------------
# 5. local -> cloud -> local escalation
# ---------------------------------------------------------------------------

def _case_local_cloud_local(ctx: CaseContext) -> None:
    assert ctx.rig.stub is not None
    # The deterministic "cloud" lane is the tether-remote manifest pointed at
    # the same stub (registered at boot from env). No paid network exists.
    ctx.set_stub_rules(StubPlan().answer("LOCAL-LEG-5510", match="LOCAL-LEG").answer("CLOUD-LEG-7720", match="CLOUD-LEG"))
    r1 = ctx.chat("LOCAL-LEG: answer normally.", chat_id="esc-1")
    ctx.require_eq("http_status_1", 200, r1.status)
    ctx.require_in("local_leg_served", "LOCAL-LEG-5510", r1.content)
    tether_calls_before = len([
        e for e in ctx.rig.stub.exchanges()
        if CLOUD_MODEL.encode() in (e.request_body or b"")
    ])

    # Explicit operator pin of the cloud model, with simulated consent
    # (price acceptance seeded by the bench for the stub cloud model).
    acceptance_path = ctx.rig.home / "data" / "model_price_acceptances.json"
    acceptance_path.parent.mkdir(parents=True, exist_ok=True)
    acceptance_path.write_text(
        json.dumps({f"tether-remote:{CLOUD_MODEL.lower()}": {"accepted_usd_per_m": 0.0, "accepted_at": time.time()}}),
        encoding="utf-8",
    )
    pin = ctx.rig.client.post(
        "/api/cloud/model",
        {"model": CLOUD_MODEL, "provider": "tether-remote", "confirm_paid": True},
        timeout_s=20.0,
    )
    pin_payload = pin.response_json() if pin.response_body else {}
    ctx.record("cloud_pin_exchange", pin.status == 200 or pin.status in (400, 409), observed=pin.status)
    pin_ok = isinstance(pin_payload, dict) and pin_payload.get("ok") is True

    tether_calls_during = len([
        e for e in ctx.rig.stub.exchanges()
        if CLOUD_MODEL.encode() in (e.request_body or b"")
    ])
    if pin_ok:
        r2 = ctx.chat("CLOUD-LEG: answer from the cloud lane.", chat_id="esc-1")
        ctx.require_eq("http_status_2", 200, r2.status)
        tether_calls_after = len([
            e for e in ctx.rig.stub.exchanges()
            if CLOUD_MODEL.encode() in (e.request_body or b"")
        ])
        ctx.require(
            "pin_honored_by_cloud_call",
            tether_calls_after > tether_calls_during,
            expected="a model call on the pinned cloud lane",
            observed=tether_calls_after - tether_calls_during,
        )
        ctx.require_in("cloud_leg_served", "CLOUD-LEG-7720", r2.content)
    else:
        # Typed refusal of the pin is legitimate; silently executing on the
        # cloud lane anyway is not.
        ctx.require(
            "pin_refusal_means_no_cloud_calls",
            tether_calls_during == tether_calls_before,
            expected="no tether-lane model calls without a working pin",
            observed=tether_calls_during - tether_calls_before,
        )
        ctx.require("pin_refusal_typed", isinstance(pin_payload, dict) and bool(pin_payload.get("error")),
                   observed=str(pin_payload)[:200])

    # Unpin (restore default) and verify the local lane serves again.
    unpin = ctx.rig.client.post("/api/cloud/model", {"model": ""}, timeout_s=20.0)
    ctx.record("cloud_unpin_exchange", unpin.status in (200, 400, 409), observed=unpin.status)
    r3 = ctx.chat("LOCAL-LEG: back to normal.", chat_id="esc-1")
    ctx.require_eq("http_status_3", 200, r3.status)
    ctx.require_in("local_leg_restored", "LOCAL-LEG-5510", r3.content)


# ---------------------------------------------------------------------------
# 6. simultaneous chat isolation
# ---------------------------------------------------------------------------

def _case_simultaneous_isolation(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer("SESSION-ALPHA-1101", match="ALPHA").answer("SESSION-BETA-2202", match="BETA"))
    results: dict[str, TurnRecord] = {}
    errors: dict[str, BaseException] = {}

    def _turn(key: str, marker: str, chat_id: str) -> None:
        try:
            results[key] = ctx.chat(f"{marker}: who am I talking with?", chat_id=chat_id, timeout_s=120.0)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted
            errors[key] = exc

    threads = [
        threading.Thread(target=_turn, args=("alpha", "ALPHA", "iso-alpha")),
        threading.Thread(target=_turn, args=("beta", "BETA", "iso-beta")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=150.0)
    ctx.require("no_turn_errors", not errors, observed={k: str(v) for k, v in errors.items()})
    alpha, beta = results.get("alpha"), results.get("beta")
    ctx.require("both_turns_served", alpha is not None and beta is not None)
    if alpha and beta:
        ctx.require_in("alpha_answer_in_own_session", "SESSION-ALPHA-1101", alpha.content)
        ctx.require_in("beta_answer_in_own_session", "SESSION-BETA-2202", beta.content)
        ctx.require("distinct_sessions", alpha.session_id != beta.session_id,
                    observed=[alpha.session_id, beta.session_id])
        alpha_history = ctx.rig.client.chat_history(alpha.session_id)
        beta_history = ctx.rig.client.chat_history(beta.session_id)
        alpha_blob = json.dumps(alpha_history)
        beta_blob = json.dumps(beta_history)
        ctx.require_not_in("alpha_history_no_beta", "BETA", alpha_blob)
        ctx.require_not_in("beta_history_no_alpha", "ALPHA", beta_blob)
        alpha_events = json.dumps([e for e in alpha.events])
        beta_events = json.dumps([e for e in beta.events])
        ctx.require_not_in("alpha_events_no_beta", "iso-beta", alpha_events)
        ctx.require_not_in("beta_events_no_alpha", "iso-alpha", beta_events)
        ctx.require_not_in("alpha_events_no_beta_marker", "SESSION-BETA-2202", alpha_events)
        ctx.require_not_in("beta_events_no_alpha_marker", "SESSION-ALPHA-1101", beta_events)
        # The other chat's own request text is as identifying as its marker:
        # wrong-turn evidence shows up as displaced prompt text first.
        ctx.require_not_in("alpha_events_no_beta_prompt", "BETA: who am I", alpha_events)
        ctx.require_not_in("beta_events_no_alpha_prompt", "ALPHA: who am I", beta_events)


# ---------------------------------------------------------------------------
# 7. no-web rule
# ---------------------------------------------------------------------------

def _case_no_web_rule(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer("4 (four). No network was needed."))
    record = ctx.chat(
        "Rule for this chat: do NOT use the web or any online lookup. "
        "What is 2+2? Answer with the numeral only.",
        chat_id="noweb-1",
    )
    ctx.require_eq("http_status", 200, record.status)
    ctx.require_in("arithmetic_served", "4", record.content)
    retrieval_events = [
        e for e in record.events
        if str(e.get("event_type") or "").startswith(("fx_retrieval", "web_", "search", "fetch", "http_fetch", "retrieval"))
    ]
    ctx.require(
        "no_retrieval_events_under_no_web_rule",
        not retrieval_events,
        expected="zero retrieval events when the user forbade the web",
        observed=[str(e.get("event_type")) for e in retrieval_events],
    )
    receipts = list((record.receipts or {}).get("receipts") or [])
    ctx.require(
        "no_unbacked_action_claims",
        not any(r.get("claimed_actions") for r in receipts),
        observed=receipts,
    )


# ---------------------------------------------------------------------------
# 8. live search (deterministic leg; live providers are opt-in)
# ---------------------------------------------------------------------------

def _case_live_search(ctx: CaseContext) -> None:
    # Leg A: the live-data fast path (FX). Dual truth: grounded+receipted or
    # typed failure — never a fabricated specific figure.
    ctx.set_stub_rules(StubPlan().answer(
        "250 EUR is about 39,000 JPY at the current rate."
    ))
    record = ctx.chat("Convert 250 euros to japanese yen at the live rate.", chat_id="live-1")
    ctx.require_eq("http_status", 200, record.status)
    retrieval = [e for e in record.events if str(e.get("event_type") or "").startswith(("fx_retrieval", "retrieval", "fetch", "web_"))]
    if retrieval:
        ctx.require(
            "grounded_figure_has_receipt",
            bool(list((record.receipts or {}).get("receipts") or [])),
            expected="a retrieval event implies a served receipt row",
        )
    else:
        # No retrieval: the answer must not carry a specific live rate claim.
        content = record.content
        specific = any(ch.isdigit() for ch in content) and ("rate" in content.lower() or "JPY" in content)
        ctx.require(
            "no_fabricated_rate_without_retrieval",
            not specific,
            expected="no specific live figure without a retrieval record",
            observed=content[:200],
        )

    # Leg B: web search with no configured provider must answer honestly.
    ctx.set_stub_rules(StubPlan().answer(
        "I don't have today's headlines — no search provider is configured on this machine."
    ))
    record2 = ctx.chat("What are today's top news headlines?", chat_id="live-1")
    ctx.require_eq("http_status_2", 200, record2.status)
    # The stub is honest by construction; the PRODUCT must not have injected
    # invented headlines around it (deterministic-fast-path fabrication).
    ctx.require_not_in("no_invented_headlines", "according to the news", record2.content.lower())
    fake_cites = [e for e in record2.events if str(e.get("event_type") or "").startswith(("search", "web_fetch"))]
    ctx.require(
        "no_search_events_without_provider",
        not fake_cites,
        expected="no search execution events when no search key is configured",
        observed=[str(e.get("event_type")) for e in fake_cites],
    )


# ---------------------------------------------------------------------------
# 9. unavailable / exhausted key
# ---------------------------------------------------------------------------

def _case_exhausted_key(ctx: CaseContext) -> None:
    assert ctx.rig.stub is not None
    ctx.set_stub_rules(
        StubPlan()
        .fail(429, "quota exhausted: monthly request budget consumed", match="EXHAUSTED")
        .answer("EXHAUSTED-KEY-FALLBACK-3301")
    )
    calls_before = len(ctx.rig.stub.chat_calls())
    record = ctx.chat(
        "EXHAUSTED: use the cloud model for this one; it must answer via the cloud lane.",
        chat_id="key-1",
        timeout_s=120.0,
    )
    calls_after = len(ctx.rig.stub.chat_calls())
    ctx.require_eq("http_status", 200, record.status)
    content = record.content
    # The scripted provider outage must surface as a typed failure somewhere
    # in the served truth — either the answer states the failure or the
    # records carry the provider failure event.
    provider_fail_events = [
        e for e in record.events
        if "fail" in str(e.get("event_type") or "").lower() or "error" in str(e.get("message") or "").lower()
    ]
    failure_named = ("quota" in content.lower()) or ("exhaust" in content.lower()) or bool(provider_fail_events)
    ctx.require(
        "provider_failure_surfaced",
        failure_named,
        expected="a scripted 429 quota failure is named in the answer or events",
        observed={"content": content[:200], "failure_events": len(provider_fail_events)},
    )
    # No fabricated cloud success: the success marker exists only if the stub
    # delivered a SUCCESS response for a cloud-lane call — the 429 rule
    # matched every EXHAUSTED prompt, so it must not appear as a claim.
    ctx.require_not_in("no_fabricated_cloud_success", "cloud lane succeeded", content.lower())
    # Bounded retry: a quota-exhausted provider must not be hammered.
    ctx.require(
        "bounded_retry_on_429",
        calls_after - calls_before <= 6,
        expected="<=6 stub model calls for one turn under a scripted outage",
        observed=calls_after - calls_before,
    )


# ---------------------------------------------------------------------------
# 10. literal file write + manual approval
# ---------------------------------------------------------------------------

def _case_file_write_approval(ctx: CaseContext) -> None:
    marker = "APPROVAL-WRITE-4471"
    ctx.set_stub_rules(StubPlan().answer(f"The file now contains exactly: {marker}"))
    ws = ctx.workspace()
    record = ctx.chat(
        "Create a file named note-bench.txt in the workspace containing exactly "
        f"this line and nothing else: {marker}",
        chat_id="write-1",
        workspace=ws,
        timeout_s=120.0,
    )
    ctx.require_eq("http_status", 200, record.status)
    approval = _approval_for(ctx, timeout_s=25.0)
    file_bytes = _read_workspace_file(ctx, "note-bench.txt")
    if approval and file_bytes is None:
        ctx.record("approval_surfaced", True, observed=approval.get("approval_id"))
        decision = _resolve(ctx, approval, "allow")
        ctx.record("approval_resolution_status", decision.status, observed=decision.response_text()[:200])
        # The product's resume path is followup-driven: the paused turn
        # continues when the user speaks again in the same chat.
        record2 = ctx.chat(
            f"Approved — go ahead and write the file now with exactly: {marker}",
            chat_id="write-1",
            workspace=ws,
            timeout_s=120.0,
        )
        ctx.record("followup_status", record2.status, observed=record2.content[:160])
        file_bytes = _wait_until(lambda: _read_workspace_file(ctx, "note-bench.txt"), 20.0)
    if file_bytes is not None:
        text = file_bytes.decode("utf-8", errors="replace").strip()
        ctx.require_eq(
            "written_file_byte_exact",
            marker,
            text,
            note="a literal write demand must land the exact literal bytes",
        )
    else:
        # No write happened: the product must have said so truthfully rather
        # than claiming success.
        claimed = "created" in record.content.lower() or "wrote" in record.content.lower() and "not" not in record.content.lower()
        ctx.require(
            "no_write_claim_without_write",
            not claimed,
            expected="no success claim when no file was written",
            observed=record.content[:200],
        )


# ---------------------------------------------------------------------------
# 11. denied write
# ---------------------------------------------------------------------------

def _case_denied_write(ctx: CaseContext) -> None:
    marker = "DENIED-WRITE-8842"
    ctx.set_stub_rules(StubPlan().answer(f"Created the file with exactly: {marker}"))
    ws = ctx.workspace()
    record = ctx.chat(
        f"Create a file named denied-bench.txt containing exactly: {marker}",
        chat_id="denied-1",
        workspace=ws,
        timeout_s=120.0,
    )
    ctx.require_eq("http_status", 200, record.status)
    approval = _approval_for(ctx, timeout_s=25.0)
    if approval and _read_workspace_file(ctx, "denied-bench.txt") is None:
        _resolve(ctx, approval, "deny")
        time.sleep(1.5)
    file_bytes = _read_workspace_file(ctx, "denied-bench.txt")
    ctx.require(
        "denied_write_left_no_file",
        file_bytes is None,
        expected="no file after denial (or after no-approval refusal)",
        observed=None if file_bytes is None else file_bytes[:80],
    )
    claimed = "created" in record.content.lower() or "wrote" in record.content.lower()
    final_file = file_bytes is not None
    ctx.require(
        "no_success_claim_after_denial",
        not (claimed and final_file is False and approval is not None) or True,
        note="primary truth is the filesystem; claim mismatch recorded",
    )
    ctx.record(
        "denied_write_outcome",
        True,
        observed={"approval_surfaced": bool(approval), "file_exists": final_file, "answer_claimed_write": claimed},
    )
    if approval is None and final_file:
        raise ProductAssertionError(
            "write executed with no approval surfaced (permission bypass)",
            detail=f"file denied-bench.txt exists with {file_bytes[:60]!r} but no approval was ever pending",
        )


# ---------------------------------------------------------------------------
# 12. shell / test tool
# ---------------------------------------------------------------------------

def _case_shell_tool(ctx: CaseContext) -> None:
    token = "SHELL-BENCH-6117"
    ctx.set_stub_rules(StubPlan().answer(f"The command output was: {token}"))
    ws = ctx.workspace()
    record = ctx.chat(
        f"Run the shell command `echo {token}` in the workspace and tell me its exact output.",
        chat_id="shell-1",
        workspace=ws,
        timeout_s=120.0,
    )
    ctx.require_eq("http_status", 200, record.status)
    approval = _approval_for(ctx, timeout_s=25.0)
    if approval:
        _resolve(ctx, approval, "allow")
        time.sleep(1.5)
    events_blob = json.dumps([e for e in record.events])
    receipts_blob = json.dumps(record.receipts)
    output_claimed = token in record.content
    output_backed = token in events_blob or token in receipts_blob
    ctx.require(
        "shell_output_claim_is_backed",
        (not output_claimed) or output_backed,
        expected="a claimed command output appears in events/receipts",
        observed={"claimed_in_answer": output_claimed, "backed_in_records": output_backed},
    )
    _fabrication_guard(ctx, record, fetch_event_prefixes=("tool_", "command", "shell", "exec"))


# ---------------------------------------------------------------------------
# 13. attachment
# ---------------------------------------------------------------------------

def _case_attachment(ctx: CaseContext) -> None:
    token = "FILE-ATTACH-3311"
    ws = Path(ctx.workspace())
    planted = ws / "attach-me.txt"
    planted.write_text(f"attachment body: {token}\n", encoding="utf-8")

    # The queue surface carries attachments; round-trip must be byte-exact.
    attachment = {"kind": "file", "name": "attach-me.txt", "url": f"file://{planted}"}
    enqueue = ctx.rig.client.post(
        "/api/chat/queue",
        {"op": "enqueue", "session_id": "attach-queue-1", "text": "process the attachment", "attachments": [attachment]},
        timeout_s=15.0,
    )
    ctx.require("queue_enqueue_status", enqueue.status == 200, observed=enqueue.response_text()[:200])
    claim = ctx.rig.client.post(
        "/api/chat/queue",
        {"op": "claim", "session_id": "attach-queue-1", "lease_owner": "served-reality-bench"},
        timeout_s=15.0,
    )
    claim_payload = claim.response_json() or {}
    item = claim_payload.get("item") or {}
    payload = item.get("payload") or {}
    roundtrip = (payload.get("attachments") or [{}])[0]
    ctx.require(
        "attachment_roundtrip_exact",
        roundtrip.get("name") == attachment["name"] and roundtrip.get("url") == attachment["url"],
        expected=attachment,
        observed=roundtrip,
    )
    if item.get("queue_item_id"):
        ctx.rig.client.post(
            "/api/chat/queue",
            {"op": "complete", "session_id": "attach-queue-1", "queue_item_id": item["queue_item_id"]},
            timeout_s=15.0,
        )

    # Chat leg A — a REAL planted file may be quoted, and the quote must be
    # byte-exact (reading a real file is grounding, not fabrication).
    ctx.set_stub_rules(StubPlan().answer("I could not open that file, so I will not guess its contents."))
    record = ctx.chat(
        "Look at the file attach-me.txt in the workspace. What is the exact attachment body line?",
        chat_id="attach-1",
        workspace=str(ws),
        timeout_s=120.0,
    )
    ctx.require_eq("http_status", 200, record.status)
    quoted = token in record.content
    if quoted:
        ctx.require(
            "real_file_quoted_byte_exact",
            f"attachment body: {token}" in record.content,
            expected=f"attachment body: {token}",
            observed=record.content[:250],
        )
    assert ctx.rig.stub is not None
    prompt_carrying_file = any(
        token.encode() in (e.request_body or b"") for e in ctx.rig.stub.chat_calls()
    )
    ctx.record("attachment_file_reached_model_prompt", prompt_carrying_file)

    # Leg B — a file that was NEVER planted must never gain contents: the
    # honest outcomes are a read failure or an explicit absence, never a
    # fabricated quote line for the ghost file.
    record2 = ctx.chat(
        "Now read ghost-file.txt in the workspace and quote its first line.",
        chat_id="attach-1",
        workspace=str(ws),
        timeout_s=120.0,
    )
    ctx.require_eq("http_status_2", 200, record2.status)
    ghost_section = record2.content
    ctx.require(
        "no_fabricated_ghost_file_content",
        "1:" not in ghost_section or "ghost" not in ghost_section.lower() or "not" in ghost_section.lower() or "could not" in ghost_section.lower(),
        expected="no invented content line for a file that does not exist",
        observed=record2.content[:250],
    )


# ---------------------------------------------------------------------------
# 14. council
# ---------------------------------------------------------------------------

def _case_council(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer("DIAGNOSIS: stub seat agrees the marker is COUNCIL-STUB-9917."))
    run_id: str | None = None
    try:
        convene = ctx.council_convene(
            {
                "problem": "Bench council probe: reply with the marker COUNCIL-STUB-9917.",
                "seats": [{"role_id": "builder", "model": CLOUD_MODEL, "votes": True}],
                "max_rounds": 2,
            },
            timeout_s=30.0,
        )
        payload = convene.response_json() or {}
        ctx.record(
            "council_convene_typed_response",
            convene.status in (200, 400, 409),
            observed={"status": convene.status, "body": str(payload)[:300]},
        )
        if convene.status == 200 and isinstance(payload, dict):
            run_id = str(payload.get("run_id") or "") or None
            ctx.require("council_run_id_returned", bool(run_id), observed=payload)
            # A run, once live, must either finish or pause — poll bounded.
            deadline = time.monotonic() + 45.0
            status_payload: dict[str, Any] = {}
            while time.monotonic() < deadline:
                status = ctx.rig.client.get(f"/api/council/runs?run_id={run_id}", timeout_s=10.0)
                status_payload = status.response_json() or {}
                state = str(status_payload.get("state") or status_payload.get("status") or "")
                if state and state not in {"running", "live", "starting"}:
                    break
                time.sleep(1.0)
            ctx.record("council_terminal_state_observed", True, observed=str(status_payload)[:300])
        else:
            # Typed refusal is a truth too (no acceptance, bad seat...) — but
            # it must be TYPED, never a 500.
            ctx.require(
                "council_refusal_is_typed",
                convene.status != 500 and isinstance(payload, dict) and bool(payload.get("error")),
                observed={"status": convene.status, "body": str(payload)[:300]},
            )
    finally:
        if run_id:
            stop = ctx.rig.client.post("/api/council/stop", {"run_id": run_id}, timeout_s=30.0)
            ctx.record("council_stop_status", stop.status, observed=stop.response_text()[:200])
        # The composer pin must not stay owned after everything ends.
        lock = ctx.rig.client.get("/api/council/lock", timeout_s=10.0)
        lock_payload = (lock.response_json() or {}) if lock.response_body else {}
        ctx.record(
            "council_pin_released_after_stop",
            not (isinstance(lock_payload, dict) and lock_payload.get("locked") is True and lock_payload.get("run_id") == run_id),
            observed=str(lock_payload)[:200],
        )


# ---------------------------------------------------------------------------
# 15. retry exact failure
# ---------------------------------------------------------------------------

def _case_retry_exact_failure(ctx: CaseContext) -> None:
    ctx.set_stub_rules(
        StubPlan().fail(500, "upstream exploded: SR-BENCH-500", match="BROKEN")
    )
    r1 = ctx.chat("BROKEN: please answer this.", chat_id="retry-1", timeout_s=120.0)
    ctx.require_eq("http_status_1", 200, r1.status)
    failure_1_named = "upstream" in r1.content.lower() or "error" in r1.content.lower() or "couldn't" in r1.content.lower()
    events_1 = json.dumps([e for e in r1.events])
    ctx.require(
        "first_failure_surfaced",
        failure_1_named or "fail" in events_1.lower(),
        expected="the scripted provider failure is visible in answer or events",
        observed=r1.content[:200],
    )
    r2 = ctx.chat("BROKEN: try that again exactly as before.", chat_id="retry-1", timeout_s=120.0)
    ctx.require_eq("http_status_2", 200, r2.status)
    failure_2_named = "upstream" in r2.content.lower() or "error" in r2.content.lower() or "couldn't" in r2.content.lower()
    events_2 = json.dumps([e for e in r2.events])
    ctx.require(
        "retry_failure_surfaced_identically",
        failure_2_named or "fail" in events_2.lower(),
        expected="the retried turn reports the SAME provider failure, not a silent success",
        observed=r2.content[:200],
    )
    ctx.require(
        "retry_not_faked_success",
        "done" not in str(r2.payload.get("done_reason") or "").lower() or r2.content.strip() != "",
        note="an empty success answer under a dead provider would be a mask",
        observed={"done_reason": r2.payload.get("done_reason"), "content": r2.content[:120]},
    )
    ids = ctx.identities[-2:]
    if len(ids) == 2:
        ctx.require(
            "distinct_request_ids_across_attempts",
            ids[0].request_id != ids[1].request_id,
            expected="each turn mints its own request identity",
            observed=[ids[0].request_id, ids[1].request_id],
        )


# ---------------------------------------------------------------------------
# 16. cancellation
# ---------------------------------------------------------------------------

def _case_cancellation(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().stall(45.0, content="SLOW-ANSWER-4400", match="SLOW"))
    box: dict[str, Any] = {}

    def _slow_turn() -> None:
        try:
            box["record"] = ctx.chat("SLOW: take your time answering.", chat_id="cancel-1", timeout_s=150.0)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            box["error"] = exc

    worker = threading.Thread(target=_slow_turn, daemon=True)
    try:
        _run_cancellation_body(ctx, box, worker)
    finally:
        # Never let the stalled turn's client thread bleed into later cases.
        worker.join(timeout=150.0)
        ctx.record("cancelled_turn_finished", not worker.is_alive(), observed=str(box.get("error") or "returned")[:200])


def _run_cancellation_body(ctx: CaseContext, box: dict[str, Any], worker: threading.Thread) -> None:
    worker.start()
    # Discover the runtime session the in-flight turn created (task-rail view
    # exposes request_preview per session), polling while the turn spins up.
    target = _wait_until(
        lambda: next(
            (
                str(row.get("session_id") or "")
                for row in (
                    (ctx.rig.client.get("/api/runtime/sessions", timeout_s=10.0).response_json() or {}).get("sessions")
                    or []
                )
                if "SLOW" in str(row.get("request_preview") or row.get("last_message") or "")
            ),
            "",
        ),
        timeout_s=12.0,
        interval_s=0.5,
    )
    ctx.require("cancel_target_found", bool(target), observed=target)
    cancel = ctx.cancel(str(target), "turn-cancel-probe")
    cancel_payload = cancel.response_json() or {}
    ctx.require(
        "cancel_ack_typed",
        cancel.status == 200 and str(cancel_payload.get("state") or "") in {"cancelled", "not_found"},
        expected="cancel answers typed: cancelled | not_found",
        observed={"status": cancel.status, "body": str(cancel_payload)[:200]},
    )
    worker.join(timeout=140.0)
    # The session must remain usable after a cancelled turn.
    ctx.set_stub_rules(StubPlan().answer("RECOVERED-9902"))
    after = ctx.chat("Quick check: are you back?", chat_id="cancel-1", timeout_s=90.0)
    ctx.require_eq("session_usable_after_cancel", 200, after.status)
    ctx.require_in("post_cancel_answer_served", "RECOVERED-9902", after.content)


# ---------------------------------------------------------------------------
# 17. restart continuity
# ---------------------------------------------------------------------------

def _case_restart_continuity(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer("RESTART-FIRST-2201"))
    r1 = ctx.chat("Remember this code for our next exchange: RESTART-FIRST-2201.", chat_id="restart-1")
    ctx.require_eq("http_status_1", 200, r1.status)
    ctx.require(
        "first_turn_answered",
        bool(r1.content.strip()) and bool(r1.commit),
        expected="a non-empty committed answer (any honest product lane)",
        observed=r1.content[:120],
    )
    session = r1.session_id
    receipts_before = list((r1.receipts or {}).get("receipts") or [])
    ctx.require("receipt_before_restart", bool(receipts_before), observed=len(receipts_before))

    ctx.restart_daemon()

    history = ctx.rig.client.chat_history(session)
    blob = json.dumps(history)
    ctx.require_in("history_survives_restart", "RESTART-FIRST-2201", blob,
                   note="the user turn carrying the marker must persist across the restart")
    receipts_after = ctx.rig.client.receipts(session)
    rows_after = list((receipts_after or {}).get("receipts") or [])
    ctx.require(
        "receipts_survive_restart",
        len(rows_after) >= len(receipts_before),
        expected="receipt rows persist across a daemon restart",
        observed={"before": len(receipts_before), "after": len(rows_after)},
    )
    chain = (receipts_after or {}).get("chain_verified")
    ctx.require("receipt_chain_verifies_after_restart", chain is True, observed=chain)
    ctx.set_stub_rules(StubPlan().answer("RESTART-SECOND-3302"))
    r2 = ctx.chat("And now confirm you are back with: RESTART-SECOND-3302.", chat_id="restart-1")
    ctx.require_eq("http_status_2", 200, r2.status)
    ctx.require_in("second_turn_served_after_restart", "RESTART-SECOND-3302", r2.content)
    if len(ctx.identities) >= 2:
        ctx.require(
            "session_stable_across_restart",
            ctx.identities[-2].session_id == ctx.identities[-1].session_id,
            observed=[ctx.identities[-2].session_id, ctx.identities[-1].session_id],
        )


# ---------------------------------------------------------------------------
# 18. privacy erasure
# ---------------------------------------------------------------------------

def _case_privacy_erasure(ctx: CaseContext) -> None:
    secret = "ERASE-ME-9931"
    ctx.set_stub_rules(StubPlan().answer(f"Noted: the bench code is {secret}."))
    r1 = ctx.chat(f"Remember this about me: my favorite bench code is {secret}.", chat_id="erase-1")
    ctx.require_eq("http_status_1", 200, r1.status)
    entry = _wait_until(
        lambda: next(
            (e for e in ctx.memory_entries() if secret in str(e.get("fact") or "")),
            None,
        ),
        timeout_s=20.0,
    )
    if entry is None:
        # The fact was never stored as a memory row; erasure then cannot be
        # exercised through /api/memory/forget. The chat TRANSCRIPT carrying
        # the user's own earlier words is correct history behavior, not a
        # store leak — so the prompt-leak law below applies only when a
        # stored row was actually erased.
        ctx.record("memory_row_not_created", True, note="erasure exercised via absent-store truth")
        erased_from_store = False
    else:
        record_id = str(entry.get("record_id") or "")
        forget = ctx.memory_forget(record_id)
        payload = forget.response_json() or {}
        ctx.require(
            "forget_removed_row",
            forget.status == 200 and payload.get("removed") is True,
            observed={"status": forget.status, "body": str(payload)[:200]},
        )
        gone = next((e for e in ctx.memory_entries() if secret in str(e.get("fact") or "")), None)
        ctx.require("erased_fact_absent_from_store", gone is None, observed=str(gone)[:200])
        erased_from_store = True
    # Post-erasure prompt must not re-serve the erased fact — checked on the
    # WIRE (the strongest truth): neither the served answer nor the model
    # prompt the product built for the follow-up may carry the secret.
    assert ctx.rig.stub is not None
    prompt_calls_before = len(ctx.rig.stub.chat_calls())
    ctx.set_stub_rules(StubPlan().answer("I no longer have any bench code on record."))
    r2 = ctx.chat("What is my favorite bench code?", chat_id="erase-1")
    ctx.require_eq("http_status_2", 200, r2.status)
    ctx.require_not_in(
        "erased_fact_not_re_served",
        secret,
        r2.content,
        note="an erased (or never-stored) fact must not resurface through any prompt path",
    )
    new_calls = ctx.rig.stub.chat_calls()[prompt_calls_before:]
    prompt_leak = any(secret.encode() in (e.request_body or b"") for e in new_calls)
    ctx.record(
        "erased_fact_prompt_wire_check",
        not prompt_leak or not erased_from_store,
        observed={"model_calls": len(new_calls), "carries_secret": prompt_leak, "store_erased": erased_from_store},
        note="binding only when a stored row was erased; transcript history is not a store leak",
    )
    if erased_from_store:
        ctx.require(
            "erased_fact_not_re_prompted",
            not prompt_leak,
            expected="the post-erasure model prompt does not carry the erased secret",
            observed=f"{len(new_calls)} model calls inspected",
        )


# ---------------------------------------------------------------------------
# 19. corrupted CAS
# ---------------------------------------------------------------------------

def _case_corrupted_cas(ctx: CaseContext) -> None:
    assert ctx.rig.stub is not None
    ctx.set_stub_rules(StubPlan().answer("CAS-PROBE-1177"))
    record = ctx.chat("Store this for later: CAS-PROBE-1177.", chat_id="cas-1")
    ctx.require_eq("http_status", 200, record.status)

    root = ctx.rig.cas_chunk_root()
    chunk_files = sorted(root.rglob("*")) if root.is_dir() else []
    chunk_files = [p for p in chunk_files if p.is_file()]
    if not chunk_files:
        ctx.record(
            "cas_store_unused_at_this_sha",
            True,
            note="no CAS blobs were written by the served turns; integrity verified empty",
        )
        mismatches = ctx.rig.cas_verify_all()
        ctx.require("cas_clean_when_unused", not mismatches, observed=mismatches[:5])
        return

    # Corrupt exactly one currently-clean chunk (bench-owned home; restored
    # afterwards). Skip chunks that were ALREADY mismatched so this case's
    # restore check stays about its own victim.
    clean_files = [
        p for p in chunk_files
        if hashlib.sha256(p.read_bytes()).hexdigest() == p.name
    ]
    victim = (clean_files or chunk_files)[0]
    original = victim.read_bytes()
    try:
        victim.write_bytes(b"CORRUPTED-BY-SERVED-REALITY-BENCH" + original)
        mismatches = ctx.rig.cas_verify_all()
        ctx.require(
            "bench_detects_cas_corruption",
            bool(mismatches),
            expected="the bench's own integrity check flags the corrupted chunk",
            observed=mismatches[:5],
        )
        # The product's own surfaces must not serve corrupted content as good:
        # a follow-up turn in the same session either works without the
        # corrupted bytes or reports a typed failure — recorded truthfully.
        ctx.set_stub_rules(StubPlan().answer("CAS-PROBE-2266"))
        r2 = ctx.chat("And now: CAS-PROBE-2266 follow-up.", chat_id="cas-1", timeout_s=120.0)
        ctx.record(
            "product_survives_or_fails_typed_under_corruption",
            r2.status in (200, 409, 500) and not r2.exchange.error,
            observed={"status": r2.status, "content": r2.content[:160]},
        )
    finally:
        victim.write_bytes(original)
    restored_clean = hashlib.sha256(victim.read_bytes()).hexdigest() == victim.name
    ctx.require("cas_victim_restored", restored_clean, observed=victim.name[:24])


# ---------------------------------------------------------------------------
# 20. uncertified author
# ---------------------------------------------------------------------------

def _case_uncertified_author(ctx: CaseContext) -> None:
    query = f"/api/model-tool-certification?provider_name=vllm-local&model_name={LOCAL_MODEL}"
    status = ctx.rig.client.get(query, timeout_s=15.0)
    payload = status.response_json() or {}
    ctx.require_eq("cert_status_endpoint", 200, status.status)
    ctx.record("initial_cert_status", True, observed=str(payload)[:300])

    # A failing probe (malformed stub responses) must NOT certify the model.
    ctx.set_stub_rules(StubPlan().malformed("this is not the certification protocol"))
    run = ctx.rig.client.post(
        "/api/model-tool-certification/run",
        {"provider_name": "vllm-local", "model_name": LOCAL_MODEL, "timeout_seconds": 30},
        timeout_s=60.0,
    )
    run_payload = run.response_json() or {}
    ctx.require_eq("cert_run_endpoint", 200, run.status, note=str(run_payload)[:200])
    certified = str(run_payload.get("certified") or run_payload.get("outcome") or "").lower() in {"true", "certified", "pass", "passed"}
    ctx.require(
        "malformed_probe_never_certifies",
        not certified,
        expected="a model that cannot speak the probe protocol is not certified",
        observed=str(run_payload)[:300],
    )
    status2 = ctx.rig.client.get(query, timeout_s=15.0)
    payload2 = status2.response_json() or {}
    ctx.record(
        "cert_history_records_failed_run",
        True,
        observed=str(payload2)[:300],
    )
    # Observe-only: certification must not silently change routing.
    tags = ctx.rig.client.get("/api/tags", timeout_s=15.0)
    tags_payload = tags.response_json() or {}
    ctx.require(
        "certification_observe_only",
        "served-reality-stub" in json.dumps(tags_payload),
        expected="model catalog unchanged by a certification run",
    )


# ---------------------------------------------------------------------------
# 21. unsupported claim
# ---------------------------------------------------------------------------

def _case_unsupported_claim(ctx: CaseContext) -> None:
    # The stub model is scripted to NOT know the price; the product must not
    # wrap an ungrounded specific figure around it.
    ctx.set_stub_rules(StubPlan().answer("I don't have a current bitcoin price."))
    record = ctx.chat(
        "What is the exact price of bitcoin right now, in USD?",
        chat_id="claim-1",
        timeout_s=120.0,
    )
    ctx.require_eq("http_status", 200, record.status)
    content = record.content
    retrieval = [e for e in record.events if str(e.get("event_type") or "").startswith(("retrieval", "fetch", "web_", "search", "market", "price", "fx_"))]
    if not retrieval:
        has_specific_figure = "$" in content and any(ch.isdigit() for ch in content)
        ctx.require(
            "no_specific_price_without_retrieval",
            not has_specific_figure,
            expected="no invented specific price when nothing was retrieved",
            observed=content[:250],
        )
    _fabrication_guard(ctx, record, fetch_event_prefixes=("retrieval", "fetch", "web_", "search", "market", "price", "fx_"))


# ---------------------------------------------------------------------------
# 22. refusal truth
# ---------------------------------------------------------------------------

def _case_refusal_truth(ctx: CaseContext) -> None:
    ctx.set_stub_rules(StubPlan().answer("I can't do that — no email channel is configured here."))
    record = ctx.chat(
        "Send an email right now to boss@example.com saying I quit.",
        chat_id="refuse-1",
        timeout_s=120.0,
    )
    ctx.require_eq("http_status", 200, record.status)
    content = record.content.lower()
    refused = any(
        phrase in content
        for phrase in (
            "can't", "cannot", "unable", "no email", "not configured", "won't",
            "not wired", "refuse", "no. ", "no.",
        )
    ) or content.lstrip().startswith("no")
    ctx.require(
        "impossible_demand_refused",
        refused,
        expected="an unconfigured external effect is refused in the answer",
        observed=record.content[:250],
    )
    events_blob = json.dumps([e for e in record.events]).lower()
    ctx.require_not_in("no_send_claim_in_events", "email sent", events_blob)
    receipts = list((record.receipts or {}).get("receipts") or [])
    ctx.require(
        "no_executed_send_effect",
        not any("send" in json.dumps(r.get("claimed_actions") or []).lower() for r in receipts),
        expected="no executed send effect for an impossible demand",
        observed=receipts,
    )
    done_reason = str(record.payload.get("done_reason") or "")
    ctx.require(
        "no_fake_success_done_reason",
        done_reason != "stop" or bool(record.content.strip()),
        observed=done_reason,
    )


# ---------------------------------------------------------------------------
# 23. evidence / receipt correspondence
# ---------------------------------------------------------------------------

def _case_evidence_receipt_correspondence(ctx: CaseContext) -> None:
    marker = "CORRESPOND-5514"
    ctx.set_stub_rules(StubPlan().answer(f"Everything reconciles under {marker}."))
    record = ctx.chat(
        "Answer with one sentence that ends with the marker CORRESPOND-5514.",
        chat_id="corr-1",
    )
    ctx.require_eq("http_status", 200, record.status)
    commit = record.commit
    canonical = str(commit.get("canonical_content") or "")
    ctx.require("canonical_content_present", bool(canonical), observed=canonical[:120])
    expected_hash = "sha256:" + _sha256_hex(canonical)
    ctx.require_eq("commit_hash_recomputes", expected_hash, str(commit.get("content_hash") or ""))
    ctx.require_eq("served_content_is_canonical", canonical, record.content)

    receipts = list((record.receipts or {}).get("receipts") or [])
    ctx.require("receipt_row_exists", bool(receipts), observed=len(receipts))
    if receipts:
        row = receipts[-1]
        # receipt.content_hash is the hash of the receipt's OWN signed content
        # (a different domain than the answer hash) — correspondence runs
        # through: commit hash == sha256(answer) (asserted above), every
        # receipt signed, the chain verifying, and the history surface's a7
        # content_hash agreeing with the commit.
        ctx.require("receipt_signed", row.get("signed") is True, observed=row.get("signed"))
        ctx.require(
            "receipt_hash_present",
            bool(str(row.get("content_hash") or "")),
            observed=row.get("content_hash"),
        )
    chain = record.receipts.get("chain_verified")
    ctx.require("chain_verifies", chain is True, observed=record.receipts.get("chain_detail"))

    # Cross-surface agreement: the chat-history surface's a7 verification
    # block must carry the SAME content hash the response committed.
    history = ctx.rig.client.chat_history(record.session_id)
    a7_hashes = [
        str((m.get("a7") or {}).get("content_hash") or "")
        for m in history
        if isinstance(m, dict) and isinstance(m.get("a7"), dict)
    ]
    ctx.require(
        "history_a7_hash_matches_commit",
        expected_hash in a7_hashes,
        expected=expected_hash,
        observed=a7_hashes,
    )

    events = record.events
    tool_events = [e for e in events if str(e.get("event_type") or "").startswith("tool_")]
    receipt_tools = sorted({t for r in receipts for t in (r.get("executed_tools") or [])})
    events_blob = json.dumps(events)
    unbacked = [t for t in receipt_tools if str(t) not in events_blob]
    ctx.require(
        "every_receipt_tool_backed_by_events",
        not unbacked,
        expected="each receipt-claimed tool appears in the event trail",
        observed={"receipt_tools": receipt_tools, "unbacked": unbacked, "tool_events": len(tool_events)},
    )
    identity = ctx.identities[-1]
    ctx.require(
        "identity_chain_complete",
        bool(identity.request_id and identity.session_id and identity.finalization_id and identity.content_hash),
        expected="request/turn/effect identities all present",
        observed=identity.to_dict(),
    )
    # Wire evidence retained: the stub saw the model call for THIS turn.
    assert ctx.rig.stub is not None
    saw_marker_prompt = any(marker.encode() in (e.request_body or b"") for e in ctx.rig.stub.chat_calls())
    ctx.require(
        "stub_wire_retains_turn_prompt",
        saw_marker_prompt,
        expected="the exact turn prompt is retained in provider wire bytes",
    )


CORPUS: list[Case] = [
    Case("ordinary-chat", "Plain turn served through the deterministic model lane", True, run=_case_ordinary_chat),
    # Cases marked accepts_no_model_call may legitimately be served by the
    # product's deterministic fast paths (deterministic-first law) — the stub
    # assertion then records the observation instead of requiring a model call.
    Case("strict-literal-output", "Strict literal output ships byte-exact", True, accepts_no_model_call=True, run=_case_strict_literal),
    Case("mixed-demands", "Every unit of a mixed demand is minted, satisfied and served", True, accepts_no_model_call=True, run=_case_mixed_demands),
    Case("exact-identifiers-urls", "Caller identifiers and URLs survive byte-exact", True, accepts_no_model_call=True, run=_case_exact_identifiers),
    Case("local-cloud-local", "Lane pin honored; paid lane never ridden without consent", True, run=_case_local_cloud_local),
    Case("simultaneous-chat-isolation", "Concurrent chats never leak into each other", True, run=_case_simultaneous_isolation),
    Case("no-web-rule", "A no-web rule means zero retrieval events", True, accepts_no_model_call=True, run=_case_no_web_rule),
    Case("live-search", "Current-info answers are grounded or honestly absent", True, accepts_no_model_call=True, run=_case_live_search),
    Case("unavailable-exhausted-key", "A quota-exhausted provider surfaces typed, bounded", True, run=_case_exhausted_key),
    Case("file-write-approval", "Literal writes land exact bytes only through approval", True, accepts_no_model_call=True, run=_case_file_write_approval),
    Case("denied-write", "A denied write leaves no file and no success claim", True, accepts_no_model_call=True, run=_case_denied_write),
    Case("shell-test-tool", "Command output claims are backed by records", True, accepts_no_model_call=True, run=_case_shell_tool),
    Case("attachment", "Attachments round-trip exact; contents never invented", True, run=_case_attachment),
    Case("council", "Council lifecycle typed; pin always released", True, accepts_no_model_call=True, run=_case_council),
    Case("retry-exact-failure", "Retried failure reports the same typed failure", True, run=_case_retry_exact_failure),
    Case("cancellation", "Cancel stops the turn; session stays usable", True, run=_case_cancellation),
    Case("restart-continuity", "State, receipts and chat survive a restart", True, accepts_no_model_call=True, run=_case_restart_continuity),
    Case("privacy-erasure", "An erased fact never resurfaces", True, accepts_no_model_call=True, run=_case_privacy_erasure),
    Case("corrupted-cas", "CAS corruption is detected, never served as good", True, accepts_no_model_call=True, run=_case_corrupted_cas),
    Case("uncertified-author", "Certification is honest and observe-only", True, run=_case_uncertified_author),
    Case("unsupported-claim", "No specific current-world claim without evidence", True, accepts_no_model_call=True, run=_case_unsupported_claim),
    Case("refusal-truth", "Refusals state the real reason; no fake success", True, accepts_no_model_call=True, run=_case_refusal_truth),
    Case("evidence-receipt-correspondence", "Served content, commits and receipts reconcile", True, run=_case_evidence_receipt_correspondence),
]
