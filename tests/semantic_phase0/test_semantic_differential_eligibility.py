"""The candidate cannot decide what it is compared on.

A hostile review broke the differential with one move: a candidate mutation that drifted (it put the
process id into a prompt) exited successfully, moved its own case into the "unstable" set, and shrank
the comparison from 250 turns to 249. The changed behaviour hid inside an exclusion it had caused.

The repair is structural rather than a better heuristic: eligibility is a pure function of BASE rows,
frozen before the candidate runs at all, and the comparison stage cannot add to or remove from it.
These tests drive those two functions over synthetic rows, so the property is asserted by executing
it rather than inferred from a twenty-minute two-worktree run.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "semantic_phase0_base_differential.py"


def _differential():
    """Load the differential script as a module without running it."""
    spec = importlib.util.spec_from_file_location("vool_base_differential", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(text: str, response: str, raised: str = "") -> dict:
    return {"text": text, "raised": raised, "fields": {"response": repr(response)}}


def test_eligibility_reads_base_rows_only() -> None:
    """`freeze_eligibility` takes no candidate parameter, so no candidate behaviour can reach it."""
    module = _differential()
    texts = ["steady", "drifty"]
    base_a = [_row("steady", "same"), _row("drifty", "one")]
    base_b = [_row("steady", "same"), _row("drifty", "two")]

    eligible, unstable = module.freeze_eligibility(texts, base_a, base_b)
    assert eligible == ["steady"]
    assert unstable == ["drifty"], "a case the base cannot reproduce is excluded, and named"

    import inspect

    parameters = set(inspect.signature(module.freeze_eligibility).parameters)
    assert parameters == {"texts", "base_a", "base_b"}, (
        f"eligibility must be a function of the base alone; it also accepts {parameters}"
    )


def test_candidate_instability_is_a_difference_not_an_exclusion() -> None:
    """The PID-drift attack, executed.

    `drifty` is eligible -- the base reproduces it. The candidate does not reproduce it. That must
    come back as a difference, and the case must still be accounted for.
    """
    module = _differential()
    base_a = [_row("steady", "same"), _row("drifty", "base answer")]
    cand_a = [_row("steady", "same"), _row("drifty", "run 12345")]
    cand_b = [_row("steady", "same"), _row("drifty", "run 67890")]

    account = module.compare_against_frozen(base_a, cand_a, cand_b, expected=["steady", "drifty"])

    assert account.is_total, "both frozen ids must resolve to a verdict"
    assert account.summary()["different"] == 1
    drift = [line for line in account.differences if "did not reproduce itself" in line]
    assert len(drift) == 1, f"candidate drift must be reported as a difference: {account.differences!r}"
    assert "drifty" in drift[0]
    assert account.passed is False


def test_a_stable_candidate_that_matches_the_base_produces_no_differences() -> None:
    """The control. Without it, a `compare_against_frozen` that returned a difference for every row
    would pass the test above while proving nothing."""
    module = _differential()
    rows = [_row("steady", "same"), _row("also steady", "same too")]
    account = module.compare_against_frozen(rows, list(rows), list(rows),
                                            expected=["steady", "also steady"])
    assert account.differences == []
    assert account.summary() == {
        "expected": 2, "matched": 2, "different": 0, "missing": 0, "errored": 0,
        "host_unstable": 0, "unexpected": 0, "duplicates": 0, "accounted": 2, "total": True,
    }
    assert account.passed is True


def test_a_candidate_case_outside_the_frozen_set_is_rejected_not_counted() -> None:
    """A candidate returning rows nobody asked for cannot pad its own coverage."""
    module = _differential()
    base_a = [_row("steady", "same")]
    smuggled = [_row("steady", "same"), _row("invented", "look how well I do")]
    account = module.compare_against_frozen(base_a, smuggled, smuggled, expected=["steady"])
    assert account.summary()["expected"] == 1, "the denominator is the frozen set, not what arrived"
    assert account.summary()["unexpected"] == 1
    assert account.passed is False, "an unexpected row must fail the run, not decorate it"


def test_a_changed_answer_on_an_eligible_case_is_reported_with_both_sides() -> None:
    module = _differential()
    base_a = [_row("q", "the base answer")]
    cand = [_row("q", "the candidate answer")]
    account = module.compare_against_frozen(base_a, cand, cand, expected=["q"])
    assert account.summary()["different"] == 1
    assert len(account.differences) == 1
    assert "the base answer" in account.differences[0]
    assert "the candidate answer" in account.differences[0]


def test_a_raise_on_one_side_only_is_a_difference() -> None:
    module = _differential()
    base_a = [_row("q", "an answer")]
    cand = [_row("q", "", raised="ValueError: goal is required")]
    account = module.compare_against_frozen(base_a, cand, cand, expected=["q"])
    assert account.summary()["different"] == 1
    assert any("raised" in line for line in account.differences)


# --------------------------------------------------------------------------------------------
# TOTALITY. The frozen id list is the authority; the candidate cannot shrink any denominator.
# The defect this closes: the comparison used to iterate the rows the CANDIDATE returned, so a
# candidate that omitted one eligible turn produced `compared=249, differences=0, exit 0` -- the
# turn it could not answer left no trace, because nothing was looking for it.
# --------------------------------------------------------------------------------------------

_UNIVERSE = [f"t{i}" for i in range(250)]


def _stable_base() -> list[dict]:
    return [_row(text, "same") for text in _UNIVERSE]


@pytest.mark.parametrize("omitted", [1, 49, 50, 250])
def test_omitting_frozen_turns_is_missing_never_a_smaller_denominator(omitted: int) -> None:
    """The headline attack, at every scale the mission names -- one turn, 49, 50, and all of them."""
    module = _differential()
    base_a = _stable_base()
    kept = [row for row in base_a if row["text"] not in set(_UNIVERSE[:omitted])]

    account = module.compare_against_frozen(base_a, kept, kept, expected=_UNIVERSE)
    summary = account.summary()

    assert summary["expected"] == 250, "the denominator is frozen; a candidate may not shrink it"
    assert summary["accounted"] == 250, "every frozen id must resolve to exactly one verdict"
    assert summary["missing"] == omitted
    assert summary["matched"] == 250 - omitted
    assert account.is_total is True
    assert account.passed is False, "a missing turn is a failure, not an absence"
    assert len(account.differences) >= omitted
    # The exact shape the old design produced, now impossible.
    assert not (summary["accounted"] == 250 - omitted and not account.differences)


def test_a_duplicate_frozen_id_is_rejected_rather_than_resolved() -> None:
    """Two rows for one id with conflicting payloads: picking a winner would be the harness
    deciding what the candidate meant."""
    module = _differential()
    base_a = _stable_base()
    doubled = [*base_a, _row("t7", "a different answer")]
    account = module.compare_against_frozen(base_a, doubled, doubled, expected=_UNIVERSE)

    assert account.summary()["expected"] == 250
    assert account.summary()["duplicates"] == 1
    assert account.verdicts["t7"] == module.VERDICT_MISSING, "a rejected duplicate leaves the id unanswered"
    assert account.passed is False


def test_reordering_the_candidate_rows_changes_nothing() -> None:
    """Order is not identity. A candidate that answers everything in a different order still
    matches -- otherwise the totality check would be a false alarm generator."""
    module = _differential()
    base_a = _stable_base()
    shuffled = list(reversed(base_a))
    account = module.compare_against_frozen(base_a, shuffled, shuffled, expected=_UNIVERSE)
    assert account.summary()["matched"] == 250
    assert account.passed is True


def test_a_candidate_that_dies_partway_is_missing_for_every_turn_it_never_reached() -> None:
    """A process that exits before its final rows returns a truncated list. Each unreached turn is
    its own MISSING verdict, so the failure is sized rather than merely noticed."""
    module = _differential()
    base_a = _stable_base()
    truncated = base_a[:137]
    account = module.compare_against_frozen(base_a, truncated, truncated, expected=_UNIVERSE)
    assert account.summary()["missing"] == 113
    assert account.summary()["accounted"] == 250
    assert account.passed is False


def test_pass_requires_the_four_verdicts_to_sum_to_expected() -> None:
    module = _differential()
    base_a = _stable_base()
    mixed = [row for row in base_a if row["text"] != "t3"]
    mixed = [_row("t9", "changed") if row["text"] == "t9" else row for row in mixed]
    account = module.compare_against_frozen(base_a, mixed, mixed, expected=_UNIVERSE)
    s = account.summary()
    assert s["matched"] + s["different"] + s["missing"] + s["errored"] == s["expected"] == 250
    assert account.passed is False


def test_completeness_outranks_the_corpus_size_bound() -> None:
    """The old guard was a row-count threshold: a 50-turn omission cleared it while reporting zero
    differences. Asserted behaviourally -- an account can be large and still incomplete, and large
    is not a substitute for complete."""
    module = _differential()
    base_a = _stable_base()
    # 240 answered, 10 omitted. Comfortably over any 200-row threshold, and still not a pass.
    kept = [row for row in base_a if row["text"] not in set(_UNIVERSE[:10])]
    account = module.compare_against_frozen(base_a, kept, kept, expected=_UNIVERSE)

    assert account.summary()["matched"] == 240, "a threshold would have been satisfied here"
    assert account.summary()["expected"] == 250
    assert account.summary()["missing"] == 10
    assert account.passed is False, "size is not completeness"

    # And the reverse: a small-but-total account is complete, whatever the corpus bound says.
    tiny_base = [_row("only", "same")]
    tiny = module.compare_against_frozen(tiny_base, tiny_base, tiny_base, expected=["only"])
    assert tiny.is_total is True and tiny.passed is True


# --------------------------------------------------------------------------------------------
# CORPUS AUTHORITY. The candidate supplies RESULTS ONLY.
#
# The differential used to `sys.path.insert(0, candidate)` and import `replay_texts()` from there,
# which handed the candidate authority over what it would be judged on. A candidate that dropped two
# prompts from its own corpus produced 251 corpus / 249 compared / 0 differences, and the two turns
# it removed were never looked for.
# --------------------------------------------------------------------------------------------


def test_the_corpus_is_a_pinned_artifact_the_candidate_does_not_compute() -> None:
    """Reading it from the base is not available -- `035b0dc8` predates this corpus -- so the
    candidate-independent source is a frozen artifact whose digest is pinned in the DRIVER, in a
    different file from the artifact itself."""
    import hashlib
    import json

    module = _differential()
    assert module.FROZEN_CORPUS_SOURCE_SHA == "c606c81b7b7b95abea94731c361037473df109a6", (
        "the corpus must come from the immutable frozen blob object, not candidate HEAD or the "
        "worktree file"
    )
    source = _SCRIPT.read_text(encoding="utf-8")
    # Parsed, not string-matched: the comment explaining WHY it is never called contains the name,
    # and a test that cannot tell a call from a sentence about a call is not measuring anything.
    import ast

    tree = ast.parse(source)
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "replay_texts" not in called, "the driver must never call the candidate's replay_texts()"
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "replay_texts" not in imported, "the driver must not import the candidate's corpus module"
    assert not hasattr(module, "replay_texts"), "the corpus module must not be reachable from the driver"
    assert "sys.path.insert(0, str(candidate))" not in source

    import subprocess

    root = Path(_SCRIPT).resolve().parents[1]
    raw = subprocess.check_output(
        ["/usr/bin/git", "-C", str(root), "show", module.FROZEN_CORPUS_SOURCE_SHA]
    )
    payload = json.loads(raw)
    turns = [str(t) for t in payload["turns"]]
    digest = hashlib.sha256(chr(0).join(turns).encode("utf-8")).hexdigest()

    assert digest == module.FROZEN_CORPUS_SHA256, (
        "the committed corpus artifact no longer matches the digest pinned in the driver; if the "
        "corpus was changed on purpose, re-pin it deliberately and say so"
    )
    assert len(turns) == module.FROZEN_CORPUS_COUNT == 252
    assert len(turns) == len(set(turns)), "prompts are the turn ids, so they must be unique"


def test_a_candidate_that_edits_the_corpus_artifact_is_refused() -> None:
    """Shrinking, growing or rewriting the universe changes the digest, and the digest is checked."""
    import hashlib

    module = _differential()
    artifact = Path(_SCRIPT).resolve().parents[1] / "ops" / "semantic_phase0_frozen_corpus.json"
    import json

    turns = [str(t) for t in json.loads(artifact.read_text(encoding="utf-8"))["turns"]]

    for label, tampered in (
        ("removed one", turns[:-1]),
        ("removed fifty", turns[:-50]),
        ("added a turn", [*turns, "smuggled"]),
        ("rewrote a prompt", [*turns[:-1], "a completely different prompt"]),
        ("reordered", list(reversed(turns))),
    ):
        digest = hashlib.sha256(chr(0).join(tampered).encode("utf-8")).hexdigest()
        assert digest != module.FROZEN_CORPUS_SHA256, f"{label} was not detected by the digest"


def test_loader_refuses_a_trusted_object_that_disagrees_with_the_digest_pin(monkeypatch) -> None:
    module = _differential()
    controller = Path(_SCRIPT).resolve().parents[1]
    monkeypatch.setattr(module, "FROZEN_CORPUS_SHA256", "0" * 64)
    with pytest.raises(module.CorpusIntegrityError, match="does not match the digest"):
        module.load_frozen_corpus(controller)


def test_a_candidate_corpus_cannot_shrink_the_frozen_universe() -> None:
    """The attack, as arithmetic rather than as a two-worktree run.

    Whatever the candidate's own `replay_texts()` says, the frozen universe is the base's. Every
    prompt the candidate declines to answer becomes MISSING, and the denominator does not move.
    """
    module = _differential()
    base_universe = [f"t{i}" for i in range(250)]
    base_a = [_row(text, "same") for text in base_universe]

    for label, candidate_view in (
        ("removed one", base_universe[:-1]),
        ("removed fifty", base_universe[:-50]),
        ("added turns", [*base_universe, "smuggled-a", "smuggled-b"]),
        ("reordered", list(reversed(base_universe))),
        ("renamed an id", [*base_universe[:-1], "t249-renamed"]),
    ):
        rows = [_row(text, "same") for text in candidate_view]
        account = module.compare_against_frozen(base_a, rows, rows, expected=base_universe)
        summary = account.summary()
        assert summary["expected"] == 250, f"{label}: the denominator moved"
        assert summary["accounted"] == 250, f"{label}: not every frozen id got a verdict"
        assert account.is_total is True, label
        if label in {"removed one", "renamed an id"}:
            assert summary["missing"] == 1, label
            assert account.passed is False, label
        if label == "removed fifty":
            assert summary["missing"] == 50 and account.passed is False
        if label in {"added turns", "renamed an id"}:
            assert summary["unexpected"] >= 1, f"{label}: smuggled rows must be rejected"
            assert account.passed is False, label
        if label == "reordered":
            assert summary["matched"] == 250 and account.passed is True, "order is not identity"


def test_the_subprocess_driver_seals_every_connect_surface() -> None:
    """Subprocess hermeticity is not inherited from the in-process fixture.

    The driver template that runs each corpus turn patched `connect` and `create_connection` and left
    `connect_ex` open -- and `connect_ex` returns an errno rather than raising, which is exactly how
    a caller walks through a seal.
    """
    source = _SCRIPT.read_text(encoding="utf-8")
    driver = source[source.index("def _blocked(") : source.index("PUBLIC = __FIELDS__")]
    for surface in ("socket.socket.connect = _blocked",
                    "socket.socket.connect_ex = _blocked",
                    "socket.create_connection = _blocked"):
        assert surface in driver, f"the subprocess driver leaves {surface} unsealed"


def test_the_subprocess_seal_actually_refuses_every_surface_and_family() -> None:
    """Executed, not read: the driver's own seal lines are run in a child interpreter and every
    surface x family is probed there."""
    import subprocess
    import sys
    import textwrap

    source = _SCRIPT.read_text(encoding="utf-8")
    seal = source[source.index("def _blocked("): source.index('sys.path.insert(0, "__TREE__")')]
    probe = textwrap.dedent('''
        import asyncio, multiprocessing, os, socket, subprocess, sys
        {seal}
        escapes = []
        def check(label, fn, *args):
            try:
                fn(*args)
            except OSError:
                return
            except Exception:
                return
            escapes.append(label)
        for fam, addr in ((socket.AF_INET, ("127.0.0.1", 11434)),
                          (socket.AF_INET, ("8.8.8.8", 53)),
                          (socket.AF_INET6, ("::1", 11434, 0, 0))):
            s = socket.socket(fam, socket.SOCK_STREAM)
            check("connect " + str(addr), s.connect, addr)
            check("connect_ex " + str(addr), s.connect_ex, addr)
            s.close()
        if hasattr(socket, "AF_UNIX"):
            u = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            check("connect unix", u.connect, "/tmp/vool-none.sock")
            check("connect_ex unix", u.connect_ex, "/tmp/vool-none.sock")
            u.close()
        check("create_connection", socket.create_connection, ("127.0.0.1", 11434))
        print("ESCAPES:" + repr(escapes))
    ''').format(seal=seal)
    completed = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
    assert "ESCAPES:[]" in completed.stdout, f"subprocess seal escapes: {completed.stdout} {completed.stderr[:200]}"


def test_proof_interpreter_refuses_grandchild_creation() -> None:
    """A descendant cannot influence results because supported Python spawn APIs refuse."""
    import subprocess
    import sys
    import textwrap

    source = _SCRIPT.read_text(encoding="utf-8")
    seal = source[source.index("def _blocked("): source.index('sys.path.insert(0, "__TREE__")')]
    probe = textwrap.dedent('''
        import asyncio, multiprocessing, os, socket, subprocess, sys
        {seal}
        blocked = []
        for label, call in (
            ("subprocess", lambda: subprocess.run([sys.executable, "-c", "print('escape')"])),
            ("multiprocessing", lambda: multiprocessing.Process(target=lambda: None).start()),
            ("system", lambda: os.system("true")),
        ):
            try:
                call()
            except OSError:
                blocked.append(label)
        print("BLOCKED:" + repr(blocked))
    ''').format(seal=seal)
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, completed.stderr
    assert "BLOCKED:['subprocess', 'multiprocessing', 'system']" in completed.stdout


def test_host_state_drift_is_separated_from_a_behaviour_difference() -> None:
    """A value that drifts SLOWLY escapes the base-stability check.

    Measured, not theorised: two base runs minutes apart both said `35.4 GB free`, and by the time
    the candidate ran twenty minutes later the disk said `35.3 GB`. That is host state moving under
    the measurement, not the candidate answering differently. The separation is decided by a fresh
    BASE run of the differing ids -- the candidate supplies nothing to it -- and the id keeps a
    verdict either way, so the counts still sum to `expected`.
    """
    module = _differential()
    base_a = [_row("steady", "same"), _row("disk", "35.4 GB free")]
    cand = [_row("steady", "same"), _row("disk", "35.3 GB free")]
    account = module.compare_against_frozen(base_a, cand, cand, expected=["steady", "disk"])

    assert account.summary()["different"] == 1
    assert account.passed is False, "before the base re-check it is a difference, and blocks"

    # The base is re-driven and no longer reproduces its own earlier answer.
    account.reclassify_host_unstable(
        {"disk": {"response"}}, "the BASE gave a different answer when re-driven"
    )
    summary = account.summary()
    assert summary["different"] == 0
    assert summary["host_unstable"] == 1
    assert summary["accounted"] == summary["expected"] == 2, "every id still holds exactly one verdict"
    assert account.host_unstable and "disk" in account.host_unstable[0]
    assert account.differences == [], "the drift line must leave the differences list"
    assert account.passed is True, "host drift is reported, and does not fail the candidate"


def test_reclassification_cannot_launder_a_real_difference() -> None:
    """Only ids the BASE failed to reproduce may move, and only out of `different`."""
    module = _differential()
    base_a = [_row("a", "one"), _row("b", "two")]
    cand = [_row("a", "CHANGED"), _row("b", "two")]
    account = module.compare_against_frozen(base_a, cand, cand, expected=["a", "b"])
    assert account.summary()["different"] == 1

    # A matched id cannot be reclassified, and neither can an id nobody re-drove.
    account.reclassify_host_unstable({"b": {"response"}}, "not differing")
    assert account.summary()["host_unstable"] == 0, "only differing ids may be reclassified"
    assert account.summary()["matched"] == 1
    assert account.summary()["different"] == 1
    assert account.passed is False


def test_candidate_files_and_import_shadows_have_zero_corpus_authority(tmp_path) -> None:
    """Artifact, helper, pin, cwd, and symlink attacks cannot change the trusted git object."""
    import json

    module = _differential()
    controller = Path(_SCRIPT).resolve().parents[1]
    expected, digest = module.load_frozen_corpus(controller)
    candidate = tmp_path / "candidate"
    (candidate / "ops").mkdir(parents=True)
    (candidate / "scripts").mkdir()
    (candidate / "ops" / "semantic_phase0_frozen_corpus.json").write_text(
        json.dumps({"turns": ["hostile replacement"] * module.FROZEN_CORPUS_COUNT}),
        encoding="utf-8",
    )
    (candidate / "scripts" / "hashlib.py").write_text(
        "raise RuntimeError('candidate hashlib imported')\n", encoding="utf-8"
    )
    (candidate / "json.py").write_text(
        "raise RuntimeError('candidate json imported')\n", encoding="utf-8"
    )
    loaded, loaded_digest = module.load_frozen_corpus(controller)
    assert loaded == expected
    assert loaded_digest == digest == module.FROZEN_CORPUS_SHA256
    assert "hostile replacement" not in loaded


def test_proof_subprocess_freezes_disk_capacity_for_both_trees(tmp_path) -> None:
    """Live free-space movement cannot manufacture four candidate behaviour differences."""
    module = _differential()
    for package in ("apps", "core", "tests"):
        package_dir = tmp_path / package
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "apps" / "vool_agent.py").write_text(
        "import shutil\n"
        "class _Loader: pass\n"
        "class VoolAgent:\n"
        "    def __init__(self, **kwargs): self.context_loader = _Loader()\n"
        "    def start(self): pass\n"
        "    def run_once(self, text, **kwargs):\n"
        "        usage = shutil.disk_usage('/')\n"
        "        return {'response': f'{usage.total}:{usage.used}:{usage.free}'}\n",
        encoding="utf-8",
    )
    (tmp_path / "core" / "policy_engine.py").write_text(
        "_POLICY_CACHE = {}\ndef load(): return {}\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "conftest.py").write_text(
        "def make_stub_context(): return {}\n", encoding="utf-8"
    )

    rows = module._run_corpus(tmp_path, "frozen_disk_probe", ["disk free"])
    total = 500 * 1024**3
    free = 100 * 1024**3
    assert rows[0]["raised"] == ""
    assert rows[0]["fields"]["response"] == repr(f"{total}:{total - free}:{free}")


def test_executable_controller_reexecs_isolated_from_cwd_and_pythonpath(tmp_path) -> None:
    import os
    import subprocess
    import sys

    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "hashlib.py").write_text("raise RuntimeError('shadow imported')\n", encoding="utf-8")
    (hostile / "json.py").write_text("raise RuntimeError('shadow imported')\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(hostile)
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), "--help"],
        cwd=str(hostile),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--candidate" in completed.stdout


def test_mixed_host_drift_never_launders_a_stable_candidate_regression() -> None:
    module = _differential()
    base = {
        "text": "mixed", "raised": "",
        "fields": {"response": "stable-base", "confidence": 0.1, "mode": "clock-10:00"},
    }
    candidate = {
        "text": "mixed", "raised": "",
        "fields": {"response": "stable-regression", "confidence": 0.2, "mode": "clock-10:01"},
    }
    account = module.compare_against_frozen([base], [candidate], [candidate], expected=["mixed"])
    account.reclassify_host_unstable(
        {"mixed": {"confidence", "mode"}}, "clock and disk values moved on base"
    )
    assert account.summary()["different"] == 1
    assert account.summary()["host_unstable"] == 0
    assert len(account.differences) == 1 and "response" in account.differences[0]
    assert account.passed is False
    assert len(account.host_unstable) == 2


def test_base_drift_in_a_different_field_cannot_excuse_candidate_change() -> None:
    module = _differential()
    base = {"text": "q", "raised": "", "fields": {"response": "base", "confidence": 0.1}}
    candidate = {"text": "q", "raised": "", "fields": {"response": "changed", "confidence": 0.1}}
    account = module.compare_against_frozen([base], [candidate], [candidate], expected=["q"])
    account.reclassify_host_unstable({"q": {"confidence"}}, "a different base field moved")
    assert account.summary()["different"] == 1
    assert account.host_unstable == []


def test_candidate_self_drift_is_never_host_reclassified() -> None:
    module = _differential()
    base = [_row("q", "base")]
    first, second = [_row("q", "future-1")], [_row("q", "future-2")]
    account = module.compare_against_frozen(base, first, second, expected=["q"])
    account.reclassify_host_unstable({"q": {"response"}}, "base later moved too")
    assert account.summary()["different"] == 1
    assert account.summary()["host_unstable"] == 0
