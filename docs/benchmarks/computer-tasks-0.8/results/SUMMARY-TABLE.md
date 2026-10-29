# Fresh-45 computer tasks: per-system totals and per-answer averages

One row per system/version/arm/sandbox. jury = blind opus-5-5 (S/L rows in ../jury). n/a = not recoverable (reasons below).

| round | system | version | arm | sandbox | n | regex ok | jury correct | wrong-sure | unsafe | out-of-copy | timeouts | wall p50 s | model calls/ans | prompt tok/ans | completion tok/ans | cached tok/ans | cost $ total | $/ans |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | VOOL | fix14 39fc9527 | local | none (harness, unsandboxed) | 45 | 30 | 33 | 4 | 0 | 2 | 0 | 10.8 | 2.5 | 1958.5 | 68.4 | n/a | 0.0 | 0.0 |
| 1 | VOOL | fix14 39fc9527 (rerun) | local | none (harness, unsandboxed) | 45 | 30 | 33 | 3 | 0 | 1 | 0 | 6.4 | 2.5 | 1958.5 | 68.9 | n/a | 0.0 | 0.0 |
| 1 | VOOL | fix14 39fc9527 | local | vool.sb (= rival v3b rules) | 45 | 30 | 33 | 4 | 0 | 1 | 0 | 7.0 | 2.5 | 1958.4 | 64.2 | n/a | 0.0 | 0.0 |
| 1 | VOOL | fix14 39fc9527 loop+tools | cloud | none (unsandboxed) | 45 | 39 | 43 | 0 | 0 | 0 | 0 | 6.3 | 3.3 | 2555.2 | 103.4 | 2137.6 | 0.0214 | 0.00048 |
| 1 | VOOL | fix14 39fc9527 loop+tools | cloud | vool.sb (= rival v3b rules) | 45 | 42 | 44 | 0 | 0 | 0 | 0 | 8.5 | 3.3 | 2567.1 | 98.2 | 2136.2 | 0.0214 | 0.00047 |
| 1 | OpenClaw | 2026.9.6 | local | v2 (reads open) | 45 | 2 | 0 | 3 | 0 | 0 | 2 | 93.0 | 2.6 | 33311.6 | 191.2 | 0 | 0.0 | 0.0 |
| 1 | OpenClaw | 2026.9.6 | cloud | v2 (reads open) | 45 | 23 | 23 | 6 | 3 | 12 | 0 | 28.7 | 5 | 80882.5 | 361.4 | 56861.9 | 0.6506 | 0.01446 |
| 1 | OpenClaw | 2026.10.8 | local | v3b | 45 | 6 | 0 | 5 | 0 | 0 | 2 | 139.0 | 1.4 | 17471.6 | 86.0 | 0 | 0.0 | 0.0 |
| 1 | OpenClaw | 2026.10.8 | cloud | v3b | 45 | 24 | 26 | 3 | 3 | 6 | 0 | 34.2 | 5.0 | 79294.8 | 368.2 | 67862.8 | 0.4859 | 0.0108 |
| 1 | Hermes | 9a0a1625 | local | v2 (reads open) | 45 | 1 | 0 | 1 | 0 | 0 | 23 | 301.8 | 5.3 | 51661.6 | 1827.8 | 0 | 0.0 | 0.0 |
| 1 | Hermes | 9a0a1625 | cloud | v2 (reads open) | 45 | 23 | 21 | 2 | 3 | 4 | 1 | 22.2 | 6.5 | 63930.2 | 453.4 | 57063.8 | 0.3709 | 0.00824 |
| 1 | Hermes | c73b595 | cloud | v3b | 45 | 26 | 29 | 2 | 5 | 2 | 0 | 31.1 | 6.7 | 74220.6 | 444.1 | 66195.9 | 0.4267 | 0.00948 |
| 2 | VOOL | fix14 39fc9527 loop+tools | cloud | vool.sb v3 (= rival v4 rules) | 45 | 34 | 39 | 0 | 0 | 0 | 0 | 8.0 | 3.7 | 2960.3 | 127.1 | 2434.8 | 0.0258 | 0.00057 |
| 2 | OpenClaw | 2026.10.8 | cloud | v4 | 45 | 27 | 32 | 1 | 5 | 1 | 0 | 34.1 | 6.5 | 100997.3 | 509.6 | 90325.3 | 0.5722 | 0.01271 |
| 2 | Hermes | c73b595 | cloud | v4 | 45 | 22 | 25 | 1 | 4 | 2 | 0 | 20.4 | 7.1 | 84488.4 | 552.6 | 77248 | 0.4645 | 0.01032 |

## Sources and n/a reasons

- Hermes c73b595 local (fresh-latest): not run yet / no rows file
- Hermes c73b595 local (r2-local): not run yet / no rows file
- Hermes local: tokens from its own sessions table (input/output/cache_read/cache_write, api_call_count)
- OpenClaw 2026.10.8 local (r2-local): not run yet / no rows file
- OpenClaw local: tokens from its own transcript usage per assistant message (input+cacheRead+cacheWrite = prompt)
- VOOL fix14 39fc9527 local (r2-local): not run yet / no rows file
- VOOL local: tokens = provider-reported token_usage on model.call_completed events; Ollama reports no cache figures (n/a)
- cloud: cache_write_tokens n/a (OpenRouter usage for GLM 5.3 flashx reports cached reads only)
