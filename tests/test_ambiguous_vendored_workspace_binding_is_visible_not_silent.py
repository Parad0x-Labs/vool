"""SCALPEL fixture repair, fixture/checkout safety, 2026-08-06.

The exact discrepancy found while mapping this fixture: TWO real, differently-behaved copies of
`api/apache/liquefy_apache_repetition_v1.py` existed reachable from the same machine -- one bundled
inside `vool-local-product/.venv/lib/python3.11/site-packages/`, still carrying the original
`except: break` truncation defect, and one in the intended sibling workspace, already fixed. Nothing
in the runtime would have noticed if a workspace's own file inventory happened to contain both.

These tests pin two things: (1) every production fixture record carries the resolved workspace
root, resolved absolute target path, target file hash, and both checkout SHAs; (2) when the
workspace's own inventory contains a vendored/dependency path colliding on basename with a real
source path, the audit refuses to silently pick one -- it returns an explicit, visible BLOCKED
decision naming the collision, never a guess.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

from core.agent_runtime.continuity_gate import is_vendored_dependency_path, vendored_path_collisions
from core.agent_runtime.stepped_audit import run_stepped_audit

TARGET = "api/apache/liquefy_apache_repetition_v1.py"
CODEC = (
    "class Codec:\n"
    "    def decompress(self, blob):\n"
    "        if blob.startswith(b'RPT1'):\n"
    "            blob = blob[4:]\n"
    "        out = bytearray()\n"
    "        return bytes(out)\n"
)
MANIFEST = SimpleNamespace(provider_id="test:pinned", model_name="test-model")


class _ScriptedRouter:
    def __init__(self, replies):
        self.replies = list(replies)

    def _requested_model_manifest(self, _context):
        return MANIFEST

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        if not self.replies:
            return (None, None, "script_exhausted")
        text = self.replies.pop(0)
        return (
            None,
            SimpleNamespace(
                output_text=text, usage={"prompt_tokens": 10, "completion_tokens": 5},
                provider_id=manifest.provider_id, model_name=manifest.model_name,
                model_call_id="call-1", response_id="resp-1",
            ),
            None,
        )


def _drive(*, all_paths, session_id):
    agent = SimpleNamespace(
        memory_router=_ScriptedRouter([]),
        _execute_tool_intent=lambda *a, **k: None,
        hive_activity_tracker=None,
        public_hive_bridge=None,
    )
    context = {
        "workspace_audit_evidence_collected": True,
        "workspace_audit_evidence": {
            "all_paths": all_paths,
            "inspected_paths": (TARGET,),
            "sources": {TARGET: CODEC},
            "workspace_root": "/tmp/vendor-collision-ws",
            "incomplete_files": (),
        },
        "requested_model": MANIFEST.model_name,
    }
    return run_stepped_audit(
        agent, task=SimpleNamespace(task_id="task-vendor"),
        effective_input="Audit " + TARGET + " for the single highest-risk bug. Do not modify anything.",
        source_context=context, session_id=session_id,
    )


def test_is_vendored_dependency_path_detects_the_real_incident_shape() -> None:
    assert is_vendored_dependency_path(
        ".venv/lib/python3.11/site-packages/api/apache/liquefy_apache_repetition_v1.py"
    )
    assert not is_vendored_dependency_path(TARGET)


def test_vendored_path_collisions_pairs_the_two_real_copies() -> None:
    collisions = vendored_path_collisions((
        ".venv/lib/python3.11/site-packages/api/apache/liquefy_apache_repetition_v1.py",
        TARGET,
        "README.md",
    ))
    assert len(collisions) == 1
    vendored, real = collisions[0]
    assert "site-packages" in vendored
    assert vendored != real
    assert real == TARGET


def test_an_ambiguous_inventory_returns_an_explicit_blocked_decision_not_a_silent_pick() -> None:
    """Acceptance: the audit must FAIL VISIBLY (a labelled BLOCKED decision naming the collision)
    rather than silently resolving to either copy -- this is the test the fixture-safety
    requirement explicitly demands."""
    decision = _drive(
        all_paths=(
            TARGET,
            ".venv/lib/python3.11/site-packages/api/apache/liquefy_apache_repetition_v1.py",
        ),
        session_id="vendor-collision-1",
    )

    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    assert stepped.get("terminal_state") == "blocked", stepped.get("terminal_state")
    report = str(decision.output_text).lower()
    assert "vendored" in report or "site-packages" in stepped.get("blocked_reason", "").lower() or (
        "vendored" in stepped.get("blocked_reason", "").lower()
    )
    # No model call was ever made -- the gate fires before the first nomination.
    assert stepped.get("model_calls", 0) == 0


def test_an_unambiguous_inventory_proceeds_normally() -> None:
    """Control: a workspace with no vendored/real collision must not be affected by this check at
    all -- confirms the gate is scoped to the genuine ambiguity, not a blanket refusal."""
    decision = _drive(all_paths=(TARGET, "README.md"), session_id="vendor-collision-control")

    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    assert stepped.get("terminal_state") != "blocked" or "vendored" not in str(
        stepped.get("blocked_reason", "")
    ).lower()


def test_every_fixture_record_carries_resolved_identity_and_both_checkout_shas() -> None:
    decision = _drive(all_paths=(TARGET, "README.md"), session_id="vendor-collision-identity")
    stepped = dict(dict(getattr(decision, "details", None) or {}).get("stepped_audit") or {})
    identity = stepped.get("fixture_identity") or {}

    assert identity.get("resolved_workspace_root") == "/tmp/vendor-collision-ws"
    assert identity.get("resolved_target_absolute_path", "").endswith(TARGET)
    assert identity.get("target_file_sha256") == hashlib.sha256(CODEC.encode("utf-8")).hexdigest()
    # Both SHA fields must exist as keys even when git resolution fails for a fake /tmp path (an
    # honest empty string, never an absent key or a fabricated value).
    assert "source_checkout_sha" in identity
    assert "daemon_checkout_sha" in identity
    # THIS repo really is a git checkout -- the daemon SHA (this running code's own checkout) must
    # resolve to a real, non-empty short SHA.
    assert identity["daemon_checkout_sha"], "the daemon's own checkout SHA failed to resolve"
    assert len(identity["daemon_checkout_sha"]) >= 7
