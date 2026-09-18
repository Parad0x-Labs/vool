"""The shared-terminal consensus loop: human fuzz -> :done -> independent reviews ->
alternating rounds -> three-way consensus -> only then implementation.

THREE reviewers, deliberately optimizing for different failure modes:
  KIMI K3    architecture/root-cause ("is the invariant wrong?") — own terminal, read-only
  GPT/CODEX  black-box product correctness ("did it answer what the human asked?") —
             own terminal, read-only, deliberately ignores kernel internals first
  FABLE 5    runtime/product review AND sole implementer after consensus

This module is a FILE BUS and a state machine — nothing more. Round 1 is
INDEPENDENT: Kimi and Codex each write their first review from the full artifact
without reading the other (the bus accepts either arrival order); Fable closes the
round having done its own complete transcript pass. Rounds 2-3 run kimi -> codex ->
fable with everything visible. Implementation opens only on a consensus block all
three have ratified: KIMI: AGREE / CODEX: AGREE / FABLE: AGREE.

EVERY reviewer receives the COMPLETE fuzz run — transcript.md (full, with embedded
evidence) plus FULL_REVIEW_CONTEXT.md (per-turn USER / OBLIGATIONS / STATE / ANSWER /
EVIDENCE / CARRYOVER with a pointer to the effect tapes). No reviewer depends on
another reviewer's selection of which turns matter.

Completion convention (anti-truncation): a round file COUNTS only when its last
non-empty line is exactly "VERDICT: AGREE" or "VERDICT: DISAGREE". The REPL parses
ONLY that line and the file name — prose is for the reviewers, never the machine.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import re
import time

__all__ = ["open_review", "run_review"]

_MAGENTA = "\033[35m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_RED = "\033[31m"
_GREEN = "\033[1;32m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

# COUNCIL 2.0 (operator directive 2026-08-20, superseding the same-day backstop
# order): there is NO arbitrary maximum round count. Rounds 1-4 target UNANIMITY —
# a majority never closes early; a unanimous AGREE at any round <= 4 closes
# APPROVED immediately. If round 4 ends without unanimity the review does NOT fail
# and does NOT stop: from round 5 onward it closes APPROVED at the FIRST round
# where a MAJORITY of ALL designated voting seats says AGREE (>50% of the seats,
# not of respondents). Guards: an AGREE carrying an EVIDENCE INCOMPLETE marker
# never counts as AGREE; a DISAGREE carrying a P0-HOLD line blocks a majority
# close (unanimity still closes — the holder themselves flipped); after round 4 a
# continued veto must bring NEW evidence (deliberative rule, enforced by the
# council, recorded in PROTOCOL). An explicit max_rounds int remains available as
# an operator-set backstop that PARKS (resumable), never a policy default.
UNANIMITY_TARGET_THROUGH_ROUND = 4
MAJORITY_FALLBACK_FROM_ROUND = 5

# CLOWN-WAGON TOPOLOGY (operator directive 2026-08-20): 7 seats — 4 voting judges,
# 2 non-voting attack factories, 1 idle arbitrator. Seats are PER-REVIEW DATA
# (state.json), never code constants: an old review resumes on the seats it was
# opened with; shadow seats can never expand or shrink quorum.
# ROSTER (operator 2026-08-20 late): KIMI K3 RETIRED and removed. DEEPSEEK V4 PRO
# is PROMOTED from sidekick to the ARCHITECT voting seat on TRIAL — the Impact
# Score adjudicates after one full review. If the trial fails, GPT-5.6 SOL PRO
# takes the architect seat (and its supreme-court role is SUSPENDED while it
# holds a vote — no seat arbitrates its own council; disputes then go to the
# operator). Legacy reviews resume on the seats in their own state.json.
DEFAULT_VOTING_SEATS = ("deepseekpro", "grok", "terra", "fable")
DEFAULT_CONTRIBUTORS = ("deepseekflash",)                 # gate ROUND 1 only
ARBITRATOR = "solpro"                                     # idle; invoked-only
LEGACY_VOTING_SEATS = ("kimi", "codex", "grok", "fable")  # pre-topology reviews

_CONTRIB_DONE_LINE = "CONTRIB: COMPLETE"


def _contrib_done(path: pathlib.Path) -> bool:
    """A contributor file COUNTS only when its last non-empty line is exactly
    'CONTRIB: COMPLETE' — the same anti-truncation convention as verdicts."""
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return False
    return bool(lines) and lines[-1].strip() == _CONTRIB_DONE_LINE


def _majority_needed(n_seats: int) -> int:
    """>50% of ALL designated voting seats (4 -> 3, 5 -> 3), never of respondents."""
    return n_seats // 2 + 1


_EVIDENCE_INCOMPLETE_MARK = "EVIDENCE INCOMPLETE"
_P0_HOLD_PREFIX = "P0-HOLD:"


def _declares_evidence_incomplete(text: str) -> bool:
    """The marker counts only as a DECLARATION — a line that STARTS with it (after
    markdown decoration). round_9_grok wrote "No EVIDENCE INCOMPLETE on this AGREE"
    and the old substring match discounted a properly bound AGREE: quoting or
    negating the marker is not declaring it."""
    return any(ln.strip().lstrip("*#>- ").startswith(_EVIDENCE_INCOMPLETE_MARK) for ln in text.splitlines())


def _counts_as_agree(path: pathlib.Path, verdict: str) -> bool:
    """An AGREE that DECLARES EVIDENCE INCOMPLETE is not an AGREE (council 2.0)."""
    if verdict != "AGREE":
        return False
    try:
        return not _declares_evidence_incomplete(path.read_text(encoding="utf-8"))
    except OSError:
        return False


_BLOCK_DIGEST_RE = re.compile(r"^BLOCK-DIGEST:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_FULL_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _block_digest(text: str) -> str:
    """Canonical digest of a block: FULL sha256 (64 lowercase hex) of the text from
    CONSENSUS_MARK to the end, rstripped. SOL ruling 1 dispute 4: a 64-bit prefix is
    not an appropriate authorization token; full digest, no material cost."""
    block = text[text.index(CONSENSUS_MARK):].rstrip()
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def _declared_digest_from(text: str) -> str | None:
    """The EXACT full-64-hex declaration parsed from already-captured bytes.
    SOL test 8: 63/65-char, uppercase, suffix-junk, duplicate, or conflicting
    declarations are all invalid (None) — never silently truncated or prefixed."""
    matches = _BLOCK_DIGEST_RE.findall(text)
    if len(matches) != 1:
        return None
    return matches[0] if _FULL_HEX_RE.fullmatch(matches[0]) else None


class _VoteSnapshot:
    """ONE read, one coherent authorization record (SOL ruling 1, root cause:
    'no coherent authorization snapshot' — verdict was cached at landing while
    hold/evidence/digest/holder bytes were re-read live, so a close could
    represent no state any seat submitted). Every field derives from the SAME
    captured bytes; evaluation never touches the mutable path again."""

    def __init__(self, seat: str, path: pathlib.Path) -> None:
        self.seat = seat
        self.name = path.name
        try:
            self.text: str | None = path.read_text(encoding="utf-8")
        except OSError:
            self.text = None
        t = self.text or ""
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        self.verdict = lines[-1] if lines and lines[-1] in _VERDICTS else None
        self.evidence_incomplete = _declares_evidence_incomplete(t)
        self.p0_hold = any(ln.strip().startswith(_P0_HOLD_PREFIX) for ln in t.splitlines())
        self.block_text = t[t.index(CONSENSUS_MARK):].rstrip() if CONSENSUS_MARK in t else None
        self.block_digest = (hashlib.sha256(self.block_text.encode("utf-8")).hexdigest()
                             if self.block_text is not None else None)
        self.declared = _declared_digest_from(t)
        self.file_sha256 = hashlib.sha256(t.encode("utf-8")).hexdigest() if self.text is not None else None

    def counts_agree(self) -> bool:
        return self.verdict == "VERDICT: AGREE" and not self.evidence_incomplete

    def bound_to(self, holder_digest: str) -> bool:
        return (self.block_digest == holder_digest
                or (self.declared is not None and self.declared == holder_digest))


def _p0_hold(path: pathlib.Path) -> bool:
    """A DISAGREE may pin an unresolved P0 destructive/security/capability-boundary
    failure with a line starting 'P0-HOLD:'. While it stands on the closing round,
    a majority cannot vote it away; only unanimity (the holder flipping) or the
    operator resolves it."""
    try:
        return any(ln.strip().startswith(_P0_HOLD_PREFIX)
                   for ln in path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False
AGENTS = ("kimi", "codex", "grok", "fable")
_YELLOW = "\033[33m"
_AGENT_NAMES = {"kimi": "KIMI K3", "codex": "GPT-5.6/CODEX", "terra": "GPT-5.6 TERRA PRO",
                "grok": "GROK (falsifier)", "fable": "FABLE 5",
                "deepseekpro": "DEEPSEEK V4 PRO (architect, trial)",
                "deepseekflash": "DEEPSEEK V4 FLASH (furnace)",
                "solpro": "GPT-5.6 SOL PRO (supreme court)"}
_AGENT_COLORS = {"kimi": _MAGENTA, "codex": _BLUE, "terra": _BLUE, "grok": _RED,
                 "fable": _CYAN, "deepseekpro": _YELLOW, "deepseekflash": _YELLOW,
                 "solpro": _BOLD}
_VERDICTS = ("VERDICT: AGREE", "VERDICT: DISAGREE")
CONSENSUS_MARK = "=== CONSENSUS ==="

PROTOCOL = """# REVIEW PROTOCOL — four judges, one raw run, no trusted packaging

