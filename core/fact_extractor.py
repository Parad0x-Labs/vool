from __future__ import annotations

import contextlib
import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import requests

from core.vool_memory import VoolMemory
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)

FactAction = Literal["ADD", "UPDATE", "DELETE", "NOOP"]

FACT_EXTRACT_SYSTEM = """You are a local memory extraction agent. Given a conversation excerpt,
extract stable facts about the user, projects, preferences, and constraints. Return JSON only.

Output format (the angle-bracket strings are placeholders describing what goes there --
never output the placeholders and never copy them literally):
{
  "facts": [
    {"action": "ADD", "block": "user_profile", "content": "<a fact the user explicitly stated about themselves>"},
    {"action": "UPDATE", "block": "preferences", "old": "<the currently stored value>", "new": "<the corrected value>"},
    {"action": "DELETE", "block": "project_context", "content": "<the outdated fact to remove>"},
    {"action": "NOOP"}
  ]
}

Rules:
- Extract ONLY facts explicitly present in the Conversation. Never copy the placeholder examples above.
- Never invent or assume a name. Add a "Name: <name>" fact only if the user actually stated their own
  name in the conversation; otherwise do not add any name fact.
- Only extract facts that should survive restart.
- Do not extract one-off tasks, transient emotions, raw logs, file paths, or private internal paths.
- Do not extract secrets, keys, tokens, passwords, seed phrases, cookies, or credentials.
- Prefer NOOP when uncertain.
- Max 5 facts per call."""

FACT_EXTRACT_USER = """Conversation:
{conversation}

Extract stable persistent facts. JSON only."""

ALLOWED_BLOCKS = {
    "user_profile",
    "project_context",
    "preferences",
    "constraints",
    "recent_context",
}
_SINGLETON_LABELS_BY_BLOCK = {
    "user_profile": ("Name",),
    "preferences": ("Answer style", "Response style", "Preferred answer style"),
    "project_context": ("Active project codename", "Project codename"),
}

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\b(?:api[_-]?key|token|secret|password|passwd|pwd)\b\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(?:seed phrase|mnemonic|private key)\b", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"/Users/[^\\s]+"),
    re.compile(r"/home/\S+"),   # Linux user home — was unscrubbed while macOS /Users was
    re.compile(r"/root/\S+"),   # Linux root home
]
_NAME_PATTERNS = [
    re.compile(r"\bmy name is\s+([a-z0-9][a-z0-9 _'-]{1,48}?)(?=\.|,|\n|\s+and\b|\s+but\b|$)", re.IGNORECASE),
    re.compile(r"\bcall me\s+([a-z0-9][a-z0-9 _'-]{1,48}?)(?=\.|,|\n|\s+and\b|\s+but\b|$)", re.IGNORECASE),
    re.compile(r"\bi go by\s+([a-z0-9][a-z0-9 _'-]{1,48}?)(?=\.|,|\n|\s+and\b|\s+but\b|$)", re.IGNORECASE),
]
_ANSWER_STYLE_PATTERNS = [
    re.compile(r"\b(?:my )?(?:answer-style|answer style|response style)\s+preference\s+is\s+([^.\n]{3,120})", re.IGNORECASE),
    re.compile(r"\bi prefer\s+([^.\n]{3,120}?)\s+(?:answers|responses|replies)\b", re.IGNORECASE),
    re.compile(r"\b(?:keep|make)\s+(?:answers|responses|replies)\s+([^.\n]{3,120})", re.IGNORECASE),
]
_PROJECT_CODENAME_PATTERNS = [
    re.compile(r"\b(?:my )?(?:active )?project codename is\s+([A-Z0-9][A-Z0-9_-]{2,80})\b", re.IGNORECASE),
    re.compile(r"\b(?:my )?preferred project codename is\s+([A-Z0-9][A-Z0-9_-]{2,80})\b", re.IGNORECASE),
]

