---
description: The checks VOOL runs before a skill or plugin is added, and what they do and don't prove.
---

# Add-on checks

In **Skills & Plugins**, looking, checking and installing are separate steps. Nothing runs
while you browse, and nothing is added until you decide. A check describes the exact files it
read. It is not a safety guarantee, a recommendation of the maker, a permission or a wallet
approval.

## Skills made by VOOL: the local check

The 75 skills made by VOOL on **Browse** come inside the app, each one pinned to its exact file.
For these, **Add this skill** runs VOOL's local check only: no key, no scan allowance and no
network request.

1. VOOL reads the skill and its licence from the app's own copy and refuses them unless they are
   exactly the shipped files (*This VOOL skill's file doesn't match the copy that came with VOOL,
   so it was not added.*).
2. The local check reads the skill for risky instructions and shows the result. A clean result
   is added with **Add and turn on**. Anything it flags needs your separate OK for that exact
   version (**Add it anyway**). **Reject** discards it.
3. The installed skill is checked against its pinned file every time it loads. A changed file
   makes it unavailable.

Skills from other makers never take this path.

## Public skills from other makers

Pick a public skill on Browse, or paste a GitHub link. VOOL fixes it to one exact version and
asks before downloading it. It then checks the skill on this device. No key is needed.

* If you turned on a scanner plugin, it also receives the skill's public text when you ask for a
  scan. This can use that service's allowance. Your chats, private files and keys are never sent.
* Read the findings first, then decide. A clean result is added with **Accept and enable**. If
  either check found a problem, VOOL adds it only after you accept the risks for that exact
  version. Accepting changes neither the report nor what the skill may do.
* A review older than 24 hours, files that changed after the check, or a different version than
  the one you checked are refused, and VOOL asks you to check again.

## Skills and plugins from the Skill Codex

The Skill Codex list is signed by the VOOL team, and VOOL refuses a list that does not verify.
For each add-on you install from it, VOOL checks that the downloaded files are exactly the ones
that were published, then runs its own safety checks on them on this device. If those fail, the
add-on is not kept: *This skill didn't pass VOOL's own safety checks, so VOOL didn't keep or
install it.*

The checks refuse, among other things, text that:

* asks someone to install or run software, or links to a program to download;
* uses short links, raw addresses or paste sites;
* asks for a recovery phrase, key, password, card number or PIN, or holds a wallet address;
* claims you already agreed, or speaks as VOOL;
* tries to change VOOL's instructions or hide what the skill does, with hidden comments,
  invisible characters or letters mixed from different alphabets;
* says the Skill Codex or VOOL vouches for it.

Badges (**Tested by VOOL**, **Editor's Choice**) come only from a document signed by two VOOL team
members, for one exact version, and each one runs out. Anything that does not verify shows no
badge.

**Check once a day for removed add-ons** is on unless you turn it off. It reads the same public
list once a day, so an add-on the team removed stops being used here, and a withdrawn badge
disappears. Nothing about you is sent.

## Optional scanner plugins

A remote scanner is optional. With none turned on, VOOL's own check on this device decides. To
add one, install a scanner plugin from its maker under **My add-ons → Plugins**, save your own
key, review its access and enable it. VOOL has no scanner of its own and no central scanner key.

VOOL's own check always runs. A scanner plugin's report is added beside it: if either one finds
a problem, nothing is installed without your OK, and a scanner can never approve an installation
on its own. A scanner with a missing or rejected key fails plainly; VOOL never replaces that scan
with a passing result. A timeout, a service error or an incomplete report leaves the skill not
installed, and VOOL never retries by itself. A timed-out request may still have used allowance.

## Plugins

A plugin is code, so it gets a full review of every file in its folder, separate from the skill
checks.

* Installing runs none of its code and keeps it switched off.
* **Review access and enable** shows everything it may use: its tools and what each one does,
  the exact websites, its service keys and any helper program. You accept that sheet before it
  switches on.
* A plugin gets only the keys it declared, when a tool runs. It can't read another plugin's keys,
  your own model keys, or anything to do with the wallet, spending limits, approvals or receipts.
* It can reach only the websites it listed. A plugin that tries any other site, or a device on
  your own network, is stopped.
* If any file changes after review, the plugin switches off until you review it again. **Disable**
  stops running calls at once.
* On a computer that can't keep a plugin's programs walled off, VOOL won't start them, and says so.

What a plugin sends back is treated as untrusted. For the full limits, see
[Plugin authoring](plugin-authoring.md).

## Good to know

* If VOOL cannot read its record of installed plugins, plugins it has no record for stop offering
  skills (apart from those that come with VOOL). If it cannot read your on and off choices, every
  plugin counts as off until you turn one on again.
* A key saved for Eyebrow in an older VOOL stays on this computer and is not used.
