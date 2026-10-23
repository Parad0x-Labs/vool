# Use VOOL from your phone

Your computer keeps the chats, models, wallet and receipts. Your phone connects to that computer. Wallet setup, recovery, key entry, terminal access, the file browser, add-ons and model downloads stay on the computer.

This build offers chat, attachments, alerts, wallet views and payment proposals. Approve payments and tool actions on the computer. Phone approval remains unavailable until the protected native approval key and its platform evidence are proven.

## Connect at home or over your private network

1. On the computer, open **Settings → Phone and chat apps**.
2. Choose its home Wi-Fi or private-network address and turn on **Use VOOL from my phone**. Fresh installations and upgrades leave access off.
3. Choose **Pair a phone** and scan the displayed QR in the companion app. A pairing code expires and works once. Manual entry also requires the computer's check code shown on the pairing page.
4. Set this phone's permissions on the computer; choose Refresh permissions if its row is not shown yet. Every permission starts off. Enable Chat on the computer to begin. History, attachments, alerts, wallet viewing, payments, camera, photos, voice and location have separate switches and expiry dates.

The other paired phones receive an in-app notice when a phone is paired or unpaired. A changed computer fingerprint is a new trust decision: check the computer and pair again. Never accept a replacement identity sent in a chat message.

The separate phone listener offers pairing and companion actions only. Do not expose the desktop API or use a development bind option. Turning phone access off closes the listener and stops bridges from accepting or sending more work. An already accepted chat may finish on the computer. Payment execution checks the access switch again.

For a private network you already use, choose the computer's address on that network. No VPN is installed or configured by the companion.

## Chat and phone abilities

A saved message keeps its request identity after a lost connection. It retries under that identity; it does not create another turn. A refused message stays blocked until you explicitly retry or discard its saved copy. Retrying preserves its identity. Discarding a local copy cannot undo work already accepted on the computer.

Choose a file or photo, or take a photo, to add an attachment. Phone uploads are limited to three megabytes per file and four attachments in the composer. The computer's file checks also apply. The computer must allow attachments and the selected camera or photo ability. Each capture asks on the phone. Temporary capture files are removed when the operation ends.

For voice, choose **Voice note**, accept that use, then hold the talk control. Release to send the recording sealed to the computer's local dictation service. Recordings are bounded to two minutes and three megabytes. The transcript enters the message box for review; it never sends itself. If the computer's recognizer is unavailable, type instead. No background microphone or wake word is used.

**Share location** asks for foreground permission and adds coordinates to the message box for review. It does not register background tracking. Incoming share-sheet support from other apps is not included in this build.

## Alerts and privacy

The phone's Alerts screen controls event types, quiet hours, presets and lock-screen detail choices. General alerts require the computer's permission. Lock-screen details start off and additionally require history or wallet-view permission.

The in-app feed keeps the record. Payment completion and refusal remain in its feed and badge while payment rights are on, even when banners are muted. Pairing and revocation notices also remain visible in-app. OS notifications are opt-in and best effort: the phone can mute or delay them. Foreground polling does not provide closed-app push; APNs/FCM delivery is not built.

The feed pages back through retained records. An old tool-approval prompt can show that it closed without knowing whether it was approved or denied after a restart; the current approval authority does not persist that terminal decision. Check the computer rather than infer an outcome.

The app locks when backgrounded and after inactivity, with a five-minute default. Status offers one, five or fifteen minutes. Unlock uses strong biometrics or the phone passcode. A phone without a screen lock must set one before unlocking. Balances start hidden. Capture protection and app-switcher privacy are implemented but require native device validation; iOS recording protection does not promise to block still screenshots.

## Wallet views and payment proposals

Enable wallet viewing on the computer to see balances, pending payments, contacts and receipts. Balances show the network's native coin, not tokens. To enable proposals, select a ready wallet account, enter its PIN or password on the computer, and set lower per-payment and daily phone limits. Payment permission expires within thirty days and can be renewed on the computer; it cannot be permanent. A wallet using Mac-device approval cannot enable this PIN-based phone grant in this build; it remains usable on the computer.

Before proposing, review the exact resolved address, amount, asset and network. New payees and unusual amounts are flagged. The PC stores the acknowledged warnings, checks that review before execution and repeats the warnings on its approval sheet. The warning is a bounded heuristic, not a scam detector. Confirming creates a proposal only; fees and approval stay on the computer. The server binds this review to the request, device and current permissions for five minutes. A changed payee or amount needs another review. Never share a wallet's secret phrase or private key.