# Standing directions the user gives ("from now on ...", "always ...", "never ...", "avoid ...").
# High-precision on purpose: anchored to directive lead-ins + an action verb so an ordinary
# "I always struggle with regex" or a question never becomes a standing rule. These land in the
# preferences/constraints blocks, which are injected as DEFAULTS on every turn (see
# core.memory_prompt_builder), and a contrary current message overrides them.
_STANDING_ACTION = (
    "use|reply|respond|answer|write|keep|make|format|call|address|sign|end|start|"
    "include|add|give|speak|talk|structure|explain|show|list|summar|phrase|word|"
    "mention|apolog|disclose|repeat|capitali[sz]e"
)
# Prohibitions exclude the transient content-avoidance verb "mention": "do not mention <X>" is a
# per-conversation constraint (handled live per-turn by term avoidance in core.web0_project_grounding),
# NOT a durable global rule. Capturing it as a standing constraint let a fresh session recall
# "private keys" as a forbidden phrase carried over from an unrelated earlier test session.
_PROHIBITION_ACTION = (
    "use|reply|respond|answer|write|keep|make|format|call|address|sign|end|start|"
    "include|add|give|speak|talk|structure|explain|show|list|summar|phrase|word|"
    "apolog|disclose|repeat|capitali[sz]e"
)
# "avoid mentioning/referencing/discussing <X>" is the same transient content-avoidance class and
# must not become a durable constraint either.
_TRANSIENT_AVOIDANCE_LEAD = re.compile(
    r"^\s*(?:mention|mentioning|say|saying|reference|referencing|bring(?:ing)?\s+up|"
    r"talk(?:ing)?\s+about|discuss(?:ing)?)\b",
    re.IGNORECASE,
)
# A whole content-avoidance directive ("do not mention X", "avoid mentioning X", "never mention X").
# Used to reject such directives on the LLM-JSON extraction path too, since the tiny model otherwise
# turns a per-conversation "do not mention private keys" into a durable global constraint.
_CONTENT_AVOIDANCE_CONSTRAINT_RE = re.compile(
    r"\b(?:do not|don't|dont|never|avoid|not|please\s+(?:do\s+not|don't))\s+"
    r"(?:ever\s+)?(?:mention|mentioning|say|saying|reference|referencing|bring(?:ing)?\s+up|"
    r"talk(?:ing)?\s+about|discuss(?:ing)?)\b",
    re.IGNORECASE,
)


def _is_transient_content_avoidance(content: str) -> bool:
    return bool(_CONTENT_AVOIDANCE_CONSTRAINT_RE.search(str(content or "")))
