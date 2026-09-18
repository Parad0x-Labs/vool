"""The six logs.* commands through the ONE execution seam: envelopes, typed
faults, operator gates, availability probes, registry lint, and the CLI + HTTP
projections of the same commands (no second dispatch path anywhere)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.command_registry.execute import ExecutionContext, execute_command
from tests.logs_operator.conftest import journal_entries, write_events


def _store():
    from core.liquefy import hooks

    return hooks.get_default_store()


@pytest.fixture()
def projected(seeded):
    write_events(_store(), journal_entries())
    return seeded["store"]


# ------------------------------------------------------------------ envelopes


def test_all_six_commands_are_registered_and_lint_clean():
    from core.command_registry.check import check_registry
    from core.command_registry.registry import registry

    reg = registry()
    for command_id in (
        "logs.health", "logs.search", "logs.event", "logs.verify", "logs.rebuild", "logs.restore",
    ):
        spec = reg.lookup(command_id)
        assert spec is not None, command_id
        assert spec.description
    report = check_registry(reg)
    assert report.ok, report.findings


def test_search_envelope_carries_the_truth_fields(projected):
    envelope = execute_command("logs.search", {"turn_id": "turn-beta", "limit": 10})
    assert envelope.ok and envelope.execution.exit_code == 0
    data = envelope.data
    for field in ("query", "time_bounds", "limit", "returned", "truncated", "scanned_events", "verification", "events"):
        assert field in data, field
    assert data["returned"] == 3 and data["truncated"] is False
    assert data["events"][0]["tier"] in ("hot", "cold") and data["events"][0]["seq"]
    assert "eff-push-02" in envelope.summary, "the summary must name exact recovered events"


def test_event_envelope_states_tier_and_verification(projected):
    envelope = execute_command("logs.event", {"event_id": "gap-shell-03"})
    assert envelope.ok
    assert envelope.data["tier"] and envelope.data["event"]["event_id"] == "gap-shell-03"
    missing = execute_command("logs.event", {"event_id": "eff-never-was"})
    assert not missing.ok and missing.fault.code == "fault_validation"
    assert missing.fault.detail.get("not_found") is True
    empty = execute_command("logs.event", {})
    assert not empty.ok, "a reference-less retrieval must be a usage fault"


def test_health_envelope_publishes_sink_and_journal_state(projected):
    envelope = execute_command("logs.health", {"deep": False})
    assert envelope.ok
    data = envelope.data
    assert data["authority"]["journal_chain"]["ok"] is True
    assert "sink" in data and "failure_count" in data["sink"]
    assert "projection failures are counted" in data["sink"]["note"]


def test_verify_envelope_and_typed_corruption_fault(projected):
    ok = execute_command("logs.verify", {"deep": True})
    assert ok.ok and ok.data["verification"]["status"] == "verified"
    store = _store()
    sealed_segment = store.segments()
    if not sealed_segment:
        store.seal()
        sealed_segment = store.segments()
    segment_id = sealed_segment[0]["segment_id"]
    path = store.root / "segments" / f"{segment_id}.lsegh"
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
    broken = execute_command("logs.verify", {"segment_id": segment_id, "deep": True})
    assert not broken.ok and broken.fault.code == "fault_validation"
    assert broken.fault.detail.get("fail_closed") is True
    narrowed = execute_command("logs.search", {"text": "eff-write-01", "limit": 5})
    assert not narrowed.ok, "search must fail CLOSED over corruption, not serve a partial answer"


def test_rebuild_and_restore_require_the_operator_principal(seeded):
    model = ExecutionContext(projection="chat", principal="model")
    refused_rebuild = execute_command("logs.rebuild", {}, context=model)
    assert not refused_rebuild.ok and refused_rebuild.fault.code == "permission_denied"
    refused_restore = execute_command("logs.restore", {}, context=model)
    assert not refused_restore.ok and refused_restore.fault.code == "permission_denied"
    # the model offer vocabulary excludes the operator-only pair
    from core.command_registry.model_tools import tool_intent_for
    from core.command_registry.registry import registry

    model_offerable = {spec.command_id for spec in registry().commands() if spec.model_offerable}
    assert {"logs.health", "logs.search", "logs.event", "logs.verify"} <= model_offerable
    assert "logs.rebuild" not in model_offerable and "logs.restore" not in model_offerable
    assert tool_intent_for("logs.search") == "operator.command.logs.search"


def test_rebuild_envelope_reports_counts_and_receipt(seeded):
    envelope = execute_command("logs.rebuild", {"batch_size": 3})
    assert envelope.ok
    assert envelope.data["appended"] == len(journal_entries())
    assert envelope.data["journal_unchanged_witness"] is True
    assert envelope.receipts and envelope.receipts[0]["kind"] == "liquefy_rebuild"
    again = execute_command("logs.rebuild", {})
    assert again.ok and again.data["appended"] == 0
    assert "skipped" in again.summary


def test_restore_envelope_reports_the_export_digest(seeded):
    execute_command("logs.rebuild", {})
    envelope = execute_command("logs.restore", {})
    assert envelope.ok
    assert envelope.data["export"]["sha256"] and envelope.data["export"]["events"] > 0
    assert envelope.receipts and envelope.receipts[0]["kind"] == "liquefy_restore"


def test_unknown_input_key_is_a_usage_fault(projected):
    envelope = execute_command("logs.search", {"raw_sql": "SELECT * FROM events"})
    assert not envelope.ok and envelope.fault.code == "usage"
    assert "unknown input keys" in envelope.fault.detail["reason"]


def test_availability_probe_refuses_when_the_lane_is_disabled(monkeypatch, seeded):
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS", "0")
    from core.liquefy import hooks

    hooks.reset_default_store()
    envelope = execute_command("logs.search", {"limit": 5})
    assert not envelope.ok and envelope.fault.code == "unavailable"
    hooks.reset_default_store()


# ------------------------------------------------------------------ projections


def test_cli_projection_runs_the_same_seam(projected, tmp_path):
    env = dict(os.environ)
    env.update(
        {
            "VOOL_HOME": str(tmp_path / "cli-home"),
            "VOOL_LIQUEFY_LOGS": "1",
            "VOOL_LIQUEFY_LOGS_HOME": os.environ["VOOL_LIQUEFY_LOGS_HOME"],
            "VOOL_BLACKBOX_DIR": os.environ["VOOL_BLACKBOX_DIR"],
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": "logs-operator-test",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    repo_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-c",
         "import sys; from core.command_registry.cli import main; sys.exit(main(sys.argv[1:]))",
         "logs.search", "--json", "--turn_id", "turn-beta", "--limit", "10"],
        cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    envelope = json.loads(completed.stdout)
    assert envelope["ok"] is True
    assert envelope["data"]["returned"] == 3
    assert [row["event_id"] for row in envelope["data"]["events"]].count("eff-push-02") == 2


def test_http_projection_serves_the_same_envelope(projected):
    """The real HTTP door: POST /api/commands/dispatch → the same execute seam."""
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices
    from tests.asgi_harness import asgi_request

    app = create_app(RuntimeServices(display_name="VOOL"))
    status, _headers, body = asgi_request(
        app,
        method="POST",
        path="/api/commands/dispatch",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"command_id": "logs.search", "input": {"outcome": "failed", "limit": 10}}).encode(),
    )
    assert status == 200, body[:400]
    payload = json.loads(body)
    assert payload["ok"] is True
    assert [row["event_id"] for row in payload["data"]["events"]] == ["eff-push-02"]

    status, _headers, body = asgi_request(
        app, method="GET", path="/api/commands", headers={"Accept": "application/json"}
    )
    assert status == 200
    surfaces = body.decode()
    assert "logs.search" in surfaces and "logs.rebuild" in surfaces

    status, _headers, body = asgi_request(
        app,
        method="POST",
        path="/api/commands/dispatch",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"command_id": "logs.search", "input": {"raw_sql": "SELECT 1"}}).encode(),
    )
    assert status == 400  # usage faults map to HTTP 400; no raw query text is ever accepted
    usage = json.loads(body)
    assert usage["fault"]["code"] == "usage"

    # The API door is the operator projection (owner-local transport): restore
    # executes here, and the model lane is refused at the gate (covered in-process).
    status, _headers, body = asgi_request(
        app,
        method="POST",
        path="/api/commands/dispatch",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"command_id": "logs.restore", "input": {}}).encode(),
    )
    assert status == 200
    assert json.loads(body)["data"]["verification"]["status"] == "verified"
