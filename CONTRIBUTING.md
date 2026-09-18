# Contributing to VOOL

Thanks for helping build a local-first agent runtime. This guide is the shortest honest
path to a merged PR.

## Read this first

- [README.md](README.md) for what VOOL is and the platform honesty rules.
- [docs/REPO_MAP.md](docs/REPO_MAP.md) for where things live.
- [docs/RUNTIME_ARCHITECTURE_CONTRACT.md](docs/RUNTIME_ARCHITECTURE_CONTRACT.md) for the
  request flow and the authorities that own each boundary.
- [SECURITY.md](SECURITY.md) — vulnerabilities never go through public issues.

## Definition of done

A change is done when:

- The owning contract is fixed, not the symptom (trace the failure to the module that
  owns the behavior; read it before editing).
- The affected behavior is verified together with everything previously completed in the
  same scope — cumulative regression, not an isolated green check.
- Failures are preserved honestly: never delete or weaken a test to get green, and never
  relabel an unclassified failure as pre-existing without running it on the baseline.
- Documentation that describes the changed behavior is updated in the same PR (the
  [Error Book](docs/ERROR_BOOK.md) is generated — run `python -m tools.generate_error_book`
  when the fault catalog changes).

## What you can work on

- Bug fixes and documentation improvements
- Installer and launcher improvements
- Test coverage (especially semantic-family coverage for repairs)
- Platform-clarity improvements (honest capability reporting)

## Out of scope for external PRs

- Changes to the security or permission boundary without prior discussion
- Anything that renames persisted identifiers (see
  [docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md](docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md) —
  frozen names exist so installed users' data survives)
- Live deployment infrastructure (per-instance)

## Development setup

```bash
git clone https://github.com/Parad0x-Labs/vool && cd vool
bash installer/bootstrap_vool.sh --install-profile local-only   # or use your own venv
```

Run tests:

```bash
python3 -m pytest tests/ -q          # full suite (slow)
python3 -m pytest tests/test_vool_api_server.py -q   # one area
```

## Code style

- Python 3.10+; run `python3.12 ops/verify.py --workers 4 --tail-lines=200` before
  submitting (the same lint/collection/stable-shard gate CI uses).
- No dead code or commented-out blocks. Tests live in `tests/` and use pytest.
- Match the surrounding code's naming and comment density.

## Test discipline

Cumulative regression, not isolated green checks: if your work has multiple steps, re-run
the already-working scope every time you add a new step (step1 pass; step1+2 pass; …).
A repair that only passes the exact reported prompt is not accepted — every defect fix
needs a semantic regression family (paraphrases, sloppy variants, negative controls) and
a load-bearing sabotage check where one exists.

## Communication

- **Issues**: use the issue templates for bugs and feature requests.
- **Security**: see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
