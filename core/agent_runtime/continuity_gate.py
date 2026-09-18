"""The semantic continuity gate: an action must be about the thing it was asked about.

The drift this closes had nothing to do with routing. The follow-up reached the right lane, the
right file and the right model, and the model was handed the right finding — and then wrote a test
for something else, which the runtime wrote to disk, executed, and scored. Nothing compared the
artifact to the claim. "Generate a test for X" and "this is a test for X" were the same statement.

So a continuation derives a TASK CONTRACT — action, target, claimed mechanism, expected observable
failure, whether production may be touched — and every artifact is checked against that contract
BEFORE it is written or run. A rejected artifact costs one correction call; it never costs a file
in someone's workspace or a verdict built on the wrong evidence.

Four ways an artifact fails its contract, and each one was observed live:

* **off_target** — it exercises a different behaviour than the claim (the incident: an
  IP/decompression finding "proved" by an empty-input test).
* **backwards_oracle** — it asserts the DEFECT as the expected result. A reproduction must fail on
  the current code, so it must assert the CORRECT contract; a test asserting the bug passes when
  the bug is present, and its exit status means the opposite of what the scorer reads.
* **contradictory_oracles** — two assertions in one file demand opposite outcomes from the same
  call, so the file cannot be evidence for either.
* **mutates_target** — it edits the subject rather than observing it.

Everything here is derived from the contract text at runtime. There is no list of bug classes, no
filename, no language-specific rule beyond "identifiers and literals are tokens", so a finding
about a Rust parser or a SQL migration is gated exactly like a Python codec.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

# --- rejection reason codes -------------------------------------------------------------------
OFF_TARGET = "off_target"
BACKWARDS_ORACLE = "backwards_oracle"
CONTRADICTORY_ORACLES = "contradictory_oracles"
MUTATES_TARGET = "mutates_target"
EMPTY_ARTIFACT = "empty_artifact"

# Identifiers, hex/byte literals, and numbers. Deliberately language-agnostic.
_TOKEN_RE = re.compile(r"0[xX][0-9a-fA-F]+|\\x[0-9a-fA-F]{2}|[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Words that carry no claim: English glue, plus the vocabulary every test file has regardless of
# what it tests. A token here can never be the thing that makes an artifact "on target".
_STOPWORDS = frozenset(
    ["the", "a", "an", "and", "or", "but", "not", "for", "with", "that", "this", "these", "those", "from", "into", "when", "then", "than", "while", "are", "was", "were", "has", "have", "had", "its", "it", "is", "be", "been", "being", "will", "would", "can", "could", "may", "might", "must", "all", "any", "some", "each", "other", "another", "same", "such", "only", "just", "also", "very", "more", "most", "less", "least", "of", "to", "in", "on", "at", "by", "as", "if", "it's", "dont", "don't", "do", "does", "did", "no", "yes", "so", "up", "out", "over", "under", "test", "tests", "testing", "tested", "case", "cases", "self", "assert", "asserts", "assertion", "assertions", "unittest", "pytest", "def", "class", "import", "imports", "from", "return", "returns", "returned", "method", "methods", "function", "functions", "call", "calls", "called", "run", "runs", "running", "file", "files", "line", "lines", "code", "source", "module", "modules", "bug", "bugs", "issue", "issues", "problem", "problems", "defect", "defects", "error", "errors", "err", "name", "names", "value", "values", "data", "result", "results", "output", "outputs", "input_", "arg", "args", "kwargs", "sys", "os", "path", "paths", "spec", "loader", "importlib", "exec", "insert", "main", "true", "false", "none", "null", "should", "must", "expect", "expected", "actual", "given", "when_", "setup", "teardown", "fixture", "mock", "patch"]
)

# Verb families that decide POLARITY: does the claim say the code wrongly raises, or wrongly
# fails to raise? The two demand opposite oracles, and getting it backwards is how a disproof
# was scored as a proof.
_RAISE_WORDS = ("raise", "raises", "raising", "throw", "throws", "thrown", "crash", "crashes", "errors out")
_SWALLOW_RE = re.compile(
    r"\b(?:raises?|throws?)\s+(?:no|nothing|none)\b"
    r"|\bwithout\s+(?:raising|throwing|an?\s+(?:error|exception))\b"
    r"|\bno\s+(?:error|exception)\s+(?:is\s+)?(?:raised|thrown)\b"
    r"|\b(?:silent(?:ly)?|swallow(?:s|ed|ing)?|suppress(?:es|ed|ing)?)\b"
    r"|\bdoes\s+not\s+(?:raise|throw|fail|reject)\b"
    r"|\bfails?\s+to\s+(?:raise|throw|reject)\b",
    re.IGNORECASE,
)
_EXCEPTION_NAME_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Fault))\b")

# `assertRaises(X)` / `pytest.raises(X)` / `expect(...).toThrow(X)` — the "an exception is correct"
# oracle, in the three shapes this runtime actually writes.
_EXPECTS_RAISE_RE = re.compile(
    r"assertRaises(?:Regex)?\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)"
    r"|(?:pytest\.)?raises\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)"
    r"|toThrow(?:Error)?\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)?",
)
_VALUE_ASSERT_RE = re.compile(
    r"\bassert(?:Equal|NotEqual|Is|IsNot|IsNone|IsNotNone|True|False|In|NotIn|Greater|Less)\b"
    r"|\bassert\s+(?!.*\braises\b)",
)
# One call expression: `something.name(args)` — the (symbol, arguments) pair an oracle is about.
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*)\)")
_METHOD_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
# Boilerplate the proof preamble always contains; it describes the harness, never the claim.
_PREAMBLE_LINE_RE = re.compile(
    r"^\s*(?:import\b|from\b|sys\.path|_spec\s*=|.*module_from_spec|.*exec_module|"
    r"if\s+__name__|\s*unittest\.main)",
)
_WRITE_MODE_RE = re.compile(r"""open\s*\([^)]*['"][rbt]*[wa]\+?[rbt]*['"]""")
# "line 117", "lines 143-152", "at line 55" — a coordinate, not a trigger.
_PROSE_LINE_REFERENCE_RE = re.compile(
    r"\blines?\s+(\d+)(?:\s*(?:[-–]|to|and)\s*(\d+))?", re.IGNORECASE
)


