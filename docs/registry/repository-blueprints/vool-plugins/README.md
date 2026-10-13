# VOOL Plugins

Community native plugin packages for VOOL. This repository stores immutable public artifacts and source/licence provenance; the existing VOOL catalog remains the discovery and reviewed-catalog authority.

Create a native package with `vool-community init`, then extend the existing plugin manifest/tools contract. Preserve every executable, vendored dependency, lock, reference and empty directory. Lint and pack, put the exact output at `packages/SHA256.json`, and open a PR from your own account. The website can also accept that SDK file privately before public consent.

Submitted code never runs on the website host or with review/publisher keys. Native permissions, spending controls, currency/payment handling, owner settings and approvals remain authoritative. A plugin declaration does not grant its requested access. Complete independent scans, native-boundary review and tests against the latest released VOOL build precede merge and signed catalog admission.

Maintainers configure protected policy/root/review inputs and require the trusted admission check. Missing coverage blocks publication. See VOOL's `docs/registry` for developer, website and operations contracts.

The `Registry advisory preflight` workflow is candidate-editable and is not an admission authority. Require the external admission App as the expected check source, or an independently protected workflow whose pinned workload identity and exact-input evidence are verified. A same-named green candidate job proves no trusted scan ran. Independent offline admission/catalog signatures remain required. See VOOL's `docs/registry/ADMISSION-SERVICE.md` for activation and custody requirements.
