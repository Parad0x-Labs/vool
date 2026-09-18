---
name: vool-browser-qa
description: "QA a web page or running site read-only: fetch the rendered page, verify expected content and structure, and report pass/fail per check. Use when the user asks to check a page, verify a site renders, or QA the web surface."
version: 1.0.0
license: MIT
author: sls_0x
status: p1
type: capability_skill
triggers: [browser, rendered, page check, verify the page, site qa, rendered page]
allowed-tools: [web.fetch, web.search]
capabilities: [web.read]
permissions: [read_files]
effects: [read_only]
inputs: [url, expected_content list]
outputs: [qa_checklist_results, fetched_page_evidence]
stop-conditions: [every expected check has a pass or fail with fetched evidence, fetch refused or blocked - report the refusal, not a guess about the page]
recovery: [if the rendered fetch fails, fall back to the raw fetch and label the evidence weaker, if a check needs interactivity this lane cannot do, report it as untested rather than inferring]
cumulative-test-law: [tests/native_skills must pass before this package changes, selection phrase "the rendered page in the browser lane" must keep selecting this package, claims about a page must cite fetched evidence, never memory]
id: browser-qa
risk-class: read_only
task-families: [integration_orchestration]
capability-families: [web]
tool-intents: [web.fetch, web.search]
permitted-tools: [web.fetch, web.search]
prerequisites: []
expected-outputs: [qa_checklist_results, fetched_page_evidence]
verification: [browser_verified, evidence_cited]
stopping-conditions: ["stop when every checklist row carries observed evidence — a row with no evidence is UNVERIFIED, never green"]
incompatible-with: []
priority: 30
---
# Browser QA (read-only lane)

You verify pages against expected content with FETCHED evidence. Every claim
cites what the fetch actually returned.

## Honest limits (read these first)

This package declares `web.read` only: fetched-page QA. INTERACTIVE browser
driving (click, type, screenshot a live session) is NOT wired into the runtime
at this version — the browser-render contract exists but has no executor lane
behind it, and this package will not pretend otherwise. Interactive checks are
reported as UNTESTED, never inferred.

## Required capabilities (declaring is not granting)

`web.read`. Declaring is not permission: fetches go through the transport door
with its size caps and refusal handling.

## Procedure

1. Collect the expected-content checklist from the user's ask.
2. `web.fetch` the URL; keep the evidence (status, extracted text).
3. Check each expectation against the FETCHED text — pass/fail per item.
4. `web.search` only to locate the right URL when the user gave a name, not a
   substitute for fetching the page itself.
5. Deliver the checklist results with the fetch receipt.

## Stop conditions

Stop when every checklist item has a verdict with evidence — including honest
UNTESTED rows for interactive checks.

## Recovery

Rendered fetch fails: fall back to raw fetch, label the evidence weaker.
Fetch refused: report the refusal verbatim; never guess the page's content.

## Cumulative test law

`tests/native_skills` is the pack; QA-behaviour edits re-run it.

## No direct execution bypass

Page fetching goes through the runtime tool door (`execute_runtime_tool`) and
its transport lane. No direct network reads around it.
