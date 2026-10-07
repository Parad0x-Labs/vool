# Reviewing VOOL

VOOL is a daily-user AI runtime, local-first. It connects a conversation to real actions on
your machine (reading a project, editing files, running commands, searching the web) and keeps
a record of what it actually did.

This guide is for anyone who wants to check that for themselves: how to set it up, how to run
it, and how to test its claims without taking our word for them. Every claim we make is listed
with the file, test or command behind it in the [evidence registry](docs/review/EVIDENCE.md).

## Before you start: what this build is

VOOL is **beta** software (version 0.6.0-beta on `main`). The authoritative list of what is
and is not shipped (installers, checksums, signing, updates) is the
[release status](docs/trust/release-status.md) page. In short:

* The supported install is **from source**. Desktop builds, where they exist, are not signed
  with a developer certificate yet, so your operating system will warn on first launch.
* There is no automatic update feed; the updater reports updates as unavailable.
* Answer quality depends on the model you connect. A small local model will make more mistakes
  than a large cloud one. Receipts prove what ran, not that an answer is right.
* Windows is an experimental path. Shell commands are blocked there because Windows has no
  kernel network-isolation backend VOOL can use; use WSL2 for those. See the
  [Windows capability matrix](docs/WINDOWS_CAPABILITY_MATRIX.md).
* Configuration formats and APIs can change between builds.

## 1. Set up a review checkout

You need Python 3.10 or newer and git. From a terminal:

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

This is the same install the CI uses, minus optional extras. It is enough for the demos and
tests below; it does not download a model.

**Command sandbox.** VOOL refuses to run shell commands and test runs unless it can confine
them at the OS level: `bwrap` (bubblewrap) on Linux, `sandbox-exec` on macOS (built in).
On Linux, install it with your package manager, for example `sudo apt install bubblewrap`.
Without it, file tools still work and command tools refuse with `sandbox_unavailable`. That
refusal is deliberate, and demo A below shows it.

To install VOOL as a daily user instead (with a local model), follow
[Install in the README](README.md#install) or the [install guide](docs/INSTALL.md).

## 2. Run the three reviewer demos

Each demo builds its own throwaway folders, points `VOOL_HOME` at a temporary directory, and
drives VOOL's real tool executor and effect gates. None of them needs a model, an account or
network access, and none touches an existing VOOL install. Each prints `[PASS]`/`[FAIL]` lines
and exits non-zero if any check fails. Run them from the repository root:

```bash
python scripts/review/demo_a_code_fix.py
python scripts/review/demo_b_denied_has_no_effect.py
python scripts/review/demo_c_receipt_isolation.py
```

| Demo | What it does | What to look for |
| --- | --- | --- |
| **A. Code fix** | A tiny project has a failing test. In Auto mode, VOOL's tools search the code, read the file, edit it, and run the tests in the sandbox. | Each call shows its permission decision; the test fails before and passes after, re-run outside VOOL. Without a sandbox backend the test step prints `[NOT RUN]` with the refusal. |
| **B. Denied means no effect** | Five operations that must not happen: writes and commands in Plan mode, an overwrite in Auto mode, a `../` escape from the workspace, and a request carrying a forged "bypass permissions" claim. | Each is blocked or held for approval, and a SHA-256 fingerprint of the workspace and of a folder beside it is identical before and after. |
| **C. Receipt isolation** | Real network and command gates run inside separate turns, one after the other and at the same time on two threads. | Each turn's receipts carry only its own turn id; a turn that ends early does not empty another; nothing is readable outside a turn. |

The model's part is scripted in demo A (the demo decides the edit), because the point is the
tool path, not model quality. To see a model make those choices, use the running app (step 4).

A line starting `Keychain unavailable` may appear on machines without a system keychain. It
comes from the isolated demo home and does not affect the result.

## 3. Run the tests behind the claims

The [evidence registry](docs/review/EVIDENCE.md) names a test file for each claim. To run all
of them at once:

```bash
python -m pytest -q -p no:cacheprovider \
  tests/test_permission_authority_is_unconditional.py \
  tests/test_mode_permission_policy.py \
  tests/test_scope_authority.py \
  tests/test_one_turn_owns_its_effect_receipts.py \
  tests/test_a_receipt_names_a_tool_that_ran.py \
  tests/test_honesty_receipt.py tests/test_honesty_receipt_cli.py \
  tests/test_sandbox_command_failure_is_not_success.py \
  tests/test_job_runner_real_confinement.py \
  tests/test_mcp_env_is_an_allowlist.py \
  tests/test_local_only_zero_public_egress.py \
  tests/test_install_profile_cli.py \
  "tests/updater/test_boot_api_ui.py::TestHonestDisable::test_no_trusted_key_means_unavailable_not_silent" \
  "tests/updater/test_download_resume.py::TestVerificationFailures::test_invalid_artifact_signature_refused"
```

Some tests exercise a real OS backend and are skipped elsewhere: the job-runner confinement
tests need macOS. The updater's full end-to-end journeys also need macOS (they call
`codesign`), so the command above runs only the two updater tests the registry cites. A skip is not a pass; the registry marks which platform proves each claim.
The full suite runs in [CI](https://github.com/Parad0x-Labs/vool/actions/workflows/ci.yml);
`python -m pytest -q` runs it locally (it is large).

## 4. Run VOOL itself

Start the local service from the checkout:

```bash
python -m apps.vool_api_server
```

Then check, in a browser or with `curl`:

* <http://127.0.0.1:11435/healthz> — the build, commit and whether the tree is modified.
* <http://127.0.0.1:11435/api/runtime/capabilities> — which features and modes are on.
* <http://127.0.0.1:11435/trace> — the live activity rail: every tool call, its decision and
  its result.

The service starts without a model. To chat, connect one: a local model through Ollama
([local models](docs/guides/local-models.md)) or your own cloud key
([connect a model](docs/getting-started/connect-a-model.md)). Then give it a real task in a
workspace you created for the review, and compare what it says it did with `/trace`.

## 5. Check the signed receipts

```bash
python -m core.honesty_receipt demo
python -m core.honesty_receipt verify-last
```

`demo` signs sample receipts, verifies them offline, and shows two tampering attempts failing.
`verify-last` verifies the most recent real session ledger in your VOOL home (on a fresh
install it reports that there is nothing to verify). The demo also states its own limit:
receipts removed from the end of a chain leave no trace, so completeness is not proven.

## What this guide does not cover

* Answer quality, memory recall and long tasks: these depend on the model and are not
  something a scripted demo can prove. Try them in the running app.
* Optional wallet and payment features, and the experimental peer networking: off by default;
  see the [repository scope map](docs/REPOSITORY_SCOPE.md).
* Desktop installers and signing: see [release status](docs/trust/release-status.md).

Found something that does not hold up? Report it through the
[security policy](SECURITY.md) for security issues, or open an issue for anything else.
