"""PB01 amendment — owned learning invariants, round 2.

Review findings these tests pin (LEARNING_LOOP_REVIEW_20260903 §5/§6):

* lesson identity: same class/intents/tool but DIFFERENT executable validation must not
  accumulate unrelated repairs toward one lesson; identity binds the validation command digest,
  the guidance and the policy version — mutation ids are witnesses, not identity;
* legacy v1 records must load as explicit unvalidated state, never silently-trusted reuse;
  unknown/invalid expiry must fail closed; current-version creation must not default promoted;
* store operations must be race-safe across processes (find-or-create, append, correction,
  delete) with bounded capacity; the evidence fallback identity must actually use the stored
  command digest; sufficiency append/reset must be cross-process safe;
* reuse feedback must be typed (successful / failed / cancelled / unvalidated), transport
  failure is never evidence against a lesson, model quality feedback is isolated per model and
  feedback version, and unknown cost stays unknown — never measured zero.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.learning import (
    LearningPolicy,
    ProcedureShardV1,
    delete_procedure,
    invalidate_procedure,
    list_procedure_records,
    load_procedure_shards,
    promote_verified_procedure,
    rank_reusable_procedures,
    record_procedure_reuse,
)
from core.learning.model_sufficiency import (
    list_sufficiency_observations,
    record_sufficiency_observation,
    reset_sufficiency_observations,
    sufficiency_adjustment,
)
from core.learning.procedure_shards import record_reuse_terminal

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def _evidence(mutation_id: str, command: str, *, session: str = "s") -> dict:
    return {
        "tool_receipts": [
            {"intent": "workspace.apply_unified_diff", "mutation_id": mutation_id, "paths": ["app.py"]},
            {"intent": "workspace.run_tests", "command": command, "returncode": 0},
        ],
        "validation": {"ok": True, "tool": "workspace.run_tests", "command": command, "returncode": 0},
    }


def _promote(mutation_id: str, command: str, *, session: str = "s", task_class: str = "debugging",
             steps=("apply diff", "run tests")):
    evidence = _evidence(mutation_id, command, session=session)
    return promote_verified_procedure(
        task_class=task_class,
        title=f"Patch and verify ({command})",
        preconditions=["workspace is writable"],
        steps=list(steps),
        tool_receipts=evidence["tool_receipts"],
        validation=evidence["validation"],
        rollback={"intent": "workspace.rollback_last_change"},
        session_id=session,
    )


class _StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name)
        patcher = mock.patch(
            "core.learning.procedure_shards.data_path",
            side_effect=lambda *parts: Path(self.store, *map(str, parts)),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self._sufficiency_patcher = mock.patch(
            "core.learning.model_sufficiency.data_path",
            side_effect=lambda *parts: Path(self.store, *map(str, parts)),
        )
        self._sufficiency_patcher.start()
        self.addCleanup(self._sufficiency_patcher.stop)

class LessonIdentityTests(_StoreTestCase):
    def test_unrelated_repairs_do_not_promote_one_another(self) -> None:
        """Same task class, same intents, same validation tool — but different executable
        validation (different command digests). These are DIFFERENT lessons and must not
        accumulate toward one promotion."""
        first = _promote("mut-1", "python3 -m pytest -q test_billing/test_invoice.py")
        second = _promote("mut-2", "python3 -m pytest -q test_search/test_index.py")
        self.assertNotEqual(first.procedure_id, second.procedure_id, "distinct commands are distinct lessons")
        self.assertEqual(first.status, LearningPolicy.STATUS_CANDIDATE)
        self.assertEqual(second.status, LearningPolicy.STATUS_CANDIDATE, "an unrelated repair is not a holdout for another")

    def test_same_executable_identity_independent_mutations_promote_with_basis(self) -> None:
        shard = _promote("mut-1", "python3 -m pytest -q test_app.py", session="s1")
        again = _promote("mut-2", "python3 -m pytest -q test_app.py", session="s2")
        self.assertEqual(shard.procedure_id, again.procedure_id)
        self.assertEqual(again.status, LearningPolicy.STATUS_PROMOTED)
        basis = dict(again.validation.get("promotion_basis") or {})
        self.assertEqual(basis.get("rule"), "independent_validated_evidence")
        self.assertEqual(int(basis.get("evidence_events") or 0), 2)
        self.assertEqual(basis.get("policy_version"), LearningPolicy.POLICY_VERSION)

    def test_identity_binds_guidance_and_policy_version(self) -> None:
        base = _promote("mut-1", "python3 -m pytest -q test_app.py")
        other_steps = _promote("mut-2", "python3 -m pytest -q test_app.py", steps=("apply diff", "run tests", "then lint"))
        self.assertNotEqual(base.procedure_id, other_steps.procedure_id, "different guidance is a different lesson")

    def test_fallback_identity_uses_the_stored_command_digest(self) -> None:
        """No mutation ids (library callers): the SAME command re-run in a DIFFERENT session is a
        second witness and promotes; the same command in the SAME session is a replay. The old
        fallback read `validation_command`, a key nothing stores, so every no-mutation witness
        collapsed onto one identity and cross-session re-verification silently never promoted."""
        command = "python3 -m pytest -q test_app.py"
        first = _promote("", command, session="session-a")
        self.assertEqual(first.status, LearningPolicy.STATUS_CANDIDATE)
        second = _promote("", command, session="session-b")
        self.assertEqual(first.procedure_id, second.procedure_id, "same command identity, same lesson")
        self.assertEqual(len(second.evidence), 2, "a different session re-running the command is a second witness")
        self.assertEqual(second.status, LearningPolicy.STATUS_PROMOTED)
        replay = _promote("", command, session="session-b")
        self.assertEqual(len(replay.evidence), 2, "same session + same command digest is a replay, not a third witness")


class LegacyAndExpiryTests(_StoreTestCase):
    def _write_v1(self, procedure_id: str = "procedure-legacy0001") -> None:
        root = self.store / "learning" / "procedures"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{procedure_id}.json").write_text(
            json.dumps(
                {
                    "procedure_id": procedure_id,
                    "task_class": "debugging",
                    "title": "Legacy one-shot lesson",
                    "preconditions": ["workspace is writable"],
                    "steps": ["apply diff", "run tests"],
                    "tool_receipts": [{"intent": "workspace.apply_unified_diff"}],
                    "validation": {"ok": True, "tool": "workspace.run_tests"},
                    "rollback": {},
                    "privacy_class": "local_private",
                    "shareability": "local_only",
                    "success_signal": "verified_success",
                    "reuse_count": 4,
                    "verified_reuse_count": 4,
                    "created_at": "2026-08-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )

    def test_v1_records_load_unvalidated_and_are_not_reusable(self) -> None:
        self._write_v1()
        shards = load_procedure_shards()
        self.assertEqual(len(shards), 1)
        self.assertEqual(shards[0].status, LearningPolicy.STATUS_LEGACY_UNVALIDATED)
        self.assertEqual(
            rank_reusable_procedures(task_class="debugging", query_text="apply diff and run tests", procedures=shards),
            [],
            "a v1 record is preserved history, not trusted reusable state",
        )

    def test_v1_bytes_are_preserved_until_a_lifecycle_event(self) -> None:
        self._write_v1()
        path = self.store / "learning" / "procedures" / "procedure-legacy0001.json"
        before = path.read_bytes()
        load_procedure_shards()
        self.assertEqual(path.read_bytes(), before, "loading must not rewrite v1 history")

    def test_legacy_upgrades_only_through_new_verified_evidence_of_the_same_recipe(self) -> None:
        self._write_v1()
        first = _promote("mut-new-1", "python3 -m pytest -q test_app.py")
        # The legacy record carried no command identity; the new evidence opens its OWN lesson.
        # The legacy record itself stays unvalidated...
        loaded = {s.procedure_id: s for s in load_procedure_shards()}
        self.assertEqual(loaded["procedure-legacy0001"].status, LearningPolicy.STATUS_LEGACY_UNVALIDATED)
        # ...and only the new, evidence-carrying lesson can ever be promoted.
        self.assertEqual(loaded[first.procedure_id].status, LearningPolicy.STATUS_CANDIDATE)
        _promote("mut-new-2", "python3 -m pytest -q test_app.py", session="s2")
        loaded = {s.procedure_id: s for s in load_procedure_shards()}
        self.assertEqual(loaded[first.procedure_id].status, LearningPolicy.STATUS_PROMOTED)

    def test_unknown_or_invalid_expiry_fails_closed_for_promoted_shards(self) -> None:
        def _shard(expires_at: str) -> ProcedureShardV1:
            return ProcedureShardV1.create(
                task_class="debugging",
                title="Patch and verify",
                preconditions=[],
                steps=["apply diff", "run tests"],
                tool_receipts=[],
                validation={"ok": True, "tool": "workspace.run_tests"},
                rollback={},
                privacy_class="local_private",
                shareability="local_only",
                success_signal="verified_success",
                status=LearningPolicy.STATUS_PROMOTED,
                expires_at=expires_at,
            )

        from datetime import datetime, timedelta, timezone

        valid = _shard((datetime.now(timezone.utc) + timedelta(days=5)).isoformat())
        self.assertEqual(
            [s.procedure_id for s in rank_reusable_procedures(task_class="debugging", query_text="apply diff run tests", procedures=[valid])],
            [valid.procedure_id],
        )
        for bad in ("", "not-a-date", "1999-01-01T00:00:00+00:00"):
            self.assertEqual(
                rank_reusable_procedures(task_class="debugging", query_text="apply diff run tests", procedures=[_shard(bad)]),
                [],
                f"expiry {bad!r} must fail closed, not silently mean fresh",
            )

    def test_legacy_status_is_excluded_even_with_a_live_expiry(self) -> None:
        """The status gate owns its own exclusion: a legacy-unvalidated record must stay
        non-reusable even when everything else (expiry, scope, shareability) is pristine, so the
        layered expiry guard is not the only thing standing between v1 history and reuse."""
        from datetime import datetime, timedelta, timezone

        shard = ProcedureShardV1.create(
            task_class="debugging",
            title="Legacy but otherwise pristine",
            preconditions=[],
            steps=["run tests"],
            tool_receipts=[],
            validation={"ok": True, "tool": "workspace.run_tests"},
            rollback={},
            privacy_class="local_private",
            shareability="local_only",
            success_signal="verified_success",
            status=LearningPolicy.STATUS_LEGACY_UNVALIDATED,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        )
        self.assertEqual(
            rank_reusable_procedures(task_class="debugging", query_text="apply diff run tests", procedures=[shard]),
            [],
        )

    def test_current_version_creation_defaults_to_candidate(self) -> None:
        shard = ProcedureShardV1.create(
            task_class="debugging",
            title="Anything",
            preconditions=[],
            steps=["run tests"],
            tool_receipts=[],
            validation={"ok": True, "tool": "workspace.run_tests"},
            rollback={},
            privacy_class="local_private",
            shareability="local_only",
            success_signal="verified_success",
        )
        self.assertEqual(shard.status, LearningPolicy.STATUS_CANDIDATE, "no evidence-free promoted defaults")


class ConcurrencyAndCapacityTests(_StoreTestCase):
    def test_concurrent_first_promotions_converge_on_one_shard(self) -> None:
        workers = 4
        code = (
            "from core.learning import promote_verified_procedure\n"
            "s = promote_verified_procedure(\n"
            "    task_class='debugging', title='t', preconditions=['workspace is writable'],\n"
            "    steps=['apply diff', 'run tests'],\n"
            "    tool_receipts=[{'intent': 'workspace.apply_unified_diff', 'mutation_id': 'mut-race-%d'},\n"
            "                   {'intent': 'workspace.run_tests', 'command': 'python3 -m pytest -q test_app.py', 'returncode': 0}],\n"
            "    validation={'ok': True, 'tool': 'workspace.run_tests', 'command': 'python3 -m pytest -q test_app.py', 'returncode': 0},\n"
            "    rollback={}, session_id='race-%d')\n"
            "print(s.procedure_id)\n"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", self._worker_prologue() + code % (i, i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=REPO_ROOT,
            )
            for i in range(workers)
        ]
        outs = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, stderr)
            outs.append(stdout.strip().splitlines()[-1])
        self.assertEqual(len(set(outs)), 1, f"all creators must converge on one shard id: {outs}")
        shards = load_procedure_shards()
        self.assertEqual(len(shards), 1)
        self.assertGreaterEqual(len(shards[0].evidence), 2, "no creator's evidence may be lost to the race")

    def test_multiprocess_appends_never_lose_evidence(self) -> None:
        _promote("mut-seed-1", "python3 -m pytest -q test_app.py")
        _promote("mut-seed-2", "python3 -m pytest -q test_app.py")
        workers = 6
        code = (
            "from core.learning import promote_verified_procedure\n"
            "promote_verified_procedure(\n"
            "    task_class='debugging', title='t', preconditions=['workspace is writable'],\n"
            "    steps=['apply diff', 'run tests'],\n"
            "    tool_receipts=[{'intent': 'workspace.apply_unified_diff', 'mutation_id': 'mut-mp-%d'},\n"
            "                   {'intent': 'workspace.run_tests', 'command': 'python3 -m pytest -q test_app.py', 'returncode': 0}],\n"
            "    validation={'ok': True, 'tool': 'workspace.run_tests', 'command': 'python3 -m pytest -q test_app.py', 'returncode': 0},\n"
            "    rollback={}, session_id='mp-%d')\n"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", self._worker_prologue() + code % (i, i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=REPO_ROOT,
            )
            for i in range(workers)
        ]
        for proc in procs:
            _, stderr = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, stderr)
        shards = load_procedure_shards()
        self.assertEqual(len(shards), 1)
        self.assertGreaterEqual(len(shards[0].evidence), 2 + workers, "every append survives concurrent updates")

    def test_correction_wins_against_concurrent_appends_without_resurrection(self) -> None:
        shard = _promote("mut-seed-1", "python3 -m pytest -q test_app.py")
        _promote("mut-seed-2", "python3 -m pytest -q test_app.py")
        append_code = (
            "from core.learning import promote_verified_procedure\n"
            "promote_verified_procedure(\n"
            "    task_class='debugging', title='t', preconditions=['workspace is writable'],\n"
            "    steps=['apply diff', 'run tests'],\n"
            "    tool_receipts=[{'intent': 'workspace.apply_unified_diff', 'mutation_id': 'mut-post-correction-%d'},\n"
            "                   {'intent': 'workspace.run_tests', 'command': 'python3 -m pytest -q test_app.py', 'returncode': 0}],\n"
            "    validation={'ok': True, 'tool': 'workspace.run_tests', 'command': 'python3 -m pytest -q test_app.py', 'returncode': 0},\n"
            "    rollback={}, session_id='post-%d')\n"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", self._worker_prologue() + append_code % (i, i)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=REPO_ROOT,
            )
            for i in range(3)
        ]
        invalidate_procedure(shard.procedure_id, reason="operator corrected mid-flight")
        for proc in procs:
            _, stderr = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, stderr)
        loaded = {s.procedure_id: s for s in load_procedure_shards()}
        self.assertEqual(loaded[shard.procedure_id].status, LearningPolicy.STATUS_DEMOTED, "correction is not undone by racing appends")

    def test_delete_then_append_does_not_resurrect(self) -> None:
        shard = _promote("mut-1", "python3 -m pytest -q test_app.py")
        promoted = _promote("mut-2", "python3 -m pytest -q test_app.py")
        self.assertEqual(promoted.status, LearningPolicy.STATUS_PROMOTED)
        invalidate_procedure(shard.procedure_id, reason="forget this lesson")
        self.assertTrue(delete_procedure(shard.procedure_id))
        # A late evidence event for the same recipe re-LEARNNS from scratch: deterministic ids
        # give it the same address, but it must come back as a fresh candidate with no memory of
        # the deleted record's corrections, demotion or promotion.
        # An in-flight locked update against the deleted id finds nothing and resurrects nothing.
        from core.learning.procedure_shards import update_procedure_shard

        self.assertIsNone(update_procedure_shard(shard.procedure_id, lambda s: s))
        after = _promote("mut-3", "python3 -m pytest -q test_app.py")
        self.assertEqual(after.status, LearningPolicy.STATUS_CANDIDATE)
        self.assertEqual(after.corrections, ())
        self.assertEqual(len(after.evidence), 1)
        self.assertEqual(len(load_procedure_shards()), 1)

    def test_sufficiency_append_and_reset_are_cross_process_safe(self) -> None:
        first = record_sufficiency_observation(task_kind="summarize", provider_id="p1", outcome="verified_success", turn_key="t-seed")
        self.assertIsNotNone(first)
        append_code = (
            "from core.learning.model_sufficiency import record_sufficiency_observation\n"
            "w = '%d'\n"
            "for i in range(4):\n"
            "    record_sufficiency_observation(task_kind='summarize', provider_id='p1',\n"
            "        outcome='quality_failure', turn_key='t-mp-' + w + '-' + str(i))\n"
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", self._worker_prologue() + append_code % (i,)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=REPO_ROOT,
            )
            for i in range(3)
        ]
        for proc in procs:
            _, stderr = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(len(list_sufficiency_observations(provider_id="p1")), 1 + 3 * 4, "no observation lost or duplicated")
        removed = reset_sufficiency_observations(provider_id="p1")
        self.assertEqual(removed, 13)
        self.assertEqual(list_sufficiency_observations(provider_id="p1"), [])

    def test_store_capacity_is_bounded(self) -> None:
        cap = LearningPolicy.MAX_STORED_PROCEDURES
        for index in range(cap + 3):
            _promote(f"mut-cap-{index}", f"python3 -m pytest -q test_capacity_{index}.py")
        self.assertLessEqual(len(load_procedure_shards()), cap, "the procedures store must stay bounded")

    def _worker_prologue(self) -> str:
        return (
            "import pathlib\n"
            "from unittest import mock\n"
            f"store = pathlib.Path({str(self.store)!r})\n"
            "import core.learning.procedure_shards as ps\n"
            "import core.learning.model_sufficiency as ms\n"
            "mock.patch.object(ps, 'data_path', side_effect=lambda *p: pathlib.Path(store, *map(str, p))).start()\n"
            "mock.patch.object(ms, 'data_path', side_effect=lambda *p: pathlib.Path(store, *map(str, p))).start()\n"
        )


class TypedTerminalFeedbackTests(_StoreTestCase):
    def _promoted(self, tag: str):
        command = f"python3 -m pytest -q test_{tag}.py"
        _promote(f"mut-{tag}-1", command)
        return _promote(f"mut-{tag}-2", command)

    def test_terminal_vocabulary_is_closed_and_transport_is_not_a_value(self) -> None:
        shard = self._promoted("vocab")
        for bad in ("provider_error", "timeout", "yolo", ""):
            result = record_reuse_terminal(procedure_ids=[shard.procedure_id], task_class="debugging", terminal=bad)
            self.assertEqual(result, [], f"transport/noise vocabulary must be refused: {bad!r}")
        reloaded = {s.procedure_id: s for s in load_procedure_shards()}[shard.procedure_id]
        self.assertEqual(reloaded.last_terminal_outcome, "", "a refused terminal must leave no state behind")
        self.assertEqual(reloaded.reuse_count, 0)

    def test_verified_reuse_failure_demotes_but_cancellation_is_neutral(self) -> None:
        promoted = self._promoted("part1")
        for index in range(LearningPolicy.DEMOTE_AFTER_VERIFIED_REUSE_FAILURES - 1):
            updated = record_reuse_terminal(
                procedure_ids=[promoted.procedure_id], task_class="debugging", terminal="failed", detail=f"validation failed {index}"
            )
            self.assertEqual(updated[0].status, LearningPolicy.STATUS_PROMOTED)
            self.assertEqual(updated[0].consecutive_failures, index + 1)
        final = record_reuse_terminal(procedure_ids=[promoted.procedure_id], task_class="debugging", terminal="failed")
        self.assertEqual(final[0].status, LearningPolicy.STATUS_DEMOTED)
        self.assertIn("verified_reuse_failure", final[0].demoted_reason)

        shard = self._promoted("part2")
        for _ in range(5):
            neutral = record_reuse_terminal(procedure_ids=[shard.procedure_id], task_class="debugging", terminal="cancelled")
        self.assertEqual(neutral[0].status, LearningPolicy.STATUS_PROMOTED, "cancelled turns teach nothing either way")
        self.assertEqual(neutral[0].consecutive_failures, 0)
        self.assertEqual(neutral[0].consecutive_unverified_reuses, 0)

    def test_legacy_caller_signature_maps_to_typed_outcomes(self) -> None:
        shard = self._promoted("part2")
        updated = record_procedure_reuse(procedure_ids=[shard.procedure_id], task_class="debugging", verified=True, outcome="completed")
        self.assertEqual(updated[0].last_terminal_outcome, "successful")
        updated = record_procedure_reuse(procedure_ids=[shard.procedure_id], task_class="debugging", verified=False, outcome="completed")
        self.assertEqual(updated[0].last_terminal_outcome, "unvalidated")

    def test_unknown_cost_is_unknown_not_measured_zero(self) -> None:
        shard = _promote("mut-1", "python3 -m pytest -q test_app.py")
        cost = dict(shard.evidence[0].get("cost") or {})
        self.assertIsNone(cost.get("estimated_cost_usd"), "unknown cost must serialize as null, not 0.0")
        self.assertEqual(cost.get("basis"), "unknown")
        self.assertIsNone(shard.estimated_cost_usd)
        record_text = json.dumps(list_procedure_records())
        self.assertNotIn('"estimated_cost_usd": 0.0', record_text)


class ModelFeedbackIsolationTests(_StoreTestCase):
    def test_models_under_one_provider_do_not_contaminate_each_other(self) -> None:
        for _ in range(4):
            record_sufficiency_observation(task_kind="summarize", provider_id="ollama-local", model_id="qwen3:8b", outcome="quality_failure")
        # A different model on the SAME provider is spotless and must not inherit the penalty.
        self.assertEqual(sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local", model_id="qwen3:14b"), 0.0)
        self.assertAlmostEqual(
            sufficiency_adjustment(task_kind="summarize", provider_id="ollama-local", model_id="qwen3:8b"),
            -LearningPolicy.SUFFICIENCY_MAX_PENALTY,
            places=4,
        )

    def test_feedback_from_another_policy_version_is_not_mixed_in(self) -> None:
        from datetime import datetime, timezone

        for _ in range(4):
            record_sufficiency_observation(
                task_kind="summarize",
                provider_id="p9",
                outcome="quality_failure",
                turn_key=f"t-old-{_}",
                created_at=datetime.now(timezone.utc),
                feedback_version=LearningPolicy.FEEDBACK_VERSION - 1,
            )
        self.assertEqual(sufficiency_adjustment(task_kind="summarize", provider_id="p9"), 0.0, "stale-version feedback must not rank")


if __name__ == "__main__":
    unittest.main()
