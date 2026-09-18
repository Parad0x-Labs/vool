from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from core.structured_literal_input import looks_like_structured_literal_input

# A word token, UNICODE-AWARE. This was `[A-Za-z0-9_'\-]+`, and the gap between that class and the
# `[^\w\s]` fallback was a silent hole: `á` IS a `\w` character, so the punctuation alternative
# skipped it, and the ASCII-only word alternative could not take it either. `findall` does not
# report what it fails to match, so the character was simply DELETED. Measured on this build:
# "What is the weather in Bogotá?" reached the runtime as "What is the weather in Bogot?", and
# "привет, как дела?" -- every character of which is `\w` -- reached it as ",?", an entire message
# reduced to its punctuation before any router or model saw a word of it.
#
# `\w` already covers `A-Za-z0-9_`, so this is the same class plus every letter the old one dropped.
_WORD_TOKEN = r"[\w'\-]+"
_TOKEN_RE = re.compile(rf"{_WORD_TOKEN}|[^\w\s]")
#: The same class, anchored. Used to decide whether a token is a WORD (rewritable) or punctuation
#: (noise-counted). It must stay in lockstep with `_WORD_TOKEN` -- when it was a second, separately
#: written ASCII literal, a non-ASCII word tokenized correctly and was then counted as punctuation.
_WORD_TOKEN_RE = re.compile(_WORD_TOKEN)

_DEFAULT_REWRITES = {
    "u": "you",
    "ur": "your",
    "pls": "please",
    "pls.": "please",
    "plz": "please",
    "hlp": "help",
    "im": "i am",
    "ive": "i have",
    "idk": "i do not know",
    "wanna": "want to",
    "gonna": "going to",
    "kinda": "kind of",
    "sorta": "sort of",
    "tho": "though",
    "cuz": "because",
    "bc": "because",
    "btw": "by the way",
    "ya": "you",
    "tho?": "though",
    "tg": "telegram",
    "msg": "message",
    "cmd": "command",
    "cfg": "config",
    "db": "database",
    "pwd": "password",
    "pwds": "passwords",
    "cred": "credit",
    "creds": "credits",
}

_TYPO_REWRITES = {
    "passwrods": "passwords",
    "teh": "the",
    "instal": "install",
    # The QUESTION WORDS. Widening _DOMAIN_VOCAB fixes the nouns and verbs of a tool request, and
    # leaves the word that says it IS a request: measured 2026-07-30, "wich apps are usign the most
    # memroy" had every noun corrected and still reached no lane, because the routing gate reads
    # the interrogative that opens the ask and "wich" is not one.
    #
    # These belong HERE rather than in _DOMAIN_VOCAB, and that is the whole safety argument: this
    # table is exact-match, so it cannot fuzzily capture a neighbour. `how` and `what` are three
    # and four letters, sit in the densest part of English, and would be catastrophic in a 0.84
    # matcher -- `who`, `now`, `low`, `chat`, `that`, `wham` are all within reach. An exact table
    # of misspellings that are not themselves words has no such failure mode.
    "hwo": "how",
    "hwat": "what",
    "waht": "what",
    "waht's": "what's",
    "whta": "what",
    "wht": "what",
    "wich": "which",
    "whcih": "which",
    "whihc": "which",
    "wehre": "where",
    "wher": "where",
    "hwere": "where",
    "mcuh": "much",
    "muhc": "much",
    "havee": "have",
    "hvae": "have",
    "haev": "have",
    "adn": "and",
    "taht": "that",
    "thier": "their",
    "yuo": "you",
    "yoru": "your",
    "amny": "many",
    # Below the four-character floor `_fuzzy_domain_match` enforces, so the vocabulary cannot
    # reach them however many words it holds. "hwo much fre space is left" needed both of these.
    "fre": "free",
    "spac": "space",
    "memry": "memory",
    "usig": "using",
    "dsk": "disk",
    "srceen": "screen",
    # `doe`/`dose` -> `does`, `wat` -> `what` and `manny` -> `many` were all drafted here and taken
    # back out: every one of them is a real dictionary word, and an exact table is only safe while
    # every key is a NON-word. That invariant is checked by a test rather than by care, because it
    # is exactly the kind of rule that decays the next time somebody adds an obvious-looking typo.
}

