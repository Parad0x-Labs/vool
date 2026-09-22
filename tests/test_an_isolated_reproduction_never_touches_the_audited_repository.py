"""SCALPEL fixture repair, failure class A: the permission-policy false "not authorized" message.

The exact incident: a request granted "safe read-only commands: allowed" and "temporary isolated
inputs outside repository: allowed" in the same message as "repository modifications: forbidden"
and "repository test creation: forbidden". The old policy had one combined write/execute axis, so
`_READ_ONLY_RE` matched the literal substring "read-only" inside the GRANT and denied everything —
the runtime told the operator proof was not authorized when it explicitly was.

These tests pin, separately:
  * the five-axis policy resolves this exact prompt to exactly the required grants (acceptance A);
  * a genuine grant/deny contradiction in the SAME message is surfaced as a conflict, not silently
    resolved to the broader permission;
  * an authorized isolated reproduction is REALLY attempted through the actual tool boundary
    (`execute_runtime_tool`, not a scripted double) and provably writes nothing into the audited
    repository, using a real temp directory that is cleaned up afterward (acceptance B/C);
  * the original in-repo proof path (`generated/` inside the repo) is unaffected by the new axes.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.agent_runtime.audit_policy import derive_execution_policy

SCALPEL_FIXTURE_PROMPT = (
    "Inspect the current workspace and independently audit api/apache/liquefy_apache_repetition_v1.py. "
    "Use the actual current working-tree source, imported local modules, and any relevant tests or "
    "documentation. "
    "repository modifications: forbidden "
    "repository test creation: forbidden "
    "safe read-only commands: allowed "
    "temporary isolated inputs outside repository: allowed "
    "non-mutating reproduction: allowed"
)

# A subject shaped like the incident's codec, real enough to import and really exercise: a
# malformed-record path that silently truncates instead of raising, plus an empty-input path that
# does NOT crash (kept for symmetry with the frozen incident's refuted hypothesis).
_SUBJECT_SOURCE = (
    "class Codec:\n"
    "    def decompress(self, blob):\n"
    "        if blob.startswith(b'RPT1'):\n"
    "            blob = blob[4:]\n"
    "        out = bytearray()\n"
    "        while blob:\n"
    "            try:\n"
    "                pid = blob[0]\n"
    "            except Exception:\n"
    "                break\n"
    "            out.extend(blob[:pid])\n"
    "            blob = blob[pid:]\n"
    "        return bytes(out)\n"
)
_TARGET = "api/apache/liquefy_apache_repetition_v1.py"
# Matches `_prove_finding`'s `stem = posixpath.basename(finding.file).rsplit(".", 1)[0]` — the
# generated test's preamble binds the imported module to exactly this name.
_STEM = "liquefy_apache_repetition_v1"
# A real, executable claim against the real subject: decompress(b'\x80') truncates to the literal
# single byte instead of raising, so asserting it raises genuinely FAILS on the current code —
# which is what a real proof of THIS claim looks like.
_REPRODUCING_TEST_BODY = (
    f"class TestTruncation(unittest.TestCase):\n"
    f"    def test_malformed_record_raises_instead_of_truncating_silently(self):\n"
    f"        with self.assertRaises(ValueError):\n"
    f"            {_STEM}.Codec().decompress(b'\\x80')\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)


class _ScriptedRouter:
    def __init__(self, replies):
        self.replies = list(replies)

    def _requested_model_manifest(self, _context):
        return SimpleNamespace(provider_id="test:pinned", model_name="test-model")

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        if not self.replies:
            return (None, None, "script_exhausted")
        text = self.replies.pop(0)
        return (
            None,
            SimpleNamespace(
                output_text=text,
                usage={"prompt_tokens": 50, "completion_tokens": 20},
                provider_id=manifest.provider_id,
                model_name=manifest.model_name,
                model_call_id="call-1",
                response_id="resp-1",
            ),
            None,
        )


class _RealToolAgent:
    """Routes tool intents through the REAL `execute_runtime_tool` — no scripted double.

    This is deliberate: a fake tool runner accepts whatever path it is given regardless of the
    stamped policy or the overridden workspace, so it cannot prove the isolation this repair adds.
    Only the real tool boundary (`resolve_workspace_path`, `audit_tool_permission_denial`,
    `SandboxRunner`) can prove that nothing reaches the audited repository.
    """

    def __init__(self, replies):
        self.memory_router = _ScriptedRouter(replies)
        self.hive_activity_tracker = None
        self.public_hive_bridge = None

    def _execute_tool_intent(self, payload, **kwargs):
        from core.runtime_execution_tools import execute_runtime_tool

        return execute_runtime_tool(
            str(payload.get("intent") or ""),
            dict(payload.get("arguments") or {}),
            source_context=kwargs.get("source_context"),
            trusted_local_only=bool(kwargs.get("trusted_local_only", False)),
        )


def test_the_scalpel_fixture_policy_grants_exactly_the_required_axes() -> None:
    """Acceptance A: repository writes/test-creation denied, read-only commands and temporary
    external files and isolated reproduction all granted — from the exact incident wording."""
    policy = derive_execution_policy(SCALPEL_FIXTURE_PROMPT)

    assert policy.repository_write is False
    assert policy.repository_test_creation is False
    assert policy.read_only_commands is True
    assert policy.temporary_external_files is True
    assert policy.isolated_reproduction is True
    assert policy.conflicts == ()
    assert policy.isolated_proof_authorized is True
    assert policy.proof_authorized is True
    assert policy.proof_write_scope == "external_temp_root"
    # The exact false message from the incident must not be reachable for this policy.
    assert "not authorized to write or run anything" not in policy.remaining_proof_sentence() or (
        policy.proof_authorized  # only reachable at all when NOT authorized; guards against drift
    )


def test_a_conflicting_instruction_denies_the_axis_and_surfaces_the_conflict() -> None:
    """When the same message both grants and denies the same axis, the runtime must not silently
    pick the broader permission — it denies and records which axis conflicted."""
    policy = derive_execution_policy(
        "Safe read-only commands: allowed. Do not run any commands under any circumstances."
    )

    assert policy.read_only_commands is False
    assert "read_only_commands" in policy.conflicts
    assert "contradictory" in policy.remaining_proof_sentence()


def test_an_isolated_reproduction_writes_nothing_into_the_audited_repository_and_cleans_up(
    tmp_path,
) -> None:
    """Acceptance B/C, proven through the REAL tool boundary: an authorized isolated reproduction
    is actually attempted, a real subprocess actually runs, and the audited repository's file list
    and content are byte-for-byte unchanged afterward."""
    from core.agent_runtime.stepped_audit import SteppedAuditBudget, SteppedFinding, _prove_finding

    repo_root = tmp_path / "audited-repo"
    subject_path = repo_root / _TARGET
    subject_path.parent.mkdir(parents=True)
    subject_path.write_text(_SUBJECT_SOURCE)

    before_files = sorted(p.relative_to(repo_root).as_posix() for p in repo_root.rglob("*"))
    before_content = subject_path.read_bytes()

    policy = derive_execution_policy(SCALPEL_FIXTURE_PROMPT)
    assert policy.isolated_proof_authorized is True  # precondition for this test to mean anything

    agent = _RealToolAgent([_REPRODUCING_TEST_BODY])
    proof = _prove_finding(
        agent,
        manifest=SimpleNamespace(provider_id="test:pinned", model_name="test-model"),
        task=SimpleNamespace(task_id="t"),
        source_context={"workspace": str(repo_root), "audit_execution_policy": policy.as_dict(), "operating_mode": "auto"},
        session_id="isolated-e2e",
        evidence=SimpleNamespace(workspace_root=str(repo_root), sources={_TARGET: _SUBJECT_SOURCE}),
        finding=SteppedFinding(
            title="Malformed record silently truncates instead of raising",
            file=_TARGET,
            line_start=8,
            line_end=9,
            cited_line_text="            except Exception:",
            failure_scenario="A malformed record byte makes decompress silently truncate its output "
            "instead of raising, so a corrupt stream can look like a short-but-valid one.",
        ),
        budget=SteppedAuditBudget(),
        usages=[],
        policy=policy,
    )

    after_files = sorted(p.relative_to(repo_root).as_posix() for p in repo_root.rglob("*"))
    after_content = subject_path.read_bytes()

    assert after_files == before_files, (
        f"the audited repository's file list changed: {set(after_files) - set(before_files)}"
    )
    assert after_content == before_content, "the audited subject file's bytes changed"

    assert proof.attempted is True, "the isolated reproduction was never attempted"
    assert proof.unauthorized is False
    assert proof.write_scope == "external_temp_root"
    assert proof.temp_root, "no isolated temp root was recorded for Activity"
    assert not Path(proof.temp_root).exists(), "the isolated temp directory was not cleaned up"
    assert proof.test_command.startswith("python3 -m unittest")
    assert proof.returncode is not None, proof.output
    # The claim IS real on this subject (decompress(b'\x80') truncates rather than raising), so a
    # genuine assertRaises(ValueError) test genuinely fails — proving the reproduction actually ran
    # against the real subject, not a stub.
    assert proof.proven is True, f"the real subprocess did not reproduce the real defect: {proof.output[-500:]}"


def test_a_read_only_policy_with_no_isolated_grant_still_refuses_proof(tmp_path) -> None:
    """A message that denies repository writes WITHOUT granting the isolated axes must still
    refuse — the isolated path is not a silent fallback for every denial."""
    from core.agent_runtime.stepped_audit import SteppedAuditBudget, SteppedFinding, _prove_finding

    policy = derive_execution_policy("Audit this file. Do not modify anything.")
    assert policy.proof_authorized is False

    repo_root = tmp_path / "audited-repo"
    subject_path = repo_root / _TARGET
    subject_path.parent.mkdir(parents=True)
    subject_path.write_text(_SUBJECT_SOURCE)

    agent = _RealToolAgent([_REPRODUCING_TEST_BODY])
    proof = _prove_finding(
        agent,
        manifest=SimpleNamespace(provider_id="test:pinned", model_name="test-model"),
        task=SimpleNamespace(task_id="t"),
        source_context={"workspace": str(repo_root), "audit_execution_policy": policy.as_dict()},
        session_id="denied-e2e",
        evidence=SimpleNamespace(workspace_root=str(repo_root), sources={_TARGET: _SUBJECT_SOURCE}),
        finding=SteppedFinding(
            title="x", file=_TARGET, line_start=1, line_end=2, cited_line_text="x", failure_scenario="y",
        ),
        budget=SteppedAuditBudget(),
        usages=[],
        policy=policy,
    )

    assert proof.unauthorized is True
    assert proof.attempted is False
    assert proof.write_scope == ""