FOUR independent judges audit ONE complete human fuzz run. The RAW run is the
only truth: this file, transcript.md, FULL_REVIEW_CONTEXT.md, any generated
report, prior consensus, tests, and PASS counters are NAVIGATION AIDS, never
authority. A beautiful review of an incomplete artifact is still a bad review.

## The council and its core question
- KIMI K3 (round_N_kimi.md) — architecture/invariant: "what invariant allowed
  this class of failure to exist?"
- GPT-5.6/CODEX (round_N_codex.md) — black-box product: "did the product do
  what the human actually asked?"
- GROK 4.6 (round_N_grok.md) — HOSTILE FALSIFIER / adversarial falsifier: "assume
  the other three built a beautifully reasoned but still stupid theory — how do
  I break it?" Grok reviews the raw run independently FIRST, then attacks the
  proposed fix with NEW cases (see the falsifier mandate below).
- FABLE 5 (round_N_fable.md) — runtime/implementation/evidence: "what do the
  code, receipts, effect tape, and actual runtime prove?" Fable is the SOLE
  implementer AFTER consensus; no source edits during deliberation.

## RAW-EVIDENCE INDEPENDENCE (the rule that overrides everything)
Read the RAW journal (its path is named at the top of FULL_REVIEW_CONTEXT.md)
and verify for yourself, before trusting any prepared artifact:
- how many turns exist, and that EVERY turn is represented, in order;
- that each turn carries the COMPLETE user-visible answer (nothing truncated
  after PARTIAL/COMMIT);
- that multiline/pasted inputs were not split or merged;
- that receipts/evidence for the turn are present;
- that the tape can be inspected where a diagnosis depends on execution;
- that carryover transitions can be followed across turns.
If the digest/transcript disagrees with the raw run: THE RAW RUN WINS. If a
required part cannot be obtained: DO NOT GUESS, DO NOT inherit it from another
reviewer — mark "EVIDENCE INCOMPLETE — REVIEW CANNOT CLOSE" and name what is
missing.

## Mandatory independent product audit (every judge, every turn)
TURN N / USER ACTUALLY ASKED / SYSTEM ACTUALLY ANSWERED / PASS|PARTIAL|FAIL /
WHY. Judge like a hostile ordinary user — "is this answer sensible?" A VALID
RECEIPT DOES NOT MAKE A WRONG ANSWER CORRECT; a successful tool call does not
make an irrelevant answer correct; a PASS counter and a COMMIT label prove
nothing. Watch for wrong number/unit/entity/formula, model-digits-as-specs,
wrong-turn answers, stale/phantom carryover, omitted or invented subrequests,
malformed JSON/tables, ignored output-only constraints, injected text gaining
authority, correct user data rejected, verifier-rejected values re-shipping via
another render path, answerable tasks hidden behind PARTIAL/REFUSED.

