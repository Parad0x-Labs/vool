"""What an agent's report may say, checked against what it actually did, then capped.

The check runs before anything reaches the chat:

* a non-zero exit is ``failed``, whatever the agent's text says; an exit the coordinator could
  not observe (a re-adopted agent after a restart) is ``unverified``, never ``done``;
* every file the agent says it changed must show up in the change scan of its claim — the ones
  that do not are listed as *claimed but not observed*;
* every file that changed in its claim but is missing from its report is listed as *changed but
  not reported*;
* a completion claim in free text ("I fixed…", "built…", "saved…") with no observed change and no
  tool receipt is flagged — the same predicate VOOL's own reply-honesty gate uses.

The relayed result is capped at 400 tokens. The full text stays on disk (and in a model agent's
own chat); the relay says where.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from core.prompt_assembly_report import estimate_tokens

RESULT_TOKEN_CAP = 400


@dataclass
class Verdict:
    state: str                    # done | failed | partial | unverified | needs_decision | stopped
    claimed_not_observed: list[str] = field(default_factory=list)
    changed_not_reported: list[str] = field(default_factory=list)
    changed_outside_claim: list[str] = field(default_factory=list)
    unsupported_claim: str = ""   # mutation | build | persistence
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.claimed_not_observed or self.changed_not_reported or self.changed_outside_claim
                    or self.unsupported_claim) and self.state == "done"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "claimed_not_observed": self.claimed_not_observed,
            "changed_not_reported": self.changed_not_reported,
            "changed_outside_claim": self.changed_outside_claim,
            "unsupported_claim": self.unsupported_claim,
            "notes": self.notes,
            "clean": self.clean,
        }


def _norm(paths: Iterable[str]) -> list[str]:
    return sorted({str(p).strip().lstrip("./") for p in paths or () if str(p).strip()})


def check_report(
    *,
    stated_status: str,
    claimed_changes: Iterable[str],
    observed_changes: Iterable[str],
    changed_outside_claim: Iterable[str] = (),
    exit_code: int | None,
    exit_observed: bool,
    text: str,
    receipts: int = 0,
    cap_hit: str = "",
    stopped: bool = False,
) -> Verdict:
    claimed = _norm(claimed_changes)
    observed = _norm(observed_changes)
    stated = str(stated_status or "").strip().lower()
    if stopped:
        state = "stopped"
    elif cap_hit:
        state = "partial"
    elif not exit_observed:
        state = "unverified"
    elif exit_code not in (0, None):
        state = "failed"
    elif stated == "needs_decision":
        state = "needs_decision"
    elif stated in {"failed", "error"}:
        state = "failed"
    elif stated == "partial":
        state = "partial"
    else:
        state = "done"
    verdict = Verdict(state=state)
    verdict.claimed_not_observed = [p for p in claimed if p not in observed]
    verdict.changed_not_reported = [p for p in observed if p not in claimed]
    verdict.changed_outside_claim = _norm(changed_outside_claim)
    if cap_hit:
        verdict.notes.append(f"stopped at its {cap_hit} limit; the result is partial")
    if not exit_observed and not stopped:
        verdict.notes.append("its exit could not be observed (re-adopted after a restart), so success is not claimed")
    if exit_observed and exit_code not in (0, None) and stated in {"done", "ok", "success", ""}:
        verdict.notes.append(f"it exited with code {exit_code}; its text is not taken as success")
    if not observed and not receipts and text:
        from core.agent_runtime.action_honesty_validator import completion_claim_kind

        kind = completion_claim_kind(text)
        if kind:
            verdict.unsupported_claim = kind
            verdict.notes.append(f"its text claims finished work ({kind}) but nothing changed and no tool ran")
    if verdict.claimed_not_observed and state == "done":
        verdict.notes.append("some changes it reported were not observed")
    return verdict


def cap_text(text: str, *, cap: int = RESULT_TOKEN_CAP, pointer: str = "") -> tuple[str, bool]:
    """``(text within cap tokens, truncated?)``. Counted with VOOL's conservative estimator."""
    body = str(text or "").strip()
    if estimate_tokens(body) <= cap:
        return body, False
    tail = f" … [cut to {cap} tokens; full result: {pointer}]" if pointer else f" … [cut to {cap} tokens]"
    budget_chars = cap * 4 - len(tail) - 4
    cut = body[: max(0, budget_chars)]
    space = cut.rfind(" ")
    if space > budget_chars * 0.6:
        cut = cut[:space]
    out = cut.rstrip() + tail
    while estimate_tokens(out) > cap and cut:
        cut = cut[:-16]
        out = cut.rstrip() + tail
    return out, True


def relay_line(*, display_name: str, verdict: Verdict, summary: str, pointer: str,
               cap: int = RESULT_TOKEN_CAP) -> tuple[str, bool]:
    """One agent's relayed result: state, the checked flags, then its summary — within ``cap``."""
    head = f"{display_name}: {verdict.state}."
    flags: list[str] = []
    if verdict.claimed_not_observed:
        flags.append("claimed but not observed: " + ", ".join(verdict.claimed_not_observed[:5]))
    if verdict.changed_not_reported:
        flags.append("changed but not reported: " + ", ".join(verdict.changed_not_reported[:5]))
    if verdict.changed_outside_claim:
        flags.append("changed outside its claim: " + ", ".join(verdict.changed_outside_claim[:5]))
    flags.extend(verdict.notes)
    text = head + (" " + "; ".join(flags) + "." if flags else "") + (" " + summary.strip() if summary.strip() else "")
    return cap_text(text, cap=cap, pointer=pointer)


__all__ = ["RESULT_TOKEN_CAP", "Verdict", "cap_text", "check_report", "relay_line"]
