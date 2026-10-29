# Changelog

All notable changes to VOOL. Dates are YYYY-MM-DD. Earlier internal development
history is not republished here.

## [0.7.5-beta] — 2026-10-27

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.5-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.5-beta).
From this version on, VOOL tells you about new versions by itself and installs them with one press, on Mac and on Linux.

Updating from 0.7.4 or 0.7.3 (one time only):
- **Mac:** turn on "Go online on its own" (Settings > Privacy & Permissions), then press Check for updates once in the header update menu. Press Update. From 0.7.5 on, the notice comes by itself.
- **Linux:** install 0.7.5 from the release once. From 0.7.5 on, updates install in the app.
- From 0.7.2, 0.7.1 or 0.7.0: download 0.7.5 and install it over your current version. Your chats and settings live in your profile, outside the app.

### Added
- **Updates arrive on their own.** With "Go online on its own" on (Settings > Privacy & Permissions), VOOL checks for a new version at start and once a day and shows a notice. Press Update: VOOL installs it, restarts on the new version and keeps your data. With it off, VOOL fetches nothing and the header says Update checks off.
- **Linux updates in the app.** The Linux download now updates itself from inside VOOL. The new version is installed next to the old one; if it does not start properly, VOOL switches back by itself.
- **Your VOOL on the local network stays yours.** When VOOL listens on your local network, only this computer reaches settings, files, keys and tools. Another device gets plain chat and can read one public web page; web searches, research, the browser, quotes, payments and your paid keys stay on your own computer. The Discord bridge answers only its owner.
- **Notes and agenda do what you said.** Saving to notes, the agenda or Apple Notes keeps exactly what you asked for. An answer is saved only when you ask, only the answer you saw, and never from a private chat.
- **VOOL's own skills first.** Skills & Plugins lists VOOL's own skills first, and the app ships with 53 ready VOOL skills. Browse Skill Codex shows the list from the Skill Codex service.

### Fixed
- Reminders keep the right date and time.
- A message with a link while web lookups are off gets a plain answer instead of an error.
- The model list loads after an upgrade, even when two parts of VOOL open it at once.
- A reply you stop is recorded once, as you saw it.
- The first browser step on a slow computer no longer times out while the browser is still starting.
- The update status no longer shows an old setup message after an upgrade.
- Random IDs, dates and times are no longer mistaken for card numbers, while real card numbers are still caught.

### Security
- Research reads web pages only through checked public addresses: a search result or redirect that points at this computer or your local network is refused.
- A very long run of spaces in a message can no longer stall VOOL.
- Security updates for bundled libraries (PDF reading, packaging, reportlab and a network library).
- Installing from source now needs Python 3.11 or newer. The app downloads include their own Python.

## [0.7.4-beta] — 2026-10-22

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.4-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.4-beta).
A security update for 0.7.3; everyone on 0.7.x should install it. Updating from 0.7.3, 0.7.2, 0.7.1 or 0.7.0:
download 0.7.4 and install it over your current version. Your chats and settings live in your profile, outside the app.

### Changed
- **Security hardening for workspace file tools.** VOOL's file tools (writing, editing and patching files, and undoing those changes) no longer change a project's git settings or the hook files git runs. To change those, edit them yourself.
- **Patches.** A patch is checked in full before anything is written. If any part of it is refused, nothing in it is applied. Old-style "context" diffs are no longer accepted; VOOL uses the standard unified format.

Everything else is the same as 0.7.3.

## [0.7.3-beta] — 2026-10-21

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.3-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.3-beta).
Updating from 0.7.2, 0.7.1 or 0.7.0: download 0.7.3 and install it over your current version. Your chats and settings
live in your profile, outside the app.

