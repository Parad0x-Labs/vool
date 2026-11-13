"""Why a model call produced no artifact — as a label the operator can act on.

The incident told the operator to "pick a model this machine can reach" after the model had
answered. It answered with 8,192 tokens of reasoning and no artifact, which is a completely
different problem with a completely different fix: the model was reachable, the call was fine, and
the request let it spend its whole budget thinking. Sending that operator to the model picker was
not merely unhelpful, it pointed away from the actual cause.

Each kind exists because it has a different remedy:

    unreachable              — transport/DNS/connection: the provider was not spoken to
    provider_error           — the provider answered with an error
    empty_choices            — a well-formed response carrying no choice at all
    empty_content            — a choice whose content is empty
    reasoning_only           — the model produced reasoning and no answer
    output_budget_exhausted  — the response was cut off by max tokens before the artifact
    unusable_artifact        — content arrived and failed the step's contract (e.g. a citation the
                               source refutes). The model answered; the answer was not usable.
    missing_usage            — the call succeeded but reported no usage (accounting, not failure)

`unreachable` is deliberately narrow. It is claimed only for transport-level failure, so the
sentence "this runtime cannot reach that model" is true whenever it is printed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UNREACHABLE = "unreachable"
PROVIDER_ERROR = "provider_error"
EMPTY_CHOICES = "empty_choices"
EMPTY_CONTENT = "empty_content"
REASONING_ONLY = "reasoning_only"
OUTPUT_BUDGET_EXHAUSTED = "output_budget_exhausted"
MISSING_USAGE = "missing_usage"
UNUSABLE_ARTIFACT = "unusable_artifact"

FAILURE_KINDS: tuple[str, ...] = (
    UNREACHABLE,
    PROVIDER_ERROR,
    EMPTY_CHOICES,
    EMPTY_CONTENT,
    REASONING_ONLY,
    OUTPUT_BUDGET_EXHAUSTED,
    MISSING_USAGE,
    UNUSABLE_ARTIFACT,
)

# Transport-level words. Anything else from a provider is an answer of some kind.
_UNREACHABLE_MARKERS = (
    "connectionerror",
    "connection refused",
    "connection aborted",
    "name or service not known",
    "failed to establish",
    "max retries exceeded",
    "no route to host",
    "newconnectionerror",
    "dns",
)
_REASONING_ONLY_RE = re.compile(
    r"<think>|<\|thinking\|>|^\s*(?:okay|alright|let me|first,? i|hmm|wait)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProviderFailure:
    kind: str
    detail: str = ""
    #: A reason written for the person, set only by a call site that has verified the wording
    #: (the audit's evidence_rejected branch). `operator_sentence` appends this and nothing else;
    #: `detail` is engineering vocabulary for the structured call record and never reaches the
    #: sentence, whatever shape it arrives in.
    plain_reason: str = ""

    @property
    def model_answered(self) -> bool:
        """Whether the provider actually produced a completion. Governs whether it is fair to
        send the operator to the model picker."""
        return self.kind in {
            EMPTY_CONTENT,
            REASONING_ONLY,
            OUTPUT_BUDGET_EXHAUSTED,
            MISSING_USAGE,
            UNUSABLE_ARTIFACT,
        }

    def operator_sentence(self, *, model_label: str = "", phase: str = "") -> str:
        # The model id and the raw transport detail never go in this sentence: it is read by the
        # person waiting on the answer. Both stay in the structured call record (last_call.error,
        # the call manifests) that this label travels with.
        where = f" during the {phase} step" if phase else ""
        if self.kind in {UNREACHABLE, PROVIDER_ERROR} and re.search(
            r"\btim(?:ed|e)\s*-?\s*out\b|\btimeout\b", str(self.detail or ""), re.IGNORECASE
        ):
            # A timeout is the one transport fact the person can act on (it is neither "offline"
            # nor "an error"), so it is said in plain words; the raw detail stays in the call record.
            return (
                f"The AI service I tried did not answer in time{where} — the request timed out "
                "before an answer came back. Try again, or pick a faster model."
            )
        if self.kind == UNREACHABLE:
            return (
                f"The AI service I tried could not be reached{where} — the connection itself "
                "failed before any answer could start. Check the connection and try again."
            )
        if self.kind == PROVIDER_ERROR:
            return (
                f"The AI service was reached{where} but answered with an error instead of a "
                "result. Try again in a little while."
            )
        if self.kind == EMPTY_CHOICES:
            return (
                f"The AI service was reached{where} but its reply carried no answer at all. "
                "Ask again — if it keeps happening, the service is having trouble."
            )
        if self.kind == EMPTY_CONTENT:
            return (
                f"The AI service was reached{where} but its answer came back empty — it did not "
                "produce the work this step needed."
            )
        if self.kind == REASONING_ONLY:
            return (
                f"The AI service was reached{where} but spent its whole reply thinking and never "
                "wrote the answer. Ask again, or ask for a shorter answer."
            )
        if self.kind == OUTPUT_BUDGET_EXHAUSTED:
            return (
                f"The AI service was reached{where} but ran out of room for its reply before it "
                "could finish. Ask for something shorter, or split the request in parts."
            )
        if self.kind == UNUSABLE_ARTIFACT:
            sentence = (
                f"The AI service answered{where}, but what it wrote could not be used for this "
                "step — the text arrived and was set aside, not used."
            )
            # Only a plain_reason a call site wrote for the person is appended. The `detail`
            # field ("the completion did not contain the required artifact" and the like) stays
            # in the structured call record, never in this sentence.
            reason = " ".join(self.plain_reason.split())
            if reason:
                reason = f"{reason[0].upper()}{reason[1:].rstrip('.')}"
                sentence = f"{sentence} {reason}."
            return sentence
        return f"The AI service finished{where} but did not report how much it used."


def classify_provider_failure(
    *,
    error: str = "",
    text: str = "",
    finish_reason: str = "",
    has_choices: bool = True,
    usage_present: bool = True,
) -> ProviderFailure:
    """The label for one call that failed to yield a usable artifact.

    Order matters. A transport error outranks everything (there is no completion to inspect). Then
    the response shape, then the content, and only then accounting. `finish_reason == "length"` is
    checked before the empty-content branch because "the budget ran out" explains the emptiness and
    "empty" does not explain the budget.
    """
    detail = str(error or "").strip()
    lowered = detail.lower()
    if detail:
        if any(marker in lowered for marker in _UNREACHABLE_MARKERS):
            return ProviderFailure(UNREACHABLE, detail)
        return ProviderFailure(PROVIDER_ERROR, detail)
    if not has_choices:
        return ProviderFailure(EMPTY_CHOICES, "the response contained no choices")
    body = str(text or "")
    if str(finish_reason or "").strip().lower() == "length":
        return ProviderFailure(
            OUTPUT_BUDGET_EXHAUSTED, "the completion stopped at the output-token limit"
        )
    if body.strip() and _REASONING_ONLY_RE.search(body.strip()):
        return ProviderFailure(REASONING_ONLY, "the completion carried reasoning and no artifact")
    if not body.strip():
        return ProviderFailure(EMPTY_CONTENT, "the final content was empty")
    if not usage_present:
        return ProviderFailure(MISSING_USAGE, "the provider reported no usage for this call")
    # Content arrived and was rejected downstream. Calling this "empty" would be the same class of
    # inaccuracy as calling a model that answered "unreachable" — the operator would look for the
    # wrong problem.
    return ProviderFailure(UNUSABLE_ARTIFACT, "the completion did not contain the required artifact")