## Independence and order
Round 1: kimi, codex, and grok write INDEPENDENTLY from the raw run — do NOT
read another judge's round-1 file first (the bus accepts any arrival order);
fable closes round 1. Rounds 2-3: kimi -> codex -> grok -> fable, everything
visible. Treat every doc/test/prior-consensus claim of "fixed" as a HYPOTHESIS
to falsify, not a fact.

## GROK's falsifier mandate (before Grok may vote AGREE on a repair)
At least: FIVE brand-new sabotage/falsification cases; TWO cross-domain
mutations; ONE test built to make the proposed guard reject a CORRECT answer;
ONE built to let a WRONG answer bypass the guard; ONE lane/invariant
interaction probing bug-displacement. These are NEW cases, not transcript
wording replayed. ONE valid counterexample -> GROK: DISAGREE until addressed.

## Every round file must contain
A. TURN COVERAGE AUDIT — every turn, none omitted, verified against the raw run.
B. SYSTEMIC FINDINGS — root-cause classes: FAILURE CLASS / AFFECTED TURNS /
   FIRST USER-VISIBLE SYMPTOM / EARLIEST WRONG DECISION / LAW-INVARIANT / ROOT
   CAUSE / WHY GENERAL / PROPOSED FIX / IMPLEMENTATION BOUNDARY / DO NOT PATCH /
   FALSIFICATION TESTS / WHAT WOULD PROVE THIS WRONG.
Disagreements use AGREE-DISAGREE / WHY / RAW USER-VISIBLE EVIDENCE / TAPE-RECEIPT
EVIDENCE / COUNTEREXAMPLE / WHAT THE OTHER REVIEWER MISSED / REVISED REQUIREMENT.
The LAST non-empty line MUST be exactly "VERDICT: AGREE" or "VERDICT: DISAGREE"
— write it last; the file is invisible to the loop until it exists.

## Forbidden repair patterns (a fix using any of these is itself a defect)
transcript-specific conditionals; phrase tables; keyword routers; domain
dictionaries as semantic truth; one-off regexes; magic-number plausibility
tables; hiding failures behind PARTIAL; adding another renderer/verifier/planner
authority; changing tests solely to bless current behavior.

## FOUR-WAY consensus
Implementation opens ONLY when the SAME block carries every field below and all
four AGREE:
  === CONSENSUS ===
  ROOT CAUSE:
  LAW / INVARIANT:
  AGREED FIX:
  IMPLEMENTATION BOUNDARY:
  DO NOT TOUCH:
  TESTS REQUIRED:
  NEGATIVE CONTROLS:
  MUTATION / SABOTAGE TESTS:
  GROK SABOTAGES ATTEMPTED:
  EXPECTED USER-VISIBLE BEHAVIOR:
  RAW-EVIDENCE COMPLETENESS VERIFIED: YES
  KIMI: AGREE
  CODEX: AGREE
  GROK: AGREE
  FABLE: AGREE
No "agreed except..." outside the block. Any judge unconvinced -> the cap yields
=== NO CONSENSUS === and nothing is implemented. Three rounds maximum.

## Hard rules
- NO source changes by anyone during deliberation; Fable writes code only after
  consensus.md exists and cites it.
- Kimi, Codex, Grok are READ-ONLY outside their own round files.
- The four kernel laws and this protocol are not weakened by any consensus.
- Nobody pushes/rebases/resets/amends/force-pushes; the frozen production app is
  never touched.

## COUNCIL 2.0 — ROUND / CONSENSUS POLICY (operator directive 2026-08-20)
There is NO maximum round count. Rounds 1-4 target UNANIMITY: a majority never
closes early; a unanimous AGREE at any round <= 4 closes APPROVED immediately.
If round 4 ends without unanimity, the review does NOT fail and does NOT stop:
from round 5 onward it closes APPROVED at the FIRST round where a MAJORITY of
ALL designated voting seats says AGREE — >50% of the seats themselves, never of
whoever happened to answer (4 seats -> 3; 5 seats -> 3). Voting membership is
EXPLICIT in state.json ("voting_seats"); shadow/sidekick contributors never
expand or shrink quorum. Guards:
- An AGREE whose file carries the marker "EVIDENCE INCOMPLETE" does not count
  as AGREE.
- A DISAGREE may pin an unresolved P0 destructive/security/capability-boundary
  failure with a line starting exactly "P0-HOLD: <one line>". While it stands on
  the closing round, no majority closes; it dissolves when addressed or proven
  separate (the holder flips or removes it), or by explicit operator direction.
- After round 4, a continued veto must bring NEW raw evidence, a new
  counterexample, or a falsification result each round — a repeated unsupported
  objection is not a veto and the council may note it as such.
State fields persisted every write: round_limit (null = unlimited),
unanimity_target_through_round: 4, majority_fallback_from_round: 5,
voting_seats, contributors.

