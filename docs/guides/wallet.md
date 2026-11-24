---
description: The optional crypto wallet — named wallets, backups in Keychain or on a USB stick, the Skills & plugins wallet, sending with approval and receipts, freezing payments, and getting back in after a forgotten PIN.
---

# Wallet

The wallet is **optional and off by default**. It lets VOOL hold a small amount of crypto for
services that charge per request, and lets you send and receive on a few networks. Nothing about it
is required to use VOOL, and nothing goes online for it until you turn it on.

The wallet is a **pilot feature**: keep small amounts in it. Its code has not been checked by an
outside reviewer yet.

## Turn it on

1. Open **Settings → Crypto** and turn on **Enable Crypto**. Turning it on creates, signs and
   sends nothing.
2. Pick a network (Solana is a good start) and press **Create wallet**.
3. Choose how you approve payments: a PIN, a wallet password, or, on a Mac, **Touch ID or your
   Mac password**. Type it twice.
4. Your **backup key** is shown once. Write it on paper or save it in your password manager, then
   press **I have saved my backup**. VOOL cannot show it again.

The wallet runs on the main networks: Solana, Base, Ethereum, BNB Smart Chain, Arbitrum One and
Robinhood Chain. Each wallet belongs to one network. **Which coins can I send?** under **Your
wallets** lists what each network can send: its own coin (for example SOL or ETH) and, on some
networks, USDT. Other tokens may show a balance but cannot be sent from here.

## Your wallets

Settings → Crypto shows your wallets grouped by network. Each has a name and a badge:

* **Main** — one per network. Payments VOOL starts for you (contacts, top-ups, chat payments) come
  from here unless you name another wallet. Your first wallet on a network becomes Main.
* **Everyday** — a separate wallet with its own key and backup. VOOL uses it only when you name it.
* **Burner** — for one-off payments. VOOL never uses it by itself. Once it has paid, it shows
  **Burner, used**: send what is left to another wallet and delete it whenever you like.

To add one, press **Add another wallet** under a network, then choose **What is it for?**
(Everyday or Burner) and give it a name. Each new wallet has its own key, its own backup and its
own PIN or password.

Under each wallet:

* **Rename** changes its name. It needs no PIN.
* **Make main** (Everyday wallets only) moves your Main to this wallet. It asks for this wallet's
  own PIN or password.
* **Delete wallet** removes it and its key from this computer. Tick that you have its backup key
  (or that it is empty) and type its PIN. Coins left in it stay at its address, and only its backup
  key can reach them again. A wallet with a payment still on its way or waiting for approval cannot
  be deleted. If you delete the Main wallet, the oldest remaining wallet on that network becomes
  Main.

## Keep a second copy of the backup

After setup, VOOL suggests a second copy. Both options below lock the backup key with a **backup
password** you choose: at least 12 characters, different from your PIN, and never stored. Without
it nobody, not even VOOL, can open the backup.

* **Save backup in Keychain** keeps it in your computer's secure store (Keychain on a Mac, the
  system keyring on Linux).
* **Save backup to a USB stick or drive** writes it to a USB stick or external drive you choose.
  Keep that drive away from this computer.

Each wallet shows **Keychain backup: saved** or **USB backup: saved on** the drive's name. Writing
the backup key on paper is still the safest second copy. **Remove the Keychain backup** deletes the
Keychain copy.

## Forgot your PIN, or lost a wallet

* **Forgot PIN or password?** under a wallet lets you type its backup key and choose a new PIN or
  password.
* **Forgot PIN? Use the Keychain backup** does the same with your backup password.
* Under **Backups on a USB stick or external drive**, press **Look for backups**. For a wallet on
  this computer, **Set the new PIN**. For one that is not (a deleted wallet, or a new computer),
  **Bring it back**. Only bring back your own backups: whoever made a backup still knows its key.
* **Wallets you can bring back from the Keychain** lists Keychain backups whose wallet is not on
  this computer. Type the backup password and press **Bring it back**.

Restoring only puts the same key back under a new PIN. It signs and sends nothing. A backup that
does not fit the wallet is refused and nothing changes. Too many wrong tries lock it for a while.

An older VOOL wallet, made before this pilot, can export its private key once. The export opens only
with your Mac's own sign-in (Touch ID or your Mac password), never with the wallet PIN.

## Send and receive

* **Receive** shows your wallet's address for that network only. **Refresh balance** reads it; a
  balance that cannot be read says so and is never shown as zero.
* **Send** (in the VOOL Wallet panel): pick the wallet, type the address or **choose from
  contacts**, choose what to send, then **Preview send**. The preview (a quote) shows the amount, the
  most the network fee can be, the recipient and the network.
* **Approve and send** with your PIN, password or Touch ID. The approval is bound to exactly that
  preview: change anything and it no longer counts. **Reject** signs and sends nothing.
* A receipt is recorded for every payment, and for every refusal. **Payment history** under a wallet
  lists its transfers with a link to the network's explorer.

Only a wallet created in VOOL on this computer sends. A watch-only address or a connected outside
wallet can receive and show its balance, but cannot send from here. There is no swap or bridge.

### Caps, chat and the freeze

* **Spending caps** (per payment, per day, per recipient per day) apply to every payment. A payment
  above a cap is refused before it reaches approval. Nothing goes around caps, approval or receipts.
* **The chat proposes, you approve.** You can ask VOOL in chat to pay someone; it prepares a payment
  for you to review. It can never approve one.
* **Emergency freeze.** Type *freeze* (or *emergency stop*, or *panic*) in chat and no payment can go
  through until you lift it. Type *unfreeze* on this computer to resume; your computer asks you to
  confirm. A freeze made from a phone is lifted only in Settings on this computer with your wallet
  PIN.

### A transfer that seems stuck

If VOOL cannot confirm a transfer, it says the outcome is unknown and keeps it counted against your
limits. Depending on the case, the transfer offers:

* **Stop waiting** — VOOL stops watching. It may still go through, so it keeps counting against
  today's limits.
* **Send again** — sends exactly the transaction you approved. It cannot pay twice.
* **Discard** — for a signed transfer that never left this computer. Nothing was paid.

## Agents & plugins

* **Agent spending** lets VOOL pay small amounts to services you name, from its own small wallet
  that you fill, inside an allowance you set. See
  [Pay for web resources](paid-resources.md).
* **Skills & plugins wallet** is a separate wallet for skills and plugins that pay for services.
  Press **Create the Skills & plugins wallet**, save its backup key, and send it only what you are
  willing to let them spend. Nothing is allowed until you tick a plugin; each ticked plugin gets its
  own limits (most per payment, per hour, day or week, most payments, an end date, and an optional
  amount above which it always asks you) and can pay only the services it declared. Unticking ends
  it at once. Your own wallets are never used for plugins, and a skill that is only instructions
  cannot pay by itself.

## Good to know

* VOOL adds no service fee to any payment in this version. Sending, creating a wallet, local models
  and your own API keys are free of VOOL fees; you pay only the network fee.
* Payments to websites that charge per request are covered in
  [Pay for web resources](paid-resources.md); limits for paid AI models in
  [Spending limits and price guards](spending-limits.md).
* **Stay safe: how people lose their crypto** in Settings explains the common tricks in a few words
  each.
