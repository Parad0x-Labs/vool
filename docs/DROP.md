# VOOL Drop (off by default, unreviewed)

**UNREVIEWED: not for real messages.** The session protocol has had no independent
cryptographic review. Drop stays off until the owner enables it in Settings. Without a saved setting,
`VOOL_DROP=1` can enable a CLI laboratory; a saved setting takes precedence.

Drop sends a private handoff from one VOOL to another: a short note, one selected file of
up to 10 MiB, and one task that the receiving person must approve before anything runs.
The result comes back to the sender. Every Drop expires seven days after its invite was made.

## How it works

- **People.** A person is their key. Each VOOL has a card (a link that carries its
  fingerprint and the name its owner suggests). Two people send each other their cards any
  way they like and each adds the other's. A card is not a secret and adds nobody by itself:
  nothing can be sent until both sides have added each other.
- **Names.** A name is a label the owner of this computer gives to one key. One name maps to
  one key. A second key under the same name, or under a name that looks like it (mixed
  alphabets, look-alike letters, invisible characters), is refused, and the refusal is the
  warning. There is no directory and no server, so a name is unique on this computer, not in
  the world. Next to every name VOOL shows three words made from the key, so two people can
  never look the same in the list.
- **Safety number.** Twenty digits made from the key. The two people read them to each other
  once, on a call or in person, and mark the person as verified. Until then every send says
  the person is not verified.
- **Pair once, send many.** Each side keeps a few one-use signed invites for the other in the
  shared folder and renews them. A send takes an unused one and opens a fresh encrypted
  session, so the next file needs no new pairing. The sender sees "Delivered" when the
  receiver's VOOL has the whole file.
- **Removing a person** deletes their Drops and refuses anything they send afterwards.
- **One-time invite.** The older flow (compare fingerprints, make a one-use link, the other
  side joins) is still there under "One-time invite, without adding the person" and in the CLI.
- **Encryption.** `core/drop/session.py` implements the DROP-HYBRID-1 profile
  (`third_party/drop-session/PROTOCOL.md`). The session is opened with X25519 plus ML-KEM-768
  and both identities sign with Ed25519 and ML-DSA-65. Each message then gets a fresh key
  from a Double Ratchet with AES-256-GCM. All primitives come from the `cryptography`
  package, so there is no Node or other runtime dependency. Python and the reference
  JavaScript package produce the same bytes. `tests/fixtures/drop/js_transcript.json` pins
  that, and `ops/drop_interop.py` runs a live conversation in both directions.
- **Shared folder.** A folder both people can reach, such as a shared Dropbox or iCloud Drive
  folder. It only holds ciphertext addressed by fingerprint. It must exist, be a real folder
  and belong to the user; it does not have to be private, and VOOL never creates one from a
  typed path. No server, account, chain transaction or payment is involved.
- **Received content is data.** Nothing in a Drop is treated as an instruction. The only task
  is a SHA-256 of the selected file. It runs once, only after the receiver approves it, and
  only if the receiver's own permission policy still allows reading files at that moment.
  Requests for any wider permission are refused.
- **Expiry and delete.** A forget barrier is published before anything is removed. The
  Drop's note, file, task, encrypted session and queued frames are then erased, together
  with any memory, embedding, summary, log or receipt that names the Drop. If cleanup is
  interrupted, the barrier stays and every reader keeps withholding.
- **Bad frames and late pieces.** A forged, replayed or malformed frame is consumed without
  effect, so it never blocks the frames behind it. A synced folder can deliver pieces in any
  order: a piece that is far ahead is kept as ciphertext and retried, and a file shows as
  arriving, with a count, until every piece is there. A file that can no longer be completed
  says so and asks for it to be sent again.
- **Limits per person.** One person can hold a bounded number of open Drops and waiting
  pieces, so nobody can use up the room other people's Drops need.
- **On this computer.** Notes, file pieces, names and the selected file are sealed with
  AES-256-GCM. For the running profile the key is kept in the system keychain (macOS
  Keychain; VOOL's own sealed vault where there is none), not in the Drop folder. A received
  file reaches plain disk only when the owner presses Save to Downloads, and saving never
  replaces a file that is already there.
- **From chat.** Asking chat to send a file to a person uses one tool, in any language. It
  asks for approval every time, only runs for the owner at this computer, and answers in
  words whether anything was sent. Received Drops are never read by a model.

## Try it

In the app, open **Settings → Backup & restore → Send with Drop** on both computers.

1. Enable Drop and choose the shared folder (Choose folder, or one of the offered synced
   places). A folder that is missing, not yours or not writable is refused with the reason
   before anything is saved. This starts no online service.
2. Type your name, press Save name, then Copy card, and send the card to the other person.
3. Paste their card under "Their card", check the name, and press Add person. When both have
   done this the person shows as Ready.
4. Choose the person, pick one file, add a note if you like, and press Send file.
5. On the other computer the Drop appears by itself while VOOL is open. Press Save to
   Downloads to keep the file, and Show in folder to see it. The sender then sees Delivered.
6. Once, on a call or in person, compare the safety number and mark the person as verified.

Enabling Drop makes that VOOL profile directory owner-only, as the switch states. Its files
are kept. The controller refuses to change the user's HOME, a filesystem root or a
foreign-owned directory; it never changes the shared folder's permissions.

The sender can request the selected file's SHA-256. The receiver can decline, or approve
and run that exact file check. Approval and execution both recheck the receiver's
permission policy. Other tasks are refused. Received text is displayed as text, never passed
to a model or interpreted as instructions.

The owner-local, same-origin controller is `POST /api/drop`; file bytes use
`POST /api/drop/send`, capped at 10 MiB. Neither accepts a caller-selected runtime home
or an arbitrary local input path. The original CLI remains available:

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
- A hosted relay with admission control and quotas, and phone support.
- General agent tasks through the planner.
- Paid tasks. When added, they must go through VOOL's wallet, caps, approval and receipts.
- Two people still need a folder they both sync. There is no transport of VOOL's own yet.
- No QR code for cards and no system notification when a Drop arrives.
- Names are unique per computer only; nothing can make a name unique in the world without a server.
- For a named test home (CLI `--home`) the vault key is a 0600 file inside that home.
- Not covered: copies the receiver makes outside VOOL, backups, SSD sanitization, traffic
  analysis by whoever runs the relay folder, and Windows (the store refuses it).
- There is no continuous post-quantum healing after a session state is compromised.