Phone limits include fee ceilings. Confirmed payments count over a rolling twenty-four hours; pending and unknown requests keep consuming their allowance without aging out. Only a positively unsent terminal outcome releases that allowance. Native-asset proposals are offered; token proposals need separate fee-asset limits and remain unavailable.

An interrupted proposal keeps its identity on the phone. Check pending payments before retrying it. Retrying cannot recreate a request declined on the computer. Discarding the phone's saved request does not cancel the computer's proposal. Completed payments cannot be undone.

Free-form phone and bot chats cannot invoke wallet or paid-AI tools through the existing remote-tool fence. Use the app's Wallet screen or the explicit bot proposal commands. Chat-origin paid-AI/x402 proposal integration needs the fence owner's review; no owner privilege is borrowed.

## Telegram and Discord

These services can read messages sent through them. Both are off until connected on the computer. Tokens go through the existing credential store and are never shown back.

1. Create your own bot with the provider. For Discord, open its private conversation in Discord's web app and copy the conversation URL ending in `/channels/@me/<id>`.
2. In Phone and chat apps, choose **Connect chat app**, select the provider and enter its bot token. For Discord, also enter the private conversation URL. The computer verifies the bot and private conversation before replacing an existing connection.
3. Send the displayed `/pair CODE` to that bot in a private conversation within five minutes. Only that conversation and sender become bound. A stranger does not become the owner by messaging first.
4. Set the bot's rights and notification choices in its desktop row. A bot can never approve payments or tool actions. Disconnect removes its credential and stops its bridge.

`/history` reads that bot's conversation when allowed. Attachments require the attachment right. `/balance`, `/contacts` and `/receipts` require wallet viewing. `/pay AMOUNT CONTACT_OR_ADDRESS` displays an exact review. The displayed `/payconfirm TOKEN` creates the scoped proposal; review fees and approve in VOOL on the computer. No bot reply approves a payment. A privately paired owner can send `/freeze` (or `/stopx402`) even when optional chat rights are off. This only freezes payments; strangers cannot invoke it, and no bot command can unfreeze or approve. Bot alerts are coarse and best effort; the PC retains the record.

The older terminal-started Telegram bridge still has its legacy first-sender binding contract. It is not used by this Settings flow and should not be used as a substitute. Removing that contract requires a separate change to its existing tests.

## Away through your own relay

A self-hostable relay forwards sealed envelopes, while the computer dials out. The relay sees timing, room and device metadata; it cannot read bodies. Hosting and TLS deployment are owner work. Pair at home first, configure the relay in the computer's Settings, then save its phone connection address in the app's Status screen. The same pinned identity and queued-message owner survive a route change. A relay outage leaves messages waiting for retry.

The wire protocol uses static identity-derived keys and has no forward secrecy. Protect both endpoints and their saved credentials. Transport credentials stored together are not independent approval factors. Wallet keys stay on the computer.

## Freeze and recovery

- **Freeze payments** stops new approvals and execution through the wallet. A freeze/unfreeze cycle invalidates earlier phone reviews and accepted request claims. Decline an invalidated unsent proposal before creating a new one. Both the canonical wallet freeze and legacy spending mirror are set. The computer alone can unfreeze, after checking its wallet PIN or password in Settings; legacy CLI and /startx402 refuse to clear this protected freeze without that credential.
- Any paired phone, even with every optional right off, can freeze payments or invoke **My computer is lost** to revoke every device. These actions only remove authority. The same safety exception allows /stopx402 from a phone chat or bot; it cannot unfreeze or approve a payment.
- **Lost phone:** unpair it from the computer. Revocation survives restart, and other paired phones receive a notice.
- **Lost computer:** choose **My computer is lost** on the phone. If reachable, the computer freezes payments and revokes every phone and bot; the phone then removes its own credentials. If the wallet store cannot confirm the freeze, the computer still attempts every device revocation, stops the bot transports and records a recovery failure. An unreadable wallet state remains unknown. If unreachable, remote freezing and revocation remain unconfirmed. Secure or recover the computer, then freeze there. Revoke the connected bot tokens with Telegram and Discord and rotate the relay connection token; these credentials are separate from phone pairing.
- **Recovered computer:** perform the wallet's existing recovery on the computer. Use a fresh companion profile and desktop identity, then pair every phone again. Restoring an entire old profile can restore its old companion identity and grants; automatic identity rotation during an in-place wallet recovery is not integrated in this build. Do not treat a restored profile as a fresh pairing boundary.
- An outcome that is unknown must be reconciled on the computer before another payment is attempted. Phone retries reuse the proposal identity. A completed payment cannot be undone.

No installed-device build has been proven on this development machine. Camera, microphone, location, screen-lock fallback, capture protection, key invalidation and local OS delivery need the documented iOS/Android device checks before release.