_DOMAIN_VOCAB = {
    "agent",
    "assistant",
    "bot",
    "capability",
    "chunk",
    "cluster",
    "config",
    "consensus",
    "credit",
    "credits",
    "daemon",
    "decentralized",
    "dialogue",
    "dispute",
    "fetch",
    "freshness",
    "greet",
    "help",
    "harden",
    "heartbeat",
    "helper",
    "identity",
    "knowledge",
    "lease",
    "liquefy",
    "lore",
    "mesh",
    "message",
    "node",
    "onboarding",
    "password",
    "passwords",
    "persona",
    "presence",
    "protect",
    "replica",
    "replication",
    "route",
    "security",
    "server",
    "setup",
    "shard",
    "solana",
    "standalone",
    "storage",
    "swarm",
    "telegram",
    "timeout",
    "transport",
    "validation",
    # ---- the runtime's own tools -------------------------------------------------------------
    # The list above was mesh/hive words only, so `difflib` at cutoff 0.84 had nothing to match a
    # tool typo against. Measured on 13 realistic misspellings, 12 were left uncorrected — including
    # "machine scpecs", which then failed twice over: no family claimed it AND `near_miss` was
    # False, so the intent arbiter was never consulted either. A tool the runtime owns became
    # unreachable because of one transposed pair of letters.
    #
    # Every entry below is a word the runtime's own 58 tool intents are named after or argued with.
    # `browser`, `weather`, `price`, `docker`, `battery`, `processor`, `notification`, `clipboard`,
    # `email` and `picture` were tried and removed: no tool is called any of those, and the first
    # one proved why it matters. Correcting `brwoser` -> `browser` is RIGHT, but the word then
    # hijacked "create hive mind task: stand alone vool brwoser version" to the live-info route,
    # and the operator was told live web lookup was disabled instead of getting their Hive task.
    # A vocabulary entry that names no tool buys no reachability and can only cost routing.
    #
    # Every entry was checked against a 900-word English list; the four that collided (`ready`,
    # `research`, `sell`, `whether`) are in _FUZZY_PROTECT above with their inflections.
    "machine",
    "specs",
    "hardware",
    "memory",
    "disk",
    "workspace",
    "folder",
    "folders",
    "directory",
    "directories",
    "file",
    "files",
    "path",
    "read",
    "write",
    "edit",
    "patch",
    "rename",
    "search",
    "find",
    "list",
    "create",
    "delete",
    "remove",
    "download",
    "upload",
    "tool",
    "tools",
    "tooling",
    "toolings",
    "skill",
    "skills",
    "plugin",
    "plugins",
    "sandbox",
    "command",
    "terminal",
    "shell",
    "script",
    "calendar",
    "image",
    "screenshot",
    "branch",
    "commit",
    "diff",
    "audit",
    "build",
    "install",
    "python",
    "process",
    "network",
    # The VERBS a tool request is phrased with. The noun-only first pass was half a fix, and the
    # live drive showed it: "chekc the workspce please" corrected `workspce`, left `chekc`, and the
    # turn gave up after 60s with "I couldn't map that cleanly to a real action". A tool request is
    # a verb and a noun, and correcting one of them is correcting neither.
    #
    # Added only after sweeping them against English, because a short verb is the dangerous end of a
    # 0.84 cutoff. Three collide and their victims are in _FUZZY_PROTECT above: `run` takes `ruin`,
    # `rung` and `runt`; `show` takes `shown` and `showy`; `inspect` takes `insect`. My own estimate
    # of the risk was WRONG in both directions before I measured — I expected `turn` and `burn` to
    # collide with `run` and they do not, because SequenceMatcher scores contiguous blocks rather
    # than subsequences.
    "check",
    "show",
    "open",
    "run",
    "inspect",
    "review",
    "explain",
    "summarize",
    "compare",
    "describe",
    "analyze",
    "print",
    "count",
    "copy",
    "move",
    "start",
    "stop",
    # ---- the ORDINARY nouns these questions are made of ---------------------------------------
    # The two passes above added the tool NAMES and the tool VERBS. A user does not type either.
    # They type "how much disk SPACE do i have left" -- and `space`, `screen`, `resolution`,
    # `version`, `downloads` and the rest were in no list, so one slip in the only word that
    # carries the question dropped the whole turn out of the fast router.
    #
    # Reproduced on the deployed build 2026-07-30: "how mcuh disk sapce do i havee left" was
    # normalized to itself, changed nothing, matched no phrase table, and cost a 60s model path;
    # the clean form answered in 0.1s. Three typos is the reported cliff, but only ONE of the
    # three mattered -- `sapce`. `mcuh` and `havee` are ordinary English and stay misspelled,
    # because the router reads the tool phrase, not the sentence.
    #
    # Every entry was swept against the 235,976-word system dictionary (/usr/share/dict/words),
    # both through the 0.84 difflib cutoff and through the exact-transposition rule, and every
    # collision that is a word people actually use is in _FUZZY_PROTECT with a note. 99 collisions
    # were found across 37 candidates; 37 of them are protected.
    #
    # Three-letter entries were tried and REJECTED, which was the opposite of what the brief
    # assumed. `ram`, `cpu` and `gpu` cannot buy anything: `_fuzzy_domain_match` returns early on
    # any token shorter than four characters, so no typo of a three-letter word is correctable in
    # either direction. They only add collisions -- `ram` alone takes `cram`, `dram`, `gram`,
    # `pram`, `ramp`, `ream`, `roam` and `tram`. Cost with no reachability, so they are not here.
    # `temperature` and `percent` are out under the existing rule: no tool is named or argued with
    # either, so like `browser` and `weather` before them they can only cost routing.
    "space",
    "version",
    "versions",
    "downloads",
    "resolution",
    "screen",
    "battery",
    "drive",
    "drives",
    "disks",
    "desktop",
    "documents",
    "document",
    "size",
    "sizes",
    "usage",
    "apps",
    "application",
    "applications",
    "program",
    "programs",
    "laptop",
    "computer",
    "system",
    "systems",
    "uptime",
    "model",
    "cores",
    "monitor",
    "display",
    "volume",
    "capacity",
    "free",
    "gigabytes",
    "settings",
    # `running` and `using` are the two participles the process detectors key on
    # (_RUNNING_PROCESSES_RE wants "running programs" adjacent, _RESOURCE_PRESSURE_RE wants
    # "using ... memory"), so a slip in either drops the turn even when every noun is spelled
    # right: "list teh runnign programms" corrected `teh` and `programms` and still reached no
    # lane. They are the most collision-prone entries here and carry the longest protect list.
    "running",
    "using",
}


