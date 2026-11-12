---
description: How VOOL pays websites and AI services that charge per request (x402), the payment channels, agent spending and pay-per-answer AI, and what happens when a payment goes wrong.
---

# Pay for web resources (x402)

Some websites and AI services charge per request. Instead of a login, they answer with HTTP 402
*Payment Required* and an offer: an amount, a coin, who gets paid, on which network. x402 is a
standard for that offer. VOOL's wallet can pay it, and every money decision stays yours.

In this release, websites that ask for x402 payment in USDC on Base can be paid from your Crypto
Pilot wallet: one approval with your PIN, within your limits. The website's payment service pays
the network fee. No live mainnet payment has been made with this yet. See
[Release status](../trust/release-status.md).

## What is this for?

Using a paid search, a paid data source or a paid AI from inside a conversation, without handing
anyone a card number or an account, and without the risk of paying for the same request twice.

## Before you start

* **Crypto is on** and you have a Crypto Pilot wallet on Base: **Settings → Crypto**. See
  [Wallet](wallet.md).
* **Web lookups is on** in **Settings → Privacy & Permissions**. A paid request asked from a chat
  is refused while it is off, and nothing is paid.
* The payment channel **x402 on Base, through the x402 Foundation SDK** is on. It is on unless
  you turned it off.

## How a payment goes

When a request VOOL makes meets a 402 offer:

1. VOOL turns the offer into one payment proposal, bound to the exact request that met it. It
   shows the amount, the coin, who gets paid and the network. Nothing is signed yet.
2. You approve it in the wallet with your PIN or password.
3. The amount is held against your spending limits at the moment you approve, so a payment can
   never be approved without counting.
4. VOOL signs exactly the approved payment, once, and sends the same request once, with the
   payment, to the same site.
5. A receipt is recorded. VOOL believes the payment settled only when the chain shows it.

### The most one paid request may cost

An offer above the automatic cap is not paid and no proposal is made
([`wallet_x402_cap_exceeded`](../ERROR_BOOK.md#wallet-x402-cap-exceeded)). The cap is 100,000 of
the coin's smallest units unless changed, which is 0.10 USDC. The wallet panel shows it as
*Most one paid web request may cost*. It is changed only with the `VOOL_WALLET_X402_CAP_MINOR`
setting when VOOL starts. Your spending limits apply on top of it; see
[Spending limits and price guards](spending-limits.md).

## Payment channels

**Settings → Crypto → Payment channels** lists every way the wallet can pay, each with its own
switch, who built it and which networks it pays on:

| Channel | What it pays |
| --- | --- |
| x402 on Base, through the x402 Foundation SDK | USDC offers on Base. The SDK builds the authorization, your wallet approves and signs it, and the site's payment service settles it and pays the network fee |
| x402, built into VOOL | Offers on the test networks only (Developer options) |
| x402 on Solana, through Solana pay-kit | Not included in the downloads; shown as not usable on this install |
| MPP on Solana, through Solana pay-kit | Not included in the downloads; shown as not usable on this install |

Turning a channel on asks for your wallet PIN or password; turning it off never asks. A channel
that is off pays nothing ([`wallet_payment_channel_off`](../ERROR_BOOK.md#wallet-payment-channel-off)).
A payment already waiting on it cannot be approved until you turn the channel back on.

Whichever channel a payment goes through, the cap, your approval, the hold against your limits,
the one signature and the receipt are your wallet's, never the channel's.

## Agent spending

**Settings → Crypto → Agent spending** lets VOOL pay small amounts to services you name without
asking you each time. It pays only from its own small wallet, which you fill from yours with an
ordinary approved transfer. Your own wallets are never used without you.

You set an allowance for that wallet: the most for one payment, the most per hour, day, week or
month, the most in total, the most payments, how many days it lasts (up to 90), and optionally an
amount above which it always asks you. You list the services it may pay, each with its own total
if you want. Anything outside those limits waits for your approval.

* The address a service is paid at is fixed when you approve its first payment. If it ever
  changes, VOOL asks you.
* VOOL pauses the allowance after three requests in a row that look wrong (a changed pay-to
  address, a sudden price jump, requests coming too fast) or two payments in a row whose service
  did not deliver.
* Allowing more asks for your PIN or password. Tightening a limit does not.
* When VOOL splits a job between helper agents, a helper may pay only if you set a helper amount
  in the allowance. Leave it empty and helpers pay nothing.

Keep in that wallet only what you are willing to lose: its key works without a prompt on this
computer, so other software running as you could spend its balance.

## Pay-per-answer AI

**Settings → Crypto → Pay-per-answer AI** adds an AI service that charges a small fee per answer,
in USDC on Base: a name, its `https://` address, the model name exactly as the service lists it,
and the most one answer may cost. Adding one asks for your PIN or password.

Then ask it from any chat, for example *ask my paid AI: …*, and name it when you have more than
one. Only your question is sent, never the rest of the chat. You approve each payment first, and
it stays under your limits and the price you set. VOOL checks that each answer comes from the
model you chose; if the service names a different model, or none, VOOL pauses it until you turn
it back on. VOOL cannot see what really runs behind the service.

## What is kept with a payment

Credentials and cookies from the original request are never stored and never sent again. Only
the method, the body (up to 64 KB) and the Content-Type, Accept and Accept-Language headers are
kept with the proposal, and the paid request goes only to the site that asked for payment.

## What can go wrong

VOOL is built so that the worst case is an unknown outcome, never a double payment.

| Situation | What VOOL does |
| --- | --- |
| The same offer reaches VOOL twice, or at two spellings of one address | It stays one proposal, paid at most once |
| A request that was already paid is asked for again | It is not sent again with a payment ([`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment)) |
| A payment's outcome is unclear: the request may have reached the site | The outcome is unknown. The hold stays, and nothing is retried |
| The offer is above the automatic cap | Not paid, no proposal ([`wallet_x402_cap_exceeded`](../ERROR_BOOK.md#wallet-x402-cap-exceeded)) |
| The offer needs a payment method the wallet cannot check end to end | Nothing is signed ([`x402_scheme_unavailable`](../ERROR_BOOK.md#x402-scheme-unavailable)) |
| A wrong PIN or password | Nothing is signed, and the attempt leaves a receipt. The payment keeps waiting; too many wrong attempts end it |
| The offer's terms changed after you were asked | The earlier approval does not carry over; a new proposal is needed |
| A payment would go over a spending limit | It is not sent ([`wallet_limit_exceeded`](../ERROR_BOOK.md#wallet-limit-exceeded)) |

A proposed payment that ends without being sent keeps a receipt that says so.

## Test networks

Base Sepolia and the other test networks are for developers, in **Settings → Crypto → Developer
options**. Test-network accounts, balances and receipts stay separate from mainnet.

## What to do next

* Set or check your limits in [Spending limits and price guards](spending-limits.md).
* Read a payment's receipt — see [Receipts](../concepts/receipts.md).
* For an unknown outcome, check the payment's status in the wallet before doing anything else. A
  blind retry is exactly what VOOL is preventing.
* Getting paid instead of paying? See [Private invoices with Zcash](zcash-invoices.md).
