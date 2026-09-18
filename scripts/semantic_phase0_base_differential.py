"""Does the candidate answer the corpus exactly as the release base does?

The OFF/ON flag comparison this replaces was the weaker claim. It shows instrumentation is inert
when switched off inside ONE build; it says nothing about whether the build as a whole still answers
like `035b0dc8`. A hostile review was right to reject it as the release proof. This runs the corpus
against the **base worktree** and the **candidate worktree** and compares what a user would see.

    .venv/bin/python scripts/semantic_phase0_base_differential.py \\
        --base /path/to/base-worktree --candidate /path/to/candidate-worktree

**Hermetic, per run.** Each tree runs in a subprocess with its own throwaway `VOOL_HOME`, its own
throwaway working directory, every socket refused, and a frozen disk-capacity reading. The latter
matters because four frozen prompts render live free-space values: a 0.1 GB host movement between
the base and candidate phases used to manufacture four behavioural differences. The host home is
never read or written.

**Eligibility is decided by the BASE alone, and frozen before the candidate runs at all.** The
earlier design ran both trees twice and excluded any case unstable in *either*, which a hostile
review broke in one move: a candidate mutation that drifted (it injected the PID into a prompt)
exited successfully, moved its own case into the "unstable" set, and shrank the comparison from 250
to 249 turns. The changed behaviour hid inside the exclusion it had itself caused.

So the candidate cannot vote. The base runs twice; cases that reproduce themselves in the base are
the eligible set; those ids are frozen and printed. The candidate is then run against exactly that
set, twice, and **any** candidate drift, error or difference on an eligible case is a DIFFERENCE --
never an exclusion. A candidate that cannot reproduce itself on a case the base reproduces fine has
told you something, and the something is not "skip me".
"""
from __future__ import annotations

import os as _bootstrap_os
import sys as _bootstrap_sys

# A script directory is normally first on ``sys.path``. A hostile candidate proved that placing a
# ``scripts/hashlib.py`` beside this file could therefore forge the corpus digest before the
# controller had done any work. Re-exec the controller in isolated mode before importing any
# non-builtin module. Tests import the pure helpers normally; the executable proof always runs -I.
if __name__ == "__main__" and not _bootstrap_sys.flags.isolated:
    _clean_env = dict(_bootstrap_os.environ)
    _clean_env.pop("PYTHONPATH", None)
    _clean_env.pop("PYTHONHOME", None)
    _bootstrap_os.execve(
        _bootstrap_sys.executable,
        [_bootstrap_sys.executable, "-I", _bootstrap_os.path.abspath(__file__), *_bootstrap_sys.argv[1:]],
        _clean_env,
    )

import _hashlib
import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path

#: Fields a user or an integrator actually sees. `session_id`/`task_id` are per-run identities and
#: `honesty_receipt`/`source_context` carry hashes and a mutated input dict, so they are compared
#: structurally elsewhere rather than by value here.
PUBLIC_FIELDS = (
    "response",
    "route",
    "route_reason",
    "route_skips",
    "response_class",
    "mode",
    "confidence",
    "fast_path_hit",
    "model_calls",
    "workflow_summary",
    "understanding_confidence",
)

