"""The stepped audit driver: small bounded model calls, each returning a checkable artifact.

Measured 2026-08-01 on the daemon: the SAME `nemotron-3-ultra-550b-a55b:free`, asked for the
highest-risk bug in one Apache codec, produced 8,320 tokens of "Let me analyze... wait... actually"
under this runtime's single open-ended audit call — and a complete audit (named bug, cited lines,
failing test written, run, verified) under a competitor harness that forces the task into steps.
The model was never the problem; the one unbounded call was. Every output-side guard shipped since
catches that monologue AFTER the tokens are spent and still hands the operator a degraded reply.

This module removes the cause. The audit's deterministic evidence is already gathered by
`run_workspace_audit`; instead of handing the whole pile to the model in one shot, the runtime
drives three bounded calls, each too small to wander and each graded before the next begins:

1. NOMINATE — name the single highest-risk bug and cite `file:line`, as JSON. The runtime checks
   that location against the evidence and replaces the model's copied line text with the canonical
   source line (`AuditEvidence.line_at`). The model selects evidence; it does not transcribe truth.
2. PROVE — **only if the turn's execution policy authorizes it.** Write the smallest failing test
   into a per-request `generated/` folder and RUN it. The verdict is `proof_holds`: a green run is
   a DISPROOF, an errored test proves nothing, a command that never ran proves nothing.
3. SYNTHESIZE — **only when the proof held.** One bounded prose call, admitted to the report as its
   analysis section. Refuted and unproven reports carry no model prose at all.

The P0 repair (AGENT_HANDOVER §1A) changed three things here, each because the old shape produced a
specific incident:

* **Permission is state.** `core/agent_runtime/audit_policy.py` resolves one `AuditExecutionPolicy`
  before the first model call and it is carried through every step. A read-only audit stops after
  nomination with a labelled `candidate_unproven` — it does not write a test into the operator's
  workspace and run it, which is what happened.
* **A disproof advances the search.** A proof that exits 0 refutes its candidate; the counterexample
  is recorded, the claim's semantic key is banned from renomination, and the next candidate is
  nominated — up to three, then an honest `no_finding`. The old lane proved once, stopped, and then
  described the disproved claim as the finding.
* **The verdict is typed and the renderer is pure.** `core/agent_runtime/audit_verdict.py` owns
  every word; affirmative bug language exists in one branch. A report saying `NOT reproduced` under
  a `Highest-risk bug` headline is no longer reachable.

There is also no `None` return: every dead end is a bounded terminal result, because each `None`
reopened the single-shot lane this module exists to replace.

Every call routes through `MemoryFirstRouter._invoke_manifest` (paid-authorization gate, circuit
breaker, `model.call_*` telemetry) and declares `reasoning_mode="disabled"` so a thinking-capable
model spends its bounded budget on the artifact. Usage is aggregated across calls WITH its
completeness, so a turn where one provider reported nothing prints a lower bound rather than a
confident partial sum. Nothing here streams: bounded non-streaming calls are the point.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import posixpath
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from core.agent_runtime.audit_call_budget import (
    CONTEXT_CITED_WINDOW,
    CONTEXT_FULL_EXCERPT,
    CONTEXT_PROOF_RESULT,
)

# One stepped audit makes 3-5 bounded calls. The ceiling exists so a wedged provider cannot turn
# "three small calls" into an unbounded wait; each call still has its own manifest read timeout.
# The wall clock and the per-step output ceiling are ONE decision, not two. Raising the ceiling
# lengthens every call, and a clock that does not move with it just relocates the truncation from
# mid-JSON to mid-search - the operator waits longer and gets LESS.
#
# Measured live 2026-08-03 on `nvidia/nemotron-3-ultra-550b-a55b:free` after the ceiling went
# 700 -> 3000: nominate 235s, challenge 37s, nominate 149s, total 423s against a 420s budget. The
# turn died three seconds over, with a second candidate nominated and never challenged, and
# reported "no verified bug found" - a clock result wearing a search result's words.
#
# 900s buys the same audit roughly four calls at that model's speed instead of two. A weak model
# still cannot monologue: `_NOMINATE_MAX_TOKENS` bounds each call, and the ledger bounds how many.
_STEPPED_TOTAL_WALL_CLOCK_SECONDS = 900.0
# `max_tokens` is a CEILING, not a budget: only tokens the model actually emits are billed, so a
# ceiling set above what a step needs costs nothing and a ceiling set below it costs everything.
# A truncated reply is not a cheaper reply - it is a wasted call, and then a re-run, and then the
# same input billed a second and third time for an answer that was reachable the first time.
#
# These were 700 / 450 / 3000 / 1100. The 700 was measured truncating a batched review mid-JSON;
# the repo already records the same failure elsewhere (a 240-token chat turn returning EMPTY
# content, a 512-token tool turn ending `finish_reason: "length"` with no call). Each is now set
# well above the largest output its step has been observed to need, and the real limits - the
# provider's own `max_completion_tokens` and the context-window share - still bind below these in
# `adapters/openai_compatible_adapter` and `core/prompt_budget`. Those are physical. These are not.
#
# There IS a second reason these are bounded at all, and it is not cost: a thinking-capable LOCAL
# model generates until it stops, and at roughly 30 tok/s an 8,000-token ceiling is four minutes
# against a 60s HTTP read timeout - the call dies with nothing returned and the full input already
# paid for. So the ceiling is set to the largest value that still survives a local read timeout,
# not to the largest value imaginable. That is a WALL-CLOCK bound, not a spend bound.
#
# If a step ever truncates again, RAISE it - and raise the read timeout with it. Do not answer a
# truncation with a re-run.
#
# H1 investigation, 2026-08-04: this DID truncate on a file with real issue density (a Hermes-vs-
# VOOL comparison surfaced only 7 findings here against Hermes's ~20 on the same file). Driven live
# against qwen3:8b at `num_predict` 3000 / 5048 (this constant's real effective value once the
# adapter's +2048 thinking-model reserve is added -- see `_thinking_aware_output_budget`) / 9000:
# every single run hit `done_reason: length` with an unterminated JSON string, so truncation is
# real and it is not a guess. But raising this constant is NOT automatically the fix, because the
# model does not reliably use extra room for more real content: the point where it stops writing
# new findings and starts repeating a title verbatim was measured at ~1770 output tokens in the
# 5048 run and ~2956 in the 9000 run -- more raw tokens of real content before repetition set in at
# the bigger budget, not fewer. As a fraction of ceiling it was slightly earlier (1770/5048 = 35.06%
# vs 2956/9000 = 32.84%, a 2.22-percentage-point difference), but that is N=1 per ceiling -- one
# live model call each, not a trial series -- so it was one data point, not a direction. Distinct-
# finding counts across the three ceilings (22 / 13 / 26) did not scale with the ceiling at all.
# That single-call series could not settle whether raising this constant helps, hurts or does
# nothing -- which is why 2026-08-05 ran a real trial series instead of trusting N=1 further.
#
# 2026-08-05, real multi-trial follow-up, this exact constant's own call: 12 live trials (n=4 per
# ceiling, one call at a time, no mocking, no background process) of the actual `_NOMINATE_SYSTEM`
# prompt against `nvidia/nemotron-3-ultra-550b-a55b:free` through the real `_call_step` ->
# `OpenAICompatibleAdapter.run_structured_task` -> `_build_openai_payload` path this constant
# actually feeds, `reasoning_mode="disabled"` set exactly as `_call_step` sets it (line ~410 below),
# at ceilings 3000 / 5000 / 8000 (wire `max_tokens` was 5048 / 7048 / 10048 at the time of this run
# -- the +2048 reserve applied unconditionally before the same-night fix to
# `_cloud_tool_output_budget` in adapters/openai_compatible_adapter.py made the wire value equal the
# ceiling itself for a disabled-reasoning openrouter call; see that function's docstring). Aggregate
# results, mean / min / max, n=4 per ceiling:
#
#   ceiling   wall-clock (s)          completion tokens         distinct findings    truncations
#   3000      85.3 / 61.3 / 129.1     2197.5 / 1644 / 2579      10.50 /  8 / 13      0/4
#   5000      92.3 / 38.2 / 142.9     1850.0 / 1673 / 2164      10.00 /  9 / 11      0/4
#   8000      66.4 / 46.8 / 102.8     1882.2 / 1701 / 2100       9.50 /  7 / 11      0/4
#
# `finish_reason` was `"stop"` in all 12/12 trials, at every ceiling: the model never once reached
# ANY tested wire budget, let alone the smallest (5048) -- the single largest completion actually
# generated across all 12 trials (2579 tokens, ceiling=3000 trial 3) is barely half of that smallest
# wire budget. Mean wall-clock and mean completion tokens move NON-monotonically across the three
# ceilings (85.3s -> 92.3s -> 66.4s; 2197.5 -> 1850.0 -> 1882.2 tokens). Mean distinct findings does
# decrease monotonically (10.50 -> 10.00 -> 9.50) as the ceiling nearly quadruples wire-side -- but
# that half-point-per-step slide sits well inside the 7-13 per-ceiling spread (n=4), and wall-clock
# alone spans 38.2s-142.9s within the single 5000 ceiling, wider than the gap between any two
# ceilings' means. A monotonic-looking mean built from four noisy trials is not yet a trend: with
# this much within-ceiling variance and this little N, this data CANNOT show 3000 costing distinct
# findings to truncation, and it equally cannot show 5000 or 8000 buying any back -- the three
# ceilings are statistically indistinguishable from each other for this file/model/prompt
# combination. (Reliability note, not a ceiling effect: 1 of 12 attempt-1 calls, at ceiling 8000,
# errored `RuntimeError: ... did not include choices` and cleared on one retry -- 8%, n=1, not
# evidence it correlates with the ceiling rather than the free lane's own variance.)
#
# So `_NOMINATE_MAX_TOKENS` stays at 3000, and the reason is stated plainly: the data collected
# to answer "should this go higher" was INCONCLUSIVE, not that 3000 was measured sufficient or
# optimal. Nothing above shows 3000 losing real content to truncation on this call (0/12
# truncations at any ceiling tried tonight) and nothing above shows a higher ceiling recovering
# anything a bigger number would have to buy back. If a future run on a different file/model
# combination DOES produce `finish_reason: "length"` on this call, that is new evidence and this
# constant should move on it -- raising `_STEPPED_TOTAL_WALL_CLOCK_SECONDS` and the read timeout
# with it, per the rule above -- but tonight's run gives no such evidence in either direction. What
# actually recovers lost coverage when a step DOES truncate is still `_recover_findings_array`, not
# a bigger ceiling: the H1 qwen3 run above was writing 13-26 real findings inside its truncated
# reply the whole time, and the defect there was that only 1 of them ever survived parsing.
_NOMINATE_MAX_TOKENS = 3000
_CHALLENGE_MAX_TOKENS = 1200
_TEST_MAX_TOKENS = 3000
_SYNTHESIS_MAX_TOKENS = 2000
_EXCERPT_CHARS = 12000
# Activity keeps the proof's real captured output for an operator judging an inconclusive run, but
# an unbounded test/traceback dump is not what "detail" means -- bounded the same way `_EXCERPT_
# CHARS` bounds the source excerpt going the other direction.
_ACTIVITY_PROOF_OUTPUT_CHARS = 4000
# The full structured record persisted per audit turn (Phase 1.7): generous enough for a real
# multi-candidate pass with proof output, small enough that one pathological turn cannot balloon
# the durable event store. Truncated, never silently dropped -- see `_persist_audit_detail`.
_ACTIVITY_DETAIL_MAX_BYTES = 64_000
# At most three distinct candidates per authorized turn (§1A rule 4). A disproof advances the
# search; it does not restart it indefinitely. On exhaustion the turn returns the refuted
# candidates plus `no_finding` — never one of the refuted candidates dressed up as the answer.
#
# The CALL ledger is the real bound (see audit_call_budget): this is the shape of the search, that
# is its cost. A turn whose candidates are cheap may use all three; one that spends its calls on
# corrections stops earlier and says so.
_MAX_CANDIDATES = 3
# How many supported candidates one read-only audit may report. Bounded low on purpose: the
# nomination prompt asks for a DIFFERENT causal mechanism after each one, so past the file's
# real defects the only compliant reply is an invented one. The adversarial check catches
# those, but paying for them is waste.
_MAX_REPORTED_FINDINGS = 3

# There is deliberately NO phrase test for "did the operator ask for a survey".
#
# One lived here for a few hours: a regex over "pros and cons" / "good and bad" / "review this". It
# scored the operator's very next request - "audit this file for me, tell me if all is sound or we
# have room to improvements or maybe some logic fails" - as a single-bug hunt, because that is not
# how the regex was worded. Normal wording for any assistant, refused by a pattern. CLAUDE.md
# prohibits exactly this: hard-coded phrase matching must not stand in for a model decision.
#
# It was also unnecessary. The nomination is BATCHED - one call returns a list - so reporting
# several findings costs no more than reporting one, and there was never anything to ration. The
# constant below bounds a different and genuinely expensive thing: extra nominate+challenge ROUNDS
# spent hunting a SECOND provable candidate on one turn.
#
# It is 1, meaning none. Chasing a second provable candidate was a redundant second survey
# mechanism added the same morning as the regex, and it is the thing that actually costs calls -
# each round is another nominate plus another challenge. The batch already returns every finding
# the model has, in the one call, so the expensive path buys nothing the free one has not.
# `_MAX_CANDIDATES` still governs retrying after a candidate is REFUTED, which is a different job.
_MAX_PROVABLE_CANDIDATE_ROUNDS = 1
# Per manifest; the ledger bounds the TURN. A correction is cheap now (it carries the cited window,
# not the file), so the attempt count is not what needed cutting — what needed cutting was this
# budget composing with the manifest list and the candidate loop into 3 x 4 x 3.
_MAX_NOMINATION_ATTEMPTS = 3
# A provider that was refused BEFORE inference — the local resource governor declining to load a
# model that does not fit free memory. Retrying the same manifest cannot change the answer.
_RESOURCE_GATE_RE = re.compile(
    r"model_load_gated|low_memory|insufficient_memory|hardware_fit", re.IGNORECASE
)

# A call that ran out of WALL CLOCK is the same kind of fact as one refused for memory: it is about
# this box and this model, not about this request, so re-asking the same manifest buys a second
# full timeout and nothing else.
#
# Measured live 2026-08-03, one `review lbx_pdf.py` turn on a 24 GB Mac:
#     12:07:27  ollama-local:qwen3:14b  Read timed out (read timeout=180.0)
#     12:10:27  ollama-local:qwen3:14b  Read timed out (read timeout=180.0)
# Six minutes on one manifest before the driver moved on. The memory gate below already breaks
# immediately for exactly this reason; a timeout took the branch under it instead, whose comment
# reasons about RATE LIMITS — where a retry genuinely is right, because a rate limit clears and a
# 180-second local generation does not.
#
# Deliberately narrow: this matches transport exhaustion, not a model that answered badly. A
# `shape_failure` or an empty reply still gets its documented second attempt.
_CALL_TIMEOUT_RE = re.compile(
    r"read timed out|readtimeout|timed out\b|timeout=|_timeout\b", re.IGNORECASE
)

# Why a proof run settled nothing, in the operator's language. The sentence that carries this
# already says "a proof test was written and run, and it did not settle the claim", so these name
# only the MECHANISM — restating the whole verdict here produced "it did not settle the claim —
# the proof test did not settle the claim (errored)" in a live report.
_PROOF_NOTE_REASONS = {
    "errored": "the test errored before it could report a verdict",
    "did_not_run": "the test never ran",
    "timed_out": "the test timed out",
}


@dataclass
class SteppedCallResult:
    text: str = ""
    error: str = ""
    # Whether a request was actually issued. A call the budget REFUSED is not a call that reported
    # no usage — the first never happened and the second did, and counting them alike inflated the
    # turn's call count by exactly the number of refusals.
    attempted: bool = False
    usage: dict[str, Any] = field(default_factory=dict)
    # The ledger row this call opened, so a caller that learns something about the result later
    # (the artifact was rejected, the citation was refuted) can say so on the row itself.
    ledger_row: Any = None
    provider_id: str = ""
    model_name: str = ""
    model_call_id: str = ""
    response_id: str = ""


@dataclass
class SteppedFinding:
    title: str
    file: str
    line_start: int
    line_end: int
    cited_line_text: str
    failure_scenario: str
    # Finding E points 2 and 5, 2026-08-04: already collected by the nomination prompt on the raw
    # candidate dict (`_finding_defect` sets `candidate["harm_class"]`), but dropped here for the
    # PRIMARY finding even though `_survey_findings` carried it (via a title string-concat hack) for
    # everything else. Real fields now, threaded to `VerdictFinding` by `_verdict_finding`.
    harm_class: str = ""
    suggested_fix: str = ""
    # Stable identity from normalized claim + target — NOT from wording, nomination round, or
    # model, so the same underlying claim renominated later under different phrasing resolves to
    # the SAME id (SCALPEL fix, failure class D: this is what lets a survey row be recognized as
    # "the candidate that already stands as the headline finding" instead of a lexical title match).
    # Computed in `__post_init__` when left blank so every construction site gets one for free.
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            from core.agent_runtime.continuity_gate import candidate_id_for

            self.id = candidate_id_for(
                target=self.file, title=self.title, scenario=self.failure_scenario
            )


@dataclass(frozen=True)
class FindingChallenge:
    """An adversarial source check for a nominated candidate.

    This is deliberately weaker than execution: ``supported`` means the causal explanation
    survives a second, falsification-first reading of the cited source. It does not promote the
    candidate to ``proven``. ``refuted`` and ``uncertain`` both keep the candidate off-screen.
    """

    verdict: str = "uncertain"
    reason: str = "the source check did not establish the causal claim"
    counterexample: str = ""
    # 2026-08-06: whether a real check actually ran -- an executed refutation, a deterministic
    # source contradiction, or a model call that returned SOMETHING (however unusable) are all
    # `True`; a call the budget refused before it ever reached the model (`SteppedCallResult.
    # attempted` is `False` by construction in exactly that case -- see its own field comment) is
    # `False`. Both previously collapsed into the identical `verdict="uncertain"` with only a
    # free-text `reason` telling them apart, which is why `run_stepped_audit`'s own top-level
    # "how many candidates were actually reviewed" count could not distinguish "we tried and
    # struck out" from "we never got to try" without brittle string-matching on `reason`. This
    # field makes that already-true fact structured instead of prose.
    attempted: bool = True
    # "" (a real structured verdict was reached) | "provider_failure" (the call itself errored) |
    # "parse_failed" (a real reply came back but was not usable JSON) | "budget_exhausted" (never
    # attempted at all). SCALPEL fix, failure class D/I: these previously all collapsed into the
    # same `verdict="uncertain"`, distinguishable only by reading free-text `reason` prose.
    error_kind: str = ""


@dataclass
class SteppedProof:
    attempted: bool = False
    blocked_reason: str = ""
    test_path: str = ""
    test_command: str = ""
    returncode: int | None = None
    output: str = ""
    proven: bool = False
    # "" (proven) | "passed" | "errored" | "did_not_run" | "timed_out" — the four prove-it states
    # plus the hang case, which for some bug classes IS the reproduction and must not be collapsed.
    note: str = ""
    # True when the proof did not run because the turn's policy forbade it, as opposed to running
    # and failing. The two are different answers and must not share a label.
    unauthorized: bool = False
    # True when every generated artifact failed the semantic continuity gate, so nothing was
    # written and nothing ran. Distinct from `unauthorized` (permission) and from a run that
    # settled nothing: here the RUNTIME refused its own model's artifact.
    contract_rejected: bool = False
    # One row per artifact the gate judged, accepted or not — the receipt for why a proof step
    # spent two calls and wrote no file.
    artifact_checks: list[dict[str, Any]] = field(default_factory=list)
    # "repository_generated" (wrote into the audited repo's own `generated/`) or
    # "external_temp_root" (wrote into a runtime-created temp dir outside the repo, SCALPEL fix) —
    # "" when nothing was attempted. Mirrors `policy.proof_write_scope`, recorded on the RESULT so
    # Activity can show which path actually ran without re-deriving it from the policy alone.
    write_scope: str = ""
    # The isolated temp directory's real path, only when `write_scope == "external_temp_root"`.
    # Recorded before cleanup so Activity retains where the reproduction actually ran.
    temp_root: str = ""

    @property
    def refutes_the_claim(self) -> bool:
        """A proof test that RAN and exited 0 disproves the claim it was written to prove.

        Only exit 0 counts. An errored test, a test that never ran and a timeout each prove
        nothing in either direction — treating them as disproof would ban a claim on the strength
        of a broken harness.
        """
        return self.attempted and self.returncode == 0 and self.note == "passed"


@dataclass
class SteppedAuditBudget:
    total_seconds: float = _STEPPED_TOTAL_WALL_CLOCK_SECONDS
    started_monotonic: float = field(default_factory=time.monotonic)
    calls: int = 0
    last_error: str = ""
    # The call ledger IS the call budget (see audit_call_budget): the count that decides whether to
    # stop and the count the operator is shown are the same number, because when they were two
    # numbers only one of them existed.
    ledger: Any = None
    # Every model choice this turn made, so an `auto` route is attributable rather than merely
    # permitted.
    routing: Any = None

    def remaining(self) -> float:
        return max(0.0, float(self.total_seconds) - (time.monotonic() - self.started_monotonic))

    def exhausted(self) -> bool:
        return self.remaining() <= 1.0

    def may_call(self, step: str) -> bool:
        """Whether another call of this purpose is inside the bounded audit policy."""
        if self.exhausted():
            return False
        return self.ledger is None or self.ledger.may_call(step)


def _evidence_from_context(source_context: dict[str, Any] | None) -> Any | None:
    blob = (source_context or {}).get("workspace_audit_evidence")
    if not isinstance(blob, dict) or not blob.get("sources"):
        return None
    from core.agent_runtime.audit_claim_verifier import AuditEvidence

    return AuditEvidence(
        inspected_paths=tuple(str(p) for p in blob.get("inspected_paths") or ()),
        all_paths=tuple(str(p) for p in blob.get("all_paths") or ()),
        sources={str(k): str(v) for k, v in dict(blob.get("sources") or {}).items()},
        workspace_root=str(blob.get("workspace_root") or ""),
        incomplete_files=tuple(str(p) for p in blob.get("incomplete_files") or ()),
        scoped_target=str(blob.get("scoped_target") or ""),
    )


def _stepped_manifests(
    agent: Any, source_context: dict[str, Any] | None
) -> tuple[list[Any], str, str, Any]:
    """(manifests to try in order, pinned model id, refusal reason, routing contract).

    The routing decision itself lives in `audit_routing`, because it is not the audit's business
    what "auto" means — it is the runtime's, and the builder lane needs the same answer. What the
    audit owns is that its helper calls obey whatever came back: the returned list is the whole set
    of models any step of this turn may reach.
    """
    from core.agent_runtime.audit_routing import (
        MANUAL,
        resolve_routing_mode,
        select_audit_manifests,
    )

    routing = resolve_routing_mode(source_context)
    manifests, reason = select_audit_manifests(agent, source_context, routing)
    pinned = routing.requested_model if routing.mode == MANUAL else ""
    return manifests, pinned, reason, routing


def _call_step(
    agent: Any,
    *,
    manifest: Any,
    task: Any,
    source_context: dict[str, Any],
    step: str,
    prompt: str,
    system_prompt: str,
    max_output_tokens: int,
    output_mode: str,
    budget: SteppedAuditBudget,
    context_source: str = "",
    justification: str = "",
) -> SteppedCallResult:
    """One bounded call on the audit's model. Empty text always carries a reason in `.error`.

    Every call opens a ledger row BEFORE it is made and closes it with what came back, so a turn
    cannot spend a call that its own receipt does not know about. `context_source` and
    `justification` are what make the row answer "what was this given?" and "why was another one
    necessary?" — the two questions a nine-call audit could not answer about itself.
    """
    from adapters.base_adapter import ModelRequest
    from core.agent_runtime.builder.app_builder import strip_reasoning_monologue

    if budget.exhausted():
        return SteppedCallResult(error="stepped_audit_budget_exhausted")
    if budget.ledger is not None and not budget.ledger.may_call(step):
        return SteppedCallResult(error=budget.ledger.refusal_for(step) or "audit_call_budget_spent")
    if budget.routing is not None and not budget.routing.routing.cloud_permitted:
        from core.agent_runtime.audit_routing import manifest_is_cloud

        if manifest_is_cloud(manifest):
            # Belt as well as braces. The candidate list is built to contain no cloud manifest under
            # local-only, so reaching here means a caller assembled a manifest some other way. This
            # is the one choke point every audit model call passes through, which makes it the only
            # place the guarantee can be made unconditional.
            return SteppedCallResult(
                error="local_only_routing_forbids_cloud_model_call",
            )
    budget.calls += 1
    row = None
    if budget.ledger is not None:
        row = budget.ledger.open_call(
            step=step,
            context_source=context_source,
            manifest=manifest,
            prompt_chars=len(prompt) + len(system_prompt),
            justification=justification,
        )
    if budget.routing is not None:
        budget.routing.record(step=step, manifest=manifest)
    request = ModelRequest(
        task_kind="normalization_assist",
        prompt=prompt,
        system_prompt=system_prompt,
        temperature=0.1,
        max_output_tokens=max_output_tokens,
        # `messages` deliberately EMPTY so the adapter builds them from system_prompt + prompt —
        # a caller-supplied list without a system message fails the protected-system-prompt check
        # in core/prompt_budget.py regardless of size (measured live; see pinned_generation).
        output_mode=output_mode,
        # Every step here is a bounded ARTIFACT call — JSON finding, test file, short prose. A
        # thinking-capable model that spends this budget reasoning returns nothing usable, which
        # is the measured cause of the 8,320-token incident. Declared as request policy (rule 8)
        # rather than inferred from the task name.
        reasoning_mode="disabled",
        # workspace_audit_turn is the c556820 stamp, kept because the single-shot audit lane and
        # the router's telemetry both key on it; the reasoning behaviour above no longer depends
        # on it.
        metadata={"workspace_audit_turn": True, "stepped_audit_step": step},
    )
    # A HELPER CALL NEVER STREAMS. `_streaming_requested` says yes to any `plain_text` call whose
    # context carries a `runtime_event_stream_id`, and the UI puts one on every turn — so the prove
    # step's generated TEST FILE was streamed to the screen as though it were the answer, twice,
    # while the runtime's composed report was the thing actually stored. Measured live 2026-08-01.
    # The stream id is dropped for the model call only; the tool receipts the honesty guards read
    # are written through the caller's own dict on a different path and are untouched.
    call_context = {
        key: value for key, value in source_context.items() if key != "runtime_event_stream_id"
    }
    try:
        _adapter, response, error = agent.memory_router._invoke_manifest(
            manifest=manifest,
            request=request,
            output_mode=output_mode,
            task=task,
            source_context=call_context,
        )
    except Exception as exc:  # one provider fault must not take the whole audit down
        budget.last_error = type(exc).__name__
        if budget.ledger is not None:
            budget.ledger.close_call(row, result=f"error:{type(exc).__name__}")
        return SteppedCallResult(error=type(exc).__name__, attempted=True, ledger_row=row)
    if error or response is None:
        budget.last_error = str(error or "no_response")
        if budget.ledger is not None:
            budget.ledger.close_call(row, result=f"error:{str(error or 'no_response')[:80]}")
        return SteppedCallResult(error=str(error or "no_response"), attempted=True, ledger_row=row)
    usage_block = dict(getattr(response, "usage", {}) or {})
    if budget.ledger is not None:
        budget.ledger.close_call(row, result="answered", usage=usage_block)
    return SteppedCallResult(
        attempted=True,
        text=strip_reasoning_monologue(str(getattr(response, "output_text", "") or "")),
        usage=usage_block,
        ledger_row=row,
        provider_id=str(getattr(response, "provider_id", "") or getattr(manifest, "provider_id", "") or ""),
        model_name=str(getattr(response, "model_name", "") or getattr(manifest, "model_name", "") or ""),
        model_call_id=str(getattr(response, "model_call_id", "") or ""),
        response_id=str(getattr(response, "response_id", "") or ""),
    )


def _target_path_for(evidence: Any, effective_input: str) -> str:
    """The file the finding should be about: the named target when it resolves, else the file the
    audit put first in scope (scoped audits order target-first).

    This is the WORKING default — the file a citation resolves against and a prompt is anchored to
    — and it must stay non-empty even for a repository-wide pass, or nomination has nothing to
    anchor on. What must NOT inherit it is the operator-facing target line: see
    `_reported_target_path`.
    """
    from core.agent_runtime.audit_claim_verifier import _resolve_cited_path
    from core.agent_runtime.workspace_audit import audit_target_in

    named = audit_target_in(effective_input)
    if named:
        resolved = _resolve_cited_path(named, evidence)
        if resolved:
            return resolved
    for path in evidence.inspected_paths:
        if path in evidence.sources:
            return path
    return next(iter(evidence.sources), "")


def _reported_target_path(evidence: Any, target_path: str) -> str:
    """The target the REPORT may name, or "" when this pass had no single subject.

    Measured 2026-08-07: three separate fresh chats asked for ordinary navigation, were mis-claimed
    by the audit lane, swept 189 of 268 files each, and every one of them printed
    `Target: api/__init__.py`. No operator named it and no stale state held it — it was
    `inspected_paths[0]`, which is the alphabetically-first source file in that repository and
    therefore identical across chats, which is exactly why it read as contamination.

    Split from `_target_path_for` rather than folded into it because the two questions differ: a
    working anchor for citation resolution may be a default, but a headline asserting what was
    audited may not. A repository sweep has no single target, and saying so is the answer.
    """
    if not str(getattr(evidence, "scoped_target", "") or ""):
        return ""
    return str(target_path or "")


def _finding_from_unreviewed(row: dict[str, Any]) -> Any | None:
    """Rebuild a candidate the previous pass surfaced but never got to challenge.

    "Challenge your own audit" is a request to attack work that already exists. Nominating again is
    the one thing it cannot mean, and it is exactly what the runtime did: the follow-up re-entered
    nomination, spent all three of its allowed calls, and ended `blocked` while eleven unreviewed
    candidates from the previous pass sat in a list nobody had persisted.
    """
    title = str((row or {}).get("title") or "").strip()
    path = str((row or {}).get("file") or "").strip()
    if not title or not path:
        return None
    try:
        line_start = int((row or {}).get("line_start") or 0)
        line_end = int((row or {}).get("line_end") or 0)
    except (TypeError, ValueError):
        return None
    return SteppedFinding(
        title=title[:200],
        file=path,
        line_start=line_start,
        line_end=line_end,
        cited_line_text=str((row or {}).get("cited_line_text") or "")[:300],
        failure_scenario=str((row or {}).get("failure_scenario") or "")[:900],
        harm_class=str((row or {}).get("harm_class") or "").strip().lower(),
        suggested_fix=str((row or {}).get("suggested_fix") or "")[:400],
    )


def _first_json_object(text: str) -> dict[str, Any] | None:
    """The first balanced top-level JSON object in ``text``, or None.

    A reasoning model answering the plain-text retry wraps its JSON in narration ("Sure — here is
    the finding: {...} Let me know…"), and `json.loads` on the whole reply fails. Same reason the
    builder replaced `rfind` with a balanced scan: take the first `{`, walk brace depth while
    honouring strings and escapes, and parse exactly that span.
    """
    body = str(text or "")
    start = body.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(body)):
            char = body[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    with suppress(Exception):
                        parsed = json.loads(body[start : index + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    break
        start = body.find("{", start + 1)
    return None


def _recover_findings_array(text: str) -> list[dict[str, Any]]:
    """Every COMPLETE object sitting inside a (possibly truncated) `"findings": [ ... ]` array.

    H1/H2 investigation, confirmed live 2026-08-04: a nomination call that hits
    `_NOMINATE_MAX_TOKENS` truncates the OUTER `{"findings": [...]}` envelope, which then never
    closes. `_first_json_object`'s brace-depth scan cannot find a closer for that outer `{`, so it
    walks forward to the NEXT `{` — the first item inside the array — and returns THAT as a bare
    dict once its own braces balance. Correct behaviour for a single-object reply, but for a
    batched one it means every finding after the first is silently discarded, even though the
    model already wrote it: measured directly against live Ollama (qwen3:8b) at three different
    `num_predict` ceilings (3000 / 5048 / 9000), the truncated reply always contained 13-26
    well-formed finding objects before the cutoff, and exactly 1 of them ever reached
    `_nominate_finding`'s caller.

    This walks the array itself (not the whole text) so it never picks up a stray object from
    outside the "findings" key, and stops the moment an item's braces fail to balance before the
    text runs out — that item is the genuinely-truncated tail, correctly left behind, not guessed
    at.
    """
    body = str(text or "")
    match = re.search(r'"findings"\s*:\s*\[', body)
    if not match:
        return []
    index = match.end()
    length = len(body)
    recovered: list[dict[str, Any]] = []
    while index < length:
        while index < length and body[index] in " \t\r\n,":
            index += 1
        if index >= length or body[index] == "]":
            break
        if body[index] != "{":
            # Not a well-formed item boundary -- stop rather than guess past it.
            break
        start = index
        depth = 0
        in_string = False
        escaped = False
        closed_at = -1
        for cursor in range(start, length):
            char = body[cursor]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    closed_at = cursor
                    break
        if closed_at == -1:
            # Cut off mid-object by the token ceiling -- the real truncated tail. Everything
            # before it is already collected; stop here rather than emit a partial/invented item.
            break
        with suppress(Exception):
            parsed = json.loads(body[start : closed_at + 1])
            if isinstance(parsed, dict):
                recovered.append(parsed)
        index = closed_at + 1
    return recovered


_NOMINATE_SYSTEM = (
    "You are auditing one file. Answer with ONE JSON object and nothing else. No prose, no "
    "markdown, no reasoning narration.\n\n"
    'Shape: {"findings": [ ... ]} — a LIST, ordered most important first. Return every distinct '
    "issue you can support from the source you were given, not just the worst one.\n\n"
    "Each item requires: title (short name), file (the path exactly as given), line_start (int), "
    "line_end (int), cited_line_text (copy of the line at line_start; the runtime replaces this "
    "with the canonical source line), failure_scenario (at most three sentences: the concrete "
    "input or state, then the wrong outcome), harm_class, and suggested_fix.\n\n"
    "harm_class is one of: integrity (wrong or corrupted output, silent loss, truncation, "
    "round-trip mismatch), crash (raises on valid input, hangs, never terminates), perf, memory, "
    "api (interface or composability defect), versioning (format or schema evolution), "
    "test-coverage, strength (something the file does WELL — include these; a review that lists "
    "only faults is not a review).\n\n"
    "suggested_fix is one or two sentences naming the concrete change that would resolve the "
    "issue (for a strength, say what to preserve or extend instead). Never leave it empty.\n\n"
    "Two rules on truth, and they are the whole point. Describe only what the CURRENT source does: "
    "a future redesign, a possible format change, or code that might someday be added is not a "
    "finding. And do not repeat yourself — two entries describing one mechanism in different words "
    "is one finding, not two."
)

_LINE_REFERENCE_RE = re.compile(
    r"\blines?\s+(\d+)(?:\s*[-\u2013]\s*(\d+))?",
    re.IGNORECASE,
)

_FUTURE_DEPENDENCY_RE = re.compile(
    r"\b(?:future\s+(?:versions?|formats?|schemas?|implementations?|changes?)|"
    r"(?:serialization|serializer|format|schema|protocol|implementation|code|version)\b"
    r".{0,80}\b(?:ever\s+)?(?:changes?|changed|adds?|added|removes?|removed)|"
    r"were\s+to\s+(?:change|add|remove|emit)|"
    r"if\s+.{0,80}\b(?:future\s+versions?|ever\s+changes?))",
    re.IGNORECASE | re.DOTALL,
)

_ABSOLUTE_SOURCE_NEGATION_RE = re.compile(
    r"\b(?P<name>[A-Za-z_]\w*)\s+(?:was|is|were|are)\s+never\s+"
    r"(?P<verb>extracted|assigned|loaded|initialized|read|used)\b",
    re.IGNORECASE,
)

# The harm classes an audit can report. `integrity` and `crash` are the only ones a proof run can
# settle, so they are the only ones that may be carried to execution; the rest are reported as
# observations and labelled as such.
#
# They used to be DELETED. `_finding_defect` rejected any scenario that did not match
# `_OUTPUT_INTEGRITY_HARM_RE`, the nomination prompt pre-banned the same categories, and the
# challenge prompt refuted them a third time. So "audit this file, tell me pros and cons" could not
# return a performance, memory, API-design or versioning observation however true it was - and that
# is where most of a real review lives. Tiering them keeps the proof machinery honest (only a
# provable class is ever called proven) while letting the rest be reported.
PROVABLE_HARM_CLASSES = ("integrity", "crash")
OBSERVATION_HARM_CLASSES = ("perf", "memory", "api", "versioning", "test-coverage", "strength")
HARM_CLASSES = PROVABLE_HARM_CLASSES + OBSERVATION_HARM_CLASSES

# As of the harm_class-downgrade fix (2026-08-04), this regex's closed vocabulary is the SOLE
# gate for the provable tier: a self-declared crash/integrity claim is trusted only when the
# claim text matches one of the phrasings below, and is otherwise downgraded to a lower severity
# (see `_finding_defect`) regardless of whether the underlying bug is real. A genuine defect
# phrased outside this vocabulary -- e.g. "state is clobbered between calls" or "subtly malformed
# under concurrent access" -- will not be recognized as provable and will be downgraded rather
# than accepted at face value. This is a known, accepted, bounded tradeoff, not a silent gap; do
# not treat it as license to keep expanding the vocabulary here indefinitely -- there will always
# be another phrasing it misses.
_OUTPUT_INTEGRITY_HARM_RE = re.compile(
    r"\b(?:incorrect|wrong|corrupt(?:ed|ion)?|data\s+loss|incompatible|"
    r"crash(?:es|ed|ing)?|decompression\s+(?:fails?|errors?|breaks?)|"
    r"fails?\s+to\s+decompress|round[- ]?trip\s+(?:fails?|differs?|changes?)|"
    r"returns?\s+(?:an?\s+)?(?:empty|partial|short|different|incorrect|wrong)\b|"
    r"(?:missing|fewer)\s+(?:bytes?|data|records?|lines?)|"
    r"(?:short|shorter)\s+(?:buffer|output|data)|never\s+terminates|hangs?|infinite\s+loop|"
    r"silent(?:ly)?\s+(?:drops?|discards?|truncates?|loses?)\s+"
    r"(?:bytes?|data|output|records?|lines?)|"
    r"(?:bytes?|data|output|records?|lines?)\s+(?:are\s+)?(?:silently\s+)?"
    r"(?:dropped|discarded|truncated|lost)|truncat(?:e|es|ed|ion|ing)|"
    r"raises?\s+(?:an?\s+)?(?:\w+)?(?:error|exception))\b",
    re.IGNORECASE,
)


def _finding_defect(
    candidate: dict[str, Any],
    evidence: Any,
    target: str,
    *,
    allow_observations: bool = False,
) -> str:
    """Why this nominated finding is not checkable, or "" when it is.

    The model chooses a source location, but never owns the source text rendered to the operator.
    ``_nominate_finding`` canonicalizes that text from the evidence after this validates the range.
    Semantic truth is then judged by challenge/proof, not by a weak model's transcription skill.
    """
    from core.agent_runtime.audit_claim_verifier import _resolve_cited_path

    for key in ("title", "file", "line_start", "line_end", "cited_line_text", "failure_scenario"):
        if key not in candidate:
            return f"missing key `{key}`"
    try:
        line_start = int(candidate["line_start"])
        line_end = int(candidate["line_end"])
    except (TypeError, ValueError):
        return "line_start/line_end must be integers"
    if line_start < 1 or line_end < line_start:
        return "line range is not a valid 1-indexed range"
    resolved = _resolve_cited_path(str(candidate["file"]), evidence) or (
        target if str(candidate["file"]).strip() == target else ""
    )
    if not resolved:
        return f"file `{candidate['file']}` is not among the files this audit read"
    if not evidence.was_read_completely(resolved) and line_end > evidence.line_count(resolved):
        return f"line {line_end} is beyond what was read of `{resolved}`"
    total = evidence.line_count(resolved)
    if line_end > total:
        return f"line {line_end} does not exist — `{resolved}` has {total} lines"
    failure_scenario = str(candidate.get("failure_scenario") or "")
    if _FUTURE_DEPENDENCY_RE.search(failure_scenario):
        return (
            "the failure scenario depends on an unbuilt future code or format change, not a "
            "concrete input that fails against the current source"
        )
    claim_text = f"{candidate.get('title') or ''} {failure_scenario}"
    # A non-integrity observation is no longer DELETED, it is TIERED. Only `integrity` and `crash`
    # can be settled by running something, so only those are carried to a proof step; perf, memory,
    # api, versioning, test-coverage and strength are reported as observations and labelled as
    # such. Rejecting them was why "tell me pros and cons" could not return a single pro.
    declared = str(candidate.get("harm_class") or "").strip().lower()
    if declared and declared not in HARM_CLASSES:
        return f"harm_class {declared!r} is not one of {', '.join(HARM_CLASSES)}"
    provable = bool(_OUTPUT_INTEGRITY_HARM_RE.search(claim_text))
    if declared in PROVABLE_HARM_CLASSES and not provable:
        # Self-declaring crash/integrity is not enough on its own to occupy the tier that maps to
        # High severity -- the claim text has to actually allege the kind of harm that tier promises,
        # or a model can pick "crash" to buy High severity (and a bigger score deduction) with no
        # content behind it. Fall through to the same undeclared-inference rule below.
        declared = ""
    if not declared:
        # An undeclared class falls back to the historical rule, so a model that ignores the field
        # cannot smuggle an unprovable claim into the provable tier by omission.
        declared = "integrity" if provable else "api"
    candidate["harm_class"] = declared
    if not allow_observations and declared not in PROVABLE_HARM_CLASSES and not provable:
        # The PRIMARY finding is the one a proof run is written against and the only one that can
        # ever be called proven, so it has to be a class execution can settle. Observations are
        # welcome - as SURVEY ROWS, where `allow_observations` is true and nothing is executed.
        # Letting a performance note occupy the primary slot would put an unprovable claim through
        # the challenge/proof machinery and print it under the headline.
        return (
            "the scenario identifies no incorrect output, corruption, silent data loss, crash on "
            "valid input, or decompression incompatibility; a performance or compression-ratio "
            "observation is reported as an observation, not as the audited defect"
        )
    # Explicit causal lines must exist in the source that was actually read. They do not have to
    # fit inside the model's initially declared range: the runtime owns citation truth and expands
    # that range in ``_nominate_finding``. Rejecting a real same-file line only traps weak models in
    # a transcription/correction loop; rejecting an unread or nonexistent line still blocks drift.
    for match in _LINE_REFERENCE_RE.finditer(claim_text):
        referenced_start = int(match.group(1))
        referenced_end = int(match.group(2) or referenced_start)
        if referenced_start < 1 or referenced_end < referenced_start:
            return f"the failure scenario contains an invalid line range {referenced_start}-{referenced_end}"
        if referenced_end > total:
            return (
                f"the failure scenario relies on line {referenced_start}"
                f"{'-' + str(referenced_end) if referenced_end != referenced_start else ''}, but "
                f"`{resolved}` has only {total} source lines in the evidence that was read"
            )
    return ""


def _canonical_finding_range(candidate: dict[str, Any]) -> tuple[int, int]:
    """Union the declared range with every explicit causal line named by the scenario/title."""
    line_start = int(candidate["line_start"])
    line_end = int(candidate["line_end"])
    claim_text = f"{candidate.get('title') or ''} {candidate.get('failure_scenario') or ''}"
    for match in _LINE_REFERENCE_RE.finditer(claim_text):
        referenced_start = int(match.group(1))
        referenced_end = int(match.group(2) or referenced_start)
        line_start = min(line_start, referenced_start)
        line_end = max(line_end, referenced_end)
    return line_start, line_end


def _bounded_row_text(value: Any, limit: int) -> str:
    """A model-supplied string, trimmed and length-capped to the same bound the primary
    finding already enforces at construction -- so every row in a batch gets the same
    guarantee the first one always had, instead of only the consumer that most recently
    got adversarially caught leaking an unbounded one."""
    return str(value or "").strip()[:limit]


# Two false positives survived `_challenge_finding` on one live audit in the same night, and both
# share a root cause: the claim was about a ROUND TRIP or a PAIRED relationship (compress vs.
# decompress, encoder state vs. decoder state), and the challenge model was handed only a narrow
# window around the CITED side. It copied `if not blob: return b""` out of `decompress` and reasoned
# about it alone; `compress`'s own empty-input branch -- the other half of the round trip the claim
# was actually about -- was never in the window, so nothing could check whether the two sides
# actually disagreed. One of the two survivors then suggested a fix (reset `last_code`/`last_size`
# on the encoder side only) that was applied and directly observed to corrupt a real decoded value
# on the very next matched line after a raw one.
#
# The repair is not execution -- it is giving the SAME challenge model MORE of the source it would
# need to trace both sides, plus a stricter sequence to follow before it may say "supported". This
# detector decides only whether the claim's own words allege a broken pair; it reads the claim TEXT
# alone (title + failure_scenario), never the filename or anything file-specific, so it generalizes
# to any codebase this runtime audits -- the same lightweight keyword/pattern style already used by
# `_ABSOLUTE_SOURCE_NEGATION_RE` above.
_ROUND_TRIP_CLAIM_RE = re.compile(r"round[\s-]?trip", re.IGNORECASE)

# A claim naming a RESET against something that reads like retained state -- the second live
# incident's exact vocabulary ("last_code/last_size must be reset after a raw line"). A reset claim
# always implies a second site (the other place that state is read, or is supposed to still apply)
# that a narrow window around one function cannot show either side of.
_STATE_RESET_CLAIM_RE = re.compile(
    r"\breset\w*\b.{0,80}?\b(?:state|variable|counter|flag|delta|last_\w+|"
    r"\w*_(?:code|size|count|length|len|offset|index)\w*)\b"
    r"|"
    r"\b(?:state|variable|counter|flag|delta|last_\w+|"
    r"\w*_(?:code|size|count|length|len|offset|index)\w*)\b.{0,80}?\breset\w*\b",
    re.IGNORECASE | re.DOTALL,
)

# Named producer/consumer vocabulary pairs. A claim mentioning BOTH sides of one of these pairs is
# alleging a mismatch between them, and that mismatch cannot be checked without seeing both sides'
# actual source -- exactly what a window around only the cited side omits.
_PAIRED_OPERATION_TERM_PAIRS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"\bencod\w*", re.IGNORECASE), re.compile(r"\bdecod\w*", re.IGNORECASE)),
    (re.compile(r"\bcompress\w*", re.IGNORECASE), re.compile(r"\bdecompress\w*", re.IGNORECASE)),
    (
        re.compile(r"\bserializ\w*|\bserialis\w*", re.IGNORECASE),
        re.compile(r"\bdeserializ\w*|\bdeserialis\w*|\bpars\w*", re.IGNORECASE),
    ),
    (re.compile(r"\bwrit(?:e|es|ing|ten)\b", re.IGNORECASE), re.compile(r"\bread\w*", re.IGNORECASE)),
)


def _is_paired_operation_claim(claim_text: str) -> bool:
    """Whether the claim's own words allege a broken PAIR rather than a single-sided defect.

    Detector only, over the model's title/failure_scenario text -- never the filename or anything
    file-specific -- so this generalizes to any codebase VOOL audits, not just this incident's file.
    """
    text = str(claim_text or "")
    if _ROUND_TRIP_CLAIM_RE.search(text) or _STATE_RESET_CLAIM_RE.search(text):
        return True
    return any(
        first.search(text) and second.search(text) for first, second in _PAIRED_OPERATION_TERM_PAIRS
    )


# Named-pair vocabulary for the STRUCTURAL scan below -- distinct from the claim-text detector
# above. This one maps a cited function's own NAME to the name-fragments a sibling method would
# plausibly carry if it is that function's pair (e.g. citing `decompress` looks for `compress`).
#
# Accepted limitation, disclosed and not fixed here: this list is finite and does not cover every
# producer/consumer vocabulary a codebase might use (e.g. `marshal`/`unmarshal`). A claim naming a
# pair outside this list gets no structural sibling resolution -- it safely falls back to the
# existing single-window prompt, the same as a bare function with no class at all, never an error
# or a fabricated pairing. Extending this list is a small, low-risk addition when a new pair shows
# up in practice; it is not extended speculatively here. `pack`/`unpack` was one such gap until a
# live round-trip claim against a frame packer needed it (see
# tests/test_verify_paired_operation_prompt_independent.py), so it is listed below now.
_SIBLING_NAME_PAIRS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"decompress"}), frozenset({"compress"})),
    (frozenset({"decode"}), frozenset({"encode"})),
    (frozenset({"deserialize", "deserialise", "parse"}), frozenset({"serialize", "serialise"})),
    (frozenset({"read"}), frozenset({"write"})),
    (frozenset({"unpack"}), frozenset({"pack"})),
)

# The paired operation's own source is typically a short, bounded method, not a whole file, so it
# gets a dedicated cap rather than reusing `_EXCERPT_CHARS` (12000, sized for a whole file excerpt).
_PAIRED_OPERATION_CHARS = 4000


def _sibling_pair_terms(function_name: str) -> frozenset[str]:
    """The name-fragments a sibling method must contain to plausibly be ``function_name``'s pair.

    Matching is by whole ``_``-separated token, never substring -- the same reason
    `_sibling_match_score` below documents its own token split. `'compress'` is a literal substring
    of `'decompress'`, and `'serialize'`/`'serialise'` are literal substrings of
    `'deserialize'`/`'deserialise'`, so a naive `term in lowered` check against the cited function's
    own (unsplit) name self-pollutes: citing `decompress` would include `decompress` itself in its
    own pair-term set, and citing `deserialize` would pull in every term in both directions. A
    polluted set then makes an unrelated method whose name merely CONTAINS the cited name (e.g. an
    unrelated `_alt_decompress` from a different codec) match as a fabricated "sibling" even when no
    real pair method exists at all. Token-set membership excludes this: `'compress' in {'decompress'}`
    is False, while the substring check `'compress' in 'decompress'` is True.
    """
    lowered = str(function_name or "").lower()
    lowered_tokens = {token for token in lowered.split("_") if token}
    terms: set[str] = set()
    for left, right in _SIBLING_NAME_PAIRS:
        if any(term in lowered_tokens for term in left):
            terms |= right
        if any(term in lowered_tokens for term in right):
            terms |= left
    return frozenset(terms)


def _sibling_match_score(name: str, pair_terms: frozenset[str]) -> int | None:
    """How well ``name`` matches one of ``pair_terms``, or None when it does not match at all.

    Matching is by whole ``_``-separated token, never substring: the term ``decompress`` matches
    the token ``decompress`` inside ``_zstd_decompress``'s split name (``{'zstd', 'decompress'}``)
    exactly as it matches the bare name ``decompress`` -- token-splitting alone cannot exclude
    ``_zstd_decompress`` as a candidate, since ``decompress`` genuinely is one of its tokens. What
    distinguishes them is EXTRA tokens: a name that consists of the bare term and nothing else
    (``decompress``, one token) scores higher than a name that carries the term plus other segments
    (``_zstd_decompress``, two tokens). This is what a real incident got wrong: citing `compress`
    lines 80-82 on `liquefy_apache_repetition_v1.py` resolved to the 3-line `_zstd_decompress`
    instead of the real ~54-line `decompress`, because substring-containment matched both and body
    order picked the wrong one first. Equal scores are left to the caller, which keeps
    first-in-body-order as the tiebreak.
    """
    tokens = [token for token in str(name or "").lower().split("_") if token]
    if not tokens:
        return None
    token_set = set(tokens)
    if not (token_set & pair_terms):
        return None
    return 2 if len(token_set) == 1 else 1


# Accepted limitation, disclosed and NOT fixed by this round or any prior round: the scoring above
# is a 2-tier heuristic (bare-name == 2, name-plus-extra-tokens == 1). `_best_sibling_match` only
# refuses to pick when two candidates land on the SAME top score (a genuine tie). When scores
# genuinely DIFFER, the higher-scoring candidate is trusted outright -- but a higher score is a
# proxy for "more likely the real pair," not proof of it. A bare-named decoy with nothing to do
# with the cited method can still legitimately outscore the true sibling if the true sibling
# happens to carry its own decorating prefix or suffix (e.g. an unrelated bare `write` method
# would outscore a real but non-bare `_versioned_write_v2` sibling, 2 to 1, even though the latter
# is the actual pair). This is a real gap in a heuristic that only has two tiers to work with, and
# it is left as-is rather than patched around here. The mechanism still degrades safely rather than
# fabricating something from nothing: the resolved sibling is handed to the challenge model as
# SUPPLIED source, and `_CHALLENGE_SYSTEM` requires it to trace what that actual source does (steps
# (b)/(c)) before it may call a claim 'supported' -- a wrong-but-real method is a misleading hint
# the challenge step can still catch, not a fabricated pairing conjured from no candidate at all.
def _best_sibling_match(methods: list[Any], cited_method: Any, pair_terms: frozenset[str]) -> Any | None:
    """The highest-`_sibling_match_score`d sibling, or None when the top score is a genuine tie.

    QA round 3, confirmed: picking the first-in-body-order candidate among an EQUAL top score
    reproduced the exact pre-fix failure this function exists to prevent -- a wrong, unrelated
    method confidently handed to the challenge model as "the paired operation", with no signal the
    pick was arbitrary. Two live shapes: a bare `parse` decoy tying a real `deserialize` sibling at
    score 2, and two `_..._internal`-named methods tying at score 1 with neither bare. In both, the
    wrong candidate happened to sit first in the class body and was returned with full confidence.

    A single winner is still returned exactly as before -- this only refuses to pick when a SECOND,
    distinct candidate reaches the same top score. `_paired_operation_source` already treats `None`
    as its existing safe fallback (the single cited-window prompt), so detecting the tie here is
    the whole fix; nothing downstream changes.
    """
    best_score: int | None = None
    best_member: Any | None = None
    tied_at_best = False
    for member in methods:
        if member is cited_method:
            continue
        score = _sibling_match_score(member.name, pair_terms)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_score = score
            best_member = member
            tied_at_best = False
        elif score == best_score:
            tied_at_best = True
    return None if tied_at_best else best_member


def _paired_operation_source(evidence: Any, finding: SteppedFinding) -> str:
    """The sibling method's full numbered source, when the cited lines sit inside a method whose
    name plausibly pairs with another one in the SAME class -- e.g. citing `decompress` finds
    `compress` in the same class body.

    Returns "" whenever this cannot be established structurally: not a Python file, unparsable, no
    enclosing class/method found for the cited lines, or no sibling name matches the pair
    vocabulary. Every one of those is a deliberate fallback, not a failure -- the caller uses the
    existing cited-window-only prompt in every such case, exactly as before this function existed.
    This does not execute anything; it is a structural read of source already in `evidence.sources`,
    the same approach `_deterministic_source_contradiction` already uses above.
    """
    if not str(finding.file).endswith(".py"):
        return ""
    body = str(evidence.sources.get(finding.file, "") or "")
    if not body:
        return ""
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, TypeError):
        return ""
    line_start = int(finding.line_start)
    line_end = int(finding.line_end)

    def _end_line(node: ast.AST) -> int:
        return int(getattr(node, "end_lineno", None) or getattr(node, "lineno", 0) or 0)

    def _effective_start_line(member: ast.AST) -> int:
        """A method's effective start, including any decorator lines above `def`/`async def`.

        `FunctionDef.lineno` / `AsyncFunctionDef.lineno` point at the `def` line itself, never at a
        decorator above it. A citation landing exactly on `@staticmethod`, `@property`, or any other
        decorator line therefore sits ABOVE `member.lineno` and outside a containment test that only
        checks the bare `lineno` -- even though the decorator is textually part of that method, not
        of whatever precedes it. Reproduced independently by adversarial and QA review against the
        same nested-class fixture: citing the `@staticmethod` line directly above
        `ArchiveManager.Inner.encode` fell through this check, so no candidate for `Inner` was
        collected at all, and the citation resolved against an unrelated OUTER class's `encode`
        instead. Falling back to the outermost decorator's line when one exists keeps a plain,
        undecorated method's start untouched.
        """
        decorator_list = getattr(member, "decorator_list", None) or []
        starts = [int(member.lineno)] + [int(decorator.lineno) for decorator in decorator_list]
        return min(starts)

    # `ast.walk` visits breadth-first from the module down, so an OUTER class is always produced
    # before a class nested inside one of its methods' bodies. A citation that lands inside a
    # nested class's method also sits inside the outer method that contains that nested class
    # textually (the nested class's line range is a strict subset of its enclosing method's range),
    # so BOTH the outer class and the inner class are legitimate containment matches for the same
    # citation line. Committing to the first one found (the outer one, in walk order) resolves the
    # citation against the WRONG class's own methods entirely -- confirmed independently by
    # adversarial review and QA against the identical fixture: citing a line inside
    # `OuterCodec.decompress`'s locally-defined `InnerCodec.decompress` resolved the "paired
    # operation" to `OuterCodec.compress`, a real but structurally unrelated method, while the
    # actual pair (`InnerCodec.compress`) was never considered.
    #
    # The fix: gather EVERY class whose own direct-child method's range contains the citation line,
    # then let the NARROWEST such containing method win -- the innermost, most specific
    # containment. A nested class's containing method range is always a strict subset of every
    # enclosing class's containing method range (nesting requires the outer method's body to
    # physically contain the inner class's own definition), so "narrowest range" and "most deeply
    # nested" agree at every depth, not just two levels. A citation that genuinely belongs to an
    # outer method -- no nested class's own method actually spans that line -- produces no inner
    # candidate at all, so this cannot overcorrect into always preferring nesting.
    candidates: list[tuple[Any, list[Any]]] = []
    for class_node in ast.walk(tree):
        if not isinstance(class_node, ast.ClassDef):
            continue
        methods = [
            member
            for member in class_node.body
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        cited_method = next(
            (
                member
                for member in methods
                if _effective_start_line(member) <= line_end and _end_line(member) >= line_start
            ),
            None,
        )
        if cited_method is None:
            continue
        candidates.append((cited_method, methods))

    if not candidates:
        return ""

    def _containing_method_width(candidate: tuple[Any, list[Any]]) -> int:
        cited_method, _methods = candidate
        return _end_line(cited_method) - _effective_start_line(cited_method)

    cited_method, methods = min(candidates, key=_containing_method_width)

    pair_terms = _sibling_pair_terms(cited_method.name)
    if not pair_terms:
        return ""
    sibling = _best_sibling_match(methods, cited_method, pair_terms)
    if sibling is None:
        return ""
    # Same reasoning as `_effective_start_line`'s own docstring, applied to the one place a
    # method's range is used for TEXT EXTRACTION rather than containment: `sibling.lineno` is the
    # `def`/`async def` line, never a decorator above it. Left as `int(sibling.lineno)`, a decorated
    # sibling's own `@staticmethod`/`@property`/etc. line is silently dropped from the "full
    # numbered source" handed to the challenge model as "the paired operation, for comparison" --
    # found by adversarial review one step past round 6's containment fix, confirmed independently.
    start = max(1, _effective_start_line(sibling))
    source_lines = body.splitlines()
    end = min(len(source_lines), _end_line(sibling))
    numbered_lines = [
        f"{number:>6}: {source_lines[number - 1]}" for number in range(start, end + 1)
    ]
    snippet = "\n".join(numbered_lines)
    if len(snippet) <= _PAIRED_OPERATION_CHARS:
        return snippet
    return _truncated_paired_source(numbered_lines)


def _paired_truncation_marker(omitted: int) -> str:
    return (
        f"        ...{omitted} line(s) omitted from the middle of this method -- source exceeds "
        f"the {_PAIRED_OPERATION_CHARS}-character paired-operation cap..."
    )


def _truncated_paired_source(numbered_lines: list[str]) -> str:
    """``numbered_lines`` kept within ``_PAIRED_OPERATION_CHARS``, with an explicit marker --
    never a silent flat slice.

    A live method QA built (~90 lines, the reset statements this whole fix is about sitting at the
    END of the body) confirmed a naive `snippet[:_PAIRED_OPERATION_CHARS]` prefix slice drops them
    with no indication anything was cut. Cleanup/reset logic characteristically lives at the END of
    a method, so this keeps the SIGNATURE (line one; what the method is) plus as much of the END of
    the body as fits, and always states how many lines were dropped from the middle -- matching
    `_numbered_excerpt`'s own convention of an explicit line-count note rather than a bare cut.
    """
    if not numbered_lines:
        return ""
    signature = numbered_lines[0]
    body_lines = numbered_lines[1:]
    kept_from_tail: list[str] = []
    for line in reversed(body_lines):
        trial_tail = [line, *kept_from_tail]
        omitted = len(body_lines) - len(trial_tail)
        pieces = [signature]
        if omitted > 0:
            pieces.append(_paired_truncation_marker(omitted))
        pieces.extend(trial_tail)
        if len("\n".join(pieces)) > _PAIRED_OPERATION_CHARS:
            break
        kept_from_tail = trial_tail
    omitted = len(body_lines) - len(kept_from_tail)
    pieces = [signature]
    if omitted > 0:
        pieces.append(_paired_truncation_marker(omitted))
    pieces.extend(kept_from_tail)
    return "\n".join(pieces)


_CHALLENGE_SYSTEM = (
    "You are the adversarial source checker for ONE proposed bug. You are not finding a new bug "
    "and you are not rewriting the report. Work through these steps, in this order, before you "
    "decide:\n"
    "(a) Restate, in one sentence, the exact invariant the claim alleges is violated.\n"
    "(b) Identify the producer and the consumer code paths for that invariant from what you were "
    "given -- the cited source window, and the paired operation's source when one is supplied.\n"
    "(c) State what the ACTUAL code does on BOTH sides for the scenario described -- not what "
    "would be plausible, what the source in front of you actually executes.\n"
    "(d) Only then decide. 'supported' requires that tracing BOTH sides -- when a paired operation "
    "was supplied -- still shows the alleged wrong outcome. If the paired operation's own behavior "
    "already produces the correct result for the scenario described, the claim is 'refuted' "
    "regardless of how plausible the cited lines look in isolation.\n\n"
    "Answer with one JSON object only: verdict ('supported', 'refuted', or 'uncertain'), reason "
    "(one precise sentence), counterexample (one concrete input or semantic contradiction, or "
    "empty). Use 'supported' only when every causal operation appears in the supplied source and "
    "the wrong outcome follows from it. A method call is not indexing unless the source or the "
    "method's specified semantics perform indexing; empty input alone does not imply an exception. "
    "If the claim depends on code or API behavior not established here, answer 'uncertain'. A claim "
    "that fails only if a future version, format, serializer, or implementation changes is "
    "'refuted' as a current bug. A claim whose only outcome is worse compression ratio, speed, "
    "memory use, or lost optimization is also 'refuted' for this output-integrity audit."
)


def _deterministic_source_contradiction(
    evidence: Any, finding: SteppedFinding
) -> tuple[str, str] | None:
    """Refute absolute source claims contradicted inside their own cited Python range.

    This is deliberately narrow. Static analysis cannot prove an arbitrary failure scenario, but
    it can disprove ``x was never assigned/extracted`` when the cited statement stores into ``x``.
    The live weak-model incident made exactly that absolute claim about ``s_raw`` while citing the
    tuple assignment that extracts it. Letting the same model judge that contradiction again only
    reproduced the hallucination.
    """
    scenario = str(finding.failure_scenario or "")
    claims = list(_ABSOLUTE_SOURCE_NEGATION_RE.finditer(scenario))
    if not claims or not str(finding.file).endswith(".py"):
        return None
    body = str(evidence.sources.get(finding.file, "") or "")
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, TypeError):
        return None

    ranged_names: list[ast.Name] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and int(finding.line_start) <= int(getattr(node, "lineno", 0) or 0) <= int(finding.line_end)
    ]
    for claim in claims:
        name = str(claim.group("name") or "")
        verb = str(claim.group("verb") or "").lower()
        expected_context = ast.Load if verb in {"read", "used"} else ast.Store
        contradicting = next(
            (
                node
                for node in ranged_names
                if node.id.lower() == name.lower() and isinstance(node.ctx, expected_context)
            ),
            None,
        )
        if contradicting is None:
            continue
        line_no = int(getattr(contradicting, "lineno", 0) or 0)
        source_line = evidence.line_at(finding.file, line_no).strip()
        action = "reads" if expected_context is ast.Load else "assigns"
        return (
            f"the scenario says `{name}` was never {verb}, but cited line {line_no} {action} it",
            f"line {line_no}: {source_line[:300]}",
        )
    return None


def _challenge_finding(
    agent: Any,
    *,
    manifest: Any,
    task: Any,
    source_context: dict[str, Any],
    evidence: Any,
    finding: SteppedFinding,
    budget: SteppedAuditBudget,
    usages: list[dict[str, Any]],
) -> FindingChallenge:
    """Falsify a read-only candidate before any of its semantics reach the operator.

    Citation validation proves only that text exists at a line. The repeated incident proved that
    this is insufficient: the model copied ``blob.startswith(...)`` correctly and then invented
    indexing and ``IndexError``. A compact second pass gets a local source window and a different
    job: attack that explanation. Failure to obtain a clean ``supported`` result fails closed.
    """
    from core.agent_runtime.audit_claim_execution import refuting_claim

    # DECIDABLE CLAIMS ARE DECIDED, NOT VOTED ON. Before any model spends a call arguing about it,
    # a claim like "`blob.startswith(X)` raises IndexError on an empty blob" is EXECUTED against the
    # empty builtin it names. That exact claim shipped to an operator twice because the adversarial
    # check was another call to the model that made it. See `audit_claim_execution`.
    executed = refuting_claim(finding.title, finding.failure_scenario)
    if executed is not None:
        with suppress(Exception):
            from core.runtime_task_events import emit_runtime_event

            emit_runtime_event(
                source_context,
                event_type="audit_claim_executed",
                message=f"claim refuted by execution: {executed.counterexample()}",
                details={
                    "snippet": executed.snippet,
                    "claimed_exception": executed.claimed_exception,
                    "observed": executed.raised or executed.result_repr,
                    "verdict": "refuted",
                },
            )
        return FindingChallenge(
            verdict="refuted",
            reason=(
                "the claimed exception does not occur: the runtime executed the operation the "
                "scenario names and observed the opposite"
            ),
            counterexample=executed.counterexample(),
        )

    contradiction = _deterministic_source_contradiction(evidence, finding)
    if contradiction is not None:
        reason, counterexample = contradiction
        return FindingChallenge(
            verdict="refuted",
            reason=reason,
            counterexample=counterexample,
        )
    body = str(evidence.sources.get(finding.file, "") or "")
    source_lines = body.splitlines()
    window_start = max(1, int(finding.line_start) - 8)
    window_end = min(len(source_lines), int(finding.line_end) + 8)
    window = "\n".join(
        f"{number:>6}: {source_lines[number - 1]}"
        for number in range(window_start, window_end + 1)
    )
    # A round-trip or producer/consumer claim cannot be checked from the cited side alone -- see the
    # comment above `_is_paired_operation_claim`. When the claim's own words allege a broken pair
    # AND the structural scan finds a plausible sibling, that sibling's full source is added to the
    # window rather than replacing it. Every other claim, and every case the structural scan cannot
    # resolve (non-Python, unparsable, no sibling found), gets exactly the prior single-window
    # prompt -- this never removes context, it only sometimes adds more.
    claim_text = f"{finding.title} {finding.failure_scenario}"
    paired_source = (
        _paired_operation_source(evidence, finding) if _is_paired_operation_claim(claim_text) else ""
    )
    paired_block = (
        (
            "The paired operation, for comparison (the other side of the same round trip / "
            f"producer-consumer relationship, in the same class):\n{paired_source}\n\n"
        )
        if paired_source
        else ""
    )
    prompt = (
        f"Proposed title: {finding.title}\n"
        f"Proposed location: {finding.file}:{finding.line_start}-{finding.line_end}\n"
        f"Proposed failure scenario: {finding.failure_scenario}\n\n"
        f"Source window (real line numbers):\n{window}\n\n"
        f"{paired_block}"
        "Follow steps (a)-(d) from your instructions against this source. Attempt the smallest "
        "counterexample. Return the JSON verdict only."
    )
    call = _call_step(
        agent,
        manifest=manifest,
        task=task,
        source_context=source_context,
        step="challenge",
        prompt=prompt,
        system_prompt=_CHALLENGE_SYSTEM,
        max_output_tokens=_CHALLENGE_MAX_TOKENS,
        output_mode="json_object",
        budget=budget,
        # The independent challenge is deliberately cheap: the claim plus a window around the lines
        # it cites (plus the paired operation's source, when a round-trip/pairing claim structurally
        # resolves one), never the whole file. It is a falsification pass on one causal chain, and a
        # chain that needs the whole file to evaluate is not a chain this step can settle anyway.
        context_source=CONTEXT_CITED_WINDOW,
        justification="an unproven candidate gets one independent source challenge before it is shown",
    )
    if call.attempted:
        usages.append(dict(call.usage or {}))
    if not call.attempted:
        return FindingChallenge(
            verdict="uncertain",
            reason=call.error or "the audit's call budget refused this challenge before it reached the model",
            attempted=False,
            error_kind="budget_exhausted",
        )
    if call.error:
        return FindingChallenge(
            verdict="uncertain",
            reason=call.error,
            attempted=True,
            error_kind="provider_failure",
        )
    if not call.text:
        return FindingChallenge(
            verdict="uncertain",
            reason="the source challenge returned no usable content",
            attempted=True,
            error_kind="parse_failed",
        )
    candidate = _first_json_object(call.text)
    if not isinstance(candidate, dict):
        return FindingChallenge(
            verdict="uncertain",
            reason="the source challenge did not return the required JSON object",
            error_kind="parse_failed",
        )
    verdict = str(candidate.get("verdict") or "").strip().lower()
    if verdict not in {"supported", "refuted", "uncertain"}:
        verdict = "uncertain"
    reason = str(candidate.get("reason") or "").strip()[:600]
    counterexample = str(candidate.get("counterexample") or "").strip()[:600]
    if verdict == "supported" and not reason:
        return FindingChallenge(
            verdict="uncertain",
            reason="the source challenge asserted support without explaining the causal chain",
        )
    # Symmetric with the `supported` gate above. Without it, a `refuted` verdict could kill a real
    # finding on a one-word reason and nothing would catch it -- unlike `supported`, which is caught
    # right above. `ExecutedClaim.counterexample()` (audit_claim_execution.py) already ties a
    # counterexample to a refutation for the deterministic execution path; this applies the same
    # convention to a model-asserted refutation, since a refutation with no counterexample and no
    # explanation is exactly the shallow, ready-made "kill it" a weak model can produce when handed
    # a wrong or irrelevant sibling (or simply reasons shallowly) under a confident label.
    if verdict == "refuted" and not (reason and counterexample):
        return FindingChallenge(
            verdict="uncertain",
            reason=(
                "the source challenge asserted refutation without a concrete counterexample "
                "explaining the causal chain"
            ),
        )
    return FindingChallenge(
        verdict=verdict,
        reason=reason or "the source check did not establish the causal claim",
        counterexample=counterexample,
    )


# What a defect is measured AGAINST: the project's stated guarantees, and the tests that are
# supposed to enforce them. Bounded hard — this rides on every nomination call, and the whole point
# of the stepped lane is that no single call gets an unbounded pile.
_CONTRACT_CHARS = 2200
_TEST_CHARS = 2200
_IMPORT_SIGNATURE_CHARS = 1800
# A module-level constant: `ZSTD_MAGIC = b'...'`. An import can name one of these as
# readily as a function, so a signature list that omits them still leaves the model
# unable to confirm the name exists.
_MODULE_CONSTANT_RE = re.compile(r"^[A-Z_][A-Z0-9_]*\s*[:=]")
_CONTRACT_NAMES = ("agents.md", "claude.md", "contributing.md", "architecture.md", "readme.md")


def audit_coverage_note(evidence: Any) -> str:
    """What this pass opened, out of what was there — in one sentence the operator can check.

    Measured live 2026-08-03 against a 333-crate Rust workspace: 493 source files present, 193
    opened, one finding reported, and no number stated anywhere. The operator asked "why? i asked
    you to audit all project?" and re-ran the same audit three more times, each returning the same
    single candidate, because nothing in the reply told them what had and had not been looked at.

    Reachable is not read, and read is not read-to-the-end: `incomplete_files` are the ones the
    excerpt budget cut short, and a citation past the end of one of those is a gap in OUR evidence
    rather than a fabrication by the model.
    """

    from core.agent_runtime.source_audit import is_source_path

    sources = dict(getattr(evidence, "sources", None) or {})
    all_paths = tuple(getattr(evidence, "all_paths", None) or ())
    incomplete = tuple(getattr(evidence, "incomplete_files", None) or ())
    read_count = len(sources)
    if not read_count:
        return ""
    available = len([path for path in all_paths if is_source_path(path)]) or len(all_paths)
    if available and available > read_count:
        note = f"Opened {read_count} of {available} source files in this pass"
    else:
        note = f"Opened {read_count} source file{'' if read_count == 1 else 's'} in this pass"
    if incomplete:
        note += f"; {len(incomplete)} of them only in part"
    return note + "."


def _supporting_context(evidence: Any, target: str) -> str:
    """The project contract and the target's tests, as the nomination prompt sees them.

    The audit already READ these — `run_workspace_audit` puts them in `evidence.sources` — and the
    nomination call was handed the target excerpt alone. Measured live 2026-08-01: `AGENTS.md` and
    `tests/test_engines_run.py` sat unread in scope while the audit answered from the target plus
    README, and a competitor on the same model landed a correct report by citing exactly those two.
    """
    from core.agent_runtime.workspace_audit import is_test_path

    sources = dict(getattr(evidence, "sources", {}) or {})
    contract = ""
    for path, body in sources.items():
        if path == target or not body:
            continue
        if path.replace("\\", "/").rsplit("/", 1)[-1].lower() in _CONTRACT_NAMES:
            contract = f"Project contract — `{path}`:\n{str(body)[:_CONTRACT_CHARS]}\n\n"
            break
    tests = ""
    for path, body in sources.items():
        if path == target or not body or not is_test_path(str(path).replace("\\", "/")):
            continue
        tests = f"Existing tests — `{path}`:\n{str(body)[:_TEST_CHARS]}\n\n"
        break
    return contract + tests + _imported_module_signatures(sources, target)


def _imported_module_signatures(sources: dict, target: str) -> str:
    """What the target's own imports actually define, so the model can check them.

    Collecting the imported module was only half the fix. Measured live 2026-08-03: after the
    collector began READING `api/liquefy_primitives.py`, its content still never reached this
    prompt — `_supporting_context` surfaced the project contract and the tests and nothing else. The
    proof was `zigzag_enc`, which that module defines but the target does not import: it was absent
    from the prompt, while the four names on the target's own import line were present. The model
    was seeing the import STATEMENT, not the module.

    Signatures only, not bodies. The question a nomination needs answered is "does this exist, and
    what does it take" — a full second file would crowd out the target excerpt for no gain.
    """

    from core.agent_runtime.workspace_audit import _imported_local_modules

    body = str(sources.get(target) or "")
    if not body:
        return ""
    stems = set(_imported_local_modules(body))
    if not stems:
        return ""

    blocks: list[str] = []
    for path, source in sources.items():
        clean = str(path).replace("\\", "/")
        if clean == target or not source:
            continue
        if clean.rsplit("/", 1)[-1].rsplit(".", 1)[0] not in stems:
            continue
        signatures = [
            line.rstrip()
            for line in str(source).splitlines()
            if line.startswith(("def ", "class ")) or _MODULE_CONSTANT_RE.match(line)
        ]
        if not signatures:
            continue
        rendered = "\n".join(signatures)[:_IMPORT_SIGNATURE_CHARS]
        blocks.append(f"Imported by the target — `{clean}` defines:\n{rendered}\n\n")
    return "".join(blocks[:4])


def _source_window(evidence: Any, path: str, start: int, end: int, *, pad: int = 20) -> str:
    """The numbered source around a citation — what a correction actually needs to see.

    A citation PAST the end of the file is the commonest rejection there is, and the window for it
    would be empty. It is clamped to the file's tail instead: a model that invented line 65 of a
    24-line file needs to see where the file actually ends, and that is the cheapest possible thing
    to send it.
    """
    body = str(evidence.sources.get(path, "") or "")
    lines = body.splitlines()
    if not lines:
        return ""
    span = max(1, int(end or start or 1) - int(start or 1)) + 2 * pad
    first = max(1, min(int(start or 1) - pad, len(lines) - span))
    last = min(len(lines), max(first + 1, int(end or start or 1) + pad))
    return "\n".join(f"{number:>6}: {lines[number - 1]}" for number in range(first, last + 1))


def _correction_prompt(
    *,
    evidence: Any,
    target: str,
    candidate: dict[str, Any],
    defect: str,
    total_lines: int,
    effective_input: str,
) -> str:
    """A rejection plus the source it is about — not the file again.

    The whole-file resend on every correction is the single largest line item in the 28,741-token
    incident. A correction is a narrow request: here is what you said, here is why it does not hold
    against the source, here is that source. Nothing about that needs the other 190 lines.
    """
    with suppress(Exception):
        start = int(candidate.get("line_start") or 0)
        end = int(candidate.get("line_end") or start)
        window = _source_window(evidence, target, start, end)
        if window:
            return (
                f"The operator asked: {str(effective_input).strip()[:300]}\n\n"
                f"You nominated this bug in `{target}` ({total_lines} lines total):\n"
                f"  title: {str(candidate.get('title') or '')[:200]}\n"
                f"  location: {start}-{end}\n"
                f"  scenario: {str(candidate.get('failure_scenario') or '')[:400]}\n\n"
                f"It was REJECTED: {defect}\n\n"
                f"The source around the lines you cited (real line numbers):\n{window}\n\n"
                "Reply with the corrected JSON object only. Copy line numbers from the prefixes."
            )
    return (
        f"Your previous answer was rejected: {defect}. `{target}` has {total_lines} lines. "
        "Reply with the corrected JSON object only."
    )


def _nominate_finding(
    agent: Any,
    *,
    manifest: Any,
    task: Any,
    source_context: dict[str, Any],
    evidence: Any,
    effective_input: str,
    budget: SteppedAuditBudget,
    usages: list[dict[str, Any]],
    banned_keys: tuple[frozenset[str], ...] = (),
    screened_keys: tuple[frozenset[str], ...] = (),
    refuted_titles: tuple[str, ...] = (),
    screened_titles: tuple[str, ...] = (),
    already_found_titles: tuple[str, ...] = (),
) -> tuple[SteppedFinding | None, str, SteppedCallResult | None, bool, str, list[dict[str, Any]]]:
    """(finding, resolved path, last call, terminal, failure note, survey rows).

    ``terminal`` means "do not try another manifest": the model produced VALID JSON twice and both
    citations were refuted by the evidence — a different model will not fix a claim the evidence
    contradicts. A SHAPE failure (empty reply, non-JSON, provider error) is the opposite: driven
    live 2026-08-01, the free cloud reasoning model completed a 91s call whose content was unusable
    under forced-JSON while the fitting local model further down the ranking would have answered —
    so shape failures retry once as plain text (JSON by instruction) and then move on.
    """
    from core.agent_runtime.audit_claim_verifier import _resolve_cited_path
    from core.agent_runtime.workspace_audit import _numbered_excerpt
    from core.model_output_contracts import validate_contract

    batch_rows: list[dict[str, Any]] = []
    target = _target_path_for(evidence, effective_input)
    body = evidence.sources.get(target, "")
    if not target or not body:
        return None, "", None, False, "no_readable_target", []
    excerpt, shown, total = _numbered_excerpt(body, limit_chars=_EXCERPT_CHARS)
    base_prompt = (
        f"The operator asked: {effective_input.strip()[:600]}\n\n"
        f"File `{target}` ({'all' if shown == total else f'first {shown} of'} {total} lines), each "
        f"line prefixed with its REAL number:\n\n{excerpt}\n\n"
        f"{_supporting_context(evidence, target)}"
        "List what is wrong with THIS file, most important first, and what it does well. Copy line "
        "numbers from the "
        "prefixes; never estimate them. A defect is a broken PROMISE: if the project contract or "
        "the existing tests above state a guarantee this file violates, that is the finding."
    )
    if refuted_titles:
        # Rule 4: a claim already disproved by execution is not available again, and the model is
        # told WHY rather than merely being rejected — a bare rejection produces a reworded version
        # of the same claim, which is exactly what the ban keys catch on the next pass.
        base_prompt += (
            "\n\nThese claims have ALREADY been tested this session and the test PASSED, which "
            "disproves them. Do not name any of them again, in any wording:\n"
            + "\n".join(f"- {title}" for title in refuted_titles[:6])
        )
    if screened_titles:
        base_prompt += (
            "\n\nThese claims already failed an adversarial check against the cited source. Do "
            "not rename or repeat them; nominate a different causal mechanism:\n"
            + "\n".join(f"- {title}" for title in screened_titles[:6])
        )
    if already_found_titles:
        # No count cap here (unlike `refuted_titles[:6]` / `screened_titles[:6]` above): these are
        # short strings -- `run_stepped_audit` truncates each title to 200 chars with `[:200]`
        # at the CALL SITE where `already_found_titles` is built from `survey_rows`, before it
        # ever reaches this function, and `_MAX_CANDIDATES` bounds how many accumulate per
        # attempt -- so even a worst-case accumulation across every attempt this turn costs a
        # few thousand prompt tokens, nowhere near context-window pressure. A `[:10]` slice here
        # was an unexamined defensive guess (CLAUDE.md 4b: a ceiling below what a step needs
        # costs everything), and it froze on the first 10 titles forever once a single batch
        # passed that count -- defeating the do-not-repeat instruction in exactly the
        # multi-attempt scenario it exists for.
        base_prompt += (
            "\n\nThese defects have ALREADY been found this turn and will already be reported. "
            "Do not repeat any of them, including a differently-worded restatement of the same "
            "underlying mechanism; nominate something NEW:\n"
            + "\n".join(f"- {title}" for title in already_found_titles)
        )
    banned = tuple(banned_keys or ())
    screened = tuple(screened_keys or ())
    last_call: SteppedCallResult | None = None
    prompt = base_prompt
    output_mode = "json_object"
    evidence_defects = 0
    shape_failures = 0
    fail_note = "uncheckable"
    context_source = CONTEXT_FULL_EXCERPT
    justification = ""
    # Two shape failures are enough to establish that this provider did not produce the required
    # artifact. Evidence rejections get one final correction attempt: the provider did answer in
    # the right shape, and the runtime can give it a precise source-truth rejection to act on.
    #
    # `_MAX_NOMINATION_ATTEMPTS` is per manifest; the LEDGER bounds the turn. Those used to be the
    # same budget composed twice — three attempts per manifest across four manifests across three
    # candidates — which is how one file audit reached nine calls.
    for _attempt in range(_MAX_NOMINATION_ATTEMPTS):
        if not budget.may_call("nominate"):
            # The budget stopping the loop must not overwrite WHY the loop was still going. The
            # last rejection is what the operator needs; that the ceiling then ended the search is
            # recorded on the ledger's refusal list, which is where a budget fact belongs.
            if fail_note == "uncheckable":
                fail_note = "nomination_budget_spent"
            break
        call = _call_step(
            agent,
            manifest=manifest,
            task=task,
            source_context=source_context,
            step="nominate",
            prompt=prompt,
            system_prompt=_NOMINATE_SYSTEM,
            max_output_tokens=_NOMINATE_MAX_TOKENS,
            output_mode=output_mode,
            budget=budget,
            context_source=context_source,
            justification=justification,
        )
        last_call = call
        # EVERY call is recorded, including one that reported nothing. `if call.usage` dropped
        # the empty blocks, which is precisely how a partial sum came to be presented as the total
        # (rule 10): a call that never reported is invisible to a filter and visible to a count.
        if call.attempted:
            usages.append(dict(call.usage or {}))
        if call.error:
            shape_failures += 1
            fail_note = call.error[:80]
            if _RESOURCE_GATE_RE.search(call.error) or _CALL_TIMEOUT_RE.search(call.error):
                # The resource governor refused to load this model. That is a fact about the box,
                # not a transient provider hiccup, so re-asking the SAME manifest cannot succeed —
                # measured live: two identical `model_load_gated_low_memory` attempts before the
                # driver moved on. Abandon this manifest immediately and let the caller try the
                # next one.
                break
            # A provider error may be transient (rate limit) — the second attempt drops the
            # provider-native JSON contract, which is itself a known failure input on reasoning
            # cloud models. Re-sending the whole excerpt is justified here and only here: the call
            # produced no artifact at all, so there is nothing narrower to correct.
            output_mode = "plain_text"
            context_source = CONTEXT_FULL_EXCERPT
            justification = f"the previous nomination call failed before producing an artifact ({fail_note})"
            if shape_failures >= 2:
                break
            continue
        if not call.text:
            shape_failures += 1
            fail_note = "empty_reply"
            output_mode = "plain_text"
            context_source = CONTEXT_FULL_EXCERPT
            justification = "the previous nomination call returned no content"
            prompt = base_prompt + "\n\nYour previous reply was empty. Reply with the JSON object only."
            if shape_failures >= 2:
                break
            continue
        validation = validate_contract("json_object", call.text)
        candidate = validation.structured_output if validation.ok else None
        if not isinstance(candidate, dict):
            candidate = _first_json_object(call.text)
        # A batched reply is `{"findings": [ ... ]}`. The first item is the candidate this pass
        # carries forward (the model was told to order them most important first); the rest are
        # survey rows. A model that still answers with a bare object is accepted unchanged, so a
        # weaker model that ignores the list shape is not punished for it.
        if isinstance(candidate, dict) and isinstance(candidate.get("findings"), list):
            batch = [item for item in candidate["findings"] if isinstance(item, dict)]
            candidate = batch[0] if batch else None
            batch_rows = batch[1:]
        else:
            # `candidate` here is either None, or a bare dict with no "findings" key. The second
            # case is ambiguous: it is the documented "model answered with one bare object"
            # shape (kept unchanged below), OR it is `_first_json_object` having recovered only
            # the first item out of a truncated `"findings": [...]` array (see
            # `_recover_findings_array`'s docstring). Try the array recovery on the raw text; it
            # returns [] when there never was a "findings" key, which leaves `candidate` exactly
            # as `_first_json_object` produced it -- so a genuinely bare single-object reply is
            # still "accepted unchanged" per the comment above.
            recovered = _recover_findings_array(call.text)
            if recovered:
                candidate = recovered[0]
                batch_rows = recovered[1:]
        if not isinstance(candidate, dict):
            shape_failures += 1
            fail_note = "not_json"
            output_mode = "plain_text"
            context_source = CONTEXT_FULL_EXCERPT
            justification = "the previous nomination reply was not the required JSON object"
            prompt = base_prompt + "\n\nYour previous reply was not a JSON object. Reply with the JSON object only."
            if shape_failures >= 2:
                break
            continue
        defect = _finding_defect(candidate, evidence, target)
        if not defect and (banned or screened):
            from core.agent_runtime.continuity_gate import claim_signature, matches_any_claim

            # By SIGNATURE, not by exact key. Told only "do not name that again", a weak model
            # renames it: "Empty input crashes at startswith" came back as "Startswith crashes
            # while decompressing an empty input". Same allegation, different string, and an exact
            # key waves it through — so the comparison is semantic containment.
            signature = claim_signature(
                str(candidate.get("title") or ""), str(candidate.get("failure_scenario") or "")
            )
            # The two ban sources are DIFFERENT facts and the operator reads this sentence. A live
            # read-only turn that ran no test at all reported "already disproved this session by a
            # proof test that passed", because a claim screened out by the source challenge went
            # into the same bucket as one an execution actually disproved.
            if matches_any_claim(signature, banned):
                defect = (
                    "that claim was already disproved this session by a proof test that passed; "
                    "rewording it does not make it a different claim — name a different mechanism"
                )
            elif matches_any_claim(signature, screened):
                defect = (
                    "that claim already failed an adversarial check against the cited source this "
                    "session; rewording it does not make it a different claim — name a different "
                    "mechanism"
                )
        if defect:
            evidence_defects += 1
            fail_note = f"evidence_rejected:{defect[:240]}"
            output_mode = "json_object"
            if call.ledger_row is not None:
                call.ledger_row.result = f"rejected:{defect[:120]}"
            # THE CORRECTION DOES NOT RE-SEND THE FILE. The model already produced a located,
            # well-formed answer; what it needs is the rejection and the source around the lines it
            # named. Measured on the incident's shape, the old correction cost the same ~3,000
            # tokens as the primary call and did so up to twice per candidate per manifest. A
            # window costs a tenth of that and carries strictly more of what the correction is
            # about.
            context_source = CONTEXT_CITED_WINDOW
            justification = f"the previous nomination was rejected against the source ({defect[:120]})"
            prompt = _correction_prompt(
                evidence=evidence,
                target=target,
                candidate=candidate,
                defect=defect,
                total_lines=total,
                effective_input=effective_input,
            )
            continue
        resolved = _resolve_cited_path(str(candidate["file"]), evidence) or target
        canonical_start, canonical_end = _canonical_finding_range(candidate)
        canonical_line = evidence.line_at(resolved, canonical_start).strip()
        return (
            SteppedFinding(
                title=str(candidate["title"]).strip()[:200],
                file=resolved,
                line_start=canonical_start,
                line_end=canonical_end,
                cited_line_text=canonical_line[:300],
                failure_scenario=str(candidate["failure_scenario"]).strip()[:900],
                harm_class=str(candidate.get("harm_class") or "").strip().lower(),
                suggested_fix=str(candidate.get("suggested_fix") or "").strip()[:400],
            ),
            resolved,
            last_call,
            False,
            "",
            batch_rows,
        )
    return None, target, last_call, evidence_defects >= _MAX_NOMINATION_ATTEMPTS, fail_note, batch_rows


_TEST_SYSTEM = (
    "You are writing ONE Python unittest file. Answer with the file's raw content only — no code "
    "fences, no prose, no reasoning narration."
)


def _prove_finding(
    agent: Any,
    *,
    manifest: Any,
    task: Any,
    source_context: dict[str, Any],
    session_id: str,
    evidence: Any,
    finding: SteppedFinding,
    budget: SteppedAuditBudget,
    usages: list[dict[str, Any]],
    policy: Any,
    banned_signatures: tuple[frozenset[str], ...] = (),
) -> SteppedProof:
    """Write the smallest failing test for the finding into a per-request scratch dir and run it.

    The FIRST thing this does is check the turn's execution policy, because the incident is that
    this function ran at all. An audit the operator asked to be read-only reached here, created a
    directory, wrote a file into their workspace and executed it. No gate refused it; there was no
    gate. `policy.proof_authorized` is that gate, and it is checked before the model is asked to
    write anything — a refused proof must not even spend a call.
    """
    from core.agent_runtime.builder.app_builder import (
        proof_holds,
        strip_code_fences,
        unittest_failure_counts,
        unittest_verdict_present,
    )
    from core.agent_runtime.builder.scaffolds import _unrooted_build_dir
    from core.agent_runtime.continuity_gate import (
        ArtifactVerdict,
        check_proof_artifact,
        contract_from_finding,
    )

    proof = SteppedProof()
    if not getattr(policy, "proof_authorized", False):
        proof.unauthorized = True
        proof.blocked_reason = (
            "this turn was not authorized to write or run anything, so no proof test was created "
            "and no command was executed"
        )
        return proof
    workspace_root = str(evidence.workspace_root or "").strip()
    if not workspace_root:
        proof.blocked_reason = "the audit evidence carries no workspace root"
        return proof

    # Keyed on the BUG identity, not the raw prompt, so a retry of the same finding overwrites its
    # own folder instead of littering generated/ (per-attempt prompt text varies; the bug does not).
    target_rel = _unrooted_build_dir(f"prove {finding.file} {finding.title}")
    stem = posixpath.basename(finding.file).rsplit(".", 1)[0] or "subject"
    test_name = f"test_{stem}_bug.py"
    test_path = posixpath.join(target_rel, test_name)
    subject_abs = finding.file if posixpath.isabs(finding.file) else posixpath.join(workspace_root, finding.file)
    subject_dir = posixpath.dirname(subject_abs)

    # The subject can sit anywhere in the audited tree, and its own imports resolve from ANY of its
    # ancestor directories — driven live 2026-08-01: `api/apache/liquefy_apache_repetition_v1.py`
    # does `from common_zstd import make_cctx`, and `common_zstd.py` lives in `api/`, the PARENT of
    # the subject's own directory, so a preamble carrying only the workspace root and the file's dir
    # errored on import and an errored test proves nothing. Every ancestor goes on sys.path. The
    # preamble is COMPOSED HERE with real absolute paths and the subject loaded by file location,
    # so the test never depends on package __init__ files or cwd luck.
    ancestor_dirs: list[str] = [workspace_root]
    walk = workspace_root
    for part in [p for p in posixpath.dirname(finding.file).replace("\\", "/").split("/") if p]:
        walk = posixpath.join(walk, part)
        ancestor_dirs.append(walk)
    if subject_dir not in ancestor_dirs:
        ancestor_dirs.append(subject_dir)
    path_lines = "".join(f"sys.path.insert(0, {d!r})\n" for d in ancestor_dirs)
    preamble = (
        "import importlib.util\n"
        "import sys\n"
        "import unittest\n"
        f"{path_lines}"
        f"_spec = importlib.util.spec_from_file_location({stem!r}, {subject_abs!r})\n"
        f"{stem} = importlib.util.module_from_spec(_spec)\n"
        f"_spec.loader.exec_module({stem})\n"
    )
    excerpt = evidence.sources.get(finding.file, "")[:_EXCERPT_CHARS]
    prompt = (
        f"Bug to reproduce, in `{finding.file}` lines {finding.line_start}-{finding.line_end} "
        f"({finding.title}): {finding.failure_scenario}\n\n"
        f"The file's source:\n\n{excerpt}\n\n"
        f"Write ONE unittest file that REPRODUCES this bug: the test must FAIL on the current code "
        f"with an assertion about the buggy behaviour, and would pass once the bug is fixed. "
        f"Start the file with EXACTLY this preamble (already correct, do not alter it):\n\n"
        f"{preamble}\n"
        f"Access the subject module as `{stem}`. One or two test methods, no more. "
        f"End with:\n\nif __name__ == '__main__':\n    unittest.main()\n"
    )
    # The task contract this artifact must satisfy. Built from the finding the operator was shown,
    # so "prove it" is bound to the claim they were shown rather than to whatever the test-writing
    # call decides to be about. See `continuity_gate`.
    contract = contract_from_finding(
        action="reproduce",
        title=finding.title,
        file=finding.file,
        cited_line_text=finding.cited_line_text,
        line_start=finding.line_start,
        line_end=finding.line_end,
        failure_scenario=finding.failure_scenario,
        production_modification_allowed=False,
        banned_signatures=tuple(banned_signatures or ()),
    )

    content = ""
    last_error = ""
    verdict = ArtifactVerdict(accepted=False, reason_code="not_generated", detail="")
    # Two attempts, and BOTH failure modes consume them: a transient provider miss (driven live
    # 2026-08-01, the free cloud lane rate-limited the prove call) and an artifact that fails its
    # contract. The rejection is fed back as a correction so the second attempt is a repair rather
    # than a re-roll of the same mistake.
    attempt_prompt = prompt
    attempt_context = CONTEXT_FULL_EXCERPT
    attempt_reason = "the operator authorized a proof, so one reproduction artifact is generated"
    for _attempt in range(2):
        if not budget.may_call("prove"):
            last_error = "the audit's bounded call budget was spent before a proof artifact existed"
            break
        call = _call_step(
            agent,
            manifest=manifest,
            task=task,
            source_context=source_context,
            step="prove",
            prompt=attempt_prompt,
            system_prompt=_TEST_SYSTEM,
            max_output_tokens=_TEST_MAX_TOKENS,
            output_mode="plain_text",
            budget=budget,
            context_source=attempt_context,
            justification=attempt_reason,
        )
        # EVERY call is recorded, including one that reported nothing. `if call.usage` dropped
        # the empty blocks, which is precisely how a partial sum came to be presented as the total
        # (rule 10): a call that never reported is invisible to a filter and visible to a count.
        if call.attempted:
            usages.append(dict(call.usage or {}))
        content = strip_code_fences(call.text or "")
        last_error = call.error
        if not content.strip():
            continue
        if "importlib.util.spec_from_file_location" not in content:
            # The loader preamble is what makes the test able to import its subject at all; a test
            # without it errors on import and an errored test proves nothing. Prepend, don't argue.
            content = preamble + "\n" + content
        # THE GATE. Before the directory, before the file, before the command: does this artifact
        # actually exercise the claim it is supposed to prove, and does it assert the correct
        # behaviour so that failing means reproduced? The incident wrote and ran an artifact that
        # did neither, and scored its exit status as a verdict about the audited code.
        verdict = check_proof_artifact(content, contract=contract)
        check_row = verdict.as_dict()
        # The artifact is judged against the finding this turn was BOUND to, and the row says which
        # one. Without the id on the row, a receipt showing "artifact accepted" cannot be checked
        # against the claim it was accepted for.
        check_row["finding_id"] = str(
            (source_context or {}).get("active_finding_id") or ""
        )
        proof.artifact_checks.append(check_row)
        if call.ledger_row is not None and not verdict.accepted:
            call.ledger_row.result = f"rejected:{verdict.reason_code}"
        if verdict.accepted:
            break
        attempt_prompt = prompt + "\n\n" + verdict.correction_sentence()
        attempt_reason = (
            f"the generated artifact failed the continuity gate ({verdict.reason_code}), so one "
            "correction was requested"
        )
    if not content.strip():
        # Wording matters: the post-hoc claim verifier reads the FINAL report, and the phrase
        # "produced no test" matches its no-tests-in-this-project pattern — the runtime's own
        # honest sentence was flagged as a contradicted model claim on a live run.
        proof.blocked_reason = (
            f"the test-writing step returned nothing usable ({last_error or 'empty response'})"
        )
        return proof
    if not verdict.accepted:
        # Nothing is written and nothing runs. The candidate stands as a candidate: an artifact
        # that could not be made to test the claim says nothing about whether the claim is true.
        proof.contract_rejected = True
        proof.blocked_reason = (
            "the generated test did not test the claimed failure, so it was not written or run — "
            f"{verdict.detail.rstrip('.')}"
        )
        return proof

    def _run_tool(
        intent: str,
        arguments: dict[str, Any],
        *,
        trusted_local_only: bool = False,
        context_override: dict[str, Any] | None = None,
    ) -> Any:
        # `context_override` is how an isolated-external proof runs its writes and its command
        # against a fresh temp directory instead of the audited repo: it carries the SAME
        # `audit_execution_policy` (so the tool-boundary check still sees the real, granted policy)
        # but a `workspace` pointed at the temp root, so `resolve_workspace_path` confines this ONE
        # call's `path`/`cwd` to that temp root — the repository is never in scope for the call at
        # all, rather than being in scope and merely un-written-to.
        result = agent._execute_tool_intent(
            {"intent": intent, "arguments": dict(arguments)},
            task_id=getattr(task, "task_id", ""),
            session_id=session_id,
            source_context=context_override if context_override is not None else source_context,
            hive_activity_tracker=getattr(agent, "hive_activity_tracker", None),
            public_hive_bridge=getattr(agent, "public_hive_bridge", None),
            trusted_local_only=trusted_local_only,
        )
        # A receipt the completion-claim honesty gate can read. The durable receipt store only
        # writes under a runtime checkpoint, which this lane does not create — without this the
        # gate judged "Test written / the test ran" against zero receipts and replaced the whole
        # report (measured live 2026-08-01). Executed ≠ succeeded: a FAILING proof run (the whole
        # point of this lane) has ok=False and a real returncode — it ran, and the receipt says so.
        details = dict(getattr(result, "details", None) or {}) if result is not None else {}
        executed = result is not None and (
            bool(getattr(result, "ok", False)) or isinstance(details.get("returncode"), (int, float))
        )
        if executed:
            execution_receipt = {
                "executed": True,
                "ok": bool(getattr(result, "ok", False)),
                "status": str(getattr(result, "status", "") or "executed"),
            }
            with suppress(Exception):
                receipts = source_context.setdefault("tool_receipts", [])
                receipts.append(
                    {
                        "tool_name": intent,
                        "receipt_id": f"stepped-audit-{intent}-{len(receipts)}",
                        "status": "executed",
                        "execution": dict(execution_receipt),
                    }
                )
            # ALSO durable, keyed by session: the API layer runs a SECOND honesty pass on a context
            # dict that never sees run_once's inner copy (measured live 2026-08-01, drive 7 — the
            # first pass accepted the report and the second rewrote it to "I did not create those
            # files"). The session-scoped receipt store is the channel both passes read.
            with suppress(Exception):
                import uuid as _uuid

                from core.runtime_continuity import store_tool_receipt

                store_tool_receipt(
                    receipt_key=f"stepped-audit-{_uuid.uuid4().hex}",
                    session_id=str(session_id or ""),
                    checkpoint_id="stepped-audit",
                    tool_name=intent,
                    idempotency_key="",
                    arguments=dict(arguments),
                    execution=dict(execution_receipt),
                )
        return result

    proof.attempted = True
    proof.write_scope = str(getattr(policy, "proof_write_scope", "repository_generated") or "repository_generated")

    isolated_ctx: dict[str, Any] | None = None
    isolated_temp_root = ""
    isolated_write_target = target_rel
    if proof.write_scope == "external_temp_root":
        import tempfile

        # A fresh OS temp directory, never inside the audited repository. The generated test file
        # still IMPORTS the real subject by its real absolute path (`subject_abs`, computed above
        # from the actual repo) — reads stay permitted by the sandbox's own Seatbelt profile
        # (`sandbox/job_runner.py:_macos_confined_profile`) except for a small secrets deny-list;
        # only WRITES and the command's `cwd` are confined, and confining them to this temp root is
        # exactly "isolated reproduction": the repository is never a writable or execution root for
        # this call at all.
        isolated_temp_root = tempfile.mkdtemp(prefix="vool-audit-proof-")
        proof.temp_root = isolated_temp_root
        isolated_ctx = dict(source_context)
        isolated_ctx["workspace"] = isolated_temp_root
        isolated_write_target = "proof"

        # ARGUS repair D1, 2026-08-06: the enforcement boundary (`audit_tool_permission_denial`)
        # no longer trusts the "external_temp_root" scope name alone — it requires a runtime-owned
        # binding it can independently verify. Registered ONLY here, ONLY once, immediately after
        # the temp root is created; released in the `finally` below, in the same place the temp
        # root itself gets removed, so a binding never outlives the directory it names.
        from core.agent_runtime.audit_policy import register_isolated_proof_root

        isolated_proof_run_id = register_isolated_proof_root(isolated_temp_root, workspace_root)
        isolated_ctx["_isolated_proof_run_id"] = isolated_proof_run_id

    ensure = _run_tool(
        "workspace.ensure_directory", {"path": isolated_write_target}, context_override=isolated_ctx
    )
    write_path = posixpath.join(isolated_write_target, test_name) if isolated_ctx is not None else test_path
    write = _run_tool(
        "workspace.write_file", {"path": write_path, "content": content}, context_override=isolated_ctx
    )
    if write is None or not bool(getattr(write, "ok", False)):
        status = str(getattr(write, "status", "") or "").strip()
        proof.blocked_reason = (
            f"writing the test was refused ({status or 'write not permitted in this mode'})"
        )
        del ensure
        if isolated_temp_root:
            with suppress(Exception):
                from core.agent_runtime.audit_policy import release_isolated_proof_root

                release_isolated_proof_root(isolated_proof_run_id)
            with suppress(Exception):
                import shutil

                shutil.rmtree(isolated_temp_root, ignore_errors=True)
        return proof
    proof.test_path = write_path if isolated_ctx is not None else test_path
    module = test_name[: -len(".py")]
    # `python3`, not a full interpreter path: the sandbox whitelists base commands by name and only
    # the `python|python3 -m unittest` shape earns heuristic-only isolation (kernel sandboxing can
    # break interpreter startup). Under the daemon, PATH resolves python3 to the runtime's venv.
    proof.test_command = f"python3 -m unittest -v {module}"
    # This subprocess imports the audited subject by file location (see the preamble above), and a
    # plain import writes `__pycache__/<stem>.pyc` next to that file -- inside the audited repository
    # -- which is a real mutation of a tree this lane promises to leave byte-identical. Suppression is
    # enforced in `sandbox/job_runner.py` on the CHILD's own environment copy, deliberately not here
    # on `os.environ`: mutating the daemon's own environment, even with a restore, leaks the flag to
    # every other thread's subprocess for the duration of this call.
    try:
        outcome = _run_tool(
            "sandbox.run_command",
            {"command": proof.test_command, "cwd": isolated_write_target},
            trusted_local_only=True,
            context_override=isolated_ctx,
        )
    finally:
        if isolated_temp_root:
            with suppress(Exception):
                from core.agent_runtime.audit_policy import release_isolated_proof_root

                release_isolated_proof_root(isolated_proof_run_id)
            with suppress(Exception):
                import shutil

                shutil.rmtree(isolated_temp_root, ignore_errors=True)
    details = dict(getattr(outcome, "details", None) or {})
    raw_code = details.get("returncode")
    proof.returncode = int(raw_code) if isinstance(raw_code, (int, float)) else None
    proof.output = str(getattr(outcome, "response_text", "") or "")
    # `unittest_runner=True` because this lane always runs `python3 -m unittest`. Without it, a
    # generated test that dies at import (SyntaxError, bad import, missing dependency) exits 1 with
    # no verdict line and scores as a PROOF — the generated test crashing on itself reported as a
    # reproduced bug in the audited code.
    proof.proven = proof_holds(
        output=proof.output, returncode=proof.returncode, unittest_runner=True
    )
    if proof.proven:
        proof.note = ""
    elif proof.returncode is None:
        proof.note = "did_not_run"
    elif proof.returncode == 124:
        proof.note = "timed_out"
    elif proof.returncode == 0:
        proof.note = "passed"
    elif not unittest_verdict_present(proof.output):
        # Exited non-zero without ever reporting a verdict: the test module did not import.
        proof.note = "errored"
    elif unittest_failure_counts(proof.output)[1] > 0:
        proof.note = "errored"
    else:
        proof.note = "passed"
    return proof


_SYNTHESIS_SYSTEM = (
    "You are writing the analysis section of an audit report whose facts are already established. "
    "Plain prose, at most 350 words. Cite the file and line range exactly as given. Do not invent "
    "line numbers, token counts, or test results; do not narrate your reasoning; do not ask to run "
    "tools. If the operator's request asked for anything else answerable from the facts given, "
    "answer it briefly."
)


def _synthesize(
    agent: Any,
    *,
    manifest: Any,
    task: Any,
    source_context: dict[str, Any],
    effective_input: str,
    finding: SteppedFinding,
    proof: SteppedProof,
    budget: SteppedAuditBudget,
    usages: list[dict[str, Any]],
) -> str:
    """The model's prose for the report, or "" when it produced nothing usable."""
    from core.agent_runtime.builder.app_builder import summarize_test_output
    from core.agent_runtime.response import suppress_internal_reasoning_leak
    from core.model_output_contracts import validate_contract
    from core.model_output_guard import claims_pending_tool

    proof_line = (
        f"proof test `{proof.test_path}` ran `{proof.test_command}` and exited "
        f"{proof.returncode} — {'the bug is REPRODUCED (test failed as required)' if proof.proven else f'the proof did NOT hold ({proof.note})'}; "
        f"output digest: {summarize_test_output(proof.output)[:800]}"
        if proof.attempted and not proof.blocked_reason
        else f"no proof test was run: {proof.blocked_reason or 'step skipped'}"
    )
    prompt = (
        f"The operator asked: {effective_input.strip()[:600]}\n\n"
        f"Established facts:\n"
        f"- Bug: {finding.title} at `{finding.file}:{finding.line_start}-{finding.line_end}`\n"
        f"- The cited line reads: {finding.cited_line_text!r}\n"
        f"- Failure scenario: {finding.failure_scenario}\n"
        f"- Proof: {proof_line}\n\n"
        "Write the analysis: why this is the highest-risk bug, the concrete failure scenario in "
        "your own words, and what a fix must guarantee."
    )
    call = _call_step(
        agent,
        manifest=manifest,
        task=task,
        source_context=source_context,
        step="synthesize",
        prompt=prompt,
        system_prompt=_SYNTHESIS_SYSTEM,
        max_output_tokens=_SYNTHESIS_MAX_TOKENS,
        output_mode="plain_text",
        budget=budget,
        context_source=CONTEXT_PROOF_RESULT,
        justification="a proof held, so one bounded prose pass explains the confirmed defect",
    )
    if call.attempted:
        usages.append(dict(call.usage or {}))
    validation = validate_contract("plain_text", call.text or "")
    prose = validation.normalized_text if validation.ok else ""
    if not prose.strip():
        return ""
    # `suppress_internal_reasoning_leak` returns None for a CLEAN answer (keep the original), an
    # extracted answer when a monologue carried a `final answer:` handoff, and a canned "no clean
    # answer" refusal when the whole reply was reasoning. The refusal is not analysis — drop it and
    # ship the report from the artifacts alone.
    replacement = suppress_internal_reasoning_leak(prose)
    final = prose if replacement is None else str(replacement).strip()
    if (
        not final
        or final.startswith("I couldn't produce a clean final answer")
        or claims_pending_tool(final)
    ):
        # A monologue or a "let me run the tests" here is exactly what the bounded call exists to
        # prevent from reaching the operator. The report ships from the artifacts without it.
        return ""
    return final.strip()


def _survey_findings(rows: list[dict[str, Any]], evidence: Any, target: str) -> list[Any]:
    """The batch's remaining rows, as verdict findings, with the same citation truth as the primary.

    Each row is put through `_finding_defect` — the citation must exist in the source that was
    actually read, and an invented line number is dropped rather than printed. A survey row is NOT
    challenged by a second model and NEVER executed, so it can only ever be reported as an
    observation; that is what keeps the honesty machine intact while the row count goes up.
    """

    from core.agent_runtime.audit_verdict import CONFIDENCE_UNREVIEWED, VerdictFinding

    out: list[Any] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _bounded_row_text(row.get("title"), 200)
        if not title or title.lower() in seen:
            continue
        resolved_row = dict(row)
        if _finding_defect(resolved_row, evidence, target, allow_observations=True):
            continue
        start, end = _canonical_finding_range(row)
        seen.add(title.lower())
        # Finding E point 2, 2026-08-04: harm_class used to be string-concatenated into the title
        # (`f"{title} [{harm}]"`) because `VerdictFinding` had no field for it. It is now a real
        # field, threaded straight through instead of mangling the display title.
        out.append(
            VerdictFinding(
                title=title,
                file=str(row.get("file") or target),
                line_start=start,
                line_end=end,
                # Same bound the primary finding's own citation gets (`canonical_line[:300]`) --
                # this field is inert today (no renderer reads it for a non-primary finding), but
                # leaving it unbounded would silently reopen this exact leak the day a "Cited line"
                # column is added to the additional-findings table.
                cited_line_text=_bounded_row_text(row.get("cited_line_text"), 300),
                # Bounded here too, independent of the upstream union-loop truncation -- this is
                # the operator-facing report-rendering boundary and gets its own guarantee rather
                # than relying solely on callers to have already truncated the row.
                failure_scenario=_bounded_row_text(row.get("failure_scenario"), 900),
                # `_finding_defect` writes the resolved (possibly downgraded) harm_class onto
                # whatever dict it is given. It must read from `resolved_row`, not `row` -- `row`
                # still holds the model's original, un-downgraded self-declaration.
                harm_class=str(resolved_row.get("harm_class") or "").strip().lower(),
                suggested_fix=str(row.get("suggested_fix") or "").strip()[:400],
                # Never challenged, never executed (see docstring) -- the weakest confidence tier.
                confidence=CONFIDENCE_UNREVIEWED,
            )
        )
    return out


def _verdict_finding(finding: SteppedFinding | None, *, confidence: str = "") -> Any:
    from core.agent_runtime.audit_verdict import CONFIDENCE_CHALLENGED, VerdictFinding

    if finding is None:
        return None
    return VerdictFinding(
        title=finding.title,
        file=finding.file,
        line_start=finding.line_start,
        line_end=finding.line_end,
        cited_line_text=finding.cited_line_text,
        failure_scenario=finding.failure_scenario,
        harm_class=finding.harm_class,
        suggested_fix=finding.suggested_fix,
        # Every caller of `_verdict_finding` (the primary finding and `extra_findings`) reaches
        # this only after surviving the adversarial source challenge, unless it was carried over
        # from an already-challenged prior turn -- so "challenged" is the correct default tier;
        # the PROVEN primary is retagged by the renderer itself (`CONFIDENCE_PROVEN`).
        confidence=confidence or CONFIDENCE_CHALLENGED,
    )


def _verdict_proof(proof: SteppedProof | None) -> Any:
    from core.agent_runtime.audit_verdict import VerdictProof
    from core.agent_runtime.builder.app_builder import summarize_test_output

    if proof is None or not proof.attempted:
        return None
    return VerdictProof(
        attempted=True,
        test_path=proof.test_path,
        test_command=proof.test_command,
        returncode=proof.returncode,
        output_digest=summarize_test_output(proof.output)[:900],
        note=proof.note,
    )


def _blocked_telemetry_line(
    *, elapsed_seconds: float | None, totals: dict[str, Any]
) -> str:
    """The runtime-owned telemetry `_blocked_decision` renders on a BLOCKED chat report.

    ARGUS repair D9, 2026-08-06: the runtime measures `elapsed_seconds` for every BLOCKED exit but
    never turned it into anything the chat-facing report actually rendered -- `render_audit_report`
    prints `verdict.usage_line` for EVERY terminal state including BLOCKED (see its own
    unconditional `if verdict.usage_line:` near the end), but `_blocked_decision` used to populate
    it only with a bare token-usage receipt, gated on `totals.get("calls")` — meaning the one field
    that IS always available on a BLOCKED turn (wall time) was the one this function never
    rendered. Mirrors the main path's own three-line composition (`_wall_time_line`/
    `_largest_context_line`/`usage_sentence` in `run_stepped_audit` below) without fabricating
    token counts for a turn that made zero provider calls: the usage receipt stays gated on
    `totals.get("calls")` exactly as before, and the context line names its own absence explicitly
    rather than printing a zero.
    """
    from core.token_usage_receipt import usage_receipt_line

    wall_time_line = f"Measured wall time: {elapsed_seconds:.1f}s" if elapsed_seconds is not None else ""
    context_line = (
        "Largest context component: not available (no provider call was made this turn)"
        if not totals.get("calls")
        else "Largest context component: not available (no context breakdown was recorded)"
    )
    return "\n".join(
        line
        for line in (usage_receipt_line(totals) if totals.get("calls") else "", wall_time_line, context_line)
        if line
    )


def _blocked_decision(
    *,
    reason: str,
    effective_input: str,
    session_id: str,
    target_path: str = "",
    model_label: str = "",
    refuted: list[dict[str, Any]] | None = None,
    usage_totals: dict[str, Any] | None = None,
    state: str = "",
    routing: Any = None,
    fixture_identity: dict[str, Any] | None = None,
    elapsed_seconds: float | None = None,
    source_context: dict[str, Any] | None = None,
    task_id: str = "",
) -> Any:
    """An honest bounded terminal result — the thing that used to be `return None`.

    Rule 7 exists because every `None` from this lane reopened the single-shot audit, which is the
    hallucination path the stepped lane was built to close. A dead end is now an ANSWER: it names
    the terminal state, names where the audit stopped, and asserts nothing about a bug.
    """
    from core.agent_runtime.audit_verdict import BLOCKED, AuditVerdict, render_audit_report
    from core.memory_first_router import ModelExecutionDecision
    from core.routing_decision_log import record_decision

    totals = dict(usage_totals or {})
    terminal = state or BLOCKED
    verdict = AuditVerdict(
        state=terminal,
        refuted=list(refuted or []),
        blocked_reason=reason,
        model_label=model_label,
        target_path=target_path,
        usage_line=_blocked_telemetry_line(elapsed_seconds=elapsed_seconds, totals=totals),
    )
    with suppress(Exception):
        record_decision(
            session_id=session_id,
            user_input=effective_input,
            family="workspace_audit_stepped",
            handled=True,
            arbiter=f"terminal:{terminal}",
        )
    blocked_detail = {
        "terminal_state": terminal,
        "target": target_path,
        "refuted": list(refuted or []),
        "blocked_reason": reason,
        "model_routing": routing.as_dict() if routing is not None else {},
        # SCALPEL final-tip verification, 2026-08-06: the durable record is the only trail an early
        # BLOCKED exit leaves -- it must carry the same fixture/checkout identity and measured
        # timing a proven/unproven candidate already gets, or an operator reading Activity later
        # cannot tell which source tree, which daemon build, or how long the refusal took.
        "fixture_identity": dict(fixture_identity or {}),
        "timing": {"elapsed_seconds": elapsed_seconds} if elapsed_seconds is not None else {},
        # ARGUS repair D5, 2026-08-06: `task_id` is the attempt identity `run_stepped_audit` was
        # itself given -- always available (it is a required parameter of the function), so there
        # is no reason a BLOCKED exit's own record should be the one place that omits it. Not a
        # second ID system: the SAME `task.task_id` the main-path `stepped_audit_detail` record
        # would carry if this turn had reached it.
        "task_id": str(task_id or ""),
    }
    # ARGUS repair D5, 2026-08-06: the prior version persisted this with a SYNTHETIC
    # `{"session_id": session_id}` context instead of the turn's real `source_context` -- durable
    # storage (`append_runtime_event`, keyed on `runtime_session_id`/`session_id`) still worked, but
    # `emit_runtime_event` also reads `cancel_turn_id` (tagged onto the event as `client_turn_id`)
    # and `runtime_event_stream_id` (looked up against the live per-stream sink registry) STRAIGHT
    # from `source_context` -- neither key exists on a synthetic one-field dict, so a BLOCKED event
    # never carried a client turn id and never reached a live-open Activity stream, even though it
    # reached durable storage. Passing the real `source_context` (already carrying `turn_id` from
    # `checkpoints.prepare_runtime_checkpoint`, `cancel_turn_id`, `runtime_event_stream_id`, and
    # `runtime_session_id` when this turn came through the normal chat/API entry point) is what
    # restores both without inventing any new identity field.
    _persist_audit_detail(
        {**dict(source_context or {}), "session_id": session_id, "runtime_session_id": session_id},
        blocked_detail,
    )
    return ModelExecutionDecision(
        source="stepped_audit",
        task_hash="",
        output_text=render_audit_report(verdict),
        confidence=0.9,
        used_model=bool(totals.get("calls")),
        validation_state="valid",
        details={
            "token_usage": totals,
            "stepped_audit": blocked_detail,
        },
    )


def _gate_blocked_decision(
    *,
    gate: Any,
    effective_input: str,
    session_id: str,
) -> Any:
    """The whole turn, when a referential follow-up had nothing to refer to.

    Deliberately NOT an audit report: there is no verdict, no candidate and no target, so a report
    shape would have to invent headings for state that does not exist. One composed sentence, no
    model call, no tool call — and `used_model=False`, so the receipt cannot later suggest that
    something was consulted.
    """
    from core.memory_first_router import ModelExecutionDecision
    from core.routing_decision_log import record_decision

    with suppress(Exception):
        record_decision(
            session_id=session_id,
            user_input=effective_input,
            family="workspace_audit_stepped",
            handled=True,
            arbiter=f"follow_up_blocked:{gate.reason_code}",
        )
    return ModelExecutionDecision(
        source="stepped_audit",
        task_hash="",
        output_text=gate.message,
        confidence=0.95,
        used_model=False,
        validation_state="valid",
        details={
            "stepped_audit": {
                "terminal_state": "follow_up_blocked",
                "follow_up_gate": gate.as_dict(),
                "model_calls": 0,
            }
        },
    )


def _emit_call_ledger(source_context: dict[str, Any], budget: SteppedAuditBudget) -> None:
    """Put the per-call trace where `core/turn_trace.py` can read it.

    The ledger already answered "which step was this, what was it given, why was another call
    necessary, and what came back" — and it lived only on `decision.details`, which nothing
    persists. So the debugger could show four calls to one model and nothing about what they were
    for; the step name is on the request metadata and was never emitted either. One event per call,
    on the ledger the trace already joins.
    """
    if budget.ledger is None:
        return
    with suppress(Exception):
        from core.runtime_task_events import emit_runtime_event

        for row in budget.ledger.as_rows():
            emit_runtime_event(
                source_context,
                event_type="audit_step",
                message=(
                    f"audit call {row['call']} — {row['purpose']} on "
                    f"{row['model_name'] or row['provider_id'] or 'unknown'}: {row['result']}"
                ),
                details=dict(row),
            )
        for refusal in budget.ledger.refusals:
            emit_runtime_event(
                source_context,
                event_type="audit_budget_refused",
                message=f"audit stopped: {refusal}",
                details={"reason": refusal, "calls": budget.ledger.count},
            )


def _redact_strings_recursive(value: Any) -> Any:
    """`redact_secrets` applied to every string reachable inside a JSON-shaped structure.

    The detail record carries free text from three sources this repair does not otherwise control:
    the model's own nomination/challenge/proof-generation replies, the operator's own turn text
    (via `failure_scenario`/titles echoing user-provided context), and real captured proof stdout —
    any of which could contain a credential the audited source itself embeds. `redact_secrets`
    (entropy/shape-aware, `core/secret_redaction.py`) is applied uniformly rather than guessing
    which specific keys might carry it.
    """
    from core.secret_redaction import redact_secrets as _redact

    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict):
        return {k: _redact_strings_recursive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_strings_recursive(v) for v in value]
    return value


