---
description: For AI agents and developers. Write a SKILL.md, lint it, install it locally, test that VOOL picks it, update it, remove it, then publish.
---

# Build and test a skill

This page is for AI agents and developers who write skill files directly. A person who wants a
skill can simply ask in chat: see [Make your own skill](make-your-own-skill.md).

The order is: **write -> lint -> install locally -> turn on -> test -> update -> publish**.
Do not publish a skill you have not installed and tested on your own VOOL first.

## Quick start: two commands

Before you start: the tool names a skill may use, what Manual and Auto mode ask for, and every
refusal a skill can run into are listed in [Skill tools and limits](skill-tools-and-limits.md).

From a VOOL source checkout (`export VOOL_SRC=/full/path/to/vool`), in a folder of your own:

```sh
PYTHONPATH="$VOOL_SRC" python -m tools.community_skills new meeting-actions --name meeting-actions --author "Your Name"
```

This writes `meeting-actions/SKILL.md` (a starter with the right frontmatter) and
`meeting-actions/LICENSE` (the MIT text with your name and the year), plus
`meeting-actions.metadata.json` **beside** the folder (for packaging later; it stays out of the
folder so the folder can be zipped and imported as it is). Edit `SKILL.md`: replace the
description, the two trigger phrases and the steps (every line that says REPLACE). `try`, `zip`
and `lint` refuse a skill that still holds REPLACE text. Then check that VOOL picks it for the
asks you mean, and leaves it alone otherwise:

```sh
PYTHONPATH="$VOOL_SRC" python -m tools.community_skills try meeting-actions \
  --pick "here are my meeting notes, what are the action items?" \
  --pick "turn these meeting notes into a to-do list" \
  --not-pick "write a poem about autumn"
```

`try` validates the skill, installs it into a throwaway profile it makes and removes itself
(never your real VOOL), and prints `ok` or `FAIL` for each ask, then a summary line. A `FAIL`
says why: `picked by trigger "..."`, `picked by word from the skill's name "meeting"`,
`picked by shared words "notes", "action"`, or `not picked: no trigger matched; shares only "notes"`.
`warn` lines (never failures) name a trigger that comes down to one word or to none once
function words are dropped ("what do they want from me" has none left), and name words that pick
the skill on their own. It exits 0 only when every ask behaved. Fix the description or triggers
and run it again until it passes.

Then write the zip to share or import:

```sh
PYTHONPATH="$VOOL_SRC" python -m tools.community_skills zip meeting-actions
```

It writes `meeting-actions.zip` beside the folder with `SKILL.md` and, when present, `LICENSE`,
`README.md` or `README.txt`, names any other file it left out, and checks the zip with the same
reader `import this skill` uses. The sections below explain each part, and how to install the
skill in your own VOOL.

Python: CI pins Python 3.12.13 and `pyproject.toml` accepts 3.11 or newer. The authoring
commands on this page (`new`, `try`, `zip`, `call`, `init`, `lint`) also run on Python 3.13.

## 1. The SKILL.md format

A skill is one folder holding a `SKILL.md`: a YAML frontmatter block, then the instructions.

```markdown
---
name: aisle-sorter
description: Sort a shopping list the user gives into supermarket aisles (produce, bakery, dairy, frozen and so on). Use when the user shares a shopping list or grocery list and wants it sorted or grouped by aisle.
triggers:
  - shopping list
  - grocery list
  - sort by aisle
license: MIT
---

# Aisle sorter

1. Read the list the user gave. Keep every item exactly once; never add items.
2. Group the items under these headings, in this order: Fruit and vegetables,
   Bakery, Meat and fish, Dairy and eggs, Dry food, Drinks, Household, Frozen.
3. Put an item you cannot place under "Other".
4. Start the reply with the heading "Shopping list by aisle" and end it with
   the line "Items: N", where N is the number of items.
```

Frontmatter keys VOOL reads:

