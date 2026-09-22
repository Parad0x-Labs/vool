# Spend caps and uncertain billing

For a BYOK paid model, VOOL reserves an estimated maximum before dispatch. Per-call,
per-task, UTC-day and calendar-month USD limits govern admission. The cap checks and
reservation write share a transaction, so simultaneous turns see each other's liabilities.
Call counts remain usage statistics; the current policy does not enforce a call-count quota.

A priced response replaces its reservation with the provider-reported cost or a token-based
estimate. Unknown model prices use a conservative rate ceiling. Neither an estimate nor a
local cap guarantees the provider's eventual invoice: prices, token accounting and several
in-flight calls can differ from the reserved estimates.

## Missing usage and failed dispatch

A response without usage leaves its reservation held as `billing_ambiguous`. A failure after
dispatch also retains the hold because an error is not proof the provider billed nothing.
These holds are durable and count against subsequent task, day and month admissions. A
later preflight refusal cannot release a prior uncertain charge. A successful retry records
its known cost without inventing the failed attempt's bill; the earlier uncertainty remains
held. Explicit reconciliation with a known total clears a hold. There is currently no automatic
provider-invoice reconciliation, so unresolved holds can conservatively block later work.

Receipts expose unknown actual totals as unknown, known charges separately, and the budget
still held. Ask `how does the spend cap work` for the configured ceilings, reported usage and
unresolved budget held today. The usage report is not the budget liability ledger.

## Provider and wallet boundaries

BYOK calls go directly from the local application to the configured provider using the
owner's key. VOOL cannot refund or cancel a charge already incurred. Configure a limit at
the provider and verify its terms if an enforceable billing ceiling is required. A budget
alert only notifies. Prepaid credit with auto-reload disabled can give a harder limit when
the provider enforces it.

UsePod wallet payments and prepaid inference use separately approved money grants and an
atomic liability ledger in the payment asset's native units. The BYOK USD caps above do not
cover that lane. A credit top-up is prepayment, not proof that a later service was delivered.
