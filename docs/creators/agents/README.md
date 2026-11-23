For AI agents asked to make a VOOL skill or plugin for a person. Skills run in VOOL 0.7.7 or newer; every command on these pages, and every plugin, needs VOOL 0.8.0 or newer.

# Build for VOOL: agent entry page

People read [../README.md](../README.md). You read this page, then exactly one task page.

## 1. Check what you have

1. You need a VOOL source checkout at 0.8.0 or newer, with its Python environment ready. Call its folder `VOOL_SRC`.
2. Run `PYTHONPATH="$VOOL_SRC" python -m tools.community_skills --help`. Expected: exit 0, and the command list holds `new`, `try`, `zip`, `stage`.
3. Run `PYTHONPATH="$VOOL_SRC" python -m tools.plugin_check --help`. Expected: exit 0.
4. If a command is missing or an import fails: stop and tell the person which check failed. Do not install anything to get past it.

## 2. Decide: skill or plugin

Apply these tests to the person's request, in order. The first test that matches decides.

1. The result is produced by wording alone (sort, rewrite, summarise, check, plan, answer in a set format): **skill**.
2. The task uses only tools VOOL already has (notes, files, web search, calendar, email): **skill**. Tool names are listed in `docs/guides/skill-tools-and-limits.md` in the checkout.
3. The task needs a program to run (a calculation, a parser, a file converter): **plugin**.
4. The task needs a web service VOOL does not already reach, or a key for one: **plugin**.
5. The task must happen on a timer, at start-up, or without a message from the person: **neither**. A skill never runs by itself and a plugin may not start itself. Stop and tell the person; offer a skill they call by message.
6. The task needs to spend money, sign, approve or change a VOOL setting by itself: **neither**. Stop and tell the person.
7. Still unsure: build a **skill**. It is text, it is checked in seconds, and it can be replaced by a plugin later.

## 3. Do this

- Skill: follow [skill.md](skill.md) from step 1 to the end.
- Plugin: follow [plugin.md](plugin.md) from step 1 to the end.
- A refusal you do not understand: look it up in [errors.md](errors.md).

## 4. Rules you must not break

1. Write a skill as text only: no commands to run, no install steps, no download links, because a skill runs no code and VOOL refuses one that asks a person to.
2. Never ask for, store or print a password, key, recovery phrase, card number or code, because a skill never needs one and a plugin gets its key only from the owner, inside VOOL.
3. Never put VOOL's name, or a word or mark that says the work was checked or chosen (Official, Verified, Tested by, Editor's Choice, a tick, a star), in an id, a name, a title or a maker's name, because those marks are given by the VOOL team on an exact version.
4. Never use another maker's name as the publisher, because a name belongs to whoever earned it.
5. Never write text that changes VOOL's rules, speaks as VOOL, says the person already agreed, or tells VOOL to skip asking, because spending, approvals and permissions stay with VOOL and the owner.
6. List every website a plugin reaches as an exact host in `vool.network`, because the owner agrees to that list before switching the plugin on and VOOL lets the plugin reach nothing else.
7. Keep every program a plugin runs inside its own folder, and never name an installer or downloader as a command, because VOOL starts nothing from outside the folder.
8. Never give a plugin a wallet, a payment, a signing or an approval field or tool name, because a plugin holds no wallet; VOOL pays a listed service itself, inside limits the owner sets.
9. Never switch a skill or plugin on for the person, and never point a test at the person's real VOOL profile, because switching on is the owner's choice. Test in a throwaway profile.
10. Run the acceptance checks on the task page and report their real output, because a skill or plugin that was not checked is not finished.
11. When a check refuses your work, change the work. Never reword to slip past a rule, and never edit VOOL's code or tests to get a pass, because the refusal is the rule working.
12. When the stop list on the task page applies, stop and ask the person, because those choices are theirs.

## 5. Reference

- [errors.md](errors.md): message or code, field, cause, fix.
- [../reference/plugin-refusals.md](../reference/plugin-refusals.md), [../reference/skill-rules.md](../reference/skill-rules.md), [../reference/names-kept-back.md](../reference/names-kept-back.md): full tables, generated from the code.
- Pages for people: [../make-a-skill.md](../make-a-skill.md), [../make-a-plugin.md](../make-a-plugin.md), [../rules-and-why.md](../rules-and-why.md), [../publish.md](../publish.md), [../faq.md](../faq.md).