| Key | Required | What VOOL does with it |
| --- | --- | --- |
| `name` | yes | The skill's identity. Use lowercase words joined by hyphens, the same as the folder name. Lists show it as plain words ("Aisle sorter"). If missing, the folder name is used. |
| `description` | yes | When the skill applies. Part of what picking compares (see section 6). An empty description fails validation. |
| `triggers` | recommended | Phrases a person says when they want this skill. A YAML list, or one comma-separated string. |
| `allowed-tools` | no | Narrows the tools offered on turns where the skill is picked. Every name must be a real VOOL tool, or validation fails: the names are listed in [Skill tools and limits](skill-tools-and-limits.md#the-tools-a-skill-may-name). Leaving it out narrows nothing. It never grants a permission. |
| `permissions` | no | A list of permission ids (such as `create_files`, `external_messages`). It grants nothing; see [the permissions key](#the-permissions-key). |
| `license` | for publishing | Read by the publishing tools. Keep the full licence text in a `LICENSE` file beside `SKILL.md` (for MIT: the standard MIT text with your name and the year). |

### The permissions key

`permissions` is a statement, not a request: VOOL never grants anything because of it. When a
skill declares it, the validator checks that it covers every permission id the tools in
`allowed-tools` declare (the **Permission ids** column in
[Skill tools and limits](skill-tools-and-limits.md#the-tools-a-skill-may-name)), and fails with,
for example, `declared permissions ['read_files'] do not cover what
operator.schedule_calendar_event requires: external_messages`. Leave it out and nothing is
checked. Either way the person's mode decides what each call may do.

The instructions (everything after the frontmatter) must be under 8,000 characters; a longer
body fails validation. The frontmatter must be a YAML object. A skill adds instructions only;
it cannot grant access, raise a spending limit or approve an action.

Write the instructions so the answer has a visible marker (a fixed heading, a fixed last
line). Then every test below shows at a glance whether the skill was used.

## 2. Lint with the SDK

The `vool-community` SDK checks the file with VOOL's own validator. From a source checkout, use
`PYTHONPATH="$VOOL_SRC" python -m tools.community_skills` in its place: a `vool-community` on your
PATH from another install may not match your checkout and can refuse with "invalid package
metadata fields". Work in a new folder of your own, with the skill at
`my-skills/aisle-sorter/SKILL.md` and its licence at `my-skills/aisle-sorter/LICENSE`. Set these
once and keep the same shell for sections 2 to 7 (sections 3 and 5 use them too):

```sh
export VOOL_SRC=/full/path/to/vool          # the VOOL source checkout
export SKILL_TEST_HOME="$(mktemp -d)"       # throwaway profile
export SKILL_FOLDER="$PWD/my-skills/aisle-sorter"
```

```sh
python - <<'PY'
import json, secrets
from pathlib import Path
info = {
    "id": "your-namespace/aisle-sorter", "kind": "skill", "version": "1.0.0",
    "summary": "Sort a shopping list into supermarket aisles.",
    "category": "personal-home", "subcategory": "shopping",
    "license": "MIT", "languages": ["en"],
    "source": {"upload_id": secrets.token_hex(16), "namespace": "your-namespace"},
}
with Path("metadata.json").open("x", encoding="utf-8") as handle:
    json.dump(info, handle)
PY
vool-community init package-draft --metadata metadata.json --license-file my-skills/aisle-sorter/LICENSE
cp my-skills/aisle-sorter/SKILL.md package-draft/skills/aisle-sorter/SKILL.md
vool-community lint package-draft --scratch sdk-scratch
```

If you started with `new`, use the `meeting-actions.metadata.json` it wrote beside the folder in
place of writing `metadata.json` by hand.

`needs` (optional) says what the skill needs from the person's VOOL, so the library can show it.
It is a list of ids from this fixed list (`core/registry/requirements.py`); any other id is
refused:

| Id | Means |
| --- | --- |
| `search_web` | Web search (a search provider key in VOOL) |
| `llm_cloud` | A cloud AI model (a cloud provider key) |
| `chain_data` | Chain data (a chain data provider key) |
| `solana_rpc` | A Solana connection |
| `wallet` | The VOOL wallet |
| `browser` | The browser |
| `files` | The person's files |

Add it to `metadata.json` before `init` (it becomes `registry.json` in the package), for example
`"needs": ["search_web"]`. When SKILL.md seems to use one you left out, `lint` adds a notice (not
a block): `SKILL.md seems to use "VOOL wallet". If it does, add "wallet" to the "needs" list in
metadata.json (registry.json in the package folder), like "needs": ["wallet"].`

`category` and `subcategory` must come from the fixed list in `core/registry/categories.py`
(for example `productivity` / `meetings`); any other value is refused. The `source` above is the
shape for an upload. A package imported from GitHub uses a GitHub `source` instead; the author
guide (section 9) shows both.

A pass prints `"status":"preflight_passed"` and exits 0. A problem prints `"status":"blocked"`,
exits 1 and names it under `findings`, for example *description is empty*. Fix
`my-skills/aisle-sorter/SKILL.md`, copy it into `package-draft` again and re-run `lint`.
`init` refuses an existing `package-draft`, and the script above refuses an existing
`metadata.json`: remove them or use new names.
Linting is a local check only. It does not publish anything.

## 3. Install it locally

Pick one of the two routes. Both need the person's consent in VOOL's normal permission flow.

### Route A: zip import, in chat

Zip the skill folder (not `package-draft`). The zip may hold `SKILL.md` and, optionally,
`LICENSE`, `README.md` or `README.txt`; VOOL refuses any other file, including scripts and a
`metadata.json` (the refusal reads `VOOL imports instruction-only skills; this zip also carries:
metadata.json`). `vool-community zip` writes only the files VOOL takes:

```sh
PYTHONPATH="$VOOL_SRC" python -m tools.community_skills zip my-skills/aisle-sorter
```

(`(cd my-skills && zip -r ../aisle-sorter.zip aisle-sorter)` works too, when the folder holds
only those files.)

In a VOOL chat, give the full path (it must start with `/` or `~` and end in `.zip`), or attach
the zip to the message and say only `import this skill`:

```text
import this skill /full/path/to/aisle-sorter.zip
```

Add `for this chat`, `for this project` or `for all of VOOL` to choose where it works (the
default is all of VOOL). VOOL replies that the zip `holds the skill aisle-sorter, staged for all
of VOOL`, shows the instructions, and says it is not on yet. In Manual mode the import first
shows a **Permission needed** bar: click **Allow once**.

### Route B: folder install with `skill.install`

`skill.install` is a VOOL runtime tool, for an agent that calls VOOL's tools directly (it is
a tool call, not a chat sentence). Its arguments: `path` (a folder holding `SKILL.md`, a
`SKILL.md` file, or a skill name), `overwrite` (true to replace an installed skill of the same
name), `scope` (`chat`, `project` or `global`, default `global`). It validates the skill and
switches it on in the same step; there is no separate turn-on. Inside VOOL it still waits for
**Allow once**, in Manual and Auto mode. Only `SKILL.md` is copied.

```text
skill.install path=/full/path/to/my-skills/aisle-sorter
```

Without `overwrite`, a second install of the same name stops with `exists`.

The chat steps have matching tools for an agent: `skill.import` (zip), `skill.list`,
`skill.validate` (with `path`: a folder, a `SKILL.md` file or a skill name, the same as
`skill.install`), `skill.remove` and `skill.rollback` (with `skill` and `version`). Their replies
are worded for a tool result, not word for word the chat replies quoted on this page. When
`skill.install` refuses a skill, its reply lists what the validator found, for example
`the skill was not installed because it does not pass the skill check: allowed-tools names tools
this runtime does not have: workspace.writefile`.

For automated tests from a source checkout, call the same tool against a **throwaway profile**
(the variables from section 2).
This skips the Allow prompt, so never point it at a person's real VOOL profile:

```sh
HOME="$SKILL_TEST_HOME" VOOL_HOME="$SKILL_TEST_HOME/vool" VOOL_PLUGINS_DIR="$SKILL_TEST_HOME/plugins" \
PYTHONPATH="$VOOL_SRC" python - <<'PY'
import os
from core.runtime_execution_tools import execute_runtime_tool
result = execute_runtime_tool("skill.install", {"path": os.environ["SKILL_FOLDER"], "overwrite": True})
print(result.status, result.response_text)
PY
```

It prints `ok Added the skill "Aisle sorter". It is on from your next message.` A
"Keychain unavailable" line on a fresh profile is expected.

`execute_runtime_tool` is the inner door for the skill tools and a few others. For a tool another
part of VOOL owns (`web.search`, every `operator.*` tool) it returns `None`, so `result.status`
raises `AttributeError: 'NoneType' object has no attribute 'status'`. To call any other tool, use
the next section.

### Exercise a skill's tool calls

To see what a tool in your `allowed-tools` answers, and what the person would be asked to
approve, call it the way a real turn does: `core.tool_intent_executor.execute_tool_intent`, which
runs the Manual/Auto permission check first. `vool-community call` does this in a throwaway
profile and a throwaway project folder, and prints one JSON line:

```sh
PYTHONPATH="$VOOL_SRC" python -m tools.community_skills call operator.schedule_calendar_event \
  --args '{"title": "Dentist", "start_iso": "2026-11-03T10:00:00", "duration_minutes": 60}'
```

```text
{"approval_needed":true,...,"reply":"Manual mode requires approval for this exact action. ...","status":"pending_approval",...}
```

`--mode auto` checks Auto mode instead, and `--approve` clicks **Allow once** for you and calls
again, so you see the result after approval. The same call from your own Python, in a throwaway
profile (the variables from section 2):

```sh
HOME="$SKILL_TEST_HOME" VOOL_HOME="$SKILL_TEST_HOME/vool" PYTHONPATH="$VOOL_SRC" python - <<'PY'
from storage.migrations import run_migrations
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent
run_migrations()  # a fresh profile has no database yet
tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
result = execute_tool_intent(
    {"intent": "web.search", "arguments": {"query": "aisle order in supermarkets"}},
    task_id="skill-test", session_id="skill-test", hive_activity_tracker=tracker,
    source_context={"runtime_session_id": "skill-test", "operating_mode": "manual"})
print(result.status, result.response_text)  # always a result, never None
PY
```

On a fresh profile this prints `disabled No. Web lookups are off. ...`: web lookups are off until
the person turns them on. Never call the functions behind this check (such as
`core.execution.operator_tools.execute_operator_tool`) to test a skill: they run the action with
no approval at all, which no real turn does. What each refusal means is in
[Skill tools and limits](skill-tools-and-limits.md#refusals-and-what-they-mean).

## 4. Turn it on

After a zip import, say in the same chat:

```text
turn it on
```

VOOL asks with **Permission needed** in both Manual and Auto mode; click **Allow once**. The reply
is `Added the skill "Aisle sorter". It is on from your next message.` Check it with
`which skills do I have?`: the skill is listed under **Made by you** as `on`. In chat,
`validate the aisle-sorter skill` re-runs VOOL's validator on the installed copy.

## 5. Test it

Use **new** messages, after the one that turned it on. Ask at least three different phrasings
that must pick the skill, and at least one ask that must **not** pick it:

| Ask | Expected |
| --- | --- |
| Here is my shopping list: milk, apples, bread, frozen peas | picked (trigger `shopping list`) |
| Can you sort my grocery list? eggs, rice, soap | picked (trigger `grocery list`) |
| Sort these by aisle: tea, yoghurt, bananas | picked (shared words `sort`, `aisle`) |
| Write me a short poem about autumn | not picked |
| What is the best supermarket near me? | not picked (one shared word only) |

In chat, look for the skill's marker (here the heading "Shopping list by aisle" and the
"Items: N" line). Picking needs no model, so you can also check it offline with the throwaway
profile from Route B (install first). This uses the same function VOOL calls before each
answer:

```sh
HOME="$SKILL_TEST_HOME" VOOL_HOME="$SKILL_TEST_HOME/vool" VOOL_PLUGINS_DIR="$SKILL_TEST_HOME/plugins" \
PYTHONPATH="$VOOL_SRC" python - <<'PY'
from core.tool_offer_assembly import skill_guidance_for
SKILL = "aisle-sorter"
MUST_PICK = [
    "Here is my shopping list: milk, apples, bread, frozen peas",
    "Can you sort my grocery list? eggs, rice, soap",
    "Sort these by aisle: tea, yoghurt, bananas",
]
MUST_NOT_PICK = ["Write me a short poem about autumn", "What is the best supermarket near me?"]
failed = False
for ask in MUST_PICK + MUST_NOT_PICK:
    picked = [row.get("name") for row in skill_guidance_for(ask, source_context={"session_id": "skill-test"}).skills]
    ok = (SKILL in picked) == (ask in MUST_PICK)
    failed |= not ok
    print("ok  " if ok else "FAIL", repr(ask), "->", picked)
raise SystemExit(1 if failed else 0)
PY
```

It exits 0 when every ask behaves as expected. The person's own skills are ranked against each
other, so install the other skills you care about into the same profile before you test.

## 6. How picking works

Before each answer VOOL compares the message with each skill's `name`, `description` and
`triggers` (code: `rank_skills` in `core/plugin_skills.py`). Words are lowercased, short words
and function words are dropped, and light suffixes are folded (`polite`/`politely`,
`organise`/`organize`, `translate`/`translation`). A skill qualifies when:

* the message **names the skill** (`use the aisle-sorter skill`), or contains every word of a
  specific multi-word name;
* the message contains a **whole trigger**. Every word of a multi-word trigger must be in the
  message; one word out of it never counts. A word that appears only inside a multi-word
  trigger does not count on its own either;
* the message shares **two or more distinct content words** with the skill. Kind-of-help words
  (`explain`, `write`, `make`, `help`, `plan`, `check`, `list`, `table`, `summarise`,
  `summarize`, `draft`, `fix`) never carry a match alone; or
* one word that is in the skill's **name** and in no other skill.

**One ordinary shared word is not enough.** A whole trigger scores more than description
overlap, and rare words score more than words many skills share. Up to three skills are added
to one answer; the person's own skills come before built-in and Browse skills.

What this means for authors:

* Add `triggers` with the 2-4 word phrases people really type ("grocery list", "sort by aisle").
* Avoid single everyday words as triggers ("table", "list", "notes"): they either never match
  alone or match far too much.
* Say the topic in the `description` with specific words, not only "Use when the user asks...".
* Write your must-not-pick asks with one ordinary word from your description in them, to prove
  one shared word is not enough. Do not use a word of the skill's **name** there: a name word that
  no other skill has picks the skill on its own (the last rule above). With a skill named
  `meeting-actions`, "when is my next meeting?" picks it.

## 7. Update it

Change `SKILL.md`, then lint again (section 2) and install again:

* **Zip route:** zip it again, `import this skill /full/path/to/aisle-sorter.zip`, then
  `turn it on`.
* **Folder route:** `skill.install` with `overwrite=true`.

Both reply `Updated the skill "Aisle sorter". It is on from your next message.` (version 2 or
later says **Updated**; the first install says **Added**). Re-run the tests from section 5.

Every install is a recorded version. In chat, `roll back the aisle-sorter skill` lists the
versions, and `roll back the aisle-sorter skill to version 1` restores one byte for byte.

## 8. Remove it

```text
remove the aisle-sorter skill
```

(`turn off ...` and `delete ...` do the same.) After **Allow once** the reply is:

> Turned off `aisle-sorter` for all of VOOL. Its files and versions were kept.

The skill stops loading from the next message and its folder is moved aside, not deleted. To use it again,
import or install it again; that install says **Added** and starts a new version history.
Built-in and Browse skills cannot be removed this way.

## 9. Publish

Adding a skill to the shared library (Skill Codex) is not open to every author yet. Until it is,
share a skill as its zip file: anyone can import it with *import this skill* (section 3, Route A).

**A skill with `allowed-tools`** works on every route that can check the names, and is refused on
the one that cannot:

* **Zip share** and **local install** (`skill.install`, `try`): accepted when every name is a
  real VOOL tool.
* **Import from a GitHub repository** (Browse): not import-ready (*This skill declares a
  host-specific tool list that needs a VOOL mapping.*). Share it as a zip instead. On this route
  the skill folder may carry pictures in `assets/` (PNG, JPEG, WebP or SVG, at most 1 MB each);
  they are not installed. Any other extra file makes the skill not import-ready.
* **Upload to Skill Codex**: the automatic checks run the same validator as `lint`, so real tool
  names pass. What is refused is a package manifest (`.codex-plugin/plugin.json`) that asks for
  tools, permissions or credentials; `vool-community init` writes none.

The full table is in [Skill tools and limits](skill-tools-and-limits.md#can-a-skill-with-allowed-tools-be-shared-or-published).

Publishing to Skill Codex is a separate, reviewed process with its own metadata, licence and
consent rules. Continue from the `package-draft` you linted in section 2 and follow the
author guide, `AUTHOR-GUIDE.md` in the repository's
[docs/registry folder](https://github.com/Parad0x-Labs/vool/tree/main/docs/registry).
Nothing on this page uploads or publishes anything.
