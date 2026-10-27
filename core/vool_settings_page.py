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

from core.ui_icons import ICON_CSS
from core.ui_palette import APPEARANCE_JS, DARK_PALETTE_CSS, LIGHT_PALETTE_CSS, appearance_html_attrs

# --------------------------------------------------------------------------------------
# Effect vocabulary. Every bound row states when its change takes hold and how far it reaches,
# because "saved" alone does not tell a user whether the answer they are reading changes.
# --------------------------------------------------------------------------------------
EFFECT_NEXT_TURN = "Applies from your next message. An answer already being written keeps the old setting."
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
# The Web lookups and "Go online on its own" rows read the live boundary truth through the
# pact GET projection (view=boundary: the owning policy's values, no tour state) and write
# through the existing boundary command POST /api/onboarding/pact/boundary from their own
# widgets.
WEB_LOOKUPS_READ = "/api/onboarding/pact?view=boundary"
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
    if kw.get("effect") == EFFECT_NEXT_TURN:
        kw["effect_kind"] = "next_turn"   # language-independent, so a page can word a shared line once
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
    groups = _settings_groups()
    # Research controls belong only to explicitly opted-in research invocations.
    # The same filtered model drives navigation, search and all locale renders.
    if not _research_networking():
        groups = [group for group in groups if group["id"] != "network"]
    return _edition_filtered(groups)


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
                        "to call you. A step counts as done when the real setting exists, wherever "
                        "in Settings you set it."
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
                    help="The tone VOOL writes in. It changes wording, not what VOOL is willing to do.",
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
                        "How much VOOL may swear, if at all, when the tone calls for it. 0% means never."
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
                    keywords="profanity swearing swear coarse crude ceiling",
                ),
                _row(
                    id="character_mode",
                    label="Character",
                    help=(
                        "A character VOOL plays until you clear it. Leave it empty for VOOL as "
                        "itself. Saying \"act like a pirate\" in chat sets it, and only emptying "
                        "this box turns it off."
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
                        "Extra instructions for how VOOL writes, for example \"be concise\" or \"no "
                        "bullet lists\". Style requests you make in chat are kept in Memory & "
                        "Personalisation instead, where you can see and forget them."
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
                _row(
                    id="show_answer_details",
                    label="Show answer details",
                    help="Adds a small line under each answer saying which model or tool made it.",
                    kind="toggle",
                    read={"url": PREFS, "field": "show_answer_details"},
                    write={"url": PREFS, "field": "show_answer_details", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    keywords="answer details model tool provenance source footer which model made it",
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
                    label="What VOOL may do alone",
                    help=(
                        "How often VOOL stops to ask before doing something. \"Just do routine work\" "
                        "asks least, \"Ask me first\" asks before every step. Whatever you pick, posting, "
                        "sending a message, anything private and spending money always ask you first."
                    ),
                    kind="select",
                    options=[
                        {"value": "hands_off", "label": "Just do routine work — routine steps on this computer run without asking"},
                        {"value": "balanced", "label": "Ask before risky changes — also asks before deleting or changing a lot"},
                        {"value": "strict", "label": "Ask me first — asks before every action"},
                    ],
                    read={"url": PREFS, "field": "autonomy_mode"},
                    write={"url": PREFS, "field": "autonomy_mode", "type": "str"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="autonomy permission approve approval ask acting hands off strict routine risky ask me first",
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
                    id="web_lookups",
                    label="Web lookups",
                    kind="custom",
                    widget="web_lookups",
                    help=(
                        "When you ask something that needs a lookup, VOOL may search the web to "
                        "answer. Off by default: nothing goes online for lookups until you switch "
                        "this on."
                    ),
                    read={"url": WEB_LOOKUPS_READ},
                    keywords="web search lookups online internet browse network access fallback allow",
                ),
                _row(
                    id="live_browser", label="Browser", kind="custom", widget="live_browser",
                    help="Review every action before it runs. Page scripts are off. Use the wallet for payments.",
                    read={"url": "/api/browser/settings"}, keywords="browser live navigate forms",
                ),
                _row(
                    id="automatic_checks",
                    label="Go online on its own",
                    kind="custom",
                    widget="automatic_checks",
                    help=(
                        "Lets VOOL go online on its own, without you asking. When this is on, VOOL may "
                        "download a missing local model when it starts, check which cloud models are "
                        "available, check for updates and sync the online calendars you chose. If this "
                        "computer is set up for the public VOOL network, VOOL also tells that network when "
                        "it is online. While this is off, VOOL does none of these, even when you press "
                        "Check for updates or Sync now. Questions you ask are not affected: Web lookups "
                        "has its own switch. Off by default."
                    ),
                    read={"url": WEB_LOOKUPS_READ},
                    keywords="online on its own automatic outbound background network model download cloud models public network",
                ),
                _row(
                    id="mode_note",
                    label="Approval mode for a single chat",
                    kind="note",
                    help=(
                        "One chat can also have its own approval mode, chosen from the mode menu "
                        "next to the message box. It applies to that chat only, and a temporary "
                        "permission there ends with its task."
                    ),
                    keywords="manual auto bypass mode approval per chat composer",
                ),
                _row(
                    id="privacy_disclosure",
                    label="Privacy & data, as it stands",
                    kind="custom",
                    widget="extras_privacy",
                    help=(
                        "Where your data lives, what is locked, and what leaves this computer and "
                        "when. There are no switches here because these are facts about how VOOL works."
                    ),
                    keywords="privacy data disclosure sealed keys spend gated cloud explicit local first retention",
                ),
                _row(
                    id="locality_note",
                    label="Where your data is",
                    kind="note",
                    help=(
                        "Your chats, settings and what VOOL remembers are files on this computer, and "
                        "your keys are locked here too. When an answer uses an online model or a web "
                        "search, the text of that one request goes to that service, and only then."
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
                    id="provider_guide", label="Find a provider", kind="custom", widget="provider_guide",
                    help="Choose a category, open the official provider site, then add your key below.",
                    keywords="api setup guide providers models web search security scans eyebrow keys official links blockchain chain data helius alchemy solscan etherscan",
                ),
                _row(
                    id="cloud_keys",
                    label="Your keys",
                    kind="custom",
                    widget="cloud_keys",
                    help=(
                        "Add, test or remove your keys. One store on this machine holds them all — "
                        "model providers, web search and security scans — so they live together here, "
                        "not in the sections that use them. VOOL runs locally without any of them. "
                        "Saving verifies a key with that one service; search checks may use credits."
                    ),
                    read={"url": CREDENTIALS},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_IMMEDIATE,
                    keywords=(
                        "api key openai anthropic claude openrouter groq gemini deepseek kimi byok "
                        "credential provider brave tavily exa serper firecrawl jina search token secret eyebrow security scans "
                        "helius alchemy solscan etherscan blockchain chain data solana"
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
                        "The mailboxes VOOL can read and send from, and which one is the default. A chat "
                        "can still use a different one, and VOOL never switches to another mailbox on "
                        "its own. Choosing a default contacts nobody, and passwords are never shown here."
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
                        "If VOOL is not sure an earlier email went out (for example after a crash), it "
                        "holds new sends until you check here. Checking shows what was kept and looks for "
                        "each message in your sent mail. Releasing the hold resends nothing. Only you can "
                        "do this, not VOOL."
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
                        "Which model answers you and where it runs: on this computer or online "
                        "through a provider. Each card shows whether it is ready, and the detailed "
                        "controls for each provider are further down."
                    ),
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="models providers overview usepod openrouter local setup guided budget price limits review",
                ),
                _row(
                    id="model_own_tools",
                    label="Use the model's own tools",
                    help=(
                        "Off by default. When on, a cloud model you choose answers without VOOL's tools or "
                        "web research. A few paid models can search the web themselves: when a question needs "
                        "current information, that search runs at the model's provider, so your question goes "
                        "to them, and the fee counts against your spending limit. When the model can't "
                        "search, VOOL still answers quick facts like prices and weather. Payments always need "
                        "your approval. Auto and local models keep VOOL's tools. In chat, say \"use the "
                        "model's own tools\" or \"use VOOL's tools\"."
                    ),
                    kind="toggle",
                    read={"url": PREFS, "field": "model_own_tools"},
                    write={"url": PREFS, "field": "model_own_tools", "type": "bool"},
                    scope=SCOPE_GLOBAL,
                    effect=EFFECT_NEXT_TURN,
                    keywords="tools native provider own built-in web search cloud model disable vool tools",
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
                        "Things VOOL picked up and confirmed in your chats, with where each came from. "
                        "Forget removes exactly that one; nothing else is touched."
                    ),
                    keywords="memory facts learned remembered forget entries store conversation confirmed",
                ),
                _row(
                    id="remembered_identity_note",
                    label="Your name, and your email signature",
                    kind="note",
                    help=(
                        "Your name and email signature are kept here with everything else VOOL "
                        "remembers. Set your name in the field at the top of this section, and edit "
                        "or forget either one in the list."
                    ),
                    keywords="name called address me preferred name email signature identity remembered",
                ),
            ],
        },
        {
            "id": "backup",
            "icon": "⇅",
            "title": "Backup & restore",
            "blurb": (
                "Save one chat to a file, or bring one back from a file. This moves chats one at a "
                "time; it does not include your settings, memory or keys."
            ),
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
                _row(
                    id="chat_import",
                    label="Bring your chats from other AI apps",
                    kind="custom",
                    widget="chat_import",
                    help=(
                        "Moving in: bring the chats you had in other AI apps, with their names and messages. "
                        "Choose an app on this computer or a data export file, look over the chats found, and "
                        "tick the ones to bring in. Everything stays on this computer, and the other app's "
                        "files are never changed. VOOL keeps these chats as history: it does not learn from "
                        "them or follow anything written in them."
                    ),
                    keywords="bring chats other apps move moving in import history conversations export zip transfer migrate switch",
                ),
            ],
        },
        {
            "id": "network",
            "icon": "◇",
            "title": "Agent Network",
            "blurb": "An unfinished research feature. It is not part of the normal app.",
            "rows": [
                _row(
                    id="network_research_note",
                    label="Research preview",
                    help=(
                        "Working with other agents (shared tasks, research while idle, talking "
                        "to other agents) is an unfinished research feature. This copy of VOOL "
                        "was started for research, so the switches below are live and take "
                        "effect when you change them. A normal VOOL install does not show them."
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
            "blurb": "How much VOOL has used, and which limits it really keeps to.",
            "rows": [
                _row(
                    id="ram_reserve_pct",
                    label="RAM kept free for you",
                    help=(
                        "VOOL always leaves this share of your computer's memory free for you, even "
                        "during a heavy task. A bigger share means VOOL uses less. This is a firm "
                        "limit, not a suggestion."
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
                        "A daily amount VOOL is told about, not a cut-off it enforces: nothing "
                        "stops an answer when it is reached, and the usage below is not compared "
                        "with it. 0 means no limit. Use it as a guide for now."
                    ),
                    kind="number",
                    min=0,
                    max=1_000_000_000,
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
                    help="How much each model was used: free on this computer, or paid online.",
                    scope=SCOPE_GLOBAL,
                    keywords="usage tokens statistics spent local cloud paid free",
                ),
            ],
        },
        {
            # Crypto (Pilot): off by default, discoverable when off. The section is absent in the School edition by the
            # edition floor (core/product_edition.py), never by a scattered check here.
            "id": "calendars",
            "icon": "▦",
            "title": "Calendars",
            "blurb": "The calendars VOOL reads for your agenda and alerts, and where new events go.",
            "rows": [
                _row(
                    id="calendar_accounts_panel",
                    label="Calendar accounts",
                    help=(
                        "Connect an online calendar, or the calendar app on a Mac. Nothing is read until you choose "
                        "calendars and turn sync on, and passwords are never typed here: VOOL uses a sign-in you have "
                        "already saved."
                    ),
                    kind="custom",
                    widget="calendar_accounts",
                    keywords=["calendar", "calendars", "google", "outlook", "microsoft", "icloud", "caldav", "apple calendar", "eventkit", "sync", "alerts", "default calendar", "account"],
                ),
            ],
        },
        {
            "id": "notifications",
            "icon": "◎",
            "title": "Notifications",
            "blurb": "Calendar alerts, reminders, quiet hours and system notifications.",
            "rows": [
                _row(
                    id="notifications_panel",
                    label="Alerts and system notifications",
                    help=(
                        "The bell in VOOL keeps every alert. In the VOOL app on a Mac you can also get a system "
                        "notification, choose quiet hours, sound and what a notification may show, and send a test."
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
                    keywords=["wallet", "crypto", "pilot", "enable", "solana", "base", "ethereum", "bnb", "robinhood", "arbitrum"],
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
                    keywords=["wallet", "crypto", "payment", "payments", "solana", "base", "ethereum", "bnb", "robinhood", "arbitrum", "pin", "backup", "receipt", "testnet", "devnet", "mainnet", "developer"],
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
            "blurb": "How VOOL looks, and the pixel pet.",
            "rows": [
                _row(
                    id="appearance_theme",
                    label="Theme and text size",
                    help=(
                        "Choose Light or Dark, or \"Same as this computer\" to follow your computer's "
                        "setting. Text size makes the conversation easier to read."
                    ),
                    kind="custom",
                    widget="appearance",
                    keywords=["appearance", "theme", "dark", "light", "system", "computer", "mode", "colour", "color", "text", "size", "font", "bigger", "larger", "readability"],
                ),
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
            "id": "phone",
            "icon": "",
            "title": "Phone and chat apps",
            "blurb": "Connect to your own computer. You decide what each device may do.",
            "rows": [
                _row(
                    id="phone_access",
                    label="Use VOOL from my phone",
                    kind="custom", widget="phone_access",
                    help="Off by default. Choose your home or private network, then pair your phone. Turning this off closes phone access.",
                    read={"url": "/api/mobile/settings"},
                    keywords="phone mobile companion network pair revoke telegram discord",
                ),
            ],
        },
        {
            "id": "advanced",
            "icon": "⌥",
            "title": "Advanced",
            "blurb": "Extra pages for people who want to look under the hood.",
            "rows": [
                _row(
                    id="advanced_surfaces",
                    label="Extra pages",
                    kind="custom",
                    widget="advanced_links",
                    help=(
                        "Two extra pages. The activity page shows, step by step, what VOOL is doing "
                        "while it answers. The Web0 page opens sites that an ordinary browser cannot. "
                        "Both open in their own tab or window and leave Settings where it is."
                    ),
                    keywords="advanced trace rail task rail events receipts web0 null browser diagnostics runtime surfaces",
                ),
            ],
        },
        {
            "id": "about",
            "icon": "ⓘ",
            "title": "About & Diagnostics",
            "blurb": "Which version of VOOL you have, and what to include when you report a problem.",
            "rows": [
                _row(
                    id="updates",
                    label="App updates",
                    kind="custom",
                    widget="updates",
                    help="Check whether a newer version of VOOL is available. Your settings and data are kept.",
                    read={"url": "/api/update/status"},
                    keywords="update upgrade release check version up to date",
                ),
                _row(
                    id="build",
                    label="This build",
                    kind="custom",
                    widget="build",
                    help=(
                        "The exact version of VOOL you are using. Copy it into a problem report so "
                        "the problem can be found."
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
                        "What VOOL can use right now: models on this computer, the online connection, "
                        "web search and plugins, each with its checked state. Something not checked "
                        "yet says so; that is not the same as broken. Opening it checks this "
                        "computer only."
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
<link rel="icon" type="image/png" href="__VOOL_LOGO_URI__"/>
<style>
.key-guide-category{border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:8px 0}
.key-guide-category summary{cursor:pointer}.key-guide-ico{flex:0 0 28px;width:28px;height:28px;margin-right:2px;display:inline-flex;align-items:center;justify-content:center;background:var(--accent-soft);border:1px solid var(--border);border-radius:8px;color:var(--accent);opacity:.85}.key-guide-ico svg{width:17px;height:17px;display:block}.key-guide-category[open] .key-guide-ico,.key-guide-category summary:hover .key-guide-ico{opacity:1}
.key-guide-provider{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 0;border-top:1px solid var(--border)}
.key-guide-provider p{margin:5px 0 0}.key-guide-provider .inline{flex-shrink:0}.key-guide-link{color:var(--accent);font-size:12px}
@media(max-width:700px){.key-guide-provider{align-items:flex-start;flex-direction:column}.key-guide-provider .inline{flex-wrap:wrap}}
.x-activity{border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:8px 0;display:flex;flex-direction:column;gap:8px}
.x-activity h4{margin:4px 0 0;font-size:13px}.x-action-card{border-top:1px solid var(--border);padding:10px 0 4px;display:flex;flex-direction:column;gap:6px}
.x-action-text{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;padding:8px 10px;border-left:3px solid var(--border);background:var(--panel,transparent);border-radius:6px}
.x-activity p{margin:0}.x-activity .inline{align-self:flex-start}.x-action-target{overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.x-limits{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.x-limits .inp{min-width:0;width:120px}
.key-setup-dialog{max-width:560px;width:calc(100% - 32px);border:1px solid var(--border);border-radius:12px;background:var(--bg);color:var(--ink);padding:20px}.key-setup-dialog::backdrop{background:rgba(0,0,0,.45)}.key-setup-steps-list{margin:8px 0 12px;padding-left:20px;line-height:1.5}
/* Models & Providers redesign (overview / cards / guided / review sheet) */
.mp-root { gap: 10px; }
.mp-what .row-label { margin-bottom: 2px; }
.mp-problems { border: 1px solid var(--warn, #f0b429); border-radius: 10px; padding: 8px 12px; }
.mp-problem { color: var(--warn, #f0b429); font-size: 12.5px; }
.mp-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.mp-card { flex: 1 1 220px; border: 1px solid var(--border, #262b35); border-radius: 12px; padding: 12px; display: flex; flex-direction: column; gap: 6px; background: var(--panel, #16191f); }
.mp-card.mp-ready { border-color: var(--accent, #5eead4); }
.mp-card-title { font-weight: 700; font-size: 13px; }
.mp-chip { display: inline-block; margin-right: 8px; font-size: 12px; color: var(--muted, #9aa1af); cursor: default; user-select: none; }
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

__VOOL_PALETTE_CSS__
* { box-sizing:border-box; }
/* The hidden attribute always wins: classes such as .inline set display, and a class rule would
   otherwise beat the user-agent [hidden] rule and keep "hidden" fields, warnings and editors on screen. */
[hidden] { display:none !important; }
html, body { height:100%; }
body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,'Inter',system-ui,Segoe UI,Roboto,sans-serif; display:flex; overflow:hidden; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:6px; }

.phone-device {border-top:1px solid var(--line); margin-top:22px; padding-top:18px;}
.phone-rights {display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 18px; margin:16px 0;}
.phone-right {display:flex; align-items:center; gap:7px; min-width:0; overflow-wrap:anywhere;}
.phone-cap {display:grid; grid-template-columns:minmax(0,1fr) minmax(0,180px); align-items:center; gap:12px; margin:10px 0;}
.phone-settings-panel, .phone-device, .phone-rights > *, .phone-cap > * {min-width:0;}
.phone-settings-panel input.inp, .phone-settings-panel select.inp {box-sizing:border-box; width:100%; min-width:0; max-width:100%;}
.phone-expiry {display:grid; min-width:0;}
.phone-actions {display:flex; gap:10px; margin-top:12px; flex-wrap:wrap;}
.phone-unfreeze {display:flex; gap:10px; flex-wrap:wrap; margin-top:12px;}
.phone-settings-panel > .inp {margin:10px 0;}
@media(max-width:650px) {.phone-rights{grid-template-columns:1fr}.phone-cap{grid-template-columns:1fr}}
/* ---- sidebar ---- */
#side { flex:0 0 248px; width:248px; background:var(--panel); border-right:1px solid var(--border); display:flex; flex-direction:column; min-height:0; }
/* App-language control: top-left, above everything else in the nav. Compact (globe + the
   language's own name); the honest coverage note sits under it. */
#uiLocaleWrap { padding:12px 12px 4px; border-bottom:1px solid var(--border); margin-bottom:8px; }
#uiLocaleWrap .loc-label { display:flex; align-items:center; gap:6px; font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:5px; }
#uiLocaleWrap .loc-globe { font-size:14px; line-height:1; }
#uiLocaleWrap .loc-word .set-sub { display:none; }
#uiLocaleWrap .loc-note a { color:var(--accent); }
#uiLocaleWrap .loc-note:empty { display:none; }
#uiLocaleSelect { width:100%; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:6px 8px; font:inherit; font-size:13px; }
#uiLocaleSelect:focus { outline:none; border-color:var(--accent); }
#uiLocaleSelect:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
#uiLocaleSelect:disabled { opacity:.55; cursor:not-allowed; }
#uiLocaleWrap .loc-note { font-size:11px; line-height:1.5; color:var(--muted); margin:6px 0 2px; }
#backRow { padding:12px 12px 4px; }
#skipToPane { position:absolute; inset-inline-start:8px; top:8px; transform:translateY(-300%); z-index:2000; background:var(--panel); color:var(--ink); border:1px solid var(--accent); border-radius:8px; padding:6px 10px; }
#skipToPane:focus { transform:none; }
#back { display:flex; align-items:center; gap:8px; width:100%; background:transparent; color:var(--muted); border:none; border-radius:8px; padding:6px 8px; font:inherit; font-size:13px; cursor:pointer; text-align:left; }
#back:hover { color:var(--ink); background:var(--active); }
#searchWrap { padding:4px 12px 10px; }
#searchBox { position:relative; display:flex; align-items:center; }
#searchIcon { position:absolute; left:9px; color:var(--muted); font-size:12px; pointer-events:none; }
#search { width:100%; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:7px 10px 7px 27px; font:inherit; font-size:13px; }
#search:focus { outline:none; border-color:var(--accent); }
#search:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
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
.row.hit { background:var(--accent-soft); box-shadow:inset 3px 0 0 var(--accent); }
.row-main { flex:1 1 auto; min-width:0; }
.row-label { font-size:13.5px; font-weight:600; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.row-help { color:var(--muted); font-size:12.5px; line-height:1.5; margin-top:4px; }
.pane-meta { margin:4px 0 12px; font-size:12px; color:var(--muted); }
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
.sw[aria-checked="true"] { background:rgba(var(--accent-rgb),.22); border-color:var(--accent); }
.sw[aria-checked="true"]::after { transform:translateX(19px); background:var(--accent); }
.sw:disabled { opacity:.45; cursor:not-allowed; }
.btn { background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:6px 13px; font:inherit; font-size:12.5px; font-weight:600; cursor:pointer; }
.btn:hover:not(:disabled) { border-color:var(--accent); }
.btn:disabled { opacity:.45; cursor:not-allowed; }
.btn.primary { background:var(--grad); color:var(--accent-ink); border:none; }
.btn.danger { color:var(--bad); border-color:var(--bad); }

/* ---- per-row save state: never optimistic ---- */
.state { font-size:11.5px; display:flex; align-items:center; gap:5px; min-height:16px; }
.state.idle { color:transparent; }
.state.loading, .state.saving { color:var(--muted); }
.state.saved { color:var(--ok); }
.state.failed { color:var(--bad); font-weight:600; }
.state.stale { color:var(--warn); }
.state .retry { background:transparent; border:none; color:var(--accent); font:inherit; font-size:11.5px; font-weight:700; cursor:pointer; padding:0 0 0 4px; text-decoration:underline; }
.offline-banner { display:flex; align-items:center; gap:12px; flex-wrap:wrap; border:1px solid var(--bad); border-radius:10px; padding:10px 14px; margin:0 0 18px; font-size:13px; }
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
.ap-label { min-width:7em; }   /* Appearance: both pickers start at the same edge */
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
<a id="skipToPane" href="#pane" data-i18n="settings.nav.skip">Skip to settings</a>
<nav id="side" aria-label="Settings sections">
  <div id="backRow"><button id="back" type="button" data-i18n="settings.nav.back">&larr; Back to VOOL</button></div>
  <div id="searchWrap">
    <div id="searchBox">
      <span id="searchIcon" aria-hidden="true">&#9906;</span>
      <input id="search" type="search" placeholder="Search settings" data-i18n-placeholder="settings.nav.search" aria-label="Search settings" data-i18n-aria-label="settings.nav.search" autocomplete="off" spellcheck="false">
    </div>
  </div>
  <div id="nav" role="list"></div>
</nav>
<main id="pane" tabindex="-1">
  <div id="paneInner">
    <div id="offlineBanner" class="offline-banner" role="alert" hidden><span data-i18n="settings.offline.banner">VOOL isn&rsquo;t responding. Changes can&rsquo;t be saved right now.</span> <button type="button" class="btn" id="offlineRetry" data-i18n="addons.retry">Try again</button></div>
    <h1 id="paneTitle">Settings</h1>
    <p class="pane-blurb" id="paneBlurb"></p>
    <p class="pane-meta" id="paneMeta" hidden></p>
    <div id="paneBody"></div>
  </div>
</main>
<div id="live" aria-live="polite" role="status" style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap"></div>
<script>
const MODEL = __SETTINGS_MODEL__;
const LANGUAGES = __LANGUAGE_CATALOG__;
const BUILD_COMMIT = "__PAGE_BUILD_COMMIT__";
/* Page-chrome strings resolve through the i18n bootstrap's VOOLT (it is injected before this
   script and defines it); the fallback literals are the English catalog source. The settings
   MODEL itself is localized server-side before it is embedded here. */
function T(key, fallback) {
  try { if (typeof VOOLT === 'function') { const t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
  return fallback;
}
/* The {param}-carrying twin: the bundle's own formatter resolves locale placeholders and
   plural branches identically to the server; the inline English fallback formats the same
   way when no bundle ships. */
function tfmt(key, fallback, params) {
  const t = T(key, fallback);
  try {
    if (typeof VOOLFMT === 'function') return VOOLFMT(t, params || {});
  } catch (e) {}
  return String(t).replace(/\{(\w+)\}/g, (m, name) => (params && params[name] != null) ? String(params[name]) : m);
}
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
const SAVE_TIMEOUT_MS = 15000;
/* One page-level notice when VOOL itself stops answering, instead of every row failing on its own with
   browser wording. Every request on this page (the fragments' too) goes through window.fetch, so its
   outcome is the one signal: a network failure shows the notice, the next answer from VOOL clears it. */
(function () {
  if (typeof window.fetch !== 'function') return;
  const realFetch = window.fetch.bind(window);
  const setOffline = (down) => { const n = document.getElementById('offlineBanner'); if (n) n.hidden = !down; };
  window.fetch = function (input, init) {
    return realFetch(input, init).then(r => {
      setOffline(false);
      /* VOOL answers again: writes whose outcome was unknown are read back (see recheckUnsure). */
      if (typeof window.__voolRecheckUnsure === 'function') window.__voolRecheckUnsure();
      return r;
    }, e => { setOffline(true); throw e; });
  };
})();
const NOT_ANSWERING_SHORT = T('settings.save.not_answering', "Not saved \u2014 VOOL isn\u2019t responding.");
const MAY_NOT_HAVE_SAVED = T('settings.save.may_not_have_saved', "VOOL didn\u2019t answer in time, so this may not have saved. It is checked again when VOOL answers.");
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
    state.sourceErr[url] = "VOOL isn\u2019t responding right now.";
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
      const st2 = rowState(rowId);
      if (row) commit(row, st2.dirty !== undefined ? st2.dirty : st2.retry).then(() => { if (row.kind === 'toggle') renderPane(); });
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
  /* Only the newest write of a row decides what it shows: arrow keys on a slider send several
     writes, and their answers can come back in any order. */
  const seq = st.seq = (st.seq || 0) + 1;
  const superseded = () => seq !== st.seq;
  st.dirty = value;
  st.pending = true;
  setState(row.id, 'saving', 'Saving…');
  let r, j;
  /* A server that stopped answering (frozen, not closed) would leave "Saving…" up for ever. */
  const ctl = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), SAVE_TIMEOUT_MS) : null;
  try {
    r = await fetch(row.write.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [row.write.field]: value }),
      signal: ctl ? ctl.signal : undefined,
    });
    j = await r.json().catch(() => null);
  } catch (e) {
    if (timer) clearTimeout(timer);
    if (superseded()) return;
    st.pending = false;
    snapBackToggle(row, st, value);
    /* A refused connection never reached VOOL. A request that timed out may still be carried out
       when a frozen VOOL wakes up, so its outcome is unknown: say so, and read the stored value
       back as soon as VOOL answers again. Both are re-checked, the first costs nothing. */
    UNSURE.set(row.id, value);
    const timedOut = !!(e && e.name === 'AbortError');
    const msg = timedOut ? MAY_NOT_HAVE_SAVED : NOT_ANSWERING_SHORT;
    setState(row.id, 'failed', msg);
    live(row.label + ': ' + msg);
    return;
  }
  if (timer) clearTimeout(timer);
  if (superseded()) return;
  st.pending = false;
  const refused = !r.ok || (j && (j.error || j.ok === false));
  if (refused) {
    const why = (j && (j.error || j.detail)) ? String(j.error || j.detail) : ('refused with HTTP ' + r.status);
    snapBackToggle(row, st, value);
    setState(row.id, 'failed', 'Not saved — ' + why);
    live(row.label + ': not saved. ' + why);
    return;
  }
  /* Read back from the authority rather than trusting the 200. */
  const ok = await readSource(row.read ? row.read.url : row.write.url);
  if (superseded()) return;
  if (!ok) {
    setState(row.id, 'stale', 'Sent, but VOOL could not confirm it. Reopen Settings to check.');
    return;
  }
  const stored = valueOf(row);
  if (!same(stored, value)) {
    /* The authority normalises and clamps on write; when it lands on a different value than the
       one asked for, say so and show what it actually holds instead of a green tick. */
    st.dirty = undefined;
    if (typeof stored === 'string' && !stored.trim()) setState(row.id, 'saved', 'Cleared');
    else if (typeof stored === 'number') setState(row.id, 'stale', 'That value is outside what VOOL accepts here, so it saved ' + stored.toLocaleString() + ' instead.');
    else setState(row.id, 'stale', 'VOOL stored ' + JSON.stringify(stored) + ' instead.');
    live(row.label + ': VOOL stored a different value.');
    renderPane();
    return;
  }
  st.dirty = undefined;
  setState(row.id, 'saved', typeof value === 'string' && !value.trim() ? 'Cleared' : 'Saved');
  live(row.label + ' saved.');
  setTimeout(() => { if (rowState(row.id).status === 'saved') setState(row.id, 'idle', ''); }, 2600);
}
/* A switch whose write did not land shows the stored value again, never a state that was not saved;
   Retry resends the value that was attempted. */
function snapBackToggle(row, st, attempted) {
  if (row.kind !== 'toggle' && row.kind !== 'select') return;
  st.retry = attempted;
  st.dirty = undefined;
  if (row.kind === 'select') {
    /* A list shows the stored choice again too; Retry resends the one that was picked. */
    const sel = document.querySelector('[data-row="' + CSS.escape(row.id) + '"] select');
    const stored = valueOf(row);
    if (sel && stored != null) sel.value = String(stored);
    return;
  }
  const b = document.querySelector('[data-row="' + CSS.escape(row.id) + '"] button.sw');
  if (b) b.setAttribute('aria-checked', valueOf(row) ? 'true' : 'false');
}

/* Writes whose outcome is unknown (VOOL did not answer), by row id -> the value that was sent.
   When VOOL answers again the stored value is read back and the row says what actually happened. */
const UNSURE = new Map();
let recheckTimer = null, rechecking = false;
function recheckUnsure(now) {
  if (!UNSURE.size || rechecking) return;
  if (recheckTimer) clearTimeout(recheckTimer);
  /* A short pause lets a frozen VOOL finish the write it was holding before it is read back. */
  recheckTimer = setTimeout(async () => {
    recheckTimer = null; rechecking = true;
    let changed = false;
    try {
      for (const [id, attempted] of [...UNSURE]) {
        const row = ALL_ROWS.find(r => r.id === id);
        const st = rowState(id);
        if (!row || st.pending) { UNSURE.delete(id); continue; }
        if (!(await readSource(row.read ? row.read.url : row.write.url))) continue;   /* still not answering */
        UNSURE.delete(id);
        if (same(valueOf(row), attempted)) {
          st.retry = undefined;
          setState(id, 'saved', T('settings.save.landed', 'VOOL answered again: this was saved after all.'));
        } else {
          setState(id, 'failed', T('settings.save.not_landed', 'VOOL answered again: this was not saved. The stored value is shown.'));
          /* A write VOOL was still holding can land a moment after it wakes: look once more. */
          setTimeout(async () => {
            const st2 = rowState(id);
            if (st2.pending || st2.status !== 'failed') return;
            if (!(await readSource(row.read ? row.read.url : row.write.url))) return;
            if (same(valueOf(row), attempted)) {
              st2.retry = undefined;
              setState(id, 'saved', T('settings.save.landed', 'VOOL answered again: this was saved after all.'));
              if (row.group === state.group) renderPane();
            }
          }, 5000);
        }
        if (row.group === state.group) changed = true;
      }
    } finally { rechecking = false; }
    if (changed) renderPane();
  }, now ? 0 : 1500);
}
window.__voolRecheckUnsure = () => recheckUnsure(false);

/* ------------------------------------------------------------------ search */
/* Search matches every typed word, in any order, and a word also matches its everyday synonyms
   ("delete" finds Forget). A match in a row's label ranks above one in its help prose. */
const SEARCH_SYNONYMS = {
  delete: ['forget', 'remove'], erase: ['forget', 'remove'], wipe: ['forget', 'remove'], remove: ['forget', 'delete'],
  password: ['passphrase', 'key', 'pin'], pin: ['passphrase'], name: ['call you', 'called'],
  bitcoin: ['crypto', 'wallet'], money: ['spend', 'budget', 'wallet'], cost: ['spend', 'budget', 'price'],
  backup: ['export'], restore: ['import'], data: ['memory', 'remember', 'privacy'],
  /* the same everyday requests typed in German */
  löschen: ['vergessen', 'entfernen'], entfernen: ['vergessen', 'löschen'], daten: ['gedächtnis', 'merkt', 'privatsphäre'],
};
/* Small words carry no meaning in a settings search: "delete my data" is searched as "delete data". */
const SEARCH_STOPWORDS = new Set(['my', 'the', 'a', 'an', 'to', 'of', 'for', 'i', 'me', 'how', 'do', 'can', 'all', 'and',
  'mein', 'meine', 'meinen', 'meiner', 'die', 'der', 'das', 'den', 'dem', 'ein', 'eine', 'einen', 'ich', 'wie', 'alle', 'und', 'zu']);
/* Where an everyday request belongs when several rows match it. */
const SEARCH_PREFER = { delete: 'profile_items', erase: 'profile_items', wipe: 'profile_items', forget: 'profile_items', data: 'profile_items',
  löschen: 'profile_items', vergessen: 'profile_items', daten: 'profile_items' };
/* Words for something Settings does not have: said plainly instead of a wrong section. */
const SEARCH_ABSENT = {
  dark: T('settings.search.no_dark', 'VOOL has one colour scheme, so there is no light or dark setting.'),
  light: T('settings.search.no_dark', 'VOOL has one colour scheme, so there is no light or dark setting.'),
  theme: T('settings.search.no_theme', 'VOOL has one colour scheme, so there is no theme setting.'),
  /* the same requests typed in German (the most used non-English app language) */
  dunkel: T('settings.search.no_dark', 'VOOL has one colour scheme, so there is no light or dark setting.'),
  hell: T('settings.search.no_dark', 'VOOL has one colour scheme, so there is no light or dark setting.'),
  thema: T('settings.search.no_theme', 'VOOL has one colour scheme, so there is no theme setting.'),
  design: T('settings.search.no_theme', 'VOOL has one colour scheme, so there is no theme setting.'),
};
function rowText(r) { return [r.label, r.help, r.keywords, r.groupTitle].filter(Boolean).join(' ').toLowerCase(); }
function queryWords(q) {
  const words = String(q || '').split(/\s+/).filter(Boolean);
  const meaningful = words.filter(w => !SEARCH_STOPWORDS.has(w));
  return meaningful.length ? meaningful : words;
}
function preferredRow(q) {
  const id = queryWords(q).map(w => SEARCH_PREFER[w]).find(Boolean);
  const r = id && ALL_ROWS.find(x => x.id === id);
  return r && matches(r, q) ? r : null;
}
function absentNote(q) { return queryWords(q).map(w => SEARCH_ABSENT[w]).find(Boolean) || ''; }
function wordHit(text, w) { return text.includes(w) || (SEARCH_SYNONYMS[w] || []).some(x => text.includes(x)); }
function matches(r, q) { if (!q) return true; const t = rowText(r); return queryWords(q).every(w => wordHit(t, w)); }
function labelMatches(r, q) { const t = String(r.label || '').toLowerCase(); return !!q && queryWords(q).every(w => wordHit(t, w)); }
function hitsFor(groupId, q) { return ALL_ROWS.filter(r => r.group === groupId && matches(r, q)).length; }

/* ------------------------------------------------------------------ render */
const SETTINGS_NAV_ICONS = {
  setup: 'check', general: 'settings', permissions: 'shield', keys: 'key', email: 'mail', models: 'models',
  memory: 'memory', backup: 'backup', network: 'globe', budgets: 'budgets', calendars: 'calendar',
  notifications: 'bell', wallet: 'wallet', companion: 'appearance', advanced: 'advanced', about: 'about',
};
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
  const head = el('div', 'nav-sec', q ? T('settings.nav.results', 'Results') : T('settings.nav.head', 'Settings'));
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
    // One line-icon style for every section (core/ui_icons.py); a section without one keeps its glyph.
    const icoName = SETTINGS_NAV_ICONS[g.id];
    const ico = el('span', icoName ? 'ico vi' : 'ico', icoName ? '' : (g.icon || '○')); ico.setAttribute('aria-hidden', 'true');
    if (icoName) ico.dataset.ico = icoName;
    b.appendChild(ico);
    b.appendChild(el('span', null, g.title));
    if (q) b.appendChild(el('span', 'hits', String(hits)));
    b.addEventListener('click', () => { state.onlyRow = ''; state.focusPart = ''; state.focusRow = ''; state.notice = ''; go(g.id); });
    nav.appendChild(b);
  });
  if (!any) nav.appendChild(el('div', 'nav-empty', tfmt('settings.search.no_match', 'No setting matches “{query}”', { query: state.query })));
  const currentItem = nav.querySelector('.nav-item[aria-current="true"]');
  if (hadNavFocus) {
    const current = currentItem || nav.querySelector('.nav-item');
    if (current) current.focus();
  }
  /* In a short or narrow window the list scrolls; the section you are in stays in view. Only the
     list's own scroll position is moved: scrollIntoView would also move the browser's Tab starting
     point to the nav item, so the first Tab would skip the skip link, language, Back and Search. */
  if (currentItem && nav.scrollHeight > nav.clientHeight) {
    const top = currentItem.offsetTop - nav.offsetTop, bottom = top + currentItem.offsetHeight;
    if (top < nav.scrollTop) nav.scrollTop = top;
    else if (bottom > nav.scrollTop + nav.clientHeight) nav.scrollTop = bottom - nav.clientHeight;
  }
}

function scopeText(scope) {
  return scope === 'global' ? T('settings.scope.global', 'Applies everywhere')
    : (scope === 'chat' ? T('settings.scope.chat', 'This chat only') : T('settings.scope.session', 'This session only'));
}
// When and how far a change reaches. Most pages share one answer for every row, so it is said
// once under the page title (state.paneMeta) and a row repeats only what differs from it.
function metaLine(row) {
  const wrap = el('div', 'row-meta');
  const common = state.paneMeta || {};
  if (row.scope && row.scope !== common.scope) wrap.appendChild(el('span', 'm', scopeText(row.scope)));
  if (row.effect && row.effect !== common.effect) wrap.appendChild(el('span', 'm', row.effect));
  return wrap;
}
function commonPaneMeta(rows) {
  const counts = {};
  let best = null;
  let kind = '';
  let mixed = false;
  rows.forEach(r => {
    if (r.kind === 'note' || r.kind === 'custom' || !r.scope || !r.effect) return;
    const k = r.scope + '\u0000' + r.effect;
    counts[k] = (counts[k] || 0) + 1;
    if (best && k !== best) mixed = true;
    if (!best) { best = k; kind = r.effect_kind || ''; }
  });
  // Only a line that is true of EVERY setting on the page is said once at the top; a single row
  // gains nothing from being summarised.
  if (!best || mixed || counts[best] < 2) return null;
  const parts = best.split('\u0000');
  return { scope: parts[0], effect: parts[1], kind: kind };
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
    /* The switch shows the value being written, then whatever the authority reports back. `v` was
       captured when the row was drawn, so it must never decide what a later click shows. One write at
       a time: a double click is one change, not two racing ones. A refused write snaps the switch back
       to the stored value; Retry resends the attempted one. */
    b.addEventListener('click', async () => {
      const st = rowState(row.id);
      if (st.pending) return;
      const next = !shownValue(row);
      b.setAttribute('aria-checked', next ? 'true' : 'false');
      b.setAttribute('aria-busy', 'true');
      await commit(row, next);
      b.removeAttribute('aria-busy');
      b.setAttribute('aria-checked', shownValue(row) ? 'true' : 'false');
    });
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
    /* Arrow keys fire one change per step: save once the value has settled, not once per step. */
    let settle = null;
    i.addEventListener('change', () => { clearTimeout(settle); settle = setTimeout(() => commit(row, i.value), 400); });
    box.appendChild(i); box.appendChild(out);
    wrap.appendChild(box);
  }
  else if (row.kind === 'number' || row.kind === 'text') {
    const i = document.createElement('input');
    i.className = 'inp';
    i.type = row.kind === 'number' ? 'number' : 'text';
    if (row.min != null) i.min = row.min;
    if (row.kind === 'number' && row.max != null) i.max = row.max;
    if (row.step != null) i.step = row.step;
    if (row.maxlength) i.maxLength = row.maxlength;
    if (row.placeholder) i.placeholder = row.placeholder;
    i.value = v == null ? '' : String(v);
    i.setAttribute('aria-label', row.label);
    /* A number box takes whole numbers only: "1e6" or "1.5" would otherwise be cut to 1 and called saved. */
    const wholeNumber = (raw) => /^\d+$/.test(String(raw).trim());
    const send = () => {
      if (row.kind === 'number' && !wholeNumber(i.value)) {
        setState(row.id, 'stale', 'Enter a whole number' + (row.min != null && Number(row.min) > 0 ? '' : ' (0 or more)') + ', for example 50000.');
        return;
      }
      if (row.kind === 'number' && row.max != null && Number(i.value) > Number(row.max)) {
        setState(row.id, 'stale', 'Enter a number up to ' + Number(row.max).toLocaleString() + '.');
        return;
      }
      if (!same(i.value, valueOf(row))) commit(row, row.kind === 'text' ? i.value.trim() : i.value);
    };
    i.addEventListener('change', send);
    i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
    wrap.appendChild(i);
    /* A text box with a limit says so as it fills, instead of cutting a paste off silently. */
    if (row.kind === 'text' && row.maxlength) {
      const count = el('div', 'subtle text-count');
      const paint = () => { count.textContent = i.value.length + ' / ' + row.maxlength; };
      i.addEventListener('input', paint); paint();
      wrap.appendChild(count);
    }
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
  if (row.kind !== 'note' && row.kind !== 'custom') { const meta = metaLine(row); if (meta.childNodes.length) main.appendChild(meta); }
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
  state.paneMeta = state.onlyRow ? null : commonPaneMeta(g.rows);
  const metaEl = $('#paneMeta');
  if (metaEl) {
    metaEl.textContent = !state.paneMeta ? ''
      : (state.paneMeta.scope === 'global' && state.paneMeta.kind === 'next_turn')
        ? T('settings.pane_meta.global_next', 'Changes here apply everywhere, from your next message.')
        : scopeText(state.paneMeta.scope) + ' \u00b7 ' + state.paneMeta.effect;
    metaEl.hidden = !state.paneMeta;
  }
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
  if (!shown && q && !MODEL.some(x => hitsFor(x.id, q) > 0)) {
    /* Nothing anywhere matches: say so in the pane too, with places to try, instead of leaving the
       previous section on screen with an empty card under it. */
    $('#paneTitle').textContent = tfmt('settings.search.no_match', 'No setting matches \u201c{query}\u201d', { query: state.query });
    const absent = absentNote(q);
    const tryOther = T('settings.search.try_other', 'Try other words, or one of these:');
    $('#paneBlurb').textContent = absent ? absent + ' ' + tryOther : tryOther;
    const tries = el('div', 'row wide');
    const bar = el('div', 'inline');
    [['memory', 'Memory & Personalisation'], ['permissions', 'Privacy & Permissions'], ['backup', 'Backup & restore']].forEach(([gid, label]) => {
      if (!MODEL.some(x => x.id === gid)) return;
      const b = el('button', 'btn', (MODEL.find(x => x.id === gid) || {}).title || label); b.type = 'button';
      b.addEventListener('click', () => { $('#search').value = ''; state.query = ''; go(gid); });
      bar.appendChild(b);
    });
    const clearB = el('button', 'btn', T('header.search_clear_title', 'Clear search')); clearB.type = 'button';
    clearB.addEventListener('click', () => { $('#search').value = ''; onSearch(''); $('#search').focus(); });
    bar.appendChild(clearB);
    tries.appendChild(bar);
    tries.appendChild(el('div', 'subtle', T('settings.search.ask_chat', 'You can also ask VOOL in a chat.')));
    card.appendChild(tries);
  } else if (!shown) card.appendChild(el('div', 'row', ''));
  if (state.notice === 'setup_hidden') {
    const n = el('div', 'card setup-hidden-note');
    const inner = el('div', 'row wide');
    inner.appendChild(el('div', 'row-help', setupHiddenByChoice() ? 'The setup checklist is hidden because you asked not to be reminded.' : 'The setup checklist is gone because every step is done.'));
    if (setupHiddenByChoice()) inner.appendChild(setupRestoreControl());
    n.appendChild(inner); body.appendChild(n);
  }
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
  /* The address names the open section, so a reload (or the app restoring the window) comes back to
     it. replaceState: no history entry per click, and no hashchange event to loop through. */
  try {
    const current = String(location.hash || '').replace(/^#/, '').split('/')[0];
    if (groupId && current !== groupId && history.replaceState) history.replaceState(null, '', '#' + groupId);
  } catch (e) { /* a sandboxed frame may refuse; the section still opens */ }
  if (rowId) state.focusRow = rowId;
  renderNav();
  $('#paneBody').textContent = T('settings.pane.loading', 'Loading settings…');
  if (!rowId) $('#pane').scrollTop = 0;
  live((MODEL.find(g => g.id === groupId) || {}).title + ' settings');
  /* only THIS group's reads, and only once -- except the setup checklist, which is a projection of
     settings changed anywhere else and is re-read every time it is opened, so its ticks are current */
  if (groupId === 'setup') await ensureGroupSources(groupId, true);
  else await ensureGroupSources(groupId);
  if (rowId) state.focusRow = rowId;
  renderPane();
}
</script>
"""

# Widgets, keyboard handling and boot. Split out so each raw string stays reviewable.
_SETTINGS_JS2 = r"""<script>
/*__VOOL_APPEARANCE_JS__*/
/* ------------------------------------------------------------------ widgets
   A widget renders a row that is not a single value. Each one is responsible for its OWN read,
   performed when its group is first opened, so nothing here runs while Settings is showing
   another page. Anything that leaves this machine (a provider catalogue, a connection test) is
   behind a button that states where it goes before it goes. */
const widgetCache = {};

async function getJSON(url) {
  let r;
  try { r = await fetch(url, { headers: { 'Accept': 'application/json' } }); }
  catch (e) { throw new Error("VOOL isn\u2019t responding right now."); }
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
  if (w === 'updates') return widgetUpdates(stack);
  if (w === 'usage') return widgetUsage(stack);
  if (w === 'addon_security') { if (window.VoolAddons) return window.VoolAddons.mountSecurity(stack); return; }
  if (w === 'provider_guide') return widgetProviderGuide(stack);
  if (w === 'cloud_keys') return widgetKeys(stack);
  if (w === 'models_overview') return widgetModelsOverview(stack);
  if (w === 'model_pin') return widgetModel(stack);
  if (w === 'local_models') return widgetLocalModels(stack);
  if (w === 'usepod') return widgetUsePod(stack);
  if (w === 'profile') return widgetProfile(stack);
  if (w === 'email_accounts') return widgetEmailAccounts(stack);
  if (w === 'email_recovery') return widgetEmailRecovery(stack);
  if (w === 'answer_language') return widgetAnswerLanguage(stack);
  if (w === 'live_browser') return widgetLiveBrowser(stack);
  if (w === 'web_lookups') return widgetWebLookups(stack);
  if (w === 'automatic_checks') return widgetAutomaticChecks(stack);
  if (w === 'phone_access') return widgetPhoneAccess(stack);
  if (w === 'wallet') return widgetWallet(stack);
  if (w === 'crypto') return widgetCrypto(stack);
  if (w === 'calendar_accounts') return widgetSurface(stack, 'VoolCalendarSettings', T('settings.pane.calendar_missing', 'The calendar accounts panel did not load.'));
  if (w === 'notifications') return widgetSurface(stack, 'VoolNotificationSettings', T('settings.pane.notifications_missing', 'The notifications panel did not load.'));
  if (w === 'scam_school') return widgetScamSchool(stack);
  if (w === 'setup_progress') return widgetSetupProgress(stack);
  if (w === 'appearance') return widgetAppearance(stack);
  if (w === 'companion_pet') return widgetCompanionPet(stack);
  if (w === 'advanced_links') return widgetAdvancedLinks(stack);
  if (w === 'extras_memory') return widgetExtras(stack, 'memory');
  if (w === 'extras_privacy') return widgetExtras(stack, 'privacy');
  if (w === 'extras_toolbelt') return widgetExtras(stack, 'toolbelt');
  if (w === 'bundle_export') return widgetBundleExport(stack);
  if (w === 'bundle_import') return widgetBundleImport(stack);
  if (w === 'chat_import') return widgetSurface(stack, 'VoolChatImport', T('settings.pane.chat_import_missing', 'The panel for bringing in chats did not load.'));
  stack.appendChild(el('div', 'empty', T('settings.pane.empty', 'This section has nothing to show.')));
}

/* --- Backup & restore: one chat as a portable, signed .voolsession bundle, through the ONE seam
   (core/session_portability/api.py) behind /api/session/bundle/*. Settings has no chat of its own,
   so the chat is picked from the sidebar's own listing (/api/chat/sessions). A passphrase exists
   only in these fields and travels only to the export/import doors, never into history or logs. */
async function bundlePost(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return await r.json().catch(() => ({ ok: false, error: 'HTTP ' + r.status }));
}
function countWords(n, one, many) { n = Number(n) || 0; return n + ' ' + (n === 1 ? one : many); }
function sizeWords(bytes) { return bytes < 1024 ? bytes + ' bytes' : bytes < 1048576 ? Math.round(bytes / 1024) + ' KB' : (bytes / 1048576).toFixed(1) + ' MB'; }
function fileStem(text, fallback) { const t = String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40); return t || fallback; }
function chatWhen(s) {
  const raw = s.updated_at || s.last_activity_at || s.last_message_at || s.created_at;
  if (!raw) return '';
  const d = new Date(typeof raw === 'number' && raw < 1e12 ? raw * 1000 : raw);
  return isNaN(d) ? '' : d.toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
function widgetBundleExport(stack) {
  const status = el('div', 'state idle'); status.id = 'sbExportStatus'; status.setAttribute('role', 'status');
  const say = (msg, warn) => { status.className = 'state ' + (warn ? 'failed' : 'saved'); status.textContent = msg; };
  const pick = el('div', 'inline');
  const chatSel = el('select', 'inp'); chatSel.id = 'sbChat'; chatSel.setAttribute('aria-label', T('settings.backup.chat_aria', 'Chat to export'));
  const none = el('option', null, T('settings.backup.choose_chat', 'Choose a chat…')); none.value = ''; chatSel.appendChild(none);
  pick.appendChild(el('span', 'subtle', 'Chat')); pick.appendChild(chatSel);
  stack.appendChild(pick);
  const preview = el('div', 'sb-box'); preview.id = 'sbExportPreview'; preview.hidden = true; stack.appendChild(preview);
  const encRow = el('label', 'inline');
  const enc = document.createElement('input'); enc.type = 'checkbox'; enc.id = 'sbEncrypt'; enc.checked = true;
  encRow.appendChild(enc); encRow.appendChild(el('span', null, T('settings.backup.encrypt', 'Encrypt with a passphrase (recommended)')));
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
      row('Included', countWords(d.counts.turns, 'turn', 'turns') + ', ' + countWords(d.counts.summaries, 'summary', 'summaries') + ', ' + countWords(d.counts.tool_receipts, 'tool record', 'tool records') + ', ' + countWords(d.counts.session_events, 'activity entry', 'activity entries'));
      const bytes = (d.embedded_files || []).reduce((s, f) => s + (f.size_bytes || 0), 0);
      row('Attachments', countWords(d.counts.embedded_files || 0, 'file', 'files') + (bytes ? ', ' + sizeWords(bytes) : ''));
      row('Redactions applied', String(d.redactions || 0));
      row('Encryption', enc.checked ? 'ON (recommended)' : 'OFF — the file will be readable by anyone who obtains it');
      row('Not included', 'VOOL\u2019s internal reasoning, your settings, memory and keys');
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
      const when = chatWhen(s);
      const o = el('option', null, (s.title || 'New chat') + (when ? ' · ' + when : ''));
      o.value = s.session_id; o.dataset.title = s.title || ''; chatSel.appendChild(o);
    });
    if (!sessions.length) {
      /* Nothing to export is a plain fact, not a success: neutral words, and nothing to press. */
      status.className = 'state loading'; status.textContent = T('settings.backup.no_chats', 'No chats to export yet.');
      chatSel.disabled = true; exportBtn.disabled = true;
    }
  }).catch(e => say('Could not list chats — ' + e.message, true));

  exportBtn.addEventListener('click', async () => {
    const sid = chatSel.value;
    if (!sid) { say('Choose the chat to export first.', true); return; }
    let passphrase = '';
    if (enc.checked) {
      passphrase = String(pass.value || '');
      if (!passphrase) { say('Type a passphrase, or untick encryption.', true); return; }
      if (passphrase !== String(pass2.value || '')) { say('The two passphrases do not match.', true); return; }
      if (passphrase.length < 8) { say('Use a passphrase of at least 8 characters. Short passphrases are easy to guess.', true); return; }
    } else if (!warn.checked) { say('Unticking encryption needs the explicit warning acknowledgement below.', true); return; }
    exportBtn.disabled = true; status.className = 'state saving'; status.textContent = 'Working…';
    try {
      const d = await bundlePost('/api/session/bundle/export', { session_id: sid, passphrase: passphrase });
      if (!d.ok) { say(d.error || 'Export refused.', true); exportBtn.disabled = false; return; }
      pass.value = ''; pass2.value = '';
      /* The file is fetched and handed to the browser as a blob, so the name said below is the name
         it is saved under (a plain link would take the server's internal file name instead). */
      const picked = chatSel.selectedOptions && chatSel.selectedOptions[0];
      const name = fileStem(picked && picked.dataset.title, 'chat') + '-' + new Date().toISOString().slice(0, 10) + '.voolsession';
      const got = await fetch('/api/session/bundle/download?path=' + encodeURIComponent(d.path));
      if (!got.ok) { say('The bundle was made but could not be downloaded. Try Export again.', true); exportBtn.disabled = false; return; }
      const blobUrl = URL.createObjectURL(await got.blob());
      const a = document.createElement('a');
      a.href = blobUrl; a.download = name; a.dataset.bundlePath = d.path;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      say(d.encrypted ? 'Saved as ' + a.download + ': a signed, encrypted bundle (' + countWords(d.counts.turns, 'turn', 'turns') + ').' : 'Saved as ' + a.download + ': signed but NOT encrypted (' + countWords(d.counts.turns, 'turn', 'turns') + ').', !d.encrypted);
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
  foreignRow.appendChild(foreign); foreignRow.appendChild(el('span', null, 'This file comes from a computer VOOL does not know. Import it anyway, marked as not trusted.'));
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
    row('Chat', (d.title || 'Untitled chat') + (d.conflicts && d.conflicts.collision ? ' (already here — it is added as a separate copy)' : ''));
    row('Turns / summaries', (counts.turns || 0) + ' / ' + (counts.summaries || 0));
    row('Attachments', String(counts.embedded_files || 0));
    if (counts.profile_candidates) row('Memory suggestions', countWords(counts.profile_candidates, 'suggestion', 'suggestions') + ', offered to you, never applied by themselves');
    const trusted = !!(d.signature && d.signature.trusted);
    row('Signed by', trusted ? 'a computer this VOOL trusts' : 'a computer this VOOL does not know');
    row('Encrypted', d.encrypted ? 'yes' : 'no');
    box.appendChild(dl);
    if (d.signature && d.signature.signer_fingerprint) {
      const more = el('details', 'subtle');
      more.appendChild(el('summary', null, 'Details'));
      more.appendChild(el('div', 'mono', 'Signer fingerprint: ' + d.signature.signer_fingerprint));
      box.appendChild(more);
    }
    if (d.conflicts && d.conflicts.collision) box.appendChild(el('div', 'fail-note', 'This chat is already here. The import is added as a separate copy; nothing existing is changed.'));
    if (d.needs_confirmation) box.appendChild(el('div', 'fail-note', 'From a computer VOOL does not know: the import will be marked as not trusted.'));
  };
  previewBtn.addEventListener('click', async () => {
    const f = file.files && file.files[0];
    if (!f) { say('Pick a .voolsession file first.', true); return; }
    if (!f.size) { say('This file is empty. Choose the .voolsession file you exported.', true); return; }
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
      say(needsConfirm ? 'This file comes from a computer VOOL does not know. Tick the box below to import it anyway, marked as not trusted.' : 'Preview ready. Check it, then choose Import.', needsConfirm);
    } catch (e) { say('Preview failed: ' + (e && e.message ? e.message : e), true); }
  });
  importBtn.addEventListener('click', async () => {
    if (!stagedPath) { say('Preview a file first.', true); return; }
    if (needsConfirm && !foreign.checked) { say('Tick the box to import a file from a computer VOOL does not know, or choose another file.', true); return; }
    importBtn.disabled = true; status.className = 'state saving'; status.textContent = 'Importing…';
    try {
      const d = await bundlePost('/api/session/bundle/import', { path: stagedPath, passphrase: String(ipass.value || ''), confirm_untrusted: !!foreign.checked });
      if (!d.ok) { say(d.error || 'Import refused — nothing was changed.', true); importBtn.disabled = false; return; }
      restoredId = d.imported_session_id || '';
      file.value = ''; ipass.value = ''; box.hidden = true; stagedPath = '';
      openBtn.hidden = !restoredId; openBtn.dataset.session = restoredId;
      say('Imported ' + countWords((d.counts || {}).turns || 0, 'turn', 'turns') + (d.collision ? ' as a separate copy (the original was already here).' : '.') +
          (d.trust && d.trust.trusted === false ? ' It is marked as not trusted because it came from a computer VOOL does not know.' : '') + ' The restored chat is at the top of your chat list.');
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
  stack.appendChild(el('div', 'empty', T('settings.pane.extras_missing', 'This section did not load.')));
}

/* --- Advanced: real links to the two runtime surfaces this daemon serves (core/web/api/service.py:
   /trace -> the task rail, /web0 -> the .null browser entry page). Same origin, same runtime; they
   open in their own tab or window so Settings stays put. Nothing here is a claim -- each is a
   plain anchor to a route that exists. */
const ADVANCED_SURFACES = [
  { href: '/trace', label: T('settings.advanced.trace', 'Open the activity page'), note: T('settings.advanced.trace_note', 'what VOOL is doing, step by step, while it answers') },
  { href: '/web0', label: T('settings.advanced.web0', 'Open the Web0 page'), note: T('settings.advanced.web0_note', 'opens sites an ordinary browser cannot') },
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
/* The removed group is kept so "Show the setup checklist again" can put it back without a reload. */
let removedSetup = null;
function removeSetupGroup() {
  const i = MODEL.findIndex(g => g.id === 'setup');
  if (i >= 0) removedSetup = { group: MODEL.splice(i, 1)[0], rows: ALL_ROWS.filter(r => r.group === 'setup') };
  for (let k = ALL_ROWS.length - 1; k >= 0; k--) if (ALL_ROWS[k].group === 'setup') ALL_ROWS.splice(k, 1);
  if (state.group === 'setup') state.group = MODEL.length ? MODEL[0].id : '';
}
/* Hidden only because the person asked (not because setup is finished): offer the way back. */
function setupHiddenByChoice() {
  const s = state.sources[SETUP_STATE_URL];
  return !!(removedSetup && s && s.dismissed && !s.complete);
}
function setupRestoreControl() {
  const line = el('div', 'inline setup-restore');
  const b = el('button', 'btn', 'Show the setup checklist again'); b.type = 'button'; b.id = 'setupRestore';
  const st = el('span', 'state idle');
  b.addEventListener('click', async () => {
    b.disabled = true; st.className = 'state saving'; st.textContent = 'Saving…';
    try {
      const r = await fetch('/api/settings/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setup_dismissed: false }) });
      const j = await r.json().catch(() => null);
      if (!r.ok || (j && j.error)) throw new Error((j && j.error) || ('HTTP ' + r.status));
      await readSource(SETUP_STATE_URL);
      const s = state.sources[SETUP_STATE_URL];
      if (s && s.show_entry && removedSetup && !MODEL.some(g => g.id === 'setup')) {
        MODEL.unshift(removedSetup.group); ALL_ROWS.unshift(...removedSetup.rows); removedSetup = null;
      }
      state.notice = '';
      go('setup');
    } catch (e) { b.disabled = false; st.className = 'state failed'; st.textContent = 'Not saved — ' + e.message; }
  });
  line.appendChild(b); line.appendChild(st);
  return line;
}
function widgetSetupProgress(stack) {
  const src = state.sources[SETUP_STATE_URL];
  const err = state.sourceErr[SETUP_STATE_URL];
  if (err) { stack.appendChild(el('div', 'empty', tfmt('settings.widget.unavailable', 'Unavailable — {error}', { error: err }))); return; }
  if (!src) { stack.appendChild(el('div', 'empty', T('settings.widget.loading', 'Loading…'))); return; }
  const head = el('div', 'row-label');
  head.id = 'setupCount';
  head.textContent = tfmt('setup.checklist.count', '{n} of {m} done', { n: src.done_count, m: src.total });
  stack.appendChild(head);
  const list = el('div', 'stack');
  list.id = 'setupSteps';
  (src.steps || []).forEach(s => {
    const line = el('div', 'inline');
    line.dataset.step = s.id;
    line.dataset.done = s.done ? 'true' : 'false';
    const pill = el('span', 'pill ' + (s.done ? 'on' : ''));
    pill.appendChild(el('span', 'dot'));
    pill.appendChild(el('span', null, s.done ? T('setup.checklist.done', 'Done')
      : (s.skipped ? T('setup.checklist.skipped', 'Skipped') : T('setup.checklist.todo', 'To do'))));
    line.appendChild(pill);
    line.appendChild(el('span', null, T('setup.step.' + s.id + '.title', s.title)));
    if (!s.done) line.appendChild(el('span', 'subtle', tfmt('setup.checklist.later_prefix', 'later: {later}', { later: T('setup.step.' + s.id + '.later', s.later) })));
    list.appendChild(line);
  });
  stack.appendChild(list);
  const bar = el('div', 'inline');
  const go_ = el('button', 'btn primary', src.done_count ? T('setup.checklist.continue', 'Continue setup') : T('setup.checklist.start', 'Start setup'));
  go_.type = 'button'; go_.id = 'setupContinue';
  go_.addEventListener('click', () => openSetupPage(src.first_undone || ''));
  const dismiss = el('button', 'btn', T('setup.checklist.dismiss', 'Don\u2019t remind me'));
  dismiss.type = 'button'; dismiss.id = 'setupDismiss';
  const st = el('span', 'state idle', '');
  /* One click must not silently remove the only guided overview: ask first, and say where it comes back. */
  const confirmBar = el('div', 'inline setup-dismiss-confirm'); confirmBar.hidden = true;
  confirmBar.appendChild(el('span', null, 'Hide the setup checklist? You can bring it back from About & Diagnostics.'));
  const yes = el('button', 'btn', 'Hide it'); yes.type = 'button'; yes.id = 'setupDismissConfirm';
  const no = el('button', 'btn', 'Keep it'); no.type = 'button';
  confirmBar.appendChild(yes); confirmBar.appendChild(no);
  no.addEventListener('click', () => { confirmBar.hidden = true; dismiss.hidden = false; dismiss.focus(); });
  dismiss.addEventListener('click', () => { confirmBar.hidden = false; dismiss.hidden = true; yes.focus(); });
  yes.addEventListener('click', async () => {
    yes.disabled = true; st.className = 'state saving'; st.textContent = T('setup.checklist.saving', 'Saving…');
    try {
      const r = await fetch('/api/settings/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ setup_dismissed: true }) });
      const j = await r.json().catch(() => null);
      if (!r.ok || (j && j.error)) throw new Error((j && j.error) || ('HTTP ' + r.status));
      await readSource(SETUP_STATE_URL);
      if (state.sources[SETUP_STATE_URL] && !state.sources[SETUP_STATE_URL].show_entry) {
        removeSetupGroup();
        live(T('setup.checklist.reminder_hidden', 'Setup reminder hidden. Every step is still an ordinary setting.'));
        go(state.group);
        return;
      }
      renderPane();
    } catch (e) { yes.disabled = false; st.className = 'state failed'; st.textContent = tfmt('setup.checklist.not_saved', 'Not saved — {error}', { error: e.message }); }
  });
  bar.appendChild(go_); bar.appendChild(dismiss); bar.appendChild(st);
  stack.appendChild(bar);
  stack.appendChild(confirmBar);
  stack.appendChild(el('div', 'subtle', T('setup.checklist.disappears', 'This entry disappears by itself when every step is done.')));
}

/* --- Stay safe: the scam cards from core/wallet/scam_school.py (one authority; the SVGs are the server's own
   static drawings, never user content). Available with the wallet off: knowledge first, custody later. */
function widgetScamSchool(stack) {
  const src = state.sources['/api/wallet/safety'];
  const err = state.sourceErr['/api/wallet/safety'];
  if (err) { stack.appendChild(el('div', 'empty', tfmt('settings.widget.unavailable', 'Unavailable — {error}', { error: err }))); return; }
  if (!src) { stack.appendChild(el('div', 'empty', T('settings.widget.loading', 'Loading…'))); return; }
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
  stack.appendChild(el('div', 'empty', T('settings.pane.wallet_missing', 'The wallet surface did not load.')));
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
// Theme and conversation text size. Presentation only: the choice is kept on this computer
// (window.VoolAppearance, core/ui_palette.py) and shows at once in every open VOOL window.
function widgetAppearance(stack) {
  const api = window.VoolAppearance;
  if (!api) { stack.appendChild(el('div', 'empty', T('settings.appearance.unavailable', 'Appearance controls did not load. Reload Settings and try again.'))); return; }
  const now = api.get();
  const said = el('span', 'subtle', '');
  said.setAttribute('role', 'status');
  function picker(label, name, options, value, key) {
    const wrap = el('label', 'inline');
    wrap.appendChild(el('span', 'ap-label', label));
    const s = el('select', 'inp');
    s.name = name;
    s.setAttribute('aria-label', label);
    options.forEach(o => { const op = el('option', null, o[1]); op.value = String(o[0]); s.appendChild(op); });
    s.value = String(value);
    s.addEventListener('change', () => {
      const next = {}; next[key] = key === 'text' ? Number(s.value) : s.value;
      said.textContent = api.set(next)
        ? T('settings.appearance.saved', 'Saved on this computer.')
        : T('settings.appearance.unsaved', 'Could not be saved, so it applies to this window only.');
    });
    wrap.appendChild(s);
    return wrap;
  }
  stack.appendChild(picker(T('settings.appearance.theme_label', 'Theme'), 'appearanceTheme', [
    ['dark', T('settings.appearance.theme_dark', 'Dark')],
    ['light', T('settings.appearance.theme_light', 'Light')],
    ['system', T('settings.appearance.theme_system', 'Same as this computer')],
  ], now.theme, 'theme'));
  stack.appendChild(picker(T('settings.appearance.text_label', 'Text size'), 'appearanceText', [
    [100, T('settings.appearance.text_100', 'Normal')],
    [112, T('settings.appearance.text_112', 'Large')],
    [125, T('settings.appearance.text_125', 'Larger')],
  ], now.text, 'text'));
  stack.appendChild(said);
}

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
    stack.appendChild(el('div', 'empty', T('settings.companion.needs_app', 'The desktop pet needs the VOOL app window. In a browser tab the companion stays docked in the chat.')));
    return;
  }
  stack.appendChild(el('div', 'subtle', 'Drag the pet out of the chat window to put it on your desktop. These buttons bring it back, hide it or find it again.'));
  const bar = el('div', 'inline');
  const said = el('span', 'subtle', '');
  // No "Move to desktop" here: the pet goes to the desktop by being dragged out of the window.
  // These stay as rescue controls for a pet that is already out there.
  const actions = [
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

function widgetUpdates(stack) {
  stack.textContent = '';
  const src = state.sources['/api/update/status'];
  const error = state.sourceErr['/api/update/status'];
  const phase = src && src.phase || 'idle';
  /* "unavailable" means this copy cannot update itself at all (for example a copy run from source):
     say so in plain words and do not offer a button that can only fail. The server's own reason
     stays reachable under Technical details. */
  const unavailable = phase === 'unavailable';
  /* The outbound switch is the master over update checks: while it is off no check goes online, a
     pressed Check included. Say so calmly, with the way to the switch, instead of a failure. */
  const onlineOff = !unavailable && !!src && src.online_access === false;
  const offText = T('update.online_off', 'Online access is off, so VOOL does not check for updates.');
  /* Only a phase with nothing of its own to say (idle, up to date, a check) is replaced by the off line; a ready,
     ready-to-restart, rolled-back or failed update keeps its own message, with the off line under it. */
  const quiet = phase === 'idle' || phase === 'up_to_date' || phase === 'checking';
  const message = onlineOff && quiet ? offText
    : phase === 'up_to_date' ? T('update.up_to_date', 'Up to date')
    : unavailable ? T('settings.updates.unavailable', 'Automatic updates are not available for this copy of VOOL.')
    : error || src && src.message || T('settings.widget.loading', 'Loading…');
  const status = el('div', 'subtle', message); status.setAttribute('role', 'status');
  status.dataset.testid = 'settings-update-status'; stack.appendChild(status);
  let onlineLink = null;
  const showOnlineSwitch = () => {
    if (onlineLink) return;
    if (!quiet) {
      const note = el('div', 'subtle', offText); note.dataset.testid = 'settings-update-online-note';
      stack.appendChild(note);
    }
    onlineLink = el('button', 'btn', T('online_access.open_setting', 'Open \u201cGo online on its own\u201d'));
    onlineLink.type = 'button'; onlineLink.dataset.testid = 'settings-update-online-setting';
    onlineLink.addEventListener('click', () => { location.hash = '#permissions/automatic_checks'; });
    stack.appendChild(onlineLink);
  };
  if (onlineOff) showOnlineSwitch();
  const check = el('button', 'btn', T('update.action_check', 'Check for updates'));
  check.type = 'button'; check.dataset.testid = 'settings-update-check';
  check.disabled = unavailable || ['checking', 'downloading', 'verifying', 'preparing', 'backing_up', 'installing', 'restarting'].includes(phase);
  check.addEventListener('click', async () => {
    check.disabled = true;
    status.textContent = T('settings.updates.checking', 'Checking…');
    try {
      const response = await fetch('/api/update/check', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const result = await response.json().catch(() => ({}));
      if (result.reason === 'online_access_off' || result.error === 'online_access_off') {
        status.textContent = quiet ? offText : message;
        showOnlineSwitch(); check.disabled = false; return;
      }
      if (!response.ok || result.ok === false) throw new Error(T('settings.updates.check_failed', 'VOOL could not check for updates just now. Try again later.'));
      await readSource('/api/update/status');
      widgetUpdates(stack);
    } catch (e) { status.textContent = String(e.message || e); check.disabled = false; }
  });
  stack.appendChild(check);
  if (unavailable && src && (src.unavailable_plain || src.message)) {
    const more = document.createElement('details');
    more.appendChild(el('summary', 'subtle', T('settings.technical_details', 'Technical details')));
    more.appendChild(el('div', 'subtle', String(src.unavailable_plain || src.message)));
    stack.appendChild(more);
  }
  if (src && src.rollback_available) {
    const rollback = el('button', 'btn', T('update.action_rollback', 'Roll back to {version}').replace('{version}', src.rollback_version || ''));
    rollback.type = 'button'; rollback.dataset.testid = 'settings-update-rollback';
    rollback.disabled = check.disabled;
    rollback.addEventListener('click', async () => {
      rollback.disabled = true;
      try {
        const response = await fetch('/api/update/rollback', { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || result.message || 'Rollback could not start');
        await readSource('/api/update/status'); widgetUpdates(stack);
      } catch (e) { status.textContent = String(e.message || e); rollback.disabled = false; }
    });
    stack.appendChild(rollback);
    stack.appendChild(el('div', 'subtle', T('update.rollback_keeps_data', 'Keeps your current chats, memory, keys and settings')));
  }
}

function widgetBuild(stack) {
  const src = state.sources['/api/runtime/version'];
  const err = state.sourceErr['/api/runtime/version'];
  if (err) { stack.appendChild(el('div', 'empty', tfmt('settings.widget.unavailable', 'Unavailable — {error}', { error: err }))); return; }
  if (!src) { stack.appendChild(el('div', 'empty', T('settings.widget.loading', 'Loading…'))); return; }
  /* One readable line first; the full field list (what a problem report needs) is folded
     under Technical details and still copied whole by "Copy build info". */
  const version = src.release_version || src.app_version || '';
  if (version) stack.appendChild(el('div', null, 'VOOL ' + version));
  const dl = el('dl', 'kv');
  const keys = Object.keys(src).filter(k => typeof src[k] !== 'object');
  keys.forEach(k => {
    dl.appendChild(el('dt', null, k.replace(/_/g, ' ')));
    dl.appendChild(el('dd', 'mono', String(src[k])));
  });
  if (!keys.length) dl.appendChild(el('dd', 'empty', 'No version details are available.'));
  const more = document.createElement('details');
  more.appendChild(el('summary', 'subtle', T('settings.technical_details', 'Technical details')));
  more.appendChild(dl);
  stack.appendChild(more);
  const bar = el('div', 'inline');
  const copy = el('button', 'btn', 'Copy build info'); copy.type = 'button';
  const st = el('span', 'subtle', '');
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(src, null, 2)); st.textContent = 'Copied.'; }
    catch (e) { st.textContent = 'Could not copy — select the values above instead.'; }
  });
  bar.appendChild(copy); bar.appendChild(st);
  stack.appendChild(bar);
  if (setupHiddenByChoice()) stack.appendChild(setupRestoreControl());
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
    body.appendChild(el('div', 'empty', T('settings.usage.reading', 'Reading usage…')));
    let data;
    try { data = await getJSON('/api/runtime/usage' + (active ? '?range=' + active : '')); }
    catch (e) { body.textContent = ''; body.appendChild(el('div', 'empty', T('settings.usage.unreadable', 'Usage could not be read.') + ' ' + e.message)); return; }
    body.textContent = '';
    const dl = el('dl', 'kv');
    const line = (label, bucket, extra) => {
      if (!bucket) return;
      dl.appendChild(el('dt', null, label));
      const t = (bucket.total_tokens || 0).toLocaleString();
      const n = bucket.responses || 0;
      dl.appendChild(el('dd', null, (n === 1 ? tfmt('settings.usage.tokens_one', '{tokens} tokens over 1 answer', { tokens: t })
        : tfmt('settings.usage.tokens_many', '{tokens} tokens over {count} answers', { tokens: t, count: n })) + (extra || '')));
    };
    line(T('settings.usage.free_local', 'Free, on this computer'), data.free_local);
    line(T('settings.usage.free_online', 'Free, online'), data.free_cloud);
    const paid = data.paid_cloud || {};
    const est = paid.usd_estimate != null ? Number(paid.usd_estimate) : null;
    const usd = est == null ? '' : (est > 0 && est < 0.01 ? ' · ' + T('settings.usage.under_cent', 'less than $0.01') : ' · ' + tfmt('settings.usage.about_usd', 'about ${amount}', { amount: est.toFixed(2) }));
    line(T('settings.usage.paid_online', 'Paid, online'), paid, usd);
    line(T('settings.usage.other', 'Other'), data.remote_unknown);
    dl.appendChild(el('dt', null, T('settings.usage.total', 'Total')));
    dl.appendChild(el('dd', null, (data.total_tokens || 0).toLocaleString() + ' tokens'));
    if (!data.total_tokens) body.appendChild(el('div', 'subtle', T('settings.usage.none_yet', 'Nothing used in this period yet.')));
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
  { id: 'search.web.', title: 'Web search', blurb: 'Used for live lookups. Built-in keyless search remains available.' },
  { id: 'security.', title: 'Security scans', blurb: 'Optional add-on checks. Scans use your provider allowance.' },
  { id: 'chain.data.', title: 'Blockchain data', blurb: 'Used for read-only chain lookups in any chat. Never used to sign or pay.' },
  { id: 'x.api.', title: 'X (Twitter)', blurb: 'Your X app keys, checked with a read-only request. VOOL does not post with them.' },
  { id: 'market.data.', title: 'Market data', blurb: 'Raises crypto price limits. Verified when saved; if the provider later refuses it, VOOL answers prices without it.' },
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
const KEY_GROUP_ORDER = ['models', 'search', 'security', 'chain'];
function keyFamilyOf(name) {
  /* A key saved for later lives in its own "Saved for later" list below; listing it here as well
     would show the person's own key twice, once as if it were VOOL's. */
  if (String(name || '').startsWith('quarantine.')) return 'quarantine';
  if (String(name || '').startsWith('security.') && name !== 'security.eyebrow') return 'managed';
  const hit = KEY_FAMILIES.find(f => String(name || '').startsWith(f.id));
  return hit ? hit.id : 'managed';
}
function isOperatorKey(name) { const f = keyFamilyOf(name); return f !== 'managed' && f !== 'quarantine'; }

function widgetKeys(stack) {
  const src = state.sources['/api/settings/credentials'];
  const err = state.sourceErr['/api/settings/credentials'];
  const list = el('div', 'stack');
  if (err) list.appendChild(el('div', 'empty', tfmt('settings.widget.unavailable', 'Unavailable — {error}', { error: err })));
  else if (!src) list.appendChild(el('div', 'empty', 'Loading…'));
  else {
    const creds = src.credentials || src.items || [];
    const yours = creds.filter(c => isOperatorKey(c.name));
    if (!yours.length) list.appendChild(el('div', 'empty', T('keys.empty_plain', 'No keys yet. Without a key VOOL can only use a model installed on this computer.')));
    const groups = [...KEY_FAMILIES.map(f => f.id), 'managed'];
    groups.forEach(fam => {
      const rows = creds.filter(c => keyFamilyOf(c.name) === fam);
      if (!rows.length) return;
      const meta = KEY_FAMILIES.find(f => f.id === fam);
      /* Keys VOOL made for itself stay listed (nothing is hidden) but folded away under their own
         heading, with nothing to do: they are not the person's keys and are not removable here. */
      let into = list;
      if (meta) {
        list.appendChild(el('div', 'row-label', meta.title));
        list.appendChild(el('div', 'subtle', meta.blurb));
      } else {
        const fold = el('details', 'key-system-keys');
        fold.appendChild(el('summary', 'subtle', 'Keys VOOL made for itself (' + rows.length + ') — nothing to do'));
        fold.appendChild(el('div', 'subtle', 'VOOL created these on its own to lock its storage on this computer. They are not yours to manage and cannot be removed here, because removing one would make what it locked unreadable.'));
        list.appendChild(fold);
        into = fold;
      }
      rows.forEach(c => {
        const rowEl = el('div', 'inline');
        const pill = el('span', isOperatorKey(c.name) ? 'pill on' : 'pill');
        pill.appendChild(el('span', 'dot'));
        pill.appendChild(el('span', null, c.label || PROVIDER_LABELS[String(c.name).slice(keyFamilyOf(c.name).length)] || c.name));
        rowEl.appendChild(pill);
        if (!isOperatorKey(c.name)) {
          rowEl.appendChild(el('span', 'pill', 'system'));
          into.appendChild(rowEl);
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
          testBtn.disabled = true; st.textContent = tfmt('keys.asking_test', 'Testing — asking {label} whether it accepts this key…', { label: providerLabel });
          try {
            if (fam === 'security.' && !confirm(T('keys.eyebrow_confirm', 'Check Eyebrow with one version request? No add-on is scanned.'))) { testBtn.disabled = false; st.textContent = ''; return; }
            const url = fam === 'security.' ? '/api/addons' : fam === 'search.web.' ? '/api/search/test' : fam === 'chain.data.' ? '/api/chain/test' : fam === 'x.api.' ? '/api/x/setup' : '/api/cloud/test';
            const payload = fam === 'security.' ? {action:'test_key'} : fam === 'x.api.' ? {action:'test'} : {provider:providerId};
            const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            const j = await r.json().catch(() => ({}));
            st.textContent = fam === 'security.' ? (r.ok && j.ok ? j.message : tfmt('keys.check_failed', 'Check failed — {message}', { message: (j.message || j.error || r.status) })) : keyTestMessage(providerLabel, j);
          } catch (e) { st.textContent = tfmt('keys.test_failed', 'Test failed: {error}', { error: (e && e.message ? e.message : e) }); }
          testBtn.disabled = false;
        });
        const rm = el('button', 'btn danger', T('keys.remove', 'Remove')); rm.type = 'button';
        rm.addEventListener('click', async () => {
          if (!confirm(tfmt('keys.remove_confirm', 'Remove the stored key “{name}”?\n\nIt is deleted from this machine. Nothing is sent anywhere. VOOL falls back to running without it.', { name: (c.label || c.name) }))) return;
          rm.disabled = true; st.textContent = T('keys.removing', 'Removing…');
          try {
            const r = await fetch(fam === 'security.' ? '/api/addons' : fam === 'x.api.' ? '/api/x/setup' : '/api/settings/credentials', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fam === 'security.' ? {action:'remove_key'} : fam === 'x.api.' ? {action:'remove'} : { name: c.name, delete: true }) });
            const j = await r.json().catch(() => null);
            if (!r.ok || (j && j.error)) throw new Error((j && j.error) || ('HTTP ' + r.status));
            await readSource('/api/settings/credentials');
            renderPane();
          } catch (e) { rm.disabled = false; st.textContent = tfmt('keys.not_removed', 'Not removed — {error}', { error: e.message }); }
        });
        /* A market-data key was verified when it was saved; its use is the check after that (a refusal
           drops back to keyless prices), so the row offers Remove only. */
        if (fam !== 'market.data.') rowEl.appendChild(testBtn);
        rowEl.appendChild(rm); rowEl.appendChild(st);
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
  const form = el('div', 'inline key-entry-form');
  const prov = el('select', 'inp key-provider-picker'); prov.setAttribute('aria-label', T('keys.provider_aria', 'Provider'));
  const auto = el('option', null, T('keys.auto_detect', 'Auto-detect from the key')); auto.value = ''; prov.appendChild(auto);
  /* The two catalogues name their id field differently -- cloud rows carry `id`
     (core/web/api/service.py), search rows carry `provider` (core/search_connection_state.py
     connection_rows). Reading only one of them drops that whole half of the list silently, with
     no error to notice. The optgroup carries a language-independent family marker: the logic
     below matches on that marker, never on the displayed (translated) label. */
  const addGroup = (family, labelKey, labelFallback, items) => {
    if (!items || !items.length) return;
    const g = document.createElement('optgroup'); g.label = T(labelKey, labelFallback); g.dataset.family = family;
    items.forEach(pr => {
      const value = pr.id || pr.provider || '';
      if (!value) return;
      const o = el('option', null, pr.label || value);
      o.value = value;
      g.appendChild(o);
    });
    if (!g.children.length) return;
    /* Fixed order whatever arrives first: most people open API Keys for a model key, and the two
       catalogues below arrive after the two local lists. */
    const rank = (f) => { const i = KEY_GROUP_ORDER.indexOf(f); return i < 0 ? KEY_GROUP_ORDER.length : i; };
    const after = [...prov.querySelectorAll('optgroup')].find(x => rank(x.dataset.family) > rank(family));
    prov.insertBefore(g, after || null);
  };
  addGroup('security', 'keys.group_security', 'Security scans', KEY_PROVIDER_GUIDE.find(g => g.id === 'security').providers);
  addGroup('chain', 'keys.group_chain', 'Blockchain data', (KEY_PROVIDER_GUIDE.find(g => g.id === 'chain') || {}).providers);
  addGroup('market', 'keys.group_market', 'Market data', (KEY_PROVIDER_GUIDE.find(g => g.id === 'market') || { providers: [] }).providers);
  getJSON('/api/cloud/providers')
    .then(d => {
      (d.providers || d.items || []).forEach(pr => { if (pr && pr.id) { PROVIDER_META[pr.id] = pr; if (pr.label) PROVIDER_LABELS[pr.id] = pr.label; } });
      addGroup('models', 'keys.group_models', 'Model providers', d.providers || d.items || []);
      syncBaseUrl();
      syncOrigin();
    })
    .catch(() => {});
  getJSON('/api/search/providers')
    .then(d => {
      const rows = d.providers || d.items || [];
      addGroup('search', 'keys.group_search', 'Web search', rows);

    })
    .catch(() => {});

  /* The custom OpenAI-compatible endpoint needs its address as well as a key. The field exists only
     while that provider is selected; the authority (set_credentials_authority) refuses a custom key
     without a safe base URL -- https to any host, or plain http only to a loopback host -- so a key is
     never sent in clear, and its reason is shown here verbatim rather than pre-empted. */
  const baseUrl = document.createElement('input');
  baseUrl.type = 'url'; baseUrl.className = 'inp key-base-url'; baseUrl.hidden = true;
  baseUrl.autocomplete = 'off'; baseUrl.spellcheck = false; baseUrl.setAttribute('aria-label', T('keys.base_url_aria', 'Custom endpoint base URL'));
  const baseUrlHint = el('div', 'subtle key-base-url-hint',
    T('keys.base_url_hint', 'Custom endpoint: the OpenAI-compatible base URL, ending in /v1 — https://host/v1, or http://127.0.0.1:port/v1 for a server on this machine. Plain http to any other host is refused so the key never travels in clear.'));
  baseUrlHint.hidden = true;
  const needsBaseUrl = () => { const v = prov.value.trim(); const meta = PROVIDER_META[v]; return meta ? !!meta.user_base_url : v === 'custom'; };
  /* Where a custom endpoint's key goes. An endpoint the operator typed in documents nothing about its
     auth, so they say: a Bearer token (the OpenAI convention), the bare key in a header they name
     (Azure's api-key, a proxy's x-api-key), or no key at all for an open server on their own machine.
     The server validates the header name and commits the choice with the endpoint and key. */
  const keyAuth = document.createElement('select');
  keyAuth.className = 'inp key-auth'; keyAuth.hidden = true; keyAuth.setAttribute('aria-label', T('keys.auth_aria', 'Where the key goes'));
  [['bearer', T('keys.auth_bearer', 'Key as Bearer token (most APIs)')],
   ['header', T('keys.auth_header', 'Key in a named header')],
   ['none', T('keys.auth_none', 'No key (open server)')]].forEach(([v, label]) => { const o = el('option', null, label); o.value = v; keyAuth.appendChild(o); });
  const keyHeader = document.createElement('input');
  keyHeader.type = 'text'; keyHeader.className = 'inp key-auth-header'; keyHeader.hidden = true; keyHeader.autocomplete = 'off'; keyHeader.spellcheck = false;
  keyHeader.placeholder = T('keys.auth_header_placeholder', 'header name, e.g. api-key'); keyHeader.setAttribute('aria-label', T('keys.auth_header_aria', 'Header that carries the key'));
  const authValue = () => keyAuth.value === 'none' ? 'none' : keyAuth.value === 'header' ? 'header:' + keyHeader.value.trim() : 'bearer';
  const syncAuth = () => {
    const show = needsBaseUrl();
    keyAuth.hidden = !show;
    keyHeader.hidden = !show || keyAuth.value !== 'header';
    const open = show && keyAuth.value === 'none';
    key.disabled = open;
    if (open) key.value = '';
    key.placeholder = open ? T('keys.no_key_placeholder', 'no key needed') : '';
  };
  keyAuth.addEventListener('change', syncAuth);
  const syncBaseUrl = () => { const show = needsBaseUrl(); baseUrl.hidden = !show; baseUrlHint.hidden = !show; syncAuth(); };
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
  origin.autocomplete = 'off'; origin.spellcheck = false; origin.placeholder = T('keys.origin_placeholder', 'optional — only an origin you chose yourself');
  origin.setAttribute('aria-label', T('keys.origin_aria', 'Provider origin'));
  const originHint = el('div', 'subtle key-origin-hint',
    tfmt('keys.origin_hint', 'UsePod puts the token in the URL path. Paste the token alone, or the whole proxy URL — it is split before storage and the token is never shown again. The optional origin names a different endpoint you chose yourself (https to any host, or http to a loopback host); leaving it empty keeps {default}. A URL you pasted never becomes the origin by itself: a paste that disagrees with the origin chosen here is refused, and the token is never sent to an origin you did not pick.', { default: USEPOD_ORIGIN_DEFAULT }));
  originHint.hidden = true;
  const isPathTokenProvider = () => { const meta = PROVIDER_META[prov.value.trim()]; return !!(meta && meta.auth_placement === 'url_path_token'); };
  const syncOrigin = () => {
    const show = isPathTokenProvider();
    origin.hidden = !show; originHint.hidden = !show;
    key.setAttribute('aria-label', show ? T('keys.token_aria', 'UsePod token or proxy URL') : T('keys.key_aria', 'API key'));
    key.title = show ? T('keys.token_title', 'Paste the UsePod token, or the whole proxy URL containing it') : T('keys.key_title', 'Paste the API key here');
  };
  prov.addEventListener('change', syncOrigin);

  const key = document.createElement('input');
  key.type = 'password'; key.className = 'inp'; key.setAttribute('aria-label', T('keys.key_aria', 'API key')); key.title = T('keys.key_title', 'Paste the API key here');
  key.autocomplete = 'off'; key.spellcheck = false;
  /* Show/Hide: reveal what is being typed, on request only. The stored value is never shown again. */
  const reveal = el('button', 'btn key-reveal', T('keys.show', 'Show')); reveal.type = 'button';
  reveal.setAttribute('aria-pressed', 'false'); reveal.title = T('keys.reveal_title', 'Show or hide the key you are typing');
  reveal.addEventListener('click', () => {
    const shown = key.type === 'text';
    key.type = shown ? 'password' : 'text';
    reveal.textContent = shown ? T('keys.show', 'Show') : T('keys.hide', 'Hide');
    reveal.setAttribute('aria-pressed', shown ? 'false' : 'true');
  });
  const save = el('button', 'btn primary key-save', T('keys.save', 'Save key')); save.type = 'button';
  /* Save for later: the EXPLICIT unverified path. The key is stored sealed in a quarantine
     slot nothing reads — no lane, no probe, no search uses it — and shows as unverified until
     a later verification promotes it. Offered next to the verified save so the honest path
     stays the obvious one. */
  const later = el('button', 'btn key-save-later', T('keys.save_later', 'Save for later (unverified)')); later.type = 'button';
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
  const selectedGroup = () => { const o = prov.selectedOptions && prov.selectedOptions[0]; const g = o && o.parentElement; return (g && g.tagName === 'OPTGROUP' && g.dataset.family) || ''; };
  const selectedLabel = () => { const o = prov.selectedOptions && prov.selectedOptions[0]; return o && prov.value ? o.textContent : ''; };
  const failText = (res) => res.j.detail || res.j.error || ('HTTP ' + res.status);
  const openEndpoint = () => needsBaseUrl() && keyAuth.value === 'none';
  /* The explicit "use anyway" for a custom endpoint that answered but cannot confirm a key. Offered
     only when the server says this session's outcome allows it; it stores the pair as unconfirmed and
     the first message is what judges the key. */
  const useAnyway = el('button', 'btn key-use-anyway', T('keys.use_anyway', 'Use this endpoint anyway')); useAnyway.type = 'button'; useAnyway.hidden = true;
  let assertableSession = '';
  useAnyway.addEventListener('click', async () => {
    if (!assertableSession) return;
    useAnyway.disabled = true;
    try {
      const done = await intakePost('/api/intake/complete', { session_id: assertableSession, persist: 'asserted' });
      assertableSession = ''; useAnyway.hidden = true;
      if (!done.ok) { setSt('failed', tfmt('keys.not_stored', 'Not stored — {error}', { error: failText(done) })); return; }
      key.value = '';
      const note = T('keys.stored_unconfirmed', 'Stored sealed on this machine as an UNCONFIRMED custom endpoint. It is now the active model provider; your first message will show whether the key works.');
      setSt('saved', note);
      widgetCache.keySaveNote = note;
      await readSource('/api/settings/credentials');
      renderPane();
    } catch (e) { setSt('failed', tfmt('keys.not_stored', 'Not stored — {error}', { error: (e && e.message ? e.message : e) })); }
    finally { useAnyway.disabled = false; }
  });
  const saveVerified = async () => {
    assertableSession = ''; useAnyway.hidden = true;
    const v = openEndpoint() ? 'no-key' : key.value;
    if (!v.trim()) { setSt('failed', T('keys.paste_first', 'Paste a key first.')); return; }
    if (needsBaseUrl() && keyAuth.value === 'header' && !keyHeader.value.trim()) { setSt('failed', T('keys.auth_header_first', 'Name the header that carries the key (for example api-key). Nothing was sent anywhere.')); keyHeader.focus(); return; }
    save.disabled = true;
    try {
      setSt('saving', T('keys.recognising', 'Recognising the key on this machine…'));
      const begun = await intakePost('/api/intake/begin', {});
      if (!begun.ok) throw new Error(failText(begun));
      const sid = begun.j.session_id;
      const classified = await intakePost('/api/intake/classify', { session_id: sid, value: v });
      if (!classified.ok) throw new Error(failText(classified));
      const candidates = classified.j.candidates || [];
      const picked = prov.value.trim();
      const providerId = picked ? (selectedGroup() === 'search' ? 'search.' + picked : selectedGroup() === 'chain' ? 'chain.' + picked : selectedGroup() === 'market' ? 'market.' + picked : picked) : String(classified.j.suggestion || '');
      if (!providerId) {
        const names = candidates.map(c => c.label).filter(Boolean);
        const choices = names.length ? ' ' + tfmt('keys.could_be', 'It could be {names}.', { names: names.join(', ') }) : '';
        /* Choosing a service only helps when the key could belong to more than one; for something that is
           not a key at all, the advice is to copy it again. */
        const advice = names.length ? T('keys.choose_retry', 'Choose the service in the list, then press Save key again. Nothing was sent anywhere.')
          : T('keys.copy_again', 'API keys are one long word with no spaces. Copy it again from the provider\u2019s site and paste it here. Nothing was sent anywhere.');
        setSt('failed', (classified.j.reason || T('keys.cannot_tell', 'VOOL cannot tell which service this key is for.')) + choices + ' ' + advice);
        prov.focus();
        return;
      }
      if (!picked) { prov.value = providerId.replace(/^(search|chain|market)\./, ''); syncBaseUrl(); syncOrigin(); }
      const label = selectedLabel() || (candidates[0] && candidates[0].label) || providerId;
      const verifyBody = { session_id: sid, provider_id: providerId };
      if (needsBaseUrl()) {
        if (!baseUrl.value.trim()) { setSt('failed', T('keys.base_url_first', 'Not stored — enter the base URL of the endpoint first (for example https://host/v1). Nothing was sent anywhere.')); baseUrl.focus(); return; }
        verifyBody.base_url = baseUrl.value.trim();
        verifyBody.auth = authValue();
      }
      /* A path-token provider (UsePod): the origin field names an endpoint the operator chose. Left
         empty, the provider's documented origin applies. The paste (a token or a whole proxy URL) is
         split on this machine before anything is sent, and a URL naming another origin is refused. */
      if (isPathTokenProvider() && origin.value.trim()) verifyBody.base_url = origin.value.trim();
      setSt('saving', tfmt('keys.asking', 'Asking {label} once whether it accepts this key…', { label: label }));
      const verified = await intakePost('/api/intake/verify', verifyBody);
      if (!verified.ok) { setSt('failed', tfmt('keys.not_stored', 'Not stored — {error}', { error: failText(verified) })); return; }
      if (verified.j.outcome !== 'verified') {
        setSt('failed', verifyOutcomeMessage(label, verified.j));
        if (verified.j.can_use_unconfirmed) {
          assertableSession = sid; useAnyway.hidden = false;
          setSt('failed', verifyOutcomeMessage(label, verified.j) + ' ' + T('keys.use_anyway_hint', 'If this is your own server and you trust it, you can use it anyway; the first message will show whether the key works.'));
        }
        return;
      }
      setSt('saving', tfmt('keys.storing', '{label} accepted the key. Storing it sealed on this machine…', { label: label }));
      const done = await intakePost('/api/intake/complete', { session_id: sid });
      if (!done.ok) { setSt('failed', tfmt('keys.not_stored', 'Not stored — {error}', { error: failText(done) })); return; }
      key.value = '';                     /* the secret leaves the field the moment it is stored */
      origin.value = '';
      const use = done.j.kind === 'security_scan' ? T('keys.use_security', ' Ready for add-on checks; nothing has been scanned or enabled.') : done.j.kind === 'search_web' ? T('keys.use_search', ' VOOL uses it for live web lookups from now on.') : done.j.kind === 'chain_data' ? T('keys.use_chain', ' VOOL uses it for read-only chain lookups in any chat from now on. It never signs or pays.') : done.j.kind === 'market_data' ? T('keys.use_market', ' VOOL uses it for crypto prices from now on.') : T('keys.use_model', ' It is now the active model provider.');
      const credit = verified.j.account_state === 'exhausted' ? T('keys.credit_exhausted', ' Its credit limit is used up, so requests will fail until credit is added.') : '';
      /* A path-token binding names what the runtime now holds, never the secret: the origin the token
         is sent to and the one-way fingerprint its receipts carry. */
      const bound = done.j.origin ? tfmt('keys.bound_origin', ' Bound to {origin}', { origin: String(done.j.origin) }) + (done.j.credential_fingerprint ? tfmt('keys.fingerprint', ', fingerprint {fingerprint}', { fingerprint: done.j.credential_fingerprint }) : '') : '';
      const note = tfmt('keys.stored_sealed', 'Stored, sealed on this machine after {label} accepted the key.', { label: label }) + use + credit + bound;
      setSt('saved', note);
      widgetCache.keySaveNote = note;
      await readSource('/api/settings/credentials');
      renderPane();
    } catch (e) { setSt('failed', tfmt('keys.not_stored', 'Not stored — {error}', { error: (e && e.message ? e.message : e) })); }
    finally { save.disabled = false; }
  };
  save.addEventListener('click', saveVerified);
  /* Save-for-later runs the same local recognition and the same ONE provider selection, then
     stores WITHOUT asking anyone: nothing is sent anywhere. The key lands in quarantine. */
  later.addEventListener('click', async () => {
    const v = openEndpoint() ? 'no-key' : key.value;
    if (!v.trim()) { setSt('failed', T('keys.paste_first', 'Paste a key first.')); return; }
    later.disabled = true;
    try {
      setSt('saving', T('keys.recognising', 'Recognising the key on this machine…'));
      const begun = await intakePost('/api/intake/begin', {});
      if (!begun.ok) throw new Error(failText(begun));
      const sid = begun.j.session_id;
      const classified = await intakePost('/api/intake/classify', { session_id: sid, value: v });
      if (!classified.ok) throw new Error(failText(classified));
      const candidates = classified.j.candidates || [];
      const picked = prov.value.trim();
      const providerId = picked ? (selectedGroup() === 'search' ? 'search.' + picked : selectedGroup() === 'chain' ? 'chain.' + picked : selectedGroup() === 'market' ? 'market.' + picked : picked) : String(classified.j.suggestion || '');
      if (!providerId) {
        const names = candidates.map(c => c.label).filter(Boolean);
        setSt('failed', (classified.j.reason || T('keys.cannot_tell', 'VOOL cannot tell which service this key is for.'))
          + (names.length ? ' ' + T('keys.choose_service_first', 'Choose the service in the list first.') : '')
          + T('keys.nothing_sent_suffix', ' Nothing was sent anywhere.'));
        prov.focus();
        return;
      }
      if (!picked) { prov.value = providerId.replace(/^(search|chain|market)\./, ''); syncBaseUrl(); syncOrigin(); }
      const laterBody = { session_id: sid, provider_id: providerId };
      if (needsBaseUrl()) {
        if (!baseUrl.value.trim()) { setSt('failed', T('keys.base_url_first_short', 'Not stored — enter the base URL of the endpoint first. Nothing was sent anywhere.')); baseUrl.focus(); return; }
        laterBody.base_url = baseUrl.value.trim();
        laterBody.auth = authValue();
      }
      if (isPathTokenProvider() && origin.value.trim()) laterBody.base_url = origin.value.trim();
      /* preview pins the provider on the session without any request; complete stores quarantined */
      const seen = await intakePost('/api/intake/preview', laterBody);
      if (!seen.ok) throw new Error(failText(seen));
      const done = await intakePost('/api/intake/complete', { session_id: sid, persist: 'later' });
      if (!done.ok) throw new Error(failText(done));
      key.value = '';
      origin.value = '';
      const note = T('keys.stored_unverified', 'Stored sealed and UNVERIFIED for later. Nothing uses this key — not the model lane, not search — until you retry it from the list below and the provider accepts it.');
      setSt('saved', note);
      widgetCache.keySaveNote = note;
      await readSource('/api/settings/credentials');
      renderPane();
    } catch (e) { setSt('failed', tfmt('keys.not_stored', 'Not stored — {error}', { error: (e && e.message ? e.message : e) })); }
    finally { later.disabled = false; }
  });
  /* A retry that did not promote the key, in words: the key stays saved and unused either way. */
  function quarantineOutcome(label, outcome) {
    const p = { label: label };
    if (outcome === 'network_unavailable' || outcome === 'timeout') return tfmt('keys.quarantine_outcome.unreachable', 'VOOL could not reach {label}. Check your internet connection and try again. The key stays saved, unused.', p);
    if (outcome === 'invalid' || outcome === 'unauthorized') return tfmt('keys.quarantine_outcome.rejected', '{label} turned this key down. Check you copied all of it, or delete it.', p);
    if (outcome === 'exhausted') return tfmt('keys.quarantine_outcome.exhausted', '{label} says this key has no credit left. Add credit with {label}, then try again.', p);
    if (outcome === 'rate_limited' || outcome === 'provider_unavailable') return tfmt('keys.quarantine_outcome.busy', '{label} has too many requests right now. Try again in a little while. The key stays saved, unused.', p);
    return tfmt('keys.quarantine_outcome.unclear', '{label} did not give a clear answer, so the key is still not checked. It stays saved, unused.', p);
  }
  /* One sentence per verification outcome. The server keeps a rejected key apart from throttling, an
     outage, a timeout, a wrong address, a redirect and a non-API answer; so does each sentence, and none
     of them calls a key bad when the provider never judged it. Each state keeps its own catalog key so
     no translation can blur one outcome into another (a rejected key never becomes "FAILED", an
     unjudged key never becomes rejected). */
  function verifyOutcomeMessage(label, j) {
    const http = j.http_status ? ' (HTTP ' + j.http_status + (j.provider_error_code ? ', ' + j.provider_error_code : '') + ')' : '';
    const p = { label: label, http: http };
    switch (String(j.outcome || '')) {
      case 'invalid': return tfmt('keys.outcome.invalid', '{label} rejected this key{http}. Check that you copied all of it and that it is a {label} key. Nothing was stored.', p);
      case 'unauthorized': return tfmt('keys.outcome.unauthorized', '{label} refused this request{http}. The key may lack permission for it, or may not be valid. Nothing was stored.', p);
      case 'exhausted': return tfmt('keys.outcome.exhausted', '{label} reports no credit or quota left for this key{http}. Add credit, then save again. Nothing was stored.', p);
      case 'gateway_blocked': return tfmt('keys.outcome.gateway_blocked', '{label} blocked this check before looking at the key{http}. The key was not judged. Try again later or from another network. Nothing was stored.', p);
      case 'rate_limited': return tfmt('keys.outcome.rate_limited', '{label} is limiting requests right now; {retry}. The key was not judged. Nothing was stored.',
        { label: label, retry: j.retry_after_s ? tfmt('keys.outcome.rate_limited.retry_secs', 'try again in {n} s', { n: Math.ceil(j.retry_after_s) }) : T('keys.outcome.rate_limited.retry_shortly', 'try again shortly') });
      case 'timeout': return tfmt('keys.outcome.timeout', '{label} did not answer in time. The key was not judged; try again. Nothing was stored.', p);
      case 'network_unavailable': return tfmt('keys.outcome.network_unavailable', 'Could not reach {label} from this machine. The key was not judged. Nothing was stored.', p);
      case 'provider_unavailable': return tfmt('keys.outcome.provider_unavailable', '{label} reported an outage{http}. The key was not judged; try again later. Nothing was stored.', p);
      case 'endpoint_not_found': return tfmt('keys.outcome.endpoint_not_found', 'No {label} API answered at that address{http}. Check the base URL. Nothing was stored.', p);
      case 'redirected': return tfmt('keys.outcome.redirected', 'The endpoint redirected to another site{origin}, so VOOL did not send the key there. Nothing was stored.',
        { origin: j.redirect_origin ? ' (' + j.redirect_origin + ')' : '' });
      case 'malformed_response': return T('keys.outcome.malformed_response', 'That address answered with a web page, not an API response (for example a network sign-in page). The key was not judged. Nothing was stored.');
      case 'unexpected_schema': return tfmt('keys.outcome.unexpected_schema', 'That address answered, but not the way the {label} API does. The key was not judged. Nothing was stored.', p);
      case 'public_endpoint': return T('keys.outcome.public_endpoint', 'That address answers without any key, so it cannot confirm this one; VOOL did not send the key. If the service has its own entry in the list, choose that instead. Nothing was stored.');
      case 'refused': return tfmt('keys.outcome.refused', 'VOOL did not send the key: {detail}. Nothing was stored.',
        { detail: j.detail || T('keys.outcome.refused.detail', 'the request was refused') });
      default: return tfmt('keys.outcome.unexpected', '{label} gave an unexpected answer{http}. The key was not judged. Nothing was stored.', p);
    }
  }
  /* The Test button's answer for a stored key, in the same terms. */
  function keyTestMessage(label, j) {
    const state = String(j.state || '');
    if (state === 'ok' && /open endpoint/.test(String(j.detail || ''))) return tfmt('keys.test.ok_open', 'Connection verified — {label} answers without a key, as you chose.', { label: label });
    if (state === 'ok') return tfmt('keys.test.ok', 'Connection verified — {label} accepted your key.', { label: label });
    if (state === 'untested' && j.checked_at) return tfmt('keys.test.unconfirmable', '{label} is reachable, but it lists its models without a key, so it cannot confirm yours. Your first message will.', { label: label });
    const reason = state === 'failed' ? String(j.detail || '') : state;
    const http = j.http_status ? ' (HTTP ' + j.http_status + ')' : '';
    const p = { label: label, http: http };
    // A 401 is the provider saying it did not accept the key; only a plain 403 leaves the key unjudged.
    if (reason === 'unauthorized' && Number(j.http_status) === 401) return tfmt('keys.test.invalid', '{label} rejected the key{http}. Check it again, or replace it.', p);
    if (reason === 'unauthorized') return tfmt('keys.test.unauthorized', '{label} refused this request{http}. The key may lack permission for it, or may not be valid.', p);
    if (reason === 'gateway_blocked') return tfmt('keys.test.gateway_blocked', '{label} blocked this check before looking at the key{http}. The key was not judged.', p);
    if (reason === 'no_key') return tfmt('keys.test.no_key', 'No key is stored for {label}.', p);
    if (reason === 'rate_limited') return tfmt('keys.test.rate_limited', '{label} is limiting requests right now{http}. The key was not judged; try again shortly.', p);
    if (reason === 'quota_exhausted' || reason === 'exhausted') return tfmt('keys.test.exhausted', '{label} reports no credit or quota left for this key{http}.', p);
    if (reason === 'redirected') return tfmt('keys.test.redirected', '{label} answered with a redirect to another site; VOOL did not send the key there.', p);
    if (reason === 'malformed_response' || reason === 'unexpected_schema') return tfmt('keys.test.schema', 'The endpoint answered, but not the way the {label} API does{http}. The key was not judged.', p);
    if (reason === 'endpoint_not_found') return tfmt('keys.test.endpoint_not_found', 'No {label} API answered at the configured address{http}. Check the base URL.', p);
    if (reason === 'provider_unavailable') return tfmt('keys.test.provider_unavailable', '{label} reported an outage{http}. The key was not judged.', p);
    if (reason === 'timeout') return tfmt('keys.test.timeout', '{label} did not answer in time. The key was not judged.', p);
    if (reason === 'refused') return tfmt('keys.test.refused', 'This runtime is not permitted to reach {label} right now{detail}. The key was not used.',
      { label: label, detail: j.detail ? tfmt('keys.test.refused_detail', ' ({detail})', { detail: j.detail }) : '' });
    if (reason === 'unreachable' || reason === 'network_unavailable') return tfmt('keys.test.unreachable', 'Could not reach {label}; the key itself was not judged.', p);
    return tfmt('keys.test.unconfirmed', 'Could not confirm the key with {label}; the key itself was not judged{detail}.',
      { label: label, detail: j.detail ? tfmt('keys.test.refused_detail', ' ({detail})', { detail: j.detail }) : '' });
  }
  /* Guided setup for a chain-data provider: the steps to get a working key (Alchemy needs its chains
     turned on), shown in a dialog on request. Presentation only; nothing is sent anywhere. */
  const steps = el('button', 'btn key-setup-steps', T('keyguide.setup_steps', 'Setup steps')); steps.type = 'button'; steps.hidden = true;
  const syncSteps = () => { steps.hidden = selectedGroup() !== 'chain'; };
  prov.addEventListener('change', syncSteps);
  steps.addEventListener('click', () => {
    const row = ((KEY_PROVIDER_GUIDE.find(g => g.id === 'chain') || {}).providers || []).find(r => r.provider === prov.value.trim());
    if (row) openKeySetupDialog(row);
  });
  form.appendChild(prov); form.appendChild(steps); form.appendChild(baseUrl); form.appendChild(keyAuth); form.appendChild(keyHeader); form.appendChild(origin); form.appendChild(el('span', 'subtle', T('keys.key_label', 'Key'))); form.appendChild(key); form.appendChild(reveal); form.appendChild(save); form.appendChild(later); form.appendChild(useAnyway);
  stack.appendChild(form);
  stack.appendChild(baseUrlHint);
  stack.appendChild(originHint);
  stack.appendChild(st);
  /* The two save buttons do different things, and the note says both (it used to say "only a verified
     key is stored" right under "Save for later (unverified)"). */
  stack.appendChild(el('div', 'subtle', T('keys.save_note', 'Leave the provider on Auto-detect and VOOL recognises most keys by their start, or asks you to choose. Save key checks the key with that one service first and then uses it. Save for later keeps the key locked away and unused until you check it from the list below. A saved key is never shown again.')));
  stack.appendChild(el('div', 'subtle', T('keys.checks_note', 'Checks use one selected provider request. Search checks may use credits; Eyebrow checks its version without scanning an add-on.')));
  /* X has more than one key, so it gets a guided setup instead of the single paste field. */
  const xRow = el('div', 'inline x-setup-entry');
  xRow.appendChild(el('span', 'row-label', T('xsetup.title', 'X (Twitter)')));
  xRow.appendChild(el('span', 'subtle', T('xsetup.entry_note', 'X uses several keys. The guided setup says what to paste where and checks them with one read.')));
  const xBtn = el('button', 'btn x-setup-open', T('xsetup.open', 'Connect X')); xBtn.type = 'button';
  xBtn.addEventListener('click', () => openXSetupDialog(async () => { await readSource('/api/settings/credentials'); renderPane(); }));
  xRow.appendChild(xBtn);
  stack.appendChild(xRow);
  if ((state.sources['/api/settings/credentials']?.credentials || []).some(c => c.name === 'x.api.default')) {
    const xActivity = el('div', 'x-activity'); stack.appendChild(xActivity); widgetXActivity(xActivity);
  }
  /* Keys saved for later: unverified, sealed, and unreachable by anything that runs. This is
     where a person SEES that status, retries one (a verified answer promotes it into use) or
     deletes it. Nothing here can touch a verified binding. */
  const quarantine = el('div', 'stack');
  stack.appendChild(quarantine);
  const drawQuarantine = async () => {
    quarantine.textContent = '';
    let q;
    try { q = await getJSON('/api/intake/quarantine/list'); }
    catch (e) { quarantine.appendChild(el('div', 'subtle', tfmt('keys.quarantine_unavailable', 'Saved-for-later list unavailable — {error}', { error: (e && e.message ? e.message : e) }))); return; }
    const rows = (q && (q.quarantined || (q.data && q.data.quarantined))) || [];
    if (!rows.length) return;
    quarantine.appendChild(el('div', 'row-label', T('keys.quarantine_title', 'Saved for later (unverified)')));
    quarantine.appendChild(el('div', 'subtle', T('keys.quarantine_help', 'These keys are sealed on this machine and nothing uses them — not the model lane, not search. Retry asks the provider once; only its acceptance promotes a key into use.')));
    rows.forEach(row => {
      const line = el('div', 'inline');
      const pill = el('span', 'pill');
      pill.appendChild(el('span', null, row.provider_label || row.provider_id));
      line.appendChild(pill);
      const why = row.status === 'unverified_quarantined' ? 'not checked yet' : 'not in use';
      const savedAt = row.created_at ? new Date(row.created_at) : null;
      const qst = el('span', 'subtle quarantine-state', why + (savedAt && !isNaN(savedAt) ? ' · ' + tfmt('keys.saved_state', 'saved {date}', { date: savedAt.toLocaleString() }) : ''));
      const retry = el('button', 'btn key-quarantine-retry', T('keys.retry', 'Retry')); retry.type = 'button'; retry.dataset.provider = row.provider_id;
      retry.addEventListener('click', async () => {
        retry.disabled = true; qst.textContent = tfmt('keys.quarantine_retrying', 'Asking {label} once whether it accepts this key…', { label: (row.provider_label || row.provider_id) });
        try {
          const r = await fetch('/api/intake/quarantine/retry', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider_id: row.provider_id }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok || j.ok === false) throw new Error((j && (j.detail || j.error)) || ('HTTP ' + r.status));
          if (j.promoted) { qst.textContent = T('keys.quarantine_promoted', 'Verified and now in use — promoted out of quarantine.'); await drawQuarantine(); await readSource('/api/settings/credentials'); renderPane(); }
          else qst.textContent = quarantineOutcome(row.provider_label || row.provider_id, String(j.outcome || ''));
        } catch (e) { qst.textContent = tfmt('keys.quarantine_retry_failed', 'Retry failed — {error}', { error: (e && e.message ? e.message : e) }); retry.disabled = false; }
      });
      const del = el('button', 'btn danger key-quarantine-delete', T('keys.delete', 'Delete')); del.type = 'button'; del.dataset.provider = row.provider_id;
      del.addEventListener('click', async () => {
        if (!confirm(tfmt('keys.quarantine_delete_confirm', 'Delete the unverified key for {label} from this machine? Nothing is sent anywhere.', { label: (row.provider_label || row.provider_id) }))) return;
        del.disabled = true; qst.textContent = T('keys.deleting', 'Deleting…');
        try {
          const r = await fetch('/api/intake/quarantine/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider_id: row.provider_id }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok || j.ok === false) throw new Error((j && (j.detail || j.error)) || ('HTTP ' + r.status));
          await drawQuarantine(); await readSource('/api/settings/credentials'); renderPane();
        } catch (e) { qst.textContent = tfmt('keys.not_deleted', 'Not deleted — {error}', { error: (e && e.message ? e.message : e) }); del.disabled = false; }
      });
      line.appendChild(retry); line.appendChild(del); line.appendChild(qst);
      quarantine.appendChild(line);
    });
  };
  drawQuarantine();

}

/* Public setup links are local metadata, not verification destinations. The server resolves
   category/provider copy through the locale's catalog (keyguide.*); these widget strings
   resolve through the page's own T() so buttons and notes follow the selected language. */
const KEY_PROVIDER_GUIDE = __KEY_PROVIDER_GUIDE__;
/* The guided setup dialog for one provider row: numbered steps, the chains it covers, and the notes
   (what this release does not use, plan limits). `onAdd` selects the provider in the key form. */
function openKeySetupDialog(row, onAdd) {
  document.querySelector('dialog.key-setup-dialog')?.remove();
  const dlg = document.createElement('dialog'); dlg.className = 'key-setup-dialog';
  dlg.setAttribute('aria-label', tfmt('keyguide.setup_title', 'Set up {label}', { label: row.label }));
  dlg.appendChild(el('h3', null, tfmt('keyguide.setup_title', 'Set up {label}', { label: row.label })));
  if (row.chains && row.chains.length) dlg.appendChild(el('p', 'subtle', tfmt('keyguide.setup_chains', 'Chains: {chains}', { chains: row.chains.join(', ') })));
  const list = document.createElement('ol'); list.className = 'key-setup-steps-list';
  (row.steps || []).forEach(text => list.appendChild(el('li', null, text)));
  dlg.appendChild(list);
  (row.notes || []).forEach(text => dlg.appendChild(el('p', 'subtle', text)));
  const actions = el('div', 'inline');
  if (row.url) {
    const link = el('a', 'key-guide-link', T('keyguide.official_site', 'Official site ↗')); link.href = row.url;
    link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.appendChild(link);
  }
  if (onAdd) {
    const add = el('button', 'btn primary', T('keyguide.setup_add', 'Add key now')); add.type = 'button';
    add.addEventListener('click', () => { dlg.close(); onAdd(); }); actions.appendChild(add);
  }
  const close = el('button', 'btn', T('keyguide.setup_close', 'Close')); close.type = 'button';
  close.addEventListener('click', () => dlg.close()); actions.appendChild(close);
  dlg.appendChild(actions);
  dlg.addEventListener('close', () => dlg.remove());
  document.body.appendChild(dlg);
  if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
}
/* Guided X (Twitter) setup. Two ways in: the app's Bearer Token (public reads) or the four keys of
   the user's own X app (their account). Each is checked with one read-only X request before it is
   saved (core/x_setup.py); the account path shows which account X names and saves only on confirm.
   Nothing here posts to X, and no key is ever shown back. */
function openXSetupDialog(onSaved) {
  document.querySelector('dialog.x-setup-dialog')?.remove();
  const dlg = document.createElement('dialog'); dlg.className = 'key-setup-dialog x-setup-dialog';
  dlg.setAttribute('aria-label', T('xsetup.dialog_title', 'Connect X'));
  const body = el('div', 'stack');
  const note = el('p', 'subtle x-setup-state'); note.setAttribute('role', 'status');
  let pendingSession = '';
  const call = async (payload) => {
    const r = await fetch('/api/x/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await r.json().catch(() => ({}));
    return { ok: r.ok && j.ok !== false, j: j };
  };
  const secretField = (label) => {
    const wrap = el('label', 'stack');
    wrap.appendChild(el('span', null, label));
    const input = el('input', 'inp'); input.type = 'password'; input.autocomplete = 'off'; input.spellcheck = false;
    input.setAttribute('aria-label', label); wrap.appendChild(input);
    return { wrap: wrap, input: input };
  };
  const steps = (items) => { const ol = document.createElement('ol'); ol.className = 'key-setup-steps-list'; items.forEach(t => ol.appendChild(el('li', null, t))); return ol; };
  const choose = () => {
    body.replaceChildren(); note.textContent = '';
    body.appendChild(el('p', null, T('xsetup.choose', 'How would you like to connect?')));
    const pub = el('button', 'btn', T('xsetup.choose_public', 'Research public posts (Bearer Token)')); pub.type = 'button';
    pub.addEventListener('click', bearerStep);
    const own = el('button', 'btn', T('xsetup.choose_keys', 'I have my keys (my X account)')); own.type = 'button';
    own.addEventListener('click', keysStep);
    const row = el('div', 'inline'); row.appendChild(pub); row.appendChild(own); body.appendChild(row);
    body.appendChild(el('p', 'subtle', T('xsetup.approval_note', 'VOOL reads with these keys. With your own account keys it can also post, like, follow or send messages for you, but only after you approve each one here in Settings.')));
  };
  const bearerStep = () => {
    body.replaceChildren(); note.textContent = '';
    body.appendChild(el('h4', null, T('xsetup.public_title', 'Public posts · Bearer Token')));
    body.appendChild(steps([
      T('xsetup.public_step1', 'Open developer.x.com and sign in with your X account.'),
      T('xsetup.public_step2', 'Open your app → Keys and tokens.'),
      T('xsetup.public_step3', 'Under Bearer Token, choose Generate (or Regenerate) and copy it.'),
      T('xsetup.public_step4', 'Paste it below. VOOL asks X for one public profile to check it, then saves it.'),
    ]));
    const f = secretField(T('xsetup.bearer_label', 'Bearer Token')); body.appendChild(f.wrap);
    const save = el('button', 'btn primary x-setup-save-bearer', T('xsetup.check_save', 'Check and save')); save.type = 'button';
    save.addEventListener('click', async () => {
      save.disabled = true; note.textContent = T('xsetup.checking', 'Asking X once whether it accepts this…');
      try {
        const res = await call({ action: 'save_bearer', bearer_token: f.input.value });
        note.textContent = res.j.message || (res.ok ? '' : T('xsetup.failed', 'X setup failed. Nothing was saved.'));
        if (res.ok) { f.input.value = ''; if (onSaved) await onSaved(); }
      } catch (e) { note.textContent = T('xsetup.failed', 'X setup failed. Nothing was saved.'); }
      save.disabled = false;
    });
    const back = el('button', 'btn', T('xsetup.back', 'Back')); back.type = 'button'; back.addEventListener('click', choose);
    const row = el('div', 'inline'); row.appendChild(save); row.appendChild(back); body.appendChild(row);
  };
  const keysStep = () => {
    body.replaceChildren(); note.textContent = '';
    body.appendChild(el('h4', null, T('xsetup.keys_title', 'My X account · four keys')));
    body.appendChild(el('p', 'subtle', T('xsetup.keys_where', 'All four are on developer.x.com → your app → Keys and tokens.')));
    body.appendChild(el('div', 'row-label', T('xsetup.step1', 'Step 1 · Consumer keys')));
    body.appendChild(el('p', 'subtle', T('xsetup.step1_help', 'Under Consumer Keys, choose Regenerate if you no longer have them, then copy the API Key and the API Key Secret.')));
    const ck = secretField(T('xsetup.consumer_key', 'Consumer Key (API Key)')); const cs = secretField(T('xsetup.consumer_secret', 'Consumer Key Secret (API Key Secret)'));
    body.appendChild(ck.wrap); body.appendChild(cs.wrap);
    body.appendChild(el('div', 'row-label', T('xsetup.step2', 'Step 2 · Access tokens')));
    body.appendChild(el('p', 'subtle', T('xsetup.step2_help_write', 'Under Access Token and Secret, choose Generate. They belong to the X account you are signed in with. To let VOOL post for you (only after you approve), first set your app\'s User authentication settings to Read and write and Direct message, then generate the tokens.')));
    const at = secretField(T('xsetup.access_token', 'Access Token')); const as = secretField(T('xsetup.access_secret', 'Access Token Secret'));
    body.appendChild(at.wrap); body.appendChild(as.wrap);
    const confirmRow = el('div', 'inline'); confirmRow.hidden = true;
    const check = el('button', 'btn primary x-setup-check', T('xsetup.check_account', 'Check my X account')); check.type = 'button';
    check.addEventListener('click', async () => {
      check.disabled = true; confirmRow.hidden = true; note.textContent = T('xsetup.checking', 'Asking X once whether it accepts this…');
      try {
        const res = await call({ action: 'keys_check', credentials: { api_key: ck.input.value, api_secret: cs.input.value, access_token: at.input.value, access_token_secret: as.input.value } });
        note.textContent = res.j.message || (res.ok ? '' : T('xsetup.failed', 'X setup failed. Nothing was saved.'));
        if (res.ok && res.j.session_id) {
          pendingSession = res.j.session_id;
          [ck, cs, at, as].forEach(f => { f.input.value = ''; });
          confirmRow.hidden = false;
        }
      } catch (e) { note.textContent = T('xsetup.failed', 'X setup failed. Nothing was saved.'); }
      check.disabled = false;
    });
    body.appendChild(el('div', 'row-label', T('xsetup.step3', 'Step 3 · Confirm')));
    const keep = el('button', 'btn primary x-setup-confirm', T('xsetup.save_account', 'Save this X account')); keep.type = 'button';
    keep.addEventListener('click', async () => {
      keep.disabled = true;
      try {
        const res = await call({ action: 'keys_save', session_id: pendingSession });
        note.textContent = res.j.message || (res.ok ? '' : T('xsetup.failed', 'X setup failed. Nothing was saved.'));
        if (res.ok) { pendingSession = ''; confirmRow.hidden = true; if (onSaved) await onSaved(); }
      } catch (e) { note.textContent = T('xsetup.failed', 'X setup failed. Nothing was saved.'); }
      keep.disabled = false;
    });
    const drop = el('button', 'btn', T('xsetup.cancel', 'Cancel')); drop.type = 'button';
    drop.addEventListener('click', async () => { if (pendingSession) await call({ action: 'keys_cancel', session_id: pendingSession }).catch(() => null); pendingSession = ''; confirmRow.hidden = true; note.textContent = T('xsetup.cancelled', 'Cancelled. Nothing was saved.'); });
    confirmRow.appendChild(keep); confirmRow.appendChild(drop);
    const back = el('button', 'btn', T('xsetup.back', 'Back')); back.type = 'button'; back.addEventListener('click', choose);
    const row = el('div', 'inline'); row.appendChild(check); row.appendChild(back);
    body.appendChild(row); body.appendChild(confirmRow);
  };
  dlg.appendChild(el('h3', null, T('xsetup.dialog_title', 'Connect X')));
  dlg.appendChild(body); dlg.appendChild(note);
  const close = el('button', 'btn', T('keyguide.setup_close', 'Close')); close.type = 'button';
  close.addEventListener('click', () => dlg.close());
  const actions = el('div', 'inline'); actions.appendChild(close); dlg.appendChild(actions);
  dlg.addEventListener('close', () => { if (pendingSession) call({ action: 'keys_cancel', session_id: pendingSession }).catch(() => null); dlg.remove(); });
  document.body.appendChild(dlg);
  choose();
  if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
}
/* Decorative line icons for the provider categories: generic shapes only (no provider marks),
   drawn in the muted accent so the list reads livelier without adding colour. Unknown ids get a
   plain dot so a new category still lines up. */
const KEY_GUIDE_ICONS = {
  models: ['M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z', 'M18.5 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z'],
  search: ['M10.5 4a6.5 6.5 0 1 1 0 13a6.5 6.5 0 0 1 0-13z', 'M15.3 15.3L20 20'],
  security: ['M12 3l7 3v5.5c0 4.3-2.9 7.9-7 9.5-4.1-1.6-7-5.2-7-9.5V6z', 'M9 12l2.2 2.2L15.2 10'],
  chain: ['M4 4h7v7H4z', 'M13 13h7v7h-7z', 'M11 7.5h3.5a2 2 0 0 1 2 2V13'],
};
function keyGuideIcon(id) {
  const NS = 'http://www.w3.org/2000/svg';
  const wrap = el('span', 'key-guide-ico'); wrap.setAttribute('aria-hidden', 'true');
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6'); svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('focusable', 'false');
  (KEY_GUIDE_ICONS[id] || ['M12 10.5a1.5 1.5 0 1 1 0 3a1.5 1.5 0 0 1 0-3z']).forEach(d => {
    const path = document.createElementNS(NS, 'path'); path.setAttribute('d', d); svg.appendChild(path);
  });
  wrap.appendChild(svg); return wrap;
}
/* X usage, limits and approvals (core/x_actions.py, core/x_usage.py). Anything VOOL would post,
   reply, like, repost or follow as the user waits here; "Approve and send" is the user's approval
   and the only thing that sends it. Usage is counted on this machine; X charges it to the user's
   own X developer account. */
async function xActivityCall(payload) {
  const r = await fetch('/api/x/activity', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, j: j || {} };
}
const X_STATE_WORDS = { sent: ['xact.state_sent', 'Sent'], declined: ['xact.state_declined', 'Declined'], failed: ['xact.state_failed', 'Not sent'],
  expired: ['xact.state_expired', 'Expired'], unknown: ['xact.state_unknown', 'Unclear if it was sent'], sending: ['xact.state_sending', 'Sending'] };
function xOpenLink(url, summary) {
  // Only links on X itself open from here.
  if (!/^https:\/\/x\.com\/[A-Za-z0-9_\/]+$/.test(String(url || ''))) return document.createTextNode('');
  const link = el('a', 'key-guide-link', T('xact.open_on_x', 'Open on X ↗')); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
  link.setAttribute('aria-label', tfmt('xact.open_aria', 'Open on X: {summary}', { summary: summary }));
  return link;
}
function widgetXActivity(host) {
  host.replaceChildren();
  const draw = async (message) => {
    const note = el('p', 'subtle x-activity-note'); note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite'); note.tabIndex = -1;
    const showNote = () => { note.textContent = message || ''; if (message) note.focus(); };
    let res;
    try { res = await xActivityCall({ action: 'overview' }); } catch (e) { res = { ok: false, j: {} }; }
    host.replaceChildren();
    if (!res.ok) {
      host.appendChild(el('h4', 'row-label', T('xact.section_title', 'X activity')));
      host.appendChild(el('p', 'subtle', T('xact.unavailable', 'Could not load your X activity right now.')));
      const row = el('div', 'inline'); const again = el('button', 'btn', T('xact.try_again', 'Try again')); again.type = 'button';
      again.addEventListener('click', () => draw('')); row.appendChild(again); host.appendChild(row);
      host.appendChild(note); showNote(); return;
    }
    const data = res.j, usage = data.usage || {};
    const pending = data.pending || [];
    host.appendChild(el('h4', 'row-label', data.store === 'unreadable' ? T('xact.section_title', 'X activity') : pending.length ? tfmt('xact.waiting_title', 'Waiting for your approval ({count})', { count: pending.length }) : T('xact.waiting_none_title', 'Nothing waiting for your approval')));
    if (data.store === 'unreadable') host.appendChild(el('p', 'subtle', data.store_message || T('xact.store_unreadable', 'VOOL could not read its list of X actions, so anything waiting can\'t be shown or sent.')));
    else if (!pending.length) host.appendChild(el('p', 'subtle', T('xact.waiting_none', 'When you ask VOOL to do something on X, like posting, replying, liking, following or sending a message, it shows here first. Nothing is sent until you approve it.')));
    host.appendChild(note);
    pending.forEach(a => {
      const card = el('div', 'x-action-card');
      const summary = a.summary || a.kind;
      card.appendChild(el('strong', null, summary));
      if (a.text) { const words = el('p', 'x-action-text', a.text); words.dir = 'auto'; card.appendChild(words); }
      const t = a.target || {};
      const reply = a.kind === 'hide_reply' || a.kind === 'unhide_reply';
      if (t.text) card.appendChild(Object.assign(el('p', 'subtle x-action-target', reply
        ? tfmt('xact.target_reply', 'Reply by @{author}: {text}', { author: t.author || '?', text: t.text })
        : tfmt('xact.target_post', 'Post by @{author}: {text}', { author: t.author || '?', text: t.text })), { dir: 'auto' }));
      if (t.url) card.appendChild(xOpenLink(t.url, summary));
      const row = el('div', 'inline');
      const yes = el('button', 'btn primary x-action-approve', T('xact.approve', 'Approve and send')); yes.type = 'button';
      const no = el('button', 'btn x-action-decline', T('xact.decline', 'Decline')); no.type = 'button';
      yes.setAttribute('aria-label', tfmt('xact.approve_aria', 'Approve and send: {summary}', { summary: summary }));
      no.setAttribute('aria-label', tfmt('xact.decline_aria', 'Decline: {summary}', { summary: summary }));
      const act = async (payload) => {
        yes.disabled = true; no.disabled = true;
        note.textContent = payload.action === 'approve' ? T('xact.sending', 'Sending to X…') : '';
        let out;
        // If the answer is lost after approving, VOOL may already have sent it: say so, never "nothing was sent".
        const lost = payload.action === 'approve'
          ? T('xact.lost_answer', 'VOOL did not get an answer. It may already have been sent: check Recent below and X before trying again.')
          : T('xact.failed', 'That did not work. Nothing was sent.');
        try { out = await xActivityCall(payload); } catch (e) { out = { ok: false, j: { message: lost } }; }
        await draw(out.j.message || (out.ok ? '' : lost));
      };
      yes.addEventListener('click', () => act({ action: 'approve', action_id: a.id, hash: a.hash || '' }));
      no.addEventListener('click', () => act({ action: 'decline', action_id: a.id }));
      row.appendChild(yes); row.appendChild(no); card.appendChild(row);
      host.appendChild(card);
    });
    host.appendChild(el('h4', 'row-label', T('xact.usage_title', 'X usage this month')));
    if (usage.ok === false) {
      host.appendChild(el('p', 'subtle', usage.message || T('xact.unavailable', 'Could not load your X activity right now.')));
      host.appendChild(el('p', 'subtle', T('xact.reset_note', 'X still counts on its side. Check your X developer account if you are close to your limit.')));
      const row = el('div', 'inline'); const reset = el('button', 'btn', T('xact.reset', 'Start counting from zero')); reset.type = 'button';
      reset.addEventListener('click', async () => {
        reset.disabled = true;
        const out = await xActivityCall({ action: 'reset_usage' }).catch(() => ({ ok: false, j: {} }));
        await draw(out.ok ? '' : (out.j.message || T('xact.reset_failed', 'The count was not reset. Try again.')));
      });
      row.appendChild(reset); host.appendChild(row);
    } else {
      const reads = Number(usage.reads || 0).toLocaleString(), sent = Number(usage.actions || 0).toLocaleString();
      host.appendChild(el('p', null, usage.monthly_reads == null
        ? tfmt('xact.usage_reads_nolimit', 'Read: {used} posts and accounts (no limit set)', { used: reads })
        : tfmt('xact.usage_reads', 'Read: {used} of {limit} posts and accounts', { used: reads, limit: Number(usage.monthly_reads).toLocaleString() })));
      host.appendChild(el('p', null, usage.monthly_actions == null
        ? tfmt('xact.usage_actions_nolimit', 'Sent: {actions} actions on X (no limit set)', { actions: sent })
        : tfmt('xact.usage_actions', 'Sent: {actions} of {action_limit} actions on X', { actions: sent, action_limit: Number(usage.monthly_actions).toLocaleString() })));
      host.appendChild(el('p', 'subtle', T('xact.usage_note', 'X charges your X developer account for each post or account VOOL reads and each thing it sends for you. VOOL counts them here and stops at your limits. Leave a box empty for no limit.')));
      const limits = el('div', 'x-limits');
      const mk = (labelKey, labelText, value) => {
        const wrap = el('label', 'x-limit'); wrap.appendChild(el('span', 'subtle', T(labelKey, labelText) + ' '));
        const inp = el('input', 'inp'); inp.type = 'number'; inp.min = '0'; inp.step = '1'; inp.inputMode = 'numeric';
        inp.value = value == null ? '' : String(value); inp.placeholder = T('xact.no_limit', 'no limit');
        wrap.appendChild(inp); limits.appendChild(wrap); return inp;
      };
      const readsInp = mk('xact.limit_reads', 'Most reads a month', usage.monthly_reads);
      const actsInp = mk('xact.limit_actions', 'Most sends a month', usage.monthly_actions);
      const save = el('button', 'btn', T('xact.save_limits', 'Save limits')); save.type = 'button';
      save.addEventListener('click', async () => {
        // A half-typed number (for example "1e") reads as empty in the box: never save it as "no limit".
        const bad = [readsInp, actsInp].find(inp => inp.validity.badInput || (inp.value.trim() !== '' && !/^[0-9]+$/.test(inp.value.trim())));
        if (bad) { note.textContent = T('xact.limits_whole', 'Use a whole number, or leave the box empty for no limit.'); bad.focus(); return; }
        save.disabled = true;
        const val = (inp) => inp.value.trim() === '' ? '' : Number(inp.value);
        const out = await xActivityCall({ action: 'set_limits', monthly_reads: val(readsInp), monthly_actions: val(actsInp) }).catch(() => ({ ok: false, j: {} }));
        await draw(out.ok ? T('xact.limits_saved', 'Limits saved.') : (out.j.message || T('xact.limits_failed', 'Your limits were not saved. Try again.')));
      });
      limits.appendChild(save);
      host.appendChild(limits);
    }
    const recent = (data.recent || []).slice(0, 5);
    if (recent.length) {
      host.appendChild(el('h4', 'row-label', T('xact.recent_title', 'Recent')));
      recent.forEach(a => {
        const line = el('p', 'subtle');
        const word = X_STATE_WORDS[a.state];
        line.textContent = (a.summary || a.kind) + ' · ' + (a.message || (word ? T(word[0], word[1]) : a.state));
        const url = (a.result || {}).url;
        if (url) { line.appendChild(document.createTextNode(' ')); line.appendChild(xOpenLink(url, a.summary || a.kind)); }
        host.appendChild(line);
      });
    }
    showNote();
  };
  draw('');
}
function widgetProviderGuide(host) {
  const input = el('input', 'inp'); input.type = 'search';
  input.placeholder = T('keyguide.search_placeholder', 'Find a provider…'); input.setAttribute('aria-label', T('keyguide.search_aria', 'Find a provider'));
  const results = el('div', 'key-guide');
  const note = el('div', 'subtle'); note.setAttribute('role', 'status');
  host.appendChild(input); host.appendChild(results); host.appendChild(note);
  const draw = () => {
    results.replaceChildren();
    const q = input.value.trim().toLocaleLowerCase();
    let count = 0;
    const stored = new Set((state.sources['/api/settings/credentials']?.credentials || []).map(c => c.name));
    KEY_PROVIDER_GUIDE.forEach(group => {
      const rows = group.providers.filter(r => [r.label, r.description, group.title].join(' ').toLocaleLowerCase().includes(q));
      if (!rows.length) return;
      count += rows.length;
      const section = el('details', 'key-guide-category'); section.dataset.category = group.id; section.open = !!q;
      const head = el('summary', 'row-label'); head.appendChild(keyGuideIcon(group.id));
      head.appendChild(document.createTextNode(group.title + ' · ' + rows.length)); section.appendChild(head);
      section.appendChild(el('p', 'subtle', group.description));
      rows.forEach(r => {
        const card = el('div', 'key-guide-provider');
        const info = el('div'); info.appendChild(el('strong', null, r.label));
        info.appendChild(el('p', 'subtle', r.description));
        const actions = el('div', 'inline');
        if (r.url) {
          const link = el('a', 'key-guide-link', T('keyguide.official_site', 'Official site ↗')); link.href = r.url;
          link.target = '_blank'; link.rel = 'noopener noreferrer'; link.setAttribute('aria-label', tfmt('keyguide.site_aria', '{label} official site', { label: r.label }));
          actions.appendChild(link);
        }
        const add = el('button', 'btn', stored.has(r.slot) ? T('keyguide.manage_key', 'Manage key') : T('keyguide.add_key', 'Add key')); add.type = 'button';
        add.setAttribute('aria-label', tfmt(stored.has(r.slot) ? 'keyguide.manage_aria' : 'keyguide.add_aria', '{label} key', { label: r.label }));
        add.addEventListener('click', () => {
          const picker = document.querySelector('.key-provider-picker');
          if (!picker || !Array.from(picker.options).some(o => o.value === r.provider)) {
            note.textContent = T('keyguide.still_loading', 'Provider list is still loading. Try again shortly.'); return;
          }
          picker.value = r.provider; picker.dispatchEvent(new Event('change', {bubbles:true}));
          const form = document.querySelector('.key-entry-form');
          form?.scrollIntoView({block:'center', behavior:'smooth'});
          const field = form?.querySelector('input[type="password"], input.key-value');
          (field || picker).focus(); note.textContent = tfmt('keyguide.selected', 'Selected {label}. Paste its API key below.', { label: r.label });
        });
        actions.appendChild(add);
        if (r.steps && r.steps.length) {
          const how = el('button', 'btn key-guide-steps', T('keyguide.setup_steps', 'Setup steps')); how.type = 'button';
          how.setAttribute('aria-label', tfmt('keyguide.setup_steps_aria', '{label} setup steps', { label: r.label }));
          how.addEventListener('click', () => openKeySetupDialog(r, () => add.click()));
          actions.appendChild(how);
        }
        if (stored.has(r.slot)) actions.appendChild(el('span', 'pill on', T('keyguide.key_saved', 'Key saved')));
        card.appendChild(info); card.appendChild(actions); section.appendChild(card);
      });
      results.appendChild(section);
    });
    if (!count) results.appendChild(el('div', 'empty', T('keyguide.no_match', 'No matching provider.')));
  };
  input.addEventListener('input', draw); draw();
  host.appendChild(el('p', 'subtle', T('keyguide.plans_note', 'Plans, prices and quotas are set by each provider. Check its official site before adding credit.')));
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
/* The machine's name in plain words: "This Mac" only on a Mac, "This computer" everywhere else. */
function thisComputerName() {
  const p = String((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '');
  return /mac/i.test(p) ? T('settings.models.this_mac', 'This Mac') : T('settings.models.this_computer', 'This computer');
}
function mpOpenRow(rowId, part) {
  location.hash = '#models/' + rowId + (part ? '/' + part : '');
}
async function mpReads() {
  const [pinR, discR, limitsR, localR] = await Promise.all([
    getJSON('/api/cloud/model').catch(() => null),
    getJSON(USEPOD_DISCOVERY_URL).catch(() => null),
    getJSON('/api/cloud/spend-limits').catch(() => null),
    getJSON('/api/models/local').catch(() => null),
  ]);
  return {
    local: localR,
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
    const pinLine = reads.pin && reads.pin.model
      ? ((reads.pin.provider && reads.pin.provider !== 'openrouter' ? reads.pin.provider + ':' : '') + reads.pin.model)
      : T('settings.models.auto_pick', 'VOOL picks automatically');
    const pinState = reads.pin ? String(reads.pin.cost_state || '') : '';
    head.appendChild(el('div', null, pinLine + (pinState ? ' — ' + pinState + (pinState === 'paid' ? ' · ready for answers once its limits and budget are set' : '') : '')));
    if (mpView.pane === 'detail') return mpDrawDetail(root, reads, st, draw);
    // Problems strip (composed from the same reads).
    const problems = [];
    /* A provider nobody set up is not a problem: only a started UsePod setup gets a line here. */
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
        if (key === 'local') { mpOpenRow('local_models'); return; }   /* straight to the row, no detour pane */
        if (key === 'usepod' && actionLabel !== 'Manage') mpView.guided.active = true;
        mpView.pane = 'detail'; mpView.provider = key; draw();
      });
      card.appendChild(btn);
      return card;
    };
    /* "ready" is earned from the real local listing, never assumed: a fresh install with no local
       model is told so, and where to go instead. */
    const localModels = (reads.local && reads.local.models) || [];
    const localReady = !!(reads.local && !reads.local.local_models_disabled && localModels.length);
    const localLine = !reads.local ? T('settings.models.local_unchecked', 'VOOL could not check for local models right now.')
      : reads.local.local_models_disabled ? T('settings.models.local_off', 'Local models are turned off on this copy of VOOL.')
      : localReady ? T('settings.models.local_ready', 'A model on this computer answers without the internet, for free.')
      : T('settings.models.local_none', 'No local model yet. Install one, or add a key in API Keys to use an online model.');
    cards.appendChild(mkCard('local', thisComputerName(), localLine, T('settings.models.view_local', 'View local models'), localReady));
    // A pending or allowed-but-unconfirmed consent is a RESUME state, not a fresh setup: the
    // card sends the operator to the detail with the saved approval, never through guided again
    // (a second proposal would duplicate the consent identity).
    const mpResumeState = st.connected && !st.budgetActive && st.consent;
    cards.appendChild(mkCard('usepod', 'UsePod', st.connected
      ? (st.budgetActive ? 'Connected · budget enabled' : (mpResumeState ? 'Connected · budget approval ' + st.consent.state + ' — one step away' : 'Connected · setup one step away'))
      : 'Inference marketplace — connect a token to start.', st.connected && !st.budgetActive && !mpResumeState ? 'Continue setup' : (st.connected ? 'Manage' : 'Set up'), st.connected && st.budgetActive));
    cards.appendChild(mkCard('openrouter', 'OpenRouter', 'Bring-your-own-key cloud models. VOOL-wide spending limits apply.', 'View OpenRouter', false));
    root.appendChild(head); root.appendChild(cards);
    const fine = el('div', 'subtle', T('settings.models.same_checks', 'Changes here go through the same checks as in a chat. Opening this page changes nothing.'));
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
  /* Numbered progress, not tabs: the steps are done in order and are not clickable. */
  steps.forEach((s, i) => chips.appendChild(el('span', 'mp-chip' + (s === g.step ? ' mp-now' : i < steps.indexOf(g.step) ? ' mp-done' : ''), (i + 1) + '. ' + (s === 'connect' ? 'Connect' : s === 'model' ? 'Model' : s === 'spending' ? 'Spending' : 'Review'))));
  chips.setAttribute('aria-label', 'Step ' + (steps.indexOf(g.step) + 1) + ' of ' + steps.length);
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
    /* A secret is typed hidden and kept out of browser autofill, as in API Keys. */
    const token = document.createElement('input'); token.type = 'password'; token.autocomplete = 'off'; token.spellcheck = false; token.className = 'inp mp-guided-token'; token.setAttribute('aria-label', 'UsePod token or proxy URL'); token.value = g.token;
    const save = el('button', 'btn primary', 'Connect'); save.type = 'button';
    const sayLine = el('div', 'subtle', '');
    save.addEventListener('click', async () => {
      const value = token.value.trim();
      if (!value) { sayLine.textContent = 'Paste your UsePod token first.'; token.focus(); return; }
      save.disabled = true; sayLine.textContent = 'Saving and verifying…';
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
    if (data.apple_silicon_notice) body.appendChild(el('div', 'state failed', T('settings.local_models.apple_silicon_notice', data.apple_silicon_notice)));
    const models = data.models || [];
    if (!models.length) {
      body.appendChild(el('div', 'empty', T('settings.models.local_empty', 'No local model yet. A local model runs privately on this computer; once one is installed, it appears here.')));
      const cmd = el('div', 'inline');
      cmd.appendChild(el('span', 'subtle', T('settings.models.terminal', 'For people comfortable with a terminal:')));
      cmd.appendChild(el('code', 'mono', 'ollama pull qwen3:8b'));
      body.appendChild(cmd);
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
    dl.appendChild(el('dd', m.model ? 'mono' : null, m.model ? String(m.model) : 'None — VOOL picks for each chat'));
    dl.appendChild(el('dt', null, 'Provider'));
    dl.appendChild(el('dd', m.model && m.provider ? 'mono' : null, m.model && m.provider ? String(m.provider) : 'None — each chat keeps its own choice'));
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
    if (!provider) {
      /* Nothing to pick yet: say what makes this row useful, with the way there. */
      const line = el('div', 'inline');
      line.appendChild(el('span', 'subtle', T('settings.models.need_key', 'Add a key in API Keys to choose a model here.')));
      const jump = el('button', 'btn', T('settings.models.go_keys', 'Go to API Keys')); jump.type = 'button';
      jump.addEventListener('click', () => { location.hash = '#keys'; });
      line.appendChild(jump);
      body.appendChild(line);
      return;
    }
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
  const asset = (facts || {}).asset || 'USDC';
  if (!facts || !facts.budget_mode || facts.budget_mode === 'once') return 'ONE request, up to ' + usepodDisplayAmount((facts || {}).per_call_atomic, asset);
  return 'All UsePod models and chats · ' + usepodDisplayAmount(facts.max_total_atomic, asset)
    + (facts.budget_mode === 'daily' ? ' every 24 hours, renewing while enabled' : ' in total, until used or disabled')
    + (facts.per_call_atomic == null ? '' : ' · Maximum per request: ' + usepodDisplayAmount(facts.per_call_atomic, asset));
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
    const walletReady = !!(payAuth.installed && payNets.length);
    const tokenReady = !!payCred.configured;
    const inUse = (ready) => ready ? ' — in use' : ' — selected, not ready yet';
    const payModes = el('div', 'stack usepod-pay-modes');
    body.appendChild(payModes);
    const useLane = async (transportMode, button) => {
      button.disabled = true;
      const { ok, body: j } = await usepodPost('/api/cloud/usepod/lane', { protocol: payLane.protocol || 'openai', transport_mode: transportMode });
      /* Wording only: the result says what is really true. A payment way with nothing behind it (no wallet
         on a supported network, no stored token) is saved as the choice, not announced as working. */
      const ready = transportMode === 'x402' ? walletReady : tokenReady;
      say(ok ? 'saved' : 'failed', ok
        ? (ready ? ('Calls are now paid by ' + (transportMode === 'x402' ? 'your wallet (x402)' : 'the funded token') + '.')
          : (transportMode === 'x402' ? 'Saved. Wallet payments can start only once Crypto is on and a wallet on a supported network is ready.' : 'Saved. Token payments can start only once a UsePod token is stored.'))
        : ('Not switched — ' + (j.error || j.code || 'refused') + '.'));
      await draw();
    };
    const tokenCard = el('div', 'stack profile-item usepod-pay-mode');
    tokenCard.setAttribute('data-mode', 'prepaid_token');
    tokenCard.appendChild(el('div', 'row-label', 'Funded token (prepaid)' + (payLane.transport_mode !== 'x402' ? inUse(tokenReady) : '')));
    tokenCard.appendChild(el('div', 'subtle', 'Each paid call spends the UsePod account balance behind your stored token. ' + (payCred.configured ? 'A token is stored.' : 'No token is stored yet (API Keys → UsePod).')));
    if (payLane.transport_mode === 'x402') {
      const useToken = el('button', 'btn usepod-use-prepaid', 'Pay with the funded token'); useToken.type = 'button';
      useToken.addEventListener('click', () => useLane('prepaid_token', useToken));
      tokenCard.appendChild(useToken);
    }
    payModes.appendChild(tokenCard);
    const walletCard = el('div', 'stack profile-item usepod-pay-mode');
    walletCard.setAttribute('data-mode', 'x402');
    walletCard.appendChild(el('div', 'row-label', 'Wallet x402 (pay per call)' + (payLane.transport_mode === 'x402' ? inUse(walletReady) : '')));
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
          kv.appendChild(el('dt', null, 'Models')); kv.appendChild(el('dd', 'mono', (facts.models || []).join(', ') || (facts.budget_mode && facts.budget_mode !== 'once' ? 'All UsePod models' : 'None')));
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
    /* The checkbox sits inside its label (no .inp width), so box and words stay together. */
    const fallback = el('input', 'usepod-policy-fallback'); fallback.type = 'checkbox'; fallback.id = 'usepodFallback'; fallback.checked = !!policy.allow_centralized_fallback;
    const fbLabel = el('label', 'inline'); fbLabel.htmlFor = 'usepodFallback';
    fbLabel.appendChild(fallback); fbLabel.appendChild(el('span', null, 'allow centralized fallback'));
    pline1.appendChild(mode); pline1.appendChild(fbLabel);
    body.appendChild(pline1);
    const pline2 = el('div', 'inline');
    const pins = el('input', 'inp usepod-policy-pins'); pins.type = 'text'; pins.placeholder = 'Providers, comma-separated'; pins.value = (policy.pinned_providers || []).join(','); pins.setAttribute('aria-label', 'Pinned centralized providers');
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
const WEB_LOOKUPS_URL = '/api/onboarding/pact?view=boundary';

/* --- The two boundary switches on Privacy & Permissions: Web lookups (system.allow_web_fallback)
   and Go online on its own (network.outbound_enabled). Both read the live boundary truth from
   WEB_LOOKUPS_URL and write ONE key through the existing boundary command. Local-only mode vetoes
   turning either on (the server refuses it too); turning off is always allowed, and neither row
   ever changes local-only mode itself. These are consent controls, so they fail closed: when the
   answer cannot be read, or does not carry both booleans, no switch is drawn -- a row never shows
   "off" (or "on") for a value it does not know, and never shows a raw error code. */
function boundarySwitchReading(src, field) {
  if (!src || src.ok === false) return null;
  if (typeof src[field] !== 'boolean' || typeof src.local_only_mode !== 'boolean') return null;
  return { localOnly: src.local_only_mode, on: src[field] && !src.local_only_mode };
}
function boundarySwitchWidget(stack, cfg) {
  const readFailed = () => T('settings.switch.read_failed', 'VOOL could not read this setting. Reopen Settings to try again.');
  const localOnlyNote = () => T(cfg.noteKey, LOCAL_ONLY_NOTE_EN);
  const reading = () => boundarySwitchReading(state.sources[WEB_LOOKUPS_URL], cfg.field);
  if (state.sourceErr[WEB_LOOKUPS_URL]) { stack.appendChild(el('div', 'empty', readFailed())); return; }
  if (!state.sources[WEB_LOOKUPS_URL]) { stack.appendChild(el('div', 'empty', T('settings.widget.loading', 'Loading…'))); return; }
  const first = reading();
  if (!first) { stack.appendChild(el('div', 'empty', readFailed())); return; }

  const st = el('div', 'state idle');
  st.setAttribute('role', 'status');
  const line = el('div', 'inline');
  const sw = el('button', 'sw');
  sw.type = 'button';
  sw.setAttribute('role', 'switch');
  sw.setAttribute('aria-label', T(cfg.labelKey, cfg.labelEn));
  const note = el('div', 'subtle', localOnlyNote());
  line.appendChild(sw);
  stack.appendChild(line);
  stack.appendChild(note);
  stack.appendChild(st);
  /* Paint one reading; an unreadable one leaves the switch unusable rather than guessing. */
  const paint = (r) => {
    if (!r) {
      sw.disabled = true;
      sw.setAttribute('aria-checked', 'false');
      note.style.display = 'none';
      return;
    }
    sw.setAttribute('aria-checked', r.on ? 'true' : 'false');
    sw.disabled = r.localOnly;
    note.style.display = r.localOnly ? '' : 'none';
  };
  paint(first);
  const reread = async () => paint((await readSource(WEB_LOOKUPS_URL)) ? reading() : null);

  sw.addEventListener('click', async () => {
    if (sw.disabled) return;
    /* The target is decided from the authority's last read, not from the value at render time. */
    const now = reading();
    if (!now || now.localOnly) { paint(now); return; }
    const want = !now.on;
    sw.disabled = true;
    st.className = 'state saving'; st.textContent = T('settings.switch.saving', 'Saving…');
    try {
      const r = await fetch('/api/onboarding/pact/boundary', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: cfg.key, value: want }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok || !j || j.ok === false) {
        /* Refused or failed: say it in plain words (never a raw error code), then re-read the
           authority so the switch shows what is really in effect -- a partial or failed write
           must not leave the switch claiming a state VOOL is not in. */
        const code = String((j && j.error) || '');
        const why = code === 'invalid_transition'
          ? localOnlyNote()
          : T(cfg.retryKey, 'VOOL could not change this right now. Try again in a moment.');
        await reread();
        st.className = 'state failed';
        st.textContent = tfmt('settings.switch.not_saved', 'Not saved — {reason}', { reason: why });
        return;
      }
      /* Read back from the authority rather than trusting the 200. */
      await reread();
      const back = state.sourceErr[WEB_LOOKUPS_URL] ? null : reading();
      if (!back || back.on !== want) {
        st.className = 'state stale';
        st.textContent = T('settings.switch.unconfirmed', 'Sent, but VOOL could not confirm it. Reopen Settings to check.');
        return;
      }
      st.className = 'state saved'; st.textContent = T('settings.switch.saved', 'Saved');
      setTimeout(() => { if (st.className === 'state saved') { st.className = 'state idle'; st.textContent = ''; } }, 2600);
    } catch (e) {
      /* No answer: re-read; if that fails too, the switch is not trusted to show the old value. */
      await reread();
      st.className = 'state failed';
      st.textContent = T('settings.switch.no_answer', 'Not saved — VOOL did not answer.');
    }
  });
}
const LOCAL_ONLY_NOTE_EN = 'Local-only mode is on for this whole computer, so this stays off. Settings has no switch to turn local-only mode off.';
function widgetLiveBrowser(stack) {
  const data = state.sources['/api/browser/settings'];
  if (!data || typeof data.enabled !== 'boolean') { stack.appendChild(el('div', 'empty', T('settings.switch.read_failed', 'VOOL could not read this setting. Reopen Settings to try again.'))); return; }
  const button = el('button', 'sw'); button.type = 'button'; button.setAttribute('role', 'switch');
  button.setAttribute('aria-label', T('settings.row.live_browser.label', 'Browser'));
  const note = el('div', 'subtle'); const status = el('div', 'state idle'); status.setAttribute('role', 'status');
  let current = data, busy = false;
  const paint = back => {
    current = back;
    button.setAttribute('aria-checked', back.enabled === true ? 'true' : 'false');
    button.disabled = busy || (!back.enabled && (back.local_only || !back.web_enabled));
    note.textContent = back.local_only || !back.web_enabled ? T('vcs.state.egress_refused', 'Network access is off') : '';
  };
  const reread = async () => {
    const r = await fetch('/api/browser/settings', {cache:'no-store'}); const back = await r.json();
    if (!r.ok || !back.ok || typeof back.enabled !== 'boolean') throw new Error('unavailable');
    paint(back); return back;
  };
  paint(data);
  button.onclick = async () => {
    busy = true; button.disabled = true;
    const want = !current.enabled; status.textContent = T('settings.switch.saving', 'Saving…');
    try {
      const r = await fetch('/api/browser/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:want})});
      const back = await reread();
      status.textContent = r.ok && back.enabled === want ? T('settings.switch.saved', 'Saved') : T('settings.switch.no_answer', 'Not saved — VOOL did not answer.');
    } catch(e) { status.textContent = T('settings.switch.no_answer', 'Not saved — VOOL did not answer.'); }
    busy = false; paint(current);
  };
  stack.appendChild(button); stack.appendChild(note); stack.appendChild(status);
  const link = el('a', '', T('live.browser', 'Browser')); link.href = '/browser?chat=browser'; link.target = '_blank'; link.rel = 'noopener'; stack.appendChild(link);
  const timer = setInterval(() => { if (!button.isConnected) { clearInterval(timer); return; } if (!busy) reread().catch(() => { button.disabled = true; }); }, 1500);
}
function widgetWebLookups(stack) {
  return boundarySwitchWidget(stack, {
    key: 'web_lookups', field: 'allow_web_fallback',
    labelKey: 'settings.row.web_lookups.label', labelEn: 'Web lookups',
    noteKey: 'settings.row.web_lookups.local_only_note',
    retryKey: 'settings.row.web_lookups.retry_note',
  });
}
function widgetAutomaticChecks(stack) {
  return boundarySwitchWidget(stack, {
    key: 'automatic_checks', field: 'outbound_enabled',
    labelKey: 'settings.row.automatic_checks.label', labelEn: 'Go online on its own',
    noteKey: 'settings.row.automatic_checks.local_only_note',
    retryKey: 'settings.row.automatic_checks.retry_note',
  });
}

let phoneSettingsNode = null, phoneSettingsReady = false, phoneSettingsLanguage = '';
const phoneBotPairing = {};
function redrawPhoneSettings() {
  const host = phoneSettingsNode && phoneSettingsNode.parentElement;
  if (host) {phoneSettingsNode=null; host.replaceChildren(); widgetPhoneAccess(host);}
}
function widgetPhoneAccess(host) {
  const url = '/api/mobile/settings';
  const src = state.sources[url];
  const language = document.documentElement.lang;
  if (src && phoneSettingsNode && phoneSettingsReady && phoneSettingsLanguage === language) {host.appendChild(phoneSettingsNode);return;}
  const stack = el('div', 'phone-settings-panel'); host.appendChild(stack);
  phoneSettingsNode=stack; phoneSettingsReady=!!src && !state.sourceErr[url]; phoneSettingsLanguage=language;
  if (!src || state.sourceErr[url]) {
    stack.appendChild(el('div', 'empty', T('settings.switch.read_failed', 'VOOL could not read this setting. Reopen Settings to try again.'))); return;
  }
  const network = el('select', 'inp');
  network.setAttribute('aria-label', T('phone.network', 'Network address'));
  const addresses = [...new Set([src.address, ...(src.addresses || [])].filter(Boolean))];
  addresses.forEach(a => { const option = el('option', '', a); option.value = a; network.appendChild(option); });
  if (src.address) network.value = src.address;
  network.disabled = !!src.enabled;
  stack.appendChild(network);
  const line = el('div', 'inline');
  const toggle = el('button', 'sw'); toggle.type = 'button'; toggle.setAttribute('role', 'switch');
  toggle.setAttribute('aria-label', T('settings.row.phone_access.label', 'Use VOOL from my phone'));
  toggle.setAttribute('aria-checked', src.enabled ? 'true' : 'false'); line.appendChild(toggle);
  const pair = el('button', 'btn', T('phone.pair', 'Pair a phone')); pair.type = 'button'; pair.disabled = !src.running;
  line.appendChild(pair); stack.appendChild(line);
  const note = el('div', 'state idle'); note.setAttribute('role', 'status'); stack.appendChild(note);
  toggle.onclick = async () => {
    toggle.disabled = true;
    try {
      const wanted = !src.enabled;
      const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled:wanted,address:network.value})});
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error();
      const back = await getJSON(url);
      if (back.enabled !== wanted) throw new Error();
      state.sources[url] = back; redrawPhoneSettings();
    } catch (_) { note.textContent = T('settings.switch.no_answer', 'Not saved — VOOL did not answer.'); toggle.disabled = false; }
  };
  const refresh = el('button','btn',T('phone.refresh','Refresh permissions')); refresh.type='button'; stack.appendChild(refresh);
  refresh.onclick=async()=>{refresh.disabled=true;try{state.sources[url]=await getJSON(url);redrawPhoneSettings();}catch(e){note.textContent=e.message;refresh.disabled=false;}};
  const frozen = el('button', 'btn', src.frozen ? T('phone.unfreeze', 'Unfreeze payments') : T('phone.freeze', 'Freeze payments'));
  frozen.type = 'button'; stack.appendChild(frozen);
  const accounts = (src.wallet_accounts || []).filter(a => a.seal_policy === 'pilot-v3' && a.setup_state === 'ready');
  function accountSelect(value) {
    const select = el('select', 'inp'); select.setAttribute('aria-label', T('phone.account', 'Wallet account'));
    accounts.forEach(a => {const option = el('option', '', (a.label || a.display_network || a.network) + ' · ' + (a.native_asset || '')); option.value = a.wallet_id; select.appendChild(option);});
    if (value) select.value = value;
    return select;
  }
  async function save(body) {
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const j = await r.json(); if (!r.ok || !j.ok) throw new Error(j.error || T('settings.switch.no_answer', 'Not saved — VOOL did not answer.'));
    state.sources[url] = await getJSON(url); redrawPhoneSettings();
  }
  const chatApps=el('details','phone-bot-connect'); stack.appendChild(chatApps);
  chatApps.appendChild(el('summary','',T('phone.bot_connect','Connect chat app')));
  chatApps.appendChild(el('p','help',T('phone.bot_privacy','Telegram and Discord can read messages sent through them. Use a private conversation.')));
  const botPlatform=el('select','inp'); ['Telegram','Discord'].forEach(name=>{const option=el('option','',name);option.value=name.toLowerCase();botPlatform.appendChild(option);});chatApps.appendChild(botPlatform);
  const botTokenLabel=el('label','phone-cap',T('phone.bot_token','Bot token'));const botToken=el('input','inp');botToken.type='password';botToken.autocomplete='off';botTokenLabel.appendChild(botToken);chatApps.appendChild(botTokenLabel);
  const dmLabel=el('label','phone-cap',T('phone.bot_dm','Private Discord conversation link'));const dmLink=el('input','inp');dmLabel.appendChild(dmLink);chatApps.appendChild(dmLabel);dmLabel.hidden=true;
  botPlatform.onchange=()=>{dmLabel.hidden=botPlatform.value!=='discord';};
  const connectBot=el('button','btn',T('phone.bot_connect','Connect chat app'));connectBot.type='button';connectBot.disabled=!src.enabled;chatApps.appendChild(connectBot);
  connectBot.onclick=async()=>{const token=botToken.value;botToken.value='';connectBot.disabled=true;try {
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'bot_connect',platform:botPlatform.value,token,channel_link:dmLink.value})});const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error);
    phoneBotPairing[j.platform]={code:j.code,expires:Date.now()+j.expires_in*1000};state.sources[url]=await getJSON(url);redrawPhoneSettings();
  }catch(e){note.textContent=e.message;connectBot.disabled=false;}};
  const relayForm=el('details','phone-relay'); stack.appendChild(relayForm);
  relayForm.appendChild(el('summary','',T('phone.relay_title','Use my relay')));
  relayForm.appendChild(el('p','help',T('phone.relay_help','Your own relay forwards encrypted messages. It sees connection times and device identifiers. Pair at home first.')));
  function relayInput(key, fallback, value, secret) {
    const label=el('label','phone-cap',T(key,fallback)); const input=el('input','inp');
    input.type=secret?'password':'text'; input.autocomplete='off'; input.value=value || ''; label.appendChild(input); relayForm.appendChild(label); return input;
  }
  const relayAddress=relayInput('phone.relay_address','Relay address',src.relay_endpoint);
  const relayRoom=relayInput('phone.relay_room','Room code',src.relay_room);
  const relayToken=relayInput('phone.relay_token','PC connection token','',true);
  const relayEnable=el('button','btn',T('phone.relay_enable','Connect relay')); relayEnable.type='button';
  relayEnable.disabled=!(src.devices || []).some(d=>d.status==='active'); relayForm.appendChild(relayEnable);
  relayEnable.onclick=()=>{const token=relayToken.value; relayToken.value=''; relayEnable.disabled=true;
    save({operation:'relay',endpoint:relayAddress.value,room:relayRoom.value,token}).catch(e=>{note.textContent=e.message;relayEnable.disabled=false;});};
  if (src.mode==='relay') relayForm.appendChild(el('p','help',T('phone.relay_phone','Phone connection address')+': '+src.relay_endpoint+'/rooms/'+src.relay_room));
  frozen.onclick = () => {
    if (!src.frozen) {frozen.disabled = true; save({operation:'freeze'}).catch(e => {note.textContent=e.message; frozen.disabled=false;}); return;}
    const form = el('div', 'phone-unfreeze'); const account = accountSelect(); const credential = el('input', 'inp');
    credential.type='password'; credential.autocomplete='off'; credential.placeholder=T('phone.credential', 'Wallet PIN or password');
    const button = el('button','btn',T('phone.unfreeze','Unfreeze payments')); button.type='button'; form.append(account, credential, button); stack.appendChild(form);
    button.onclick = () => {const secret=credential.value; credential.value=''; button.disabled=true; save({operation:'unfreeze',wallet_id:account.value,credential:secret}).catch(e=>{note.textContent=e.message;button.disabled=false;});};
  };
  const names = {chat:'Chat', history:'Read chat history', attachments:'Send attachments', tool_approval:'Approve actions', alerts:'Alerts',
    wallet_view:'Wallet balances and receipts', payment_propose:'Propose payments', payment_approve:'Approve payments', camera:'Camera', photos:'Photos', location:'Location', microphone:'Voice',
    computer_tools:'Use tools on my computer', paid_services:'Use my paid services'};
  const noticeNames={task_completed:'Task finished',task_failed:'Task failed',task_pending_approval:'VOOL needs an approval',
    task_cancelled:'Task stopped',payment_pending:'Payment waiting for approval',payment_done:'Payment done',payment_refused:'Payment refused or failed',
    desktop_inbox:'New desktop inbox item',calendar_alert:'Calendar alert',calendar_catch_up:'Missed calendar alerts',reminder:'Reminder',model_offer:'Model offer',model_market:'Model update',market_watch:'Market watch'};
  function botNotices(row,device) {
    const choices=JSON.parse(JSON.stringify(device.notification_preferences));
    const section=el('fieldset','phone-bot-notifications');section.appendChild(el('legend','',T('phone.right.alerts','Alerts')));row.appendChild(section);
    const preset=el('select','inp');preset.setAttribute('aria-label',T('phone.right.alerts','Alerts'));
    ['everything','important','off','custom'].forEach(value=>{const option=el('option','',T('phone.event.preset_'+value,{everything:'Everything',important:'Important only',off:'Off',custom:'Custom'}[value]));option.value=value;preset.appendChild(option);});
    preset.value=choices.preset;section.appendChild(preset);
    const boxes={};Object.entries(choices.types).forEach(([kind,value])=>{
      const line=el('label','phone-right');const box=el('input');box.type='checkbox';box.checked=value.enabled;box.dataset.notificationType=kind;boxes[kind]=box;
      line.append(box,document.createTextNode(' '+T('phone.event.'+kind,noticeNames[kind]||kind)));section.appendChild(line);box.onchange=()=>{preset.value='custom';};
    });
    preset.onchange=()=>{const configured=src.notification_presets[preset.value];if(configured)Object.entries(boxes).forEach(([kind,box])=>{box.checked=configured.types[kind].enabled;});};
    function quietSwitch(key,label) {const line=el('label','phone-right');const box=el('input');box.type='checkbox';box.checked=choices.quiet[key];line.append(box,document.createTextNode(' '+T('phone.event.'+label[0],label[1])));section.appendChild(line);return box;}
    const quiet=quietSwitch('enabled',['quiet','Quiet hours']);
    function quietTime(key,label) {const line=el('label','phone-expiry',T('phone.event.quiet_'+key,label));const input=el('input','inp');input.type='time';input.value=choices.quiet[key];line.appendChild(input);section.appendChild(line);return input;}
    const start=quietTime('start','Quiet hours start'),end=quietTime('end','Quiet hours end');
    const approvals=quietSwitch('approvals_through',['approvals_through','Allow approvals through']);
    const payments=quietSwitch('payments_through',['payments_through','Allow payments through']);
    const button=el('button','btn',T('notif.save','Save'));button.type='button';section.appendChild(button);
    button.onclick=async()=>{
      const preferences={quiet:{enabled:quiet.checked,start:start.value,end:end.value,offset_minutes:-new Date().getTimezoneOffset(),approvals_through:approvals.checked,payments_through:payments.checked}};
      if(preset.value==='custom')preferences.types=Object.fromEntries(Object.entries(boxes).map(([kind,box])=>[kind,{enabled:box.checked}]));else preferences.preset=preset.value;
      section.disabled=true;try {
        const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'bot_notifications',device_id:device.device_id,preferences})});const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error);
        device.notification_preferences=j.preferences;section.remove();botNotices(row,device);
      }catch(e){note.textContent=e.message;section.disabled=false;}
    };
  }
  (src.devices || []).forEach(device => {
    const row=el('section','phone-device'); row.dataset.device=device.device_id;
    const isBot=device.kind==='bot';
    row.appendChild(el('h3','',device.name || T('phone.device','Phone')));
    row.appendChild(el('p','help', T('phone.paired','Paired') + ': ' + (device.paired_at ? new Date(device.paired_at).toLocaleString() : '') + ' · ' + T('phone.last_seen','Last seen') + ': ' + (device.last_seen_at ? new Date(device.last_seen_at).toLocaleString() : '')));
    if (device.status !== 'active') {row.appendChild(el('p','help',T('phone.unpaired','Unpaired'))); stack.appendChild(row); return;}
    const rightsGrid=el('div','phone-rights'); row.appendChild(rightsGrid);
    if (isBot && phoneBotPairing[device.platform]?.expires>Date.now() && device.binding_state==='waiting') row.appendChild(el('p','help',T('phone.bot_paircode','Send /pair {code} to this bot in a private conversation within five minutes.').replace('{code}',phoneBotPairing[device.platform].code)));
    const boxes={}, expiryInputs={}; Object.entries(names).forEach(([name,label])=> {
      if (isBot && ['camera','photos','location','microphone','tool_approval','payment_approve','computer_tools','paid_services'].includes(name)) return;
      const line=el('label','phone-right'); const box=el('input'); box.type='checkbox';
      /* An expired right shows as off, so saving anything else never sends it back on. */
      const until=(device.right_expiry || {})[name]; box.checked=!!(device.rules || {})[name] && !(until && until*1000<=Date.now());
      box.dataset.right=name; box.disabled=name==='tool_approval' || name==='payment_approve'; boxes[name]=box;
      line.append(box, document.createTextNode(' '+T('phone.right.'+name,label))); rightsGrid.appendChild(line);
      const expiry=el('input','inp');expiry.type='datetime-local';expiry.setAttribute('aria-label',T('phone.right_expiry','Expires')+' · '+T('phone.right.'+name,label));
      const epoch=(device.right_expiry || {})[name];if(epoch)expiry.value=new Date(epoch*1000-new Date(epoch*1000).getTimezoneOffset()*60000).toISOString().slice(0,16);
      expiry.dataset.initial=expiry.value;
      expiry.disabled=box.disabled;expiryInputs[name]=expiry;const expiryLabel=el('label','phone-expiry',T('phone.right_expiry','Expires'));expiryLabel.appendChild(expiry);rightsGrid.appendChild(expiryLabel);
    });
    if(!isBot)row.appendChild(el('p','help',T('phone.tools_help','Tools: files, apps, commands and your accounts on this computer. Paid services: your paid models and paid search. Neither can send money; payments stay in the phone’s Wallet tab. What the phone sets up, like watches or saved files, stays after you switch a right off.')));
    row.appendChild(el('p','help',isBot?T('phone.bot_approve_pc','Bots can never approve payments or actions. Approve on your computer.'):T('phone.approve_on_pc','Payments and actions must be approved on your computer in this version.')));
    if(isBot)botNotices(row,device);
    const account=accountSelect((device.payment_cap || {}).wallet_id); row.appendChild(account);
    function amount(label,value) {const line=el('label','phone-cap',label); const input=el('input','inp'); input.inputMode='decimal'; input.value=value || ''; line.appendChild(input); row.appendChild(line); return input;}
    const cap=device.payment_cap_display || {};
    const per=amount(T('phone.per_payment','Per-payment maximum'),cap.per_payment);
    const daily=amount(T('phone.per_day','Daily maximum'),cap.daily);
    row.appendChild(el('p','help',T('phone.cap_help','Enter amounts in the account’s native asset. Both limits must be lower than the wallet’s limits.')));
    const credential=el('input','inp'); credential.type='password'; credential.autocomplete='off'; credential.placeholder=T('phone.credential','Wallet PIN or password'); row.appendChild(credential);
    const actions=el('div','phone-actions'); row.appendChild(actions);
    const saveButton=el('button','btn',T('phone.save_rights','Save permissions')); saveButton.type='button'; actions.appendChild(saveButton);
    saveButton.onclick=()=> {
      const rules=Object.fromEntries(Object.entries(boxes).map(([k,v])=>[k,v.checked]));
      const body={operation:'device_rules',device_id:device.device_id,rules,expected_permission_revision:device.permission_revision || 0};
      const changedExpiry={};Object.entries(expiryInputs).forEach(([name,input])=>{if(input.value!==input.dataset.initial)changedExpiry[name]=input.value?new Date(input.value).getTime()/1000:0;});
      if(Object.keys(changedExpiry).length)body.right_expiry=changedExpiry;
      if (rules.payment_propose) {
        const selected=accounts.find(a=>a.wallet_id===account.value); if (!selected) {note.textContent=T('phone.setup_wallet','Set up a wallet on your computer first.');return;}
        body.credential=credential.value; body.payment_cap_human={wallet_id:account.value,network:selected.network,per_payment:per.value,daily:daily.value};
      }
      credential.value=''; saveButton.disabled=true;
      save(body).catch(e=>{note.textContent=e.message;saveButton.disabled=false;});
    };
    const revoke=el('button','btn',isBot?T('phone.bot_disconnect','Disconnect chat app'):T('phone.revoke','Unpair this phone')); revoke.type='button'; actions.appendChild(revoke);
    revoke.onclick=async()=>{revoke.disabled=true;try {const r=await fetch(isBot?url:'/api/mobile/devices/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(isBot?{operation:'bot_disconnect',platform:device.platform}:{device_id:device.device_id})});if(!r.ok)throw new Error();delete phoneBotPairing[device.platform];state.sources[url]=await getJSON(url);redrawPhoneSettings();}catch(_){note.textContent=T('settings.switch.no_answer','Not saved — VOOL did not answer.');revoke.disabled=false;}};
    stack.appendChild(row);
  });
  pair.onclick = async () => {
    pair.disabled = true;
    try {
      const r = await fetch('/api/mobile/pairing/start', {method:'POST', headers:{'Content-Type':'application/json'},body:'{}'});
      const j = await r.json(); if (!r.ok || !j.pairing_page) throw new Error();
      window.open(j.pairing_page, '_blank', 'noopener');
    } catch (_) { note.textContent = T('settings.switch.no_answer', 'Not saved — VOOL did not answer.'); }
    finally { pair.disabled = !src.running; }
  };
}

function widgetAnswerLanguage(stack) {
  const src = state.sources['/api/profile'];
  const err = state.sourceErr['/api/profile'];
  if (err) { stack.appendChild(el('div', 'empty', tfmt('settings.widget.unavailable', 'Unavailable — {error}', { error: err }))); return; }
  if (!src) { stack.appendChild(el('div', 'empty', T('settings.widget.loading', 'Loading…'))); return; }

  const items = (src.items || []).filter(i => i.category === 'language' && i.status !== 'deleted');
  /* resolve()'s precedence is chat > project > context > global. Settings has no chat, so it
     writes the GLOBAL one; a narrower item still wins where it applies, and saying so is the
     difference between a control and a lie. */
  const global_ = items.find(i => i.scope === 'global');
  const narrower = items.filter(i => i.scope !== 'global');
  const known = new Set(LANGUAGES.map(l => l.label.toLowerCase()).concat(LANGUAGES.map(l => l.code)));
  const readable = (v) => known.has(String(v || '').trim().toLowerCase());

  const sel = el('select', 'inp');
  sel.setAttribute('aria-label', T('settings.row.answer_language.label', 'Answer language'));
  const none = el('option', null, T('settings.answer_language.none', 'No preference — VOOL follows your message'));
  none.value = '';
  sel.appendChild(none);
  LANGUAGES.forEach(l => { const o = el('option', null, l.label); o.value = l.label; sel.appendChild(o); });
  if (global_ && readable(global_.value)) sel.value = String(global_.value);

  const st = el('div', 'state idle');
  const row = el('div', 'inline');
  row.appendChild(sel);
  const clear = el('button', 'btn', T('header.search_clear', 'Clear'));
  clear.type = 'button';
  clear.disabled = !global_;
  row.appendChild(clear);
  stack.appendChild(row);
  stack.appendChild(st);
  /* The row is redrawn after a save; the outcome is carried across that redraw so "Saved" is seen. */
  if (widgetCache.answerLanguageNote) { st.className = 'state saved'; st.textContent = widgetCache.answerLanguageNote; widgetCache.answerLanguageNote = ''; setTimeout(() => { st.className = 'state idle'; st.textContent = ''; }, 2600); }
  /* Paused memory refuses every write to the profile, this one included: say so before a choice is made. */
  if (src.paused) {
    sel.disabled = true; clear.disabled = true;
    st.className = 'state stale'; st.textContent = T('settings.answer_language.paused', 'Memory is paused. Resume it in Memory & Personalisation to change this.');
  }

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
      widgetCache.answerLanguageNote = 'Saved';
      renderPane();
    } catch (e) { fail('Not saved — ' + profileWords(e.message)); }
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
      'VOOL has \u201c' + String(global_.value) + '\u201d remembered as your answer language, but it is not one ' +
      'of the languages VOOL can be set to answer in, so it is not used. Pick one above to replace it, or clear it.'));
  }
  if (narrower.length) {
    stack.appendChild(el('div', 'subtle',
      'A more specific choice also exists (' + narrower.map(i => (PROFILE_SCOPE_TEXT[i.scope] || i.scope) + ': ' + i.value).join(', ') +
      ') and wins where it applies. This setting applies everywhere else.'));
  }
  stack.appendChild(el('div', 'subtle',
    T('settings.answer_language.footer', 'Kept with everything else VOOL remembers, under Memory & Personalisation. For each answer, ' +
    'what your message asks for comes first, then this, then the language you wrote in.')));
}

/* Add a password email account (core/email_setup.py, POST /api/email/accounts/add). The server signs in to
   the incoming server and, when one is given, the outgoing server once each, with certificates verified,
   and saves the account only after both accept; Local Only and an existing account are refused before any
   connection. The password field is never prefilled, it is cleared after every attempt, and no answer from
   the server carries it. Removal (POST /api/email/accounts/remove) deletes the stored entries, clears a
   default that named the account, and changes nothing on the mail server. */
const EMAIL_SETUP_FIELDS = {
  address: () => T('emailadd.address', 'Email address'),
  username: () => T('emailadd.username', 'Username (leave empty to use the email address)'),
  password: () => T('emailadd.password', 'Password'),
  imap_host: () => T('emailadd.imap_host', 'Incoming mail server (IMAP)'),
  imap_port: () => T('emailadd.imap_port', 'Incoming server port'),
  imap_security: () => T('emailadd.imap_security', 'Incoming connection security'),
  smtp_host: () => T('emailadd.smtp_host', 'Outgoing mail server (SMTP), optional'),
  smtp_port: () => T('emailadd.smtp_port', 'Outgoing server port'),
  smtp_security: () => T('emailadd.smtp_security', 'Outgoing connection security'),
  replace: () => T('emailadd.replace', 'Replace the saved details for this address'),
};
const EMAIL_SETUP_ERRORS = {
  invalid: () => T('emailadd.err.invalid', 'Some details are missing or not valid. Nothing was saved.'),
  password_charset: () => T('emailadd.err.password_charset', 'This password has characters a mail server sign-in cannot carry. If your provider offers app passwords, create one and use it here. Nothing was saved.'),
  local_only: () => T('emailadd.err.local_only', 'Local Only is on, so VOOL did not contact this mail server. Nothing was saved.'),
  exists: () => T('emailadd.err.exists', 'This email account is already set up. Tick replace to save the new details.'),
  conflict: () => T('emailadd.err.conflict', 'Another mailbox is already saved under this account name, so nothing was changed.'),
  managed_elsewhere: () => T('emailadd.err.managed_elsewhere', "This account was connected through its provider's sign-in, so it is changed there, not here."),
  auth_failed: () => T('emailadd.err.auth_failed', 'The incoming mail server did not accept this username and password. Some providers need an app password, and some do not allow password sign-in for other apps at all. Nothing was saved.'),
  cannot_reach: () => T('emailadd.err.cannot_reach', 'VOOL could not reach the incoming mail server. Check the server name and port. Nothing was saved.'),
  tls_failed: () => T('emailadd.err.tls_failed', "The incoming mail server's secure connection could not be verified, so VOOL did not send the password. Nothing was saved."),
  timeout: () => T('emailadd.err.timeout', 'The incoming mail server did not answer in time. Nothing was saved.'),
  smtp_auth_failed: () => T('emailadd.err.smtp_auth_failed', 'The outgoing mail server did not accept this username and password. Some providers need an app password, and some do not allow password sign-in for other apps at all. Nothing was saved.'),
  smtp_cannot_reach: () => T('emailadd.err.smtp_cannot_reach', 'VOOL could not reach the outgoing mail server. Check the server name and port. Nothing was saved.'),
  smtp_tls_failed: () => T('emailadd.err.smtp_tls_failed', "The outgoing mail server's secure connection could not be verified, so VOOL did not send the password. Nothing was saved."),
  smtp_timeout: () => T('emailadd.err.smtp_timeout', 'The outgoing mail server did not answer in time. Nothing was saved.'),
  store_failed: () => T('emailadd.err.store_failed', 'VOOL could not save the change securely on this machine. Check local storage and try again.'),
  not_found: () => T('emailadd.err.not_found', 'There is no such email account.'),
  owner_local_required: () => T('emailadd.err.forbidden', 'Email accounts can only be changed inside the VOOL app on this machine.'),
};
async function emailSetupCall(action, payload) {
  try {
    const r = await fetch('/api/email/accounts/' + action, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await r.json().catch(() => ({}));
    return { ok: r.ok && !!j && j.ok === true, http: r.status, j: j || {} };
  } catch (e) { return { ok: false, http: 0, j: {} }; }
}
function emailSetupError(res) {
  const kind = String((res && res.j && res.j.error) || '');
  if (kind === 'invalid' && EMAIL_SETUP_FIELDS[res.j.field]) {
    return tfmt('emailadd.err.invalid_field', 'Check “{field}”. Nothing was saved.', { field: EMAIL_SETUP_FIELDS[res.j.field]() });
  }
  if (EMAIL_SETUP_ERRORS[kind]) return EMAIL_SETUP_ERRORS[kind]();
  if (res && res.http === 403) return EMAIL_SETUP_ERRORS.owner_local_required();
  return T('emailadd.err.failed', 'Something went wrong. Nothing was saved.');
}
/* The ports whose usual security is STARTTLS; any other port suggests SSL/TLS. Only a suggestion: a
   choice the person makes in the menu is kept. Mirrors core/email_setup.py default_security. */
const EMAIL_STARTTLS_PORTS = { imap: [143], smtp: [25, 587] };
function openEmailAddDialog(onSaved) {
  document.querySelector('dialog.email-add-dialog')?.remove();
  const dlg = document.createElement('dialog'); dlg.className = 'key-setup-dialog email-add-dialog';
  dlg.setAttribute('aria-label', T('emailadd.dialog_title', 'Add an email account'));
  dlg.appendChild(el('h3', null, T('emailadd.dialog_title', 'Add an email account')));
  dlg.appendChild(el('p', 'subtle', T('emailadd.intro', 'VOOL signs in to your mail server once to check these details. They are saved on this machine only after the server accepts them.')));
  dlg.appendChild(el('p', 'subtle', T('emailadd.app_password_hint', "Some providers need an app password, and some do not allow password sign-in for other apps at all. Your provider's security settings say which.")));
  const form = document.createElement('form'); form.className = 'stack email-add-form'; form.noValidate = true;
  const labelled = (name, control) => {
    const wrap = el('label', 'stack');
    wrap.appendChild(el('span', null, EMAIL_SETUP_FIELDS[name]()));
    control.name = name; control.setAttribute('aria-label', EMAIL_SETUP_FIELDS[name]());
    wrap.appendChild(control); form.appendChild(wrap);
    return control;
  };
  const field = (name, type, value, placeholder) => {
    const input = el('input', 'inp'); input.type = type; input.spellcheck = false;
    input.autocomplete = type === 'password' ? 'new-password' : 'off';
    if (value) input.value = value;
    if (placeholder) input.placeholder = placeholder;
    if (type === 'number') { input.min = '1'; input.max = '65535'; }
    return labelled(name, input);
  };
  const securityMenu = (name) => {
    const select = el('select', 'inp');
    [['ssl', T('emailadd.security_ssl', 'Encrypted from the start (SSL/TLS)')],
     ['starttls', T('emailadd.security_starttls', 'Upgraded to encryption after connecting (STARTTLS)')]].forEach(([value, text]) => {
      const option = document.createElement('option'); option.value = value; option.textContent = text; select.appendChild(option);
    });
    return labelled(name, select);
  };
  const address = field('address', 'email', '', 'name@example.com');
  const username = field('username', 'text');
  const password = field('password', 'password');
  const imapHost = field('imap_host', 'text', '', 'imap.example.com');
  const imapPort = field('imap_port', 'number', '993');
  const imapSecurity = securityMenu('imap_security');
  const smtpHost = field('smtp_host', 'text', '', 'smtp.example.com');
  const smtpPort = field('smtp_port', 'number', '465');
  const smtpSecurity = securityMenu('smtp_security');
  const followPort = (kind, port, menu) => {
    const suggest = () => { if (!menu.dataset.chosen) menu.value = EMAIL_STARTTLS_PORTS[kind].includes(Number(port.value)) ? 'starttls' : 'ssl'; };
    menu.addEventListener('change', () => { menu.dataset.chosen = '1'; });
    port.addEventListener('input', suggest); suggest();
  };
  followPort('imap', imapPort, imapSecurity);
  followPort('smtp', smtpPort, smtpSecurity);
  form.appendChild(el('p', 'subtle', T('emailadd.smtp_note', 'Without an outgoing server, VOOL can read this mailbox but not send from it.')));
  const replaceWrap = el('label', 'inline');
  const replace = el('input'); replace.type = 'checkbox'; replace.name = 'replace';
  replaceWrap.appendChild(replace); replaceWrap.appendChild(el('span', null, EMAIL_SETUP_FIELDS.replace()));
  form.appendChild(replaceWrap);
  const note = el('p', 'subtle email-add-state'); note.setAttribute('role', 'status');
  const save = el('button', 'btn primary email-add-save', T('emailadd.save', 'Check and save')); save.type = 'submit';
  const close = el('button', 'btn', T('keyguide.setup_close', 'Close')); close.type = 'button';
  const actions = el('div', 'inline'); actions.appendChild(save); actions.appendChild(close);
  form.appendChild(note); form.appendChild(actions);
  let busy = false;
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    if (busy) return;
    busy = true; save.disabled = true; close.disabled = true;
    note.textContent = T('emailadd.checking', 'Signing in to the mail server once…');
    const payload = { address: address.value.trim(), password: password.value, imap_host: imapHost.value.trim(),
                      imap_port: Number(imapPort.value) || 993, imap_security: imapSecurity.value, replace: replace.checked };
    password.value = '';
    if (username.value.trim()) payload.username = username.value.trim();
    if (smtpHost.value.trim()) {
      payload.smtp_host = smtpHost.value.trim(); payload.smtp_port = Number(smtpPort.value) || 465; payload.smtp_security = smtpSecurity.value;
    }
    const res = await emailSetupCall('add', payload);
    payload.password = '';
    busy = false; save.disabled = false; close.disabled = false;
    if (res.ok) {
      const done = tfmt('emailadd.saved', 'Saved. VOOL can now use {address}.', { address: res.j.address || payload.address });
      dlg.close();
      if (onSaved) await onSaved(done);
      return;
    }
    note.textContent = emailSetupError(res);
    password.focus();
  });
  close.addEventListener('click', () => { if (!busy) dlg.close(); });
  /* Escape would close the dialog in the middle of a check and lose its answer: wait for it. */
  dlg.addEventListener('cancel', (ev) => { if (busy) ev.preventDefault(); });
  dlg.appendChild(form);
  dlg.addEventListener('close', () => { password.value = ''; dlg.remove(); });
  document.body.appendChild(dlg);
  if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
  address.focus();
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
/* Why an account is the one in use, in words; the internal source ids never reach the page. */
const EMAIL_SOURCE_TEXT = {
  global: 'your default',
  chat: 'chosen for this chat',
  only_account: 'the only account',
  legacy_default: 'used because it is named “default”'
};
function widgetEmailAccounts(stack) {
  const src = state.sources['/api/email/accounts'];
  const err = state.sourceErr['/api/email/accounts'];
  const body = el('div', 'stack'); body.id = 'emailAccounts';
  const status = el('div', 'state idle'); status.id = 'emailAccountsStatus'; status.setAttribute('role', 'status');
  /* The list is redrawn after every change, so a message is written to the status line that is live then. */
  const sayNow = (msg, bad) => { const s = document.getElementById('emailAccountsStatus'); if (s) { s.className = 'state ' + (bad ? 'failed' : 'saved'); s.textContent = msg; } };
  const addRow = el('div', 'inline email-add-entry');
  const addBtn = el('button', 'btn email-add-open', T('emailadd.open', 'Add email account')); addBtn.type = 'button';
  addBtn.addEventListener('click', () => openEmailAddDialog(async (msg) => {
    await readSource('/api/email/accounts'); renderPane(); sayNow(msg);
    document.querySelector('button.email-add-open')?.focus();
  }));
  addRow.appendChild(addBtn);
  stack.appendChild(body);
  stack.appendChild(addRow);
  stack.appendChild(status);
  if (err) { body.appendChild(el('div', 'empty', tfmt('settings.widget.unavailable', 'Unavailable — {error}', { error: err }))); return; }
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
    /* No account: say so once, plainly, and nothing else -- no "Reading uses / Sending uses" lines that
       would name a mailbox that does not exist. The Add email account button above is the way in. */
    body.appendChild(el('div', 'empty', T('emailadd.empty', 'No email account is set up yet. Use Add email account to connect one.')));
    return;
  }
  accounts.forEach(a => {
    const rowEl = el('div', 'inline email-account'); rowEl.dataset.emailAccount = a.account;
    const pill = el('span', 'pill' + (a.state === 'configured' ? ' on' : ''));
    pill.appendChild(el('span', 'dot'));
    /* An account added from Settings is named by its address plus a hash; the address alone says it. */
    const derived = !!a.address && /-[0-9a-f]{10}$/.test(a.account);
    pill.appendChild(el('span', 'email-account-name', derived ? a.address : a.account + (a.address ? ' · ' + a.address : '')));
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
    /* A password account can be removed here; an account connected by its provider's sign-in cannot. */
    if (a.provider !== 'gmail' && a.provider !== 'graph') {
      const shown = a.address || a.account;
      const rm = el('button', 'btn danger email-remove', T('keys.remove', 'Remove')); rm.type = 'button'; rm.dataset.account = a.account;
      rm.addEventListener('click', async () => {
        if (!confirm(tfmt('emailadd.remove_confirm', 'Remove the email account “{account}” from this machine?\n\nIts saved password is deleted, and if it is your default account the default is cleared. Nothing changes on the mail server.', { account: shown }))) return;
        rm.disabled = true; say(T('emailadd.removing', 'Removing…'));
        const res = await emailSetupCall('remove', { account: a.account });
        if (!res.ok) { rm.disabled = false; say(tfmt('emailadd.not_removed', 'Not removed. {reason}', { reason: emailSetupError(res) }), true); return; }
        await refresh();
        if (res.j.default_error) sayNow(tfmt('emailadd.removed_default_kept', 'Removed {account}, but its default choice could not be cleared. Clear it below.', { account: shown }), true);
        else sayNow(tfmt('emailadd.removed', 'Removed {account}.', { account: shown }));
      });
      rowEl.appendChild(rm);
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
    const name = s.account === 'default' ? 'the account named “default”' : s.account;
    line.textContent = label + ': ' + (s.ok ? (name + (s.address ? ' (' + s.address + ')' : '') + (EMAIL_SOURCE_TEXT[s.source] ? ' — ' + EMAIL_SOURCE_TEXT[s.source] : '')) : ('none — ' + (s.message || 'no account can do this')));
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
  if (err) { body.appendChild(el('div', 'empty', tfmt('settings.widget.unavailable', 'Unavailable — {error}', { error: err }))); return; }
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
  summary.textContent = rec ? (src.summary || '') : (src.send_hold ? (src.summary || '') : T('settings.email.no_hold', 'Nothing is on hold. Sending is not paused.'));
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
/* Profile records in words: category ids, origins and scopes are the store's vocabulary, not the reader's. */
const PROFILE_CATEGORY_TEXT = { language: T('settings.profile.cat.language', 'Answer language'), preferred_name: T('settings.profile.cat.name', 'Your name'), email_signature: T('settings.profile.cat.signature', 'Email signature'), 'default_account.email': T('settings.profile.cat.default_email', 'Default email account') };
const PROFILE_ORIGIN_TEXT = { explicit: T('settings.profile.origin.explicit', 'You set this'), settings: T('settings.profile.origin.settings', 'You set this in Settings'), inferred: T('settings.profile.origin.inferred', 'VOOL picked this up in a chat'), imported: T('settings.profile.origin.imported', 'Imported') };
const PROFILE_SCOPE_TEXT = { global: T('settings.profile.scope.global', 'applies everywhere'), work: T('settings.profile.scope.work', 'work only'), personal: T('settings.profile.scope.personal', 'personal only'), chat: T('settings.profile.scope.chat', 'one chat only'), project: T('settings.profile.scope.project', 'one project only') };
/* Short scope names for the pill and the Move scope list (the store's ids stay the values). */
const PROFILE_SCOPE_NAME = { global: T('settings.profile.scope_name.global', 'Everywhere'), work: T('settings.profile.scope_name.work', 'Work'), personal: T('settings.profile.scope_name.personal', 'Personal'), chat: T('settings.profile.scope_name.chat', 'One chat'), project: T('settings.profile.scope_name.project', 'One project') };
/* Settings has its own Undo; a chat hint to "say 'undo that'" does not belong in its messages. */
function profileWords(text) {
  return String(text == null ? '' : text).replace(/\s*Say ['\u2018\u2019]undo that['\u2018\u2019] to restore\.?/gi, '').trim();
}
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
  /* The profile authority's reports end with a chat hint ("Say 'undo that' to restore."); in Settings the
     undo is a button on this page, and a fresh install may have no model to say it to. */
  const report = (res, fallback) => profileWords((res.data && res.data.change && res.data.change.report) || (res.data && (res.data.error || res.data.detail)) || fallback);
  let current = null;

  const draw = async () => {
    body.textContent = '';
    body.appendChild(el('div', 'empty', T('settings.profile.reading', 'Reading what VOOL remembers…')));
    let p;
    try { p = await getJSON('/api/profile'); }
    catch (e) { body.textContent = ''; body.appendChild(el('div', 'empty', 'Unavailable — ' + e.message)); return; }
    current = p;
    /* The answer-language row in General reads the same profile from the page cache: keep it current,
       so a pause or an edit made here is seen there without a reload. */
    state.sources['/api/profile'] = p; delete state.sourceErr['/api/profile'];
    body.textContent = '';
    /* Store-wide: Pause/Resume (POST /api/profile/pause) and Export (GET /api/profile/export). */
    const bar = el('div', 'inline');
    const pause = el('button', 'btn', p.paused ? T('settings.profile.resume', 'Resume memory') : T('settings.profile.pause', 'Pause memory')); pause.type = 'button'; pause.id = 'profilePauseBtn';
    pause.setAttribute('aria-pressed', p.paused ? 'true' : 'false');
    pause.addEventListener('click', async () => {
      pause.disabled = true;
      const res = await post('pause', { paused: !p.paused });
      if (!res.ok) { pause.disabled = false; say('Not changed — ' + report(res, 'HTTP ' + res.status), true); return; }
      await draw();
      say(res.data.paused ? T('settings.profile.paused_note', 'Memory paused: VOOL keeps what it knows and learns nothing new until you resume.') : T('settings.profile.resumed_note', 'Memory resumed: VOOL learns from conversation again.'));
    });
    const exp = el('button', 'btn', T('settings.profile.export', 'Export profile')); exp.type = 'button'; exp.id = 'profileExportBtn';
    const out = el('pre', 'mono profile-export'); out.id = 'profileExportOut'; out.hidden = true;
    /* Export gives a file. It never overwrites the clipboard, which may hold something the person copied. */
    exp.addEventListener('click', async () => {
      exp.disabled = true;
      try {
        const doc = await getJSON('/api/profile/export');
        const text = JSON.stringify(doc, null, 2);
        out.textContent = text; out.hidden = true; view.hidden = false; view.textContent = T('settings.profile.show_export', 'Show what was exported');
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
        a.download = 'vool-memory-' + new Date().toISOString().slice(0, 10) + '.json';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        say('Saved as a file: ' + a.download + '. It holds what VOOL remembers, never a key or password.');
      } catch (e) { say('Could not export — ' + e.message, true); }
      exp.disabled = false;
    });
    /* The exported document stays folded away; it is there to look at on request, and closes again. */
    const view = el('button', 'btn', T('settings.profile.show_export', 'Show what was exported')); view.type = 'button'; view.hidden = true;
    view.addEventListener('click', () => { out.hidden = !out.hidden; view.textContent = out.hidden ? T('settings.profile.show_export', 'Show what was exported') : T('keys.hide', 'Hide'); });
    bar.appendChild(pause); bar.appendChild(exp); bar.appendChild(view);
    body.appendChild(bar);
    if (p.paused) body.appendChild(el('div', 'subtle', 'Memory is paused: VOOL is not learning anything new right now. What it already remembers stays in force and stays editable below.'));
    body.appendChild(out);
    const items = (p.items || []).filter(it => it.status !== 'deleted');
    body.appendChild(profileNameField(items, !!p.paused));
    if (!items.length) { body.appendChild(el('div', 'empty', T('settings.profile.empty', 'Nothing remembered yet. As you chat, what VOOL learns about you is listed here, and you can edit or forget any of it.'))); return; }
    /* Scopes an item can be moved to from here. A chat- or project-scoped move needs the chat or
       project it belongs to, which Settings does not have -- those two are done where they apply. */
    const movable = (p.scopes || ['global', 'work', 'personal']).filter(s => s !== 'chat' && s !== 'project');
    items.forEach(it => body.appendChild(profileItemRow(it, movable)));
    body.appendChild(el('div', 'subtle', (items.length === 1 ? T('settings.profile.count_one', '1 remembered item.') : tfmt('settings.profile.count_many', '{count} remembered items.', { count: items.length }))
      + ' ' + T('settings.profile.count_note', 'Restore previous undoes the latest change to an item. A suggestion VOOL made in a chat is decided in that chat.')));
  };

  /* "What should VOOL call you?": the setup step points here, so the step can be done here. It writes the
     same preferred_name profile item through the same door the setup page uses (POST /api/settings/prefs
     user_address -> the Operator Profile, origin settings); an empty field forgets the name. */
  function profileNameField(items, paused) {
    const wrap = el('div', 'stack profile-name');
    const nameItem = items.find(it => it.category === 'preferred_name' && (it.scope || 'global') === 'global');
    wrap.appendChild(el('div', 'row-label', T('settings.profile.name_label', 'What should VOOL call you?')));
    const line = el('div', 'inline');
    const input = document.createElement('input'); input.type = 'text'; input.className = 'inp'; input.id = 'profileNameInput';
    input.maxLength = 60; input.autocomplete = 'off'; input.placeholder = T('settings.profile.name_placeholder', 'A first name or nickname');
    input.setAttribute('aria-label', T('settings.profile.name_label', 'What should VOOL call you?'));
    input.value = nameItem ? String(nameItem.value_text || nameItem.value || '') : '';
    const save = el('button', 'btn primary', T('settings.profile.name_save', 'Save name')); save.type = 'button'; save.id = 'profileNameSave';
    const st = el('span', 'state idle'); st.setAttribute('role', 'status');
    if (paused) {
      input.disabled = true; save.disabled = true;
      st.className = 'state stale'; st.textContent = T('settings.profile.name_paused', 'Memory is paused. Resume memory to change your name.');
    }
    const send = async () => {
      const value = input.value.trim();
      save.disabled = true; st.className = 'state saving'; st.textContent = 'Saving…';
      try {
        const r = await fetch('/api/settings/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user_address: value }) });
        const j = await r.json().catch(() => null);
        if (!r.ok || (j && j.error)) throw new Error(profileWords((j && j.error) || ('HTTP ' + r.status)));
        widgetCache.profileNote = value ? tfmt('settings.profile.name_saved', 'Saved. VOOL will call you {name}.', { name: value }) : T('settings.profile.name_cleared', 'Name cleared.');
        await draw();
      } catch (e) { save.disabled = false; st.className = 'state failed'; st.textContent = 'Not saved — ' + e.message; }
    };
    save.addEventListener('click', send);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
    if (widgetCache.profileNote) { st.className = 'state saved'; st.textContent = widgetCache.profileNote; widgetCache.profileNote = ''; }
    line.appendChild(input); line.appendChild(save); line.appendChild(st);
    wrap.appendChild(line);
    return wrap;
  }

  function profileItemRow(it, movable) {
    const rowEl = el('div', 'stack profile-item'); rowEl.dataset.profileItem = it.item_id;
    const head = el('div', 'inline');
    const shown = (it.value_text != null && it.value_text !== '') ? it.value_text : (it.value == null ? '' : it.value);
    head.appendChild(el('span', 'profile-value', (PROFILE_CATEGORY_TEXT[it.category] || it.label || it.category || 'Item') + ': ' + String(shown)));
    const scopePill = el('span', 'pill profile-scope-pill', (it.scope || 'global') + (it.scope_key ? ' · ' + it.scope_key : ''));
    scopePill.title = PROFILE_SCOPE_TEXT[it.scope || 'global'] || '';
    head.appendChild(scopePill);
    rowEl.appendChild(head);
    const whenUsed = it.last_used_at ? new Date(it.last_used_at).toLocaleString() : '';
    rowEl.appendChild(el('div', 'subtle', (PROFILE_ORIGIN_TEXT[it.origin] || ('From ' + (it.origin || 'VOOL'))) + ' · ' + (PROFILE_SCOPE_TEXT[it.scope || 'global'] || it.scope) + (whenUsed && whenUsed !== 'Invalid Date' ? ' · ' + tfmt('settings.profile.last_used', 'last used {when}', { when: whenUsed }) : ' · ' + T('settings.profile.not_used', 'not used yet'))));
    const run = async (label, action, payload, okText, plainOk) => {
      const res = await post(action, payload);
      if (res.status === 409) { say(T('settings.profile.changed_elsewhere', 'This item changed elsewhere. The list was reloaded; try again.'), true); await draw(); return; }
      say(res.ok && plainOk ? okText : report(res, res.ok ? okText : ('Could not ' + label.toLowerCase() + '.')), !res.ok);
      if (res.ok) await draw();
    };
    const acts = el('div', 'inline');
    /* Edit: an inline field bound to POST /api/profile/item, CAS-guarded by the revision shown. */
    const edit = el('button', 'btn profile-edit', T('settings.profile.edit', 'Edit')); edit.type = 'button';
    const editor = el('div', 'inline'); editor.hidden = true;
    /* The answer language is edited with the same list the General row offers, so Memory cannot store a
       language VOOL then ignores. */
    let input;
    if (it.category === 'language') {
      input = el('select', 'inp profile-edit-input');
      LANGUAGES.forEach(l => { const o = el('option', null, l.label); o.value = l.label; input.appendChild(o); });
      input.value = String(it.value_text || '');
    } else {
      input = document.createElement('input'); input.className = 'inp profile-edit-input'; input.type = 'text';
      input.value = String(it.value_text || '');
    }
    input.setAttribute('aria-label', tfmt('settings.profile.new_value', 'New value for {item}', { item: PROFILE_CATEGORY_TEXT[it.category] || it.label || it.category }));
    const saveBtn = el('button', 'btn primary profile-edit-save', 'Save'); saveBtn.type = 'button';
    const cancel = el('button', 'btn', T('xsetup.cancel', 'Cancel')); cancel.type = 'button';
    edit.addEventListener('click', () => { editor.hidden = !editor.hidden; if (!editor.hidden) input.focus(); });
    cancel.addEventListener('click', () => { editor.hidden = true; input.value = String(it.value_text || ''); });
    saveBtn.addEventListener('click', () => run('Edit', 'item', { item_id: it.item_id, value: input.value, expected_revision: it.revision }, T('settings.profile.saved', 'Saved.')));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); saveBtn.click(); } });
    editor.appendChild(input); editor.appendChild(saveBtn); editor.appendChild(cancel);
    const forget = el('button', 'btn danger profile-forget', T('settings.profile.forget', 'Forget')); forget.type = 'button';
    /* Forget acts at once, so the page offers its own Undo for a short while: it restores the item's
       previous revision through the same restore door as "Restore previous". */
    forget.addEventListener('click', async () => {
      await run('Forget', 'forget', { item_id: it.item_id, expected_revision: it.revision }, T('settings.profile.forgotten', 'Forgotten. You can undo this for a few seconds.'), true);
      if (status.className.indexOf('failed') >= 0) return;
      const undo = el('button', 'retry', T('settings.profile.undo', 'Undo')); undo.type = 'button';
      undo.addEventListener('click', async () => {
        undo.remove();
        const res = await post('restore', { item_id: it.item_id });
        say(res.ok ? T('settings.profile.brought_back', 'Brought back.') : (T('settings.profile.undo_failed', 'Could not undo.') + ' ' + report(res, 'HTTP ' + res.status)), !res.ok);
        if (res.ok) await draw();
      });
      status.appendChild(document.createTextNode(' ')); status.appendChild(undo);
      setTimeout(() => undo.remove(), 10000);
    });
    const scopeSel = el('select', 'inp profile-scope-select'); scopeSel.setAttribute('aria-label', tfmt('settings.profile.scope_for', 'Where {item} applies', { item: PROFILE_CATEGORY_TEXT[it.category] || it.label || it.category }));
    const options = movable.includes(it.scope) ? movable : [it.scope].concat(movable);
    options.forEach(s => { const o = el('option', null, PROFILE_SCOPE_NAME[s] || s); o.value = s; if (s === it.scope) o.selected = true; scopeSel.appendChild(o); });
    const move = el('button', 'btn profile-scope-move', T('settings.profile.move', 'Move scope')); move.type = 'button';
    move.addEventListener('click', () => {
      if (scopeSel.value === it.scope) { say(T('settings.profile.already_there', 'It already applies there.')); return; }
      run('Move scope', 'scope', { item_id: it.item_id, scope: scopeSel.value, expected_revision: it.revision }, T('settings.profile.moved', 'Moved.'));
    });
    const restore = el('button', 'btn profile-restore', T('settings.profile.restore', 'Restore previous')); restore.type = 'button';
    restore.hidden = Number(it.revision || 1) <= 1;   /* a first version has nothing earlier to restore */
    restore.addEventListener('click', () => run('Restore previous', 'restore', { item_id: it.item_id }, T('settings.profile.restored', 'Restored.')));
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
  const labelHit = preferredRow(state.query) || ALL_ROWS.find(r => labelMatches(r, state.query));
  const first = labelHit ? MODEL.find(g => g.id === labelHit.group) : MODEL.find(g => hitsFor(g.id, state.query) > 0);
  if (state.query && first) {
    const firstRow = labelHit || ALL_ROWS.find(r => r.group === first.id && matches(r, state.query));
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
    if (e.key === 'Escape') { if (!e.defaultPrevented && escapeClosesInnermost(typing)) e.preventDefault(); return; }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && document.activeElement && document.activeElement.classList.contains('nav-item')) {
      e.preventDefault();
      const items = [...document.querySelectorAll('.nav-item')];
      const i = items.indexOf(document.activeElement);
      const next = items[e.key === 'ArrowDown' ? Math.min(i + 1, items.length - 1) : Math.max(i - 1, 0)];
      if (next && next.dataset.group) go(next.dataset.group);   /* renderNav re-focuses the new current item */
    }
  });
  window.addEventListener('focus', () => { reconcile(); });
  /* Typed-but-unsaved text in the pane keeps Escape from closing Settings (escapeClosesInnermost). */
  document.addEventListener('input', e => { const t = e.target; if (t && t.closest && t.closest('#paneBody') && t.dataset) t.dataset.typed = '1'; });
  document.addEventListener('change', e => { const t = e.target; if (t && t.dataset && t.dataset.typed && t.type !== 'password') delete t.dataset.typed; });
}

/* Escape closes the innermost open thing, never more: an open dialog or sheet first (a native
   <dialog> closes itself), then nothing at all while focus sits on a control or a field in the
   content. Settings itself closes on Escape only when nothing is open and focus is on the page or
   the section list, and never while a field in the pane holds typed text. Returns whether it acted. */
function escapeClosesInnermost(typing) {
  if (document.querySelector('dialog[open]')) return false;           /* the dialog handles its own Escape */
  const sheets = [...document.querySelectorAll('.mp-sheet, [role="dialog"], [aria-modal="true"]')]
    .filter(n => !n.hidden && n.offsetParent !== null);
  if (sheets.length) {
    const top = sheets[sheets.length - 1];
    const close = top.querySelector('.mp-sheet-close, [data-close], .btn-close');
    if (close) close.click();
    return true;
  }
  if (typing) return false;
  const a = document.activeElement;
  const onPage = !a || a === document.body || a === document.documentElement || a.id === 'pane'
    || a.id === 'back' || (a.classList && a.classList.contains('nav-item'));
  if (!onPage) return false;
  const typed = [...document.querySelectorAll('#paneBody [data-typed="1"]')].some(i => i.value);
  if (typed) return false;
  closeSettingsWindow();
  return true;
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
  /* A link to the setup checklist after it was hidden says so instead of quietly opening another section. */
  state.notice = (!group && parts[0] === 'setup') ? 'setup_hidden' : '';
  if (group && parts[1] && group.rows.some(r => r.id === parts[1])) {
    state.focusRow = parts[1]; state.onlyRow = parts[1]; state.focusPart = parts[2] || '';
  }
  return group ? group.id : '';
}

async function boot() {
  $('#back').addEventListener('click', closeSettingsWindow);
  $('#offlineRetry').addEventListener('click', () => { reconcile(); recheckUnsure(true); });
  $('#skipToPane').addEventListener('click', e => { e.preventDefault(); $('#pane').focus(); });
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
    the selector never implies a fully translated product it cannot deliver. A catalog where
    every key resolves in-locale is called complete — but surfaces not yet wired to the
    catalog stay named here in English until they are, so "complete catalog" never gets read
    as "every screen ships in this language".
    """
    from core.i18n.catalog import catalog_for

    catalog = catalog_for(tag)
    total = len(catalog.keys)
    # Supplied-and-accepted keys, not text-difference: a technical identifier ("PIN") is a
    # legitimate byte-identical translation and still counts toward completeness.
    translated = len(catalog.locale_keys & set(catalog.keys))
    # A string count is translator metadata, not something a reader acts on: English and a complete
    # catalog get no count, and only a partial translation is mentioned at all.
    if tag == "en":
        return "English (the source language)"
    fallback = total - translated
    if fallback == 0:
        return ""
    return catalog.format("settings.language_coverage_partial", translated=translated, total=total, fallback=fallback)


# The always-discoverable app-language control, top-left of Settings (top of the side nav,
# above search). Globe symbol + each language's own name (the picker fills the options from
# the locale registry's endonyms — never flags, never English exonyms alone). This is the
# APP UI language: it changes these screens only. The model ANSWER language keeps its own
# existing Settings widget (General → Answer language) and dictation recognition keeps its
# own control next to the microphone; the note under the select says so in the selected
# language (settings.language_help, an existing catalog key).
_LOCALE_BLOCK = """<div id="uiLocaleWrap">
  <label class="loc-label" for="uiLocaleSelect"><span class="loc-globe vi" data-ico="globe" aria-hidden="true"></span><span class="loc-word" data-i18n-html="settings.language_heading">Language <span class="set-sub">screens only &mdash; never the answer language</span></span></label>
  <select id="uiLocaleSelect" aria-label="App language — these screens only, never the answer language" title="Choose the language of these screens and controls. It never changes the language VOOL answers in." data-i18n-aria-label="settings.language_select_aria" data-i18n-title="settings.language_select_title"></select>
  <p class="loc-note" data-i18n-html="settings.language_help">Choose the language of these screens and controls. It never changes the language VOOL answers in &mdash; that has its own setting.</p>
  <p class="loc-note" id="uiLocaleCoverage"></p>
</div>
"""
_LOCALE_HELP_KEYED = (
    '<p class="loc-note" data-i18n-html="settings.language_help">Choose the language of these screens and controls. '
    "It never changes the language VOOL answers in &mdash; that has its own setting.</p>"
)
_LOCALE_HELP_EN = (
    '<p class="loc-note">Language of menus and buttons. To change the language VOOL replies in, see '
    '<a href="#general/answer_language">General &rarr; Answer language</a>.</p>'
)


def _localized_settings_groups(ui_locale: str) -> list[dict]:
    """The settings model with its operator-facing strings resolved through the i18n catalog.

    English is byte-identical to ``settings_groups()`` — the model's own literals ARE the
    English catalog source (a test pins the byte equality). Any other locale swaps each
    group title/blurb and row label/help/placeholder/effect/option-label for the catalog
    text under the stable ``settings.group.<id>.*`` / ``settings.row.<id>.*`` keys, falling
    back visibly to English per the engine's law. Search keywords stay as authored: they
    are a match aid against typed queries, not displayed prose, and translated keywords
    would silently narrow what the search box finds.
    """
    import copy

    from core.i18n.catalog import catalog_for

    groups = settings_groups()
    catalog = catalog_for(ui_locale)
    if catalog.locale == "en":
        return groups
    english = catalog_for("en")
    en_keys = set(english.keys)

    def resolved(key: str, fallback: str) -> str:
        if key not in en_keys:
            return fallback
        # Per-key form of the catalog's staleness law: a translation is only as current as the
        # English it was made from. When the English text in the model has changed since the
        # catalog's source, the old translation may say something no longer true (a removed
        # file path, an old option name), so the current English is shown until the catalog
        # is refreshed.
        if english.text(key) != fallback:
            return fallback
        return catalog.text(key)

    localized = copy.deepcopy(groups)
    for group in localized:
        group["title"] = resolved(f"settings.group.{group['id']}.title", group["title"])
        if group.get("blurb"):
            group["blurb"] = resolved(f"settings.group.{group['id']}.blurb", group["blurb"])
        for row in group.get("rows", []):
            rid = row["id"]
            row["label"] = resolved(f"settings.row.{rid}.label", row["label"])
            for field in ("help", "placeholder", "effect"):
                if row.get(field):
                    row[field] = resolved(f"settings.row.{rid}.{field}", row[field])
            for option in row.get("options") or []:
                option["label"] = resolved(
                    f"settings.row.{rid}.option.{option['value']}", option["label"]
                )
    return localized


def render_vool_settings_html(*, build_commit: str = "", ui_locale: str = "en", appearance: tuple[str, int] | None = None) -> str:
    """The Settings page: one declarative model, rendered by one script, bound to the authorities
    that already own each value."""
    model = json.dumps(_localized_settings_groups(ui_locale), separators=(",", ":"), ensure_ascii=False)
    catalog = json.dumps(language_catalog(), separators=(",", ":"), ensure_ascii=False)
    from core.addon_store_fragment import render_addon_store_fragment
    from core.calendar_settings_fragment import render_calendar_settings_fragment
    from core.chat_import_settings_fragment import render_chat_import_settings_fragment
    from core.notification_settings_fragment import render_notification_settings_fragment
    from core.provider_key_guide import provider_key_guide
    from core.settings_extras_fragment import render_settings_extras_fragment
    from core.wallet_fragment import render_wallet_fragment

    # the wallet section and the settings extras (learned facts, privacy disclosure, Toolbelt) live
    # here: each fragment renders itself into the host its group provides
    page = (_SETTINGS_HTML + _SETTINGS_JS + _SETTINGS_JS2).replace(
        "__VOOL_PALETTE_CSS__", DARK_PALETTE_CSS + "\n" + LIGHT_PALETTE_CSS + "\n" + ICON_CSS
    ).replace("/*__VOOL_APPEARANCE_JS__*/", APPEARANCE_JS, 1).replace(
        "</body>", render_addon_store_fragment() + "\n" + render_wallet_fragment() + "\n" + render_settings_extras_fragment() + "\n" + render_calendar_settings_fragment()
        + "\n" + render_notification_settings_fragment() + "\n" + render_chat_import_settings_fragment() + "\n</body>", 1
    )
    tag, direction, bootstrap = _ui_locale_head(ui_locale)
    # The same VOOL mark the chat tab uses, inline, so the Settings tab is recognisable and the
    # browser does not request a /favicon.ico that is not served.
    from base64 import b64encode
    from pathlib import Path

    logo = Path(__file__).with_name("assets") / "vool-logo-dark.png"
    logo_uri = ("data:image/png;base64," + b64encode(logo.read_bytes()).decode("ascii")) if logo.is_file() else "data:,"
    return (
        page.replace("__SETTINGS_MODEL__", model)
        .replace("__VOOL_LOGO_URI__", logo_uri, 1)
        .replace("__LANGUAGE_CATALOG__", catalog)
        .replace("__KEY_PROVIDER_GUIDE__", json.dumps(provider_key_guide(tag), ensure_ascii=False).replace("<", "\\u003c"))
        .replace("__PAGE_BUILD_COMMIT__", str(build_commit or "").strip())
        # The i18n bootstrap must run before the page's own script: it defines VOOLT.
        .replace(
            "<script>\nconst MODEL",
            bootstrap + "\n<script>\nconst MODEL",
            1,
        )
        .replace('<html lang="en">', f'<html lang="{tag}" dir="{direction}"{appearance_html_attrs(appearance)}>')
        .replace('<div id="backRow">', _LOCALE_BLOCK + '<div id="backRow">', 1)
        .replace('<p class="loc-note" id="uiLocaleCoverage"></p>',
                 f'<p class="loc-note" id="uiLocaleCoverage">{_locale_coverage_note(tag)}</p>', 1)
        # English: the help names the real place of the answer-language control, as a link. The keyed
        # catalog text ("…in the operator profile") points at a place that does not exist; other
        # locales keep their translation until the catalog carries the corrected sentence.
        # English gets the same sentence with a working link; other languages get the catalog's
        # translation of it (a catalog message carries no link markup).
        .replace(_LOCALE_HELP_KEYED, _LOCALE_HELP_EN if tag == "en" else _LOCALE_HELP_KEYED, 1)
    )
