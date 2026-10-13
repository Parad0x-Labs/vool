"""Secret-only redaction for anything persisted to disk (chat log, memory, summaries).

Unlike task_router.redact_text (a lossy SUMMARY redactor that also collapses whitespace and strips
URLs/emails), this masks ONLY high-confidence secrets and leaves ordinary prose intact, so it is safe
to run on the raw conversation before it is written to disk. It is high-precision on purpose: it would
rather miss an unlabelled secret than mangle a normal message. Pair it with encryption at rest — this
reduces plaintext exposure, it is not a substitute for protecting the store's key.
"""
from __future__ import annotations

import re
import threading

#: Exact-value scrub registry (credential intelligence, 2026-09-02). The pattern rules below
#: only catch keys with a KNOWN vendor shape; a custom-gateway key like ``zk9-…`` matches
#: nothing and would survive every rule. The intake boundary therefore registers the exact
#: pasted value here the moment it holds it, and ``redact_secrets`` scrubs those exact values
#: FIRST — so the conversation-log choke point, credential diagnostics, and the bug-report
#: backstop (which delegates here) cannot leak a format no pattern knows.
_EXACT_CAP = 64
_EXACT_MIN_LEN = 8
_exact_lock = threading.Lock()
_exact_secrets: dict[str, None] = {}


def register_exact_secret(value: str) -> None:
    """Register one exact secret value for scrubbing (>= 8 chars). Bounded to the most recent
    ``_EXACT_CAP`` values so a long process cannot grow an unbounded plaintext set."""
    cleaned = str(value or "").strip()
    if len(cleaned) < _EXACT_MIN_LEN:
        return
    with _exact_lock:
        _exact_secrets[cleaned] = None
        while len(_exact_secrets) > _EXACT_CAP:
            _exact_secrets.pop(next(iter(_exact_secrets)))


def clear_exact_secrets_for_tests() -> None:
    with _exact_lock:
        _exact_secrets.clear()
        _public_identifiers.clear()


# Key-SHAPED strings the runtime itself minted as public -- a confirmed transaction signature is
# 64 bytes of base58 exactly like a Solana secret key, and a shape rule cannot tell them apart.
# The wallet registers each signature it broadcasts or renders; the base58 rules then leave that
# exact value readable and keep masking every unregistered run. Bounded like the secret set.
_public_identifiers: dict[str, None] = {}


def register_public_identifier(value: str) -> None:
    """Mark one exact key-shaped value as public (a tx signature the runtime produced)."""
    cleaned = str(value or "").strip()
    if len(cleaned) < 32:
        return
    with _exact_lock:
        _public_identifiers[cleaned] = None
        while len(_public_identifiers) > _EXACT_CAP:
            _public_identifiers.pop(next(iter(_public_identifiers)))


def _is_public_identifier(value: str) -> bool:
    with _exact_lock:
        return value in _public_identifiers


def _mask_key_shaped(match: re.Match[str]) -> str:
    run = match.group(0)
    return run if _is_public_identifier(run) else "[redacted-key]"


def _exact_secrets_for_tests():
    """Read-only view for tests auditing the bounded window."""
    with _exact_lock:
        return dict(_exact_secrets)


# PEM private-key blocks (RSA/EC/OPENSSH/generic).
_PEM_MARKER_RE = re.compile(r"-----(?P<kind>BEGIN|END) [A-Z0-9 ]*PRIVATE KEY-----")


def _private_key_spans(value: str):
    """Scan each marker once; an incomplete private key stays confidential too."""
    start = None
    for marker in _PEM_MARKER_RE.finditer(value):
        if marker.group("kind") == "BEGIN":
            if start is None:
                start = marker.start()
        elif start is not None:
            yield start, marker.end()
            start = None
    if start is not None:
        yield start, len(value)


def _redact_private_keys(value: str) -> str:
    pieces = []
    end = 0
    for start, stop in _private_key_spans(value):
        pieces.extend((value[end:start], "[redacted-private-key]"))
        end = stop
    pieces.append(value[end:])
    return "".join(pieces)