def _persist_audit_detail(source_context: dict[str, Any], detail: dict[str, Any]) -> None:
    """Write the full structured audit record durably, not only onto the returned decision object.

    SCALPEL fix, failure class Activity/7, 2026-08-06: `details.stepped_audit` (candidate ids,
    fingerprints, lifecycle states, challenge verdicts/reasons, proof command/output, per-stage
    timing) previously existed ONLY on the in-memory `ModelExecutionDecision` — once the HTTP
    response was sent, none of it was recoverable. This writes the same record, redacted and
    size-bounded, to the durable runtime event store `_emit_call_ledger` already writes its own
    thin per-call slice to, so Activity/`core.turn_trace` can recover it after the fact.
    """
    import json

    with suppress(Exception):
        from core.runtime_task_events import emit_runtime_event

        redacted = _redact_strings_recursive(detail)
        encoded = json.dumps(redacted, default=str)
        if len(encoded.encode("utf-8")) > _ACTIVITY_DETAIL_MAX_BYTES:
            # Truncate the largest list fields first (candidate-by-candidate detail), never the
            # scalar fields (terminal state, policy, proof outcome) a reader needs to orient at
            # all — and say so explicitly rather than silently dropping rows.
            trimmed = dict(redacted)
            trimmed["detail_truncated"] = True
            for key in ("screened_out", "additional_findings", "refuted", "strengths"):
                rows = trimmed.get(key)
                if isinstance(rows, list) and rows:
                    trimmed[key] = rows[:3]
            encoded = json.dumps(trimmed, default=str)
            if len(encoded.encode("utf-8")) > _ACTIVITY_DETAIL_MAX_BYTES:
                # Still too large (e.g. one enormous proof output) -- cut the largest known single
                # string field down hard rather than lose the row-level structure entirely.
                proof = trimmed.get("proof")
                if isinstance(proof, dict) and proof.get("output"):
                    proof["output"] = str(proof["output"])[:500] + " …[truncated for size]"
                encoded = json.dumps(trimmed, default=str)
            redacted = json.loads(encoded)

        emit_runtime_event(
            source_context,
            event_type="audit_candidate_detail",
            message=(
                f"audit candidate detail — terminal:{redacted.get('terminal_state', '')} "
                f"target:{redacted.get('target', '')}"
            ),
            details=redacted,
        )


