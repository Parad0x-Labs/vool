# Docs pipeline (repository copy)

This repository carries the **canonical documentation source** for vool.dev/docs: a GitBook
layout at `docs/` (`.gitbook.yaml` at the repo root is the renderer contract) plus the
generated references that must never be hand-edited. The builder and the publish gate are
vendored here from the live website toolchain (`~/Desktop/hhfdsfdfsfdsdsfsdf/website/tools/`,
verified live 2026-09-19) so repository CI can build and gate the docs with zero package
installs; the website copy stays authoritative for vool.dev deploys — keep the two in sync
deliberately when either changes.

## Build, check, preview (local, stdlib only)

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs --site-url https://vool.dev
python3 tools/docs/check_docs_build.py docs-build /docs     # must print "build is publishable"
mkdir -p /tmp/preview && ln -sfn "$PWD/docs-build" /tmp/preview/docs
python3 -m http.server -d /tmp/preview 8000                 # open http://127.0.0.1:8000/docs/
```

The build output must be served under the `/docs/` base (as on the live site), or the search
index and asset URLs will not resolve.

## Generated references — regenerate, never hand-edit

```bash
PYTHONPATH=. python3 -m tools.generate_error_book              # docs/ERROR_BOOK.md from core/faults
PYTHONPATH=. python3 -m tools.generate_localization_handoff    # config/localization-handoff.json
```

`tests/test_docs_site_builds.py` fails if either file drifts from its registry, if SUMMARY.md
lists missing pages, if public-section pages are missing from SUMMARY.md (an unlisted page
never publishes), or if the build/gate does not pass.

## Publishing — two documented options, both deliberate

1. **Approved-branch automation (preferred once public).** Push `.gitbook.yaml` and `docs/` to
   `Parad0x-Labs/vool` `main` (needs the owner's explicit approval). The server-side
   `vool-docs-sync` timer clones the repo, builds, runs the same gate, and atomically swaps the
   served release only if the gate passes. From then on, docs edits land by commit.
2. **Manual release (while the repo is not yet public).** Follow website `HANDOVER.md` §5.9:
   build and gate locally, rsync `docs/` (this repository's GitBook tree) to the server seed,
   and run the server-side build/gate/swap runbook by hand.

No step in this repository pushes, deploys, or credentials anything; every publish action is a
human decision.
