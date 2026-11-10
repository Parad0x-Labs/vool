---
description: For AI agents and developers. Which tool names a skill may put in allowed-tools, what each tool does, what Manual and Auto mode ask for, and the refusals a skill can run into.
---

# Skill tools and limits

This page is for AI agents and developers writing a skill. It answers four questions: which
names may go in `allowed-tools`, what a skill can and cannot make VOOL do, what the person is
asked to approve, and what each refusal means. Writing, testing and sharing a skill is covered in
[Build and test a skill](build-and-test-a-skill.md).

## What a skill can and can never do

| A skill can | A skill can never |
| --- | --- |
| Add instructions to the turns where the person's message picks it. | Grant a permission, approve an action, raise a spending limit or turn a setting on. Its text is never an approval. |
| Narrow the tools VOOL offers on those turns with `allowed-tools` (every name must be a real tool, see the table below). | Add a tool. Naming a tool that is switched off does not switch it on. |
| Ask VOOL to use a tool. The call goes through the same permission check as any other call. | Run by itself. It is loaded only when a person's message picks it: SKILL.md has no timer, schedule or background setting (`lint` and `try` warn about a `schedule`, `interval`, `cron` or `every` key). For something that should be checked over time, the skill can ask for `watch.add` (a wallet or a token price); the person approves it, and a watch checks only while VOOL is open. |
| Write files where the tool allows: the chat's project folder (`workspace.*`) or ~/Desktop, ~/Downloads and ~/Documents (`machine.*`). | Write anywhere else (`I can only write inside this lane: ~/Desktop, ~/Downloads, ~/Documents.`), or touch startup files, credentials, Git metadata or release keys unless the person allows it for that turn. |
| Run a command through the sandbox tools. | Reach the network from a command (`Network egress is disabled by execution policy.`). |
| Search the web once the person has turned web lookups on. | Look anything up on the web before that (`Web lookups are off. ...`). |
| Prepare a payment as a proposal the owner then approves, once the owner has switched the wallet on. | Move money by itself. The wallet is off until the owner switches it on, and every payment waits for the owner's approval in every mode. |
| Set a reminder, by putting "remind me on <date> at <time>" in the `text` of `operator.save_note` (see [Reminders from a skill](#reminders-from-a-skill)). | Set a reminder any other way: there is no reminder tool for skills. |
| Ship as SKILL.md plus an optional LICENSE or README. | Carry scripts, programs or any other file. A zip with one is refused at import. |

## What Manual and Auto mode do with a skill's tool calls

A picked skill changes the instructions, never the permission check. Every tool call is checked
against the chat's mode (`core/mode_permission_policy.py`, `MODE_PERMISSION_MATRIX`):

* **Manual mode** runs reads (reading files, listing folders, safe commands, public web reads
  once web lookups are on). Every other call stops with **Permission needed**:
  `Manual mode requires approval for this exact action. Review the details, then allow or deny this request.`
  Nothing happens until the person clicks **Allow once**.
* **Auto mode** also runs creating and changing files, side-effecting commands, network use and
  connected services. It still asks before deleting, overwriting an existing file, installing
  dependencies, changing provider settings or other settings, Git commit/push/merge/reset,
  deploying, sending messages and anything with money:
  `Auto mode requires approval for this exact action. ...`
* A call whose effect VOOL cannot classify is refused in Auto mode and asked for in Manual mode:
  `Auto mode does not permit unknown side effect. The controller blocked the tool before dispatch.`
  The table below marks those tools *refused* under Auto.

Tools such as `skill.install` and the calendar tools ask in both modes. A skill cannot change any
of this: write the instructions so the answer still makes sense when the person says no.

## Refusals and what they mean

| What the skill (or you) sees | Why | What to do |
| --- | --- | --- |
| `allowed-tools names tools this runtime does not have: workspace.writefile` | A name in `allowed-tools` is not a VOOL tool. `skill.validate`, `try` and `skill.install` all say this. | Use a name from the table below (here `workspace.write_file`). |
| `declared permissions ['read_files'] do not cover what operator.schedule_calendar_event requires: external_messages` | The frontmatter `permissions` list leaves out an action a named tool needs. | Add the missing id, or leave `permissions` out. See [the permissions key](build-and-test-a-skill.md#the-permissions-key). |
| `Manual mode requires approval for this exact action. ...` (or `Auto mode ...`) | The mode asks before this call. | Nothing to fix. The person approves or declines. |
| `Auto mode does not permit unknown side effect. The controller blocked the tool before dispatch.` | Auto mode refuses tools whose effect is not classified (marked *refused* under Auto below). | Expect it. In Manual mode the same call asks instead. |
| `No. Web lookups are off. Turn them on in Settings > Privacy & Permissions > Web lookups. (Command line: web lookup is opt-in; enable with VOOL_ENABLE_WEB=1.)` | Web lookups are off by default. | Say in the skill that it needs web lookups, and add `"search_web"` to `needs` in metadata.json. |
| `... and web access is off. Turn on web access in Settings ...` (for example `Price watches read public trading data online, and web access is off. Turn on web access in Settings to watch a price. Nothing was saved.`) | Tools that read public data online (`token.performance`, `token.flow`, a price `watch.add` and others) use the same switch as web lookups, which is off by default. Nothing was looked up or saved. | Say in the skill that it needs web lookups, and add `"search_web"` to `needs` in metadata.json. |
| `Web fetch is disabled by runtime web policy.` | `web.fetch` is off on this runtime (its reason in the tool list). | Use `web.search` or `web.research`, or say the skill needs web fetch on. |
| `I can only write inside this lane: ~/Desktop, ~/Downloads, ~/Documents.` | `machine.write_file` was given a path outside those folders. | Write inside them, or into the chat's project folder with `workspace.write_file`. |
| `` `machine.write_file` was not executed: it names high-risk local paths -- ... (startup). Credentials, Git metadata, startup files, configuration and release keys need an explicit operator allowance for this turn before any byte of them moves.`` | The path is a startup file (`.bashrc`, `.zshrc`, LaunchAgents and so on), a credential, Git metadata, configuration or a release key. | Do not write there. |
| `Network egress is disabled by execution policy.` | A command run by `sandbox.run_command` tried to reach the network. | Commands get no network. Use the web tools for web content. |
| `... did not run (sandbox_unavailable): Sandbox isolation unavailable: ...` | `workspace.run_tests`, `run_lint` and `run_formatter` need OS isolation (bwrap on Linux, sandbox-exec on macOS). | Nothing a skill can change. |
| `The wallet is switched off on this runtime (VOOL_WALLET_ENABLED).` | The wallet is off until the owner switches it on. | Say in the skill that it needs the wallet, and add `"wallet"` to `needs` in metadata.json. |
| `X is not connected. Add your X keys in Settings > API Keys > X to turn the X tools on.` | The X tools need the person's own X keys. | Say in the skill that it needs X. |
| `the skill was not installed because it does not pass the skill check: ...` | `skill.install` re-runs the validator and lists what it found. | Fix each item, then install again. |

To see these for yourself, run a tool in a throwaway profile with `vool-community call` (see
[Exercise a skill's tool calls](build-and-test-a-skill.md#exercise-a-skills-tool-calls)).

## Reminders from a skill

There is no reminder tool for skills. `operator.save_note` sets one when its `text` asks to save a
note and also says "remind me ...":

```text
save a note called "ACME" saying invoice ACME due 2026-11-10. remind me on 2026-11-08 at 09:00
```

The reply has two parts: the note saved, and the reminder scheduled.

* **The date after "remind me" wins.** Here the reminder is due on 8 November at 09:00, not on
  the due date in the note. When the words after "remind me" name no time, the time is read
  from the whole text.
* **One reminder per call.** With several "remind me" phrases in one `text`, only the first
  one's time is used and only one reminder is set. Call `operator.save_note` once per reminder.
* **What it says.** "remind me on 2026-11-08 at 09:00 to pay the invoice" reads *pay the
  invoice*. With no "to ..." it reads *the note "ACME"*.
* **Time zone.** A time with no zone is read in the person's time zone set in VOOL; a zone
  named in the text (`Europe/London`, `UTC`) is used instead. The due time is stored in UTC and
  shown in that zone, for example *08 Nov 2026 at 09:00:00 EET*.
* **Only the future.** A time that has already passed is refused, and nothing is scheduled.
* **When it shows.** The reminder appears in that chat while VOOL is running; one that came due
  while VOOL was closed appears the next time it runs. `operator.list_reminders` lists them.

## Can a skill with allowed-tools be shared or published?

`allowed-tools` only narrows what VOOL offers, so it is accepted wherever VOOL can check the
names, and refused where it cannot:

| Route | A skill with `allowed-tools` |
| --- | --- |
| Zip share (`import this skill`, `skill.import`) | Accepted. Turning it on runs the same validator as a local install. |
| Local install (`skill.install`, `vool-community try`) | Accepted when every name is a real VOOL tool; otherwise refused with the names it does not know. |
| Import from a GitHub repository (Browse) | Not import-ready: *This skill declares a host-specific tool list that needs a VOOL mapping.* GitHub skills are written for other hosts, so their tool names are never trusted as VOOL names. Share it as a zip instead. |
| Upload to the shared library (Skill Codex) | The automatic checks run VOOL's validator on SKILL.md, so `allowed-tools` with real names passes, the same as `vool-community lint`. What is refused is the package manifest (`.codex-plugin/plugin.json`) asking for tools, permissions or credential bindings, which `vool-community init` never writes. Uploading is not open to every author yet. |

## The tools a skill may name

Every name below may go in `allowed-tools`. The table is generated from VOOL's own tool
contracts; do not edit it by hand. After a tool changes, run
`python -m tools.skill_tool_reference` (a test fails while the page and the contracts differ).
The full contract of a tool, with every field, is in Python:
`core.runtime_tool_contracts.runtime_tool_contract_map()["web.search"]`.

<!-- BEGIN GENERATED TOOL TABLE (python -m tools.skill_tool_reference) -->

145 tools, read from `core.runtime_tool_contracts` in a fresh profile. `?` marks an optional input. **Permission ids** are what a frontmatter `permissions` list must cover when it is declared. **Manual** and **Auto** say what that mode does with a call: *runs*, *asks* (a **Permission needed** prompt; nothing happens until the person clicks **Allow once**) or *refused*. A call can still be refused for its arguments (a path outside the allowed folders, a startup file) or ask where this says *runs* (overwriting an existing file asks in Auto mode).

### chain

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `market.analyze` | Compute patterns from a dataset market.collect saved. | `dataset_id`, `focus?` | reads | `read_files` | runs | runs |
| `market.collect` | Collect a token's trade data into a local dataset that market.analyze and market.thesis_check read: price/volume candles and the most liquid pool's last 300 trades of... | `token`, `chain?`, `window?` | saves VOOL data | `read_files`, `use_network_access` | asks | runs |
| `market.dataset_delete` | Delete one collected market dataset (dataset_id) and its rows from VOOL's local storage. | `dataset_id` | saves VOOL data | `read_files` | runs | runs |
| `market.datasets` | List the market datasets collected in this VOOL: id, token, chain, window, when, row counts per source, and whether coverage is partial. | none | reads | `read_files` | runs | runs |
| `market.thesis_check` | Check a user's thesis about a collected dataset by evaluating claims written with ONLY the metric names market.analyze defines: window_return_pct, daily_return_pct:<UT... | `dataset_id`, `claims`, `window?` | reads | `read_files` | runs | runs |
| `token.flow` | Buy and sell activity for one token right now, from public DEX trading data: its most liquid pools (up to 5, each named with its DEX and pair) and, per window (default... | `token`, `chain?`, `windows?` | reads | `use_network_access` | asks | runs |
| `token.launches` | List the newest tokens created on Solana, newest first, with the user's own Helius key: address, name, symbol and authority. | `limit?`, `authority?`, `creator?` | reads | `read_files`, `use_network_access` | asks | runs |
| `token.performance` | How a token did after a moment: its price then, now, and the highest and lowest after (with when the peak came), from public DEX trading data. | `calls`, `token?`, `at?`, `chain?` | reads | `use_network_access` | asks | runs |
| `token.research` | Research a token from public data with the user's own keys, in one call: its facts (name, symbol, supply), its largest holders and how concentrated they are, who creat... | `token`, `chain?`, `parts?`, `creator?` | reads | `read_files`, `use_network_access` | asks | runs |
| `web.chain_lookup` | Look up public blockchain data with the operator's own chain-data API keys (Helius, Alchemy, Solscan, Etherscan, Moralis): a wallet's native balance, its token balance... | `query`, `chain?`, `address`, `token`, `tx`, `provider?`, `limit?` | reads | `read_files`, `use_network_access` | asks | runs |

### code

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `code.review_evidence` | Summarize the Blackbox-journaled evidence for a coding-assistant engagement: every authorized workspace effect with before/after hashes, verified against the bytes cur... | `turn_id?` | reads | `read_files` | runs | runs |

### code_task

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `code.task.approve` | Approve a previewed proposal; only the byte-identical mutation over the reviewed content may then execute, once. | `task_id`, `proposal_id` | reads | `read_files` | runs | runs |
| `code.task.cancel` | Cancel the task: every later step is refused before any effect. | `task_id`, `reason?` | reads | `read_files` | runs | runs |
| `code.task.identify` | Record the earliest owning defect (path, line, reason) after the failure is reproduced. | `task_id`, `path`, `line?`, `reason?` | reads | `read_files` | runs | runs |
| `code.task.open` | Open a coding task for an objective; returns the task_id and the enforced root-cause plan. | `objective` | reads | `read_files` | runs | runs |
| `code.task.pr_description` | Prepare a reviewable pull-request title and body assembled ONLY from the task journal (defect, diff paths, executed commands and outcomes). | `task_id` | reads | `read_files` | runs | runs |
| `code.task.propose` | Preview a repair (a mutating tool call) without executing it; returns the canonical files, the reviewed base of each (the content this task read or last wrote) and the... | `task_id`, `proposal_id?`, `intent`, `arguments`, `rationale`, `unit?` | reads | `read_files` | runs | runs |
| `code.task.report` | Evidence report assembled only from executed steps: verdict, files changed, git diff paths, commands, tests, receipts, faults, rollback state, refused and unresolved w... | `task_id` | reads | `read_files` | runs | runs |
| `code.task.rollback` | Restore the exact pre-task bytes of every file the task mutated, from the Blackbox journal. | `task_id` | writes files | `overwrite_existing_files`, `delete_files` | asks | asks |
| `code.task.step` | Execute one contracted tool as a step of the task. | `task_id`, `step_id?`, `intent`, `arguments?`, `rerun_reason?` | writes files | `create_files`, `modify_files`, `run_side_effecting_commands` | asks | refused |

### contacts

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `contacts.delete` | Propose deleting one saved contact: target is its contact id or its exact saved name. | `target` | saves VOOL data | `change_settings` | asks | asks |
| `contacts.resolve` | Resolve one saved person or service to ONE exact destination of one kind (email, phone, messaging or wallet), optionally narrowed by label (work, personal), wallet net... | `name`, `kind`, `label?`, `network?`, `chain?`, `channel?`, `account?` | reads | `read_files` | runs | runs |
| `contacts.save` | Save a new contact, or propose adding aliases and entries to a saved one (contact_id). | `name`, `contact_id?`, `create_new?`, `kind?`, `aliases?`, `email?`, `phone?`, `wallet_address?`, `network?`, `messaging_identity?`, `channel?`, `account?`, `label?`, `endpoints?`, `notes?` | saves VOOL data | `create_files` | asks | runs |
| `contacts.search` | Find saved contacts by name, alias or part of an address and show each one's exact saved entries: email addresses, phone numbers, messaging identities with their chann... | `query?`, `contact_id?`, `limit?` | reads | `read_files` | runs | runs |
| `contacts.update` | Propose a change to one saved contact (contact_id, or target = its exact name): rename it, add or remove aliases, edit notes or labels, and add, change or remove an en... | `contact_id?`, `target?`, `name?`, `add_aliases?`, `remove_aliases?`, `notes?`, `email?`, `phone?`, `wallet_address?`, `network?`, `messaging_identity?`, `channel?`, `account?`, `label?`, `add_endpoints?`, `change_endpoints?`, `remove_endpoint_ids?` | saves VOOL data | `create_files` | asks | runs |

### file

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `file.deliver` | Hand the user a downloadable FILE built from text you write: a Word document (.docx), Excel workbook (.xlsx), PowerPoint deck (.pptx), PDF, CSV/TSV, JSON, YAML, XML, H... *Hands the person a file to download; nothing is written to their folders.* | `filename`, `content?`, `format?`, `title?`, `files?` | saves VOOL data | `create_files` | asks | runs |

### machine

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `machine.disk_usage` | Report free and total space for each local drive on this machine. | `drive?` | reads | none | runs | runs |
| `machine.display_inspect` | **Windows only.** Report connected-display resolution and refresh rate from Windows (physical size only if EDID provides it). | none | reads | none | runs | runs |
| `machine.ensure_directory` | Create a directory inside a safe local directory such as Desktop, Downloads, or Documents. *Only inside ~/Desktop, ~/Downloads and ~/Documents.* | `path` | writes files | none | asks | runs |
| `machine.event_log_errors` | **Windows only.** List recent Critical/Error entries from the Windows Event Log (System and Application). | `limit?` | reads | `read_files` | runs | runs |
| `machine.find_file` | Search the safe local folders (Desktop, Downloads, Documents) for FILES whose name matches a query. | `name` | reads | none | runs | runs |
| `machine.find_folder` | Search local drives for folders whose name matches a query. | `name` | reads | none | runs | runs |
| `machine.find_largest` | Measure the largest files and folders on a drive (bounded, read-only) with a delete-safety class per entry; partial sizes are marked, never fabricated. | `drive?`, `top?`, `kind?` | reads | none | runs | runs |
| `machine.host_state` | Report this machine's live state: uptime, battery level and power source, and whether it is a laptop or a desktop. | none | reads | `read_files` | runs | runs |
| `machine.inspect_specs` | Inspect grounded machine hardware and runtime specs for this local host. | none | reads | none | runs | runs |
| `machine.list_directory` | List visible entries inside a safe local directory such as Desktop, Downloads, or Documents. | `path?`, `directories_only?`, `limit?`, `count_only?` | reads | none | runs | runs |
| `machine.list_processes` | List the heaviest running processes on this machine, ranked by memory or by CPU. | `limit?`, `sort?` | reads | none | runs | runs |
| `machine.move_path` | Move or rename a file or folder on this machine. *Refuses protected system locations and asks for a live confirmation from the operating system before moving.* | `source`, `destination` | writes files | none | asks | asks |
| `machine.read_file` | Read a text file inside a safe local directory such as Desktop, Downloads, or Documents. *Only inside ~/Desktop, ~/Downloads and ~/Documents.* | `path`, `start_line?`, `max_lines?`, `verbatim?` | reads | none | runs | runs |
| `machine.write_file` | Write text content to a file inside a safe local directory such as Desktop, Downloads, or Documents. *Only inside ~/Desktop, ~/Downloads and ~/Documents.* | `path`, `content` | writes files | none | asks | runs |

### market

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `watch.add` | Start watching a wallet or a token price and get a notification when something happens. | `kind`, `chain?`, `subject`, `label?`, `rule?` | saves VOOL data | none | asks | refused |
| `watch.list` | Every watch with its label, chain, rule in words, state (active, paused or needs_setup) and when it was last checked. | none | saves VOOL data | none | asks | refused |
| `watch.pause` | Pause one watch (no checks, no notifications) by id or by the address it watches. | `id?`, `subject?` | saves VOOL data | none | asks | refused |
| `watch.remove` | Stop watching: delete one watch by id or by the address it watches. | `id?`, `subject?` | saves VOOL data | none | asks | refused |
| `watch.resume` | Resume a paused watch, or re-arm a watch that stopped because its requirement was missing (its key removed, web access off). | `id?`, `subject?` | saves VOOL data | none | asks | refused |

### marketplace

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `marketplace.purchase_knowledge` | Buy access to a listed shard for the local node, burning the price from its compute-credit balance. | `shard_id`, `receipt_id?` | spends credits | none | asks | asks |
| `marketplace.search_listings` | Search the knowledge marketplace for shards by keyword or domain tag. | `query?`, `domain_tag?`, `max_results?` | reads | none | asks | runs |
| `sell.quote` | Quote a price for a listed capability. | `resource`, `null_name?` | reads | `read_files` | runs | runs |

### media

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `media.edit` | Apply ONE typed edit operation to a media project: trim \| delete_range \| split \| crop \| resize \| set_aspect_ratio \| rotate \| set_volume \| normalize_audio. | `project_id`, `kind`, `params`, `use_selection_range?` | writes files | none | asks | refused |
| `media.export` | Export the edited media project to a NEW output file via the isolated nebula-media worker. | `project_id`, `output_path`, `profile?` | writes files | none | asks | refused |
| `media.inspect` | Inspect the current state of an open media project (duration, dimensions, edit revision, operations, working range). | `project_id` | reads | `read_files` | runs | runs |
| `media.open` | Open a local media file as a non-destructive MediaEditProject for the Media Studio editor. | `source_path` | writes files | none | asks | refused |
| `media.redo` | Redo a previously undone edit operation on a media project. | `project_id` | writes files | none | asks | refused |
| `media.undo` | Undo the last edit operation on a media project. | `project_id` | writes files | none | asks | refused |

### operator

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `operator.apple_note_list` | **macOS only.** List the Apple Notes notes of a named folder or account; a report, never a mutation. | `folder?`, `account?` | reads | `read_files` | runs | runs |
| `operator.apple_note_read` | **macOS only.** Read one Apple Notes note by title, narrowed by the folder or account the request names; reports the note's body without changing anything. | `title`, `folder?`, `account?` | reads | `read_files` | runs | runs |
| `operator.cancel_reminder` | Cancel one of this runtime's own pending reminders from the request as written. | `text` | saves VOOL data | `delete_files` | asks | asks |
| `operator.check_availability` | List a day's events on the configured calendar provider and compute free slots for a requested duration. *Takes the request as written in `text` ("am I free Tuesday afternoon?"). Needs a calendar account in Settings. With no morning/afternoon/evening word it checks the whole day from 00:00.* | `text` | reads | `use_network_access` | asks | runs |
| `operator.cleanup_temp_files` | DELETE temporary files from this machine. | `target_path?` | writes files | `delete_files` | asks | asks |
| `operator.find_notes` | Search the active workspace's notes by text; reports matches without inventing content. *The notes operator.save_note wrote in the chat's project folder, not other apps' notes.* | `query` | reads | `read_files` | runs | runs |
| `operator.inspect_calendar_event` | Show one provider event's stored time, title, identity and version; event text is data, never executed. *Needs a calendar account in Settings.* | `label?` | reads | `use_network_access` | asks | runs |
| `operator.inspect_disk_usage` | Report disk usage per mounted volume. | `target_path?` | reads | none | runs | runs |
| `operator.inspect_processes` | List running processes on this machine. | none | reads | none | runs | runs |
| `operator.inspect_services` | List service units and their state on this machine. | none | reads | none | runs | runs |
| `operator.list_calendars` | List the calendar collections the configured provider serves. *Needs a calendar account in Settings.* | none | reads | `use_network_access` | asks | runs |
| `operator.list_reminders` | List this runtime's own reminder rows; a report, never a mutation. *Only VOOL's own reminders in this chat, not reminders in other apps.* | none | reads | `read_files` | runs | runs |
| `operator.list_tools` | List the operator tools this runtime exposes. | none | reads | none | runs | runs |
| `operator.move_path` | Move or rename a path on this machine. | `source_path`, `destination_path` | writes files | `move_files` | asks | runs |
| `operator.propose_calendar_event` | Propose a real event on the configured calendar provider; executes only after explicit approval with conflict and version re-checks. *Takes the request as written in `text`. Put the title in quotes (propose "Project review" on Tuesday at 15:00 for 30 minutes), or it asks "What should it be called?". Needs a calendar account in Settings.* | `text` | publishes | `access_external_providers` | asks | runs |
| `operator.save_note` | Save a note as a real file in the active workspace's notes directory, optionally linked to a provider event receipt. *Takes the request as written in `text` (save a note called "Tea" saying buy tea). Writes a Markdown file into the chat's project folder, under notes/. Adding "remind me on 2026-11-08 at 09:00" to the same `text` also sets one reminder (see Reminders from a skill).* | `text` | writes files | `create_files` | asks | runs |
| `operator.schedule_calendar_event` | Create a calendar event on this machine's calendar. *Writes one .ics file into VOOL's calendar outbox. It does not add the event to any calendar app.* | `title`, `start_iso`, `duration_minutes?` | writes files | `external_messages` | asks | asks |
| `operator.schedule_reminder` | Schedule one of this runtime's own reminders from the request as written. | `text` | saves VOOL data | `create_files` | asks | runs |
| `operator.search_calendar_event` | Search the configured provider's events by text; reports matches without inventing content. *Needs a calendar account in Settings.* | `query` | reads | `use_network_access` | asks | runs |
| `operator.show_agenda` | Show the calendar agenda over a named window; a report of what is scheduled, never a change to it. *Needs a calendar account in Settings. `window` is the words of the request ("tomorrow", "this week").* | `window?` | reads | `use_network_access` | asks | runs |
| `operator.show_note` | Display one workspace note by title, with its session and linked event receipt. *The notes operator.save_note wrote in the chat's project folder, not other apps' notes.* | `title` | reads | `read_files` | runs | runs |
| `operator.update_calendar_event` | Stage a provider event update (time and/or title) protected by the version (etag) the user last saw. *Takes the request as written in `text`. Stages a change; it applies only after the person approves. Needs a calendar account in Settings.* | `text` | publishes | `access_external_providers` | asks | runs |

### pdf

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `pdf.extract_text` | **macOS only.** Read the text a PDF already carries, page by page. | `path`, `max_pages?` | reads | `read_files` | runs | runs |
| `pdf.ocr` | **macOS only.** Recognise the text on a SCANNED PDF page, on-device. | `path`, `max_pages?`, `scale?` | reads | `read_files` | runs | runs |

### profile

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `profile.forget` | Forget one category of the operator's profile (or everything with category '*'). *The person's own profile in VOOL.* | `category` | saves VOOL data | none | asks | refused |
| `profile.list` | List what VOOL remembers about the operator: each item's category, value, scope, origin and last use. | none | reads | `read_files` | runs | runs |
| `profile.remember` | Save one fact about the operator to their Operator Profile: preferred_name, language, locale, timezone, response_style, format_preference, email_signature, default_acc... *The person's own profile in VOOL (name, language, time zone and so on).* | `category`, `value`, `scope?`, `explicit?` | saves VOOL data | none | asks | refused |

### repo_ops

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `repo.bind` | Freeze the exact SHAs this session operates on: remote head, local head, PR base and PR head. | `repo_session_id`, `allow_dirty?` | reads | `read_files` | runs | runs |
| `repo.ci.artifacts` | List the CI artifacts the forge holds for the bound head SHA. | `repo_session_id` | reads | `access_external_providers` | asks | runs |
| `repo.ci.cancel` | Ask the forge to cancel one running CI job. | `repo_session_id`, `job_id` | sends out | `access_external_providers` | asks | runs |
| `repo.ci.jobs` | List the CI jobs the forge reports FOR THE BOUND HEAD SHA -- never for a branch name. | `repo_session_id` | reads | `access_external_providers` | asks | runs |
| `repo.ci.log` | Retrieve one CI job's log from the forge. | `repo_session_id`, `job_id` | reads | `access_external_providers` | asks | runs |
| `repo.ci.rerun` | Ask the forge to rerun one CI job. | `repo_session_id`, `job_id` | sends out | `access_external_providers` | asks | runs |
| `repo.diagnose` | Record the owning cause: the path, the line and the reason, cited to a CI job or a local command this session actually ran. | `repo_session_id`, `path`, `line?`, `reason`, `evidence_step_id?` | reads | `read_files` | runs | runs |
| `repo.diff` | Retrieve the diff for the bound pull request, from the forge, at the bound head SHA. | `repo_session_id` | reads | `access_external_providers` | asks | runs |
| `repo.git` | One typed local git operation: branch, commit, cherry_pick, revert, merge, tag, or restore. | `repo_session_id`, `operation`, `name?`, `commit?`, `onto?`, `message?`, `delete?`, `step_id?` | writes files | `git_commit`, `git_merge_or_rebase` | asks | asks |
| `repo.inspect` | Inspect the bound repository: resolved root, remote URL, current branch, HEAD, whether the working tree is dirty, and the pull request's state when one is named. | `repo_session_id` | reads | `read_files` | runs | runs |
| `repo.issue` | Read one issue of the bound repository's forge: title, state, author, labels, body. | `repo_session_id`, `number` | reads | `access_external_providers` | asks | runs |
| `repo.issue.comments` | Read the comments of one issue. | `repo_session_id`, `number` | reads | `access_external_providers` | asks | runs |
| `repo.pr.comment` | Post the authorized exact comment on one pull request or issue. | `repo_session_id` | sends out | `access_external_providers` | asks | runs |
| `repo.pr.create` | Open the authorized DRAFT pull request through the forge adapter. | `repo_session_id` | sends out | `access_external_providers` | asks | runs |
| `repo.pr.request` | Build the exact forge-action plan -- a DRAFT pull request (title/body/head/base at the current remote head SHA), a title/body update to one open pull request, or an ex... | `repo_session_id`, `action`, `title?`, `body?`, `task_id?`, `head_ref?`, `base_ref?`, `number?`, `subject?` | reads | `access_external_providers` | asks | runs |
| `repo.pr.update` | Replace the authorized title/body of one open pull request. | `repo_session_id` | sends out | `access_external_providers` | asks | runs |
| `repo.push` | Execute the authorized push. | `repo_session_id`, `simulate?`, `force?` | publishes | `git_push` | asks | asks |
| `repo.push.authorize` | The operator's explicit Authorize Push, bound to one plan hash. | `repo_session_id`, `plan_hash` | changes VOOL's setup | `change_settings` | asks | asks |
| `repo.push.request` | Produce the exact push plan -- remote, ref, the local SHA that would move it, the remote SHA it would move FROM, and a plan hash. | `repo_session_id`, `remote?`, `ref?`, `force?` | reads | `read_files` | runs | runs |
| `repo.receipt` | The sealed receipt: assembled only from journaled, executed steps -- the binding, the diagnosis, the repairs, the tests, the review, the authorization, the push outcom... | `repo_session_id` | reads | `read_files` | runs | runs |
| `repo.review` | Record the review verdict for the repaired work. | `repo_session_id`, `verdict`, `notes?` | reads | `read_files` | runs | runs |
| `repo.session.open` | Open a RepoOps session against a repository (and optionally a pull request). | `objective`, `provider?`, `namespace?`, `pull_request?`, `remote?`, `cwd?`, `reviewer_model?`, `auth_binding?` | reads | `read_files` | runs | runs |
| `repo.step` | Execute one contracted runtime tool as a step of this session -- a read, a command, a test run, or an approved repair. | `repo_session_id`, `step_id?`, `intent`, `arguments?` | writes files | `modify_files`, `run_side_effecting_commands` | asks | runs |
| `repo.verify_remote` | Ask the forge what the pushed ref resolves to NOW, and compare it to what was pushed. | `repo_session_id` | reads | `access_external_providers` | asks | runs |

### sandbox

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `sandbox.run_command` | Run one bounded shell command inside the active workspace with network blocked. *Runs with no network, inside a sandbox; refused where the sandbox is unavailable.* | `command`, `cwd?` | runs a command | none | asks | refused |

### set

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `set.save` | Save or update a named creative set that locks a character's look, wardrobe, and voice plus the setting, style, palette, and story, so continuing videos and images sta... | `name`, `description?`, `characters?`, `character?`, `appearance?`, `wardrobe?`, `voice?`, `setting?`, `background?`, `style?`, `palette?`, `aspect?`, `mood?`, `story?`, `negative?` | saves VOOL data | none | asks | asks |
| `set.use` | Compose an on-model generation prompt for one video or image shot from a saved creative set plus a short script for this shot, re-injecting the set's exact character a... | `name`, `script`, `medium?`, `camera?` | reads | `read_files` | runs | runs |

### skill

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `skill.create` | Write a new skill (name, description, instructions) to the staging area. | `name`, `description`, `body`, `allowed_tools?`, `triggers?`, `overwrite?`, `scope?` | writes files | `create_files` | asks | runs |
| `skill.export` | Package a skill you made (or a plugin you made, kind=plugin) as one zip that anyone can import into their VOOL. | `name`, `kind?`, `scope?` | writes files | `read_files`, `create_files` | asks | runs |
| `skill.import` | Read a skill or plugin zip (a chat attachment or a local path), check it, and stage it for this chat, this project or all of VOOL. | `attachment_id?`, `path?`, `scope?` | writes files | `read_files`, `create_files` | asks | runs |
| `skill.inspect` | Show one native skill's typed contract - id, version, risk class, task families, permitted tools, verification and stopping conditions - plus its live state (enabled,... | `id` | reads | `read_files` | runs | runs |
| `skill.install` | Activate a validated skill by moving it into the plugins tree the loader scans. | `path`, `plugin_id?`, `overwrite?`, `scope?`, `sha256?`, `kind?` | changes VOOL's setup | `create_files`, `change_settings` | asks | asks |
| `skill.list` | List every skill this runtime can see: the bound workspace's SKILL.md files, the first-party native packages the runtime ships, staged drafts, and installed (active) s... | `workspace?` | reads | `read_files` | runs | runs |
| `skill.pack` | Bundle skills you made into a named plugin pack (instruction-only), staged for review; activate it with skill.install kind=plugin, share it with skill.export. | `name`, `description`, `skills` | writes files | `create_files` | asks | runs |
| `skill.remove` | Turn off a skill you made by moving it out of the active tree of its scope. | `name`, `scope?` | changes VOOL's setup | `modify_files`, `change_settings` | asks | asks |
| `skill.rollback` | Restore a prior recorded version of an installed skill, byte for byte. | `skill`, `version` | changes VOOL's setup | `create_files`, `change_settings` | asks | asks |
| `skill.validate` | Parse a skill with the real loader and report what would stop it working - an empty description can never rank, and an unknown tool narrows the turn to nothing. | `path` | reads | `read_files` | runs | runs |

### web

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `web.research` | Run a bounded multi-source web research pass for a question. *Off until the person turns on web lookups (Settings > Privacy & Permissions > Web lookups).* | `query`, `max_hits?`, `max_pages?` | reads | none | runs | runs |
| `web.search` | Search the public web for pages matching a query. *Off until the person turns on web lookups (Settings > Privacy & Permissions > Web lookups).* | `query`, `limit?` | reads | none | runs | runs |

### web0

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `web0.add_block` | Append a content block (heading/text/quote/callout/image/divider) to a page in the builder project. | `project_id`, `page_id?`, `block`, `position?` | saves a draft | none | asks | runs |
| `web0.add_gated_section` | Encrypt a section behind a wallet whitelist and insert it as a gated block (AES-GCM; only the ciphertext ships). | `project_id`, `page_id?`, `content`, `whitelist`, `mode?`, `label?` | saves a draft | none | asks | runs |
| `web0.compile_preview` | Compile the builder project to a self-contained HTML preview without publishing. | `project_id` | saves a draft | none | asks | runs |
| `web0.create_project` | Start a local web0.null builder project from a template and target .null domain. | `template_id`, `domain`, `project_name?` | saves a draft | none | asks | runs |
| `web0.encrypt_whole_site` | Encrypt every text-bearing block on every page behind one wallet whitelist; only ciphertext remains in the project. | `project_id`, `whitelist`, `mode?`, `label?` | saves a draft | none | asks | runs |
| `web0.fill_slots` | Fill named content slots (title, tagline, sections) on a local web0.null builder project. | `project_id`, `slots` | saves a draft | none | asks | runs |
| `web0.open_builder_draft` | Create a local web0.null builder URL from a title and self-contained code draft. | `title`, `code`, `domain?` | saves a draft | none | asks | runs |
| `web0.publish` | Publish the builder project to Arweave. | `project_id`, `allow_network_publish`, `vool_wallet` | publishes | none | asks | asks |

### workspace

| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |
| --- | --- | --- | --- | --- | --- | --- |
| `workspace.apply_unified_diff` | Apply a unified diff patch inside the active workspace and track rollback metadata. | `patch` | writes files | none | asks | runs |
| `workspace.ensure_directory` | Create a directory inside the active workspace. | `path` | writes files | none | asks | runs |
| `workspace.git_diff` | Show git diff output for the active workspace or a target path. | `cwd?`, `path?`, `cached?` | reads | none | runs | runs |
| `workspace.git_status` | Report git status for the active workspace or a subdirectory. | `cwd?` | reads | none | runs | runs |
| `workspace.git_summary` | Summarize grounded git branch counts, current HEAD, and local day-bucketed commit activity for the active workspace. | `cwd?`, `recent_limit?` | reads | none | runs | runs |
| `workspace.identity` | Report WHERE the active workspace is — its full path, project name, whether it is a git repository, and how many files and folders sit at its top level. | none | reads | `read_files`, `list_directories` | runs | runs |
| `workspace.list_files` | List files or directories inside the active workspace. | `path?`, `glob?`, `limit?` | reads | none | runs | runs |
| `workspace.list_tree` | List the visible file and directory tree inside the active workspace. | `path?`, `limit?` | reads | none | runs | runs |
| `workspace.read_file` | Read a workspace file with line numbers. | `path`, `start_line?`, `max_lines?`, `verbatim?` | reads | none | runs | runs |
| `workspace.replace_in_file` | Replace text inside a workspace file. | `path`, `old_text`, `new_text`, `replace_all?`, `expected_hash?` | writes files | none | asks | runs |
| `workspace.rollback_last_change` | Rollback the last VOOL-tracked workspace mutation in the active session. | none | writes files | none | asks | asks |
| `workspace.run_formatter` | Run the workspace formatter in check mode by default or apply formatting when explicitly requested. *Runs inside a sandbox with no network; refused where the sandbox is unavailable.* | `command?`, `cwd?`, `apply?` | runs a command | none | asks | runs |
| `workspace.run_lint` | Run the workspace linter or one bounded lint command. *Runs inside a sandbox with no network; refused where the sandbox is unavailable.* | `command?`, `cwd?` | runs a command | none | asks | runs |
| `workspace.run_tests` | Run the workspace test suite or one bounded test command. *Runs inside a sandbox with no network; refused where the sandbox is unavailable.* | `command?`, `cwd?` | runs a command | none | asks | runs |
| `workspace.search_text` | Search text inside workspace files and return file/line matches. | `query`, `path?`, `glob?`, `limit?`, `scan_limit?` | reads | none | runs | runs |
| `workspace.symbol_search` | Search likely symbol definitions and references in workspace files. | `symbol`, `path?`, `glob?`, `limit?`, `scan_limit?` | reads | none | runs | runs |
| `workspace.write_file` | Write full text content to a workspace file. | `path`, `content`, `expected_hash?` | writes files | none | asks | runs |

### Left out of the table

These tools exist, and a skill may name them in `allowed-tools` (the validator accepts every name below), but on a fresh install they are off until the person sets something up.

**Browser rendering is disabled by runtime browser policy.**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `browser.render` | Render a public page with the configured browser engine when browser tools are enabled. | `url`, `timeout_ms?`, `max_scroll?` | reads |

**Demo planning needs web access to read the repo README or docs page (enable web fallback).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `demo.plan` | Read a GitHub repo README or a docs/website page and return either a timed subtitled shot list plus image prompts (format=slideshow, default) or a continuous product-p... | `url`, `presenter?`, `seconds?`, `format?`, `set?`, `edits?` | reads |

**Email drafts are part of sending, which is disabled on this runtime (set email.send_enabled).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `email.draft.approve` | Record the user's explicit approval of the CURRENT draft content (binds the exact content hash and version). | `draft_id?`, `expected_version?` | reads |
| `email.draft.get` | Show the session's current draft (latest, or by id): content, version, and whether an approval is bound to exactly this content. | `draft_id?` | reads |
| `email.draft.save` | Create or edit a reviewable email draft (recipient/account/subject/body + threading). | `to`, `subject`, `body`, `account?`, `in_reply_to?`, `references?`, `draft_id?`, `kind?` | reads |

**Email open is disabled on this runtime (set email.read_enabled and store IMAP credentials).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `email.open` | Open one message by its Message-ID with FULL content (whole body, decoded headers, threading headers), or its whole thread (thread=true): the transitive In-Reply-To/Re... | `message_id`, `thread?`, `account?`, `folder?` | reads |

**Email read is disabled on this runtime (set email.read_enabled and store IMAP credentials).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `email.read` | Read or search messages in a configured inbox: recent by default, or filtered by sender/subject substrings, a date window (since/before, YYYY-MM-DD) and unread state. | `account?`, `folder?`, `limit?`, `sender?`, `subject?`, `since?`, `before?`, `unseen_only?` | reads |

**Email reconciliation is disabled on this runtime (set email.read_enabled and store IMAP credentials).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `email.draft.reconcile` | Resolve a delivery_unknown draft: check the account's own Sent folder for OUR generated Message-ID. | `draft_id` | reads |

**Email reply is disabled on this runtime (set email.send_enabled and store SMTP credentials).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `email.reply` | Reply to an email (threaded, 'Re:' subject, your signature appended). | `to`, `subject`, `body`, `in_reply_to?`, `references?`, `account?`, `allow_send`, `approve` | sends out |

**Email send is disabled on this runtime (set email.send_enabled and store SMTP credentials).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `email.draft.send` | Send an APPROVED draft (threaded if in_reply_to was set, signature appended). | `draft_id?` | sends out |
| `email.send` | Send an email (subject + body) from a locally-configured account. | `to`, `subject`, `body`, `account?`, `allow_send`, `approve` | sends out |

**Image generation is disabled on this runtime (set image.generate_enabled and configure an image service).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `image.generate` | Generate an image from a text prompt via a configured image service. | `prompt`, `account?` | makes media |

**The browser lane needs a Chromium-family browser and the Playwright driver, enabled by runtime browser policy.**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `vool-browser.assert` | Assert bounded page facts (url/title/text contains, element present, element count) with a pass/fail verdict per check. | `session`, `checks` | reads |
| `vool-browser.cancel` | Cancel a session: stop the engine process so in-flight work returns typed cancelled; delete the disposable profile. | `session` | writes files |
| `vool-browser.checkout.begin` | Begin a checkout from a CONFIRMED profile: fills contact/address fields only; card fields are never touched. | `session`, `profile_name`, `checkout_url?` | writes files |
| `vool-browser.checkout.handoff` | Hand the payment to the OPERATOR: detects the merchant's wallet methods (Apple Pay / Google Pay), requires a VISIBLE session for foreground authentication, never click... | `session` | writes files |
| `vool-browser.click` | Click an element on the session's page by CSS selector, bounded by timeout. | `session`, `selector`, `timeout_seconds?` | reads |
| `vool-browser.download` | Download a bounded file from a granted origin into the session directory; oversize downloads are deleted and refused, never kept. | `session`, `url`, `max_bytes?`, `timeout_seconds?` | writes files |
| `vool-browser.inspect` | Read a session's current page as UNTRUSTED evidence (text, selector text, or outline). | `session`, `mode?`, `selector?` | reads |
| `vool-browser.navigate` | Navigate a session to an http(s) URL with a bounded timeout and redirect control: cross-origin redirects refuse unless the target origin holds a navigation grant. | `session`, `url`, `timeout_seconds?`, `redirect_policy?` | reads |
| `vool-browser.offer.compare` | Compare 2..10 extracted offers on EXACT totals (price+shipping) with returns as tie-break; refuses mangled records instead of normalising them. | `offers` | reads |
| `vool-browser.offer.extract` | Extract an exact structured offer (SKU, merchant, price, shipping, returns) from the session's current page into a comparable record. | `session`, `mapping?` | reads |
| `vool-browser.order.reconcile` | Idempotent order reconciliation against the MERCHANT's order-status endpoint: pending until the merchant confirms; amount must match exactly; one terminal receipt per... | `session`, `order_id`, `expected_total_minor`, `idempotency_key`, `status_url?` | writes files |
| `vool-browser.permission.grant` | Grant one permission (navigation, downloads, uploads, new_tabs) to one origin for one session. | `session`, `origin`, `permission` | reads |
| `vool-browser.permission.list` | List a session's per-origin grants. | `session` | reads |
| `vool-browser.profile.confirm` | Record the operator's CONFIRMED checkout profile (contact + address). | `profile_name`, `contact`, `address`, `operator_confirmed` | writes files |
| `vool-browser.screenshot` | Capture a bounded screenshot of the session's page into the session directory, with byte size and sha256 on the receipt. | `session`, `full_page?`, `max_bytes?` | writes files |
| `vool-browser.session.close` | Close a browser session: stop the engine, delete the disposable profile, keep the receipts. | `session` | writes files |
| `vool-browser.session.open` | Open an isolated browser session: a disposable scratch profile, mock keychain, per-origin deny-by-default permissions and an op/download budget. | `session`, `start_url`, `headless?`, `op_budget?`, `download_budget_bytes?` | writes files |
| `vool-browser.session.status` | Report a browser session's registry truth (live, stale after a restart, cancelled) without spending its budget. | `session` | reads |
| `vool-browser.type` | Type text into a page field by CSS selector. | `session`, `selector`, `text`, `secret?`, `submit?` | reads |
| `vool-browser.upload` | Attach previously staged files into a page file input by CSS selector. | `session`, `selector`, `staged` | writes files |
| `vool-browser.upload.stage` | Stage bounded content for upload into the session's staging area under a bare name. | `session`, `name`, `content_b64`, `max_bytes?` | writes files |

**The wallet is switched off on this runtime (VOOL_WALLET_ENABLED).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `ai.paid_ask` | Ask a pay-per-answer AI endpoint the owner added in Settings (Crypto, Pay-per-answer AI) one question, paid in USDC on Base with x402. | `question`, `endpoint?` | saves VOOL data |
| `pay.x402` | Settle an x402 payment. | `resource`, `allow_spend?`, `approve?`, `max_spend_usdc?`, `null_name?`, `privacy_path?` | spends money |
| `wallet.payment_status` | Report the state of a payment proposal (pending approval, confirmed with its transaction signature, rejected, failed) from the wallet's own records. | `proposal_id` | reads |
| `wallet.propose` | Propose a payment for the owner to approve: destination address, the amount as a decimal string in the coin's units (or amount_minor in atomic units), the asset symbol... | `destination`, `amount?`, `amount_minor?`, `asset`, `chain?`, `network?`, `wallet_id?`, `memo?`, `idempotency_key?` | saves VOOL data |
| `wallet.simulate` | Re-run the simulation for an existing payment proposal and report the fee and outcome. | `proposal_id` | reads |
| `wallet.status` | Read the wallet's public-safe status: custody mode, network, public key, spending limits, pending approvals and the last receipt. | none | reads |
| `x402.propose` | Turn an x402 payment-required challenge (v2 first, v1 compatibility read where safe) into a payment proposal for the owner. | `status`, `headers?`, `body?`, `resource_url?` | saves VOOL data |

**Video generation is disabled on this runtime (set video.generate_enabled and configure a video service).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `video.generate` | Generate a video from a text/scene prompt via a configured video service (RunPod/Replicate/Fal or any configured provider). | `prompt`, `account?` | makes media |

**Wallet spend is disabled on this runtime (set wallet.spend_enabled, configure a capped SpendPolicy, and wire a signer).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `wallet.spend` | Spend from the agent wallet under the user's SpendPolicy caps, with per-spend confirmation. | `to`, `amount_lamports`, `asset?`, `allow_spend`, `approve` | spends money |

**Web fetch is disabled by runtime web policy.**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `web.fetch` | Fetch a public web page over HTTP(S) and return bounded text plus the fetch status. | `url`, `timeout_s?` | reads |

**X is not connected. Add your X keys in Settings > API Keys > X to turn the X tools on.**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `x.account` | About the connected X account: kind=muted or blocked (who it muted or blocked), kind=trends (trends X picks for this account), kind=x_usage (X's own count of this app'... | `kind`, `max_results?`, `next_token?` | reads |
| `x.action.propose` | Propose an action on X as the user: post, reply, quote, delete (one of the user's own posts), like, unlike, repost, unrepost, follow, unfollow, hide_reply/unhide_reply... | `kind`, `text`, `post`, `username`, `list_id`, `name`, `description?`, `private?` | saves VOOL data |
| `x.action.status` | Check a proposed X action: waiting for the user's approval, declined, sent (with the post link) or failed. | `action_id?` | reads |
| `x.dms` | The connected user's own recent direct messages on X, or the conversation with one account (with_user=username). | `with_user?`, `max_results?`, `next_token?` | reads |
| `x.follows` | List an account's followers (kind=followers) or the accounts it follows (kind=following). | `username`, `kind?`, `max_results?`, `next_token?` | reads |
| `x.lists` | X lists: an account's lists (kind=owned\|memberships\|followed, with username), or one list's details, posts, members or followers (kind=details\|posts\|members\|followers,... | `username?`, `list_id?`, `kind?`, `max_results?`, `next_token?` | reads |
| `x.post` | Read X posts by link or id (kind=post), or who liked (liking_users), reposted (reposted_by), quoted (quotes) or replied to (replies) a post. *Reads only. Posting, replying and every other action on X goes through x.action.propose, which the person approves.* | `post`, `kind?`, `max_results?`, `next_token?` | reads |
| `x.search` | Search X posts. | `token?`, `query?`, `mode?`, `sort?`, `granularity?`, `archive?`, `include_reposts?`, `start_time?`, `end_time?`, `max_results?`, `next_token?` | reads |
| `x.spaces` | X Spaces (live audio rooms): search by words (query, state=live\|scheduled\|all), look up by id or link (ids), or the Spaces some accounts host (hosts=usernames). | `query?`, `ids?`, `hosts?`, `state?`, `max_results?` | reads |
| `x.token_callers` | Find who called a token on X: every account that posted its address, $CASHTAG or name, ordered by who posted first, with followers, account age, number of posts and en... | `token`, `max_results?`, `history_for?`, `history_posts?`, `min_followers?`, `evaluate_calls?`, `archive?`, `include_reposts?`, `start_time?`, `end_time?` | reads |
| `x.usage` | How much of the X API the user's keys used this month (requests, posts and accounts read, actions sent) against the monthly limits the user set. | none | reads |
| `x.user` | Look up X accounts by username, @handle or profile link (or 'me' for the connected account): name, bio, followers, following, post count, account age, verified. | `usernames`, `search?`, `max_results?`, `next_token?` | reads |
| `x.user_posts` | Read an account's posts (kind=posts), mentions of it (kind=mentions), posts it liked (kind=likes), or the connected user's home timeline (kind=home). | `username`, `kind?`, `exclude?`, `start_time?`, `end_time?`, `max_results?`, `next_token?` | reads |

**X trending is disabled on this runtime (set x.trending_enabled and configure a bearer token).**

| Tool | What it does | Inputs | Reads or changes |
| --- | --- | --- | --- |
| `x.trending` | Fetch trending topics from X for a location (read-only). | `account?`, `woeid?`, `limit?` | reads |

Not for skills:

* `capability.expand_family`: VOOL's own way of widening the tool offer mid-turn.
* `orchestration.execute_envelope`: VOOL's own sub-task runner, used by VOOL's planner rather than by a skill.
* `respond.direct`: the plain answer itself; it is always offered, so naming it changes nothing.
* `repo.pr.authorize`: owner-only; never offered to a model, so naming it in a skill does nothing.

<!-- END GENERATED TOOL TABLE -->