def _register_audit_reads(evidence: Any, session_id: str) -> None:
    """Register the audit's OWN reads as execution records.

    Driven live 2026-08-01: the inspection honesty gate was dormant on audit turns (nothing had
    execution records), the prove step's write/run receipts woke it, and the gate then judged the
    report against a record list with NO read of the audited file — the entire report was rewritten
    to "I did not actually open <file>". These files WERE read this turn, verbatim, by
    `run_workspace_audit`; the record is registration of real work, not an exemption.
    """
    with suppress(Exception):
        from core.runtime_flags import flag_enabled

        if not flag_enabled("execution_records"):
            return
        from core import execution_records

        for path in evidence.sources:
            execution_records.record(
                session_id=str(session_id or ""),
                intent="workspace.read_file",
                arguments={"path": str(path)},
                details={"resolved_target": str(path)},
                ok=True,
                status="executed",
            )


def _substitution_note(
    *,
    substituted: bool,
    prior_terminal: str,
    prior_titles: tuple[str, ...],
    finding: SteppedFinding | None,
) -> str:
    """The sentence a continuation owes the operator when it could not prove what they meant.

    Rule 3 of the proof contract: a different bug may be reported, but it must be presented AS a
    replacement. Composed here rather than by a model, so it cannot be omitted on the turns where
    it is least convenient.
    """
    from core.agent_runtime.audit_verdict import NO_FINDING, REFUTED

    if not substituted:
        return ""
    referent = f" (`{prior_titles[-1]}`)" if prior_titles else ""
    if prior_terminal == REFUTED:
        opening = (
            f"The finding you referred to{referent} was disproved by an executed test earlier in "
            "this session, so there was nothing left to prove."
        )
    elif prior_terminal in {NO_FINDING, ""}:
        opening = (
            "The earlier turn did not leave a finding to prove — it ended without a checkable "
            "candidate."
        )
    else:
        opening = (
            f"The earlier turn ended `{prior_terminal}` and left no candidate to prove."
        )
    if finding is None:
        return f"{opening} This turn found nothing to put in its place."
    return (
        f"{opening} What follows is a REPLACEMENT finding this turn nominated, not the one you "
        "asked about."
    )