@dataclass
class NormalizationResult:
    raw_text: str
    normalized_text: str
    replacements: dict[str, str] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)


def shorthand_expansions() -> dict[str, str]:
    """The shorthand table normalization applies, read-only.

    Consumers that must recognize what a user's words BECAME after normalization (the plugin
    intent matcher re-identifies a dotted intent the shorthand rewrite silently renamed, e.g.
    `vool-database.db.create` typed and normalized to `...database database...`) read this
    table instead of importing a private — one authority, no drift.
    """

    return dict(_DEFAULT_REWRITES)


def _join_tokens(tokens: list[str]) -> str:
    out: list[str] = []
    for token in tokens:
        if not out:
            out.append(token)
            continue
        if token in {".", ",", "!", "?", ":", ";", ")"}:
            out[-1] = out[-1].rstrip()
            out.append(token)
            continue
        if token in {"(", "[", "{"}:
            out.append(token)
            continue
        if out[-1] in {"(", "[", "{", "/", "-", "->"}:
            out.append(token)
            continue
        out.append(f" {token}")
    return "".join(out).strip()


# Common English words that collide with the domain vocabulary under fuzzy match
# (e.g. "project" -> "protect"). Never rewrite these — corrupting a mission word is
# worse than missing a rare typo fix.
_FUZZY_PROTECT = {
    "project", "projects", "product", "products", "protect", "protects",
    "connect", "correct", "collect", "subject", "object", "select",
    "please",
    # Every entry below was measured, not guessed: each one is a word that `_fuzzy_domain_match`
    # would otherwise REWRITE. Found by running the matcher over a 900-word English list.
    #
    # Inflections of these are deliberately NOT listed. `_is_inflection_of` already covers
    # `researcher`, `selling`, `served`, `warmth` and the rest, and a first draft that padded them in
    # by hand turned out to be dead weight — a sabotage removing four of them broke nothing, which
    # is how the redundancy was found. A protect list nobody can reach protects nothing.
    #
    # Introduced by the tool vocabulary below:
    "research",   # -> "search"
    "sell",       # -> "shell"
    # PRE-EXISTING, from the original mesh/hive vocabulary and live before any of this: "it's hard"
    # became "it's shard", "stay warm" became "stay swarm", "that person" became "that persona",
    # "how strange" became "how storage".
    "hard",       # -> "shard"
    # `shared` -> `shard` too, and the 900-word sweep above did not carry the word. Measured on the
    # running daemon: "What's a sensible file naming convention for a shared drive that has many
    # versions of the same document?" was rewritten to "...a shard drive..." before any router saw
    # it, and "a s|hard drive|" then matched the disk phrase table as a bare substring — the turn was
    # answered with this machine's free space, 2/2. "shared drive" and "shared folder" are ordinary
    # storage English, so this one collides constantly rather than rarely.
    #
    # Re-sweeping the matcher over the 235,974-word system dictionary (not 900) says this list is
    # incomplete by far more than one word: `both`->`bot`, `older`->`folder`, `personal`->`persona`,
    # `relation`->`replication`, `delegate`->`delete`, `creative`->`create`, `preview`->`review`,
    # `acknowledge`->`knowledge` and ~360 others rewrite live today. That is its own measured pass —
    # some of the 366 hits ARE the typos this corrector exists to fix (`creat`, `foder`, `machin`,
    # `calender`), so the list cannot simply be pasted in — and it is NOT done here.
    "shared",     # -> "shard"
    "identify",   # -> "identity"
    # Measured on a served daemon 2026-09-14 (tests/test_email_served_account_journeys.py): "Now check my
    # personal inbox for the orchard supplier." reached the model's task summary as "my persona inbox".
    # `personal` names an email ACCOUNT the operator chooses by that word ("my personal inbox", "from my
    # personal account"), so this rewrite garbles the one word an account choice turns on. (`personally`
    # scores 0.82 against `persona`, under the cutoff, and needs no entry.)
    "personal",   # -> "persona"
    "person",     # -> "persona"
    "persons",    # -> "persona"
    "serve",      # -> "server"
    "strange",    # -> "storage"
    "warm",       # -> "swarm"
    # Taken by the tool VERBS below. Same rule, same sweep.
    "ruin", "rung", "runt",   # -> "run"
    "shown", "showy",         # -> "show"
    "insect",                 # -> "inspect"
    # ---- Taken by the ORDINARY NOUNS below ----------------------------------------------------
    # Swept over the whole 235,976-word system dictionary, not a 900-word sample: 99 words would
    # be rewritten by the 37 nouns added there. These are the ones a person actually types.
    #
    # The other 62 are dictionary entries like `aspace`, `spae`, `creen`, `attery`, `compter`,
    # `doment`, `versine`, `grogram` and `bescreen`, and they are deliberately NOT protected. They
    # are archaic, dialect or technical-obsolete, and a modern user who types one has almost
    # certainly dropped or swapped a letter in the vocabulary word -- `creen` for `screen`,
    # `attery` for `battery` -- which is precisely the correction this exists to make. Protecting
    # a word nobody types would only block the fix.
    "pace",                                        # -> "space"
    "aversion", "diversion", "erosion",            # -> "version"
    "inversion", "reversion",
    "evolution", "revolution", "solution",         # -> "resolution"
    "irresolution",
    "scree",                                       # -> "screen"
    "buttery", "battler", "cattery",               # -> "battery"
    "derive", "dive", "drivel", "driven",          # -> "drive"
    "diss",                                        # -> "disks"
    "documentary", "docent",                       # -> "document"
    "seize",                                       # -> "size"
    "sage",                                        # -> "usage"
    "supplication", "applicator",                  # -> "application"
    "misapplication",
    "commuter", "compute", "copter",               # -> "computer"
    "systemic",                                    # -> "system"
    "mode",                                        # -> "model"
    "misplay", "redisplay",                        # -> "display"
    "incapacity", "rapacity",                      # -> "capacity"
    "settling", "settlings",                       # -> "settings"
    "onscreen",                                    # -> "screen"
    # ---- and the INFLECTED forms, which the dictionary sweep could not see --------------------
    # /usr/share/dict/words is very nearly uninflected, so sweeping it says `derive` collides with
    # `drive` and stops there. It cannot say that `derives`, `dries`, `deprives` and `drivers` all
    # collide with `drives`, that `computed` collides with `computer`, that `programmers` collides
    # with `programs`, or that `conversions` collides with `versions` -- and those are the forms
    # people actually write. Found by re-sweeping 2.5M generated surface forms, and corroborated
    # against this repo's own prose, where `computed` appears 227 times and `drivers` is ordinary
    # machine vocabulary.
    #
    # Protecting the singular does NOT protect these: `_is_inflection_of` relates a token to a
    # VOCABULARY word, and `derives` is not an inflection of `drives`. It is a different word that
    # merely scores above the cutoff.
    "computed", "computes",                                    # -> "computer"
    "chores", "scores", "cors", "ores", "crores",              # -> "cores"
    "docents", "documenters",                                  # -> "document(s)"
    "deprives", "derives", "dives", "dries",                   # -> "drives"
    "drivers", "drivels",
    "programmer", "programmers", "programme", "programmes",    # -> "program(s)"
    "revolutions", "solutions",                                # -> "resolution"
    "sittings", "seatings", "nettings", "stings",              # -> "settings"
    "bettings", "gettings",
    "seizes",                                                  # -> "sizes"
    "systemics",                                               # -> "systems"
    "aversions", "conversions", "diversions", "erosions",      # -> "versions"
    "inversions", "perversions", "reversions", "subversions",
    "traversions",
    "applicators", "supplications", "misapplications",         # -> "applications"
    "amplifications", "reapplications", "plications",
    "utime",                                                   # -> "uptime"
    # `running` and `using` are the most collision-prone entries in the vocabulary, which is why
    # they are swept hardest. `sing` -> `using` scores 0.888 and `cunning` -> `running` scores
    # 0.857; both are ordinary words and both would be silently corrupted.
    "cunning", "dunning", "gunning", "pruning",   # -> "running"
    "sunning", "ruining",
    "sing", "suing", "musing", "busing", "fusing",  # -> "using"
    "rerunning",                                    # -> "running"
    "skillset", "skillsets",                        # -> "skills"
}


