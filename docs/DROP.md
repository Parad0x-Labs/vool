# VOOL Drop (off by default, unreviewed)

**UNREVIEWED: not for real messages.** The session protocol has had no independent
cryptographic review. Drop stays off unless `VOOL_DROP=1` is set.

Drop sends a private handoff from one VOOL to another: a short note, one selected file of
up to 10 MiB, and one task that the receiving person must approve before anything runs.
The result comes back to the sender. Every Drop expires seven days after its invite was made.

## How it works

- **Identity.** Each VOOL has a fingerprint. Two people confirm each other's fingerprints
  directly (in person or on a call) before they connect. A link alone never authenticates
  anyone.
- **Connecting.** `invite <their fingerprint>` makes a one-use link. The other person runs
  `join <link> <your fingerprint>`. Both fingerprints are pinned for that Drop.
- **Encryption.** `core/drop/session.py` implements the DROP-HYBRID-1 profile
  (`third_party/drop-session/PROTOCOL.md`). The session is opened with X25519 plus ML-KEM-768
  and both identities sign with Ed25519 and ML-DSA-65. Each message then gets a fresh key
  from a Double Ratchet with AES-256-GCM. All primitives come from the `cryptography`
  package, so there is no Node or other runtime dependency. Python and the reference
  JavaScript package produce the same bytes. `tests/fixtures/drop/js_transcript.json` pins
  that, and `ops/drop_interop.py` runs a live conversation in both directions.
- **Relay.** The relay is a folder both people can reach, such as a shared or synced folder.
  It only holds ciphertext addressed by fingerprint. No server, account, chain transaction or
  payment is involved.
- **Received content is data.** Nothing in a Drop is treated as an instruction. The only task
  is a SHA-256 of the selected file. It runs once, only after the receiver approves it, and
  only if the receiver's own permission policy still allows reading files at that moment.
  Requests for any wider permission are refused.
- **Expiry and delete.** A forget barrier is published before anything is removed. The
  Drop's note, file, task, encrypted session and queued frames are then erased, together
  with any memory, embedding, summary, log or receipt that names the Drop. If cleanup is
  interrupted, the barrier stays and every reader keeps withholding.
- **Bad frames.** A forged, replayed, late or malformed frame is consumed without effect, so
  it never blocks the frames behind it. A frame that arrives before the frame it depends on
  is held and retried.

## Try it

```sh
export VOOL_DROP=1
python -m core.drop --home ~/drop-a --relay /shared/relay relay-init
python -m core.drop --home ~/drop-a --relay /shared/relay identity
python -m core.drop --home ~/drop-a --relay /shared/relay invite <B fingerprint>
python -m core.drop --home ~/drop-b --relay /shared/relay join <link> <A fingerprint>
python -m core.drop --home ~/drop-a --relay /shared/relay poll
python -m core.drop --home ~/drop-a --relay /shared/relay send <sid> --note "..." --file report.pdf
python -m core.drop --home ~/drop-b --relay /shared/relay poll
python -m core.drop --home ~/drop-b --relay /shared/relay show <sid>
```

`python -m ops.drop_demo --scratch <new dir>` runs the whole two-home flow end to end,
including the failure paths.

## Not done yet

- An independent review of the protocol composition and its storage.
- In-app screens and API routes. Today there is only the CLI.
- A hosted relay with admission control and quotas, and phone support.
- General agent tasks through the planner.
- Paid tasks. When added, they must go through VOOL's wallet, caps, approval and receipts.
- The session vault key is a 0600 file inside the home, not the OS keychain.
- Not covered: copies the receiver makes outside VOOL, backups, SSD sanitization, traffic
  analysis by whoever runs the relay folder, and Windows (the store refuses it).
- There is no continuous post-quantum healing after a session state is compromised.