def _active_finding_snapshot(scope: Any) -> dict[str, Any]:
    """The internal finding record, for receipts and tests. Never rendered to the operator."""
    from core.agent_runtime.active_finding import active_finding_for

    row = active_finding_for(scope)
    return row.as_dict() if row is not None else {}


def _register_active_finding(
    *,
    scope: Any,
    finding: SteppedFinding | None,
    terminal: str,
    target_path: str,
    evidence: Any,
    effective_input: str,
    closing_reason: str = "",
    refuted_titles: tuple[str, ...] = (),
) -> None:
    """Publish this turn's conclusion as the referent a later "prove it" resolves against.

    The audit keeps its own capsule (evidence, policy, model pin); this is the SHARED row, so a
    continuation is bound the same way whichever lane made the finding. Terminal states that
    settled nothing referable — refuted, blocked, no finding — deliberately register a closed row
    rather than no row: "the claim you mean was disproved" is an answer, and silence is a guess.
    """
    from core.agent_runtime.active_finding import (
        CANDIDATE_UNPROVEN as AF_CANDIDATE,
    )
    from core.agent_runtime.active_finding import (
        CONFIRMED,
        HYPOTHESIS,
        SUPPORTED,
        ActiveFinding,
        finding_id_for,
        record_active_finding,
        record_no_finding,
    )
    from core.agent_runtime.active_finding import (
        PROVEN as AF_PROVEN,
    )
    from core.agent_runtime.active_finding import (
        REFUTED as AF_REFUTED,
    )
    from core.agent_runtime.active_finding import (
        WITHDRAWN as AF_WITHDRAWN,
    )
    from core.agent_runtime.audit_verdict import (
        BLOCKED,
        CANDIDATE_UNPROVEN,
        NO_FINDING,
        PROVEN,
        REFUTED,
    )

    if finding is None and terminal in {NO_FINDING, BLOCKED} and not refuted_titles:
        # The authoritative conclusion of a search that ended with nothing. Recorded as its own
        # state rather than as a withdrawn finding, because a follow-up must be able to tell "I
        # looked and found nothing" from "I found something and then withdrew it" — those are
        # different answers to "prove it".
        with suppress(Exception):
            record_no_finding(
                scope,
                target_files=((target_path,) if target_path else ()),
                lane="workspace_audit",
                origin_request=str(effective_input or "")[:400],
                reason=closing_reason,
            )
        return
    if finding is None and refuted_titles:
        # A search that DISPROVED something and then ran out. The rejection is the more specific
        # fact and it outranks the empty ending: "the claim you mean was tested and did not hold"
        # answers the next `prove it` precisely, where "I found nothing" would drop the one thing
        # this turn actually established. Neither is referable — both leave a null active id.
        status, confidence = AF_REFUTED, HYPOTHESIS
        title = refuted_titles[-1]
        closing_reason = closing_reason or (
            "a test written to reproduce it ran and passed, which disproves the claim"
        )
        with suppress(Exception):
            record_active_finding(
                ActiveFinding(
                    finding_id=finding_id_for(scope=scope, title=title, target=target_path),
                    scope=scope,
                    title=title,
                    target_files=((target_path,) if target_path else ()),
                    claimed_failure_mechanism=title,
                    evidence_sources=tuple(getattr(evidence, "inspected_paths", ()) or ())[:8],
                    confidence=confidence,
                    verification_status=status,
                    lane="workspace_audit",
                    origin_request=str(effective_input or "")[:400],
                    closing_reason=closing_reason,
                )
            )
        return
    if finding is None and terminal not in {REFUTED}:
        status, confidence = AF_WITHDRAWN, HYPOTHESIS
    elif terminal == PROVEN:
        status, confidence = AF_PROVEN, CONFIRMED
    elif terminal == CANDIDATE_UNPROVEN:
        status, confidence = AF_CANDIDATE, SUPPORTED
    elif terminal == REFUTED:
        status, confidence = AF_REFUTED, HYPOTHESIS
    else:
        status, confidence = AF_WITHDRAWN, HYPOTHESIS

    title = finding.title if finding is not None else "no checkable finding"
    path = (finding.file if finding is not None else "") or target_path
    with suppress(Exception):
        record_active_finding(
            ActiveFinding(
                finding_id=finding_id_for(scope=scope, title=title, target=path),
                scope=scope,
                title=title,
                target_files=(path,) if path else (),
                target_symbols_or_lines=(
                    (f"{finding.line_start}-{finding.line_end}",) if finding is not None else ()
                ),
                claimed_failure_mechanism=title,
                expected_failure_behavior=(
                    finding.failure_scenario if finding is not None else ""
                ),
                evidence_sources=tuple(getattr(evidence, "inspected_paths", ()) or ())[:8],
                confidence=confidence,
                verification_status=status,
                lane="workspace_audit",
                origin_request=str(effective_input or "")[:400],
                closing_reason=closing_reason,
            )
        )


