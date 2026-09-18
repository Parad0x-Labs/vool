# Provider guide — three lanes, all opt-in

VOOL answers on a **local model by default** (Ollama, qwen-class). Paid or remote capacity is
never used until you turn a lane on.

## 1. Local (default)

Ollama on `http://127.0.0.1:11434` (override: `VOOL_OLLAMA_URL`). The first run pulls a
model chosen for your hardware. Local inference is free and stays on your machine.

## 2. BYOK — your own cloud key

Settings → Providers → add an OpenRouter/OpenAI-compatible key. The key is stored in your
local credential store (Keychain service `nulla-credentials` — a frozen legacy name; see the
[upgrade guide](UPGRADE_NULLA_TO_VOOL.md)) and used **only** for the lanes you enable. Payment
goes straight from you to the provider; VOOL never intermediates.

The **cloud burst** (`cloud off | ask | auto`, default **off**) spends your key for tasks too
big for the local model. `auto` bursts up to a daily call cap you set; the cap and mode can be
changed only from your own local session. Per-call approval prompts are not wired in this
build (`ask` stays local) — an honest limitation, see [STATUS](STATUS.md).

## 3. UsePod prepaid

A prepaid metered lane (`https://api.usepod.ai/proxy/<token>/...`) authenticated by a path
token you provision. Turn it on in provider settings. Spending draws down your prepaid balance;
VOOL treats the path token as a credential (it is redacted in every log and receipt by
construction).

## 4. Wallet / x402 (disabled by default)

Optional Solana wallet for Web0 `.null` names and metered x402 spends — **disabled in this
build**; the spend lane is frozen with `/stopx402` semantics. Details, custody and spending
controls: [Wallet & Web0 guide](WALLET_WEB0_GUIDE.md).

## Model selection and honesty

The runtime picks the local tier from your hardware, honors any manifest you seed, and reports
per-feature capability truth at `GET /api/runtime/capabilities` (implemented / simulated /
disabled) — payments surface as simulated until a real rail is wired.
