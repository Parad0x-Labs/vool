---
description: Start here to build a VOOL skill or plugin. What each one is, which one you need, and the shortest path to a working one.
---

# Build for VOOL: start here

Skills work in VOOL 0.7.7 and later; the author commands `new`, `try`, `zip`, `stage` and `call`
came after 0.7.7 and are in 0.8. Plugins need VOOL 0.8.0 or newer.

If you are an AI agent, read [agents/README.md](agents/README.md) instead.

## What they are

**A skill** is text. It tells VOOL how to do a task you do again and again.
It runs no code, holds no keys and is used only when a message asks for that task.

**A plugin** is code from someone else. It adds a tool, a model service or a scanner to VOOL, or it is a browser plugin: a browser extension that runs in the VOOL browser window.
It runs walled off, with only the websites and keys it listed and the owner agreed to, and it is off until the owner switches it on.

## Which one do I need?

| I want VOOL to ... | Build a |
| --- | --- |
| Turn meeting notes into the same kind of brief every time | Skill |
| Sort, rewrite or check text the way I like it | Skill |
| Follow my checklist using tools VOOL already has (notes, files, email, web search) | Skill |
| Answer in my team's house style | Skill |
| Look something up in a service VOOL does not know (a parcel, a ticket, a stock level) | Plugin |
| Run my own program on text or a file and use the result | Plugin |
| Offer chat models from another model service | Plugin |
| Change how web pages look or work in the VOOL browser window (block adverts, tidy a site) | Plugin (browser): see [Browser plugins](../guides/plugin-authoring.md#browser-plugins) |
| Do something every morning by itself | Neither starts by itself. A skill can set a reminder: see [the FAQ](faq.md#can-my-skill-run-every-morning) |

Rule of thumb: if words are enough, write a skill. If it needs new code or a new service, build a plugin.

## Shortest path: a skill

No files needed: in a VOOL chat say *make me a skill that ...*, click **Allow once**, then say *turn it on*.
To write the file yourself, from a VOOL source checkout (`export VOOL_SRC=/full/path/to/vool`):

1. Make a starter:
   `PYTHONPATH="$VOOL_SRC" python -m tools.community_skills new aisle-sorter --name aisle-sorter --author "Your Name"`
2. Open `aisle-sorter/SKILL.md` and replace every line that says REPLACE with your description, trigger phrases and steps.
3. Check which asks pick it:
   `PYTHONPATH="$VOOL_SRC" python -m tools.community_skills try aisle-sorter --pick "here is my shopping list: milk, apples, bread" --not-pick "write a poem about autumn"`
4. Write the zip:
   `PYTHONPATH="$VOOL_SRC" python -m tools.community_skills zip aisle-sorter`
5. In a VOOL chat say `import this skill /full/path/to/aisle-sorter.zip`, then `turn it on`.

Full walk-through: [Make a skill](make-a-skill.md).

## Shortest path: a plugin

From the root of a VOOL source checkout:

1. Copy the working example: `cp -R examples/plugins/template word-tally`
2. In `word-tally/.codex-plugin/plugin.json` set your own `name`, `vool.id`, `vool.name`, `vool.publisher`,
   `vool.homepage` and the tool's `intent` (`word-tally.<tool>`). Replace `bin/server` with your program.
3. Check it: `python -m tools.plugin_check word-tally`. It prints `OK: ...` or the field to fix.
4. In VOOL open **Skills & Plugins > My add-ons > Plugins**, press **Install from a folder**, **Review folder**, then **Install and keep disabled**.
5. Press **Review access and enable**, read the sheet, press **Accept and enable**, then ask for the tool in a chat.

Full walk-through: [Make a plugin](make-a-plugin.md).

## What VOOL never lets a skill or plugin do

1. **Spend money or approve anything.** Spending, approvals and permissions stay with VOOL and the owner, so no add-on can agree on the owner's behalf.
2. **Read the owner's keys.** A skill holds no keys; a plugin gets only the key slots it declared, so one add-on cannot take what belongs to the owner or to another add-on.
3. **Start by itself.** A skill is used only when a message picks it, and a plugin is off until the owner switches it on, so nothing runs that nobody asked for.
4. **Call itself VOOL's, official, verified or tested.** Those names and marks are given by the VOOL team on an exact version, so a package cannot award them to itself.
5. **Fetch or install software.** A skill cannot tell anyone to install or run a program, and a plugin runs only the files in the folder the owner reviewed, so what was checked is what runs.

An add-on that tries any of these is refused with a sentence that says why. Every rule, its reason and what to do
instead: [Rules and why](rules-and-why.md).

## All pages

| Page | What it covers |
| --- | --- |
| [Make a skill](make-a-skill.md) | Write, check, try, turn on, share as a zip, publish |
| [Make a plugin](make-a-plugin.md) | The template, the manifest field by field, the check command, review, install, share |
| [Rules and why](rules-and-why.md) | Every rule, its reason, the sentence you see, what to do instead |
| [Publish and share](publish.md) | The Skill Codex: who can publish, checks, marks, reports, removal, download counts |
| [FAQ](faq.md) | Short answers to the questions creators ask most |
| [agents/README.md](agents/README.md) | The same facts written for an AI agent |

Generated from the code, always current:
[plugin refusals](reference/plugin-refusals.md), [skill rules](reference/skill-rules.md),
[names kept back](reference/names-kept-back.md).