_DRIVER = '''
import asyncio, json, multiprocessing, os, shutil, socket, subprocess, sys, tempfile

# Host capacity is an INPUT to four public corpus answers. Freeze it before importing either tree,
# so the proof compares routing/rendering rather than whatever unrelated write happened between
# its base and candidate phases. These controller literals are identical for both trees; neither
# candidate output nor candidate stability has any authority over them or over eligibility.
_FROZEN_DISK_TOTAL = 500 * 1024**3
_FROZEN_DISK_FREE = 100 * 1024**3

def _frozen_disk_usage(_path):
    return shutil._ntuple_diskusage(
        _FROZEN_DISK_TOTAL,
        _FROZEN_DISK_TOTAL - _FROZEN_DISK_FREE,
        _FROZEN_DISK_FREE,
    )

shutil.disk_usage = _frozen_disk_usage

def _blocked(*_a, **_k):
    raise OSError("network blocked in the base/candidate differential")

# connect_ex RETURNS AN ERRNO instead of raising, so a caller that uses it walked straight through
# a seal that patched only the other two. The in-process fixture closed that months before this
# driver did; subprocess hermeticity is not inherited from it and has to be stated here.
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked

def _blocked_process(*_a, **_k):
    raise OSError("process creation blocked in the base/candidate differential")

# The proof claims this interpreter and its measured result, not an OS sandbox. Refuse every
# supported Python process-creation route so no descendant can contribute bytes to that result.
subprocess.Popen = _blocked_process
multiprocessing.Process.start = _blocked_process
for _name in (
    "fork", "forkpty", "posix_spawn", "posix_spawnp", "system",
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
):
    if hasattr(os, _name):
        setattr(os, _name, _blocked_process)

async def _blocked_async_process(*_a, **_k):
    raise OSError("async process creation blocked in the base/candidate differential")

asyncio.create_subprocess_exec = _blocked_async_process
asyncio.create_subprocess_shell = _blocked_async_process
asyncio.BaseEventLoop.subprocess_exec = _blocked_async_process
asyncio.BaseEventLoop.subprocess_shell = _blocked_async_process

sys.path.insert(0, "__TREE__")
os.chdir(tempfile.mkdtemp(prefix="vool_diff_cwd_"))

from unittest.mock import Mock

from apps.vool_agent import VoolAgent
from core import policy_engine
from tests.conftest import make_stub_context

base = dict(policy_engine.load())
system = dict(base.get("system") or {})
system["allow_web_fallback"] = False
base["system"] = system
policy_engine._POLICY_CACHE = base

PUBLIC = __FIELDS__
TEXTS = __TEXTS__

def agent():
    a = VoolAgent(backend_name="diff", device="diff", persona_id="default")
    a._sync_public_presence = lambda *x, **k: None
    a._start_public_presence_heartbeat = lambda *x, **k: None
    a._start_idle_commons_loop = lambda *x, **k: None
    a.start()
    a.context_loader.load = Mock(return_value=make_stub_context())
    return a

rows = []
for index, text in enumerate(TEXTS):
    session = "diff-%d" % index
    try:
        result = agent().run_once(
            text, session_id_override=session,
            source_context={"surface": "cli", "session_id": session, "runtime_session_id": session},
        )
        rows.append({"text": text, "raised": "", "fields": {k: repr(result.get(k)) for k in PUBLIC}})
    except Exception as exc:
        rows.append({"text": text, "raised": "%s: %s" % (type(exc).__name__, exc), "fields": {}})

print("@@JSON@@" + json.dumps(rows))
'''


def _run_corpus(tree: Path, label: str, texts: list[str]) -> list[dict]:
    """Drive the whole corpus inside `tree`, in its own process and its own throwaway home.

    The corpus arrives as DATA, embedded in the driver source, rather than being imported from the
    tree. The base worktree is a pristine checkout of the release commit and must stay one -- copying
    a corpus file into it to make the import work would mean the base being measured was no longer
    the base.
    """
    with tempfile.TemporaryDirectory(prefix=f"vool_diff_home_{label}_") as home:
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": home,
            "VOOL_HOME": home,
            "VOOL_CREDENTIAL_STORE": "vault",
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": "differential",
            "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
            "VOOL_SKIP_TORCH_GPU_PROBE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        script = (
            _DRIVER.replace("__TREE__", str(tree))
            .replace("__FIELDS__", repr(list(PUBLIC_FIELDS)))
            .replace("__TEXTS__", repr(list(texts)))
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script], cwd=str(tree), env=env, capture_output=True, text=True
        )
    marker = completed.stdout.rfind("@@JSON@@")
    if marker < 0:
        raise SystemExit(
            f"{label}: driver produced no result\n--- stdout ---\n{completed.stdout[-2000:]}\n"
            f"--- stderr ---\n{completed.stderr[-3000:]}"
        )
    return json.loads(completed.stdout[marker + len("@@JSON@@") :])


