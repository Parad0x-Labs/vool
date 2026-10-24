# Writing a plugin

Skills teach a workflow; plugins add tools or services. Start from the complete examples in
`examples/plugins/`. Bundle source, dependencies and locks, README, MIT LICENSE and CHANGELOG.
Keep runtime output in scratch, never beside executable code.

## Manifest

Extend `.codex-plugin/plugin.json` with the `vool` block specified by the repository's
`docs/reference/plugin-manifest.schema.json`. Package `name` and
`vool.id` must agree, as must both semantic versions. Declare a display name, publisher,
description, public HTTPS homepage and licence.

| Field | Meaning |
| --- | --- |
| `kind` | One or more of `tools`, `model_provider`, `scanner`. |
| `tools` | Existing runtime contracts: intent, description, input schema, handler, effects and permissions. |
| `mcpServers` | Bundled commands, arguments and declared credential environment names. |
| `network` | Exact lower-case public HTTPS hosts, or an empty list. |
| `credentials` | Slot labels, help URLs and environment names; never key values. |
| `model_provider` | HTTPS base URL, header or path authentication, model list and pricing source. |
| `scanner` | HTTPS base URL, test/scan paths, input template and validated response mapping. |

Tool intents belong to the package namespace. Subprocess handlers use the existing JSON
stdio protocol. Each MCP tool names its bundled server and explicitly pins its effect class
and permission actions; unpinned tools have no trusted execution path. Commands stay inside
the package. Runtime fetchers such as `npx` and `uvx` are refused.

Native providers are data only and use the existing OpenAI-compatible adapter. Generic path
authentication can place `{credential}` in a wire path; the key is never saved in a model URL.
Published prices are service reports, not charge receipts or spending authority. Native
scanners receive requested public add-on content through the shared service transport.

## Access

Install through the Plugins section, review the entire distribution, then install disabled.
Connect your own key in its card and accept the separate access sheet to enable it. Saving
a key makes no request. Keys live under `plugin.<id>.<slot>` and are resolved only for that
plugin's invocation. Wildcards, IP addresses, localhost and private destinations are refused.

Executable process network is currently off on the isolated backends; native provider and
scanner HTTPS works separately. On macOS the owner must provision a local runtime. Advanced
setup uses `VOOL_PLUGIN_DOCKER_SOCKET`, a local Unix socket, and the already present image
pinned by `core/plugin_container.py`. VOOL never provisions either through a plugin. Python
handlers use the guest interpreter; other bundled executables must support the guest Linux
architecture. The package and approved dependency copies are read-only; writable tools
receive only their own scratch. Immutable macOS service packages may use threads but cannot
fork detached processes. Unsupported confinement refuses execution.

Every invocation checks enabled state and exact package/dependency digests. Disable stops
owned work and discards late results. Changed bytes withdraw consent. Updates and re-installs
start disabled. Uninstall retains history and defaults to deleting saved keys.

Plugins cannot request wallet, payment, signing, cap, budget, approval or receipt-writing
authority, owner paid model keys or another plugin's credentials. Auto-enable and autostart
are forbidden. Remote and phone turns cannot invoke executable plugins. Output stays
untrusted data. An x402 declaration never approves payment: VOOL's existing owner-controlled
wallet, caps, approval and receipts still govern any retained monetary flow.

## Test and publish

Use VOOL's actual parser, then exercise install, decline, enable, invoke, disable, update and
uninstall in a disposable profile. Include missing keys, invalid responses, timeout, tampering
and cancellation. Keep secrets out of fixtures and logs. Provider/scanner transport tests
should use a real TLS service process and disposable CA.

Keep `.claude-plugin/plugin.json` minimal: `name`, `version`, `description`. Include `.mcp.json`
when bundling a stdio server, and prove initialization, `tools/list` and an actual call using
its exact command. The template demonstrates that exchange. Metadata alone does not add
native providers or scanners to other hosts. Installation inside Claude Code and Codex
requires testing in those applications.

For the existing registry, supply `registry.json` with `kind: plugin` and your publisher/package
identity. Set package `name`, `vool.id` and tool namespaces to
`core.registry.artifact.native_identity(package_id)` before packing. Run
`python -m tools.community_skills pack` with scratch and output outside the distribution.
The registry performs complete deterministic and independent review; its existing signing
authority binds the full distribution digest. Local validation is not admission. A store
review never enables the plugin or grants runtime permissions.
