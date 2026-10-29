# Tool card drafts, 2026-10-29

- Source: delivery/modular-tools-20261029 @ cd8b765, `python -m tools.tool_card_author`
- Model: claude-sonnet-5-5 via the org-credit API helper; job cap $6
- Tools without a card: 166. Drafted: 166 (2 in a 2-tool dry check, then 164 in the full run). Failed: 0.
- Retries: 1 (sell.quote, first reply had no JSON; passed on retry). 168 model calls.
- Spend: $1.94 (helper ledger rows for this job: helper-ledger-rows.csv)
- Run time: full run 1603 s (~27 min), plus about 17 s for the dry check
- Every draft passed core.tool_cards.parse_card. Each has 14 examples (English plus es/fr/de/lt) and 6 near misses.
- Drafts are proposals: someone reviews them before they are copied into core/tool_card_data.
- Branch delivery/modular-tools-card-drafts-20261029 committed locally, not pushed (the push needs sls's own OK).
