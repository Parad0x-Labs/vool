---
description: Extend VOOL with skills and plugins, from Browse, the Skill Codex or your own folder.
---

# Skills and plugins

A **skill** is a page of written instructions for a task you do again and again. It runs no
code and never gives VOOL new permissions. A **plugin** adds something new: tools VOOL can
call, a model service, an extra scanner, or a browser plugin. Plugins may also bring skills.

Everything here lives on one page: open **Home → Skills & Plugins**. It has four tabs:

| Tab | What it is for |
| --- | --- |
| **Browse** | Skills that come with VOOL, plus a short list of public skills from other makers. |
| **Skill Codex** | Skills and plugins published by their creators, with ratings, badges and team notices. |
| **My add-ons** | What is installed on this computer: **Skills** and **Plugins**, each with its own on and off switch. |
| **Security** | The checks VOOL runs before anything is added, and any scanner plugin you chose. |

## What a skill or plugin can never do

The Skill Codex page says it plainly: *Skills contribute instructions. VOOL native rules, owner
settings, spending limits, currency handling, permissions and approvals always take precedence.*
The same holds for plugins:

* A skill or plugin never overrides VOOL's own rules, your settings or your spending limits.
* It never approves anything for you. Tools still ask where they would ask anyway.
* A plugin can't spend money, use your wallet, read your own model keys or another plugin's keys.
* Nothing starts by itself. A skill is used only when a message asks for it, and a plugin stays
  off until you switch it on.

## Skills from Browse

**Browse** lists VOOL's own skills first: 75 skills written by the VOOL team that come inside
the app, so Browse shows them without going online. 22 of them use VOOL tools (your email,
notes, files, PDFs, wallet and more) on the turns they are picked. Open a skill's **Source &
technical details** to see the tools it uses. Below them are two public skills from other
makers.

How a skill is added depends on who made it.

**Skills made by VOOL**

1. Open the skill and press **Add this skill**.
2. VOOL checks on your computer that the file is exactly the one it shipped, then reads it for
   risky instructions. No key is needed and nothing is sent anywhere.
3. If the check is clean, press **Add and turn on**. If it flagged something, read it; to go
   ahead, tick *I read what VOOL's local check flagged and still want to add this exact
   version.* and press **Add it anyway**. **Reject** throws it away.

If the file does not match, VOOL says *This VOOL skill's file doesn't match the copy that came
with VOOL, so it was not added. Update or reinstall VOOL to repair it.*

**Skills from other makers** (a skill on Browse from someone else, or a GitHub link you paste
into **Search skills, or paste a GitHub link…**)

1. Press **Check compatibility**, then **Download and scan**. VOOL downloads the public skill at
   one fixed version and checks it on this device. If you turned on a scanner plugin, it also
   gets the skill's text and its report is shown beside VOOL's.
2. Read the result. A clean skill is added with **Accept and enable**. If either check found a
   problem, VOOL adds it only after you tick that you accept the risks and press **Accept risks
   and enable this version**.

An installed skill from Browse shows **Checked on this device** on its card.

## The Skill Codex

The Skill Codex is the shared library where anyone can publish skills, and from VOOL 0.8 also
plugins. Press **Browse Skill Codex** to load the list. That one request goes to the public
list; no account, chats, local files or keys are sent. You need no account to browse or install.

Each listing card has **View details**, which opens the full page for that add-on:

* **About**: the creator's own description. VOOL shows it as plain text with headings, lists
  and bold, never as a web page, so it can't run anything or load pictures.
* **What it needs**: the websites it can reach, the service keys it asks for and the tools it adds.
* **Versions and changes**, and **Details**: version, which VOOL versions it works with
  (**Works with**), platforms, package checksum and how many download requests it had.
* **Links**: the creator's home page, support and guide. *The creator wrote these addresses.
  VOOL has not checked the sites.*
* **Ratings and reviews**, and **Notices from the VOOL team**, when there are any.

### Ratings

Only people who installed an add-on from the Skill Codex can rate it. On an installed add-on,
**Rate this add-on** then **Open Skill Codex** opens the website in your browser with proof that
you installed it; you sign in there and give 1 to 5 stars. VOOL sends nothing itself. Until an
add-on has 5 ratings, its page says *Not enough ratings yet* and shows no average. Reviews from
people who installed it show which version they had.

### Badges

Two badges can appear, and only the VOOL team gives them:

* **Tested by VOOL**: *The VOOL team installed and used this exact version.*
* **Editor's Choice**: *Picked by the VOOL team as one of the best of its kind.*

A badge belongs to one exact version, so an update shows none until the team checks it again.
Every badge runs out after a while (Tested by VOOL after about six months, Editor's Choice after
about a year) and the team can withdraw one sooner. A name, a tick or a star in an add-on's own
text never counts as a badge. **Editor's pick** and **Featured** only order the list.

### Installing from the Skill Codex