def _transposed_domain_match(token: str) -> str | None:
    """A single adjacent-letter swap that lands exactly on a vocabulary word.

    Transposition is the most common typing error and the 0.84 similarity cutoff sits just above it:
    `serach`/`search` scores 0.833 and `imgae`/`image` scores 0.800, so both were left uncorrected
    while `scpecs`/`specs` at 0.909 was fixed. Lowering the cutoff for everything would buy those
    two at the cost of every near-miss in the language.

    An EXACT swap is a far stronger signal than a similarity score: the corrected form has to be a
    tool word character-for-character, so there is nothing to be approximately wrong about. Checked
    against a 900-word English list, no ordinary word transposes into one.
    """

    for index in range(len(token) - 1):
        swapped = (
            token[:index] + token[index + 1] + token[index] + token[index + 2:]
        )
        if swapped != token and swapped in _DOMAIN_VOCAB:
            return swapped
    return None


# Endings that make a real word out of a vocabulary word. A token that differs from a vocabulary
# word only by one of these is English, not a typo.
_INFLECTIONS = frozenset(
    ["s", "es", "d", "ed", "ing", "er", "ers", "or", "ors", "ion", "ions", "al", "ly", "y", "ies", "ied", "ier", "iest", "ment", "ness", "able", "ful", "less"]
)


