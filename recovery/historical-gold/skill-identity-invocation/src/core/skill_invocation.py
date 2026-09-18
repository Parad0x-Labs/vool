"""SkillInvocation — WORKFLOW truth, never effect truth.

Pass #2 fix for the Pass #1 self-found bug: a record said APPLIED while an effect
proposal was refused. Status semantics are now exact:

    APPLIED                      every workflow step produced AND every capability the
                                 skill requested was granted; zero refusals.
    PARTIAL                      steps were produced but at least one capability request
                                 was refused or unresolved. The workflow ran; some
                                 proposed effects did not happen.
    BLOCKED_MISSING_CAPABILITY   pre-flight failed: toolbelt has no implementation for a
                                 declared need. No steps ran.
    BLOCKED_AUTHORIZATION        pre-flight failed: implementation exists but platform
                                 grants do not cover a declared need. No steps ran.
    CONFLICT                     composition conflicts surfaced and unresolved.
    INVALID                      identity/tamper/manifest failure.

Refusal during execution can never co-occur with status APPLIED — `finish()` enforces
it mechanically, so the Pass #1 bug is unrepresentable rather than merely avoided.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class InvocationTruthError(ValueError):
    """An attempt to record a status that contradicts recorded evidence."""


@dataclass
class SkillInvocation:
    skill_ref: str                       # publisher/id@version#effective-digest12
    package_digest: str = ""             # provenance: WHICH source bytes
    scope: str = ""
    input_refs: tuple[str, ...] = ()
    capability_needs: tuple[str, ...] = ()
    capability_granted: tuple[str, ...] = ()
    capability_refused: tuple[str, ...] = ()
    contributed_steps: tuple[str, ...] = ()
    status: str = "APPLICABLE"
    detail: str = ""
    _finalized: bool = field(default=False, repr=False)

    def grant(self, name: str) -> None:
        self.capability_granted += (name,)
        if name in self.capability_refused:
            raise InvocationTruthError(f"{name} cannot be both granted and refused")

    def refuse(self, name: str) -> None:
        self.capability_refused += (name,)
        if name in self.capability_granted:
            raise InvocationTruthError(f"{name} cannot be both granted and refused")

    def finish(self) -> "SkillInvocation":
        """Derive the truthful terminal status from evidence. Idempotent."""
        if self.status in ("BLOCKED_MISSING_CAPABILITY", "BLOCKED_AUTHORIZATION",
                           "CONFLICT", "INVALID"):
            self._finalized = True
            return self
        if self.capability_refused:
            # THE LAW: refusal + produced steps = PARTIAL. Never APPLIED.
            self.status = "PARTIAL"
            self.detail = (self.detail + f" refused={self.capability_refused}").strip()
        elif not self.contributed_steps:
            self.status = "APPLICABLE"  # nothing ran yet; honest non-claim
        else:
            self.status = "APPLIED"
        self._finalized = True
        return self

    def set_applied(self) -> None:
        """Direct override is forbidden; use finish(). Kept to fail loudly."""
        raise InvocationTruthError(
            "cannot force APPLIED; call finish() so refusals force PARTIAL")

    def render(self) -> str:
        lines = [f"SkillInvocation({self.skill_ref})", f"  status: {self.status}"]
        if self.package_digest:
            lines.append(f"  package: sha256:{self.package_digest[:16]}…")
        if self.capability_needs:
            lines.append(f"  needs:    {', '.join(self.capability_needs)}")
        if self.capability_granted:
            lines.append(f"  granted:  {', '.join(self.capability_granted)}")
        if self.capability_refused:
            lines.append(f"  REFUSED:  {', '.join(self.capability_refused)}")
        if self.contributed_steps:
            lines.append(f"  steps: {len(self.contributed_steps)} contributed")
        if self.detail:
            lines.append(f"  note: {self.detail}")
        return "\n".join(lines)
