"""Representative error journeys, end to end, through the real seams -- deterministic injection only.

The delivery goal's validation standard: demonstrate representative errors end to end through
served runtime records; verify recovery actions do not bypass permission or spending authority;
verify reporting redaction preserves useful meaning. No live spending, no network -- every
failure is injected at its owning seam and followed through classification -> record -> surface
wording -> exported (redacted) evidence.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.faults.bug_export import fault_evidence_export
from core.faults.mapping import map_exception
from core.faults.recorder import faults_for_turn, record_fault


@pytest.fixture
def fault_home(tmp_path, monkeypatch):
    """An isolated store so recorded rows are exactly this test's own."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    yield tmp_path / "home"


def _recorded(turn: str):
    return faults_for_turn(turn)


# --- 1. an injected failure maps once, records durably, and reads back --------------------


def test_an_injected_timeout_maps_records_and_reads_back_with_its_contract(fault_home) -> None:
    record = map_exception(
        TimeoutError("simulated engine stall"), authority="tests.representative", turn_key="turn-e2e-1"
    )
    assert record.code == "timeout"
    assert record.effect == "uncertain", "a timeout may not promise nothing happened"
    record_fault(record)

    rows = _recorded("turn-e2e-1")
    assert [row.code for row in rows] == ["timeout"]
    assert rows[0].user_message == record.user_message
    assert rows[0].retry == "retry_now"


def test_a_wrapped_failure_cannot_be_reclassified_by_an_upstream_handler(fault_home) -> None:
    """The wrapper-drift law, end to end: an owner's classification survives re-wrapping."""
    from core.faults.mapping import FaultError

    owned = map_exception(
        PermissionError("simulated policy stop"), authority="tests.owner", turn_key="turn-e2e-2"
    )
    assert owned.code == "permission_denied"
    rewritten = map_exception(
        RuntimeError("turn failed") from FaultError(owned), authority="tests.upstream", turn_key="turn-e2e-2"
    )
    assert rewritten.code == "permission_denied", "an upstream wrapper re-classified an owned failure"


# --- 2. the named wording laws: what the user reads for the five repair classes -----------


def _execution_with(details: dict) -> SimpleNamespace:
    return SimpleNamespace(source="model_execution", details=details)


def test_a_missing_credential_is_worded_as_a_local_refusal_not_a_model_failure() -> None:
    from core.agent_runtime.memory_runtime import chat_surface_honest_degraded_response

    agent = SimpleNamespace()
    text = chat_surface_honest_degraded_response(
        agent,
        _execution_with(
            {
                "requested_model": "openrouter:some-model",
                "attempted": ["openrouter:some-model"],
                "attempt_details": [
                    {"provider": "openrouter", "error": "provider_credential_unavailable", "kind": "local refusal"}
                ],
            }
        ),
        user_input="hello",
    )
    assert "API key was not available" in text and "before sending" in text
    assert "nothing was charged" in text.lower()
    assert "could not be reached" not in text, "a local refusal must not read as a transport/model failure"


def test_a_pre_send_spend_refusal_names_the_cap_and_never_claims_provider_contact() -> None:
    from core.agent_runtime.memory_runtime import _selected_model_block_cause

    cause = _selected_model_block_cause("per_task_spend_cap_exceeded", model_was_attempted=False)
    assert "exceed your spend cap" in cause
    assert "provider" not in cause.lower(), "a pre-send refusal never implies the provider was contacted"


def test_an_unknown_paid_outcome_says_unknown_and_points_at_the_resume_path() -> None:
    from core.agent_runtime.memory_runtime import _paid_result_unknown_hint

    text = _paid_result_unknown_hint(
        _execution_with(
            {
                "requested_model": "usepod:some-model",
                "block_reason": "paid_result_unknown: the paid answer never arrived",
            }
        )
    )
    assert "result is unknown" in text
    assert "without paying again" in text, "the only supported retry is the non-paying Resume"
    assert "try again" not in text.lower().replace("send your message again", ""), text


def test_a_missing_workspace_tells_the_user_how_to_bind_one(tmp_path, monkeypatch) -> None:
    """The unbound-workspace 409 carries a stable reason code AND an actionable sentence naming
    the bind step (Projects -> New project / pick one). The sentence lives inline at the serving
    seam, so its contract is pinned both behaviorally (the reason code) and at the seam."""
    from pathlib import Path

    from core.context_namespace import authoritative_chat_workspace

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    session = {"chat_id": "e2e-chat", "project": None}
    _root, reason = authoritative_chat_workspace(session)
    assert reason in {"unbound", "missing_chat"}, reason

    service_source = (Path(__file__).resolve().parents[1] / "core" / "web" / "api" / "service.py").read_text(
        encoding="utf-8"
    )
    assert '"unbound": "bind this chat to a project folder first' in service_source, (
        "the unbound-workspace message lost its bind instruction"
    )


# --- 3. exported evidence: typed codes ride out, secrets and context never do --------------


def test_exported_fault_evidence_carries_codes_not_context(fault_home) -> None:
    record = map_exception(
        PermissionError("simulated stop"),
        authority="tests.representative",
        turn_key="turn-e2e-3",
        context={"api_key": "sk-super-secret-value", "path": "/Users/someone/secret.txt"},
    )
    record_fault(record)

    export = fault_evidence_export(turn_key="turn-e2e-3")
    blob = repr(export)
    assert export["fault_count"] == 1
    assert export["faults"][0]["code"] == "permission_denied"
    assert "sk-super-secret-value" not in blob, "a secret from fault context leaked into the export"
    assert "/Users/someone" not in blob, "a private path from fault context leaked into the export"
    assert export["faults"][0]["operator_action"], "the recovery action must survive redaction"


def test_bug_report_redaction_preserves_meaning_while_removing_identity() -> None:
    from core.bug_report.redaction import redact_for_report

    raw = (
        "Failed calling provider from /Users/alice/Projects/app for alice@example.com "
        "with token ghp_0123456789abcdef at 192.168.1.4: connection reset"
    )
    redacted = redact_for_report(raw)
    for secret in ("/Users/alice", "alice@example.com", "ghp_0123456789abcdef", "192.168.1.4"):
        assert secret not in redacted, f"{secret} survived redaction"
    for meaning in ("Failed calling provider", "connection reset"):
        assert meaning in redacted, f"redaction destroyed useful meaning: lost {meaning!r}"


# --- 4. recovery actions do not bypass authority -------------------------------------------


def test_recovery_wording_never_grants_or_spends() -> None:
    """The recovery texts of the spend/payment faults must instruct a HUMAN action (raise a cap,
    approve, check state) -- none may word the runtime into bypassing permission or spending
    authority on the user's behalf."""
    from core.faults.catalog import all_specs

    for spec in all_specs():
        action = spec.operator_action.lower()
        assert not any(
            phrase in action for phrase in ("automatically retries", "bypass", "force-send", "without approval")
        ), f"{spec.code}: operator action suggests an authority bypass: {spec.operator_action}"
        if spec.code.startswith("wallet_") and spec.effect == "uncertain":
            assert "check" in action, (
                f"{spec.code}: an uncertain-outcome fault must tell the operator to CHECK state first"
            )