# Capture the directive clause but STOP at a sentence/list boundary (period, comma, semicolon,
# newline, or a following " and ") so chained directions become separate clean rules instead of
# one greedy blob that runs across sentences.
_CLAUSE = r"(?:(?!\s+and\b)[^.,;\n]){0,120}"
_FROM_NOW_ON_RE = re.compile(
    r"\b(?:from now on|going forward|in (?:the )?future|from here on(?: out)?|henceforth)\b[,:]?\s+(" + _CLAUSE + r")",
    re.IGNORECASE,
)
_ALWAYS_RE = re.compile(rf"\b(?:please\s+)?always\s+((?:{_STANDING_ACTION})\b{_CLAUSE})", re.IGNORECASE)
_NEVER_RE = re.compile(rf"\b(?:never|don't|do not|dont)\s+(?:ever\s+)?((?:{_PROHIBITION_ACTION})\b{_CLAUSE})", re.IGNORECASE)
_AVOID_RE = re.compile(r"\bavoid\s+(" + _CLAUSE + r")", re.IGNORECASE)
_PROHIBITION_LEAD = re.compile(r"^\s*(?:never|don't|do not|dont|no\b|avoid|stop)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractedFact:
    action: FactAction
    block: str = ""
    content: str = ""
    old: str = ""
    new: str = ""


_NAME_FACT_RE = re.compile(r"^\s*name\s*[:=]\s*(.+)$", re.IGNORECASE)


def _fact_grounded(fact: ExtractedFact, user_text: str) -> bool:
    """Reject an LLM-invented user name, and any name that contradicts the authoritative user_address.

    The extraction prompt's format example once used the literal "Name: Loop", and the tiny model
    copied it into essentially every user's profile. Keep an ADD user_profile name fact only when the
    name value actually appears in the user's own messages; every other fact passes through unchanged.

    Additionally, `user_address` ("call me X") is the single authoritative identity fact. When it is
    set, only a matching Name is admitted -- an explicit "call me LOOP" wins over any harvested or
    model-invented "Rick", so a contradiction can never be persisted as memory. Changing the name is
    done by updating user_address, not by letting a stray mention create a competing profile Name.
    """
    if fact.action != "ADD" or fact.block != "user_profile":
        return True
    match = _NAME_FACT_RE.match(fact.content)
    if not match:
        return True
    name = match.group(1).strip().strip(".\"'` ")
    if not name:
        return False
    try:
        from core.user_preferences import user_address

        authoritative = user_address()
    except Exception:
        authoritative = ""
    # The Operator Profile is the ONE name authority. A harvested "Name:" line is admitted only
    # when it AGREES with the profile; with no profile name it is refused outright -- the name the
    # user states in chat becomes a profile candidate they confirm, never a memory block written
    # behind their back (that block leaked a chat-only name into every chat's recall).
    if authoritative:
        return name.lower() == authoritative.lower()
    return False


#: How many background extractions may be in flight at once. Each one owns a socket for its
#: `/api/chat` POST and a thread-local SQLite connection for the memory write, so this number IS
#: the descriptor ceiling this module can reach. It is deliberately small: extraction is background
#: enrichment, never on the answer path, and a saturated bound coalesces rather than queues.
_MAX_CONCURRENT_EXTRACTIONS = 2
_EXTRACTION_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_EXTRACTIONS)



def _direct_lane_num_ctx(model_tag: str) -> int:
    """Sized context for a lane that posts straight to Ollama, from the existing authority."""

    from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role

    return _ollama_context_window_for_bundle_role(
        "lightweight_utility", model_tag=str(model_tag or "")
    )


