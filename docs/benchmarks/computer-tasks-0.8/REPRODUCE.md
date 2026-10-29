# Reproduce

1. Test homes: `python3 round1/make_fakehome.py <dest>` (round 1) and `python3 round2/build_home.py <dest>` with round2/AUTHOR-REPLY.json
   next to it (round 2). Compare with the fixture-MANIFEST.json files (per-file sha256).
2. Tasks: round1/fresh-asks.json and round2/round2-asks.json; check their sha256 against round*/FROZEN.txt.
3. Rivals: OpenClaw 2026.10.8 (npm, node >= 26.12.1) and Hermes main c73b595 (git), September builds 2026.9.6 and 9a0a1625.
   Models: qwen3:8b on Ollama (local arm), z-ai/glm-5.3-flashx on OpenRouter at temperature 0 (cloud arm) through scripts/or_proxy.py.
4. Runner: scripts/run_h2h.py --system vool|openclaw|hermes|voolcloud --set fresh|r2 [--cloud] [--latest] [--vool-sandbox]; set the
   paths at the top of the script for your machine (<work> placeholders) and the Keychain item in or_proxy.py (<keychain-item-args>).
   Every rival ask runs in a fresh copy of the test home inside sandbox/rival-v4.sb (macOS sandbox-exec), 300 s cap.
5. Scoring: scripts/build_rows.py (regex scorer + token/cost join); blind jury: round*/jury (build_items.py, judge.py, summarize.py),
   judge claude-opus-5-5, tie-break claude-fable-5-1, prompt JUDGE-SYSTEM.txt.
6. Sandbox probe: `sandbox/sb_probe.sh sandbox/rival-v4.sb` prints the allowed/denied table.
