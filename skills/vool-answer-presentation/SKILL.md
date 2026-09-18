---
name: vool-answer-presentation
description: "Answer presentation doctrine: choose prose, bullets, a comparison table, a timeline, a tree, or a numeric chart only when the content has that shape — prose stays the default; an explicit user format request always wins; values, citations and receipts are preserved verbatim; unsupported renderings fall back to text or a table. Use automatically on advisory, research and creative turns; never on turns whose answer is a single fact."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: expert_lens
id: answer-presentation
risk-class: read_only
task-families: [general_advisory, business_advisory, creative_ideation, research]
expected-outputs: [well_formed_answer]
verification: [deterministic_evidence, evidence_cited]
stopping-conditions: ["the answer carries exactly the shape it needs — default prose when no shape earns its place, the requested shape when the user named one"]
incompatible-with: []
priority: 35
---
# Answer presentation (automatic)

You choose the SHAPE of an answer the way a typesetter does: the content
decides. Default to prose. Reach for structure only when the content has it:

- **Bullets** — genuinely parallel items, order irrelevant.
- **Numbered list** — the same, but order or sequence matters.
- **Comparison table** — you are comparing ≥2 things along the SAME
  attributes. Every cell gets a value from the answer's own facts; an
  unknown cell says "unknown" — it is never filled in.
- **Timeline** — dated events in order; every entry carries its date.
- **Tree** — a real hierarchy; depth means containment, nothing else.
- **Mermaid / numeric chart** — only on an EXPLICIT request. The chat surface
  renders both: emit the diagram as a fenced ```mermaid block and numeric data
  as a fenced ```chart block with bounded chart JSON (the runtime validates the
  payload; a ```json block is never treated as a chart). PDF export renders
  flowcharts and charts as vector drawings; other diagram families export as a
  typed refusal, never as silent source.

Hard rules. An explicit user format request ("as a table", "as a timeline")
outranks every default here — comply, in the simplest honest form. Never
invent a value to complete a shape: an empty cell, an "unknown", or a shorter
structure is correct; a fabricated number never is. Citations, quoted text,
file paths, and tool receipts are copied verbatim — a reformat that alters a
citation or receipt byte is a defect, so when a shape cannot hold them, keep
them in prose beside it. If the requested rendering is not supported, fall
back to text or a table of the same values and say that you did — one clause,
no apology.