class FactExtractor:
    """Mem0-style post-turn extractor that updates local memory without blocking inference."""

    def __init__(
        self,
        memory: VoolMemory,
        ollama_url: str | None = None,
        model: str = "qwen3:0.6b",
        model_client: Callable[[str], str] | None = None,
        close_memory_on_finish: bool = False,
        session_id: str = "",
        project_id: str = "",
    ) -> None:
        self._memory = memory
        from core.ollama_endpoint import ollama_base_url

        # The runtime's ONE Ollama resolution: a literal default here reached the operator's real
        # Ollama from an isolated daemon whose environment pointed everywhere else (2026-09-07).
        self._ollama_url = str(ollama_url or ollama_base_url()).rstrip("/")
        self._model = str(model or "qwen3:0.6b").strip() or "qwen3:0.6b"
        self._model_client = model_client
        self._close_memory_on_finish = bool(close_memory_on_finish)
        self._session_id = str(session_id or "").strip()
        self._project_id = str(project_id or "").strip()

    def trigger_async(self, messages: list[dict]) -> threading.Thread | None:
        """Start one bounded background extraction, or coalesce into the ones already running.

        This used to spawn an unbounded thread per invocation. Each one holds a socket for the
        length of its `/api/chat` POST and a thread-local SQLite connection for the memory write,
        so the in-flight COUNT is what consumes descriptors -- not any failure to clean up. The
        30s request timeout bounds each thread's lifetime but says nothing about how many exist,
        and turns arrive far faster than 30s whenever the model endpoint is slow, unreachable, or
        absent (measured: 36 threads simultaneously blocked in `_read_status`, ~5 descriptors each,
        exhausting a 256-descriptor limit).

        Extraction is best-effort enrichment of a conversation that only grows, so when the bound
        is already saturated the honest behaviour is to coalesce: skip this round and let the next
        turn extract from the fuller transcript. That keeps the resource ceiling flat instead of
        trading it for a queue that grows without limit.
        """

        if not _EXTRACTION_SLOTS.acquire(blocking=False):
            return None
        try:
            thread = threading.Thread(
                target=self._run_bounded,
                args=(messages,),
                daemon=True,
                name="vool-fact-extractor",
            )
            thread.start()
        except BaseException:
            _EXTRACTION_SLOTS.release()
            raise
        return thread

    def _run_bounded(self, messages: list[dict]) -> None:
        """Own this thread's resources for exactly as long as this thread lives."""

        try:
            self._run_safely(messages)
        finally:
            # This thread opened its own thread-local SQLite connection for the memory write (see
            # `storage.db.get_connection`, which caches per thread). Nothing else can be holding
            # it, so releasing it here is safe and is the only boundary that exists -- the thread
            # is about to disappear and would otherwise strand the DB and its WAL descriptor.
            with contextlib.suppress(Exception):
                from storage.db import reset_default_connection

                reset_default_connection()
            _EXTRACTION_SLOTS.release()

    def run_sync(self, messages: list[dict]) -> list[ExtractedFact]:
        return self._run(messages)

    def _run_safely(self, messages: list[dict]) -> None:
        try:
            self._run(messages)
        except Exception:
            return
        finally:
            if self._close_memory_on_finish:
                with contextlib.suppress(Exception):
                    self._memory.close()

    def _run(self, messages: list[dict]) -> list[ExtractedFact]:
        conversation_text = self._format_conversation(messages)
        if not conversation_text:
            return []
        raw = self._call_model(conversation_text)
        # Deterministic self-disclosed facts must win the small write budget.
        # The tiny extractor is useful, but noisy output cannot be allowed to
        # crowd out explicit profile/preferences/project declarations. Its facts
        # are also grounded against the user's own text so a copied "Name: ..."
        # example (or any invented name) never reaches the profile.
        user_text = self._user_text(messages)
        # The tiny LLM extractor otherwise turns a per-conversation "do not mention private keys" into
        # a durable global constraint that contaminates unrelated future sessions. The rule-based path
        # already excludes bare content-avoidance; drop it from the LLM path too (explicit "from now
        # on ..." directives still persist via _rule_based_facts).
        llm_facts = [
            fact
            for fact in self._parse_facts(raw)
            if _fact_grounded(fact, user_text)
            and not (fact.block == "constraints" and _is_transient_content_avoidance(fact.content))
        ]
        facts = _merge_facts([*_rule_based_facts(conversation_text), *llm_facts])
        applied: list[ExtractedFact] = []
        for fact in facts[:5]:
            if self._apply(fact):
                applied.append(fact)
        return applied

    def _format_conversation(self, messages: list[dict]) -> str:
        parts: list[str] = []
        for msg in list(messages or [])[-20:]:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "unknown").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = _message_content_text(msg.get("content", ""))
            content = _redact_sensitive_text(content)[:500].strip()
            if content:
                parts.append(f"{role}: {content}")
        return "\n".join(parts)

    def _user_text(self, messages: list[dict]) -> str:
        """Concatenated user-authored text only, for grounding extracted facts against what the user said."""
        parts: list[str] = []
        for msg in list(messages or [])[-20:]:
            if isinstance(msg, dict) and str(msg.get("role") or "").strip().lower() == "user":
                parts.append(_message_content_text(msg.get("content", "")))
        return " ".join(part for part in parts if part).strip()

    def _call_model(self, conversation: str) -> str:
        if self._model_client is not None:
            return str(self._model_client(conversation) or "")
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": FACT_EXTRACT_SYSTEM},
                {"role": "user", "content": FACT_EXTRACT_USER.format(conversation=conversation)},
            ],
            "stream": False,
            "think": False,
            # Sized: this lane posts straight to Ollama, so the adapter's num_ctx never reaches
            # it and the model loads at its NATIVE context. Its default model is qwen3:0.6b,
            # measured at 4.99 GiB unsized against 2.30 GiB at the adaptive default -- a 0.6B
            # model costing as much as a 7B. See core.intent_arbiter._arbiter_num_ctx.
            "options": {
                "temperature": 0.1,
                "num_predict": 512,
                "num_ctx": _direct_lane_num_ctx(self._model),
            },
        }
        try:
            permit = seal_direct_provider_invocation(
                provider_id="ollama:fact-extractor",
                model_id=self._model,
                operation="fact_extraction",
                payload=payload,
                request_id=(
                    "fact-extract-"
                    + hashlib.sha256(
                        conversation.encode("utf-8")
                    ).hexdigest()
                ),
                context_manifest={
                    "chat_id": self._session_id,
                    "project_id": self._project_id,
                    "capsule_version": "none",
                    "items_included": [],
                    "items_excluded": [],
                },
                max_output_tokens=512,
                header_names=("Content-Type",),
            )
            response = requests.post(
                f"{self._ollama_url}/api/chat",
                json=permit.consume(),
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            return ""
        return str(dict(data.get("message") or {}).get("content") or "")

    def _parse_facts(self, raw: str) -> list[ExtractedFact]:
        payload = _extract_json_object(raw)
        if not payload:
            return []
        facts: list[ExtractedFact] = []
        for item in list(payload.get("facts") or []):
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "NOOP").strip().upper()
            if action not in {"ADD", "UPDATE", "DELETE", "NOOP"}:
                continue
            facts.append(
                ExtractedFact(
                    action=action,  # type: ignore[arg-type]
                    block=str(item.get("block") or "").strip().lower(),
                    content=str(item.get("content") or "").strip(),
                    old=str(item.get("old") or "").strip(),
                    new=str(item.get("new") or "").strip(),
                )
            )
        return facts

    def _apply(self, fact: ExtractedFact) -> bool:
        if fact.action == "NOOP":
            return False
        if fact.block not in ALLOWED_BLOCKS:
            return False
        if fact.action == "ADD":
            content = _safe_memory_text(fact.content)
            if not content:
                return False
            existing = self._memory.block_read(fact.block) or ""
            if _memory_line_exists(existing, content):
                return False
            singleton_line = _existing_singleton_line(existing, block=fact.block, content=content)
            if singleton_line:
                changed = self._memory.block_replace(fact.block, singleton_line, content)
                if changed:
                    self._store_fact_node(content=f"{singleton_line} -> {content}", block=fact.block, action="UPDATE")
                return changed
            self._memory.block_append(fact.block, content)
            self._store_fact_node(content=content, block=fact.block, action=fact.action)
            return True
        if fact.action == "UPDATE":
            old = _safe_memory_text(fact.old)
            new = _safe_memory_text(fact.new)
            if not old or not new:
                return False
            changed = self._memory.block_replace(fact.block, old, new)
            if changed:
                self._store_fact_node(content=f"{old} -> {new}", block=fact.block, action=fact.action)
            return changed
        if fact.action == "DELETE":
            content = _safe_memory_text(fact.content)
            if not content:
                return False
            existing = self._memory.block_read(fact.block) or ""
            if content not in existing:
                return False
            updated = "\n".join(line for line in existing.replace(content, "").splitlines() if line.strip())
            self._memory.block_write(fact.block, updated)
            self._store_fact_node(content=f"Removed: {content}", block=fact.block, action=fact.action)
            return True
        return False

    def _store_fact_node(self, *, content: str, block: str, action: str) -> None:
        if not self._session_id:
            # A node without an immutable chat namespace cannot safely
            # participate in semantic retrieval.
            return
        embedding = stable_text_embedding(content)
        related = [
            node.node_id
            for node, _ in self._memory.node_search(
                embedding,
                top_k=3,
                min_score=0.78,
                session_id=self._session_id,
            )
        ]
        session_scope = (
            "v2:"
            + hashlib.sha256(self._session_id.encode("utf-8")).hexdigest()
        )
        project_scope = (
            "v2:"
            + hashlib.sha256(self._project_id.encode("utf-8")).hexdigest()
            if self._project_id
            else ""
        )
        tags = [block, action.lower(), f"session:{session_scope}"]
        if project_scope:
            tags.append(f"project:{project_scope}")
        self._memory.node_store(
            content=content,
            keywords=_keywords_for_text(content),
            tags=tags,
            context_description=(
                f"{block}: {content}; session={session_scope}"
                + (
                    f"; project={project_scope}"
                    if project_scope
                    else ""
                )
            ),
            embedding=embedding,
            linked_node_ids=related,
            # A8 pass-002 lineage law: facts persisting conversation-derived
            # content stamp the governing A0 request truth when known, so
            # canonical erasure reaches them. Legacy shape ('') stays honest.
            lineage_request_id=self._current_lineage_request_id(),
        )

    @staticmethod
    def _current_lineage_request_id() -> str:
        try:
            from core.semantic.semantic_admissions import current_request_id

            return str(current_request_id() or "").strip()
        except Exception:
            return ""


