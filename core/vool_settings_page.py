"""VOOL Settings — one organised surface, served by the always-on API server at ``/settings``.

Why this exists
---------------
Settings were scattered: a long scrolling modal inside the chat page (behaviour, limits, keys,
usage, build), the operating mode in the composer popover, plugins in their own modal, updates
nowhere, and roughly a dozen real preferences reachable only by typing a sentence at VOOL. This
module is the single organised surface: a searchable sidebar, a focused content pane, and rows
that bind to the SAME canonical authorities the old controls used.

Authority
---------
Nothing here is a new store and nothing here is a new permission system. Every write goes to an
endpoint that already exists and already forwards into the command registry:

* ``POST /api/settings/prefs``       -> ``settings.prefs.set``       (capability ``change_settings``,
                                        operator authority ``settings.write``, receipt
                                        ``settings_write/prefs``)
* ``POST /api/settings/credentials`` -> ``settings.credentials.set`` (same gates, surface
                                        ``credentials``)
* ``POST /api/profile/*``            -> the Operator Profile authority (scoped, revisioned)
* ``POST /api/cloud/model``, ``/api/cloud/auto-model``, ``/api/plugins/enable`` -> their own
  existing authorities.

Design contract for the page
----------------------------
* **Opening Settings probes nothing.** Boot issues plain reads only. No provider probe, no model
  launch, no Keychain access, no permission prompt. Anything that costs money, reaches a provider
  or touches a secret is behind a button that first states its destination and effect.
* **A failed save stays visibly failed.** A row that could not be written keeps the edited value,
  shows the server's reason, and offers Retry. There is no optimistic "Saved".
* **Truth over completeness.** A capability the runtime does not have gets an explanation, not a
  disabled toggle that implies it is coming. A guidance-only number says it is guidance.
* **One value, one control.** A setting that genuinely belongs to a chat (operating mode) is
  explained here and edited where it applies, rather than duplicated.

The page is self-contained (inline CSS/JS, no build step, no CDN) so it works inside a packaged
installer with no network, and it is served from the same origin and the same runtime as the chat
surface — a second window, never a second backend.
"""
from __future__ import annotations

import json

# --------------------------------------------------------------------------------------
# Effect vocabulary. Every bound row states when its change takes hold and how far it reaches,
# because "saved" alone does not tell a user whether the answer they are reading changes.
# --------------------------------------------------------------------------------------
EFFECT_NEXT_TURN = "Applies from your next message. The turn in flight keeps the old value."
EFFECT_IMMEDIATE = "Applies immediately."
EFFECT_NEXT_LAUNCH = "Applies the next time VOOL starts."

SCOPE_GLOBAL = "global"
SCOPE_CHAT = "chat"
SCOPE_SESSION = "session"

# Read sources. The page fetches each of these ONCE at boot (deduplicated) and re-reads the
# affected source after every successful write, so two open windows converge.
PREFS = "/api/settings/prefs"
CREDENTIALS = "/api/settings/credentials"
SETUP_STATE = "/api/setup/state"
VERSION = "/api/runtime/version"
PROFILE = "/api/profile"
# Email: the account authority's listing (credential NAMES and identities, never a secret) and the
# draft store's recovery hold. Both are owner-local reads served by this runtime (core/web/api/service.py).
EMAIL_ACCOUNTS = "/api/email/accounts"
EMAIL_RECOVERY = "/api/email/recovery"


def language_catalog() -> list[dict]:
    """The answer-language options, taken from the ONE canonical table.

    ``core.response_language_policy.LANGUAGE_NAMES`` is what the policy normalises against and
    what it words its instruction with. Importing it means the picker cannot offer a language the
    policy would then fail to read, and adding a language there adds it here.
    """
    from core.response_language_policy import LANGUAGE_NAMES

    return [{"code": code, "label": name} for code, name in sorted(LANGUAGE_NAMES.items(), key=lambda kv: kv[1])]


def _research_networking() -> bool:
    from core.runtime_mode import research_networking_enabled

    return research_networking_enabled()


def _row(**kw) -> dict:
    """One settings row. Unset optional keys stay absent so the JSON stays small."""
    return {k: v for k, v in kw.items() if v is not None}


def _edition_filtered(groups: list[dict]) -> list[dict]:
    """The School edition mechanically lacks the wallet surface (core/product_edition.py): its Settings page has no
    Crypto group at all, so nothing there can be enabled, listed or created."""
    from core.product_edition import edition_allows

    wallet_ok, _reason = edition_allows("wallet")
    return groups if wallet_ok else [group for group in groups if group.get("id") != "wallet"]


def settings_groups() -> list[dict]:
    """The declarative settings model.

    ONE list drives the sidebar, the search index and the rendered rows. Adding a control means
    adding an entry here, not touching three places. Every ``write`` names an endpoint that
    already carries the authority, gate and receipt for that value.
    """
    return _edition_filtered(_settings_groups())