# JSON Web Tokens (header.payload.signature, base64url).
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")
# Well-known API-key / token shapes (prefix + sufficient length to avoid false positives).
_API_KEY_RE = re.compile(
    r"\b("
    r"sk-(?:ant-)?[A-Za-z0-9_-]{16,}"        # OpenAI / Anthropic / OpenRouter (sk-or-) / DeepSeek / Moonshot
    r"|gsk_[A-Za-z0-9]{20,}"                   # Groq
    r"|AKIA[0-9A-Z]{16}"                       # AWS access key id
    r"|gh[posru]_[A-Za-z0-9]{20,}"             # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"           # Slack
    r"|AIza[A-Za-z0-9_-]{20,}"                 # Google
    r"|glpat-[A-Za-z0-9_-]{16,}"               # GitLab
    r"|npm_[A-Za-z0-9]{20,}"                   # npm
    r")\b"
)
# Long base58 (>= 64 chars) — a Solana secret key / seed. A 32-44 char base58 (a public address) is
# deliberately NOT matched, so ordinary wallet addresses in chat stay readable.
_B58_SECRET_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{64,88}\b")
# Bitcoin WIF private key: 51-52 base58 chars starting 5/K/L. Distinguishable from a Solana
# public address (32-44 chars) by length + prefix, so this does not mask ordinary addresses.
_WIF_RE = re.compile(r"\b[5KL][1-9A-HJ-NP-Za-km-z]{50,51}\b")
# 0x/0X + 64 hex — an EVM private key (the wallet's BACKUP_FORMAT_EVM). The SAME shape is a public
# EVM transaction hash and spelling alone cannot tell them apart, so this follows the base58 law
# above: every unregistered run is masked, and the wallet registers each hash it actually mints
# or renders (publish_identifier at the settlement/journal/view seams) so its own stay readable.
# Both PREFIX spellings are the wallet's own accepted form — pilot_custody decodes a backup by
# ``text[:2].lower() == "0x"``, so ``0X…`` is the same key and is masked identically. An EVM
# ADDRESS (0x + 40 hex) is a different, public shape and stays untouched. A BARE 64-hex digest
# (no 0x prefix) is deliberately NOT this rule's business: the runtime's own diagnostics are
# full of bare sha256 hex, so that ambiguity stays explicit (see the base58 rule above) instead
# of silently masking every diagnostic digest.
_EVM_HEX64_RE = re.compile(r"\b0[xX][0-9a-fA-F]{64}\b")
# "Bearer <token>" with a SPACE (Authorization headers, OAuth) — the labelled rule below only
# catches label:value / label=value, so a space-separated bearer token would otherwise persist.
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
# A credential that lives in a URL PATH instead of a header. UsePod authenticates a prepaid call
# with ``https://api.usepod.ai/proxy/<token>/v1``: the path segment after ``/proxy/`` IS the key, so
# the URL itself is a secret, and an HTTP library's own error text ("... for url: <url>"), a log
# line or a stored base URL carries it verbatim. No vendor-shape rule above can see it (a UUID has
# no prefix), and the exact-value registry only knows a token once something has resolved it. The
# segment is masked in the plain, percent-encoded and JSON-escaped spellings of the path; the
# accountless ``/proxy/x402/`` path holds no token and stays readable.
_PATH_TOKEN_SEGMENT = r"(?!x402(?![A-Za-z0-9_-]))[A-Za-z0-9][A-Za-z0-9_-]{15,127}(?![A-Za-z0-9_-])"
_PATH_TOKEN_RE = re.compile(r"(/proxy/|%2[Ff]proxy%2[Ff]|\\/proxy\\/)(" + _PATH_TOKEN_SEGMENT + r")")
# label: value / label=value where the label names a secret. Keeps the label, masks the value.
#
# The label may be part of a COMPOUND env-var style name (AWS_SECRET_ACCESS_KEY, DB_PASSWORD,
# MY_AUTH_TOKEN). A plain \b cannot find it there: underscore is a word character, so \bsecret\b
# never matches inside AWS_SECRET_ACCESS_KEY, and every env-var form leaked. Allow underscore/dash
# separated segments on either side and capture the WHOLE key name, so the output still says which
# key was masked. Values were previously caught only when they matched a known vendor shape (sk-,
# ghp_, ...), which left generic passwords fully exposed.
_SECRET_LABEL = (
    r"password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|bearer"
    r"|client[_-]?secret|private[_-]?key|seed[_-]?phrase|mnemonic|passphrase|credential"
)
_LABELED_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:" + _SECRET_LABEL + r")(?:[_-][A-Za-z0-9]+)*)"
    r"\s*[:=]\s*[\"']?([^\s\"']{4,})[\"']?"
)

# Zcash keys (bech32/bech32m, lowercase or uppercase) on mainnet, testnet and regtest: Sapling spending keys
# (extended and not), and the unified full and incoming viewing keys and Sapling viewing keys (extended full, full,
# incoming) that reveal every transaction of an account. No leading word boundary: a key glued to "_" or to an escaped
# "\n" in JSON text is still a key, and the long bech32 tail each prefix needs keeps ordinary words out.
_ZCASH_KEY_RE = re.compile(
    r"(?i)(?:secret-(?:extended|spending)-key-(?:main|test|regtest)1[0-9a-z]{20,}"
    r"|u(?:view|ivk)(?:test|regtest)?1[0-9a-z]{60,}|z(?:xview|view|ivk)(?:s|testsapling|regtestsapling)1[0-9a-z]{60,})"
)

