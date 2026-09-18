# Conversation-truth scenario inventory (stratified)

Beyond the executable kit, the full stratified inventory this audit derived. "Covered" points at
the kit test (this folder), a repo test (tests/), or nothing. IDs are stable for future lanes.

## A. Topic-change adversaries (current request must replace prior authority)

| id | prior obligation | turn | expected | covered |
|---|---|---|---|---|
| A01 | gold quote | the recorded VW comparison | decline; no plan; not LIVE_DATA | kit cp1#1; repo flagship |
| A02 | gold quote | "and the weather there?" / "and the temperature?" | decline (cross-domain noun ≠ bare nudge) | kit repro CT-201 |
| A03 | weather city | "and the price?" / "what about the news?" | decline | kit repro CT-201 |
| A04 | gold quote | "write a poem about gold" / "how is gold mined?" / "tell me the history of gold" / "how much gold is there?" | decline (general/creative ≠ price re-ask) | kit repro CT-203 |
| A05 | weather city | "Write About Winter" / "Code About Python Now" | decline (Title-case ≠ place) | kit repro CT-204 |
| A06 | weather 2 cities | "which one has a river?" / "which one has water?" | decline (non-comparison -er/-est) | kit repro CT-205 |
| A07 | gold quote | "and golf, them two, was that what i asked about?" | decline (near-slot newcomer without restatement) | kit repro CT-206 |
| A08 | weather city | "write me a python function that sorts a list" | decline | kit control |
| A09 | gold quote | "help me write a short poem about the sea" | decline | kit control |
| A10 | weather→code→writing chain | chained topic changes | each declines; thread broken | kit control (single hop), chain not covered |
| A11 | gold quote | document attached + unrelated question | decline (attachment lane) | NOT covered (attachment lanes out of scope) |

## B. Genuine follow-ups (prior authority must survive)

| id | prior | turn | expected | covered |
|---|---|---|---|---|
| B01 | gold | "and now?" / "what about now?" / "and?" | re-ask gold | kit control; repo pack |
| B02 | gold | "what about silver?" / "and silver?" | rebind silver | kit control; repo pack |
| B03 | gold | "no, i meant silver" | rebind silver (correction target wins) | kit control |
| B04 | gold | "not gold, silver" | rebind silver — NEVER gold | kit repro CT-202 |
| B05 | weather 2 cities | "which one is warmer/warmest?" | aggregate both | kit control |
| B06 | weather 2 cities | typo clarification ("kauans and talling… them two right?") | recover both | kit control; gauntlet G1 |
| B07 | gold→silver comparison | "back to gold, what is the price?" | answer gold (self-classifying return) | kit control |
| B08 | gold | "und jetzt?" / "а сейчас?" / "y ahora?" / "o dabar?" | decline SAFELY (never substitute) | kit control (multilingual decline) |
| B09 | gold | "compare them, which is cheaper?" | aggregate (today: declines — two comparison words) | NOT covered as red (accepted limitation, flag CT-208) |
| B10 | weather (no obligation row, legacy lane served) | "and now?" | re-ask from history walk-back | kit control (designed boundary pin) |

## C. Quoted/ambiguous reference

| id | prior | turn | expected | covered |
|---|---|---|---|---|
| C01 | gold | 'you asked "what is gold price now?" - now do the same for silver' | no inheritance; serve the new ask | kit control |
| C02 | gold | same, target "silver vs platinum" | plan must not contain the QUOTED subject | kit observation CT-207 (entity leak) — recorded, not red |
| C03 | gold | "what about that other thing?" / "ok so what about the stuff?" | decline (no silent resurrection) | kit control |
| C04 | gold | "the same for yesterday" (temporal change) | decline or new request | NOT covered |

## D. Obligation completeness (census)

| id | turn | expected | covered |
|---|---|---|---|
| D01 | clean multi-dimension comparison (5 dims) | 5 units, one per dimension | kit repro CT-301 |
| D02 | incident run-on tail (regions+engines+and so on) | engines tracked separately | kit repro CT-301 |
| D03 | receipt binding across text spaces (any mis-bind input) | unit bound only if its own text carries the entity | kit repro CT-302 |
| D04 | conservation: unclaimed counts every unverifiable unit | unclaimed = all canonical units | kit repro CT-302 |
| D05 | two asks in one turn ("A and also B") | both minted | repo test_rss_demand_accounting |
| D06 | revision ("actually just X") | per-turn re-mint | mint is per-turn (verified; not separately kit-tested) |
| D07 | removed requirement mid-plan | dropped unit not required | NOT covered |
| D08 | partial tool failure | failed unit = "dispatched, failed", never satisfied | repo answer-integrity D |
| D09 | incident arithmetic (guard severed) | 1 of 4 claimed via collision, 3 unanswered | kit cp3 attribution |
| D10 | served multi-dimension census visibility | every minted unit appears with an explicit state | NOT covered served (machine-load caveat) |

## E. Cross-turn, identity, recovery

| id | scenario | expected | covered |
|---|---|---|---|
| E01 | two sessions, two obligations, interleaved nudges | disjoint subjects; per-session identity | kit cp4 pure + served |
| E02 | nudge in obligation-less, history-less session | decline | kit cp4 |
| E03 | intervening unrelated request kills thread | decline | kit cp4; repo pack |
| E04 | identical re-ask (same text twice) | earlier exchange survives (trailing-only dedup) | refuted as defect; law pinned in cp4 (provenance-first) |
| E05 | same body, same X-Request-ID | replay of committed finalization, no re-execution | kit cp4 served (code-verified door) |
| E06 | same text, fresh request id | fresh turn | kit cp4 served |
| E07 | delayed previous-turn completion (obligation recorded late) | text-bound, not timing-bound | repo pack (late recording) |
| E08 | queue: busy-turn enqueue → claim → run | queued text runs; composer not source | repo runtime-continuity packs |
| E09 | cancel mid-turn | turn stops; composer freed; no phantom commit | repo a9/chat-startup packs |
| E10 | restart → resume/continue | stored request text re-dispatched under lineage | repo checkpoint packs |
| E11 | approval resend after a newer turn in same chat | newer answer not popped/replaced | NOT reproduced (client-side code-read finding, CP4) |
| E12 | two windows, one canonical session | session gate serializes; no cross-window answer swap | structural (FIFO gate); not kit-tested |
| E13 | byte-identical redelivery, no explicit request id | fresh id each post → fresh turn (page never sends the header) | kit cp4 served (fresh-id arm) |

## F. Amplifiers (wrong answer still looks authoritative)

| id | scenario | expected | covered |
|---|---|---|---|
| F01 | task_completed ships pre-gate preview before gate/census | (observed in the wild: seq 44<45<50) | raw evidence; repo tests pin finalization order |
| F02 | provenance footer names producer, never subject | — | CP0 map; no kit test (rendering seam) |
| F03 | RSS/gate rewrites never propagate to conversation memory | next-turn recall can re-serve removed claims | CP0 map (D-Q); not kit-tested |
| F04 | covered=False certificate-only on literal lanes | ships with unanswered demands, no disclosure | CP0 map (D-R); not kit-tested |
