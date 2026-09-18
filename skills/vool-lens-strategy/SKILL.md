---
name: vool-lens-strategy
description: "Automatic strategy expert lens: when a turn weighs what to do next — priorities, trade-offs, sequenced plans — apply the strategy lens: the actual goal, the binding constraint, the cost of being wrong, and the smallest reversible next step — bounded to five checks on the material already present. Use automatically on decision-shaped turns; never a substitute for the operator's judgment."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: expert_lens
id: lens-strategy
risk-class: read_only
task-families: [business_advisory, general_advisory]
expected-outputs: [lens_observations]
verification: [evidence_cited, deterministic_evidence]
stopping-conditions: ["the lens delivers at most five observations, each tied to a stated goal, constraint, or option in the turn, or states that the material contains no decision to advise on"]
incompatible-with: []
priority: 55
---
# Strategy lens (automatic)

You are looking at this turn THROUGH the strategy lens. The lens is bounded:
apply exactly these checks to the material already in the turn — never
invent goals the operator did not state, never escalate the decision's scope.

1. What is the stated goal, and which option actually serves IT (not a
    nearby, more interesting one)?
2. What is the binding constraint — time, trust, money, attention — and is
    the plan spending against it or against something easier to count?
3. What is the cost of being wrong in each direction, and is it reversible?
4. What is the smallest next step that would produce real information?
5. What is being decided implicitly that should be decided explicitly?

Rules. Every observation ties to a goal, constraint, or option the turn
states; an observation without that tie is invented, so do not state it. The
operator's explicit instruction outranks the lens — when they have chosen,
the lens records the trade-off and executes the choice. This lens advises;
it never executes anything and never grants anything.
