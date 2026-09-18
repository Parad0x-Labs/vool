# Corrections that stick: VOOL's self-learning memory

Design, 2026-08-15. Grounded in live research (forge §0.8), not recalled knowledge.

## 1. GOAL

A correction the operator makes repeatedly stops needing to be made. "When I ask for reports,
format them like this" said three times becomes a durable, project-scoped instruction that applies
without being restated — and every such instruction can be traced to the exact turns that taught
it, reversed, and audited.

## 2. WHAT THE COMPETITION ACTUALLY DOES (checked 2026-08-15, not from training data)

**OpenClaw** — memory is plain Markdown in the workspace: `MEMORY.md` plus daily session files,
with a pre-compaction flush that reminds the model to write to disk. If it was not written, it does
not exist. Retrieval over archives is embedding search.

**Hermes** — five pillars (memory, skills, soul, crons, self-improvement). A background
self-improvement review after a turn "may quietly save a memory or update a skill". Self-improvement
is claimed at three levels: user preferences, skill reliability, and retrieval signal-to-noise.

## 3. WHERE THEY ARE WEAK — measured, from their own trackers and the literature

| weakness | evidence |
|---|---|
| `MEMORY.md` silently truncated ~20K chars, no warning; 150K aggregate cap | openclaw#45415, #42877 |
| appends but rarely consolidates or removes stale entries — "memory management is in chaos" | openclaw#43747 |
| retrieval gets noisier as the corpus grows; tokens burned on outdated context | community writeups |
| "remembers everything you tell it but understands none of it" — cannot relate facts | dailydoseofds |
| inconsistent across installs — one instance writes daily files, another remembers nothing | openclaw#43747 |
| **background execution silently pollutes memory**; pre-compaction flush, heartbeat prompts and standard access controls are all shown insufficient | arXiv 2603.23064 |

Every one of these is a **provenance and lifecycle** failure, not a storage failure. They store text
and hope. The pollution paper's own recommendation — isolated compartments, integrity verification,
audit logging of every write — is a description of provenance.

## 4. WHY VOOL IS STRUCTURALLY ADVANTAGED HERE

This is the one lane where local-first is an advantage rather than a tax, and VOOL already carries
the machinery the pollution paper asks for. A memory record today already has:

    record_id  created_at  last_confirmed_at  text  category  fact_key  scope  share_scope
    authority  confidence  status  expires_at  review_after  superseded_record_id  provenance
    origin_chat_id  origin_project_id  project_id  session_id  source  source_id

That is richer than OpenClaw's entire model. VOOL does not need a new store. It needs the loop.

## 5. THE GAP, precisely

1. **Nothing counts repetition.** A correction made three times is stored exactly like one made
   once, so it can never become stronger than an ordinary fact. This is the whole "make it stick"
   ask, and it is missing.
2. **No promotion path** from *observation* to *project instruction*.
3. **Provenance does not bind to a turn.** `provenance` records `kind` and `source_id`, and
   `session_id` names the chat — but not the specific turn. Without a turn id, an entry cannot be
   replayed against the exchange that produced it, which is precisely the audit the pollution paper
   says is required.

## 6. THE DESIGN — what makes this beat rather than shadow

**6.1 Nothing enters memory without an attributable turn.** Every learned entry names the turn id
that taught it. An entry that cannot name its origin is invalid by construction, so the silent
pollution class is closed structurally rather than by a heartbeat that an attacker can outwait.

**6.2 Repetition is evidence, and evidence is counted.** Corrections that agree on the same
`fact_key` accumulate `evidence_turn_ids`. Strength is the count of DISTINCT turns, never the
number of times a background job re-saved the same thing — which is how a self-improvement loop
inflates its own confidence.

**6.3 Promotion is a recorded, reversible event, never a silent write.** At the threshold an entry
is proposed for promotion to a project-scoped instruction, carrying its evidence turns. Hermes
"quietly saves"; VOOL proposes, records, and can un-promote by pointing at the same evidence.

**6.4 Contradiction supersedes, it does not overwrite.** A later correction that conflicts sets
`superseded_record_id` and keeps both, so "why does it think that?" is answerable. Overwriting is
how a store ends up remembering everything and understanding nothing.

*Implemented, and it caught a live defect in §7's first commit.* Polarity ("always" vs "never",
"stop", "don't") had been treated as noise, so a reversal merged into the directive it reversed and
the WITHDRAWN rule was promoted carrying the strength of the operator's objections to it. Polarity
is now part of directive identity; a reversal marks the old entry with the turn id that withdrew it
and permanently bars it from promotion, while keeping it readable. The control that matters as much:
"stop adding X", "never add X" and "don't add X" agree, and must still merge.

**6.5 Decay is automatic, curation is not manual.** `last_confirmed_at` already exists and is
unused for lifecycle. An instruction never re-confirmed within its window is proposed for
retirement. This is the answer to bloat that does not require the operator to garden a file.

**6.6 Budgets are enforced at write time and are visible.** A write that would exceed the budget
fails loudly and names what would be dropped. Never silent truncation.

## 7. SCOPE OF THE FIRST INCREMENT (this commit)

Implemented: correction detection, turn-attributed evidence accumulation, and promotion
PROPOSALS.

**Deliberately NOT implemented: automatic application of a promoted instruction.** Auto-promotion
changes the assistant's behaviour without the operator saying so, and Hermes calls its equivalent
"consent-aware" for a reason. That is an owner decision about consent, not an engineering default —
see the question recorded in the session ledger. Everything above it is measurement and
attribution, which is safe, useful on its own, and the foundation the rest needs.

## 8. NON-GOALS

No vector database, no knowledge graph, no second model call per turn. The failures above are not
caused by a missing index; adding one would import their token-bloat problem while leaving the
provenance hole open.

## Sources

- https://github.com/openclaw/openclaw/issues/45415 — MEMORY.md size warning/limit enforcement
- https://github.com/openclaw/openclaw/issues/42877 — bounded memory tool with hard character limits
- https://github.com/openclaw/openclaw/issues/43747 — "Memory management is in chaos"
- https://github.com/openclaw/openclaw/issues/50096 — long-term memory & knowledge management
- https://arxiv.org/pdf/2603.23064 — Mind Your HEARTBEAT! background execution enables silent memory pollution
- https://arxiv.org/pdf/2606.31121 — selective updates in sequentially evolving LLM memory
- https://hermes-agent.nousresearch.com/docs/user-guide/features/memory — Hermes persistent memory
- https://www.mindstudio.ai/blog/hermes-agent-five-pillars-memory-skills-soul-crons — Hermes five pillars
- https://velvetshark.com/openclaw-memory-masterclass — OpenClaw memory masterclass
- https://blog.dailydoseofds.com/p/openclaws-memory-is-broken-heres — "OpenClaw's memory is broken"
