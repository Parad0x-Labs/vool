# v14.6 hardening item 7: overfitting scan of the production diff since v13 (fd6898fd..e1672236, core/ only)

Method (11:50Z): the added, non-comment lines of `git diff fd6898fd..HEAD -- core` (3,020 lines, 15 files) were searched for
(a) benchmark question ids (q + 16 hex, LoCoMo and sealed-set ids), (b) gold answer strings from the sealed sets' gold files
and the fixtures' gold fields, (c) rare tokens (3 or fewer occurrences) drawn from every fixture question, gold and raw reply
plus the sealed golds, (d) literal dates and epoch numbers, (e) regex alternations naming one fixture's nouns.

Findings:
- (a) No question id in any code path. Ids appear only in comments and docstrings that cite the miss a rule was built for
  (q4bac36e6..., q4181...): keep, they are provenance, not branches.
- (b) No gold string in code. The docstring of core/unsourced_current_claim.py quotes "$283 total: shoes $140, bike lights
  $48, helmet $95" as the measured example of the withdrawn totals: keep (documentation of the failure class).
- (c) 90 rare fixture tokens matched added lines; every match is either a docstring example (helmet, shoes, lights, flash
  unit, tunnel route, ridge walk, Skoda Octavia, coding exercises) or a closed-class English word inside a generic
  vocabulary (preference verbs, aggregate words, live-world quantities, months, weekdays, units, possessors). No branch
  tests for a fixture noun. Keep all.
- (d) No literal benchmark date or epoch in code; the only dates are docstring examples (2023-05-29 in the receipts
  module's worked example). Keep.
- (e) Alternations reviewed by hand: _PREFERENCE_RE (receipts and compiler), _STATE_RE/_STATE_NOUN_KEYS, _FORMER_STATE_RE,
  _QUESTION_STATE_KEY (vehicle, employer, home_city, job, school, team, pet, phone, diet), _LIVE_WORLD_RE,
  _RECORD_MODIFIER_FALLBACK_RE (its exclusions are English -est words that are not superlatives: interest, forest,
  harvest, honest, priest), _DIFFERENCE_ASK_RE, _SEQUENCE_RE, _EXTREMUM_RE, _ABSENCE_RE, _LIST_ALL_RE, _AGGREGATE_ASK_RE.
  Each is a class of wording, not a case; each carries members no fixture used. Keep. Two entries worth a note, kept as
  class members: "early riser|night owl|morning person" in the preference regex (set five's wording was "early riser";
  the class is chronotype statements) and the unit noise list gaining hours and minutes (set five's "by how many minutes";
  the class is units, days and weeks were already there).

Verdict: nothing to remove. Failure-class fixes only; no hard-coded answers, no fixture-only branches.