def _is_inflection_of(token: str, word: str) -> bool:
    """Whether ``token`` is ``word`` wearing an ordinary English ending.

    This is what keeps a TYPO corrector from turning into a lemmatiser. `difflib` at cutoff 0.84
    scores `created`/`create` at 0.923 and `router`/`route` at 0.909, so adding `create` and `route`
    to the vocabulary silently rewrote every past tense and agent noun of both. Measured: adding the
    tool words rewrote `created` -> `create`, and the sentence "update the one you created already"
    became "update the one you create already" — six Hive tests failed at once because the update
    detector no longer recognised its own trigger phrase.

    Several of these were live before any of this. `message`, `route`, `node`, `server`, `storage`
    and `persona` were already in the vocabulary, so `messages`, `router`, `nodes` and `served` were
    already being rewritten. A protect list cannot keep up with this — it is a property of the
    matcher, so it is checked as one.
    """

    if token == word:
        return True
    stems = {word}
    if word.endswith(("e", "y")):
        stems.add(word[:-1])
    for stem in stems:
        if token != stem and token.startswith(stem) and token[len(stem):] in _INFLECTIONS:
            return True
        if token != stem and stem.startswith(token) and stem[len(token):] in _INFLECTIONS:
            return True
    return False


def _fuzzy_domain_match(token: str) -> str | None:
    """A vocabulary word this token is a misspelling OF, or None.

    The three guards below are STRUCTURAL -- they hold for every word in the language rather than
    for a list of words somebody remembered to add. That matters because the protect list cannot be
    the whole defence: it is a growing enumeration of collisions, and this matcher was corrupting
    427 entries of the 234,335-word system dictionary while that list held ~120 of them.

    Each guard is measured, not assumed. Against the 29 real typo corrections this repo's own tests
    require (`scpecs`, `serach`, `chekc`, `creat`, `foldr`, `sapce`, `runnign`, ...), all three
    together lose EXACTLY ZERO, while removing 199 of the 427 dictionary corruptions.
    """

    if len(token) < 4 or not token.isalpha():
        return None
    # NON-ASCII is not correctable here, only damageable. Every word in `_DOMAIN_VOCAB` is ASCII, so
    # a token carrying a diacritic or a non-Latin script has no true match in it -- the only thing a
    # similarity score can do is drag it onto an unrelated English word. Contract: Unicode must not
    # alter semantic identity.
    if not token.isascii():
        return None
    if token in _FUZZY_PROTECT:
        return None
    transposed = _transposed_domain_match(token)
    if transposed:
        return transposed
    matches = difflib.get_close_matches(token, _DOMAIN_VOCAB, n=1, cutoff=0.84)
    if not matches:
        return None
    match = matches[0]
    if match == token:
        return None
    # THE MATCH needs the same four-character floor the token already had. The floor was written on
    # one side only, and the comment above `_DOMAIN_VOCAB` drew the wrong conclusion from it -- it
    # argues three-letter entries "cannot buy anything" because the matcher "returns early on any
    # token shorter than four characters, so no typo of a three-letter word is correctable in either
    # direction". That is true for the TOKEN and false for the MATCH: `bot` and `run` were still in
    # the vocabulary as targets, and a four-letter token reaches a three-letter word at ratio 0.857,
    # above the cutoff, for free. Measured live: `both`, `boat`, `boot`, `bolt`, `bout` and `brot`
    # all became `bot`. Deleting a character from a three-letter word is the highest-risk edit in
    # the language and it was the one edit with no floor under it.
    if len(match) < 4:
        return None
    # THE FIRST LETTER IS NOT A TYPO SITE. This is the guard that closes the reported defect:
    # `bread` -> `read` (0.888) and `kill` -> `skill` (0.888) are both a whole leading character
    # added or removed, which does not produce a misspelling of the word -- it produces a DIFFERENT
    # word, and a common one. "Explain in simple words why bread rises" reached the model as "why
    # read rises", which it then reasonably asked for clarification about; "kill the zombie
    # processes" reached it as "skill the zombie processes".
    #
    # People do not typically lose the first letter of a word: the sweep over the repo's own typo
    # corpus is unanimous -- all 29 corrections share their first character with their target, and
    # so do the doubled-first-letter forms (`sspace` -> `space`). The cost of this guard is
    # therefore zero corrections and the benefit is 184 dictionary words, `bread` and `kill`
    # included. Anchoring one end is what separates "misspelled that word" from "meant another one".
    if token[0] != match[0]:
        return None
    if _is_inflection_of(token, match):
        return None
    return match


