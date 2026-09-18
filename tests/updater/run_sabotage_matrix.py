"""Sabotage matrix for the signed-atomic-updater invariants (CLAUDE.md §6b.4).

For each load-bearing enforcement, this runner BREAKS THE SOURCE, runs the named
tests, and requires them to FAIL (a sabotage that survives means the guard is dead
or the test is vacuous), then restores the file via git and cmp-verifies the restore
against the committed tree.

Usage (run from the repo root, clean tree):
    /path/to/python tests/updater/run_sabotage_matrix.py

Exit 0 iff every sabotage reddened at least one named test and every restore was
byte-exact. The pinned interpreter is /tmp/vool-venv312/bin/python in this campaign.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Each sabotage is an exact single-occurrence source replacement + the tests whose
# failure PROVES the guard is load-bearing.
SABOTAGES: list[Sabotage] = []


@dataclass
class Sabotage:
    ident: str
    description: str
    file: Path
    old: str
    new: str
    tests: list[str] = field(default_factory=list)
    all_occurrences: bool = False

    def register(self) -> Sabotage:
        SABOTAGES.append(self)
        return self


Sabotage(
    ident="S1-manifest-signature",
    description="manifest Ed25519 verification disabled (every forged manifest verifies)",
    file=Path("core/updater/manifest.py"),
    old="if not verify_ed25519(trust.public_key_hex(key_id), canonical_manifest_bytes(data), str(signature.get(\"sig\"))):",
    new="if False:",
    tests=[
        "tests/updater/test_trust_manifest.py::TestManifestVerification::test_tampered_body_fails_signature",
        "tests/updater/test_trust_manifest.py::TestManifestVerification::test_tampered_artifact_entry_fails_signature",
    ],
).register()

Sabotage(
    ident="S2-artifact-signature",
    description="artifact Ed25519 verification disabled (tampered bytes pass)",
    file=Path("core/updater/download.py"),
    old="    if not verify_artifact_bytes(blob, artifact, public_key_hex):\n        return DownloadResult(False, DownloadReason.ARTIFACT_SIGNATURE_INVALID)",
    new="    if False:\n        return DownloadResult(False, DownloadReason.ARTIFACT_SIGNATURE_INVALID)",
    tests=[
        "tests/updater/test_download_resume.py::TestVerificationFailures::test_invalid_artifact_signature_refused",
    ],
).register()

Sabotage(
    ident="S3-rollback",
    description="automatic rollback disabled (failures abort instead of restoring)",
    file=Path("core/updater/transaction.py"),
    old="    def _fail_and_rollback(\n        self, detail: str, fault: UpdateFault = UpdateFault.UNEXPECTED\n    ) -> FlowResult:\n        result = self.rollback_active(detail=detail, fault=fault)\n        return result",
    new="    def _fail_and_rollback(\n        self, detail: str, fault: UpdateFault = UpdateFault.UNEXPECTED\n    ) -> FlowResult:\n        result = self._abort(detail, fault=fault)\n        return result",
    tests=[
        "tests/updater/test_transaction_flow.py::TestRollback::test_failed_health_check_rolls_back_and_restarts_prior",
        "tests/updater/test_transaction_flow.py::TestRollback::test_failed_migration_rolls_everything_back",
    ],
).register()

Sabotage(
    ident="S4-replay-highwater",
    description="per-channel sequence high-water ignored (replayed/rolled-back manifests accepted)",
    file=Path("core/updater/decision.py"),
    old="    seen_sequence = int(high_water.get(\"sequence\") or 0)\n    if manifest.sequence == seen_sequence:",
    new="    seen_sequence = -1\n    if manifest.sequence == seen_sequence:",
    tests=[
        "tests/updater/test_decision.py::TestReplayAndRollback::test_older_sequence_is_a_manifest_rollback",
        "tests/updater/test_decision.py::TestReplayAndRollback::test_equal_sequence_is_a_replay",
    ],
).register()

Sabotage(
    ident="S5-codesign-gate",
    description="macOS codesign gate skipped (unsigned/tampered bundles accepted)",
    file=Path("core/updater/macos.py"),
    old="        codesign = self._run([\"codesign\", \"--verify\", \"--deep\", \"--strict\", str(bundle)])\n        if codesign.returncode != 0:",
    new="        codesign = self._run([\"codesign\", \"--verify\", \"--deep\", \"--strict\", str(bundle)])\n        if False:",
    tests=[
        "tests/updater/test_platforms_macos.py::TestCodeSignVerificationFake::test_codesign_failure_refuses",
        "tests/updater/test_platforms_macos.py::TestRealCodeSign::test_tampered_bundle_fails_codesign",
    ],
).register()

Sabotage(
    ident="S6-downgrade-guard",
    description="downgrade guard removed (older versions install without authorization)",
    file=Path("core/updater/decision.py"),
    old="    if candidate_semver < installed_semver:\n        if downgrade_authorization is None or not downgrade_authorization.covers(manifest.version):\n            return outcome(UpdateDecisionReason.DOWNGRADE_REFUSED)",
    new="    if candidate_semver < installed_semver:\n        if False:\n            return outcome(UpdateDecisionReason.DOWNGRADE_REFUSED)",
    tests=[
        "tests/updater/test_decision.py::TestDowngrade::test_downgrade_is_refused_without_authorization",
    ],
).register()

Sabotage(
    ident="S7-destructive-gate",
    description="destructive-work restart guard dropped",
    file=Path("core/updater/work.py"),
    old="    if destructive and (resolution is None or not resolution.covers(destructive)):",
    new="    if False:",
    all_occurrences=True,  # breaks BOTH the sync refusal predicate and the authoritative flow gate
    tests=[
        "tests/updater/test_work_pause.py::TestPauseGate::test_destructive_work_blocks_restart",
        "tests/updater/test_transaction_flow.py::TestDestructiveWork::test_destructive_work_blocks_the_install",
    ],
).register()

Sabotage(
    ident="S8-notarization-posture",
    description="production notarization requirement flipped off by default",
    file=Path("core/updater/macos.py"),
    old="        default_require_notarization: bool = True,",
    new="        default_require_notarization: bool = False,",
    tests=[
        "tests/updater/test_platforms_macos.py::TestRealCodeSign::test_production_default_refuses_un_notarized_bundle",
        "tests/updater/test_platforms_macos.py::TestCodeSignVerificationFake::test_default_posture_requires_notarization",
    ],
).register()

Sabotage(
    ident="S9-health-identity",
    description="exact-installed-SHA gate disabled (any 2xx body finalizes the update)",
    file=Path("core/updater/health.py"),
    old="            if expected_version:\n                ok = ok and version == str(expected_version)",
    new="            if False:\n                ok = ok and version == str(expected_version)",
    tests=[
        "tests/updater/test_final_p1.py::TestExactShaGate::test_wrong_pinned_sha_rolls_back_with_typed_fault",
        "tests/updater/test_final_p1.py::TestCurrentHealthSchema::test_default_probe_parses_a_real_healthz_shape_over_http",
    ],
).register()

Sabotage(
    ident="S10-channel-gate",
    description="channel match ignored (a preview manifest installs on a stable app)",
    file=Path("core/updater/decision.py"),
    old="    if manifest.channel != channel:\n        return outcome(UpdateDecisionReason.CHANNEL_MISMATCH)",
    new="    if False:\n        return outcome(UpdateDecisionReason.CHANNEL_MISMATCH)",
    tests=[
        "tests/updater/test_final_p1.py::TestChannelsExplicit::test_cross_channel_offer_is_refused_by_the_decision",
        "tests/updater/test_decision.py::TestChannelAndPlatform::test_channel_mismatch_refused",
    ],
).register()

Sabotage(
    ident="S11-short-read-discard",
    description="clean short read reclassified as corrupt (partial deleted, resume destroyed)",
    file=Path("core/updater/download.py"),
    old="                else:\n                    # The connection closed CLEANLY before the declared size (a FIN, not",
    new="                elif False:\n                    # The connection closed CLEANLY before the declared size (a FIN, not",
    tests=[
        "tests/updater/test_download_resume.py::TestInterruptionAndResume::test_clean_short_read_keeps_the_partial_resumable",
    ],
).register()


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False)


def _pytest(tests: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-x", *tests],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        check=False,
    )


def _collects(tests: list[str]) -> tuple[bool, str]:
    """Whether every named node id resolves to at least one test.

    THIS IS WHY THE CHECK EXISTS. `reddened` below is `returncode != 0`, and pytest exits
    non-zero for a node id that matches nothing ("ERROR: not found: ... no match in any of").
    So a row naming a test that has been renamed, moved between classes, or deleted reports
    RED -- "guard load-bearing" -- while the guard was never executed. Measured on this very
    matrix: the S11 row named
    ``test_download_resume.py::TestDownload::test_clean_short_read_keeps_the_partial_resumable``
    and no ``TestDownload`` class exists in that file; the method lives on
    ``TestInterruptionAndResume``. The row passed, having proved nothing.

    A matrix whose purpose is to catch vacuous guards must not itself contain vacuous rows.
    """
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "--collect-only", *tests],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        check=False,
    )
    if done.returncode != 0:
        return False, (done.stdout + done.stderr).strip().splitlines()[-1:][0] if (
            done.stdout or done.stderr
        ) else "collection failed"
    return True, ""


def run() -> int:
    if _git("diff", "--quiet").returncode != 0 or _git("diff", "--cached", "--quiet").returncode != 0:
        print("REFUSING: working tree is not clean (sabotage restores must be cmp-verifiable).")
        return 2

    failures = 0
    for sabotage in SABOTAGES:
        target = REPO / sabotage.file
        original = target.read_text(encoding="utf-8")
        found = original.count(sabotage.old)
        if found < 1 or (found != 1 and not sabotage.all_occurrences):
            print(f"[{sabotage.ident}] SKIPPED/FAILED: anchor not found exactly once in {sabotage.file}")
            failures += 1
            continue

        collected, why = _collects(sabotage.tests)
        if not collected:
            print(f"[{sabotage.ident}] BROKEN ROW: named tests do not collect -- {why}")
            failures += 1
            continue
        control = _pytest(sabotage.tests)
        if control.returncode != 0:
            # A test already red on the pristine tree turns every mutation into a false RED.
            print(f"[{sabotage.ident}] BROKEN ROW: control is not green before the mutation")
            failures += 1
            continue

        target.write_text(original.replace(sabotage.old, sabotage.new), encoding="utf-8")
        try:
            result = _pytest(sabotage.tests)
            reddened = result.returncode != 0
            status = "RED (guard load-bearing)" if reddened else "SURVIVED (vacuous guard or dead test)"
            print(f"[{sabotage.ident}] {status}")
            if not reddened:
                failures += 1
        finally:
            _git("checkout", "--", str(sabotage.file))
        if _git("diff", "--quiet").returncode != 0:
            print(f"[{sabotage.ident}] RESTORE FAILED — tree still dirty")
            failures += 1

    print(f"\n{len(SABOTAGES)} sabotages, {failures} problem(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