_SPECIFIC = (_JWT_RE, _API_KEY_RE, _B58_SECRET_RE, _WIF_RE, _EVM_HEX64_RE, _BEARER_RE, _PATH_TOKEN_RE, _ZCASH_KEY_RE)


# Personal secrets people type into chat in plain words (v0.7.1 memory secret filter). The
# labelled rules above need ``label: value`` or ``label=value``; "my card PIN is 4433", "my
# password is hunter22" and a bare card number slipped through every one of them and were stored
# and served back by memory. Each rule below fires only when the clause itself names the value a
# PIN, passcode, password, security code or card number, or the digits are a grouped, Luhn-valid
# card number, so dates, prices, phone numbers, order ids and version numbers stay untouched.

#: Numeric secrets: the value must be 3-12 digits right after the label and its verb or sign.
_PIN_LABEL = (
    r"(?:card\s+|bank\s+|atm\s+|debit\s+|credit\s+|sim\s+|phone\s+)?"
    r"(?:pin(?:\s+(?:code|number|no\.?))?|passcode|cvv2?|cvc2?|csc|security\s+code)"
)
#: Word secrets: any value token, minus ordinary words that describe the secret instead of being it.
_WORD_VALUE_LABEL = r"(?:password|passwd|pwd|passphrase|passcode|login\s+code)"
#: "password for my email is ...": up to four short words between the label and its verb.
#: Label, object, verb and value stay on one line: "password is" ending a line does not own
#: the first word of the next one.
_LABEL_OBJECT = r"(?:[ \t]+(?:for|to|of|on|at)(?:[ \t]+[\w'@.-]{1,40}){1,4}?)?"
_LABEL_VERB = r"(?:[ \t]*[:=#][ \t]*|[ \t]+(?:is|was|are|were)[ \t]*:?[ \t]*|[ \t]+-[ \t]+)"
_PIN_VALUE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(" + _PIN_LABEL + r")" + _LABEL_OBJECT + _LABEL_VERB
    + r"[\"'“‘]?(\d(?:[ -]?\d){2,11})(?![\d])"
)
_LABELLED_WORD_VALUE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(" + _WORD_VALUE_LABEL + r")s?" + _LABEL_OBJECT + _LABEL_VERB
    + r"[\"'“‘]?([^\s\"'“”‘’]{3,128})"
)
#: Words that follow "password is" without being a password ("is required", "is the same as ...").
_WORD_VALUE_NON_VALUES = frozenset(
    {
        "the", "a", "an", "my", "your", "his", "her", "our", "their", "its", "this", "that", "not",
        "no", "now", "still", "also", "just", "too", "very", "so", "same", "different", "required",
        "needed", "optional", "wrong", "incorrect", "correct", "right", "invalid", "valid", "expired",
        "changed", "reset", "set", "saved", "stored", "missing", "empty", "blank", "weak", "strong",
        "secure", "insecure", "long", "short", "case-sensitive", "hidden", "encrypted", "hashed",
        "unknown", "forgotten", "lost", "new", "old", "ok", "okay", "fine", "good", "bad", "easy",
        "hard", "simple", "safe", "private", "secret", "protected", "locked", "unlocked", "being",
        "been", "here", "there", "in", "on", "at", "for", "with", "from", "by", "to", "of", "and",
        "or", "but", "if", "when", "what", "which", "where", "how", "why", "who", "something",
        "nothing", "anything", "everything", "one", "it", "ready", "done", "working", "broken",
        "sent", "shown", "visible", "updated", "generated", "accepted", "rejected", "disabled",
        "enabled", "used", "shared", "written", "typed", "entered", "managed", "kept", "same.",
    }
)
_CARD_RUN_RE = re.compile(r"(?<![\d.,])\d(?:[ -]?\d){12,18}(?![\d])")
_CARD_GROUPED_RE = re.compile(r"\d{4}(?:([ -])\d{4}\1\d{4}\1\d{1,7}|[ -]\d{6}[ -]\d{4,5})")
_CARD_CONTEXT_RE = re.compile(
    r"(?i)\b(?:card|cards|visa|mastercard|master\s+card|amex|american\s+express|maestro|discover"
    r"|diners|jcb|unionpay|credit|debit|pan|cc)\b"
)
_CARD_CONTEXT_WINDOW = 60
_LABEL_CLAUSE_BOUNDARY_RE = re.compile(r"[.;!?\n]\s")
_MASKED_VALUE_PREFIX = "[redacted"


