# x402 payments in VOOL 0.7.1

x402 lets a website ask for a small payment before it returns content (an HTTP 402
answer). In VOOL 0.7.1:

- **Websites: USDC on Base.** Websites that ask for x402 payment in USDC on Base or Base
  Sepolia can be paid from your Crypto Pilot wallet: one approval with your PIN, within
  your limits. The website's payment service covers the network fee. No live mainnet
  payment has been made with this yet. Solana Devnet, Ethereum Sepolia and BNB testnet
  remain available as test networks.
- **Payment channels.** **Settings → Crypto → Payment channels** lists every way the wallet
  can pay a website, each with its own on/off switch. A channel that's off refuses before
  anything is signed. Paying through Solana pay-kit is not included in the downloads, so
  that channel shows as not usable.
- **One mainnet exception.** The pay-per-call option in the model marketplace in Settings
  can pay from the Solana Mainnet Crypto Pilot wallet in USDC or SOL. Each call waits for
  your approval within your wallet limits and shows the 0.1% VOOL fee. It has not yet been
  tested on a real chain.
- **Crypto is off by default.** Test networks are in **Settings → Crypto → Developer
  options**.
- **One money path.** A 402 answer becomes a payment proposal in VOOL's wallet. It is
  checked against your spending caps, waits for your approval, and leaves a receipt.
  Each offer is paid at most once, and an unknown outcome is never paid again.
- **No other payer.** The older `vool x402-pay` command and the `null://` dial's pay step
  are retired: they show the 402 and refuse to sign. Only the wallet pays, with your approval.

The client that builds canonical x402 "exact" payments on Solana is
`core/x402/client.py`; the wallet flow is in `core/wallet/x402.py`.

See also: [Wallet](guides/wallet.md), [Spending limits](guides/spending-limits.md) and
[SPEND_CAPS](SPEND_CAPS.md).
