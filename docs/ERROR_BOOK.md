# VOOL Error Book

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

Every error VOOL can show you, generated from the app's own error catalog: the codes,
messages and next steps below are exactly what the app shows.

- **Schema:** `vool.fault.v1`
- **Catalog version:** `1`
- **Catalog digest:** `83290d22af3092c2aee6c9a3a5fe466107140dd7d52ab2aab1e8d817dc548b64`
- **Fault count:** 58

Each code is stable and never reused for a different meaning. Every code has its own
section below whose heading is the code itself, so searching this page for the code as
you were given it finds its section, and a link to the section is this page's address
followed by `#` and the code with underscores as hyphens. Each section says what
happened, what may already have happened, what to do next, and whether trying again
can help. If a code you were given is not in this book, it is not from this version of
VOOL: check which version showed it, or report it with the code as written.

## Security

| Code | Title | Severity | What may already have happened |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Change outside the allowed scope | high | Nothing was sent, signed or changed |
| [`credential_failure`](#credential-failure) | Stored credential could not be read | high | Nothing was sent, signed or changed |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Backup already shown once | high | Nothing was sent, signed or changed |
| [`wallet_caller_refused`](#wallet-caller-refused) | Not started from VOOL's own window | high | Nothing was sent, signed or changed |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Card data refused | high | Nothing was sent, signed or changed |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Chain endpoint identity unproven | high | A request reached an external service; no payment moved |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Wrong network environment | medium | Nothing was sent, signed or changed |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | Export unlocks only by device authentication | high | Nothing was sent, signed or changed |
| [`wallet_export_refused`](#wallet-export-refused) | Old export path retired | high | Nothing was sent, signed or changed |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Export unavailable for this wallet | high | Nothing was sent, signed or changed |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Legacy money path retired | high | Nothing was sent, signed or changed |
| [`wallet_network_disabled`](#wallet-network-disabled) | Network not declared for this wallet | high | Nothing was sent, signed or changed |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Request outside approved endpoints | high | Nothing was sent, signed or changed |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | Approval no longer matches the preview | high | Nothing was sent, signed or changed |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | Recovery proof does not fit this wallet | high | Nothing was sent, signed or changed |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | Signature did not match the wallet's key | high | A request reached an external service; no payment moved |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Wallet locked after failed unlocks | high | Nothing was sent, signed or changed |

## Integrity

| Code | Title | Severity | What may already have happened |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Answer records failed a consistency check | high | A local record/change happened |
| [`integrity_verification_failure`](#integrity-verification-failure) | Record failed its tamper check | critical | A local record/change happened |
| [`unsupported_claim`](#unsupported-claim) | Unsupported statements withheld | medium | A local record/change happened |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Payment already proposed or sent | medium | A local record/change happened |

## Policy

| Code | Title | Severity | What may already have happened |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Legacy pocket is Solana only | low | Nothing was sent, signed or changed |
| [`permission_denied`](#permission-denied) | Permission denied | medium | Nothing was sent, signed or changed |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Acknowledgement required | low | Nothing was sent, signed or changed |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Amount not exact for this coin | low | Nothing was sent, signed or changed |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Approval not accepted | medium | Nothing was sent, signed or changed |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Typed confirmation required | low | Nothing was sent, signed or changed |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Entries did not match | low | Nothing was sent, signed or changed |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | EVM libraries not installed | medium | Nothing was sent, signed or changed |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Device unlock declined | low | Nothing was sent, signed or changed |
| [`wallet_disabled`](#wallet-disabled) | Wallet is switched off | low | Nothing was sent, signed or changed |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | Balance does not cover amount and fee | low | A request reached an external service; no payment moved |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Spending limit exceeded | medium | Nothing was sent, signed or changed |
| [`wallet_not_found`](#wallet-not-found) | Wallet, proposal or destination not recognised | low | Nothing was sent, signed or changed |
| [`wallet_password_invalid`](#wallet-password-invalid) | Wallet password invalid | low | Nothing was sent, signed or changed |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Solana pay extra not installed | medium | Nothing was sent, signed or changed |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Payment channel turned off | low | Nothing was sent, signed or changed |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | PIN invalid | low | Nothing was sent, signed or changed |
| [`wallet_quote_expired`](#wallet-quote-expired) | Preview expired or was replaced | low | Nothing was sent, signed or changed |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | Recipient cannot safely receive this | medium | Nothing was sent, signed or changed |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | More than one account or network fits | low | Nothing was sent, signed or changed |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Sending needs a VOOL Wallet | low | Nothing was sent, signed or changed |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | Setup step does not apply now | low | Nothing was sent, signed or changed |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | Wallet cannot sign from here | medium | Nothing was sent, signed or changed |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Key storage is memory-only | medium | Nothing was sent, signed or changed |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Paid resource above the automatic cap | medium | Nothing was sent, signed or changed |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | x402 extra not installed | medium | Nothing was sent, signed or changed |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Payment scheme unsupported | low | Nothing was sent, signed or changed |

## Availability

| Code | Title | Severity | What may already have happened |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Provider limit reached | medium | A request reached an external service; no payment moved |
| [`provider_unavailable`](#provider-unavailable) | Provider unreachable | medium | Nothing was sent, signed or changed |
| [`timeout`](#timeout) | Timed out | medium | Outcome unknown — check state before retrying |
| [`tool_unavailable`](#tool-unavailable) | Tool unavailable | low | Nothing was sent, signed or changed |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Payment could not be broadcast | medium | Outcome unknown — check state before retrying |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Device unlock unavailable | medium | Nothing was sent, signed or changed |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Balance or fee unknown | medium | A request reached an external service; no payment moved |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | Payment failed simulation | medium | A request reached an external service; no payment moved |

## Cancellation

| Code | Title | Severity | What may already have happened |
|---|---|---|---|
| [`cancelled`](#cancelled) | Cancelled | info | Outcome unknown — check state before retrying |

## Internal

| Code | Title | Severity | What may already have happened |
|---|---|---|---|
| [`unknown`](#unknown) | Unexpected error | medium | Outcome unknown — check state before retrying |

## Reference by code

Every code, in alphabetical order. The section heading is the code's stable anchor.

<a name="cancelled"></a>

### cancelled

**Cancelled**

- **What happened:** This was cancelled, so it did not finish.
- **What may already have happened:** Outcome unknown — check state before retrying
- **What to do next:** No action needed; start again if you want it done.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** info · **Category:** cancellation

<a name="confinement-refusal"></a>

### confinement_refusal

**Change outside the allowed scope**

- **What happened:** A file change outside the allowed scope was blocked. Nothing outside the scope was touched.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** If this change is wanted, request it explicitly so it is inside the authorized scope.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="credential-failure"></a>

### credential_failure

**Stored credential could not be read**

- **What happened:** A stored credential could not be read. Re-enter it in settings to continue.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** The vault entry failed to decrypt; re-store the credential or check the vault key.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="evidence-corruption"></a>

### evidence_corruption

**Answer records failed a consistency check**

- **What happened:** This answer's supporting records failed a consistency check, so treat the answer as unverified.
- **What may already have happened:** A local record/change happened
- **What to do next:** Investigate this turn's execution ledger against the runtime event stream.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** integrity · **Security-relevant:** yes

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Legacy pocket is Solana only**

- **What happened:** A legacy pocket, the phrase-based wallet, holds only a Solana key, so it cannot be created or restored on an EVM network. Nothing was created. For an EVM network, create a VOOL Wallet on that network in Settings, Crypto, add the account watch-only, or connect your own EVM wallet.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** The legacy pocket seals one Solana seed from its recovery phrase. On an EVM network, use a Crypto Pilot wallet (Create wallet on that network in Settings, Crypto, Networks and wallets), a watch-only account, or an external EVM wallet (EIP-1193) that keeps its own key.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** low · **Category:** policy

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Record failed its tamper check**

- **What happened:** A record failed its tamper-evidence check. Nothing was silently accepted.
- **What may already have happened:** A local record/change happened
- **What to do next:** Treat the affected receipt chain as compromised; investigate before trusting its entries.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** critical · **Category:** integrity · **Security-relevant:** yes

<a name="permission-denied"></a>

### permission_denied

**Permission denied**

- **What happened:** That action was not permitted, so nothing was changed.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Check the permission policy or approval settings that govern this action.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** medium · **Category:** policy · **Security-relevant:** yes

<a name="provider-exhausted"></a>

### provider_exhausted

**Provider limit reached**

- **What happened:** The model provider's usage limit is reached. You can try again later.
- **What may already have happened:** A request reached an external service; no payment moved
- **What to do next:** Check quota, rate limits and billing for the configured provider.
- **Trying again:** Try again later: what stopped this clears with time.
- **Severity:** medium · **Category:** availability

<a name="provider-unavailable"></a>

### provider_unavailable

**Provider unreachable**

- **What happened:** The model provider could not be reached. You can try again in a moment.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Check the provider's status, network reachability, and configured endpoints.
- **Trying again:** Try again later: what stopped this clears with time.
- **Severity:** medium · **Category:** availability

<a name="timeout"></a>

### timeout

**Timed out**

- **What happened:** This took too long and was stopped. Trying again may succeed.
- **What may already have happened:** Outcome unknown — check state before retrying
- **What to do next:** If timeouts repeat, check network latency and provider responsiveness.
- **Trying again:** Trying again now may work.
- **Severity:** medium · **Category:** availability

<a name="tool-unavailable"></a>

### tool_unavailable

**Tool unavailable**

- **What happened:** That tool is not available right now, so this part was not done. Enabling it is needed before you retry.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Enable the tool for this mode in settings, or choose a different approach.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** availability

<a name="unknown"></a>

### unknown

**Unexpected error**

- **What happened:** Something went wrong on this machine while handling that. You can try again.
- **What may already have happened:** Outcome unknown — check state before retrying
- **What to do next:** See the fault's redacted cause chain in diagnostics; an unknown cause is never retried blind.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** medium · **Category:** internal

<a name="unsupported-claim"></a>

### unsupported_claim

**Unsupported statements withheld**

- **What happened:** One or more statements were not supported by the retrieved sources and were withheld.
- **What may already have happened:** A local record/change happened
- **What to do next:** No action needed -- this is the grounding gate working. Check retrieval quality if frequent.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** medium · **Category:** integrity

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Acknowledgement required**

- **What happened:** This step needs your acknowledgement first, so nothing was signed or sent. Read what the wallet says this action does, acknowledge it exactly, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** When an approval would grant lasting spending rights, change ownership or run an upgrade, or the wallet finds a mismatch or cannot read the request, it asks you to type its exact acknowledgement first; nothing generic passes in its place.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Amount not exact for this coin**

- **What happened:** That amount is not an exact amount this coin can hold, so nothing was proposed. Check the amount, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Send a plain decimal amount no more precise than the coin allows and within the pilot's per-transfer ceiling.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Approval not accepted**

- **What happened:** The approval for this payment was not accepted, so nothing was signed or sent.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Approve the exact proposal with the owner's PIN or biometric; approvals do not transfer between proposals.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** medium · **Category:** policy · **Security-relevant:** yes

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Backup already shown once**

- **What happened:** This wallet's backup key was already shown once, so it cannot be shown again. If you did not save it, cancel setup and do not send funds to this address.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** A Crypto Pilot backup is released exactly once during setup; there is no second reveal and no export for pilot wallets.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Payment could not be broadcast**

- **What happened:** The payment could not be broadcast. Check its status before retrying.
- **What may already have happened:** Outcome unknown — check state before retrying
- **What to do next:** Check the network's RPC endpoint and the proposal's receipt; the spend was not recorded as sent.
- **Trying again:** Try again later: what stopped this clears with time.
- **Severity:** medium · **Category:** availability

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**Not started from VOOL's own window**

- **What happened:** This wallet action can only be started from VOOL's own window on this computer, so nothing was changed, signed or sent. Open VOOL and try again there.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Drive trusted wallet doors from the served page or the native window; a page on another origin, a framed page, a text/plain form and the command-dispatch projection are refused by design.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Card data refused**

- **What happened:** Card numbers and security codes are never stored here; only a provider token is accepted. Nothing was saved.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Tokenize the card with the provider and register the token reference instead.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Chain endpoint identity unproven**

- **What happened:** The chain endpoint did not prove the identity of the network it claims to be, so nothing was signed or sent.
- **What may already have happened:** A request reached an external service; no payment moved
- **What to do next:** Check the RPC endpoint for the network; a lying chain id or genesis is refused before any payment.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Typed confirmation required**

- **What happened:** That step needs the typed confirmation first, so nothing was changed. Confirm it, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Read the warning and type the exact confirmation phrase to proceed.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Entries did not match**

- **What happened:** The two entries did not match, so nothing was created. Enter the same PIN or password twice, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** A Crypto Pilot credential is confirmed before any key material exists.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**EVM libraries not installed**

- **What happened:** This build cannot do EVM signing or ABI encoding: the Ethereum libraries are not installed on this machine. Nothing was signed or sent. Solana, custody and spend ceilings are unaffected; once the libraries are installed, try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Install the EVM extras (eth-abi, eth-utils, eth-account) to enable the EVM lanes; every other wallet lane works without them.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** policy

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Device unlock declined**

- **What happened:** Touch ID or your Mac password was declined or did not open this wallet, so nothing was created, changed, unlocked, revealed, signed or sent. Try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Try again and confirm the Touch ID or Mac password prompt from VOOL. A wallet that approves with Touch ID has no PIN or password of its own to use instead. If the prompt keeps failing, restore the wallet from its recovery phrase (a Crypto Pilot from the backup shown at setup) and give it a PIN or a password; a private-key export still needs Touch ID or the Mac password.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Device unlock unavailable**

- **What happened:** Touch ID or your Mac password is not available for this wallet here, so nothing was created, changed, unlocked, revealed, signed or sent. Try again once it is available.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** A new wallet can approve with its own PIN or password instead. A wallet that approves with Touch ID has no PIN or password of its own: where Touch ID and the Mac password stay unavailable, restore it from its recovery phrase (a Crypto Pilot from the backup shown at setup) and give it a PIN or a password. A private-key export opens only with Touch ID or the Mac password, so restore the wallet on a Mac where they work and export there.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** availability

<a name="wallet-disabled"></a>

### wallet_disabled

**Wallet is switched off**

- **What happened:** The wallet is switched off, so no payment was proposed or sent. Turn on Enable Crypto in Settings, Crypto, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Turn on Enable Crypto in Settings, Crypto only if you want payments on this install. When the VOOL_WALLET_ENABLED variable is set, it turns the wallet on or off whatever the switch says.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Payment already proposed or sent**

- **What happened:** This payment was already proposed or sent, so it was not sent again.
- **What may already have happened:** A local record/change happened
- **What to do next:** Check the existing proposal's receipt; use a new idempotency key for a genuinely new payment.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** medium · **Category:** integrity · **Security-relevant:** yes

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Wrong network environment**

- **What happened:** That network belongs to the other network environment (Mainnet or Test networks), so nothing was created, proposed or signed. Switch in Settings, Crypto, Developer options, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Choose the network environment deliberately in Crypto settings; accounts, balances and receipts never move between environments.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** security · **Security-relevant:** yes

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**Export unlocks only by device authentication**

- **What happened:** Export never unlocks with a PIN, password or passphrase, only with device authentication (Touch ID or your Mac password), so nothing was revealed. Try again without the PIN or password.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Export is unlocked by device authentication only, on purpose; a PIN, password or passphrase in the request is refused before it is ever checked.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Old export path retired**

- **What happened:** That way of exporting a private key is retired, so nothing was revealed. A wallet that allows export has an Export private key button in Settings, Crypto, which opens only with Touch ID or your Mac password.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Use Export private key in Settings, Crypto, Other accounts: only a legacy pocket exports, and only with Touch ID or the Mac password. A Crypto Pilot wallet never exports; it recovers from the backup shown at setup.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Export unavailable for this wallet**

- **What happened:** This wallet cannot be exported that way: it is watch-only or holds no key on this machine, its one backup was shown at setup and it never exports, VOOL does not export to the wallet app you chose, or it is a legacy pocket from an earlier version that must be restored from its recovery phrase before it exports to MetaMask. Nothing was revealed.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Only a legacy pocket sealed on this machine exports, to Phantom or MetaMask, and only with Touch ID or the Mac password. A Crypto Pilot wallet recovers from the backup shown at setup. A legacy pocket from an earlier version exports to MetaMask after it is restored from its recovery phrase.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**Balance does not cover amount and fee**

- **What happened:** The balance does not cover the amount plus the most this network fee can be, so nothing was prepared. Lower the amount or add funds, then try again.
- **What may already have happened:** A request reached an external service; no payment moved
- **What to do next:** Spendable excludes other in-flight holds; a Solana remainder must be zero-free and at least the rent minimum in the pilot.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Legacy money path retired**

- **What happened:** That money path is retired: every payment goes through the wallet's proposal and approval flow. Nothing was signed or sent.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Use the wallet tools (wallet.propose) or the wallet API; the legacy signer, spender and exporter no longer exist.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Spending limit exceeded**

- **What happened:** That payment is above a spending limit, so it was not sent.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Review the per-transaction, daily and per-destination limits before retrying.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** medium · **Category:** policy · **Security-relevant:** yes

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Network not declared for this wallet**

- **What happened:** That network is not one this wallet declares for this action, so nothing was created or sent.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Use one of the declared network rows; undeclared chains, aliases and look-alike names are refused by construction.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-not-found"></a>

### wallet_not_found

**Wallet, proposal or destination not recognised**

- **What happened:** That wallet, proposal or destination was not recognised, so nothing was done. Check it and try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Check the wallet id, proposal id or destination address and try again.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Request outside approved endpoints**

- **What happened:** That request was not allowed to leave the wallet's approved endpoints, so no connection was made.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Check the declared RPC origins and payment-resource policy; private, metadata and unapproved hosts are refused.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Wallet password invalid**

- **What happened:** The password did not meet the policy or did not unlock the wallet, so nothing was signed or changed. Check the password and try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Wallet passwords are 10 to 128 characters and not only digits. A wrong password leaves the key sealed and counts toward the wallet's lock after too many wrong tries.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Solana pay extra not installed**

- **What happened:** This build cannot pay Solana x402 or MPP offers: the optional pay extra (Solana pay-kit, Python 3.11 or newer) is not installed on this machine. Nothing was signed or sent. Every other wallet lane works without it; once it is installed, try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Install VOOL's optional pay extra on Python 3.11 or newer to enable pay-kit payments; the wallet keeps its own signer, spend caps and approval either way.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** policy

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Payment channel turned off**

- **What happened:** That payment goes through a payment channel that is turned off, so nothing was signed or sent. Turn the channel on in Settings, Crypto, Payment channels, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Turn a payment channel on in Crypto settings only if you want payments made through it; the cap and your approval apply on every channel.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**PIN invalid**

- **What happened:** The PIN did not meet the policy or did not unlock the wallet, so nothing was signed. Check the PIN and try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Use a 6 to 12 digit PIN; a wrong PIN leaves the sealed key untouched.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**Preview expired or was replaced**

- **What happened:** This transfer preview expired or was replaced, so it cannot be approved and nothing was sent. Refresh the preview, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** A quote lives for a short window and is superseded by a refresh or an environment switch; approval never carries over.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**Approval no longer matches the preview**

- **What happened:** The approval no longer matches the transfer that was previewed, so nothing was signed or sent. Start again from a fresh preview.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** An approval binds one quote digest; a changed account, chain, recipient, amount, environment or fee ceiling invalidates it.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Balance or fee unknown**

- **What happened:** The network did not give a complete answer for this transfer's balance or fee, so no approval was prepared and nothing was sent. Try again in a moment.
- **What may already have happened:** A request reached an external service; no payment moved
- **What to do next:** Unknown chain data is never treated as zero; check the row's endpoint and its identity proof.
- **Trying again:** Try again later: what stopped this clears with time.
- **Severity:** medium · **Category:** availability

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**Recipient cannot safely receive this**

- **What happened:** That recipient cannot safely receive this transfer on this network, so nothing was prepared. Check the address, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Refused: a new Solana account below the rent minimum, an executable or off-curve account, a bad EIP-55 checksum, the zero address, precompiles and system contracts.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** policy

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**Recovery proof does not fit this wallet**

- **What happened:** That recovery proof does not fit this wallet, so nothing was changed: the saved key must be the backup VOOL showed for this exact address, in the format it was shown. Check it and try again, or use the other options.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Recovery re-seals an existing wallet only from a backup that decodes to the wallet's own key (both derivations agree), or from an enrolled device secret released by fresh user presence; a wrong or foreign key is refused before any write and counted on the recovery throttle only.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**More than one account or network fits**

- **What happened:** More than one account or network fits this transfer, so nothing was proposed. Say which account or network you mean, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** A transfer names one account on one network; the candidates are listed in the fault context.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Sending needs a VOOL Wallet**

- **What happened:** This account cannot send from VOOL on this network: only a VOOL Wallet created on this device sends here. A connected or watch-only account can still receive and show its balance. Nothing was signed or sent. To send, choose a VOOL Wallet on this device and try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Send from a VOOL Wallet on this device, or send from the connected wallet's own app; nothing in VOOL signs for a connected or watch-only account on this network.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**Setup step does not apply now**

- **What happened:** That setup step does not apply to this wallet right now, so nothing changed. Check the wallet's setup status, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Setup moves generating, awaiting backup, backup revealed, ready; cancel and resume never reveal a backup twice.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**Signature did not match the wallet's key**

- **What happened:** The signature returned for this payment did not match the wallet's key, so it was not broadcast.
- **What may already have happened:** A request reached an external service; no payment moved
- **What to do next:** Check the connected signer; a mismatched signature is refused before broadcast.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**Wallet cannot sign from here**

- **What happened:** This wallet cannot sign from here: it is watch-only or its external signer is not connected. Nothing was sent; connect a signer, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Connect the external wallet, or create a VOOL Wallet on that network in Settings, Crypto.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** policy

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**Payment failed simulation**

- **What happened:** The payment did not pass simulation, so it was not sent. Check the balance and destination, then try again.
- **What may already have happened:** A request reached an external service; no payment moved
- **What to do next:** Check the balance, the destination and the network's RPC endpoint, then propose again.
- **Trying again:** Try again later: what stopped this clears with time.
- **Severity:** medium · **Category:** availability

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Key storage is memory-only**

- **What happened:** This installation keeps its device key only in memory, so a wallet created here could not be opened after a restart. Nothing was created. Switch key storage to a lasting mode, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Crypto Pilot wallets are refused while VOOL_KEY_STORAGE_MODE is ephemeral; use file or keyring storage.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** policy

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Wallet locked after failed unlocks**

- **What happened:** There were too many wrong PIN or password attempts, so this wallet is locked for a while and nothing was unlocked. Wait, then try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Failed unlocks are counted durably per wallet and for the whole app, across restarts; the lock lifts on its own after the stated wait.
- **Trying again:** Try again later: what stopped this clears with time.
- **Severity:** high · **Category:** security · **Security-relevant:** yes

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Paid resource above the automatic cap**

- **What happened:** A paid resource asked for more than the automatic payment cap, so it was not paid.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Raise VOOL_WALLET_X402_CAP_MINOR deliberately, or pay the resource by an explicit proposal.
- **Trying again:** Do not send the same request again unchanged.
- **Severity:** medium · **Category:** policy · **Security-relevant:** yes

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**x402 extra not installed**

- **What happened:** This build cannot pay x402 offers on Base through the x402 Foundation SDK: the optional x402 extra is not installed on this machine. Nothing was signed or sent. Every other wallet lane works without it; once it is installed, try again.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Install VOOL's optional x402 extra to pay x402 offers on Base from a Crypto Pilot wallet; the wallet keeps its own signer, spend caps and approval either way.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** medium · **Category:** policy

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Payment scheme unsupported**

- **What happened:** This payment offer needs a scheme this wallet cannot prove end to end, so nothing was signed. Try an offer whose scheme, token and facilitator are verified for this network.
- **What may already have happened:** Nothing was sent, signed or changed
- **What to do next:** Use an offer whose scheme, token and facilitator are verified for the network; partial signing support is never improvised.
- **Trying again:** Try again after making the change this entry names.
- **Severity:** low · **Category:** policy