def run_stepped_audit(
    agent: Any,
    *,
    task: Any,
    effective_input: str,
    source_context: dict[str, Any] | None,
    session_id: str,
) -> Any:
    """Drive the stepped audit and ALWAYS return a decision.

    Three things happen here that did not before, and each one is a rule in AGENT_HANDOVER §1A:

    1. **Permission is resolved once, before the first model call** (rule 1), into an
       `AuditExecutionPolicy` carried through every step and stamped on the turn's context so the
       research lane and the tool layer read the same state. A read-only audit stops after
       nomination with a labelled `candidate_unproven`; it does not write, and it does not "try".
    2. **A proof that PASSES refutes its candidate and the search continues** (rule 4). The old
       lane proved once and stopped, then described the disproved claim as the finding. Now the
       counterexample is recorded, its semantic key is banned from renomination, and the next
       candidate is nominated — up to three distinct candidates, then an honest `no_finding`.
    3. **There is no `None`** (rule 7). Every dead end returns a bounded terminal result rather
       than falling back into the single-shot lane this module exists to replace.
    """
    # SCALPEL fix, failure class I/8, 2026-08-06: runtime-owned monotonic timing per stage. Never
    # model-generated (rule: "do not ask the model to estimate wall-clock time") -- `time.monotonic`
    # is immune to wall-clock adjustment, which matters for a stage that can span a real network
    # call. Accumulated (not overwritten) at each stage's own call site below, since nomination and
    # challenge can each run more than once per turn.
    _turn_started = time.monotonic()
    stage_timing: dict[str, float] = {
        "context_collection": 0.0, "nomination": 0.0, "challenge": 0.0, "proof": 0.0, "rendering": 0.0,
    }
    _context_collection_started = time.monotonic()
    # SCALPEL final-tip verification, 2026-08-06: computed here, not only after evidence resolves,
    # so an early BLOCKED exit (no evidence, a vendor collision, no reachable provider) can still
    # name which daemon checkout actually ran it -- this never depended on the audited workspace,
    # only on this running process's own source tree.
    from core.agent_runtime.continuity_gate import git_head_sha

    _daemon_checkout_sha = git_head_sha(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )

    from core.agent_runtime.active_finding import scope_from_context
    from core.agent_runtime.audit_call_budget import proof_ledger, read_only_ledger
    from core.agent_runtime.audit_policy import derive_execution_policy
    from core.agent_runtime.audit_routing import RoutingLedger, routing_refusal_sentence
    from core.agent_runtime.audit_session import (
        AuditSessionCapsule,
        RefutedCandidate,
        audit_follow_up_resumes,
        claim_key,
        load_audit_capsule,
        save_audit_capsule,
        source_identity,
    )
    from core.agent_runtime.audit_verdict import (
        BLOCKED,
        CANDIDATE_CHALLENGE_PARSE_FAILED,
        CANDIDATE_CHALLENGE_PROVIDER_FAILURE,
        CANDIDATE_CHALLENGED,
        CANDIDATE_FILTERED,
        CANDIDATE_REJECTED,
        CANDIDATE_UNPROVEN,
        CANDIDATE_UNREVIEWED_BUDGET_EXHAUSTED,
        CONFIDENCE_CHALLENGED,
        NO_FINDING,
        PROVEN,
        REFUTED,
        AuditVerdict,
        _challenge_attempted_but_unresolved,
        _challenge_never_attempted,
        _refuted_challenges,
        compose_search_ended_reason,
        duplicate_candidate_ids,
        reconcile_candidate_counts,
        render_audit_report,
        split_strengths,
    )
    from core.agent_runtime.candidate_sanity import structural_exception_mismatch
    from core.agent_runtime.continuity_gate import candidate_id_for, claim_signature, matches_any_claim
    from core.agent_runtime.follow_up_gate import (
        gate_follow_up,
        gate_follow_up_conclusion,
    )
    from core.memory_first_router import ModelExecutionDecision
    from core.routing_decision_log import record_decision
    from core.secret_redaction import redact_secrets
    from core.token_usage_receipt import (
        aggregate_call_usage,
        audit_token_breakdown,
        audit_usage_sentence,
    )
    from core.turn_model_call_ledger import turn_model_calls

    # The CALLER'S dict, not a copy. Receipts and evidence must stay visible to the honesty guards
    # that run on this same dict after the turn returns; a copy strands them (measured live: the
    # completion-claim gate saw zero receipts for work that genuinely ran and rewrote the report).
    ctx = source_context if isinstance(source_context, dict) else {}

    # The isolation boundary for everything below. A capsule and an active finding belong to one
    # project and one chat; a follow-up asked elsewhere must miss and say so, never inherit.
    scope = scope_from_context(ctx, session_id=session_id)

    resumed = audit_follow_up_resumes(
        effective_input,
        session_id=session_id,
        project_id=scope.project_id,
        chat_id=scope.chat_id,
    )

    # THE FOLLOW-UP GATE (§B). Before the policy is derived, before evidence is loaded, and long
    # before the first model call: an action that needs a finding cannot run without one. The
    # incident ran this whole function on a pronoun that pointed at nothing and produced a test for
    # a defect nobody had named.
    gate = gate_follow_up(effective_input, scope)
    if gate.blocked:
        return _gate_blocked_decision(
            gate=gate, effective_input=effective_input, session_id=session_id
        )
    # A continuation that resumed a capsule whose search ended with nothing is the same situation
    # reached by a different door — the capsule outlives the finding row's referability, and a
    # `no_finding` capsule has no pending candidate to carry. Blocking here is what stops the
    # substitution; the alternative was nominating something new under the operator's pronoun.
    if (
        gate.requires_finding
        and not gate.bound
        and resumed is not None
        and resumed.pending_finding is None
    ):
        blocked = gate_follow_up_conclusion(gate, terminal_state=resumed.terminal_state)
        return _gate_blocked_decision(
            gate=blocked, effective_input=effective_input, session_id=session_id
        )

    # Every downstream step — model request metadata, artifact check, receipt — carries this id, so
    # a later stage cannot quietly be about a different claim than the one that was bound here.
    ctx["active_finding_id"] = gate.finding_id or ""

    prior_policy = resumed.policy if resumed is not None else None
    policy = derive_execution_policy(effective_input, ctx, prior=prior_policy)
    # Stamped BEFORE the first model call so every consumer — the research lane, the tool layer,
    # the receipt — reads one permission state rather than re-deriving it from prose.
    ctx["audit_execution_policy"] = policy.as_dict()

    evidence = _evidence_from_context(ctx)
    if evidence is None and resumed is not None:
        # A continuation whose turn carries no fresh evidence still knows its subject: the capsule
        # pinned the file and the bytes. Rebuilding evidence from it is what makes "prove it" mean
        # the same file, rather than whatever the next turn happens to be about.
        evidence = _evidence_from_context({"workspace_audit_evidence": resumed.evidence_blob})
    if evidence is None:
        return _blocked_decision(
            reason="this turn carried no readable audit evidence, so there was nothing to audit",
            effective_input=effective_input,
            session_id=session_id,
            fixture_identity={"daemon_checkout_sha": _daemon_checkout_sha},
            elapsed_seconds=time.monotonic() - _turn_started,
            source_context=ctx,
            task_id=getattr(task, "task_id", ""),
        )

    _register_audit_reads(evidence, session_id)

    # SCALPEL final-tip verification, 2026-08-06: available as soon as evidence resolves, so every
    # BLOCKED exit from this point on -- including the vendor-collision gate right below -- can
    # carry the same source/target identity a candidate_unproven/proven turn already gets, not a
    # narrower record just because nothing was ever nominated.
    _source_checkout_sha = git_head_sha(str(evidence.workspace_root or ""))

    def _blocked_fixture_identity(target: str) -> dict[str, Any]:
        return {
            "resolved_workspace_root": str(evidence.workspace_root or ""),
            "resolved_target_absolute_path": (
                target
                if target and posixpath.isabs(target)
                else posixpath.join(str(evidence.workspace_root or ""), target or "")
            ),
            "target_file_sha256": (
                hashlib.sha256(
                    str(evidence.sources.get(target, "") or "").encode("utf-8", errors="replace")
                ).hexdigest()
                if target and evidence.sources.get(target)
                else ""
            ),
            "source_checkout_sha": _source_checkout_sha,
            "daemon_checkout_sha": _daemon_checkout_sha,
        }

    # SCALPEL fix, failure class fixture-safety, 2026-08-06: the incident found TWO real,
    # differently-behaved copies of the same-named fixture file in one session (a `.venv`-bundled
    # site-packages copy still carrying the original defect, and a workspace copy already fixed) --
    # a workspace whose own inventory contains a vendored/dependency path colliding on basename with
    # a real source path is exactly "ambiguous workspace binding", and it must be visible and
    # deterministic, never silently resolved one way. Checked BEFORE the first model call: a
    # candidate nominated against the wrong copy is not a nomination-quality problem, it is asking
    # the wrong question.
    from core.agent_runtime.continuity_gate import vendored_path_collisions

    _vendor_collisions = vendored_path_collisions(tuple(evidence.all_paths))
    if _vendor_collisions:
        _collision_note = "; ".join(f"{v!r} vs {r!r}" for v, r in _vendor_collisions[:3])
        _blocked_target = _reported_target_path(
            evidence, _target_path_for(evidence, effective_input)
        )
        return _blocked_decision(
            reason=(
                "this workspace's own file inventory contains a vendored/dependency copy and a "
                f"real source copy sharing the same filename ({_collision_note}) -- resolving "
                "either one silently would risk auditing the wrong file. Narrow the request to the "
                "exact path you mean, or exclude the vendored directory from this workspace."
            ),
            effective_input=effective_input,
            session_id=session_id,
            target_path=_blocked_target,
            fixture_identity=_blocked_fixture_identity(_blocked_target),
            elapsed_seconds=time.monotonic() - _turn_started,
            source_context=ctx,
            task_id=getattr(task, "task_id", ""),
        )

    manifests, pinned_model, refusal_reason, routing = _stepped_manifests(agent, ctx)
    if not manifests:
        _blocked_target = _reported_target_path(
            evidence, _target_path_for(evidence, effective_input)
        )
        return _blocked_decision(
            reason=routing_refusal_sentence(routing, refusal_reason),
            effective_input=effective_input,
            session_id=session_id,
            target_path=_blocked_target,
            model_label=pinned_model,
            routing=RoutingLedger(routing=routing),
            fixture_identity=_blocked_fixture_identity(_blocked_target),
            elapsed_seconds=time.monotonic() - _turn_started,
            source_context=ctx,
            task_id=getattr(task, "task_id", ""),
        )

    # The call budget is chosen by what this turn is allowed to DO. A read-only audit cannot spend
    # calls generating a proof it may not run, so its shape is analysis -> challenge -> verdict and
    # its ceiling says so; an authorized turn gets the artifact and synthesis calls as well.
    budget = SteppedAuditBudget(
        ledger=proof_ledger() if policy.proof_authorized else read_only_ledger(),
        routing=RoutingLedger(routing=routing),
    )
    usages: list[dict[str, Any]] = []
    target_path = _target_path_for(evidence, effective_input)
    capsule = (
        resumed
        or load_audit_capsule(session_id, project_id=scope.project_id, chat_id=scope.chat_id)
        or AuditSessionCapsule(session_id=session_id)
    )
    if resumed is None:
        # A fresh audit request starts a fresh EPISODE. Rule 4 scopes the renomination ban to the
        # authorized search it belongs to; carrying refutations across a later, separate request
        # would let a claim disproved against one file suppress the same wording against another —
        # and would silently prove a stale candidate the operator never asked about again. A
        # continuation (`resumed`) is the opposite case and deliberately carries everything.
        capsule = AuditSessionCapsule(session_id=session_id)
    capsule.session_id = session_id
    capsule.project_id = scope.project_id
    capsule.chat_id = scope.chat_id
    capsule.target_path = target_path or capsule.target_path
    capsule.workspace_root = str(evidence.workspace_root or "") or capsule.workspace_root
    capsule.source_hash = source_identity(evidence.sources.get(target_path, "")) or capsule.source_hash
    capsule.policy = policy
    capsule.evidence_blob = {
        "all_paths": list(evidence.all_paths),
        "inspected_paths": list(evidence.inspected_paths),
        "sources": dict(evidence.sources),
        "workspace_root": evidence.workspace_root,
        "incomplete_files": list(evidence.incomplete_files),
        # Carried, or a resumed audit forgets it had a subject. Measured on the acceptance drive:
        # the continuation replayed the right three files and then headed the report
        # `Status: No Issues Found — the workspace`, because the capsule's own blob omitted this
        # field and the reported target resolved to "" — a scoped audit of one file describing
        # itself as a clean repository sweep. The mirror image of the phantom target, and worse.
        "scoped_target": str(getattr(evidence, "scoped_target", "") or ""),
    }
    if not capsule.original_request:
        capsule.original_request = effective_input

    # A resumed turn proves the candidate the operator was already shown. Re-nominating would let
    # a follow-up silently change subject, which is the failure this capsule exists to prevent.
    carried = resumed.pending_finding if resumed is not None else None
    # Whether `carried` has ALREADY survived the adversarial challenge. A pending finding has (it
    # was displayed to the operator on the turn that produced it), and re-challenging it would
    # change the continuation's subject. A candidate rebuilt from the unreviewed list has not —
    # being unreviewed is the entire reason it is still in that list — so it must go through the
    # gate the previous turn ran out of budget before reaching.
    carried_prechallenged = carried is not None
    if carried is None and resumed is not None and resumed.unreviewed:
        from core.agent_runtime.active_finding import CHALLENGE, classify_follow_up

        if classify_follow_up(effective_input).action == CHALLENGE:
            carried = _finding_from_unreviewed(resumed.unreviewed[0])
    # …and when there is nothing to resume, the turn must SAY so. A continuation that quietly
    # nominates something new answers a question the operator did not ask, under the pronoun they
    # used for a different claim. Driven live 2026-08-01: turn 1 ended `no_finding`, "prove the bug
    # you identified" nominated a fresh candidate, and nothing in the report distinguished it from
    # the finding the operator meant.
    substituted_for_referent = resumed is not None and carried is None
    # Snapshotted HERE, not read back at render time: `resumed` IS the capsule this turn goes on to
    # mutate, so by the end `terminal_state` holds THIS turn's verdict. Reading it late made the
    # note say "the earlier turn ended `candidate_unproven`" while describing the turn printing it.
    prior_terminal = str(getattr(resumed, "terminal_state", "") or "") if resumed else ""
    prior_titles = tuple(getattr(resumed, "nominated", ()) or ()) if resumed else ()

    used_manifest = manifests[0]
    finding: SteppedFinding | None = None
    proof: SteppedProof | None = None
    terminal = ""
    blocked_reason = ""
    failure_notes: list[str] = []
    last_call: SteppedCallResult | None = None
    screened_signatures: list[frozenset[str]] = []
    screened_rows: list[dict[str, str]] = []
    # Candidates a deterministic pre-filter rejected before spending a challenge/proof call on them
    # (SCALPEL fix, failure class F, 2026-08-06) -- distinct from `screened_rows` (which DID spend a
    # real challenge call). This is what finally gives `reconcile_candidate_counts`'s `filtered`
    # term a real, non-zero population.
    filtered_rows: list[dict[str, Any]] = []
    # Candidates that survived the adversarial source check but were not carried to proof. Before
    # this the search stopped at the FIRST supported candidate, so "audit this file, tell me pros
    # and cons" was answered with one defect and the rest of the pass was discarded.
    extra_findings: list[Any] = []
    survey_rows: list[dict[str, Any]] = []

    # Everything above this point (evidence/policy/capsule/routing resolution) is the closest this
    # function's own scope gets to "source/context collection" -- the evidence READ itself happens
    # upstream in workspace_audit.py, before this function is ever called, so it is not measurable
    # from here; this stage's number honestly covers audit-turn setup, not file I/O.
    stage_timing["context_collection"] = time.monotonic() - _context_collection_started

    for _candidate_index in range(_MAX_CANDIDATES):
        carried_candidate = carried is not None
        if carried is not None:
            finding, carried = carried, None
        else:
            finding = None
            for manifest in manifests:
                _nominate_started = time.monotonic()
                (
                    finding,
                    resolved,
                    last_call,
                    terminal_citation,
                    fail_note,
                    batch_rows,
                ) = _nominate_finding(
                    agent,
                    manifest=manifest,
                    task=task,
                    source_context=ctx,
                    evidence=evidence,
                    effective_input=effective_input,
                    budget=budget,
                    usages=usages,
                    banned_keys=capsule.banned_signatures(),
                    screened_keys=tuple(screened_signatures),
                    refuted_titles=tuple(item.title for item in capsule.refuted),
                    screened_titles=tuple(item["title"] for item in screened_rows),
                    already_found_titles=tuple(
                        dict.fromkeys(
                            str(row.get("title") or "").strip()[:200]
                            for row in survey_rows
                            if row.get("title")
                        )
                    ),
                )
                stage_timing["nomination"] += time.monotonic() - _nominate_started
                used_manifest = manifest
                # The rest of the SAME nomination call, kept whether or not the primary survives.
                # Observations were never going to be proven - they are not challenged and not
                # executed - so discarding them because the PROVABLE candidate was refuted throws
                # away work the model already did and the operator already paid for. Measured live
                # 2026-08-03: a run whose sole candidate was refuted reported "no verified bug
                # found" with zero observations, having been handed a batch containing several.
                # H2, confirmed live 2026-08-04: three sequential nomination attempts on the same
                # file each returned a real, largely non-overlapping batch (18 / 19 / 15 rows
                # against a live free-cloud model) and only the FIRST attempt's batch ever reached
                # `_survey_findings` below -- attempts 2 and 3 were generated at real model cost
                # and then discarded whole, including findings absent from attempt 1 (a timezone
                # bug in `parse_timestamp`, a div-by-zero in `average_line_length`, an unclosed
                # file handle in `export_binary`, ...). Union every attempt's rows instead,
                # deduping by the same `claim_signature`/`matches_any_claim` mechanism this loop
                # already uses to keep a later nomination from repeating a screened or refuted
                # claim, so a later attempt that restates an earlier row in different words does
                # not double-count it. `_survey_findings` below still re-validates every row's
                # citation against the evidence before it can print, so a hallucinated row from a
                # later attempt is caught exactly as one from the first attempt already was.
                for row in batch_rows:
                    # Bound BEFORE the signature is computed and BEFORE the append. This is not the
                    # sole guarantee for either downstream reader -- `already_found_titles` below
                    # already applies its own independent `[:200]` slice at its construction site,
                    # and `_survey_findings` applies its own `_bounded_row_text` call too -- but
                    # bounding `survey_rows` itself here means a THIRD reader added later gets the
                    # same guarantee for free, instead of needing to remember to truncate again.
                    row["title"] = _bounded_row_text(row.get("title"), 200)
                    row["failure_scenario"] = _bounded_row_text(row.get("failure_scenario"), 900)
                    signature = claim_signature(
                        str(row.get("title") or ""), str(row.get("failure_scenario") or "")
                    )
                    existing_signatures = tuple(
                        claim_signature(
                            str(seen.get("title") or ""), str(seen.get("failure_scenario") or "")
                        )
                        for seen in survey_rows
                    )
                    if matches_any_claim(signature, existing_signatures):
                        continue
                    # Which nomination round produced this row -- Activity-only detail (point 4:
                    # "which model call/round produced each candidate"), never read by any renderer
                    # or by `_finding_defect`/`_survey_findings`'s own citation checks.
                    row["nomination_round"] = _candidate_index
                    # Stable id + fingerprint, same derivation as `SteppedFinding.id` — so a survey
                    # row and the eventual primary can be recognized as the SAME candidate even
                    # though one is a dict and the other a dataclass (SCALPEL fix, failure class D).
                    row["id"] = candidate_id_for(
                        target=str(row.get("file") or target_path),
                        title=str(row.get("title") or ""),
                        scenario=str(row.get("failure_scenario") or ""),
                    )
                    row["claim_signature"] = sorted(signature)
                    survey_rows.append(row)
                if finding is not None:
                    target_path = resolved or target_path
                    break
                failure_notes.append(
                    f"{getattr(manifest, 'model_name', '') or getattr(manifest, 'provider_id', '?')!s}:{fail_note}"
                )
                # Walking to the next manifest is only justified by a TRANSPORT failure — the model
                # never produced an artifact, so another provider might. A citation the evidence
                # refutes is not that: the claim is wrong about the source, and a second model
                # re-reading the same source pays another full excerpt to be wrong differently.
                # That fan-out is a third of the incident's nine calls.
                if terminal_citation or budget.exhausted() or not budget.may_call("nominate"):
                    break
                if str(fail_note).startswith("evidence_rejected:"):
                    break
        if finding is None:
            break
        capsule.nominated.append(finding.title)

        # SCALPEL fix, failure class F, 2026-08-06: a cheap, deterministic pre-filter, spent BEFORE
        # a challenge/proof model call, not instead of one. `structural_exception_mismatch` catches
        # one specific, general, reusable shape: a claim about exception behavior directly
        # contradicted by the cited code's own control flow (a "this raises unexpectedly" claim
        # against a controlled, designed `raise`). General on purpose -- no filename, no bug class.
        _filter_reason = structural_exception_mismatch(
            source=str(evidence.sources.get(finding.file, "") or ""),
            line_start=finding.line_start,
            line_end=finding.line_end,
            failure_scenario=finding.failure_scenario,
        )
        if _filter_reason:
            screened_signatures.append(claim_signature(finding.title, finding.failure_scenario))
            filtered_rows.append(
                {
                    "id": finding.id,
                    "claim_signature": sorted(claim_signature(finding.title, finding.failure_scenario)),
                    "title": finding.title,
                    "file": finding.file,
                    "line_start": finding.line_start,
                    "line_end": finding.line_end,
                    "failure_scenario": finding.failure_scenario,
                    "reason": _filter_reason,
                    "lifecycle_state": CANDIDATE_FILTERED,
                    "nomination_round": _candidate_index,
                }
            )
            finding = None
            if budget.exhausted():
                blocked_reason = "the audit budget ended after a candidate was filtered before challenge"
                break
            continue

        # An authorized proof is the stronger falsification step. A read-only candidate cannot be
        # executed, so it receives a compact adversarial source challenge before any of its
        # semantics are allowed into the report. A carried follow-up candidate already passed this
        # gate on the turn that displayed it; challenging it again would change the continuation's
        # subject and spend a call before the explicitly requested proof.
        if not policy.proof_authorized and not (carried_candidate and carried_prechallenged):
            _challenge_started = time.monotonic()
            challenge = _challenge_finding(
                agent,
                manifest=used_manifest,
                task=task,
                source_context=ctx,
                evidence=evidence,
                finding=finding,
                budget=budget,
                usages=usages,
            )
            stage_timing["challenge"] += time.monotonic() - _challenge_started
            if challenge.verdict != "supported":
                screened_signatures.append(claim_signature(finding.title, finding.failure_scenario))
                screened_rows.append(
                    {
                        "id": finding.id,
                        "claim_signature": sorted(claim_signature(finding.title, finding.failure_scenario)),
                        "title": finding.title,
                        # 2026-08-06: `file`/`line_start`/`line_end`/`failure_scenario` were absent
                        # here before -- every OTHER "is this the same claim?" question in this
                        # function goes through `claim_signature` (title + failure_scenario), but
                        # the exclusion of already-screened candidates from `additional_findings`
                        # (below) had nothing but a bare title string to compare against, so two
                        # genuinely different candidates that happened to share an identical title
                        # (different file, different mechanism) collided and the second, real,
                        # never-reviewed one silently vanished. These fields make that comparison
                        # possible; they also let Activity show exactly what was proposed, not just
                        # what the challenge said about it.
                        "file": finding.file,
                        "line_start": finding.line_start,
                        "line_end": finding.line_end,
                        "failure_scenario": finding.failure_scenario,
                        "verdict": challenge.verdict,
                        "reason": challenge.reason,
                        "counterexample": challenge.counterexample,
                        # Structured, not inferred from `reason` text: whether a real check ran at
                        # all (a model reply, however unusable, or a deterministic execution) versus
                        # the budget refusing the call before it ever reached the model. See
                        # `FindingChallenge.attempted`'s own comment.
                        "attempted": challenge.attempted,
                        # SCALPEL fix, failure class D/I: which specific way an inconclusive
                        # challenge was inconclusive, not just prose in `reason`.
                        "error_kind": challenge.error_kind,
                        # Explicit, not inferred downstream from `verdict`/`attempted` by whoever
                        # reads this row -- one of the `CANDIDATE_*` constants in audit_verdict.py.
                        # `error_kind` only refines WHICH non-terminal state a still-`CHALLENGED`
                        # row is in; it never overrides a real `refuted`/not-`attempted` verdict.
                        "lifecycle_state": (
                            CANDIDATE_REJECTED
                            if challenge.verdict == "refuted"
                            else CANDIDATE_UNREVIEWED_BUDGET_EXHAUSTED
                            if not challenge.attempted
                            else CANDIDATE_CHALLENGE_PARSE_FAILED
                            if challenge.error_kind == "parse_failed"
                            else CANDIDATE_CHALLENGE_PROVIDER_FAILURE
                            if challenge.error_kind == "provider_failure"
                            else CANDIDATE_CHALLENGED
                        ),
                        # Activity-only detail (point 4): which nomination round promoted this
                        # candidate before it was sent to challenge.
                        "nomination_round": _candidate_index,
                    }
                )
                finding = None
                if budget.exhausted():
                    blocked_reason = "the audit budget ended after a candidate failed source checking"
                    break
                continue

        if not policy.proof_authorized:
            # Rule 2/3: no execution was authorized, so this is a CANDIDATE and is labelled one.
            terminal = CANDIDATE_UNPROVEN
            proof = None
            # Nothing can be executed on this turn, so there is no proof step to spend the rest of
            # the budget on. Keep looking instead: a supported candidate is banned by signature the
            # same way a refuted one is, so the next nomination must name a different mechanism,
            # and it faces the same adversarial check — a fabricated extra is refuted, not printed.
            # The ledger (nominate 3 / challenge 2 on a read-only turn) is what actually bounds
            # this; the range cap never had to.
            if len(extra_findings) + 1 >= _MAX_PROVABLE_CANDIDATE_ROUNDS or budget.exhausted():
                break
            screened_signatures.append(claim_signature(finding.title, finding.failure_scenario))
            extra_findings.append(finding)
            finding = None
            continue

        _proof_started = time.monotonic()
        proof = _prove_finding(
            agent,
            manifest=used_manifest,
            task=task,
            source_context=ctx,
            session_id=session_id,
            evidence=evidence,
            finding=finding,
            budget=budget,
            usages=usages,
            policy=policy,
            banned_signatures=capsule.banned_signatures() + tuple(screened_signatures),
        )
        stage_timing["proof"] += time.monotonic() - _proof_started
        if proof.proven:
            terminal = PROVEN
            break
        if proof.refutes_the_claim:
            # Rule 4: the test ran and exited 0. That DISPROVES the claim. Record the
            # counterexample, ban the semantic claim, and keep searching in the same turn.
            capsule.refuted.append(
                RefutedCandidate(
                    title=finding.title,
                    claim_key=claim_key(finding.title, finding.failure_scenario),
                    signature=claim_signature(finding.title, finding.failure_scenario),
                    counterexample=(
                        "a test written to reproduce it ran and PASSED (exit 0), so the described "
                        "failure does not occur on this code"
                    ),
                    test_command=proof.test_command,
                    returncode=proof.returncode,
                )
            )
            terminal = REFUTED
            finding = None
            proof = None
            if budget.exhausted():
                break
            continue
        # Errored, never ran, timed out, or the write was refused: none of these disproves the
        # claim and none of them proves it, so the CANDIDATE stands — unproven, with the mechanism
        # that failed named. `blocked` is reserved for "no verdict was reachable at all" (no
        # evidence, no model, no candidate); a checkable citation with a broken proof harness is a
        # different and more useful answer, and collapsing the two would throw away the finding.
        terminal = CANDIDATE_UNPROVEN
        blocked_reason = proof.blocked_reason or _PROOF_NOTE_REASONS.get(
            proof.note, "the outcome was inconclusive"
        )
        break

    refuted_rows = [
        {
            "title": item.title,
            "counterexample": item.counterexample,
            "test_command": item.test_command,
            "returncode": item.returncode,
        }
        for item in capsule.refuted
    ]

    # The loop banks each supported candidate and clears `finding` before looking for the next, so
    # a search that ends by running out of nominations leaves the findings in `extra_findings` and
    # nothing in `finding`. Promote the first — it is the model's own highest-ranked one, since the
    # nomination prompt asks for "the single highest-risk real bug" each time — and report the rest
    # beside it. Without this, collecting candidates would LOSE the very finding it collected.
    #
    # Moved ABOVE the terminal/blocked_reason resolution below (2026-08-06): this decides whether
    # the turn is actually going to end CANDIDATE_UNPROVEN (a live primary) or NO_FINDING/BLOCKED
    # (nothing to promote). Composing `blocked_reason` before this ran meant a summary sentence
    # could get written for a `NO_FINDING` outcome that was about to be overturned into
    # `CANDIDATE_UNPROVEN` one statement later -- academic for what actually renders (only
    # `NO_FINDING`/`BLOCKED` show `blocked_reason`), but the field still ends up on the `AuditVerdict`
    # and in telemetry, so it should describe the terminal state this turn actually reaches.
    if finding is None and extra_findings and terminal in ("", CANDIDATE_UNPROVEN):
        finding = extra_findings.pop(0)
        terminal = CANDIDATE_UNPROVEN

    # Every row any nomination call ever proposed, deduplicated against the candidates that were
    # actually promoted and challenged (`screened_rows`) -- computed here, BEFORE blocked_reason
    # below, and by CLAIM SIGNATURE rather than a bare title string.
    #
    # QA regression (adversarial review), 2026-08-06: the first version of this exclusion matched
    # on `title.strip().lower()` alone. Every OTHER "is this the same claim?" question in this
    # function (banned candidates, screened candidates, refuted candidates) goes through
    # `claim_signature`/`matches_any_claim` (title AND failure_scenario) for exactly the reason
    # documented where `claim_signature` is first used above: two real, DIFFERENT candidates can
    # legitimately share an identical title ("Retry loop returns incorrect state" naming a webhook
    # sender in one file and a DB health-check probe in another). A bare-title match collided them,
    # and the second, genuinely distinct, never-reviewed candidate silently vanished from
    # `additional_findings` -- an over-eager version of the exact double-count bug this exclusion
    # exists to prevent, just in the opposite direction (dropping a real candidate instead of
    # duplicating one). `screened_rows` did not carry `failure_scenario` before this same round of
    # fixes; it does now (see its own append site above), which is what makes this comparison
    # possible.
    # SCALPEL fix, failure class D, 2026-08-06: this exclusion previously covered only
    # `screened_rows` (candidates that were challenged and did NOT survive) — never the WINNING
    # candidate itself. Any candidate that reaches this point as the surviving `finding` has, by
    # construction, already been through its own challenge (the branch above that promotes a
    # `verdict == "supported"` candidate out of the screening loop) — so it inherently carries a
    # "challenged" history. A batch sibling or a later nomination round proposing the SAME
    # underlying claim under different wording was never matched against the primary's own
    # signature, so it survived into `survey_extra` and rendered a SECOND time as "unreviewed" next
    # to the very candidate it describes — the exact "likely / challenged / not reviewed, all at
    # once, about one candidate" contradiction. Excluding the winning finding's own signature here
    # closes that: the same underlying claim can no longer occupy both a terminal bucket (as the
    # promoted primary) and the unreviewed bucket (as an un-collapsed duplicate of itself).
    excluded_signatures = tuple(
        claim_signature(str(item.get("title") or ""), str(item.get("failure_scenario") or ""))
        for item in screened_rows
    ) + tuple(
        claim_signature(str(item.get("title") or ""), str(item.get("failure_scenario") or ""))
        for item in filtered_rows
    )
    if finding is not None:
        excluded_signatures = (
            *excluded_signatures,
            claim_signature(finding.title, finding.failure_scenario),
        )
    screened_claim_signatures = excluded_signatures
    survey_rows_unscreened = [
        row
        for row in survey_rows
        if not matches_any_claim(
            claim_signature(str(row.get("title") or ""), str(row.get("failure_scenario") or "")),
            screened_claim_signatures,
        )
    ]
    # Computed HERE, before `blocked_reason` below, not after it: `_survey_findings` applies its
    # OWN citation-validity check (`_finding_defect`, which can drop a row whose citation does not
    # resolve against the evidence actually read) on top of the title-signature exclusion above.
    #
    # QA regression (adversarial review), 2026-08-06: the prior version composed `blocked_reason`
    # from a count of raw, PRE-`_finding_defect`-filter `survey_rows`, while the score line's own
    # "Unreviewed candidates" (via `_unresolved_candidate_counts`, reading `verdict.
    # additional_findings`) counted the POST-filter `survey_extra`. A survey row with an invalid
    # citation is silently dropped by `_survey_findings` but was still being counted toward the
    # narrative sentence -- reproducing, through a different path, the exact "two numbers in one
    # report disagree" defect this whole fix exists to close. Both consumers now read the identical,
    # already-filtered `survey_extra`.
    survey_extra = _survey_findings(survey_rows_unscreened, evidence, target_path) if survey_rows_unscreened else []
    # ARGUS repair D4, 2026-08-06: `survey_extra` can legitimately carry `harm_class == "strength"`
    # rows (an observation batch may report something done WELL, not only defects). Every downstream
    # CANDIDATE count below (`unreviewed_count`, the Activity `unreviewed_survey_only` figure) must
    # exclude them -- `AuditVerdict.__post_init__` splits strengths out of `additional_findings`
    # into `verdict.strengths` before `_score_lines` ever counts anything, so a count taken HERE that
    # still includes them disagrees with the score line's own count of the identical candidates by
    # however many strengths were in the batch. Confirmed live: a batch of 1 genuine unreviewed
    # candidate + 2 strengths rendered "Unreviewed: 1" in the score line (post-split) next to "3
    # candidates remained unreviewed" in the blocked-reason sentence (pre-split) -- one report,
    # two disagreeing counts of what should be the same set. `split_strengths` is the SAME function
    # `AuditVerdict.__post_init__` itself calls; using it here instead of re-deriving the filter is
    # what keeps this count from being able to drift from that one again.
    survey_extra_candidates, survey_extra_strengths = split_strengths(survey_extra)
    # Activity-only lookup (point 4): `_survey_findings` returns `VerdictFinding` objects, which
    # have no `nomination_round` field of their own -- rather than widen that shared dataclass
    # (used well beyond this function) for one Activity-only detail, the round each survivor came
    # from is recovered here by the same `claim_signature` this whole function already uses to
    # decide "is this the same claim", keyed off the raw rows that carried it.
    _survey_row_round_lookup: dict[frozenset[str], int] = {
        claim_signature(str(row.get("title") or ""), str(row.get("failure_scenario") or "")): int(
            row.get("nomination_round") if isinstance(row.get("nomination_round"), int) else -1
        )
        for row in survey_rows_unscreened
    }

    # SCALPEL fix, failure class D: a per-candidate hard invariant, complementing (not replacing)
    # `reconcile_candidate_counts`'s aggregate check below -- that one confirms the TOTALS add up;
    # this confirms no single candidate id is COUNTED IN two of them at once, which a compensating
    # pair of miscounts could hide from the aggregate check alone. Runs unconditionally (not only in
    # the NO_FINDING/BLOCKED branch below), since the exact incident shape -- one candidate rendered
    # as both the promoted primary and a still-unreviewed row -- only exists when a primary DID
    # survive (`CANDIDATE_UNPROVEN`/`PROVEN`), which that branch's own gate excludes.
    _duplicate_candidate_ids = duplicate_candidate_ids(
        screened_rows,
        filtered_rows,
        survey_rows_unscreened,
        [{"id": finding.id}] if finding is not None else [],
    )
    if _duplicate_candidate_ids:
        with suppress(Exception):
            from core.runtime_task_events import emit_runtime_event

            emit_runtime_event(
                ctx,
                event_type="audit_candidate_duplicate_terminal_bucket",
                message=(
                    "candidate id(s) occupy more than one terminal bucket: "
                    + ", ".join(_duplicate_candidate_ids)
                ),
                details={"duplicate_ids": list(_duplicate_candidate_ids)},
            )

    # Defaults so these are always defined by the time `details.stepped_audit` is built below,
    # regardless of which terminal state this turn actually reaches -- only the `NO_FINDING`
    # sub-branch a few lines down computes real values; every other state (PROVEN,
    # CANDIDATE_UNPROVEN, REFUTED-with-a-surviving-finding, or a BLOCKED/NO_FINDING turn with no
    # screened candidates at all) leaves the count of candidates this specific accounting concerns
    # itself with at a correctly-honest zero.
    rejected_count = 0
    challenged_count = 0
    unreviewed_count = 0
    never_attempted_count = 0
    attempted_unresolved_count = 0

    if not terminal or (terminal == REFUTED and finding is None):
        # Either nothing was ever nominated, or every candidate this turn was disproved and the
        # budget ran out. Rule 4 is explicit: never resurrect a refuted candidate as the answer.
        terminal = NO_FINDING if refuted_rows or screened_rows or not failure_notes else BLOCKED
        # Name the model that produced the LAST call, not whichever manifest the loop variable
        # happens to hold. Measured live: two dead `qwen3:14b` attempts left `used_manifest` on the
        # local model while `nemotron` was the one that answered and returned empty content — and
        # the operator was told `qwen3:14b` answered. A failure label naming the wrong model sends
        # them after the wrong problem, which is the rule-9 defect in a new place.
        blocked_reason = blocked_reason or _failure_sentence(
            failure_notes,
            last_call,
            (last_call.model_name if last_call is not None and last_call.model_name else "")
            or getattr(used_manifest, "model_name", ""),
        )
        # Live incident, 2026-08-06, session `openclaw:d77e4cf7487fcb92b78c`: the OLD line here was
        # `blocked_reason = "all nominated candidates failed adversarial checking against the
        # source"` whenever `screened_rows` was merely non-empty -- true only that SOMETHING had
        # been screened, not that EVERYTHING had. That run spent its 5-call budget across 3 nominate
        # + 2 challenge calls, leaving 6 of 8 nominated candidates never offered a challenge at all,
        # yet the rendered report claimed universal adversarial rejection next to `Rejected
        # candidates: 0`. Replaced with `compose_search_ended_reason`, which can only ever describe
        # the counts it is actually given -- the same counts the score line's WITHHELD block and the
        # additional-findings table already show, so the two cannot disagree again. `screened_rows`
        # is non-empty here by construction (the `NO_FINDING` branch just above required it, since
        # `refuted_rows` was already ruled out by this point and `not failure_notes` alone would not
        # explain a non-BLOCKED terminal), so `challenged_count` is always >= 1 in this branch.
        #
        # `challenged_count` counts only candidates a REAL check actually ran against (a model reply
        # of any kind, or a deterministic execution) -- `_challenge_never_attempted` items (the
        # budget refused the call before it reached the model) are counted as unreviewed instead,
        # alongside plain survey rows that were never even offered a challenge. This is the
        # `UNREVIEWED_BUDGET_EXHAUSTED` vs. `CHALLENGED` distinction: a candidate the runtime never
        # actually checked must never be counted as "challenged", no matter how it got skipped.
        if screened_rows and not failure_notes:
            never_attempted_count = len(_challenge_never_attempted(screened_rows))
            attempted_unresolved_count = len(_challenge_attempted_but_unresolved(screened_rows))
            rejected_count = len(_refuted_challenges(screened_rows))
            challenged_count = rejected_count + attempted_unresolved_count
            unreviewed_count = len(survey_extra_candidates) + never_attempted_count
            blocked_reason = compose_search_ended_reason(
                rejected_count=rejected_count,
                challenged_count=challenged_count,
                unreviewed_count=unreviewed_count,
            )
            # A `NO_FINDING`/`BLOCKED` resolution is reached ONLY when neither `extra_findings` nor
            # `finding` survived to this point (see the promotion step above, moved earlier in this
            # same round of fixes specifically so that guarantee holds here) -- so `confirmed` and
            # any challenge-supported-but-not-yet-proven candidate are both structurally 0 whenever
            # this branch runs. The two counts on the left below are therefore each computed as a
            # straight sum of the counts on their right by construction, so this check cannot catch
            # a live disagreement TODAY -- its value is as a regression guard: if a future change to
            # this accounting miscomputes one side without updating the other, this fires instead of
            # silently drifting. The genuine cross-check -- that these constructed counts actually
            # match what the raw `survey_rows`/`screened_rows`/`capsule.nominated` data independently
            # shows -- lives in the integration tests that drive real audit runs through this
            # function, not in a same-function tautology.
            violations = reconcile_candidate_counts(
                nominated=unreviewed_count + challenged_count + len(filtered_rows),
                filtered=len(filtered_rows),
                unreviewed=unreviewed_count,
                challenged=challenged_count,
                rejected=rejected_count,
                confirmed=0,
                unresolved_after_challenge=attempted_unresolved_count,
            )
            if violations:
                with suppress(Exception):
                    from core.runtime_task_events import emit_runtime_event

                    emit_runtime_event(
                        ctx,
                        event_type="audit_candidate_accounting_violation",
                        message="candidate counts do not reconcile: " + "; ".join(violations),
                        details={"violations": violations},
                    )

    provider_id = str(getattr(used_manifest, "provider_id", "") or "")
    model_name = str(getattr(used_manifest, "model_name", "") or "")
    totals = aggregate_call_usage(
        usages,
        provider_id=provider_id,
        model_id=model_name,
        # This turn's OWN count of provider calls entered, so a call that never reached the
        # usage-recording seam is labelled unmetered rather than dropped from the denominator.
        provider_calls=turn_model_calls(ctx),
    )

    synthesis = ""
    if terminal == PROVEN and finding is not None and proof is not None:
        synthesis = _synthesize(
            agent,
            manifest=used_manifest,
            task=task,
            source_context=ctx,
            effective_input=effective_input,
            finding=finding,
            proof=proof,
            budget=budget,
            usages=usages,
        )
        # Recomputed after synthesis, which may itself have entered calls. Same rule as above.
        totals = aggregate_call_usage(
            usages,
            provider_id=provider_id,
            model_id=model_name,
            provider_calls=turn_model_calls(ctx),
        )

    call_rows = budget.ledger.as_rows() if budget.ledger is not None else []
    token_breakdown = audit_token_breakdown(call_rows, totals)
    _emit_call_ledger(ctx, budget)

    capsule.terminal_state = terminal
    capsule.provider_id = provider_id
    capsule.model_name = model_name
    capsule.pinned = bool(pinned_model)
    capsule.pending_finding = finding if terminal == CANDIDATE_UNPROVEN else None
    # How far this pass got, and what it left behind. Both exist so the NEXT turn can answer
    # "resume where, on what?" from state instead of asking a model to nominate the whole thing
    # again — the failure the 2026-08-07 continuation reproduced three times in a row.
    if terminal == PROVEN:
        capsule.phase = "proven"
    elif screened_rows or capsule.refuted:
        capsule.phase = "challenged"
    elif survey_rows or capsule.nominated:
        capsule.phase = "nominated"
    _reviewed_titles = {str(title).strip().lower() for title in capsule.nominated if title}
    _reviewed_titles |= {
        str(row.get("title") or "").strip().lower() for row in screened_rows
    }
    _reviewed_titles |= {str(item.title).strip().lower() for item in capsule.refuted if item.title}
    _unreviewed: list[dict[str, Any]] = []
    for row in survey_rows:
        row_title = str(row.get("title") or "").strip()
        if not row_title or row_title.lower() in _reviewed_titles:
            continue
        row_start, row_end = _canonical_finding_range(row)
        _unreviewed.append(
            {
                "title": row_title[:200],
                "file": str(row.get("file") or target_path),
                "line_start": row_start,
                "line_end": row_end,
                "cited_line_text": str(row.get("cited_line_text") or "")[:300],
                "failure_scenario": str(row.get("failure_scenario") or "")[:900],
                "harm_class": str(row.get("harm_class") or "").strip().lower(),
                "suggested_fix": str(row.get("suggested_fix") or "")[:400],
            }
        )
    # Bounded for the same reason the capsule store itself is: a long-lived daemon must not grow a
    # row per candidate it has ever seen. Twelve is more than the read-only ledger can review.
    capsule.unreviewed = _unreviewed[:12]
    save_audit_capsule(capsule)
    _register_active_finding(
        scope=scope,
        finding=finding,
        terminal=terminal,
        target_path=target_path,
        evidence=evidence,
        effective_input=effective_input,
        closing_reason=blocked_reason,
        refuted_titles=tuple(item.title for item in capsule.refuted if item.title),
    )

    # SCALPEL fix, failure class I, 2026-08-06: the final-answer contract names wall time and the
    # largest context component as required fields, alongside the token figures `audit_usage_
    # sentence` already renders. Runtime-measured only -- `stage_timing["total"]` is `time.
    # monotonic()`, never asked of the model -- and states the field explicitly rather than
    # omitting it when a figure is genuinely unavailable (`token_breakdown` has no rows).
    _wall_time_line = f"Measured wall time: {time.monotonic() - _turn_started:.1f}s"
    _largest_context = token_breakdown.get("assembled_project_context_largest")
    _largest_context_line = (
        f"Largest context component: ~{int(_largest_context):,} tokens (assembled project context)"
        if isinstance(_largest_context, int) and _largest_context > 0
        else "Largest context component: not available (no context was assembled this turn)"
    )
    usage_sentence = audit_usage_sentence(token_breakdown)
    telemetry_line = "\n".join(
        line for line in (usage_sentence, _wall_time_line, _largest_context_line) if line
    )

    verdict = AuditVerdict(
        state=terminal,
        finding=_verdict_finding(finding),
        proof=_verdict_proof(proof),
        refuted=refuted_rows,
        additional_findings=[
            rendered
            for rendered in (_verdict_finding(item) for item in extra_findings)
            if rendered is not None
        ]
        + survey_extra,
        analysis=synthesis,
        remaining_proof=_remaining_proof_sentence(terminal, policy, proof, blocked_reason),
        blocked_reason=blocked_reason,
        model_label=model_name or provider_id or "the selected model",
        # ONE block, composed from the separated figures. The old line printed the cumulative
        # nine-call sum with no indication that it was nine calls or that most of it was the same
        # excerpt sent again; the full composition now lives in Activity, where it belongs.
        usage_line=telemetry_line,
        # The REPORTED target, which is "" for a repository-wide pass. `target_path` above stays
        # the working anchor every citation resolved against; only the headline is withheld.
        target_path=_reported_target_path(evidence, target_path),
        coverage_note=audit_coverage_note(evidence),
        continuation_note=_substitution_note(
            substituted=substituted_for_referent,
            prior_terminal=prior_terminal,
            prior_titles=prior_titles,
            finding=finding,
        ),
        # Finding E point 6, 2026-08-04: a challenge model actively tried to disprove each of these
        # and could -- a real, executed falsification that previously reached only internal
        # telemetry (`details.stepped_audit.screened_out` below) and never the visible report.
        challenged_out=list(screened_rows),
    )
    _render_started = time.monotonic()
    report = render_audit_report(verdict)
    stage_timing["rendering"] = time.monotonic() - _render_started

    with suppress(Exception):
        record_decision(
            session_id=session_id,
            user_input=effective_input,
            family="workspace_audit_stepped",
            handled=True,
            arbiter=f"terminal:{terminal}|calls:{budget.calls}",
        )
    _target_source = evidence.sources.get(target_path, "") if target_path else ""
    _target_abs = (
        target_path
        if target_path and posixpath.isabs(target_path)
        else posixpath.join(str(evidence.workspace_root or ""), target_path or "")
    )
    stepped_audit_detail = {
                "terminal_state": terminal,
                "target": target_path,
                # SCALPEL fix, failure class fixture-safety, 2026-08-06: every production fixture
                # must record exactly which bytes, on which checkout, were actually audited --
                # required to tell "the daemon ran against the intended commit" from "it silently
                # ran against something else" after the fact, and to tell two differently-behaved
                # same-named files apart (the exact incident this whole check exists for).
                "fixture_identity": {
                    "resolved_workspace_root": str(evidence.workspace_root or ""),
                    "resolved_target_absolute_path": _target_abs,
                    "target_file_sha256": (
                        hashlib.sha256(_target_source.encode("utf-8", errors="replace")).hexdigest()
                        if _target_source
                        else ""
                    ),
                    "source_checkout_sha": _source_checkout_sha,
                    "daemon_checkout_sha": _daemon_checkout_sha,
                    "vendor_collisions_in_inventory": [list(pair) for pair in _vendor_collisions],
                },
                "execution_policy": policy.as_dict(),
                "refuted": refuted_rows,
                "screened_out": screened_rows,
                # SCALPEL fix, failure class F, 2026-08-06: candidates a deterministic pre-filter
                # rejected before spending a challenge/proof call -- previously this bucket existed
                # only as an unpopulated constant (CANDIDATE_FILTERED, always zero).
                "filtered": filtered_rows,
                "blocked_reason": blocked_reason,
                # Point 4 (Activity diagnostic exposure): the exact counts `compose_search_ended_
                # reason` and `reconcile_candidate_counts` used, named plainly rather than left for
                # a reader to re-derive from `screened_out`/`additional_findings` by hand. Zero in
                # every field outside the `NO_FINDING` branch that actually populates them (see the
                # default-initialization comment above) -- an honest zero, not an absent key.
                "candidate_accounting": {
                    "rejected": rejected_count,
                    "challenged": challenged_count,
                    "filtered": len(filtered_rows),
                    "unreviewed_never_attempted": never_attempted_count,
                    # ARGUS repair D4, 2026-08-06: strengths excluded -- see the comment where
                    # `survey_extra_candidates`/`survey_extra_strengths` are split, above. Reported
                    # separately (never folded into a candidate count) so the strength batch size is
                    # still visible without letting it inflate an "unreviewed CANDIDATES" figure.
                    "unreviewed_survey_only": len(survey_extra_candidates),
                    "survey_strengths_excluded": len(survey_extra_strengths),
                    "challenged_attempted_but_unresolved": attempted_unresolved_count,
                },
                # 2026-08-06: `additional_findings`/`strengths` reach the chat-facing report only
                # when the primary confirms or a live candidate remains (see `render_audit_report`'s
                # `NO_FINDING` branch, which no longer inlines them at all -- that detail moves to
                # Activity). Before this they existed ONLY in the rendered text; a `NO_FINDING`
                # outcome with real observations in the same nomination batch would have made them
                # genuinely unrecoverable, not merely un-inlined. Each entry carries an explicit
                # `lifecycle_state` (one of the `CANDIDATE_*` constants in audit_verdict.py) rather
                # than leaving the reader to infer it from which list an item sits in or from a bare
                # `.confidence` string -- the exact "implied review" gap this round of fixes closes.
                # `nomination_round` names which pass of the nominate loop proposed it. A
                # `suggested_fix` here is real model output but UNVERIFIED -- it survives only
                # because `_fixes_lines` (audit_verdict.py) refuses to render ANY fix for anything
                # but a `PROVEN` primary; Activity may still show it, clearly labelled as such,
                # since an operator inspecting the raw data is a different audience than the
                # chat-facing final answer this restriction protects.
                "additional_findings": [
                    {
                        "title": item.title,
                        "file": item.file,
                        "line_start": item.line_start,
                        "line_end": item.line_end,
                        "failure_scenario": item.failure_scenario,
                        "harm_class": item.harm_class,
                        "confidence": item.confidence,
                        "lifecycle_state": (
                            CANDIDATE_CHALLENGED
                            if item.confidence == CONFIDENCE_CHALLENGED
                            else CANDIDATE_UNREVIEWED_BUDGET_EXHAUSTED
                        ),
                        "nomination_round": _survey_row_round_lookup.get(
                            claim_signature(item.title, item.failure_scenario), -1
                        ),
                        "suggested_fix_unverified": item.suggested_fix,
                    }
                    for item in verdict.additional_findings
                ],
                "strengths": [
                    {
                        "title": item.title,
                        "file": item.file,
                        "line_start": item.line_start,
                        "line_end": item.line_end,
                        "failure_scenario": item.failure_scenario,
                    }
                    for item in verdict.strengths
                ],
                "finding": (
                    {
                        "title": finding.title,
                        "file": finding.file,
                        "line_start": finding.line_start,
                        "line_end": finding.line_end,
                    }
                    if finding is not None
                    else {}
                ),
                "proof": (
                    {
                        "attempted": proof.attempted,
                        "proven": proof.proven,
                        "note": proof.note,
                        "unauthorized": proof.unauthorized,
                        "blocked_reason": proof.blocked_reason,
                        "test_path": proof.test_path,
                        "test_command": proof.test_command,
                        "returncode": proof.returncode,
                        "contract_rejected": proof.contract_rejected,
                        "artifact_checks": list(proof.artifact_checks),
                        # SCALPEL fix, failure class Activity/8, 2026-08-06: these three previously
                        # existed on `SteppedProof` but were dropped when building this dict --
                        # `output` (the captured stdout/stderr) is exactly what an operator needs to
                        # judge an inconclusive run, and `write_scope`/`temp_root` are what makes an
                        # isolated-external proof attempt auditable after its temp dir is cleaned up.
                        "output": redact_secrets(proof.output)[:_ACTIVITY_PROOF_OUTPUT_CHARS],
                        "write_scope": proof.write_scope,
                        "temp_root": proof.temp_root,
                    }
                    if proof is not None
                    else {"attempted": False, "unauthorized": not policy.proof_authorized}
                ),
                "active_finding": _active_finding_snapshot(scope),
                "active_finding_id": ctx.get("active_finding_id") or "",
                "follow_up_gate": gate.as_dict(),
                "synthesis_used": bool(synthesis),
                "model_calls": budget.calls,
                # The two questions the nine-call audit could not answer about itself: where did
                # the calls go, and which model answered each one.
                "call_budget": budget.ledger.as_dict() if budget.ledger is not None else {},
                "model_routing": budget.routing.as_dict() if budget.routing is not None else {},
                "token_breakdown": token_breakdown,
                # Runtime-measured, never model-estimated. `total` is the whole function's own
                # wall clock; the per-stage figures are each stage's OWN accumulated time (a stage
                # invoked more than once per turn, like nomination or challenge, sums every call).
                # A stage that never ran this turn (e.g. `proof` on a read-only turn) reads 0.0 --
                # an honest zero, not an absent key.
                "timing": {**stage_timing, "total": time.monotonic() - _turn_started},
    }
    # SCALPEL fix, failure class Activity/7, 2026-08-06: this entire structured record used to live
    # ONLY on the returned `ModelExecutionDecision.details` object, which nothing persists -- once
    # the HTTP response was sent, candidate IDs, challenge verdicts/reasons, proof output, and
    # per-stage timing were gone. `_persist_audit_detail` writes the same record `_emit_call_ledger`
    # writes its thin per-call slice into, so Activity/turn_trace can recover it after the fact.
    _persist_audit_detail(ctx, stepped_audit_detail)
    return ModelExecutionDecision(
        source="model" if budget.calls else "stepped_audit",
        task_hash="",
        provider_id=provider_id,
        provider_name=provider_id,
        model_name=model_name,
        output_text=report,
        confidence=0.85 if terminal == PROVEN else 0.7,
        trust_score=0.8 if terminal == PROVEN else 0.6,
        used_model=bool(budget.calls),
        cache_hit=False,
        validation_state="valid",
        details={
            "token_usage": totals,
            "model_call_id": (last_call.model_call_id if last_call else ""),
            "response_id": (last_call.response_id if last_call else ""),
            "stepped_audit": stepped_audit_detail,
        },
    )