def _row_key(row: dict) -> tuple:
    return (row["raised"], tuple(sorted(row["fields"].items())))


#: The turn universe this proof is defined over. Pinned in the DRIVER, deliberately in a different
#: file from the artifact it describes, so a candidate that edits its corpus is caught rather than
#: obeyed. Regenerate deliberately (and say so in the commit) if the corpus is ever meant to change.
FROZEN_CORPUS_SHA256 = "b9d1dfb5de50fd2cdaa41e0c82ebfba369a2df1150bf8f44b292acdf74783d02"
FROZEN_CORPUS_COUNT = 252
# The immutable git object that first froze this corpus. The candidate worktree is never read for
# corpus bytes, pins, loader code, or ids. Git's object id is content-addressed; replacing a worktree
# file, symlink, import helper, or pin cannot change these bytes.
FROZEN_CORPUS_SOURCE_SHA = "c23257da1afd53ed721aa01da529ef74bd6df45a"
FROZEN_CORPUS_OBJECT = "ops/semantic_phase0_frozen_corpus.json"


class CorpusIntegrityError(RuntimeError):
    """The turn universe is not the one this proof is defined over."""


def load_frozen_corpus(controller_tree: Path) -> tuple[list[str], str]:
    """Load the universe from an immutable git object in the trusted controller repository."""
    root = Path(controller_tree).resolve()
    git = next(
        (path for path in (Path("/usr/bin/git"), Path("/bin/git"), Path("/usr/local/bin/git")) if path.is_file()),
        None,
    )
    if git is None:
        raise CorpusIntegrityError("trusted git executable is unavailable")
    completed = subprocess.run(
        [str(git), "-C", str(root), "show", f"{FROZEN_CORPUS_SOURCE_SHA}:{FROZEN_CORPUS_OBJECT}"],
        cwd="/",
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "LC_ALL": "C"},
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CorpusIntegrityError(
            "the immutable frozen corpus object is unavailable: "
            + completed.stderr.decode("utf-8", "replace")[:200]
        )
    try:
        frozen = json.loads(completed.stdout.decode("utf-8"))
    except Exception as exc:
        raise CorpusIntegrityError(f"the immutable corpus object is unreadable: {exc}") from exc
    texts = [str(turn) for turn in frozen.get("turns") or []]
    seen = _hashlib.openssl_sha256(chr(0).join(texts).encode("utf-8")).hexdigest()
    if seen != FROZEN_CORPUS_SHA256:
        raise CorpusIntegrityError(
            f"the corpus artifact does not match the digest pinned in this driver.\n"
            f"  pinned : {FROZEN_CORPUS_SHA256}\n  found  : {seen}\n"
            f"  turns  : {len(texts)} (expected {FROZEN_CORPUS_COUNT})"
        )
    if len(texts) != FROZEN_CORPUS_COUNT:
        raise CorpusIntegrityError(f"corpus holds {len(texts)} turns, pinned count is {FROZEN_CORPUS_COUNT}")
    if len(texts) != len(set(texts)):
        raise CorpusIntegrityError("the corpus contains duplicate prompts, so ids are not unique")
    return texts, seen


def freeze_eligibility(texts: list[str], base_a: list[dict], base_b: list[dict]) -> tuple[list[str], list[str]]:
    """Decide what may be compared, from the BASE alone. Returns `(eligible, base_unstable)`.

    A pure function on purpose. The eligibility rule is the one thing in this script a hostile
    candidate has an interest in influencing, so it takes only base rows as input -- there is no
    parameter through which a candidate could reach it -- and it is unit-testable without a
    twenty-minute two-worktree run.
    """
    unstable = [
        left["text"]
        for left, right in zip(base_a, base_b, strict=True)
        if _row_key(left) != _row_key(right)
    ]
    return [text for text in texts if text not in set(unstable)], unstable