### Added
- **Goals.** Type `/goal` followed by what you want done. VOOL plans the steps and works through them, with a card above the message box to pause, resume or stop. A goal never spends money by itself, and it waits while the chat is private.
- **Run commands from chat.** More everyday tools run in VOOL's sandbox, such as make, go, uv, linters and formatters, and command lines chained together. The approval card shows the exact command before it runs. An install gets internet access only after your OK. A failed command shows its exit code and output.
- **Remove a calendar account** from Settings. VOOL forgets the calendars you chose, the events it copied and its pending alerts. Nothing changes in the calendar itself.
- **Apple Calendar and Contacts on Mac,** once macOS has your permission. For Calendar, VOOL asks only when you add an account or refresh the calendar list in Settings.
- **Paying a contact (crypto, off by default).** A payment to a saved contact goes only to the exact address you reviewed. If the contact changes, VOOL asks you to review it again.

### Changed
- **Forget removes the text from the memory file.** Forget now erases the forgotten words from VOOL's local memory file and its search index, and from long text you pasted into that chat. "Forget A and forget B" forgets both, the count shows only memory that was really removed, and curly apostrophes work too.
- **"Go online on its own" covers more.** With the switch off, VOOL also skips update checks and online calendar sync, and says so calmly. Calendars on this computer keep working.
- **Plain words in Activity.** The Activity panel and the card under each reply say what happened in plain words, in your app language. Failures show as failures.
- **No model yet? VOOL says so,** with a "Set up a model" button, even before your first message. It also finds a local model at another address on this computer.
- **Better Auto model choice.** Auto picks only a model that can do what the task needs. A model you pick yourself handles the whole answer, tool steps included. A brief hiccup from a free model is retried once.
- **One chat, two windows.** A new message waits its turn while the chat is answering in another window.
- **Exported chats with real names,** named after the chat and the date, with a short header that says what is left out.
- **Notes that make sense.** "Write a note to my neighbour" writes the note; "Take a note to buy milk" still saves it.
- **Clearer Wallet panel.** Exact amounts, real times, and a "Not sent" receipt when a payment is declined or cancelled. On mainnet, a connected or watch-only account can receive and show its balance but cannot send from VOOL.
- **Security updates.** pillow 13.0.0 and certifi 2026.10.19.

### Fixed
- Spending limits count each paid cloud answer once; a charge VOOL cannot confirm is held at that call's approved limit.
- One question keeps one answer after a reload.
- After you press Allow, the action you approved is the one that runs.
- Cooking questions like "what oven temperature for roasting potatoes" are no longer treated as weather lookups.
- Very long answers keep streaming smoothly to the end. Keyboard hints show Ctrl on Linux.

### Known limitations
- Skill Codex: only the skill's text goes along when you share, so a skill with extra files cannot be shared yet.
- After a forget, VOOL keeps the words you asked it to forget in a short list on your computer, so it can keep refusing them.
- On Intel Macs, local models run on the processor and are slow. A long memory answer can time out, so use a cloud model there for now.
- Paying websites through Solana pay-kit is not included in the downloads.
- No live mainnet payment has been made yet with x402 or pay-per-answer AI services.
- Before a local model can write answers, press **Certify** for it once (Settings > Models & Providers > Local models).
- Scanned-PDF reading works on Macs only.
- The macOS apps are not notarized.

## [0.7.2-beta] — 2026-10-20

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.2-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.2-beta).
Updating from 0.7.1 or 0.7.0: download 0.7.2 and install it over your current version. Your chats and settings
live in your profile, outside the app.

### Added
- **Try again and edit.** Every reply has Try again, and you can edit and resend your own message. Attachments come along.
- **Themes.** Light, Dark or Match system, plus bigger conversation text.
- **Bring your chats with you.** Import chats from another AI app on this computer or from an export file. Chat titles and messages come over.
- **"Go online on its own" switch, off by default.** No downloads or cloud checks on VOOL's own until you allow it.
- **X from any chat.** Search posts and accounts, Spaces, trends and DMs, and look up past calls on a token. Anything that acts on your account waits for your OK.
- **Email accounts.** Add one from Settings. The login and the server's certificate are checked before saving.
- **Cloud web search.** Cloud models can use their own built-in web search when you switch it on. Fees still go through your spending limits.
- **Crypto (off by default).**
  - Send USDT from chat on Solana, Ethereum, BNB Smart Chain and Arbitrum One, with your approval, limits and a receipt.
  - All your balances on every chain in one answer.
  - Save a friend's wallet on their contact, with the right chain.
  - Watch alerts for a wallet or a token price. Read-only; VOOL never trades.
  - Trader tools: who is buying, selling or holding a token; collect its trades; check a thesis claim by claim.
  - Pay-per-answer AI services, paid from your VOOL wallet only after you approve.

