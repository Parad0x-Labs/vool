"""How many reasoning calls one file audit is allowed, and a receipt for every one of them.

Measured on a real audit of a single Python file: **9 model calls, 28,741 input tokens, 1,772 output
tokens.** Traced, none of it was mysterious and none of it was the model's fault:

* the nomination prompt carries the whole numbered file excerpt (12,000 characters, ~3,000 tokens),
  and EVERY retry re-sent it in full — the correction only needed the lines it was correcting;
* a rejected candidate was retried up to three times **per manifest**, and the driver then walked to
  the next manifest and started again, so one unlucky file could multiply 3 × 4;
* each of three candidates then paid for its own adversarial challenge call;
* nothing anywhere counted the calls, so nothing could notice.

9 × ~3,000 tokens of re-sent source is the whole 28,741 to within the overhead. So the repair is
architectural, not a cap: retries send the cited window instead of the file, manifest fan-out
happens only on a transport failure (a second model cannot fix a claim the evidence contradicts),
and the candidate budget is spent on distinct candidates rather than on re-asking.

The ceiling here is the backstop behind those repairs, and it is a RECORDED one. The intended shape
of a bounded audit is:

    primary analysis  ->  optional independent challenge  ->  verdict (deterministic, no call)

with proof-artifact generation and one synthesis call when the turn authorized execution. Anything
beyond that must name the justification that bought it, and the justification is stored on the call
row — so "why did this cost nine calls?" is answered by reading the ledger rather than by guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The ceiling for a turn that may EXECUTE. Each extra candidate here is bought with an executed
# disproof — a test that ran and exited 0 — which is recorded evidence that the search should
# continue (AGENT_HANDOVER §1A rule 4). Eight allows three proved candidates plus one synthesis and
# leaves room for a single correction; it does not allow re-asking.
DEFAULT_MAX_TOTAL_CALLS = 8
# Per-purpose ceilings across the WHOLE turn — not per candidate, not per manifest. The incident's
# multiplication came entirely from per-something budgets that composed: three attempts per manifest
# across four manifests across three candidates.
DEFAULT_STEP_CEILINGS: dict[str, int] = {
    "nominate": 4,
    "challenge": 2,
    "prove": 4,
    "synthesize": 1,
}

# What a call was given, so a token total can be attributed to a decision rather than to a step name.
CONTEXT_FULL_EXCERPT = "full_file_excerpt"
CONTEXT_CITED_WINDOW = "cited_source_window"
CONTEXT_FINDING_ONLY = "finding_and_claim_only"
CONTEXT_PROOF_RESULT = "proof_result"

# ~4 characters per token, the same approximation the prompt assembler uses. Labelled an estimate
# everywhere it surfaces; it exists to say which SHARE of a prompt was assembled context, never to
# contradict a provider's measurement.
_CHARS_PER_TOKEN = 4.0


@dataclass
class AuditCall:
    """One model call, with everything needed to answer "why was this necessary?"."""

    number: int
    step: str
    context_source: str
    provider_id: str = ""
    model_name: str = ""
    cloud: bool = False
    prompt_chars: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    result: str = "pending"
    justification: str = ""

    @property
    def assembled_context_tokens(self) -> int:
        """The estimated size of what this call was HANDED, as distinct from what it cost."""
        return round(self.prompt_chars / _CHARS_PER_TOKEN)

    def as_dict(self) -> dict[str, Any]:
        return {
            "call": self.number,
            "purpose": self.step,
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "cloud": self.cloud,
            "context_source": self.context_source,
            "assembled_context_tokens_estimated": self.assembled_context_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "result": self.result,
            "why_another_call": self.justification,
        }


@dataclass
class AuditCallLedger:
    """The turn's call budget and its receipt, in one object.

    Budget and record are deliberately the same thing. When they were separate, the count that
    decided whether to stop and the count shown to the operator were maintained by different code,
    and only one of them existed.
    """

    max_total_calls: int = DEFAULT_MAX_TOTAL_CALLS
    step_ceilings: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_STEP_CEILINGS))
    calls: list[AuditCall] = field(default_factory=list)
    # Refusals the ceiling issued, so a truncated audit says it was truncated instead of reading as
    # a search that finished.
    refusals: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.calls)

    def spent_on(self, step: str) -> int:
        """Calls of this purpose that actually REASONED.

        A call the provider refused before inference — `model_load_gated_low_memory` on a box under
        memory pressure — produced no artifact and consumed no thinking. Counting it against the
        step ceiling meant two dead local attempts spent the whole nomination budget and the one
        model that did answer had no attempts left (measured live 2026-08-01). `max_total_calls`
        still counts everything, so a wedged provider cannot loop.
        """
        return sum(
            1
            for call in self.calls
            if call.step == step and not str(call.result or "").startswith("error:")
        )

    def refusal_for(self, step: str) -> str:
        """Why this step may not run another call, or ``""`` when it may."""
        if self.count >= int(self.max_total_calls):
            return (
                f"the audit's bounded call budget ({self.max_total_calls} model calls) was spent"
            )
        ceiling = int(self.step_ceilings.get(step, self.max_total_calls))
        if self.spent_on(step) >= ceiling:
            return f"the `{step}` step already used its {ceiling} allowed call(s)"
        return ""

    def may_call(self, step: str) -> bool:
        reason = self.refusal_for(step)
        if reason:
            if reason not in self.refusals:
                self.refusals.append(reason)
            return False
        return True

    def open_call(
        self,
        *,
        step: str,
        context_source: str,
        manifest: Any = None,
        prompt_chars: int = 0,
        justification: str = "",
    ) -> AuditCall:
        from core.agent_runtime.audit_routing import manifest_is_cloud

        call = AuditCall(
            number=self.count + 1,
            step=str(step or ""),
            context_source=str(context_source or ""),
            provider_id=str(getattr(manifest, "provider_id", "") or ""),
            model_name=str(getattr(manifest, "model_name", "") or ""),
            cloud=manifest_is_cloud(manifest) if manifest is not None else False,
            prompt_chars=int(prompt_chars or 0),
            justification=str(justification or ""),
        )
        self.calls.append(call)
        return call

    def close_call(
        self, call: AuditCall | None, *, result: str, usage: dict[str, Any] | None = None
    ) -> None:
        if call is None:
            return
        call.result = str(result or "")
        from core.token_usage_receipt import measured_call_usage

        measured = measured_call_usage(usage or {})
        call.input_tokens = measured.get("input_tokens")
        call.output_tokens = measured.get("output_tokens")

    @property
    def cloud_calls(self) -> int:
        return sum(1 for call in self.calls if call.cloud)

    def as_rows(self) -> list[dict[str, Any]]:
        return [call.as_dict() for call in self.calls]

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.count,
            "cloud_calls": self.cloud_calls,
            "max_total_calls": int(self.max_total_calls),
            "budget_refusals": list(self.refusals),
            "trace": self.as_rows(),
        }


def read_only_ledger() -> AuditCallLedger:
    """The budget for an audit that may not execute anything — the incident's own shape.

    `analysis -> optional independent challenge -> verdict`, and the verdict costs no call at all
    because the runtime composes it. A turn that cannot run a proof cannot spend calls writing one,
    so `prove` and `synthesize` are zero rather than merely unused.

    Five, applied to the incident: candidate one costs nomination and challenge, its refutation buys
    candidate two, and the third nomination is refused with the reason recorded. The measured turn
    made nine calls and reported no finding either way — the difference is four calls and roughly
    21,000 input tokens of re-sent source.
    """
    return AuditCallLedger(
        max_total_calls=5,
        step_ceilings={"nominate": 3, "challenge": 2, "prove": 0, "synthesize": 0},
    )


def proof_ledger() -> AuditCallLedger:
    """The budget for a turn that was authorized to write and run a proof."""
    return AuditCallLedger()


__all__ = [
    "CONTEXT_CITED_WINDOW",
    "CONTEXT_FINDING_ONLY",
    "CONTEXT_FULL_EXCERPT",
    "CONTEXT_PROOF_RESULT",
    "DEFAULT_MAX_TOTAL_CALLS",
    "AuditCall",
    "AuditCallLedger",
    "proof_ledger",
    "read_only_ledger",
]
