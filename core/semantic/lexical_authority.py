"""An inventory of the words this runtime routes on, and where each one came from.

Roughly two thousand string literals across two dozen files currently decide, before any model is
consulted, whether a message is about the weather or about a folder. That is the quantity the
semantic architecture exists to reduce, so it has to be *measured* -- an argument about whether the
runtime is "too keyword-driven" that cannot produce a number is an argument that never ends.

Three properties make this measurement worth trusting:

**1. It is syntax-blind (the restyle property).** The same ten terms written as a tuple literal, as
a `frozenset`, or packed into `re.compile(r"\\b(a|b|...)\\b")` all extract to the same ten terms. A
measurement that could be moved by reformatting would be a measurement of formatting, and the first
person who wanted a better number would repack their table as a regex.

**2. It is provenance-aware.** Every term carries where it came from -- which file and symbol, or
which runtime registration, or which plugin manifest. Built-in vocabulary and vocabulary that
arrives at runtime are the same kind of authority when they gate the same decision, and counting
only the former would let the total fall while the real dependence stayed put.

**3. Nothing it fails to parse disappears.** A table this module cannot decompose is recorded as one
`opaque` term naming its symbol, never skipped. Silent omission would make the number improve
exactly when the code got harder to analyse, which is the wrong direction for a metric to move.

What it does NOT claim: that every table found is load-bearing on a routing decision. Reachability
is not authority, and proving which tables actually gate a route needs the executed corpus in
`core.semantic.generalization`, not a static scan. This module reports the vocabulary that EXISTS,
tagged by provenance; the scorer decides what it means.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Provenance kinds. Strings, not an Enum, so a new source of vocabulary can be recorded by whoever
#: finds it without editing a closed type in this module first.
KIND_LITERAL_COLLECTION = "literal_collection"
KIND_REGEX_ALTERNATION = "regex_alternation"
KIND_MAPPING_KEYS = "mapping_keys"
KIND_OPAQUE = "opaque"
KIND_RUNTIME_REGISTRATION = "runtime_registration"
KIND_PLUGIN_TRIGGER = "plugin_trigger"
#: Tables the runtime itself declares as filler, noise, stopwords or exclusions -- words it strips
#: rather than routes on. Inventoried like everything else, but excluded from COVERAGE (below).
KIND_FILLER = "filler"

#: How a filler table announces itself. Read off the runtime's own symbol names rather than a
#: hand-written stopword list, deliberately: a list written here would be a new piece of vocabulary
#: invented by the measurement, and a measurement that needs its own keyword table to decide what
#: counts as a keyword table has conceded the argument. `_REQUEST_FILLER_WORDS`, `_PATH_STOP_WORDS`
#: and `_RELEVANCE_STOPWORDS` already say what they are.
_FILLER_SYMBOL_MARKERS = (
    "STOPWORD", "STOP_WORD", "FILLER", "NOISE", "EXCLUSION", "EXCLUDE",
    "DROP_TOKEN", "GENERIC_TOKEN", "PROTECT", "FUNCTION_WORD", "NOT_A_",
)

#: Shorter than this, a term cannot discriminate a route -- "a", "is", "of" appear in every
#: sentence. Applies to coverage only; the inventory still counts them.
_MIN_COVERAGE_TERM_LENGTH = 3

#: REMOVED, deliberately. There used to be a `_FUNCTION_WORD_SYMBOLS` tuple whose terms were
#: subtracted from coverage. Subtracting terms shrinks coverage, which leaves more classes eligible
#: to count as generalized, which RAISES the score -- so it was a developer-controlled lever on the
#: number, and a hostile review showed real trigger vocabulary could be absorbed through it.
#:
#: There is now no exclusion list at all. Coverage is every discovered term. That makes the score
#: strictly pessimistic -- ordinary English sentences contain ordinary words, and this runtime's
#: tables contain plenty of ordinary words -- and pessimistic is the only safe direction for a metric
#: whose whole job is to resist being talked upward.

#: Files whose module-level string tables gate routing decisions. Derived from the actual dispatch
#: path, not from a name pattern: these are the modules a turn passes through before a model is
#: consulted. Kept as data so adding a routing module to the runtime and forgetting to measure it is
#: a visible omission in one list rather than an invisible one across the tree.
ROUTING_AUTHORITY_FILES = (
    "core/market_intent.py",
    "core/task_router.py",
    "core/execution/constants.py",
    "core/execution/planner.py",
    "core/input_normalizer.py",
    "core/plain_task_routing.py",
    "core/agent_runtime/workspace_intent_detection.py",
    "core/agent_runtime/intent_claims.py",
    "core/agent_runtime/fast_live_info_mode_classifier.py",
    "core/agent_runtime/fast_live_info_mode_weather_markers.py",
    "core/agent_runtime/fast_live_info_mode_lookup_markers.py",
    "core/agent_runtime/fast_live_info_mode_news_markers.py",
    "core/agent_runtime/fast_live_info_mode_clock_markers.py",
    "core/agent_runtime/fast_live_info_price.py",
    "core/agent_runtime/fast_paths_utility.py",
    "core/agent_runtime/fast_paths_machine.py",
    "core/agent_runtime/fast_paths_web.py",
    "core/agent_runtime/fast_paths_pdf.py",
    "core/agent_runtime/workspace_audit.py",
    "core/agent_runtime/runtime_checkpoint_lane_policy.py",
    "core/agent_runtime/proceed_intent_support.py",
    "core/agent_runtime/turn_dispatch.py",
    "core/agent_runtime/grounded_mode.py",
    # Carries `_FUNCTION_WORDS` (113 entries) -- the runtime's own declaration of which words mean
    # nothing on their own. Listed so coverage can subtract it rather than invent an equivalent.
    "core/agent_runtime/turn_planner.py",
)

#: Regex syntax that carries no vocabulary. Stripped before a pattern fragment becomes a term, so
#: `\bprice\b` and `price` are one term rather than two.
_REGEX_NOISE = re.compile(r"\\b|\\s\*|\\s\+|\\s|\(\?:|\(\?i\)|\(\?P<[^>]*>|\^|\$|\\")

#: A fragment is vocabulary only if it reads as words. `[a-z]{2,}` is structure, not a term.
_LITERAL_TERM = re.compile(r"^[\w][\w '\-./]*$")

_GROUP = re.compile(r"\(([^()]*)\)")

#: A group's leading directive: `?:`, `?i:`, `?=`, `?!`, `?<=`, `?P<name>`. Stripped from the group
#: BODY, because `_GROUP` has already consumed the opening paren by the time this runs -- so
#: `_REGEX_NOISE`, which matches `(?:` including its paren, can no longer see it.
#:
#: Without this the first alternative of every non-capturing group is lost: `(?:price|cost|worth)`
#: extracted `cost` and `worth` but not `price`, because the first fragment was `?:price`. Found by
#: the restyle proof, which is exactly the vocabulary-hiding-by-reformatting it exists to catch --
#: `(price|...)` and `(?:price|...)` are the same table and must measure the same.
_GROUP_DIRECTIVE = re.compile(r"^\?(?:P<[^>]*>|<?[=!:iLmsuxa]*:?)")


def normalize_term(raw: Any) -> str:
    """One spelling for one term: lowercased, whitespace-collapsed, regex noise removed.

    This is what makes the restyle property hold. `"  Price "`, `r"\\bprice\\b"` and `"price"` are
    the same piece of vocabulary and must count once, or the total measures typing rather than
    dependence.
    """
    text = _REGEX_NOISE.sub("", str(raw or ""))
    return " ".join(text.replace("\\", "").split()).strip().lower()


def terms_from_pattern(pattern: str) -> tuple[str, ...]:
    """Vocabulary inside a regex source string.

    Alternations are the payload: `(price|cost|worth)` is three terms packed into one literal, and
    counting it as one is precisely how a repack would hide vocabulary. Groups that are structural
    (`[a-z]+`, `\\d{2}`) contribute nothing and are skipped -- they are not words.

    A pattern with no decomposable alternation yields its own normalized literal when that reads as
    a word, and nothing otherwise. The caller records `opaque` in the nothing case, so an
    undecomposable pattern is still counted once rather than dropped.
    """
    found: list[str] = []
    for raw_group in _GROUP.findall(str(pattern or "")):
        group = _GROUP_DIRECTIVE.sub("", raw_group)
        for alternative in group.split("|"):
            term = normalize_term(alternative)
            if term and _LITERAL_TERM.match(term):
                found.append(term)
    if not found:
        whole = normalize_term(pattern)
        if whole and _LITERAL_TERM.match(whole):
            found.append(whole)
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True)
class AuthorityEntry:
    """One vocabulary table, with where it came from and what is in it."""

    provenance: str
    symbol: str
    kind: str
    terms: tuple[str, ...]

    @property
    def term_count(self) -> int:
        return len(self.terms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance,
            "symbol": self.symbol,
            "kind": self.kind,
            "term_count": self.term_count,
            "terms": list(self.terms),
        }


def _reclassify_filler(entry: AuthorityEntry) -> AuthorityEntry:
    """Retag a table the runtime names as filler -- a REPORTING label only.

    This does not affect coverage or the generalization score; `AuthorityInventory.function_words`
    explains why that separation exists. It is here because "how much of this vocabulary is strip
    lists rather than triggers?" is a genuinely useful thing to read off the inventory, and because
    a table whose name says `_STOPWORDS` while its terms are weather markers is worth seeing.
    """
    upper = entry.symbol.upper()
    if any(marker in upper for marker in _FILLER_SYMBOL_MARKERS):
        return AuthorityEntry(entry.provenance, entry.symbol, KIND_FILLER, entry.terms)
    return entry


def _string_constants(node: ast.AST) -> list[str]:
    return [
        element.value
        for element in ast.walk(node)
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]


def _assembles_a_string(node: ast.AST) -> bool:
    """True when this expression builds a STRING out of pieces, so no piece is a term.

    `r"\\b(?:what\\s+is\\s+a|" + _SKILL_WORD + r"|explain)\\b"` is one pattern. Its literal fragments
    are not vocabulary -- reading them gives `('es',)`, which matches nothing the real regex matches.
    Tuple concatenation (`("a",) + ("b",)`) is the opposite case and stays readable, so the test is
    on the operands: a string on either side means the result is an assembled string.
    """
    if isinstance(node, ast.JoinedStr):  # an f-string
        return True
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Add):
        return False
    for side in (node.left, node.right):
        if isinstance(side, ast.Constant) and isinstance(side.value, str):
            return True
        if _assembles_a_string(side):
            return True
    return False


def _is_statically_decomposable(node: ast.AST) -> bool:
    """Whether every string in `node` is a literal we can read, with nothing computed in the way.

    This is the end of AST whack-a-mole. Rather than teaching the scanner one more shape each time a
    hostile review invents one -- `tuple("a b".split())`, `dict.fromkeys([...])`,
    `re.compile("|".join([...]))` -- anything COMPUTED is declared undecomposable and recorded as
    opaque. A scanner that guesses at computed vocabulary will always be one trick behind; a scanner
    that admits it cannot see is correct forever.

    `tuple("stormterm rainterm".split())` is the case that made this necessary: the old scan walked
    into it, found the string `"stormterm rainterm"`, and recorded ONE term that matches nothing --
    so the two real terms were both invisible AND the class they cover was credited as generalized.
    """
    for child in ast.walk(node):
        if isinstance(child, (ast.Call, ast.comprehension, ast.GeneratorExp, ast.ListComp,
                              ast.SetComp, ast.DictComp, ast.IfExp, ast.Starred, ast.Subscript)):
            return False
        if isinstance(child, ast.Attribute):
            return False
        # A bare NAME is a reference to a binding this scan has not resolved. Inside a regex pattern
        # it is the difference between the pattern and a fragment of it -- `re.compile(r"(?:a|" +
        # _SKILL_WORD + r")")` read as decomposable produced the single term `('es',)` for a real
        # routing table while the inventory still called itself complete. Unresolved is unreadable.
        if isinstance(child, ast.Name):
            return False
        # A string built out of pieces: no piece is a term, so the assembled value is unreadable.
        if _assembles_a_string(child):
            return False
    return True


def _entry_for_value(provenance: str, symbol: str, value: ast.AST) -> AuthorityEntry | None:
    """Classify one module-level assignment into an inventory entry, or None when it holds no words."""
    # A module-level bare string can be routing authority too: ``MARKER = "weather"`` is no less
    # lexical because it holds one term instead of a tuple. The old scanner had no Constant arm,
    # so the binding vanished while the inventory still called itself complete.
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        term = normalize_term(value.value)
        return (
            AuthorityEntry(provenance, symbol, KIND_LITERAL_COLLECTION, (term,))
            if term
            else None
        )

    # re.compile("..."), re.compile("...", re.I) -- the packed form.
    if isinstance(value, ast.Call):
        func = value.func
        is_compile = (isinstance(func, ast.Attribute) and func.attr == "compile") or (
            isinstance(func, ast.Name) and func.id == "compile"
        )
        if is_compile and value.args:
            if not _is_statically_decomposable(value.args[0]):
                # A pattern assembled at runtime (`"|".join([...])`, an f-string over names). Its
                # vocabulary is real and unreadable, so it is recorded as opaque rather than guessed.
                return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))
            pattern = " ".join(_string_constants(value.args[0]))
            terms = terms_from_pattern(pattern)
            if terms:
                return AuthorityEntry(provenance, symbol, KIND_REGEX_ALTERNATION, terms)
            # A pattern with no extractable vocabulary still IS routing authority; recording it as
            # one opaque term keeps it in the total instead of rewarding an unparseable rewrite.
            if pattern.strip():
                return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<pattern:{symbol}>",))
            return None
        # frozenset({...}), set([...]), tuple(...) -- a collection behind a constructor.
        if isinstance(func, ast.Name) and func.id in {"frozenset", "set", "tuple", "list"}:
            if not all(_is_statically_decomposable(arg) for arg in value.args):
                return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))
            terms = tuple(dict.fromkeys(t for t in (normalize_term(s) for s in _string_constants(value)) if t))
            return AuthorityEntry(provenance, symbol, KIND_LITERAL_COLLECTION, terms) if terms else None
        # Any other call -- `dict.fromkeys([...])`, a helper, a factory. If it carries strings at
        # all, it is vocabulary we cannot read. Recorded, never dropped and never guessed.
        if _string_constants(value):
            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))
        return None

    if isinstance(value, ast.Dict):
        if not _is_statically_decomposable(value):
            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))
        # KEYS AND VALUES. A mapping's values are vocabulary as often as its keys are --
        # `{"forecast": "weather live lookup"}` routes on either side -- and reading only the keys
        # meant a table could be hidden by moving it across the colon: the words vanished from
        # coverage while the entry stayed non-opaque, so the inventory did not even call itself
        # incomplete for it. Both sides are literals here, so both are recovered rather than
        # guessed; a value that is not vocabulary costs a term in the count, which is the safe
        # direction -- it can only make coverage broader and the score lower, never better.
        literals = [
            node.value
            for node in list(value.keys) + list(value.values)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        terms = tuple(dict.fromkeys(t for t in (normalize_term(k) for k in literals) if t))
        return AuthorityEntry(provenance, symbol, KIND_MAPPING_KEYS, terms) if terms else None

    if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
        if not _is_statically_decomposable(value):
            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))
        terms = tuple(dict.fromkeys(t for t in (normalize_term(s) for s in _string_constants(value)) if t))
        return AuthorityEntry(provenance, symbol, KIND_LITERAL_COLLECTION, terms) if terms else None

    # `_MARKERS = ("a", "b") + ("c", "d")` and `_MARKERS = _BASE + ("extra",)`. A hostile review
    # showed splitting a table across a concatenation hid the whole thing: a BinOp is not a
    # Tuple/List/Set, so the branch above never saw it and the entry vanished from the inventory.
    # `_string_constants` already walks, so both operands are covered -- and the `_BASE + (...)`
    # shape still reports the literal half rather than nothing.
    if isinstance(value, ast.BinOp):
        # STRING concatenation first: `"storm" + "term"` is one term, `stormterm`, and neither
        # fragment matches anything. Reporting the fragments was measured on the real tree and it
        # produced three phantom terms while the real trigger stayed invisible.
        if _assembles_a_string(value):
            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))
        terms = tuple(dict.fromkeys(t for t in (normalize_term(s) for s in _string_constants(value)) if t))
        if terms:
            return AuthorityEntry(provenance, symbol, KIND_LITERAL_COLLECTION, terms)
        # A concatenation of two NAMES contributes no literal of its own, but it is still a table
        # being assembled -- recorded opaquely so the assembly is visible rather than absent.
        if any(isinstance(node, ast.Name) for node in ast.walk(value)):
            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<assembled:{symbol}>",))
        return None

    # A comprehension building vocabulary: `TERMS = [w for w in ("a", "b")]`. There was no branch
    # for this, so the value fell off the end and returned None -- the table vanished from the
    # inventory entirely AND left `is_complete` True, which is worse than being unreadable: it is
    # being unreadable while reporting otherwise. Recorded opaque, never dropped.
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        if _string_constants(value) or any(isinstance(n, ast.Name) for n in ast.walk(value)):
            return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))
        return None

    # An f-string assembling vocabulary is a string built out of pieces, same as a concatenation.
    if isinstance(value, ast.JoinedStr) and _string_constants(value):
        return AuthorityEntry(provenance, symbol, KIND_OPAQUE, (f"<computed:{symbol}>",))

    return None


def scan_source_file(path: Path, *, provenance: str = "") -> tuple[AuthorityEntry, ...]:
    """Every module-level string table in one file. Returns () for a file that cannot be parsed.

    Module level only, deliberately. A literal inside a function body is usually a local detail; a
    module-level table is a vocabulary someone maintains. The two inline literal sets inside
    `has_explicit_tool_intent_request` are the known exception, and they are reported as a
    function-scope entry rather than missed -- see `_function_scope_entries`.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return ()
    source = provenance or f"source:{path.name}"
    entries: list[AuthorityEntry] = []
    for node in tree.body:
        targets: list[str] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        if not targets or value is None:
            continue
        for symbol in targets:
            entry = _entry_for_value(source, symbol, value)
            if entry is not None:
                entries.append(_reclassify_filler(entry))
    entries.extend(_function_scope_entries(tree, source))
    return tuple(entries)