### Changed
- **Clean streaming.** No raw symbols flash on screen while an answer arrives.
- **Calmer chat.** Jump to latest, clear tab titles, one icon style, and a friendly "are you sure?" before deleting.
- **Esc stops the answer,** every time.
- **Your words, as typed.** Messages in your own language are kept as you wrote them.
- **Desk pet.** Says what VOOL is doing, stays quiet otherwise, and keeps problems on screen.
- **Private chat in every app language.** Works in all 17 languages and leaves nothing behind.
- **Deeper forget.** Forget also clears the matching run history, and "forget 12" no longer touches "room 312".
- **Calendar.** Understands "next Friday" or "for two hours", warns about clashes, and no longer reads "3 apples" as a date.
- **Safer installs.** Safety checks run again right before any skill or plugin installs; a failure shows on the skill's card.
- **Secrets.** Kept out of more logs and files, with clearer messages when an API key is blocked.
- **Slower computers.** Local models on computers without a graphics chip get more time, so answers finish.

### Fixed
- VOOL restarts properly after "Stop VOOL".

### Known limitations
- Skill Codex is not open yet. Browsing, installing and sharing skills start once it opens.
- A forgotten item is no longer recalled or listed, but its text can stay in VOOL's local memory file. Removing it from that file is planned for 0.7.3, not released yet.
- On Intel Macs, local models run on the processor and are slow. A long memory answer can time out, so use a cloud model there for now.
- Paying websites through Solana pay-kit is not included in the downloads.
- No live mainnet payment has been made yet with x402 or pay-per-answer AI services.
- Before a local model can write answers, press **Certify** for it once (Settings > Models & Providers > Local models).
- Scanned-PDF reading works on Macs only.
- The macOS apps are not notarized.

## [0.7.1-beta] — 2026-10-14

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.1-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.1-beta).
Updating from 0.7.0: download 0.7.1 and install it over 0.7.0. Your chats and settings live
in your profile, outside the app.

### Added
- **Private chat.** Say "private chat", "don't record this" or "off the record", and nothing from that chat is saved: no transcript, no memory, no summary, no log. A small ghost marks its messages. Say "turn recording back on" to end it. A private chat stays private after a restart.
- **Answers as files.** Ask for an answer as a Word document, spreadsheet, slides, PDF, Markdown, CSV, JSON, YAML, XML, HTML, ZIP or tar.gz. VOOL checks that each file opens before offering it as a download link in the chat.
- **Scanned PDFs.** Multi-page scanned PDFs are recognised as scanned and read with the Mac's built-in text recognition.
- **Crypto (off by default).**
  - Arbitrum One joins Solana, Base, Ethereum, BNB Smart Chain and Robinhood Chain, with Arbitrum Sepolia for testing. Arbitrum supports sending ETH.
  - Settings > Crypto > Payment channels lists every way the wallet can pay a website. Each has its own on/off switch; a channel that's off refuses before anything is signed.
  - Websites that ask for x402 payment in USDC on Base or Base Sepolia can be paid from your Crypto Pilot wallet: one approval with your PIN, within your limits.

### Changed
- **Secrets stay out.** PINs, card numbers and passwords you type are kept out of memory and out of later prompts sent to a model.
- **Forget, the way you say it.** "Forget what I told you about X" removes it from VOOL's memory, so it isn't recalled or listed again. After you ask VOOL to forget something in a chat, it won't take that same fact back in that chat; start a new chat to tell it again.
- **Follow-ups keep the task going.** A message sent while VOOL is working joins the running task instead of restarting it.
- **No surprise tool fees.** VOOL refuses to use a cloud model that runs paid web or code tools at the provider without reporting the cost.
- **Custom endpoints and keys.** Keys for custom model endpoints go where the endpoint expects them, and their models are listed. Market-data API keys are kept through a refusal or a key change.
- **Intel Macs.** Local-model certification allows for slower processors.

