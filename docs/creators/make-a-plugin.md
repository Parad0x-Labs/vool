---
description: From the template to a VOOL plugin that passes the check, is reviewed, installed, switched on and called. The manifest field by field.
---

# Make a plugin

Plugins need VOOL 0.8.0 or newer.

A plugin is a folder with your program and one setup file, the **manifest**, which lists everything the
program needs: its tools, the websites it reaches and the keys it uses. VOOL runs the program walled off with
exactly that and nothing more. The owner reads the list before switching the plugin on.

If words are enough for your task, [make a skill](make-a-skill.md) instead: it is quicker.

## 1. Start from the working example

From the root of a VOOL source checkout:

```sh
cp -R examples/plugins/template word-tally
python -m tools.plugin_check word-tally
```

`examples/plugins/template` is a complete plugin: one tool that returns the SHA-256 fingerprint of a text,
served by a small bundled program, `bin/server`. It needs Python 3 and nothing else. The folder holds:

| File | What it is for |
| --- | --- |
| `.codex-plugin/plugin.json` | The manifest VOOL reads |
| `bin/server` | The program (here a small MCP server: a program that offers tools over a standard protocol) |
| `README.md`, `LICENSE`, `CHANGELOG.md` | For the people who review your plugin |
| `.claude-plugin/plugin.json`, `.mcp.json` | For other apps that read those files. VOOL does not read them |

Try the program by hand, from inside the folder:

```sh
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"sha256_text","arguments":{"text":"hello"}}}' | bin/server
```

The third line of the answer holds the result:

```text
{"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "{\"sha256\": \"2c:f2:4d:ba:5f:b0:a3:0e:26:e8:3b:2a:c5:b9:e2:9e:1b:16:1e:5c:1f:a7:42:5e:73:04:33:62:93:8b:98:24\", \"bytes\": 5}"}], "isError": false}}
```

## 2. Make it yours

In `word-tally/.codex-plugin/plugin.json` change:

| Change | To |
| --- | --- |
| `name` and `vool.id` | Your plugin id, the same in both, for example `word-tally` |
| `vool.name` | The name people see, for example `Word tally` |
| `vool.publisher` | Your own name |
| `vool.homepage` | Your `https://` page |
| the tool's `intent` | `word-tally.<tool>`, for example `word-tally.sha256` |
| both `description` fields, both `version` fields | Your own |

Set the same `name`, `version` and `description` in `.claude-plugin/plugin.json`. Then replace `bin/server`
with your code and check again:

```sh
python -m tools.plugin_check word-tally
```

```text
OK: word-tally 1.0.0 would be offered for review. Tools: word-tally.sha256. Reaches: no websites.
```

## 3. The check command

`python -m tools.plugin_check FOLDER [--json]` reads the folder by the same rules the Add-ons page uses.
Nothing is installed, started or sent anywhere.

| Exit code | Meaning |
| --- | --- |
| 0 | The folder would be offered for review |
| 1 | It is refused. The sentence and the fix are printed |
| 2 | The folder could not be read |

On a refusal it prints two lines: the sentence a person installing the plugin would read, then a note for
you with the field at fault and what to write instead. A real one: the manifest asks for a key under the
name `OPENAI_API_KEY`.

```text
Refused: A plugin may not ask for owner model keys, and it can't change settings for other programs. It was not installed.
For the plugin's maker: vool.credentials.main.env: 'OPENAI_API_KEY' is kept back. Pick a name of your own for the key, such as ACME_API_KEY. Not accepted: the owner's model keys (OPENAI_, ANTHROPIC_, GOOGLE_ ...), VOOL's own (VOOL_), system names (PATH, HOME, HTTPS_PROXY ...) and language runtime names (PYTHON..., NODE_..., LD_..., DYLD_...).
```

With `--json` it prints one JSON object. The same refusal:

```json
{"fix": "'OPENAI_API_KEY' is kept back. Pick a name of your own for the key, such as ACME_API_KEY. Not accepted: the owner's model keys (OPENAI_, ANTHROPIC_, GOOGLE_ ...), VOOL's own (VOOL_), system names (PATH, HOME, HTTPS_PROXY ...) and language runtime names (PYTHON..., NODE_..., LD_..., DYLD_...).", "message": "A plugin may not ask for owner model keys, and it can't change settings for other programs. It was not installed.", "ok": false, "where": "vool.credentials.main.env"}
```

And a pass:

```json
{"files": 7, "ok": true, "plugin": {"id": "text-digest-example", "kind": ["tools"], "name": "Text digest example", "network": [], "publisher": "Your name", "version": "1.0.0"}, "tools": ["text-digest-example.sha256"]}
```

| JSON field | Holds |
| --- | --- |
| `ok` | `true` or `false` |
| `message` | On a refusal: the sentence the installing person reads |
| `where` | On a refusal: the manifest field at fault |
| `fix` | On a refusal: what to write instead |
| `plugin`, `tools`, `files` | On a pass: what VOOL read, the tool names and the number of files |

The check lists every problem it can find in one run, each with its field and its fix; `--json` carries them in `problems`. Fix them and run again until it prints `OK`.
Every refusal with its fix: [plugin refusals](reference/plugin-refusals.md).

## 4. The manifest, field by field

At the top: `name` and `version`. Everything VOOL needs sits in the `vool` block.