def _function_scope_entries(tree: ast.AST, provenance: str) -> list[AuthorityEntry]:
    """Literal string collections compared against inside a function body.

    `has_explicit_tool_intent_request` holds two of these -- a 16-phrase tuple and a 23-phrase set,
    both written inline at the comparison. They are routing vocabulary by every meaningful test, and
    a module-level-only scan would report the runtime as less keyword-driven than it is simply
    because someone inlined a table.
    """
    entries: list[AuthorityEntry] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        collected: list[str] = []
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Tuple, ast.List, ast.Set)):
                strings = [
                    element.value
                    for element in inner.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                ]
                # Two or more sibling string literals is a table; one is an argument.
                if len(strings) >= 2:
                    collected.extend(strings)
        terms = tuple(dict.fromkeys(t for t in (normalize_term(s) for s in collected) if t))
        if terms:
            entries.append(
                AuthorityEntry(provenance, f"{node.name}()", KIND_LITERAL_COLLECTION, terms)
            )
    return entries


@dataclass(frozen=True)
class AuthorityDiscovery:
    entries: tuple[AuthorityEntry, ...]
    unavailable_sources: tuple[str, ...] = ()


def _runtime_registration_discovery() -> AuthorityDiscovery:
    """Vocabulary arriving from registered tool contracts, built-in and plugin alike.

    A contract's `description` is read verbatim into the planner catalog, so its words steer a
    model's choice of operation exactly as a keyword table steers a regex. Measuring only the static
    tables would let the number fall every time vocabulary moved from a frozenset into a manifest,
    which is a reorganisation, not a reduction.
    """
    entries: list[AuthorityEntry] = []
    try:
        from core.tool_registry import registered_tools

        for contract in registered_tools():
            intent = str(getattr(contract, "intent", "") or "")
            description = str(getattr(contract, "description", "") or "")
            source = str(getattr(contract, "source", "builtin") or "builtin")
            terms = tuple(dict.fromkeys(t for t in (normalize_term(w) for w in description.split()) if t))
            if terms:
                entries.append(
                    AuthorityEntry(
                        f"runtime:{source}", intent or "<unnamed>", KIND_RUNTIME_REGISTRATION, terms
                    )
                )
    except Exception as exc:
        return AuthorityDiscovery(
            entries=tuple(entries),
            unavailable_sources=(f"runtime:registered_tools:{type(exc).__name__}",),
        )
    return AuthorityDiscovery(entries=tuple(entries))


