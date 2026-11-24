---
description: Short answers to the questions people ask when they build a VOOL skill or plugin, each with a link to the detail.
---

# Creator FAQ

Short answers. Each links to the page with the detail. New here? Start with the [README](README.md).

## Getting started

### Do I need a skill or a plugin?

If words are enough, a skill. If it needs new code or a new service, a plugin.
[Which one do I need?](README.md#which-one-do-i-need)

### Which VOOL do I need?

Skills work in VOOL 0.7.7 and later. Plugins need VOOL 0.8.0 or newer.

### Why does `vool-community` not know `new`, `try` or `zip`?

Those commands came after 0.7.7; an installed 0.7.7 has only `init`, `lint`, `pack`, `check` and
`submission-link`. Run them from a VOOL source checkout as `python -m tools.community_skills ...`, with
Python 3.11 or newer. [Make a skill](make-a-skill.md#1-a-complete-skill-that-works)

### Can I make a skill without writing a file?

Yes. In a VOOL chat say *make me a skill that ...*, click **Allow once**, then say *turn it on*.
[Make your own skill](../guides/make-your-own-skill.md)

### Do I need an account?

Only to publish, star, comment or report on the Skill Codex. Building, sharing a zip or a folder, browsing
and installing need none. [Publish and share](publish.md#who-can-publish)

## Skills

### Why is my skill not picked?

One ordinary shared word is not enough. The message has to name the skill, hold every word of one trigger
phrase, or share two or more meaningful words with it. Add the phrases people really type as `triggers` and
test with `try --pick`. [Try which asks pick it](make-a-skill.md#3-try-which-asks-pick-it)

### Why is my skill picked when it should not be?

A word from the skill's name that no other skill uses picks it on its own, and single everyday words make
weak triggers. Choose a more specific name, use 2 to 4 word triggers, and test with `try --not-pick`.
[Try which asks pick it](make-a-skill.md#3-try-which-asks-pick-it)

### Why is my tool list ignored?

It is read, and it only narrows. On someone else's VOOL an imported, downloaded or published skill never
adds a tool: its `allowed-tools` limits which tools are offered while the skill is in use, and each tool
still asks where it would ask anyway. Permissions come from the owner, never from a skill's text.
A name that is not a VOOL tool is refused (`workspace.writefile` for `workspace.write_file`, say): use the
names in [the tool table](../guides/skill-tools-and-limits.md#the-tools-a-skill-may-name).
[Rules: tools](rules-and-why.md#tools)

### Can my skill run every morning?

No skill runs by itself; it is used only when a message asks for it. A `schedule`, `cron` or `every` line
in the header is answered with *a skill never runs by itself*. What a skill can do: set a reminder (put
"remind me on <date> at <time>" in the text of `operator.save_note`, one reminder per call), or ask for a
wallet or token price watch with `watch.add`, which checks only while VOOL is open. The person approves both.
[Reminders from a skill](../guides/skill-tools-and-limits.md#reminders-from-a-skill)

### What happens to a header setting VOOL does not read?

Nothing, and VOOL tells you: *"priority" is a setting VOOL does not read, so it changes nothing.* The skill
still loads. [A setting VOOL does not read](make-a-skill.md#a-setting-vool-does-not-read)

### Can a skill include a script or other files?

No. A skill is `SKILL.md` plus an optional `LICENSE` or `README`. VOOL refuses a zip that carries anything
else, because a skill is text and runs no code. If it needs code, build a plugin.
[Rules: running programs](rules-and-why.md#running-programs)

### Can my skill ask the person for a password or a key?

No. `VOOL skills can't ask for passwords, keys, recovery phrases or codes.` A skill never needs them: a
tool that needs a key gets it from VOOL's settings. [Rules: secrets](rules-and-why.md#secrets)

### Why was my skill refused for a command I only quoted as advice?

Commands are refused even inside advice about them. Write the advice in words instead. [Skill rules](reference/skill-rules.md)

### Does a SKILL.md written for another app work in VOOL?

Yes, when it has a `name`, a `description` and instructions, no extra files, and no tool list written for
the other app. A tool list must use VOOL's tool names; leave it out and nothing is narrowed.
[The header settings VOOL reads](make-a-skill.md#2-the-header-settings-vool-reads)

### How long can a skill be?

The instructions under the header stay under 8,000 characters. Shorter is better: a picked skill is added
to the answer whole or left out, never cut short.
[How picking works](../guides/build-and-test-a-skill.md#6-how-picking-works)

### How do I update a skill?

On your own VOOL: change `SKILL.md`, import the new zip and say *turn it on*. VOOL replies **Updated** and
keeps every version, so you can roll back. On the Skill Codex: same id, higher version number.
[Update or remove it](make-a-skill.md#6-update-or-remove-it), [Updating](publish.md#updating)

## Plugins

### Why does my plugin say "a setup mistake"?

`This plugin has a setup mistake its maker needs to fix. It was not installed.` is the sentence for the
person installing. The line for you comes right after it: run `python -m tools.plugin_check FOLDER` and read
**For the plugin's maker**, which names the field and what to write. The usual causes: a fixed value in a
server's `env`, `${...}` in `args`, a tool that names a server nobody declared, or a key name no slot declares.
[The check command](make-a-plugin.md#3-the-check-command)

### Why does it say "The plugin's setup file is invalid or missing"?

The manifest has a field VOOL does not know, lacks a required one, or a tool name has the wrong shape. The
maker's note names it, for example `manifest.vool.tools[0]: unknown field timeout`.
[The manifest, field by field](make-a-plugin.md#4-the-manifest-field-by-field)

### Why does it say "made for an older VOOL"?

The manifest has no `vool` block. A plugin made for another app needs one added: it is where the plugin
lists its tools, websites and keys for the owner to review.
[The manifest, field by field](make-a-plugin.md#4-the-manifest-field-by-field)

### Does the check show every problem at once?

Yes. The plugin check and the skill check each list every problem they can find in one run, with the field and the fix.

### Can my plugin use the person's OpenAI key?

No. A plugin gets only the key slots it declared, under names of its own, and names such as
`OPENAI_API_KEY` are kept back. The owner's model keys pay for the owner's chats; a plugin that could read
them could spend them. Ask for a key to your own service in `vool.credentials`.
[Rules: secrets](rules-and-why.md#secrets)

### Can I bring an MCP server I already have?

Yes, if it runs from inside the plugin folder. The `command` is a path in the folder, not `npx`, `uvx` or a
program elsewhere on the computer, and each tool is listed in `vool.tools` with its `input_schema`. VOOL
runs no installer, so ship everything the server needs in the folder.
[Rules: running programs](rules-and-why.md#running-programs)

### Can my plugin reach any website it likes?

It reaches the hosts it lists in `network`, each one exact and public: no wildcard, no address in numbers,
no local names. The owner agrees to that list before switching the plugin on.
[Rules: websites](rules-and-why.md#websites)

### Can my plugin read the person's files?

No. It reads its own folder and writes its own working space. Give your tool what it needs through its
input, and return the result as its answer. [Writing a plugin](../guides/plugin-authoring.md#files-and-processes)

### Why did my plugin switch off after I edited it?

VOOL checks the exact files on every call. A changed file is a plugin nobody reviewed, so it is switched off
until you review and enable it again. [Call it](make-a-plugin.md#6-call-it)

### Why is my plugin's tool not offered in a chat?

Check three things: the plugin is **Enabled**, **Let chats use plugin tools** is on in the Plugins section,
and you are at the computer VOOL runs on. Phone and remote turns cannot use plugins that run programs.
[Call it](make-a-plugin.md#6-call-it)

### Can I charge for a plugin?

A plugin never takes payment itself. If your service charges per call, list it in `vool.paid_services`:
VOOL pays the service from the owner's wallet, only after the owner ticked your plugin and only inside the
limits they set. A service that bills through an account works too: the owner saves that account's key in
your plugin's key slot. Skills on the Skill Codex are free, under the MIT licence.
[Rules: money](rules-and-why.md#money)

## Names, publishing and sharing

### Why can't I call it "Official ..."?

Words such as Official, Verified or Tested, VOOL's own name, ticks and stars say that someone checked or
chose the package. Those marks are given by the VOOL team on an exact version, so a package cannot put them
on itself. Name it for what it does. [Rules: identity and names](rules-and-why.md#identity-and-names)

### Can I publish a plugin on the Skill Codex?

Yes, from VOOL 0.8, with `plugin-init`, `lint` and `pack`, then an upload. It installs through the same review,
switched off. You can still share it as a folder or as a public GitHub folder at an exact commit.
[Share it](make-a-plugin.md#7-share-it)

### Why can't I publish yet?

A new account can publish 14 days after it starts, counted from the older of your GitHub account and your
Skill Codex account. The message gives your date. [Who can publish](publish.md#who-can-publish)

### Can I change or take back a published version?

A published version is permanent and stays public under its MIT licence. To change the skill, publish a
higher version. [What "published" means](publish.md#what-published-means)

### Why was my skill paused?

Three different people, each with an account at least 14 days old, reported it as a scam, harmful or
copied. Downloads pause for at most 48 hours unless the team confirms the pause. A pause is not a verdict,
and the skill stays listed. Reports are private: no page shows how many a skill has or who sent them.
[Reports and pauses](publish.md#reports-and-pauses)

### What do the marks on a listing mean?

Tested, Highlighted, Editor's pick and Featured are set by the VOOL team by hand. Stars and downloads never
set them. [The team's marks](publish.md#the-teams-marks)

### Does VOOL track installs?

No. The Skill Codex keeps one number per package, the downloads served. Who downloaded is not recorded, and
VOOL reports no installs. [The download count](publish.md#the-download-count)

### What happens to my skills if I delete my account?

Published skills stay public under their MIT licence and your public name stays reserved. Your account,
unpublished uploads, comments and stars are deleted.
[When you delete your account](publish.md#when-you-delete-your-account)
