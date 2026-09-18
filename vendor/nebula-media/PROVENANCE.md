# Vendored: nebula-media worker

Source repo : ~/Desktop/OX-NEBULA MEDIA/nebula-media
Source branch: feature/media-studio-m1 @ 331b7105006b11ab894e487b96d8a52e5518a559
Vendored on : 2026-08-29
Files       : nebula/__init__.py, nebula/media_ops.py, nebula/worker.py

## Why vendored rather than referenced

`nebula/worker.py` and `nebula/media_ops.py` exist ONLY on `feature/media-studio-m1`;
the nebula repo's `master` does not have them (verified: `git cat-file -e master:nebula/worker.py`
fails). Pointing `NEBULA_MEDIA_HOME` at that checkout would therefore depend on a branch that is
not checked out, and checking it out would modify a repository this recovery treats as read-only.

The worker is stdlib-only by design (commit 331b710 made the re-exports lazy so it runs without
the encoder stack); `media_ops` shells out to `ffmpeg`, which is the one external requirement.

The worker's own docstring names VOOL as the intended host:
"The host (e.g. VOOL) owns this process's lifecycle; heavy ffmpeg work therefore never runs
inside a UI event loop."

## Upstream

This is a copy, not a fork. Changes belong upstream in the nebula-media repository; if that branch
merges to master, prefer pointing `NEBULA_MEDIA_HOME` at a real checkout and delete this vendor
directory.
