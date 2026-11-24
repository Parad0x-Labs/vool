---
description: Sharing a VOOL skill or plugin and publishing a skill to the Skill Codex. Who can publish, the checks, the marks, reports, removal and download counts.
---

# Publish and share

Publishing to the Skill Codex works with VOOL 0.7.7 and later. Plugins need VOOL 0.8.0 or newer.

## Three ways to share

| What you made | How you share it | Who can get it |
| --- | --- | --- |
| A skill | Send its zip. They say *import this skill*, then *turn it on* | Anyone you send it to |
| A skill | Publish it to the Skill Codex, VOOL's shared library of skills | Everyone, from the website and from inside VOOL |
| A plugin | Send the folder, or point to a public GitHub folder at an exact commit | Anyone you send it to |
| A plugin | Publish it to the Skill Codex | Everyone, from the website and from inside VOOL |

From VOOL 0.8 the Skill Codex takes plugins as well as skills. A plugin is code, so the person installing it
still reviews its files and its access sheet on their own computer, and it installs switched off: see
[Make a plugin](make-a-plugin.md#7-share-it).

Nobody needs an account to browse the Skill Codex or to install from it.

## Publish a skill, step by step

1. **Start.** In VOOL, open **My add-ons > Skills** and press **Publish to Skill Codex** on a skill you made.
   VOOL runs the Codex's checks on your device and shows any reason with its line. Then **Open Skill Codex**
   opens the website with the skill filled in. VOOL sends nothing itself: only the skill's name,
   description and text go along, no chats, memory or keys. From a file, see
   [Make a skill](make-a-skill.md#8-publish-it-to-the-skill-codex).
2. **Sign in** on the Skill Codex website and choose your public name. You choose it once; it stays yours.
3. **Pick a category** and tick the agreement: *I made this skill, or have the right to share it, and I
   release it under the MIT licence. Anyone may use, copy, change and share it.* The box starts unticked.
4. **Send.** The checks run at once. A clean upload stays private and shows you its exact checksum.
5. **Agree to publish that exact checksum.** This is a second, separate box, also unticked. Only then does
   the skill go to the final checks.
6. **Published or refused.** A refusal lists every finding with the file, the line and the words that
   tripped it. Fix them and send it again.

## Who can publish

| Rule | Reason |
| --- | --- |
| You are signed in and have a public name. | A published skill has a named maker. |
| Your account is at least 14 days old, counted from the older of your GitHub account and your Skill Codex account. Before that you see `new accounts can publish 14 days after they start; yours can from YYYY-MM-DD`. | Throwaway accounts made today cannot publish today. |
| The skill is your own work, or you have the right to share it, under the MIT licence (`Skill Codex skills use the MIT licence`). | Everyone who installs it may use, change and share it. |
| At most 10 uploads a day per account, and 30 safety checks of your uploads a month per public name. This is not a limit on ratings or reviews from people who use a skill (`This account has used this month's checks. Upload it again next month.`). | The checks cost time and money; the limits keep them available to everyone. |
| Your public name is your own: not VOOL's, not another maker's, not a role such as `admin` or `support`. | A name is not proof. See [names kept back](reference/names-kept-back.md). |

Importing a skill from GitHub needs a GitHub sign-in, and the public repository must belong to that account.

## The checks an upload gets

Every upload gets all of them, every time, before anyone else can see it:

1. **Format.** One or more `SKILL.md` files, the MIT licence text, and nothing that runs: no scripts, no
   programs, no tools, permissions or keys in the package.
2. **The text rules.** The same rules VOOL applies to a skill sent as a file: no install or download
   commands, no short links, no asks for secrets or card details, no wallet addresses, no hidden text, no
   "the person already agreed", no speaking as VOOL. Listed in [skill rules](reference/skill-rules.md).
3. **Names and words.** The title, id and summary may not say the skill was checked or chosen, and a title
   or id may not carry VOOL's name. Listed in [names kept back](reference/names-kept-back.md).
4. **Secrets.** Text that looks like a key, password or recovery phrase stops the upload, and is never quoted back.
5. **Size.** Instructions long enough to be useful (at least 80 characters) and all text under 120,000 characters.
6. **A review model reads it.** It refuses a skill that does something its summary does not say, sends
   anything somewhere the person did not choose, moves money, or is harmful.

VOOL runs the text rules again on the person's computer before it installs a published skill.

If the review cannot run right now, your upload waits; it is never published unchecked, and the wait does
not count against you. An upload still waiting after 35 days expires, and you send it again.

## What "published" means

- The skill is listed on the Skill Codex website and inside VOOL, under the category you chose.
- It passed the automatic checks. That is all it says: the checks cover known patterns, and they are not a
  promise that a skill is safe. People still read a skill before they turn it on.
- A version is permanent. Its bytes are never replaced, and published skills stay public under their MIT licence.
- Being listed gives a skill no permissions. On each person's VOOL it is text that is used when their
  message picks it, under their own settings.

### Updating

Keep the same id and send a higher version number; you can add a line of release notes. A reused or lower number is
refused, for example `Version 1.0.0 is not higher than 1.0.0, which is already listed. Use a higher version number.`
Stars, comments and the download count stay with the skill across versions.

## The team's marks

These are set by the VOOL team by hand. Stars, downloads and the creator never set them, and a package
cannot claim one in its own name or text.

| Mark | What it means today |
| --- | --- |
| **Tested** (shown as "Tested by Parad0x Labs") | The team tested this exact version. An update drops the mark until the team tests the new version. |
| **Highlighted** | The team put the skill in the Highlighted spot. |
| **Editor's pick** | The team picked the skill. |
| **Featured** | The team features the skill in the listing. |
| **Official** | The skill is published under the team's own name. Nobody else can choose that name. |

A listing also shows that the automatic checks passed. That is not a statement that a person reviewed the skill.

Coming next: two badges, "Editor's Choice" and "Tested by VOOL", and ratings from people who installed the skill.

Pictures: a skill may carry a square icon and a wide banner, PNG only. A person looks at every new picture
before it shows; until then the skill shows the standard tile.

## Reports and pauses

Anyone signed in can report a skill on the website as a scam, harmful, copied, a misleading picture, broken,
or something else. Reports are private: no page shows how many a skill has, their reasons or who sent them.

| What happens | The rule |
| --- | --- |
| Downloads pause | Open reports as a scam, harmful or copied from **three different people**. |
| Who counts | An account at least **14 days old** that is not muted. One person counts once, however many sign-ins they have, and also after deleting an account and coming back. |
| How long | A pause that only reports caused ends by itself after **48 hours**, unless the team confirms it. It does not start again until the team has closed those reports. |
| What people see | The skill stays listed. Someone who tries to download it reads: *Downloads of this skill are paused while a person looks into it. Please check back later.* |
| What never pauses by reports alone | A skill under the team's name, and a version that carries the Tested mark. Their reports go to a person, who can pause by hand. |
| Other reasons | Reports about a picture, a broken skill or something else reach the team and never pause a skill. |

Why it works this way: three people, each with an account two weeks old, are hard to fake in an afternoon,
and the 48-hour limit means the worst a group of reporters can do to a harmless skill is two days. A pause
is not a verdict. You cannot report your own skill, and each account can send at most ten reports in 24 hours.

## When a skill is removed

The team can remove a skill, or suspend its creator. Then:

- Every version leaves the list, and new uploads of that skill are refused.
- On a person's VOOL, a removed skill stops being used once VOOL reads the public list again. That happens
  when they press **Browse Skill Codex**, or once a day by itself. The switch **Check once a day for removed add-ons** on the Skill Codex tab is
  on unless the owner turns it off, and the check sends
  nothing about the person: *Nothing about you, your chats or your add-ons is sent.*
- Copies people already downloaded and public history cannot be erased from other computers.
- A suspended creator reads `this creator can't publish right now`.

The daily check covers skills and plugins installed from the Skill Codex. A skill shared as a zip, and a plugin
shared as a folder or from GitHub, are removed by the owner on their own computer.

## When you delete your account

- Deleted: the account, its sign-ins and sessions, uploads that were not published, your comments, your
  stars and the notes you typed into reports.
- Kept: your published skills, which stay public under their MIT licence, and your public name, which stays
  reserved so nobody else can take it.
- For 30 days the service keeps one row per sign-in, as a hash only it can make: no name in words, no
  provider id, no email, no text. It exists so that a mute or a suspension, and the daily limits, cannot be
  shed by deleting an account and signing in again. If you come back within those 30 days you wait until
  they have passed before choosing a new public name.

## The download count

Each skill has one number: how many times it was downloaded. That is all that is stored, one number per
package. Who downloaded is not recorded, and VOOL reports no installs. The count is of downloads served,
so it is not a count of different people.

Browsing and installing need no account. Signing in is for publishing, starring, commenting and reporting.
