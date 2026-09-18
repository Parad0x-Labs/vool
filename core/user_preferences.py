from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.runtime_paths import data_path


@dataclass
class UserPreferences:
    humor_percent: int = 20
    user_address: str = ""  # how VOOL addresses the user (a name/handle), e.g. "Alex", "boss", "ser"
    deep_reasoning: bool = False  # local thinking model reasons before answering — OFF by default (fast chat)
    character_mode: str = ""
    tone_hint: str = "direct"
    boundaries_mode: str = "user_defined"
    profanity_level: int = 40
    style_notes: str = ""
    communication_style: str = "casual"
    email_signature: str = ""
    autonomy_mode: str = "hands_off"
    show_workflow: bool = False
    hive_followups: bool = True
    idle_research_assist: bool = True
    accept_hive_tasks: bool = True
    social_commons: bool = True
    ram_reserve_pct: int = 20       # RAM % the governor keeps free for the user (higher = VOOL uses less)
    daily_token_budget: int = 0     # soft cap on cloud tokens/day (0 = unlimited); surfaced against real usage
    timezone: str = ""              # IANA timezone identity, e.g. "Europe/Berlin"; empty = UTC fallback
    wallet_enabled: bool = False    # 🧪 experimental crypto wallet -- OFF by default; the Settings switch, not an env var
    wallet_enabled_generation: int = 0  # counts changes of the switch; a signed wallet transfer compares it before sending
    speech_notice_dismissed: bool = False  # durable even in private native web sessions
    # First-run setup (core/setup_progress.py). The flow persists ONLY these two: which steps the
    # user skipped and whether the "Complete setup" reminder was dismissed. Done-ness is never
    # stored -- it is derived from the authorities that own each value.
    setup_skipped_steps: str = ""   # comma-separated setup step ids the user chose to skip
    setup_dismissed: bool = False   # "Don't remind me" on the Complete-setup entry
    # Provenance for a preference that always has a value: autonomy_mode defaults to hands_off, so
    # the field alone cannot say whether anyone CHOSE it. Every door that writes autonomy_mode
    # stamps this; a value without a stamp is the default, not a decision.
    autonomy_chosen_at: str = ""