def stable_text_embedding(text: str, *, dimensions: int = 64) -> list[float]:
    vector = [0.0] * max(8, int(dimensions))
    for token in _keywords_for_text(text, limit=80):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % len(vector)
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    magnitude = sum(value * value for value in vector) ** 0.5
    if not magnitude:
        return vector
    return [value / magnitude for value in vector]


def _extract_json_object(raw: str) -> dict[str, object]:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{.*"facts".*\}', text, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _message_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return " ".join(part for part in parts if part.strip())
    return str(content or "")


def _safe_memory_text(text: str) -> str:
    clean = _redact_sensitive_text(str(text or "")).strip()
    if not clean or "[REDACTED]" in clean:
        return ""
    if len(clean) > 600:
        clean = clean[:600].rsplit(" ", 1)[0].strip()
    return clean


def _rule_based_facts(conversation: str) -> list[ExtractedFact]:
    text = "\n".join(_declarative_user_lines(conversation))
    facts: list[ExtractedFact] = []
    # Names are Operator Profile business (core.operator_profile): the front door turns a stated
    # name into a candidate the user confirms. The heuristic extractor no longer writes one.
    for pattern in _ANSWER_STYLE_PATTERNS:
        match = pattern.search(text)
        if match:
            style = _clean_fact_value(match.group(1))
            if style:
                facts.append(ExtractedFact(action="ADD", block="preferences", content=f"Answer style: {style}"))
            break
    for pattern in _PROJECT_CODENAME_PATTERNS:
        match = pattern.search(text)
        if match:
            codename = _clean_fact_value(match.group(1)).upper()
            if codename:
                facts.append(
                    ExtractedFact(
                        action="ADD",
                        block="project_context",
                        content=f"Active project codename: {codename}",
                    )
                )
            break
    facts.extend(_standing_direction_facts(text))
    return facts


