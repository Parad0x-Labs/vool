"""Project archaeology (C21): the ``recovery_plan`` op, tested FIRST.

"Can this lost work be recovered?" answered as DATA, never as action:

- The op locates recoverable copies (blackbox journal effect hashes + CAS
  blobs, git objects incl. dangling/unreachable commits and stash, workspace
  copies), verifies integrity, and emits a typed PLAN whose steps each carry
  the source, the authority and the permission an EXECUTOR would need.
- The module itself executes nothing: envelope ``execution: "none"``, plan
  ``execution: "not_performed_read_only_plan"``. No step ever claims the
  permission it names.
- GIT SAFETY: over a git repo the op may run ONLY read-only git verbs
  (status/log/show/cat-file/rev-parse/branch/diff/stash list/fsck). Any
  checkout/reset/clean/restore/apply/push fails the test.
- READ-ONLY LAW: planning leaves the journal, the workspace bytes and the git
  repo byte-and-commit identical.
- HONESTY LAW: when the evidence is hashes-only (no restorable content), the
  plan says so in ``notes`` instead of claiming recovery.
- WITHHELD interop: a governed payload may be *noted*, never read — the
  withheld marker must not appear anywhere in the envelope.

The module does not exist yet (RED); every test imports it lazily inside the
function body so this file always COLLECTS and never breaks sibling packs.
Deterministic: no network, no daemon, no live model.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.project_archaeology.conftest import (
    SECRET_TOKEN,
    WITHHELD_TEXT,
    journal_count,
)

WITHHELD_MARKER = "VERBATIM-WITHHELD-MARKER-91c4"

# The exact provenance block every result item must carry (keys always present).
RESULT_KEYS = frozenset(
    {
        "store", "object_id", "hash", "timestamp", "authority", "verified",
        "turn_id", "session_id", "effect_id", "attempt_id", "path", "kind",
        "outcome", "confidence", "availability", "excerpt", "truncated",
    }
)

# The exact per-step block every plan step must carry.
PLAN_STEP_KEYS = frozenset(
    {"step", "source", "object_id", "hash", "detail", "required_permission"}
)

# Read-only git verbs recovery_plan may invoke. Anything else fails the test.
GIT_READONLY_VERBS = {
    "status", "log", "show", "cat-file", "rev-parse", "branch", "diff",
    "stash list", "fsck",
}


def _import_pa():
    """Lazy import: core.project_archaeology does not exist yet (RED)."""
    from core import project_archaeology as pa

    return pa


def _assert_result_item(item: dict) -> None:
    assert isinstance(item, dict)
    assert set(item) >= RESULT_KEYS, f"missing provenance keys: {RESULT_KEYS - set(item)}"
    assert isinstance(item["object_id"], str) and item["object_id"], item
    assert isinstance(item["authority"], str) and item["authority"], item
    assert item["confidence"] in {"exact", "derived", "partial"}, item
    assert item["availability"] in {"AVAILABLE", "WITHHELD", "ERASED", "UNKNOWN"}, item


def _assert_envelope(pa, out: dict) -> dict:
    """Envelope discipline for recovery_plan: honest, read-only, plan-carrying."""
    assert isinstance(out, dict)
    assert out["schema"] == pa.SCHEMA == "vool.archaeology.v1"
    assert out["operation"] == "recovery_plan"
    assert out["untrusted_content"] is True
    assert out["execution"] == "none"
    assert isinstance(out["query"], dict)
    assert isinstance(out["results"], list)
    assert out["returned"] == len(out["results"])
    assert isinstance(out["truncated"], bool)
    assert isinstance(out["limits"], dict)
    scope = out["scope"]
    assert isinstance(scope, dict) and "workspace_root" in scope
    assert isinstance(scope["refused"], list)
    assert isinstance(out["stores_read"], list)
    assert isinstance(out["redaction"], dict)
    for item in out["results"]:
        _assert_result_item(item)
    plan = out["plan"]
    assert isinstance(plan, dict)
    assert {"reference", "recoverable", "steps", "execution", "notes"} <= set(plan)
    assert plan["execution"] == "not_performed_read_only_plan"
    assert isinstance(plan["recoverable"], bool)
    assert isinstance(plan["steps"], list)
    assert isinstance(plan["notes"], list)
    return plan


def _assert_step_law(plan: dict) -> None:
    """Every step carries the full typed block, numbered 1..n, and names —
    but never claims — the permission an executor would need."""
    for index, step in enumerate(plan["steps"], start=1):
        assert isinstance(step, dict)
        assert set(step) >= PLAN_STEP_KEYS, f"step missing keys: {PLAN_STEP_KEYS - set(step)}"
        assert step["step"] == index, step
        assert isinstance(step["source"], str) and step["source"], step
        assert isinstance(step["object_id"], str) and step["object_id"], step
        assert isinstance(step["hash"], str), step
        assert isinstance(step["detail"], str) and step["detail"], step
        assert isinstance(step["required_permission"], str) and step["required_permission"], step
    dumped = json.dumps(plan, default=str)
    assert "permission_granted" not in dumped, "the plan must never claim the permission itself"
    assert not any("granted" in key for key in plan), "the plan must never claim the permission itself"


# ---------------------------------------------------------------------------
# A. THE HAPPY RECOVERY: the night report
# ---------------------------------------------------------------------------


def test_recovery_plan_eff_report_01_IS_recoverable_with_typed_steps_and_read_only_envelope(corpus) -> None:
    """HAPPY PATH: the lost report is recoverable and the plan is pure plan."""
    pa = _import_pa()
    out = pa.recovery_plan(reference="eff-report-01")
    plan = _assert_envelope(pa, out)
    assert plan["reference"] == "eff-report-01"
    assert plan["recoverable"] is True
    assert plan["steps"], "a recoverable object must yield at least one step"
    _assert_step_law(plan)
    blackbox_items = [i for i in out["results"] if i["store"] == "blackbox"]
    assert blackbox_items, out["results"]
    assert any(i["effect_id"] == "eff-report-01" for i in blackbox_items)
    # the intended.after_sha256 ("ab"*32) is legitimate recoverable hash evidence
    assert any("ab" * 32 in (i["hash"] or "") for i in blackbox_items) or any(
        "ab" * 32 in json.dumps(i["excerpt"] or "") for i in blackbox_items
    ), "the journal's after_sha256 hash evidence must surface somewhere in the blackbox items"


def test_every_plan_step_carries_source_object_hash_detail_AND_a_required_permission(corpus) -> None:
    """STEP LAW: steps are executor contracts — source, object, hash, detail,
    and the permission REQUIRED (never claimed)."""
    pa = _import_pa()
    for kwargs in (
        {"reference": "eff-report-01"},
        {"path": "src/config.py", "workspace_root": str(corpus["git"]["repo"])},
    ):
        plan = _assert_envelope(pa, pa.recovery_plan(**kwargs))
        assert plan["steps"], kwargs
        _assert_step_law(plan)


# ---------------------------------------------------------------------------
# B. TWO-STORE RECOVERY: src/config.py via blackbox effect + git commit
# ---------------------------------------------------------------------------


def test_recovery_plan_src_config_py_spans_TWO_stores_blackbox_AND_git_with_sha_b_evidence(corpus) -> None:
    """TWO STORES: the config edit lives as eff-config-08 in the journal AND as
    commit sha_b in git; the plan must draw on at least two sources."""
    pa = _import_pa()
    repo = corpus["git"]["repo"]
    sha_b = corpus["git"]["sha_b"]
    out = pa.recovery_plan(path="src/config.py", workspace_root=str(repo))
    plan = _assert_envelope(pa, out)
    assert plan["recoverable"] is True

    git_items = [i for i in out["results"] if i["store"] == "git"]
    assert git_items and any(i["hash"] == sha_b for i in git_items), (
        f"sha_b must appear as git evidence: {[(i['store'], i['hash']) for i in out['results']]}"
    )
    blackbox_items = [i for i in out["results"] if i["store"] == "blackbox"]
    assert any(i["effect_id"] == "eff-config-08" for i in blackbox_items), out["results"]

    sources = {step["source"] for step in plan["steps"]}
    assert len(sources) >= 2, f"both stores hold evidence; the plan must use >= 2 sources: {sources}"
    dumped = json.dumps(plan, default=str).lower()
    assert any("git" in s.lower() for s in sources) or "git" in dumped
    assert (
        any("blackbox" in s.lower() or "journal" in s.lower() for s in sources)
        or "blackbox" in dumped
        or "journal" in dumped
    )


def test_recovery_plan_BY_commit_sha_plans_git_object_recovery(corpus) -> None:
    """BY SHA: referencing the commit itself yields git evidence and a plan."""
    pa = _import_pa()
    repo = corpus["git"]["repo"]
    sha_b = corpus["git"]["sha_b"]
    out = pa.recovery_plan(reference=sha_b, workspace_root=str(repo))
    plan = _assert_envelope(pa, out)
    git_items = [i for i in out["results"] if i["store"] == "git"]
    assert git_items and git_items[0]["hash"] == sha_b, out["results"]
    assert plan["recoverable"] is True
    assert plan["steps"]


# ---------------------------------------------------------------------------
# C. THE FAILED PUSH: honesty about what is and is not recoverable
# ---------------------------------------------------------------------------


def test_recovery_plan_eff_push_77_WITHOUT_git_is_HONEST_about_what_is_missing(blackbox) -> None:
    """HONESTY: the failed push left hashes in the journal and no content blob;
    with no git workspace the plan must not claim recovery it cannot back —
    and if it confesses, it must explain what is missing in notes."""
    pa = _import_pa()
    out = pa.recovery_plan(reference="eff-push-77")
    plan = _assert_envelope(pa, out)
    assert out["execution"] == "none"
    if plan["recoverable"] is False:
        assert plan["notes"], "a 'not recoverable' verdict must say why in notes"
        assert any(str(note).strip() for note in plan["notes"])


def test_recovery_plan_eff_push_77_WITH_git_workspace_MAY_claim_recovery_reflecting_reality(corpus) -> None:
    """REALITY: the pushed content still exists as local git objects, so with a
    workspace_root the plan may honestly say recoverable — and then it must
    provide real, permission-carrying steps."""
    pa = _import_pa()
    repo = corpus["git"]["repo"]
    out = pa.recovery_plan(reference="eff-push-77", workspace_root=str(repo))
    plan = _assert_envelope(pa, out)
    if plan["recoverable"] is True:
        assert plan["steps"], "a recovery claim must come with executable-by-someone-else steps"
        _assert_step_law(plan)
    else:
        assert plan["notes"], "refusing recovery with git evidence present still demands an explanation"


# ---------------------------------------------------------------------------
# D. TYPED EMPTINESS AND INPUT ERRORS
# ---------------------------------------------------------------------------


def test_recovery_plan_UNKNOWN_reference_is_TYPED_empty_not_an_exception(blackbox) -> None:
    """TYPED EMPTY: an unknown reference yields an empty, honest plan — no raise."""
    pa = _import_pa()
    out = pa.recovery_plan(reference="no-such-effect-000")
    plan = _assert_envelope(pa, out)
    assert out["returned"] == 0
    assert out["results"] == []
    assert out["truncated"] is False
    assert plan["recoverable"] is False
    assert plan["steps"] == []
    assert plan["notes"], "the plan must explain that nothing was found"


def test_recovery_plan_WITH_no_arguments_RAISES_ArchaeologyInputError(blackbox) -> None:
    """INPUT LAW: a query naming nothing is a typed input error."""
    pa = _import_pa()
    with pytest.raises(pa.ArchaeologyInputError):
        pa.recovery_plan()


# ---------------------------------------------------------------------------
# E. WITHHELD INTEROP: governed payloads are noted, never read
# ---------------------------------------------------------------------------


def test_recovery_plan_WITHHELD_payload_is_never_smuggled_into_the_plan(corpus) -> None:
    """WITHHOLD LAW: locate shows the governed item WITHHELD with an empty
    excerpt; the recovery plan may note it exists but no step may read it and
    the withheld marker must not appear anywhere in the envelope."""
    pa = _import_pa()
    fid = corpus["governance"]["withheld"]["finalization_id"]

    located = pa.locate(fid)
    items = located["results"]
    assert items, "locate must resolve the withheld finalization id"
    assert all(i["availability"] == "WITHHELD" for i in items)
    assert all(i["excerpt"] == "" for i in items)

    out = pa.recovery_plan(reference=fid)
    _assert_envelope(pa, out)
    dumped = json.dumps(out, default=str)
    assert WITHHELD_MARKER not in dumped, "the withheld marker must never enter the recovery envelope"
    assert WITHHELD_TEXT not in dumped, "the withheld text must never enter the recovery envelope"


# ---------------------------------------------------------------------------
# F. GIT SAFETY: read-only verbs only
# ---------------------------------------------------------------------------


def _git_verb(argv: list[str]) -> str:
    """Extract the git subcommand from a git argv (handling global flags)."""
    rest = argv[1:]
    while rest:
        head = rest[0]
        if head == "-C":
            rest = rest[2:]
            continue
        if head.startswith("-"):
            rest = rest[1:]
            continue
        break
    if not rest:
        return ""
    if rest[0] == "stash":
        return "stash list" if len(rest) > 1 and rest[1] == "list" else "stash"
    return rest[0]


def test_git_safety_recovery_plan_invokes_ONLY_read_only_git_verbs(corpus, monkeypatch) -> None:
    """GIT SAFETY: planning over a git repo records every subprocess git call
    and allows only read-only verbs; checkout/reset/clean/restore/apply/push
    fail the test on the spot."""
    pa = _import_pa()
    repo = corpus["git"]["repo"]
    real_run = subprocess.run
    calls: list[tuple[str, list[str]]] = []

    def _recording_run(args, *run_rest, **run_kwargs):
        argv = [str(a) for a in args] if isinstance(args, (list, tuple)) else str(args).split()
        if argv and os.path.basename(argv[0]) == "git":
            verb = _git_verb(argv)
            calls.append((verb, argv))
            if verb not in GIT_READONLY_VERBS:
                raise AssertionError(f"non-read-only git verb invoked by recovery_plan: {argv}")
        return real_run(args, *run_rest, **run_kwargs)

    monkeypatch.setattr(subprocess, "run", _recording_run)

    out = pa.recovery_plan(path="src/config.py", workspace_root=str(repo))
    plan = _assert_envelope(pa, out)
    assert out["execution"] == "none"

    assert calls, "a git source was in play; recovery_plan must have probed git read-only"
    for verb, argv in calls:
        assert verb in GIT_READONLY_VERBS, f"forbidden git verb {verb!r}: {argv}"
    assert plan["recoverable"] is True


# ---------------------------------------------------------------------------
# G. THE READ-ONLY LAW: planning changes nothing
# ---------------------------------------------------------------------------


def test_read_only_law_journal_workspace_and_git_are_BYTE_identical_after_planning(corpus) -> None:
    """READ-ONLY: after planning across references, the journal still has 9
    entries, the report bytes are unchanged, git HEAD is unchanged, the git
    worktree is clean, and the workspace gained and lost no files."""
    pa = _import_pa()
    ws = Path(corpus["workspace_root"])
    repo = Path(corpus["git"]["repo"])

    def _git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60
        )
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    def _ws_snapshot() -> set[str]:
        return {
            str(p.relative_to(ws))
            for p in ws.rglob("*")
            if not p.is_symlink()
        }

    report = ws / "reports" / "night-report.md"
    bytes_before = report.read_bytes()
    head_before = _git("rev-parse", "HEAD")
    entries_before = journal_count(corpus["blackbox_root"])
    assert entries_before == 9
    ws_before = _ws_snapshot()
    assert _git("status", "--porcelain") == ""

    for kwargs in (
        {"reference": "eff-report-01", "workspace_root": str(repo)},
        {"path": "src/config.py", "workspace_root": str(repo)},
        {"reference": corpus["git"]["sha_b"], "workspace_root": str(repo)},
    ):
        _assert_envelope(pa, pa.recovery_plan(**kwargs))

    assert journal_count(corpus["blackbox_root"]) == entries_before, "the journal must not grow"
    assert report.read_bytes() == bytes_before, "the report bytes must be untouched"
    assert _git("rev-parse", "HEAD") == head_before, "HEAD must not move"
    assert _git("status", "--porcelain") == "", "no new files may appear in the git worktree"
    assert _ws_snapshot() == ws_before, "the workspace must gain and lose no files"


# ---------------------------------------------------------------------------
# H. SCOPE LAW: protected paths are refused, nothing is read
# ---------------------------------------------------------------------------


def _plan_tolerating_refusal(pa, **kwargs):
    """Run recovery_plan; a loud ScopeRefusal is returned as None."""
    try:
        return pa.recovery_plan(**kwargs)
    except pa.ScopeRefused:
        return None


def test_scope_law_protected_paths_are_refused_and_nothing_is_read(corpus) -> None:
    """SCOPE: ~/.ssh, the app home and the .env file are never read — the op
    either raises ScopeRefused or returns a refused envelope with a reason and
    zero results; the secret never surfaces."""
    pa = _import_pa()
    ws = Path(corpus["workspace_root"])

    for protected in (
        str(Path.home() / ".ssh"),
        str(corpus["home"]),
        str(ws / ".env"),
    ):
        out = _plan_tolerating_refusal(pa, path=protected, workspace_root=str(ws))
        if out is None:
            continue  # refused loudly: the strongest form of "nothing read"
        plan = _assert_envelope(pa, out)
        assert out["returned"] == 0 and out["results"] == [], f"{protected} yielded results"
        assert plan["recoverable"] is False
        refused = out["scope"]["refused"]
        assert refused, f"{protected} must be disclosed as refused with a reason"
        assert any(str(rec.get("reason", "")).strip() for rec in refused), refused
        assert SECRET_TOKEN not in json.dumps(out, default=str)


# ---------------------------------------------------------------------------
# I. ENVELOPE DISCIPLINE: stores_read is truthful
# ---------------------------------------------------------------------------


def test_stores_read_is_TRUTHFUL_git_absent_until_a_workspace_root_is_given(blackbox, corpus) -> None:
    """TRUTHFUL STORES: journal-only planning reads the journal and nothing
    else; git appears in stores_read only once a workspace root is given."""
    pa = _import_pa()
    journal_only = pa.recovery_plan(reference="eff-report-01")
    _assert_envelope(pa, journal_only)
    stores = {row["store"] for row in journal_only["stores_read"]}
    assert "blackbox" in stores, journal_only["stores_read"]
    assert "git" not in stores, "no workspace root was given; git cannot have been scanned"
    assert "workspace" not in stores, "no workspace root was given; no tree can have been walked"

    with_git = pa.recovery_plan(
        path="src/config.py", workspace_root=str(corpus["git"]["repo"])
    )
    _assert_envelope(pa, with_git)
    stores_now = {row["store"] for row in with_git["stores_read"]}
    assert "git" in stores_now, with_git["stores_read"]