_PREFS_FILE = "user_preferences.json"
_HUMOR_RE = re.compile(r"(?:set\s+)?humou?r\s*[:=]?\s*(\d{1,3})\s*%?", re.IGNORECASE)
_CHAR_RE = re.compile(
    r"^(?:set\s+persona|act\s+like|act\s+as|be\s+like|be\s+(?:a\s+)?|character|roleplay\s+as|pretend\s+(?:to\s+be|you(?:'re| are)\s+)|play\s+(?:the\s+role\s+of|as)|i\s+name\s+(?:you|u)|your?\s+name\s+(?:is|now)|you\s+are\s+now|from\s+now\s+on\s+you(?:'re| are))\s*[:=]?\s*(.+)$",
    re.IGNORECASE,
)
# Deep-reasoning ("thinking") toggle — off by default, switchable on. Kept clear of the think_harder
# mux so it does not collide with it.
_ENABLE_REASONING_RE = re.compile(
    r"^(?:turn on|enable|switch on)\s+(?:deep\s+)?(?:reasoning|thinking)\b"
    r"|^(?:deep\s+)?(?:reasoning|thinking)\s+(?:on|mode)\b"
    r"|^think\s+(?:deeply|deep|harder|more|it through)\b"
    r"|^(?:reason|think)\s+(?:harder|deeper)\b",
    re.IGNORECASE,
)
_DISABLE_REASONING_RE = re.compile(
    r"^(?:turn off|disable|switch off|stop)\s+(?:deep\s+)?(?:reasoning|thinking)\b"
    r"|^(?:deep\s+)?(?:reasoning|thinking)\s+off\b"
    r"|^fast\s+(?:mode|replies|answers)\b"
    r"|^stop\s+(?:over[\s-]?thinking|thinking\s+so\s+hard)\b",
    re.IGNORECASE,
)
_BOUNDARIES_RE = re.compile(r"^(?:set\s+)?boundaries\s*[:=]?\s*(relaxed|standard|strict)\b", re.IGNORECASE)
_PROFANITY_RE = re.compile(r"^(?:set\s+)?profanity\s*[:=]?\s*(\d{1,3})\s*%?", re.IGNORECASE)
_AUTONOMY_RE = re.compile(
    r"^(?:set\s+)?(?:autonomy|security level|security mode|execution mode)\s*[:=]?\s*(hands[\s_-]?off|balanced|strict)\b",
    re.IGNORECASE,
)
# Explicit communication-style command. Kept specific ("... style: cheeky") so it never swallows
# free-form style notes like "be concise" (handled separately below).
_COMM_STYLE_RE = re.compile(
    r"^(?:set\s+)?(?:comm(?:unication)?s?\s*style|talk\s*style|conversation\s*style|style)\s*(?:to\s*)?[:=]?\s*(business|casual|cheeky)\b",
    re.IGNORECASE,
)
_NO_MICRO_APPROVAL_RE = re.compile(
    r"(?:don't|do not|stop)\s+(?:ask(?:ing)?\s+for\s+)?(?:micro|tiny|every)\s+step\s+approval",
    re.IGNORECASE,
)
_ONLY_SIGNIFICANT_APPROVAL_RE = re.compile(
    r"(?:ask|only ask)\s+(?:me\s+)?(?:only\s+)?(?:for\s+)?approval.+(?:significant|security|risky|destructive|leak)",
    re.IGNORECASE,
)
_SHOW_WORKFLOW_RE = re.compile(
    r"^(?:show|enable)\s+(?:your\s+)?(?:workflow|thinking|thinking flow|reasoning summary|work log)\b",
    re.IGNORECASE,
)
_HIDE_WORKFLOW_RE = re.compile(
    r"^(?:hide|disable)\s+(?:your\s+)?(?:workflow|thinking|thinking flow|reasoning summary|work log)\b",
    re.IGNORECASE,
)
_ENABLE_HIVE_FOLLOWUPS_RE = re.compile(
    r"^(?:show|enable|turn on)\s+(?:hive|brain hive|research)\s+(?:followups|updates|heartbeat|heartbeat updates)\b",
    re.IGNORECASE,
)
_DISABLE_HIVE_FOLLOWUPS_RE = re.compile(
    r"^(?:hide|disable|stop)\s+(?:hive|brain hive|research)\s+(?:followups|updates|heartbeat|heartbeat updates)\b",
    re.IGNORECASE,
)
_ENABLE_IDLE_ASSIST_RE = re.compile(
    r"^(?:help|assist|jump in)\s+(?:with\s+)?research\s+(?:when|if)\s+idle\b|^(?:enable|turn on)\s+idle research assist\b",
    re.IGNORECASE,
)
_DISABLE_IDLE_ASSIST_RE = re.compile(
    r"^(?:don't|do not|stop|disable|turn off)\s+(?:help|assisting|assist)?\s*(?:with\s+)?research\s+(?:when|if)\s+idle\b|^(?:disable|turn off)\s+idle research assist\b",
    re.IGNORECASE,
)
_ENABLE_HIVE_TASKS_RE = re.compile(
    r"^(?:accept|take|resume|enable|turn on)\s+(?:hive|swarm|shared|available\s+)?(?:tasks|research tasks|hive tasks|swarm tasks)\b|^(?:you can|you may)\s+(?:take|accept)\s+(?:hive|swarm|available\s+)?tasks\b",
    re.IGNORECASE,
)
_DISABLE_HIVE_TASKS_RE = re.compile(
    r"^(?:don't|do not|stop|disable|turn off)\s+(?:take|accept|claim|pull|help with)?\s*(?:any\s+)?(?:hive|swarm|shared|available\s+)?(?:tasks|research tasks|hive tasks|swarm tasks)\b|^(?:stay|remain)\s+visible\s+but\s+don't\s+take\s+tasks\b",
    re.IGNORECASE,
)
_ENABLE_SOCIAL_COMMONS_RE = re.compile(
    r"^(?:enable|turn on|resume|allow)\s+(?:agent\s+)?(?:commons|hangout|social(?:is(?:e|ing)|iz(?:e|ing))|brainstorm(?:ing)?)\b",
    re.IGNORECASE,
)
_DISABLE_SOCIAL_COMMONS_RE = re.compile(
    r"^(?:disable|turn off|stop|pause)\s+(?:agent\s+)?(?:commons|hangout|social(?:is(?:e|ing)|iz(?:e|ing))|brainstorm(?:ing)?)\b|^(?:don't|do not)\s+(?:let|have)\s+you\s+(?:sociali[sz]e|brainstorm|hang out)\b",
    re.IGNORECASE,
)
# How the USER wants to be addressed — distinct from renaming VOOL ("call YOU X" / "YOUR name is X"
# are handled by _RENAME_PATTERNS below). "call me / my name is / address me as X" set user_address.
_ADDRESS_ME_RE = re.compile(
    r"^(?:you\s+can\s+)?(?:call\s+me|address\s+me\s+as|refer\s+to\s+me\s+as|my\s+name\s+is|my\s+name'?s|i\s+go\s+by)\s+(.+)$",
    re.IGNORECASE,
)
_FORGET_ADDRESS_RE = re.compile(
    r"^(?:stop\s+calling\s+me|don'?t\s+call\s+me\s+(?:that|anything)|forget\s+my\s+name|clear\s+my\s+name)\b",
    re.IGNORECASE,
)
_RENAME_PATTERNS = [
    re.compile(r"^(?:now\s+)?you\s+are\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:i\s+(?:am\s+)?)?renam(?:e|ing)\s+you\s+to\s+(.+)$", re.IGNORECASE),
    re.compile(r"^rename\s+(?:yourself|you)\s+to\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:from\s+now\s+on\s+)?your\s+name\s+is\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:i\s+)?call\s+you\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:i(?:'m|\s+am)?\s+calling\s+you)\s+(.+)$", re.IGNORECASE),
]


