# The error-worker localization contract (Worker C ↔ the i18n lane)

Worker C owns stable fault codes and their structured records (`core/faults/`).
The i18n lane owns their localized presentation. This file is the seam between the two:
what each side must provide, and what enforces it.

## What Worker C owns (do not edit here)

- `core/faults/catalog.py`: the `FaultSpec` vocabulary — `code`, `category`, `severity`,
  `retry`, `user_message`, `operator_action`, `authority`. Meanings are frozen once shipped.
- The canonical record a fault carries at runtime (ids, redacted detail, paths). Records are
  NEVER translated, parsed for meaning, or rebuilt from prose.

## What the i18n lane owns

Every fault code MUST carry two presentation keys in `core/i18n/catalogs/*.json`:

| key | mirrors byte-exactly in English | localized as |
| --- | --- | --- |
| `fault.<code>.message` | `FaultSpec.user_message` | the human explanation |
| `fault.<code>.action` | `FaultSpec.operator_action` | the recovery / what-to-do text |

Structured parameters: presentations are static sentences resolved by pure functions
(`core.i18n.catalog.format_message`, mirrored in the served page as `VOOLFMT`). Fault
presentations currently take NO parameters — the authority deliberately keeps exception
text, paths and identifiers OFF the vocabulary (they live, redacted, on the record). If a
future fault presentation needs a parameter (for example a provider name or a limit),
agree the placeholder name here first: `{provider}`-style names, values passed as
already-redacted strings, never raw record detail.

## What a new fault code requires, in one change

1. Worker C adds the `FaultSpec` (English `user_message` + `operator_action`).
2. The same change adds `fault.<code>.message` and `fault.<code>.action` to
   `catalogs/en.json`, byte-equal to the authority fields.
3. The same change adds both keys to every shipped locale catalog and refreshes each
   catalog's `source_catalog_sha256` (the checker fails CI otherwise).

`tests/i18n/test_app_language_completion.py::test_every_fault_code_carries_both_presentation_keys_mirroring_the_authority`
is the gate: a code without both keys, or a drifted mirror, fails the suite. The runtime
authority itself is never looked up from the catalogs — consumers resolve the key through
`core.i18n.catalog.fault_message_key` and fall back to the authority's English.

## Integration status at this writing

All 48 fault codes carry both keys in English; the mirror test pins byte equality.
Locale catalogs carry the translations for their language (see the coverage note in
Settings → App language for per-locale completeness).

## Related seams (same pattern, other owners)

- Activity/event labels: every ledger event type the activity panel can label needs an
  `activity.ledger.<event_type>` key (guarded by the same test file).
- The UsePod payment-receipt prose: `usepod.receipt.*` keys with exact-value parameters;
  amounts are pre-formatted strings passed through untouched.
