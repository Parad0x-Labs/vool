# Make a plugin: agent procedure

For AI agents: make one VOOL plugin, check it, and hand it over. Needs VOOL 0.8.0 or newer, as a source checkout to build with and as the app to run in.

A plugin is a folder of code plus a setup file (the manifest, `.codex-plugin/plugin.json`) that states what the code is, which tools it adds, which websites it reaches and which keys it needs. VOOL runs it in a confined process with only what the manifest lists and the owner agreed to. It is off until the owner switches it on. Read [README.md](README.md) section 4 first.

`VOOL_SRC` is the checkout folder. Work in a new folder of your own, outside the checkout. Replace `word-tally` with your plugin's id.

## Procedure

1. Pick the id. Lowercase letters, digits and hyphens. It may not start with `vool` or `parad0x`, and may not be a name VOOL's own tools start with (`email`, `file`, `web`, `skill`, `memory` and others). Full list: [../reference/names-kept-back.md](../reference/names-kept-back.md).
2. Copy the working example.

   ```sh
   cp -R "$VOOL_SRC/examples/plugins/template" word-tally
   ```

   Layout you now have, and must keep:

   ```text
   word-tally/.codex-plugin/plugin.json   the manifest VOOL reads
   word-tally/.claude-plugin/plugin.json  name, version, description only
   word-tally/.mcp.json                   the same server command, for other apps
   word-tally/bin/server                  the program (executable; Python 3, standard library only)
   word-tally/README.md
   word-tally/CHANGELOG.md
   word-tally/LICENSE                     MIT; put the maker's name on the copyright line
   ```

3. Replace `word-tally/.codex-plugin/plugin.json` with your manifest. Complete working example (one tool, no websites, no keys):

   ```json
   {
     "name": "word-tally",
     "version": "1.0.0",
     "description": "Count the words and characters in text you supply.",
     "vool": {
       "id": "word-tally",
       "version": "1.0.0",
       "name": "Word tally",
       "publisher": "MAKER NAME",
       "license": "MIT",
       "description": "Count the words and characters in text you supply.",
       "homepage": "https://example.org/word-tally",
       "kind": ["tools"],
       "network": [],
       "credentials": {},
       "mcpServers": {
         "tally": {"command": "bin/server", "args": [], "env": {}}
       },
       "tools": [
         {
           "intent": "word-tally.count",
           "description": "Count the words and characters in the supplied text.",
           "input_schema": {
             "type": "object",
             "required": ["text"],
             "additionalProperties": false,
             "properties": {"text": {"type": "string", "maxLength": 4096}}
           },
           "handler": {"kind": "mcp", "server": "tally", "tool": "count_words", "timeout_seconds": 10},
           "trust": {"side_effect_class": "read_only", "permission_actions": ["read_files"]}
         }
       ]
     }
   }
   ```

   Rules for the fields:
   1. `name` equals `vool.id`. `version` equals `vool.version`.
   2. `vool.name` and `vool.publisher`: plain letters, numbers and spaces. `publisher` is the person's own name; ask if you do not know it.
   3. `vool.homepage`: a full `https://` address of the person's own page. Ask for it.
   4. Each tool's `intent` is `<plugin id>.<tool>`, the tool part in lowercase letters, digits and `_`.
   5. `handler.server` is a name declared in `vool.mcpServers`; `handler.tool` is the name the server gives the tool.
   6. `trust.side_effect_class` is one of `read_only`, `workspace_write`, `network_send`, `network_publish`.
   7. Add no field that is not in the example or in `docs/reference/plugin-manifest.schema.json` in the checkout. An unknown field is refused.