def _standing_direction_facts(declarative_text: str) -> list[ExtractedFact]:
    """Capture explicit standing directions into the preferences/constraints blocks so they become
    defaults applied to every future turn. Anchored to directive lead-ins + action verbs for
    precision; prohibitions go to constraints, positive style directions to preferences."""
    facts: list[ExtractedFact] = []
    seen: set[str] = set()

    def _add(block: str, content: str) -> None:
        value = _clean_fact_value(content)
        if len(value) < 3:
            return
        key = f"{block}:{value.lower()}"
        if key in seen:
            return
        seen.add(key)
        facts.append(ExtractedFact(action="ADD", block=block, content=value))

    def _capitalize(clause: str) -> str:
        clause = clause.strip()
        return clause[:1].upper() + clause[1:] if clause else clause

    for match in _FROM_NOW_ON_RE.finditer(declarative_text):
        clause = match.group(1).strip()
        block = "constraints" if _PROHIBITION_LEAD.match(clause) else "preferences"
        _add(block, _capitalize(clause))
    for match in _ALWAYS_RE.finditer(declarative_text):
        _add("preferences", "Always " + match.group(1).strip())
    for match in _NEVER_RE.finditer(declarative_text):
        _add("constraints", "Never " + match.group(1).strip())
    for match in _AVOID_RE.finditer(declarative_text):
        clause = match.group(1).strip()
        if _TRANSIENT_AVOIDANCE_LEAD.match(clause):
            continue
        _add("constraints", "Avoid " + clause)
    return facts


