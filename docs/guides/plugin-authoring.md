# Writing a plugin

Skills teach a workflow; plugins add tools or services. Start from the complete example in
`examples/plugins/template`. Bundle source, dependencies and locks, README, MIT LICENSE and CHANGELOG.
Keep runtime output in scratch, never beside executable code.

## Manifest

Extend `.codex-plugin/plugin.json` with the `vool` block specified by the repository's
`docs/reference/plugin-manifest.schema.json`. Package `name` and
`vool.id` must agree, as must both semantic versions. Declare a display name, publisher,
description, public HTTPS homepage and licence.

| Field | Meaning |
| --- | --- |
| `kind` | One or more of `tools`, `model_provider`, `scanner`, `browser`. |
| `tools` | Existing runtime contracts: intent, description, input schema, handler, effects and permissions. |
| `mcpServers` | Bundled commands, arguments and declared credential environment names. |
| `network` | Exact lower-case public HTTPS hosts, or an empty list. |
| `credentials` | Slot labels, help URLs and environment names; never key values. |
| `model_provider` | HTTPS base URL, header or path authentication, model list and pricing source. |
| `scanner` | HTTPS base URL, test/scan paths, input template and validated response mapping. |
| `browser` | `extension`: the folder inside the package that holds a standard browser extension. |

Tool intents belong to the package namespace and have one shape: `PLUGIN-ID.TOOL` (the plugin's id, a dot, the tool's name), the tool part in
lowercase letters, digits and `_` (for example `parcel-desk.lookup`). An MCP tool's own name uses
letters, digits, `.`, `_` and `-`. Subprocess handlers use the existing JSON
stdio protocol. Each MCP tool names its bundled server and explicitly pins its effect class
and permission actions; unpinned tools have no trusted execution path. Commands stay inside
the package. Runtime fetchers such as `npx` and `uvx` are refused.

Native providers are data only and use the existing OpenAI-compatible adapter. Generic path
authentication can place `{credential}` in a wire path; the key is never saved in a model URL.
Published prices are service reports, not charge receipts or spending authority. Native
scanners receive requested public add-on content through the shared service transport.

## Access

Plugin tools are offered to the model only while **Let chats use plugin tools** is on in the
Plugins section, and then only for plugins the owner enabled. Once the owner ticks or unticks
it, that choice holds. Until then it follows the `VOOL_PLUGIN_RUNTIME_TOOLS` switch, which is
on unless set to `0`, and older packages without the `vool` block follow only that switch.
Model provider and scanner plugins work either way. The ids `vool-plugin-usepod` and
`vool-plugin-eyebrow` are reserved for the retired UsePod and Eyebrow services, and VOOL
offers no plugin for either; another package that claims one of them is refused.

Other ids VOOL keeps for itself are refused the same way: the id of any pack shipped inside VOOL
(such as `vool-database`), any id that starts with `vool` or `parad0x`, and any name VOOL's own
tools start with (`email`, `skill`, `workspace`, `memory` and the rest). The name, the publisher and
each key label are plain text: letters, numbers, spaces and a few marks. They may not carry VOOL's
name, a word or mark that says the plugin was checked or chosen (Official, Verified, Tested by,
Editor's Choice, a tick, a star), hidden characters, look-alike letters or a link. The refusal
names which of the three it is. Put your own name in `publisher`; the template ships "Your name".

Install through the Plugins section, review the entire distribution, then install disabled.
Connect your own key in its card and accept the separate access sheet to enable it. Saving
a key makes no request. Keys live under `plugin.<id>.<slot>` and are resolved only for that
plugin's invocation. Wildcards, IP addresses, localhost and private destinations are refused.

### Keys

A handler always receives its keys on its private input: the JSON request on standard
input carries them under `context.credentials`, keyed by the declared environment names.
Read them from there; it works on every platform. On Linux they are also set as those
environment variables. On macOS they never are, because any program of the same user,
plugin code included, can read another program's starting environment there.

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
On a Mac, code a plugin runs can still see other programs' starting settings, as any app you
install can; the access sheet says so. Paths in a handler's or server's `args` are passed to
your program but never made readable: keep every file your code reads inside the package.

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

### Paid services

A tools plugin may list up to eight `paid_services` in its `vool` block: `https://` origins on
websites it already lists under `network`, with no path or query. A tool of that plugin may then
end its result with `"observation": {"paid_fetch": {"url": "https://..."}}`. The plugin never
pays: VOOL fetches that address itself and pays the service's x402 request only from the
Skills & plugins wallet, only after the owner ticked this plugin in Settings, only inside the
limits the owner set for it (per payment, per day or other period, payments per period, end
date), and only on the owner's own turn at this computer. Anything else pays nothing and the
tool's text says so. What the service delivers comes back as the tool's text, still untrusted.

## Browser plugins

A browser plugin changes what the VOOL browser window does on web pages: block adverts, tidy a
site, add a button. Its package is an ordinary plugin with kind `browser` and one extra folder
holding a standard browser extension, the same format the major browsers load (version 3).

The quickest start is **Make a browser plugin** in the Plugins section: give it a name and VOOL
writes a working plugin into `my-browser-plugins` beside your installed plugins.

```
calm-reading/
  .codex-plugin/plugin.json   name, version, kind ["browser"], "browser": {"extension": "extension"}
  extension/manifest.json     which pages it runs on and what it asks the browser for
  extension/page.js           the script that runs on those pages
  README.txt
```

Change `extension/page.js`, or give the folder to VOOL in chat and say what the plugin should
do. Then review the folder under **Install from a folder**, install it and enable it. After a
change, raise the version in both json files and tick **Review an update to an installed
plugin**. The open browser window picks up an enabled plugin at once.

What VOOL checks at review, before anything is installed:

- the extension is version 3 of the format and names no update address: updates come only
  through a new review;
- it does not ask for `nativeMessaging`, `debugger`, `proxy`, `vpnProvider`, `management`,
  `privacy`, `passwordsPrivate`, `webAuthenticationProxy`, `certificateProvider`, `platformKeys`,
  `contentSettings`, `desktopCapture`, the `enterprise` group or `downloads`, and it does not
  replace the start page or search (`chrome_settings_overrides`, or `newtab` under
  `chrome_url_overrides`). These reach saved sign-ins,
  the browser's connection and safety settings, other plugins, the screen and programs outside
  the browser, or save files without the person.

The review lists the sites the extension can read and change, and says so plainly when that is
every site. A browser plugin is never loaded in a private window. VOOL loads a copy of the
approved package each time the window starts, so the extension cannot change its own code.
A package can be both a browser plugin and a tools plugin; each part follows its own rules.

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

Share a plugin as its folder, or as a folder in a public GitHub repository at an exact commit
(Add-ons, Plugins, "Review from GitHub": repository, the full 40-character commit, the folder).
From VOOL 0.8 a plugin can also be published in the Skill Codex (section "Publishing a plugin" in `docs/registry/AUTHOR-GUIDE.md`). Check a folder before you share it
with `python -m tools.plugin_check FOLDER` (add `--json` for a tool or an agent): it prints the
sentence a person installing would read and, for the maker, every field at fault and what to write
instead. The guides for makers are in [docs/creators](../creators/README.md).