def runtime_registration_entries() -> tuple[AuthorityEntry, ...]:
    """Backward-compatible entry view; inventory building also retains discovery failures."""
    return _runtime_registration_discovery().entries


def _entries_from_skills(skills: Any) -> AuthorityDiscovery:
    entries: list[AuthorityEntry] = []
    failures: list[str] = []
    for index, skill in enumerate(skills or ()):
        try:
            name = str(getattr(skill, "name", "") or "<unnamed>")
            description = str(getattr(skill, "description", "") or "")
            triggers = tuple(getattr(skill, "triggers", ()) or ())
            raw_terms = [name, *description.split(), *triggers]
            terms = tuple(dict.fromkeys(t for t in (normalize_term(x) for x in raw_terms) if t))
            if terms:
                entries.append(AuthorityEntry("plugin:skill", name, KIND_PLUGIN_TRIGGER, terms))
        except Exception as exc:
            failures.append(f"plugin:skill:{index}:{type(exc).__name__}")
    return AuthorityDiscovery(tuple(entries), tuple(failures))


def _plugin_trigger_discovery(skills: Any = None) -> AuthorityDiscovery:
    """Declared trigger phrases from installed skills.

    `Skill.triggers` is the one place in the tree where vocabulary is *declared* as vocabulary. It
    ranks rather than gates today, which is why it is inventoried under its own kind instead of
    being folded into the routing total.
    """
    if skills is not None:
        return _entries_from_skills(skills)
    failures: list[str] = []
    loaded: list[Any] = []
    try:
        from core.plugin_catalog import plugins_root
        from core.plugin_skills import load_skills

        root = plugins_root()
    except Exception as exc:
        return AuthorityDiscovery((), (f"plugin:root:{type(exc).__name__}",))
    if root is None:
        return AuthorityDiscovery((), ("plugin:root:unavailable",))
    try:
        plugin_dirs = sorted(path for path in (Path(root) / "plugins").iterdir() if path.is_dir())
    except Exception as exc:
        return AuthorityDiscovery((), (f"plugin:enumeration:{type(exc).__name__}",))
    for plugin_dir in plugin_dirs:
        plugin_id = plugin_dir.name
        manifest = plugin_dir / ".codex-plugin" / "plugin.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not str(payload.get("name") or plugin_id).strip():
                raise ValueError("manifest has no plugin identity")
        except Exception as exc:
            failures.append(f"plugin:{plugin_id}:manifest:{type(exc).__name__}")
        candidates = tuple((plugin_dir / "skills").glob("*/SKILL.md"))
        try:
            found = tuple(load_skills(plugin_dir, plugin_id=plugin_id))
        except Exception as exc:
            failures.append(f"plugin:{plugin_id}:load:{type(exc).__name__}")
            continue
        if len(found) != len(candidates):
            failures.append(f"plugin:{plugin_id}:skills:{len(found)}-of-{len(candidates)}")
        loaded.extend(found)
    measured = _entries_from_skills(loaded)
    return AuthorityDiscovery(
        measured.entries,
        tuple(dict.fromkeys([*failures, *measured.unavailable_sources])),
    )


