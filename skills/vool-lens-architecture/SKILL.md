---
name: vool-lens-architecture
description: "Automatic architecture expert lens: when a turn designs, integrates, or trades off structure, apply the architecture lens — boundaries, coupling, blast radius, failure modes — bounded to five checks, grounded in the material already present. Use automatically on design-shaped turns; never a substitute for real repo inspection."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: expert_lens
id: lens-architecture
risk-class: read_only
task-families: [system_design, integration_orchestration]
expected-outputs: [lens_assessment]
verification: [evidence_cited, deterministic_evidence]
stopping-conditions: ["the lens delivers at most five structural observations with their referents, or states that the material shown carries no structural decision to assess"]
incompatible-with: []
priority: 50
---
# Architecture lens (automatic)

You are looking at this turn THROUGH the architecture lens. The lens is
bounded: apply exactly these checks to the material already in the turn —
never go read the repository for more, never redesign what was asked for.

1. What are the boundaries, and does one side know the other's internals?
2. What couples this to its neighbours — and what breaks when one moves?
3. What is the blast radius if this component is wrong or down?
4. Which failure mode is silent, and what would make it loud instead?
5. What is the one simplification that removes a boundary violation?

Rules. Every observation names its referent — the boundary, seam, or trade-off
in the material shown; an observation without a referent is invented, so do
not state it. Say so plainly when the material carries no structural decision.
This lens advises; it never executes anything, never grants anything, and it
defers to the turn's actual task — the lens shapes the answer, it does not
become the answer unless structure is what was asked about.
