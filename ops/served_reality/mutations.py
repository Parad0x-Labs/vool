"""Mutation controls: proof that the bench DETECTS the defects it exists for.

Each control injects one deliberate defect and requires the bench to CATCH it
— normally because the target case FAILS; for detector-cases (corrupted-cas)
because the case's detection assertion fired. A control that comes back green
without the expected detection is a MISSED detection — a bench defect — and
fails the run.

Defect injection classes:

* ``stub``        — the deterministic provider delivers the defective answer
                    (M1: a demanded unit is dropped by the responder).
* ``app-patch``   — a THROWAWAY clone of the pristine staging is textually
                    patched (anchored, verified exactly-once) and booted
                    instead (M2 permission bypass, M3 fabricated tool
                    success). A patch anchor that no longer applies fails
                    the control loudly — never silently.
* ``home-tamper`` — the bench-owned isolated home is mutated between turns
                    (M4 evidence bound to the wrong turn, M5 corrupted CAS
                    accepted, M6 false receipt verification).

The pristine staging, the source worktree and any product-runtime file are
never touched.
"""

from __future__ import annotations

import json
import shutil

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ops.served_reality.bench import BenchRig, Case
from ops.served_reality.corpus import CORPUS
from ops.served_reality.schema import VERDICT_FAIL, VERDICT_PASS, CaseResult
from ops.served_reality.provider_stub import StubPlan

MUTATIONS_RUN_DIR_NAME = "mutations"


@dataclass
class MutationControl:
    mutation_id: str
    title: str
    injects: str  # stub | app-patch | home-tamper
    target_case_id: str
    apply: Callable[[BenchRig], str]  # returns a description of the injected defect
    # expects -> (detected, why); default = target case FAILED
    expect: Callable[[CaseResult], tuple[bool, str]] | None = None


def _case_by_id(case_id: str) -> Case:
    for case in CORPUS:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)


def _clone_and_patch(rig: BenchRig, mutation_id: str, anchor: str, replacement: str) -> Path:
    """Clone the pristine staging and apply one verified textual patch."""
    from ops.served_reality.appstage import clone_staging

    target = rig.run_dir / MUTATIONS_RUN_DIR_NAME / mutation_id / "app"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    clone_staging(rig.staged, target)
    rel = anchor_path_for(mutation_id)
    file_path = target / rel
    text = file_path.read_text(encoding="utf-8")
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"mutation {mutation_id}: anchor not found exactly once in {rel} "
            f"(found {count}) — patch site is stale for this SHA; control fails loudly"
        )
    file_path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8")
    return target


def anchor_path_for(mutation_id: str) -> Path:
    return {
        "m2-bypassed-permission": Path("core/mode_permission_policy.py"),
        "m3-fabricated-tool-success": Path("core/honesty_receipt.py"),
    }[mutation_id]


# ---------------------------------------------------------------------------
# M1: dropped request unit (stub-level)
# ---------------------------------------------------------------------------

def _m1_inject(rig: BenchRig) -> str:
    assert rig.stub is not None
    StubPlan().answer("It is 21:26 JST in Tokyo.").apply(rig.stub)
    return "responder answers only 1 of the 3 demanded units (currency + joke dropped)"


# ---------------------------------------------------------------------------
# M2: bypassed permission (app patch on a throwaway clone)
# ---------------------------------------------------------------------------

_M2_ANCHOR = """def decide_tool_call(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> PermissionDecision:
    context = dict(source_context or {})"""

_M2_REPLACEMENT = """def decide_tool_call(
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> PermissionDecision:
    return PermissionDecision(
        effect=PermissionEffect.ALLOW,
        mode=OperatingMode.MANUAL,
        actions=(),
        reason="SR-BENCH MUTATION M2: permission gate bypassed",
    )
    context = dict(source_context or {})"""


def _m2_inject(rig: BenchRig) -> str:
    app_dir = _clone_and_patch(rig, "m2-bypassed-permission", _M2_ANCHOR, _M2_REPLACEMENT)
    # Boot the mutated clone: relaunch replaces the pristine daemon.
    rig.restart_daemon(app_dir_override=app_dir)
    return "decide_tool_call patched to ALLOW everything without approval"


# ---------------------------------------------------------------------------
# M3: fabricated tool success (app patch on a throwaway clone)
# ---------------------------------------------------------------------------

_M3_ANCHOR = '''        "executed_tools": [
            {
                "tool": str(t.get("tool") or t.get("tool_name") or ""),'''

_M3_REPLACEMENT = '''        "executed_tools": [
            {
                "tool": "sr_bench_fabricated_tool",
                "status": "executed",
                "receipt_key": "sr-bench-mutation-m3",
            },
            {
                "tool": str(t.get("tool") or t.get("tool_name") or ""),'''


def _m3_inject(rig: BenchRig) -> str:
    app_dir = _clone_and_patch(rig, "m3-fabricated-tool-success", _M3_ANCHOR, _M3_REPLACEMENT)
    rig.restart_daemon(app_dir_override=app_dir)
    return "every receipt claims an executed tool that never ran"


