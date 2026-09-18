"""The claim-predicate census, and the conformance runner that measures it.

WHY THIS EXISTS
---------------
Six independent investigations at six unrelated seams found the same shape: **a claim predicate
answers a different question than its consumer asks.** A function named for one question is wired
into a guard that asks another, and because the wrong answer is usually the same as the right one,
the seam holds until it does not:

* `core/currency_intent.py` `_RATE_WORD_RE` treats the word `quoted` as a price cue, so
  "the text `EUR/USD` is quoted here as plain text -- how many characters" reaches the live FX lane.
* `core/turn_ir.py` `classify_clause_kind` looks up a head verb and is blind to negation, so
  "Do not search the web." has head `do` and classifies as KNOW -- a *content request*.
* `core/agent_runtime/answer_coverage.py` `_claims_file_write` delegates to
  `imperative_build_sentences`, which answers BUILD INTENT under a FILE-WRITE name.
* `coverage_for(...).covers_whole_turn` means "no other REGISTERED family objected".
  `core/agent_runtime/turn_frontdoor.py:1122` reads it as "this family answered the turn".
* `core/execution_requirements.py` `answer_mode == "LIVE_DATA"` means "which toolset executes this",
  and is read as "this lane may terminate the turn".
* `tools/web/web_research.py` `_extract_market_entity_candidates` admits on SHAPE alone, so
  `tell me both` becomes a tradeable asset.

WHAT THIS MODULE IS -- AND IS NOT
--------------------------------
It is an **instrument**, not a gate. It runs every censused predicate over a shared corpus and
reports divergence as ranked data. It does not fail a build on divergence, because the divergence
it measures is the CURRENT state of the tree: a red harness on day one carries no information.
`tests/test_claim_predicate_conformance.py` asserts the instrument's own integrity -- that every
predicate imports, that every predicate has ground truth in BOTH directions, that the mandated hard
input classes are present, and that the instrument can detect a broken predicate.

GROUND TRUTH
------------
Every directional case carries a `basis` string naming WHO decided and ON WHAT. There are exactly
three admissible bases and no others:

* ``name`` -- the predicate's own name and docstring. Self-declared contract; the predicate loses
  any argument with its own docstring.
* ``consumer`` -- the guard at the call site, quoted in the registry row. What the consumer must be
  told to be safe.
* ``both`` -- name and consumer agree.

Where the right answer is genuinely arguable, the case is marked ``CONTESTED`` with a reason and is
**excluded from every count** rather than resolved by picking a side.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------------------------

CLAIM = "CLAIM"
NO_CLAIM = "NO_CLAIM"
CONTESTED = "CONTESTED"

#: Divergence classes, in the census's own vocabulary.
DIV_NONE = "none"
DIV_NARROWER = "narrower"
DIV_WIDER = "wider"
DIV_DIFFERENT = "DIFFERENT QUESTION"

#: Severity ladder. S5 is worst.
SEVERITY = {
    "S5": "unauthorized action / exfiltration / write past a denial",
    "S4": "fabricated fact presented as real",
    "S3": "silent incompleteness / wrong obligation binding",
    "S2": "wrong answer, correctly attributed",
    "S1": "format/contract miss with correct content",
}
_SEVERITY_WEIGHT = {"S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 5}

#: Blast radius: what the consumer's guard is able to do with the answer.
BLAST = {
    3: "guard can TERMINATE the turn (front-door whole-turn claim)",
    2: "guard ROUTES or ADMITS a lane / toolset",
    1: "guard only shapes or formats an answer already decided",
}


# ---------------------------------------------------------------------------------------------
# Hard-input classes. Section 0.4: a predicate that only works on well-formed prose is not working.
# ---------------------------------------------------------------------------------------------

TAG_SLOPPY = "sloppy"
TAG_NUMBERED = "numbered_list"
TAG_BULLETED = "bulleted_list"
TAG_NEGATION = "negation"
TAG_QUOTED = "quoted_literal"
TAG_CANCEL = "mid_turn_cancel"
TAG_CONTINUATION = "continuation"
TAG_WELLFORMED = "wellformed"

MANDATED_TAGS = (
    TAG_SLOPPY, TAG_NUMBERED, TAG_BULLETED, TAG_NEGATION,
    TAG_QUOTED, TAG_CANCEL, TAG_CONTINUATION,
)


@dataclass(frozen=True)
class Case:
    """One corpus row: a text, the verdict ground truth demands, and who decided."""

    case_id: str
    text: str
    verdict: str
    basis: str
    #: Prose justification. For CONTESTED, the reason both readings survive.
    why: str
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in (CLAIM, NO_CLAIM, CONTESTED):
            raise ValueError(f"{self.case_id}: bad verdict {self.verdict!r}")
        if self.verdict != CONTESTED and self.basis not in ("name", "consumer", "both"):
            raise ValueError(f"{self.case_id}: basis must be name/consumer/both, got {self.basis!r}")


@dataclass(frozen=True)
class Predicate:
    """One censused claim predicate, with its measured divergence and its consumers."""

    pred_id: str
    #: `module:symbol` as written in the tree.
    module: str
    symbol: str
    line: int
    #: text -> claimed?  Raising is recorded, never swallowed into False.
    call: Callable[[str], bool]
    name_claims: str
    actually_answers: str
    consumer_assumes: str
    divergence: str
    #: `file:line` for every call site outside the defining module, measured by grep.
    consumers: tuple[str, ...]
    impl_kind: str
    severity: str
    blast: int
    cases: tuple[Case, ...]
    #: The consumer guard, quoted verbatim from the call site.
    guard_quote: str = ""

    @property
    def where(self) -> str:
        return f"{self.module}:{self.line}"

    @property
    def consumer_count(self) -> int:
        return len(self.consumers)


# ---------------------------------------------------------------------------------------------
# Adapters. Every predicate is normalized to text -> bool so one corpus drives all of them.
# A predicate that raises is reported as an ERROR row, never silently as False: `slice_families`
# already swallows probe exceptions in production, and the harness must not repeat that.
# ---------------------------------------------------------------------------------------------


def _fx_rate_lookup(text: str) -> bool:
    from core.currency_intent import fx_rate_lookup_intent

    return fx_rate_lookup_intent(text) is not None


def _currency_semantics(text: str) -> bool:
    from core.currency_intent import currency_semantics_present

    return bool(currency_semantics_present(text))


def _currency_transaction(text: str) -> bool:
    from core.currency_intent import currency_transaction_intent

    return bool(currency_transaction_intent(text))


def _clause_is_content_request(text: str) -> bool:
    from core.turn_ir import ClauseKind, classify_clause_kind

    return classify_clause_kind(text) is ClauseKind.KNOW


def _preempt(family_attr: str) -> Callable[[str], bool]:
    def call(text: str) -> bool:
        from core.agent_runtime import answer_coverage as ac

        return bool(ac.claim_may_preempt_turn(text, getattr(ac, family_attr)))

    return call


def _coverage_consumes(family_attr: str) -> Callable[[str], bool]:
    def call(text: str) -> bool:
        from core.agent_runtime import answer_coverage as ac

        return bool(ac.coverage_for(text, getattr(ac, family_attr)).consumed)

    return call


def _mixed_intent(text: str) -> bool:
    from core.agent_runtime.answer_coverage import is_mixed_intent_turn

    return bool(is_mixed_intent_turn(text))


def _claims_file_write_probe(text: str) -> bool:
    from core.agent_runtime.answer_coverage import _claims_file_write

    return bool(_claims_file_write(text))


def _live_data_mode(text: str) -> bool:
    from core.execution_requirements import requirements_for

    return requirements_for(text).answer_mode == "LIVE_DATA"


def _market_entity_candidates(text: str) -> bool:
    from tools.web.web_research import _extract_market_entity_candidates

    return bool(_extract_market_entity_candidates(text))


def _market_semantics(text: str) -> bool:
    from core.market_intent import market_semantics_present

    return bool(market_semantics_present(text))


def _market_quote_intent(text: str) -> bool:
    from core.market_intent import market_quote_intent_present

    return bool(market_quote_intent_present(text))


def _market_negated(text: str) -> bool:
    from core.market_intent import market_terms_are_negated

    return bool(market_terms_are_negated(text))


def _imperative_build(text: str) -> bool:
    from core.agent_runtime.build_request_intent import imperative_build_sentences

    return bool(imperative_build_sentences(text))


def _is_build_instruction(text: str) -> bool:
    from core.agent_runtime.build_request_intent import is_build_instruction

    return bool(is_build_instruction(text))


def _is_opted_out(text: str) -> bool:
    from core.agent_runtime.build_request_intent import is_opted_out

    return bool(is_opted_out(text))


def _is_deliberation(text: str) -> bool:
    from core.agent_runtime.build_request_intent import is_deliberation

    return bool(is_deliberation(text))


def _is_advice_question(text: str) -> bool:
    from core.agent_runtime.build_request_intent import is_advice_question

    return bool(is_advice_question(text))


def _agentic_build(text: str) -> bool:
    from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

    return bool(looks_like_agentic_build_request(text))


def _code_audit(text: str) -> bool:
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    return bool(looks_like_code_audit_request(text))


def _location_ask(text: str) -> bool:
    from core.action_receipt_location import looks_like_location_ask

    return bool(looks_like_location_ask(text))


def _machine_specs(text: str) -> bool:
    from core.agent_runtime.fast_paths_machine import looks_like_machine_specs_question

    return bool(looks_like_machine_specs_question(text))


def _machine_write(text: str) -> bool:
    from core.agent_runtime.fast_paths_machine import looks_like_safe_machine_write_request

    return bool(looks_like_safe_machine_write_request(text))


def _instructions_not_execution(text: str) -> bool:
    from core.instructional_request import asks_for_instructions_not_execution

    return bool(asks_for_instructions_not_execution(text))


def _impossible_action(text: str) -> bool:
    from core.capability_request_addressing import runtime_asked_for_impossible_action

    return bool(runtime_asked_for_impossible_action(text))


def _plain_task(text: str) -> bool:
    from core.plain_task_routing import is_plain_task

    return bool(is_plain_task(text))


def _ordinary_multipart(text: str) -> bool:
    from core.plain_task_routing import is_ordinary_multi_part_plain_task

    return bool(is_ordinary_multi_part_plain_task(text))


def _asks_about_contents(text: str) -> bool:
    from core.execution.constants import asks_about_contents

    return bool(asks_about_contents(text))


def _asks_about_processes(text: str) -> bool:
    from core.execution.constants import asks_about_running_processes

    return bool(asks_about_running_processes(text))


def _retrieval_prohibition(text: str) -> bool:
    from core.retrieval_constraints import analyze_retrieval_constraints

    return bool(analyze_retrieval_constraints(text).has_prohibition)


def _raw_output_contract(text: str) -> bool:
    from core.raw_output_contract import parse_raw_output_contract

    return parse_raw_output_contract(text) is not None


def _response_constraint(text: str) -> bool:
    from core.response_constraints import parse_response_constraint

    return parse_response_constraint(text) is not None


def _assistant_identity(text: str) -> bool:
    from core.user_identity_authority import classify_identity_question

    return bool(classify_identity_question(text).asks_assistant_identity)


# ---------------------------------------------------------------------------------------------
# Shared hard corpus. Every predicate is run over ALL of it and its claim rate recorded.
# These rows carry NO ground truth -- asserting what each of 30 predicates owes each of these
# would be inventing 900 verdicts. They are OBSERVATION: a narrow-named predicate that claims most
# of a corpus it has no business in is the signal, and the ranking says so as an observation.
# ---------------------------------------------------------------------------------------------

SHARED_HARD_CORPUS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("sh_quoted_fx", "the text `EUR/USD` is quoted here as plain text -- how many characters is it",
     (TAG_QUOTED,)),
    ("sh_neg_search", "Do not search the web. Just tell me the capital of France.", (TAG_NEGATION,)),
    ("sh_write_notes", "Write the summary to notes.txt", (TAG_WELLFORMED,)),
    ("sh_save_notes", "Save the summary to notes.txt", (TAG_WELLFORMED,)),
    ("sh_sloppy_market", "gimme btc n gold", (TAG_SLOPPY,)),
    ("sh_sloppy_deny", "dont search", (TAG_SLOPPY, TAG_NEGATION)),
    ("sh_sloppy_deny2", "no web pls", (TAG_SLOPPY, TAG_NEGATION)),
    ("sh_tell_both", "tell me both", (TAG_CONTINUATION,)),
    ("sh_what_about", "What about Silver?", (TAG_CONTINUATION,)),
    ("sh_cancel", "Run `ping -c 5 1.1.1.1` to test connectivity. WAIT. Cancel the terminal command "
                  "completely. Do not execute anything. Instead, count the consonants in the word "
                  "`CLOUDFLARE`. Output ONLY the integer.", (TAG_CANCEL, TAG_QUOTED, TAG_NEGATION)),
    ("sh_numbered", "1. Add 2+2\n2. Name a color\n3. Do not use the web",
     (TAG_NUMBERED, TAG_NEGATION)),
    ("sh_bulleted", "- price of gold\n- price of silver\n- do not write any file",
     (TAG_BULLETED, TAG_NEGATION)),
    ("sh_never_write", "Never write anything to disk. Explain how to write a file instead.",
     (TAG_NEGATION,)),
    ("sh_live_gold", "What is the current price of gold?", (TAG_WELLFORMED,)),
    ("sh_greeting", "hello", (TAG_WELLFORMED,)),
    ("sh_backtick_cmd", "Explain what `rm -rf /` does. Do not run it.", (TAG_QUOTED, TAG_NEGATION)),
)


# ---------------------------------------------------------------------------------------------
# THE CENSUS
# ---------------------------------------------------------------------------------------------

def _registry() -> tuple[Predicate, ...]:
    return (
        # -------------------------------------------------------------------------------------
        # The six measured seams
        # -------------------------------------------------------------------------------------
        Predicate(
            pred_id="fx_rate_lookup_intent",
            module="core/currency_intent.py", symbol="fx_rate_lookup_intent", line=1379,
            call=_fx_rate_lookup,
            name_claims="the pair whose CURRENT PRICE this turn asks for, or None",
            actually_answers="any turn containing two 3-letter tokens joined by / - to vs, plus any "
                             "of {rate rates exchange fx quote quoted price worth trading} or a "
                             "freshness word, ANYWHERE in the text including inside backticks",
            consumer_assumes="this turn is a live FX price request and the FX lane owns it",
            divergence=DIV_WIDER,
            consumers=("core/agent_runtime/turn_frontdoor.py:197",
                       "core/agent_runtime/fast_paths_currency.py:279",
                       "core/conductor/fresh_data_operations.py:40"),
            impl_kind="keyword list + regex frames",
            severity="S4", blast=3,
            guard_quote="request = fx_conversion_intent(text, ...) or fx_rate_lookup_intent(...)",
            cases=(
                Case("fx_c1", "What is the EUR/USD rate today?", CLAIM, "both",
                     "A pair plus an explicit rate word plus a freshness word. The docstring's "
                     "central case.", (TAG_WELLFORMED,)),
                Case("fx_c2", "gimme eur to usd rate", CLAIM, "both",
                     "Same request, sloppy. The predicate's job does not depend on punctuation.",
                     (TAG_SLOPPY,)),
                Case("fx_n1", "the text `EUR/USD` is quoted here as plain text -- how many "
                              "characters is it", NO_CLAIM, "name",
                     "The docstring says CURRENT PRICE. This asks for a character count of a "
                     "literal. `quoted` is a description of the typography, not a price cue.",
                     (TAG_QUOTED,)),
                Case("fx_n2", "Do not interpret TRY as a currency code. What does TRY mean in "
                              "English?", NO_CLAIM, "both",
                     "The turn denies the currency reading outright; a price lane claiming it "
                     "writes past a stated denial.", (TAG_NEGATION,)),
                Case("fx_n3", "I quoted her price of admission from EUR memory", CONTESTED, "-",
                     "Contains a rate word and a currency token in a non-financial sentence, but "
                     "the sentence is unnatural enough that a reasonable reader could call it "
                     "either. Not resolved.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="clause_kind_is_content_request",
            module="core/turn_ir.py", symbol="classify_clause_kind", line=176,
            call=_clause_is_content_request,
            name_claims="the KIND of this clause -- know / compute / observe / act / transform / "
                        "create / recall",
            actually_answers="a lookup of the clause's FIRST verb-shaped token in five frozensets, "
                             "after stripping a politeness prefix. Blind to negation: `Do not X` "
                             "has head `do`, which is in _KNOW_HEADS, so a PROHIBITION is typed "
                             "as a content request",
            consumer_assumes="this clause asks the answer for content, so satisfying it means "
                             "producing prose",
            divergence=DIV_DIFFERENT,
            consumers=("core/semantic/preflight.py:305",),
            impl_kind="verb lexicon (head-word frozensets)",
            severity="S3", blast=2,
            guard_quote="quoted_action_outside_quote = classify_clause_kind(...)",
            cases=(
                Case("ck_c1", "What is the capital of France?", CLAIM, "both",
                     "A bare factual question is the definition of a content request.",
                     (TAG_WELLFORMED,)),
                Case("ck_c2", "Tell me what a mutex is", CLAIM, "both",
                     "Imperative form of the same content request.", (TAG_WELLFORMED,)),
                Case("ck_n1", "Do not search the web.", NO_CLAIM, "both",
                     "A prohibition asks for NOTHING. Typing it as a content request means a "
                     "downstream stage tries to satisfy a denial by answering it.",
                     (TAG_NEGATION,)),
                Case("ck_n2", "Do not execute anything.", NO_CLAIM, "both",
                     "Same shape, taken verbatim from the tool-preemption corpus.",
                     (TAG_NEGATION,)),
                Case("ck_n3", "Never write anything to disk.", NO_CLAIM, "both",
                     "`Never` heads a prohibition. Not in any head set, so it falls to UNKNOWN "
                     "rather than KNOW -- the right verdict reached for the wrong reason, which "
                     "the harness records as a pass either way.", (TAG_NEGATION,)),
                Case("ck_n4", "Do not draft it.", NO_CLAIM, "both",
                     "Prohibits authoring. Reading it as a content request inverts it.",
                     (TAG_NEGATION,)),
                Case("ck_n5", "Cancel the terminal command completely.", NO_CLAIM, "both",
                     "A mid-turn cancellation is an instruction about the turn, not a request for "
                     "content.", (TAG_CANCEL,)),
                Case("ck_n6", "Do you have a moment?", CONTESTED, "-",
                     "Head `do` puts it in KNOW. Whether a discourse-management question is a "
                     "content request is a real disagreement, not a defect. Not resolved.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="claims_file_write",
            module="core/agent_runtime/answer_coverage.py", symbol="_claims_file_write", line=223,
            call=_claims_file_write_probe,
            name_claims="this clause asks for a FILE WRITE",
            actually_answers="`imperative_build_sentences(clause)` is non-empty -- BUILD INTENT, "
                             "which covers arithmetic, code-to-stdout and prose authoring with no "
                             "file anywhere in the clause",
            consumer_assumes="the FILE_WRITE family reads this clause, so FILE_WRITE is entitled "
                             "to coverage authority over it",
            divergence=DIV_DIFFERENT,
            consumers=("core/agent_runtime/answer_coverage.py:_PROBES (FAMILY_FILE_WRITE)",),
            impl_kind="delegation (to a differently-named predicate)",
            severity="S3", blast=3,
            guard_quote="(FAMILY_FILE_WRITE, _claims_file_write) in _PROBES",
            cases=(
                Case("fw_c1", "Write the summary to notes.txt", CLAIM, "both",
                     "Names a write verb and a concrete file. The name's central case.",
                     (TAG_WELLFORMED,)),
                Case("fw_c2", "Save the summary to notes.txt", CLAIM, "name",
                     "Identical request, different verb. A FILE-WRITE predicate that turns on "
                     "which synonym was typed is answering about the verb, not about the write.",
                     (TAG_WELLFORMED,)),
                Case("fw_c3", "append a line to config.yaml", CLAIM, "name",
                     "Append to a named file is a file write.", (TAG_SLOPPY,)),
                Case("fw_n1", "Add 2+2", NO_CLAIM, "name",
                     "Arithmetic. No file, no artifact, nothing to write.", (TAG_WELLFORMED,)),
                Case("fw_n2", "1. Add 2+2\n2. Name a color\n3. Do not use the web", NO_CLAIM, "name",
                     "A numbered list of a sum, a word and a prohibition. No file write in any "
                     "item.", (TAG_NUMBERED, TAG_NEGATION)),
                Case("fw_n3", "print the first 10 primes", NO_CLAIM, "name",
                     "Code to stdout. `print` is a build verb and not a file write.",
                     (TAG_WELLFORMED,)),
                Case("fw_n4", "Never write anything to disk. Explain how to write a file instead.",
                     NO_CLAIM, "both",
                     "The turn forbids the write outright and asks for prose. A FILE_WRITE claim "
                     "here is a claim to act past a denial.", (TAG_NEGATION,)),
                Case("fw_n5", "write a README for Thunder", CONTESTED, "-",
                     "The module's own comment says this is the case the probe exists for, and a "
                     "README is a file by convention -- but no path is named. Both readings "
                     "survive. Not resolved.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="preempt_turn__file_write",
            module="core/agent_runtime/answer_coverage.py", symbol="claim_may_preempt_turn", line=331,
            call=_preempt("FAMILY_FILE_WRITE"),
            name_claims="whether this family may return a whole-turn answer",
            actually_answers="no OTHER REGISTERED family claims a clause this one does not. "
                             "Consuming nothing at all still returns True",
            consumer_assumes="this family answered the turn, so returning its reply ends the turn",
            divergence=DIV_DIFFERENT,
            consumers=("core/web/api/runtime.py:1563",
                       "core/agent_runtime/turn_frontdoor.py:1122",
                       "core/agent_runtime/turn_frontdoor.py:947",
                       "core/agent_runtime/turn_frontdoor.py:1212",
                       "core/agent_runtime/turn_frontdoor.py:1737",
                       "core/agent_runtime/turn_frontdoor.py:128"),
            impl_kind="structural (set difference over registered probes)",
            severity="S3", blast=3,
            guard_quote="if claim_may_preempt_turn(user_text, FAMILY_ASSISTANT_IDENTITY): return "
                        "finalize_turn_trace(assistant_identity)",
            cases=(
                Case("pt_c1", "Write the summary to notes.txt", CLAIM, "both",
                     "One clause, and the file-write family is the one that reads it. Ending the "
                     "turn here is correct.", (TAG_WELLFORMED,)),
                Case("pt_n1", "hello", NO_CLAIM, "consumer",
                     "The file-write family consumes nothing in a greeting. Telling a consumer "
                     "whose next line ends the turn that it MAY is the divergence itself.",
                     (TAG_WELLFORMED,)),
                Case("pt_n2", "What is the current price of gold?", NO_CLAIM, "consumer",
                     "Nothing here is a file write. The consumer must not be told it may end "
                     "this turn.", (TAG_WELLFORMED,)),
                Case("pt_n3", "What about Silver?", NO_CLAIM, "consumer",
                     "A continuation of a market turn. No file write anywhere.",
                     (TAG_CONTINUATION,)),
                Case("pt_n4", "Do not search the web. Just tell me the capital of France.",
                     NO_CLAIM, "consumer",
                     "A prohibition plus a factual question. The file-write family owns neither.",
                     (TAG_NEGATION,)),
                Case("pt_n5", "- price of gold\n- price of silver\n- do not write any file",
                     NO_CLAIM, "consumer",
                     "A bulleted market list that explicitly forbids a file write.",
                     (TAG_BULLETED, TAG_NEGATION)),
            ),
        ),
        Predicate(
            pred_id="preempt_turn__currency",
            module="core/agent_runtime/answer_coverage.py", symbol="claim_may_preempt_turn", line=331,
            call=_preempt("FAMILY_CURRENCY"),
            name_claims="whether the currency family may return a whole-turn answer",
            actually_answers="same set difference, evaluated for the currency probe",
            consumer_assumes="the currency lane accounted for this turn and may end it",
            divergence=DIV_DIFFERENT,
            consumers=("core/agent_runtime/turn_frontdoor.py:1122",
                       "core/agent_runtime/turn_frontdoor.py:128"),
            impl_kind="structural (set difference over registered probes)",
            severity="S3", blast=3,
            guard_quote="if currency_coverage.covers_whole_turn and not "
                        "currency_binding_poisoned:",
            cases=(
                Case("ptc_c1", "1000 TRY to USD?", CLAIM, "both",
                     "One clause, currency family reads it, nothing else does.",
                     (TAG_WELLFORMED,)),
                Case("ptc_n1", "hello", NO_CLAIM, "consumer",
                     "Consumes nothing; must not be handed whole-turn authority.",
                     (TAG_WELLFORMED,)),
                Case("ptc_n2", "gimme btc n gold", NO_CLAIM, "consumer",
                     "A market list, not a currency turn.", (TAG_SLOPPY,)),
                Case("ptc_n3", "Run `ping -c 5 1.1.1.1` to test connectivity. WAIT. Cancel the "
                               "terminal command completely. Do not execute anything. Instead, "
                               "count the consonants in the word `CLOUDFLARE`. Output ONLY the "
                               "integer.", NO_CLAIM, "consumer",
                     "A cancellation plus a character count. The currency family owns no part "
                     "of it.", (TAG_CANCEL, TAG_QUOTED, TAG_NEGATION)),
            ),
        ),
        Predicate(
            pred_id="live_data_answer_mode",
            module="core/execution_requirements.py", symbol="requirements_for", line=294,
            call=_live_data_mode,
            name_claims="the execution requirements of this turn -- which toolsets may run and "
                        "whether external evidence is required",
            actually_answers="whether a live-data classifier recognized a fresh-value request "
                             "anywhere in the turn, including in a clause that is not the whole "
                             "turn",
            consumer_assumes="the live-data lane owns this turn and may terminate it",
            divergence=DIV_DIFFERENT,
            consumers=("core/execution/planner.py (mode dispatch)",
                       "core/agent_runtime/turn_frontdoor.py:1737"),
            impl_kind="delegation + keyword classifier",
            severity="S3", blast=2,
            guard_quote="live_info_status = None if (action_forbidden or not "
                        "live_info_coverage.covers_whole_turn) else ...",
            cases=(
                Case("ld_c1", "What is the current price of gold?", CLAIM, "both",
                     "A fresh value nothing in the process can know without fetching.",
                     (TAG_WELLFORMED,)),
                Case("ld_c2", "gimme btc n gold prices now", CLAIM, "both",
                     "The same two fresh values, sloppy. Freshness does not depend on grammar.",
                     (TAG_SLOPPY,)),
                Case("ld_n1", "What is the capital of France?", NO_CLAIM, "both",
                     "Stable knowledge. No toolset required.", (TAG_WELLFORMED,)),
                Case("ld_n2", "Do not search the web. Just tell me the capital of France.",
                     NO_CLAIM, "both",
                     "Stable knowledge under an explicit retrieval denial.", (TAG_NEGATION,)),
                Case("ld_n3", "the text `EUR/USD` is quoted here as plain text -- how many "
                              "characters is it", NO_CLAIM, "both",
                     "A character count over a literal. Nothing live.", (TAG_QUOTED,)),
                Case("ld_n4", "1. Add 2+2\n2. Name a color\n3. Do not use the web",
                     NO_CLAIM, "both",
                     "Arithmetic, a word, and a prohibition.", (TAG_NUMBERED, TAG_NEGATION)),
            ),
        ),
        Predicate(
            pred_id="market_entity_candidates",
            module="tools/web/web_research.py", symbol="_extract_market_entity_candidates", line=1158,
            call=_market_entity_candidates,
            name_claims="the market entities named in this text",
            actually_answers="any 1-3 token alphanumeric run following a price cue, with no check "
                             "that the run names an asset -- SHAPE ONLY",
            consumer_assumes="these strings are tradeable assets and may be rendered to the user "
                             "as row labels in a quote table",
            divergence=DIV_WIDER,
            consumers=("tools/web/web_research.py:1452 (same module, quote fallback)",
                       "tools/web/web_research.py:1473"),
            impl_kind="regex + shape admission",
            severity="S4", blast=2,
            guard_quote="if not market_quote_intent_present(lowered): ... candidates = "
                        "_extract_market_entity_candidates(...)",
            cases=(
                Case("me_c1", "price of gold and silver", CLAIM, "both",
                     "Two real assets after a price cue.", (TAG_WELLFORMED,)),
                Case("me_c2", "gimme btc n gold", CLAIM, "both",
                     "Two real assets, sloppy. Dropping these is the failure the digit-tolerant "
                     "shape check exists to avoid.", (TAG_SLOPPY,)),
                Case("me_n1", "tell me both", NO_CLAIM, "both",
                     "`both` is a pronoun. Rendering it as `| Tell Me Both |` in a quote table "
                     "presents a fabricated asset as real.", (TAG_CONTINUATION,)),
                Case("me_n2", "what is the price", NO_CLAIM, "both",
                     "A price cue with no entity at all.", (TAG_WELLFORMED,)),
                Case("me_n3", "do not quote me anything", NO_CLAIM, "both",
                     "A prohibition. No asset named.", (TAG_NEGATION,)),
                Case("me_n4", "price of Qwertycoin999xyz", CONTESTED, "-",
                     "The module's comment says an unresolved or emerging ticker must survive to "
                     "be reported as unresolved. Whether admitting it is right depends on what "
                     "the caller does next. Not resolved.", (TAG_WELLFORMED,)),
            ),
        ),
        # -------------------------------------------------------------------------------------
        # The named typers, censused from the same corpus
        # -------------------------------------------------------------------------------------
        Predicate(
            pred_id="coverage_consumes__currency",
            module="core/agent_runtime/answer_coverage.py", symbol="coverage_for(...).consumed",
            line=279, call=_coverage_consumes("FAMILY_CURRENCY"),
            name_claims="exactly which clauses this family accounts for",
            actually_answers="the clauses the currency probe returned True for. This half of the "
                             "return value is accurate; the divergence is that the SCOPE field "
                             "beside it does not depend on it",
            consumer_assumes="the clause ids the currency lane will answer",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/turn_frontdoor.py:1119",
                       "core/agent_runtime/turn_frontdoor.py:1139"),
            impl_kind="structural",
            severity="S3", blast=2,
            guard_quote="elif currency_coverage.consumed:",
            cases=(
                Case("cc_c1", "1000 TRY to USD?", CLAIM, "both",
                     "The currency probe reads this clause, so it appears in consumed.",
                     (TAG_WELLFORMED,)),
                Case("cc_n1", "hello", NO_CLAIM, "both",
                     "Nothing currency-shaped, so consumed must be empty.", (TAG_WELLFORMED,)),
                Case("cc_n2", "- price of gold\n- price of silver\n- do not write any file",
                     NO_CLAIM, "both",
                     "Commodity prices are the market family, not the currency family.",
                     (TAG_BULLETED, TAG_NEGATION)),
            ),
        ),
        Predicate(
            pred_id="is_mixed_intent_turn",
            module="core/agent_runtime/answer_coverage.py", symbol="is_mixed_intent_turn", line=336,
            call=_mixed_intent,
            name_claims="two or more DISTINCT families read two or more DIFFERENT clauses",
            actually_answers="exactly that, over the registered probe set only -- so a turn whose "
                             "clauses belong to families with no registered probe reads as "
                             "single-intent",
            consumer_assumes="no whole-turn fallback exists, so a fallback lane would answer part "
                             "and drop the rest",
            divergence=DIV_NARROWER,
            consumers=("core/agent_runtime/turn_frontdoor.py:944",),
            impl_kind="structural",
            severity="S3", blast=2,
            guard_quote="mixed_turn = is_mixed_intent_turn(effective_input)",
            cases=(
                Case("mi_c1", "What is TRY? Also audit this project and give me a verdict.",
                     CLAIM, "both",
                     "The docstring's own worked example: currency plus workspace audit.",
                     (TAG_WELLFORMED,)),
                Case("mi_n1", "What is the capital of France?", NO_CLAIM, "both",
                     "One clause, one intent.", (TAG_WELLFORMED,)),
                Case("mi_n2", "hello", NO_CLAIM, "both",
                     "No family reads it at all.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="currency_semantics_present",
            module="core/currency_intent.py", symbol="currency_semantics_present", line=1434,
            call=_currency_semantics,
            name_claims="whether this turn is about currency at all -- the domain-admission "
                        "question",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the currency domain is in play, so currency recognizers may run",
            divergence=DIV_NONE,
            consumers=("core/currency_comparison.py:16 (documented contract example)",),
            impl_kind="keyword list",
            severity="S2", blast=2,
            guard_quote="currency_semantics_present(...) -> domain admission",
            cases=(
                Case("cs_c1", "convert 1000 TRY to USD", CLAIM, "both",
                     "Names two currency codes and a conversion verb.", (TAG_WELLFORMED,)),
                Case("cs_n1", "hello", NO_CLAIM, "both", "No currency anywhere.",
                     (TAG_WELLFORMED,)),
                Case("cs_n2", "Do not interpret TRY as a currency code.", CONTESTED, "-",
                     "The docstring asks whether the turn is about currency AT ALL, and a "
                     "sentence about how to read a currency code arguably is. It is equally "
                     "arguable that admitting the domain past an explicit denial is what lets "
                     "the denial be overridden downstream. Both survive; not resolved.",
                     (TAG_NEGATION,)),
                Case("cs_n3", "What is the capital of France?", NO_CLAIM, "both",
                     "A world fact naming no currency, no amount and no code.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="currency_transaction_intent",
            module="core/currency_intent.py", symbol="currency_transaction_intent", line=632,
            call=_currency_transaction,
            name_claims="the user asks to MOVE money, not merely to name or convert it",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="this turn must NOT be claimed by the read-only currency fast path",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/fast_paths_currency.py:171",
                       "core/agent_runtime/fast_paths_currency.py:321",
                       "core/currency_comparison.py:496",
                       "core/currency_comparison.py:676"),
            impl_kind="verb lexicon + object check",
            severity="S5", blast=2,
            guard_quote="if currency_transaction_intent(text): return None  # decline the lane",
            cases=(
                Case("ct_c1", "send 500 USD to my brother", CLAIM, "both",
                     "A transfer verb with money as its object. The predicate's central case, "
                     "and the one whose miss lets a money-moving turn onto a read-only lane.",
                     (TAG_WELLFORMED,)),
                Case("ct_c2", "wire 200 EUR to account 12345", CLAIM, "both",
                     "Same, different verb.", (TAG_WELLFORMED,)),
                Case("ct_n1", "convert 1000 TRY to USD", NO_CLAIM, "both",
                     "A conversion is arithmetic, not a transfer.", (TAG_WELLFORMED,)),
                Case("ct_n2", "what is 1000 TRY worth", NO_CLAIM, "both",
                     "A valuation question.", (TAG_WELLFORMED,)),
                Case("ct_n3", "Do not send any money. Just tell me the EUR/USD rate.",
                     NO_CLAIM, "both",
                     "Contains a transfer verb with money as object, under an explicit "
                     "prohibition. A negation-blind lexicon reads the prohibition as the request.",
                     (TAG_NEGATION,)),
            ),
        ),
        Predicate(
            pred_id="market_semantics_present",
            module="core/market_intent.py", symbol="market_semantics_present", line=328,
            call=_market_semantics,
            name_claims="the REQUEST ITSELF is about price / quote / market value / trading",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="a market presence pass may run and a live plan may name assets",
            divergence=DIV_NONE,
            consumers=("core/followup_subject_continuity.py:272",
                       "core/followup_subject_continuity.py:301",
                       "core/execution_requirements.py:124",
                       "core/agent_runtime/live_data_plan.py:246"),
            impl_kind="keyword list + negation scan",
            severity="S4", blast=2,
            guard_quote="presence_pass_allowed = market_semantics_present(candidate_text)",
            cases=(
                Case("ms_c1", "What is the current price of gold?", CLAIM, "both",
                     "An explicit price request.", (TAG_WELLFORMED,)),
                Case("ms_c2", "gimme btc n gold", CLAIM, "both",
                     "Two assets, no price word. The market family owns it; the name says the "
                     "request is about market value.", (TAG_SLOPPY,)),
                Case("ms_n1", "hello", NO_CLAIM, "both", "Nothing market-shaped.",
                     (TAG_WELLFORMED,)),
                Case("ms_n2", "Do not perform market lookup or currency conversion.",
                     NO_CLAIM, "both",
                     "A prohibition naming market terms. The docstring says the REQUEST ITSELF "
                     "must be about markets; a prohibition is not a request.", (TAG_NEGATION,)),
                Case("ms_n3", "the price of freedom is eternal vigilance", CONTESTED, "-",
                     "Uses `price` metaphorically. Whether a keyword scan owes this case is "
                     "arguable. Not resolved.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="market_quote_intent_present",
            module="core/market_intent.py", symbol="market_quote_intent_present", line=372,
            call=_market_quote_intent,
            name_claims="the QUOTE recognizers' admission test",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="a quote fetch and a rendered quote table are authorized for this turn",
            divergence=DIV_NONE,
            consumers=("core/semantic_claim_authority.py:20",
                       "tools/web/web_research.py:1452",
                       "tools/web/web_research.py:1473"),
            impl_kind="keyword list + bare-recognizer fallback",
            severity="S4", blast=2,
            guard_quote="if not market_quote_intent_present(lowered): return None",
            cases=(
                Case("mq_c1", "price of gold and silver", CLAIM, "both",
                     "An explicit quote request.", (TAG_WELLFORMED,)),
                Case("mq_n1", "hello", NO_CLAIM, "both",
                     "A greeting names no asset and asks for no quote, so no fetch is authorized.",
                     (TAG_WELLFORMED,)),
                Case("mq_n2", "do not quote me anything", NO_CLAIM, "both",
                     "A prohibition containing the cue word.", (TAG_NEGATION,)),
                Case("mq_n3", "tell me both", NO_CLAIM, "consumer",
                     "A bare continuation. Admitting it is what let `| Tell Me Both |` render as "
                     "an asset row.", (TAG_CONTINUATION,)),
            ),
        ),
        Predicate(
            pred_id="market_terms_are_negated",
            module="core/market_intent.py", symbol="market_terms_are_negated", line=397,
            call=_market_negated,
            name_claims="the message names market terms and EVERY one of them is explicitly negated",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="authority to make a market claim is withheld for this turn",
            divergence=DIV_NONE,
            consumers=("core/semantic_claim_authority.py:299",),
            impl_kind="regex + negation cue scan",
            severity="S3", blast=2,
            guard_quote="return not market_terms_are_negated(body)",
            cases=(
                Case("mn_c1", "Do not perform market lookup or currency conversion.",
                     CLAIM, "both",
                     "Every market term in the message sits under one explicit prohibition.",
                     (TAG_NEGATION,)),
                Case("mn_c2", "no web pls, and dont quote gold", CLAIM, "both",
                     "Sloppy negation of the only market term present.",
                     (TAG_SLOPPY, TAG_NEGATION)),
                Case("mn_n1", "What is the current price of gold?", NO_CLAIM, "both",
                     "A plain request with no negation cue anywhere. Reporting True withholds "
                     "authority from a market claim the user asked for.", (TAG_WELLFORMED,)),
                Case("mn_n2", "Do not fetch silver, but do give me the price of gold.",
                     NO_CLAIM, "both",
                     "One market term is negated and one is not, so `EVERY` fails. Reporting "
                     "True here would withhold authority from a request the user made.",
                     (TAG_NEGATION,)),
            ),
        ),
        Predicate(
            pred_id="imperative_build_sentences",
            module="core/agent_runtime/build_request_intent.py", symbol="imperative_build_sentences",
            line=490, call=_imperative_build,
            name_claims="the sentences of this turn that are imperative BUILD requests",
            actually_answers="measured by this harness; see the report row. Note it splits and "
                             "re-emits text, so a numbered list yields fragments like `add 2+2 2.`",
            consumer_assumes="varies by call site -- FILE WRITE at answer_coverage.py:228, "
                             "mutation scope at mutation_scope.py:223, plan admission at "
                             "small_project_plan.py:486",
            divergence=DIV_DIFFERENT,
            consumers=("core/agent_runtime/answer_coverage.py:228",
                       "core/agent_runtime/intent_claims.py:328",
                       "core/agent_runtime/builder/mutation_scope.py:223",
                       "core/agent_runtime/builder/small_project_plan.py:486",
                       "core/agent_runtime/builder/named_file_build.py:188"),
            impl_kind="verb lexicon + sentence split",
            severity="S3", blast=2,
            guard_quote="return bool(imperative_build_sentences(clause))  # as _claims_file_write",
            cases=(
                Case("ib_c1", "Build me a CLI that renames files", CLAIM, "both",
                     "An imperative build request.", (TAG_WELLFORMED,)),
                Case("ib_c2", "write a python script that prints primes", CLAIM, "both",
                     "Same, artifact scope.", (TAG_WELLFORMED,)),
                Case("ib_n1", "Never write anything to disk. Explain how to write a file instead.",
                     NO_CLAIM, "both",
                     "The build is forbidden outright and prose is requested instead.",
                     (TAG_NEGATION,)),
                Case("ib_n2", "Would it be a good idea to build a CLI for this?",
                     NO_CLAIM, "both",
                     "An advice question, which `is_advice_question` exists to separate.",
                     (TAG_WELLFORMED,)),
                Case("ib_n3", "1. Add 2+2\n2. Name a color\n3. Do not use the web",
                     CONTESTED, "-",
                     "`Add` is a build verb and the list item is an imperative, so BUILD INTENT "
                     "arguably holds; a numbered arithmetic step is arguably not a build. The "
                     "defect measured here is the FILE-WRITE consumer, not this predicate. Not "
                     "resolved.", (TAG_NUMBERED,)),
            ),
        ),
        Predicate(
            pred_id="is_build_instruction",
            module="core/agent_runtime/build_request_intent.py", symbol="is_build_instruction",
            line=511, call=_is_build_instruction,
            name_claims="only for an instruction to produce code or files",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the builder lane may run and may write to the workspace",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/fast_paths_utility.py:820",
                       "core/agent_runtime/fast_paths_builder.py:25",
                       "core/agent_runtime/builder_facade.py:378",
                       "core/agent_runtime/builder_facade.py:499",
                       "core/agent_runtime/builder/named_file_build.py:171"),
            impl_kind="verb lexicon + object head check",
            severity="S5", blast=2,
            guard_quote="if build_request_intent.is_build_instruction(text):",
            cases=(
                Case("bi_c1", "write a python script that prints primes", CLAIM, "both",
                     "An instruction to produce code.", (TAG_WELLFORMED,)),
                Case("bi_n1", "What is the capital of France?", NO_CLAIM, "both",
                     "A factual question produces nothing.", (TAG_WELLFORMED,)),
                Case("bi_n2", "Never write anything to disk. Explain how to write a file instead.",
                     NO_CLAIM, "both",
                     "An explicit opt-out. A builder lane admitted here writes past a denial.",
                     (TAG_NEGATION,)),
                Case("bi_n3", "Would it be a good idea to build a CLI for this?",
                     NO_CLAIM, "both", "Advice, not an instruction.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="is_opted_out",
            module="core/agent_runtime/build_request_intent.py", symbol="is_opted_out", line=484,
            call=_is_opted_out,
            name_claims="the operator explicitly said not to write anything",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="every build lane must decline. This is the denial-detection "
                             "predicate, so a false NO_CLAIM is a write past a denial",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/fast_paths_utility.py:806",
                       "core/agent_runtime/fast_paths_builder.py:18",
                       "core/agent_runtime/fast_paths_builder.py:96",
                       "core/agent_runtime/fast_paths_skill.py:108",
                       "core/agent_runtime/builder_facade.py:367",
                       "core/agent_runtime/builder_facade.py:483",
                       "core/agent_runtime/builder/small_project_plan.py:483",
                       "core/agent_runtime/builder/named_file_build.py:186"),
            impl_kind="keyword list",
            severity="S5", blast=2,
            guard_quote="if build_request_intent.is_deliberation(text) or "
                        "build_request_intent.is_opted_out(text): return False",
            cases=(
                Case("oo_c1", "Do not write any files. Just explain the approach.", CLAIM, "both",
                     "An explicit denial in plain form.", (TAG_NEGATION,)),
                Case("oo_c2", "Never write anything to disk. Explain how to write a file instead.",
                     CLAIM, "both", "`Never` form of the same denial.", (TAG_NEGATION,)),
                Case("oo_c3", "dont write any file pls", CLAIM, "both",
                     "The same denial, sloppy. A denial detector that needs an apostrophe is not "
                     "a denial detector.", (TAG_SLOPPY, TAG_NEGATION)),
                Case("oo_c4", "- price of gold\n- price of silver\n- do not write any file",
                     CLAIM, "both",
                     "The denial is the third bullet. A predicate that only reads the first "
                     "sentence misses it.", (TAG_BULLETED, TAG_NEGATION)),
                Case("oo_n1", "write a python script that prints primes", NO_CLAIM, "both",
                     "No denial. Reporting True would refuse work the user asked for.",
                     (TAG_WELLFORMED,)),
                Case("oo_n2", "Write the summary to notes.txt", NO_CLAIM, "both",
                     "An explicit request to write.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="is_deliberation",
            module="core/agent_runtime/build_request_intent.py", symbol="is_deliberation", line=464,
            call=_is_deliberation,
            name_claims="the operator is discussing building rather than asking for it",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="every build lane must decline",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/fast_paths_utility.py:806",
                       "core/agent_runtime/fast_paths_builder.py:18",
                       "core/agent_runtime/fast_paths_builder.py:96",
                       "core/agent_runtime/fast_paths_skill.py:127",
                       "core/agent_runtime/builder_facade.py:487",
                       "core/agent_runtime/builder/small_project_plan.py:483",
                       "core/agent_runtime/builder/named_file_build.py:186"),
            impl_kind="keyword list",
            severity="S3", blast=2,
            guard_quote="if build_request_intent.is_deliberation(lowered):",
            cases=(
                Case("dl_c1", "I'm thinking about whether we should build a CLI for this",
                     CLAIM, "both", "Discussion, not a request.", (TAG_WELLFORMED,)),
                Case("dl_n1", "Build me a CLI that renames files", NO_CLAIM, "both",
                     "A direct request. Reading it as discussion drops the work silently.",
                     (TAG_WELLFORMED,)),
                Case("dl_n2", "Write the summary to notes.txt", NO_CLAIM, "both",
                     "A direct imperative with a named file. Nothing is being discussed.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="is_advice_question",
            module="core/agent_runtime/build_request_intent.py", symbol="is_advice_question",
            line=470, call=_is_advice_question,
            name_claims="the operator is asking whether something is a GOOD IDEA, not for it",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="answer with prose, do not build",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/fast_paths_utility.py:1237",),
            impl_kind="keyword list",
            severity="S2", blast=2,
            guard_quote="if build_request_intent.is_advice_question(text):",
            cases=(
                Case("aq_c1", "Would it be a good idea to build a CLI for this?", CLAIM, "both",
                     "The docstring's central case.", (TAG_WELLFORMED,)),
                Case("aq_n1", "Build me a CLI that renames files", NO_CLAIM, "both",
                     "A request, not a question about one.", (TAG_WELLFORMED,)),
                Case("aq_n2", "What is the capital of France?", NO_CLAIM, "both",
                     "A factual question, not an advice question.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="looks_like_agentic_build_request",
            module="core/agent_runtime/fast_paths_utility.py",
            symbol="looks_like_agentic_build_request", line=791, call=_agentic_build,
            name_claims="a do-the-work / build-an-app / build-and-verify-in-the-workspace request",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the agentic builder owns the turn, may write files and may skip "
                             "other dispatch",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/turn_frontdoor.py:1300",
                       "core/agent_runtime/answer_coverage.py:216",
                       "core/agent_runtime/builder/support.py:140",
                       "core/agent_runtime/turn_dispatch.py:325"),
            impl_kind="keyword list",
            severity="S5", blast=3,
            guard_quote="if looks_like_agentic_build_request(effective_input) and str(...)",
            cases=(
                Case("ab_c1", "Build a small FastAPI app in the workspace and verify it runs",
                     CLAIM, "both", "Build plus verify in the workspace.", (TAG_WELLFORMED,)),
                Case("ab_n1", "hello", NO_CLAIM, "both",
                     "A greeting names no artifact and no workspace, and this lane may write files.",
                     (TAG_WELLFORMED,)),
                Case("ab_n2", "Do not write any files. Just explain the approach.",
                     NO_CLAIM, "both",
                     "An explicit opt-out; a lane that may write must not be admitted.",
                     (TAG_NEGATION,)),
                Case("ab_n3", "What is the current price of gold?", NO_CLAIM, "both",
                     "A market question needs a fetch, not a build. Claiming it here would skip "
                     "the dispatch that reaches the live lane.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="looks_like_code_audit_request",
            module="core/agent_runtime/workspace_audit.py", symbol="looks_like_code_audit_request",
            line=370, call=_code_audit,
            name_claims="a request to audit code in the workspace",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the workspace-audit family reads this clause and may take the turn",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/answer_coverage.py:210",
                       "core/agent_runtime/turn_frontdoor.py:948"),
            impl_kind="keyword list",
            severity="S3", blast=3,
            guard_quote="if audit_coverage.covers_whole_turn: workspace_audit = "
                        "agent._maybe_handle_workspace_audit_request(...)",
            cases=(
                Case("ca_c1", "Audit this project and give me a verdict", CLAIM, "both",
                     "The docstring's central case, taken from the consumer's own comment.",
                     (TAG_WELLFORMED,)),
                Case("ca_n1", "hello", NO_CLAIM, "both",
                     "A greeting names no code and no workspace, and this family can end the turn.",
                     (TAG_WELLFORMED,)),
                Case("ca_n2", "What is the capital of France?", NO_CLAIM, "both",
                     "A world fact. An audit claim here answers with a workspace verdict the user "
                     "never asked for -- the failure the consumer comment already records.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="looks_like_location_ask",
            module="core/action_receipt_location.py", symbol="looks_like_location_ask", line=537,
            call=_location_ask,
            name_claims="the user asks WHERE a receipt or artifact is",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the receipt-location family reads this clause and may take the turn",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/answer_coverage.py:204",
                       "core/agent_runtime/fast_paths_receipt_location.py:57"),
            impl_kind="keyword list",
            severity="S3", blast=3,
            guard_quote="if not looks_like_location_ask(user_input): return None",
            cases=(
                Case("la_c1", "Where did you save that file?", CLAIM, "both",
                     "A direct where-is question about an artifact this runtime produced.",
                     (TAG_WELLFORMED,)),
                Case("la_n1", "hello", NO_CLAIM, "both",
                     "A greeting asks where nothing is, and this family can end the turn.",
                     (TAG_WELLFORMED,)),
                Case("la_n2", "What is the capital of France?", NO_CLAIM, "both",
                     "A where-shaped fact about the world, not about a receipt. A location lane "
                     "claiming it answers the wrong question entirely.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="looks_like_machine_specs_question",
            module="core/agent_runtime/fast_paths_machine.py",
            symbol="looks_like_machine_specs_question", line=317, call=_machine_specs,
            name_claims="the sentence asks what hardware this machine IS",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the machine-specs family claims the turn",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/intent_claims.py:111",),
            impl_kind="keyword list",
            severity="S2", blast=2,
            guard_quote="return IntentClaim(FAMILY_MACHINE_SPECS) if "
                        "looks_like_machine_specs_question(text) else None",
            cases=(
                Case("mspec_c1", "How much RAM does this machine have?", CLAIM, "both",
                     "A hardware-identity question.", (TAG_WELLFORMED,)),
                Case("mspec_n1", "hello", NO_CLAIM, "both", "No hardware question.",
                     (TAG_WELLFORMED,)),
                Case("mspec_n2", "How much does a MacBook cost?", NO_CLAIM, "both",
                     "A price question about a product, not about THIS machine.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="looks_like_safe_machine_write_request",
            module="core/agent_runtime/fast_paths_machine.py",
            symbol="looks_like_safe_machine_write_request", line=463, call=_machine_write,
            name_claims="a machine write this runtime may perform safely",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="a filesystem write outside the workspace is authorized",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/fast_path_facade.py:145",),
            impl_kind="keyword list + path check",
            severity="S5", blast=2,
            guard_quote="return agent_fast_paths.looks_like_safe_machine_write_request(user_input)",
            cases=(
                Case("mw_c1", "Create a folder called reports on my Desktop", CLAIM, "both",
                     "A concrete write under an allowed home folder.", (TAG_WELLFORMED,)),
                Case("mw_n1", "hello", NO_CLAIM, "both",
                     "A greeting names no path and no write. Authorizing a filesystem write on it "
                     "is an unrequested side effect.", (TAG_WELLFORMED,)),
                Case("mw_n2", "Never write anything to disk. Explain how to write a file instead.",
                     NO_CLAIM, "both",
                     "An explicit denial. A write authorized here is an S5 write past a denial.",
                     (TAG_NEGATION,)),
                Case("mw_n3", "Delete everything in /System", NO_CLAIM, "both",
                     "Destructive and outside every allowed root. `safe` in the name must "
                     "exclude it.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="asks_for_instructions_not_execution",
            module="core/instructional_request.py", symbol="asks_for_instructions_not_execution",
            line=137, call=_instructions_not_execution,
            name_claims="asks HOW to do something rather than asking for it to be done",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="do not execute; answer with instructions",
            divergence=DIV_NONE,
            consumers=("core/execution/planner.py:417",
                       "core/task_router.py:1844",
                       "core/agent_runtime/fast_paths_utility.py:753",
                       "core/agent_runtime/fast_paths_machine.py:474"),
            impl_kind="keyword list",
            severity="S5", blast=2,
            guard_quote="if asks_for_instructions_not_execution(text): return <no-execution route>",
            cases=(
                Case("ie_c1", "How do I delete a file in Python?", CLAIM, "both",
                     "A how-to question. Executing it would be acting on a request for prose.",
                     (TAG_WELLFORMED,)),
                Case("ie_c2", "Never write anything to disk. Explain how to write a file instead.",
                     CLAIM, "both",
                     "Explicitly asks for the explanation and forbids the act.", (TAG_NEGATION,)),
                Case("ie_n1", "Delete config.yaml", NO_CLAIM, "both",
                     "A direct instruction to act. Routing it to prose drops the work.",
                     (TAG_WELLFORMED,)),
                Case("ie_n2", "Write the summary to notes.txt", NO_CLAIM, "both",
                     "A direct instruction to act.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="runtime_asked_for_impossible_action",
            module="core/capability_request_addressing.py",
            symbol="runtime_asked_for_impossible_action", line=271, call=_impossible_action,
            name_claims="the user asked THIS runtime to perform or claim a physically impossible "
                        "action",
            actually_answers="one of TWELVE hard-coded phrases (teleport / read my mind / cook "
                             "dinner / drive over / smell this / taste this / touch this / hack a "
                             "bank / steal a password / be physically there / physically cook / "
                             "mind read) appears and is addressed here. Anything physically "
                             "impossible that is not on the list answers False",
            consumer_assumes="answer by naming the impossibility instead of attempting it",
            divergence=DIV_NARROWER,
            consumers=("core/execution/capabilities.py:244",),
            impl_kind="keyword list + command-opening check",
            severity="S3", blast=2,
            guard_quote="if runtime_asked_for_impossible_action(text):",
            cases=(
                Case("ia_c1", "Can you teleport my laptop to Berlin?", CLAIM, "both",
                     "The docstring's own worked example, and on the phrase list.",
                     (TAG_WELLFORMED,)),
                Case("ia_c2", "Physically drive my car to the shop", CLAIM, "name",
                     "Driving a car is physically impossible for a software runtime, and the "
                     "sentence is an imperative addressed here. `drive over` is on the phrase "
                     "list and `drive my car` is not, which is the whole divergence: the "
                     "predicate answers about twelve phrases, not about physical possibility.",
                     (TAG_WELLFORMED,)),
                Case("ia_n1", "Write the summary to notes.txt", NO_CLAIM, "both",
                     "Entirely possible. Declaring it impossible refuses real work.",
                     (TAG_WELLFORMED,)),
                Case("ia_n2", "Explain how a car engine works", NO_CLAIM, "both",
                     "A description of a physical thing is not a request to do it.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="is_plain_task",
            module="core/plain_task_routing.py", symbol="is_plain_task", line=328, call=_plain_task,
            name_claims="one plain, tool-free model answer settles this turn",
            actually_answers="`plain_task_kind(text)` is one of SIX named generation kinds -- "
                             "multi_part_qa / translation / rewrite / summary / simple_code / "
                             "explanation. A bare factual question is none of them and answers "
                             "False, so the name over-promises the delegate",
            consumer_assumes="nothing measurable: the guard at channel_actions.py:43 is subsumed "
                             "by line 45, so forcing this predicate True or False leaves every "
                             "outcome unchanged (measured, see test_is_plain_task_guard_is_inert)",
            divergence=DIV_NARROWER,
            consumers=("core/channel_actions.py:43",),
            impl_kind="keyword list + structural",
            severity="S3", blast=2,
            guard_quote="if is_plain_task(raw) and not _EXPLICIT_CHANNEL_ACTION_RE.search(raw):",
            cases=(
                Case("pl_c1", "Translate this to French: hello", CLAIM, "both",
                     "Translation is one of the six kinds the delegate names, and one tool-free "
                     "answer settles it.", (TAG_WELLFORMED,)),
                Case("pl_c2", "Summarize this paragraph: the cat sat on the mat and then left",
                     CLAIM, "both",
                     "Summary is another of the six named kinds.", (TAG_WELLFORMED,)),
                Case("pl_x1", "What is the capital of France?", CONTESTED, "-",
                     "The NAME says any turn one tool-free answer settles, which this is. The "
                     "delegate's docstring restricts the question to six generation kinds, which "
                     "this is not. Both readings survive the text; the gap between them is the "
                     "divergence, and picking a side here would hide it.", (TAG_WELLFORMED,)),
                Case("pl_n1", "What is the current price of gold?", NO_CLAIM, "both",
                     "Needs a live fetch. Calling it plain routes a fresh-value question to a "
                     "model that will invent the number.", (TAG_WELLFORMED,)),
                Case("pl_n2", "Build a small FastAPI app in the workspace and verify it runs",
                     NO_CLAIM, "both", "Needs tools and files.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="is_ordinary_multi_part_plain_task",
            module="core/plain_task_routing.py", symbol="is_ordinary_multi_part_plain_task",
            line=271, call=_ordinary_multipart,
            name_claims="every part belongs in one complete, tool-free model answer",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="skip multi-request planning; one model answer covers the turn",
            divergence=DIV_NONE,
            consumers=("core/execution/planner.py:372",
                       "core/agent_runtime/turn_planner.py:238",
                       "core/agent_runtime/memory_runtime.py:99",
                       "core/conductor/planner.py:1267"),
            impl_kind="structural + keyword list",
            severity="S3", blast=2,
            guard_quote="if is_ordinary_multi_part_plain_task(text) or not "
                        "turn_may_hold_several_requests(text):",
            cases=(
                Case("mp_c1", "Name a color, and tell me the capital of France.", CLAIM, "both",
                     "Two parts, both tool-free.", (TAG_WELLFORMED,)),
                Case("mp_n1", "Name a color, and tell me the current price of gold.",
                     NO_CLAIM, "both",
                     "One part needs a live fetch, so one tool-free answer cannot cover the turn.",
                     (TAG_WELLFORMED,)),
                Case("mp_n2", "1. Add 2+2\n2. Name a color\n3. Write it to notes.txt",
                     NO_CLAIM, "both",
                     "The third item needs a file write.", (TAG_NUMBERED,)),
            ),
        ),
        Predicate(
            pred_id="asks_about_contents",
            module="core/execution/constants.py", symbol="asks_about_contents", line=1328,
            call=_asks_about_contents,
            name_claims="the sentence asks what is INSIDE a place rather than where the place is",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="a directory listing answers this turn",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/turn_frontdoor.py:1903",),
            impl_kind="keyword list",
            severity="S2", blast=2,
            guard_quote="return asks_about_contents(text)",
            cases=(
                Case("ac_c1", "What is in my Downloads folder?", CLAIM, "both",
                     "Asks for the contents of a place.", (TAG_WELLFORMED,)),
                Case("ac_n1", "Where is my Downloads folder?", NO_CLAIM, "both",
                     "The docstring's own contrast case: where, not what is inside.",
                     (TAG_WELLFORMED,)),
                Case("ac_n2", "hello", NO_CLAIM, "both",
                     "A greeting names no place, so there is nothing whose contents could be "
                     "listed.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="asks_about_running_processes",
            module="core/execution/constants.py", symbol="asks_about_running_processes", line=1133,
            call=_asks_about_processes,
            name_claims="the sentence is ABOUT running processes",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the list-processes family claims the turn",
            divergence=DIV_NONE,
            consumers=("core/agent_runtime/intent_claims.py:119",),
            impl_kind="keyword list",
            severity="S2", blast=2,
            guard_quote="return IntentClaim(FAMILY_LIST_PROCESSES) if "
                        "asks_about_running_processes(text) else None",
            cases=(
                Case("rp_c1", "What processes are running right now?", CLAIM, "both",
                     "Directly about running processes.", (TAG_WELLFORMED,)),
                Case("rp_n1", "hello", NO_CLAIM, "both", "Not about processes.",
                     (TAG_WELLFORMED,)),
                Case("rp_n2", "Explain how process scheduling works", NO_CLAIM, "both",
                     "A conceptual question about processes, not about THIS machine's.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="retrieval_prohibition",
            module="core/retrieval_constraints.py", symbol="analyze_retrieval_constraints", line=272,
            call=_retrieval_prohibition,
            name_claims="the retrieval families this turn explicitly forbids",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="a forbidden toolset must not run, and a live claim must not be made",
            divergence=DIV_NONE,
            consumers=("core/execution_requirements.py:~300 (constraints.has_prohibition)",
                       "core/execution_requirements.py:~310 (constraints.forbids)"),
            impl_kind="regex + negative clause spans",
            severity="S5", blast=2,
            guard_quote="unrestricted_live_data = _live_data_classification(candidate_text) if "
                        "constraints.has_prohibition else None",
            cases=(
                Case("rc_c1", "Do not search the web. Just tell me the capital of France.",
                     CLAIM, "both", "An explicit retrieval prohibition.", (TAG_NEGATION,)),
                Case("rc_c2", "no web pls", CLAIM, "both",
                     "The same prohibition, sloppy. A denial detector that needs formal prose "
                     "lets the denial through.", (TAG_SLOPPY, TAG_NEGATION)),
                Case("rc_c3", "dont search", CLAIM, "both",
                     "Sloppy, no apostrophe, no object.", (TAG_SLOPPY, TAG_NEGATION)),
                Case("rc_c4", "1. Add 2+2\n2. Name a color\n3. Do not use the web",
                     CLAIM, "both",
                     "The prohibition is the third numbered item.", (TAG_NUMBERED, TAG_NEGATION)),
                Case("rc_n1", "What is the current price of gold?", NO_CLAIM, "both",
                     "No prohibition. Reporting one would suppress a fetch the user needs.",
                     (TAG_WELLFORMED,)),
                Case("rc_n2", "Search the web for the price of gold", NO_CLAIM, "both",
                     "An explicit request TO retrieve.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="raw_output_contract",
            module="core/raw_output_contract.py", symbol="parse_raw_output_contract", line=777,
            call=_raw_output_contract,
            name_claims="the raw-output shape this turn stipulates, or None",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the reply must be reshaped to this contract before it ships",
            divergence=DIV_NONE,
            consumers=("core/response_constraints.py (reconcile)",
                       "core/agent_runtime (raw output enforcement)"),
            impl_kind="regex + structural directive parse",
            severity="S1", blast=1,
            guard_quote="contract = parse_raw_output_contract(user_text)",
            cases=(
                Case("ro_c1", "Output ONLY the integer.", CLAIM, "both",
                     "An explicit raw-output stipulation.", (TAG_WELLFORMED,)),
                Case("ro_c2", "Run `ping -c 5 1.1.1.1` to test connectivity. WAIT. Cancel the "
                              "terminal command completely. Do not execute anything. Instead, "
                              "count the consonants in the word `CLOUDFLARE`. Output ONLY the "
                              "integer.", CLAIM, "both",
                     "The same stipulation surviving a mid-turn cancellation.",
                     (TAG_CANCEL, TAG_QUOTED, TAG_NEGATION)),
                Case("ro_n1", "What is the capital of France?", NO_CLAIM, "both",
                     "No shape stipulated.", (TAG_WELLFORMED,)),
                Case("ro_n2", "hello", NO_CLAIM, "both", "No shape stipulated.",
                     (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="response_constraint",
            module="core/response_constraints.py", symbol="parse_response_constraint", line=633,
            call=_response_constraint,
            name_claims="the length or shape constraint this turn puts on the answer",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the answer must satisfy this constraint or be retried",
            divergence=DIV_NONE,
            consumers=("core/response_constraints.py:768 check_response_constraint",
                       "core/agent_runtime (retry instruction)"),
            impl_kind="regex",
            severity="S1", blast=1,
            guard_quote="constraint = parse_response_constraint(user_text)",
            cases=(
                Case("rs_c1", "Answer in exactly three words.", CLAIM, "both",
                     "An explicit answer-shape constraint.", (TAG_WELLFORMED,)),
                Case("rs_n1", "hello", NO_CLAIM, "both",
                     "No length or shape is stipulated, so any parsed constraint is invented and "
                     "would reshape an answer the user never bounded.", (TAG_WELLFORMED,)),
                Case("rs_n2", "What is the current price of gold?", NO_CLAIM, "both",
                     "A plain question with no stipulated answer shape.", (TAG_WELLFORMED,)),
            ),
        ),
        Predicate(
            pred_id="assistant_identity_question",
            module="core/user_identity_authority.py", symbol="classify_identity_question", line=493,
            call=_assistant_identity,
            name_claims="the turn asks about the ASSISTANT's identity",
            actually_answers="measured by this harness; see the report row",
            consumer_assumes="the identity family answers, and at runtime.py:1563 may END the turn",
            divergence=DIV_NONE,
            consumers=("core/web/api/runtime.py:1563",
                       "core/agent_runtime/answer_coverage.py:198"),
            impl_kind="keyword list",
            severity="S3", blast=3,
            guard_quote="if claim_may_preempt_turn(user_text, FAMILY_ASSISTANT_IDENTITY): return "
                        "finalize_turn_trace(assistant_identity)",
            cases=(
                Case("ai_c1", "What is your name?", CLAIM, "both",
                     "The consumer's own worked example.", (TAG_WELLFORMED,)),
                Case("ai_n1", "What is my name?", NO_CLAIM, "both",
                     "The USER's identity, not the assistant's. Answering with the assistant's "
                     "name answers a different question.", (TAG_WELLFORMED,)),
                Case("ai_n2", "hello", NO_CLAIM, "both", "No identity question.",
                     (TAG_WELLFORMED,)),
            ),
        ),
    )


PREDICATES: tuple[Predicate, ...] = _registry()


# ---------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------

ERROR = "ERROR"


def _observe(pred: Predicate, text: str) -> tuple[Any, str | None]:
    try:
        return bool(pred.call(text)), None
    except Exception as exc:  # a raising predicate is a finding, not a False
        return ERROR, f"{type(exc).__name__}: {exc}"


@dataclass
class PredicateResult:
    pred_id: str
    should_claim_total: int = 0
    should_claim_missed: int = 0
    should_not_total: int = 0
    should_not_claimed: int = 0
    contested: int = 0
    errors: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    shared_claim_rate: float = 0.0
    shared_claimed: tuple[str, ...] = ()

    @property
    def counted(self) -> int:
        return self.should_claim_total + self.should_not_total

    @property
    def divergent_cases(self) -> int:
        return self.should_claim_missed + self.should_not_claimed


def run_conformance(
    predicates: Sequence[Predicate] = PREDICATES,
    shared: Sequence[tuple[str, str, tuple[str, ...]]] = SHARED_HARD_CORPUS,
) -> dict[str, Any]:
    """Run every predicate over its directional cases and the shared hard corpus.

    Returns the machine-readable report. Does not assert. Reporting is the point: the divergence
    measured here is the state of the tree as it stands, and a harness that failed on day one
    would be deleted by the first person who ran it.
    """
    rows: list[dict[str, Any]] = []
    for pred in predicates:
        res = PredicateResult(pred_id=pred.pred_id)
        for case in pred.cases:
            got, err = _observe(pred, case.text)
            if err is not None:
                res.errors += 1
                res.failures.append(
                    {"case_id": case.case_id, "verdict": case.verdict, "got": ERROR,
                     "error": err, "text": case.text, "basis": case.basis, "why": case.why,
                     "tags": list(case.tags)}
                )
                continue
            if case.verdict == CONTESTED:
                res.contested += 1
                continue
            if case.verdict == CLAIM:
                res.should_claim_total += 1
                if got is not True:
                    res.should_claim_missed += 1
                    res.failures.append(
                        {"case_id": case.case_id, "verdict": CLAIM, "got": False,
                         "direction": "MISSED_CLAIM", "text": case.text,
                         "basis": case.basis, "why": case.why, "tags": list(case.tags)}
                    )
            else:
                res.should_not_total += 1
                if got is True:
                    res.should_not_claimed += 1
                    res.failures.append(
                        {"case_id": case.case_id, "verdict": NO_CLAIM, "got": True,
                         "direction": "FALSE_CLAIM", "text": case.text,
                         "basis": case.basis, "why": case.why, "tags": list(case.tags)}
                    )

        claimed_shared: list[str] = []
        for sid, text, _tags in shared:
            got, err = _observe(pred, text)
            if got is True:
                claimed_shared.append(sid)
        res.shared_claimed = tuple(claimed_shared)
        res.shared_claim_rate = (len(claimed_shared) / len(shared)) if shared else 0.0

        score = (
            _SEVERITY_WEIGHT[pred.severity]
            * max(pred.consumer_count, 1)
            * pred.blast
            * (1 + res.divergent_cases)
        )
        rows.append(
            {
                "pred_id": pred.pred_id,
                "where": pred.where,
                "symbol": pred.symbol,
                "name_claims": pred.name_claims,
                "actually_answers": pred.actually_answers,
                "consumer_assumes": pred.consumer_assumes,
                "divergence": pred.divergence,
                "impl_kind": pred.impl_kind,
                "severity": pred.severity,
                "blast": pred.blast,
                "blast_meaning": BLAST[pred.blast],
                "consumers": list(pred.consumers),
                "consumer_count": pred.consumer_count,
                "guard_quote": pred.guard_quote,
                "should_claim_total": res.should_claim_total,
                "missed_claims": res.should_claim_missed,
                "should_not_total": res.should_not_total,
                "false_claims": res.should_not_claimed,
                "contested_excluded": res.contested,
                "errors": res.errors,
                "counted_cases": res.counted,
                "divergent_cases": res.divergent_cases,
                "shared_claim_rate": round(res.shared_claim_rate, 3),
                "shared_claimed": list(res.shared_claimed),
                "rank_score": score,
                "failures": res.failures,
            }
        )

    rows.sort(key=lambda r: (-r["rank_score"], r["pred_id"]))
    report = {
        "predicates_censused": len(rows),
        "different_question_divergence": sum(
            1 for r in rows if r["divergence"] == DIV_DIFFERENT
        ),
        "directional_cases": sum(r["counted_cases"] for r in rows),
        "contested_excluded": sum(r["contested_excluded"] for r in rows),
        "divergent_cases": sum(r["divergent_cases"] for r in rows),
        "false_claims": sum(r["false_claims"] for r in rows),
        "missed_claims": sum(r["missed_claims"] for r in rows),
        "errors": sum(r["errors"] for r in rows),
        "shared_corpus_size": len(shared),
        "severity_ladder": SEVERITY,
        "blast_ladder": BLAST,
        "rows": rows,
    }
    return report


def report_path() -> str:
    override = os.environ.get("VOOL_PREDICATE_REPORT")
    if override:
        return override
    return os.path.join(tempfile.gettempdir(), "vool_claim_predicate_divergence.json")


def write_report(report: dict[str, Any], path: str | None = None) -> str:
    dest = path or report_path()
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, sort_keys=False)
    return dest


def render_table(report: dict[str, Any]) -> str:
    """The human-readable ranked divergence table."""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 118)
    lines.append(
        f"CLAIM PREDICATE DIVERGENCE -- {report['predicates_censused']} predicates censused, "
        f"{report['different_question_divergence']} with DIFFERENT QUESTION divergence"
    )
    lines.append(
        f"  directional cases: {report['directional_cases']} counted, "
        f"{report['contested_excluded']} CONTESTED excluded  |  "
        f"divergent: {report['divergent_cases']} "
        f"(false claims {report['false_claims']}, missed claims {report['missed_claims']}), "
        f"errors {report['errors']}"
    )
    lines.append("=" * 118)
    header = (
        f"{'#':>2}  {'score':>5}  {'sev':<3} {'blast':>5} {'cons':>4}  "
        f"{'div':<18} {'FC':>3} {'MC':>3} {'shared':>6}  predicate"
    )
    lines.append(header)
    lines.append("-" * 118)
    for index, row in enumerate(report["rows"], start=1):
        lines.append(
            f"{index:>2}  {row['rank_score']:>5}  {row['severity']:<3} {row['blast']:>5} "
            f"{row['consumer_count']:>4}  {row['divergence']:<18} "
            f"{row['false_claims']:>3} {row['missed_claims']:>3} "
            f"{row['shared_claim_rate']:>6}  {row['pred_id']}"
        )
    lines.append("-" * 118)
    lines.append("FC = false claim (should NOT claim, did). MC = missed claim (should claim, did not).")
    lines.append("shared = fraction of the 16-row shared hard corpus this predicate claims (OBSERVATION,")
    lines.append("         not a verdict: the shared rows carry no ground truth).")
    lines.append("")
    lines.append("DIVERGENT CASES, in rank order:")
    for row in report["rows"]:
        if not row["failures"]:
            continue
        lines.append(f"  [{row['severity']} x{row['consumer_count']}cons] {row['pred_id']}  "
                     f"({row['where']})")
        for fail in row["failures"]:
            direction = fail.get("direction", fail.get("got"))
            text = fail["text"].replace("\n", " / ")
            lines.append(f"      {direction:<13} {fail['case_id']:<10} basis={fail['basis']:<9} "
                         f"{text[:74]!r}")
            lines.append(f"                    why: {fail['why'][:96]}")
    clean = [r["pred_id"] for r in report["rows"] if not r["failures"] and not r["errors"]]
    lines.append("")
    lines.append(f"CLEAN ROWS ({len(clean)}) -- measured on both directions, no divergence found:")
    for name in clean:
        lines.append(f"      {name}")
    lines.append("")
    return "\n".join(lines)
