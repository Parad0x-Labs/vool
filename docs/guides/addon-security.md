# Add-on checks

Add-ons keeps browsing, inspection and installation separate. A scan describes particular bytes;
it is not a safety guarantee, publisher endorsement, runtime permission or wallet approval.

Browse lists VOOL's own skills first, including 53 text-only VOOL skills bundled with the app,
then the reviewed catalogue add-ons. Skill Codex lists VOOL team skills first, above editor's
picks and featured skills.

## Public skills

Choose a public GitHub skill at a fixed revision and approve downloading its contents. VOOL checks
it on this device with `core/local_scan.py`. Without an enabled scanner plugin, the page explains
that the checks stayed on this device. No scanner key is required for that path.

An enabled scanner plugin also receives the public skill content when a scan is requested. This
can consume its service allowance. Local chats, private workspace files and other credentials are
not submitted. Saving a key and opening Add-ons make no connection test or scan request. There
are no automatic request retries.

Read findings and accept installation separately. Report validation requires complete coverage
and valid findings. A passing report has no findings or policy violations. A separately offered
risk acceptance binds the reviewed version; it never changes the report or grants more access.
Expired reviews, changed staging bytes, links and conflicting target folders are refused.
Receipts bind the complete installed distribution. Changed files withdraw runtime availability.
Skill guidance is inserted whole or explicitly omitted when it cannot fit the prompt budget.

## Optional scanners

Install `examples/plugins/vool-plugin-eyebrow` through the Plugins section, save your own key,
review access and enable it. Its declaration uses `/v1/version` for an explicit connection test
and `/v1/scan` for requested scanning. The shared native plugin transport owns those requests;
there is no built-in Eyebrow client or central Eyebrow key setting. Older saved keys are kept.

VOOL's own check on this device always runs. A scanner plugin's report is added beside it:
either one finding an issue blocks installation, and a plugin report must name the exact
content that was checked. A scanner plugin can never approve an installation on its own.

An enabled scanner with a missing or rejected key fails plainly. VOOL never silently replaces
that requested remote scan with a passing local result. Timeouts, service errors, redirects,
oversized responses and incomplete reports leave installation unaccepted. A timed-out request
may still have consumed service allowance.

## Executable packages and store review

Plugins use a complete local-folder or pinned public GitHub package review, separate from the
text-only skill importer. Installation runs no package code and keeps it disabled. Store plugins
also require the existing signed receipt and full distribution digest. Enablement requires a
separate access sheet covering tools, effects, exact hosts, credential slots and bundled servers.

Executable code receives only its own declared keys at invocation. It cannot read another
plugin's slots, owner paid model keys or wallet signing, cap, approval or receipt authority.
Its output stays untrusted. Changed bytes withdraw consent; disable stops calls. Unsupported
confinement refuses execution. See [Plugin authoring](plugin-authoring.md) for runtime limits.

## Local API and transport

`GET /api/addons` reads inventory. Mutations at `POST /api/addons` require owner-local access,
JSON, same-origin browser requests and action-specific fields. Public source downloads use
exact revisions and bounded verified blobs. Native services require declared public HTTPS hosts,
validate TLS, pin resolved public addresses, refuse redirects, bound responses and stop when
the plugin is disabled.

Local fake HTTPS services can verify transport and error handling without paid calls. They do
not establish live service reliability, billing, model competence or installation inside other
applications. Those require separate verification.