4. Replace `word-tally/bin/server` with your program. Keep it executable. Complete working example, a server that speaks MCP (the protocol VOOL uses to call a tool server) over standard input and output:

   ```python
   #!/usr/bin/env python3
   """Local stdio MCP server: counts the words and characters of the supplied text."""
   import json
   import sys
   from pathlib import Path

   manifest = json.loads((Path(__file__).resolve().parents[1] / ".codex-plugin/plugin.json").read_text())
   tool = manifest["vool"]["tools"][0]

   for line in sys.stdin:
       request = None
       try:
           request = json.loads(line)
           identity = request.get("id")
           if identity is None:
               continue
           method = request.get("method")
           if method == "initialize":
               result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                         "serverInfo": {"name": "tally", "version": "1.0.0"}}
           elif method == "tools/list":
               result = {"tools": [{"name": "count_words", "description": tool["description"],
                                    "inputSchema": tool["input_schema"]}]}
           elif method == "tools/call":
               parameters = request.get("params", {})
               arguments = parameters.get("arguments", {})
               text = arguments.get("text")
               if parameters.get("name") != "count_words" or set(arguments) != {"text"} or not isinstance(text, str) or len(text) > 4096:
                   raise ValueError("Supply text to the declared tool.")
               output = {"words": len(text.split()), "characters": len(text)}
               result = {"content": [{"type": "text", "text": json.dumps(output)}], "isError": False}
           else:
               raise ValueError("Unsupported operation.")
           response = {"jsonrpc": "2.0", "id": identity, "result": result}
       except (ValueError, TypeError, AttributeError):
           response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                       "error": {"code": -32602, "message": "The request does not match this server's declared tools."}}
       print(json.dumps(response), flush=True)
   ```

5. Make the other files agree.
   1. `.claude-plugin/plugin.json`: `{"name": "word-tally", "version": "1.0.0", "description": "..."}`.
   2. `.mcp.json`: `{"mcpServers": {"tally": {"command": "bin/server", "args": [], "env": {}}}}`.
   3. `README.md`: what it does, the websites it reaches, the keys it needs. `CHANGELOG.md`: one entry for 1.0.0. `LICENSE`: the maker's name.
6. Only when the plugin reaches a website: list each exact host and set the tool's trust to match.

   ```json
   "network": ["api.example.com"],
   "trust": {"side_effect_class": "network_send", "permission_actions": ["use_network_access"]}
   ```

   One host per entry: lowercase, no `*`, no `https://`, no path, no port, no address in numbers, no local name. On Linux a plugin's program runs with no internet access: when the person uses Linux, tell them before you build a plugin that needs a website.
7. Only when the service needs a key: declare a slot, never a value. The owner types the key into VOOL after installing.

   ```json
   "credentials": {"main": {"label": "Example service key", "help_url": "https://example.com/keys", "env": "EXAMPLE_SERVICE_KEY"}},
   "mcpServers": {"tally": {"command": "bin/server", "args": [], "env": {"EXAMPLE_SERVICE_KEY": "${EXAMPLE_SERVICE_KEY}"}}}
   ```

   Pick an `env` name of your own. Names of the owner's model keys (`OPENAI_`, `ANTHROPIC_`, `GOOGLE_`), `VOOL_` names and system names are refused. Then apply stop list item 1.

## Acceptance checks

Run all four. All must pass before you report.

1. The server answers by hand. From inside `word-tally`:

   ```sh
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"count_words","arguments":{"text":"one two three"}}}' | bin/server
   ```

   Expected: exit 0 and three JSON lines with ids 1, 2 and 3, each holding `"result"`. The third holds your tool's answer, here `{\"words\": 3, \"characters\": 13}` inside `content`.

2. VOOL's own check, in sentences.

   ```sh
   PYTHONPATH="$VOOL_SRC" python -m tools.plugin_check word-tally
   ```

   Expected: exit 0 and one line:

   ```text
   OK: word-tally 1.0.0 would be offered for review. Tools: word-tally.count. Reaches: no websites.
   ```

3. The same check as JSON.

   ```sh
   PYTHONPATH="$VOOL_SRC" python -m tools.plugin_check word-tally --json
   ```

   Expected: exit 0 and one JSON object:

   ```json
   {"files": 7, "ok": true, "plugin": {"id": "word-tally", "kind": ["tools"], "name": "Word tally", "network": [], "publisher": "MAKER NAME", "version": "1.0.0"}, "tools": ["word-tally.count"]}
   ```

   Confirm `tools` lists every tool you wrote and `network` lists exactly the hosts you meant.

