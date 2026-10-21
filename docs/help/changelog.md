---
description: Notable changes to VOOL.
---

# Changelog

Entries are added as builds go out to testers. Newest first.

## 0.7.3-beta — 2026-10-21

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.3-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.3-beta).
Updating from 0.7.2, 0.7.1 or 0.7.0: download 0.7.3 and install it over your current version. Your chats and settings
live in your profile, outside the app.

* **Goals.** Type `/goal` followed by what you want done. VOOL plans the steps and works through them, with a card above the message box to pause, resume or stop. A goal never spends money by itself, and it waits while the chat is private.
* **Run commands from chat.** More everyday tools run in VOOL's sandbox, such as make, go, uv, linters and formatters, and command lines chained together. The approval card shows the exact command before it runs. An install gets internet access only after your OK. A failed command shows its exit code and output.
* **Remove a calendar account** from Settings. VOOL forgets the calendars you chose, the events it copied and its pending alerts. Nothing changes in the calendar itself.
* **Apple Calendar and Contacts on Mac,** once macOS has your permission. For Calendar, VOOL asks only when you add an account or refresh the calendar list in Settings.
* **Paying a contact (crypto, off by default).** A payment to a saved contact goes only to the exact address you reviewed. If the contact changes, VOOL asks you to review it again.
* **Forget removes the text from the memory file.** Forget now erases the forgotten words from VOOL's local memory file and its search index, and from long text you pasted into that chat. "Forget A and forget B" forgets both, the count shows only memory that was really removed, and curly apostrophes work too.
* **"Go online on its own" covers more.** With the switch off, VOOL also skips update checks and online calendar sync, and says so calmly. Calendars on this computer keep working.
* **Plain words in Activity.** The Activity panel and the card under each reply say what happened in plain words, in your app language. Failures show as failures.
* **No model yet? VOOL says so,** with a "Set up a model" button, even before your first message. It also finds a local model at another address on this computer.
* **Better Auto model choice.** Auto picks only a model that can do what the task needs. A model you pick yourself handles the whole answer, tool steps included. A brief hiccup from a free model is retried once.
* **One chat, two windows.** A new message waits its turn while the chat is answering in another window.
* **Exported chats with real names,** named after the chat and the date, with a short header that says what is left out.
* **Notes that make sense.** "Write a note to my neighbour" writes the note; "Take a note to buy milk" still saves it.
* **Clearer Wallet panel.** Exact amounts, real times, and a "Not sent" receipt when a payment is declined or cancelled. On mainnet, a connected or watch-only account can receive and show its balance but cannot send from VOOL.
* **Security updates.** pillow 13.0.0 and certifi 2026.10.19.
* Spending limits count each paid cloud answer once; a charge VOOL cannot confirm is held at that call's approved limit.
* One question keeps one answer after a reload.
* After you press Allow, the action you approved is the one that runs.
* Cooking questions like "what oven temperature for roasting potatoes" are no longer treated as weather lookups.
* Very long answers keep streaming smoothly to the end. Keyboard hints show Ctrl on Linux.

Known limits in this beta:

* Skill Codex: only the skill's text goes along when you share, so a skill with extra files cannot be shared yet.
* After a forget, VOOL keeps the words you asked it to forget in a short list on your computer, so it can keep refusing them.
* On Intel Macs, local models run on the processor and are slow. A long memory answer can time out, so use a cloud model there for now.
* Paying websites through Solana pay-kit is not included in the downloads.
* No live mainnet payment has been made yet with x402 or pay-per-answer AI services.
* Before a local model can write answers, press **Certify** for it once (Settings > Models & Providers > Local models).
* Scanned-PDF reading works on Macs only.
* The macOS apps are not notarized.

## 0.7.2-beta — 2026-10-20

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.2-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.2-beta).
Updating from 0.7.1 or 0.7.0: download 0.7.2 and install it over your current version. Your chats and settings
live in your profile, outside the app.

* **Try again and edit.** Every reply has Try again, and you can edit and resend your own message. Attachments come along.
* **Themes.** Light, Dark or Match system, plus bigger conversation text.
* **Bring your chats with you.** Import chats from another AI app on this computer or from an export file. Chat titles and messages come over.
* **"Go online on its own" switch, off by default.** No downloads or cloud checks on VOOL's own until you allow it.
* **X from any chat.** Search posts and accounts, Spaces, trends and DMs, and look up past calls on a token. Anything that acts on your account waits for your OK.
* **Email accounts.** Add one from Settings. The login and the server's certificate are checked before saving.
* **Cloud web search.** Cloud models can use their own built-in web search when you switch it on. Fees still go through your spending limits.
* **Crypto (off by default).**
  * Send USDT from chat on Solana, Ethereum, BNB Smart Chain and Arbitrum One, with your approval, limits and a receipt.
  * All your balances on every chain in one answer.
  * Save a friend's wallet on their contact, with the right chain.
  * Watch alerts for a wallet or a token price. Read-only; VOOL never trades.
  * Trader tools: who is buying, selling or holding a token; collect its trades; check a thesis claim by claim.
  * Pay-per-answer AI services, paid from your VOOL wallet only after you approve.

