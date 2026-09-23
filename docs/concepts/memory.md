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