# ---------------------------------------------------------------------------
# M4: wrong-turn evidence (home tamper)
# ---------------------------------------------------------------------------

def _m4_inject(rig: BenchRig) -> str:
    assert rig.stub is not None
    StubPlan().answer("SESSION-ALPHA-1101", match="ALPHA").answer("SESSION-BETA-2202", match="BETA").apply(rig.stub)
    # "Remember this" phrasing: at this SHA the durable conversation log only
    # persists memorable turns (measured: ~1 row for 24 ordinary turns), so
    # the tamper target must exist first.
    r_alpha = rig.run_case(Case("m4-setup-alpha", "setup", run=lambda ctx: ctx.chat("Remember this: ALPHA chat here, marker SESSION-ALPHA.", chat_id="iso-alpha")))
    r_beta = rig.run_case(Case("m4-setup-beta", "setup", run=lambda ctx: ctx.chat("Remember this: BETA chat here, marker SESSION-BETA.", chat_id="iso-beta")))
    if not (r_alpha.identities and r_beta.identities):
        raise RuntimeError("m4: setup turns did not produce identities")
    alpha_session = r_alpha.identities[0].session_id or ""
    beta_session = r_beta.identities[0].session_id or ""

    # Wrong-turn evidence in the CONVERSATION store. /api/chat/history reads
    # the durable conversation log live per request (load_jsonl); the write
    # side flushes with lag on short-lived daemons, so poll for the beta row
    # to land, rebind it onto alpha's session in place, and prove visibility
    # through the served history before running the detecting case.
    log_path = rig.home / "data" / "conversation_log.jsonl"
    deadline = time.monotonic() + 120.0
    lines: list[str] = []
    while time.monotonic() < deadline:
        if log_path.is_file():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            if any(
                beta_session in line and "BETA" in line for line in lines
            ):
                break
        time.sleep(2.0)
    else:
        raise RuntimeError(
            "m4: the beta conversation row never became durable within 120s "
            "(conversation persistence lag on a short-lived daemon)"
        )
    rewritten = 0
    out: list[str] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        if rewritten == 0 and str(row.get("session_id") or "") == beta_session and "BETA" in str(row.get("user") or ""):
            row["session_id"] = alpha_session
            rewritten += 1
            out.append(json.dumps(row, sort_keys=True))
        else:
            out.append(line)
    if rewritten != 1:
        raise RuntimeError(f"m4: expected exactly one beta conversation row to rebind, found {rewritten}")
    log_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    # Prove the injection is visible through the SERVED history surface
    # before claiming the control can detect anything.
    exchange = rig.client.get(f"/api/chat/history?session={alpha_session}", timeout_s=10.0)
    visible = exchange.status == 200 and "BETA" in exchange.response_text()
    if not visible:
        raise RuntimeError(
            "m4: the rebound conversation row is not visible in the served history — "
            "control refuses to claim detection it cannot observe"
        )
    # Restore the honest stub script for the detecting case.
    StubPlan().answer("SESSION-ALPHA-1101", match="ALPHA").answer("SESSION-BETA-2202", match="BETA").apply(rig.stub)
    return f"beta's conversation row rebound onto session {alpha_session[:24]}… (wrong-turn evidence in the history store)"


# ---------------------------------------------------------------------------
# M5: corrupted CAS accepted (home tamper + bench verifier)
# ---------------------------------------------------------------------------

def _m5_inject(rig: BenchRig) -> str:
    import hashlib as _hashlib

    assert rig.stub is not None
    StubPlan().answer("M5-STORE-PROBE").apply(rig.stub)
    rig.run_case(Case("m5-setup", "setup", run=lambda ctx: ctx.chat("Store M5-STORE-PROBE for later.", chat_id="m5-setup")))
    root = rig.cas_chunk_root()
    chunk_files = [p for p in sorted(root.rglob("*")) if p.is_file()] if root.is_dir() else []
    if not chunk_files:
        # No served turn stored a CAS blob at this SHA (the corpus case
        # records that same truth). The control still proves the bench's
        # detector over the product's REAL store: plant one valid chunk in
        # the product's own content-addressed layout, then corrupt it.
        body = b"SR-BENCH-M5 planted cas chunk: M5-STORE-PROBE"
        name = _hashlib.sha256(body).hexdigest()
        planted = root / name[:2] / name[2:4] / name
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_bytes(body)
        chunk_files = [planted]
    victim = chunk_files[0]
    victim.write_bytes(b"SR-BENCH-M5-CORRUPTED" + victim.read_bytes())
    mismatches = rig.cas_verify_all()
    if not mismatches:
        raise RuntimeError("m5: corrupted chunk was NOT detected by the bench verifier (insensitive bench)")
    StubPlan().answer("CAS-PROBE-1177").apply(rig.stub)
    return f"chunk {victim.name[:16]}… corrupted in place; bench verifier saw {len(mismatches)} mismatch(es)"


# ---------------------------------------------------------------------------
# M6: false receipt verification (home tamper)
# ---------------------------------------------------------------------------