def _settings_groups() -> list[dict]:
    return [
        {
            # First while setup is unfinished; removed from the page at boot once every step is
            # really done (derived, core/setup_progress.py) or the person asked not to be reminded.
            "id": "setup",
            "icon": "✓",
            "title": "Complete setup",
            "blurb": "A few quick steps. Each one is also an ordinary setting below.",
            "rows": [
                _row(
                    id="setup_progress",
                    label="Finish setting up VOOL",
                    kind="custom",
                    widget="setup_progress",
                    help=(
                        "Where it thinks, which folder it works in, what it may do alone, and what "
                        "to call you. A step counts as done when the real setting exists — set it "
                        "here or anywhere else in Settings and the tick appears by itself."
                    ),
                    read={"url": SETUP_STATE},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords="setup onboarding first run getting started complete finish steps welcome guide",
                ),
            ],
        },
        {
            "id": "general",
            "icon": "⚙",
            "title": "General",
            "blurb": "How VOOL talks.",
            "rows": [
                _row(
                    id="communication_style",
                    label="Talk style",
                    help="The register VOOL writes in. It changes wording, not what VOOL is willing to do.",
                    kind="select",
                    options=[
                        {"value": "casual", "label": "Casual"},
                        {"value": "business", "label": "Business"},
                        {"value": "cheeky", "label": "Cheeky"},
                    ],
                    read={"url": PREFS, "field": "communication_style"},
                    write={"url": PREFS, "field": "communication_style", "type": "str"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="tone voice register casual business cheeky formal",
                ),
                _row(
                    id="humor_percent",
                    label="Humour",
                    help="How much levity VOOL allows itself. 0% is plain and flat.",
                    kind="range",
                    min=0,
                    max=100,
                    step=5,
                    unit="%",
                    read={"url": PREFS, "field": "humor_percent"},
                    write={"url": PREFS, "field": "humor_percent", "type": "int"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="humour humor jokes funny levity personality",
                ),
                _row(
                    id="profanity_level",
                    label="Profanity ceiling",
                    help=(
                        "How coarse VOOL is allowed to be when the register calls for it. Injected "
                        "into every turn's context alongside the boundaries setting."
                    ),
                    kind="range",
                    min=0,
                    max=100,
                    step=10,
                    unit="%",
                    read={"url": PREFS, "field": "profanity_level"},
                    write={"url": PREFS, "field": "profanity_level", "type": "int"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="profanity swearing language coarse crude ceiling",
                ),
                _row(
                    id="character_mode",
                    label="Character",
                    help=(
                        "A persona VOOL stays in until you clear it. Empty means VOOL as itself. "
                        "This is a strong instruction: while it is set, VOOL is told to keep that "
                        "character's voice and not to break character. Saying \"act like a pirate\" "
                        "in chat sets this, and nothing you can say in chat sets it back — emptying "
                        "this field is the way out."
                    ),
                    kind="text",
                    placeholder="empty — VOOL as itself",
                    maxlength=120,
                    read={"url": PREFS, "field": "character_mode"},
                    write={"url": PREFS, "field": "character_mode", "type": "str"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="character persona roleplay act as pretend voice",
                ),
                _row(
                    id="style_notes",
                    label="Style notes",
                    help=(
                        "Anything else about how you want VOOL to write — \"be concise\", \"no "
                        "bullet lists\", \"always show units\". Passed to the model as written. "
                        "Saying this in chat no longer lands here: a free-form style request becomes "
                        "a Memory item you can see and forget. This field stays because the runtime "
                        "still reads it, so anything set before that change is still in effect, and "
                        "this is the only place left to read it or clear it."
                    ),
                    kind="text",
                    placeholder="e.g. be concise; no bullet lists",
                    maxlength=600,
                    read={"url": PREFS, "field": "style_notes"},
                    write={"url": PREFS, "field": "style_notes", "type": "str"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="style notes writing instructions concise format preferences",
                ),
                _row(
                    id="answer_language",
                    label="Answer language",
                    kind="custom",
                    widget="answer_language",
                    help=(
                        "The language VOOL answers in when your message does not ask for one. "
                        "Asking inside a message still wins for that message: \"answer in Polish\" "
                        "is obeyed whatever is set here. App language at the top of Settings "
                        "controls the interface separately. This preference does not make VOOL "
                        "equally good in every language: the "
                        "model answers as well as it can, and tools and web results are unaffected."
                    ),
                    read={"url": PROFILE},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords=(
                        "language answer reply respond lithuanian polish spanish french german "
                        "japanese chinese russian ukrainian translate locale tongue"
                    ),
                ),
                _row(
                    id="show_workflow",
                    label="Show VOOL's work",
                    help="Show the steps VOOL took to reach an answer, rather than only the answer.",
                    kind="toggle",
                    read={"url": PREFS, "field": "show_workflow"},
                    write={"url": PREFS, "field": "show_workflow", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="workflow steps thinking work log show reasoning summary transparency",
                ),
                _row(
                    id="deep_reasoning",
                    label="Deep reasoning",
                    help=(
                        "Let the local model think before it answers. Noticeably slower, and on a "
                        "thinking-capable local model it can be much slower — the reasoning is "
                        "generated before a single word of the answer appears."
                    ),
                    kind="toggle",
                    read={"url": PREFS, "field": "deep_reasoning"},
                    write={"url": PREFS, "field": "deep_reasoning", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="thinking reasoning slow think first chain of thought",
                ),
            ],
        },
        {
            "id": "permissions",
            "icon": "⛊",
            "title": "Privacy & Permissions",
            "blurb": "What VOOL may do on its own, and where your data sits.",
            "rows": [
                _row(
                    id="autonomy_mode",
                    label="Autonomy",
                    help=(
                        "How much VOOL does before stopping to ask. Read the order carefully — it "
                        "runs the way the runtime actually gates actions "
                        "(core/execution_gate.py): Hands-off asks about the FEWEST things, Strict "
                        "asks about everything. Whatever you pick, anything outward-facing (a post, "
                        "a message) or privacy-sensitive always asks first, and spending always asks."
                    ),
                    kind="select",
                    options=[
                        {"value": "hands_off", "label": "Hands-off — routine local steps run unprompted"},
                        {"value": "balanced", "label": "Balanced — also asks before anything destructive"},
                        {"value": "strict", "label": "Strict — asks before every action"},
                    ],
                    read={"url": PREFS, "field": "autonomy_mode"},
                    write={"url": PREFS, "field": "autonomy_mode", "type": "str"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="autonomy permission approve approval ask acting hands off strict",
                ),
                _row(
                    id="boundaries_mode",
                    label="Content boundaries",
                    help=(
                        "How cautious VOOL is about subject matter. \"As I set them\" is the "
                        "default and means VOOL follows what you have told it in conversation "
                        "rather than a preset."
                    ),
                    kind="select",
                    options=[
                        {"value": "user_defined", "label": "As I set them in conversation"},
                        {"value": "relaxed", "label": "Relaxed"},
                        {"value": "standard", "label": "Standard"},
                        {"value": "strict", "label": "Strict"},
                    ],
                    read={"url": PREFS, "field": "boundaries_mode"},
                    write={"url": PREFS, "field": "boundaries_mode", "type": "str"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="boundaries content limits safe relaxed standard strict subject matter",
                ),
                _row(
                    id="mode_note",
                    label="Approval mode for a single chat",
                    kind="note",
                    help=(
                        "Manual, Auto and Bypass belong to ONE conversation, not to the app — a "
                        "bypass grant is scoped to a task and expires. That is why it is set from "
                        "the mode control in the composer, next to the chat it applies to, and is "
                        "not duplicated here."
                    ),
                    keywords="manual auto bypass mode approval per chat composer",
                ),
                _row(
                    id="privacy_disclosure",
                    label="Privacy & data, as it stands",
                    kind="custom",
                    widget="extras_privacy",
                    help=(
                        "What is true today about where data lives, what is sealed, what leaves this "
                        "machine and when. A disclosure, not a set of switches: retention and "
                        "cloud-learning toggles are absent because nothing enforces them yet."
                    ),
                    keywords="privacy data disclosure sealed keys spend gated cloud explicit local first retention",
                ),
                _row(
                    id="locality_note",
                    label="Where your data is",
                    kind="note",
                    help=(
                        "Conversations, receipts, preferences and profile items are files on this "
                        "machine. Provider keys are sealed at rest here too. That is data locality, "
                        "and it is separate from whether VOOL may reach the network: a cloud model "
                        "or a live web lookup sends the text of that request to that provider, and "
                        "only when a turn actually uses one."
                    ),
                    keywords="privacy local data locality network browse cloud where stored",
                ),
            ],
        },
        {
            "id": "keys",
            "icon": "⚿",
            "title": "API Keys",
            "blurb": "Keys you bring. Stored sealed on this machine, never shown again.",
            "rows": [
                _row(
                    id="cloud_keys",
                    label="Your keys",
                    kind="custom",
                    widget="cloud_keys",
                    help=(
                        "Keys are their own thing, not a model setting: one store holds the model "
                        "providers AND the web-search providers (core/credential_store.py), and "
                        "some of it is neither — so they live together here rather than being split "
                        "across the sections that happen to use them. VOOL runs local and free "
                        "without any of them. Saving a key asks that one service once whether it accepts "
                        "the key (model providers answer an auth check that spends nothing; search providers "
                        "answer one small search) and stores only a verified key; a model provider's key "
                        "becomes the active cloud provider."
                    ),
                    read={"url": CREDENTIALS},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords=(
                        "api key openai anthropic claude openrouter groq gemini deepseek kimi byok "
                        "credential provider brave tavily exa serper firecrawl jina search token secret"
                    ),
                ),
            ],
        },
        {
            "id": "email",
            "icon": "✉",
            "title": "Email",
            "blurb": "Which mailbox VOOL reads and sends from, and whether sending is on hold.",
            "rows": [
                _row(
                    id="email_accounts",
                    label="Email accounts",
                    kind="custom",
                    widget="email_accounts",
                    help=(
                        "Every configured mailbox, what it can do (read, send), its state, and which one "
                        "is the default. The default is one Operator Profile item (an opaque credential "
                        "name, core/email_accounts.py resolves it): a chat's own choice outranks it, an "
                        "explicit account in a request outranks both, and an account that cannot do what "
                        "a step needs is refused with the accounts that can -- VOOL never falls back to "
                        "another mailbox. Choosing a default stores a name and contacts nobody; passwords "
                        "and grants are never shown here."
                    ),
                    read={"url": EMAIL_ACCOUNTS},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords=(
                        "email account mailbox default gmail outlook graph microsoft imap smtp icloud "
                        "reconnect work personal inbox sender identity which account"
                    ),
                ),
                _row(
                    id="email_recovery",
                    label="Sending hold",
                    kind="custom",
                    widget="email_recovery",
                    help=(
                        "When the email draft store is recovered from damaged bytes that recorded a send "
                        "whose outcome is unknown, NEW sends are held until you acknowledge it here "
                        "(core/email_drafts.py). This shows what was preserved, the Message-IDs it named, "
                        "a check of each against the account's own sent mail, and exactly what "
                        "acknowledging does: it releases new approved sends and resends nothing. The "
                        "acknowledgement names the exact recovery it was read for; a page that is out of "
                        "date cannot release a newer one. The model cannot do any of this."
                    ),
                    read={"url": EMAIL_RECOVERY},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords=(
                        "email sending hold recovery quarantine draft store acknowledge release "
                        "unresolved send unknown delivery message-id reconcile"
                    ),
                ),
            ],
        },
        {
            "id": "models",
            "icon": "◈",
            "title": "Models & Providers",
            "blurb": "Which brain answers, and which providers are actually eligible.",
            "rows": [
                _row(
                    id="models_overview",
                    label="What will answer",
                    kind="custom",
                    widget="models_overview",
                    help=(
                        "One overview of the model that answers and the providers behind it: "
                        "connection, spending limits and recovery, each action going through the "
                        "authority that already owns it. The classic per-provider controls remain "
                        "in the rows below."
                    ),
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="models providers overview usepod openrouter local setup guided budget price limits review",
                ),
                _row(
                    id="model_pin",
                    label="Model",
                    kind="custom",
                    widget="model_pin",
                    help=(
                        "Preference and eligibility are different things. A model you pin here is "
                        "what VOOL asks for; what it can actually use also depends on a stored key "
                        "for that provider and on the provider accepting the request. When the two "
                        "differ, the effective lane is named below rather than silently substituted."
                    ),
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="model pin cloud local auto free paid provider eligibility lane",
                ),
                _row(
                    id="local_models",
                    label="Local models",
                    kind="custom",
                    widget="local_models",
                    help=(
                        "Models installed in Ollama on this machine. Registering one adds it as a "
                        "lane VOOL can route to; certifying it runs the sealed tool probe that "
                        "decides whether it may write final answers. Nothing is downloaded and "
                        "nobody is contacted — this reads your local Ollama only."
                    ),
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords=(
                        "local model ollama register certify certification lane offline "
                        "private qwen installed add"
                    ),
                ),
                _row(
                    id="usepod",
                    label="UsePod marketplace",
                    kind="custom",
                    widget="usepod",
                    read={"url": "/api/cloud/usepod/discovery"},
                    help=(
                        "The UsePod provider's own panel: what its marketplace lists and costs "
                        "(with the source and age of every price), what your credential sees, "
                        "which route each model may serve, and the approvals that gate paid "
                        "dispatch. Opening this reads caches only; the one refresh button is the "
                        "only thing here that contacts UsePod, and it runs no inference."
                    ),
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords=(
                        "usepod marketplace proxy token prepaid x402 route approve ceiling "
                        "price balance model discovery origin"
                    ),
                ),
            ],
        },
        {
            "id": "memory",
            "icon": "◑",
            "title": "Memory & Personalisation",
            "blurb": "What VOOL remembers about you — yours to edit and to forget.",
            "rows": [
                _row(
                    id="profile_items",
                    label="What VOOL remembers",
                    kind="custom",
                    widget="profile",
                    help=(
                        "Each item shows its value, scope, origin and last use. Edit, Forget, Move "
                        "scope and Restore previous act on one item at a time; Pause stops VOOL "
                        "learning anything new until you resume; Export gives you every active item "
                        "as one document. Nothing remembered here grants VOOL permission to read, "
                        "send, post or spend."
                    ),
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="memory remember profile forget scope personalisation personalization name called address signature pause resume export edit restore undo",
                ),
                _row(
                    id="learned_facts",
                    label="Facts VOOL learned in conversation",
                    kind="custom",
                    widget="extras_memory",
                    help=(
                        "A second store, separate from the profile items above: facts VOOL confirmed "
                        "during chats (core/memory/entries.py), each shown with the scope it was stored "
                        "under and where it came from. Forget removes exactly that record and its "
                        "mirror line; nothing else is touched. Reads are governed per chat, so what is "
                        "listed is what those chats may still see."
                    ),
                    keywords="memory facts learned remembered forget entries store conversation confirmed",
                ),
                _row(
                    id="remembered_identity_note",
                    label="Your name, and your email signature",
                    kind="note",
                    help=(
                        "Neither is a setting any more. Both were moved out of the preferences "
                        "file into this memory (core/user_preferences.py: "
                        "migrate_legacy_profile_fields), because VOOL learns them from "
                        "conversation and keeps them across chats. They appear in the list above "
                        "with everything else it remembers, and they are changed the same way — by "
                        "telling VOOL, or by editing the item. A second field here would be a "
                        "second writer for a value that already has enough of them."
                    ),
                    keywords="name called address me preferred name email signature identity remembered",
                ),
            ],
        },
        {
            "id": "backup",
            "icon": "⇅",
            "title": "Backup & restore",
            "blurb": "Carry one chat to another machine, or bring one back, as a signed bundle.",
            "rows": [
                _row(
                    id="session_export",
                    label="Export a chat",
                    kind="custom",
                    widget="bundle_export",
                    help=(
                        "A .voolsession file carries one conversation's turns, receipts and evidence "
                        "references, signed by this machine's key. Encryption is recommended for any "
                        "bundle leaving this computer; an unencrypted export needs the warning "
                        "acknowledged first. Model-internal reasoning is not part of a bundle, and the "
                        "preview states exactly what will be written before anything is."
                    ),
                    keywords="export backup bundle voolsession chat save file passphrase encrypt download portable",
                ),
                _row(
                    id="session_import",
                    label="Import a chat",
                    kind="custom",
                    widget="bundle_import",
                    help=(
                        "Pick a .voolsession file, preview what it contains and who signed it, then "
                        "import. A bundle signed by an unknown key is refused unless you accept it, and "
                        "stays marked untrusted. An import never modifies an existing chat: if the id is "
                        "already here, the copy lands under a new one. Passphrases exist only in these "
                        "fields, never in history, logs or receipts."
                    ),
                    keywords="import restore bundle voolsession file passphrase untrusted signer preview recover",
                ),
            ],
        },
        {
            "id": "network",
            "icon": "◇",
            "title": "Agent Network",
            "blurb": "Unfinished research — not part of the production runtime.",
            "rows": [
                _row(
                    id="network_research_note",
                    label="Research only - not active in this build",
                    help=(
                        "Agent-to-agent networking (shared tasks, idle research, the agent "
                        "commons) is unfinished research. It does not run, connect or "
                        "advertise in a production VOOL build, and these switches have no "
                        "effect here. It becomes available only under an explicit research "
                        "invocation (VOOL_RESEARCH_NETWORKING=1)."
                    ),
                    kind="note",
                    keywords="research unfinished mesh hive swarm network agents disabled",
                ),
                *([] if not _research_networking() else [
                _row(
                    id="accept_hive_tasks",
                    label="Take shared research tasks",
                    help=(
                        "Let VOOL claim work from the shared task pool while it is idle. Off means "
                        "VOOL stays visible to other agents but claims nothing."
                    ),
                    kind="toggle",
                    read={"url": PREFS, "field": "accept_hive_tasks"},
                    write={"url": PREFS, "field": "accept_hive_tasks", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords="hive tasks shared research swarm claim work idle",
                ),
                _row(
                    id="idle_research_assist",
                    label="Research while idle",
                    help="Let VOOL keep researching an open question when you are not typing.",
                    kind="toggle",
                    read={"url": PREFS, "field": "idle_research_assist"},
                    write={"url": PREFS, "field": "idle_research_assist", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords="idle research assist background work autonomous",
                ),
                _row(
                    id="hive_followups",
                    label="Tell me what came back",
                    help="Show follow-up messages when shared research finishes, instead of staying quiet.",
                    kind="toggle",
                    read={"url": PREFS, "field": "hive_followups"},
                    write={"url": PREFS, "field": "hive_followups", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="followups updates hive notifications heartbeat research results",
                ),
                _row(
                    id="social_commons",
                    label="Talk to other agents",
                    help="Let VOOL join the agent commons to brainstorm with other agents.",
                    kind="toggle",
                    read={"url": PREFS, "field": "social_commons"},
                    write={"url": PREFS, "field": "social_commons", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords="commons social agents brainstorm hangout socialise socialize",
                ),
                ])
            ],
        },
        {
            "id": "budgets",
            "icon": "◴",
            "title": "Usage & Budgets",
            "blurb": "What has been spent, and which limits the runtime enforces rather than merely notes.",
            "rows": [
                _row(
                    id="ram_reserve_pct",
                    label="RAM kept free for you",
                    help=(
                        "Enforced live by the resource governor: a heavy local task will not take "
                        "this share of memory. A bigger reserve means VOOL uses less. This is a hard "
                        "limit, not advice."
                    ),
                    kind="range",
                    min=10,
                    max=80,
                    step=5,
                    unit="%",
                    read={"url": PREFS, "field": "ram_reserve_pct"},
                    write={"url": PREFS, "field": "ram_reserve_pct", "type": "int"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    badge="Enforced",
                    keywords="ram memory reserve governor resources hard limit",
                ),
                _row(
                    id="daily_token_budget",
                    label="Daily cloud token budget",
                    help=(
                        "A number VOOL is told about, not a cut-off it enforces: no code stops a "
                        "turn when this is reached, and the usage table below is not charted "
                        "against it. 0 means unlimited. Treat it as guidance until a hard cut-off "
                        "ships."
                    ),
                    kind="number",
                    min=0,
                    step=1000,
                    read={"url": PREFS, "field": "daily_token_budget"},
                    write={"url": PREFS, "field": "daily_token_budget", "type": "int"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    badge="Guidance only",
                    keywords="budget tokens spend cost daily cloud cap limit",
                ),
                _row(
                    id="usage",
                    label="Token usage",
                    kind="custom",
                    widget="usage",
                    help="Tokens that ran free on a local model against tokens that ran on a paid cloud lane, per model.",
                    scope=SCOPE_GLOBAL,
                    keywords="usage tokens statistics spent local cloud paid free",
                ),
            ],
        },
        {
            # Crypto (Pilot): off by default, discoverable when off. The section is absent in the School edition by the
            # edition floor (core/product_edition.py), never by a scattered check here.
            "id": "calendars",
            "icon": "\U0001f4c5",
            "title": "Calendars",
            "blurb": "The calendars VOOL reads for your agenda and alerts, and where new events go.",
            "rows": [
                _row(
                    id="calendar_accounts_panel",
                    label="Calendar accounts",
                    help=(
                        "Connect Google Calendar, Microsoft 365, an iCloud or other CalDAV server, or Apple Calendar on this "
                        "Mac. Nothing is fetched until you choose calendars and turn sync on, and a credential is chosen by "
                        "its binding, never typed here."
                    ),
                    kind="custom",
                    widget="calendar_accounts",
                    keywords=["calendar", "calendars", "google", "outlook", "microsoft", "icloud", "caldav", "apple calendar", "eventkit", "sync", "alerts", "default calendar", "account"],
                ),
            ],
        },
        {
            "id": "notifications",
            "icon": "\U0001f514",
            "title": "Notifications",
            "blurb": "Calendar alerts, reminders, quiet hours and macOS notifications.",
            "rows": [
                _row(
                    id="notifications_panel",
                    label="Alerts and macOS notifications",
                    help=(
                        "The bell keeps every alert. Turn on macOS notifications for an extra copy, set quiet hours, sound "
                        "and what a notification may show, and send a test notification."
                    ),
                    kind="custom",
                    widget="notifications",
                    keywords=["notifications", "alerts", "bell", "macos", "banner", "quiet hours", "sound", "lock screen", "test notification", "permission", "reminders"],
                ),
            ],
        },
        {
            "id": "wallet",
            "icon": "◇",
            "title": "Crypto",
            "badge": "Pilot",
            "blurb": "A pilot feature, off by default. Chat and every other tool work without it. Turning it on creates, signs and pays nothing.",
            "rows": [
                _row(
                    id="wallet_enabled",
                    label="Enable Crypto",
                    badge="Pilot",
                    help=(
                        "Off by default. On, this section lists the supported networks with what each can do right now and lets you "
                        "create a VOOL wallet on this device (credential first, one backup shown once). Nothing is created, signed or "
                        "sent by turning this on. This code has not been audited by an external party: keep small test amounts and "
                        "do not use it as a main wallet."
                    ),
                    kind="toggle",
                    read={"url": PREFS, "field": "wallet_enabled"},
                    write={"url": PREFS, "field": "wallet_enabled", "type": "bool"},
                    keywords=["wallet", "crypto", "pilot", "enable", "solana", "base", "ethereum", "bnb", "robinhood"],
                ),
                _row(
                    id="wallet_safety",
                    label="Stay safe: how people lose their crypto",
                    help="Short, plain explanations of the tricks that empty wallets, with a picture each. Two minutes, once.",
                    kind="custom",
                    widget="scam_school",
                    read={"url": "/api/wallet/safety"},
                    keywords=["scam", "phishing", "airdrop", "migration", "support", "seed", "phrase", "private key", "approval", "drain", "safety", "stay safe"],
                ),
                _row(
                    id="crypto_networks",
                    label="Networks and wallets",
                    help=(
                        "Every supported network from the runtime's own registry, in its order: what each row can do now, the "
                        "accounts on it with their address and balance, Create wallet where none exists, and Developer options "
                        "for Test networks. Existing accounts keep Receive, balance refresh, payment history and recovery."
                    ),
                    kind="custom",
                    widget="crypto",
                    keywords=["wallet", "crypto", "payment", "payments", "solana", "base", "ethereum", "bnb", "robinhood", "pin", "backup", "receipt", "testnet", "devnet", "mainnet", "developer"],
                ),
                _row(
                    id="wallet_legacy",
                    label="Other accounts: watch-only, connected wallets, legacy pocket",
                    help=(
                        "The earlier account kinds stay reachable here: a watch-only address, a connected Phantom or EVM wallet "
                        "(signing stays in that wallet), and the phrase-based legacy pocket (test networks only). New VOOL wallets "
                        "are created above."
                    ),
                    kind="custom",
                    widget="wallet",
                    keywords=["watch-only", "phantom", "evm", "external", "connect", "legacy", "pocket", "phrase"],
                ),
            ],
        },
        {
            "id": "companion",
            "icon": "◐",
            "title": "Appearance",
            "blurb": "The pixel pet, on the desktop or docked in the app.",
            "rows": [
                _row(
                    id="companion_pet_panel",
                    label="Desktop pet",
                    help=(
                        "The pet can sit on the desktop as its own small window, above ordinary "
                        "windows but never above a dialog you have to answer. These are presentation "
                        "controls only: hiding or moving the pet never stops a task, a chat or the "
                        "runtime."
                    ),
                    kind="custom",
                    widget="companion_pet",
                    keywords=["appearance", "character", "palette", "pet", "companion", "desktop", "sprite", "pixel", "hide", "reset", "position", "return", "detach"],
                ),
            ],
        },
        {
            "id": "advanced",
            "icon": "⌥",
            "title": "Advanced",
            "blurb": "The runtime's own surfaces, served by this same daemon.",
            "rows": [
                _row(
                    id="advanced_surfaces",
                    label="Runtime surfaces",
                    kind="custom",
                    widget="advanced_links",
                    help=(
                        "Two pages this runtime serves alongside chat and Settings. The trace rail "
                        "(/trace) shows the running turn's events, receipts and obligations as the "
                        "runtime records them. The Web0 browser (/web0) is the entry page for .null "
                        "sites, which an ordinary browser cannot open. Both open in their own tab or "
                        "window and leave Settings where it is."
                    ),
                    keywords="advanced trace rail task rail events receipts web0 null browser diagnostics runtime surfaces",
                ),
            ],
        },
        {
            "id": "about",
            "icon": "ⓘ",
            "title": "About & Diagnostics",
            "blurb": "The exact build answering you, and how to report a problem.",
            "rows": [
                _row(
                    id="build",
                    label="This build",
                    kind="custom",
                    widget="build",
                    help=(
                        "The precise build answering you right now. Quote this when reporting a "
                        "problem — a short commit alone does not identify a build."
                    ),
                    read={"url": VERSION},
                    keywords="version build commit about diagnostics identify",
                ),
                _row(
                    id="toolbelt",
                    label="Toolbelt",
                    kind="custom",
                    widget="extras_toolbelt",
                    help=(
                        "The capability inventory: local models, the cloud connection, web-search "
                        "connections and plugins, each with its real probed state. A capability never "
                        "probed reads UNKNOWN, which is neither failed nor available. Opening it reads "
                        "local state only."
                    ),
                    keywords="toolbelt capabilities inventory plugins connections local models cloud status unknown",
                ),
            ],
        },
    ]


# --------------------------------------------------------------------------------------
# The page. Self-contained: inline CSS and JS, no build step, no CDN, no external font.
# Colour tokens are the chat surface's tokens verbatim, so the two windows are one product.
# --------------------------------------------------------------------------------------
_SETTINGS_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VOOL Settings</title>
<style>
/* Models & Providers redesign (overview / cards / guided / review sheet) */
.mp-root { gap: 10px; }
.mp-what .row-label { margin-bottom: 2px; }
.mp-problems { border: 1px solid var(--warn, #f0b429); border-radius: 10px; padding: 8px 12px; }
.mp-problem { color: var(--warn, #f0b429); font-size: 12.5px; }
.mp-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.mp-card { flex: 1 1 220px; border: 1px solid var(--border, #262b35); border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 6px; background: var(--panel, #16191f); }
.mp-card.mp-ready { border-color: var(--accent, #5eead4); }
.mp-card-title { font-weight: 700; font-size: 13px; }
.mp-chip { display: inline-block; margin-right: 8px; font-size: 12px; color: var(--muted, #9aa1af); }
.mp-chip.mp-now { color: var(--ink, #e8eaf0); font-weight: 700; border-bottom: 2px solid var(--accent, #5eead4); }
.mp-chip.mp-done { color: var(--accent, #5eead4); }
.mp-picker { max-height: 300px; overflow-y: auto; border: 1px solid var(--border, #262b35); border-radius: 10px; }
.mp-model-row { display: block; width: 100%; text-align: left; background: transparent; border: 0; border-bottom: 1px solid var(--border, #262b35); padding: 8px 10px; color: var(--ink, #e8eaf0); font: inherit; font-size: 12.5px; cursor: pointer; }
.mp-model-row.mp-picked { background: rgba(94, 234, 212, .12); }
.mp-sheet { position: fixed; inset: 0; background: rgba(5,7,10,.65); z-index: 1400; }
.mp-sheet-head { font-weight: 700; padding: 14px 18px; border-bottom: 1px solid var(--border, #262b35); color: var(--warn, #f0b429); width: min(560px, 92vw); margin: 6vh auto 0; background: var(--panel, #16191f); border-left: 1px solid var(--border, #262b35); border-right: 1px solid var(--border, #262b35); }
.mp-sheet-body { max-height: 60vh; overflow-y: auto; padding: 12px 18px; width: min(560px, 92vw); margin: 0 auto; background: var(--panel, #16191f); border-left: 1px solid var(--border, #262b35); border-right: 1px solid var(--border, #262b35); }
.mp-sheet-steps { margin-top: 8px; }
.mp-step { font-size: 12.5px; padding: 2px 0; color: var(--muted, #9aa1af); }
.mp-step-done { color: var(--accent, #5eead4); }
.mp-step-failed { color: #ff8f8f; }
.mp-step-run { color: var(--ink, #e8eaf0); }
.mp-sheet-note { font-size: 12px; color: var(--muted, #9aa1af); margin-top: 8px; white-space: pre-wrap; }
.mp-sheet-actions { display: flex; gap: 8px; padding: 12px 18px 14px; width: min(560px, 92vw); margin: 0 auto; background: var(--panel, #16191f); border: 1px solid var(--border, #262b35); }
.mp-detail { gap: 10px; }
.mp-back { align-self: flex-start; margin-bottom: 4px; }

:root { --bg:#101216; --panel:#16191f; --field:#1d2129; --ink:#e8eaf0; --muted:#9aa1af; --accent:#5eead4; --accent2:#34d399; --grad:linear-gradient(135deg,#5eead4,#34d399); --border:#262b35; --active:#1d2129; --ok:#34d399; --bad:#f87171; --warn:#fbbf24; }
* { box-sizing:border-box; }
html, body { height:100%; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,'Inter',system-ui,Segoe UI,Roboto,sans-serif; display:flex; overflow:hidden; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:6px; }

/* ---- sidebar ---- */
#side { flex:0 0 248px; width:248px; background:var(--panel); border-right:1px solid var(--border); display:flex; flex-direction:column; min-height:0; }
/* App-language control: top-left, above everything else in the nav. Compact (globe + the
   language's own name); the honest coverage note sits under it. */
#uiLocaleWrap { padding:12px 12px 4px; border-bottom:1px solid var(--border); margin-bottom:8px; }
#uiLocaleWrap .loc-label { display:flex; align-items:center; gap:6px; font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:5px; }
#uiLocaleWrap .loc-globe { font-size:14px; line-height:1; }
#uiLocaleWrap .loc-word .set-sub { display:block; text-transform:none; letter-spacing:0; font-size:10.5px; color:var(--muted); margin-top:1px; }
#uiLocaleSelect { width:100%; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:6px 8px; font:inherit; font-size:13px; }
#uiLocaleSelect:focus { outline:none; border-color:var(--accent); }
#uiLocaleSelect:disabled { opacity:.55; cursor:not-allowed; }
#uiLocaleWrap .loc-note { font-size:11px; line-height:1.5; color:var(--muted); margin:6px 0 2px; }
#backRow { padding:12px 12px 4px; }
#back { display:flex; align-items:center; gap:8px; width:100%; background:transparent; color:var(--muted); border:none; border-radius:8px; padding:6px 8px; font:inherit; font-size:13px; cursor:pointer; text-align:left; }
#back:hover { color:var(--ink); background:var(--active); }
#searchWrap { padding:4px 12px 10px; }
#searchBox { position:relative; display:flex; align-items:center; }
#searchIcon { position:absolute; left:9px; color:var(--muted); font-size:12px; pointer-events:none; }
#search { width:100%; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:7px 10px 7px 27px; font:inherit; font-size:13px; }
#search:focus { outline:none; border-color:var(--accent); }
#search::placeholder { color:var(--muted); }
#nav { flex:1 1 auto; overflow-y:auto; padding:0 8px 12px; min-height:0; }
.nav-sec { color:var(--muted); font-size:11px; font-weight:700; letter-spacing:.6px; text-transform:uppercase; padding:12px 8px 4px; }
.nav-item { display:flex; align-items:center; gap:9px; width:100%; background:transparent; color:var(--ink); border:none; border-radius:8px; padding:7px 9px; font:inherit; font-size:13.5px; cursor:pointer; text-align:left; }
.nav-item:hover { background:var(--active); }
/* Selection is the background plus the tinted icon below. No accent rail: the background alone
   would be indistinguishable from :hover, so the icon carries the difference instead. */
.nav-item[aria-current="true"] { background:var(--active); color:var(--ink); }
.nav-item .ico { flex:0 0 16px; width:16px; text-align:center; color:var(--muted); font-size:13px; }
.nav-item[aria-current="true"] .ico { color:var(--accent); }
.nav-item .hits { margin-left:auto; color:var(--accent); font-size:11px; font-weight:700; }
.nav-empty { color:var(--muted); font-size:12.5px; padding:10px 9px; }
.scam-rule { border-left:3px solid #f87171; padding:8px 12px; margin:4px 0 12px; font-weight:600; line-height:1.4; }
.scam-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); gap:12px; }
.scam-card { display:flex; gap:12px; border:1px solid var(--border); border-radius:10px; padding:12px; background:var(--panel, transparent); }
.scam-ill { flex:0 0 64px; color:var(--ink); }
.scam-ill svg { width:64px; height:64px; display:block; }
.scam-body { min-width:0; font-size:12.5px; line-height:1.4; }
.scam-title { font-weight:700; margin-bottom:6px; font-size:13px; }
.scam-line { margin:3px 0; color:var(--muted); }
.scam-line.scam-do { color:var(--ink); font-weight:600; }
.scam-principle { margin-top:12px; padding:8px 12px; border-left:3px solid #34d399; font-weight:600; }

/* ---- content ---- */
#pane { flex:1 1 auto; overflow-y:auto; min-width:0; padding:34px 40px 80px; }
#paneInner { max-width:760px; margin:0 auto; }
h1 { margin:0 0 4px; font-size:26px; font-weight:700; letter-spacing:-.2px; }
.pane-blurb { color:var(--muted); font-size:13px; margin:0 0 22px; }
.card { border:1px solid var(--border); border-radius:12px; background:var(--panel); overflow:hidden; margin-bottom:18px; }
.row { display:flex; align-items:flex-start; gap:18px; padding:14px 16px; border-bottom:1px solid var(--border); }
.row:last-child { border-bottom:none; }
.row.hit { background:rgba(94,234,212,.07); box-shadow:inset 3px 0 0 var(--accent); }
.row-main { flex:1 1 auto; min-width:0; }
.row-label { font-size:13.5px; font-weight:600; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.row-help { color:var(--muted); font-size:12.5px; line-height:1.5; margin-top:4px; }
.row-meta { display:flex; gap:10px; flex-wrap:wrap; margin-top:7px; font-size:11.5px; color:var(--muted); }
.row-meta .m { display:inline-flex; align-items:center; gap:4px; }
.row-ctl { flex:0 0 auto; display:flex; flex-direction:column; align-items:flex-end; gap:6px; min-width:0; padding-top:1px; }
.badge { font-size:10.5px; font-weight:700; letter-spacing:.3px; text-transform:uppercase; padding:2px 7px; border-radius:999px; border:1px solid var(--border); color:var(--muted); }
.badge.enforced { color:var(--accent); border-color:var(--accent); }
.badge.guidance { color:var(--warn); border-color:var(--warn); }
.badge.pilot { color:var(--accent); border-color:var(--accent); }

/* ---- controls ---- */
.inp { background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:7px 10px; font:inherit; font-size:13px; min-width:200px; }
.inp:focus { outline:none; border-color:var(--accent); }
select.inp { min-width:220px; cursor:pointer; }
input[type=number].inp { min-width:140px; }
.rangewrap { display:flex; align-items:center; gap:10px; }
.rangewrap input[type=range] { width:190px; accent-color:var(--accent); cursor:pointer; }
.rangeval { color:var(--accent); font-weight:700; font-size:13px; min-width:44px; text-align:right; }
.sw { position:relative; width:44px; height:25px; flex:0 0 auto; background:var(--field); border:1px solid var(--border); border-radius:999px; cursor:pointer; padding:0; transition:background .14s,border-color .14s; }
.sw::after { content:""; position:absolute; top:2px; left:2px; width:19px; height:19px; border-radius:50%; background:var(--muted); transition:transform .14s,background .14s; }
.sw[aria-checked="true"] { background:rgba(94,234,212,.22); border-color:var(--accent); }
.sw[aria-checked="true"]::after { transform:translateX(19px); background:var(--accent); }
.sw:disabled { opacity:.45; cursor:not-allowed; }
.btn { background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:6px 13px; font:inherit; font-size:12.5px; font-weight:600; cursor:pointer; }
.btn:hover:not(:disabled) { border-color:var(--accent); }
.btn:disabled { opacity:.45; cursor:not-allowed; }
.btn.primary { background:var(--grad); color:#0b0f14; border:none; }
.btn.danger { color:var(--bad); border-color:var(--bad); }

/* ---- per-row save state: never optimistic ---- */
.state { font-size:11.5px; display:flex; align-items:center; gap:5px; min-height:16px; }
.state.idle { color:transparent; }
.state.loading, .state.saving { color:var(--muted); }
.state.saved { color:var(--ok); }
.state.failed { color:var(--bad); font-weight:600; }
.state.stale { color:var(--warn); }
.state .retry { background:transparent; border:none; color:var(--accent); font:inherit; font-size:11.5px; font-weight:700; cursor:pointer; padding:0 0 0 4px; text-decoration:underline; }
.fail-note { color:var(--bad); font-size:12px; margin-top:6px; text-align:right; max-width:280px; }
.note-body { color:var(--muted); font-size:12.5px; line-height:1.55; }
.subtle { color:var(--muted); font-size:12.5px; }
.mono { font-family:ui-monospace,Consolas,monospace; font-size:12px; word-break:break-all; user-select:text; -webkit-user-select:text; }
dl.kv { display:grid; grid-template-columns:auto minmax(0,1fr); gap:6px 14px; margin:0; font-size:12.5px; }
dl.kv dt { color:var(--muted); white-space:nowrap; }
dl.kv dd { margin:0; }
.stack { display:flex; flex-direction:column; gap:10px; width:100%; }
.inline { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.row.wide { flex-direction:column; align-items:stretch; }
.row.wide .row-ctl { align-items:stretch; width:100%; padding-top:12px; }
.pill { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; color:var(--muted); border:1px solid var(--border); border-radius:999px; padding:3px 9px; }
.pill .dot { width:7px; height:7px; border-radius:50%; background:var(--muted); }
.pill.on .dot { background:var(--ok); }
.pill.off .dot { background:var(--bad); }
.empty { color:var(--muted); font-size:12.5px; padding:10px 0; }
.profile-item { border-top:1px solid var(--border); padding-top:10px; }
/* a mounted extras section sits under its own row label, so the fragment's heading is redundant here */
.extras-host .vs-sec { margin-top:0; }
.extras-host .vs-sec h4 { display:none; }
.sb-box { border:1px solid var(--border); border-radius:8px; padding:10px 12px; background:var(--bg); }
input[type=file].inp { min-width:0; }
label.inline { cursor:pointer; }
.profile-scope-select { min-width:120px; }
.profile-export { max-height:240px; overflow:auto; background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:8px 10px; white-space:pre-wrap; margin:0; }

/* ---- narrow windows ---- */
@media (max-width: 720px) {
  body { flex-direction:column; overflow:auto; }
  #side { flex:0 0 auto; width:100%; border-right:none; border-bottom:1px solid var(--border); }
  #nav { max-height:190px; }
  #pane { padding:20px 16px 60px; }
  .row { flex-direction:column; gap:10px; }
  .row-ctl { align-items:stretch; width:100%; }
  .rangewrap input[type=range] { width:100%; }
}
@media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
</style>
</head>
<body>
<nav id="side" aria-label="Settings sections">
  <div id="backRow"><button id="back" type="button">&larr; Back to VOOL</button></div>
  <div id="searchWrap">
    <div id="searchBox">
      <span id="searchIcon" aria-hidden="true">&#9906;</span>
      <input id="search" type="search" placeholder="Search settings" aria-label="Search settings" autocomplete="off" spellcheck="false">
    </div>
  </div>
  <div id="nav" role="list"></div>
</nav>
<main id="pane" tabindex="-1">
  <div id="paneInner">
    <h1 id="paneTitle">Settings</h1>
    <p class="pane-blurb" id="paneBlurb"></p>
    <div id="paneBody"></div>
  </div>
</main>
<div id="live" aria-live="polite" role="status" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap"></div>
<script>
const MODEL = __SETTINGS_MODEL__;
const LANGUAGES = __LANGUAGE_CATALOG__;
const BUILD_COMMIT = "__PAGE_BUILD_COMMIT__";
</script>
"""

# The page script. Kept as a separate raw string so the markup above stays readable; the two are
# concatenated by render_vool_settings_html().
_SETTINGS_JS = r"""<script>
/* ------------------------------------------------------------------ state */
const state = {
  sources: {},        /* url -> last JSON read from the authority */
  sourceErr: {},      /* url -> why a read failed, so rows say "unavailable" and not "off" */
  rows: {},           /* rowId -> {status, message, dirty, pending} */
  group: MODEL.length ? MODEL[0].id : '',
  query: '',
  focusRow: '',
  onlyRow: '',
  focusPart: '',
};
const $ = (sel, root) => (root || document).querySelector(sel);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const live = (msg) => { const l = $('#live'); if (l) l.textContent = msg; };

/* Every row that reads from an authority, flattened once. */
const ALL_ROWS = MODEL.flatMap(g => g.rows.map(r => ({ ...r, group: g.id, groupTitle: g.title })));

/* Reads are PER GROUP, never all at once. Two reasons, both load-bearing:
   - /api/settings/credentials reaches the macOS Keychain (credential_store.list_credentials ->
     _reconcile_index_with_keychain). Opening Settings must not touch the Keychain, so that read
     happens only when someone actually opens Models & Providers.
   - a group nobody opened costs nothing. */
function urlsForGroup(groupId) {
  return [...new Set(ALL_ROWS.filter(r => r.group === groupId && r.read && r.read.url).map(r => r.read.url))];
}
async function ensureGroupSources(groupId, force) {
  const urls = urlsForGroup(groupId).filter(u => force || (state.sources[u] === undefined && state.sourceErr[u] === undefined));
  if (!urls.length) return;
  urls.forEach(u => { ALL_ROWS.filter(r => r.read && r.read.url === u).forEach(r => setState(r.id, 'loading', 'Loading…')); });
  await Promise.all(urls.map(readSource));
  urls.forEach(u => { ALL_ROWS.filter(r => r.read && r.read.url === u).forEach(r => setState(r.id, 'idle', '')); });
}

/* ------------------------------------------------------------------ reads */
/* Boot performs PLAIN READS ONLY. No provider probe, no model launch, no Keychain touch, no
   permission prompt: every URL below is a local GET the runtime answers from disk. */
async function readSource(url) {
  try {
    const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
    const j = await r.json().catch(() => null);
    if (!r.ok) { state.sourceErr[url] = (j && (j.error || j.detail)) || ('HTTP ' + r.status); return false; }
    state.sources[url] = j || {};
    delete state.sourceErr[url];
    return true;
  } catch (e) {
    state.sourceErr[url] = 'VOOL did not answer (' + (e && e.message ? e.message : 'network') + ')';
    return false;
  }
}
function valueOf(row) {
  if (!row.read || !row.read.url) return undefined;
  const src = state.sources[row.read.url];
  if (!src) return undefined;
  return row.read.field ? src[row.read.field] : src;
}
function rowState(id) { return state.rows[id] || (state.rows[id] = { status: 'idle', message: '', dirty: undefined }); }
function shownValue(row) {
  const st = rowState(row.id);
  return st.dirty !== undefined ? st.dirty : valueOf(row);
}

/* ------------------------------------------------------------------ writes */
function coerce(row, raw) {
  const t = (row.write && row.write.type) || 'str';
  if (t === 'int') { const n = parseInt(raw, 10); return Number.isFinite(n) ? n : 0; }
  if (t === 'bool') return !!raw;
  return String(raw == null ? '' : raw);
}
function same(a, b) {
  if (typeof a === 'number' || typeof b === 'number') return Number(a) === Number(b);
  if (typeof a === 'boolean' || typeof b === 'boolean') return !!a === !!b;
  return String(a == null ? '' : a) === String(b == null ? '' : b);
}
function setState(rowId, status, message) {
  const st = rowState(rowId);
  st.status = status; st.message = message || '';
  paintState(rowId);
}
function paintState(rowId) {
  const node = document.querySelector('[data-state-for="' + CSS.escape(rowId) + '"]');
  if (!node) return;
  const st = rowState(rowId);
  node.className = 'state ' + st.status;
  node.textContent = st.message;
  if (st.status === 'failed') {
    const b = el('button', 'retry', 'Retry');
    b.type = 'button';
    b.addEventListener('click', () => {
      const row = ALL_ROWS.find(r => r.id === rowId);
      if (row) commit(row, rowState(rowId).dirty);
    });
    node.appendChild(b);
  }
}

/* One write, one authority, one truthful outcome.
   The value is posted to the endpoint that already carries this setting's gate and receipt; then
   the source is re-read and the stored value compared. "Saved" is only ever said about a value
   the runtime actually reports back. */
async function commit(row, rawValue) {
  if (!row.write) return;
  const value = coerce(row, rawValue);
  const st = rowState(row.id);
  st.dirty = value;
  st.pending = true;
  setState(row.id, 'saving', 'Saving…');
  let r, j;
  try {
    r = await fetch(row.write.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [row.write.field]: value }),
    });
    j = await r.json().catch(() => null);
  } catch (e) {
    st.pending = false;
    setState(row.id, 'failed', 'Not saved — VOOL did not answer.');
    live(row.label + ': not saved. VOOL did not answer.');
    return;
  }
  st.pending = false;
  const refused = !r.ok || (j && (j.error || j.ok === false));
  if (refused) {
    const why = (j && (j.error || j.detail)) ? String(j.error || j.detail) : ('refused with HTTP ' + r.status);
    setState(row.id, 'failed', 'Not saved — ' + why);
    live(row.label + ': not saved. ' + why);
    return;
  }
  /* Read back from the authority rather than trusting the 200. */
  const ok = await readSource(row.read ? row.read.url : row.write.url);
  if (!ok) {
    setState(row.id, 'stale', 'Sent, but VOOL could not confirm it. Reopen Settings to check.');
    return;
  }
  const stored = valueOf(row);
  if (!same(stored, value)) {
    /* The authority normalises and clamps on write; when it lands on a different value than the
       one asked for, say so and show what it actually holds instead of a green tick. */
    st.dirty = undefined;
    setState(row.id, 'stale', 'VOOL stored ' + JSON.stringify(stored) + ' instead.');
    live(row.label + ': VOOL stored a different value.');
    renderPane();
    return;
  }
  st.dirty = undefined;
  setState(row.id, 'saved', 'Saved');
  live(row.label + ' saved.');
  setTimeout(() => { if (rowState(row.id).status === 'saved') setState(row.id, 'idle', ''); }, 2600);
}

/* ------------------------------------------------------------------ search */
function rowText(r) { return [r.label, r.help, r.keywords, r.groupTitle].filter(Boolean).join(' ').toLowerCase(); }
function matches(r, q) { return !q || rowText(r).includes(q); }
function hitsFor(groupId, q) { return ALL_ROWS.filter(r => r.group === groupId && matches(r, q)).length; }

/* ------------------------------------------------------------------ render */
function renderNav() {
  const nav = $('#nav');
  /* Rebuilding the list destroys the focused element, which silently ends keyboard navigation:
     the next arrow press has nothing to move from. Remember that focus was in the sidebar and
     put it back on whichever item is current now. */
  const active = document.activeElement;
  const hadNavFocus = !!(active && active.classList && active.classList.contains('nav-item'));
  nav.textContent = '';
  const q = state.query;
  let any = false;
  const head = el('div', 'nav-sec', q ? 'Results' : 'Settings');
  nav.appendChild(head);
  MODEL.forEach(g => {
    const hits = hitsFor(g.id, q);
    if (q && !hits) return;
    any = true;
    const b = el('button', 'nav-item');
    b.type = 'button';
    b.setAttribute('role', 'listitem');
    b.dataset.group = g.id;
    b.setAttribute('aria-current', g.id === state.group ? 'true' : 'false');
    const ico = el('span', 'ico', g.icon || '○'); ico.setAttribute('aria-hidden', 'true');
    b.appendChild(ico);
    b.appendChild(el('span', null, g.title));
    if (q) b.appendChild(el('span', 'hits', String(hits)));
    b.addEventListener('click', () => { state.onlyRow = ''; state.focusPart = ''; state.focusRow = ''; go(g.id); });
    nav.appendChild(b);
  });
  if (!any) nav.appendChild(el('div', 'nav-empty', 'Nothing matches “' + state.query + '”.'));
  if (hadNavFocus) {
    const current = nav.querySelector('.nav-item[aria-current="true"]') || nav.querySelector('.nav-item');
    if (current) current.focus();
  }
}

function metaLine(row) {
  const wrap = el('div', 'row-meta');
  if (row.scope) {
    const s = el('span', 'm');
    s.textContent = row.scope === 'global' ? 'Applies everywhere' : (row.scope === 'chat' ? 'This chat only' : 'This session only');
    wrap.appendChild(s);
  }
  if (row.effect) wrap.appendChild(el('span', 'm', row.effect));
  return wrap;
}

function control(row) {
  const wrap = el('div', 'row-ctl');
  const v = shownValue(row);
  const unavailable = row.read && state.sourceErr[row.read.url];

  if (row.kind === 'note') { /* nothing to control */ }
  else if (unavailable) {
    wrap.appendChild(el('span', 'subtle', 'Unavailable — ' + state.sourceErr[row.read.url]));
  }
  else if (row.kind === 'toggle') {
    const b = el('button', 'sw');
    b.type = 'button';
    b.setAttribute('role', 'switch');
    b.setAttribute('aria-checked', v ? 'true' : 'false');
    b.setAttribute('aria-label', row.label);
    b.addEventListener('click', () => { commit(row, !shownValue(row)); b.setAttribute('aria-checked', !v ? 'true' : 'false'); });
    wrap.appendChild(b);
  }
  else if (row.kind === 'select') {
    const s = el('select', 'inp');
    s.setAttribute('aria-label', row.label);
    (row.options || []).forEach(o => { const op = el('option', null, o.label); op.value = o.value; s.appendChild(op); });
    if (v != null) s.value = String(v);
    s.addEventListener('change', () => commit(row, s.value));
    wrap.appendChild(s);
  }
  else if (row.kind === 'range') {
    const box = el('div', 'rangewrap');
    const i = document.createElement('input');
    i.type = 'range'; i.min = row.min; i.max = row.max; i.step = row.step || 1;
    i.value = v == null ? row.min : v;
    i.setAttribute('aria-label', row.label);
    const out = el('span', 'rangeval', (i.value) + (row.unit || ''));
    i.addEventListener('input', () => { out.textContent = i.value + (row.unit || ''); });
    i.addEventListener('change', () => commit(row, i.value));
    box.appendChild(i); box.appendChild(out);
    wrap.appendChild(box);
  }
  else if (row.kind === 'number' || row.kind === 'text') {
    const i = document.createElement('input');
    i.className = 'inp';
    i.type = row.kind === 'number' ? 'number' : 'text';
    if (row.min != null) i.min = row.min;
    if (row.step != null) i.step = row.step;
    if (row.maxlength) i.maxLength = row.maxlength;
    if (row.placeholder) i.placeholder = row.placeholder;
    i.value = v == null ? '' : String(v);
    i.setAttribute('aria-label', row.label);
    const send = () => { if (!same(i.value, valueOf(row))) commit(row, i.value); };
    i.addEventListener('change', send);
    i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
    wrap.appendChild(i);
  }
  const st = el('div', 'state idle');
  st.dataset.stateFor = row.id;
  wrap.appendChild(st);
  return wrap;
}

function renderRow(row) {
  const node = el('div', 'row');
  if (row.kind === 'custom') node.classList.add('wide');
  node.dataset.row = row.id;
  const main = el('div', 'row-main');
  const lab = el('div', 'row-label');
  lab.appendChild(el('span', null, row.label));
  if (row.badge) {
    const b = el('span', 'badge ' + (row.badge === 'Enforced' ? 'enforced' : (row.badge === 'Pilot' ? 'pilot' : 'guidance')), row.badge);
    lab.appendChild(b);
  }
  main.appendChild(lab);
  if (row.help) main.appendChild(el('div', row.kind === 'note' ? 'row-help note-body' : 'row-help', state.focusPart === 'spend' && row.id === 'usepod' ? 'Review a spending limit for your next UsePod request. Your message stays in the chat until you send it.' : row.help));
  if (row.kind !== 'note' && row.kind !== 'custom') main.appendChild(metaLine(row));
  node.appendChild(main);
  if (row.kind === 'custom') {
    const host = el('div', 'row-ctl');
    node.appendChild(host);
    renderWidget(row, host);
  } else {
    node.appendChild(control(row));
  }
  return node;
}

function renderPane() {
  const g = MODEL.find(x => x.id === state.group) || MODEL[0];
  if (!g) return;
  $('#paneTitle').textContent = g.title;
  $('#paneBlurb').textContent = g.blurb || '';
  const body = $('#paneBody');
  body.textContent = '';
  const card = el('div', 'card');
  const q = state.query;
  let shown = 0;
  g.rows.forEach(r => {
    if (state.onlyRow && r.id !== state.onlyRow) return;
    const row = { ...r, group: g.id, groupTitle: g.title };
    if (q && !matches(row, q)) return;
    shown++;
    const node = renderRow(row);
    if (q) node.classList.add('hit');
    card.appendChild(node);
  });
  if (!shown) card.appendChild(el('div', 'row', ''));
  body.appendChild(card);
  Object.keys(state.rows).forEach(paintState);
  if (state.focusRow) {
    const target = body.querySelector('[data-row="' + CSS.escape(state.focusRow) + '"]');
    if (target) { $('#pane').scrollTop = state.onlyRow ? 0 : target.offsetTop; target.classList.add('hit'); }
    state.focusRow = '';
  }
}

async function go(groupId, rowId) {
  rowId = rowId || state.focusRow;
  state.group = groupId;
  if (rowId) state.focusRow = rowId;
  renderNav();
  $('#paneBody').textContent = 'Loading settings…';
  if (!rowId) $('#pane').scrollTop = 0;
  live((MODEL.find(g => g.id === groupId) || {}).title + ' settings');
  await ensureGroupSources(groupId);   /* only THIS group's reads, and only once */
  if (rowId) state.focusRow = rowId;
  renderPane();
}
</script>
"""

# Widgets, keyboard handling and boot. Split out so each raw string stays reviewable.
_SETTINGS_JS2 = r"""<script>
/* ------------------------------------------------------------------ widgets
   A widget renders a row that is not a single value. Each one is responsible for its OWN read,
   performed when its group is first opened, so nothing here runs while Settings is showing
   another page. Anything that leaves this machine (a provider catalogue, a connection test) is
   behind a button that states where it goes before it goes. */
const widgetCache = {};

async function getJSON(url) {
  const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error((j && (j.error || j.detail)) || ('HTTP ' + r.status));
  return j || {};
}

function renderWidget(row, host) {
  host.textContent = '';
  const stack = el('div', 'stack');
  host.appendChild(stack);
  const w = row.widget;
  if (w === 'build') return widgetBuild(stack);
  if (w === 'usage') return widgetUsage(stack);
  if (w === 'cloud_keys') return widgetKeys(stack);
  if (w === 'models_overview') return widgetModelsOverview(stack);
  if (w === 'model_pin') return widgetModel(stack);
  if (w === 'local_models') return widgetLocalModels(stack);
  if (w === 'usepod') return widgetUsePod(stack);
  if (w === 'profile') return widgetProfile(stack);
  if (w === 'email_accounts') return widgetEmailAccounts(stack);
  if (w === 'email_recovery') return widgetEmailRecovery(stack);
  if (w === 'answer_language') return widgetAnswerLanguage(stack);
  if (w === 'wallet') return widgetWallet(stack);
  if (w === 'crypto') return widgetCrypto(stack);
  if (w === 'calendar_accounts') return widgetSurface(stack, 'VoolCalendarSettings', 'The calendar accounts panel did not load.');
  if (w === 'notifications') return widgetSurface(stack, 'VoolNotificationSettings', 'The notifications panel did not load.');
  if (w === 'scam_school') return widgetScamSchool(stack);
  if (w === 'setup_progress') return widgetSetupProgress(stack);
  if (w === 'companion_pet') return widgetCompanionPet(stack);
  if (w === 'advanced_links') return widgetAdvancedLinks(stack);
  if (w === 'extras_memory') return widgetExtras(stack, 'memory');
  if (w === 'extras_privacy') return widgetExtras(stack, 'privacy');
  if (w === 'extras_toolbelt') return widgetExtras(stack, 'toolbelt');
  if (w === 'bundle_export') return widgetBundleExport(stack);
  if (w === 'bundle_import') return widgetBundleImport(stack);
  stack.appendChild(el('div', 'empty', 'This section has nothing to show.'));
}

/* --- Backup & restore: one chat as a portable, signed .voolsession bundle, through the ONE seam
   (core/session_portability/api.py) behind /api/session/bundle/*. Settings has no chat of its own,
   so the chat is picked from the sidebar's own listing (/api/chat/sessions). A passphrase exists
   only in these fields and travels only to the export/import doors, never into history or logs. */
async function bundlePost(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return await r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status }));
}
function widgetBundleExport(stack) {
  const status = el('div', 'state idle'); status.id = 'sbExportStatus'; status.setAttribute('role', 'status');
  const say = (msg, warn) => { status.className = 'state ' + (warn ? 'failed' : 'saved'); status.textContent = msg; };
  const pick = el('div', 'inline');
  const chatSel = el('select', 'inp'); chatSel.id = 'sbChat'; chatSel.setAttribute('aria-label', 'Chat to export');
  const none = el('option', null, 'Choose a chat…'); none.value = ''; chatSel.appendChild(none);
  pick.appendChild(el('span', 'subtle', 'Chat')); pick.appendChild(chatSel);
  stack.appendChild(pick);
  const preview = el('div', 'sb-box'); preview.id = 'sbExportPreview'; preview.hidden = true; stack.appendChild(preview);
  const encRow = el('label', 'inline');
  const enc = document.createElement('input'); enc.type = 'checkbox'; enc.id = 'sbEncrypt'; enc.checked = true;
  encRow.appendChild(enc); encRow.appendChild(el('span', null, 'Encrypt with a passphrase (recommended)'));
  stack.appendChild(encRow);
  const passRow = el('div', 'inline'); passRow.id = 'sbPassFields';
  const pass = document.createElement('input'); pass.type = 'password'; pass.id = 'sbPass'; pass.className = 'inp';
  pass.autocomplete = 'new-password'; pass.spellcheck = false; pass.setAttribute('aria-label', 'Bundle passphrase');
  const pass2 = document.createElement('input'); pass2.type = 'password'; pass2.id = 'sbPass2'; pass2.className = 'inp';
  pass2.autocomplete = 'new-password'; pass2.spellcheck = false; pass2.setAttribute('aria-label', 'Repeat bundle passphrase');
  passRow.appendChild(el('span', 'subtle', 'Passphrase')); passRow.appendChild(pass); passRow.appendChild(el('span', 'subtle', 'again')); passRow.appendChild(pass2);
  stack.appendChild(passRow);
  const warnRow = el('label', 'inline'); warnRow.id = 'sbPlainWarnRow'; warnRow.hidden = true;
  const warn = document.createElement('input'); warn.type = 'checkbox'; warn.id = 'sbPlainWarn';
  warnRow.appendChild(warn); warnRow.appendChild(el('span', null, 'I understand an unencrypted bundle can be read by anyone who obtains the file.'));
  stack.appendChild(warnRow);
  const bar = el('div', 'inline');
  const exportBtn = el('button', 'btn primary', 'Export this chat…'); exportBtn.type = 'button'; exportBtn.id = 'sbExportBtn';
  bar.appendChild(exportBtn); stack.appendChild(bar); stack.appendChild(status);

  const renderPreview = async () => {
    const sid = chatSel.value;
    if (!sid) { preview.hidden = true; preview.textContent = ''; return; }
    try {
      const d = await bundlePost('/api/session/bundle/preview', { session_id: sid });
      if (!d || !d.counts) { preview.hidden = true; return; }
      preview.hidden = false; preview.textContent = '';
      preview.appendChild(el('div', 'row-label', 'Export preview — exactly what will be written'));
      const dl = el('dl', 'kv');
      const row = (k, v) => { dl.appendChild(el('dt', null, k)); dl.appendChild(el('dd', null, v)); };
      row('Included', d.counts.turns + ' turns, ' + d.counts.summaries + ' summaries, ' + d.counts.obligation_sets + ' obligation records, ' + d.counts.tool_receipts + ' receipts, ' + d.counts.session_events + ' activity events');
      const bytes = (d.embedded_files || []).reduce((s, f) => s + (f.size_bytes || 0), 0);
      row('Attachments', (d.counts.embedded_files || 0) + ' file(s), ' + bytes + ' bytes');
      row('Redactions applied', String(d.redactions || 0));
      row('Encryption', enc.checked ? 'ON (recommended)' : 'OFF — the file will be readable by anyone who obtains it');
      row('Excluded', d.scope_note || 'model-internal reasoning is not part of a bundle');
      preview.appendChild(dl);
    } catch (e) { preview.hidden = true; }
  };
  const syncEnc = () => { passRow.hidden = !enc.checked; warnRow.hidden = enc.checked; renderPreview(); };
  enc.addEventListener('change', syncEnc);
  chatSel.addEventListener('change', renderPreview);
  getJSON('/api/chat/sessions').then(d => {
    const sessions = d.sessions || [];
    sessions.forEach(s => {
      if (!s.session_id) return;
      const o = el('option', null, (s.title || 'New chat') + ' — ' + String(s.session_id).slice(0, 28));
      o.value = s.session_id; chatSel.appendChild(o);
    });
    if (!sessions.length) say('No chats to export yet.');
  }).catch(e => say('Could not list chats — ' + e.message, true));

  exportBtn.addEventListener('click', async () => {
    const sid = chatSel.value;
    if (!sid) { say('Choose the chat to export first.', true); return; }
    let passphrase = '';
    if (enc.checked) {
      passphrase = String(pass.value || '');
      if (!passphrase) { say('Type a passphrase, or untick encryption.', true); return; }
      if (passphrase !== String(pass2.value || '')) { say('The two passphrases do not match.', true); return; }
    } else if (!warn.checked) { say('Unticking encryption needs the explicit warning acknowledgement below.', true); return; }
    exportBtn.disabled = true; status.className = 'state saving'; status.textContent = 'Working…';
    try {
      const d = await bundlePost('/api/session/bundle/export', { session_id: sid, passphrase: passphrase });
      if (!d.ok) { say(d.error || 'Export refused.', true); exportBtn.disabled = false; return; }
      pass.value = ''; pass2.value = '';
      const a = document.createElement('a');
      a.href = '/api/session/bundle/download?path=' + encodeURIComponent(d.path);
      a.download = String(d.session_id || 'chat').replace(/[^a-z0-9_-]/gi, '_') + '.voolsession';
      document.body.appendChild(a); a.click(); a.remove();
      say(d.encrypted ? 'Saved: signed + encrypted bundle (' + d.counts.turns + ' turns).' : 'Saved: signed, UNENCRYPTED bundle (' + d.counts.turns + ' turns).', !d.encrypted);
      renderPreview();
    } catch (e) { say('Export failed: ' + (e && e.message ? e.message : e), true); }
    exportBtn.disabled = false;
  });
  syncEnc();
}

function widgetBundleImport(stack) {
  const status = el('div', 'state idle'); status.id = 'sbImportStatus'; status.setAttribute('role', 'status');
  const say = (msg, warn) => { status.className = 'state ' + (warn ? 'failed' : 'saved'); status.textContent = msg; };
  let stagedPath = '', needsConfirm = false, restoredId = '';
  const row1 = el('div', 'inline');
  const file = document.createElement('input'); file.type = 'file'; file.id = 'sbImportFile'; file.accept = '.voolsession'; file.className = 'inp';
  file.setAttribute('aria-label', 'Bundle file');
  const ipass = document.createElement('input'); ipass.type = 'password'; ipass.id = 'sbImportPass'; ipass.className = 'inp';
  ipass.autocomplete = 'off'; ipass.spellcheck = false; ipass.setAttribute('aria-label', 'Import passphrase, if the bundle is encrypted');
  const previewBtn = el('button', 'btn', 'Preview import'); previewBtn.type = 'button'; previewBtn.id = 'sbPreviewBtn';
  row1.appendChild(file); row1.appendChild(el('span', 'subtle', 'Passphrase')); row1.appendChild(ipass); row1.appendChild(previewBtn);
  stack.appendChild(row1);
  const box = el('div', 'sb-box'); box.id = 'sbPreviewBox'; box.hidden = true; stack.appendChild(box);
  const foreignRow = el('label', 'inline'); foreignRow.id = 'sbForeignRow'; foreignRow.hidden = true;
  const foreign = document.createElement('input'); foreign.type = 'checkbox'; foreign.id = 'sbConfirmForeign';
  foreignRow.appendChild(foreign); foreignRow.appendChild(el('span', null, 'This bundle is signed by an UNKNOWN key — import it anyway as untrusted.'));
  stack.appendChild(foreignRow);
  const row2 = el('div', 'inline');
  const importBtn = el('button', 'btn primary', 'Import'); importBtn.type = 'button'; importBtn.id = 'sbImportBtn'; importBtn.disabled = true;
  const openBtn = el('button', 'btn', 'Show in VOOL'); openBtn.type = 'button'; openBtn.id = 'sbOpenRestored'; openBtn.hidden = true;
  row2.appendChild(importBtn); row2.appendChild(openBtn);
  stack.appendChild(row2); stack.appendChild(status);

  const reset = () => {
    stagedPath = ''; needsConfirm = false; importBtn.disabled = true;
    box.hidden = true; box.textContent = ''; foreignRow.hidden = true; foreign.checked = false; openBtn.hidden = true;
    status.className = 'state idle'; status.textContent = '';
  };
  file.addEventListener('change', reset);
  const renderImportPreview = (d) => {
    box.hidden = false; box.textContent = '';
    box.appendChild(el('div', 'row-label', 'This import will:'));
    const dl = el('dl', 'kv');
    const row = (k, v) => { dl.appendChild(el('dt', null, k)); dl.appendChild(el('dd', null, v)); };
    const counts = d.counts || {};
    row('Chat', (d.title || d.source_session_id || 'untitled') + (d.conflicts && d.conflicts.collision ? ' (id already exists here — a NEW id will be used)' : ''));
    row('Turns / summaries', (counts.turns || 0) + ' / ' + (counts.summaries || 0));
    row('Receipts / activity events', (counts.tool_receipts || 0) + ' / ' + (counts.session_events || 0));
    row('Obligation records', String(counts.obligation_sets || 0));
    row('Embedded attachments', String(counts.embedded_files || 0));
    row('Profile references (as candidates)', String(counts.profile_candidates || 0));
    row('Signed by', ((d.signature && d.signature.signer_fingerprint) || 'unknown') + (d.signature && d.signature.trusted ? ' (trusted)' : ' (UNKNOWN key)'));
    row('Encryption on file', d.encrypted ? 'yes' : 'no');
    box.appendChild(dl);
    if (d.conflicts && d.conflicts.excluded) box.appendChild(el('div', 'subtle', 'Excluded: ' + d.conflicts.excluded));
    if (d.conflicts && d.conflicts.collision) box.appendChild(el('div', 'fail-note', 'A chat with this id already exists here. The import lands under a new id; nothing existing is modified.'));
    if (d.needs_confirmation) box.appendChild(el('div', 'fail-note', 'Unknown signer: the import stays marked untrusted.'));
  };
  previewBtn.addEventListener('click', async () => {
    const f = file.files && file.files[0];
    if (!f) { say('Pick a .voolsession file first.', true); return; }
    status.className = 'state saving'; status.textContent = 'Reading file…';
    try {
      const staged = await fetch('/api/session/bundle/upload', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream', 'X-Vool-Bundle-Name': encodeURIComponent(f.name) }, body: f });
      const sj = await staged.json();
      if (!sj.ok) { say(sj.error || 'Could not stage the file.', true); return; }
      stagedPath = sj.path;
      status.textContent = 'Previewing…';
      const d = await bundlePost('/api/session/bundle/inspect-import', { path: stagedPath, passphrase: String(ipass.value || '') });
      if (!d.ok) { say(d.error || 'Preview refused.', true); return; }
      needsConfirm = !!d.needs_confirmation;
      renderImportPreview(d);
      importBtn.disabled = false; foreignRow.hidden = !needsConfirm; openBtn.hidden = true;
      say(needsConfirm ? 'Signed by an UNKNOWN key — tick the acknowledgement to import it untrusted.' : 'Preview ready — review, then Import.', needsConfirm);
    } catch (e) { say('Preview failed: ' + (e && e.message ? e.message : e), true); }
  });
  importBtn.addEventListener('click', async () => {
    if (!stagedPath) { say('Preview a file first.', true); return; }
    if (needsConfirm && !foreign.checked) { say('Tick the unknown-signer acknowledgement, or cancel the import.', true); return; }
    importBtn.disabled = true; status.className = 'state saving'; status.textContent = 'Importing…';
    try {
      const d = await bundlePost('/api/session/bundle/import', { path: stagedPath, passphrase: String(ipass.value || ''), confirm_untrusted: !!foreign.checked });
      if (!d.ok) { say(d.error || 'Import refused — nothing was changed.', true); importBtn.disabled = false; return; }
      restoredId = d.imported_session_id || '';
      file.value = ''; ipass.value = ''; box.hidden = true; stagedPath = '';
      openBtn.hidden = !restoredId; openBtn.dataset.session = restoredId;
      say('Imported ' + ((d.counts || {}).turns || 0) + ' turns' + (d.collision ? ' under a new id (the original was already here).' : '.') +
          (d.trust && d.trust.trusted === false ? ' Marked UNTRUSTED (unknown signer).' : '') + ' The restored chat is at the top of your chat list.');
    } catch (e) { say('Import failed: ' + (e && e.message ? e.message : e), true); importBtn.disabled = false; }
  });
  /* The chat surface has no session deep link and this is not the chat window, so the most this page
     can do is bring VOOL forward: the restored chat sits at the top of its list, named as above. */
  openBtn.addEventListener('click', () => { if (restoredId) closeSettingsWindow(); });
}

/* --- Settings extras: the learned-facts memory browser, the privacy disclosure and the Toolbelt come
   from core/settings_extras_fragment.py, one section each, mounted into the group they belong to.
   The fragment stays the ONE authority for those surfaces; this widget only gives each a home. */
function widgetExtras(stack, which) {
  const host = el('div', 'extras-host'); host.dataset.extras = which;
  stack.appendChild(host);
  const extras = window.VoolSettingsExtras;
  if (extras && typeof extras.mountInto === 'function' && extras.mountInto(host, which)) return;
  stack.appendChild(el('div', 'empty', 'This section did not load.'));
}

/* --- Advanced: real links to the two runtime surfaces this daemon serves (core/web/api/service.py:
   /trace -> the task rail, /web0 -> the .null browser entry page). Same origin, same runtime; they
   open in their own tab or window so Settings stays put. Nothing here is a claim -- each is a
   plain anchor to a route that exists. */
const ADVANCED_SURFACES = [
  { href: '/trace', label: 'Open the trace rail', note: 'events, receipts and obligations of the running turn' },
  { href: '/web0', label: 'Open the Web0 browser', note: 'the entry page for .null sites' },
];
function widgetAdvancedLinks(stack) {
  ADVANCED_SURFACES.forEach(s => {
    const line = el('div', 'inline');
    const a = document.createElement('a');
    a.className = 'btn advanced-link';
    a.href = s.href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = s.label;
    a.style.textDecoration = 'none';
    line.appendChild(a);
    line.appendChild(el('span', 'subtle mono', s.href));
    line.appendChild(el('span', 'subtle', s.note));
    stack.appendChild(line);
  });
}

/* --- Complete setup: the derived first-run state (core/setup_progress.py). "N of M done", a tick per
   step, Continue (reopens the guided page at the first undone step), and "Don't remind me". The
   group vanishes on its own once every step is really done -- the tick comes from the setting
   existing, not from this page remembering a visit. */
const SETUP_STATE_URL = '/api/setup/state';
function openSetupPage(stepId) {
  const url = '/setup' + (stepId ? ('#step=' + stepId) : '');
  if (window.top !== window.self) {
    // Framed inside the chat: the same frame simply shows the guided page; its "Do this later"
    // asks the host to hide the frame.
    window.location.href = url;
    return;
  }
  let opened = null;
  try { opened = window.open(url, 'vool-setup'); } catch (e) { opened = null; }
  if (opened) { try { opened.focus(); } catch (e) {} return; }
  window.location.href = url;
}
function removeSetupGroup() {
  const i = MODEL.findIndex(g => g.id === 'setup');
  if (i >= 0) MODEL.splice(i, 1);
  for (let k = ALL_ROWS.length - 1; k >= 0; k--) if (ALL_ROWS[k].group === 'setup') ALL_ROWS.splice(k, 1);
  if (state.group === 'setup') state.group = MODEL.length ? MODEL[0].id : '';
}
function widgetSetupProgress(stack) {
  const src = state.sources[SETUP_STATE_URL];
  const err = state.sourceErr[SETUP_STATE_URL];
  if (err) { stack.appendChild(el('div', 'empty', 'Unavailable — ' + err)); return; }
  if (!src) { stack.appendChild(el('div', 'empty', 'Loading…')); return; }
  const head = el('div', 'row-label');
  head.id = 'setupCount';
  head.textContent = src.done_count + ' of ' + src.total + ' done';
  stack.appendChild(head);
  const list = el('div', 'stack');
  list.id = 'setupSteps';
  (src.steps || []).forEach(s => {
    const line = el('div', 'inline');
    line.dataset.step = s.id;
    line.dataset.done = s.done ? 'true' : 'false';
    const pill = el('span', 'pill ' + (s.done ? 'on' : ''));
    pill.appendChild(el('span', 'dot'));
    pill.appendChild(el('span', null, s.done ? 'Done' : (s.skipped ? 'Skipped' : 'To do')));
    line.appendChild(pill);
    line.appendChild(el('span', null, s.title));
    if (!s.done) line.appendChild(el('span', 'subtle', 'later: ' + s.later));
    list.appendChild(line);
  });
  stack.appendChild(list);
  const bar = el('div', 'inline');
  const go_ = el('button', 'btn primary', src.done_count ? 'Continue setup' : 'Start setup');
  go_.type = 'button'; go_.id = 'setupContinue';
  go_.addEventListener('click', () => openSetupPage(src.first_undone || ''));
  const dismiss = el('button', 'btn', 'Don\u2019t remind me');
  dismiss.type = 'button'; dismiss.id = 'setupDismiss';
  const st = el('span', 'state idle', '');
  dismiss.addEventListener('click', async () => {
    dismiss.disabled = true; st.className = 'state saving'; st.textContent = 'Saving…';
    try {
      const r = await fetch('/api/settings/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setup_dismissed: true }) });
      const j = await r.json().catch(() => null);
      if (!r.ok || (j && j.error)) throw new Error((j && j.error) || ('HTTP ' + r.status));
      await readSource(SETUP_STATE_URL);
      if (state.sources[SETUP_STATE_URL] && !state.sources[SETUP_STATE_URL].show_entry) {
        removeSetupGroup();
        live('Setup reminder hidden. Every step is still an ordinary setting.');
        go(state.group);
        return;
      }
      renderPane();
    } catch (e) { dismiss.disabled = false; st.className = 'state failed'; st.textContent = 'Not saved — ' + e.message; }
  });
  bar.appendChild(go_); bar.appendChild(dismiss); bar.appendChild(st);
  stack.appendChild(bar);
  stack.appendChild(el('div', 'subtle', 'This entry disappears by itself when every step is done.'));
}

/* --- Stay safe: the scam cards from core/wallet/scam_school.py (one authority; the SVGs are the server's own
   static drawings, never user content). Available with the wallet off: knowledge first, custody later. */
function widgetScamSchool(stack) {
  const src = state.sources['/api/wallet/safety'];
  const err = state.sourceErr['/api/wallet/safety'];
  if (err) { stack.appendChild(el('div', 'empty', 'Unavailable — ' + err)); return; }
  if (!src) { stack.appendChild(el('div', 'empty', 'Loading…')); return; }
  const rule = el('div', 'scam-rule'); rule.setAttribute('role', 'note'); rule.textContent = src.golden_rule || '';
  stack.appendChild(rule);
  const grid = el('div', 'scam-grid');
  (src.cards || []).forEach(c => {
    const card = el('div', 'scam-card'); card.dataset.card = c.id;
    const ill = el('div', 'scam-ill'); ill.innerHTML = c.svg || ''; card.appendChild(ill);
    const body = el('div', 'scam-body');
    body.appendChild(el('div', 'scam-title', (c.emoji ? c.emoji + ' ' : '') + c.title));
    body.appendChild(el('div', 'scam-line', 'What happens: ' + c.what_happens));
    body.appendChild(el('div', 'scam-line', 'What they want: ' + c.they_want));
    body.appendChild(el('div', 'scam-line scam-do', 'What you do: ' + c.you_do));
    card.appendChild(body); grid.appendChild(card);
  });
  stack.appendChild(grid);
  if (src.principle) stack.appendChild(el('div', 'scam-principle', src.principle));
}

/* --- Calendars and Notifications: each panel is its own fragment (core/calendar_settings_fragment.py,
   core/notification_settings_fragment.py), the one authority for that surface; this only gives it a home. */
function widgetSurface(stack, name, missing) {
  const host = el('div');
  stack.appendChild(host);
  const surface = window[name];
  if (surface && typeof surface.mountInto === 'function' && surface.mountInto(host)) return;
  stack.appendChild(el('div', 'empty', missing));
}

/* --- Wallet: the wallet fragment's own section, mounted into this pane. The fragment is the one
   authority for that surface (core/wallet_fragment.py); this widget only gives it a reachable home. */
function widgetWallet(stack) {
  const host = el('div'); host.id = 'walletHost';
  stack.appendChild(host);
  if (window.VoolWallet && typeof window.VoolWallet.mountInto === 'function') { window.VoolWallet.mountInto(host); return; }
  stack.appendChild(el('div', 'empty', 'The wallet surface did not load.'));
}

/* --- Crypto: the wallet fragment's onboarding surface (registry-backed network rows, create → one reveal →
   acknowledgement, Developer options), mounted into this pane. The fragment is the one authority for that surface. */
function widgetCrypto(stack) {
  const host = el('div'); host.id = 'cryptoHost';
  stack.appendChild(host);
  if (window.VoolWallet && typeof window.VoolWallet.mountCrypto === 'function') { window.VoolWallet.mountCrypto(host); return; }
  stack.appendChild(el('div', 'empty', 'The crypto surface did not load.'));
}

/* --- About: the exact running build, straight from the runtime version stamp. */
/* --- Desktop pet: the keyboard-reachable equivalent of the pet's own hover controls.
   The pet's window is deliberately non-activating, so it can never take the keyboard; that makes
   Settings -- not the pet -- the accessible home for Return, Hide and Reset. These call the SAME
   native bridge the pet's buttons call, so there is no second control path and no second store. */
function widgetCompanionPet(stack) {
  const api = (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  const appearance = el('button', 'btn', 'Customise pet');
  appearance.type = 'button';
  appearance.id = 'petAppearanceBtn';
  const appearanceStatus = el('span', 'subtle', '');
  appearanceStatus.setAttribute('role', 'status');
  appearance.addEventListener('click', async function () {
    try {
      if (api && api.open_companion_appearance) {
        const result = await api.open_companion_appearance();
        appearanceStatus.textContent = result && result.ok ? 'Pet appearance opened in VOOL.' : 'Open a VOOL chat to customise the pet.';
        return;
      }
      const owner = window.opener || (window.parent !== window ? window.parent : null);
      if (owner && owner.VoolCompanionDrawer) {
        if (owner.closeSettingsFrame) owner.closeSettingsFrame();
        owner.VoolCompanionDrawer.open();
        owner.focus();
        appearanceStatus.textContent = 'Pet appearance opened in VOOL.';
      } else {
        appearanceStatus.textContent = 'Open Settings from a VOOL chat to customise the pet.';
      }
    } catch (e) {
      appearanceStatus.textContent = 'Pet appearance is unavailable. Open Settings from a VOOL chat and try again.';
    }
  });
  const appearanceBar = el('div', 'inline');
  appearanceBar.append(appearance, appearanceStatus);
  stack.appendChild(appearanceBar);
  if (!api || !api.detach_companion) {
    stack.appendChild(el('div', 'empty', 'The desktop pet needs the VOOL app window. In a browser tab the companion stays docked in the chat.'));
    return;
  }
  const bar = el('div', 'inline');
  const said = el('span', 'subtle', '');
  const actions = [
    ['Move to desktop', 'detach_companion', 'The pet is on the desktop.'],
    ['Return to app', 'attach_companion', 'The pet is back in VOOL.'],
    ['Hide pet', 'hide_companion', 'The pet is hidden. Chat and the runtime keep running.'],
    ['Reset position', 'reset_companion_position', 'The pet is back on the main display.'],
  ];
  actions.forEach(function (entry) {
    const button = el('button', 'btn', entry[0]);
    button.type = 'button';
    button.addEventListener('click', async function () {
      button.disabled = true;
      try {
        const call = api[entry[1]];
        const result = call ? await call() : null;
        said.textContent = (result && result.ok) ? entry[2] : 'That is not available right now.';
      } catch (e) {
        said.textContent = 'That is not available right now.';
      } finally {
        button.disabled = false;
      }
    });
    bar.appendChild(button);
  });
  bar.appendChild(said);
  stack.appendChild(bar);
}

function widgetBuild(stack) {
  const src = state.sources['/api/runtime/version'];
  const err = state.sourceErr['/api/runtime/version'];
  if (err) { stack.appendChild(el('div', 'empty', 'Unavailable — ' + err)); return; }
  if (!src) { stack.appendChild(el('div', 'empty', 'Loading…')); return; }
  const dl = el('dl', 'kv');
  const keys = Object.keys(src).filter(k => typeof src[k] !== 'object');
  keys.forEach(k => {
    dl.appendChild(el('dt', null, k.replace(/_/g, ' ')));
    dl.appendChild(el('dd', 'mono', String(src[k])));
  });
  if (!keys.length) dl.appendChild(el('dd', 'empty', 'The runtime reported no version fields.'));
  stack.appendChild(dl);
  const bar = el('div', 'inline');
  const copy = el('button', 'btn', 'Copy build info'); copy.type = 'button';
  const st = el('span', 'subtle', '');
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(src, null, 2)); st.textContent = 'Copied.'; }
    catch (e) { st.textContent = 'Could not copy — select the values above instead.'; }
  });
  bar.appendChild(copy); bar.appendChild(st);
  stack.appendChild(bar);
}

/* --- Usage: a local read of this machine's own meter. Never contacts a provider. */
function widgetUsage(stack) {
  const ranges = [['today', 'Today'], ['week', 'Week'], ['month', 'Month'], ['', 'All time']];
  const tabs = el('div', 'inline');
  const body = el('div', null);
  body.setAttribute('aria-live', 'polite');
  let active = widgetCache.usageRange || 'today';
  const draw = async () => {
    body.textContent = '';
    body.appendChild(el('div', 'empty', 'Reading the local usage meter…'));
    let data;
    try { data = await getJSON('/api/runtime/usage' + (active ? '?range=' + active : '')); }
    catch (e) { body.textContent = ''; body.appendChild(el('div', 'empty', 'Unavailable — ' + e.message)); return; }
    body.textContent = '';
    const dl = el('dl', 'kv');
    const line = (label, bucket, extra) => {
      if (!bucket) return;
      dl.appendChild(el('dt', null, label));
      const t = (bucket.total_tokens || 0).toLocaleString();
      dl.appendChild(el('dd', null, t + ' tokens over ' + (bucket.responses || 0) + ' responses' + (extra || '')));
    };
    line('Free, on this machine', data.free_local);
    line('Free, cloud', data.free_cloud);
    const paid = data.paid_cloud || {};
    const usd = paid.usd_estimate != null ? (' · about $' + Number(paid.usd_estimate).toFixed(4)) : '';
    line('Paid, cloud', paid, usd);
    line('Unattributed', data.remote_unknown);
    dl.appendChild(el('dt', null, 'Total'));
    dl.appendChild(el('dd', null, (data.total_tokens || 0).toLocaleString() + ' tokens'));
    body.appendChild(dl);
    (data.notices || []).forEach(n => body.appendChild(el('div', 'subtle', typeof n === 'string' ? n : (n.message || ''))));
  };
  ranges.forEach(([val, label]) => {
    const b = el('button', 'btn' + (val === active ? ' primary' : ''), label);
    b.type = 'button';
    b.addEventListener('click', () => {
      active = val; widgetCache.usageRange = val;
      [...tabs.children].forEach(c => c.className = 'btn');
      b.className = 'btn primary';
      draw();
    });
    tabs.appendChild(b);
  });
  stack.appendChild(tabs);
  stack.appendChild(body);
  draw();
}

/* --- API keys. ONE store holds two families -- llm.cloud.* (model providers) and search.web.*
   (web search) -- so the list groups by slot prefix rather than pretending they are all model
   keys. Names and labels only: the runtime never returns a stored secret and nothing here
   renders one. */
const KEY_FAMILIES = [
  { id: 'llm.cloud.', title: 'Model providers', blurb: 'Used only when a turn actually runs on that provider.' },
  { id: 'search.web.', title: 'Web search', blurb: 'Used for live lookups. Without one VOOL uses its built-in keyless search.' },
];

/* The same store also holds keys the RUNTIME minted for itself. blackbox.cas.keys is the
   AES-256-GCM keyring for the Blackbox CAS: delete it and every blob it sealed is unreadable,
   with no plaintext fallback (core/blackbox/coverage/cas_keys.py). Those are listed -- hiding
   them would be its own lie -- but they are not offered a Remove button, because a one-click
   destroy of a key the user never supplied is not a setting. */
/* Human labels for provider ids, filled from the server's own provider table the first time the keys widget
   renders; a row rendered before that fetch resolves falls back to the id. */
const PROVIDER_LABELS = {};
/* Per-provider facts from the same table, keyed by id: `user_base_url` marks the custom OpenAI-compatible
   endpoint, whose address the user supplies. */
const PROVIDER_META = {};
/* The custom endpoint's base URL is stored beside its key in its own slot so the lane survives a restart
   (core/cloud_providers.py: CUSTOM_BASE_URL_SLOT). It is an address, not a key, and the list says so. */
const CUSTOM_BASE_URL_SLOT = 'llm.cloud.custom_base_url';
function keyFamilyOf(name) {
  const hit = KEY_FAMILIES.find(f => String(name || '').startsWith(f.id));
  return hit ? hit.id : 'managed';
}
function isOperatorKey(name) { return keyFamilyOf(name) !== 'managed'; }

function widgetKeys(stack) {
  const src = state.sources['/api/settings/credentials'];
  const err = state.sourceErr['/api/settings/credentials'];
  const list = el('div', 'stack');
  if (err) list.appendChild(el('div', 'empty', 'Unavailable — ' + err));
  else if (!src) list.appendChild(el('div', 'empty', 'Loading…'));
  else {
    const creds = src.credentials || src.items || [];
    if (!creds.length) list.appendChild(el('div', 'empty', 'No key stored. VOOL runs on this machine, free.'));
    const groups = [...KEY_FAMILIES.map(f => f.id), 'managed'];
    groups.forEach(fam => {
      const rows = creds.filter(c => keyFamilyOf(c.name) === fam);
      if (!rows.length) return;
      const meta = KEY_FAMILIES.find(f => f.id === fam);
      list.appendChild(el('div', 'row-label', meta ? meta.title : 'Managed by VOOL'));
      list.appendChild(el('div', 'subtle', meta ? meta.blurb :
        'Minted by the runtime for its own encrypted storage, not supplied by you. They are listed so nothing is hidden, and they are not removable from here: deleting one makes what it sealed permanently unreadable.'));
      rows.forEach(c => {
        const rowEl = el('div', 'inline');
        const pill = el('span', 'pill on');
        pill.appendChild(el('span', 'dot'));
        pill.appendChild(el('span', null, c.label || c.name));
        rowEl.appendChild(pill);
        rowEl.appendChild(el('span', 'subtle mono', c.name));
        if (!isOperatorKey(c.name)) {
          rowEl.appendChild(el('span', 'pill', 'system'));
          list.appendChild(rowEl);
          return;                       /* no Remove: this one is not the user's to delete */
        }
        if (c.name === CUSTOM_BASE_URL_SLOT || c.name === 'llm.cloud.usepod_origin') {
          /* The address of the custom endpoint, not a key: nothing to Test (a URL has no auth to probe),
             and the credentials door's closed slot list does not take a delete for it -- saving the custom
             key again with another base URL replaces it. Said here rather than offered as a button that
             would be refused. */
          pill.lastChild.textContent = c.name === CUSTOM_BASE_URL_SLOT ? 'Custom endpoint base URL' : 'UsePod endpoint';
          rowEl.appendChild(el('span', 'pill', 'endpoint'));
          rowEl.appendChild(el('span', 'subtle', 'replaced by saving the custom key again with a new base URL'));
          list.appendChild(rowEl);
          return;
        }
        /* Test: one real check with the provider -- does it accept this key? Model providers answer an
           auth probe (no completion, no spend); search providers answer one minimal search. The three
           outcomes are named apart, so "could not reach" is never dressed up as "rejected" or "verified". */
        const fam = keyFamilyOf(c.name);
        const providerId = String(c.name).slice(fam.length);
        const providerLabel = c.label || PROVIDER_LABELS[providerId] || providerId;
        const testBtn = el('button', 'btn key-test', 'Test'); testBtn.type = 'button'; testBtn.dataset.provider = providerId;
        const st = el('span', 'subtle key-test-state', ''); st.dataset.provider = providerId;
        testBtn.addEventListener('click', async () => {
          testBtn.disabled = true; st.textContent = 'Testing — asking ' + providerLabel + ' whether it accepts this key…';
          try {
            const url = fam === 'search.web.' ? '/api/search/test' : '/api/cloud/test';
            const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: providerId }) });
            const j = await r.json().catch(() => ({}));
            st.textContent = keyTestMessage(providerLabel, j);
          } catch (e) { st.textContent = 'Test failed: ' + (e && e.message ? e.message : e); }
          testBtn.disabled = false;
        });
        const rm = el('button', 'btn danger', 'Remove'); rm.type = 'button';
        rm.addEventListener('click', async () => {
          if (!confirm('Remove the stored key "' + (c.label || c.name) + '"?\n\nIt is deleted from this machine. Nothing is sent anywhere. VOOL falls back to running without it.')) return;
          rm.disabled = true; st.textContent = 'Removing…';
          try {
            const r = await fetch('/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: c.name, delete: true }) });
            const j = await r.json().catch(() => null);
            if (!r.ok || (j && j.error)) throw new Error((j && j.error) || ('HTTP ' + r.status));
            await readSource('/api/settings/credentials');
            renderPane();
          } catch (e) { rm.disabled = false; st.textContent = 'Not removed — ' + e.message; }
        });
        rowEl.appendChild(testBtn); rowEl.appendChild(rm); rowEl.appendChild(st);
        list.appendChild(rowEl);
      });
    });
  }
  stack.appendChild(list);

  /* Add a key. Saving recognises the key on this machine, asks the ONE selected service once whether
     it accepts it, and stores only a verified key (the save handler below). The selector is built from
     BOTH server catalogues so it cannot drift from what the runtime accepts, and neither catalogue read
     leaves this machine (/api/cloud/providers is a static table; /api/search/providers is presence-only
     and documents that it makes no live call). */
  const form = el('div', 'inline');
  const prov = el('select', 'inp'); prov.setAttribute('aria-label', 'Provider');
  const auto = el('option', null, 'Auto-detect from the key'); auto.value = ''; prov.appendChild(auto);
  /* The two catalogues name their id field differently -- cloud rows carry `id`
     (core/web/api/service.py), search rows carry `provider` (core/search_connection_state.py
     connection_rows). Reading only one of them drops that whole half of the list silently, with
     no error to notice. */
  const addGroup = (label, items) => {
    if (!items || !items.length) return;
    const g = document.createElement('optgroup'); g.label = label;
    items.forEach(pr => {
      const value = pr.id || pr.provider || '';
      if (!value) return;
      const o = el('option', null, pr.label || value);
      o.value = value;
      g.appendChild(o);
    });
    if (g.children.length) prov.appendChild(g);
  };
  getJSON('/api/cloud/providers')
    .then(d => {
      (d.providers || d.items || []).forEach(pr => { if (pr && pr.id) { PROVIDER_META[pr.id] = pr; if (pr.label) PROVIDER_LABELS[pr.id] = pr.label; } });
      addGroup('Model providers', d.providers || d.items || []);
      syncBaseUrl();
      syncOrigin();
    })
    .catch(() => {});
  getJSON('/api/search/providers')
    .then(d => {
      const rows = d.providers || d.items || [];
      addGroup('Web search', rows);
      renderSearchSignups(signups, rows);
    })
    .catch(() => {});

  /* The custom OpenAI-compatible endpoint needs its address as well as a key. The field exists only
     while that provider is selected; the authority (set_credentials_authority) refuses a custom key
     without a safe base URL -- https to any host, or plain http only to a loopback host -- so a key is
     never sent in clear, and its reason is shown here verbatim rather than pre-empted. */
  const baseUrl = document.createElement('input');
  baseUrl.type = 'url'; baseUrl.className = 'inp key-base-url'; baseUrl.hidden = true;
  baseUrl.autocomplete = 'off'; baseUrl.spellcheck = false; baseUrl.setAttribute('aria-label', 'Custom endpoint base URL');
  const baseUrlHint = el('div', 'subtle key-base-url-hint',
    'Custom endpoint: the OpenAI-compatible base URL, ending in /v1 — https://host/v1, or http://127.0.0.1:port/v1 for a server on this machine. Plain http to any other host is refused so the key never travels in clear.');
  baseUrlHint.hidden = true;
  const needsBaseUrl = () => { const v = prov.value.trim(); const meta = PROVIDER_META[v]; return meta ? !!meta.user_base_url : v === 'custom'; };
  const syncBaseUrl = () => { const show = needsBaseUrl(); baseUrl.hidden = !show; baseUrlHint.hidden = !show; };
  prov.addEventListener('change', syncBaseUrl);

  /* A path-token provider (UsePod: the credential rides the URL path, not a header) accepts a
     DIFFERENT paste here: a bare token, or a whole proxy URL containing it. The authority
     (registry_authorities.set_credentials_authority) splits the paste before anything is stored,
     keeps the token sealed, and binds it to an origin ONLY when the operator chose one explicitly
     in this field -- a URL paste alone never re-points the token. This hunk is the
     auth-placement-driven generic contract Task 01 owns: the selector's metadata decides the
     form, no provider id is special-cased here. */
  const origin = document.createElement('input');
  origin.type = 'url'; origin.className = 'inp key-origin'; origin.hidden = true;
  origin.autocomplete = 'off'; origin.spellcheck = false; origin.placeholder = 'optional — only an origin you chose yourself';
  origin.setAttribute('aria-label', 'Provider origin');
  const originHint = el('div', 'subtle key-origin-hint',
    'UsePod puts the token in the URL path. Paste the token alone, or the whole proxy URL -- it is split before storage and the token is never shown again. The optional origin names a different endpoint you chose yourself (https to any host, or http to a loopback host); leaving it empty keeps ' + USEPOD_ORIGIN_DEFAULT + '. A URL you pasted never becomes the origin by itself: a paste that disagrees with the origin chosen here is refused, and the token is never sent to an origin you did not pick.');
  originHint.hidden = true;
  const isPathTokenProvider = () => { const meta = PROVIDER_META[prov.value.trim()]; return !!(meta && meta.auth_placement === 'url_path_token'); };
  const syncOrigin = () => {
    const show = isPathTokenProvider();
    origin.hidden = !show; originHint.hidden = !show;
    key.setAttribute('aria-label', show ? 'UsePod token or proxy URL' : 'API key');
    key.title = show ? 'Paste the UsePod token, or the whole proxy URL containing it' : 'Paste the API key here';
  };
  prov.addEventListener('change', syncOrigin);

  const key = document.createElement('input');
  key.type = 'password'; key.className = 'inp'; key.setAttribute('aria-label', 'API key'); key.title = 'Paste the API key here';
  key.autocomplete = 'off'; key.spellcheck = false;
  /* Show/Hide: reveal what is being typed, on request only. The stored value is never shown again. */
  const reveal = el('button', 'btn key-reveal', 'Show'); reveal.type = 'button';
  reveal.setAttribute('aria-pressed', 'false'); reveal.title = 'Show or hide the key you are typing';
  reveal.addEventListener('click', () => {
    const shown = key.type === 'text';
    key.type = shown ? 'password' : 'text';
    reveal.textContent = shown ? 'Show' : 'Hide';
    reveal.setAttribute('aria-pressed', shown ? 'false' : 'true');
  });
  const save = el('button', 'btn primary key-save', 'Save key'); save.type = 'button';
  /* Save for later: the EXPLICIT unverified path. The key is stored sealed in a quarantine
     slot nothing reads — no lane, no probe, no search uses it — and shows as unverified until
     a later verification promotes it. Offered next to the verified save so the honest path
     stays the obvious one. */
  const later = el('button', 'btn key-save-later', 'Save for later (unverified)'); later.type = 'button';
  const st = el('span', 'state idle key-save-state', '');
  const setSt = (cls, text) => { st.className = 'state ' + cls + ' key-save-state'; st.textContent = text; };
  /* A successful save re-renders the group so the new slot appears in the list; the confirmation
     is carried across that redraw, otherwise the only outcome the person ever sees is a refusal. */
  if (widgetCache.keySaveNote) { setSt('saved', widgetCache.keySaveNote); widgetCache.keySaveNote = ''; }
  /* Saving is the credential intake flow (/api/intake/*, core/credential_intelligence): the key is
     recognised on this machine, the ONE selected service is asked once whether it accepts it, and only
     a verified key is stored and bound. "Auto-detect" used to post the bare value to the slot store,
     which answered "unsupported credential name" for every key (measured 2026-09-14). The dropdown's
     web-search options carry the bare search id; the intake registry names them search.<id>. */
  const intakePost = async (path, body) => {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    return { ok: r.ok && j.ok !== false, status: r.status, j: j || {} };
  };
  const selectedGroup = () => { const o = prov.selectedOptions && prov.selectedOptions[0]; const g = o && o.parentElement; return g && g.tagName === 'OPTGROUP' ? g.label : ''; };
  const selectedLabel = () => { const o = prov.selectedOptions && prov.selectedOptions[0]; return o && prov.value ? o.textContent : ''; };
  const failText = (res) => res.j.detail || res.j.error || ('HTTP ' + res.status);
  const saveVerified = async () => {
    const v = key.value;
    if (!v.trim()) { setSt('failed', 'Paste a key first.'); return; }
    save.disabled = true;
    try {
      setSt('saving', 'Recognising the key on this machine…');
      const begun = await intakePost('/api/intake/begin', {});
      if (!begun.ok) throw new Error(failText(begun));
      const sid = begun.j.session_id;
      const classified = await intakePost('/api/intake/classify', { session_id: sid, value: v });
      if (!classified.ok) throw new Error(failText(classified));
      const candidates = classified.j.candidates || [];
      const picked = prov.value.trim();
      const providerId = picked ? (selectedGroup() === 'Web search' ? 'search.' + picked : picked) : String(classified.j.suggestion || '');
      if (!providerId) {
        const names = candidates.map(c => c.label).filter(Boolean);
        const choices = names.length > 1 ? ' It could be ' + names.slice(0, -1).join(', ') + ' or ' + names[names.length - 1] + '.' : '';
        setSt('failed', (classified.j.reason || 'VOOL cannot tell which service this key is for.') + choices + ' Choose the service in the list, then press Save key again. Nothing was sent anywhere.');
        prov.focus();
        return;
      }
      if (!picked) { prov.value = providerId.replace(/^search\./, ''); syncBaseUrl(); syncOrigin(); }
      const label = selectedLabel() || (candidates[0] && candidates[0].label) || providerId;
      const verifyBody = { session_id: sid, provider_id: providerId };
      if (needsBaseUrl()) {
        if (!baseUrl.value.trim()) { setSt('failed', 'Not stored — enter the base URL of the endpoint first (for example https://host/v1). Nothing was sent anywhere.'); baseUrl.focus(); return; }
        verifyBody.base_url = baseUrl.value.trim();
      }
      /* A path-token provider (UsePod): the origin field names an endpoint the operator chose. Left
         empty, the provider's documented origin applies. The paste (a token or a whole proxy URL) is
         split on this machine before anything is sent, and a URL naming another origin is refused. */
      if (isPathTokenProvider() && origin.value.trim()) verifyBody.base_url = origin.value.trim();
      setSt('saving', 'Asking ' + label + ' once whether it accepts this key…');
      const verified = await intakePost('/api/intake/verify', verifyBody);
      if (!verified.ok) { setSt('failed', 'Not stored — ' + failText(verified)); return; }
      if (verified.j.outcome !== 'verified') { setSt('failed', verifyOutcomeMessage(label, verified.j)); return; }
      setSt('saving', label + ' accepted the key. Storing it sealed on this machine…');
      const done = await intakePost('/api/intake/complete', { session_id: sid });
      if (!done.ok) { setSt('failed', 'Not stored — ' + failText(done)); return; }
      key.value = '';                     /* the secret leaves the field the moment it is stored */
      origin.value = '';
      const use = done.j.kind === 'search_web' ? ' VOOL uses it for live web lookups from now on.' : ' It is now the active model provider.';
      const credit = verified.j.account_state === 'exhausted' ? ' Its credit limit is used up, so requests will fail until credit is added.' : '';
      /* A path-token binding names what the runtime now holds, never the secret: the origin the token
         is sent to and the one-way fingerprint its receipts carry. */
      const bound = done.j.origin ? ' Bound to ' + String(done.j.origin) + (done.j.credential_fingerprint ? ', fingerprint ' + done.j.credential_fingerprint : '') + '.' : '';
      const note = 'Stored, sealed on this machine after ' + label + ' accepted the key.' + use + credit + bound;
      setSt('saved', note);
      widgetCache.keySaveNote = note;
      await readSource('/api/settings/credentials');
      renderPane();
    } catch (e) { setSt('failed', 'Not stored — ' + (e && e.message ? e.message : e)); }
    finally { save.disabled = false; }
  };
  save.addEventListener('click', saveVerified);
  /* Save-for-later runs the same local recognition and the same ONE provider selection, then
     stores WITHOUT asking anyone: nothing is sent anywhere. The key lands in quarantine. */
  later.addEventListener('click', async () => {
    const v = key.value;
    if (!v.trim()) { setSt('failed', 'Paste a key first.'); return; }
    later.disabled = true;
    try {
      setSt('saving', 'Recognising the key on this machine…');
      const begun = await intakePost('/api/intake/begin', {});
      if (!begun.ok) throw new Error(failText(begun));
      const sid = begun.j.session_id;
      const classified = await intakePost('/api/intake/classify', { session_id: sid, value: v });
      if (!classified.ok) throw new Error(failText(classified));
      const candidates = classified.j.candidates || [];
      const picked = prov.value.trim();
      const providerId = picked ? (selectedGroup() === 'Web search' ? 'search.' + picked : picked) : String(classified.j.suggestion || '');
      if (!providerId) {
        const names = candidates.map(c => c.label).filter(Boolean);
        setSt('failed', (classified.j.reason || 'VOOL cannot tell which service this key is for.') + (names.length ? ' Choose the service in the list first.' : '') + ' Nothing was sent anywhere.');
        prov.focus();
        return;
      }
      if (!picked) { prov.value = providerId.replace(/^search\./, ''); syncBaseUrl(); syncOrigin(); }
      const laterBody = { session_id: sid, provider_id: providerId };
      if (needsBaseUrl()) {
        if (!baseUrl.value.trim()) { setSt('failed', 'Not stored — enter the base URL of the endpoint first. Nothing was sent anywhere.'); baseUrl.focus(); return; }
        laterBody.base_url = baseUrl.value.trim();
      }
      if (isPathTokenProvider() && origin.value.trim()) laterBody.base_url = origin.value.trim();
      /* preview pins the provider on the session without any request; complete stores quarantined */
      const seen = await intakePost('/api/intake/preview', laterBody);
      if (!seen.ok) throw new Error(failText(seen));
      const done = await intakePost('/api/intake/complete', { session_id: sid, persist: 'later' });
      if (!done.ok) throw new Error(failText(done));
      key.value = '';
      origin.value = '';
      const note = 'Stored sealed and UNVERIFIED for later. Nothing uses this key — not the model lane, not search — until you retry it from the list below and the provider accepts it.';
      setSt('saved', note);
      widgetCache.keySaveNote = note;
      await readSource('/api/settings/credentials');
      renderPane();
    } catch (e) { setSt('failed', 'Not stored — ' + (e && e.message ? e.message : e)); }
    finally { later.disabled = false; }
  });
  /* One sentence per verification outcome. The server keeps a rejected key apart from throttling, an
     outage, a timeout, a wrong address, a redirect and a non-API answer; so does each sentence, and none
     of them calls a key bad when the provider never judged it. */
  function verifyOutcomeMessage(label, j) {
    const http = j.http_status ? ' (HTTP ' + j.http_status + (j.provider_error_code ? ', ' + j.provider_error_code : '') + ')' : '';
    const kept = ' Nothing was stored.';
    switch (String(j.outcome || '')) {
      case 'invalid': return label + ' rejected this key' + http + '. Check that you copied all of it and that it is a ' + label + ' key.' + kept;
      case 'unauthorized': return label + ' recognised the key but refused this request' + http + '. Check the key’s permissions.' + kept;
      case 'exhausted': return label + ' reports no credit or quota left for this key' + http + '. Add credit, then save again.' + kept;
      case 'rate_limited': return label + ' is limiting requests right now' + (j.retry_after_s ? '; try again in ' + Math.ceil(j.retry_after_s) + ' s' : '; try again shortly') + '. The key was not judged.' + kept;
      case 'timeout': return label + ' did not answer in time. The key was not judged; try again.' + kept;
      case 'network_unavailable': return 'Could not reach ' + label + ' from this machine. The key was not judged.' + kept;
      case 'provider_unavailable': return label + ' reported an outage' + http + '. The key was not judged; try again later.' + kept;
      case 'endpoint_not_found': return 'No ' + label + ' API answered at that address' + http + '. Check the base URL.' + kept;
      case 'redirected': return 'The endpoint redirected to another site' + (j.redirect_origin ? ' (' + j.redirect_origin + ')' : '') + ', so VOOL did not send the key there.' + kept;
      case 'malformed_response': return 'That address answered with a web page, not an API response (for example a network sign-in page). The key was not judged.' + kept;
      case 'unexpected_schema': return 'That address answered, but not the way the ' + label + ' API does. The key was not judged.' + kept;
      case 'public_endpoint': return 'That address answers without any key, so it cannot confirm this one; VOOL did not send the key. If the service has its own entry in the list, choose that instead.' + kept;
      case 'refused': return 'VOOL did not send the key: ' + (j.detail || 'the request was refused') + '.' + kept;
      default: return label + ' gave an unexpected answer' + http + '. The key was not judged.' + kept;
    }
  }
  /* The Test button's answer for a stored key, in the same terms. */
  function keyTestMessage(label, j) {
    const state = String(j.state || '');
    if (state === 'ok') return 'Connection verified — ' + label + ' accepted your key.';
    const reason = state === 'failed' ? String(j.detail || '') : state;
    const http = j.http_status ? ' (HTTP ' + j.http_status + ')' : '';
    if (reason === 'unauthorized') return label + ' rejected the key' + http + '. Re-check or replace it.';
    if (reason === 'no_key') return 'No key is stored for ' + label + '.';
    if (reason === 'rate_limited') return label + ' is limiting requests right now' + http + '. The key was not judged; try again shortly.';
    if (reason === 'quota_exhausted' || reason === 'exhausted') return label + ' reports no credit or quota left for this key' + http + '.';
    if (reason === 'redirected') return label + ' answered with a redirect to another site; VOOL did not send the key there.';
    if (reason === 'malformed_response' || reason === 'unexpected_schema') return 'The endpoint answered, but not the way the ' + label + ' API does' + http + '. The key was not judged.';
    if (reason === 'endpoint_not_found') return 'No ' + label + ' API answered at the configured address' + http + '. Check the base URL.';
    if (reason === 'provider_unavailable') return label + ' reported an outage' + http + '. The key was not judged.';
    if (reason === 'timeout') return label + ' did not answer in time. The key was not judged.';
    if (reason === 'refused') return 'This runtime is not permitted to reach ' + label + ' right now' + (j.detail ? ' (' + j.detail + ')' : '') + '. The key was not used.';
    if (reason === 'unreachable' || reason === 'network_unavailable') return 'Could not reach ' + label + '; the key itself was not judged.';
    return 'Could not confirm the key with ' + label + '; the key itself was not judged' + (j.detail ? ' (' + j.detail + ')' : '') + '.';
  }
  form.appendChild(prov); form.appendChild(baseUrl); form.appendChild(origin); form.appendChild(el('span', 'subtle', 'Key')); form.appendChild(key); form.appendChild(reveal); form.appendChild(save); form.appendChild(later);
  stack.appendChild(form);
  stack.appendChild(baseUrlHint);
  stack.appendChild(originHint);
  stack.appendChild(st);
  stack.appendChild(el('div', 'subtle', 'Leave the provider on Auto-detect: VOOL recognises keys with a documented prefix on this machine and asks you to choose when it cannot tell. Saving asks that one service once whether it accepts the key, and only a verified key is stored. A stored key is never displayed again.'));
  stack.appendChild(el('div', 'subtle', 'Test asks the provider once whether it accepts the key: model providers answer an auth check that spends nothing; search providers answer one small search.'));
  /* Keys saved for later: unverified, sealed, and unreachable by anything that runs. This is
     where a person SEES that status, retries one (a verified answer promotes it into use) or
     deletes it. Nothing here can touch a verified binding. */
  const quarantine = el('div', 'stack');
  stack.appendChild(quarantine);
  const drawQuarantine = async () => {
    quarantine.textContent = '';
    let q;
    try { q = await getJSON('/api/intake/quarantine/list'); }
    catch (e) { quarantine.appendChild(el('div', 'subtle', 'Saved-for-later list unavailable — ' + (e && e.message ? e.message : e))); return; }
    const rows = (q && (q.quarantined || (q.data && q.data.quarantined))) || [];
    if (!rows.length) return;
    quarantine.appendChild(el('div', 'row-label', 'Saved for later (unverified)'));
    quarantine.appendChild(el('div', 'subtle', 'These keys are sealed on this machine and nothing uses them — not the model lane, not search. Retry asks the provider once; only its acceptance promotes a key into use.'));
    rows.forEach(row => {
      const line = el('div', 'inline');
      const pill = el('span', 'pill');
      pill.appendChild(el('span', null, row.provider_label || row.provider_id));
      line.appendChild(pill);
      const why = row.status === 'unverified_quarantined' ? 'unverified' : row.status;
      const qst = el('span', 'subtle quarantine-state', why + (row.created_at ? ' · saved ' + row.created_at : ''));
      const retry = el('button', 'btn key-quarantine-retry', 'Retry'); retry.type = 'button'; retry.dataset.provider = row.provider_id;
      retry.addEventListener('click', async () => {
        retry.disabled = true; qst.textContent = 'Asking ' + (row.provider_label || row.provider_id) + ' once whether it accepts this key…';
        try {
          const r = await fetch('/api/intake/quarantine/retry', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider_id: row.provider_id }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok || j.ok === false) throw new Error((j && (j.detail || j.error)) || ('HTTP ' + r.status));
          if (j.promoted) { qst.textContent = 'Verified and now in use — promoted out of quarantine.'; await drawQuarantine(); await readSource('/api/settings/credentials'); renderPane(); }
          else qst.textContent = 'Still unverified (' + (j.outcome || 'unknown') + ') — the key was not judged unless the reason says so.';
        } catch (e) { qst.textContent = 'Retry failed — ' + (e && e.message ? e.message : e); retry.disabled = false; }
      });
      const del = el('button', 'btn danger key-quarantine-delete', 'Delete'); del.type = 'button'; del.dataset.provider = row.provider_id;
      del.addEventListener('click', async () => {
        if (!confirm('Delete the unverified key for ' + (row.provider_label || row.provider_id) + ' from this machine? Nothing is sent anywhere.')) return;
        del.disabled = true; qst.textContent = 'Deleting…';
        try {
          const r = await fetch('/api/intake/quarantine/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider_id: row.provider_id }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok || j.ok === false) throw new Error((j && (j.detail || j.error)) || ('HTTP ' + r.status));
          await drawQuarantine(); await readSource('/api/settings/credentials'); renderPane();
        } catch (e) { qst.textContent = 'Not deleted — ' + (e && e.message ? e.message : e); del.disabled = false; }
      });
      line.appendChild(retry); line.appendChild(del); line.appendChild(qst);
      quarantine.appendChild(line);
    });
  };
  drawQuarantine();
  const signups = el('div', 'stack');
  stack.appendChild(signups);
}

/* Where to get a web-search key, and what each one costs. Both strings come from the provider
   table itself (core/search_providers.py: signup_url, free_tier), so nothing here is a claim this
   page invented and a changed free tier changes here too. Providers with no free tier are still
   listed, with what they actually require -- an offer that is not free should not read as one. */
function renderSearchSignups(host, rows) {
  host.textContent = '';
  const withSignup = (rows || []).filter(r => r.signup_url);
  if (!withSignup.length) return;
  host.appendChild(el('div', 'row-label', 'Getting a web-search key'));
  host.appendChild(el('div', 'subtle',
    'VOOL searches without any of these using its built-in keyless lookup; a key makes live ' +
    'answers more reliable. Terms below are the providers\u2019 own and change without notice.'));
  const list = el('div', 'stack');
  withSignup.forEach(r => {
    const line = el('div', 'inline');
    const a = document.createElement('a');
    a.href = r.signup_url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = r.label || r.provider;
    a.style.color = 'var(--accent)';
    line.appendChild(a);
    if (r.free_tier) line.appendChild(el('span', 'subtle', r.free_tier));
    if (r.connected) {
      const pill = el('span', 'pill on');
      pill.appendChild(el('span', 'dot'));
      pill.appendChild(el('span', null, 'key stored'));
      line.appendChild(pill);
    }
    list.appendChild(line);
  });
  host.appendChild(list);
}

/* --- Local models: the Ollama inventory on THIS machine, its registration state here, and the
   sealed tool-certification verdict per lane. Every action stays on this machine: registering
   writes a local manifest row, certifying runs the observe-only probe. A model that is not
   certified cannot author final answers -- that is the runtime's law, and this widget shows it
   rather than hiding it. */
// ---- Models & Providers redesign: overview, provider detail, guided setup, review sheet ----
// Layout from the reviewed design (the 2026-09-16 models-and-providers design review); every action
// goes through the authority that already owns it: pin /api/cloud/model, price limits
// /api/cloud/usepod/approve-route, budget propose->operator-allow->confirm, connection
// /api/settings/credentials + /api/cloud/test. A close grants nothing, keeps what was saved, and
// never claims an in-flight step stopped. The classic widgets below stay as the deep controls.
const MP_CSS_ADDED_KEY = window.MP_CSS_ADDED || false;
let mpView = { pane: 'overview', provider: '', guided: { active: false, step: 'connect', token: '', baseUrl: '', modelId: '', modelName: '', prices: null, budgetMode: 'daily', budgetUsdc: '' } };

function mpUsdcPerM(v) {
  const n = Number(v);
  if (!isFinite(n)) return '?';
  return (Math.round(n * 10000) / 10000).toString();
}
function mpMicroToUsdc(micro) {
  const n = Math.trunc(Number(micro) || 0);
  let s = String(n); while (s.length < 7) s = '0' + s;
  s = s.slice(0, -6) + '.' + s.slice(-6);
  return s.replace(/0+$/, '').replace(/\.$/, '') || '0';
}
function mpOpenRow(rowId, part) {
  location.hash = '#models/' + rowId + (part ? '/' + part : '');
}
async function mpReads() {
  const [pinR, discR, limitsR] = await Promise.all([
    getJSON('/api/cloud/model').catch(() => null),
    getJSON(USEPOD_DISCOVERY_URL).catch(() => null),
    getJSON('/api/cloud/spend-limits').catch(() => null),
  ]);
  return {
    pin: pinR && pinR.ok ? pinR : null,
    disc: discR && discR.ok !== false ? discR : null,
    spendLimits: limitsR && limitsR.limits ? limitsR : null,
  };
}
function mpUsePodState(disc) {
  const cred = (disc && disc.credential) || {};
  const grant = ((disc && disc.spend_approval) || {}).grant;
  const consent = (disc && disc.spend_approval) || null;
  const market = (disc && disc.marketplace) || {};
  return {
    connected: !!cred.configured,
    origin: String(cred.origin || ''),
    budgetActive: !!(grant && grant.kind === 'provider_budget' && grant.state === 'active'),
    grant: grant || null,
    consent: consent && consent.state ? consent : null,
    feedFresh: !!(market && market.fetched_at != null),
  };
}

// -- the review sheet: one explicit final confirmation over disclosed facts -------------------
function mpClose(mpSheet, onClose, inFlight) {
  // State-aware close wording: a close grants nothing, keeps completed saves, never claims an
  // in-flight authorized step stopped.
  if (inFlight()) return;
  if (mpSheet.parentNode) mpSheet.parentNode.removeChild(mpSheet);
  if (onClose) onClose();
}
function mpOpenSheet({ title, facts, steps, primaryLabel, onClose }) {
  const mpSheet = document.createElement('div');
  mpSheet.className = 'mp-sheet';
  mpSheet.setAttribute('role', 'dialog');
  mpSheet.setAttribute('aria-modal', 'true');
  const head = el('div', 'mp-sheet-head', title);
  const body = el('div', 'mp-sheet-body');
  const kv = el('dl', 'kv');
  facts.forEach(([k, v]) => { kv.appendChild(el('dt', null, k)); kv.appendChild(el('dd', null, String(v))); });
  body.appendChild(kv);
  const statusBox = el('div', 'stack mp-sheet-steps');
  const stepState = steps.map(() => 'todo');
  const drawSteps = () => {
    statusBox.textContent = '';
    steps.forEach((step, i) => {
      const line = el('div', 'mp-step mp-step-' + stepState[i], (stepState[i] === 'done' ? '✓ ' : stepState[i] === 'failed' ? '✕ ' : stepState[i] === 'run' ? '… ' : '· ') + step.label);
      statusBox.appendChild(line);
    });
  };
  drawSteps();
  body.appendChild(statusBox);
  const note = el('div', 'mp-sheet-note', '');
  body.appendChild(note);
  const actionsRow = el('div', 'mp-sheet-actions');
  let busy = false;
  const inFlight = () => busy;
  const runBtn = el('button', 'btn primary mp-sheet-run', primaryLabel); runBtn.type = 'button';
  const closeBtn = el('button', 'btn mp-sheet-close', 'Leave for now'); closeBtn.type = 'button';
  closeBtn.addEventListener('click', () => mpClose(mpSheet, onClose, inFlight));
  const finishFrom = async (startAt) => {
    if (busy) return;
    busy = true; runBtn.disabled = true; note.textContent = '';
    for (let i = startAt; i < steps.length; i++) {
      stepState[i] = 'run'; drawSteps();
      let outcome;
      try { outcome = await steps[i].run(); }
      catch (e) { outcome = { ok: false, error: e && e.message ? e.message : 'failed' }; }
      if (!outcome || !outcome.ok) {
        stepState[i] = 'failed'; drawSteps();
        const kept = steps.slice(0, i).filter((s, j) => stepState[j] === 'done').map((s) => s.label);
        note.textContent = (outcome && outcome.error ? outcome.error : 'The step failed.')
          + (kept.length ? (' Kept: ' + kept.join(', ') + '. Retry continues from the failed step only.') : ' Retry continues from the failed step only.')
          + (outcome && outcome.kept && outcome.kept.approval_id ? (' (saved approval ' + String(outcome.kept.approval_id).slice(0, 12) + '… resumes on reopen)') : '');
        busy = false; runBtn.disabled = false;
        return;
      }
      stepState[i] = 'done'; drawSteps();
    }
    note.textContent = 'Saved.';
    busy = false; runBtn.disabled = true;
    setTimeout(() => mpClose(mpSheet, onClose, inFlight), 600);
  };
  runBtn.addEventListener('click', () => finishFrom(0));
  actionsRow.appendChild(runBtn); actionsRow.appendChild(closeBtn);
  mpSheet.appendChild(head); mpSheet.appendChild(body); mpSheet.appendChild(actionsRow);
  document.body.appendChild(mpSheet);
  runBtn.focus();
  return { sheet: mpSheet, retryFrom: finishFrom, note };
}

// -- the single-confirmation budget orchestration (propose/allow/confirm are internal) ---------
async function mpBudgetStep({ usdc, mode, resume }) {
  const atomic = usepodAtomicAmount(usdc, 'USDC');
  if (!atomic) return { ok: false, error: 'Enter a positive budget in USDC.' };
  let approval = resume && resume.approval_id ? { approval_id: resume.approval_id } : null;
  if (!approval) {
    const p = await usepodPost('/api/cloud/usepod/spend-approval/propose', { per_call_atomic: atomic, max_total_atomic: atomic, budget_mode: mode || 'daily', expiry_epoch: 0, asset: 'USDC' });
    if (!p.ok) return { ok: false, error: 'Budget not registered — ' + (p.body && (p.body.error || p.body.code) || 'refused') + '.' };
    approval = { approval_id: p.body.approval_id };
  }
  const r = await fetch('/api/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: 'resolve_approval', approval_id: approval.approval_id, decision: 'allow', session_id: 'openclaw:settings-operator' }) });
  if (!r.ok) return { ok: false, kept: { approval_id: approval.approval_id }, error: 'Registered and waiting for the allow step — reopen to continue the same approval; nothing is enabled yet.' };
  const c = await usepodPost('/api/cloud/usepod/spend-approval/confirm', { approval_id: approval.approval_id });
  if (!c.ok) return { ok: false, kept: { approval_id: approval.approval_id }, error: 'Allowed but not enabled — reopen to finish enabling; the approval is kept.' };
  return { ok: true };
}
function mpSheetForSetup({ pin, usepod, guided, reads, onClose }) {
  const facts = [];
  const steps = [];
  if (pin) {
    facts.push(['Model', (pin.provider && pin.provider !== 'openrouter' ? pin.provider + ':' : '') + pin.model + (pin.cost_state ? ' — ' + pin.cost_state : '')]);
    steps.push({ label: 'Set ' + pin.model + ' as the model that answers', run: async () => {
      const r = await usepodPost('/api/cloud/model', { model: pin.model, provider: pin.provider || 'usepod', confirm_paid: true });
      return r.ok ? { ok: true } : { ok: false, error: 'Model not pinned — ' + (r.body && (r.body.error || r.body.message) || 'refused') + '.' };
    } });
  }
  if (usepod && usepod.limits) {
    facts.push(['Maximum input and output prices', usepod.limits.in + ' / ' + usepod.limits.out + ' USDC per 1M tokens (rates; not a per-request bill)']);
    steps.push({ label: 'Save the maximum prices', run: async () => {
      const r = await usepodPost('/api/cloud/usepod/approve-route', { model_id: usepod.modelId, max_input_usdc_per_million: usepod.limits.in, max_output_usdc_per_million: usepod.limits.out });
      return r.ok ? { ok: true } : { ok: false, error: 'Limits not saved — ' + (r.body && (r.body.error || r.body.code) || 'refused') + '.' };
    } });
  }
  if (usepod && usepod.budget) {
    const resume = reads && reads.disc && reads.disc.spend_approval && (reads.disc.spend_approval.state === 'pending' || reads.disc.spend_approval.state === 'approved') ? reads.disc.spend_approval : null;
    facts.push(['Budget', usepod.budget.usdc + ' USDC · ' + (usepod.budget.mode === 'total' ? 'total, until used or disabled' : 'every 24 hours while enabled')]);
    if (resume) facts.push(['Saved approval to continue', String(resume.approval_id).slice(0, 12) + '… (' + resume.state + ')']);
    steps.push({ label: 'Enable the budget' + (resume ? ' (continuing the saved approval)' : ''), run: () => mpBudgetStep({ usdc: usepod.budget.usdc, mode: usepod.budget.mode, resume }) });
  }
  facts.push(['Payment', usepod && usepod.prepaid ? 'UsePod prepaid token balance (a budget never authorizes wallet spending)' : 'provider account']);
  return mpOpenSheet({ title: guided ? 'Turn on this setup?' : 'Save these changes?', facts, steps, primaryLabel: guided ? 'Turn on' : 'Save changes', onClose });
}

// -- provider cards / detail / guided -----------------------------------------------------------
function widgetModelsOverview(stack) {
  const root = el('div', 'stack mp-root');
  stack.appendChild(root);
  const draw = async () => {
    root.textContent = '';
    const reads = await mpReads();
    const st = mpUsePodState(reads.disc);
    const head = el('div', 'stack mp-what');
    head.appendChild(el('div', 'row-label', 'What will answer'));
    const pinLine = reads.pin
      ? ((reads.pin.provider && reads.pin.provider !== 'openrouter' ? reads.pin.provider + ':' : '') + (reads.pin.model || 'auto'))
      : 'VOOL Auto (local first)';
    const pinState = reads.pin ? String(reads.pin.cost_state || '') : '';
    head.appendChild(el('div', null, pinLine + (pinState ? ' — ' + pinState + (pinState === 'paid' ? ' · ready for answers once its limits and budget are set' : '') : '')));
    if (mpView.pane === 'detail') return mpDrawDetail(root, reads, st, draw);
    // Problems strip (composed from the same reads).
    const problems = [];
    if (!st.connected) problems.push('UsePod is not connected yet.');
    if (st.connected && !st.budgetActive) problems.push(st.consent ? ('A UsePod budget approval is ' + st.consent.state + ' — one step away.') : 'No UsePod budget is enabled.');
    if (st.connected && !st.feedFresh) problems.push('No UsePod price feed is cached — prices are unknown until a refresh.');
    if (problems.length) {
      const strip = el('div', 'stack mp-problems');
      problems.forEach((p) => strip.appendChild(el('div', 'mp-problem', p)));
      root.appendChild(strip);
    }
    // Provider cards.
    const cards = el('div', 'mp-cards');
    const mkCard = (key, name, stateLine, actionLabel, ready) => {
      const card = el('div', 'stack mp-card' + (ready ? ' mp-ready' : ''));
      card.appendChild(el('div', 'mp-card-title', name + (ready ? ' · ready' : '')));
      card.appendChild(el('div', 'subtle', stateLine));
      const btn = el('button', 'btn mp-card-open', actionLabel); btn.type = 'button';
      btn.addEventListener('click', () => {
        if (key === 'usepod' && actionLabel !== 'Manage') mpView.guided.active = true;
        mpView.pane = 'detail'; mpView.provider = key; draw();
      });
      card.appendChild(btn);
      return card;
    };
    cards.appendChild(mkCard('local', 'This Mac', 'Local models answer without a network.', 'View local models', true));
    // A pending or allowed-but-unconfirmed consent is a RESUME state, not a fresh setup: the
    // card sends the operator to the detail with the saved approval, never through guided again
    // (a second proposal would duplicate the consent identity).
    const mpResumeState = st.connected && !st.budgetActive && st.consent;
    cards.appendChild(mkCard('usepod', 'UsePod', st.connected
      ? (st.budgetActive ? 'Connected · budget enabled' : (mpResumeState ? 'Connected · budget approval ' + st.consent.state + ' — one step away' : 'Connected · setup one step away'))
      : 'Inference marketplace — connect a token to start.', st.connected && !st.budgetActive && !mpResumeState ? 'Continue setup' : (st.connected ? 'Manage' : 'Set up'), st.connected && st.budgetActive));
    cards.appendChild(mkCard('openrouter', 'OpenRouter', 'Bring-your-own-key cloud models. VOOL-wide spending limits apply.', 'View OpenRouter', false));
    root.appendChild(head); root.appendChild(cards);
    const fine = el('div', 'subtle', 'Every action here goes through the authority that already owns it — the same doors the chat uses. Nothing is approved by looking at this page.');
    root.appendChild(fine);
  };
  draw();
  return stack;
}
function mpDrawDetail(root, reads, st, redraw) {
  const back = el('button', 'btn mp-back', '← All providers'); back.type = 'button';
  back.addEventListener('click', () => { mpView.pane = 'overview'; mpView.provider = ''; redraw(); });
  root.appendChild(back);
  const body = el('div', 'stack mp-detail');
  root.appendChild(body);
  if (mpView.provider === 'usepod') return mpDrawUsePod(body, reads, st, redraw);
  if (mpView.provider === 'openrouter') return mpDrawOpenRouter(body, reads);
  return mpDrawLocal(body, reads);
}
function mpDrawLocal(body) {
  body.appendChild(el('div', 'row-label', 'Local models'));
  body.appendChild(el('div', null, 'Local models answer on this machine with no network and no per-token cost. Registering and certifying stay where they always lived.'));
  const btn = el('button', 'btn', 'Open local model controls'); btn.type = 'button';
  btn.addEventListener('click', () => mpOpenRow('local_models'));
  body.appendChild(btn);
}
async function mpDrawOpenRouter(body, reads) {
  body.appendChild(el('div', 'row-label', 'OpenRouter'));
  body.appendChild(el('div', null, 'Cloud models behind your own OpenRouter key. Each paid pick is reserved against VOOL\u2019s own spending limits before anything is sent.'));
  const lim = reads.spendLimits;
  const card = el('div', 'stack mp-card');
  card.appendChild(el('div', 'row-label', 'Spending limits · VOOL-wide'));
  if (lim) {
    const kv = el('dl', 'kv');
    [['Per call', lim.limits.per_call_usd], ['Per task', lim.limits.per_task_usd], ['Daily', lim.limits.daily_usd], ['Monthly', lim.limits.monthly_usd]].forEach(([k, v]) => {
      kv.appendChild(el('dt', null, k)); kv.appendChild(el('dd', null, '$' + Number(v).toFixed(2)));
    });
    card.appendChild(kv);
    card.appendChild(el('div', 'subtle', lim.scope + '.'));
  } else {
    card.appendChild(el('div', 'subtle', 'Limits unreadable right now; they are still enforced.'));
  }
  const link = el('button', 'btn', 'Usage & Budgets'); link.type = 'button';
  link.addEventListener('click', () => { location.hash = '#budgets'; });
  card.appendChild(link);
  body.appendChild(card);
  body.appendChild(el('div', 'subtle', 'These are VOOL\u2019s limits for paid calls on key-based providers — not OpenRouter account credit, and not any UsePod budget.'));
  const all = el('button', 'btn', 'Open the classic model controls'); all.type = 'button';
  all.addEventListener('click', () => mpOpenRow('model_pin'));
  body.appendChild(all);
}
async function mpDrawUsePod(body, reads, st, redraw) {
  if (!st.connected || (mpView.guided.active && mpView.guided.step !== 'ready')) return mpDrawGuided(body, reads, st, redraw);
  body.appendChild(el('div', 'row-label', 'UsePod'));
  const kv = el('dl', 'kv');
  kv.appendChild(el('dt', null, 'Connection')); kv.appendChild(el('dd', null, 'Connected · ' + st.origin));
  body.appendChild(kv);
  // Budget card.
  const budgetCard = el('div', 'stack mp-card');
  budgetCard.appendChild(el('div', 'row-label', 'Budget'));
  if (st.budgetActive && st.grant) {
    const headroom = st.grant.headroom || {};
    budgetCard.appendChild(el('div', null, 'Enabled — about ' + usepodDisplayAmount(headroom.principal_left_atomic, 'USDC') + ' remaining' + (st.grant.renews_at ? ' · resets ' + usepodTime(st.grant.renews_at) : '') + '.'));
  } else if (st.consent) {
    budgetCard.appendChild(el('div', null, 'A saved approval is ' + st.consent.state + ' — one step away. Finishing continues the same approval; nothing new is authorized by looking.'));
    const fin = el('button', 'btn primary', st.consent.state === 'pending' ? 'Allow the saved budget' : 'Finish enabling'); fin.type = 'button';
    fin.addEventListener('click', () => mpSheetForSetup({
      usepod: { budget: { usdc: mpMicroToUsdc(st.consent.facts && (st.consent.facts.max_total_atomic || st.consent.facts.per_call_atomic) || 0), mode: (st.consent.facts && st.consent.facts.budget_mode) || 'daily' } },
      reads, onClose: redraw,
    }));
    budgetCard.appendChild(fin);
  } else {
    budgetCard.appendChild(el('div', 'subtle', 'No budget is enabled. A budget is what authorizes paid UsePod requests account-wide.'));
  }
  const change = el('button', 'btn', st.budgetActive ? 'Change budget' : 'Enable a budget'); change.type = 'button';
  change.addEventListener('click', () => {
    const line = el('div', 'inline');
    const amount = document.createElement('input'); amount.type = 'number'; amount.min = '0.000001'; amount.step = '0.000001'; amount.value = '3'; amount.className = 'inp mp-budget-usdc'; amount.setAttribute('aria-label', 'Budget in USDC');
    const mode = document.createElement('select'); mode.className = 'inp mp-budget-mode'; mode.setAttribute('aria-label', 'Budget type');
    [['daily', 'Every 24 hours — renew while enabled'], ['total', 'Total — until used or disabled']].forEach(([v, t]) => { const o = el('option', null, t); o.value = v; mode.appendChild(o); });
    const review = el('button', 'btn primary', 'Review'); review.type = 'button';
    review.addEventListener('click', () => mpSheetForSetup({ usepod: { budget: { usdc: amount.value, mode: mode.value }, prepaid: true }, reads, onClose: redraw }));
    const l1 = el('label', 'stack', 'Budget (USDC)'); l1.appendChild(amount);
    const l2 = el('label', 'stack', 'Type'); l2.appendChild(mode);
    line.appendChild(l1); line.appendChild(l2); line.appendChild(review);
    budgetCard.appendChild(line);
  });
  budgetCard.appendChild(change);
  body.appendChild(budgetCard);
  // Price limits on file.
  const pin = reads.pin && reads.pin.provider === 'usepod' ? reads.pin : null;
  const bound = pin && reads.disc && reads.disc.approved_routes ? reads.disc.approved_routes[pin.model] : null;
  const limitsCard = el('div', 'stack mp-card');
  limitsCard.appendChild(el('div', 'row-label', 'Maximum input and output prices'));
  if (bound) {
    limitsCard.appendChild(el('div', null, mpMicroToUsdc(bound.max_input_microunits_per_million) + ' / ' + mpMicroToUsdc(bound.max_output_microunits_per_million) + ' USDC per 1M tokens · saved ' + usepodTime(bound.approved_at) + '. A price above these is refused, never silently allowed.'));
  } else {
    limitsCard.appendChild(el('div', 'subtle', 'No saved limits for the pinned model yet.'));
  }
  const manage = el('button', 'btn', 'Manage limits'); manage.type = 'button';
  manage.addEventListener('click', () => {
    const line = el('div', 'inline');
    const mk = (label, val) => { const i = document.createElement('input'); i.type = 'text'; i.inputMode = 'decimal'; i.className = 'inp mp-limits-' + label; i.value = val; i.setAttribute('aria-label', 'Maximum ' + label + ' price (USDC per 1M)'); const l = el('label', 'stack', 'Maximum ' + label + ' (USDC / 1M)'); l.appendChild(i); return l; };
    const inL = mk('input', bound ? mpMicroToUsdc(bound.max_input_microunits_per_million) : '');
    const outL = mk('output', bound ? mpMicroToUsdc(bound.max_output_microunits_per_million) : '');
    const review = el('button', 'btn primary', 'Review'); review.type = 'button';
    review.addEventListener('click', () => {
      const inEl = line.querySelector('.mp-limits-input'), outEl = line.querySelector('.mp-limits-output');
      if (!/^\d{1,9}(\.\d{1,6})?$/.test(inEl.value.trim()) || !/^\d{1,9}(\.\d{1,6})?$/.test(outEl.value.trim())) { limitsCard.appendChild(el('div', 'mp-problem', 'Give both maxima as positive numbers in USDC per 1M tokens.')); return; }
      mpSheetForSetup({ usepod: { limits: { in: inEl.value.trim(), out: outEl.value.trim() }, modelId: pin ? pin.model : '', prepaid: true }, reads, onClose: redraw });
    });
    line.appendChild(inL); line.appendChild(outL); line.appendChild(review);
    limitsCard.appendChild(line);
  });
  limitsCard.appendChild(manage);
  body.appendChild(limitsCard);
  // Advanced collapsed.
  const adv = el('details', 'mp-advanced');
  adv.appendChild(el('summary', null, 'Advanced: payment lane, routing policy, feed provenance'));
  const allBtn = el('button', 'btn', 'All UsePod controls'); allBtn.type = 'button';
  allBtn.addEventListener('click', () => mpOpenRow('usepod'));
  adv.appendChild(allBtn);
  body.appendChild(adv);
}
async function mpDrawGuided(body, reads, st, redraw) {
  const g = mpView.guided;
  const steps = ['connect', 'model', 'spending', 'review'];
  const chips = el('div', 'mp-steps');
  steps.forEach((s, i) => chips.appendChild(el('span', 'mp-chip' + (s === g.step ? ' mp-now' : i < steps.indexOf(g.step) ? ' mp-done' : ''), s === 'connect' ? 'Connect' : s === 'model' ? 'Model' : s === 'spending' ? 'Spending' : 'Review')));
  body.appendChild(chips);
  const stepBody = el('div', 'stack mp-guided');
  body.appendChild(stepBody);
  if (g.step === 'connect') {
    if (st.connected) {
      stepBody.appendChild(el('div', null, 'Connected — the token is saved and was accepted. It stays saved if you leave at any point.'));
      const cont = el('button', 'btn primary', 'Continue'); cont.type = 'button';
      cont.addEventListener('click', () => { g.step = 'model'; redraw(); });
      stepBody.appendChild(cont);
      return;
    }
    stepBody.appendChild(el('div', null, 'Paste your UsePod token (or the whole proxy URL). It is saved the moment it verifies — before anything else — and stays saved if you leave.'));
    const token = document.createElement('input'); token.type = 'text'; token.className = 'inp mp-guided-token'; token.setAttribute('aria-label', 'UsePod token or proxy URL'); token.value = g.token;
    const save = el('button', 'btn primary', 'Connect'); save.type = 'button';
    const sayLine = el('div', 'subtle', '');
    save.addEventListener('click', async () => {
      save.disabled = true; sayLine.textContent = 'Saving and verifying…';
      const value = token.value.trim();
      let baseUrl = '';
      let tokenValue = value;
      const m = value.match(/^(https?:\/\/[^\/]+)\/proxy\/([^\/]+)\/v1$/);
      if (m) { baseUrl = m[1]; tokenValue = m[2]; }
      const r = await usepodPost('/api/settings/credentials', { provider: 'usepod', value: tokenValue, base_url: baseUrl || undefined });
      if (!r.ok) { sayLine.textContent = 'Not saved — ' + (r.body && (r.body.error || r.body.code) || 'refused') + '.'; save.disabled = false; return; }
      const t = await usepodPost('/api/cloud/test', { provider: 'usepod' });
      sayLine.textContent = t.ok && t.body.state === 'ok' ? 'Connected — the token is saved and UsePod accepted it.' : 'Saved, but the test did not confirm — you can retry from the full controls.';
      g.token = '';
      g.step = 'model';
      await usepodPost('/api/cloud/usepod/refresh', {});
      redraw();
    });
    const line = el('div', 'inline'); line.appendChild(token); line.appendChild(save);
    stepBody.appendChild(line); stepBody.appendChild(sayLine);
    return;
  }
  if (g.step === 'model') {
    stepBody.appendChild(el('div', null, 'Pick the model that answers. Paid models need their maximum prices and a budget — both come next.'));
    const search = document.createElement('input'); search.type = 'search'; search.className = 'inp mp-guided-search'; search.setAttribute('aria-label', 'Search models'); search.placeholder = 'Search…';
    const list = el('div', 'stack mp-picker');
    const drawList = async () => {
      list.textContent = '';
      list.appendChild(el('div', 'subtle', 'Reading the catalog…'));
      const j = await getJSON('/api/cloud/models?provider=usepod').catch(() => null);
      list.textContent = '';
      const rows = (j && j.models) || [];
      if (!rows.length) { list.appendChild(el('div', 'subtle', 'No catalog yet — use Refresh in the full controls, then come back.')); return; }
      const q = search.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
      const shown = rows.filter((r) => !q || String(r.id).toLowerCase().replace(/[^a-z0-9]+/g, '').indexOf(q) !== -1).slice(0, 40);
      shown.forEach((r) => {
        const row = el('button', 'mp-model-row' + (r.id === g.modelId ? ' mp-picked' : ''), (r.name || r.id) + (r.free ? ' · free' : ' · paid ' + mpUsdcPerM(r.prompt_usd_per_m) + '/' + mpUsdcPerM(r.completion_usd_per_m) + ' USDC per 1M'));
        row.type = 'button';
        row.addEventListener('click', () => { g.modelId = r.id; g.modelName = r.name || r.id; g.prices = { in: mpUsdcPerM(r.prompt_usd_per_m), out: mpUsdcPerM(r.completion_usd_per_m) }; next.disabled = !g.modelId; drawList(); });
        list.appendChild(row);
      });
      if (rows.length > shown.length) list.appendChild(el('div', 'subtle', (rows.length - shown.length) + ' more — keep typing to narrow.'));
    };
    search.addEventListener('input', drawList);
    stepBody.appendChild(search); stepBody.appendChild(list);
    drawList();
    const next = el('button', 'btn primary', 'Continue'); next.type = 'button'; next.disabled = !g.modelId;
    next.addEventListener('click', () => { g.step = 'spending'; redraw(); });
    stepBody.appendChild(next);
    return;
  }
  if (g.step === 'spending') {
    stepBody.appendChild(el('div', null, 'Set the account-wide budget for paid UsePod requests. Amounts are ordinary USDC.'));
    const amount = document.createElement('input'); amount.type = 'number'; amount.min = '0.000001'; amount.step = '0.000001'; amount.value = g.budgetUsdc || '3'; amount.className = 'inp mp-guided-budget'; amount.setAttribute('aria-label', 'Budget in USDC');
    const mode = document.createElement('select'); mode.className = 'inp mp-guided-mode'; mode.setAttribute('aria-label', 'Budget type');
    [['daily', 'Every 24 hours — renew while enabled'], ['total', 'Total — until used or disabled']].forEach(([v, t]) => { const o = el('option', null, t); o.value = v; mode.appendChild(o); });
    mode.value = g.budgetMode;
    const l1 = el('label', 'stack', 'Budget (USDC)'); l1.appendChild(amount);
    const l2 = el('label', 'stack', 'Type'); l2.appendChild(mode);
    const line = el('div', 'inline'); line.appendChild(l1); line.appendChild(l2);
    stepBody.appendChild(line);
    stepBody.appendChild(el('div', 'subtle', 'The example amount is a suggestion to review, never a pre-authorized default.'));
    const next = el('button', 'btn primary', 'Review'); next.type = 'button';
    next.addEventListener('click', () => {
      g.budgetUsdc = amount.value; g.budgetMode = mode.value;
      g.step = 'review'; redraw();
    });
    stepBody.appendChild(next);
    return;
  }
  if (g.step === 'ready') {
    stepBody.appendChild(el('div', null, 'Ready — the model is pinned, its maximum prices and the budget are saved. The overview shows the current state.'));
    const done = el('button', 'btn primary', 'Back to overview'); done.type = 'button';
    done.addEventListener('click', () => { mpView.pane = 'overview'; mpView.provider = ''; redraw(); });
    stepBody.appendChild(done);
    return;
  }
  // review
  const facts = [['Model', g.modelName || g.modelId]];
  if (g.prices) facts.push(['Maximum input and output prices', g.prices.in + ' / ' + g.prices.out + ' USDC per 1M tokens (rates; edit below)']);
  facts.push(['Budget', g.budgetUsdc + ' USDC · ' + (g.budgetMode === 'total' ? 'total' : 'every 24 hours')]);
  facts.push(['Payment', 'UsePod prepaid token balance']);
  const kv = el('dl', 'kv'); facts.forEach(([k, v]) => { kv.appendChild(el('dt', null, k)); kv.appendChild(el('dd', null, String(v))); });
  stepBody.appendChild(kv);
  const limitsLine = el('div', 'inline');
  const mk = (label, val) => { const i = document.createElement('input'); i.type = 'text'; i.inputMode = 'decimal'; i.className = 'inp mp-review-' + label; i.value = val; i.setAttribute('aria-label', 'Maximum ' + label + ' price'); const l = el('label', 'stack', 'Maximum ' + label + ' (USDC / 1M)'); l.appendChild(i); return l; };
  limitsLine.appendChild(mk('input', g.prices ? g.prices.in : ''));
  limitsLine.appendChild(mk('output', g.prices ? g.prices.out : ''));
  stepBody.appendChild(limitsLine);
  const turnOn = el('button', 'btn primary mp-guided-turnon', 'Turn on'); turnOn.type = 'button';
  turnOn.addEventListener('click', () => {
    const inEl = stepBody.querySelector('.mp-review-input'), outEl = stepBody.querySelector('.mp-review-output');
    if (!/^\d{1,9}(\.\d{1,6})?$/.test(inEl.value.trim()) || !/^\d{1,9}(\.\d{1,6})?$/.test(outEl.value.trim())) { stepBody.appendChild(el('div', 'mp-problem', 'Give both maxima as positive numbers in USDC per 1M tokens.')); return; }
    mpSheetForSetup({
      pin: { model: g.modelId, provider: 'usepod' },
      usepod: { modelId: g.modelId, limits: { in: inEl.value.trim(), out: outEl.value.trim() }, budget: { usdc: g.budgetUsdc, mode: g.budgetMode }, prepaid: true },
      guided: true, reads,
      onClose: async () => { g.step = 'ready'; g.active = false; await usepodPost('/api/cloud/usepod/refresh', {}); redraw(); },
    });
  });
  stepBody.appendChild(turnOn);
  stepBody.appendChild(el('div', 'subtle', 'One confirmation covers everything listed. Leaving before it saves the connection only — already-saved steps are kept and resumed.'));
}

function widgetLocalModels(stack) {
  const body = el('div', 'stack');
  stack.appendChild(body);
  const draw = async (note) => {
    body.textContent = '';
    body.appendChild(el('div', 'empty', 'Reading your local Ollama…'));
    let data;
    try { data = await getJSON('/api/models/local'); }
    catch (e) { body.textContent = ''; body.appendChild(el('div', 'empty', 'Unavailable — ' + e.message)); return; }
    body.textContent = '';
    if (data.local_models_disabled) {
      body.appendChild(el('div', 'empty', 'Local models are disabled by policy on this runtime.'));
      return;
    }
    const models = data.models || [];
    if (!models.length) {
      body.appendChild(el('div', 'empty', 'No text models found in your local Ollama. Install one (e.g. `ollama pull qwen3:8b`) and it will appear here.'));
      return;
    }
    const certLabel = (s) => s === 'verified' ? 'certified author' : (s === 'degraded' || s === 'incompatible' || s === 'stale') ? 'probe ' + s : s === 'probing' ? 'probing…' : 'not certified';
    for (const m of models) {
      const rowEl = el('div', 'stack profile-item');
      const head = el('div', 'inline');
      head.appendChild(el('span', 'profile-value', m.model_name));
      head.appendChild(el('span', 'subtle', m.size_gb ? String(m.size_gb) + ' GB' : 'size unknown'));
      const state = !m.registered ? 'installed, not registered'
        : (m.certification_state === 'verified' ? 'registered · certified author'
          : m.certification_state ? 'registered · ' + certLabel(m.certification_state) : 'registered');
      head.appendChild(el('span', 'subtle', state));
      if (!m.registered) {
        const b = el('button', 'btn', 'Register'); b.type = 'button';
        b.addEventListener('click', async () => {
          b.disabled = true; b.textContent = 'Registering…';
          try {
            const r = await fetch('/api/models/local/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_name: m.model_name }) });
            const j = await r.json().catch(() => ({}));
            if (!r.ok || j.ok === false) throw new Error((j && j.error) || ('HTTP ' + r.status));
            await draw('Registered ' + m.model_name + '. Run the certification to see if it may author answers.');
          } catch (e) { b.disabled = false; b.textContent = 'Register'; body.appendChild(el('div', 'state failed', 'Not registered — ' + (e && e.message ? e.message : e))); }
        });
        head.appendChild(b);
      } else if (m.certification_state !== 'verified') {
        const b = el('button', 'btn', m.certification_state ? 'Re-run certification' : 'Certify'); b.type = 'button';
        b.addEventListener('click', async () => {
          b.disabled = true; b.textContent = 'Running the sealed probe…';
          try {
            const r = await fetch('/api/model-tool-certification/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider_name: 'ollama-local', model_name: m.model_name, timeout_seconds: 150 }) });
            const j = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error((j && j.error) || ('HTTP ' + r.status));
            const st = String(j.state || 'unknown');
            const failed = Object.values(j.stages || {}).filter(s => s && s.state === 'failed').map(s => s.failure_code || 'failed');
            await draw(st === 'verified'
              ? m.model_name + ' passed every sealed stage — it is a certified author now.'
              : m.model_name + ' did not certify (' + st + (failed.length ? ': ' + failed.join(', ') : '') + ').');
          } catch (e) { b.disabled = false; b.textContent = 'Certify'; body.appendChild(el('div', 'state failed', 'Certification did not run — ' + (e && e.message ? e.message : e))); }
        });
        head.appendChild(b);
      }
      rowEl.appendChild(head);
      body.appendChild(rowEl);
    }
    if (note) body.appendChild(el('div', 'state saved', note));
    body.appendChild(el('div', 'subtle', 'Certification is a sealed local probe (synthetic tools, no workspace, no credentials). A model that fails it can still route and call tools; it just cannot write a final answer.'));
  };
  draw();
}

/* --- Model: the authoritative pin, read locally. The provider's catalogue is NOT fetched until
   asked for, because that request leaves this machine. */


/* --- Auto fallback: the ONE verified-free OpenRouter model VOOL Auto may escalate to, independent of the
   pin above (POST /api/cloud/auto-model -> models.auto, the same door the composer's Auto-fallback control
   used). The server accepts only `auto` or a :free id present in its cached verified-free catalogue, so
   paid models are not offered here at all: VOOL Auto never selects paid. The stored value is read back from
   the catalogue route (`auto_free_model`) rather than trusted from the 200. */
async function autoFallbackControl(body, openrouterCatalogue) {
  let cat = openrouterCatalogue;
  if (!cat) { try { cat = await getJSON('/api/cloud/models?provider=openrouter'); } catch (e) { cat = null; } }
  const line = el('div', 'inline');
  const lab = el('label', null, 'Auto fallback'); lab.htmlFor = 'autoFallbackSelect';
  const fbSel = el('select', 'inp auto-fallback'); fbSel.id = 'autoFallbackSelect';
  fbSel.setAttribute('aria-label', 'Free model VOOL Auto falls back to');
  const best = el('option', null, 'Best verified free — VOOL chooses'); best.value = 'auto'; fbSel.appendChild(best);
  const st = el('span', 'subtle auto-fallback-state', '');
  const free = ((cat && (cat.models || cat.items)) || []).filter(x => x && x.free);
  free.forEach(x => { const o = el('option', null, (x.name || x.id) + ' · free'); o.value = x.id; fbSel.appendChild(o); });
  const stored = String((cat && cat.auto_free_model) || 'auto');
  fbSel.value = stored;
  if (fbSel.value !== stored) { fbSel.value = 'auto'; st.textContent = 'VOOL has ' + stored + ' stored, which is no longer in the verified-free catalogue; Auto uses the best verified free model until you pick another.'; }
  if (!cat) st.textContent = 'The OpenRouter catalogue is not readable right now; the choice is shown but its list is empty.';
  let confirmed = fbSel.value;   /* the last value the catalogue route reported back */
  fbSel.addEventListener('change', async () => {
    const wanted = fbSel.value;
    fbSel.disabled = true; st.textContent = 'Saving…';
    try {
      const r = await fetch('/api/cloud/auto-model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: wanted }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || (j && j.ok === false)) throw new Error((j && (j.error || j.message)) || ('HTTP ' + r.status));
      const back = await getJSON('/api/cloud/models?provider=openrouter');
      const now = String(back.auto_free_model || 'auto');
      confirmed = now; fbSel.value = now;
      if (now !== wanted) st.textContent = 'VOOL stored ' + now + ' instead.';
      else st.textContent = now === 'auto' ? 'Auto falls back to the best verified free model.' : 'Auto falls back to ' + now + '. Used only by VOOL Auto; never paid.';
    } catch (e) { fbSel.value = confirmed; st.textContent = 'Not changed — ' + (e && e.message ? e.message : e); }
    fbSel.disabled = false;
  });
  line.appendChild(lab); line.appendChild(fbSel); line.appendChild(st);
  body.appendChild(line);
  body.appendChild(el('div', 'subtle', 'When a turn runs on VOOL Auto and escalates to the cloud, this is the free model it may use. It is separate from the pin above and can never authorise a paid route.'));
}

/* --- UsePod: the marketplace provider's own panel. Everything on first paint comes from
   GET /api/cloud/usepod/discovery, a CACHE-ONLY read the runtime answers from disk — opening
   this panel contacts nobody. The one action that leaves this machine is Refresh, and its button
   says where it goes before it goes: the public marketplace feed, plus (only if a token is
   stored) that token's own model list and balance. No inference, no spend, no wallet.

   Three honest-refusal facts are shown as they stand, not as we would like them: prices carry
   their source and age (a stale or missing feed never reads as free), a balance is an
   observation with its time (never a spending authority), and the two paid-dispatch dependencies
   (a monetary authority for prepaid, a verified-network wallet authority for x402) are named
   when absent, with the Settings door each belongs behind — a lane that will refuse says so
   here, before a turn is attempted. */
const USEPOD_DISCOVERY_URL = '/api/cloud/usepod/discovery';
function modelSearchKey(value) { return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[\s_\-\u2010-\u2015]+/g, ''); }
function widgetModel(stack) {
  let browseProvider = '', searchText = '', drawRevision = 0;
  const body = el('div', 'stack');
  stack.appendChild(body);
  const draw = async (note) => {
    const revision = ++drawRevision;
    body.textContent = '';
    body.appendChild(el('div', 'empty', 'Reading model settings…'));
    let m;
    try { m = await getJSON('/api/cloud/model'); }
    catch (e) { body.textContent = ''; body.appendChild(el('div', 'empty', 'Unavailable — ' + e.message)); return; }
    if (revision !== drawRevision) return;
    body.textContent = '';
    const dl = el('dl', 'kv');
    dl.appendChild(el('dt', null, 'Default model'));
    dl.appendChild(el('dd', 'mono', m.model ? String(m.model) : 'none — VOOL chooses'));
    dl.appendChild(el('dt', null, 'Provider'));
    dl.appendChild(el('dd', 'mono', m.model && m.provider ? String(m.provider) : 'No default — each chat keeps its selection'));
    if (m.cost_state) {
      dl.appendChild(el('dt', null, 'Cost'));
      dl.appendChild(el('dd', null, m.cost_state === 'paid' ? 'Paid — spends your provider credits' : (m.cost_state === 'free' ? 'Free' : 'Not classified')));
    }
    body.appendChild(dl);
    if (m.error || m.ok === false) body.appendChild(el('div', 'subtle', 'VOOL could not resolve the pin (' + (m.error || 'unavailable') + '), so it will not act on one.'));
    /* Pick and pin from here too. The list is the provider's own catalogue (/api/cloud/models), the pin is the
       same door the composer uses (POST /api/cloud/model), including the server's 409 handshake for a paid or
       unpriced model -- the person confirms, or nothing is pinned. */
    let providers = [];
    try {
      const [registry, credentials, connections] = await Promise.all([
        getJSON('/api/cloud/providers'), getJSON('/api/settings/credentials'),
        getJSON('/api/connections').catch(() => ({connections:[]})),
      ]);
      if (revision !== drawRevision) return;
      const slots = new Set((credentials.credentials || []).map(c => c.name));
      const walletLane = (connections.connections || []).some(c => c.id === 'cloud' && c.mode === 'accountless_x402');
      providers = (registry.providers || []).filter(p => slots.has('llm.cloud.' + p.id) || (p.id === 'usepod' && walletLane));
    } catch (e) {
      body.appendChild(el('div', 'subtle', 'Could not read configured providers. Reopen Settings to retry.'));
      return;
    }
    if (!providers.some(p => p.id === browseProvider)) browseProvider = providers.some(p => p.id === m.provider) ? m.provider : (providers[0] || {}).id || '';
    const provider = browseProvider;
    if (!provider) { body.appendChild(el('div', 'subtle', 'Add a provider in API Keys, or configure a wallet payment route.')); return; }
    const providerLine = el('label', 'stack', 'Provider');
    const providerSelect = el('select', 'inp model-provider'); providerSelect.setAttribute('aria-label', 'Model provider');
    providers.forEach(p => { const o = el('option', null, p.label || p.id); o.value = p.id; providerSelect.appendChild(o); });
    providerSelect.value = provider;
    providerSelect.addEventListener('change', () => { browseProvider = providerSelect.value; searchText = ''; draw(); });
    providerLine.appendChild(providerSelect); body.appendChild(providerLine);
    const search = el('input', 'inp model-search'); search.type = 'search'; search.placeholder = 'Search models'; search.value = searchText;
    search.setAttribute('aria-label', 'Search models for selected provider'); body.appendChild(search);
    const pick = el('div', 'inline');
    const sel = el('div', 'model-picker'); sel.setAttribute('role', 'listbox'); sel.setAttribute('aria-label', 'Model to pin');
    sel.style.cssText = 'display:flex;flex-direction:column;gap:4px;max-height:260px;overflow-y:auto;width:100%;border:1px solid var(--border);border-radius:8px;padding:6px';
    const pinBtn = el('button', 'btn primary model-pin', 'Set default'); pinBtn.type = 'button';
    const pst = el('span', 'subtle model-pin-state', '');
    const count = el('div', 'subtle model-result-count'); body.appendChild(count);
    let catalogue = null, models = [];
    try {
      catalogue = await getJSON('/api/cloud/models?provider=' + encodeURIComponent(provider));
      if (revision !== drawRevision) return;
      if (catalogue.provider !== provider) throw new Error('Provider catalogue identity mismatch');
      models = (catalogue.models || []).filter(x => x && x.id && (!x.provider || x.provider === provider));
    } catch (e) { count.textContent = 'Model list unavailable — ' + e.message; pinBtn.disabled = true; }
    function renderMatches() {
      const previous = sel.value;
      sel.textContent = '';
      function addChoice(id, label) {
        const row = el('button', 'btn model-result', label); row.type = 'button'; row.value = id; row.setAttribute('role','option');
        row.style.cssText = 'text-align:left;white-space:normal;flex-shrink:0;width:100%';
        row.addEventListener('click', () => { sel.value = id; paintSelection(); }); sel.appendChild(row);
      }
      function paintSelection() {
        Array.from(sel.children).forEach(row => { const selected = row.value === sel.value; row.setAttribute('aria-selected', String(selected)); row.classList.toggle('primary', selected); });
      }
      addChoice('auto', 'No default — VOOL chooses');
      const query = search.value.trim().toLocaleLowerCase();
      const matches = models.filter(x => !query || modelSearchKey(x.id + ' ' + (x.name || '')).includes(modelSearchKey(query)));
      const shown = matches.slice(0, 40);
      const preferred = previous && previous !== 'auto' ? previous : (m.provider === provider ? m.model : 'auto');
      const chosen = matches.find(x => x.id === preferred);
      if (chosen && !shown.includes(chosen)) shown.unshift(chosen);
      shown.forEach(x => addChoice(x.id, (x.name || x.id) + (x.free ? ' · free' : ' · paid')));
      sel.value = shown.some(x => x.id === preferred) ? preferred : 'auto';
      paintSelection(); sel.scrollTop = 0;
      if (catalogue) count.textContent = matches.length ? 'Showing ' + shown.length + ' of ' + matches.length + ' models' + (matches.length > shown.length ? ' — search to narrow the list.' : '.') : 'No matching models.';
    }
    search.addEventListener('input', () => { searchText = search.value; renderMatches(); });
    renderMatches();
    pinBtn.addEventListener('click', async () => {
      const id = sel.value; const clearing = (id === 'auto'); pinBtn.disabled = true; pst.textContent = clearing ? 'Clearing the pin…' : 'Pinning…';
      try {
        let r = await fetch('/api/cloud/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: id, provider: provider }) });
        let j = await r.json().catch(() => ({}));
        if (r.status === 409 && j && typeof j.code === 'string') {
          const why = j.code === 'paid_model_confirm_required' ? 'The server classified this model as PAID.'
            : j.code === 'MODEL_COST_UNKNOWN' ? 'The server could not verify what this model costs.'
            : j.code === 'price_above_accepted' ? 'The catalogue now lists this model above the price you accepted earlier.'
            : 'This model\'s published pricing is indeterminate on the server.';
          if (!window.confirm(why + ' Pin "' + id + '" and allow spend on the pinned lane? Spend caps still apply.')) { pst.textContent = 'Not pinned.'; pinBtn.disabled = false; return; }
          r = await fetch('/api/cloud/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: id, provider: provider, confirm_paid: true }) });
          j = await r.json().catch(() => ({}));
        }
        if (!r.ok || (j && j.ok === false)) throw new Error((j && (j.error || j.code)) || ('HTTP ' + r.status));
        await draw(clearing ? 'Pin cleared — VOOL chooses.' : 'Pinned for every conversation.');
      } catch (e) { pst.textContent = 'Not pinned — ' + (e && e.message ? e.message : e); pinBtn.disabled = false; }
    });
    if (note) pst.textContent = note;   /* the message survives the redraw that shows the new pin */
    body.appendChild(sel); pick.appendChild(pinBtn); body.appendChild(pick); body.appendChild(pst);
    body.appendChild(el('div', 'subtle', 'This pin applies to every conversation. The composer\'s model pill changes the model for the conversation you are in.'));
    /* Discovery truth: what was actually OBSERVED for this provider's key and endpoint, when,
       and how fresh it is — distinct from the catalogue above, which is the picker's data. The
       refresh asks the provider once (re-verify, then the model list); an outage keeps the last
       observation and is named as itself; a model the provider dropped stays listed, marked
       gone. This is the place a person SEES provenance instead of trusting a bare list. */
    const discBox = el('details', 'stack');
    discBox.appendChild(el('summary', null, 'Provider details'));
    body.appendChild(discBox);
    const drawDiscovery = async () => {
      discBox.textContent = '';
      discBox.appendChild(el('summary', null, 'Provider details'));
      let d;
      try { d = await getJSON('/api/discovery'); } catch (e) { discBox.appendChild(el('div', 'subtle', 'Discovery state unavailable — ' + (e && e.message ? e.message : e))); return; }
      const entry = (d.providers || {})[provider];
      const refBtn = el('button', 'btn model-discovery-refresh', 'Refresh from ' + provider); refBtn.type = 'button';
      const rst = el('span', 'subtle model-discovery-state', '');
      refBtn.addEventListener('click', async () => {
        refBtn.disabled = true; rst.textContent = 'Asking ' + provider + ' once: verifying the key, then listing its models…';
        try {
          const r = await fetch('/api/discovery/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: provider }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok || j.ok === false) throw new Error((j && j.error) || ('HTTP ' + r.status));
          rst.textContent = j.last_refresh_status === 'verified'
            ? 'Refreshed ' + (j.observed_at || '') + ' — the key was re-verified and the list re-observed.'
            : 'Not refreshed — ' + (j.last_refresh_status || 'unknown') + '. The key was not judged unless the reason says so.';
          drawDiscovery();
        } catch (e) { rst.textContent = 'Refresh failed — ' + (e && e.message ? e.message : e); }
        refBtn.disabled = false;
      });
      const head = el('div', 'inline'); head.appendChild(refBtn); head.appendChild(rst); discBox.appendChild(head);
      if (!entry) { discBox.appendChild(el('div', 'subtle', 'Nothing observed yet for this provider. Refresh asks it once under its saved key.')); return; }
      const provenance = entry.evidence === 'observed' ? (entry.fresh ? 'observed ' + entry.observed_at + ' (fresh until ' + entry.expires_at + ')' : 'observed ' + entry.observed_at + ' (expired — refresh to re-observe)') : entry.evidence;
      discBox.appendChild(el('div', 'subtle', 'Evidence: ' + provenance + (entry.endpoint_fingerprint ? ' · endpoint ' + entry.endpoint_fingerprint : '') + (entry.last_refresh_status ? ' · last refresh: ' + entry.last_refresh_status : '')));
      const rows = entry.models || [];
      if (!rows.length) { discBox.appendChild(el('div', 'subtle', 'No models recorded for this provider yet.')); return; }
      rows.forEach(m => {
        const line = el('div', 'inline');
        line.appendChild(el('span', 'mono', m.model_id));
        const tag = m.status === 'missing' ? 'gone from the provider, last seen ' + (m.last_observed_at || '?')
          : m.evidence === 'observed' ? 'observed' : 'not observed for the current key/endpoint';
        line.appendChild(el('span', 'subtle', (m.display_name && m.display_name !== m.model_id ? m.display_name + ' · ' : '') + tag));
        discBox.appendChild(line);
      });
    };
    drawDiscovery();
    await autoFallbackControl(body, provider === 'openrouter' ? catalogue : null);
    body.appendChild(el('div', 'subtle model-mode-note',
      'Local Only is a composer mode, not a global pin: it belongs to ONE conversation and is set from the mode ' +
      'control next to the message box. Nothing on this page switches every chat to local-only; clearing the pin ' +
      'above lets VOOL choose, which includes local models when they are available.'));
  };
  draw();
}
const USEPOD_ORIGIN_DEFAULT = 'https://api.usepod.ai';
function usepodTime(ts) {
  const n = Number(ts);
  if (!isFinite(n) || n <= 0) return 'unknown time';
  try { return new Date(n * 1000).toLocaleString(); } catch (e) { return 'unknown time'; }
}
/* Exact integer microunits stay visible; the decimal is a display of the same number, never a
   rounding the arithmetic relies on (server-side money is integer microunits only). */
function usepodUsdc(microunits) {
  const n = Number(microunits);
  if (!isFinite(n)) return '?';
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(Math.round(n));
  return sign + (abs / 1e6).toFixed(6);
}
// Decimal input is converted exactly once at the boundary; the money law still receives integers.
function usepodAtomicAmount(value, asset) {
  const decimals = asset === 'SOL' ? 9 : 6;
  const text = String(value || '').trim();
  if (!/^\d+(?:\.\d+)?$/.test(text)) return null;
  const parts = text.split('.'), fraction = parts[1] || '';
  if (fraction.length > decimals) return null;
  const atomic = BigInt(parts[0]) * (10n ** BigInt(decimals)) + BigInt(fraction.padEnd(decimals, '0'));
  return atomic > 0n && atomic <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(atomic) : null;
}
function usepodBudgetScope(facts) {
  if (!facts || !facts.budget_mode || facts.budget_mode === 'once') return 'ONE request, up to ' + usepodDisplayAmount((facts || {}).per_call_atomic, 'USDC');
  return 'All UsePod models and chats · ' + usepodDisplayAmount(facts.max_total_atomic, 'USDC')
    + (facts.budget_mode === 'daily' ? ' every 24 hours, renewing while enabled' : ' in total, until used or disabled');
}
function usepodDisplayAmount(atomic, asset) {
  if (atomic == null || !Number.isSafeInteger(Number(atomic))) return '?';
  return (Number(atomic) / (asset === 'SOL' ? 1e9 : 1e6)).toFixed(asset === 'SOL' ? 9 : 6).replace(/\.?0+$/, '') + ' ' + asset;
}
function usepodRate(price) {
  if (!price) return null;
  return usepodUsdc(price.input_microunits_per_million) + ' input / ' + usepodUsdc(price.output_microunits_per_million) + ' output USDC per 1M tokens';
}
async function usepodPost(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, body: j || {} };
}
function widgetUsePod(stack) {
  const spendingOnly = state.focusPart === 'spend';
  const body = el('div', 'stack');
  stack.appendChild(body);
  const note = el('div', 'state idle usepod-note', '');
  stack.appendChild(note);
  const say = (cls, text) => { note.className = 'state ' + cls + ' usepod-note'; note.textContent = text; };

  const draw = async () => {
    body.textContent = '';
    body.appendChild(el('div', 'empty', 'Reading the UsePod caches…'));
    let d;
    try { d = await getJSON(USEPOD_DISCOVERY_URL); }
    catch (e) { body.textContent = ''; body.appendChild(el('div', 'empty', 'Unavailable — ' + e.message)); return; }
    body.textContent = '';

    /* -- how calls are paid: two distinct ways, never one blurred "UsePod". A funded token spends the UsePod account's
       balance; accountless x402 is quoted per call and paid from your wallet after you approve it on the Crypto Pilot
       card. The lane selector below switches between them too; this block says which one is in use. */
    const usepodAmount = (atomic, unit) => {
      const n = Number(atomic);
      if (atomic == null || !isFinite(n)) return '?';
      return usepodDisplayAmount(n, String(unit) === 'lamport' ? 'SOL' : 'USDC');
    };
    const payLane = d.lane || {};
    const payAuth = (d.authorities || {}).payment || {};
    const payCred = d.credential || {};
    const payNets = payAuth.verified_networks || [];
    body.appendChild(el('div', 'row-label', 'How calls are paid'));
    const payModes = el('div', 'stack usepod-pay-modes');
    body.appendChild(payModes);
    const useLane = async (transportMode, button) => {
      button.disabled = true;
      const { ok, body: j } = await usepodPost('/api/cloud/usepod/lane', { protocol: payLane.protocol || 'openai', transport_mode: transportMode });
      say(ok ? 'saved' : 'failed', ok ? ('Calls are now paid by ' + (transportMode === 'x402' ? 'your wallet (x402)' : 'the funded token') + '.') : ('Not switched — ' + (j.error || j.code || 'refused') + '.'));
      await draw();
    };
    const tokenCard = el('div', 'stack profile-item usepod-pay-mode');
    tokenCard.setAttribute('data-mode', 'prepaid_token');
    tokenCard.appendChild(el('div', 'row-label', 'Funded token (prepaid)' + (payLane.transport_mode !== 'x402' ? ' — in use' : '')));
    tokenCard.appendChild(el('div', 'subtle', 'Each paid call spends the UsePod account balance behind your stored token. ' + (payCred.configured ? 'A token is stored.' : 'No token is stored yet (API Keys → UsePod).')));
    if (payLane.transport_mode === 'x402') {
      const useToken = el('button', 'btn usepod-use-prepaid', 'Pay with the funded token'); useToken.type = 'button';
      useToken.addEventListener('click', () => useLane('prepaid_token', useToken));
      tokenCard.appendChild(useToken);
    }
    payModes.appendChild(tokenCard);
    const walletCard = el('div', 'stack profile-item usepod-pay-mode');
    walletCard.setAttribute('data-mode', 'x402');
    walletCard.appendChild(el('div', 'row-label', 'Wallet x402 (pay per call)' + (payLane.transport_mode === 'x402' ? ' — in use' : '')));
    walletCard.appendChild(el('div', 'subtle', 'Each call is quoted, waits for your approval on the Crypto Pilot card, and is paid from your wallet in USDC or SOL — no UsePod token needed.'));
    walletCard.appendChild(el('div', null, payAuth.installed
      ? ('Wallet authority ' + String(payAuth.label || '?') + ' · pays on: ' + (payNets.length ? payNets.join(', ') : 'no network now (turn Crypto on and finish a Crypto Pilot wallet on Solana Mainnet)'))
      : 'No wallet payment authority is installed.'));
    walletCard.appendChild(el('div', 'mono', 'Origin ' + String(d.origin || '?')));
    if (!payCred.configured) {
      const originLine = el('div', 'inline');
      const originInput = document.createElement('input'); originInput.type = 'url'; originInput.className = 'inp usepod-accountless-origin';
      originInput.setAttribute('aria-label', 'UsePod origin for accountless x402 (empty for the documented origin)');
      const originBtn = el('button', 'btn usepod-origin-save', 'Use this origin'); originBtn.type = 'button';
      originBtn.addEventListener('click', async () => {
        originBtn.disabled = true;
        const { ok, body: j } = await usepodPost('/api/cloud/usepod/lane', { protocol: payLane.protocol || 'openai', transport_mode: payLane.transport_mode || 'prepaid_token', origin: originInput.value.trim() });
        say(ok ? 'saved' : 'failed', ok ? ('Accountless calls now go to ' + String(j.origin || '?') + '.') : ('Origin not changed — ' + (j.error || j.code || 'refused') + '.'));
        await draw();
      });
      originLine.appendChild(originInput); originLine.appendChild(originBtn);
      walletCard.appendChild(originLine);
      walletCard.appendChild(el('div', 'subtle', 'Leave it empty for the documented UsePod origin; another origin is only ever your explicit choice.'));
    } else {
      walletCard.appendChild(el('div', 'subtle', 'The origin belongs to the stored token and changes only when the token is saved again.'));
    }
    if (payLane.transport_mode !== 'x402') {
      const useWallet = el('button', 'btn usepod-use-x402', 'Pay from the wallet (x402)'); useWallet.type = 'button';
      useWallet.addEventListener('click', () => useLane('x402', useWallet));
      walletCard.appendChild(useWallet);
    }
    payModes.appendChild(walletCard);

    /* -- credential: configuration facts only. The stored token itself is never returned by the
       runtime and has no field here to land in; the fingerprint is one-way over origin+token. */
    body.appendChild(el('div', 'row-label', 'Credential'));
    const cred = d.credential || {};
    if (!cred.configured) {
      body.appendChild(el('div', 'subtle',
        'No UsePod token stored. Save one under API Keys (Model providers → UsePod): paste the token, or the whole proxy URL — the runtime splits them and stores only the token, sealed.'));
    } else {
      const kv = el('dl', 'kv');
      const stateText = { configured_not_verified_here: 'stored, not verified in this view (Test asks UsePod once)', token_or_origin_unusable: 'stored, but the token or origin is not usable as configured' }[cred.state] || cred.state;
      kv.appendChild(el('dt', null, 'Token')); kv.appendChild(el('dd', null, stateText + (cred.source ? ' · from ' + cred.source : '')));
      kv.appendChild(el('dt', null, 'Origin'));
      kv.appendChild(el('dd', 'mono', String(cred.origin || 'unknown') + (cred.origin_is_default === false ? ' (you chose this origin)' : '')));
      if (cred.fingerprint) { kv.appendChild(el('dt', null, 'Fingerprint')); kv.appendChild(el('dd', 'mono', cred.fingerprint)); }
      if (cred.token_shape) { kv.appendChild(el('dt', null, 'Token shape')); kv.appendChild(el('dd', null, String(cred.token_shape))); }
      body.appendChild(kv);
      const line = el('div', 'inline');
      const test = el('button', 'btn usepod-test', 'Test'); test.type = 'button';
      const st = el('span', 'subtle usepod-test-state', '');
      test.addEventListener('click', async () => {
        test.disabled = true; st.textContent = 'Asking UsePod once whether this token is accepted (a balance read; no inference, no spend)…';
        const { ok, body: j } = await usepodPost('/api/cloud/test', { provider: 'usepod' });
        st.textContent = j.state === 'ok' ? 'Connection verified — UsePod accepted the token.'
          : (j.detail === 'unauthorized' || j.http_status === 401 || j.http_status === 403) ? 'UsePod rejected the token (HTTP ' + (j.http_status || '?') + ').'
          : 'Could not reach UsePod — the token itself was not judged' + (j.detail ? ' (' + j.detail + ')' : '') + '.';
        test.disabled = false;
      });
      line.appendChild(test); line.appendChild(st); body.appendChild(line);
    }

    /* -- balance: one observation with its time. Absent is named, never zero. */
    const disc = d.credential_discovery || {};
    const bal = disc.balance;
    body.appendChild(el('div', 'row-label', 'Balance'));
    if (!bal) {
      body.appendChild(el('div', 'subtle', cred.configured
        ? 'Not observed for this credential yet. Refresh reads it once (GET /proxy/<token>/balance; no inference, no spend).'
        : 'Nothing to read without a stored token.'));
    } else if (bal.state === 'reported') {
      body.appendChild(el('div', null,
        usepodUsdc(bal.usdc_balance_microunits) + ' USDC (' + bal.usdc_balance_microunits + ' µUSDC) — observed ' + usepodTime(bal.observed_at) + '. An observation, not a spending authority: concurrent use makes it stale immediately.'));
    } else {
      body.appendChild(el('div', null, 'Not reported (' + String(bal.state) + (bal.error_code ? ': ' + bal.error_code : '') + '). Missing is not zero.'));
    }

    /* -- marketplace snapshot: source, age, expiry. Refresh is the only network action on this panel. */
    body.appendChild(el('div', 'row-label', 'Marketplace prices'));
    const market = d.marketplace || {};
    const marketLine = el('div', 'inline');
    const refresh = el('button', 'btn usepod-refresh', 'Refresh from UsePod'); refresh.type = 'button';
    const rst = el('span', 'subtle usepod-refresh-state', '');
    refresh.addEventListener('click', async () => {
      refresh.disabled = true; rst.textContent = 'Fetching the public marketplace feed' + (cred.configured ? ' and this credential\u2019s model list and balance' : '') + '…';
      const { ok, body: j } = await usepodPost('/api/cloud/usepod/refresh', {});
      if (!ok) { rst.textContent = 'Not refreshed — ' + (j.error || 'HTTP refused') + '.'; refresh.disabled = false; return; }
      const m = j.marketplace || {}, c = j.credential || {};
      /* The outcome goes on the persistent note: draw() below rebuilds this row, and a message
         written into the row it destroys was never seen by anyone. */
      say('saved', 'Refreshed: feed ' + String(m.state || '?') + (m.error_code ? ' (' + m.error_code + ')' : '') + ' · credential ' + String(c.state || '?') + '.');
      await draw();
    });
    marketLine.appendChild(refresh); marketLine.appendChild(rst); body.appendChild(marketLine);
    /* provenance() carries no `state` field when a snapshot exists -- only the no-cache view
       does -- so presence is decided by the observation itself, not by a missing key. */
    const hasSnapshot = !!market && (market.fetched_at != null || market.body_sha256 != null);
    if (!hasSnapshot) {
      body.appendChild(el('div', 'subtle', 'Never fetched here. No price is known, and none is invented: dispatch refuses (price_feed_unavailable) until a real feed is fetched.'));
    } else {
      const kv = el('dl', 'kv');
      kv.appendChild(el('dt', null, 'Source')); kv.appendChild(el('dd', 'mono', String(market.source || market.origin || 'unknown')));
      kv.appendChild(el('dt', null, 'Fetched')); kv.appendChild(el('dd', null, usepodTime(market.fetched_at) + ' · age ' + Math.max(0, Math.round(Number(market.age_seconds) || 0)) + 's of a ' + (market.ttl_seconds || '?') + 's TTL'));
      if (market.stale) kv.appendChild(el('dd', null, 'STALE — dispatch refuses at stale prices until Refresh.'));
      kv.appendChild(el('dt', null, 'Rows'));
      kv.appendChild(el('dd', null, (market.row_count ?? '?') + ' received · ' + (market.readable_rows ?? '?') + ' usable · ' + (market.rejected_rows ?? '?') + ' rejected'));
      if (market.fixture_label) { kv.appendChild(el('dt', null, 'Fixture')); kv.appendChild(el('dd', null, String(market.fixture_label))); }
      kv.appendChild(el('dt', null, 'Unit')); kv.appendChild(el('dd', null, String(market.unit || '?') + ' (' + String(market.currency || '?') + ')'));
      body.appendChild(kv);
      body.appendChild(el('div', 'subtle', 'Snapshot body ' + String(market.body_sha256 || '').slice(0, 16) + '… — every approved bound names the snapshot it was approved against.'));
    }

    /* -- paid-dispatch dependencies, as they stand. A missing authority is a fact with a door,
       not a gap to paper over: the lane still selects, and its card says it will refuse. */
    const auth = d.authorities || {};
    body.appendChild(el('div', 'row-label', 'Paid-dispatch dependencies'));
    const money = (auth.monetary || {});
    const pay = (auth.payment || {});
    const depLine = (ok2, label, have, missing) => el('div', null,
      (ok2 ? '✓ ' : '✗ ') + label + ' — ' + (ok2 ? have : missing));
    /* The money LAW is installed once merged; the operative facts for the operator are the
       grants (what may be spent) and the projection (what has been). A test double says so. */
    const moneyLabel = String(money.label || '?');
    const grants = money.grants || [];
    const grantBits = grants.map(g => String(g.grant_id).slice(0, 14) + '… up to ' + usepodUsdc(g.per_operation_max_atomic) + ' USDC/call');
    body.appendChild(depLine(!!money.installed, 'Monetary authority (the money law)',
      moneyLabel.startsWith('test_double:')
        ? 'a labelled TEST DOUBLE is installed (' + moneyLabel + ') — not real money integration'
        : 'installed: ' + moneyLabel + (grants.length
            ? '. Active spend grants: ' + grantBits.join(' · ')
            : '. NO active spend grant for UsePod — every paid turn refuses (MONEY_AUTHORITY_INVALID) until an operator grants one.'),
      moneyLabel.startsWith('unavailable:')
        ? 'none integrated. Every prepaid dispatch is refused before anything is sent. This is the monetary-budgets integration — Settings → Budgets.'
        : 'Spending caps come from operator grants (the money law), not from route approval alone.'));
    const payNetworks = pay.verified_networks || [];
    body.appendChild(depLine(!!pay.installed && payNetworks.length > 0, 'Wallet payment authority (x402 signing)',
      'installed: ' + String(pay.label || '?') + (String(pay.label || '').startsWith('test_double:') ? ' (a labelled TEST DOUBLE, not a real wallet)' : '') + '; pays on: ' + payNetworks.join(', '),
      pay.installed
        ? 'installed (' + String(pay.label || '?') + ') but it pays on no network now: turn Crypto on, choose Mainnet and finish a Crypto Pilot wallet on Solana Mainnet. Accountless x402 is refused at the pick until then (wallet_payment_network_unverified) — Settings → Wallet.'
        : 'none integrated. Accountless x402 is refused before any reservation or payment (wallet_payment_authority_unavailable) — Settings → Wallet.'));

    /* -- trusted spend approval: the user's consent bridge. Proposing registers a PENDING
       approval in the SAME approval store the tool gate uses; the operator allows or denies it
       through the ordinary approval surface; confirming mints the grant from the facts shown
       here (server-derived account/asset/models/routes; the operator chooses only ceilings).
       Route approval above bounds what a dispatch may cost; this consents to what may be
       SPENT. Every cap field is integer µUSDC. */
    if (spendingOnly) body.textContent = '';
    if (spendingOnly) {
      /* The decision, readable in one place: provider, model(s), the current price, the balance
         the money law will judge, the maximum this approval allows, how long it is valid and its
         actual scope (ONE request). Ordinary USDC; integer units and fingerprints stay in the
         approval details below. Nothing here is authority -- it is what the operator decides on. */
      const summary = el('div', 'stack usepod-spend-summary');
      summary.appendChild(el('div', 'row-label', '1. Your UsePod account'));
      const kv = el('dl', 'kv');
      const laneMode = String((d.lane || {}).transport_mode || 'prepaid_token');
      kv.appendChild(el('dt', null, 'Provider')); kv.appendChild(el('dd', null, 'UsePod — ' + (laneMode === 'x402' ? 'wallet x402, paid per call' : 'prepaid token balance')));
      const bal = (d.credential_discovery || {}).balance;
      kv.appendChild(el('dt', null, 'Balance'));
      const balanceLine = el('dd', 'usepod-spend-balance');
      if (bal && bal.state === 'reported') {
        const age = bal.observed_at ? Math.max(0, Math.round(Date.now() / 1000 - Number(bal.observed_at))) : null;
        balanceLine.textContent = usepodDisplayAmount(bal.usdc_balance_microunits, 'USDC') + (age == null ? '' : (age < 60 ? ' — checked just now' : ' — checked ' + Math.round(age / 60) + ' min ago'))
          + (age != null && age > 900 ? ' · older than 15 minutes; it is re-read before sending' : '');
      } else if (bal) {
        balanceLine.textContent = 'Not verified (' + String(bal.state) + (bal.http_status ? ', HTTP ' + bal.http_status : (bal.error_code ? ', ' + bal.error_code : '')) + ') — sending is refused until UsePod confirms a balance.';
      } else {
        balanceLine.textContent = 'Not read yet — it is read before your first request.';
      }
      kv.appendChild(balanceLine);
      summary.appendChild(kv);
      const checkLine = el('div', 'inline');
      const checkBtn = el('button', 'btn usepod-balance-check', 'Check balance now'); checkBtn.type = 'button';
      const checkState = el('span', 'subtle usepod-balance-check-state', '');
      checkBtn.addEventListener('click', async () => {
        checkBtn.disabled = true; checkState.textContent = 'Reading the balance once (no inference, no spend)…';
        const { ok, body: j } = await usepodPost('/api/cloud/usepod/balance', { max_age_seconds: 0 });
        say(ok && j.state === 'reported' ? 'saved' : 'failed', ok && j.state === 'reported'
          ? 'Balance ' + usepodDisplayAmount(j.usdc_balance_microunits, 'USDC') + ' confirmed by UsePod.'
          : 'Balance not verified — ' + String(j.state || j.error || 'refused') + (j.http_status ? ' (HTTP ' + j.http_status + ')' : '') + '.');
        await draw();
      });
      checkLine.appendChild(checkBtn); checkLine.appendChild(checkState); summary.appendChild(checkLine);
      body.appendChild(summary);
    }
    body.appendChild(el('div', 'row-label', spendingOnly ? '2. Choose your budget' : 'UsePod spending approval'));
    if (spendingOnly && !payCred.configured && payLane.transport_mode !== 'x402') {
      body.appendChild(el('p', null, 'Connect your funded UsePod token first. It stays on this device.'));
      const connect = el('button', 'btn primary', 'Connect UsePod');
      connect.addEventListener('click', () => { location.hash = '#keys'; });
      body.appendChild(connect); return;
    }
    const approvalBox = el('div', 'stack usepod-spend-approval');
    body.appendChild(approvalBox);
    const drawApproval = async () => {
      approvalBox.textContent = '';
      const consent = d.spend_approval;
      if (consent && consent.facts && consent.state) {
        const facts = consent.facts;
        const kv = el('dl', 'kv');
        const headline = {
          pending: 'Awaiting YOUR approval — Allow or Deny it here',
          approved: 'You approved this budget; finish enabling it below',
          denied: 'You DENIED this consent; nothing was enabled',
          expired: 'Previous approval expired. Choose a new budget below.',
          minted: consent.grant && consent.grant.expired ? 'Spending approval expired — allow a fresh budget below' : (facts.budget_mode && facts.budget_mode !== 'once' ? (consent.grant && consent.grant.state === 'revoked' ? 'Budget disabled' : 'UsePod budget enabled') : 'One paid call was enabled by this consent; remaining availability is checked before sending'),
          invalid: 'This consent record is incomplete; it cannot be used',
        }[consent.state] || consent.state;
        kv.appendChild(el('dt', null, 'Consent')); kv.appendChild(el('dd', null, headline));
        if (consent.state !== 'minted' && facts && Object.keys(facts).length) {
          kv.appendChild(el('dt', null, 'Exactly')); kv.appendChild(el('dd', null, usepodBudgetScope(facts)));
          kv.appendChild(el('dt', null, 'Account')); kv.appendChild(el('dd', 'mono', String(facts.account)));
          kv.appendChild(el('dt', null, 'Principal asset')); kv.appendChild(el('dd', null, String(facts.asset) + ' on ' + String(facts.network)));
          if (facts.x402) {
            /* The fee rail is a SEPARATE monetary fact the operator consents to. */
            kv.appendChild(el('dt', null, 'Fee asset')); kv.appendChild(el('dd', null, String(facts.x402.fee_asset) + ' on ' + String(facts.x402.fee_network) + ' — up to ' + usepodAmount(facts.x402.fee_max_atomic, String(facts.x402.fee_asset) === 'SOL' ? 'lamport' : 'usdc_microunit') + ' per call'));
          }
          if (facts.expiry_epoch) {
            kv.appendChild(el('dt', null, 'Spending window ends'));
            kv.appendChild(el('dd', null, new Date(Number(facts.expiry_epoch) * 1000).toLocaleString()));
          }
          kv.appendChild(el('dt', null, 'Models')); kv.appendChild(el('dd', 'mono', (facts.models || []).join(', ')));
        }
        if (consent.grant) {
          kv.appendChild(el('dt', null, 'Grant'));
          kv.appendChild(el('dd', 'mono', String(consent.grant.grant_id) + ' · ' + String(consent.grant.state)));
        }
        if (spendingOnly) {
          approvalBox.appendChild(el('div', 'row-label', headline));
          approvalBox.appendChild(el('div', null, usepodBudgetScope(facts)));
          if (facts.x402) approvalBox.appendChild(el('div', null, 'Network fee: up to ' + usepodAmount(facts.x402.fee_max_atomic, String(facts.x402.fee_asset) === 'SOL' ? 'lamport' : 'usdc_microunit')));
          const details = el('details'); details.appendChild(el('summary', null, 'Approval details')); details.appendChild(kv); approvalBox.appendChild(details);
        } else approvalBox.appendChild(kv);
        if (consent.grant && consent.grant.kind === 'provider_budget' && consent.grant.state === 'active') {
          const remaining = consent.grant.headroom || {};
          approvalBox.appendChild(el('div', null, 'Remaining budget: ' + usepodDisplayAmount(remaining.principal_left_atomic, 'USDC')
            + (consent.grant.renews_at ? ' · resets ' + usepodTime(consent.grant.renews_at) : '')));
          const disable = el('button', 'btn danger usepod-budget-disable', 'Disable UsePod budget');
          disable.addEventListener('click', async () => {
            disable.disabled = true;
            const result = await usepodPost('/api/money/grants/revoke', {grant_id: consent.grant.grant_id, reason:'Disabled in UsePod Settings'});
            say(result.ok ? 'saved' : 'failed', result.ok ? 'Budget disabled. No new paid requests are authorized.' : 'Could not disable budget.'); await draw();
          }); approvalBox.appendChild(disable);
        }
        if (consent.state === 'pending') {
          /* The operator's OWN Allow/Deny, through the same trusted resolution door the chat
             approval buttons use. Visible controls; denial mints nothing. */
          const line = el('div', 'inline');
          const allowBtn = el('button', 'btn primary usepod-spend-allow', facts.budget_mode && facts.budget_mode !== 'once' ? 'Approve UsePod budget' : (spendingOnly ? 'Approve one paid call' : 'Allow')); allowBtn.type = 'button';
          const denyBtn = el('button', 'btn danger usepod-spend-deny', 'Deny'); denyBtn.type = 'button';
          const ast = el('span', 'subtle usepod-spend-allow-state', '');
          let resolving = false;
          const resolve = async (decision) => {
            if (resolving) return; resolving = true;
            allowBtn.disabled = true; denyBtn.disabled = true; ast.textContent = 'Recording your ' + decision + '…';
            try {
            const r = await fetch('/api/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: 'resolve_approval', approval_id: consent.approval_id, decision: decision, session_id: 'openclaw:settings-operator' }) });
            const j = await r.json().catch(() => ({}));
            ast.textContent = r.ok ? ('Consent ' + decision + 'ed.') : ('Not recorded — ' + (j.error || 'HTTP ' + r.status) + '.');
            if (r.ok && decision === 'allow') {
              const result = await usepodPost('/api/cloud/usepod/spend-approval/confirm', { approval_id: consent.approval_id });
              say(result.ok ? 'saved' : 'failed', result.ok ? 'Budget enabled. Return to chat to send; eligible tasks already queued can start automatically.' : 'Approval recorded, but enabling failed — ' + (result.body.error || 'retry below') + '.');
            }
            if (!r.ok) say('failed', 'Approval not recorded — ' + (j.error || 'HTTP ' + r.status) + '.');
            await draw();
            } catch (e) {
              say('failed', 'Could not finish this approval. Checking its saved state — ' + e.message);
              await draw();
            } finally { resolving = false; allowBtn.disabled = false; denyBtn.disabled = false; }
          };
          allowBtn.addEventListener('click', () => resolve('allow'));
          denyBtn.addEventListener('click', () => resolve('deny'));
          line.appendChild(allowBtn); line.appendChild(denyBtn); line.appendChild(ast);
          approvalBox.appendChild(line);
          return;
        }
        if (consent.state === 'approved') {
          const line = el('div', 'inline');
          const confirmBtn = el('button', 'btn primary usepod-spend-confirm', 'Enable approved budget'); confirmBtn.type = 'button';
          const cst = el('span', 'subtle usepod-spend-confirm-state', '');
          confirmBtn.addEventListener('click', async () => {
            confirmBtn.disabled = true; cst.textContent = 'Enabling…';
            const { ok, body: j } = await usepodPost('/api/cloud/usepod/spend-approval/confirm', { approval_id: consent.approval_id });
            /* The outcome rides the persistent note: the redraw below replaces this branch. */
            if (ok) say('saved', 'Enabled: ' + String(j.grant_id) + (j.idempotent ? ' (already enabled)' : '') + '. ' + usepodBudgetScope(facts));
            else say('failed', 'Not enabled — ' + (j.error || 'refused') + '.');
            await draw();
          });
          line.appendChild(confirmBtn); line.appendChild(cst); approvalBox.appendChild(line);
          return;
        }
        /* expired, invalid, denied and a used (or revoked) mint are all dead ends for THIS
           consent: the only recovery is a FRESH proposal, which needs a NEW explicit Allow. */
        if (consent.state !== 'pending' && facts.x402) {
          const line = el('div', 'inline');
          const againBtn = el('button', 'btn usepod-spend-propose-again', spendingOnly ? 'Review a new approval' : 'Enable one paid call — start a fresh approval'); againBtn.type = 'button';
          const ast2 = el('span', 'subtle usepod-spend-again-state', '');
          againBtn.addEventListener('click', async () => {
            againBtn.disabled = true; ast2.textContent = 'Requesting a fresh approval…';
            /* The ceilings the expired consent named are pre-filled as the starting point, not
               carried as authority: the operator re-reads and re-allows everything. */
            const per = facts && facts.per_call_atomic ? String(facts.per_call_atomic) : '';
            const tot = facts && facts.max_total_atomic ? String(facts.max_total_atomic) : '';
            const again = { per_call_atomic: parseInt(per, 10) || 0, max_total_atomic: parseInt(tot, 10) || 0 };
            if (facts && facts.x402 && facts.asset) again.asset = String(facts.asset);
            const { ok, body: j } = await usepodPost('/api/cloud/usepod/spend-approval/propose', again);
            if (!ok) { ast2.textContent = 'Not proposed — ' + (j.error || 'refused') + '.'; againBtn.disabled = false; return; }
            say('saved', 'Fresh approval requested. Allow or deny it above — nothing is enabled until you do.');
            await draw();
          });
          line.appendChild(againBtn); line.appendChild(ast2); approvalBox.appendChild(line);
        }
        /* A dead end for THIS consent is not the end of consenting: the form below proposes fresh ceilings (and, for
           accountless x402, the asset) instead of repeating the old ones. */
      }
      const line = el('div', 'inline');
      const perCall = document.createElement('input'); perCall.type = 'number'; perCall.min = '0.000001'; perCall.step = '0.000001'; perCall.placeholder = 'e.g. 3'; perCall.className = 'inp usepod-spend-percall'; perCall.setAttribute('aria-label', 'Maximum for this request');
      const total = document.createElement('input'); total.type = 'number'; total.min = '0.000001'; total.step = '0.000001'; total.placeholder = 'e.g. 3'; total.className = 'inp usepod-spend-total'; total.setAttribute('aria-label', 'Total spending limit');
      const hours = document.createElement('input'); hours.type = 'number'; hours.min = '1'; hours.max = '720'; hours.value = '24'; hours.className = 'inp usepod-spend-hours'; hours.setAttribute('aria-label', 'Approval lifetime in hours');
      const proposeBtn = el('button', 'btn primary usepod-spend-propose', 'Review budget'); proposeBtn.type = 'button';
      const pst = el('span', 'subtle usepod-spend-propose-state', '');
      const accountlessLane = String((d.lane || {}).transport_mode || '') === 'x402';
      const assetSelect = el('select', 'inp usepod-spend-asset'); assetSelect.setAttribute('aria-label', 'Asset this consent pays in');
      [['USDC', 'USDC'], ['SOL', 'SOL']].forEach(([v, t]) => { const o = el('option', null, t); o.value = v; assetSelect.appendChild(o); });
      const budgetMode = el('select', 'inp usepod-budget-mode'); budgetMode.setAttribute('aria-label', 'Budget type');
      [['daily','Every 24 hours — renew while enabled'],['total','Total budget — until used or disabled']].forEach(([v,t])=>{const o=el('option',null,t);o.value=v;budgetMode.appendChild(o);});
      budgetMode.value = 'daily';
      const unitHint = el('span', 'subtle usepod-spend-unit', 'Amounts in USDC');
      assetSelect.addEventListener('change', () => {
        const asset = assetSelect.value === 'SOL' ? 'SOL' : 'USDC';
        unitHint.textContent = 'Amounts in ' + asset;
        perCall.min = total.min = perCall.step = total.step = asset === 'SOL' ? '0.000000001' : '0.000001';
      });
      proposeBtn.addEventListener('click', async () => {
        proposeBtn.disabled = true; pst.textContent = 'Registering the approval…';
        const asset = accountlessLane && assetSelect.value === 'SOL' ? 'SOL' : 'USDC';
        const tot = usepodAtomicAmount(total.value || perCall.value, asset), per = usepodAtomicAmount(perCall.value || total.value, asset), hrs = Number(hours.value);
        if (!per || !tot || (accountlessLane && (!Number.isInteger(hrs) || hrs < 1 || hrs > 720))) {
          pst.textContent = accountlessLane ? 'Enter a positive amount in ' + asset + ' and a validity of 1–720 whole hours.' : 'Enter a positive budget in USDC.'; proposeBtn.disabled = false; return;
        }
        const proposal = { per_call_atomic: per, max_total_atomic: tot, expiry_epoch: Math.floor(Date.now() / 1000) + hrs * 3600 };
        if (accountlessLane) proposal.asset = assetSelect.value;
        else { proposal.budget_mode = budgetMode.value; proposal.expiry_epoch = 0; }
        const { ok, body: j } = await usepodPost('/api/cloud/usepod/spend-approval/propose', proposal);
        if (!ok) { pst.textContent = 'Not proposed — ' + (j.error || 'refused') + '.'; proposeBtn.disabled = false; return; }
        say('saved', 'Review the budget above, then approve or deny it. Nothing is enabled yet.');
        await draw();
        /* The redraw replaced the form; a refusal written into it would never be seen. */
      });
      if (accountlessLane) line.appendChild(assetSelect);
      line.appendChild(unitHint);
      const field = (text, input) => { const label = el('label', 'stack', text); label.appendChild(input); line.appendChild(label); };
      if (!accountlessLane) field('Budget type', budgetMode);
      field(accountlessLane ? 'Total spending limit' : 'Budget (USDC)', total);
      const advanced = el('details', 'usepod-budget-advanced'); advanced.appendChild(el('summary', null, 'Advanced limits'));
      const perLabel = el('label', 'stack', 'Maximum per request (USDC; optional)'); perLabel.appendChild(perCall); advanced.appendChild(perLabel); line.appendChild(advanced);
      if (accountlessLane) field('Valid for (hours)', hours);
      line.appendChild(proposeBtn); line.appendChild(pst);
      if (spendingOnly && consent && consent.facts) {
        const change = el('details'); change.open = !accountlessLane && (!consent.facts.budget_mode || consent.facts.budget_mode === 'once' || consent.state !== 'minted' || (consent.grant && consent.grant.state !== 'active')); change.appendChild(el('summary', null, 'Change spending limits')); change.appendChild(line); approvalBox.appendChild(change);
      } else approvalBox.appendChild(line);
      approvalBox.appendChild(el('div', 'subtle', accountlessLane ? 'This approval covers one wallet-paid request.' : 'One approval covers all UsePod models and chats on this prepaid account. A 24-hour budget renews while enabled; a total budget stops when used. New limits replace the previous budget. Disable it here at any time. Price limits still apply; eligible tasks already queued can start automatically.'));
    };
    drawApproval();
    const waitBox = el('details', 'stack usepod-price-wait'); waitBox.appendChild(el('summary', null, 'Advanced: wait for a lower price'));
    waitBox.appendChild(el('p', 'subtle', 'Save a start price for a model, then select it in chat and send your task. VOOL keeps the task queued until both prices fit. Keep VOOL open; pending tasks survive a restart. You can cancel from the chat. Optional: allow a price rise during this task. Above that limit VOOL pauses before the next model call and continues automatically when prices fit. An already-sent call finishes. Keep VOOL open while paused; closing it does not preserve an active model-call continuation. Every call still requires your available budget.'));
    const waitModel = el('input', 'inp usepod-wait-model'); waitModel.placeholder = 'Model ID, e.g. gpt-6-astra'; waitModel.setAttribute('aria-label','Price-wait model');
    const waitInput = el('input', 'inp usepod-wait-input'); waitInput.type='number';waitInput.min='0.000001';waitInput.step='0.000001';waitInput.placeholder='e.g. 0.8';
    const waitOutput = el('input', 'inp usepod-wait-output'); waitOutput.type='number';waitOutput.min='0.000001';waitOutput.step='0.000001';waitOutput.placeholder='e.g. 4';
    const waitTolerance = el('input', 'inp usepod-wait-tolerance');waitTolerance.type='number';waitTolerance.min='0';waitTolerance.max='1000';waitTolerance.step='1';waitTolerance.placeholder='e.g. 10';
    const waitInterval = el('input', 'inp usepod-wait-interval');waitInterval.type='number';waitInterval.min='1';waitInterval.max='60';waitInterval.value='5';
    const waitField=(label,input)=>{const field=el('label','stack',label);field.appendChild(input);waitBox.appendChild(field);};
    waitField('Model',waitModel);waitField('Maximum input price · USDC per 1M tokens',waitInput);waitField('Maximum output price · USDC per 1M tokens',waitOutput);waitField('Check every (minutes)',waitInterval);waitField('Allowed price rise during task (%) — blank means budget only',waitTolerance);
    const waitSave=el('button','btn usepod-wait-save','Save start-price rule');
    waitSave.addEventListener('click',async()=>{waitSave.disabled=true;const result=await usepodPost('/api/cloud/usepod/price-wait/save',{model_id:waitModel.value.trim(),max_input_usdc:waitInput.value,max_output_usdc:waitOutput.value,poll_minutes:Number(waitInterval.value),active_price_tolerance_percent:waitTolerance.value.trim()===''?null:Number(waitTolerance.value)});say(result.ok?'saved':'failed',result.ok?'Start-price rule saved. No task or payment was sent.':(result.body.error||'Could not save the rule'));if(result.ok)await draw();waitSave.disabled=false;});waitBox.appendChild(waitSave);
    Object.entries(d.approved_routes || {}).forEach(([id,bound])=>{if(!bound.wait_poll_seconds)return;const row=el('div','stack');row.appendChild(el('div',null,id+' · input '+bound.max_input_usdc_per_million+' / output '+bound.max_output_usdc_per_million+' USDC per 1M · every '+bound.wait_poll_seconds/60+' minutes' + (bound.active_price_tolerance_percent == null ? ' · active task limited by budget' : ' · pause above +'+bound.active_price_tolerance_percent+'%')));const stop=el('button','btn','Remove price rule');stop.addEventListener('click',async()=>{await usepodPost('/api/cloud/usepod/forget-route',{model_id:id});say('saved','Price rule removed. Previously queued tasks remain paused; cancel them in chat. Select the model again to approve current prices.');await draw();});row.appendChild(stop);waitBox.appendChild(row);});
    body.appendChild(waitBox);
    if (spendingOnly) {
      body.appendChild(el('div', 'subtle', 'Your chat and draft are unchanged. Return to VOOL to send your draft. Tasks you already queued can start when their price and budget conditions are met.'));
      $('#pane').scrollTop = 0;
      return;
    }

    /* -- paid calls waiting for an answer: an accountless call that was paid but whose answer never arrived (a lost
       answer, or the app stopping mid-call). Resume sends the recorded request and payment proof once more: never a
       new quote, never a second payment. */
    body.appendChild(el('div', 'row-label', 'Paid calls waiting for an answer'));
    const unresolvedBox = el('div', 'stack usepod-x402-unresolved');
    body.appendChild(unresolvedBox);
    getJSON('/api/cloud/usepod/x402/unresolved').then(u => {
      const ops = (u && u.operations) || [];
      if (!ops.length) { unresolvedBox.appendChild(el('div', 'subtle', 'None: every paid call has its answer.')); return; }
      ops.forEach(op => {
        const row = el('div', 'inline usepod-x402-operation');
        row.setAttribute('data-operation', String(op.operation_id));
        row.appendChild(el('span', 'mono', String(op.model_id || '?') + ' · ' + String(op.state || '?').replace(/_/g, ' ') + ' · liability ' + String(op.liability_state || '?') + ' · signature ' + String(op.proof_signature || '').slice(0, 10) + '…'));
        if (op.resumable) {
          const resumeBtn = el('button', 'btn usepod-x402-resume', 'Resume'); resumeBtn.type = 'button';
          resumeBtn.addEventListener('click', async () => {
            resumeBtn.disabled = true;
            const { ok, body: j } = await usepodPost('/api/cloud/usepod/x402/resume', { operation_id: op.operation_id });
            const answer = String(j.answer || '');
            say(ok ? 'saved' : 'failed', ok
              ? ('Resumed: the paid call answered — ' + answer.slice(0, 160) + (answer.length > 160 ? '…' : '') + ' (' + String(j.settlement_recording || '').replace(/_/g, ' ') + ')')
              : ('Not resumed — ' + (j.code || j.error || 'refused') + (j.http_status ? ' (provider HTTP ' + j.http_status + ')' : '') + '. Nothing was paid again.'));
            await draw();
          });
          row.appendChild(resumeBtn);
        }
        unresolvedBox.appendChild(row);
      });
    }).catch(e => { unresolvedBox.appendChild(el('div', 'subtle', 'Unavailable — ' + e.message)); });

    /* -- money state: what has actually been reserved, settled or held unknown, from the
       owner-local money projection. A read; it grants nothing. */
    body.appendChild(el('div', 'row-label', 'Money state (reserved · settled · unknown)'));
    const moneyState = el('div', 'stack usepod-money-state');
    body.appendChild(moneyState);
    getJSON('/api/money/projection?provider_id=usepod').then(p => {
      const proj = (p && p.projection) || {};
      const usdcBuckets = (buckets) => Object.entries(buckets || {}).map(([k, v]) => {
        const parts = [];
        if (Number(v.settled_exact) > 0) parts.push('settled ' + usepodUsdc(v.settled_exact));
        if (Number(v.settled_bounded) > 0) parts.push('settled ≤' + usepodUsdc(v.settled_bounded));
        if (Number(v.held) > 0) parts.push('held ' + usepodUsdc(v.held));
        if (Number(v.unknown) > 0) parts.push('unknown ' + usepodUsdc(v.unknown));
        return parts.length ? k + ': ' + parts.join(', ') : '';
      }).filter(Boolean);
      const lines = [].concat(usdcBuckets(proj.inference_expense), usdcBuckets(proj.provider_credit_debits));
      if (lines.length) lines.forEach(l => moneyState.appendChild(el('div', 'subtle mono', l)));
      else moneyState.appendChild(el('div', 'subtle', 'No UsePod money has moved through the money law yet.'));
      const unknown = (proj.unknown_liabilities || []).length;
      if (unknown) moneyState.appendChild(el('div', 'state failed', unknown + ' liability(ies) held UNKNOWN — they keep their maximum until evidence closes them; never read as free.'));
    }).catch(() => { moneyState.appendChild(el('div', 'subtle', 'The money projection is not readable right now.')); });

    /* -- lane: the wire protocol and how calls are paid. Separate from price approvals on purpose. */
    body.appendChild(el('div', 'row-label', 'Lane: protocol & payment transport'));
    const lane = d.lane || {};
    const laneLine = el('div', 'inline');
    const proto = el('select', 'inp usepod-protocol'); proto.setAttribute('aria-label', 'UsePod wire protocol');
    [['openai', 'OpenAI-compatible (chat completions)'], ['anthropic', 'Anthropic-compatible (messages)']].forEach(([v, t]) => { const o = el('option', null, t); o.value = v; if (lane.protocol === v) o.selected = true; proto.appendChild(o); });
    const transport = el('select', 'inp usepod-transport'); transport.setAttribute('aria-label', 'UsePod payment transport');
    [['prepaid_token', 'Prepaid (token in the proxy URL)'], ['x402', 'Accountless x402 (pay per call)']].forEach(([v, t]) => { const o = el('option', null, t); o.value = v; if (lane.transport_mode === v) o.selected = true; transport.appendChild(o); });
    const laneBtn = el('button', 'btn usepod-lane-save', 'Save lane'); laneBtn.type = 'button';
    const lst = el('span', 'subtle usepod-lane-state', '');
    laneBtn.addEventListener('click', async () => {
      laneBtn.disabled = true; lst.textContent = 'Saving…';
      const { ok: ok2, body: j } = await usepodPost('/api/cloud/usepod/lane', { protocol: proto.value, transport_mode: transport.value });
      if (!ok2) { lst.textContent = 'Not saved — ' + (j.error || j.code || 'refused') + '.'; laneBtn.disabled = false; return; }
      say('saved', 'Lane saved; ' + ((j.refreshed_lanes || []).length) + ' registered lane(s) refreshed.');
      await draw();
    });
    laneLine.appendChild(proto); laneLine.appendChild(transport); laneLine.appendChild(laneBtn); laneLine.appendChild(lst);
    body.appendChild(laneLine);
    const laneWarn = el('div', 'subtle usepod-lane-warn', '');
    const syncLaneWarn = () => {
      const hasGrant = (money.grants || []).length > 0;
      laneWarn.textContent = transport.value === 'x402' && !pay.installed
        ? 'x402 selected, but no wallet payment authority is integrated: x402 turns refuse at the pick — before any reservation, quote or payment — until one is (Settings → Wallet).'
        : transport.value === 'x402' && !(pay.verified_networks || []).length
          ? 'x402 selected, but the wallet pays on no network now: x402 turns refuse at the pick (wallet_payment_network_unverified) — before any reservation, quote or payment — until Crypto is on with a ready Crypto Pilot wallet on Solana Mainnet (Settings → Wallet).'
        : transport.value === 'prepaid_token' && money.installed && !hasGrant && !String(money.label || '').startsWith('test_double:')
          ? 'Prepaid selected on the money law, but no spend grant exists yet: paid turns refuse with MONEY_AUTHORITY_INVALID until an operator grants one.'
          : 'Protocol and transport do not change prices; an approved route bound survives a lane change.';
    };
    transport.addEventListener('change', syncLaneWarn);
    syncLaneWarn();
    body.appendChild(laneWarn);

    /* -- routing policy: what dispatch will ask for, and the ceilings that bind it. */
    body.appendChild(el('div', 'row-label', 'Routing policy'));
    const policy = d.route_policy || {};
    const pline1 = el('div', 'inline');
    const mode = el('select', 'inp usepod-policy-mode'); mode.setAttribute('aria-label', 'UsePod routing mode');
    [['marketplace-only', 'Marketplace only'], ['centralized-only', 'Centralized only'], ['auto', 'Auto (marketplace, then centralized)']].forEach(([v, t]) => { const o = el('option', null, t); o.value = v; if (policy.mode === v) o.selected = true; mode.appendChild(o); });
    const fallback = el('input', 'inp usepod-policy-fallback'); fallback.type = 'checkbox'; fallback.id = 'usepodFallback'; fallback.checked = !!policy.allow_centralized_fallback;
    const fbLabel = el('label', null, 'allow centralized fallback'); fbLabel.htmlFor = 'usepodFallback';
    pline1.appendChild(mode); pline1.appendChild(fallback); pline1.appendChild(fbLabel);
    body.appendChild(pline1);
    const pline2 = el('div', 'inline');
    const pins = el('input', 'inp usepod-policy-pins'); pins.type = 'text'; pins.placeholder = 'pinned providers (centralized), comma-separated'; pins.value = (policy.pinned_providers || []).join(','); pins.setAttribute('aria-label', 'Pinned centralized providers');
    pline2.appendChild(pins); body.appendChild(pline2);
    const pline3 = el('div', 'inline');
    const cin = el('input', 'inp usepod-policy-ceiling-in'); cin.type = 'number'; cin.min = '1'; cin.placeholder = 'no explicit ceiling'; cin.value = policy.max_input_microunits_per_million ?? ''; cin.setAttribute('aria-label', 'Input price ceiling in µUSDC per million tokens');
    const cout = el('input', 'inp usepod-policy-ceiling-out'); cout.type = 'number'; cout.min = '1'; cout.placeholder = 'no explicit ceiling'; cout.value = policy.max_output_microunits_per_million ?? ''; cout.setAttribute('aria-label', 'Output price ceiling in µUSDC per million tokens');
    pline3.appendChild(el('span', 'subtle', 'Ceilings, µUSDC per Mtok — input')); pline3.appendChild(cin); pline3.appendChild(el('span', 'subtle', 'output')); pline3.appendChild(cout);
    body.appendChild(pline3);
    const polBtn = el('button', 'btn usepod-policy-save', 'Save policy'); polBtn.type = 'button';
    const pst = el('span', 'subtle usepod-policy-state', '');
    polBtn.addEventListener('click', async () => {
      polBtn.disabled = true; pst.textContent = 'Saving…';
      const payload = { mode: mode.value, allow_centralized_fallback: !!fallback.checked,
        pinned_providers: pins.value.split(',').map(s => s.trim()).filter(Boolean) };
      if (cin.value.trim() !== '') payload.max_input_microunits_per_million = parseInt(cin.value.trim(), 10);
      if (cout.value.trim() !== '') payload.max_output_microunits_per_million = parseInt(cout.value.trim(), 10);
      const { ok: ok2, body: j } = await usepodPost('/api/cloud/usepod/route-policy', payload);
      if (!ok2) { pst.textContent = 'Not saved — ' + (j.error || j.code || 'refused') + '.'; polBtn.disabled = false; return; }
      say('saved', 'Policy saved. Existing route approvals bound to the previous policy will refuse (route_policy_changed_since_approval) until re-approved.');
      await draw();
    });
    const pline4 = el('div', 'inline'); pline4.appendChild(polBtn); pline4.appendChild(pst); body.appendChild(pline4);
    if (d.route_store_error) body.appendChild(el('div', 'state failed', 'Route store error — ' + d.route_store_error));
    body.appendChild(el('div', 'subtle', 'A pin names centralized providers only; marketplace-only with a pin is refused upstream. Ceilings are maxima per million tokens in integer µUSDC; dispatch re-checks the live price against them before every send.'));

    /* -- models: prices with source and age, and the approval that gates paid dispatch. */
    body.appendChild(el('div', 'row-label', 'Models'));
    const search = el('input', 'inp usepod-model-search'); search.type = 'search'; search.placeholder = 'filter by model id'; search.setAttribute('aria-label', 'Filter UsePod models');
    const modelList = el('div', 'stack usepod-models');
    const renderModels = () => {
      modelList.textContent = '';
      const q = search.value.trim().toLowerCase();
      const rows = (d.models || []).filter(m => !q || modelSearchKey(m.model_id).includes(modelSearchKey(q)));
      if (!(d.models || []).length) {
        modelList.appendChild(el('div', 'empty', hasSnapshot ? 'No usable rows in the cached feed.' : 'No prices cached. Use Refresh to fetch the public feed first.'));
        return;
      }
      if (!rows.length) { modelList.appendChild(el('div', 'empty', 'No model id matches “' + search.value.trim() + '”.')); return; }
      rows.forEach(m => modelList.appendChild(usepodModelRow(m, d, say, draw)));
    };
    search.addEventListener('input', renderModels);
    const searchLine = el('div', 'inline'); searchLine.appendChild(search);
    body.appendChild(searchLine); body.appendChild(modelList);
    renderModels();
    body.appendChild(el('div', 'subtle',
      'Approving a route is not choosing a model: the pin above (Model) picks what a turn asks for; an approval here is what you have agreed to PAY for that model. Dispatch needs both, and re-checks the live route and price against the approval before every send.'));
  };
  draw();
}
/* One model row: exact prices with their route source, spendability, and the approval state.
   The Approve confirm names exactly what will be bound — model, ceilings (and where each ceiling
   came from), route classes, origin — so the operator approves a fact, not a hope. */
function usepodModelRow(m, d, say, draw) {
  const rowEl = el('div', 'stack profile-item usepod-model');
  const head = el('div', 'inline');
  head.appendChild(el('span', 'profile-value mono', String(m.model_id || '?')));
  if (m.listed_for_this_credential === true) head.appendChild(el('span', 'pill on', 'listed for this token'));
  else if (m.listed_for_this_credential === false) head.appendChild(el('span', 'pill', 'not listed for this token'));
  else head.appendChild(el('span', 'pill', 'listing not observed'));
  if (m.unusable_reason) head.appendChild(el('span', 'pill', 'not usable: ' + m.unusable_reason));
  else head.appendChild(el('span', 'pill on', 'usable for spend'));
  rowEl.appendChild(head);
  const priceLine = (label, price) => {
    if (!price) return;
    rowEl.appendChild(el('div', 'subtle mono', label + ': ' + usepodRate(price)));
  };
  priceLine('marketplace', m.marketplace);
  priceLine('cheapest centralized', m.centralized_cheapest);
  priceLine('best available', m.best_available);
  if (m.request_price_microunits != null) rowEl.appendChild(el('div', 'subtle', 'per-request price ' + m.request_price_microunits + ' µUSDC — per-request billing cannot bound a token-billed call'));
  (m.problems || []).forEach(p => rowEl.appendChild(el('div', 'state failed', 'price row problem: ' + p)));

  const bound = m.approved_route;
  if (bound) {
    const kv = el('dl', 'kv');
    kv.appendChild(el('dt', null, 'Approved'));
    kv.appendChild(el('dd', null, usepodTime(bound.approved_at) + ' · ' + String(bound.approval_id || '?')));
    kv.appendChild(el('dt', null, 'Ceiling'));
    kv.appendChild(el('dd', null, 'in ≤ ' + bound.max_input_microunits_per_million + ' (' + bound.input_basis + '), out ≤ ' + bound.max_output_microunits_per_million + ' (' + bound.output_basis + ') µUSDC/Mtok'));
    kv.appendChild(el('dt', null, 'Routes')); kv.appendChild(el('dd', null, (bound.allowed_route_classes || []).join(', ') || '?'));
    kv.appendChild(el('dt', null, 'Priced from')); kv.appendChild(el('dd', null, 'snapshot ' + String(bound.snapshot_sha256 || '').slice(0, 12) + '… fetched ' + usepodTime(bound.snapshot_fetched_at) + (bound.origin ? ' · origin ' + bound.origin : '')));
    rowEl.appendChild(kv);
    const policy = d.route_policy || {};
    if (bound.policy && (String(bound.policy.mode) !== String(policy.mode) || !!bound.policy.allow_centralized_fallback !== !!policy.allow_centralized_fallback)) {
      rowEl.appendChild(el('div', 'state stale', 'The routing policy changed after this approval — dispatch will refuse (route_policy_changed_since_approval) until you re-approve.'));
    }
    const line = el('div', 'inline');
    const forget = el('button', 'btn danger usepod-route-forget', 'Withdraw approval'); forget.type = 'button';
    const fst = el('span', 'subtle', '');
    forget.addEventListener('click', async () => {
      forget.disabled = true; fst.textContent = 'Withdrawing…';
      const { ok, body: j } = await usepodPost('/api/cloud/usepod/forget-route', { model_id: m.model_id });
      if (!ok) { fst.textContent = 'Not withdrawn — ' + (j.error || 'refused') + '.'; forget.disabled = false; return; }
      say('saved', 'Route approval for ' + m.model_id + ' withdrawn. Paid turns on it refuse immediately.');
      await draw();
    });
    line.appendChild(forget); line.appendChild(fst); rowEl.appendChild(line);
  } else if (!m.unusable_reason) {
    const line = el('div', 'inline');
    const approve = el('button', 'btn primary usepod-route-approve', 'Approve route'); approve.type = 'button';
    const ast = el('span', 'subtle', '');
    approve.addEventListener('click', async () => {
      const policy = d.route_policy || {};
      const what = 'Approve paid dispatch for ' + m.model_id + '?\n\n' +
        'Route: ' + String(policy.mode || '?') + (policy.allow_centralized_fallback ? ' (centralized fallback allowed)' : '') + '\n' +
        'Price ceilings: the cheaper of the policy ceilings and the prices below, in integer µUSDC per million tokens.\n' +
        (m.marketplace ? 'Marketplace ' + usepodRate(m.marketplace) + '\n' : '') +
        (m.centralized_cheapest ? 'Centralized ' + usepodRate(m.centralized_cheapest) + '\n' : '') +
        'Origin: ' + String((d.credential || {}).origin || USEPOD_ORIGIN_DEFAULT) + '\n\n' +
        'Spend caps still apply on top. You can withdraw this at any time and paid turns refuse immediately.';
      if (!window.confirm(what)) { ast.textContent = 'Not approved.'; return; }
      approve.disabled = true; ast.textContent = 'Approving at current prices…';
      const { ok, status, body: j } = await usepodPost('/api/cloud/usepod/approve-route', { model_id: m.model_id });
      if (!ok) {
        ast.textContent = 'Not approved — ' + (status === 409 ? 'no route can be approved at current prices (' + (j.code || '?') + ')' : (j.error || 'refused')) + '.';
        approve.disabled = false; return;
      }
      const b = j.approved_route || {};
      say('saved', 'Route approved for ' + m.model_id + ': in ≤ ' + (b.max_input_microunits_per_million ?? '?') + ' / out ≤ ' + (b.max_output_microunits_per_million ?? '?') + ' µUSDC/Mtok.');
      await draw();
    });
    line.appendChild(approve); line.appendChild(ast); rowEl.appendChild(line);
  }
  return rowEl;
}

/* --- Answer language. The value is one Operator Profile item in the `language` category, written
   through POST /api/profile/remember and cleared through /api/profile/forget -- the same authority
   the Memory list uses, not a second store. The options come from the policy's OWN table, so the
   picker cannot offer something the policy would then fail to read. */
function widgetAnswerLanguage(stack) {
  const src = state.sources['/api/profile'];
  const err = state.sourceErr['/api/profile'];
  if (err) { stack.appendChild(el('div', 'empty', 'Unavailable — ' + err)); return; }
  if (!src) { stack.appendChild(el('div', 'empty', 'Loading…')); return; }

  const items = (src.items || []).filter(i => i.category === 'language' && i.status !== 'deleted');
  /* resolve()'s precedence is chat > project > context > global. Settings has no chat, so it
     writes the GLOBAL one; a narrower item still wins where it applies, and saying so is the
     difference between a control and a lie. */
  const global_ = items.find(i => i.scope === 'global');
  const narrower = items.filter(i => i.scope !== 'global');
  const known = new Set(LANGUAGES.map(l => l.label.toLowerCase()).concat(LANGUAGES.map(l => l.code)));
  const readable = (v) => known.has(String(v || '').trim().toLowerCase());

  const sel = el('select', 'inp');
  sel.setAttribute('aria-label', 'Answer language');
  const none = el('option', null, 'No preference — VOOL follows your message');
  none.value = '';
  sel.appendChild(none);
  LANGUAGES.forEach(l => { const o = el('option', null, l.label); o.value = l.label; sel.appendChild(o); });
  if (global_ && readable(global_.value)) sel.value = String(global_.value);

  const st = el('div', 'state idle');
  const row = el('div', 'inline');
  row.appendChild(sel);
  const clear = el('button', 'btn', 'Clear');
  clear.type = 'button';
  clear.disabled = !global_;
  row.appendChild(clear);
  stack.appendChild(row);
  stack.appendChild(st);

  const fail = (msg) => { st.className = 'state failed'; st.textContent = msg; };
  const refresh = async () => { await readSource('/api/profile'); renderPane(); };

  sel.addEventListener('change', async () => {
    const value = sel.value;
    if (!value) { clear.click(); return; }
    st.className = 'state saving'; st.textContent = 'Saving…';
    try {
      const r = await fetch('/api/profile/remember', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: 'language', value: value, scope: 'global', replace: true }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j || j.ok === false) throw new Error((j && (j.error || (j.change && j.change.report))) || ('HTTP ' + r.status));
      await readSource('/api/profile');
      const back = (state.sources['/api/profile'].items || []).find(i => i.category === 'language' && i.scope === 'global');
      if (!back || String(back.value) !== value) {
        st.className = 'state stale';
        st.textContent = 'Sent, but VOOL reports ' + JSON.stringify(back ? back.value : null) + '.';
        renderPane();
        return;
      }
      st.className = 'state saved'; st.textContent = 'Saved';
      renderPane();
    } catch (e) { fail('Not saved — ' + e.message); }
  });

  clear.addEventListener('click', async () => {
    if (!global_) return;
    st.className = 'state saving'; st.textContent = 'Clearing…';
    try {
      const r = await fetch('/api/profile/forget', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_id: global_.item_id, expected_revision: global_.revision }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j || j.ok === false) throw new Error((j && (j.error || (j.change && j.change.report))) || ('HTTP ' + r.status));
      await refresh();
    } catch (e) { fail('Not cleared — ' + e.message); }
  });

  /* What is actually in force, stated rather than implied. */
  if (global_ && !readable(global_.value)) {
    stack.appendChild(el('div', 'fail-note',
      'VOOL has ' + JSON.stringify(global_.value) + ' remembered as your language, but the policy ' +
      'reads only one language at a time — it cannot use this, and is ignoring it. Pick one above ' +
      'to replace it, or clear it.'));
  }
  if (narrower.length) {
    stack.appendChild(el('div', 'subtle',
      'A narrower preference exists (' + narrower.map(i => i.scope + ': ' + i.value).join(', ') +
      ') and wins where it applies — chat beats project beats work/personal beats this one. ' +
      'Settings has no chat of its own, so it sets the global preference.'));
  }
  stack.appendChild(el('div', 'subtle',
    'Stored as one remembered item, listed under Memory & Personalisation with everything else ' +
    'VOOL keeps about you. Precedence for any given answer: what your message asks for, then this, ' +
    'then the language you wrote in when that is unambiguous.'));
}

/* --- Email accounts: every configured mailbox as the account authority sees it (core/email_accounts.py,
   GET /api/email/accounts): its identity, what it can do, its state, and which one is the default. The
   default is ONE Operator Profile item (default_account.email, an opaque credential NAME), written through
   the same POST /api/profile/remember the profile widget uses and cleared through POST /api/profile/forget;
   the listing is re-read after every action, so the page shows only what the authority reports. */
const EMAIL_STATE_TEXT = {
  configured: 'ready',
  needs_reconnect: 'needs reconnect: its grant is no longer stored',
  send_only: 'send only: it has no way to read',
  read_only: 'read only: it has no way to send',
  mismatch: 'not one mailbox: its send and read entries name different mailboxes',
  unreadable: 'could not be read as an account'
};
function widgetEmailAccounts(stack) {
  const src = state.sources['/api/email/accounts'];
  const err = state.sourceErr['/api/email/accounts'];
  const body = el('div', 'stack'); body.id = 'emailAccounts';
  const status = el('div', 'state idle'); status.id = 'emailAccountsStatus'; status.setAttribute('role', 'status');
  stack.appendChild(body);
  stack.appendChild(status);
  if (err) { body.appendChild(el('div', 'empty', 'Unavailable — ' + err)); return; }
  if (!src) { body.appendChild(el('div', 'empty', 'Loading…')); return; }
  const say = (msg, bad) => { status.className = 'state ' + (bad ? 'failed' : 'saved'); status.textContent = msg; };
  const refresh = async () => { await readSource('/api/email/accounts'); renderPane(); };
  const post = async (action, payload) => {
    let r, j;
    try {
      r = await fetch('/api/profile/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      j = await r.json().catch(() => null);
    } catch (e) { return { ok: false, data: { error: 'VOOL did not answer' } }; }
    return { ok: r.ok && !!(j && j.ok !== false), data: j || {} };
  };
  const why = (res, fallback) => (res.data && res.data.change && res.data.change.report) || (res.data && (res.data.error || res.data.detail)) || fallback;
  const accounts = src.accounts || [];
  const def = src.default || {};
  const sel = src.selection || {};
  if (!accounts.length) {
    body.appendChild(el('div', 'empty', 'No email account is configured. Add one through email account setup; nothing here asks for a password.'));
  }
  accounts.forEach(a => {
    const rowEl = el('div', 'inline email-account'); rowEl.dataset.emailAccount = a.account;
    const pill = el('span', 'pill' + (a.state === 'configured' ? ' on' : ''));
    pill.appendChild(el('span', 'dot'));
    pill.appendChild(el('span', 'email-account-name', a.account + (a.address ? ' · ' + a.address : '')));
    rowEl.appendChild(pill);
    rowEl.appendChild(el('span', 'subtle mono', a.provider));
    rowEl.appendChild(el('span', 'subtle', (a.read ? 'reads' : 'cannot read') + ' · ' + (a.send ? 'sends' : 'cannot send')));
    const st = el('span', 'subtle email-account-state', EMAIL_STATE_TEXT[a.state] || a.state); st.dataset.state = a.state;
    rowEl.appendChild(st);
    if (a.is_default) {
      rowEl.appendChild(el('span', 'pill email-default-badge', 'default' + (def.source && def.source !== 'global' ? ' · ' + def.source : '')));
    } else if (a.state !== 'unreadable') {
      /* One profile item, one authority: the value is the credential NAME the account authority names. */
      const use = el('button', 'btn email-use-default', 'Use as default'); use.type = 'button'; use.dataset.account = a.account;
      use.addEventListener('click', async () => {
        use.disabled = true; say('Choosing ' + a.account + ' as the default…');
        const res = await post('remember', { category: 'default_account.email', value: a.binding_ref, scope: 'global', replace: true });
        if (!res.ok) { use.disabled = false; say('Not chosen — ' + why(res, 'the profile refused it'), true); return; }
        await refresh();
        const back = ((state.sources['/api/email/accounts'] || {}).default || {}).account;
        if (back === a.account) say('Default email account: ' + a.account + '.');
        else say('VOOL stored ' + JSON.stringify(back) + ' as the default instead.', true);
      });
      rowEl.appendChild(use);
    }
    body.appendChild(rowEl);
  });
  const chosen = el('div', 'subtle email-default-summary'); chosen.id = 'emailDefaultSummary';
  if (def.account) {
    chosen.textContent = 'Default: ' + def.account + ' (' + (def.source || 'global') + ' scope)' + (def.configured ? '.' : ' — that account is not configured any more.');
    if (def.item_id) {
      const clear = el('button', 'btn danger email-clear-default', 'Clear default'); clear.type = 'button';
      clear.addEventListener('click', async () => {
        clear.disabled = true; say('Clearing the default…');
        const res = await post('forget', { item_id: def.item_id });
        if (!res.ok) { clear.disabled = false; say('Not cleared — ' + why(res, 'the profile refused it'), true); return; }
        await refresh(); say('Default cleared. With several accounts VOOL asks which one instead of guessing.');
      });
      chosen.appendChild(document.createTextNode(' ')); chosen.appendChild(clear);
    }
  } else if (src.legacy_default_configured) {
    chosen.textContent = 'No default chosen: the account named "default" is used.';
  } else if (accounts.length > 1) {
    chosen.textContent = 'No default chosen. With several accounts, VOOL asks which one instead of guessing.';
  } else if (accounts.length === 1) {
    chosen.textContent = 'No default chosen: the only configured account is used.';
  }
  body.appendChild(chosen);
  [['imap', 'Reading uses'], ['smtp', 'Sending uses']].forEach(([kind, label]) => {
    const s = sel[kind] || {};
    const line = el('div', 'subtle email-selection'); line.dataset.kind = kind;
    line.textContent = label + ': ' + (s.ok ? (s.account + (s.address ? ' (' + s.address + ')' : '') + ' — ' + (s.source || 'selected')) : ('refused — ' + (s.message || s.status)));
    body.appendChild(line);
  });
}

/* --- Sending hold: the draft store's recovery state (core/email_drafts.py, GET /api/email/recovery).
   Everything shown is what the store reports: the preserved evidence, the identifiers the damaged bytes
   named, each check recorded, and the exact effect an acknowledgement would have. The two actions are
   the operator's alone -- a check (POST /api/email/recovery/check) records a sent-view lookup and never
   releases anything; the acknowledgement (POST /api/email/recovery/acknowledge) names the exact recovery
   generation this page read, so a stale page cannot release a newer hold -- and the state is re-read
   after each, so "released" is only ever said about what the store persisted. */
function widgetEmailRecovery(stack) {
  const src = state.sources['/api/email/recovery'];
  const err = state.sourceErr['/api/email/recovery'];
  const body = el('div', 'stack'); body.id = 'emailRecovery';
  const status = el('div', 'state idle'); status.id = 'emailRecoveryStatus'; status.setAttribute('role', 'status');
  stack.appendChild(body);
  stack.appendChild(status);
  if (err) { body.appendChild(el('div', 'empty', 'Unavailable — ' + err)); return; }
  if (!src) { body.appendChild(el('div', 'empty', 'Loading…')); return; }
  const say = (msg, bad) => { status.className = 'state ' + (bad ? 'failed' : 'saved'); status.textContent = msg; };
  const refresh = async () => { await readSource('/api/email/recovery'); renderPane(); };
  const post = async (path, payload) => {
    let r, j;
    try {
      r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      j = await r.json().catch(() => null);
    } catch (e) { return { ok: false, http: 0, data: { status: 'no_answer', message: 'VOOL did not answer' } }; }
    return { ok: r.ok && !!(j && j.ok !== false), http: r.status, data: j || {} };
  };
  const rec = src.recovery || null;
  const summary = el('div', 'email-recovery-summary'); summary.id = 'emailRecoverySummary'; summary.setAttribute('role', 'status');
  summary.textContent = src.summary || '';
  body.appendChild(summary);
  if (!rec) return;
  const evidence = el('div', 'subtle mono email-recovery-evidence');
  evidence.textContent = 'Preserved: ' + (rec.quarantine || '?') + ' · ' + (rec.bytes || 0) + ' bytes · sha256 ' + String(rec.sha256 || '').slice(0, 16) + '…'
    + (rec.unresolved_markers && rec.unresolved_markers.length ? ' · markers: ' + rec.unresolved_markers.join(', ') : '');
  body.appendChild(evidence);
  if (!src.send_hold) {
    if (rec.acknowledged_at) body.appendChild(el('div', 'subtle', 'Acknowledged ' + new Date(rec.acknowledged_at * 1000).toLocaleString() + (rec.acknowledged_via ? ' via ' + rec.acknowledged_via : '') + '. The evidence above is kept.'));
    return;
  }
  const ids = rec.message_ids_in_damaged_store || [];
  const checks = rec.checks || {};
  const accounts = rec.accounts_in_damaged_store || [];
  body.appendChild(el('div', 'row-label', ids.length ? 'Unresolved sends named in the damaged bytes' : 'No Message-ID could be recovered from the damaged bytes'));
  ids.forEach(mid => {
    const rowEl = el('div', 'inline email-recovery-message'); rowEl.dataset.messageId = mid;
    rowEl.appendChild(el('span', 'mono', mid));
    const outcome = el('span', 'subtle email-check-outcome');
    const known = checks[mid];
    outcome.textContent = known ? (known.status + (known.account ? ' in ' + known.account : '') + ' — ' + known.message) : (accounts.length ? 'not checked yet (account ' + accounts.join(', ') + ')' : 'no account named for it; check it with the provider directly');
    if (accounts.length) {
      const check = el('button', 'btn email-check-sent', 'Check sent view'); check.type = 'button';
      check.addEventListener('click', async () => {
        check.disabled = true; outcome.textContent = 'Asking the account\'s sent view…';
        const res = await post('/api/email/recovery/check', { message_id: mid });
        const c = (res.data && res.data.check) || {};
        outcome.textContent = c.status ? (c.status + (c.account ? ' in ' + c.account : '') + ' — ' + c.message) : ('Check failed — ' + (res.data.message || res.data.error || ('HTTP ' + res.http)));
        check.disabled = false;
        if (c.status) await refresh();
      });
      rowEl.appendChild(check);
    }
    rowEl.appendChild(outcome);
    body.appendChild(rowEl);
  });
  const effect = el('div', 'email-acknowledge-effect'); effect.id = 'emailAcknowledgeEffect';
  effect.textContent = src.acknowledge_effect || '';
  body.appendChild(effect);
  const confirmRow = el('label', 'inline');
  const tick = document.createElement('input'); tick.type = 'checkbox'; tick.id = 'emailAcknowledgeConfirm';
  confirmRow.appendChild(tick);
  confirmRow.appendChild(el('span', null, 'I have checked the sends named above (or accept that they stay unresolved). Release NEW approved sends.'));
  body.appendChild(confirmRow);
  const ack = el('button', 'btn danger email-acknowledge', 'Acknowledge and release sending'); ack.type = 'button'; ack.id = 'emailAcknowledgeBtn';
  ack.disabled = true;
  tick.addEventListener('change', () => { ack.disabled = !tick.checked; });
  ack.addEventListener('click', async () => {
    ack.disabled = true; say('Acknowledging ' + rec.quarantine + '…');
    const res = await post('/api/email/recovery/acknowledge', { quarantine: rec.quarantine, generation: src.generation, confirm: true });
    const d = res.data || {};
    if (d.status === 'acknowledged' || d.status === 'no_hold') {
      await refresh();
      const now = state.sources['/api/email/recovery'] || {};
      if (now.send_hold === false) say('Sending released. The evidence ' + rec.quarantine + ' is kept; nothing was resent.');
      else say('The store still reports a hold; nothing was released.', true);
      return;
    }
    if (d.status === 'generation_mismatch' || d.status === 'quarantine_mismatch') {
      say('This page is out of date: the store was recovered again since it loaded. Reloading the current hold.', true);
      await readSource('/api/email/recovery', true); renderPane(); return;
    }
    say('Not released — ' + (d.message || d.error || ('HTTP ' + res.http)), true);
    ack.disabled = !tick.checked;
  });
  body.appendChild(ack);
}

/* --- Operator profile: what VOOL remembers, with every control the authority offers on a STORED
   item -- Edit, Forget, Move scope, Restore previous -- plus Pause/Resume and Export. All of it is
   the ONE Operator Profile authority (core/operator_profile.py) behind POST /api/profile/*: each
   action is one revision on one item, CAS-guarded by expected_revision, and the listing is re-read
   after every action so the page only ever shows what the store reports. A suggestion VOOL made
   from a chat (a candidate) is decided in that chat, where the conversation that produced it is. */
function widgetProfile(stack) {
  const body = el('div', 'stack');
  const status = el('div', 'state idle'); status.id = 'profileStatus'; status.setAttribute('role', 'status');
  stack.appendChild(body);
  stack.appendChild(status);
  const say = (msg, bad) => { status.className = 'state ' + (bad ? 'failed' : 'saved'); status.textContent = msg; };
  const post = async (action, payload) => {
    let r, j;
    try {
      r = await fetch('/api/profile/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      j = await r.json().catch(() => null);
    } catch (e) { return { ok: false, status: 0, data: { error: 'VOOL did not answer' } }; }
    return { ok: r.ok && !!(j && j.ok !== false), status: r.status, data: j || {} };
  };
  const report = (res, fallback) => (res.data && res.data.change && res.data.change.report) || (res.data && (res.data.error || res.data.detail)) || fallback;
  let current = null;

  const draw = async () => {
    body.textContent = '';
    body.appendChild(el('div', 'empty', 'Reading what VOOL remembers…'));
    let p;
    try { p = await getJSON('/api/profile'); }
    catch (e) { body.textContent = ''; body.appendChild(el('div', 'empty', 'Unavailable — ' + e.message)); return; }
    current = p;
    body.textContent = '';
    /* Store-wide: Pause/Resume (POST /api/profile/pause) and Export (GET /api/profile/export). */
    const bar = el('div', 'inline');
    const pause = el('button', 'btn', p.paused ? 'Resume memory' : 'Pause memory'); pause.type = 'button'; pause.id = 'profilePauseBtn';
    pause.setAttribute('aria-pressed', p.paused ? 'true' : 'false');
    pause.addEventListener('click', async () => {
      pause.disabled = true;
      const res = await post('pause', { paused: !p.paused });
      if (!res.ok) { pause.disabled = false; say('Not changed — ' + report(res, 'HTTP ' + res.status), true); return; }
      await draw();
      say(res.data.paused ? 'Memory paused: VOOL keeps what it knows and learns nothing new until you resume.' : 'Memory resumed: VOOL learns from conversation again.');
    });
    const exp = el('button', 'btn', 'Export profile'); exp.type = 'button'; exp.id = 'profileExportBtn';
    const out = el('pre', 'mono profile-export'); out.id = 'profileExportOut'; out.hidden = true;
    exp.addEventListener('click', async () => {
      exp.disabled = true;
      try {
        const doc = await getJSON('/api/profile/export');
        const text = JSON.stringify(doc, null, 2);
        out.textContent = text; out.hidden = false;
        let copied = false;
        try { await navigator.clipboard.writeText(text); copied = true; } catch (e) { copied = false; }
        say(copied ? 'Export copied to the clipboard and shown below. Values only — never a credential.' : 'Export shown below; select it to copy (the clipboard was not available here).');
      } catch (e) { say('Could not export — ' + e.message, true); }
      exp.disabled = false;
    });
    bar.appendChild(pause); bar.appendChild(exp);
    body.appendChild(bar);
    if (p.paused) body.appendChild(el('div', 'subtle', 'Memory is paused: VOOL is not learning anything new right now. What it already remembers stays in force and stays editable below.'));
    body.appendChild(out);
    const items = (p.items || []).filter(it => it.status !== 'deleted');
    if (!items.length) { body.appendChild(el('div', 'empty', 'VOOL remembers nothing about you yet.')); return; }
    /* Scopes an item can be moved to from here. A chat- or project-scoped move needs the chat or
       project it belongs to, which Settings does not have -- those two are done where they apply. */
    const movable = (p.scopes || ['global', 'work', 'personal']).filter(s => s !== 'chat' && s !== 'project');
    items.forEach(it => body.appendChild(profileItemRow(it, movable)));
    body.appendChild(el('div', 'subtle', items.length + ' remembered item' + (items.length === 1 ? '' : 's') + '. Each action is one revision on one item, recorded by the profile authority; Restore previous undoes the latest one. A suggestion VOOL made from a chat is decided in that chat.'));
  };

  function profileItemRow(it, movable) {
    const rowEl = el('div', 'stack profile-item'); rowEl.dataset.profileItem = it.item_id;
    const head = el('div', 'inline');
    const shown = (it.value_text != null && it.value_text !== '') ? it.value_text : (it.value == null ? '' : it.value);
    head.appendChild(el('span', 'profile-value', (it.label || it.category || 'item') + ': ' + String(shown)));
    head.appendChild(el('span', 'pill profile-scope-pill', (it.scope || 'global') + (it.scope_key ? ' · ' + it.scope_key : '')));
    rowEl.appendChild(head);
    rowEl.appendChild(el('div', 'subtle', 'origin: ' + (it.origin || '—') + ' · last used: ' + (it.last_used_at ? String(it.last_used_at).slice(0, 16).replace('T', ' ') : 'never') + ' · revision ' + String(it.revision || 1)));
    const run = async (label, action, payload, okText) => {
      const res = await post(action, payload);
      if (res.status === 409) { say(label + ': this item changed elsewhere — the list was reloaded; try again.', true); await draw(); return; }
      say(report(res, res.ok ? okText : ('Could not ' + label.toLowerCase() + '.')), !res.ok);
      if (res.ok) await draw();
    };
    const acts = el('div', 'inline');
    /* Edit: an inline field bound to POST /api/profile/item, CAS-guarded by the revision shown. */
    const edit = el('button', 'btn profile-edit', 'Edit'); edit.type = 'button';
    const editor = el('div', 'inline'); editor.hidden = true;
    const input = document.createElement('input'); input.className = 'inp profile-edit-input'; input.type = 'text';
    input.value = String(it.value_text || ''); input.setAttribute('aria-label', 'New value for ' + (it.label || it.category));
    const saveBtn = el('button', 'btn primary profile-edit-save', 'Save'); saveBtn.type = 'button';
    const cancel = el('button', 'btn', 'Cancel'); cancel.type = 'button';
    edit.addEventListener('click', () => { editor.hidden = !editor.hidden; if (!editor.hidden) input.focus(); });
    cancel.addEventListener('click', () => { editor.hidden = true; input.value = String(it.value_text || ''); });
    saveBtn.addEventListener('click', () => run('Edit', 'item', { item_id: it.item_id, value: input.value, expected_revision: it.revision }, 'Saved.'));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); saveBtn.click(); } });
    editor.appendChild(input); editor.appendChild(saveBtn); editor.appendChild(cancel);
    const forget = el('button', 'btn danger profile-forget', 'Forget'); forget.type = 'button';
    forget.addEventListener('click', () => run('Forget', 'forget', { item_id: it.item_id, expected_revision: it.revision }, 'Forgotten.'));
    const scopeSel = el('select', 'inp profile-scope-select'); scopeSel.setAttribute('aria-label', 'Scope for ' + (it.label || it.category));
    const options = movable.includes(it.scope) ? movable : [it.scope].concat(movable);
    options.forEach(s => { const o = el('option', null, s); o.value = s; if (s === it.scope) o.selected = true; scopeSel.appendChild(o); });
    const move = el('button', 'btn profile-scope-move', 'Move scope'); move.type = 'button';
    move.addEventListener('click', () => {
      if (scopeSel.value === it.scope) { say('Already ' + it.scope + '.'); return; }
      run('Move scope', 'scope', { item_id: it.item_id, scope: scopeSel.value, expected_revision: it.revision }, 'Moved.');
    });
    const restore = el('button', 'btn profile-restore', 'Restore previous'); restore.type = 'button';
    restore.addEventListener('click', () => run('Restore previous', 'restore', { item_id: it.item_id }, 'Restored.'));
    acts.appendChild(edit); acts.appendChild(forget); acts.appendChild(scopeSel); acts.appendChild(move); acts.appendChild(restore);
    rowEl.appendChild(acts);
    rowEl.appendChild(editor);
    return rowEl;
  }
  draw();
}

/* ------------------------------------------------------------------ concurrency
   Another Settings window, or a chat command, can change the same value. On regaining focus the
   open group is re-read: a row the user has not edited follows the runtime, and a row they HAVE
   edited keeps their text and is told the stored value moved. */
async function reconcile() {
  if (MODEL.some(g => g.id === 'setup')) {
    await readSource(SETUP_STATE_URL);
    const setup = state.sources[SETUP_STATE_URL];
    if (!setup || !setup.show_entry) { removeSetupGroup(); renderNav(); }
  }
  const before = {};
  ALL_ROWS.filter(r => r.group === state.group && r.read).forEach(r => { before[r.id] = valueOf(r); });
  await ensureGroupSources(state.group, true);
  ALL_ROWS.filter(r => r.group === state.group && r.read).forEach(r => {
    const st = rowState(r.id);
    const now = valueOf(r);
    if (same(before[r.id], now)) return;
    if (st.dirty !== undefined && !same(st.dirty, now)) setState(r.id, 'stale', 'Changed elsewhere to ' + JSON.stringify(now) + '.');
    else if (st.status !== 'saving') setState(r.id, 'idle', '');
  });
  renderPane();
}

/* ------------------------------------------------------------------ search + keys */
function onSearch(value) {
  state.query = String(value || '').trim().toLowerCase();
  const first = MODEL.find(g => hitsFor(g.id, state.query) > 0);
  if (state.query && first) {
    const firstRow = ALL_ROWS.find(r => r.group === first.id && matches(r, state.query));
    go(first.id, firstRow ? firstRow.id : undefined);
    live(ALL_ROWS.filter(r => matches(r, state.query)).length + ' settings match');
  } else {
    renderNav();
    renderPane();
  }
}

function installKeys() {
  const search = $('#search');
  search.addEventListener('input', () => onSearch(search.value));
  search.addEventListener('keydown', e => {
    if (e.key === 'Escape') { search.value = ''; onSearch(''); }
    if (e.key === 'Enter') { e.preventDefault(); $('#pane').focus(); }
  });
  document.addEventListener('keydown', e => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '');
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); search.focus(); search.select(); return; }
    if (e.key === '/' && !typing) { e.preventDefault(); search.focus(); return; }
    if (e.key === 'Escape' && !typing) { closeSettingsWindow(); return; }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && document.activeElement && document.activeElement.classList.contains('nav-item')) {
      e.preventDefault();
      const items = [...document.querySelectorAll('.nav-item')];
      const i = items.indexOf(document.activeElement);
      const next = items[e.key === 'ArrowDown' ? Math.min(i + 1, items.length - 1) : Math.max(i - 1, 0)];
      if (next && next.dataset.group) go(next.dataset.group);   /* renderNav re-focuses the new current item */
    }
  });
  window.addEventListener('focus', () => { reconcile(); });
}

/* Closing Settings closes THIS window only. The chat window, its draft and the daemon are
   untouched — Settings is a second window on the same runtime, never a second runtime. */
function closeSettingsWindow() {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.close_settings) {
      window.pywebview.api.close_settings();
      return;
    }
  } catch (e) { /* fall through to the browser paths */ }
  if (window.top !== window.self) {
    // Framed inside the chat page (the popup-blocked browser fallback): the host hides the frame.
    // Navigating here would load the chat INSIDE the frame; closing cannot close a frame.
    try { window.parent.postMessage({ type: 'vool-settings-close' }, window.location.origin); } catch (e) {}
    return;
  }
  try { window.close(); } catch (e) { /* a tab the user opened themselves cannot be closed */ }
  if (!window.closed) window.location.href = '/chat';
}

/* ------------------------------------------------------------------ boot */
/* A deep link picks the section: /settings#memory lands on Memory. Callers that used to scroll
   an element inside the chat page's own panel use this instead, now that Settings is a separate
   window and cannot be scrolled from the chat document. */
function sectionFromHash() {
  const raw = String(location.hash || '').replace(/^#/, '').trim();
  const parts = raw.split('/');
  const group = MODEL.find(g => g.id === parts[0]);
  state.onlyRow = ''; state.focusPart = '';
  if (group && parts[1] && group.rows.some(r => r.id === parts[1])) {
    state.focusRow = parts[1]; state.onlyRow = parts[1]; state.focusPart = parts[2] || '';
  }
  return group ? group.id : '';
}

async function boot() {
  $('#back').addEventListener('click', closeSettingsWindow);
  installKeys();
  // Registered BEFORE the first read: an already-open window that is re-pointed at a section
  // changes its hash while boot is still awaiting, and a listener attached afterwards would miss
  // exactly that navigation.
  window.addEventListener('hashchange', () => { const g = sectionFromHash(); if (g) go(g); });
  // The "Complete setup" group exists only while setup is unfinished and not dismissed. Its state
  // is a plain local read (no provider, no model, no Keychain), decided before the first paint so
  // a finished install never flashes the entry.
  if (MODEL.some(g => g.id === 'setup')) {
    await readSource(SETUP_STATE_URL);
    const setup = state.sources[SETUP_STATE_URL];
    if (!setup || !setup.show_entry) removeSetupGroup();
  }
  const deep = sectionFromHash();
  if (deep) state.group = deep;
  renderNav();
  renderPane();
  await go(state.group);          /* reads ONLY the first group's sources */
  const late = sectionFromHash();  /* the hash may have arrived during that await */
  if (late && late !== state.group) await go(late);
}
boot();

/* Test surface: the same shape the chat page exposes, so a drive can act without pixel hunting. */
window.__voolSettings = {
  groups: () => MODEL.map(g => g.id),
  go: (g, r) => go(g, r),
  search: (q) => { $('#search').value = q; onSearch(q); },
  valueOf: (rowId) => { const r = ALL_ROWS.find(x => x.id === rowId); return r ? valueOf(r) : undefined; },
  stateOf: (rowId) => ({ ...rowState(rowId) }),
  commit: (rowId, v) => { const r = ALL_ROWS.find(x => x.id === rowId); return r ? commit(r, v) : null; },
  reconcile: () => reconcile(),
  sources: () => JSON.parse(JSON.stringify(state.sources)),
  readUrls: () => [...new Set(ALL_ROWS.filter(r => r.read).map(r => r.read.url))],
};
</script>
</body>
</html>
"""


def _ui_locale_head(ui_locale: str) -> tuple[str, str, str]:
    """(tag, dir, bootstrap script) for one resolved UI locale. Deterministic, catalog-backed.

    The tag/dir pair rides on <html>; the bootstrap is the page's ONE i18n authority
    (core/i18n/page_bundle): it publishes the resolved bundle, defines VOOLT with the
    English-then-key fallback, applies the data-i18n attributes already in this markup, and
    wires the locale picker the selector below carries. Nothing here invents a second
    localization system.
    """
    from core.i18n.catalog import catalog_for
    from core.i18n.locales import get_locale
    from core.i18n.page_bundle import render_i18n_bootstrap

    tag = catalog_for(ui_locale).locale
    spec = get_locale(tag)
    return tag, (spec.direction if spec else "ltr"), render_i18n_bootstrap(tag)


def _locale_coverage_note(tag: str) -> str:
    """The honest completeness line for the selected locale, computed from the real catalog.

    A locale with a shipped catalog serves its strings; anything the catalog does not carry
    falls back to English VISIBLY (the i18n engine's law). The line states exactly that, so
    the selector never implies a fully translated product it cannot deliver.
    """
    from core.i18n.catalog import catalog_for

    catalog = catalog_for(tag)
    total = len(catalog.keys)
    translated = sum(
        1
        for key in catalog.keys
        if catalog.text(key) != catalog_for("en").text(key)
    )
    if tag == "en":
        return f"{total} strings · English (the source language)"
    fallback = total - translated
    if fallback == 0:
        return f"Partial app translation · {translated}/{total} catalog entries translated. Some screens remain in English."
    return (
        f"Partial app translation · {translated}/{total} catalog entries translated; {fallback} fall back to English."
        " Some screens are not yet in the catalog and remain in English."
    )


# The always-discoverable app-language control, top-left of Settings (top of the side nav,
# above search). Globe symbol + each language's own name (the picker fills the options from
# the locale registry's endonyms — never flags, never English exonyms alone). This is the
# APP UI language: it changes these screens only. The model ANSWER language keeps its own
# existing Settings widget (General → Answer language) and dictation recognition keeps its
# own control next to the microphone; the note under the select says so in the selected
# language (settings.language_help, an existing catalog key).
_LOCALE_BLOCK = """<div id="uiLocaleWrap">
  <label class="loc-label" for="uiLocaleSelect"><span class="loc-globe" aria-hidden="true">&#127760;</span><span class="loc-word" data-i18n-html="settings.language_heading">Language <span class="set-sub">screens only &mdash; never the answer language</span></span></label>
  <select id="uiLocaleSelect" aria-label="App language (temporarily unavailable in this beta build)" title="App language switching is temporarily unavailable in this beta build — it returns in an update." data-i18n-aria-label="settings.language_select_aria" data-i18n-title="settings.language_select_title" disabled></select>
  <p class="loc-note" data-i18n-html="settings.language_help">Choose the language of these screens and controls. It never changes the language VOOL answers in &mdash; that has its own setting.</p>
  <p class="loc-note" id="uiLocaleCoverage"></p>
</div>
"""


def render_vool_settings_html(*, build_commit: str = "", ui_locale: str = "en") -> str:
    """The Settings page: one declarative model, rendered by one script, bound to the authorities
    that already own each value."""
    model = json.dumps(settings_groups(), separators=(",", ":"), ensure_ascii=False)
    catalog = json.dumps(language_catalog(), separators=(",", ":"), ensure_ascii=False)
    from core.calendar_settings_fragment import render_calendar_settings_fragment
    from core.notification_settings_fragment import render_notification_settings_fragment
    from core.settings_extras_fragment import render_settings_extras_fragment
    from core.wallet_fragment import render_wallet_fragment

    # the wallet section and the settings extras (learned facts, privacy disclosure, Toolbelt) live
    # here: each fragment renders itself into the host its group provides
    page = (_SETTINGS_HTML + _SETTINGS_JS + _SETTINGS_JS2).replace(
        "</body>", render_wallet_fragment() + "\n" + render_settings_extras_fragment() + "\n" + render_calendar_settings_fragment()
        + "\n" + render_notification_settings_fragment() + "\n</body>", 1
    )
    tag, direction, bootstrap = _ui_locale_head(ui_locale)
    return (
        page.replace("__SETTINGS_MODEL__", model)
        .replace("__LANGUAGE_CATALOG__", catalog)
        .replace("__PAGE_BUILD_COMMIT__", str(build_commit or "").strip())
        # The i18n bootstrap must run before the page's own script: it defines VOOLT.
        .replace(
            "<script>\nconst MODEL",
            bootstrap + "\n<script>\nconst MODEL",
            1,
        )
        .replace('<html lang="en">', f'<html lang="{tag}" dir="{direction}">')
        .replace('<div id="backRow">', _LOCALE_BLOCK + '<div id="backRow">', 1)
        .replace('<p class="loc-note" id="uiLocaleCoverage"></p>',
                 f'<p class="loc-note" id="uiLocaleCoverage">{_locale_coverage_note(tag)}</p>', 1)
    )
