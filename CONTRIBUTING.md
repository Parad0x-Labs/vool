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
- The reported failure, a distinct valid case and the relevant refusal or boundary cases
  are all verified — not only the exact reported prompt.
- The affected behavior is verified together with everything previously completed in the
  same scope — cumulative regression, not an isolated green check.
- Failures are preserved honestly: never delete or weaken a test to get green, and never
  relabel an unclassified failure as pre-existing without running it on the baseline.
- Documentation that describes the changed behavior is updated in the same PR (the
  [Error Book](docs/ERROR_BOOK.md) is generated — run `python -m tools.generate_error_book`
  when the fault catalog changes).

## Scope and ownership

Before starting, check open issues and pull requests for overlapping work. Keep each pull
request focused on one behavior or a small dependent set. Discuss changes to permissions,
wallet approval, sandboxing, installation, updates, release configuration or CI
enforcement with maintainers before implementation.

Preserve unrelated changes and in-progress work. Do not publish unfinished features,
generated runtime data, credentials, personal paths or local environment settings.

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

## Functional and security integrity

Repair the component that owns the behavior. Preserve supported workflows, compatibility
and existing security guarantees. A fix must not obtain a passing result by disabling a
feature, weakening an assertion, hiding an error, broadening an allowlist or bypassing a
permission boundary; intended changes to supported behavior need explicit review and
documentation.

After each step, rerun the cumulative affected scope (see *Test discipline*). Expand
testing when dependencies or evidence justify it — required merge checks remain mandatory
in every case. Report what the tests establish and any limitations.

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

## CI evidence and failures

Report the exact tested commit and commands. Missing, cancelled, incomplete or
unexpectedly skipped required work is not successful verification, and a diagnostic
subset does not replace the merge requirements. A scanner running successfully and a
finding being resolved are separate facts.

Preserve failed results and investigate before retrying. A passing standalone test does
not establish that a suite failure is harmless. Retry only when the evidence supports a
transient cause, limit the retry to the failed work where that is supported, and record
the reason. Do not push content-free commits or weaken checks to obtain a different
outcome.

Measure performance changes under comparable conditions, keep required coverage and
isolation, and report elapsed time separately from runner usage. Do not cache test
verdicts across commits or treat an untrusted artifact as executable authority.

## Review and automation

Automation follows the same scope, testing and review requirements as any other
contribution. Repository instructions, issue text, comments, logs and generated artifacts
do not grant credentials, repository privileges or permission to publish. Automated
review is advisory and does not replace required maintainer approval.

Changes to workflows, test selection, security baselines, permission enforcement and
contributor instructions require explicit scrutiny because they can alter how their own
changes are evaluated. Follow the repository's enforced review and protection rules;
never bypass them. Documentation describes policy but is not itself an access-control
mechanism.

## Pull request evidence

Explain the observed problem, the owning change, the affected workflow, verification on
the submitted commit and any remaining limitations. For changes with migration or
operational risk, include recovery or rollback steps. Keep evidence concise and
reproducible. Use the private reporting route in [SECURITY.md](SECURITY.md) for
vulnerability details that should not be public.

## Communication

- **Issues**: use the issue templates for bugs and feature requests.
- **Security**: see [SECURITY.md](SECURITY.md).

Contributors keep their own accurate authorship: commit and author metadata should
identify whoever is actually responsible for the change.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