# Exact-literal spans that must survive normalization character-for-character:
# grouped numbers, decimals/versions, ISO dates + times, Windows/POSIX paths, dotted names
# (.null domains, filenames), and owner/repo names. Without this the tokenizer
# splits "0.037" -> "0. 037", "alice.null" -> "alice. null", and paths on / and \.
_LITERAL_SPAN_RE = re.compile(
    r"""(
      # A QUOTED SPAN, first of all: quoting is the one gesture a user has for "these exact words",
      # and it was the one gesture this function ignored. Measured on this build, `the spell is
      # called "Kill" in the game` reached the runtime as `the spell is called " skill " in the
      # game` -- the quoted term rewritten to a different word AND the quotes pushed off it, so
      # nothing downstream could even tell a quoted term had been there to protect.
      #
      # A fictional name, a term of art, a literal string the user wants searched for and a word
      # being discussed AS a word all arrive this way, and every one of them is exactly the case
      # where a typo corrector is wrong by construction: quoted text is evidence, not prose.
      #
      # Bounded to a single line and to 400 characters so an unmatched quote cannot swallow the
      # message; with no closing quote nothing matches and the old path runs unchanged.
        "[^"\n]{1,400}"                                            # "quoted span"
      | `[^`\n]{1,400}`                                            # `code span`
      # Single quotes need the contraction guard: `don't`, `it's` and `the boys' toys` must NOT read
      # as quote delimiters, so an opening quote may not follow a word character and a closing quote
      # may not precede one.
      | (?<![\w'])'[^'\n]{1,400}'(?![\w])                          # 'quoted span'
      # A URL, FIRST, so it wins over every path alternative below. There was no URL rule here at
      # all, so `://` was tokenized and rejoined with spaces: measured 2026-07-30, "fetch
      # https://example.com and tell me what the page says" reached the front door as
      # "fetch https: / /example.com and ...". Nothing downstream could see a URL in that, while
      # `explicit_path_in` DID see the absolute posix path `//example.com` — so every "open this
      # link" turn was answered "I can only read files inside: ~/Desktop, ~/Downloads, ~/Documents".
      # The web lane, the machine lane and the arbiter were all reading a corrupted string.
      | \b[A-Za-z][A-Za-z0-9+.\-]*://[^\s`"']+                    # url  https://example.com/a?b=c
      # An EMAIL ADDRESS is one literal for the same reason. Measured 2026-09-15 on the served Contacts journey:
      # "work email alex.chen@example.test" reached the runtime as "alex. chen @ example. test", so every lane read an
      # address that does not exist, and request provenance could not find the address the user typed.
      | \b[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+  # email  alex.chen@example.test
      # Bare hosts are literals too. Their spelling must not depend on a hand-picked
      # extension list (which omitted .ai); resolution belongs to the network owner.
      | \b(?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\b(?:/[^\s`"']*)?
      | [A-Za-z]:\\[^\s]+                                        # windows path  C:\Users\...
      # The `~` / `.` / `..` prefix has to be INSIDE the protected span. Without it only the
      # `/Desktop/...` part was protected, `~` fell through to the tokenizer as its own token, and the
      # rejoin put a space in: "~/Desktop/x" reached the runtime as "~ /Desktop/x". Every home-rooted
      # path a user typed was silently corrupted, and the planner then failed to see a path at all and
      # fell back to listing the bare home folder.
      | (?:~|\.{1,2})(?:/[A-Za-z0-9_.\-]+)+/?                    # home/relative path  ~/Desktop/x, ./src/app.py
      | (?:/[A-Za-z0-9_.\-]+)+/?                                 # absolute posix path  /mnt/data/file.txt
      | \b[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+/?               # relative path  data/keys/file.json, owner/repo
      # The extension list is what makes a dotted name a LITERAL rather than two tokens, so a
      # format missing from it is a filename the runtime never sees. `pdf` was missing, measured
      # 2026-07-30: "scanned_invoice.pdf in my downloads folder - what invoice number does it
      # show?" reached the front door as "scanned_invoice. pdf in my downloads folder ...", no lane
      # could see a filename, and the turn answered with a listing of the folder instead of the
      # file. The document and media formats a user actually names are listed here for the same
      # reason the code formats already were.
      | \b[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)*\.(?:null|sol|eth|io|com|org|net|xyz|dev|app|py|ps1|txt|json|jsonl|md|toml|cfg|ini|log|sh|bat|cmd|yaml|yml|js|ts|tsx|rs|go|html|css|pdf|csv|tsv|doc|docx|xls|xlsx|ppt|pptx|rtf|epub|png|jpg|jpeg|gif|webp|svg|heic|mp3|mp4|mov|wav|zip|sql|xml|srt)\b
      | \b\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b   # ISO date [+ time]
      | \b\d{1,2}:\d{2}(?::\d{2})?\b                             # clock time    14:30
      | \b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b                         # grouped number  3,000 / 1,234.56
      | \b\d+\.\d+(?:\.\d+)*\b                                   # decimals / versions  0.037, 1.2.3
    )""",
    re.VERBOSE,
)