def luhn_valid(digits: str) -> bool:
    """The card-number checksum (ISO/IEC 7812). The wallet's card door uses this same check."""
    if not digits.isdigit():
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _clause_before(text: str, start: int) -> str:
    prefix = text[max(0, start - _CARD_CONTEXT_WINDOW):start]
    parts = _LABEL_CLAUSE_BOUNDARY_RE.split(prefix)
    return parts[-1] if parts else prefix


def _luhn_digit_run_spans(value: str):
    for match in _CARD_RUN_RE.finditer(value):
        raw = match.group(0)
        digits = re.sub(r"[ -]", "", raw)
        if not 13 <= len(digits) <= 19:
            continue
        named_card = _CARD_CONTEXT_RE.search(_clause_before(value, match.start())) is not None
        grouped = _CARD_GROUPED_RE.fullmatch(raw) is not None
        if named_card or (grouped and luhn_valid(digits)):
            yield match.start(), match.end()


def _labelled_word_spans(value: str):
    for match in _LABELLED_WORD_VALUE_RE.finditer(value):
        token = match.group(2)
        if token.startswith(_MASKED_VALUE_PREFIX):
            continue
        clean = token.rstrip(".,;)")
        if len(clean) < 3 or clean.lower() in _WORD_VALUE_NON_VALUES:
            continue
        yield match.start(2), match.start(2) + len(clean)


