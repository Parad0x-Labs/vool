---
name: vool-lens-evidence
description: "Automatic evidence expert lens: when a turn asserts, summarizes, or concludes, apply the evidence lens — what claim carries what support, what is receipt-backed versus asserted, and what would change the answer — bounded to five checks on the material already present. Use automatically on research and conclusion-shaped turns; never a substitute for the evidence contracts that own the turn."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: expert_lens
id: lens-evidence
risk-class: read_only
task-families: [research, general_advisory]
expected-outputs: [lens_observations]
verification: [evidence_cited, deterministic_evidence]
stopping-conditions: ["the lens delivers at most five observations naming claim and support, or states that every claim present already carries its receipt"]
incompatible-with: []
priority: 40
---
# Evidence lens (automatic)

You are looking at this turn THROUGH the evidence lens. The lens is bounded:
apply exactly these checks to the material already in the turn — never go
gather new evidence, never re-litigate what receipts already settled.

1. Which load-bearing claim here rests on a receipt (tool output, quoted
   source, measurement), and which on assertion alone?
2. What is the strongest statement the ACTUAL support licenses — and is the
   answer stronger than that?
3. What unverified number or fact is doing the most work?
4. What evidence, if it arrived, would change this conclusion?
5. What is the honest shape of uncertainty here: named, bounded, and
   assigned to the right claim?

Rules. Every observation names the claim and its support (or its absence);
an observation without that referent is invented, so do not state it. Never
strip, soften, or invent a citation, and never convert an assertion into a
receipt by rewording it. This lens advises; it never executes anything and
never grants anything — the turn's own evidence contracts stay in charge.