* **Clean streaming.** No raw symbols flash on screen while an answer arrives.
* **Calmer chat.** Jump to latest, clear tab titles, one icon style, and a friendly "are you sure?" before deleting.
* **Esc stops the answer,** every time.
* **Your words, as typed.** Messages in your own language are kept as you wrote them.
* **Desk pet.** Says what VOOL is doing, stays quiet otherwise, and keeps problems on screen.
* **Private chat in every app language.** Works in all 17 languages and leaves nothing behind.
* **Deeper forget.** Forget also clears the matching run history, and "forget 12" no longer touches "room 312".
* **Calendar.** Understands "next Friday" or "for two hours", warns about clashes, and no longer reads "3 apples" as a date.
* **Safer installs.** Safety checks run again right before any skill or plugin installs; a failure shows on the skill's card.
* **Secrets.** Kept out of more logs and files, with clearer messages when an API key is blocked.
* **Slower computers.** Local models on computers without a graphics chip get more time, so answers finish.
* **Stop VOOL, then start again.** VOOL now restarts properly after "Stop VOOL".

Known limits in this beta:

* Skill Codex is not open yet. Browsing, installing and sharing skills start once it opens.
* A forgotten item is no longer recalled or listed, but its text can stay in VOOL's local memory file. Removing it from that file is planned for 0.7.3, not released yet.
* On Intel Macs, local models run on the processor and are slow. A long memory answer can time out, so use a cloud model there for now.
* Paying websites through Solana pay-kit is not included in the downloads.
* No live mainnet payment has been made yet with x402 or pay-per-answer AI services.
* Before a local model can write answers, press **Certify** for it once (Settings > Models & Providers > Local models).
* Scanned-PDF reading works on Macs only.
* The macOS apps are not notarized.

## 0.7.1-beta — 2026-10-14

Downloads for Apple Silicon Macs, Intel Macs and Linux x86_64 are on the
[v0.7.1-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.1-beta).
Updating from 0.7.0: download 0.7.1 and install it over 0.7.0. Your chats and settings
live in your profile, outside the app.

* **Private chat.** Say "private chat", "don't record this" or "off the record", and
  nothing from that chat is saved: no transcript, no memory, no summary, no log. A small
  ghost marks its messages. Say "turn recording back on" to end it. Private chat commands
  work in English only.
* **Secrets stay out.** PINs, card numbers and passwords you type are kept out of memory
  and out of later prompts sent to a model.
* **Forget, the way you say it.** "Forget what I told you about X" removes it from VOOL's
  memory. After you forget something in a chat, VOOL won't take that same fact back in
  that chat; start a new chat to tell it again. The original chat messages stay in VOOL's
  local activity records for now.
* **Answers as files.** Word, spreadsheet, slides, PDF, Markdown, CSV, JSON, YAML, XML,
  HTML, ZIP or tar.gz. VOOL checks that each file opens before offering it.
* **Follow-ups keep the task going.** A message sent while VOOL is working joins the
  running task instead of restarting it.
* **Scanned PDFs** are read with the Mac's built-in text recognition (Macs only).
* **No surprise tool fees.** VOOL refuses a cloud model that runs paid tools at the
  provider without reporting the cost.
* **Custom endpoints and keys** go where the endpoint expects them, and their models are
  listed.
* **Intel Macs.** Local-model certification allows for slower processors. Long local
  answers can still time out there, so use a cloud model on Intel for now.
* **Crypto, off by default.** Arbitrum One joins the supported chains. Settings → Crypto →
  Payment channels has an on/off switch for each way the wallet can pay a website. Websites
  that ask for x402 payment in USDC on Base or Base Sepolia can be paid from your Crypto
  Pilot wallet, one PIN approval within your limits. No live mainnet payment has been made
  with this yet. Solana pay-kit is not included in the downloads.

## 0.7.0-beta — 2026-10-13

Downloads for Apple Silicon Macs, Intel Macs and Linux x86_64 are on the
[v0.7.0-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta).

* **Runs on more computers.** Separate downloads for Apple Silicon Macs, Intel Macs and
  Linux x86_64. The Mac app carries its own local-model runner.
* **Installed by download.** Install 0.7.0 from the release download.
* **Better memory.** VOOL recalls what you told it, forgets on request, and says so when
  there was nothing to forget.
* **Your rules.** Set rules for everything, one chat or one project. Ask "what are my
  rules", and remove one by its number.
* **Personal assistant fixes.** Reminders and calendar use your time zone, past reminder
  times are refused, all-day events land on the right day, and email connections check
  the server's certificate.
* **Chain data in chat.** Add your own Helius, Alchemy or Solscan key for read-only chain
  data in chat. With no key, no network call is made.
* **X.** Draft posts in chat, with a guided setup that checks your keys read-only.
* **Report a wrong answer.** Report and 👎 on every answer; you see the report text, with
  private details removed, before it is sent.
* **Skill Codex** is on the Add-ons page. Browsing is not open yet in this release.
* **More languages.** 16 languages besides English.
* **Crypto stays off by default.** Paying websites with x402 started on test networks.
  The pay-per-call option in the model marketplace in Settings can pay from the Solana
  Mainnet wallet, one approval per call with the 0.1% fee shown; it has not yet been tested
  on a real chain.

The Mac apps are not notarized yet: see
[first open on a Mac](../getting-started/install.md#first-open-on-a-mac).