def _redactable_personal_spans(value: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    spans.extend((start, end, "[redacted-card-number]") for start, end in _luhn_digit_run_spans(value))
    spans.extend((match.start(2), match.end(2), "[redacted]") for match in _PIN_VALUE_RE.finditer(value))
    spans.extend((start, end, "[redacted]") for start, end in _labelled_word_spans(value))
    spans.sort()
    merged: list[tuple[int, int, str]] = []
    for start, end, mask in spans:
        if merged and start < merged[-1][1]:
            last_start, last_end, last_mask = merged[-1]
            merged[-1] = (last_start, max(last_end, end), last_mask)
            continue
        merged.append((start, end, mask))
    return merged


def contains_personal_secret(text: str) -> bool:
    """True if `text` carries a card number, PIN or password written in plain words."""
    return bool(text) and bool(_redactable_personal_spans(str(text)))


def redact_personal_secrets(text: str) -> str:
    """Mask only plain-word card numbers, PINs and passwords; everything else untouched."""
    return _redact_personal_secrets(str(text)) if text else text


def _redact_personal_secrets(value: str) -> str:
    spans = _redactable_personal_spans(value)
    if not spans:
        return value
    pieces: list[str] = []
    cursor = 0
    for start, end, mask in spans:
        pieces.append(value[cursor:start])
        pieces.append(mask)
        cursor = end
    pieces.append(value[cursor:])
    return "".join(pieces)


def redactable_personal_kinds(text: str) -> tuple[str, ...]:
    """Which plain-word personal secrets ``text`` carries: ``card_number``, ``pin``, ``password``.

    Used to tell the user what was not saved; never returns the values themselves."""
    if not text:
        return ()
    value = str(text)
    kinds: list[str] = []
    if next(_luhn_digit_run_spans(value), None) is not None:
        kinds.append("card_number")
    if _PIN_VALUE_RE.search(value) is not None:
        kinds.append("pin")
    if next(_labelled_word_spans(value), None) is not None:
        kinds.append("password")
    return tuple(kinds)

# A BIP-39 recovery phrase typed as plain words ("i wrote this down: used term aspect …") with
# no label at all. The labelled rule below masks only the FIRST whitespace token of its value,
# so even ``mnemonic: <12 words>`` kept eleven of twelve words readable — and under the BIP-39
# checksum the missing word is recoverable from the 2048-word list. A run of 12/15/18/21/24
# wordlist words that ALSO passes the BIP-39 checksum is a recovery phrase; a four-bit checksum
# means a random wordlist-heavy 12-word run can still validate (~1/16), so a rare prose masking
# is possible — the safe direction for a redactor, and the precision bar this module keeps
# (it misses/mangles nothing that is not a word-perfect, checksum-valid run). Tokens are matched
# under the SAME normalization the wallet's canonical mnemonic module accepts for real phrases
# (`_nfkd(phrase).lower().split()`, core/wallet/mnemonic.py): mixed case, UPPERCASE and
# NFKD-foldable spellings of a valid phrase are phrases the wallet itself would accept, so they
# must mask here too. The wordlist is the wallet product's own canonical file, read as DATA:
# this module never imports wallet code and never raises. If the file is absent or malformed
# the rule is UNAVAILABLE — never silently off: `mnemonic_redaction_available()` reports it so
# a sensitive-persistence caller can fail closed instead of storing plaintext (load failures are
# not cached; the next call retries the read).
_BIP39_VALID_COUNTS = (24, 21, 18, 15, 12)  # longest first: a sub-run of a longer phrase must not win
_BIP39_EDGE = ".,;:!?\"'`()[]{}<>–—"
_BIP39_INDEX: dict[str, int] | None = None  # cached ONLY on a successful, well-formed load


def _read_bip39_wordlist() -> list[str]:
    """Read the canonical wordlist next to the wallet module (the packaging seam). Raises on
    an absent/unreadable file; the CALLER verifies the canonical shape before trusting it."""
    from pathlib import Path

    return (Path(__file__).resolve().parent / "wallet" / "bip39_english.txt").read_text(encoding="utf-8").split()


def _bip39_index() -> dict[str, int]:
    global _BIP39_INDEX
    if _BIP39_INDEX is None:
        try:
            words = _read_bip39_wordlist()
            if len(words) != 2048 or len(set(words)) != 2048:
                raise ValueError("wordlist is not the canonical 2048-word BIP-39 list")
            _BIP39_INDEX = {w: i for i, w in enumerate(words)}
        except Exception:
            return {}  # unavailable: not cached, so a repaired install recovers on the next call
    return _BIP39_INDEX


def mnemonic_redaction_available() -> bool:
    """Whether recovery-phrase detection is armed (canonical wordlist present and well-formed).

    A sensitive-persistence caller that would store message text should refuse to persist when
    this is False — the rule cannot see a phrase, so 'redacted' would be a plaintext success."""
    return bool(_bip39_index())


def _bip39_checksum_ok(words: list[str]) -> bool:
    """The BIP-39 checksum over ALREADY-normalized wordlist words (11 bits per word)."""
    import hashlib

    index = _bip39_index()
    bits = "".join(bin(index[w])[2:].zfill(11) for w in words)
    checksum_bits = len(words) * 11 // 33
    entropy_bits = bits[:-checksum_bits]
    entropy = int(entropy_bits, 2).to_bytes(len(entropy_bits) // 8, "big")
    expected = bin(hashlib.sha256(entropy).digest()[0])[2:].zfill(8)[:checksum_bits]
    return bits[-checksum_bits:] == expected


def _redact_bip39_phrases(value: str) -> str:
    """Mask complete BIP-39 recovery phrases typed as plain word runs (checksum-verified).

    Longest run wins, edge punctuation is tolerated on the run's outer words, matching is
    NFKD+lowercase like the wallet's own validator, and the masked span covers the RAW tokens —
    so ``… somewhere: USED TERM … IDEA -- is that safe`` keeps its prose and loses exactly the
    phrase as typed. Tokens are normalized and membership-checked ONCE; the checksum runs only
    inside maximal all-wordlist runs, so ordinary prose pays one normalization per word.

    Fragment rule: OVERLAPPING checksum-valid windows can leave the tail of one phrase
    unmasked (eleven consecutive words of a 12-word phrase are brute-forceable through the
    checksum), so inside a run that produced a mask, any surviving consecutive wordlist span
    of nine or more words is masked too. Eight surviving words leave four unknown — 40+ bits —
    which is not recoverable. Runs with no checksum-valid window (wordlist-heavy prose) are
    left completely untouched; the rule never fires outside a run already proven to contain
    a phrase."""
    import unicodedata

    index = _bip39_index()
    if not value or not index:
        return value
    tokens = list(re.finditer(r"\S+", value))
    normalized: list[str] = []
    is_word: list[bool] = []
    seen: dict[str, str] = {}
    for match in tokens:
        raw = match.group(0).strip(_BIP39_EDGE)
        word = seen.get(raw)
        if word is None:
            word = unicodedata.normalize("NFKD", raw).lower()
            seen[raw] = word
        normalized.append(word)
        is_word.append(word in index)
    spans: list[tuple[int, int]] = []  # [start, end) token indexes to mask
    i = 0
    while i < len(tokens):
        if not is_word[i]:
            i += 1
            continue
        run_end = i
        while run_end < len(tokens) and is_word[run_end]:
            run_end += 1
        start = i
        run_masked = False
        while start < run_end:
            replaced = False
            for count in _BIP39_VALID_COUNTS:
                if start + count <= run_end and _bip39_checksum_ok(normalized[start : start + count]):
                    spans.append((start, start + count))
                    start += count
                    replaced = True
                    run_masked = True
                    break
            if not replaced:
                start += 1
        if run_masked:
            masked = [False] * (run_end - i)
            for s, e in spans:
                if s >= i:
                    for k in range(s - i, e - i):
                        masked[k] = True
            free = 0
            for k in range(run_end - i - 1, -1, -1):
                if masked[k]:
                    free = 0
                else:
                    free += 1
                    if free >= 9:
                        spans.append((i + k, i + k + 1))
        i = run_end
    if not spans:
        return value
    spans.sort()
    pieces: list[str] = []
    out_end = 0
    for s, e in spans:
        if tokens[s].start() < out_end:
            continue  # overlapped an already-masked span
        pieces.append(value[out_end : tokens[s].start()])
        pieces.append("[redacted-mnemonic]")
        out_end = tokens[e - 1].end()
    pieces.append(value[out_end:])
    return "".join(pieces)


def redact_url_path_tokens(text: str) -> str:
    """Mask only credentials carried in a URL path (see ``_PATH_TOKEN_RE``). Never raises.

    For call sites that serialize a URL on purpose (a diagnostic, a redirect ``Location``) and must
    keep the rest of the URL readable.
    """
    if not text:
        return text
    return _PATH_TOKEN_RE.sub(lambda match: f"{match.group(1)}[redacted-path-token]", str(text))

# Phrase-shaped secrets (recovery/seed/mnemonic phrases) are MULTI-WORD values: the
# single-token labelled rule above can only mask "label: <one-word>", so an explicitly
# recovery-phrase-framed 8/12/24-word phrase sailed through verbatim into embedding,
# VoolMemory and the FTS index (measured 2026-09-27: the mnemonic-like canary was
# stored and re-injected word-for-word). Only the LABEL authorises masking here — an
# unlabelled word list is never touched. The value must be WORDLIST-SHAPED: 4-24
# tokens separated by whitespace OR commas (both ordinary ways users paste a phrase),
# each token purely alphabetic and 3-8 characters (the BIP39/Electrum word shape),
# optionally wrapped in straight or curly quotes — so ordinary prose after the label
# keeps its existing behaviour whenever it contains a short function word ("is stored
# in the vault" — "in"), a longer word ("written on the whiteboard") or fewer than
# four such tokens ("my passphrase is fine"). Deliberate trade-off: a benign
# four-word run of short alpha words directly after an explicit phrase-secret label
# can be masked — under-masking here was the measured product failure, and the label
# requirement keeps prose without a labelled value untouched. The trailing
# (?![A-Za-z0-9'-]) guard keeps the value from ending MID-WORD inside a longer
# following token ("flute" inside "flute-something"): the value either ends at a
# real token boundary or the whole match is refused, never mangled.
_PHRASE_SECRET_LABEL = (
    r"recovery\s+phrase|recovery\s+seed|recovery\s+words|seed\s+phrase|seed\s+words"
    r"|backup\s+phrase|backup\s+seed|backup\s+words|mnemonic(?:\s+(?:phrase|words|seed))?"
    r"|passphrase|secret\s+phrase|secret\s+words"
)
_LABELED_PHRASE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9]+[_-])*(?:" + _PHRASE_SECRET_LABEL + r")(?:[_-][A-Za-z0-9]+)*)"
    r"\s*(?:[:=]|(?:\bis\b|\bare\b)\s*[:=]?)\s*[\u201c\u2018\"']?"
    r"([A-Za-z]{3,8}(?:(?:\s+|\s*,\s*)[A-Za-z]{3,8}){3,23})"
    r"[\u201d\u2019\"']?(?![A-Za-z0-9'\-])"
)