def _prefs_path() -> Path:
    return data_path(_PREFS_FILE)


def default_preferences() -> UserPreferences:
    return _apply_edition_clamps(UserPreferences())


def _apply_edition_clamps(prefs: UserPreferences) -> UserPreferences:
    """Product-edition clamps applied on every load and every save.

    SCHOOL pins ``profanity_level`` to 0 (the runtime preference, default 40,
    is a persona knob PERSONAL users keep) and forces ``accept_hive_tasks``
    off: the hive intake lane does not exist in that edition. Applied at the
    preference boundary so every consumer — persona context, settings page,
    agent presence — reads the clamped truth (goal §4, operator law).
    """
    try:
        from core.product_edition import edition_allows, edition_profanity_ceiling

        ceiling = edition_profanity_ceiling()
        if ceiling < int(prefs.profanity_level):
            prefs.profanity_level = _clamp_pct(ceiling)
        hive_ok, _reason = edition_allows("hive_task_intake")
        if not hive_ok:
            prefs.accept_hive_tasks = False
    except Exception:
        pass
    return prefs


def load_preferences() -> UserPreferences:
    path = _prefs_path()
    if not path.exists():
        return default_preferences()  # already edition-clamped
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        prefs = UserPreferences(
            humor_percent=_clamp_pct(int(raw.get("humor_percent", 20))),
            user_address=str(raw.get("user_address", "")).strip()[:60],
            deep_reasoning=bool(raw.get("deep_reasoning", False)),
            character_mode=str(raw.get("character_mode", "")).strip()[:120],
            tone_hint=str(raw.get("tone_hint", "direct")).strip()[:32] or "direct",
            boundaries_mode=_normalize_boundaries(str(raw.get("boundaries_mode", "user_defined"))),
            profanity_level=_clamp_pct(int(raw.get("profanity_level", 40))),
            style_notes=str(raw.get("style_notes", "")).strip()[:400],
            communication_style=_normalize_communication_style(str(raw.get("communication_style", "casual"))),
            email_signature=str(raw.get("email_signature", "")).strip()[:200],
            autonomy_mode=_normalize_autonomy(str(raw.get("autonomy_mode", "hands_off"))),
            show_workflow=bool(raw.get("show_workflow", False)),
            hive_followups=bool(raw.get("hive_followups", True)),
            idle_research_assist=bool(raw.get("idle_research_assist", True)),
            accept_hive_tasks=bool(raw.get("accept_hive_tasks", True)),
            social_commons=bool(raw.get("social_commons", True)),
            wallet_enabled=bool(raw.get("wallet_enabled", False)),
            wallet_enabled_generation=max(0, int(raw.get("wallet_enabled_generation", 0) or 0)),
            ram_reserve_pct=min(80, max(10, int(raw.get("ram_reserve_pct", 20)))),
            daily_token_budget=max(0, int(raw.get("daily_token_budget", 0))),
            timezone=_validate_timezone(str(raw.get("timezone", "") or "")),
            setup_skipped_steps=str(raw.get("setup_skipped_steps", "") or "").strip()[:200],
            setup_dismissed=bool(raw.get("setup_dismissed", False)),
            speech_notice_dismissed=bool(raw.get("speech_notice_dismissed", False)),
            autonomy_chosen_at=str(raw.get("autonomy_chosen_at", "") or "").strip()[:40],
        )
        return _apply_edition_clamps(prefs)
    except Exception:
        return default_preferences()


