For AI agents: look up a VOOL refusal by its message or code. Matches VOOL 0.8.0.

# Refusals: message, field, cause, fix

How to use: find the message (or the start of it) in the first column. Do what the last column says. Then run the check again. Full tables, generated from the code:

- [../reference/plugin-refusals.md](../reference/plugin-refusals.md): every common plugin mistake with the exact fix text.
- [../reference/skill-rules.md](../reference/skill-rules.md): every skill text rule, header setting, limit and package detail.
- [../reference/names-kept-back.md](../reference/names-kept-back.md): ids and names that are refused.

Three dots in a message stand for a part that changes with your input.

## Plugins (`python -m tools.plugin_check FOLDER --json`)

The message is in `message`; the field is in `where`; the exact fix is in `fix`. Trust `fix` over this table.

| Message | Field | Cause | Fix |
| --- | --- | --- | --- |
| `This plugin lists the websites it uses in a way VOOL doesn't accept. Ask its maker to fix it.` | `vool.network`, or a tool's `handler.network` | A host is not one exact public host name: a wildcard, an address in numbers, a local name, capitals, `https://`, a path, a port, or listed twice. Or a tool asks for the internet while the list is empty. | Write each host by itself in lower case, once, such as `api.example.com`. |
| `The plugin's setup file is invalid or missing. Review it before installing.` | The field in `where`, often `manifest.vool.tools[0]` | A field VOOL does not know, a needed field left out, a tool name of the wrong shape, or a trust class that does not match its permissions. | Remove the unknown field, add the missing one, name the tool `<plugin id>.<tool>` in lowercase letters, digits and `_`. |
| `This plugin has a setup mistake its maker needs to fix. It was not installed.` | `vool.kind`, `vool.mcpServers...`, a tool's `handler` | The parts do not agree: a server no one declared, tools without `tools` in `vool.kind`, a fixed value or a variable where none is passed, a key no slot declares. | Make the tool, the server and the key slot name each other exactly. |
| `This plugin tries to run something outside its own files, so VOOL won't start it.` | `vool.mcpServers.<name>.command`, a tool's `handler.entry` | The command is an absolute path, climbs out with `..`, or is an installer or downloader. | Ship the program inside the plugin folder and name it by a path inside it, such as `bin/server`. |
| `Plugins can't spend money, use your wallet or approve actions for you.` | `manifest.wallet`, a tool's `trust.side_effect_class` or `intent` | A wallet block, a spending class, or a tool named like a money action. | Remove it. Use `read_only`, `workspace_write`, `network_send` or `network_publish`. Stop and ask the person if money is the point. |
| `Plugins cannot request wallet, payment, signing, approval, cap, budget or receipt authority.` | A tool's `trust.permission_actions` | A permission that belongs to VOOL and the owner. | Remove it. Stop and ask the person. |
| `A plugin may not ask for owner model keys, and it can't change settings for other programs. It was not installed.` | `vool.credentials.<slot>.env` | The key's name is one VOOL keeps back: an owner model key, a `VOOL_` name or a system name. | Pick a name of your own, such as `ACME_API_KEY`. |
| `A plugin can't hold credentials that belong to you or to another plugin.` | `credential_bindings` | The plugin asks for the owner's or another plugin's keys. | Remove the field. Declare the plugin's own slot in `vool.credentials`. |
| `The plugin's name and version must match its package.` | `vool.id / vool.version` | `vool.id` differs from the top-level `name`, or `vool.version` from `version`. | Make each pair equal. |
| `This name belongs to a plugin made by VOOL. Give your plugin a different name to install it.` | `name`, `vool.id` | The id is one VOOL keeps: a shipped pack's id, an id starting with VOOL's or its maker's name, or a name VOOL's own tools start with. | Pick another id. Do not vary the spelling to get past it. |
| `This plugin's name, maker or key label uses VOOL's own name. Only VOOL's own plugins can carry it. Take it out to install it.` | `vool.name`, `vool.publisher`, a key `label` | VOOL's name is in a shown name. | Take it out. Use the person's own name as publisher. |
| `This plugin's name, maker or key label says it was checked or chosen, with a word like Official or Verified, or with a tick or a star. Take that out to install it.` | `vool.name`, `vool.publisher`, a key `label` | A word or mark that claims a check or a choice. | Take it out. |
| `This plugin's name, maker or key label has hidden marks, look-alike letters, links or symbols. Write it in plain letters, numbers and spaces to install it.` | `vool.name`, `vool.publisher`, a key `label` | A link, a symbol, a hidden character or letters of two alphabets in one word. | Retype it in plain letters, numbers and spaces. |
| `That folder could not be read.` | none (exit code 2) | The path is not a folder. | Give the folder that holds `.codex-plugin/plugin.json`. |

