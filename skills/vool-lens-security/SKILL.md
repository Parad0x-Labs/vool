---
name: vool-lens-security
description: "Automatic security expert lens: when a turn designs, reviews, or changes anything attackable, apply the security lens — name the trust boundary, the worst plausible attacker, and the fail-closed answer — bounded to five checks, evidence-first. Use automatically on security-relevant turns; never a substitute for a real audit."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: expert_lens
id: lens-security
risk-class: read_only
task-families: [security_hardening]
expected-outputs: [lens_findings]
verification: [evidence_cited, deterministic_evidence]
stopping-conditions: ["the lens states at most five findings with their evidence, or states plainly that no security-relevant surface was found in the material already present"]
incompatible-with: []
priority: 45
---
# Security lens (automatic)

You are looking at this turn THROUGH the security lens. The lens is bounded:
apply exactly these checks to the material already in the turn — never go
gather new facts, never expand the user's request into an audit.

1. What is the trust boundary here, and what crosses it unvalidated?
2. What is the worst plausible attacker action if this fails open?
3. Does the error path fail closed, and who can trigger it?
4. Is any secret, credential, or personal value exposed by this design?
5. What is the single cheapest change that removes the worst exposure?

Rules. Every finding cites the exact material it came from — a lens finding
without a referent is invented, so do not state it. Say "no security-relevant
surface in what I have" when that is true; an honest negative is a result.
This lens advises; it never executes, never widens any permission, and never
replaces a requested `security-audit` — if the user asked for an audit, that
workflow owns the turn and this lens adds at most its five checks.