def save_preferences(prefs: UserPreferences) -> Path:
    prefs = _apply_edition_clamps(prefs)
    path = _prefs_path()
    payload = asdict(prefs)
    payload["humor_percent"] = _clamp_pct(int(payload.get("humor_percent", 20)))
    payload["profanity_level"] = _clamp_pct(int(payload.get("profanity_level", 40)))
    payload["boundaries_mode"] = _normalize_boundaries(str(payload.get("boundaries_mode", "user_defined")))
    payload["autonomy_mode"] = _normalize_autonomy(str(payload.get("autonomy_mode", "hands_off")))
    payload["communication_style"] = _normalize_communication_style(str(payload.get("communication_style", "casual")))
    payload["show_workflow"] = bool(payload.get("show_workflow", False))
    payload["hive_followups"] = bool(payload.get("hive_followups", True))
    payload["idle_research_assist"] = bool(payload.get("idle_research_assist", True))
    payload["accept_hive_tasks"] = bool(payload.get("accept_hive_tasks", True))
    payload["social_commons"] = bool(payload.get("social_commons", True))
    payload["wallet_enabled"] = bool(payload.get("wallet_enabled", False))
    payload["wallet_enabled_generation"] = max(0, int(payload.get("wallet_enabled_generation", 0) or 0))
    payload["ram_reserve_pct"] = min(80, max(10, int(payload.get("ram_reserve_pct", 20))))
    payload["daily_token_budget"] = max(0, int(payload.get("daily_token_budget", 0)))
    payload["timezone"] = _validate_timezone(str(payload.get("timezone", "") or ""))
    payload["setup_skipped_steps"] = str(payload.get("setup_skipped_steps", "") or "").strip()[:200]
    payload["speech_notice_dismissed"] = bool(payload.get("speech_notice_dismissed", False))
    payload["setup_dismissed"] = bool(payload.get("setup_dismissed", False))
    payload["autonomy_chosen_at"] = str(payload.get("autonomy_chosen_at", "") or "").strip()[:40]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def mark_autonomy_chosen(prefs: UserPreferences) -> UserPreferences:
    """Record that ``autonomy_mode`` was chosen by the user right now.

    Called by every door that writes the field (the settings write authority and the chat
    preference command). The setup projection treats an unstamped value as the default.
    """
    from datetime import datetime, timezone

    prefs.autonomy_chosen_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return prefs


def describe_preferences_for_context() -> str:
    prefs = load_preferences()
    fragments = [
        f"humor={prefs.humor_percent}/100",
        f"boundaries={prefs.boundaries_mode}",
        f"profanity={prefs.profanity_level}/100",
        f"autonomy={prefs.autonomy_mode}",
        f"show_workflow={'on' if prefs.show_workflow else 'off'}",
        f"hive_followups={'on' if prefs.hive_followups else 'off'}",
        f"idle_research_assist={'on' if prefs.idle_research_assist else 'off'}",
        f"accept_hive_tasks={'on' if prefs.accept_hive_tasks else 'off'}",
        f"social_commons={'on' if prefs.social_commons else 'off'}",
    ]
    if prefs.character_mode:
        fragments.append(f"character_mode={prefs.character_mode}")
    if prefs.style_notes:
        fragments.append(f"style_notes={prefs.style_notes}")
    tz = prefs.timezone or "UTC"
    fragments.append(f"timezone={tz}")
    return "; ".join(fragments)


_STYLE_KEYWORDS = (
    "concise", "concisely", "brief", "briefly", "short", "terse", "telegram",
    "formal", "casual", "blunt", "bluntly", "direct", "technical", "plain",
    "no fluff", "no-fluff", "no nonsense", "no-nonsense", "professional",
    "minimal", "punchy", "to the point", "dev style", "bullet points", "one line",
    "one-line", "friendly",
)
_STYLE_PHRASE_RE = re.compile(
    r"\b(?:in|with|using)\b[\w\s-]{0,30}\b(?:style|tone|voice|manner|register|format)\b", re.IGNORECASE
)
_STYLE_PERSIST_RE = re.compile(r"\b(?:always|from now on|going forward|by default|by preference)\b", re.IGNORECASE)
_STYLE_KEEP_RE = re.compile(r"\bkeep\s+(?:it|your\s+(?:answers|responses|replies|messages))\b", re.IGNORECASE)
_STYLE_IMPERATIVE_RE = re.compile(r"^(?:please\s+)?(?:be|keep|stay|answer|respond|reply|talk|speak)\b", re.IGNORECASE)
_STYLE_TONE_WORDS = ("concise", "brief", "direct", "formal", "casual", "blunt", "technical", "friendly", "professional")


