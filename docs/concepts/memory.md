---
description: How VOOL remembers things between sessions, and where that memory lives.
---

# Memory

Memory is how VOOL carries facts across sessions. It is stored as files on your
machine, in VOOL's data home, in a readable format.

## Properties

* **On disk, in the open.** Memory is files you can read. `MEMORY.md` is the readable
  face; the facts behind it are kept in an entries file beside it.
* **Local.** Memory is not synchronised to a server.
* **One store per VOOL home.** Memory is not partitioned per workspace — it is kept
  for the whole installation, so it is not a per-project isolation boundary.

## Managing it

Notes you add to `MEMORY.md` yourself are preserved. Facts VOOL recorded are erased
through VOOL's forget operation, not by deleting the file: forgetting rewrites the
entries store and the mirror in one step and re-reads both to confirm the text is
gone. Deleting `MEMORY.md` alone is not erasure — the entries file still holds the
facts, and the mirror is rebuilt from it.

In cloud mode, relevant memory may be part of the context sent to the provider, on
the same terms as any other context. See [Local and cloud](local-and-cloud.md).

Forgetting in a chat also erases the words from that chat's run history in the runtime
database: the turn records, checkpoints, attempts, task plans, finalized answers (with the
copies the answer erasure sweeps), candidate knowledge, context reports, notifications,
research artifacts the chat produced, and pinned messages that quoted them. Records that
other records depend on stay with the forgotten words blanked, together with any digest
stored beside them; the rest are deleted. A turn that was still running when the forget
came cannot write the words back: its events are blanked and, when it finishes, its records
are erased again. The database's write-ahead log is then folded so older copies do not stay
in the `-wal` file. Another chat's records are not touched. A queued message that has not
been sent yet is kept, and the forget then says it could not be fully applied. A table no
erasure step knows is named in the reply, which says a retry will not clear it.

Words are compared case-insensitively, and a digit written in another script is that
digit (a fullwidth "１２" is 12). A forgotten number matches only where it stands
as its own number: the letters and digits written together with it may hold no other
digit, and it may not continue across `.`, `,`, `:`, `/`, `-` or `_` into another digit. Forgetting "12" erases "code 12", "12kg" and "B12",
but not "room 312", "3.12", "12.5", "12:30" or "2026-10-12", and forgetting "200" does not
erase "1,200". In the run history, a bare number the runtime counted or timed (such as an
`elapsed_ms` or `step_count` value) is not matched.

Memory readers also check a durable forget authority. A fresh memory home creates
it before accepting writes. An older semantic store upgrades in place only when
no prior erasure is recorded; its retained memories remain available. An existing
unreadable authority, a missing authority with a persisted witness, or a recorded
forget barrier causes readers to withhold affected memory. Deleting authority
files is not a way to restore forgotten content.

A pending erasure journal retains its token for recovery. Once the permanent
scoped revocation exists, completion clears the journal's redundant token while
keeping the operation identity and status. The revocation authority remains.