4. Refusal drill. Prove the check is live: copy the folder, set `"network": ["*.example.com"]` in the copy's manifest, and run check 3 on the copy. Expected: exit 1, `"ok": false`, `"where": "vool.network[0]"`. Delete the copy.

Exit codes of `tools.plugin_check`: 0 the folder would be offered for review; 1 it is refused; 2 the folder could not be read. The check installs nothing, starts nothing and sends nothing: it reads the folder in a throwaway profile and removes its copy.

## How to read a refusal

With `--json`, a refusal is one object:

| Field | Meaning |
| --- | --- |
| `ok` | `false`. |
| `message` | The sentence a person installing the plugin would read. It names the kind of fault, not the field. |
| `where` | The manifest field at fault, for example `vool.network[0]`. Can be empty when the fault is not in one field. |
| `fix` | What to write instead. Act on this. |
| `problems` | Every problem the check found in this run, each with its own `message`, `where` and `fix`. The first three fields repeat the first one. |

Without `--json` the same answer is two lines: `Refused: ` and the message, then `For the plugin's maker: ` with the field and the fix.

Act on a refusal in this order: read `fix`; change the field named in `where`; run the check again. The check lists every problem it finds in one run under `problems`; fix them all, then run again until exit 0. Look a message up in [errors.md](errors.md); every common mistake is in [../reference/plugin-refusals.md](../reference/plugin-refusals.md).

## Tell the person

Report these six things, in this order.

1. What was built: the plugin's name, id and version, and the full path of its folder.
2. What it adds: each tool's name and one sentence on what it returns.
3. What it reaches: the exact hosts in `network`, or that it reaches no websites. The keys it asks for, by label, or that it needs none.
4. What it cannot do: it is given only its own folder and its own scratch space to work in, reaches no website it did not list, gets no key it did not declare, holds no wallet, approves nothing, and does not start by itself.
5. The checks: the lines printed by acceptance checks 1 to 3.
6. How to switch it on. In VOOL open **Skills & Plugins**, then **My add-ons**, then **Plugins**. Choose **Install from a folder**, give the folder, and choose **Review folder**. VOOL shows every file and installs the plugin switched off. Then choose **Review access and enable**, read the access sheet, and accept it. Chats use the plugin's tools while **Let chats use plugin tools** is on. A key, when the plugin has a slot for one, is typed into the plugin's card by the owner.

Sharing: give someone the folder, or put it in a public GitHub project; they install it with **Install from public GitHub source**, naming the project, the folder and an exact version (a full commit id). To publish it in the Skill Codex instead, run `python -m tools.community_skills plugin-init FOLDER --namespace NAME --category CATEGORY --subcategory SUBCATEGORY`, then `lint FOLDER --scratch DIR` and `pack FOLDER --scratch DIR --output NAME.vool.json`; the package id must end in the plugin's id and the listing `title` must equal `vool.name`. Publishing needs the person's own account and their yes: stop and ask before any upload. See [../publish.md](../publish.md).

## Stop list

Stop and ask the person. Do not work around the refusal.

1. The plugin needs a key or sign-in. Ask which service and tell the person they will type the key into VOOL themselves. Never ask them to give you the key, and never write a key into a file.
2. The plugin would spend money, call a paid service, sign or approve something. Ask first; a paid service is listed in `vool.paid_services` and VOOL pays it inside limits the owner sets.
3. The person wants VOOL's name, another maker's name, or a word such as Official or Verified in the id, name or publisher.
4. The program needs something installed (a package, a runtime, a download) that is not already inside the plugin folder. Never add an installer or downloader as a command.
5. The program needs a host you cannot name exactly, a local or private address, or a host the person did not ask for.
6. The program needs to read or write the person's files outside its own folder and scratch space.
7. The plugin needs a key and the person uses a Mac: say that a tool server which gets a key runs there only in VOOL's optional isolated runtime, and ask how they want to go on.
8. The same check refuses three times in a row after three different fixes.
9. Any step would touch the person's real VOOL profile, or needs their sign-in.
