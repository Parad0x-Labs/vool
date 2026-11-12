---
description: Create shielded Zcash invoices and see which were paid, using a view-only key that cannot spend. Off by default; setup is for people comfortable with a terminal.
---

# Private invoices with Zcash

Get paid in Zcash without exposing your balances or history, and without VOOL ever holding your
money. VOOL creates invoices and watches for payment with a view-only key: it can see incoming
payments and cannot spend anything.

Zcash invoices are off by default and separate from VOOL's wallet and spending. Nothing here can
move money out. There is no switch for them in Settings yet: setting them up means building a
small tool and starting VOOL with a few environment variables, so this page suits people who are
comfortable with a terminal or run VOOL from source.

## What is this for?

Receiving payments privately: an invoice for a client, a friend or a customer, settled in
shielded Zcash, with a record of exactly which invoices were paid.

## Set it up

### 1. The watch tool

VOOL watches the chain through `zcash-devtool`, run in view-only mode. It is not included in the
downloads. Build it with the small patch that ships in VOOL's source code
(`core/zcash/devtool/init-fvk-stdin.patch`), which lets VOOL hand the viewing key over without it
ever appearing in a command line. An unpatched build cannot read the key and VOOL stops.

Then start VOOL with:

```bash
VOOL_ZCASH_ENABLED=1
VOOL_ZCASH_DEVTOOL=/path/to/zcash-devtool
VOOL_ZCASH_DEVTOOL_SHA256=the-sha256-of-that-file
```

VOOL checks the file against that SHA-256, copies it into its own owner-only folder and runs only
that checked copy, re-checked every time. It runs only commands that read or derive from the
viewing key; anything that could create, restore, send or reveal a spending key is refused before
it starts.

### 2. Your viewing key

With the same environment variables set, using VOOL's own Python environment:

```bash
python -m core.zcash set-key
```

It asks for a unified full viewing key (it starts with `uview1`) and does not echo it. Never give a
spending key or a seed phrase: VOOL does not need one and refuses anything that can spend, saying
why. Add `--birthday HEIGHT` (a block height) to scan from an earlier block if the wallet already
received payments you want VOOL to see; otherwise scanning starts near the chain tip.

When it works, it prints the network and your shielded receiving address. A second, different key
is refused: the watch wallet is never quietly pointed at another one.

### 3. Create invoices in chat

Ask in plain words, for example *Invoice Alex 0.5 ZEC for the design work*. VOOL answers with:

* a standard ZIP 321 payment link, which any wallet that understands ZIP 321 links can pay;
* the memo, `VOOL-` followed by the invoice's ID, which is how VOOL matches the payment to the
  invoice;
* a QR code page, which opens only on this computer.

Write amounts as plain digits, with at most 8 decimal places and no thousands separator, for
example `1000` or `0.05`. An amount that could be read two ways is refused.

### 4. Ask what is outstanding

* *Who still owes me ZEC?* lists the invoices that are not settled, each as unpaid, unconfirmed,
  underpaid or unknown, plus any payment that names no invoice.
* *Show my Zcash payments* lists what was paid, with amount, memo, transaction ID and receipt. Add
  dates or a name to narrow it.

From the command line you can also run:

| Command | What it does |
| --- | --- |
| `python -m core.zcash address` | Prints your shielded receiving address |
| `python -m core.zcash status` | Syncs, then prints every invoice's state |
| `python -m core.zcash export --since 2026-11-01 --until 2026-11-30` | Writes the paid invoices between two dates (YYYY-MM-DD) as CSV |

## What permission or connection does it require?

* **Your viewing key.** It is read-only by design and kept with VOOL's other saved secrets. It is
  removed from logs and bug reports.
* **Network access, approved like any other network action.** Creating an invoice stays on this
  computer. Checking for payments syncs the view-only wallet with a public Zcash server, so it is
  refused in **Plan** and asks first in **Manual**; see [Permissions](../concepts/permissions.md).
* **Offline.** With **Offline** chosen in the model menu, VOOL starts nothing and contacts no
  one; the answer is unknown, never *unpaid*. The message calls this *Local Only*.

The view-only wallet and the invoice records are kept in an owner-only folder in VOOL's data
directory, one per network.

## What can go wrong?

VOOL marks an invoice paid only after the payment has enough confirmations, three unless you
change it, and writes exactly one receipt for it, even if VOOL stops part-way or two checks run
at once. If a paid invoice's transaction later drops off the chain, VOOL says so; the earlier
receipt stays as a record, not as paid.

When VOOL cannot be sure, the answer is unknown, never *paid* and never *unpaid*:

| VOOL says | What it means |
| --- | --- |
| The watch wallet has not synced yet, or has not scanned up to the latest block yet | It has not caught up. Check again shortly |
| The last sync failed, or the last successful sync is too old | No recent good sync (older than 15 minutes). Check the connection and try again |
| The watch wallet does not match the saved viewing key | The key and the wallet disagree. Set the right key |
| The computer's clock moved, so the last sync cannot be dated | Check the clock, then check again |
| A payment arrived in a Zcash pool this VOOL version cannot read yet | Shown as unknown rather than guessed |
| Its receipt could not be written yet | VOOL tries again on the next check |
| Local Only is on, so VOOL did not contact the Zcash network | **Offline** is chosen, so nothing was checked |

If the watch tool is missing, has no SHA-256, or does not match it, VOOL does not run it and says
which.

A payment that arrives without a matching memo appears as a payment that names no invoice, with
its amount and transaction, so you can match it yourself.

Zcash invoices are new. The view-only wallet, the sync and the invoice records are tested; a real
payment detected and receipted end to end on a live network has not been shown yet.

## Advanced settings

These environment variables are read by VOOL as it runs; set them before you start it.

| Variable | What it sets |
| --- | --- |
| `VOOL_ZCASH_NETWORK` | `mainnet` (the default) or `testnet`. A test-network key starts with `uviewtest1` and amounts are in TAZ |
| `VOOL_ZCASH_CONFIRMATIONS` | Confirmations before an invoice counts as paid, 1 to 1000. Default 3 |
| `VOOL_ZCASH_SERVER` | The Zcash server the watch tool syncs with. Default: the tool's own `zecrocks` |
| `VOOL_ZCASH_CONNECTION` | `direct` (default), `tor`, or `socks5://host:port` |
| `VOOL_ZCASH_STALE_AFTER_SECONDS` | How old the last good sync may be before answers are unknown. Default 900 (15 minutes) |

## What do I do next?

* Set it up, then create a small test invoice and pay it from another wallet.
* For paying for things rather than getting paid, see [Pay for web resources](paid-resources.md)
  and [Wallet](wallet.md).