def _declarative_user_lines(conversation: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(conversation or "").splitlines():
        line = raw_line.strip()
        if not line.lower().startswith("user:"):
            continue
        text = line.split(":", 1)[1].strip()
        lowered = text.lower().lstrip()
        if "?" in text or lowered.startswith(("what ", "who ", "which ", "where ", "when ", "why ", "how ")):
            continue
        lines.append(text)
    return lines


def _merge_facts(facts: list[ExtractedFact]) -> list[ExtractedFact]:
    merged: list[ExtractedFact] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    seen_singleton_labels: set[tuple[str, str]] = set()
    for fact in facts:
        singleton_label = _singleton_fact_label(fact)
        if singleton_label:
            if singleton_label in seen_singleton_labels:
                continue
            seen_singleton_labels.add(singleton_label)
        key = (
            fact.action,
            fact.block,
            " ".join(fact.content.lower().split()),
            " ".join(fact.old.lower().split()),
            " ".join(fact.new.lower().split()),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(fact)
    return merged


def _singleton_fact_label(fact: ExtractedFact) -> tuple[str, str] | None:
    if fact.action != "ADD":
        return None
    label = _content_singleton_label(block=fact.block, content=fact.content)
    if not label:
        return None
    return (fact.block, label.lower())


def _content_singleton_label(*, block: str, content: str) -> str:
    text = str(content or "").strip()
    for label in _SINGLETON_LABELS_BY_BLOCK.get(str(block or "").strip().lower(), ()):
        prefix = f"{label}:"
        if text.lower().startswith(prefix.lower()):
            return label
    return ""


def _existing_singleton_line(existing: str, *, block: str, content: str) -> str:
    label = _content_singleton_label(block=block, content=content)
    if not label:
        return ""
    prefix = f"{label}:"
    for raw_line in str(existing or "").splitlines():
        line = raw_line.strip()
        if line.lower().startswith(prefix.lower()):
            return line
    return ""


def _memory_line_exists(existing: str, addition: str) -> bool:
    normalized_addition = " ".join(str(addition or "").split()).lower()
    return any(" ".join(line.split()).lower() == normalized_addition for line in str(existing or "").splitlines())


def _clean_fact_value(value: str, *, title_case: bool = False) -> str:
    text = _safe_memory_text(str(value or ""))
    text = text.strip(" .,:;\"'`")
    if title_case and text:
        return " ".join(part[:1].upper() + part[1:] for part in text.split())
    return text


def _redact_sensitive_text(text: str) -> str:
    clean = str(text or "")
    for pattern in _SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED]", clean)
    return clean


def _keywords_for_text(text: str, *, limit: int = 12) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(text or "").lower())
    stop = {"about", "that", "this", "with", "from", "have", "into", "user", "prefers"}
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in stop or token in seen:
            continue
        seen.add(token)
        unique.append(token)
        if len(unique) >= limit:
            break
    return unique


__all__ = ["ALLOWED_BLOCKS", "ExtractedFact", "FactExtractor", "stable_text_embedding"]