# A style instruction with a real question/task attached ("answer in X style: alpha vs beta
# readiness in one sentence", "in telegram style, tell me a joke") must be ANSWERED in that style
# (the model applies the style from the prompt), not swallowed as a bare preference-save.
_ATTACHED_TASK_RE = re.compile(
    r":\s*\w"  # "answer in X style: <task>"
    r"|\b(tell me|what(?:'s| is| are)\b|explain\b|summari[sz]e\b|compare\b|describe\b|give me\b|"
    r"write\b|create\b|implement\b|code\b|build\b|make\b|fix\b|refactor\b|return\b|"
    r"function\b|class\b|script\b|snippet\b|how (?:do|does|much|many)\b|why\b|\bvs\.?\b|versus\b)",
    re.IGNORECASE,
)


def _extract_style_directive(text: str) -> str | None:
    """Detect a free-form 'answer in X style' / 'be concise' preference and return the
    style descriptor, else None. Requires a style keyword AND a preference signal
    (persistence, an 'in X style' phrase, 'keep it/your answers', or a <=4-word
    imperative) and rejects questions/attached tasks, so a one-off task ('write a concise
    summary of the notes', 'answer in telegram style: <question>') is answered, not swallowed
    as a style command."""
    raw = str(text or "").strip()
    if not raw or "?" in raw or _ATTACHED_TASK_RE.search(raw):
        return None
    lowered = " ".join(raw.lower().split())
    if not any(kw in lowered for kw in _STYLE_KEYWORDS):
        return None
    words = lowered.split()
    signal = (
        bool(_STYLE_PERSIST_RE.search(lowered))
        or bool(_STYLE_PHRASE_RE.search(lowered))
        or bool(_STYLE_KEEP_RE.search(lowered))
        or (len(words) <= 4 and bool(_STYLE_IMPERATIVE_RE.match(lowered)))
    )
    if not signal:
        return None
    note = re.sub(r"^\s*(?:please|can you|could you|i want you to|i'?d like you to)\s+", "", raw, flags=re.IGNORECASE)
    return note.strip()[:400]