def _stem(word: str) -> str:
    """A crude, deliberate stem: enough that "raise"/"raises"/"raising" and "byte"/"bytes" agree.

    Not linguistics — a reworded restatement of a disproved claim must not read as a new claim, and
    that only needs plural/participle collapse plus the raise/throw synonym. The rules matter in
    this order: `decompress` must not lose its final `s` (hence the `ss` guard) while
    `decompressing` must lose its `ing`, and the trailing-`e` drop is what makes the two families
    `raise`/`raises`/`raising` land on one token.
    """
    token = word.lower()
    if token in {"throw", "throws", "thrown", "throwing"}:
        token = "raise"
    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif token.endswith("ing") and len(token) > 5:
        token = token[:-3]
    elif (token.endswith("ed") and len(token) > 4) or (token.endswith("es") and len(token) > 4 and not token.endswith("sses")):
        token = token[:-2]
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        token = token[:-1]
    if token.endswith("e") and len(token) > 3:
        token = token[:-1]
    return token


def claim_tokens(*texts: str) -> frozenset[str]:
    """The meaning-bearing tokens of a claim or an artifact.

    Identifiers are also split on camelCase and underscores, so `IndexError` contributes `index`
    and `error` as well as `indexerror`, and `test_decompress_empty_bytes` contributes its parts.
    That is what lets a claim stated in prose and a claim stated in a method name be compared.
    """
    found: set[str] = set()
    for text in texts:
        for raw in _TOKEN_RE.findall(str(text or "")):
            if raw.lower().startswith("0x") or raw.startswith("\\x"):
                # A hex literal and its prose form ("0x80", "b'\\x80'") must agree.
                found.add(raw.lower().lstrip("\\").removeprefix("0x").removeprefix("x").lstrip("0") or "0")
                continue
            if raw.isdigit():
                found.add(str(int(raw)))
                continue
            parts = [piece for piece in _CAMEL_SPLIT_RE.split(raw) for piece in piece.split("_") if piece]
            for piece in [raw, *parts]:
                stem = _stem(piece)
                if len(stem) > 2 and stem not in _STOPWORDS and piece.lower() not in _STOPWORDS:
                    found.add(stem)
    return frozenset(found)


def claim_signature(title: str, scenario: str = "") -> frozenset[str]:
    """The identity of "the same semantic claim", robust to rewording."""
    return claim_tokens(title, scenario)