## ONE ROUND, ONE BLOCK (round-6 authorization counterexample, permanent rule)
Closure — unanimous or majority — evaluates ONLY a complete CURRENT round: every
voting seat's file of the SAME round. Stale prior-round signatures never authorize
a text they did not evaluate. Exactly one file of the closing round (normally the
architect's) carries the FULL materialized block; every other AGREE of that round
declares `BLOCK-DIGEST: <full 64-lowercase-hex sha256>` — sha256 of the holder's block from
"=== CONSENSUS ===" to end, full digest, never a prefix. HARD RULE (round-7 amendment): a counted
AGREE that neither carries the block nor declares a MATCHING digest is UNBOUND and
blocks closure — a missing declaration is never accepted. The recorded consensus.md
carries the digest and holder name.

## CLOWN-WAGON TOPOLOGY — canonical multi-model execution order (operator, 2026-08-20)
SEVEN seats: 4 voting judges, 2 non-voting attack factories, 1 idle arbitrator.
- kimi   KIMI K3 MAX — architect/root-cause/invariants — VOTING
- grok   GROK MAX — chief falsifier: hostile falsification, speculative bets — VOTING
- terra  GPT-5.6 TERRA PRO — mass product-correctness prosecutor — VOTING
- fable  FABLE 5 XHIGH — runtime/code/evidence judge + SOLE implementer — VOTING
- deepseekpro   DEEPSEEK V4 PRO — deputy falsifier: independent counterexamples,
  architectural alternatives, speculative bets, bypasses — NON-VOTING (contrib files)
- deepseekflash DEEPSEEK V4 FLASH — mutation furnace: mass mutations, cross-domain
  attacks, negative controls, false-positive traps — NON-VOTING (contrib files)
- solpro GPT-5.6 SOL PRO — supreme court — IDLE by default, never auto-run
Contributors NEVER alter quorum. Sol never joins quorum unless a review designates
it a temporary voting seat BEFORE its answer is known — never silently after.

EXECUTION IS WAVES, NEVER SERIAL (serial contaminates: reviewer B starts reasoning
from reviewer A instead of raw evidence):
- WAVE 0 — the test run. No reviewer touches product source. Freeze build/SHA, raw
  journal range, exact turn count, complete user-visible outputs, tape/receipts/
  state; open the review dir; announce ROUND READY.
- WAVE 1 (round 1) — SIX-WAY PARALLEL: kimi/grok/terra/fable each write an
  INDEPENDENT raw-evidence pass BEFORE reading one another; deepseekpro hunts
  counterexamples/speculative bets/bypasses and deepseekflash manufactures mutations/
  negative controls — both land contrib_1_<seat>.md ending "CONTRIB: COMPLETE".
  Wave 2 starts only after ALL round-1 material is complete. Sol stays idle.
- WAVE 2+ (rounds 2..) — the four judges again IN PARALLEL, now required to attack
  one another's conclusions and address material disagreement; DeepSeeks keep
  feeding ammunition concurrently (not gated, never quorum). Read current raw
  evidence + previous round + new counterevidence; attack; revise. Unanimity at
  any round <= 4 closes immediately; round 5+ majority per council 2.0.
- IMPLEMENTATION — only after approval, FABLE alone edits; everyone else is
  read-only. One consensus-bounded implementation, then NEW build -> NEW run ->
  NEW review. A fix is never declared successful because tests written during
  implementation passed: the NEXT independent run proves survival. Never
  "Fable fixes, then the models explain why it was good" — that is how
  fake-green systems are born.
PRIORITY under constraint: P1 terra/kimi/grok/fable (parallel, never serial) ·
P2 deepseekpro · P3 deepseekflash · exception solpro.

## SOL PRO — SUPREME COURT / EXCEPTION ARBITRATOR (permanent rule)
Sol is NOT an always-on reviewer; it stays idle unless a MATERIAL disagreement
justifies the strongest/most expensive judge. The bus owner (fable) must
explicitly recommend Sol arbitration when ANY of these occur:
 1. two+ voting reviewers reach strongly contradictory conclusions from the same
    raw evidence; 2. PASS vs FAIL disagreement on a P0/P1 issue; 3. security /
 capability-boundary / unauthorized-effect disagreement; 4. agreement that
 something failed but material disagreement on earliest wrong decision, root
 cause, governing invariant, implementation boundary, or whether the fix
 addresses the class; 5. a large/risky architectural change with conflicting
 conclusions; 6. a Grok speculative bet survives serious attacks but the council cannot
 judge adoption; 7. a claim that another reviewer graded the wrong user-visible
 behavior/tape event/receipt/obligation/turn; 8. approaching the majority
 fallback with a technically substantive (non-repetitive) dispute; 9. a final
 release-gate disagreement where a false PASS materially matters.
DO NOT spend Sol on: obvious arithmetic, already-proven failures, duplicates,
ordinary mutation generation, agreement where raw evidence is clear, anything the
council can settle mechanically. Sol exists for expensive ambiguity, not volume.

CASE FILE (smallest complete case preserving the dispute — never the whole
review unless necessary): CASE / BUILD-SHA / TURN(S) / EXACT USER ASK / EXACT
USER-VISIBLE ANSWER / RELEVANT OBLIGATIONS / RECEIPTS / TAPE EVENTS / STATE
TRANSITIONS / KIMI POSITION / GROK POSITION / TERRA POSITION / FABLE POSITION /
DEEPSEEK EVIDENCE IF RELEVANT / EXACT POINTS OF CONTRADICTION / WHAT MUST BE
DECIDED — plus direct paths to the RAW material, and the explicit instruction:
"DO NOT TRUST THIS CASE SUMMARY. It is navigation only. Verify disputed claims
against raw evidence."

SOL'S REQUIRED RETURN (claim-by-claim arbitration, not an essay). Per dispute:
CLAIM A / CLAIM B / RAW EVIDENCE CHECKED / WHAT ACTUALLY HAPPENED / APPROVE A |
APPROVE B | BOTH WRONG | EVIDENCE INCOMPLETE / WHY / EXACT LOCATION TO VERIFY /
WHAT WOULD DISPROVE THIS JUDGMENT. Then: USER-VISIBLE VERDICT (PASS/PARTIAL/
FAIL) · EARLIEST WRONG DECISION · ROOT CAUSE (PROVEN/PLAUSIBLE/NOT PROVEN) ·
PROPOSED FIX (APPROVE/DENY/MODIFY/INSUFFICIENT EVIDENCE) · IMPLEMENTATION
BOUNDARY · TESTS REQUIRED BEFORE ACCEPTANCE · UNRESOLVED QUESTIONS · SOL FINAL
(APPROVE/DENY/RETURN TO COUNCIL/EVIDENCE INCOMPLETE).

SOL DOES NOT BECOME GOD: its answer is high-quality arbitration EVIDENCE, not an
override of raw runtime truth — if Sol contradicts raw evidence, RAW EVIDENCE
WINS; unsupported assertions may be challenged; EVIDENCE INCOMPLETE means go
collect the missing evidence. Sol does not implement, does not edit source, and
does not become a permanent voter by being invoked. After Sol reports, publish
its arbitration to ALL reviewers and run a normal council round so the judges
can accept or attack the ruling.
"""


def _sleep(seconds: float) -> None:  # separate so tests can interrupt the wait
    time.sleep(seconds)


def _bus_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("VOOL_REVIEW_BUS", str(pathlib.Path.home() / "vool" / "review-bus")))


def _verdict(path: pathlib.Path) -> str | None:
    """AGREE/DISAGREE when the file is complete per the trailing-line convention."""
    try:
        text = path.read_text()
    except OSError:
        return None
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if lines and lines[-1] in _VERDICTS:
        return lines[-1].split(":", 1)[1].strip()
    return None


def _write_state(review: pathlib.Path, **kw: object) -> None:
    (review / "state.json").write_text(json.dumps(kw, indent=2) + "\n")


def _strip(text: str) -> str:
    return text.replace("\x01", "").replace("\x02", "").replace("\x03", "").replace("\x04", "")


def _turn_context(n: int, question: str, transcript: str, answer: str | None = None) -> str:
    """One turn's canonical digest for FULL_REVIEW_CONTEXT.md, parsed from the
    marked transcript: answer between \\x01..\\x02, evidence between \\x03..\\x04.

    TIER 6: when `answer` is supplied (the reconciled bytes from the raw journal) it
    is authoritative — a review rebuilt from the journal has a marker-stripped
    transcript, and parsing that would print "(nothing shipped)" over real bytes.
    An empty-but-present answer is named distinctly from a genuinely absent one, so
    a silence contract is never confused with a parser failure."""
    if answer is None and "\x01" in transcript:
        answer = transcript.split("\x01")[1].split("\x02")[0]
    if answer is None:
        answer = "(nothing shipped)"
    else:
        answer = answer.strip() or "(empty answer — zero bytes)"
    evidence = transcript.split("\x03")[1].split("\x04")[0].strip() if "\x03" in transcript else "(none recorded)"
    plain = _strip(transcript)
    obligations = [ln.strip() for ln in plain.splitlines() if re.match(r"\s+ob\d+ \[", ln)]
    state = next((ln.strip() for ln in plain.splitlines()
                  if ln.startswith(("COMMIT", "KERNEL REFUSED", "SHIPPED AS",
                                    "turn failed, named:"))), "(no state line)")
    shipped = next((ln.strip() for ln in plain.splitlines() if ln.startswith("SHIPPED AS")), "")
    if shipped and not state.startswith("SHIPPED"):
        state = f"{state} -> {shipped}"
    narrative = [ln.strip() for ln in plain.splitlines()
                 if ln.strip().startswith(("!", ">", "*", "+", "?"))]
    carry = next((ln.strip() for ln in plain.splitlines() if ln.startswith("still open from earlier")), "(none)")
    parts = [
        f"## turn {n}",
        f"USER: {question}",
        "OBLIGATIONS:" + ("\n  " + "\n  ".join(obligations) if obligations else " (none extracted)"),
        "KERNEL NARRATIVE:" + ("\n  " + "\n  ".join(narrative) if narrative else " (clean)"),
        f"STATE: {state}",
        f"ANSWER:\n{answer}",
        f"EVIDENCE:\n{evidence}",
        f"CARRYOVER: {carry}",
        "",
    ]
    return "\n".join(parts)


class FragmentedTranscriptError(ValueError):
    """The transcript does not contain every driver turn of the run (consensus-5
    LAW 8). A review must never open on a fragmented artifact — a black-box
    reviewer could not then audit the missing turns. Raised instead of opening."""


class ReconciliationError(ValueError):
    """TIER 6: the review digest disagrees with the raw journal's structured answer
    bytes, or a journal row's own answer hash does not match its answer. A review
    must never open on a digest that misrepresents the run — that is exactly how a
    false consensus is born (the gauntlet's "(nothing shipped)" ×20 over live bytes).
    Raised instead of opening."""


def _load_journal_answers(journal_path: str, run_id: str) -> dict[int, dict]:
    """{turn_index: {"answer","present","len"}} for one run, each row SELF-VERIFIED.

    Reads the raw journal (the only canonical truth), keeps this run's rows, and
    recomputes sha256(answer) == answer_sha256 for every present answer. A mismatch
    means the raw record itself is corrupt: raise rather than reconcile against a lie.
    Rows without a turn_index (pre-tier-6 records, replay rows) are skipped — they
    carry no tier-6 fields to reconcile."""
    out: dict[int, dict] = {}
    with open(journal_path, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if row.get("run_id") != run_id:
                continue
            ti = row.get("turn_index")
            if ti is None:
                continue
            present = bool(row.get("answer_present"))
            ans = row.get("answer")
            if present and ans is not None:
                actual = hashlib.sha256(ans.encode("utf-8")).hexdigest()
                if actual != row.get("answer_sha256"):
                    raise ReconciliationError(
                        f"raw journal turn {ti}: sha256(answer) {actual} != stored "
                        f"answer_sha256 {row.get('answer_sha256')} — the record is "
                        "corrupt; review refused (the raw run is the only truth)")
            out[ti] = {"answer": ans, "present": present, "len": row.get("answer_len", 0)}
    return out


def open_review(turns: list[tuple], build: str, bus: pathlib.Path | None = None,
                expected_turns: int | None = None, run_id: str | None = None,
                journal_path: str | None = None,
                voting_seats: tuple[str, ...] | None = None,
                contributors: tuple[str, ...] | None = None) -> pathlib.Path:
    """Create the review directory: full transcript, canonical per-turn context,
    protocol, initial state. Every reviewer gets the COMPLETE run.

    REVIEW-HARNESS INTEGRITY (consensus-5 LAW 8): if the caller states how many
    driver turns the run produced (expected_turns), it must equal len(turns), or
    the review is refused with a named harness error rather than opened
    fragmented — the exact hole the resume-boundary specimen exposed (transcript
    held turns 15-58 while the run had 58)."""
    if not turns:
        raise ValueError("nothing to review: the session has no turns")
    if run_id and journal_path:
        try:
            import json as _json
            with open(journal_path) as _jf:
                rid_rows = sum(1 for ln in _jf
                               if ln.strip() and _json.loads(ln).get("run_id") == run_id)
            if rid_rows and expected_turns is None:
                expected_turns = rid_rows
        except OSError:
            pass
    if expected_turns is not None and expected_turns != len(turns):
        raise FragmentedTranscriptError(
            f"transcript has {len(turns)} turns but the run produced {expected_turns} — "
            "a review may not open on a fragmented artifact (LAW 8); reconstruct the "
            "full transcript across the process restart first"
        )
    # TIER 6: load the raw journal's SELF-VERIFIED answer bytes for this run. A
    # corrupt row (its own hash mismatches) raises ReconciliationError here, before a
    # single digest line is written. Empty map when there is no journal to reconcile.
    journal_answers: dict[int, dict] = {}
    if run_id and journal_path:
        with contextlib.suppress(OSError):
            journal_answers = _load_journal_answers(journal_path, run_id)
    root = bus or _bus_root()
    review = root / f"review-{time.strftime('%Y%m%d-%H%M%S')}"
    review.mkdir(parents=True, exist_ok=False)

    body = [f"# Fuzz transcript under review — build {build}", ""]
    context = [
        f"# FULL_REVIEW_CONTEXT — build {build}, {len(turns)} turns, complete and ordered",
        "",
        "Every turn of the human fuzz run. No turn has been omitted or curated.",
        f"RAW EVIDENCE (the ONLY canonical truth — this digest and transcript.md are"
        f" navigation aids, NOT authority): {journal_path or (os.environ.get('VOOL_HOME', '/tmp/vool-kernel-repl-home') + '/repl_sessions.jsonl')}"
        + (f"  ·  this run's rows carry \"run_id\": \"{run_id}\"" if run_id else "")
        + " — one JSON row per turn, each carrying the exact question, the full plain"
        " transcript (obligations, terminal state, receipts, the answer), and the"
        " \"tape\" field (every model/tool execution in order). Every reviewer MUST"
        " independently verify turn count, ordering, and each complete answer against"
        " this raw file. If the digest or transcript disagrees with the raw run, THE"
        " RAW RUN WINS; if a required part cannot be obtained, mark EVIDENCE INCOMPLETE"
        " — REVIEW CANNOT CLOSE and name what is missing.",
        "",
    ]
    for n, turn in enumerate(turns, 1):
        question, transcript = turn[0], turn[1]
        ts = turn[2] if len(turn) > 2 else ""
        envelope = turn[3] if len(turn) > 3 else None
        body += [f"## turn {n}" + (f" ({ts})" if ts else ""), f"you> {question}", "", _strip(transcript).rstrip(), ""]
        # TIER 6 reconciliation: the raw journal's answer bytes are authoritative. A
        # review rebuilt from the journal has a marker-stripped transcript, so parsing
        # it would print "(nothing shipped)" over real bytes — pass the stored answer
        # so the digest shows the run. When the live transcript ALSO shipped an answer,
        # it MUST equal the journal's bytes, else the digest would misrepresent the run.
        jrow = journal_answers.get(n)
        live_answer = transcript.split("\x01")[1].split("\x02")[0] if "\x01" in transcript else None
        answer_override = None
        if jrow is not None and jrow["present"]:
            answer_override = jrow["answer"]
            if live_answer is not None and live_answer.strip() != (jrow["answer"] or "").strip():
                raise ReconciliationError(
                    f"turn {n}: the review transcript's answer disagrees with the raw "
                    "journal's stored answer bytes — refusing to open a review whose "
                    "digest would misrepresent the run (the raw run is the only truth)")
        digest = _turn_context(n, question, transcript, answer=answer_override)
        if envelope:
            digest = digest.replace(
                f"## turn {n}\n",
                f"## turn {n}\nINPUT: {envelope.get('delimiter', 'line')} ({envelope.get('lines', 1)} line(s))\n", 1)
        # DIGEST PARITY (consensus-2 fix 5, measured: all 32 turns digested as
        # "(nothing shipped)" while the transcript held answers): a turn with an
        # answer block must never digest as empty — fail review generation instead.
        if "\x01" in transcript and "ANSWER:\n(nothing shipped)" in digest:
            raise ValueError(f"digest parity violation on turn {n}: answer present but digested empty")
        context.append(digest)
    (review / "transcript.md").write_text("\n".join(body))
    (review / "FULL_REVIEW_CONTEXT.md").write_text("\n".join(context))
    (review / "PROTOCOL.md").write_text(PROTOCOL)
    # Initial state carries the ACTUAL topology so watchers fire correctly before
    # the first landing (bug: the old hardcoded seat list left terra/deepseek
    # watchers blind until something landed).
    v = list(voting_seats or DEFAULT_VOTING_SEATS)
    c = list(contributors if contributors is not None else DEFAULT_CONTRIBUTORS)
    _write_state(review, build=build, round=1,
                 awaiting=[f"round_1_{a}.md" for a in v] + [f"contrib_1_{x}.md" for x in c],
                 verdicts={}, status="open", round_limit=None,
                 unanimity_target_through_round=UNANIMITY_TARGET_THROUGH_ROUND,
                 majority_fallback_from_round=MAJORITY_FALLBACK_FROM_ROUND,
                 voting_seats=v, contributors=c)
    return review


def _print_round(path: pathlib.Path, agent: str, round_no: int) -> None:
    color = _AGENT_COLORS[agent]
    print(f"\n{_BOLD}{color}=== {_AGENT_NAMES[agent]} REVIEW — ROUND {round_no} ==={_RESET}")
    print(f"{color}{path.read_text().strip()}{_RESET}\n", flush=True)


def _steps(voting: tuple[str, ...], contributors: tuple[str, ...],
           max_rounds: int) -> list[list[str]]:
    """WAVES, not serial (clown-wagon topology): round 1 is ONE parallel wave —
    every voting judge writes an INDEPENDENT raw-evidence pass, and the attack
    factories dump contrib files; wave 2 (round 2) starts only after ALL round-1
    material is complete. Rounds 2+ are one parallel wave of the voting judges
    each (contributors keep feeding ammunition async, never gated, never quorum).
    Serial one-by-one review is banned — reviewer B must not start reasoning from
    reviewer A instead of the raw evidence."""
    steps: list[list[str]] = [[f"round_1_{a}.md" for a in voting]
                              + [f"contrib_1_{c}.md" for c in contributors]]
    for r in range(2, max_rounds + 1):
        steps.append([f"round_{r}_{a}.md" for a in voting])
    return steps


def _round_of_step(step_i: int, max_rounds: int | None) -> int:
    """Wave plan: step N is round N+1."""
    r = step_i + 1
    return r if max_rounds is None else min(r, max_rounds)


def run_review(
    turns: list[tuple],
    build: str,
    bus: pathlib.Path | None = None,
    poll_s: float = 1.0,
    max_wait_s: float | None = None,
    max_rounds: int | None = None,
    run_id: str | None = None,
    journal_path: str | None = None,
    voting_seats: tuple[str, ...] | None = None,
    contributors: tuple[str, ...] | None = None,
) -> str:
    """Drive one full four-way review cycle in the terminal. Returns the final
    status: 'consensus' | 'no_consensus' | 'aborted' | 'stalled' | 'refused'."""
    try:
        review = open_review(turns, build, bus, run_id=run_id, journal_path=journal_path,
                             voting_seats=voting_seats, contributors=contributors)
    except ValueError as exc:
        print(f"review refused: {exc}")
        return "refused"
    print(f"\n{_BOLD}review open:{_RESET} {review}")
    print(f"transcript: {len(turns)} turns · context: {review / 'FULL_REVIEW_CONTEXT.md'}")
    print("Round 1 is INDEPENDENT: Kimi K3 and GPT/Codex review the full run in their own"
          " terminals without reading each other; Fable 5 closes the round. Ctrl-C aborts.\n", flush=True)
    return _drive(review, build, poll_s=poll_s, max_wait_s=max_wait_s, max_rounds=max_rounds,
                  voting=tuple(voting_seats or DEFAULT_VOTING_SEATS),
                  contributors=tuple(contributors if contributors is not None else DEFAULT_CONTRIBUTORS))


def resume_review(review: pathlib.Path | str, build: str, poll_s: float = 1.0,
                  max_wait_s: float | None = None, max_rounds: int | None = None) -> str:
    """Re-enter a parked review (stalled, aborted, or capped no_consensus) and keep
    driving it. Operator order 2026-08-20: the round cap is abolished — a parked
    dispute RESUMES rather than terminating. Already-landed files replay from disk;
    a stale no_consensus.md written by the old cap is archived so the watchers do
    not read the review as terminal."""
    review = pathlib.Path(review)
    stale = review / "no_consensus.md"
    if stale.exists():
        stale.rename(review / "no_consensus_superseded.md")
    # A review resumes on the SEATS IT WAS OPENED WITH — its own state.json is the
    # authority; pre-topology reviews fall back to the legacy four with no
    # contributors. Never re-seat a review mid-flight.
    voting: tuple[str, ...] = tuple(LEGACY_VOTING_SEATS)
    contributors: tuple[str, ...] = ()
    try:
        st = json.loads((review / "state.json").read_text())
        if st.get("voting_seats"):
            voting = tuple(st["voting_seats"])
        contributors = tuple(st.get("contributors") or ())
    except (OSError, ValueError):
        pass
    print(f"\n{_BOLD}review resumed:{_RESET} {review} (seats: {', '.join(voting)};"
          " driving until unanimous or majority per council 2.0)", flush=True)
    return _drive(review, build, poll_s=poll_s, max_wait_s=max_wait_s, max_rounds=max_rounds,
                  voting=voting, contributors=contributors)


def _drive(review: pathlib.Path, build: str, poll_s: float,
           max_wait_s: float | None, max_rounds: int | None,
           voting: tuple[str, ...], contributors: tuple[str, ...] = ()) -> str:
    verdicts: dict[str, str] = {}
    seen: set[str] = set()
    landing_order: list[str] = []
    waited = 0.0
    # COUNCIL 2.0 policy, persisted on every state write so watchers and the
    # operator read the live rules, not a doc.
    policy = {"round_limit": max_rounds,
              "unanimity_target_through_round": UNANIMITY_TARGET_THROUGH_ROUND,
              "majority_fallback_from_round": MAJORITY_FALLBACK_FROM_ROUND,
              "voting_seats": list(voting),
              "contributors": list(contributors)}
    # round_limit None => unlimited: seed a horizon and EXTEND at exhaustion.
    plan = _steps(voting, contributors,
                  max_rounds if max_rounds is not None else MAJORITY_FALLBACK_FROM_ROUND)
    step_i = 0

    def _close(holder_snap: _VoteSnapshot, snaps: dict[str, _VoteSnapshot],
               round_no: int, basis: str, detail: str) -> None:
        # SOL fix point 5: persist EXACTLY the captured, validated holder bytes —
        # never re-read the mutable path. Audit metadata goes to a SEPARATE file
        # (SOL test 11: the annotation must live outside the digest domain).
        (review / "consensus.md").write_text(holder_snap.text or "")
        (review / "consensus_audit.json").write_text(json.dumps({
            "round": round_no, "basis": basis, "holder": holder_snap.name,
            "block_sha256": holder_snap.block_digest,
            "vote_file_sha256": {a: sn.file_sha256 for a, sn in snaps.items()},
            "verdicts": {a: sn.verdict for a, sn in snaps.items()},
        }, indent=2) + "\n")
        _write_state(review, build=build, round=round_no, awaiting=None,
                     verdicts=verdicts, status="consensus", basis=basis,
                     block_sha256=holder_snap.block_digest, holder=holder_snap.name, **policy)
        print(f"{_GREEN}{CONSENSUS_MARK} registered ({detail}) -> {review / 'consensus.md'}{_RESET}")
        print(f"{_GREEN}implementation window open: the implementer seat builds in the worktree,"
              f" citing consensus.md; restart the REPL when the banner shows a new build.{_RESET}\n")

    def _evaluate_round(round_no: int, quiet: bool) -> bool:
        """Evaluate ONE complete round for closure from IMMUTABLE VOTE SNAPSHOTS
        (SOL ruling 1): each file is read once; verdict, evidence status, hold,
        block bytes, and declaration all derive from the same captured bytes; the
        close persists exactly the captured holder text. Runs on every landing AND
        every poll — an in-place amendment is a new snapshot next evaluation, so
        DISAGREE->AGREE edits count and AGREE->DISAGREE edits stop counting."""
        snaps = {a: _VoteSnapshot(a, review / f"round_{round_no}_{a}.md") for a in voting}
        if any(sn.verdict is None for sn in snaps.values()):
            return False              # incomplete/truncated file: never close (SOL test 10)
        agrees = [a for a, sn in snaps.items() if sn.counts_agree()]
        disagrees = [a for a, sn in snaps.items() if sn.verdict == "VERDICT: DISAGREE"]
        holds = [a for a, sn in snaps.items()
                 if sn.verdict == "VERDICT: DISAGREE" and sn.p0_hold]

        def _try_close(counted: list[str], basis_fmt: str, detail_fmt: str) -> bool:
            holders = [sn for a, sn in snaps.items() if a in counted and sn.block_text]
            if not holders:
                return False
            # deterministic holder: fullest block, ties by voting-seat order (SOL d5)
            order = {a: i for i, a in enumerate(voting)}
            holder = max(holders, key=lambda sn: (len(sn.block_text or ""), -order[sn.seat]))
            hd = holder.block_digest or ""
            unbound = [a for a in counted
                       if snaps[a].name != holder.name and not snaps[a].bound_to(hd)]
            if unbound:
                if not quiet:
                    print(f"  UNBOUND/MISMATCHED vote by {', '.join(unbound)} vs holder"
                          f" {hd[:16]}... — no close", flush=True)
                return False
            _close(holder, snaps, round_no,
                   basis=basis_fmt.format(hd=hd[:16]), detail=detail_fmt)
            return True

        if len(agrees) == len(voting):
            if _try_close(agrees, "unanimous (block {hd})", "unanimous"):
                return True
        if round_no >= MAJORITY_FALLBACK_FROM_ROUND and not holds \
                and len(agrees) >= _majority_needed(len(voting)):
            joined = ", ".join(sorted(agrees))
            if _try_close(agrees,
                          f"majority {len(agrees)}/{len(voting)}" + " (block {hd}): " + joined,
                          f"majority {len(agrees)}/{len(voting)} at round {round_no}"):
                return True
        if not quiet:
            if holds and round_no >= MAJORITY_FALLBACK_FROM_ROUND:
                print(f"  P0-HOLD by {', '.join(holds)} — majority close blocked;"
                      " deliberation continues", flush=True)
            if agrees and disagrees and (len(agrees) == len(disagrees)
                                         or round_no >= UNANIMITY_TARGET_THROUGH_ROUND):
                print(f"  {_BOLD}SOL PRO arbitration may be warranted{_RESET}: round {round_no}"
                      f" split {len(agrees)} AGREE / {len(disagrees)} DISAGREE"
                      " — if the disagreement is substantive (not repetition),"
                      " prepare the smallest complete case file per PROTOCOL", flush=True)
        return False
    try:
        while True:
            pending = [f for f in plan[step_i] if f not in seen]
            landed = False
            for fname in pending:
                agent = fname.rsplit("_", 1)[1].split(".")[0]
                round_no = int(fname.split("_")[1])
                if fname.startswith("contrib_"):
                    if not _contrib_done(review / fname):
                        continue
                    landed, waited = True, 0.0
                    seen.add(fname)
                    landing_order.append(fname)
                    print(f"{_AGENT_COLORS.get(agent, '')}contrib landed: {fname}"
                          f" ({_AGENT_NAMES.get(agent, agent)}) — evidence, never quorum{_RESET}",
                          flush=True)
                    continue
                verdict = _verdict(review / fname)
                if verdict is None:
                    continue
                landed, waited = True, 0.0
                seen.add(fname)
                landing_order.append(fname)
                verdicts[fname] = verdict
                _print_round(review / fname, agent, round_no)

                if _evaluate_round(round_no, quiet=False):
                    return "consensus"
            if not landed:
                # IN-PLACE EDITS (round-7 lesson): a seat may bind or amend its
                # already-landed file; nothing new lands, so re-evaluate the last
                # completed round each poll, silently.
                done_rounds = [r for r in range(1, len(plan) + 1)
                               if all(f"round_{r}_{a}.md" in seen for a in voting)]
                if done_rounds and _evaluate_round(max(done_rounds), quiet=True):
                    return "consensus"
                if max_wait_s is not None and waited >= max_wait_s:
                    _write_state(review, build=build, round=0, awaiting=pending,
                                 verdicts=verdicts, status="stalled", **policy)
                    print(f"review stalled waiting for {', '.join(pending)} — state saved, nothing implemented")
                    return "stalled"
                if int(waited) % 60 == 0 and waited > 0:
                    print(f"  … still waiting for {', '.join(pending)} (Ctrl-C aborts the review)", flush=True)
                _sleep(poll_s)
                waited += poll_s
                continue
            if all(f in seen for f in plan[step_i]):
                step_i += 1
                if step_i >= len(plan):
                    if max_rounds is None:
                        next_round = len(plan) + 1
                        plan.append([f"round_{next_round}_{a}.md" for a in voting])
                    else:
                        summary = "\n".join(f"{f}: {v}" for f, v in verdicts.items())
                        (review / "no_consensus.md").write_text(
                            f"=== NO CONSENSUS ===\noperator backstop: {max_rounds} rounds on build {build}"
                            " without a close.\n\n"
                            f"{summary}\n\nThe dispute is parked for the operator. Nothing is implemented.\n"
                            "Deliberation may continue: resume_review(<this dir>, <build>) after operator direction.\n")
                        _write_state(review, build=build, round=max_rounds, awaiting=None,
                                     verdicts=verdicts, status="no_consensus", **policy)
                        print(f"{_BOLD}=== NO CONSENSUS === operator backstop at {max_rounds} rounds — parked at "
                              f"{review / 'no_consensus.md'}; nothing implemented; resumable.{_RESET}\n")
                        return "no_consensus"
            _write_state(review, build=build, round=_round_of_step(step_i, max_rounds),
                         awaiting=[f for f in plan[step_i] if f not in seen],
                         verdicts=verdicts, status="open", **policy)
    except KeyboardInterrupt:
        _write_state(review, build=build, round=0, awaiting=None, verdicts=verdicts,
                     status="aborted", **policy)
        (review / "aborted.md").write_text("review aborted by the operator; nothing implemented\n")
        print("\nreview aborted — state saved, nothing implemented\n")
        return "aborted"