#: One verdict per frozen eligible id. Every id gets exactly one, and they sum to `expected`.
VERDICT_MATCHED = "matched"
VERDICT_DIFFERENT = "different"
VERDICT_MISSING = "missing"
VERDICT_ERRORED = "errored"
#: The BASE itself no longer reproduces the answer it gave earlier in this run. Decided by base runs
#: only -- the candidate has no input to it -- and separated from `different` because a live disk
#: reading that moved from 35.4 GB to 35.3 GB while the corpus was driving is host state changing
#: under the measurement, not the candidate answering differently. Counted and reported, never
#: dropped: it is still one of the verdicts that must sum to `expected`.
VERDICT_HOST_UNSTABLE = "host_unstable"


class DifferentialAccount:
    """Total accounting over the FROZEN eligible ids. The candidate cannot shrink any denominator.

    The defect this replaces: the comparison iterated the rows the CANDIDATE returned. A candidate
    that simply omitted one eligible turn produced `compared=249, differences=0, exit 0` -- the
    turn it could not answer left no trace, because nothing was looking for it. Coverage was
    whatever the candidate chose to hand back, which makes the candidate the authority on its own
    proof.

    Here the frozen id list is the authority and iteration runs over IT. Every expected id resolves
    to exactly one verdict -- matched, different, missing, or errored -- and `PASS` requires those
    four to sum to `expected`. Rows the candidate returns that are not expected ids, and ids it
    returns more than once, are counted separately and rejected; they can never substitute for a
    missing one.
    """

    def __init__(self, expected: list[str]) -> None:
        #: Frozen BEFORE the candidate runs and never written again. Ordered, so the report is stable.
        self.expected: tuple[str, ...] = tuple(expected)
        self.verdicts: dict[str, str] = {}
        self.differences: list[str] = []
        self.unexpected: list[str] = []
        self.duplicates: list[str] = []
        self.host_unstable: list[str] = []
        self._difference_claims: dict[str, dict[str, str]] = {}
        self._host_reclassifiable: set[str] = set()

    # -- counts, all derived from `expected`, none from candidate output ---------------------
    @property
    def expected_count(self) -> int:
        return len(self.expected)

    def count(self, verdict: str) -> int:
        return sum(1 for value in self.verdicts.values() if value == verdict)

    @property
    def accounted(self) -> int:
        return len(self.verdicts)

    @property
    def is_total(self) -> bool:
        """Every expected id has exactly one verdict. This is the completeness proof."""
        return self.accounted == self.expected_count and set(self.verdicts) == set(self.expected)

    @property
    def passed(self) -> bool:
        return (
            self.is_total
            and not self.unexpected
            and not self.duplicates
            and self.count(VERDICT_DIFFERENT) == 0
            and self.count(VERDICT_MISSING) == 0
            and self.count(VERDICT_ERRORED) == 0
        )

    def add_difference(self, text: str, claim: str, message: str, *, host_reclassifiable: bool) -> None:
        self._difference_claims.setdefault(text, {})[claim] = message
        if host_reclassifiable:
            self._host_reclassifiable.add(text)
        self._refresh_differences()

    def _refresh_differences(self) -> None:
        self.differences = [
            claims[claim]
            for text in self.expected
            for claims in (self._difference_claims.get(text, {}),)
            for claim in sorted(claims)
        ]

    def reclassify_host_unstable(
        self, unstable_claims: Mapping[str, Collection[str]], reason: str
    ) -> None:
        """Excuse only exact claims that a fresh BASE run proved unstable.

        A row containing one volatile value and one stable candidate regression stays DIFFERENT.
        Candidate self-drift is never reclassifiable: base volatility cannot make two candidate
        runs agreeing with neither other become equivalent.
        """
        for text, claims in unstable_claims.items():
            if self.verdicts.get(text) != VERDICT_DIFFERENT or text not in self._host_reclassifiable:
                continue
            row_claims = self._difference_claims.get(text, {})
            excused = sorted(set(row_claims) & {str(claim) for claim in claims})
            for claim in excused:
                row_claims.pop(claim, None)
                self.host_unstable.append(f"{text!r}: {claim}: {reason}")
            if excused and not row_claims:
                self.verdicts[text] = VERDICT_HOST_UNSTABLE
        self._refresh_differences()

    def summary(self) -> dict:
        return {
            "expected": self.expected_count,
            "matched": self.count(VERDICT_MATCHED),
            "different": self.count(VERDICT_DIFFERENT),
            "missing": self.count(VERDICT_MISSING),
            "errored": self.count(VERDICT_ERRORED),
            "host_unstable": self.count(VERDICT_HOST_UNSTABLE),
            "unexpected": len(self.unexpected),
            "duplicates": len(self.duplicates),
            "accounted": self.accounted,
            "total": self.is_total,
        }