* **A skill**: open it to see its exact files and text, then press **Install without enabling**
  or **Install and enable**.
* **A plugin**: VOOL shows its review sheet first: what it is, who published it, the websites
  and keys it needs, and every file in the package (files that can run as a program are marked).
  Press **Install and keep disabled**. To switch it on, open **My add-ons → Plugins** and review
  its access (see below).

Before anything is kept, VOOL runs its own safety checks on the downloaded files. If they fail,
you see *This skill didn't pass VOOL's own safety checks, so VOOL didn't keep or install it.*
You can report it with **Report this skill**, which opens the Skill Codex website in your browser.

### When an add-on is removed

**Check once a day for removed add-ons** is on unless you turn it off. While you have at least
one add-on from the Skill Codex, VOOL reads the public list once a day, so an add-on the VOOL
team removed stops being used here. Nothing about you, your chats or your add-ons is sent. If a
check can't finish, VOOL tries again within the hour. A removed add-on's page says *The VOOL
team removed this add-on from the Skill Codex, so VOOL no longer uses it.* An add-on that is only
no longer listed keeps working. Skills shared as a zip and plugins from a folder or GitHub are
not covered: you remove those yourself.

## Turning a skill on or off

Open **My add-ons → Skills**. Each skill shows **On** or **Off** with an **Enable** or
**Disable** button. A skill you turn off is not used in any chat until you turn it on again,
and its files are kept. Skills that come with VOOL sit on the **Built-in skills** card under
**Plugins**; turn them on or off one by one under **Skills**.

You can also ask in any chat: *which skills do I have?* lists them by where they came from
(made by you, built in, added from Browse, added from a file, in your project folder) and
says whether each is on, off or not available here, with the reason.

## Whose skill it is matters

* **A skill you made in VOOL** (see [Make your own skill](make-your-own-skill.md)) can bring the
  tools it lists, and it is picked when your message fits it.
* **A skill VOOL wrote on its own** (for example while reading a web page or someone else's
  skill) gets the tools it lists only after you click Allow on the card that lists those tools
  for that exact version. Auto mode asks for that card too.
* **A skill from anyone else** (Browse, the Skill Codex, GitHub, a zip, a connected server) is
  used only when your message asks for it, and its tool list can only narrow what VOOL already
  offers. When you turn one on, VOOL says which parts of it are ignored.

## Seeing which skill shaped an answer

When a skill's instructions were part of an answer, a short line under it says so, for example
**Skill used:** Browser. It shows while the chat is open; answers loaded later from your
history don't show it.

## Plugins

Open **My add-ons → Plugins**. VOOL comes with two: **Built-in skills** and **VOOL Database**
(small databases of its own on this computer, changed with a backup first). Once you have a browser
plugin, the list is split into **Web browser** and **Tools and services**.

To add one:

1. Choose **Install from a folder** or **Install from public GitHub source** (a project, a folder
   inside it and an exact version), or install it from the Skill Codex.
2. VOOL shows every file and checks them on this device. Press **Install and keep disabled**.
   Nothing runs yet.
3. If it needs a service key, paste it into the plugin's card and press **Save key**. Saving sends
   nothing. **Test connection** sends one request when you press it.
4. Press **Review access and enable**. The sheet lists what it may do, the websites it can reach,
   its keys and any helper program it runs. Press **Accept and enable**, or **Keep disabled**.

**Let chats use plugin tools** turns all plugin tools on or off at once. Model and scanner
plugins work either way.

**Disable** stops a plugin at once, including calls that are running. **Uninstall** switches it
off first, then removes its files; **Also remove saved keys** is ticked unless you untick it.
An update (**Review an update to an installed plugin**) is reviewed again and installs switched
off. If any file of a plugin changes after review, it switches off until you review it again.

### Browser plugins

A browser plugin is an ordinary browser extension packed as a VOOL plugin. It runs in the VOOL
browser window (the Google Chrome or Chromium browser already on your computer, with its own separate
profile) only while it is enabled, and never in a private window. Its access sheet says which
sites it can read and change. VOOL refuses a browser plugin that would update itself from
outside VOOL, replace the start page or search, or ask for saved sign-ins.

**Make a browser plugin** writes a small working plugin into a folder on this computer. Change
it yourself or ask VOOL in chat to write it for you, then review and install it like any other.

## Good to know

* A check describes the exact files it read. It is not a promise that an add-on is safe, and a
  listing is not a recommendation.
* Some plugins that run their own programs stay off on a computer that can't keep them walled off
  from your saved keys; VOOL says so on the plugin. Plugins that only connect a service still work.
* UsePod and Eyebrow are no longer part of VOOL. A key you saved for either in an older VOOL stays
  on this computer and is not used.

For the details of each check, see [Add-on checks](addon-security.md). To build your own, see
[Make your own skill](make-your-own-skill.md) and [Build for VOOL](../creators/README.md).