# Benign-public declaration. "mnemonic" alone is AMBIGUOUS: it names both wallet
# seed phrases and legitimate shared memory aids ("every good bird dances softly").
# An explicit publicity qualifier attached to the label noun ("our public mnemonic")
# resolves the ambiguity toward the memory-aid sense and preserves the value.
# Coherence rules, so one word can never launder a secret class:
#   * only the BARE "mnemonic" label is overridable — labels that name wallet
#     recovery material (recovery/seed/backup/secret/passphrase) keep their secret
#     classification no matter what qualifier precedes them;
#   * the qualifier must govern the label noun directly (same noun phrase, at most
#     two words between qualifier and label) — a stray "public" elsewhere in the
#     sentence proves nothing;
#   * the declaration must be AFFIRMATIVE: a negation, uncertainty hedge or
#     secrecy contradiction governing the qualifier ("not public", "non-public",
#     "maybe public", "public but private") withholds the exemption. The first
#     version matched the qualifier on a bare word boundary, which fires after
#     "not" and after a hyphen — measured (review N1/N2, 2026-09-27): both
#     "not public mnemonic" and "non-public mnemonic" leaked all eight words.
_PUBLIC_QUALIFIER_TAIL_RE = re.compile(
    r"\b(?:public|well[- ]known|famous)\s+(?:[A-Za-z]+\s+){0,2}$",
    re.IGNORECASE,
)
#: Negation governing a publicity qualifier, in the words of its own clause.
_PUBLICITY_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|none|nor|isn'?t|aren'?t|wasn'?t|weren'?t|don'?t|doesn'?t|"
    r"didn'?t|won'?t|wouldn'?t|can'?t|cannot|couldn'?t|shouldn'?t|hardly|barely|"
    r"scarcely|without)\b|n't\b",
    re.IGNORECASE,
)
#: Uncertainty/contradiction vocabulary that makes a declared publicity tentative.
_PUBLICITY_UNCERTAIN_RE = re.compile(
    r"\b(?:maybe|perhaps|possibly|probably|apparently|seemingly|arguably|somewhat|"
    r"kind\s+of|sort\s+of|allegedly|supposedly|reportedly|unclear|unsure)\b",
    re.IGNORECASE,
)
#: Secrecy contradiction between the qualifier and the label it governs.
_PUBLICITY_CONTRADICTION_RE = re.compile(
    r"\b(?:private|secret|sensitive|confidential|hidden)\b",
    re.IGNORECASE,
)
#: Clause boundaries delimiting how far back a governing negation can reach.
_CLAUSE_BOUNDARY_RE = re.compile(r"[.;:!?\u2014\n]")