| Field | In plain words |
| --- | --- |
| `id` | The plugin's id: lowercase letters, digits, `-` and `_`, starting with a letter, at most 80 characters. Must equal the top-level `name`. |
| `version` | Three numbers such as `1.0.0`. Must equal the top-level `version`. |
| `name` | The name people see. Plain letters, numbers and spaces. |
| `publisher` | Who made it: your own name. |
| `description` | What it does, in a sentence or two. |
| `homepage` | A full `https://` address where people can read about it. |
| `license` | The licence name, for example `MIT`. Put the full text in `LICENSE`. |
| `kind` | What the plugin adds: one or more of `tools`, `model_provider`, `scanner`. |
| `network` | Every website the plugin reaches, each as one exact host such as `api.example.com`. An empty list means it reaches none. |
| `credentials` | The keys the plugin needs from the owner. Each slot has a `label` the owner sees, a `help_url` where they get the key, and an `env` name of your own, such as `ACME_API_KEY`. Never a key itself. |
| `mcpServers` | Programs in the folder that serve tools: `command` (a path inside the folder), `args`, and `env` (only your own key names). |
| `tools` | The tools the plugin adds. See below. |
| `paid_services` | Optional: up to 8 services VOOL may pay for the plugin, inside limits the owner sets. See [Rules and why](rules-and-why.md#money). |
| `model_provider`, `scanner` | For a plugin that connects a model service or a scanner. These are settings only, no program. See [Writing a plugin](../guides/plugin-authoring.md). |

The first ten rows are required, even when `network` is an empty list and `credentials` an empty object.

Each entry in `tools`:

| Field | In plain words |
| --- | --- |
| `intent` | The tool's name: your plugin id, a dot, then a short name in lowercase letters, digits and `_`. Example: `word-tally.sha256`. |
| `description` | What the tool does, at least 12 characters. VOOL uses it to decide when to offer the tool. |
| `input_schema` | What the tool takes, as a JSON Schema object. |
| `handler` | How VOOL runs it. `"kind": "mcp"` with `server` (a name from `mcpServers`) and `tool` (the name that server gives it); or `"kind": "subprocess"` with `entry`, a program inside the folder. `timeout_seconds` is 1 to 300. `network: true` asks for the websites in `vool.network`. |
| `trust` | What the tool does to the world: `side_effect_class` is one of `read_only`, `workspace_write`, `network_send`, `network_publish`, with the matching `permission_actions`. Leave it out and the plugin still passes the check, but the sheet says *VOOL can't tell what this does, so it asks you every time*. |

The published schema is `docs/reference/plugin-manifest.schema.json`. How keys reach your program and how
network access works on each system: [Writing a plugin](../guides/plugin-authoring.md#keys).

## 5. Install it and read the review sheet

In VOOL open **Add-ons > My add-ons > Plugins**.

1. Press **Install from a folder**, choose your folder, press **Review folder**.
2. VOOL shows **Files in this package**, each file that *can run as a program*, the note *Not reviewed by the
   VOOL store* and the line *VOOL checks these files haven't changed. That does not prove they are safe.*
3. Press **Install and keep disabled**. The plugin is installed and off.
4. Press **Review access and enable**. This is the access sheet. It opens with *Enabling lets this plugin use
   the access listed below. Your normal permission checks still apply.* and lists:
   - **Network access**: every host from `network`, or *No network access.*
   - **Service keys**: every key slot by its label, or *No service keys requested.*
   - what each tool does: *Read information*, *Change files in its own working space*, *Send information* or *Publish information*
   - *Runs a small helper program on this computer.* when the plugin bundles one
   - on a Mac: *On a Mac, a plugin's programs can see some settings of other apps you run, like any app can. Your plugin keys are never put there.*
5. Press **Accept and enable**, or **Keep disabled**.

If the plugin needs a key, the owner pastes it into the plugin's card under **Service keys** and presses
**Save key**. Saving a key makes no request.

A refused folder shows the sentence from the check, and under it **For the plugin's maker** with the same
note the command prints.

## 6. Call it

Plugin tools are offered in chats while **Let chats use plugin tools** is on in the Plugins section and the
plugin is enabled. Ask in a chat for what the tool does, in words close to its `description`. Every call
goes through the owner's normal permission checks, and the plugin's answer is treated as data, never as
instructions.

Three things to know while you develop:

- **A changed file switches the plugin off.** VOOL checks the exact bytes on every call. Edit, then review and enable again.
- **An update starts disabled** and needs a new review.
- **Disable** stops running calls and drops late results. **Uninstall** removes the files and, unless the owner unticks it, the saved keys.

## 7. Share it

Plugins are shared in two ways:

- **A folder.** Send the folder; the other person uses **Install from a folder**.
- **A public GitHub folder at an exact commit.** Push the folder to a public repository. The other person
  presses **Install from public GitHub source** and enters the **GitHub project (owner/name)**, the
  **Version to check** (the full 40-character commit) and the **Folder inside the project**, then ticks
  *Download the public package for review and check its contents on this device*. VOOL checks every file
  against that commit, then shows the same review. A commit never changes, so what they review is what you
  published.

- **The Skill Codex.** Everyone can find and install it from the website and from inside VOOL.

Publish a plugin to the Skill Codex with three commands, then upload the packed file:

```text
python -m tools.community_skills plugin-init FOLDER --namespace YOUR-PUBLIC-NAME --category CATEGORY --subcategory SUBCATEGORY
python -m tools.community_skills lint FOLDER --scratch SCRATCH-FOLDER
python -m tools.community_skills pack FOLDER --scratch SCRATCH-FOLDER --output NAME.vool.json
```

`plugin-init` writes the listing (`registry.json`) from your manifest, so the two agree. Three things must hold:
the package id ends in the plugin's id (`your-name/parcel-desk` for the plugin `parcel-desk`), the listing `title`
equals `vool.name`, and the maker shown is your public name, never `vool.publisher`. A published plugin installs
through the same review, switched off, and nothing about publishing loosens a rule: the same checks run at upload
and again on the installer's computer. Details: [Publishing a plugin](../registry/AUTHOR-GUIDE.md#publishing-a-plugin).

A package may hold at most 4,096 files and 128 MB in all,
64 MB per file.

## 8. Before you hand it over

- `python -m tools.plugin_check FOLDER` prints `OK`.
- Your program runs by hand with the inputs you expect and with bad ones.
- `README.md` says what the plugin reaches, which keys it needs and what it changes.
- No key, password or private file is in the folder.
- You installed, enabled, called, disabled and uninstalled it in your own VOOL.

Rules your plugin meets and the reason for each: [Rules and why](rules-and-why.md).
