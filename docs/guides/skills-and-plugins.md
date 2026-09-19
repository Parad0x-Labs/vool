---
description: Extend VOOL with skills and plugins.
---

# Skills and plugins

A **skill** is a packaged set of instructions for a recurring task. A **plugin** adds new
tools to the runtime.

## Bundled versus externally installed

VOOL ships with a **native skill library** — the skills that come with the app itself — and,
in current builds, the **VOOL Database** plugin. You can also install skills and plugins of
your own.

| | Bundled (ships with VOOL) | Externally installed |
| --- | --- | --- |
| Where it lives | Inside the app | Your plugins folder (set in Settings) |
| Updates | With the app | When you update the pack |
| Can be disabled | Yes — individually | Yes — individually |
| Grants permissions | **No** | **No** |

Two rules hold for both sources:

* **Skills teach; they never grant.** A skill's declared tools are a *narrowing* signal (the
  workflow's tool budget), never a grant of anything. The permission controller does not read
  skills.
* **One inventory.** The catalogue the console shows and the packs the runtime loads are the
  same list. A skill appearing in a list is not proof it can load — each entry carries its
  live state (available, disabled, prerequisite missing, invalid contract) and the reason.

## Availability states

A skill can show as unavailable with a typed reason: a missing prerequisite (for example, the
database skill needs the database plugin enabled), an unavailable capability on this machine,
an invalid contract, or disabled by you. Reasons are shown per entry, and disabling is
per-entry and persistent across restarts.

## Duplicate identities

If two packs declare the same plugin identity (a bundled pack and an installed copy, say),
the bundled one is kept **deterministically** and the dropped copy is reported in the
catalogue — never silently shadowed.

## Installing your own

Install from the in-app catalogue, or point VOOL at a plugins folder of your own. Packs carry
a manifest (`plugin.json`) declaring their tools; a manifest whose permission declarations do
not match its declared behaviour class is refused at load.

{% hint style="danger" %}
A plugin runs with the same access the runtime has. Install only what you trust, and read
what a plugin does before installing it.
{% endhint %}

## Writing a skill

A skill is a folder with an instruction file and any files it references:

```text
my-skill/
├── SKILL.md
└── templates/
```

Draft in-app (`skill.create` stages it without activating anything), validate it, then
install to activate — three separate steps on purpose, so authoring is never an implicit
behaviour change. The catalogue and authoring format are still being finalised for public
release.
