# Spend caps: a brake, not a fence

VOOL can burst a turn to a paid cloud model using **your** provider key. The spend caps
below decide whether such a call is allowed to start. This document states exactly what
they enforce, what they cannot enforce, and why — so you can decide whether they are
enough for you.

Short version: **VOOL will not start a paid call it estimates would breach your cap, and
it stops starting them once the cap is reached. It cannot promise you will never go a cent
over.** Each call is reserved at what that call is estimated to cost, so several turns running at once cannot all slip under the ceiling on the same small reservation. Overshoot is bounded by roughly one call's cost **only when the model's price is known**. When a provider returns no usage figures, or understates them, VOOL records less than was spent and the dollar ceiling stops advancing -- in that case the dollar cap does not bound the spend at all, and only the daily call count does.

## What VOOL never does

- VOOL never holds your money. There is no balance, no deposit, no credit with us.
- VOOL never proxies the paid call. Your key goes from your machine to the provider. The
  billing relationship is between you and the provider; VOOL is not in it.
- Because of both of the above, VOOL cannot refund, reverse, or cancel a charge. It can
  only decline to start the next call.

## What the caps actually are

Two independent gates sit in front of a paid call.

1. **A call-count cap per UTC day** — `daily_cap` in the cloud escalation policy. Auto
   escalation stops when the day's paid-call count reaches it.
2. **USD ceilings** — per call, per task, per UTC day, and per calendar month. A call is
   reserved against these before it runs; if the reservation would push a projected total
   past a ceiling, the call is refused and the turn stays local.

Ask VOOL `how does the spend cap work` for the values configured on this machine and
what has been used today. `cloud usage today` shows the token and dollar report.

## What the caps do not guarantee, and why

### 1. A call's cost is not knowable before it runs

Output length is only known once the model has finished generating. VOOL reserves against
an estimated maximum, but the true cost of the call in flight is settled afterwards. The
last call before the ceiling can therefore cross it.

### 2. VOOL settles from token counts times a published price

That is an estimate of the provider's bill, not the bill. Providers meter on their own
side and may count cached reads, system tokens, or tool tokens differently than VOOL
does. Only one provider family (OpenRouter) returns a cost field VOOL can settle against
directly; for the others VOOL computes the number itself from token counts.

### 3. Prices change and VOOL's table can be stale

The price table ships with the build and is refreshed from the catalog where a catalog
exists. A provider price change between refreshes makes every estimate on that model wrong
until it catches up.

### 4. A call already in flight cannot be recalled

Reaching the ceiling stops the *next* call. It does not abort a request the provider has
already accepted, and you are billed for what it generated.

## If you need a hard ceiling

Set the limit at the provider. That is the only real one, because the provider is the party
that meters and bills. Every major provider console has a spend limit, a budget alert, or
prepaid credits:

- Prepaid credit with auto-reload disabled is the strictest form — the account simply
  cannot spend past what is loaded.
- A hard monthly cap in the provider's billing settings is the next strictest.
- A budget *alert* is not a cap. It notifies; it does not stop anything.

Use VOOL's caps as the day-to-day brake and the provider's cap as the wall behind it.

## Current implementation status

The reasons above are permanent — they hold for any spend cap in any product. The paragraphs
below are about *this build* and are expected to change.

The paid-cloud lane on this branch is unaudited and its caps have been measured **not to bind
as described above**:

- Settlement reads a provider-supplied cost field that only OpenRouter returns. For every other
  provider the USD ledger currently settles at `$0.00`, so the dollar ceilings do not yet stop
  anything on those lanes. The call-count cap still applies.
- The cap check and the counter write are not atomic, so concurrent turns can each pass a cap
  that only had room for one. Overshoot under concurrency is therefore **not** bounded by one
  call's cost.
- A reservation is taken before the gates that decide whether anything runs, and is not always
  released, so the paid lane can quietly disable itself.

Until those are fixed, treat a provider-side limit as the only limit. This section is removed
when the measurements say otherwise.

## How to read a number VOOL gives you

When VOOL reports today's paid spend it will say whether the figure is the provider's own
number or VOOL's estimate. Treat an estimate as an estimate: correct in magnitude, not to
the cent. The provider's invoice is the authority, always.
