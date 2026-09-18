---
name: vool-lens-market
description: "Automatic market expert lens: when a turn reasons about positioning, customers, pricing, or competition, apply the market lens — who actually pays, what alternative they already use, and what would make them switch — bounded to five checks on the material already present. Use automatically on market-shaped turns; never a substitute for real market data."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: expert_lens
id: lens-market
risk-class: read_only
task-families: [business_advisory, research]
expected-outputs: [lens_observations]
verification: [evidence_cited, deterministic_evidence]
stopping-conditions: ["the lens delivers at most five observations, each grounded in a claim or number already present in the turn, or states that the material carries no market claim to assess"]
incompatible-with: []
priority: 50
---
# Market lens (automatic)

You are looking at this turn THROUGH the market lens. The lens is bounded:
apply exactly these checks to the material already in the turn — never cite a
market number you were not given, never invent a competitor or a price.

1. Who specifically pays for this, and what job do they pay it to do?
2. What do these customers do TODAY instead — including "nothing" and
   "a spreadsheet"?
3. What would make them switch, and what would make them switch back?
4. Which claim here is a guess wearing a number? Flag it as unverified.
5. What is the cheapest next contact with reality — one question to one
   real customer or one checkable number?

Rules. Every observation ties to a claim or figure the turn already contains;
a market assertion without that tie is invented, so do not state it. Numbers
you were not given stay unknown — say "not in evidence" rather than estimating
from feel. This lens advises; it never executes anything, never grants
anything, and it never replaces real research the user explicitly asked for.
