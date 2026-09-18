# VOOL Local Desktop
## Complete Product, UX, Architecture, Security, Plugin, Testing, and Delivery Blueprint

**Target platforms:** Windows and macOS  
**Product:** VOOL Local  
**Document purpose:** Turn the current chat-first closed-test UI into a dependable local-first desktop agent that can operate across the user’s machine, repositories, applications, local models, and optional paid cloud models without losing control, privacy, cost visibility, or verifiability.

---

# 1. The real product

VOOL must not become another chat wrapper with a model dropdown.

It must become the user’s **local AI operating layer**:

- one desktop application;
- one persistent workspace system;
- one permission model;
- one tool and plugin system;
- one local memory layer;
- one task engine;
- one preview environment;
- one honest activity ledger;
- many models underneath it.

The user should be able to say:

> “Find the largest files on C:, inspect this repository, fix the issue, run the tests, show me the result, and prepare a commit.”

VOOL should then:

1. understand that this is a local-machine task;
2. use the local filesystem tool rather than web search;
3. inspect permissions and excluded locations;
4. explain any required permission expansion;
5. create a plan;
6. execute through real tools;
7. show live progress and previews;
8. pause safely when blocked;
9. use a paid model only for the exact part that local models cannot perform reliably;
10. estimate and obtain approval for the cost;
11. return automatically to the free local model;
12. verify the result;
13. show the changed files, commands, tests, cost, and signed receipt;
14. allow one-click undo or Git rollback.

That is the product.

---

# 2. What the screenshots reveal

The current interface is a reasonable closed-test shell, but it exposes the fact that the underlying product contracts are not yet complete.

## 2.1 Current visible strengths

- Clean, lightweight dark interface.
- Familiar chat layout.
- Conversation history is easy to understand.
- The composer is uncomplicated.
- The application already feels like a native standalone product rather than a website.
- The turquoise accent is recognizable and can become part of the VOOL identity.

## 2.2 Current visible product gaps

The current window lacks:

- a useful operating mode selector;
- a visible model lane;
- local-versus-paid state;
- cost state;
- workspace identity;
- permissions;
- settings;
- active tool state;
- task plan;
- changes and diff view;
- terminal output;
- tests;
- preview;
- file context;
- background job state;
- undo and checkpoints;
- plugin and skill access;
- model escalation controls;
- notifications;
- Git and GitHub status;
- failure recovery;
- receipts presented in a useful place.

The top-right `Web0` and `Trace` entries are currently occupying the most valuable control area while the essential controls are missing.

## 2.3 The most important problem shown in the screenshot

The screenshot contains requests such as:

- “find out what are the biggest files on drive C”
- “find me dark null folder on this PC”
- “WEBSITE V3 30122025 try to find this folder”

The application responds with generic search-style text or says that it completed a search result. This is not mainly a wording failure. It is a **task classification and tool-routing failure**.

VOOL is failing to distinguish:

- public web lookup;
- local filesystem search;
- semantic memory lookup;
- workspace file lookup;
- operating-system inventory;
- repository search.

A new UI will not solve this by itself. The product needs a typed capability system and a deterministic task router. A local-file request must never silently degrade into a web search.

## 2.4 Current activity wording is too vague

“Completed 1 real tool step” is technically defensive, but not useful.

The user needs to see:

- **Tool:** Local file search
- **Scope:** `C:\`, excluding Windows, Program Files, and configured exclusions
- **Result:** 3 matching folders
- **Duration:** 4.2 seconds
- **Permission:** Workspace read permission
- **Files changed:** None

“Real tool step” should remain internal terminology for honesty tests, not become the primary user-facing status.

---

# 3. Non-negotiable product principles

## 3.1 Local-first, not local-only

Everyday work should use local models and local tools by default.

Cloud use must be:

- optional;
- disabled until connected;
- scoped;
- visible;
- budgeted;
- reversible;
- minimized;
- recorded;
- followed by an automatic return to local execution.

The cloud is an accelerator and specialist, not the owner of the session.

## 3.2 Models are workers, not the product

Users should not need to understand 400 model names to use VOOL.

The primary product should offer a VOOL-managed lane:

- **Local Fast**
- **Local Daily**
- **Local Heavy**
- **Paid Specialist**
- **VOOL Auto**

Advanced users can pin exact models. Normal users should see capability, expected quality, speed, privacy, and price.

## 3.3 Capability must be explicit

VOOL must know the difference between:

- answer a question;
- inspect files;
- edit files;
- run a command;
- use a browser;
- query GitHub;
- create a commit;
- access the network;
- install a package;
- use a secret;
- call a paid model;
- delete data;
- publish or send something.

A model’s text cannot directly perform these actions. It can only propose structured actions to the tool broker.

## 3.4 “Done” means verified

The completion standard varies by task:

- code: build and tests;
- website: preview and console checks;
- document: rendered preview and file validation;
- filesystem task: result count and path verification;
- Git task: diff and repository status;
- deployment: endpoint verification;
- email or message: explicit send confirmation and delivery tool result;
- paid-model task: usage and cost recorded;
- destructive task: post-action verification.

## 3.5 No invisible spending

VOOL must never spend because a model autonomously decided it wanted a stronger model.

The policy engine, not the model, decides whether cloud escalation is allowed.

## 3.6 No fake progress

The thinking animation must be entertaining, but it cannot imply certainty that does not exist.

The animation should visualize real task events and work cycles, not a fabricated percentage.

## 3.7 Windows and macOS are equal products

Every feature definition must include:

- Windows behavior;
- macOS behavior;
- permission differences;
- path differences;
- shell differences;
- secret storage;
- packaging;
- update behavior;
- filesystem edge cases.

“Works on the developer’s Mac” is not a release gate.

---

# 4. New information architecture

The application should have five permanent regions.

## 4.1 Left navigation rail

The left side should evolve from “Chats” into the persistent operating structure.

### Primary entries

- New Task
- Search
- Workspaces
- Sessions
- Scheduled
- Skills
- Activity
- Settings

### Workspace area

A workspace is more than a chat folder. It contains:

- approved root folders;
- workspace rules;
- memory;
- model preference;
- Git repositories;
- task history;
- checkpoints;
- connected tools;
- budgets;
- active background jobs.

Example:

- Parad0x Labs
  - VOOL Local
  - VOOL
  - Dark Null
  - Website V3
- Personal
- Downloads cleanup
- Temporary session

### Session history

Chats become sessions within a workspace.

Each session shows:

- title;
- current state;
- last model lane;
- files changed;
- cost;
- active or completed job;
- warning badge if unfinished or unverified.

### Pinned items

Pinning should support:

- workspaces;
- repositories;
- sessions;
- skills;
- files;
- recurring tasks.

## 4.2 Top command bar

The top bar should become the control plane.

From left to right:

1. workspace name and repository branch;
2. task status;
3. model lane;
4. operating mode;
5. local/cloud status;
6. budget indicator when cloud is enabled;
7. preview toggle;
8. activity/receipts;
9. settings.

### Example compact layout

`VOOL Local / parad0x-labs/vool-local / main`

`Local Daily ▾`  `Build ▾`  `€0.00 today`  `Preview`  `Activity`  `⚙`

Do not keep `Web0` and `Trace` in this position.

## 4.3 Main conversation and task canvas

The center should retain conversation, but responses must support rich task blocks:

- plan;
- permission request;
- tool action;
- file change;
- test result;
- preview;
- model escalation proposal;
- checkpoint;
- final result.

The conversation should explain what is happening without dumping raw internal reasoning.

## 4.4 Right preview and inspection panel

Every task-capable window must support a side panel.

Tabs:

- Preview
- Changes
- Files
- Terminal
- Tests
- Plan
- Browser
- Activity
- Receipts

The panel can be:

- hidden;
- docked right;
- docked bottom;
- detached into a separate window;
- expanded full screen.

VOOL should remember panel layout per workspace.

## 4.5 Bottom composer and task controls

The composer requires:

- attachment button;
- current context button;
- command palette;
- microphone where supported;
- task scope indicator;
- send/run button;
- stop button while running;
- queue button for background execution.

Under or inside the composer, show compact context chips:

- `Workspace: VOOL Local`
- `Read/write: repo only`
- `Model: Local Daily`
- `Network: ask`
- `Cloud: off`

---

# 5. Useful operating modes in the top-right

The mode selector should control behavior, permissions, and completion criteria. It must not merely change prompt wording.

## 5.1 Ask

Purpose:

- answer;
- explain;
- search allowed sources;
- no file changes;
- no shell writes;
- no Git mutations.

Default permissions:

- read approved context;
- local memory read;
- optional public web access if allowed.

Completion:

- answer with sources or inspected local evidence.

## 5.2 Plan

Purpose:

- inspect;
- analyze;
- propose an implementation or action plan;
- make no changes.

Default permissions:

- read-only workspace;
- read-only Git status;
- safe diagnostic commands;
- no edits.

Completion:

- executable plan;
- affected files;
- risks;
- test strategy;
- estimated time and cloud cost.

## 5.3 Build

Purpose:

- implement;
- edit;
- run;
- test;
- fix;
- repeat.

Default permissions:

- workspace-scoped read/write;
- approved commands;
- network and installs according to policy;
- Git changes allowed;
- push or publish requires separate permission.

Completion:

- requested result;
- tests;
- preview where relevant;
- diff;
- verification;
- checkpoint.

## 5.4 Review

Purpose:

- audit code, documents, configuration, outputs, or another agent’s work.

Default permissions:

- read-only;
- tests and diagnostics allowed;
- no changes unless user selects “Fix findings.”

Completion:

- findings ranked by severity;
- evidence;
- reproduction;
- remediation;
- confidence and unresolved areas.

## 5.5 Automate

Purpose:

- create a repeatable or long-running workflow.

Default permissions:

- selected tools only;
- explicit schedule;
- background execution;
- strict spend and notification limits.

Completion:

- automation definition;
- dry run;
- permissions summary;
- trigger;
- stop conditions;
- rollback.

## 5.6 Optional advanced mode: Operate

This may be introduced later for broad machine operations.

It should not be enabled by default because it can include:

- multiple applications;
- system settings;
- file moves across workspaces;
- browser sessions;
- package management.

It requires a visibly broader trust profile.

## 5.7 Mode switching rules

- Switching into a more powerful mode shows the permission delta.
- A mode change never silently expands access.
- Modes can be locked per workspace.
- Tasks remember their mode.
- A task can temporarily request a narrower or broader sub-permission.
- The user can choose “Allow once,” “Allow this session,” “Always allow in this workspace,” or “Deny.”

---

# 6. Model selector: FREE first, PAID second

The model selector should be a structured panel, not a flat list.

## 6.1 Default top entry

### VOOL Auto

Subtitle:

> Uses local models by default. Suggests a paid specialist only when the expected improvement justifies the cost.

The card should show:

- privacy state;
- likely speed;
- current local model;
- cloud policy;
- today’s cost;
- remaining daily cap.

## 6.2 FREE — On this machine

Models should be grouped by detected role rather than only by technical name.

### Local Fast

- instant chat;
- classification;
- basic file triage;
- simple transformations;
- low memory use.

### Local Daily

- normal conversation;
- coding assistance;
- tool planning;
- document work;
- moderate context.

### Local Heavy

- deeper reasoning;
- larger repository context;
- slower;
- higher RAM or VRAM use.

Each card shows:

- exact model;
- downloaded or not;
- size;
- RAM/VRAM requirement;
- observed tokens per second on this machine;
- context capacity under the current configuration;
- tool-calling capability;
- vision support;
- coding rating from VOOL’s own verified local benchmark;
- warm/cold state;
- estimated battery and memory impact.

Do not label a local model “best” based on internet reputation. Use machine-specific tests.

## 6.3 FREE — Optional OpenRouter free models

Keep these separate from local models because they are not private or guaranteed.

Label clearly:

- Free cloud
- Data leaves device
- Provider availability may change
- Rate limits apply
- Not equivalent to local privacy

Do not mix them into the “Free Local” section.

## 6.4 PAID — OpenRouter

Group paid models by task suitability:

- Best for coding
- Best for deep review
- Best for long context
- Best for vision
- Best value
- Fast specialist
- User favorites
- Recently used

Each model card should show:

- provider and model;
- input and output price;
- context;
- tool support;
- vision or media support;
- reliability status;
- historical success rate inside VOOL;
- typical latency from the user’s own history;
- estimated cost for the current task;
- whether VOOL recommends it.

## 6.5 Manual and automatic selection

The user can choose:

- Auto;
- Local only;
- Ask before every paid escalation;
- Allow automatic escalation under a per-action cap;
- Pin exact model for this task;
- Pin model for this workspace.

The default should be:

> Local first; ask before paid use.

## 6.6 Never confuse model choice with tool choice

A stronger model does not fix a missing local filesystem tool.

Before proposing paid escalation, VOOL must classify the failure:

- missing permission;
- missing tool;
- tool malfunction;
- insufficient context;
- local model capability;
- provider outage;
- ambiguous request.

Paid escalation is allowed only for capability problems that a stronger model can plausibly solve.

---

# 7. OpenRouter onboarding in three understandable steps

VOOL should offer a guided connection flow.

## Step 1 — Connect OpenRouter

Primary button:

`Connect OpenRouter`

Preferred path:

- launch secure OAuth PKCE flow in the system browser;
- create a dedicated user-controlled key for VOOL;
- label it clearly;
- configure an initial credit limit;
- return to VOOL;
- store the resulting key in the OS secret vault.

Fallback:

- paste a dedicated OpenRouter API key manually.

Do not ask the user to paste a general management key during normal onboarding.

## Step 2 — Add credits

After connection:

- check the dedicated key;
- show whether it has usable credit;
- offer `Add credits in OpenRouter`;
- open the official credit page in the system browser;
- re-check when the user returns.

VOOL does not need to process payments.

## Step 3 — Choose a safety limit

Simple presets:

- Careful: $1/day, ask above $0.05 per action
- Normal: $3/day, ask above $0.20 per action
- Power: $10/day, ask above $1.00 per action
- Custom

Also configure:

- monthly cap;
- per-task cap;
- per-request cap;
- auto-escalation threshold;
- low-balance warning;
- hard stop;
- whether background jobs may spend.

## 7.1 What balance VOOL can show safely

There are several different concepts:

- total OpenRouter account credits;
- total account usage;
- dedicated VOOL key limit;
- remaining key limit;
- VOOL-recorded spend today;
- pending or in-flight estimated spend.

The default UI should emphasize the dedicated VOOL key:

- `VOOL key remaining`
- `Spent today`
- `Daily VOOL cap`
- `Estimated days remaining`

This provides least privilege.

A total account balance view may require stronger account-management access. It should be an optional advanced connection, not part of basic setup.

## 7.2 Key storage

Windows:

- Windows Credential Manager or DPAPI-backed encrypted storage;
- never plain `.env`;
- never shown after initial save;
- clipboard cleared after paste where practical.

macOS:

- Keychain;
- application-scoped access;
- never plain preferences or logs.

Both:

- redact secrets from logs;
- prohibit plugins from reading secrets directly;
- pass secret handles to approved connectors;
- allow one-click revoke and disconnect;
- detect exposed-looking keys in chat and warn;
- never include the key in model context.

---

# 8. Cost estimation and remaining-use prediction

The cost system should be honest about uncertainty.

## 8.1 Preflight estimate

Before a paid action, show:

> **Recommended specialist:** [model]  
> **Why:** local model failed repository-wide dependency reasoning twice  
> **Expected cost:** $0.04–$0.11  
> **Upper hard limit:** $0.15  
> **Data sent:** 9 selected files, 42 KB; secrets excluded  
> **Afterward:** return to Local Daily for edits and tests

Actions:

- Use recommended
- Choose another model
- Continue locally
- Cancel

## 8.2 Estimation inputs

- model input price;
- model output price;
- selected context tokens;
- system and tool schemas;
- predicted response length;
- likely tool loops;
- cache behavior where known;
- historical retry rate;
- previous tasks of the same type;
- verification pass;
- maximum output limit;
- provider price timestamp.

## 8.3 Display ranges, not fake precision

Good:

- `$0.03–$0.07 likely`
- `$0.10 hard cap`
- `medium confidence`

Bad:

- `$0.043721 estimated`

## 8.4 Daily-use projection

VOOL can calculate locally:

- average spend per active day;
- median spend;
- seven-day and thirty-day trends;
- average paid escalations per task;
- remaining days at current usage;
- expensive task categories;
- local substitution savings.

Example:

> Balance available to VOOL key: **$8.40**  
> Typical active day: **$0.46**  
> Estimated remaining: **12–24 active days**  
> Biggest cost source: **repository-wide code reviews**

Always show that this is a projection.

## 8.5 Cost ledger

Every paid call records:

- session;
- task;
- model;
- provider;
- estimated cost;
- actual usage and cost;
- context size;
- retry status;
- reason for escalation;
- whether output was accepted;
- whether the task returned to local mode.

This ledger remains local.

## 8.6 Cost-aware model recommendation

Recommendation score should combine:

- capability fit;
- user’s verified success history;
- estimated acceptance probability;
- expected cost;
- latency;
- context needs;
- tool support;
- privacy policy;
- current provider availability.

Optimize for:

> **Expected accepted result per dollar**, not cheapest raw token price.

---

# 9. The local → paid specialist → local return loop

This is a defining VOOL feature.

## 9.1 Escalation triggers

A paid-model suggestion may be triggered by:

- repeated local reasoning failure;
- context beyond the safe local limit;
- task requires a capability not present locally;
- review risk exceeds configured threshold;
- user requests premium quality;
- local output fails objective verification;
- local model cannot produce valid tool plans after bounded retries.

## 9.2 Escalation is a subtask, not a session takeover

Example:

1. Local Daily inspects the repo.
2. Local tools build a compact context capsule.
3. Paid specialist receives only the relevant files and question.
4. Paid specialist returns a plan or patch proposal.
5. VOOL validates the response.
6. Local Daily performs edits.
7. Local tools run tests.
8. Local Heavy handles ordinary repair loops.
9. Paid specialist is called again only if a new explicit gate is crossed.
10. VOOL returns to Local Daily and releases cloud context.

## 9.3 Visible state

The top model chip should temporarily show:

`Paid Specialist · $0.06 so far`

When finished:

`Back to Local Daily`

The activity panel records the handoff.

## 9.4 Manual model mode

When the user pins local-only mode, VOOL may suggest but cannot switch.

When the user pins a paid model, VOOL should still offer to return local after the expensive reasoning phase:

> “The architecture decision is complete. The remaining edits and tests can run locally at no additional model cost.”

## 9.5 Automatic paid mode

Automatic escalation requires:

- user-enabled policy;
- per-action limit;
- daily limit;
- model allowlist;
- data-scope policy;
- no secrets;
- no excluded files;
- high-confidence capability match.

---

# 10. Permissions equivalent to serious coding agents—but clearer

The permission system needs four scopes:

1. global;
2. workspace;
3. session;
4. one action.

## 10.1 Permission categories

### Filesystem

- read file;
- list folder;
- search folder;
- write file;
- create file;
- move;
- rename;
- delete;
- access outside workspace;
- access removable drive;
- follow symlink;
- inspect hidden files.

### Terminal

- safe read-only commands;
- build commands;
- test commands;
- package manager;
- network commands;
- process control;
- elevated commands;
- destructive commands;
- arbitrary shell.

### Network

- no network;
- approved domains;
- ask per domain;
- public web;
- local network;
- localhost;
- arbitrary outbound network;
- inbound listener.

### Git

- status and diff;
- create branch;
- stage;
- commit;
- reset;
- rebase;
- merge;
- push;
- force push;
- delete branch;
- tag;
- release.

### GitHub

- public repository read;
- private repository read;
- issues;
- pull requests;
- workflows;
- releases;
- repository write;
- organization access.

### Secrets

- use named secret through connector;
- expose secret to process;
- modify secret;
- create secret;
- reveal secret to user;
- never expose to model.

### Applications and OS

- clipboard;
- notifications;
- microphone;
- camera;
- browser control;
- open applications;
- accessibility automation;
- system settings.

### Cloud models

- send text;
- send code;
- send files;
- send images;
- spend threshold;
- background spending;
- model allowlist.

## 10.2 Permission decisions

Each prompt offers:

- Allow once
- Allow for this session
- Always allow in this workspace
- Edit scope
- Deny

For dangerous actions:

- no “Always allow” option by default;
- require explicit path or command;
- show impact preview;
- require second confirmation for irreversible actions.

## 10.3 Permission preview

Bad:

> VOOL wants terminal access.

Good:

> VOOL wants to run `npm test` inside  
> `C:\Users\...\vool-local`  
> This may read project files and create temporary test output.  
> Network access: blocked.  
> Maximum runtime: 10 minutes.

## 10.4 Workspace trust profiles

Presets:

### Read-only

For reviews and unfamiliar repositories.

### Standard project

Read/write inside roots, common test/build commands, ask for network and installs.

### Trusted development

Broader commands and Git operations, but still ask for push, delete, secrets, and elevation.

### Custom

Full policy editor.

## 10.5 Filesystem edge cases

The policy engine must defend against:

- `..` path traversal;
- symlink escape;
- Windows junction escape;
- case-insensitive path tricks;
- UNC paths;
- network drives;
- removable drives;
- macOS aliases;
- mounted DMGs;
- cloud-synced placeholder files;
- reserved Windows device names;
- alternate data streams;
- excessively long paths;
- hidden system folders.

Permissions apply to the resolved canonical target, not merely the user-visible path.

---

# 11. Working folders, excluded drives, and workspace setup

Settings must provide a clear Workspace & Storage section.

## 11.1 Approved working roots

Users can add:

- folder;
- repository;
- drive;
- temporary workspace.

Each root shows:

- read/write status;
- Git status;
- indexing status;
- memory status;
- excluded patterns;
- last used;
- size.

## 11.2 Exclusions

Global exclusions:

- system folders;
- browser profiles;
- password stores;
- wallets;
- SSH keys;
- cloud credential folders;
- `.env` by default;
- package caches;
- build output;
- node modules;
- virtual environments;
- recycle bin/trash;
- Time Machine;
- Windows recovery data.

Workspace exclusions:

- user-selected paths;
- glob patterns;
- file types;
- files over a size threshold;
- generated folders.

## 11.3 Sensitive files

VOOL should detect likely secrets and classify them:

- never index content;
- show filename only;
- permit tool use through handles;
- require explicit reveal permission.

## 11.4 Indexing

Indexing must be:

- optional;
- local;
- incremental;
- pauseable;
- bandwidth and battery aware;
- scope visible;
- deletable.

Show:

- files indexed;
- last update;
- errors;
- excluded files;
- index storage;
- delete index button.

---

# 12. Rules: global, workspace, repository, and session

Rules should be first-class, editable, and transparent.

## 12.1 Rule levels

1. built-in safety and integrity rules;
2. organization policy;
3. user global rules;
4. workspace rules;
5. repository rules;
6. session instructions;
7. task request.

Higher integrity rules cannot be overridden by lower levels.

## 12.2 Rule files

Suggested:

- global: VOOL settings database and exportable `global-rules.md`;
- workspace: `.vool/workspace.md`;
- repository: `VOOL.md` or `.vool/rules.md`;
- path-specific: `.vool/rules/*.md`.

## 12.3 Rule viewer

The context panel must show:

- active rules;
- source;
- scope;
- conflicts;
- last modified;
- whether a rule is user-authored or plugin-provided.

## 12.4 Examples

- Never push directly to main.
- Use `pytest`, not ad hoc scripts.
- Do not modify migrations.
- Do not access wallet keys.
- Use British English for documentation.
- Prefer local models for routine edits.
- Paid code review is allowed up to $0.20 per task.

## 12.5 Conflict handling

VOOL should not guess silently.

Example:

> Global rule says “Never install packages.”  
> Workspace rule says “Install missing dev dependencies automatically.”  
> Global rule wins. The install is blocked.

---

# 13. Git and GitHub must be built-in capabilities

## 13.1 Local Git integration

Always-visible repository state:

- branch;
- clean/dirty;
- changed files;
- ahead/behind;
- merge conflict;
- detached HEAD;
- untracked files.

## 13.2 Checkpoints

Before meaningful changes:

- create internal patch checkpoint;
- optionally create Git branch;
- optionally commit work-in-progress;
- record base SHA.

Checkpoint actions:

- View
- Restore selected files
- Restore all
- Compare
- Create branch from checkpoint

## 13.3 Git operation policy

Safe by default:

- status;
- diff;
- log;
- blame;
- branch listing.

Ask:

- commit;
- reset;
- checkout with overwrites;
- merge;
- rebase;
- push.

Strong confirmation:

- force push;
- delete remote branch;
- rewrite history;
- remove untracked files.

## 13.4 GitHub connection

Preferred integration:

- use installed `gh` CLI where available;
- otherwise OAuth through the system browser;
- request minimal scopes;
- show exactly which organizations and repositories are accessible.

Capabilities:

- clone;
- inspect issues;
- create branch;
- draft PR;
- review PR;
- read actions;
- diagnose failed workflow;
- create release draft;
- manage labels;
- never merge or publish without explicit permission unless a workspace policy allows it.

## 13.5 PR preview

Right panel should show:

- title;
- description;
- files;
- tests;
- linked issue;
- reviewers;
- target branch;
- unresolved warnings;
- final `Create draft PR` button.

---

# 14. Settings architecture

Settings should be a full page, searchable, and exportable.

## 14.1 General

- launch at login;
- reopen previous workspace;
- default workspace;
- default mode;
- language;
- update channel;
- crash recovery;
- telemetry choice;
- notifications.

## 14.2 Appearance

- system, light, dark;
- accent colour;
- interface density;
- font size;
- code font;
- sidebar width;
- preview position;
- animation level;
- reduced motion;
- sound;
- high contrast.

## 14.3 Workspaces & Storage

- approved roots;
- exclusions;
- indexing;
- cache size;
- generated files;
- cleanup;
- backup and export.

## 14.4 Local Models

- installed models;
- download;
- remove;
- update;
- benchmark;
- assign Fast/Daily/Heavy role;
- RAM/VRAM limits;
- context allocation;
- concurrency;
- idle unload;
- battery policy.

## 14.5 Paid Models & OpenRouter

- connection status;
- dedicated key;
- key remaining;
- daily/monthly caps;
- per-task cap;
- ask threshold;
- model allowlist;
- automatic escalation;
- low-balance warnings;
- usage history;
- disconnect and revoke.

## 14.6 Permissions

- global trust profile;
- workspace overrides;
- folder access;
- terminal rules;
- network rules;
- Git;
- GitHub;
- applications;
- secrets;
- cloud-data policy;
- reset decisions.

## 14.7 Tools & Integrations

- shell;
- Git;
- GitHub;
- browser;
- local search;
- document tools;
- image/video tools;
- calendars;
- email;
- databases;
- developer tools;
- connection health.

## 14.8 Skills & Plugins

- installed;
- available;
- updates;
- permissions;
- enabled workspaces;
- logs;
- quarantine;
- developer mode.

## 14.9 Memory & Privacy

- memory enabled;
- project memory;
- personal memory;
- private sessions;
- retained history;
- delete memory;
- export;
- cloud-sharing policy;
- sensitive-data handling.

## 14.10 Notifications & Background Work

- task completed;
- permission needed;
- test failed;
- spend threshold;
- scheduled task;
- app idle;
- system notification;
- sound.

## 14.11 Accessibility

- keyboard navigation;
- screen reader labels;
- reduce animation;
- high contrast;
- larger controls;
- captions;
- focus indicators.

## 14.12 Advanced

- logs;
- developer console;
- daemon state;
- IPC;
- database repair;
- model diagnostics;
- network diagnostics;
- reset UI;
- safe mode.

## 14.13 Labs

This is where Web0 belongs.

Web0 should be:

- off by default;
- absent from the top bar while off;
- described as an optional experimental integration;
- shown in relevant menus only after activation.

`Trace` should be removed from the main header.

The useful replacement is `Activity`, available in the side panel. Developer-grade trace data remains in Advanced or Diagnostics.

---

# 15. Preview system for every task

The preview panel is not optional decoration. It is how users trust the work.

## 15.1 Supported preview types

- text;
- Markdown;
- code;
- diff;
- HTML;
- local web app;
- image;
- SVG;
- PDF;
- document;
- spreadsheet;
- slides;
- audio;
- video;
- JSON;
- logs;
- terminal;
- test report.

## 15.2 Live web preview

For web projects:

- detect framework;
- offer to start dev server;
- bind to localhost only;
- choose available port;
- display inside sandboxed webview;
- show console errors;
- show network errors;
- support desktop/tablet/mobile viewport;
- refresh after edits;
- stop server when task ends unless pinned.

## 15.3 File preview safety

- never execute unknown active content by default;
- sandbox HTML;
- disable external network unless allowed;
- sanitize SVG;
- limit file size;
- warn on macros;
- do not auto-open executables;
- render PDFs and documents through controlled preview services.

## 15.4 Preview provenance

Every preview should indicate:

- source path;
- generated or existing;
- last modified;
- unsaved status;
- current checkpoint;
- whether it reflects latest changes.

## 15.5 Compare mode

Support:

- before/after;
- side-by-side;
- image overlay;
- text diff;
- rendered-page comparison;
- test baseline comparison.

## 15.6 Detachable preview

Developers with two monitors should be able to move preview into another native window.

---

# 16. The VOOL “snake eats dots” thinking animation

The idea is strong because it can become memorable and native to VOOL.

It needs a real state model so it does not become an animated lie.

## 16.1 Visual concept

- A small VOOL snake or angular loop travels around a compact track.
- Work events appear as dots.
- The snake consumes dots as tasks complete.
- When the loop fills, it contracts or “implodes.”
- A new loop begins for the next execution cycle.
- A complex task can have multiple loops: inspect, plan, edit, test, verify.

## 16.2 Do not represent fake percentages

The loop should not mean “82% complete” unless VOOL has a measurable bounded job.

It should represent:

- active work cycle;
- completed tool events;
- current stage;
- elapsed time;
- next expected event.

## 16.3 State mapping

### Thinking

Slow orbit with sparse dots.

Text:

`Understanding the task`

### Planning

Dots form around corners.

Text:

`Building a 5-step plan`

### Tool execution

A dot appears for each queued tool action.

Text:

`Searching the workspace · 14,280 files checked`

### Editing

Snake consumes file-shaped dots.

Text:

`Editing 3 files`

### Testing

Dots become test nodes.

Text:

`Running tests · 38 passed · 1 running`

### Waiting for permission

Animation pauses and opens at one side.

Text:

`Waiting for permission`

### Paid specialist

A subtle cloud marker appears; show live cost.

Text:

`Paid review · $0.04 so far`

### Retry

The loop sheds a failed dot and starts a smaller correction loop.

Text:

`Test failed · applying repair 1 of 3`

### Complete

The loop closes into the VOOL icon.

Text:

`Verified`

### Failed safely

The loop opens and remains stable rather than exploding.

Text:

`Stopped safely · no unverified changes applied`

## 16.4 Implosion behavior

The “implosion” should be:

- fast;
- quiet by default;
- visually satisfying;
- not alarming;
- not resemble a crash;
- disabled under reduced motion.

The full loop compresses into the icon, pulses once, and expands into the next stage.

## 16.5 Time indication

Show:

- elapsed time;
- current stage;
- bounded estimate only when reliable.

Examples:

- `Testing · 1m 14s elapsed`
- `Indexing · 42%` because indexing has a measurable total
- `Reasoning · usually 20–60s` rather than a false countdown
- `Waiting for OpenRouter · 12s`

## 16.6 Performance

- render at low GPU cost;
- pause when window is hidden;
- reduce frame rate on battery;
- no memory leak during multi-hour jobs;
- support high-DPI;
- support light/dark;
- screen-reader status text;
- reduced-motion replacement uses a static icon and textual stage updates.

## 16.7 Placement

Use it in:

- assistant response header;
- top task status;
- background task list;
- minimized tray status.

Do not let a large animation consume the conversation.

---

# 17. Skills and plugins are mandatory

A monolithic application will create exactly the nuclear blast radius you described.

The core must be small. Capabilities should be modular.

## 17.1 Core owns

- task state machine;
- permission engine;
- tool broker;
- model gateway;
- local memory;
- workspace management;
- audit ledger;
- plugin supervisor;
- updates;
- UI shell;
- secret broker.

## 17.2 Plugins own

- GitHub;
- browser automation;
- document formats;
- image tools;
- video tools;
- databases;
- calendars;
- email;
- cloud providers;
- specialist workflows;
- VOOL;
- Web0;
- future external systems.

## 17.3 Plugin manifest

Each plugin declares:

- name;
- publisher;
- version;
- compatible core version;
- capabilities;
- permissions;
- commands;
- tools;
- UI panels;
- file types;
- network domains;
- secrets requested;
- background privileges;
- update channel;
- signature.

## 17.4 Process isolation

Plugins should not execute inside the core process.

Use:

- one plugin host process per trust group or plugin;
- local authenticated IPC;
- memory and time limits;
- crash restart policy;
- kill switch;
- quarantine after repeated failure;
- no direct database access;
- no direct secret access;
- no unrestricted filesystem access.

## 17.5 Tool contract

Every tool has a typed schema:

- input;
- output;
- permission declaration;
- side-effect class;
- cost class;
- timeout;
- cancellation;
- idempotency;
- rollback capability;
- verification method.

Example side-effect classes:

- read-only;
- local reversible;
- local destructive;
- external reversible;
- external irreversible;
- paid.

## 17.6 Plugin UI

Plugins may add:

- command palette actions;
- settings pages;
- preview renderers;
- side-panel tabs;
- context providers;
- task templates.

They must not freely replace core permission prompts or impersonate system UI.

## 17.7 Updates

- signed packages;
- staged rollout;
- compatibility check;
- rollback;
- per-plugin disable;
- migration sandbox;
- health check after update.

A broken PDF plugin must not break Git or chat.

## 17.8 Developer SDK

Provide:

- manifest schema;
- typed tool API;
- UI extension API;
- local test harness;
- permission simulator;
- fixture workspace;
- signing flow;
- compatibility tests;
- packaging command.

## 17.9 Initial built-in plugins

1. Local Files
2. Shell
3. Git
4. GitHub
5. Browser and Web Search
6. Documents
7. Image and Media Preview
8. OpenRouter
9. Scheduler
10. Notifications
11. VOOL
12. Web0, disabled by default

---

# 18. Architecture that supports the product

## 18.1 Separate desktop UI from the agent daemon

The UI must not directly run tools or models.

Recommended high-level structure:

### Desktop shell

- native window;
- navigation;
- editor and preview;
- settings;
- user interaction;
- accessibility.

### VOOL daemon

- task orchestration;
- state;
- permissions;
- tool calls;
- model calls;
- recovery;
- ledger.

### Model workers

- local model runtime adapters;
- OpenRouter adapter;
- future provider adapters.

### Plugin hosts

- isolated capability processes.

## 18.2 Local IPC

Windows:

- named pipes or authenticated localhost transport with strict binding.

macOS:

- Unix domain socket.

Requirements:

- per-install authentication token;
- protocol versioning;
- request IDs;
- cancellation;
- streaming events;
- backpressure;
- reconnect after UI restart;
- no unauthenticated network listener.

## 18.3 Task state machine

Suggested states:

- created;
- classifying;
- planning;
- awaiting_permission;
- queued;
- running;
- awaiting_user;
- awaiting_external;
- verifying;
- paused;
- recovering;
- completed;
- completed_with_warning;
- failed;
- cancelled;
- rolled_back.

State transitions are persisted.

## 18.4 Event-sourced task journal

Record append-only events:

- user request;
- classification;
- plan;
- permission decision;
- tool start;
- tool result;
- model start;
- model result metadata;
- file patch;
- checkpoint;
- verification;
- cost;
- cancellation;
- completion.

This powers:

- crash recovery;
- UI progress;
- receipts;
- debugging;
- replay tests.

Sensitive payloads can be separately encrypted or omitted.

## 18.5 Data storage

Use separate stores:

- settings database;
- workspace database;
- memory store;
- task journal;
- cost ledger;
- plugin registry;
- cache;
- secret vault handles.

SQLite is suitable for local structured state if migrations, backups, corruption recovery, and concurrency are handled properly.

## 18.6 Secret broker

Tools request:

> `secret_handle: github_default`

The broker:

- checks permission;
- injects secret into the approved process;
- redacts output;
- expires access;
- records usage.

The model never receives secret contents.

## 18.7 Model gateway

One internal API for:

- local runtimes;
- OpenRouter;
- future providers.

Responsibilities:

- capabilities;
- prompt formatting;
- structured output;
- token counting;
- context packing;
- retry policy;
- cancellation;
- streaming;
- cost accounting;
- provider errors;
- model deprecation;
- health.

## 18.8 Tool broker

The tool broker:

1. validates schema;
2. resolves canonical scope;
3. checks permission;
4. checks network policy;
5. checks spend policy;
6. launches isolated tool;
7. streams status;
8. enforces timeout;
9. captures result;
10. runs verifier;
11. records receipt.

A model must never bypass this path.

---

# 19. Deterministic task classification

The current local-file failures require a dedicated classifier and routing contract.

## 19.1 Capability namespaces

Examples:

- `web.search`
- `files.search`
- `files.large_files`
- `workspace.semantic_search`
- `memory.search`
- `git.search`
- `github.search`
- `apps.search`
- `contacts.search`

## 19.2 Routing rules

“On this PC,” drive letters, local paths, Finder, Explorer, folder names, file sizes, disk usage, and installed applications strongly indicate local tools.

“Latest,” “online,” public sites, current officeholders, prices, and news indicate web tools.

The router should consider:

- explicit source;
- target noun;
- path syntax;
- workspace;
- requested freshness;
- privacy;
- available tools;
- permissions.

## 19.3 No silent fallback between trust domains

If `files.search` fails, do not call `web.search` and present it as a local result.

Instead:

> “I could not search C: because VOOL currently has access only to `C:\Users\Loop\Projects`. Expand the scope?”

## 19.4 Router verification tests

Examples that must always route correctly:

- “Find the biggest files on C:” → local disk inventory.
- “Find the Dark Null folder on this PC” → local folder search.
- “Find our Dark Null repository on GitHub” → GitHub search.
- “Find the latest Dark Null documentation online” → web search.
- “What did we decide about Dark Null?” → memory and workspace history.
- “Search inside this repository for Poseidon” → repository text search.

---

# 20. Tool activity should be useful, not noisy

## 20.1 Compact inline event

`Searched C:\ · 14,280 entries · 4.2s`

Click expands:

- exact tool;
- scope;
- exclusions;
- command or API;
- output;
- permission;
- receipt.

## 20.2 Activity panel

Group by stage:

### Inspect

- read 8 files;
- searched repository;
- checked Git status.

### Change

- edited 3 files;
- created 1 file.

### Verify

- ran 42 tests;
- opened preview;
- checked console.

### Cloud

- sent 9 files;
- model;
- cost;
- returned local.

## 20.3 Raw trace

Keep raw trace behind:

`Settings → Advanced → Diagnostics`

It should not occupy the default header.

---

# 21. Plans, edits, and verification in the conversation

## 21.1 Plan card

- goal;
- steps;
- files likely affected;
- permissions;
- expected cost;
- stop conditions.

Buttons:

- Run
- Edit plan
- Run step by step
- Cancel

## 21.2 File change card

- filename;
- additions/deletions;
- reason;
- test relation;
- open in diff;
- revert file.

## 21.3 Test card

- command;
- duration;
- passed;
- failed;
- skipped;
- logs;
- rerun;
- fix failures.

## 21.4 Final card

- result;
- files changed;
- tests;
- preview;
- cost;
- warnings;
- checkpoint;
- next safe action.

---

# 22. Background and long-running tasks

## 22.1 Task queue

Show:

- running;
- waiting;
- permission needed;
- scheduled;
- paused;
- completed;
- failed.

## 22.2 User controls

- pause;
- resume;
- stop after current step;
- stop now;
- lower priority;
- move to foreground;
- change budget;
- inspect logs.

## 22.3 Sleep and reboot

Before sleep:

- persist task state;
- stop unsafe processes;
- record checkpoint.

After restart:

- explain what was interrupted;
- verify filesystem and Git state;
- offer resume;
- never assume an external operation did or did not complete.

## 22.4 Notification examples

- “VOOL needs permission to run the build.”
- “Tests failed after 18 minutes.”
- “Paid review reached the $0.20 cap and stopped.”
- “Task completed and verified.”

---

# 23. Failure and non-happy-path design

This product will be trusted only if failures are handled better than successes.

## 23.1 Local model missing

- show model unavailable;
- offer download;
- offer another installed local model;
- do not silently use cloud.

## 23.2 Local model out of memory

- cancel safely;
- unload other model;
- reduce context;
- switch local lane;
- explain quality impact;
- cloud remains opt-in.

## 23.3 Invalid OpenRouter key

- stop paid call;
- keep local task state;
- open reconnect flow;
- do not ask the model to interpret auth errors.

## 23.4 Insufficient credits

- show actual provider error;
- show last known balance;
- offer add-credit page;
- continue locally where possible;
- do not loop retries.

## 23.5 Model removed or price changed

Before paid execution:

- refresh model metadata;
- compare price timestamp;
- if the upper estimate changes beyond configured tolerance, ask again;
- select fallback only if policy allows.

## 23.6 Rate limit or provider outage

- bounded retries with backoff;
- show waiting state;
- offer another model;
- preserve cap;
- bill only recorded successful usage;
- no retry storm.

## 23.7 Network drops mid-call

- mark result uncertain;
- query provider status or generation record if supported;
- do not automatically resubmit until billing state is understood;
- keep a request id.

## 23.8 Tool crash

- plugin host dies without taking down VOOL;
- record crash;
- restart once;
- quarantine after repeated crashes;
- present partial result;
- preserve checkpoint.

## 23.9 Permission denied

- do not re-prompt repeatedly;
- explain blocked step;
- offer narrower alternative;
- allow plan modification.

## 23.10 File changed externally

- detect modification since read;
- refuse blind overwrite;
- show three-way comparison;
- merge or ask.

## 23.11 Dirty repository

Before broad edits:

- show current user changes;
- distinguish user changes from VOOL changes;
- create patch checkpoint;
- never reset unrelated work.

## 23.12 Tests hang

- command timeout;
- stream last output;
- offer continue, stop, or extend;
- kill child process tree correctly on both platforms.

## 23.13 Build opens a server

- record PID;
- show port;
- stop on task end unless pinned;
- avoid orphaned processes.

## 23.14 Destructive command proposed by model

- blocked by side-effect classifier;
- require exact human confirmation;
- create backup where possible;
- verify target;
- prohibit wildcard ambiguity.

## 23.15 Plugin compromised

- signature failure blocks update;
- permissions remain least privilege;
- network and filesystem are brokered;
- revoke plugin secrets;
- quarantine;
- show affected actions;
- core remains operational.

## 23.16 App crash during file edit

- atomic writes;
- temporary file plus rename;
- journal;
- restore option;
- re-check Git and file hashes on launch.

## 23.17 Database corruption

- automatic backup;
- integrity check;
- read-only recovery mode;
- rebuild derived indexes;
- never delete memory silently.

## 23.18 Partial external action

For push, publish, send, payment, or deployment:

- mark “verification required”;
- query external system;
- never retry irreversible actions without idempotency or user approval.

---

# 24. Security model

## 24.1 Threat assumptions

Assume:

- model output can be malicious or mistaken;
- repository content can contain prompt injection;
- web pages can contain prompt injection;
- plugins can be buggy or malicious;
- shell output can include secrets;
- filenames can be adversarial;
- local user may grant overly broad access;
- cloud provider metadata and prices can change.

## 24.2 Untrusted-content boundaries

Mark content origin:

- user instruction;
- trusted rule;
- local file;
- web page;
- tool output;
- plugin output;
- model output.

A README cannot grant permission.

## 24.3 Prompt-injection defense

- tool permissions are outside model context;
- retrieved content cannot modify policy;
- suspicious instructions are flagged;
- secrets unavailable to model;
- external content cannot broaden network or filesystem scope;
- actions require typed tool requests.

## 24.4 Sandboxing

Use platform-appropriate isolation for:

- previews;
- plugins;
- shell jobs where feasible;
- document converters;
- media parsers.

## 24.5 Updates

- signed core updates;
- verified publisher;
- staged rollout;
- rollback;
- release notes;
- no silent privilege expansion.

## 24.6 Receipts

Receipts should include:

- user request hash;
- plan hash;
- permission decisions;
- tool invocations;
- file change hashes;
- verification;
- model and cost metadata;
- completion state.

Receipts should prove actions without exposing secrets or full private content.

---

# 25. Performance requirements

## 25.1 Startup

- useful window quickly;
- daemon health displayed;
- previous workspace restored;
- models loaded lazily.

## 25.2 Chat responsiveness

- user message appears immediately;
- classification starts without blocking UI;
- status changes within hundreds of milliseconds;
- cancellation always responsive.

## 25.3 Large repository behavior

- incremental search;
- bounded context;
- stream results;
- avoid UI freeze;
- cache file metadata;
- watch changes.

## 25.4 Resource governor

User controls:

- maximum RAM;
- maximum VRAM;
- CPU percentage;
- battery behavior;
- background priority;
- concurrent tasks;
- local-model idle unload.

## 25.5 Thermal and battery state

On laptops:

- detect battery;
- offer efficient mode;
- pause indexing;
- reduce animation;
- avoid loading Heavy lane automatically.

---

# 26. Accessibility and keyboard-first use

Required shortcuts:

- new task;
- command palette;
- change mode;
- change model;
- toggle preview;
- stop task;
- open activity;
- open settings;
- navigate permissions;
- approve or deny.

Requirements:

- full keyboard navigation;
- visible focus;
- screen-reader labels;
- status announcements;
- no colour-only meaning;
- reduced motion;
- resizable text;
- high contrast;
- accessible diff.

---

# 27. Cross-platform implementation matrix

Every feature ticket must include Windows and macOS acceptance.

## 27.1 Windows

Test:

- Windows 10 and 11 where supported;
- NTFS;
- long paths;
- drive letters;
- junctions;
- UNC;
- PowerShell;
- Command Prompt where needed;
- Credential Manager/DPAPI;
- antivirus interaction;
- code signing;
- installer and uninstaller;
- update while app is running;
- process-tree termination;
- high-DPI scaling.

## 27.2 macOS

Test:

- current supported macOS versions;
- Apple Silicon;
- Intel only if still supported;
- APFS;
- aliases and symlinks;
- Keychain;
- application sandbox or entitlements;
- Gatekeeper;
- notarization;
- Full Disk Access prompts;
- shell differences;
- sleep/wake;
- multiple Spaces and displays.

## 27.3 Shared tests

- Unicode filenames;
- emoji;
- non-English system locale;
- spaces;
- case differences;
- very large files;
- disconnected network;
- proxy;
- VPN;
- daylight saving;
- clock changes.

---

# 28. Testing strategy

“No cheating” means test the architecture and user outcomes, not only unit functions.

## 28.1 Unit tests

- path canonicalization;
- permission resolution;
- rule precedence;
- model capability matching;
- cost estimation;
- budget enforcement;
- state transitions;
- plugin manifest validation;
- redaction;
- receipt generation.

## 28.2 Integration tests

- UI ↔ daemon;
- daemon ↔ local model;
- daemon ↔ OpenRouter;
- tool broker ↔ filesystem;
- Git and GitHub;
- preview server;
- plugin crash;
- secret injection;
- cancellation;
- crash recovery.

## 28.3 End-to-end task tests

Examples:

1. Find largest files on C: without web search.
2. Find a folder by partial name.
3. Inspect repo, fix test, run suite, show diff.
4. Build local webpage and preview.
5. Local model fails; paid escalation suggested; user denies; continue locally.
6. Paid escalation approved under cap; actual cost recorded; return local.
7. Balance insufficient; task does not lose state.
8. Excluded folder is never read.
9. Symlink points outside workspace; access is blocked.
10. Plugin crashes during PDF render; core survives.
11. User edits file during task; VOOL avoids overwrite.
12. App restarts during test; task recovers.
13. Git push requires permission.
14. Force push requires strong confirmation.
15. Web prompt injection attempts to request secrets; blocked.

## 28.4 Adversarial tests

- malicious repository instructions;
- fake tool output;
- secret-shaped strings;
- path traversal;
- archive extraction escape;
- shell injection;
- poisoned plugin;
- model attempts to call undeclared tool;
- repeated cost escalation;
- network domain redirect;
- DNS rebinding;
- destructive glob.

## 28.5 UX tests

A normal user must be able to:

- create workspace;
- add folder;
- understand permissions;
- connect OpenRouter;
- add credits;
- set cap;
- choose mode;
- preview work;
- undo;
- understand cost;
- find settings.

No terminal instructions in the primary onboarding path.

## 28.6 Performance tests

- 100,000-file workspace;
- multi-GB log;
- 10-hour task journal;
- 100 sessions;
- multiple model switches;
- repeated preview refresh;
- plugin memory leaks;
- cold/warm startup.

## 28.7 Accessibility tests

- keyboard-only;
- screen reader;
- reduced motion;
- 200% scaling;
- high contrast.

## 28.8 Release gates

A release cannot ship if:

- any paid call bypasses a cap;
- excluded files are read;
- local file requests route to web;
- secrets appear in logs;
- unrelated user changes are overwritten;
- cancellation leaves dangerous processes;
- core crashes with a plugin;
- cost ledger differs materially from provider usage;
- update cannot roll back.

---

# 29. Delivery plan

Do not attempt all features in one giant branch.

Build vertical slices that prove the core architecture.

## Phase 0 — Freeze the product contract

Deliver:

- mode definitions;
- capability namespaces;
- permission taxonomy;
- task state machine;
- plugin manifest;
- model lane contract;
- cost policy;
- UI map;
- test fixtures.

Gate:

- all teams use the same language and schemas.

## Phase 1 — Fix routing and tool truth

Deliver:

- deterministic source classifier;
- real local filesystem search;
- largest-file tool;
- workspace search;
- clear activity cards;
- no silent web fallback;
- cancellation;
- basic permission prompts.

Prove with:

- exact screenshot requests.

Gate:

- 100% correct routing in preregistered local/web/memory/GitHub test set.

## Phase 2 — Workspace and settings foundation

Deliver:

- workspace creation;
- approved roots;
- exclusions;
- global/workspace rules;
- settings shell;
- appearance;
- secret storage;
- basic model settings.

Gate:

- no file outside resolved roots can be accessed in adversarial tests.

## Phase 3 — Task engine and side panel

Deliver:

- persisted task states;
- plan cards;
- activity panel;
- files;
- terminal;
- tests;
- changes;
- preview framework;
- checkpoints;
- crash recovery.

Gate:

- restart mid-task and recover without corrupting files or state.

## Phase 4 — Modes and permission depth

Deliver:

- Ask;
- Plan;
- Build;
- Review;
- permission scopes;
- trust profiles;
- command policies;
- Git policies;
- destructive-action confirmation.

Gate:

- mode behavior is enforced by broker, not prompt.

## Phase 5 — Local model center

Deliver:

- Fast/Daily/Heavy roles;
- model detection;
- benchmark;
- machine-specific speed;
- warm state;
- resource governor;
- local-only policy;
- model selector FREE section.

Gate:

- reliable switching without losing task context or freezing UI.

## Phase 6 — OpenRouter and paid specialist loop

Deliver:

- PKCE connection;
- dedicated capped key;
- model list;
- pricing cache;
- preflight estimates;
- usage accounting;
- daily history;
- budget enforcement;
- paid subtask handoff;
- automatic local return;
- balance and key state.

Gate:

- hard caps cannot be exceeded by retries, concurrency, restart, or plugin calls.

## Phase 7 — Preview completeness

Deliver:

- web;
- Markdown;
- image;
- PDF;
- document;
- video;
- diff;
- detach;
- compare;
- console.

Gate:

- previews are sandboxed and reflect current checkpoint.

## Phase 8 — GitHub

Deliver:

- connect;
- repository permissions;
- issues;
- PR draft;
- actions;
- review;
- push policy.

Gate:

- no external mutation without explicit policy decision and verification.

## Phase 9 — Plugin platform

Deliver:

- process isolation;
- SDK;
- manifest;
- signing;
- update/rollback;
- permission UI;
- plugin marketplace or catalog;
- first-party plugins migrated from monolith.

Gate:

- crash or disable any plugin without breaking core.

## Phase 10 — Snake animation and product polish

Deliver:

- event-driven animation;
- states;
- reduced motion;
- task tray;
- notifications;
- onboarding;
- empty states;
- keyboard shortcuts;
- polished final cards.

Do this after real task events exist. Otherwise the animation will hide an unreliable backend.

## Phase 11 — Controlled public alpha

Entry requirements:

- security review;
- Windows/macOS matrix;
- recovery;
- signed builds;
- update rollback;
- local routing benchmark;
- paid-spend audit;
- plugin isolation;
- diagnostics export;
- privacy documentation;
- known-limitations page.

---

# 30. Immediate screen redesign

Based on the current window, the first redesign should be practical rather than total replacement.

## Header

Current:

`VOOL v0.4.0-closed-test                     Web0 Trace`

Replace with:

`VOOL / [Workspace] / [Branch]`

Right side:

`Local Daily ▾` `Build ▾` `$0.00` `Preview` `Activity` `⚙`

## Left side

Current:

`CHATS`

Replace with:

- `+ New Task`
- Search
- Workspaces
- Sessions
- Scheduled
- Skills

Keep current session list underneath selected workspace.

## Center

Keep conversation, but replace vague tool messages with structured cards.

## Bottom

Add:

- attachment;
- context;
- command;
- mode;
- stop;
- queue.

## Right panel

Default hidden.

When opened:

- Preview;
- Changes;
- Terminal;
- Tests;
- Plan;
- Activity.

## Web0 and Trace

- remove both from header;
- Web0 in `Settings → Labs`;
- trace in `Settings → Advanced → Diagnostics`;
- Activity remains user-facing.

---

# 31. Minimum viable “wow” vertical slice

The first impressive demo should not try to prove everything.

Use this exact scenario:

> “Find the Website V3 folder on my PC, inspect it, run it, show me a preview, fix the broken mobile menu, test it, and prepare a Git commit.”

Expected experience:

1. VOOL detects local-machine intent.
2. Asks to search selected roots, not the entire drive by default.
3. Finds candidate folders.
4. User selects one.
5. VOOL creates a workspace.
6. Shows Git branch and dirty state.
7. Enters Build mode.
8. Creates a checkpoint.
9. Inspects project.
10. Starts local dev server.
11. Opens side preview.
12. Reproduces mobile issue.
13. Edits files.
14. Preview refreshes.
15. Runs tests and console checks.
16. If local model cannot solve a complex issue, proposes a paid specialist with cost and exact files.
17. Returns local after specialist advice.
18. Completes edits and tests.
19. Shows diff.
20. Prepares commit message.
21. User approves commit.
22. Final card shows verified result, cost, and receipt.

This one workflow proves:

- local search;
- permissions;
- workspace;
- model routing;
- optional paid escalation;
- preview;
- tools;
- Git;
- verification;
- receipts;
- safe control.

---

# 32. Metrics that matter

Do not optimize for chat messages or model tokens.

Track locally and anonymously only if the user opts in:

- task completion rate;
- verified completion rate;
- local-only completion rate;
- paid escalations per completed task;
- accepted result per paid dollar;
- permission denial rate;
- incorrect tool route rate;
- rollback rate;
- plugin crash rate;
- user intervention count;
- time to first useful preview;
- cost-estimate error;
- tasks resumed successfully after crash;
- unrelated-file modification rate, target zero.

---

# 33. Things not to do

- Do not put every model in one giant dropdown.
- Do not call OpenRouter free models “local.”
- Do not silently upload whole repositories.
- Do not use a management API key by default.
- Do not let plugins read keys from environment variables freely.
- Do not rely on prompts to enforce permissions.
- Do not call web search when local search fails.
- Do not show fake progress percentages.
- Do not make Trace a headline product feature.
- Do not expose Web0 before the user enables it.
- Do not overwrite dirty repositories.
- Do not auto-push.
- Do not add a plugin marketplace before plugin isolation and signatures.
- Do not build the animation before the task event system.
- Do not consider a generated answer a completed task.
- Do not make macOS the reference product and “port later” to Windows.
- Do not release auto-spend before concurrency and crash tests prove the cap.

---

# 34. Final product standard

VOOL is ready to claim “one Swiss Army knife on your machine” only when a user can safely trust it with a real workspace and understand:

- what it is doing;
- where it is working;
- which model is active;
- whether data is local or cloud;
- what it will cost;
- what permission it has;
- what changed;
- whether it passed verification;
- how to stop it;
- how to undo it;
- which plugin performed the action;
- whether the result is proven.

The “wow” is not the number of icons or model names.

The “wow” is:

> VOOL found the correct local tool, used the minimum access, understood when the local model was enough, asked before spending four cents on a specialist, returned local, fixed the real project, showed the live result, passed the tests, and left a verifiable receipt—with one application and no terminal setup.

---

# 35. OpenRouter implementation notes from current official documentation

These points should be verified again immediately before implementation because APIs and pricing can change.

- OpenRouter documents an OAuth PKCE flow that can create a user-controlled API key for an application. This is the preferred onboarding path.
- The current-key endpoint can report rate-limit or credit information associated with the API key.
- OpenRouter responses can include usage accounting for token counts and cost, which should be captured in VOOL’s local ledger.
- The models endpoint can supply current model metadata.
- OpenRouter supports API-key limits. VOOL should create or request a dedicated capped key rather than reuse a broad key.
- Full account-credit information can require a management key. Basic VOOL onboarding should not demand this higher privilege merely to display a total account balance.
- Failed calls, provider fallbacks, rate limits, low credits, model removal, and pricing changes must all be handled as first-class states.
- Provider-reported usage is the source of truth for completed paid calls; VOOL’s estimator is only a preflight range.

---

# 36. First engineering tickets

1. Replace ambiguous `search` action with typed source capabilities.
2. Implement `files.search_folder`.
3. Implement `files.find_largest`.
4. Add canonical path and junction/symlink enforcement.
5. Add task event schema.
6. Replace “Completed 1 real tool step” with structured tool activity.
7. Remove Web0 and Trace from the default header.
8. Add mode selector shell.
9. Add model selector shell with FREE and PAID groups.
10. Add Settings entry and settings navigation.
11. Add workspace root selection and exclusions.
12. Add right-side panel framework.
13. Add file, diff, terminal, tests, and plan tabs.
14. Add persisted task state.
15. Add checkpoint service.
16. Add OS secret vault.
17. Add OpenRouter adapter behind spend policy.
18. Add cost-estimation interface and hard-cap accounting.
19. Add automatic return-to-local state.
20. Add plugin manifest and isolated host proof of concept.
21. Add cross-platform E2E fixture for the exact screenshot requests.
22. Add reduced-motion event-driven VOOL animation prototype after task events exist.

---

**Recommended build order:** routing truth → permissions and workspaces → task state and preview → local model center → paid specialist loop → GitHub → plugin platform → animation and polish.

That order creates a real product instead of placing a beautiful dashboard over the same failure shown in the current screenshot.

---

# 37. Optional capability plugins: Web0, image generation, and video generation

Web0 must not be part of the default VOOL desktop surface or core runtime.

It belongs in the same isolated capability system as other optional specialist integrations.

## 37.1 Web0 plugin

Suggested package:

`com.parad0xlabs.vool.web0`

Default state:

- installed only in Parad0x Labs or advanced distributions, or offered through the official plugin catalog;
- disabled by default;
- invisible in the main header;
- enabled through `Skills & Plugins`;
- settings appear only after activation.

Potential capabilities:

- `.null` workspace and identity actions;
- permanent-page publishing;
- NullPay;
- optional x402 payment actions;
- optional Dark Null privacy operations;
- verification and receipt views.

Web0 must request each capability separately. Enabling the plugin must not automatically grant wallet access, publishing rights, network access, payment authority, or access to private workspace files.

## 37.2 Image-generation plugin

Suggested package:

`com.parad0xlabs.vool.image`

The core should expose a generic image-generation contract. Providers and local runtimes remain adapters.

Capabilities may include:

- text-to-image;
- image-to-image;
- editing;
- masking;
- upscaling;
- background removal;
- variation generation;
- local preview;
- metadata and cost records;
- optional VOOL grading.

Possible backends:

- local ComfyUI or another local runtime;
- OpenRouter where supported;
- fal or other explicitly connected providers;
- future Parad0x-hosted service.

The plugin must show:

- local or cloud;
- exact provider/model;
- input files being sent;
- expected output dimensions;
- estimated cost;
- maximum cost;
- generated-file location;
- content provenance;
- verification state.

Image generation should not become a permanent core dependency.

## 37.3 Video-generation plugin

Suggested package:

`com.parad0xlabs.vool.video`

Capabilities may include:

- text-to-video;
- image-to-video;
- video extension;
- storyboard and shot planning;
- audio or voice handling;
- local and cloud backends;
- job queue;
- previews;
- retries;
- cost tracking;
- VOOL grading and routing.

The plugin must isolate:

- provider SDKs;
- large media dependencies;
- codecs;
- GPU jobs;
- asynchronous webhook handling;
- output storage;
- generation history.

A video-provider crash, codec error, or GPU failure must not crash the VOOL core.

## 37.4 VOOL as a reusable grading plugin

VOOL can be a first-party plugin used by:

- image generation;
- video generation;
- document rendering;
- website previews;
- future media tools.

Suggested package:

`com.parad0xlabs.vool.vool`

It should expose grading, verification, acceptance, and retry recommendations through typed contracts. Image and video plugins can depend on a stable VOOL interface without importing the entire VOOL implementation into the core.

## 37.5 Plugin dependency rule

Dependencies must be narrow and versioned.

Good:

`Video Plugin -> VOOL grading interface v1`

Bad:

`Video Plugin imports VOOL internal database, UI state, model router, secrets, and filesystem directly`

The plugin broker remains the only route to core capabilities.

---

# 38. Strict local-development and final-push protocol

This protocol is mandatory for the VOOL desktop rebuild.

The chat transcript is not the source of truth.  
A developer's memory is not the source of truth.  
An uncommitted working tree is not the source of truth.

The repository is the source of truth.

## 38.1 Fundamental rule

During implementation:

- work in a dedicated local branch;
- do not push the branch;
- create frequent local commits;
- keep machine-readable project state;
- keep human-readable handoff state;
- run the required gate after every milestone;
- push only after the user explicitly says `GO`;
- final push must contain only audited, complete, reproducible work.

“Do not push” must never mean “leave weeks of work uncommitted.”

Local commits are required for safe bookkeeping and rollback.

## 38.2 Dedicated branch

Suggested branch:

`feature/vool-desktop-agent-os`

Before starting:

```bash
git fetch origin
git switch main
git pull --ff-only
git switch -c feature/vool-desktop-agent-os
```

After branch creation, normal development does not push.

## 38.3 Remote protection during local-only development

Use at least one hard protection.

Recommended local configuration:

```bash
git config branch.feature/vool-desktop-agent-os.pushRemote DISABLED
```

Additionally provide a local pre-push hook that refuses every push unless an explicit release approval file exists and the release command is being used.

Normal `git push` must fail.

Only the audited release script may push.

## 38.4 Required bookkeeping files

Create a versioned directory:

```text
VOOL-DELIVERY/
├── CURRENT_STATE.json
├── HANDOFF.md
├── DECISIONS.md
├── TEST_MATRIX.md
├── KNOWN_RISKS.md
├── CHANGELOG_LOCAL.md
├── MILESTONES.md
├── FILE_OWNERSHIP.md
├── TASK_LEDGER.jsonl
├── RELEASE_MANIFEST.json
└── evidence/
```

### `CURRENT_STATE.json`

Machine-readable current truth:

- schema version;
- branch;
- base commit;
- current local commit;
- completed milestones;
- active milestone;
- blocked work;
- unverified work;
- failing tests;
- changed subsystems;
- migrations;
- plugin versions;
- Windows status;
- macOS status;
- last full gate;
- push state;
- next exact action.

This file is updated at every checkpoint commit.

### `HANDOFF.md`

Human-readable restart instructions:

- what was completed;
- what is currently being changed;
- exact commands to continue;
- test commands;
- known failures;
- files that must not be touched;
- architectural decisions;
- next three actions.

A fresh engineer or agent must be able to continue from this file without relying on chat history.

### `DECISIONS.md`

Architecture decision log:

- decision ID;
- date;
- decision;
- alternatives;
- reason;
- consequences;
- affected components;
- superseded decision if applicable.

No major design decision should exist only inside conversation text.

### `TEST_MATRIX.md`

Tracks:

- unit;
- integration;
- end-to-end;
- adversarial;
- Windows;
- macOS;
- security;
- performance;
- accessibility.

Each row records:

- command;
- environment;
- status;
- evidence;
- commit tested;
- date.

### `KNOWN_RISKS.md`

Every unresolved item includes:

- severity;
- affected component;
- reproduction;
- current containment;
- owner;
- release-blocking status.

### `TASK_LEDGER.jsonl`

Append-only task events:

- task created;
- files touched;
- commands run;
- result;
- commit;
- test outcome;
- blocker;
- decision reference.

This can be generated automatically by development scripts.

### `RELEASE_MANIFEST.json`

Created only during final release audit:

- approved commit range;
- files included;
- migrations;
- plugin versions;
- test evidence;
- binary hashes;
- signing status;
- release notes;
- known limitations;
- final push target.

## 38.5 Local checkpoint command

Provide one canonical command:

```bash
./scripts/vool-checkpoint "milestone message"
```

Windows PowerShell equivalent:

```powershell
.\scripts\vool-checkpoint.ps1 "milestone message"
```

The command must:

1. verify the expected branch;
2. reject detached HEAD;
3. list modified and untracked files;
4. reject secrets and oversized accidental artifacts;
5. run formatting and fast tests;
6. update `CURRENT_STATE.json`;
7. append `TASK_LEDGER.jsonl`;
8. update `HANDOFF.md` fields;
9. create a local Git commit;
10. print the exact commit hash;
11. confirm that nothing was pushed.

A checkpoint must fail if bookkeeping files cannot be updated.

## 38.6 Milestone commits

Each coherent milestone receives a local commit.

Examples:

- typed task-routing foundation;
- filesystem permission broker;
- workspace settings shell;
- side-preview framework;
- OpenRouter budget enforcement;
- plugin host isolation;
- Windows process-tree cancellation;
- macOS Keychain integration.

Do not combine unrelated work into one giant final commit.

## 38.7 Worktree cleanliness

At the end of every work session:

- no important untracked source files;
- no undocumented modified files;
- no test evidence living only in temporary folders;
- `HANDOFF.md` current;
- `CURRENT_STATE.json` current;
- local checkpoint commit created.

A dirty tree is allowed only while actively implementing. It is not an acceptable handoff state.

## 38.8 Generated and temporary files

The repository must distinguish:

- source;
- fixtures;
- release artifacts;
- test evidence;
- generated cache;
- local secrets;
- temporary output.

`.gitignore` must be explicit. Important evidence must not be accidentally ignored; local secrets and caches must never be committed.

## 38.9 No half-integrated code

A subsystem cannot be marked complete merely because files were added.

It is complete only when:

- wired into the actual runtime;
- enabled through the intended UI or API;
- permission-controlled;
- covered by tests;
- error-handled;
- documented;
- included in cross-platform checks;
- restart-safe where applicable.

Dead code, alternate unused routers, duplicate implementations, hidden fallback paths, and test-only wiring block completion.

## 38.10 Feature flags

Incomplete work may remain locally behind a feature flag, but:

- the flag must default off;
- the incomplete state must be recorded;
- no public UI should expose it;
- final release audit either completes it or excludes it from the push.

A feature flag is not permission to push abandoned half-code.

## 38.11 Mandatory pre-push audit

Provide:

```bash
./scripts/vool-release-audit
```

and:

```powershell
.\scripts\vool-release-audit.ps1
```

The audit must verify:

1. correct branch;
2. correct base;
3. no unexpected merge commits;
4. clean working tree;
5. no unresolved conflict markers;
6. no secrets;
7. no large accidental binaries;
8. no disabled or skipped required tests;
9. no release-blocking TODO markers;
10. no duplicated old/new production path;
11. all migrations reversible or documented;
12. plugins declare correct permissions;
13. Windows suite passes;
14. macOS suite passes;
15. security and adversarial gates pass;
16. cost caps pass concurrency and restart tests;
17. excluded filesystem paths remain inaccessible;
18. routing tests never confuse local and web sources;
19. plugin failure does not crash core;
20. task recovery works after forced restart;
21. release documentation matches code;
22. `CURRENT_STATE.json` reports release-ready;
23. `KNOWN_RISKS.md` contains no unapproved release blocker;
24. `RELEASE_MANIFEST.json` is generated and internally consistent.

The audit exits non-zero on any failure.

## 38.12 Explicit user approval

The release process requires an explicit human approval artifact.

Suggested local file:

```text
VOOL-DELIVERY/GO_APPROVAL.json
```

It contains:

- approved by;
- approval timestamp;
- approved commit hash;
- target remote;
- target branch;
- release manifest hash.

The file is created only after the user says `GO`.

Casual phrases such as “looks good,” “continue,” or “nice” do not count.

## 38.13 Single controlled push command

Provide one command:

```bash
./scripts/vool-push-approved
```

PowerShell:

```powershell
.\scripts\vool-push-approved.ps1
```

It must:

1. require `GO_APPROVAL.json`;
2. verify the approved commit is current HEAD;
3. rerun the final release audit;
4. verify the working tree is clean;
5. show the exact remote, branch, and commit range;
6. require a final typed confirmation;
7. push once;
8. verify the remote commit after push;
9. record the remote commit in the release ledger;
10. remove or invalidate the approval artifact.

Normal Git push remains blocked.

## 38.14 No force push

The release script must not force push.

If the remote changed:

- stop;
- fetch;
- show divergence;
- reassess;
- update and rerun all affected gates;
- obtain new approval if the approved commit changes.

## 38.15 Post-push verification

After push:

- verify remote branch SHA;
- verify expected files;
- run CI;
- verify signed artifacts where applicable;
- verify installation packages;
- update release state;
- create a final local tag only after remote and CI verification.

A successful `git push` is not by itself a successful release.

## 38.16 Recovery after interrupted development

At any later time, continuation must begin with:

```bash
./scripts/vool-resume-status
```

The script prints:

- current branch and commit;
- base;
- dirty files;
- active milestone;
- last passing gate;
- known blockers;
- exact next commands;
- remote push status.

The project must be recoverable even if the prior chat, developer, or agent is unavailable.

## 38.17 Backup

Before large migrations or release:

- create a Git bundle;
- store the current branch and tags;
- back up local databases and fixtures;
- hash the backup;
- record it in `CURRENT_STATE.json`.

Example:

```bash
git bundle create ../vool-desktop-agent-os.bundle --all
```

## 38.18 Strict release definition

The final push is allowed only when:

- all intended features are complete;
- excluded incomplete features are physically absent from production paths or securely off;
- tests pass on both platforms;
- repository is clean;
- release manifest is reproducible;
- no secret or local-machine path is included;
- no local-only dependency is missing;
- the user has explicitly said `GO`;
- the controlled push script succeeds;
- remote verification succeeds.

This protocol prevents work from being lost locally and prevents unfinished code from drifting into the remote repository.