#: Typography equivalence for the ANALYSIS copy only (same convention as
#: core/memory/admission.py's possessive normalization): apostrophe-family
#: characters fold to ASCII ' so "isn\u2019t" analyses as "isn't". Stored user
#: text is never rewritten — only the slices the publicity checks inspect.
_ANALYSIS_APOSTROPHES = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u02bc": "'", "\uff07": "'",
})


def _publicity_analysis_text(text: str) -> str:
    """Lower-cased, ASCII-apostrophe analysis copy of a publicity-declaration slice.

    Case and typography must never decide whether a NEGATIVE declaration is
    treated as affirmative (review C2/C3, 2026-09-27: 'NON-public' passed the
    case-sensitive hyphen guard and 'isn\u2019t public' missed the ASCII-only
    contraction negation — both leaked). Containment-only checks consume this;
    every match span stays in the original prefix, which is never rewritten.
    """
    return str(text or "").translate(_ANALYSIS_APOSTROPHES).lower()


def _label_declared_public(label: str, text: str, start: int) -> bool:
    """Whether an AFFIRMATIVE publicity declaration directly governs this labelled value."""
    if str(label or "").strip().lower() != "mnemonic":
        return False
    prefix = str(text or "")[: max(0, int(start))]
    match = _PUBLIC_QUALIFIER_TAIL_RE.search(prefix)
    if match is None:
        return False
    before = prefix[: match.start()]
    # The clause whose tail the qualifier completes: a governing negation or hedge
    # anywhere in it ("my not public", "not really famous", "definitely not
    # well-known") makes the declaration non-affirmative. A hyphenated negation
    # prefix ("non-public" — in ANY letter case) is the same government without
    # a space. All content checks run on the normalized analysis copy so case
    # and apostrophe typography cannot flip a negative to affirmative.
    clause = _CLAUSE_BOUNDARY_RE.split(before)[-1]
    clause_analysis = _publicity_analysis_text(clause)
    if _PUBLICITY_NEGATION_RE.search(clause_analysis) or _PUBLICITY_UNCERTAIN_RE.search(clause_analysis):
        return False
    if clause_analysis.rstrip().endswith(("non-", "not-", "never-", "un-")):
        return False
    # Contradiction between qualifier and label ("public but private mnemonic").
    # The qualifier span itself consumes up to two filler words, so the whole
    # stretch from the qualifier to the label is checked (analysis copy again;
    # the span bounds remain the original prefix's).
    return not _PUBLICITY_CONTRADICTION_RE.search(_publicity_analysis_text(prefix[match.start():]))


def _mask_secret_label(match: re.Match) -> str:
    if _label_declared_public(match.group(1), match.string, match.start()):
        return match.group(0)
    return f"{match.group(1)}: [redacted]"


