# VOOL Error Book

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

Every fault VOOL can surface, as a human-readable view of the one canonical fault catalog
(`core/faults/catalog.py`). This document is *derived from* that catalog, not a second copy
of it: the codes, messages and actions below are exactly what the runtime emits.

- **Schema:** `vool.fault.v1`
- **Catalog version:** `1`
- **Catalog digest:** `ebeddf8d5a336fd38fa9ebc663283b0a1b48b6c388c5825b58681516f1dc9207`
- **Fault count:** 48

A fault's `code` is stable and safe to match on; `user_message` is what a person sees;
`operator_action` is what to do about it; `authority` names the module that owns the fault.

## security

| Code | Severity | Retryable | Retry | Security | Meaning | Operator action | Authority |
|---|---|---|---|---|---|---|---|
| `confinement_refusal` | high | no | never | yes | A file change outside the allowed scope was blocked. Nothing outside the scope was touched. | If this change is wanted, request it explicitly so it is inside the authorized scope. | `core.agent_runtime.builder.app_builder` |
| `credential_failure` | high | no | never | yes | A stored credential could not be read. Re-enter it in settings to continue. | The vault entry failed to decrypt; re-store the credential or check the vault key. | `core.credential_store` |
| `wallet_backup_unavailable` | high | no | never | yes | This wallet's backup key was already shown once, so it cannot be shown again. If you did not save it, cancel setup and do not send funds to this address. | A Crypto Pilot backup is released exactly once during setup; there is no second reveal and no export for pilot wallets. | `core.wallet.pilot_custody` |
| `wallet_caller_refused` | high | yes | retry_after_change | yes | This wallet action can only be started from VOOL's own window on this computer, so nothing was changed, signed or sent. Open VOOL and try again there. | Drive trusted wallet doors from the served page or the native window; a page on another origin, a framed page, a text/plain form and the command-dispatch projection are refused by design. | `core.wallet.caller_binding` |
| `wallet_card_data_refused` | high | no | never | yes | Card numbers and security codes are never stored here; only a provider token is accepted. Nothing was saved. | Tokenize the card with the provider and register the token reference instead. | `core.wallet.cards` |
| `wallet_chain_identity_mismatch` | high | no | never | yes | The chain endpoint did not prove the identity of the network it claims to be, so nothing was signed or sent. | Check the RPC endpoint for the network; a lying chain id or genesis is refused before any payment. | `core.wallet.chains` |
| `wallet_environment_inactive` | medium | yes | retry_after_change | yes | That network belongs to the other network environment (Mainnet or Test networks), so nothing was created, proposed or signed. Switch in Settings, Crypto, Developer options, then try again. | Choose the network environment deliberately in Crypto settings; accounts, balances and receipts never move between environments. | `core.wallet.environment` |
| `wallet_export_refused` | high | no | never | yes | Private keys are never exported by this runtime. The recovery phrase is shown once at creation and nowhere else. Nothing was revealed. | Recover a VOOL Wallet from the phrase written down at creation; there is no export door to enable. | `core.wallet.authority` |
| `wallet_legacy_surface_retired` | high | no | never | yes | That money path is retired: every payment goes through the wallet's proposal and approval flow. Nothing was signed or sent. | Use the wallet tools (wallet.propose) or the wallet API; the legacy signer, spender and exporter no longer exist. | `core.wallet.authority` |
| `wallet_network_disabled` | high | no | never | yes | That network is not one this wallet declares for this action, so nothing was created or sent. | Use one of the declared network rows; undeclared chains, aliases and look-alike names are refused by construction. | `core.wallet.custody` |
| `wallet_outbound_refused` | high | no | never | yes | That request was not allowed to leave the wallet's approved endpoints, so no connection was made. | Check the declared RPC origins and payment-resource policy; private, metadata and unapproved hosts are refused. | `core.wallet.outbound` |
| `wallet_quote_mismatch` | high | no | never | yes | The approval no longer matches the transfer that was previewed, so nothing was signed or sent. Start again from a fresh preview. | An approval binds one quote digest; a changed account, chain, recipient, amount, environment or fee ceiling invalidates it. | `core.wallet.quotes` |
| `wallet_recovery_refused` | high | yes | retry_after_change | yes | That recovery proof does not fit this wallet, so nothing was changed: the saved key must be the backup VOOL showed for this exact address, in the format it was shown. Check it and try again, or use the other options. | Recovery re-seals an existing wallet only from a backup that decodes to the wallet's own key (both derivations agree), or from an enrolled device secret released by fresh user presence; a wrong or foreign key is refused before any write and counted on the recovery throttle only. | `core.wallet.pilot_custody` |
| `wallet_signature_invalid` | high | no | never | yes | The signature returned for this payment did not match the wallet's key, so it was not broadcast. | Check the connected signer; a mismatched signature is refused before broadcast. | `core.wallet.signers` |
| `wallet_unlock_throttled` | high | yes | retry_later | yes | There were too many wrong PIN or password attempts, so this wallet is locked for a while and nothing was unlocked. Wait, then try again. | Failed unlocks are counted durably per wallet and for the whole app, across restarts; the lock lifts on its own after the stated wait. | `core.wallet.pilot_custody` |

