# VOOL Skills

Community skill packages for VOOL. The canonical catalog is maintained in the VOOL repository; this repository stores immutable public submissions and their provenance.

Use the `vool-community` SDK from the released VOOL version to lint and package your native skill. Put the exact canonical output at `packages/SHA256.json` and open a PR from your own account. Alternatively, submit SKILL.md and licence text through the website's private form. Do not put secrets into a public PR.

Your original author and licence remain in the package/source metadata. A submission is not an endorsement or approval. All required independent security, native-rule and exact-release compatibility reports must pass before protected merge and signed catalog admission. Skills cannot override VOOL's own budgets, currency handling, settings, permissions or approval requirements.

Maintainers configure the published VOOL source pin, public root, policy and independent signed reports in protected paths. Until those exist, admission stays blocked. Uploaded content is data to the public PR workflow; it is never executed with repository secrets or publisher credentials.

Stars and download-request totals belong to stable package IDs in the website registry, not GitHub repository stars. Details and author instructions are available in VOOL's `docs/registry`.

The `Registry advisory preflight` workflow is candidate-editable and is not an admission authority. Require the external admission App as the expected check source, or an independently protected workflow whose pinned workload identity and exact-input evidence are verified. A same-named green candidate job proves no trusted scan ran. Independent offline admission/catalog signatures remain required. See VOOL's `docs/registry/ADMISSION-SERVICE.md` for activation and custody requirements.
