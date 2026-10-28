---
description: For AI agents and developers. Write a SKILL.md, lint it, install it locally, test that VOOL picks it, update it, remove it, then publish.
---

# Build and test a skill

This page is for AI agents and developers who write skill files directly. A person who wants a
skill can simply ask in chat: see [Make your own skill](make-your-own-skill.md).

The order is: **write -> lint -> install locally -> turn on -> test -> update -> publish**.
Do not publish a skill you have not installed and tested on your own VOOL first.

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
| `allowed-tools` | no | Narrows the tools offered on turns where the skill is picked. Every name must be a real VOOL tool, or validation fails. Leaving it out narrows nothing. It never grants a permission. |
| `license` | for publishing | Read by the publishing tools. Keep the full licence text in a `LICENSE` file beside `SKILL.md`. |

The instructions (everything after the frontmatter) must be under 8,000 characters; a longer
body fails validation. The frontmatter must be a YAML object. A skill adds instructions only;
it cannot grant access, raise a spending limit or approve an action.

Write the instructions so the answer has a visible marker (a fixed heading, a fixed last
line). Then every test below shows at a glance whether the skill was used.

## 2. Lint with the SDK

The `vool-community` SDK checks the file with VOOL's own validator. From a source checkout,
`python -m tools.community_skills` is the same command. Work in a new folder of your own, with
the skill at `my-skills/aisle-sorter/SKILL.md` and its licence at
`my-skills/aisle-sorter/LICENSE`:

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
`LICENSE`, `README.md` or `README.txt`; VOOL refuses any other file, including scripts.

```sh
(cd my-skills && zip -r ../aisle-sorter.zip aisle-sorter)
```

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

For automated tests from a source checkout, call the same tool against a **throwaway profile**.
This skips the Allow prompt, so never point it at a person's real VOOL profile:

```sh
export VOOL_SRC=/full/path/to/vool          # the VOOL source checkout
export SKILL_TEST_HOME="$(mktemp -d)"       # throwaway profile
export SKILL_FOLDER="$PWD/my-skills/aisle-sorter"
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
* Write your must-not-pick asks with one of your words in them, to prove one shared word is not
  enough.

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
import or install it again. Built-in and Browse skills cannot be removed this way.

## 9. Publish

Publishing to Skill Codex is a separate, reviewed process with its own metadata, licence and
consent rules. Continue from the `package-draft` you linted in section 2 and follow the
author guide, `AUTHOR-GUIDE.md` in the repository's
[docs/registry folder](https://github.com/Parad0x-Labs/vool/tree/main/docs/registry).
Nothing on this page uploads or publishes anything.
