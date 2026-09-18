"""Council roles: fixed responsibilities with distinct information diets.

A role defines three things and nothing else:
  * ``brief``   — the responsibility contract the seat is prompted with;
  * ``diet``    — which information classes its round context may contain;
  * ``investigates`` — whether the seat is told to gather its own evidence with tools.

Seats do NOT all get the same prompt and then "vote" — that is fake decentralization
with extra tokens. A builder sees implementation context; a falsifier attacks the
candidate; an adjudicator weighs reports and never touches the workspace. The diet is
enforced where context is ASSEMBLED (`orchestrator._seat_context`), so a role without
``DIET_PEER_REPORTS`` structurally cannot read the other seats even in open rounds.

Every seat's answer contract ends with the anti-truncation verdict convention shared
with ``core/kernel/consensus.py``: a report only counts as a vote when its LAST
non-empty line is exactly ``VERDICT: AGREE`` or ``VERDICT: DISAGREE``. Prose is for the
humans and the other seats; the machine parses only that line.
"""

from __future__ import annotations

from dataclasses import dataclass

# Information classes a role's context may contain. The orchestrator assembles seat
# context strictly from this diet — briefs never smuggle extra context in prose.
DIET_PROBLEM = "problem"                # the operator's problem statement
DIET_EXHIBITS = "exhibits"              # operator-supplied proof/logs (Exhibit A)
DIET_WORKSPACE = "workspace"            # workspace root + investigation rights note
DIET_CANDIDATE = "candidate"            # the current leading diagnosis/candidate
DIET_PEER_REPORTS = "peer_reports"      # all other seats' previous-round reports


@dataclass(frozen=True)
class RoleSpec:
    role_id: str
    label: str
    brief: str
    diet: frozenset[str]
    investigates: bool


ROLE_REGISTRY: dict[str, RoleSpec] = {
    spec.role_id: spec
    for spec in (
        RoleSpec(
            role_id="builder",
            label="Builder / root-causer",
            brief=(
                "You are the BUILDER seat. Identify the root cause of the stated problem and "
                "sketch the fix with the smallest possible blast radius. Read the actual code "
                "and logs — never diagnose from the problem description alone. Your report MUST "
                "contain a line starting exactly 'DIAGNOSIS: ' with a one-sentence root cause, "
                "and a section 'FIX:' describing the minimal change. You never apply the fix — "
                "the council produces candidates, the operator promotes them."
            ),
            diet=frozenset({DIET_PROBLEM, DIET_EXHIBITS, DIET_WORKSPACE, DIET_CANDIDATE, DIET_PEER_REPORTS}),
            investigates=True,
        ),
        RoleSpec(
            role_id="falsifier",
            label="Falsifier / counterexample",
            brief=(
                "You are the FALSIFIER seat. Your only job is to BREAK the current candidate "
                "diagnosis: find a concrete counterexample that contradicts it. If you find one, "
                "demonstrate it against the real workspace (run the reproduction with your tools "
                "so the runtime records receipts) and put it in your report under a line starting "
                "exactly 'COUNTEREXAMPLE: '. A counterexample without a tool-backed demonstration "
                "is an opinion, not a fact. If you cannot break the candidate after genuinely "
                "trying, say what you tried and how it held."
            ),
            diet=frozenset({DIET_PROBLEM, DIET_WORKSPACE, DIET_CANDIDATE, DIET_PEER_REPORTS}),
            investigates=True,
        ),
        RoleSpec(
            role_id="reviewer",
            label="Independent reviewer",
            brief=(
                "You are the INDEPENDENT REVIEWER seat. Judge whether the candidate actually "
                "answers the operator's problem — black-box first: what did the operator ask, "
                "does the diagnosis explain every observed symptom, does the proposed fix "
                "plausibly remove the cause without collateral damage? Check the evidence "
                "yourself; do not take another seat's reading on faith."
            ),
            diet=frozenset({DIET_PROBLEM, DIET_EXHIBITS, DIET_WORKSPACE, DIET_CANDIDATE, DIET_PEER_REPORTS}),
            investigates=True,
        ),
        RoleSpec(
            role_id="verifier",
            label="Evidence verifier",
            brief=(
                "You are the EVIDENCE VERIFIER seat. You do not judge the idea — you judge the "
                "EVIDENCE. For every claim in the reports you can see: is it backed by something "
                "that actually ran (a file read, a command, a receipt), or is it plausible prose? "
                "Name every claim that has no backing. An unverified claim chain is grounds to "
                "vote DISAGREE regardless of how convincing the story reads."
            ),
            diet=frozenset({DIET_PROBLEM, DIET_WORKSPACE, DIET_CANDIDATE, DIET_PEER_REPORTS}),
            investigates=True,
        ),
        RoleSpec(
            role_id="adjudicator",
            label="Adjudicator",
            brief=(
                "You are the ADJUDICATOR seat. You weigh the other seats' reports — you never "
                "investigate the workspace yourself and you never rewrite history because a "
                "verdict is inconvenient. State which arguments carry, which fail, and why. "
                "Remember: consensus is not proof — one receipt-backed counterexample outranks "
                "any number of agreeing opinions, including yours."
            ),
            diet=frozenset({DIET_PROBLEM, DIET_CANDIDATE, DIET_PEER_REPORTS}),
            investigates=False,
        ),
    )
}

# The default bench when the operator just presses Convene: three voting seats.
DEFAULT_JUDGE_ROLES = ("builder", "falsifier", "reviewer")

_VERDICT_CONTRACT = (
    "\n\nEnd your report with exactly one final line: 'VERDICT: AGREE' or 'VERDICT: DISAGREE' "
    "about the current candidate. If you have no candidate to judge yet (round 1), end with "
    "your DIAGNOSIS/findings instead — no verdict line."
)

_ADVISOR_CONTRACT = (
    "\n\nYou sit as an ADVISOR: your report informs the judges but you hold no vote. "
    "Do not write a VERDICT line."
)


def role_brief(role_id: str, *, votes: bool, round_no: int) -> str:
    """The seat's responsibility contract for one round. Raises on unknown roles."""
    spec = ROLE_REGISTRY[role_id]
    brief = spec.brief
    if not votes:
        return brief + _ADVISOR_CONTRACT
    if round_no >= 2:
        return brief + _VERDICT_CONTRACT
    return brief
