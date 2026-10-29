# Text digest example

A complete, working tools plugin to copy. It bundles a small MCP server that computes the SHA-256 fingerprint of
text you give it. Plugins are planned for VOOL 0.8, not released yet.

## What it does

The tool `vool-plugin-template.sha256` returns the SHA-256 digest as colon-separated hexadecimal bytes, plus
the text's length in UTF-8 bytes. Removing the colons gives the usual 64-character digest. The colons keep the
result from looking like a bare private key, so VOOL's key filtering stays on.

## What it reaches

- Network: none.
- Keys: none.
- Files: none. It reads only its own manifest.
- Needs: Python 3, standard library only.

## Files

| File | Purpose |
| --- | --- |
| `.codex-plugin/plugin.json` | The manifest: one tool, handled by the bundled MCP server `text-digest` |
| `.claude-plugin/plugin.json` | Minimal package metadata |
| `.mcp.json` | The same server command, for apps that read `.mcp.json` |
| `bin/server` | The stdio MCP server: `initialize`, `tools/list`, `tools/call` |

## Try the server by hand

From this folder:

```
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"sha256_text","arguments":{"text":"hello"}}}' | bin/server
```

## Make it yours

1. Copy the folder and rename it to your plugin id.
2. Change `name`, `vool.id`, the tool `intent` prefix, `homepage`, `publisher`, both `description` fields
   and both `version` fields, in `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json`.
3. Replace `bin/server` with your code. To use a plain program instead of MCP, see the word-count walk-through
   in [docs/authoring.md](https://github.com/Parad0x-Labs/vool-plugins/blob/main/docs/authoring.md#build-your-first-plugin).
4. Update this README, CHANGELOG.md, `.mcp.json`, and the copyright line in LICENSE.
5. Run the vool-plugins repository's `python3 scripts/check.py --vool-source /path/to/vool <your-folder>`, then `scripts/try.py` from the vool-plugins repository, as shown
   in the guide.