## Skills (`python -m tools.community_skills try`, `zip`, `stage`)

The message is in `reason` (JSON) or after `the skill was not installed:` (`try`). For a text rule it names the line and quotes it.

| Message or code | Field | Cause | Fix |
| --- | --- | --- | --- |
| `This skill did not pass VOOL's safety check, so it was not kept.` | The line named after it | One or more text rules matched. The rule's own sentence follows. | Fix each named line by its rule below. |
| Code `install`: `VOOL skills can't carry install commands or ask anyone to run software.` | A line of `SKILL.md` | A command, an install step, a script path. | Describe the step in words. Never keep the command in another spelling. |
| Code `download`: `VOOL skills can't link to programs or archives to download.` | A line of `SKILL.md` | A link to a program or an archive. | Take the link out, or name the website in words. |
| Code `secret-ask`: `VOOL skills can't ask for passwords, keys, recovery phrases or codes.` | A line of `SKILL.md` | The skill asks a person for a secret. | Take the sentence out. Stop and ask the person if the task needs it. |
| Code `consent`: `VOOL skills can't say the person already agreed, or tell VOOL to skip asking.` | A line of `SKILL.md` | The text claims consent or skips asking. | Take the sentence out. |
| Code `injection`: `This reads like an attempt to change VOOL's own instructions or to hide what the skill does.` | A line of `SKILL.md` | Words that override instructions, hide an action, or pose as the app. | Say plainly what the skill should do. |
| `allowed-tools names tools this runtime does not have: ...` | Header, `allowed-tools` | A tool name VOOL does not have. | Use a name from `docs/guides/skill-tools-and-limits.md`, or remove the line. |
| "still holds the starter text from" | `SKILL.md` | A line with REPLACE is left from `new`. | Write your own description, triggers and steps. |
| `VOOL imports instruction-only skills; this zip also carries: ...` | The zip | A file other than `SKILL.md`, `LICENSE` or a README. | Build the zip with the `zip` command. |
| `This skill sets ..., which only VOOL's own skills may set.` | Header | A setting kept for VOOL's own skills, such as `id` or `priority`. | Take those lines out of the header. |
| `a skill never runs by itself. It is used only when a message asks for it.` | Header (in `not_used`, not a refusal) | A timer setting such as `schedule` or `cron`. | Take the line out. Tell the person the skill is called by message. |
| `Names that start with "vool-" are kept for VOOL's own skills, so this skill needs another name.` | Header, `name` | The name starts with `vool-`. | Pick another name. |

## Package details (`python -m tools.community_skills init`, `lint`, `pack`)

`reason` is a short fixed phrase; `fix` says what to write.

| `reason` | Field | Cause | Fix |
| --- | --- | --- | --- |
| `invalid package metadata fields` | `metadata.json` | A field is missing or not known. | `fix` lists which. Needed and optional fields are in [../reference/skill-rules.md](../reference/skill-rules.md). |
| `invalid stable package id` | `id` | Not `publisher/name` in lowercase letters, digits and hyphens. | Write it as in `alice/meeting-notes`. |
| `reserved publisher namespace` | `id` | The publisher part is one VOOL keeps. | Publish under the person's own public name. |
| `category and subcategory must come from the registry category list` | `category`, `subcategory` | A value that is not on the list. | Pick a pair from `core/registry/categories.py` in the checkout. |
