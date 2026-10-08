# Two rule features after the port lands: user rules and standing instructions (2026-10-07)

Main's learning-loop build (9adff83 plus 3367bf5 and 32094c6) adds `core/user_rules.py`. This
branch adds `core/standing_instructions.py`. The port does not carry user_rules, so once the port
lands on main both features will exist. Commits were read from main's history and nothing there was
changed.

Where the commits live: 3367bf5 and 32094c6 are on the local branches `rules-owner-20261005` and
`fix/claim-without-tool-honesty` in the rules-audit clone (and its learning-loop copy). They are
not on GitHub main. GitHub main is 9adff83, their parent, and GitHub has neither branch.

## What each stores, and where

| | user rules (3367bf5, 32094c6) | standing instructions (this branch) |
|---|---|---|
| Entry | Explicit commands that name rules: "add to chat rules: never use emojis", "what are my rules?", "remove rule 2", "forget the rule about X", "in this chat, allow ffff" | Ordinary owner sentences with standing wording ("from now on …", "always …", "never …", "by default …"), or a correction that states a lasting preference ("No, use Celsius, not Fahrenheit") |
| Who may write | The command lane checks `request_is_owner_local` | Owner-authored turns only (`turn_is_owner_authored`). An agent turn, a quote, a hypothetical, a one-off ("this time", "in this chat") or another person's habit saves nothing |
| Scope | `global`, `project` (the server-owned chat binding) or `chat`. Bans add up across scopes. Since 32094c6 a narrower scope can lift a broader rule through an exception | Owner plus workspace: the bound project, else the turn's folder, else default |
| Store | SQLite table `user_rules` in the runtime DB. Each row is numbered; removal is a tombstone (`status='removed'`, time and turn); exceptions are rows with `kind='exception'` | JSON file `standing_instructions.json` in the active data dir. At most 24 saved and 12 rendered, 240 characters each. A take-back deletes the entry it names by topic |
| Parsed fields | Banned terms and targets (`reply`, `git`) read from the rule text | The sentence only, kept whole (reason and instruction together) |

## How each reaches the model and the user

- **user rules.** The front door (`turn_frontdoor.py:659`, `observe_rules_turn`) answers add, list
  and remove from the store and claims the turn. A reply guard (`action_honesty_validator.py:765`)
  masks banned words and adds a note. Git doors refuse a commit, tag, branch, merge or push that
  carries a banned word (`execution_gate`, `repoops`).
  **`rules_prompt_block` has no runtime caller at 32094c6.** Only `tests/test_user_rules.py` calls
  it. The module docstring says rules reach the model as a system block, but no prompt path renders
  it, so the model is never told a rule. Bans are enforced only after the fact, by masking and git
  refusal. A rule that bans no word ("always answer in British English") is saved and listed and
  then has no effect at all.
- **standing instructions.** `observe_turn` runs in `operator_profile_turn` before the profile reads
  the turn. `standing_block` is rendered in `prompt_normalizer` after the runtime-truth block, and
  in the minimal plain-task profile too, together with the profile's own hydration lines. It holds
  stored text only, with no retrieval.

## Should one absorb the other?

Yes. **user_rules should own storage, scope, listing and removal. standing_instructions should
become an intake into it.** The reasons:

- user_rules already has what a durable rule needs and this branch lacks: numbered rows, tombstoned
  removal, a listing command, chat/project/global scopes with exceptions, and enforcement in replies
  and git.
- standing_instructions has what user_rules lacks: it recognises a rule the user never called a rule
  ("from now on give me distances in kilometres"), it refuses agent, quoted, hypothetical, one-off
  and third-party sentences, and it has a working prompt path, including the plain-task profile.
- Two stores would let one sentence live in two places, be listed in one and removed from only one.

## Proposed shape after landing

1. One store, `user_rules`. A standing instruction with a project binding is saved as a
   `scope='project'` row. **Chosen default for folder-only workspaces:** add a fourth scope,
   `workspace`, keyed by the folder's realpath. That is the same key `standing_instructions.workspace_key`
   uses today. `chat` would be too narrow and `global` would leak across workspaces. The choice is
   reversible.
2. One prompt block. Wire `rules_prompt_block` into the prompt at the place `standing_block` uses now
   (after runtime truth, the plain-task profile included), and drop `standing_block`'s own header so
   the model sees one list.
3. Take-back maps to tombstoned removal. "Forget the kilometres rule" and "you can stop adding the
   summary" become `remove_rules` on the matching row, so the removal is still answerable.
4. Intake guards stay where they are. The owner-only, quote, hypothetical, one-off and third-party
   checks from this branch run before anything is written.

## What an "add … rule" request should do after landing, so nothing already saved is lost

- "add … rule", "what are my rules", "remove rule N" and exceptions keep going to the user_rules
  lane unchanged. Rules users saved on main keep their numbers and scopes.
- On first start after landing, migrate `standing_instructions.json` into `user_rules` once. Each
  entry becomes an active row (created turn `migrated:standing-instructions`), skipping any whose
  normalised text already exists in the same scope. Keep the JSON file read-only as the migration's
  source and do not delete it, so a rollback loses nothing. Record the migration count in the
  receipt.
- After migration, "what are my rules?" lists both origins in one numbered list, and "remove rule N"
  works on either.

## Defects to fix during reconciliation

- **D1: at 32094c6, `rules_prompt_block` has no runtime caller.** A rule that bans no word is saved and
  listed, and then has no effect. Fix: render the block in the prompt (item 2 above).
- **D2: "forget the rule about X" is caught by the rules lane.** A standing instruction on that topic
  survives the take-back. Fix: once there is one store (item 1), the lane's `remove_about` reaches
  migrated and new standing rows too.

Implementation is on hold until the landing plan puts main's user_rules and the port in one tree.

## Collisions that exist today if both land unchanged

- **"forget the rule about kilometres"** is claimed by the user_rules lane (`parse_rule_command`
  returns `remove_about`, checked against 32094c6). If "kilometres" was saved as a standing
  instruction, the lane answers from its own store (no match) and claims the turn, and the standing
  instruction stays. "Forget the kilometres rule." without "about" is not claimed
  (`parse_rule_command` returns None), so this branch's take-back handles it.
- **"add to chat rules: never use emojis"** goes to the user_rules lane. It is not checked whether
  `observe_turn` also runs on that turn, because that depends on where the front door returns
  relative to `operator_profile_turn`. If it does run, the sentence lands in both stores. Check this
  on the landed tree before relying on either listing.
- **A no-word rule** ("always answer in British English") saved through "add … rule" is never
  shown to the model (see above). The same sentence said plainly is saved as a standing instruction
  and is shown. Users will see one phrasing work and the other do nothing until the prompt block is
  wired.

Status: analysis only. Nothing here is implemented on this branch.