## integrity

| Code | Severity | Retryable | Retry | Security | Meaning | Operator action | Authority |
|---|---|---|---|---|---|---|---|
| `evidence_corruption` | high | no | never | yes | This answer's supporting records failed a consistency check, so treat the answer as unverified. | Investigate this turn's execution ledger against the runtime event stream. | `core.execution_truth` |
| `integrity_verification_failure` | critical | no | never | yes | A record failed its tamper-evidence check. Nothing was silently accepted. | Treat the affected receipt chain as compromised; investigate before trusting its entries. | `core.contribution_proof` |
| `unsupported_claim` | medium | no | never | no | One or more statements were not supported by the retrieved sources and were withheld. | No action needed -- this is the grounding gate working. Check retrieval quality if frequent. | `core.grounding_publication` |
| `wallet_duplicate_payment` | medium | no | never | yes | This payment was already proposed or sent, so it was not sent again. | Check the existing proposal's receipt; use a new idempotency key for a genuinely new payment. | `core.wallet.proposals` |

## policy

| Code | Severity | Retryable | Retry | Security | Meaning | Operator action | Authority |
|---|---|---|---|---|---|---|---|
| `evm_pocket_custody_unavailable` | low | no | never | no | EVM keys cannot be held on this device: use watch-only or an external wallet signer. Nothing was created. | Register the account watch-only or connect an external EVM signer (EIP-1193); in-process EVM custody does not exist in this build. | `core.wallet.custody` |
| `permission_denied` | medium | no | never | yes | That action was not permitted, so nothing was changed. | Check the permission policy or approval settings that govern this action. | `core.runtime_execution_tools` |
| `wallet_amount_invalid` | low | yes | retry_after_change | no | That amount is not an exact amount this coin can hold, so nothing was proposed. Check the amount, then try again. | Send a plain decimal amount no more precise than the coin allows and within the pilot's per-transfer ceiling. | `core.wallet.amounts` |
| `wallet_approval_rejected` | medium | no | never | yes | The approval for this payment was not accepted, so nothing was signed or sent. | Approve the exact proposal with the owner's PIN or biometric; approvals do not transfer between proposals. | `core.wallet.lifecycle` |
| `wallet_confirmation_required` | low | yes | retry_after_change | no | That step needs the typed confirmation first, so nothing was changed. Confirm it, then try again. | Read the warning and type the exact confirmation phrase to proceed. | `core.wallet.custody` |
| `wallet_credential_mismatch` | low | yes | retry_after_change | no | The two entries did not match, so nothing was created. Enter the same PIN or password twice, then try again. | A Crypto Pilot credential is confirmed before any key material exists. | `core.wallet.pilot_custody` |
| `wallet_dependency_unavailable` | medium | yes | retry_after_change | no | This build cannot do EVM signing or ABI encoding: the Ethereum libraries are not installed on this machine. Nothing was signed or sent. Solana, custody and spend ceilings are unaffected; once the libraries are installed, try again. | Install the EVM extras (eth-abi, eth-utils, eth-account) to enable the EVM lanes; every other wallet lane works without them. | `core.wallet.evm` |
| `wallet_disabled` | low | yes | retry_after_change | no | The wallet is switched off, so no payment was proposed or sent. Enable it first, then try again. | Enable the wallet explicitly (VOOL_WALLET_ENABLED=1) only if payments are wanted on this install. | `core.wallet.custody` |
| `wallet_insufficient_funds` | low | yes | retry_after_change | no | The balance does not cover the amount plus the most this network fee can be, so nothing was prepared. Lower the amount or add funds, then try again. | Spendable excludes other in-flight holds; a Solana remainder must be zero-free and at least the rent minimum in the pilot. | `core.wallet.quotes` |
| `wallet_limit_exceeded` | medium | no | never | yes | That payment is above a spending limit, so it was not sent. | Review the per-transaction, daily and per-destination limits before retrying. | `core.wallet.limits` |
| `wallet_not_found` | low | yes | retry_after_change | no | That wallet, proposal or destination was not recognised, so nothing was done. Check it and try again. | Check the wallet id, proposal id or destination address and try again. | `core.wallet.custody` |
| `wallet_pin_invalid` | low | yes | retry_after_change | no | The PIN did not meet the policy or did not unlock the wallet, so nothing was signed. Check the PIN and try again. | Use a 6 to 12 digit PIN; a wrong PIN leaves the sealed key untouched. | `core.wallet.custody` |
| `wallet_quote_expired` | low | yes | retry_after_change | no | This transfer preview expired or was replaced, so it cannot be approved and nothing was sent. Refresh the preview, then try again. | A quote lives for a short window and is superseded by a refresh or an environment switch; approval never carries over. | `core.wallet.quotes` |
| `wallet_recipient_refused` | medium | yes | retry_after_change | no | That recipient cannot safely receive this transfer on this network, so nothing was prepared. Check the address, then try again. | Refused: a new Solana account below the rent minimum, an executable or off-curve account, a bad EIP-55 checksum, the zero address, precompiles and system contracts. | `core.wallet.quotes` |
| `wallet_request_ambiguous` | low | yes | retry_after_change | no | More than one account or network fits this transfer, so nothing was proposed. Say which account or network you mean, then try again. | A transfer names one account on one network; the candidates are listed in the fault context. | `core.wallet.transfers` |
| `wallet_setup_state_invalid` | low | yes | retry_after_change | no | That setup step does not apply to this wallet right now, so nothing changed. Check the wallet's setup status, then try again. | Setup moves generating, awaiting backup, backup revealed, ready; cancel and resume never reveal a backup twice. | `core.wallet.pilot_custody` |
| `wallet_signing_unavailable` | medium | yes | retry_after_change | no | This wallet cannot sign from here: it is watch-only or its external signer is not connected. Nothing was sent; connect a signer, then try again. | Connect the external wallet, or create a VOOL Wallet after reading its warning. | `core.wallet.signers` |
| `wallet_storage_class_refused` | medium | yes | retry_after_change | no | This installation keeps its device key only in memory, so a wallet created here could not be opened after a restart. Nothing was created. Switch key storage to a lasting mode, then try again. | Crypto Pilot wallets are refused while VOOL_KEY_STORAGE_MODE is ephemeral; use file or keyring storage. | `core.wallet.pilot_custody` |
| `wallet_x402_cap_exceeded` | medium | no | never | yes | A paid resource asked for more than the automatic payment cap, so it was not paid. | Raise VOOL_WALLET_X402_CAP_MINOR deliberately, or pay the resource by an explicit proposal. | `core.wallet.x402` |
| `x402_scheme_unavailable` | low | yes | retry_after_change | no | This payment offer needs a scheme this wallet cannot prove end to end, so nothing was signed. Try an offer whose scheme, token and facilitator are verified for this network. | Use an offer whose scheme, token and facilitator are verified for the network; partial signing support is never improvised. | `core.wallet.x402` |

