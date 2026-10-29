---
description: Extend VOOL with skills and plugins.
---

# Skills and plugins

A **skill** is a packaged set of instructions for a recurring task. A **plugin** adds tools,
a model provider or an add-on scanner. Plugins may also bundle skills.

## Bundled versus externally installed

VOOL ships with a **native skill library** — the skills that come with the app itself — and,
in current builds, the **VOOL Database** plugin. You can also install skills and plugins of
your own.

| | Bundled (ships with VOOL) | Externally installed |
| --- | --- | --- |
| Where it lives | Inside the app | Your plugins folder (set in Settings) |
| Updates | With the app | When you update the pack |
| Can be disabled | Yes — individually | Yes — individually |
| Grants permissions | **No** | **No** |

Two rules hold for both sources:

* **Skills teach; they never grant.** A skill's declared tools are a *narrowing* signal (the
  workflow's tool budget), never a grant of anything. The permission controller does not read
  skills.
* **One inventory.** The catalogue the console shows and the packs the runtime loads are the
  same list. A skill appearing in a list is not proof it can load — each entry carries its
  live state (available, disabled, prerequisite missing, invalid contract) and the reason.

## Finding skills

Skills & Plugins lists VOOL's own skills first. **Browse** comes with 53 ready VOOL
skills. They are text-only: each one is a set of instructions, bundled with the app, so
Browse lists them without a lookup online. VOOL skills that use VOOL tools are listed in
the **Skill Codex** tab, where VOOL team skills come first. Review a skill before you
enable it.

## Availability states

A skill can show as unavailable with a typed reason: a missing prerequisite (for example, the
database skill needs the database plugin enabled), an unavailable capability on this machine,
an invalid contract, or disabled by you. Reasons are shown per entry, and disabling is
per-entry and persistent across restarts.

## Duplicate identities

If two packs declare the same plugin identity (a bundled pack and an installed copy, say),
the bundled one is kept **deterministically** and the dropped copy is reported in the
catalogue — never silently shadowed.

## Installing your own

Install from the in-app catalogue, or point VOOL at a plugins folder of your own. Packs carry
a manifest (`plugin.json`) declaring their tools; a manifest whose permission declarations do
not match its declared behaviour class is refused at load.

{% hint style="danger" %}
Review the entire package before verification. A review is evidence about specific bytes,
not a safety certificate or a grant of permissions.
{% endhint %}

## Plugins in Add-ons

Open **Add-ons → My add-ons → Plugins**. Review a local folder or a public GitHub folder at
an exact commit. Installation captures the complete package and keeps it disabled. Signed
store plugins use the existing registry chain and also install disabled. Review the access
sheet separately before enabling tools or services. Saving a service key causes no request;
connection tests and model-list refreshes are explicit actions.

Disable removes tools and stops running calls. Uninstall disables first, removes owned files
and scratch, defaults to deleting saved keys, and retains history. Updates and re-installs
require a new review and start disabled. UsePod and Eyebrow reference plugins live under
`examples/plugins/`; they are examples maintained with VOOL, not service endorsements.
Older saved keys are kept. Add-ons explains how to reuse them after explicitly installing
and enabling the reference. Previous UsePod model pins fall back to an already chosen
configured provider or are cleared, with a notice and automatic escalation turned off.

See [Plugin authoring](plugin-authoring.md) and [Add-on checks](addon-security.md).

## Complete package approval

Verification covers every package file and directory, including handler executables,
vendored libraries, dependency locks, references, assets and executable mode bits.
Adding, removing, renaming or changing any of these invalidates approval. Symlinks,
hardlinks, special files, privileged modes, unstable reads and oversized distributions
are refused. The current capture limits are 4,096 entries, 64 MiB per file and 128 MiB
per package. There are no ignored cache or state directories inside a package.

Previously stored metadata-only approvals do not activate packages in this version.
Review the full distribution, explicitly verify it again, then enable it. Updates remain
disabled until verified and enabled; approval is never silently migrated.
Managed add-ons also need a fresh authenticated store review when an older receipt
does not bind the complete distribution; ordinary verification cannot replace that receipt.

`requires` names installed plugin dependencies. Verification pins their recursive approval digests;
each dependency must already be verified and enabled. A changed, updated, disabled or
revoked dependency makes its dependants unavailable, including through cached tools.
Admission rechecks enabled and revoked state for the whole captured closure after hashing.
Re-verifying a dependency does not silently approve its new bytes for the parent: review
and re-verify every parent too. Each `approval_digest` binds its package digest and dependency
approvals, so a changed transitive dependency changes that approval even when the parent's
own files stay identical. Cyclic dependency approval cannot activate. The dependency closure
is limited to 64 packages.

Each registered invocation checks lifecycle state again and captures private read-only
copies of the package and its pinned dependency closure. The tool contract must match the
captured manifest. The registered handler launches from that capture; editing the source
after capture cannot replace that handler or its bundled files. Execution observations
include package, recursive approval, and dependency digests. Registered packages require actual kernel confinement even if a
local low-level caller selected a weaker override.
Hosts without safe no-follow descriptor capture or kernel confinement refuse registered
packages. Invocation timeouts are bounded to 1–300 seconds; the invocation's owned
process group is stopped before its execution copy is removed.

Keep persistent output, caches and logs in `PLUGIN_SCRATCH`, not beside executable code.
Read-only tools receive no write grant. Other tools may write scratch but cannot modify
their execution copy. `VOOL_PLUGIN_DEPENDENCIES` is a JSON mapping from declared dependency
identities to the captured dependency directories. Bundle other third-party libraries
and their locks inside the package; a lockfile alone does not attest an external environment.
The runtime interpreter and system libraries remain part of the trusted platform, rather
than being represented as reviewed third-party package bytes. This integrity check does
not certify behavior, attest arbitrary code downloaded or loaded from outside the approved
distribution, broker every internal effect or replace native wallet approval.

On macOS, plugin code runs in the macOS sandbox. Keys reach a handler only over its private
input, never its environment, because any program of the same user can read another
program's starting environment on a Mac. A connection server that needs a key in its
environment runs only in the optional isolated Linux runtime, which you provision yourself;
VOOL never starts a VM or downloads an image. Without it, that plugin stays off and says why.
Linux uses bwrap, including a private process namespace and `/proc`.

On macOS a tool that declares network can reach only a private proxy VOOL starts for each
call, and the proxy connects only to the declared hosts. On Linux and in the isolated runtime
plugin processes have no network. Native provider and scanner requests use their separate
declared-host HTTPS transport on every platform.

## Writing a skill

A skill is a folder with an instruction file and any files it references:

```text
my-skill/
├── SKILL.md
└── templates/
```

Draft in-app (`skill.create` stages it without activating anything), validate it, then
install to activate — three separate steps on purpose, so authoring is never an implicit
behaviour change. The catalogue and authoring format are still being finalised for public
release.