def _protect_literals(value: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return f" zzlit{len(spans) - 1}zz "  # alnum placeholder: one token, no rewrite/fuzzy

    return _LITERAL_SPAN_RE.sub(_stash, value), spans


def _restore_literals(text: str, spans: list[str]) -> str:
    for index, literal in enumerate(spans):
        text = text.replace(f"zzlit{index}zz", literal)
    return text


def normalize_user_text(text: str, *, session_lexicon: dict[str, str] | None = None) -> NormalizationResult:
    raw_text = text or ""
    # NFC on the DERIVED text only; `raw_text` is returned exactly as typed. The two forms of an
    # accented word ("\u00e9" precomposed vs "e" + combining acute) are canonically equivalent -- same
    # character, same meaning, different code points -- and leaving both in circulation means a
    # downstream comparison can miss a word that is visibly present. NFC is the composition
    # `core.semantic.canonical_text` already names as this runtime's canonical representation.
    #
    # NFC and NOT NFKC, deliberately: the compatibility forms rewrite ligatures, fullwidth
    # characters and superscripts into different characters, which is the destructive kind of
    # normalization this function exists to stop doing.
    value = unicodedata.normalize("NFC", raw_text).strip()
    value = re.sub(r"[\u2018\u2019]", "'", value)
    value = re.sub(r"[\u201c\u201d]", '"', value)
    if looks_like_structured_literal_input(value):
        quality_flags: list[str] = []
        if len(value.split()) <= 5:
            quality_flags.append("short_input")
        if value and not re.search(r"[.?!]$", value):
            quality_flags.append("fragmented")
        return NormalizationResult(
            raw_text=raw_text,
            normalized_text=value,
            replacements={},
            quality_flags=quality_flags,
        )
    value = re.sub(r"\s+", " ", value)
    value, literal_spans = _protect_literals(value)
    value = re.sub(r"[!?]{2,}", "?", value)
    value = re.sub(r"\.{3,}", ".", value)

    rewrites = dict(_DEFAULT_REWRITES)
    typo_rewrites = dict(_TYPO_REWRITES)
    if session_lexicon:
        rewrites.update({str(k).lower(): str(v).lower() for k, v in session_lexicon.items()})

    output_tokens: list[str] = []
    replacements: dict[str, str] = {}
    shorthand_count = 0
    typo_count = 0
    ambiguous_noise = 0

    for token in _TOKEN_RE.findall(value):
        lower = token.lower()
        replacement = token
        if _WORD_TOKEN_RE.fullmatch(token):
            if lower in rewrites:
                replacement = rewrites[lower]
                replacements[token] = replacement
                shorthand_count += 1
            elif lower in typo_rewrites:
                replacement = typo_rewrites[lower]
                replacements[token] = replacement
                typo_count += 1
            else:
                fuzzy = _fuzzy_domain_match(lower)
                if fuzzy:
                    replacement = fuzzy
                    replacements[token] = replacement
                    typo_count += 1
        elif token not in {".", ",", "!", "?", ":", ";", "(", ")"}:
            ambiguous_noise += 1
        output_tokens.extend(replacement.split(" "))

    normalized = _restore_literals(_join_tokens(output_tokens), literal_spans)
    quality_flags: list[str] = []
    if shorthand_count >= 2:
        quality_flags.append("shorthand_heavy")
    if typo_count >= 1:
        quality_flags.append("typo_heavy")
    if ambiguous_noise >= 2:
        quality_flags.append("noisy_punctuation")
    if len(normalized.split()) <= 5:
        quality_flags.append("short_input")
    if normalized and not re.search(r"[.?!]$", normalized):
        quality_flags.append("fragmented")

    return NormalizationResult(
        raw_text=raw_text,
        normalized_text=normalized,
        replacements=replacements,
        quality_flags=quality_flags,
    )