def redact_secrets(text: str, *, personal: bool = True) -> str:
    """Return `text` with high-confidence secrets masked and everything else untouched. Never raises.

    ``personal=False`` leaves plain-word card numbers, PINs and passwords to a caller that masks
    them itself under its own rule names (the bug report scrubber) and then calls
    ``redact_personal_secrets`` as its own last pass.

    The exact guarantee: every pattern rule (keys, JWTs, private keys, bearer values, labelled
    secrets, registered exact values) always applies, but recovery-phrase masking applies ONLY
    when the canonical wordlist is loaded. A caller that persists or exports this output AS
    secret-free must refuse while ``mnemonic_redaction_available()`` is False — otherwise a
    phrase rides the output as "protected" plaintext (the routing decision log shows the
    fail-closed pattern)."""
    if not text:
        return text
    value = str(text)
    with _exact_lock:
        exact = sorted(_exact_secrets, key=len, reverse=True)
    for secret in exact:
        if secret in value:
            value = value.replace(secret, "[redacted-credential]")
    value = redact_url_path_tokens(value)
    value = _redact_private_keys(value)
    value = _redact_bip39_phrases(value)
    value = _JWT_RE.sub("[redacted-jwt]", value)
    value = _ZCASH_KEY_RE.sub("[redacted-zcash-key]", value)
    value = _API_KEY_RE.sub("[redacted-api-key]", value)
    value = _B58_SECRET_RE.sub(_mask_key_shaped, value)
    value = _WIF_RE.sub(_mask_key_shaped, value)
    value = _EVM_HEX64_RE.sub(_mask_key_shaped, value)
    value = _BEARER_RE.sub("Bearer [redacted]", value)
    # Card numbers, PINs and passwords written in plain words, before the labelled rules so
    # "password is hunter22" and "password: hunter22" end in the same mask.
    if personal:
        value = _redact_personal_secrets(value)
    # Multi-word phrase secrets before the single-token rule: a masked first word
    # ("ostrich" -> "[redacted]") must not truncate the phrase match mid-list.
    # Both labelled rules honour the public-mnemonic declaration in _mask_secret_label.
    value = _LABELED_PHRASE_RE.sub(_mask_secret_label, value)
    # Run labelled last so "api_key: sk-..." collapses cleanly even after the value was masked above.
    value = _LABELED_RE.sub(_mask_secret_label, value)
    return value


def contains_secret(text: str, *, personal: bool = True) -> bool:
    """True if `text` appears to contain a high-confidence secret (used to gate/flag, never to store).

    ``personal=False`` leaves plain-word card numbers, PINs and passwords out, for a caller that
    checks them itself and then asks ``contains_personal_secret`` last (the bug report scanner).

    True is detection; False is NOT proof of absence. When the canonical wordlist is
    unavailable, a recovery phrase is invisible to this predicate, so a gate that releases
    data out of the machine on False must also refuse while ``mnemonic_redaction_available()``
    is False."""
    if not text:
        return False
    value = str(text)
    with _exact_lock:
        if any(secret in value for secret in _exact_secrets):
            return True
    if next(_private_key_spans(value), None) is not None:
        return True
    if _redact_bip39_phrases(value) != value:
        return True
    for pattern in _SPECIFIC:
        for match in pattern.finditer(value):
            if pattern in (_B58_SECRET_RE, _WIF_RE, _EVM_HEX64_RE) and _is_public_identifier(match.group(0)):
                continue
            return True
    if personal and _redactable_personal_spans(value):
        return True
    for match in _LABELED_RE.finditer(value):
        if not _label_declared_public(match.group(1), value, match.start()):
            return True
    for match in _LABELED_PHRASE_RE.finditer(value):
        if not _label_declared_public(match.group(1), value, match.start()):
            return True
    return False


def redacted_copy(value):
    """A copy of a served value (text, or lists and dicts of it) with every secret masked.

    For read doors over rows written before a redaction rule existed: the stored bytes are
    never changed, the reader only ever receives the masked copy. Text with no secret is
    returned as the same object."""
    if isinstance(value, str):
        return redact_secrets(value) if contains_secret(value) else value
    if isinstance(value, dict):
        return {key: redacted_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redacted_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redacted_copy(item) for item in value)
    return value


__all__ = [
    "clear_exact_secrets_for_tests",
    "contains_personal_secret",
    "contains_secret",
    "luhn_valid",
    "mnemonic_redaction_available",
    "redact_personal_secrets",
    "redact_secrets",
    "redact_url_path_tokens",
    "redactable_personal_kinds",
    "redacted_copy",
    "register_exact_secret",
    "register_public_identifier",
]
