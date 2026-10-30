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

Plugin tools are offered to the model only while **Let chats use plugin tools** is on in the
Plugins section, and then only for plugins the owner enabled. The setting is off by default.
`VOOL_PLUGIN_RUNTIME_TOOLS=1` also turns it on, and is still the only switch for older
packages without the `vool` block. Model provider and scanner plugins work either way. The ids `vool-plugin-usepod` and
`vool-plugin-eyebrow` belong to VOOL's own packages, recognised by their exact bytes; a
package that only claims one of them is refused.

Install through the Plugins section, review the entire distribution, then install disabled.
Connect your own key in its card and accept the separate access sheet to enable it. Saving
a key makes no request. Keys live under `plugin.<id>.<slot>` and are resolved only for that
plugin's invocation. Wildcards, IP addresses, localhost and private destinations are refused.

### Keys

A handler always receives its keys on its private input: the JSON request on standard
input carries them under `context.credentials`, keyed by the declared environment names.
Read them from there; it works on every platform. On Linux they are also set as those
environment variables. On macOS they never are, because any program of the same user that
runs outside the sandbox can read another program's starting environment there.

An MCP server receives its declared keys as environment variables, as Claude Code and Codex
expect. On macOS such a server therefore runs only in the optional isolated runtime
(`VOOL_PLUGIN_DOCKER_SOCKET`, a local Unix socket, and the already present image pinned by
`core/plugin_container.py`); without it, VOOL keeps that plugin off and says why. There the
keys travel on the server's input into the container and are set as environment variables
only inside it, never in a process on the Mac. An MCP server that needs no key runs in the
macOS sandbox like any handler.

### Network

On macOS a tool that declares network runs in the sandbox with exactly one allowed
destination: a private proxy VOOL starts for that call, which connects only to the hosts in
`network`. Every other address, including the plugin's own direct sockets, is refused. The
proxy reads the TLS greeting and refuses a connection whose server name is not the declared
host, so a plugin cannot reach another site that shares that host's address. One connection
carries at most 64 MB. On
Linux and in the isolated runtime a process cannot reach that proxy, so the tool runs with no
network, as the access sheet says. Native provider and scanner requests are made by VOOL
itself, to declared hosts only, on every platform.

### Files and processes

The package and approved dependency copies are read-only; writable tools receive only their
own scratch. Python handlers in the isolated runtime use its interpreter; other bundled
executables must support its Linux architecture. Unsupported confinement refuses execution.
The macOS sandbox also refuses reading another program's arguments and starting
environment. Paths in a handler's or server's `args` are passed to your program but never
made readable: keep every file your code reads inside the package.

Every invocation checks enabled state and exact package/dependency digests. Disable stops
owned work and discards late results; turning a plugin off in the Plugins panel does the same
and also withdraws its consent. Changed bytes withdraw consent. Updates and re-installs
start disabled. An update from another repository or publisher does not keep the saved keys;
the owner saves them again after reviewing it. Uninstall retains history and defaults to
deleting saved keys.

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