def _remaining_proof_sentence(
    terminal: str, policy: Any, proof: SteppedProof | None, blocked_reason: str
) -> str:
    """What still stands between this candidate and a proof — and why, when an attempt was made.

    The two cases are different answers and must read differently: "you did not authorize a test"
    is a decision the operator can reverse in one word, while "the test errored on import" is a
    mechanical failure they can only act on if it is named.
    """
    from core.agent_runtime.audit_verdict import CANDIDATE_UNPROVEN

    if terminal != CANDIDATE_UNPROVEN:
        return ""
    if proof is None or proof.unauthorized:
        return policy.remaining_proof_sentence()
    if not proof.test_command:
        # Attempted, but nothing ever executed — the write was refused, or the model produced no
        # test. Saying "written and run" here would be the same class of false completion claim
        # this whole repair exists to remove.
        return (
            "What proof remains: the proof test did not run — "
            f"{blocked_reason or 'the test could not be created'}. Nothing was executed against "
            "the source, so this stays a candidate and not a bug."
        )
    return (
        "What proof remains: a proof test was written and run, and it did not settle the claim — "
        f"{blocked_reason or 'the outcome was inconclusive'}. Until a test FAILS on the current "
        "code, this stays a candidate and not a bug."
    )


def _failure_sentence(
    failure_notes: list[str], last_call: SteppedCallResult | None, model_label: str
) -> str:
    """Where the audit actually stopped, in the operator's language and factually (rule 9).

    The incident told an operator to pick "a model this machine can reach" after the model had
    answered. `classify_provider_failure` separates a transport failure from a model that answered
    and produced no artifact, and only the former may mention reachability.
    """
    from core.agent_runtime.provider_failure import (
        UNUSABLE_ARTIFACT,
        ProviderFailure,
        classify_provider_failure,
    )

    error = ""
    text = ""
    if last_call is not None:
        error = last_call.error
        text = last_call.text
    if not failure_notes and not error and not text:
        return "no candidate survived checking against the source that was read"

    # What the RUNTIME observed outranks what can be inferred from the raw completion. A citation
    # the source refutes is a known, specific outcome; describing it as "the content was empty"
    # because the text failed a downstream check would send the operator after the wrong problem —
    # the same mistake as the incident's "pick a model this machine can reach".
    joined = "|".join(failure_notes)
    if "evidence_rejected:" in joined:
        rejection = joined.rsplit("evidence_rejected:", 1)[-1].split("|", 1)[0].strip()
        failure = ProviderFailure(
            UNUSABLE_ARTIFACT,
            f"three nominated candidates failed source checking; latest rejection: {rejection}",
        )
    elif "not_json" in joined:
        failure = ProviderFailure(UNUSABLE_ARTIFACT, "the reply was not the JSON object required")
    else:
        failure = classify_provider_failure(error=error, text=text)
    return failure.operator_sentence(model_label=model_label, phase="nomination")