# Directory names that mean "this path is a vendored/installed dependency copy, not the operator's
# own source" -- SCALPEL fix, failure class fixture-safety, 2026-08-06. General on purpose: the
# incident found TWO real, differently-behaved copies of the same-named file in one session (one
# genuinely fixed, one still carrying the original defect) because a workspace's own `.venv` sat
# inside the bound root and target resolution had no way to prefer the operator's real source over
# an installed package that happened to share a path. Never file-specific.
_VENDOR_DIR_NAMES = frozenset(
    {".venv", "venv", "env", "site-packages", "dist-packages", "node_modules", "vendor",
     ".tox", "__pycache__", ".git", "dist-info", "egg-info"}
)


def is_vendored_dependency_path(path: str) -> bool:
    """Whether ``path`` sits inside a vendored/installed-dependency directory.

    A path-segment check, not a suffix/prefix guess: ``.venv`` must be an actual directory
    component (``api/.venv/x.py``), not a substring of a real project directory that happens to
    contain the letters (``api/venv_config/x.py`` is NOT vendored).
    """
    normalized = str(path or "").replace("\\", "/")
    segments = [seg for seg in normalized.split("/") if seg]
    return any(seg in _VENDOR_DIR_NAMES or seg.endswith(".egg-info") for seg in segments)


def vendored_path_collisions(paths: tuple[str, ...] | list[str]) -> tuple[tuple[str, str], ...]:
    """Pairs of (vendored_path, real_path) sharing the same basename in the same file inventory.

    This is the concrete shape of "ambiguous workspace binding": the SAME filename resolvable to
    two different, potentially differently-behaved files, with nothing in the citation alone to
    say which one a model meant. Detected, never silently resolved one way -- see this function's
    caller for what "visible and deterministic" means in practice (a blocked decision, not a guess).
    """
    import posixpath
    from collections import defaultdict

    by_basename: dict[str, list[str]] = defaultdict(list)
    for path in paths or ():
        by_basename[posixpath.basename(str(path))].append(str(path))
    collisions: list[tuple[str, str]] = []
    for _basename, candidates in by_basename.items():
        vendored = [p for p in candidates if is_vendored_dependency_path(p)]
        real = [p for p in candidates if not is_vendored_dependency_path(p)]
        for v in vendored:
            for r in real:
                collisions.append((v, r))
    return tuple(collisions)


