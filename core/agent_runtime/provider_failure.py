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

    @property
    def model_answered(self) -> bool:
        """Whether the provider actually produced a completion. Governs whether it is honest to
        send the operator to the model picker."""
        return self.kind in {
            EMPTY_CONTENT,
            REASONING_ONLY,
            OUTPUT_BUDGET_EXHAUSTED,
            MISSING_USAGE,
            UNUSABLE_ARTIFACT,
        }

    def operator_sentence(self, *, model_label: str = "", phase: str = "") -> str:
        model = f"`{model_label}`" if model_label else "the selected model"
        where = f" during the {phase} step" if phase else ""
        if self.kind == UNREACHABLE:
            return f"{model} could not be reached{where} ({self.detail or 'transport failure'})."
        if self.kind == PROVIDER_ERROR:
            return f"{model} returned a provider error{where}: {self.detail or 'unspecified'}."
        if self.kind == EMPTY_CHOICES:
            return f"{model} answered{where} but the response carried no choices."
        if self.kind == EMPTY_CONTENT:
            return (
                f"{model} answered{where} and the final content was empty — the model was reached; "
                "it did not produce the artifact this step required."
            )
        if self.kind == REASONING_ONLY:
            return (
                f"{model} answered{where} with reasoning only and no artifact. The model was "
                "reached and spent its output budget thinking rather than answering."
            )
        if self.kind == OUTPUT_BUDGET_EXHAUSTED:
            return (
                f"{model} hit its output-token limit{where} before finishing the artifact. The "
                "model was reached; the budget ran out."
            )
        if self.kind == UNUSABLE_ARTIFACT:
            return (
                f"{model} answered{where} and the content did not satisfy this step's contract: "
                f"{self.detail or 'the artifact was rejected'}. The model was reached and produced "
                "text; the text was not usable here."
            )
        return f"{model} completed the call{where} but reported no token usage."


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