def _index_candidate(rows: list[dict], account: DifferentialAccount) -> dict[str, dict]:
    """Index candidate rows by id, recording duplicates and unexpected ids rather than absorbing them.

    A duplicate is NOT resolved by keeping one of them: two rows for one id with conflicting payloads
    means the candidate answered the same turn twice differently, and picking a winner would be the
    harness deciding what the candidate meant. Both are rejected and the id is left to resolve as
    errored.
    """
    expected = set(account.expected)
    indexed: dict[str, dict] = {}
    seen: set[str] = set()
    for row in rows:
        text = row.get("text")
        if text not in expected:
            account.unexpected.append(f"{text!r}: candidate returned a case that is not a frozen eligible id")
            continue
        if text in seen:
            account.duplicates.append(f"{text!r}: candidate returned this frozen id more than once")
            indexed.pop(text, None)
            continue
        seen.add(text)
        indexed[text] = row
    return indexed


def compare_against_frozen(
    base_a: list[dict], cand_a: list[dict], cand_b: list[dict], expected: list[str] | None = None
) -> DifferentialAccount:
    """Account for every frozen eligible id exactly once.

    `expected` is the frozen id list. It defaults to the base's own ids only so older callers keep
    working; the driver always passes the frozen set explicitly, because that is the authority.
    """
    frozen = list(expected) if expected is not None else [row["text"] for row in base_a]
    account = DifferentialAccount(frozen)

    base_by_text = {row["text"]: row for row in base_a}
    first = _index_candidate(cand_a, account)
    second = _index_candidate(cand_b, account) if cand_b is not cand_a else first

    for text in account.expected:
        base_row = base_by_text.get(text)
        row_a = first.get(text)
        row_b = second.get(text)

        # MISSING: the candidate did not answer a turn the base answered twice identically. This is
        # the verdict the old design had no way to express, so it produced silence instead.
        if row_a is None or row_b is None:
            account.verdicts[text] = VERDICT_MISSING
            account.add_difference(
                text,
                "missing",
                f"{text!r}: the candidate returned no result for a frozen eligible turn",
                host_reclassifiable=False,
            )
            continue
        if base_row is None:
            account.verdicts[text] = VERDICT_ERRORED
            account.add_difference(
                text, "base_missing", f"{text!r}: frozen as eligible but the base has no row for it",
                host_reclassifiable=False,
            )
            continue

        # The candidate must reproduce ITSELF on a case the base reproduces. Drift is a difference,
        # never grounds for exclusion -- a mutation once drifted its own case out of the comparison.
        if _row_key(row_a) != _row_key(row_b):
            account.verdicts[text] = VERDICT_DIFFERENT
            account.add_difference(
                text,
                "candidate_self_drift",
                f"{text!r}: the CANDIDATE did not reproduce itself on a case the base "
                f"reproduces -- that is a difference, not grounds for exclusion",
                host_reclassifiable=False,
            )
            continue

        if _row_key(base_row) == _row_key(row_a):
            account.verdicts[text] = VERDICT_MATCHED
            continue

        account.verdicts[text] = VERDICT_DIFFERENT
        if base_row["raised"] != row_a["raised"]:
            account.add_difference(
                text, "raised",
                f"{text!r}: raised base={base_row['raised']!r} cand={row_a['raised']!r}",
                host_reclassifiable=True,
            )
        for field in PUBLIC_FIELDS:
            if base_row["fields"].get(field) != row_a["fields"].get(field):
                account.add_difference(
                    text,
                    field,
                    f"{text!r}: {field}: base={base_row['fields'].get(field)} "
                    f"cand={row_a['fields'].get(field)}",
                    host_reclassifiable=True,
                )
    return account


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    # Resolved, not taken as given. Each corpus run executes in its own process with its own cwd, so
    # a relative `--candidate .` reached the driver as a path that no longer pointed anywhere and the
    # candidate half failed with `No module named 'apps'` -- a run that looked like a tooling hiccup
    # rather than what it was: the candidate never being measured at all.
    base, candidate = Path(args.base).resolve(), Path(args.candidate).resolve()
    for label, tree in (("base", base), ("candidate", candidate)):
        if not (tree / "apps" / "vool_agent.py").is_file():
            print(f"{label} tree {tree} does not hold apps/vool_agent.py", file=sys.stderr)
            return 2
    print(f"base      : {base}")
    print(f"candidate : {candidate}")

    # THE CORPUS IS A FROZEN ARTIFACT WITH A PINNED DIGEST, not something the candidate computes.
    #
    # The driver used to put the CANDIDATE on `sys.path` and import `replay_texts()` from there,
    # which handed the candidate authority over what it would be judged on: a candidate that dropped
    # two prompts from its own corpus produced 251 corpus / 249 compared / 0 differences, and the two
    # turns it removed were never looked for.
    #
    # Reading it from the base instead is not available -- `035b0dc8` predates this corpus, so the
    # base has no such module. The other candidate-independent source is a trusted frozen artifact,
    # so that is what this is: the turn universe is committed to
    # `ops/semantic_phase0_frozen_corpus.json` and its digest is pinned HERE, in the driver, in a
    # different file from the corpus. Shrinking the universe now requires editing both and still
    # fails, because the digest below is what the run checks. `replay_texts()` is never called.
    try:
        texts, seen_digest = load_frozen_corpus(Path(__file__).resolve().parents[1])
    except CorpusIntegrityError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print("corpus artifact        : semantic_phase0_frozen_corpus.json (digest pinned in this driver)")
    print(f"corpus digest          : {seen_digest[:16]}  ({len(texts)} turns)")

    # PHASE 1 -- eligibility, from the BASE only. The candidate has not run yet and cannot influence
    # this set by any behaviour it has.
    base_a, base_b = _run_corpus(base, "base_a", texts), _run_corpus(base, "base_b", texts)
    base_by_text = {row["text"]: row for row in base_a}
    eligible, ineligible = freeze_eligibility(texts, base_a, base_b)
    print(f"\neligible (frozen from base): {len(eligible)} of {len(texts)}")
    if ineligible:
        print("base-unstable, excluded before the candidate ran:")
        for text in ineligible:
            print(f"  {text!r}")

    # PHASE 2 -- the candidate runs against exactly that frozen set, twice. Its own drift is a
    # DIFFERENCE, never an exclusion.
    cand_a = _run_corpus(candidate, "cand_a", eligible)
    cand_b = _run_corpus(candidate, "cand_b", eligible)

    account = compare_against_frozen(base_a, cand_a, cand_b, expected=eligible)

    # A value that drifts SLOWLY escapes the base-stability check: two base runs minutes apart agree,
    # and by the time the candidate runs twenty minutes later the disk has moved. Measured, not
    # theorised -- two turns reported 35.4 GB free from the base and 35.3 GB from the candidate. So
    # any id that differs is re-driven ON THE BASE, now, and one the base can no longer reproduce is
    # host state changing under the measurement rather than the candidate answering differently.
    # Decided by base runs only; the candidate has no input to it.
    differing = [text for text, verdict in account.verdicts.items() if verdict == "different"]
    if differing:
        print(f"\nre-driving {len(differing)} differing turn(s) on the BASE to separate host drift...")
        base_recheck = {row["text"]: row for row in _run_corpus(base, "base_recheck", differing)}
        drifted: dict[str, set[str]] = {}
        for text in differing:
            if text not in base_recheck:
                continue
            original, fresh = base_by_text[text], base_recheck[text]
            claims: set[str] = set()
            if original["raised"] != fresh["raised"]:
                claims.add("raised")
            claims.update(
                field
                for field in PUBLIC_FIELDS
                if original["fields"].get(field) != fresh["fields"].get(field)
            )
            if claims:
                drifted[text] = claims
        if drifted:
            account.reclassify_host_unstable(
                drifted, "the BASE gave a different answer when re-driven, so this is host state moving"
            )

    summary = account.summary()
    differences = account.differences

    total = len(texts)
    print(f"\ncorpus turns           : {total}")
    print(f"base-unstable (frozen) : {len(ineligible)}")
    print(f"expected (frozen ids)  : {summary['expected']}")
    print(f"  matched              : {summary['matched']}")
    print(f"  different            : {summary['different']}")
    print(f"  missing              : {summary['missing']}")
    print(f"  errored              : {summary['errored']}")
    print(f"  host-unstable        : {summary['host_unstable']}  (base could not reproduce itself)")
    print(f"accounted              : {summary['accounted']}  (total={summary['total']})")
    print(f"unexpected rows        : {summary['unexpected']}")
    print(f"duplicate rows         : {summary['duplicates']}")
    print(f"public-path differences: {len(differences)}")
    for line in differences[:20]:
        print(f"  {line}")
    for line in account.host_unstable[:10]:
        print(f"  {line}")
    for line in (account.unexpected + account.duplicates)[:10]:
        print(f"  {line}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(
                {
                    "schema": "base_candidate_differential_v2",
                    "corpus_turns": total,
                    "unstable": ineligible,
                    "accounting": summary,
                    "differences": differences,
                    "host_unstable": account.host_unstable,
                    "unexpected": account.unexpected,
                    "duplicates": account.duplicates,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # COMPLETENESS FIRST. The old guard was a row-count threshold, which is not a
    # completeness proof: a candidate that omitted 50 eligible turns still cleared it and reported
    # zero differences. What has to hold is that every frozen id resolved to exactly one verdict.
    if not account.is_total:
        unaccounted = [text for text in account.expected if text not in account.verdicts]
        print(
            f"\nFAIL: {summary['accounted']} of {summary['expected']} frozen ids were accounted for; "
            f"unaccounted: {unaccounted[:10]}"
        )
        return 2
    if account.unexpected or account.duplicates:
        print(f"\nFAIL: candidate returned {len(account.unexpected)} unexpected and "
              f"{len(account.duplicates)} duplicate rows; a proof cannot rest on rows nobody asked for")
        return 2
    if summary["missing"] or summary["errored"]:
        print(f"\nFAIL: {summary['missing']} missing and {summary['errored']} errored frozen ids")
        return 2
    # Kept as a sanity bound on the CORPUS, not as the completeness guard it used to stand in for.
    if summary["expected"] < 200:
        print(f"\nFAIL: only {summary['expected']} turns were eligible; the proof needs at least 200")
        return 2
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
