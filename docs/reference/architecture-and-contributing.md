---
description: How VOOL is built — the architecture in one page, developer setup, and how to contribute.
---

# Architecture and contributing

## Architecture in one page

VOOL is a local-first desktop runtime with a served core:

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Principles the code enforces:

* **Local by default; cloud by your key.** Cloud lanes exist only with your credentials and
  your ceilings.
* **One authority per decision.** Permissions, spending and network egress each have one
  owning contract; faults classify at the boundary that owns them.
* **Fail honestly.** Unknown outcomes are typed as unknown; refusals say what did not
  happen; a pinned model is never silently substituted.
* **Everything recorded.** Tool calls, approvals, payments and updates write receipts under
  your profile.

## Developer setup

```bash
git clone REPOSITORY_URL vool
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Platform notes, the full toolchain layout and the release gates live in the repository's
engineering docs (`docs/` in the repo root; the deep internals are not part of this public
docs site).

## Docs: one canonical source

These docs are generated from **one** source: this repository's `docs/` folder (GitBook
layout, `.gitbook.yaml` at the repo root). vool.dev/docs is built from it. To work on docs:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

The error reference (`ERROR_BOOK.md`) and the localization handoff manifest are
**generated from the runtime's own registries** — regenerate them, never hand-edit:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

A focused test fails the build if the generated files drift from their registries.

## Contributing

* Small, reviewed changes; every claim in docs must match the code as shipped.
* Preserve stable codes and identifiers — they outlive builds.
* Never widen an authority silently: a new permission, spend path or egress lane is a design
  change, not a patch.
* Beta limitations are documented, not hidden — if you find an undocumented one, that is a
  bug in the docs.