### Known limitations
- On Intel Macs, local models run on the processor and are slow. A long memory answer can time out, so use a cloud model there for now.
- Paying websites through Solana pay-kit is not included in the downloads. That channel shows as not usable on this install.
- No live mainnet payment has been made yet with the new x402 options.
- Private chat commands work in English only.
- 0.7.1 is installed by download.
- Before a local model can write answers, press **Certify** for it once (Settings > Models & Providers > Local models).
- Scanned-PDF reading works on Macs only.
- Forget clears VOOL's memory, but the original chat messages stay in VOOL's local activity records. Clearing those too comes in 0.7.2.
- Skill Codex browsing is not open yet.
- The macOS apps are not notarized.

## [0.7.0-beta] — 2026-10-13

Downloads: Apple Silicon Mac, Intel Mac and Linux x86_64 on the
[v0.7.0-beta release](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta).

### Added
- **Intel Mac and Linux builds.**
  - Separate self-contained macOS x86_64 and arm64 apps. The launcher checks your CPU and macOS version and explains a mismatch instead of failing.
  - A self-contained Linux x86_64 package. It needs no sudo or system Python, and adds menu and desktop shortcuts.
- **Bundled local-model runner.** The macOS app ships the runner its local models need, per CPU slice.
- **Signed update support with rollback** for each supported platform, not switched on in 0.7.0; 0.7.0 is installed by download:
  - the v0.7 release publisher key is pinned;
  - update checks need explicit consent;
  - your current data is kept on rollback.
- **User rules.** Global, chat and project rules; "what are my rules"; remove by number. Forget also reaches rules you gave in plain words.
- **Chain data keys.**
  - Add your own Helius, Alchemy or Solscan key, and get read-only chain data in chat. An Etherscan key is also accepted.
  - The keys share the provider key flow.
  - With no key, no network call is made.
- **Guided X setup** with read-only checks: a Bearer Token or your own app keys. X stays drafting-only.
- **Bug reports.** Report and 👎 on every answer.
  - The report is built on your computer and shown to you before sending.
  - Cards, PINs, phone numbers, wallet addresses and your user name are scrubbed.
  - Open on GitHub files a prefilled public report, with no token needed.
- **Skill Codex** on the Add-ons page:
  - signed skill packages, with Download for inspection, Install without enabling, and Install and enable;
  - ten main categories with subcategories.
  - Browsing shows "not open yet" in this version.
- **Add-on store.** Reviewed add-ons with local add-on security checks.
- **Notifications.**
  - Notifications and model offers share one inbox.
  - A task-finished chime, with a persistent sound setting.
- **Zcash private invoices** with a watch-only viewing key (opt-in, off by default).
- **Localization.** The interface, first run and settings are translated across 16 shipped catalogs besides English.

### Changed
- **Memory.**
  - Recall works on a fresh install within the local time budget.
  - Forget and absence answers are tightened.
  - Scoped erasure covers stored memory carriers.
- **Local-model time limits** scale once on machines with no GPU.
- **Chat header.** One place for model and run-mode controls. Activity controls stay reachable.
- **Crypto.** Mainnet is the default once crypto is turned on. Test networks move to Developer options.
- **Provider keys.** Setup is organized by models, search and security.
- **Pets.** The original artwork is back, pets are larger, and they show chat activity bubbles. Hit areas stay clear of controls.
- **Installers.** They no longer set up an outside agent runtime; VOOL's own runtime does the work.
- **The old public VoolBook site pages are retired.**
- **A pinned paid model** runs its own tool-selection rounds.

