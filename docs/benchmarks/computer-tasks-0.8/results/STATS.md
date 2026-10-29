# McNemar exact tests (jury verdict "correct", paired by ask)

Two-sided exact binomial test on the discordant pairs. b01 = first right, second wrong; b10 = the reverse.

## Round 1, cloud (newest builds; rival v3b, VOOL vool.sb v2)

| pair | n | correct A | correct B | b01 | b10 | p |
|---|---|---|---|---|---|---|
| VOOL vs OpenClaw 2026.10.8 | 45 | 44 | 26 | 19 | 1 | 4.01e-05 |
| VOOL vs Hermes c73b595 | 45 | 44 | 29 | 16 | 1 | 0.000275 |
| OpenClaw 2026.10.8 vs Hermes c73b595 | 45 | 26 | 29 | 5 | 8 | 0.581 |

## Round 1, cloud (September builds; rival v2, VOOL unsandboxed)

| pair | n | correct A | correct B | b01 | b10 | p |
|---|---|---|---|---|---|---|
| VOOL vs OpenClaw 2026.9.6 | 45 | 43 | 23 | 21 | 1 | 1.1e-05 |
| VOOL vs Hermes 9a0a1625 | 45 | 43 | 21 | 23 | 1 | 2.98e-06 |
| OpenClaw 2026.9.6 vs Hermes 9a0a1625 | 45 | 23 | 21 | 6 | 4 | 0.754 |

## Round 1, local qwen3:8b (VOOL vool.sb v2, OpenClaw 2026.10.8 v3b, Hermes 9a0a1625 v2)

| pair | n | correct A | correct B | b01 | b10 | p |
|---|---|---|---|---|---|---|
| VOOL vs OpenClaw 2026.10.8 | 45 | 33 | 0 | 33 | 0 | 2.33e-10 |
| VOOL vs Hermes 9a0a1625 | 45 | 33 | 0 | 33 | 0 | 2.33e-10 |
| OpenClaw 2026.10.8 vs Hermes 9a0a1625 | 45 | 0 | 0 | 0 | 0 | 1 |

## Round 2, cloud (rival v4, VOOL vool.sb v3)

| pair | n | correct A | correct B | b01 | b10 | p |
|---|---|---|---|---|---|---|
| VOOL vs OpenClaw 2026.10.8 | 45 | 39 | 32 | 12 | 5 | 0.143 |
| VOOL vs Hermes c73b595 | 45 | 39 | 25 | 18 | 4 | 0.00434 |
| OpenClaw 2026.10.8 vs Hermes c73b595 | 45 | 32 | 25 | 7 | 0 | 0.0156 |

# Round 2 per-system medians (cloud arm, headline rows)

| system | sandbox | n | median $/answer | median wall s | median prompt tokens | median cached prompt tokens | median completion tokens |
|---|---|---|---|---|---|---|---|
| VOOL | vool.sb v3 (= rival v4 rules) | 45 | 0.00057 | 8.0 | 2464 | 1856 | 126 |
| OpenClaw | v4 | 45 | 0.01009 | 34.1 | 78373 | 67712 | 362 |
| Hermes | v4 | 45 | 0.00665 | 20.4 | 50810 | 44864 | 304 |