def maybe_handle_preference_command(user_text: str) -> tuple[bool, str]:
    text = str(user_text or "").strip()
    if not text:
        return False, ""
    lowered = text.lower()

    if lowered in {"/prefs", "/preferences", "show preferences", "show my preferences"}:
        prefs = load_preferences()
        return True, (
            "Preferences active: "
            f"addresses_you_as={prefs.user_address or 'not set'}, "
            f"deep_reasoning={'on' if prefs.deep_reasoning else 'off'}, "
            f"humor={prefs.humor_percent}/100, "
            f"boundaries={prefs.boundaries_mode}, "
            f"profanity={prefs.profanity_level}/100, "
            f"character={prefs.character_mode or 'default'}, "
            f"autonomy={prefs.autonomy_mode}, "
            f"workflow={'on' if prefs.show_workflow else 'off'}, "
            f"hive_followups={'on' if prefs.hive_followups else 'off'}, "
            f"idle_research_assist={'on' if prefs.idle_research_assist else 'off'}, "
            f"accept_hive_tasks={'on' if prefs.accept_hive_tasks else 'off'}, "
            f"social_commons={'on' if prefs.social_commons else 'off'}."
        )

    humor = _HUMOR_RE.search(text)
    if humor:
        prefs = load_preferences()
        prefs.humor_percent = _clamp_pct(int(humor.group(1)))
        save_preferences(prefs)
        return True, f"Humor set to {prefs.humor_percent}%."

    if _ENABLE_REASONING_RE.match(text):
        prefs = load_preferences()
        prefs.deep_reasoning = True
        save_preferences(prefs)
        return True, "Deep reasoning on. I'll reason through harder problems before answering — replies will be a bit slower. Say `reasoning off` for fast mode."
    if _DISABLE_REASONING_RE.match(text):
        prefs = load_preferences()
        prefs.deep_reasoning = False
        save_preferences(prefs)
        return True, "Deep reasoning off — fast, direct answers. Say `think harder` when you want me to reason through something."

    # Explicit high-level communication style. Runs before the free-form style handler so an
    # explicit "style: cheeky" sets the preset; bare "be concise" still falls through to style_notes.
    comm_style = _COMM_STYLE_RE.match(text)
    if comm_style:
        prefs = load_preferences()
        prefs.communication_style = _normalize_communication_style(comm_style.group(1))
        save_preferences(prefs)
        return True, f"Communication style set to {prefs.communication_style}."

    # Free-form answer-style preferences ("be concise", "from now on keep it short") are Operator
    # Profile items (response_style / format_preference): explicit forms persist and report, stated
    # forms become a candidate. This surface no longer saves them silently into style_notes.
    if _extract_style_directive(text):
        return False, ""

    character = _CHAR_RE.match(text)
    if character:
        mode = character.group(1).strip()
        if mode:
            prefs = load_preferences()
            prefs.character_mode = mode[:120]
            save_preferences(prefs)
            return True, f"Character mode set: {prefs.character_mode}."

    boundaries = _BOUNDARIES_RE.match(text)
    if boundaries:
        prefs = load_preferences()
        prefs.boundaries_mode = _normalize_boundaries(boundaries.group(1))
        save_preferences(prefs)
        return True, f"Boundaries mode set to {prefs.boundaries_mode}."

    autonomy = _AUTONOMY_RE.match(text)
    if autonomy:
        prefs = load_preferences()
        prefs.autonomy_mode = _normalize_autonomy(autonomy.group(1))
        save_preferences(mark_autonomy_chosen(prefs))
        return True, _describe_autonomy_change(prefs.autonomy_mode)

    if _NO_MICRO_APPROVAL_RE.search(text) or _ONLY_SIGNIFICANT_APPROVAL_RE.search(text):
        prefs = load_preferences()
        prefs.autonomy_mode = "hands_off"
        save_preferences(mark_autonomy_chosen(prefs))
        return True, _describe_autonomy_change(prefs.autonomy_mode)

    profanity = _PROFANITY_RE.match(text)
    if profanity:
        prefs = load_preferences()
        prefs.profanity_level = _clamp_pct(int(profanity.group(1)))
        save_preferences(prefs)
        return True, f"Profanity level set to {prefs.profanity_level}%."

    if _SHOW_WORKFLOW_RE.match(text) or _looks_like_workflow_toggle(text, enable=True):
        prefs = load_preferences()
        prefs.show_workflow = True
        save_preferences(prefs)
        return True, "Workflow summaries enabled. I'll show the execution flow without exposing raw chain-of-thought."

    if _HIDE_WORKFLOW_RE.match(text) or _looks_like_workflow_toggle(text, enable=False):
        prefs = load_preferences()
        prefs.show_workflow = False
        save_preferences(prefs)
        return True, "Workflow summaries disabled."

    if _ENABLE_HIVE_FOLLOWUPS_RE.match(text):
        prefs = load_preferences()
        prefs.hive_followups = True
        save_preferences(prefs)
        return True, "Hive followups enabled. I’ll surface active research updates and new hive work in chat when it matters."

    if _DISABLE_HIVE_FOLLOWUPS_RE.match(text):
        prefs = load_preferences()
        prefs.hive_followups = False
        save_preferences(prefs)
        return True, "Hive followups disabled."

    if _ENABLE_IDLE_ASSIST_RE.match(text):
        prefs = load_preferences()
        prefs.idle_research_assist = True
        save_preferences(prefs)
        return True, "Idle research assist enabled. I’ll keep nudging about available hive research unless you tell me to stop."

    if _DISABLE_IDLE_ASSIST_RE.match(text):
        prefs = load_preferences()
        prefs.idle_research_assist = False
        save_preferences(prefs)
        return True, "Idle research assist disabled."

    if _ENABLE_HIVE_TASKS_RE.match(text):
        prefs = load_preferences()
        prefs.accept_hive_tasks = True
        save_preferences(prefs)
        return True, "Hive task intake enabled. I’ll stay visible in Hive stats and can accept swarm/public research tasks again."

    if _DISABLE_HIVE_TASKS_RE.match(text):
        prefs = load_preferences()
        prefs.accept_hive_tasks = False
        save_preferences(prefs)
        return True, "Hive task intake disabled. I’ll stay visible in Hive stats, but I won’t accept swarm/public research tasks until you re-enable it."

    if _ENABLE_SOCIAL_COMMONS_RE.match(text):
        prefs = load_preferences()
        prefs.social_commons = True
        save_preferences(prefs)
        return True, "Agent commons enabled. When I'm idle, I’ll keep the background curiosity and brainstorm lane alive."

    if _DISABLE_SOCIAL_COMMONS_RE.match(text):
        prefs = load_preferences()
        prefs.social_commons = False
        save_preferences(prefs)
        return True, "Agent commons disabled. I’ll stop the idle social and brainstorm lane."

    # How the USER wants to be addressed is Operator Profile business now (core.operator_profile,
    # reached from the turn front door BEFORE this command surface): explicit forms persist and
    # report, stated forms become a candidate the user confirms. Nothing here writes a name.

    candidate = extract_requested_agent_name(text)
    if candidate:
        try:
            from core.identity_manager import update_local_persona
            from core.onboarding import force_rename

            force_rename(candidate)
            update_local_persona("default", display_name=candidate)
            return True, f"Alright. I'll go by {candidate} now."
        except Exception:
            return True, "Rename requested, but I couldn't persist it right now."

    return False, ""