### Fixed
- **Personal assistant.**
  - Reminders and calendar fall back to your profile's time zone instead of UTC.
  - Past reminder times are refused.
  - Common "when" phrases are read correctly.
  - Apple Calendar all-day events are no longer a day off.
  - Gmail nested and HTML-only bodies are read.
  - A missing calendar is reported as such.
  - The calendar and note tools receive the request text.
- **IMAP.** Connections verify the server certificate and have a bounded timeout.
- **Live prices.** A follow-up like "and ETH?" keeps every asset you already asked about.
- **Claims.** A time phrase inside a claim ("as of today") is treated as framing, not as what the claim is about.
- **Image generation.** Stopping it only stops VOOL's own image process.
- **Recall routing.** "What did I tell you about X" is answered from the conversation and memory, not the web.
- **Chat lists.** Numbered lists no longer restart at 1, and Copy labels show in your language.
- **Update menu.** It is no longer clipped.
- **Native app.** A slow health answer from a running local runtime no longer closes the app.
- **Linux.** Shutdown cleanup is fixed.
- **Wallet payments.**
  - Each x402 offer is paid at most once, whatever the URL spelling.
  - The spend is held from proposal to settlement.
  - Every refusal leaves a receipt.
  - An unknown outcome is never re-paid.
- **Zcash.** Payments in an unknown pool are reported as uncertain, not unpaid. Receipts are recorded exactly once.

### Security
- Recovery phrases typed as plain words are redacted. More key shapes are masked, including EVM and Zcash viewing keys.
- Text parsers were rewritten to run in linear time, so long input cannot stall them.
- Sandboxed jobs cannot read external volumes, and their child processes are cleaned up.
- Plugin approval is bound to the package code and its dependencies, and rechecked when it runs.
- Wallet connections are pinned to approved DNS answers.
- Patched dependency releases are pinned.

### Known limitations
- x402 for websites works on test networks only in 0.7.0 (Solana Devnet, Base Sepolia, Ethereum Sepolia and BNB testnet). One exception: the pay-per-call option in the model marketplace in Settings can pay from the Solana Mainnet Crypto Pilot wallet. Each call waits for your approval within your wallet limits and shows the 0.1% VOOL fee. It has not yet been tested on a real chain.
- Skill Codex browsing is not open yet.
- X is drafting plus guided read-only setup; trending is hidden.
- The macOS apps are not notarized.
- No in-app update is offered for 0.7.0; it is installed by download.
- Before a local model can answer, open Settings > Models & Providers > **Local models** and press **Certify** for it once.
- On Intel Macs, local models run on the CPU and may be too slow to pass the one-time Certify check or to answer in time, so use a cloud model there for now. We tested the Intel build under Rosetta on an Apple Silicon Mac, not yet on an Intel Mac.
- Saying "don't record this" does not stop a chat from being saved yet. Ask VOOL to forget it afterwards.
- Dragging the pet out of the VOOL window does not move it to the desktop. Use right-click > **Move to desktop**, or the button in Settings.

## [0.6.0-beta] — 2026-09-19

First public-facing VOOL beta tree (macOS, Apple Silicon).

### Changed
- Initial public tree: runtime, memory, tool loop, permissions, receipts, providers and the
  bundled macOS app (`VOOL.app`, entry point `Contents/MacOS/VOOL`, ad-hoc code signature,
  builder-home paths scrubbed from the bundle by a binary-safe build gate). Legacy
  compatibility identifiers are frozen deliberately — see
  docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md for the engineering record.

### Fixed
- Provider invocation manifests: canonical marker hashing is now idempotent. Previously a
  context-trimming marker re-verified after persistence was re-redacted (its sha256 digest
  matched key-shape rules) and re-hashed, failing the manifest's own canonicality check and
  blocking the model call ("provider manifest contains non-canonical persistence data").

### Known limitations
- Partial translations; language and voice controls disabled in this build.
- macOS app is ad-hoc signed, not notarized.
- Windows experimental (installer sources in-tree, no shipped artifact); Linux unsupported.
- Cloud burst `ask` mode's per-call approval prompt is not wired yet.
- Wallet/x402 spend lane disabled; mainnet impossible by construction.