## availability

| Code | Severity | Retryable | Retry | Security | Meaning | Operator action | Authority |
|---|---|---|---|---|---|---|---|
| `provider_exhausted` | medium | yes | retry_later | no | The model provider's usage limit is reached. You can try again later. | Check quota, rate limits and billing for the configured provider. | `core.turn_model_call_ledger` |
| `provider_unavailable` | medium | yes | retry_later | no | The model provider could not be reached. You can try again in a moment. | Check the provider's status, network reachability, and configured endpoints. | `core.turn_model_call_ledger` |
| `timeout` | medium | yes | retry_now | no | This took too long and was stopped. Trying again may succeed. | If timeouts repeat, check network latency and provider responsiveness. | `core.remote_fetch_policy` |
| `tool_unavailable` | low | yes | retry_after_change | no | That tool is not available right now, so this part was not done. Enabling it is needed before you retry. | Enable the tool for this mode in settings, or choose a different approach. | `core.runtime_execution_tools` |
| `wallet_broadcast_failed` | medium | yes | retry_later | no | The payment could not be broadcast. Check its status before retrying. | Check the test-network RPC and the proposal's receipt; the spend was not recorded as sent. | `core.wallet.lifecycle` |
| `wallet_quote_unavailable` | medium | yes | retry_later | no | The network did not give a complete answer for this transfer's balance or fee, so no approval was prepared and nothing was sent. Try again in a moment. | Unknown chain data is never treated as zero; check the row's endpoint and its identity proof. | `core.wallet.quotes` |
| `wallet_simulation_failed` | medium | yes | retry_later | no | The payment did not pass simulation, so it was not sent. Check the balance and destination, then try again. | Check the balance, the destination and the test-network RPC, then propose again. | `core.wallet.lifecycle` |

## cancellation

| Code | Severity | Retryable | Retry | Security | Meaning | Operator action | Authority |
|---|---|---|---|---|---|---|---|
| `cancelled` | info | no | never | no | This was cancelled, so it did not finish. | No action needed; start again if you want it done. | `core.remote_fetch_policy` |

## internal

| Code | Severity | Retryable | Retry | Security | Meaning | Operator action | Authority |
|---|---|---|---|---|---|---|---|
| `unknown` | medium | no | never | no | Something went wrong on this machine while handling that. You can try again. | See the fault's redacted cause chain in diagnostics; an unknown cause is never retried blind. | `core.faults.mapping` |