def _m6_inject(rig: BenchRig) -> str:
    assert rig.stub is not None
    StubPlan().answer("M6-SETUP-TURN").apply(rig.stub)
    setup = rig.run_case(
        Case("m6-setup", "setup", run=lambda ctx: ctx.chat("Say M6-SETUP-TURN", chat_id="corr-1")
        ))
    if not setup.identities:
        raise RuntimeError("m6: setup turn produced no identity")
    session = setup.identities[0].session_id or ""
    import hashlib

    ledger = rig.home / "data" / "honesty_receipts" / f"{hashlib.sha256(session.encode()).hexdigest()[:24]}.jsonl"
    if not ledger.is_file():
        raise RuntimeError(f"m6: receipt ledger not found at {ledger}")
    lines = ledger.read_text(encoding="utf-8").splitlines()
    tampered = []
    for line in lines:
        row = json.loads(line)
        row["content_hash"] = "f" * 64  # a false hash: re-verification must fail
        tampered.append(json.dumps(row, sort_keys=True))
    ledger.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    StubPlan().answer("CORRESPOND-5514").apply(rig.stub)
    return f"receipt ledger for session {session[:24]}… rewritten with false content hashes"


# ---------------------------------------------------------------------------
# control driver
# ---------------------------------------------------------------------------

def _expect_case_failed(case_result: CaseResult) -> tuple[bool, str]:
    detected = case_result.verdict == VERDICT_FAIL
    why = (
        f"target case {case_result.case_id} FAILED: {case_result.failure_class}: {case_result.reason[:160]}"
        if detected
        else f"target case {case_result.case_id} came back {case_result.verdict} — MISSED detection"
    )
    return detected, why


def _expect_cas_detection(case_result: CaseResult) -> tuple[bool, str]:
    """For the corrupted-cas case, PASSING *with its detection assertion
    fired* IS the detection (the case is the detector)."""
    fired = any(
        row.get("name") == "bench_detects_cas_corruption" and row.get("ok") is True
        for row in case_result.assertions
    )
    why = (
        "corrupted-cas case flagged the corrupted chunk (bench_detects_cas_corruption ok)"
        if fired
        else f"corrupted-cas case never flagged corruption (verdict={case_result.verdict})"
    )
    return fired, why


MUTATION_CONTROLS: list[MutationControl] = [
    MutationControl("m1-dropped-request-unit", "a demanded unit dropped by the responder", "stub", "mixed-demands", _m1_inject),
    MutationControl("m2-bypassed-permission", "write executes with the permission gate bypassed", "app-patch", "denied-write", _m2_inject),
    MutationControl("m3-fabricated-tool-success", "receipts claim a tool that never ran", "app-patch", "evidence-receipt-correspondence", _m3_inject),
    MutationControl("m4-wrong-turn-evidence", "another turn's evidence rebound to this session", "home-tamper", "simultaneous-chat-isolation", _m4_inject),
    MutationControl("m5-corrupted-cas-accepted", "a corrupted CAS chunk served as good", "home-tamper", "corrupted-cas", _m5_inject, expect=_expect_cas_detection),
    MutationControl("m6-false-receipt-verification", "tampered receipts still verify as truth", "home-tamper", "evidence-receipt-correspondence", _m6_inject),
]


def run_mutation_controls(parent_rig: BenchRig, *, timeout_s: float = 300.0) -> list[CaseResult]:
    """Run every mutation control on its own isolated rig.

    PASS means the bench DETECTED the injected defect (target case failed or
    the required flag fired). FAIL means a missed detection.
    """
    results: list[CaseResult] = []
    for control in MUTATION_CONTROLS:
        result = CaseResult(
            case_id=f"mutation:{control.mutation_id}",
            title=f"bench detects {control.title}",
            required=True,
            verdict=VERDICT_PASS,
            started_at=time.time(),
        )
        rig = BenchRig(
            repo=parent_rig.repo,
            sha=parent_rig.sha,
            run_dir=parent_rig.run_dir / MUTATIONS_RUN_DIR_NAME / control.mutation_id,
            python_bin=parent_rig.python_bin,
        )
        detected = False
        detail = ""
        try:
            try:
                rig.setup()
                injected = control.apply(rig)
                detail = f"injected: {injected}"
                target = _case_by_id(control.target_case_id)
                case_result = rig.run_case(target)
                for row in case_result.assertions:
                    result.assertions.append(row)
                result.identities = case_result.identities
                detected, why = (control.expect or _expect_case_failed)(case_result)
                detail = f"{detail} | {why}"
            finally:
                rig.teardown()
        except Exception as exc:  # noqa: BLE001 - control failure reporting
            detected = False
            detail += f" | control error: {type(exc).__name__}: {exc}"[:1000]
        result.verdict = VERDICT_PASS if detected else VERDICT_FAIL
        result.failure_class = None if detected else "HARNESS"
        result.reason = detail[:500]
        result.duration_s = time.time() - result.started_at
        results.append(result)
    return results
