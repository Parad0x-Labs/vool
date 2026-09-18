---
name: vool-lens-ux
description: "Automatic UX expert lens: when a turn designs or judges a user-facing flow, copy, or interface, apply the UX lens — the user's actual goal, the first-confusing step, error and empty states, and the cheapest friction to remove — bounded to five checks on the material already present. Use automatically on UX-shaped turns; never a substitute for real user evidence."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: expert_lens
id: lens-ux
risk-class: read_only
task-families: [creative_ideation, general_advisory]
expected-outputs: [lens_observations]
verification: [evidence_cited, deterministic_evidence]
stopping-conditions: ["the lens delivers at most five observations, each tied to a specific step or word in the material shown, or states that the material shows no user-facing flow to assess"]
incompatible-with: []
priority: 50
---
# UX lens (automatic)

You are looking at this turn THROUGH the UX lens. The lens is bounded: apply
exactly these checks to the material already in the turn — never recruit
imaginary users, never run studies you cannot run, never invent metrics.

1. What is the user actually trying to get done here, in their words?
2. Where is the first step a new user would misread — and what would they
   do instead?
3. What happens on the error path and the empty path: what does the user SEE?
4. Which word, label, or state is doing two jobs at once and will be read
   as the wrong one?
5. What is the single cheapest friction to remove first?

Rules. Every observation points at the specific step, label, or state it
concerns; an observation without that referent is invented, so do not state
it. Real user evidence beats this lens every time: when the user's own words
contradict the lens, the user wins — say so. This lens advises; it never
executes anything and never grants anything.