def git_head_sha(repo_dir: str, *, short: bool = True) -> str:
    """The checked-out commit of ``repo_dir``, or "" when it cannot be determined.

    Best-effort and read-only (``git rev-parse``, never a mutating command): used only to STAMP
    which source checkout a proof/fixture actually ran against, for the audit trail this repair's
    production regression tests need — never a gate a turn's outcome depends on, so a machine
    without git on PATH degrades to an empty string rather than failing the turn.
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        return ""
    try:
        args = ["git", "rev-parse", "--short=10", "HEAD"] if short else ["git", "rev-parse", "HEAD"]
        result = subprocess.run(
            args, cwd=repo_dir, capture_output=True, text=True, timeout=2, check=False
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def candidate_id_for(*, target: str, title: str, scenario: str = "", category: str = "") -> str:
    """A stable identity for a candidate, from its normalized claim plus target and category.

    Deliberately NOT derived from nomination round or model: the same underlying claim, renominated
    in a later round under reworded text, must resolve to the SAME id — that identity is what lets
    the runtime recognize "this is the candidate that already stands as the headline finding"
    instead of treating a reworded resurfacing as a brand-new row (SCALPEL fixture, failure class D).
    Uses `claim_signature`'s own token set rather than the raw title/scenario strings, so wording
    drift that `claim_signature`/`same_claim` already treat as the same claim also hashes the same.
    """
    signature = claim_signature(title, scenario)
    seed = "\x1f".join([str(target or ""), str(category or ""), *sorted(signature)])
    return "cand-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def same_claim(
    signature: frozenset[str],
    other: frozenset[str],
    *,
    min_overlap: int = 3,
    containment: float = 0.6,
) -> bool:
    """Whether two claims allege the same thing.

    Containment of the SMALLER signature, not Jaccard: a restatement that adds detail
    ("...before decoding") should still match the claim it restates, and Jaccard punishes exactly
    that. The absolute floor stops two three-word claims from matching on one shared token.
    """
    if not signature or not other:
        return False
    shared = len(signature & other)
    if shared < min_overlap:
        return False
    smaller = min(len(signature), len(other))
    return smaller > 0 and (shared / smaller) >= containment


def matches_any_claim(signature: frozenset[str], banned: tuple[frozenset[str], ...]) -> bool:
    return any(same_claim(signature, item) for item in banned)


def claim_polarity(*texts: str) -> str:
    """``"swallows"`` when the claim is that the code wrongly does NOT fail; ``"raises"`` when the
    claim is that it wrongly does; ``""`` when the claim is about a value, not an exception.

    This decides which oracle is correct, and it is read from the claim's own words rather than
    from any notion of bug class.
    """
    body = " ".join(str(text or "") for text in texts)
    if _SWALLOW_RE.search(body):
        return "swallows"
    lowered = body.lower()
    if any(word in lowered for word in _RAISE_WORDS) or _EXCEPTION_NAME_RE.search(body):
        return "raises"
    return ""


def claimed_exceptions(*texts: str) -> frozenset[str]:
    body = " ".join(str(text or "") for text in texts)
    return frozenset(_EXCEPTION_NAME_RE.findall(body))


def _literal_triggers(texts: tuple[str, ...], *, exclude: frozenset[str]) -> frozenset[str]:
    """The concrete input values a claim names — the strongest evidence of what it is about.

    A claim that says "a trailing 0x80 varint returns the first 89 bytes" is a claim about that
    input. An artifact that never mentions it is exercising something else, however much generic
    vocabulary the two share. Line numbers are excluded: those cite the claim, they do not trigger
    it.
    """
    found: set[str] = set()
    for text in texts:
        for raw in _TOKEN_RE.findall(str(text or "")):
            if raw.lower().startswith("0x") or raw.startswith("\\x"):
                found.add(raw.lower().lstrip("\\").removeprefix("0x").removeprefix("x").lstrip("0") or "0")
            elif raw.isdigit():
                found.add(str(int(raw)))
    return frozenset(found - exclude)


@dataclass(frozen=True)
class TaskContract:
    """What a follow-up action is bound to. Derived once, checked against every artifact."""

    action: str = "reproduce"
    target_files: tuple[str, ...] = ()
    target_symbols: tuple[str, ...] = ()
    claimed_mechanism: str = ""
    expected_observable_failure: str = ""
    production_modification_allowed: bool = False
    banned_signatures: tuple[frozenset[str], ...] = ()
    # Numbers that describe WHERE the claim points rather than WHAT makes it fail: the cited line
    # range, and any literal already present in the cited source line. A claim about attempt 30 of
    # `const delay = 2 ** attempt` is triggered by 30, not by the 2 it quotes from the code.
    excluded_literals: frozenset[str] = frozenset()

    @property
    def signature(self) -> frozenset[str]:
        return claim_tokens(
            self.claimed_mechanism, self.expected_observable_failure, *self.target_symbols
        )

    @property
    def polarity(self) -> str:
        return claim_polarity(self.claimed_mechanism, self.expected_observable_failure)

    @property
    def literal_triggers(self) -> frozenset[str]:
        return _literal_triggers(
            (self.claimed_mechanism, self.expected_observable_failure), exclude=self.excluded_literals
        )

    def describe(self) -> str:
        """One line for the receipt and for the correction prompt."""
        where = ", ".join(self.target_files) or "the inspected source"
        return (
            f"{self.action} the claim `{self.claimed_mechanism[:160]}` in {where}; "
            f"observable failure: {self.expected_observable_failure[:200]}"
        )


def contract_from_finding(
    *,
    action: str = "reproduce",
    title: str,
    file: str,
    cited_line_text: str = "",
    line_start: int = 0,
    line_end: int = 0,
    failure_scenario: str = "",
    production_modification_allowed: bool = False,
    banned_signatures: tuple[frozenset[str], ...] = (),
) -> TaskContract:
    """The contract a continuation inherits from the finding it is about."""
    citations = {str(int(value)) for value in (line_start, line_end) if value}
    citations |= _literal_triggers((cited_line_text,), exclude=frozenset())
    # …and every line the scenario CITES in prose. Measured live 2026-08-01: a finding at 55-119
    # whose scenario said "reaches line 117 where blob.startswith(...)" made `117` a required test
    # input, and a perfectly good reproduction was refused for "never exercises the input the
    # finding names (117)". A line number says WHERE the claim points; it is never WHAT makes it
    # fail, and only the declared range was being stripped.
    citations |= {
        str(int(number))
        for match in _PROSE_LINE_REFERENCE_RE.finditer(f"{title} {failure_scenario}")
        for number in match.groups()
        if number
    }
    symbols = tuple(item for item in (cited_line_text.strip(), f"{line_start}-{line_end}") if item)
    return TaskContract(
        action=action,
        target_files=(file,) if file else (),
        target_symbols=symbols,
        claimed_mechanism=title,
        expected_observable_failure=failure_scenario,
        production_modification_allowed=production_modification_allowed,
        banned_signatures=tuple(banned_signatures),
        excluded_literals=frozenset(citations),
    )


@dataclass
class ArtifactVerdict:
    """Whether a proposed artifact may be written and executed under its contract."""

    accepted: bool = True
    reason_code: str = ""
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def correction_sentence(self) -> str:
        """What the model is told so its next attempt is a correction, not a re-roll."""
        if self.accepted:
            return ""
        return (
            f"Your previous test was rejected before it was written or run: {self.detail} "
            "Write a test that exercises exactly the claimed mechanism, asserts the CORRECT "
            "behaviour (so it FAILS on the current code), and contains one oracle only."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason_code": self.reason_code,
            "detail": self.detail,
            **({"evidence": dict(self.evidence)} if self.evidence else {}),
        }


def _artifact_claim_body(source: str) -> str:
    """The artifact minus its harness boilerplate — the part that states what is being asserted."""
    return "\n".join(
        line for line in str(source or "").splitlines() if not _PREAMBLE_LINE_RE.match(line)
    )


def _oracles(source: str) -> list[tuple[str, str, bool]]:
    """(method, call-signature, expects_exception) for every assertion in the artifact.

    The call signature is `symbol(args)` with whitespace collapsed, so two assertions about the
    same call with the same input compare equal — which is what makes "one method says it raises,
    the other says it returns" detectable without understanding the code.
    """
    rows: list[tuple[str, str, bool]] = []
    method = ""
    starts = {match.start(): match.group(1) for match in _METHOD_DEF_RE.finditer(source)}
    pending_raise_block = False
    raise_block_indent = -1
    offset = 0
    for line in source.splitlines(keepends=True):
        if offset in starts:
            method = starts[offset]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        offset += len(line)
        if not stripped or stripped.startswith("#"):
            continue
        expects_raise = bool(_EXPECTS_RAISE_RE.search(stripped))
        if expects_raise and stripped.startswith("with "):
            # `with assertRaises(X):` — the call under test is on a FOLLOWING, deeper line.
            pending_raise_block = True
            raise_block_indent = indent
            continue
        calls = [
            f"{name}({' '.join(args.split())})"
            for name, args in _CALL_RE.findall(stripped)
            # The oracle itself is not the call under test. Matched by NAME rather than by the
            # raise pattern: `assertRaises(` with nothing after the paren does not satisfy that
            # pattern, so the assertion would have been recorded as the subject of its own check.
            if not name.lower().endswith(("raises", "throw", "throwerror"))
            and not name.startswith("assert")
            and name not in {"print", "len", "str", "bytes", "int", "expect"}
            # A call with no arguments carries no input, so it cannot distinguish two oracles.
            # `Codec()` legitimately appears inside an assertRaises block for a bad input AND in a
            # value assertion for a good one; recording it would report those as contradictory.
            and args.strip()
        ]
        if pending_raise_block and indent > raise_block_indent:
            for call in calls:
                rows.append((method, call, True))
            continue
        if pending_raise_block and indent <= raise_block_indent:
            pending_raise_block = False
        if expects_raise:
            for call in calls:
                rows.append((method, call, True))
        elif _VALUE_ASSERT_RE.search(stripped):
            for call in calls:
                rows.append((method, call, False))
    return rows


def check_proof_artifact(source: str, *, contract: TaskContract) -> ArtifactVerdict:
    """Whether this artifact may be written and executed as proof of ``contract``.

    Runs BEFORE any workspace write and before any command. An artifact that fails here costs one
    correction call and nothing else; the incident's cost was a file in a read-only workspace and a
    verdict composed from the wrong evidence.
    """
    body = str(source or "")
    if not body.strip():
        return ArtifactVerdict(
            accepted=False, reason_code=EMPTY_ARTIFACT, detail="the test artifact was empty."
        )

    claim_body = _artifact_claim_body(body)
    artifact_tokens = claim_tokens(claim_body)

    # 1. Does it touch the subject at all, and is it the subject's CLAIM it exercises?
    triggers = contract.literal_triggers
    contract_overlap = len(contract.signature & artifact_tokens)
    banned_overlap = max(
        (len(item & artifact_tokens) for item in contract.banned_signatures), default=0
    )
    if triggers and not (triggers & artifact_tokens):
        return ArtifactVerdict(
            accepted=False,
            reason_code=OFF_TARGET,
            detail=(
                "it never exercises the input the finding names ("
                + ", ".join(sorted(triggers)[:4])
                + "), so whatever it observes is a different behaviour."
            ),
            evidence={"expected_triggers": sorted(triggers)[:8]},
        )
    # A shared trigger is necessary and never sufficient. `0` and `1` are literals half the claims
    # in any codebase mention, and an artifact that matches one while sharing no other vocabulary
    # with the claim is testing something else that happens to use the same number.
    if contract_overlap < 2:
        return ArtifactVerdict(
            accepted=False,
            reason_code=OFF_TARGET,
            detail=(
                "it shares almost no vocabulary with the claimed mechanism "
                f"({contract.claimed_mechanism[:120]}), so it is not a test of that claim."
            ),
            evidence={"contract_overlap": contract_overlap},
        )
    if contract.banned_signatures and banned_overlap > contract_overlap:
        return ArtifactVerdict(
            accepted=False,
            reason_code=OFF_TARGET,
            detail=(
                "it matches a claim that was already disproved this session more closely than the "
                "claim it is supposed to prove."
            ),
            evidence={"contract_overlap": contract_overlap, "disproved_overlap": banned_overlap},
        )

    # 2. Does it assert the DEFECT as expected? A reproduction must fail on the current code.
    polarity = contract.polarity
    expected_exceptions = claimed_exceptions(
        contract.claimed_mechanism, contract.expected_observable_failure
    )
    oracles = _oracles(claim_body)
    if polarity == "raises" and expected_exceptions:
        for match in _EXPECTS_RAISE_RE.finditer(claim_body):
            asserted = next((group for group in match.groups() if group), "")
            short = asserted.rsplit(".", 1)[-1]
            if short and short in expected_exceptions:
                return ArtifactVerdict(
                    accepted=False,
                    reason_code=BACKWARDS_ORACLE,
                    detail=(
                        f"it asserts that `{short}` IS raised, which is the defect the finding "
                        "claims — so the test passes while the bug is present and its exit status "
                        "means the opposite of a reproduction."
                    ),
                    evidence={"asserted_exception": short},
                )

    # 3. Does it contradict itself? Two oracles on the same call with the same input cannot both
    #    be the expected behaviour, so the file is evidence for neither.
    by_call: dict[str, set[bool]] = {}
    for _method, call, expects_raise in oracles:
        by_call.setdefault(call, set()).add(expects_raise)
    conflicting = [call for call, outcomes in by_call.items() if len(outcomes) > 1]
    if conflicting:
        return ArtifactVerdict(
            accepted=False,
            reason_code=CONTRADICTORY_ORACLES,
            detail=(
                f"it asserts both an exception and a value for the same call `{conflicting[0]}`, "
                "so no exit status of this file can settle the claim."
            ),
            evidence={"conflicting_calls": conflicting[:3]},
        )

    # 4. Does it modify the thing it is supposed to observe?
    if not contract.production_modification_allowed:
        for target in contract.target_files:
            stem = target.replace("\\", "/").rsplit("/", 1)[-1]
            if stem and stem in body and _WRITE_MODE_RE.search(body):
                return ArtifactVerdict(
                    accepted=False,
                    reason_code=MUTATES_TARGET,
                    detail=(
                        f"it opens `{stem}` for writing; a reproduction observes the subject, it "
                        "does not edit it."
                    ),
                )

    return ArtifactVerdict(accepted=True)