def plugin_trigger_entries(skills: Any = None) -> tuple[AuthorityEntry, ...]:
    """Declared plugin trigger/name/description vocabulary, using the shipped loader contract."""
    return _plugin_trigger_discovery(skills).entries


@dataclass(frozen=True)
class AuthorityInventory:
    """The whole reading: every entry, with the derived totals a report actually quotes."""

    entries: tuple[AuthorityEntry, ...]
    #: Files the scanner could not parse. Reported as UNMEASURED rather than skipped: a metric that
    #: silently improves when code becomes unanalysable is moving in the wrong direction.
    unreadable_files: tuple[str, ...] = ()
    #: Runtime/plugin sources whose absence or failure made their vocabulary unknowable.
    unavailable_sources: tuple[str, ...] = ()

    @property
    def terms(self) -> frozenset[str]:
        """Every distinct term, regardless of where it came from."""
        return frozenset(term for entry in self.entries for term in entry.terms)

    @property
    def opaque_entries(self) -> tuple[AuthorityEntry, ...]:
        """Authority that exists and cannot be read. Never zero-rated, never guessed at."""
        return tuple(entry for entry in self.entries if entry.kind == KIND_OPAQUE)

    @property
    def is_complete(self) -> bool:
        """Whether EVERY authority-bearing source in scope was actually readable.

        False the moment anything is opaque or unparseable. This is what makes opacity unable to
        help: `core.semantic.generalization` withholds its scalar entirely when the inventory it was
        scored against is incomplete, so hiding a table behind a computation does not produce a
        better number -- it produces no number.
        """
        return not self.opaque_entries and not self.unreadable_files and not self.unavailable_sources

    @property
    def total_term_slots(self) -> int:
        """Terms counted WITH duplication across tables -- the size of what is maintained."""
        return sum(entry.term_count for entry in self.entries)

    def terms_by_kind(self, kind: str) -> frozenset[str]:
        return frozenset(term for entry in self.entries if entry.kind == kind for term in entry.terms)

    @property
    def coverage_terms(self) -> frozenset[str]:
        """The terms that can actually claim a turn -- what `covers` matches against.

        Three exclusions, each for a stated reason rather than to make the number look better:

        * **Filler tables** (`KIND_FILLER`) are words the runtime STRIPS. "the" appearing in a
          sentence is not evidence that a keyword decided anything.
        * **Runtime registrations** (`KIND_RUNTIME_REGISTRATION`) are tool DESCRIPTIONS. Their words
          steer a model's choice; they do not fire a branch. They are counted in the inventory under
          their own kind -- which is what stops vocabulary from escaping measurement by moving into
          a manifest -- but a description containing "file" is not a gate on the word "file".
        * **Terms under three characters**, which cannot discriminate.

        Every exclusion shrinks the set of things that count as "lexically covered", and coverage
        only ever *reduces* the generalization score. So each of these can only make the reported
        dependence look WORSE, never better -- the safe direction for a metric to be wrong in.
        """
        """EVERY discovered term of usable length, from EVERY provenance.

        No exclusions, and that is the repair. Three separate exclusions used to live here -- a
        function-word list, the `KIND_FILLER` label, and runtime registrations -- and every one of
        them shrank coverage, which raises the score. A hostile review demonstrated all three as
        levers: absorb triggers into the function-word symbol, rename a trigger table to a stopword
        name, or move vocabulary into a manifest, and the number improves with no behaviour change.

        Runtime registrations are included now. They were counted in the inventory total but omitted
        from coverage, which is exactly the "counted but not covered" asymmetry that made moving
        words into a plugin a free improvement.
        """
        return frozenset(
            term
            for entry in self.entries
            for term in entry.terms
            if len(term) >= _MIN_COVERAGE_TERM_LENGTH and not term.startswith("<")
        )

    def covers(self, text: str) -> tuple[str, ...]:
        """Which coverage terms appear in `text`, word-bounded where the term is a single word.

        Word-bounded so "cost" does not match "costume". A multi-word term is matched as a
        substring, because a phrase table is written to be found inside a sentence.
        """
        haystack = " ".join(str(text or "").lower().split())
        if not haystack:
            return ()
        hits: list[str] = []
        for term in self.coverage_terms:
            if " " in term or not term.isalnum():
                if term in haystack:
                    hits.append(term)
            elif re.search(rf"\b{re.escape(term)}\b", haystack):
                hits.append(term)
        return tuple(sorted(hits))

    def with_extra_terms(self, symbol: str, terms: tuple[str, ...]) -> AuthorityInventory:
        """A copy with one more table. Used by the tamper test to prove a keyword cannot buy score."""
        added = AuthorityEntry(
            "source:tamper_probe", symbol, KIND_LITERAL_COLLECTION,
            tuple(dict.fromkeys(t for t in (normalize_term(x) for x in terms) if t)),
        )
        return AuthorityInventory(
            entries=(*self.entries, added),
            unreadable_files=self.unreadable_files,
            unavailable_sources=self.unavailable_sources,
        )

    def to_dict(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for entry in self.entries:
            by_kind[entry.kind] = by_kind.get(entry.kind, 0) + entry.term_count
        return {
            "table_count": len(self.entries),
            "distinct_terms": len(self.terms),
            "coverage_terms": len(self.coverage_terms),
            "total_term_slots": self.total_term_slots,
            "terms_by_kind": dict(sorted(by_kind.items())),
            # What the scan could NOT read, stated rather than dropped.
            "complete": self.is_complete,
            "unmeasured": {
                "unreadable_files": list(self.unreadable_files),
                "unavailable_sources": list(self.unavailable_sources),
                "opaque_tables": len(self.opaque_entries),
                "opaque_symbols": sorted({e.symbol for e in self.opaque_entries})[:20],
                "note": (
                    "static discovery is bounded to WIDE_DISCOVERY_ROOTS; vocabulary reached only "
                    "through dynamic construction is not mechanically discoverable and is UNKNOWN"
                ),
            },
        }


#: Directories swept in full when `discovery="wide"`. Bounded on purpose -- a whole-repo scan pulls
#: in tests and vendored code, which are not routing authority -- but WIDER than the curated list,
#: because a hostile review moved a table into an unlisted helper module and watched it disappear
#: from the measurement entirely.
WIDE_DISCOVERY_ROOTS = ("core", "tools", "adapters")


def build_inventory(
    repo_root: Path | str,
    *,
    files: tuple[str, ...] = ROUTING_AUTHORITY_FILES,
    include_runtime: bool = True,
    include_plugins: bool = True,
    discovery: str = "wide",
) -> AuthorityInventory:
    """The full inventory: static tables, plus runtime and plugin vocabulary.

    `discovery="wide"` (the default) sweeps `WIDE_DISCOVERY_ROOTS` entirely, so vocabulary moved into
    a module nobody listed is still counted. `discovery="declared"` restricts to `files` and is for
    reporting the curated routing subset specifically -- never for producing a smaller total.

    `include_runtime`/`include_plugins` exist so a test can measure one provenance at a time, never
    so production can quietly exclude a source.
    """
    root = Path(repo_root)
    entries: list[AuthorityEntry] = []
    unreadable: list[str] = []
    unavailable: list[str] = []

    if discovery == "wide":
        paths: list[tuple[Path, str]] = []
        for top in WIDE_DISCOVERY_ROOTS:
            base = root / top
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                paths.append((path, path.relative_to(root).as_posix()))
    else:
        paths = [(root / rel, rel) for rel in files if (root / rel).exists()]

    for path, relative in paths:
        found = scan_source_file(path, provenance=f"source:{relative}")
        if not found and not _parses(path):
            unreadable.append(relative)
        entries.extend(found)

    if include_runtime:
        runtime = _runtime_registration_discovery()
        entries.extend(runtime.entries)
        unavailable.extend(runtime.unavailable_sources)
    if include_plugins:
        plugins = _plugin_trigger_discovery()
        entries.extend(plugins.entries)
        unavailable.extend(plugins.unavailable_sources)
    return AuthorityInventory(
        entries=tuple(entries),
        unreadable_files=tuple(unreadable),
        unavailable_sources=tuple(dict.fromkeys(unavailable)),
    )


def _parses(path: Path) -> bool:
    try:
        ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        return True
    except (OSError, SyntaxError, ValueError):
        return False


__all__ = [
    "KIND_LITERAL_COLLECTION",
    "KIND_MAPPING_KEYS",
    "KIND_OPAQUE",
    "KIND_PLUGIN_TRIGGER",
    "KIND_REGEX_ALTERNATION",
    "KIND_RUNTIME_REGISTRATION",
    "ROUTING_AUTHORITY_FILES",
    "AuthorityEntry",
    "AuthorityInventory",
    "build_inventory",
    "normalize_term",
    "plugin_trigger_entries",
    "runtime_registration_entries",
    "scan_source_file",
    "terms_from_pattern",
]