def _profile_lookup(category: str) -> str:
    """The Operator Profile is the ONE authority for how the user is addressed and signs. The
    lookup resolves for the turn in flight (its principal + chat, bound by the front door) or, off
    a turn, for the machine owner's global profile. Legacy JSON values migrate in once."""
    try:
        from core.operator_profile import OWNER_PRINCIPAL, resolve
        from core.operator_profile_turn import current_turn_scope

        principal, session_id, project_id = current_turn_scope()
        item = resolve(principal or OWNER_PRINCIPAL, category, session_id=session_id, project_id=project_id)
        return str(item.value_text) if item is not None else ""
    except Exception:
        return ""


def migrate_legacy_profile_fields() -> dict[str, str]:
    """Move ``user_address`` / ``email_signature`` out of user_preferences.json into the Operator
    Profile (origin ``settings``), then empty the JSON fields so there is ONE authority.

    Called once at daemon boot (apps.vool_api_server) and by the profile API on demand. It is
    NOT consulted on reads: the JSON fields are no longer an authority, so a value written into
    them behind the runtime's back is picked up at the next boot, never silently at read time.
    Returns the categories migrated."""
    migrated: dict[str, str] = {}
    path = _prefs_path()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    legacy_name = str(raw.get("user_address") or "").strip()[:60]
    legacy_sig = str(raw.get("email_signature") or "").strip()[:400]
    if not legacy_name and not legacy_sig:
        return migrated
    from core.operator_profile import OWNER_PRINCIPAL, remember

    for category, value in (("preferred_name", legacy_name), ("email_signature", legacy_sig)):
        if not value:
            continue
        change = remember(OWNER_PRINCIPAL, category, value, origin="settings", actor="settings_migration",
                          reason="legacy user_preferences.json field", replace=True)
        if change.kind in {"saved", "updated", "unchanged"}:
            migrated[category] = value
        raw[category if category == "email_signature" else "user_address"] = ""
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return migrated


def user_address() -> str:
    """How the user asked to be addressed, or '' if unset -- resolved from the Operator Profile
    for the turn in flight. Kept as a tiny helper so the greeting fast-path can weave it in."""
    return _profile_lookup("preferred_name")


def email_signature() -> str:
    """The operator's email signature from the Operator Profile ('' if none)."""
    return _profile_lookup("email_signature")


def extract_requested_agent_name(user_text: str) -> str | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    for pattern in _RENAME_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        candidate = _clean_requested_name(match.group(1))
        if _looks_like_real_name(candidate):
            return candidate
    return None


def hive_task_intake_enabled() -> bool:
    return bool(getattr(load_preferences(), "accept_hive_tasks", True))


def _looks_like_workflow_toggle(text: str, *, enable: bool) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    workflow_terms = (
        "workflow",
        "work flow",
        "thinking flow",
        "reasoning summary",
        "work log",
        "internal workflow",
        "internal workflows",
    )
    if not any(term in lowered for term in workflow_terms):
        return False
    disable_phrases = (
        "don't show",
        "do not show",
        "stop showing",
        "hide",
        "disable",
        "keep it to yourself",
        "keep that to yourself",
    )
    if enable:
        if any(phrase in lowered for phrase in disable_phrases):
            return False
        return any(
            phrase in lowered
            for phrase in (
                "show me",
                "show your",
                "enable",
                "turn on",
                "i need to see",
            )
        )
    return any(phrase in lowered for phrase in disable_phrases)


