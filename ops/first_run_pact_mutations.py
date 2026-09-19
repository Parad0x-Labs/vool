"""First-Run Pact sabotage harness — one sabotage per invariant, each biting its named red.

Discipline (ops/keychain_preflight_mutations.py): a byte-exact ``old → new`` mutation is
applied to the production tree, the NAMED red test is run (it must FAIL under the
mutation), the file is restored byte-exactly (sha256-verified), and the named test is
re-run (it must PASS on the restored tree). Results print one line per mutation for
``proofs/first-run-pact-20260905/mutations.txt``.

S-P12 (a bespoke pact chat path replacing /api/chat) is structurally pinned rather than
mutated: at this SHA the served task journey IS the ordinary /api/chat door
(tests/test_first_run_pact_task_evidence.py enters nowhere else), so the named pin is
the served journey itself; a byte-mutation "adding a second chat door" is a new-feature
sabotage, not a regression this harness can express as old→new.

Run:  .venv/bin/python ops/first_run_pact_mutations.py
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PY = REPO / "core" / "first_run_pact.py"
PAGE = REPO / "core" / "vool_chat_page.py"
WEB = REPO / "core" / "web" / "api" / "onboarding_endpoints.py"
RUNTIME = REPO / "core" / "web" / "api" / "runtime.py"
PE = REPO / "core" / "policy_engine.py"
GRP = REPO / "core" / "command_registry" / "groups" / "first_run.py"
CPLL = REPO / "core" / "cross_process_lock.py"

PYTEST = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"]


@dataclass
class Mutation:
    name: str
    file: Path
    old: str
    new: str
    named_red_test: str


MUTATIONS = [
    Mutation(
        name="S-P1 card becomes a fixed overlay over the composer",
        file=PAGE,
        old="  .pact-card { border:1px solid var(--border,#333);",
        new="  .pact-card { position:fixed; top:0; left:0; right:0; z-index:9999; pointer-events:all; border:1px solid var(--border,#333);",
        named_red_test="tests/test_chat_page_pact_ui.py::test_card_never_blocks_composer",
    ),
    Mutation(
        name="S-P2 task.claim accepts the client's assertion without ledger evidence",
        file=PY,
        old="        verified = _verify_turn_evidence(session_id, request_id, receipt_id=receipt_id)\n\n        def _apply(data: dict[str, Any]) -> dict[str, Any]:\n            _mark_step(data, \"task\", \"done\")",
        new="        verified = {\"ok\": True, \"receipt_id\": \"hr-sabotaged\", \"verdict\": \"clean\", \"signed\": True, \"signature_verified\": True, \"chain_verified\": True, \"proof_state\": \"VERIFIED\", \"refusals\": [], \"refusal_evidence\": None}\n\n        def _apply(data: dict[str, Any]) -> dict[str, Any]:\n            _mark_step(data, \"task\", \"done\")",
        named_red_test="tests/test_first_run_pact_task_evidence.py::test_a_fabricated_request_id_is_refused_with_the_typed_fault",
    ),
    Mutation(
        name="S-P3 pact region renders its own chip state markup",
        file=PAGE,
        old="const PACT_TOUR_STATES = ",
        new="const PACT_SABOTAGE_CHIP = '<span class=\"pc-state pc-state-verified\">VERIFIED</span>';\nconst PACT_TOUR_STATES = ",
        named_red_test="tests/test_chat_page_pact_ui.py::test_the_pact_region_never_fabricates_a_chip_state",
    ),
    Mutation(
        name="S-P4 boundary.set writes only a pact marker and drops the authority calls",
        file=PY,
        old='                    _effect("policy_composite", lambda: _set_composite(value))',
        new='                    pass  # SABOTAGE: the authority writes are dropped',
        named_red_test="tests/test_first_run_pact_boundaries.py::test_the_composite_flips_every_authority_store_never_the_pact_file",
    ),
    Mutation(
        name="S-P5 facts.set leaks the typed value into the pact file",
        file=PY,
        old="        def _apply(data: dict[str, Any]) -> dict[str, Any]:\n            if saved_any:\n                _mark_step(data, \"facts\", \"done\")\n            return data",
        new="        def _apply(data: dict[str, Any]) -> dict[str, Any]:\n            if saved_any:\n                _mark_step(data, \"facts\", \"done\")\n                data[\"seed_reason\"] = \"facts:\" + \"|\".join(str(i.get(\"value\")) for i in items)\n            return data",
        named_red_test="tests/test_first_run_pact_facts.py::test_file_never_contains_fact_values",
    ),
    Mutation(
        name="S-P6 a pact POST mutates the file directly beside the registry dispatch",
        file=WEB,
        old="    status, payload = _cr_forward(command_id, input_data)",
        new="    from core import first_run_pact as _sabotage_direct\n    try:\n        _sabotage_direct.skip()\n    except Exception:\n        pass\n    status, payload = _cr_forward(command_id, input_data)",
        named_red_test="tests/test_first_run_pact_registry_authority.py::test_a_pact_post_consumes_exactly_one_revision",
    ),
    Mutation(
        name="S-P7 the boot path calls the credential store once pact state exists",
        file=RUNTIME,
        old="        _pact_seed = _first_run_pact.seed()\n        logger.info(",
        new="        _pact_seed = _first_run_pact.seed()\n        try:\n            import subprocess as _sab\n\n            _sab.run([\"security\", \"find-generic-password\", \"-s\", \"vool-credentials\"],\n                     capture_output=True, timeout=5)\n        except Exception:\n            pass\n        logger.info(",
        named_red_test="tests/test_first_run_pact_no_popups.py::test_the_full_pact_tour_with_the_card_visible_makes_zero_credential_calls",
    ),
    Mutation(
        name="S-P8 the pact write drops the atomic pattern for a plain write",
        file=PY,
        old="            os.fsync(fh.fileno())\n        os.replace(tmp_name, _path())",
        new="            os.fsync(fh.fileno())\n        _path().write_text(payload)  # SABOTAGE: truncate-then-write, no atomic replace",
        named_red_test="tests/test_first_run_pact_restart_multiplicity.py::test_sigkill_between_writes_never_leaves_a_half_written_file",
    ),
    Mutation(
        name="S-P9 the seed predicate is inverted (existing users get onboarded)",
        file=PY,
        old="def has_existing_signal() -> bool:\n    \"\"\"Any real prior use on this home trips at least one signal (spec §12).\"\"\"",
        new="def has_existing_signal() -> bool:\n    return False  # SABOTAGE: every home looks fresh\n    \"\"\"Any real prior use on this home trips at least one signal (spec §12).\"\"\"",
        named_red_test="tests/test_first_run_pact_existing_users.py::test_each_existing_use_signal_seeds_not_applicable",
    ),
    Mutation(
        name="S-P10 the skip button stops working on the facts screen",
        file=PAGE,
        old="  pactById('pactSkip').addEventListener('click', () => pactPost('/api/onboarding/pact/skip', {}));",
        new="  pactById('pactSkip').addEventListener('click', () => { if (pactView.data && pactView.data.state === 'facts') { return; } pactPost('/api/onboarding/pact/skip', {}); });",
        named_red_test="tests/test_chat_page_pact_ui.py::test_skip_button_posts_skip_from_every_tour_state",
    ),
    Mutation(
        name="S-P11 the denial step completes without any refusal check",
        file=PY,
        old="        if refusal_evidence is None:\n            refusal_evidence = _composite_contained_attempt(proof)\n        if refusal_evidence is None:\n            raise PactFault(\n                FAULT_EVIDENCE_MISSING,",
        new="        if refusal_evidence is None:\n            refusal_evidence = _composite_contained_attempt(proof)\n        if refusal_evidence is None and False:\n            raise PactFault(\n                FAULT_EVIDENCE_MISSING,",
        named_red_test="tests/test_first_run_pact_denial_evidence.py::test_a_turn_where_nothing_was_attempted_never_completes_the_denial",
    ),
    Mutation(
        name="S-P13 an OAuth button appears on the choice card",
        file=PAGE,
        old='"connect": "Connect a provider",',
        new='"connect": "Connect with OAuth",',
        named_red_test="tests/test_first_run_pact_no_popups.py::test_the_pact_dom_carries_no_oauth_and_no_browser_deep_links",
    ),
    Mutation(
        name="S-P14 reset from done erases the operator's boundaries",
        file=PY,
        old='        data["state"] = STATE_WELCOME\n        data["welcome_hidden"] = False\n        data["completed_at"] = ""',
        new='        data["state"] = STATE_WELCOME\n        data["welcome_hidden"] = False\n        from core import policy_engine as _sabotage_reset\n\n        _sabotage_reset.set_operator_policy_values({"system.local_only_mode": False})\n        data["completed_at"] = ""',
        named_red_test="tests/test_first_run_pact_state.py::test_reset_replays_without_erasing_facts_or_boundaries",
    ),
    Mutation(
        name="S-P15 the receipt line claims chain verification the server did not return",
        file=PAGE,
        old="d.chain_verified === true ? 'chain verified within this chat' : 'chain not proven within this chat'",
        new="'chain verified within this chat'",
        named_red_test="tests/test_chat_page_pact_ui.py::test_the_receipt_line_never_claims_unreturned_chain_verification",
    ),
    Mutation(
        name="S-P16 re-opening one outbound door no longer degrades the egress census",
        file=PY,
        old='        "proof": "complete" if complete else "partial",',
        new='        "proof": "complete",  # SABOTAGE: the claim is asserted, not proved',
        named_red_test="tests/test_first_run_pact_boundaries.py::test_reopening_one_door_degrades_the_census_and_the_sentence_narrows",
    ),
    Mutation(
        name="S-P17 the content whitelist stops stripping unknown keys",
        file=PY,
        old="    clean = {key: data[key] for key in allowed_top if key in data}",
        new="    clean = dict(data)  # SABOTAGE: anything a writer stuffs in reaches the file",
        named_red_test="tests/test_first_run_pact_state.py::test_the_sanitizer_strips_unknown_keys_even_from_a_poisoned_write",
    ),
    Mutation(
        name="S-P18 claims accept a receipt that fails signature/hash verification",
        file=PY,
        old=("    ok, why = verify_honesty_receipt(located)\n"
             "    if not ok:\n"
             "        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, f\"receipt verification failed: {why}\")\n"
             "    chain_ok, chain_why = verify_honesty_chain(receipts)\n"
             "    if not chain_ok:\n"
             "        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, f\"receipt chain broken: {chain_why}\")"),
        new=("    ok, why = verify_honesty_receipt(located)\n"
             "    if False and not ok:\n"
             "        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, f\"receipt verification failed: {why}\")\n"
             "    chain_ok, chain_why = verify_honesty_chain(receipts)\n"
             "    if False and not chain_ok:\n"
             "        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, f\"receipt chain broken: {chain_why}\")"),
        named_red_test="tests/test_first_run_pact_task_evidence.py::test_a_broken_chain_is_refused_even_when_the_last_receipt_verifies_alone",
    ),
    Mutation(
        name="S-P19 address-classified values are accepted as pact facts",
        file=PY,
        old='    if "postal_address" in risks:\n        return "address_shaped"',
        new='    if False and "postal_address" in risks:\n        return "address_shaped"',
        named_red_test="tests/test_first_run_pact_facts.py::test_an_address_classified_value_is_refused_in_every_fact_field_c2",
    ),
    Mutation(
        name="S-P20 a synthesized denial is accepted without a real contained attempt",
        file=PY,
        old='    if not contained:\n        return None\n    return {"source": "execution_facts", "refusals": contained, "note": "attempt contained by the Local Only composite"}',
        new='    if not contained:\n        return {"source": "execution_facts", "refusals": [{"name": "sabotage.synthesized", "kind": "retrieval", "outcome": "refused", "fact_id": "exec-sabotage"}], "note": "SABOTAGE"}\n    return {"source": "execution_facts", "refusals": contained, "note": "attempt contained by the Local Only composite"}',
        named_red_test="tests/test_first_run_pact_denial_evidence.py::test_a_turn_where_nothing_was_attempted_never_completes_the_denial",
    ),
    Mutation(
        name="S-P21 the policy setter discards its return contract",
        file=PE,
        old='            with PublicationLock(lock_path):\n                return _write_operator_values_locked(values)',
        new='            with PublicationLock(lock_path):\n                _write_operator_values_locked(values)\n        return None  # SABOTAGE: the documented return contract vanishes',
        named_red_test="tests/test_first_run_pact_correction.py::test_the_production_setter_returns_the_exact_effective_values_written",
    ),
    Mutation(
        name="S-P22 boundary policy-lock contention escapes untyped",
        file=PY,
        old='            except LockUnavailable as exc:\n                # the owning policy lock refused: typed, and ZERO authority effects',
        new='            except OSError as exc:  # SABOTAGE: the LockUnavailable mapping is gone\n                # the owning policy lock refused: typed, and ZERO authority effects',
        named_red_test="tests/test_first_run_pact_correction.py::test_boundary_policy_lock_contention_raises_the_typed_pact_fault",
    ),
    Mutation(
        name="S-P23 stale boundary requests mutate authorities before the CAS refusal",
        file=PY,
        old='            # CAS VALIDITY FIRST — a stale request is refused before ANY authority\n            # is touched (policy composite, wallet freeze, memory pause, web lookups).\n            if expect_revision is not None and int(expect_revision) != int(current.get("revision", 0)):',
        new='            # CAS VALIDITY FIRST — a stale request is refused before ANY authority\n            # is touched (policy composite, wallet freeze, memory pause, web lookups).\n            if False and expect_revision is not None and int(expect_revision) != int(current.get("revision", 0)):',
        named_red_test="tests/test_first_run_pact_correction.py::test_a_stale_boundary_request_returns_stale_revision_and_touches_nothing",
    ),
    Mutation(
        name="S-P24 the shared publication lock fails open on contention",
        file=CPLL,
        old='            raise LockUnavailable(self._path, str(exc)) from exc',
        new='            pass  # SABOTAGE: yield unlocked and continue',
        named_red_test="tests/test_first_run_pact_correction.py::test_posix_multiprocess_exclusion_through_the_shared_lock",
    ),
    Mutation(
        name="S-P25 the policy setter re-reads after releasing the publication lock",
        file=PE,
        old='            with PublicationLock(lock_path):\n                return _write_operator_values_locked(values)',
        new='            with PublicationLock(lock_path):\n                _write_operator_values_locked(values)\n        return {dotted: get(dotted) for dotted in values}  # SABOTAGE: re-read after release',
        named_red_test="tests/test_first_run_pact_correction.py::test_a_racing_publisher_between_lock_release_and_read_cannot_change_the_return",
    ),
    Mutation(
        name="S-P26 lock-file open failures escape as raw OSError",
        file=CPLL,
        old='        except (OSError, ImportError) as exc:\n            if fh is not None:',
        new='        except ImportError as exc:  # SABOTAGE: OSError (open failures) escapes raw\n            if fh is not None:',
        named_red_test="tests/test_first_run_pact_correction.py::test_direct_policy_write_with_a_broken_lock_open_is_typed",
    ),
    Mutation(
        name="S-P27 the boundary command contract stops declaring its new faults",
        file=GRP,
        old='            fault_bindings=_bindings("boundary_key_unknown", "invalid_transition", "stale_revision",\n                                     "lock_unavailable", "boundary_partial", "authority_write_failed"),',
        new='            fault_bindings=_bindings("boundary_key_unknown", "invalid_transition", "stale_revision"),  # SABOTAGE: the new faults are undeclared',
        named_red_test="tests/test_first_run_pact_correction.py::test_every_boundary_fault_the_handler_can_produce_is_declared",
    ),
    Mutation(
        name="S-P28 pact publication failure after authority effects escapes raw",
        file=PY,
        old='            try:\n                next_data = _publish(_apply)\n            except Exception as exc:',
        new='            try:\n                next_data = _publish(_apply)\n            except PactFault as exc:  # SABOTAGE: raw publication failures escape',
        named_red_test="tests/test_first_run_pact_correction.py::test_a_pact_publication_failure_after_effects_is_a_truthful_partial",
    ),
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _purge_bytecode() -> None:
    """A restore can land in the same mtime-second as the mutated write; a stale .pyc
    from the mutated source would poison the green re-run. Purge before every run."""
    import shutil

    for cache_dir in (REPO / "core", REPO / "tests", REPO / "ops"):
        for pyc in cache_dir.rglob("__pycache__"):
            shutil.rmtree(pyc, ignore_errors=True)


def _run_red(named_test: str) -> int:
    _purge_bytecode()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([*PYTEST, named_test], cwd=REPO, capture_output=True, text=True, env=env).returncode


def main() -> int:
    wanted = sys.argv[1:]
    print(f"# first-run-pact sabotage run — {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} @ {REPO}")
    failures: list[str] = []
    for mutation in MUTATIONS:
        if wanted and not any(w in mutation.name for w in wanted):
            continue
        path = mutation.file
        original_bytes = path.read_bytes()
        original_sha = _sha(path)
        original_text = path.read_text(encoding="utf-8")
        count = original_text.count(mutation.old)
        if count != 1:
            print(f"SKIP {mutation.name}: anchor found {count} times in {path.name}")
            failures.append(mutation.name)
            continue
        try:
            path.write_text(original_text.replace(mutation.old, mutation.new, 1), encoding="utf-8")
            red = _run_red(mutation.named_red_test)
            restored_ok = False
            if red != 0:
                path.write_bytes(original_bytes)
                restored_ok = _sha(path) == original_sha
                green = _run_red(mutation.named_red_test)
                if restored_ok and green == 0:
                    print(f"OK   {mutation.name}: named red ({mutation.named_red_test.split('::')[-1]}) then green after exact restore")
                else:
                    print(f"FAIL {mutation.name}: restore_ok={restored_ok} green_exit={green}")
                    failures.append(mutation.name)
            else:
                path.write_bytes(original_bytes)
                print(f"FAIL {mutation.name}: named test PASSED under the sabotage (it guards nothing)")
                failures.append(mutation.name)
        finally:
            if _sha(path) != original_sha:
                path.write_bytes(original_bytes)
                print(f"  !! forced restore of {path.name}")
    print()
    if failures:
        print(f"{len(failures)} sabotage(s) failed: {failures}")
        return 1
    print(f"All {len(MUTATIONS)} sabotages bit their named reds and every restore is sha256-verified green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