def _clean_requested_name(value: str) -> str:
    candidate = str(value or "").strip().strip("\"'")
    lower = candidate.lower()
    cut_markers = [
        " and my name is ",
        " and i'm ",
        " and i am ",
        ",",
        ".",
        "!",
        "?",
    ]
    cut_at = len(candidate)
    for marker in cut_markers:
        idx = lower.find(marker)
        if idx != -1:
            cut_at = min(cut_at, idx)
    cleaned = candidate[:cut_at].strip().strip("\"'")
    if cleaned.lower().endswith(" now"):
        cleaned = cleaned[:-4].strip()
    return cleaned


def _clamp_pct(value: int) -> int:
    return max(0, min(100, int(value)))


def _normalize_boundaries(value: str) -> str:
    val = value.strip().lower()
    if val in {"relaxed", "standard", "strict", "user_defined"}:
        return val
    return "user_defined"


def _normalize_autonomy(value: str) -> str:
    val = value.strip().lower().replace("_", "-")
    if val in {"hands-off", "handsoff"}:
        return "hands_off"
    if val in {"balanced", "strict"}:
        return val
    return "hands_off"


_COMMUNICATION_STYLES = ("business", "casual", "cheeky")


def _normalize_communication_style(value: str) -> str:
    val = str(value or "").strip().lower()
    return val if val in _COMMUNICATION_STYLES else "casual"


def communication_style_directive(style: str | None = None) -> str:
    """A one-line system-prompt directive for the active communication style. Reads the saved
    preference when `style` is None. Returns '' only if the style is unknown (never raises)."""
    resolved = str(
        style if style is not None else getattr(load_preferences(), "communication_style", "casual") or ""
    ).strip().lower()
    return {
        "business": "Keep your tone professional and concise: no slang, minimal emoji, straight to the point.",
        "casual": "Keep your tone warm and relaxed, like talking with a friend.",
        "cheeky": "Be playful and cheeky: free to use slang and emojis, and mirror the user's energy and register.",
    }.get(resolved, "")


def _describe_autonomy_change(mode: str) -> str:
    normalized = _normalize_autonomy(mode)
    if normalized == "strict":
        return "Autonomy set to strict. I'll ask before any side-effect action or borderline risk."
    if normalized == "balanced":
        return "Autonomy set to balanced. I'll execute low-risk steps directly and ask before destructive or outward-facing actions."
    return "Autonomy set to hands-off. I'll move through research and low-risk execution without micro approvals, and only stop for destructive changes, leak risk, or significant security exposure."


def _looks_like_real_name(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"wrong", "bad", "stupid", "crazy", "broken"}:
        return False
    tokens = re.findall(r"[a-z0-9'-]+", lowered)
    if not 1 <= len(tokens) <= 4:
        return False
    if any(
        token in {
            "acting",
            "weird",
            "create",
            "make",
            "write",
            "save",
            "file",
            "folder",
            "directory",
            "download",
            "fetch",
            "grab",
            "please",
            "repo",
            "workspace",
            "desktop",
            "downloads",
            "documents",
        }
        for token in tokens
    ):
        return False
    return bool(value.strip())


# ── Persisted IANA timezone ─────────────────────────────────────────────────


def _validate_timezone(tz_name: str) -> str:
    """Validate an IANA timezone name.

    Returns the validated name on success, or ``""`` (empty) on failure.  An
    empty string is valid and means "let the runtime use UTC fallback".
    Validation is strict: ``ZoneInfoNotFoundError`` is caught, and unknown
    or invented names produce an empty result.
    """
    cleaned = str(tz_name or "").strip()
    if not cleaned:
        return ""
    try:
        ZoneInfo(cleaned)
        return cleaned
    except (ZoneInfoNotFoundError, TypeError):
        return ""


def load_user_timezone() -> str:
    """Load the persisted user timezone, or ``""`` if unset.

    The caller should treat ``""`` as "use UTC fallback", never infer a
    permanent timezone from a single model answer.
    """
    return _validate_timezone(str(getattr(load_preferences(), "timezone", "") or ""))


def save_user_timezone(tz_name: str) -> bool:
    """Persist a validated IANA timezone for the user.

    Returns ``True`` if the timezone was accepted and saved, ``False`` if
    it was rejected (empty or invalid).
    """
    validated = _validate_timezone(tz_name)
    prefs = load_preferences()
    prefs.timezone = validated
    save_preferences(prefs)
    return bool(validated)
