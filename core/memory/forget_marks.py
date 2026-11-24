"""The forget list keeps keyed fingerprints of the forgotten words, never the words.

The forget list (``.forget-authority/ledger.json``, ``memory_revocations`` and ``memory_erasure_operations`` in the
memory database) is read by every memory reader: a later text that carries forgotten words is withheld or refused.
It kept the words themselves, so after "Forget what I told you about Ilona's favourite tree." the words of the
forgotten fact were still on disk in plain text (Intel smoke of the v0.8 line: "forgotten 'silver birch' is gone from
every file under HOME" failed on those three stores). The owner's law: forget covers every store.

A forget list entry is now a *forget mark*: ``fk1:`` and a JSON record of keyed fingerprints (keyed BLAKE2b) of
exactly what each reader of the forget list checks, made from the words at the moment of the forget and never the
words themselves:

* ``n``: the forget matcher's needle (`core.memory.carriers.forget_needle_in`) in each spelling the matcher tries
  (as written, the two spelling folds, the input normalizer's re-spacing), with its length. A text carries the
  mark when one of its spans of that length that the matcher's word and number rules allow has the same
  fingerprint: the same spans the plain matcher would have found, by the same rules (`_own_word`, `_own_number`).
* ``c``: every span of the forgotten words themselves that the matcher allows, for the readers that ask whether
  the forgotten words hold a text whole (`core.fact_extractor`, a learned fact's source).
* ``u``: the recall subject (`core.memory_recall_intent._recall_subject`).
* ``q``: the content terms (`core.chat_housekeeping.query_terms`) the cross-chat recall reads.
* ``f``: each content word of four or more letters (`core.fact_extractor._folded_words`) with what `_same_word`
  compares: the word, the word less one letter, the word with two neighbouring letters swapped, its starts.
* ``w``: a subject withdrawal (`core.memory.withdrawals`): the withdrawal's subject and value words, their starts,
  and the noun phrase walk the temporal law reads (`core.temporal_selection._field_np`).

The key is derived from VOOL's node signing key (`network.signer.derive_local_secret`), which VOOL keeps in the
macOS Keychain where it can, never written beside the forget list itself. Each mark names its key (``k``); a mark
made under another key cannot be read and fails closed (`ForgetMarkKeyError`), as an unreadable forget list does.

A mark is made only of lowercase hex digits, digits, letters and JSON punctuation with no whitespace, hyphen or
underscore, so every normalization the readers apply to a forget token (case fold, separator folding, whitespace
joining) leaves it as it is.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import hashlib
import json
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PREFIX = "fk1:"
_KEY_FILE = "fingerprint.key"
_KEY_LOCK = threading.RLock()
#: Every forget list key this process has read, by its id; and each authority home's (id, key).
_KEYS: dict[str, bytes] = {}
_HOME_KEYS: dict[Path, tuple[str, bytes]] = {}
_ACTIVE: contextvars.ContextVar[bytes | None] = contextvars.ContextVar("vool_forget_mark_key", default=None)
_MARK_RE = re.compile(r'^fk1:\{[0-9a-z":,\[\]{}]*\}$')
_SEP_RUN = re.compile(r"[\s\-_]+")


class ForgetMarkKeyError(RuntimeError):
    """A forget mark that no key of this install can read: the forget list is unreadable, never empty."""


def _authority_home(runtime_home=None) -> Path:
    from core.runtime_paths import active_vool_home

    return Path(runtime_home).expanduser().resolve() if runtime_home else active_vool_home()


def _key_id_of(key: bytes) -> str:
    return hashlib.blake2b(b"forget-list-key", key=key, digest_size=4, person=b"vool-fk-keyid").hexdigest()


def _sync_directory(directory) -> None:
    """Make a new key file's name durable with its bytes."""
    with contextlib.suppress(OSError):
        fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def home_key(runtime_home=None, *, create: bool = False) -> tuple[str, bytes] | None:
    """This authority home's forget list key (id, key): 32 random bytes in ``.forget-authority/fingerprint.key``,
    owner-only, made when the home's forget authority opens. None when the home has none and ``create`` is false.

    The key belongs to the home, not to the process: any process that opens the home (the app after a restart, a
    helper, another home that depends on it) reads the same marks with it."""
    home = _authority_home(runtime_home)
    with _KEY_LOCK:
        known = _HOME_KEYS.get(home)
        if known is not None:
            return known
        path = home / ".forget-authority" / _KEY_FILE
        try:
            key = path.read_bytes()
        except FileNotFoundError:
            if not create:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                key = path.read_bytes()
            else:
                try:
                    os.write(fd, os.urandom(32))
                    os.fsync(fd)
                finally:
                    os.close(fd)
                _sync_directory(path.parent)
                key = path.read_bytes()
        if len(key) != 32:
            raise ForgetMarkKeyError("forget list key is damaged")
        known = (_key_id_of(key), key)
        _HOME_KEYS[home] = known
        _KEYS[known[0]] = key
        return known


def _key_for(key_id: str) -> bytes:
    key = _KEYS.get(key_id)
    if key is None:
        with contextlib.suppress(OSError):
            home_key(None)
        key = _KEYS.get(key_id)
    if key is None:
        raise ForgetMarkKeyError("forget list entry was made under a key this install does not hold")
    return key


@contextlib.contextmanager
def _using(key: bytes):
    token = _ACTIVE.set(key)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def _key() -> bytes:
    key = _ACTIVE.get()
    if key is None:
        raise ForgetMarkKeyError("no forget list key in use")
    return key


def _digest(domain: bytes, text: str, size: int = 8) -> str:
    return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), key=_key(), digest_size=size,
                           person=domain).hexdigest()


def _h(text: str) -> str:
    """A matcher span's fingerprint."""
    return _digest(b"vool-fk-span", text)


def _hc(text: str) -> str:
    """A contained span's fingerprint (shorter: there are many)."""
    return _digest(b"vool-fk-span", text)[:12]


def _hw(text: str) -> str:
    """A word's (or a word part's) fingerprint."""
    return _digest(b"vool-fk-word", text)


def is_forget_mark(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX) and _MARK_RE.match(value) is not None


# --------------------------------------------------------------------------- the matcher's spans


def _normal_map(text: str) -> tuple[str, list[int]]:
    """``text`` with each separator run as one space (the matcher's needle form), and where each character came from."""
    chars: list[str] = []
    where: list[int] = []
    last = 0
    for match in _SEP_RUN.finditer(text):
        for index in range(last, match.start()):
            chars.append(text[index])
            where.append(index)
        chars.append(" ")
        where.append(match.start())
        last = match.end()
    for index in range(last, len(text)):
        chars.append(text[index])
        where.append(index)
    return "".join(chars), where


def _span_allowed(text: str, start: int, end: int, span: str) -> bool:
    from core.memory.carriers import _own_number, _own_word

    if any(char.isdecimal() for char in span):
        return _own_number(text, start, end, span[0].isdecimal(), span[-1].isdecimal())
    if start > 0 and text[start - 1].isalnum() and text[start].isalnum():
        return False
    return _own_word(text, start, end)


#: How many characters of a needle its start fingerprint covers (`_TextIndex`).
_LEAD = 3


class _TextIndex:
    """One folded text, read once: its needle form, where each character came from, and where a needle may start.

    A needle of words starts where a word starts (`_span_allowed`); one with a digit may start anywhere (inside a
    run of letters, "B12" for "12"). Only those starts are fingerprinted, by their first `_LEAD` characters, once
    per text whatever the number of marks: a word start index, and an index of every place built when a needle
    with a digit is first looked for."""

    def __init__(self, text: str):
        self.text = text
        self.normal, self.where = _normal_map(text)
        normal = self.normal
        self.word_starts = [index for index, char in enumerate(normal) if char != " "
                            and not (index > 0 and normal[index - 1].isalnum() and char.isalnum())]
        self.all_starts = [index for index, char in enumerate(normal) if char != " "]
        self._leads: dict[bool, dict[str, list[int]]] = {}

    def _lead_index(self, numeric: bool) -> dict[str, list[int]]:
        leads = self._leads.get(numeric)
        if leads is None:
            leads = {}
            for index in (self.all_starts if numeric else self.word_starts):
                leads.setdefault(_h(self.normal[index:index + _LEAD]), []).append(index)
            self._leads[numeric] = leads
        return leads

    def found(self, length: int, lead: str, digest: str, numeric: bool) -> bool:
        """Whether a span of ``length`` (needle form) with fingerprint ``digest`` stands here by the matcher's rules."""
        normal, where, text = self.normal, self.where, self.text
        if length >= _LEAD:
            positions = self._lead_index(numeric).get(lead, ())
        else:
            positions = self.all_starts if numeric else self.word_starts
        for index in positions:
            last = index + length - 1
            if last >= len(normal) or normal[last] == " ":
                continue
            span = normal[index:last + 1]
            if _h(span) == digest and _span_allowed(text, where[index], where[last] + 1, span):
                return True
        return False


@functools.lru_cache(maxsize=4096)
def _text_index(text: str, key_id: str) -> _TextIndex:
    return _TextIndex(text)


def _all_spans(text: str) -> set[str]:
    """Every span of ``text`` (a folded text) that the matcher's rules let a needle of its characters match: every length,
    with `_own_word`'s ending rule read from running counts (the last
    whitespace before each place, the letters and digits up to it) instead of from the text again for each span."""
    from core.memory.carriers import _WORD_ENDING_MAY_GROW, _own_number

    normal, where = _normal_map(text)
    last_space = []
    seen = -1
    for index, char in enumerate(text):
        last_space.append(seen)
        if char.isspace():
            seen = index
    last_space.append(seen)
    alnum = [0]
    for char in text:
        alnum.append(alnum[-1] + char.isalnum())
    digits = [0]
    for char in normal:
        digits.append(digits[-1] + char.isdecimal())
    spans: set[str] = set()
    for first in range(len(normal)):
        if normal[first] == " ":
            continue
        start = where[first]
        word_start = not (start > 0 and text[start - 1].isalnum() and text[start].isalnum())
        for last in range(first, len(normal)):
            if normal[last] == " ":
                continue
            end = where[last] + 1
            span = normal[first:last + 1]
            if digits[last + 1] > digits[first]:
                allowed = _own_number(text, start, end, span[0].isdecimal(), span[-1].isdecimal())
            elif not word_start:
                continue
            elif end < len(text) and text[end].isalnum() and text[end - 1].isalnum():
                allowed = alnum[end] - alnum[last_space[end] + 1] >= _WORD_ENDING_MAY_GROW
            else:
                allowed = True
            if allowed:
                spans.add(span)
    return spans


def _text_forms(hay: object) -> dict[str, str | None]:
    """The text forms `forget_needle_in` compares, by the needle form each is compared with."""
    if isinstance(hay, str):
        return _string_forms(hay)
    return _forms_of(hay)


@functools.lru_cache(maxsize=4096)
def _string_forms(hay: str) -> dict[str, str | None]:
    return _forms_of(hay)


def _forms_of(hay: object) -> dict[str, str | None]:
    from core.memory.carriers import _INVISIBLE_BESIDE_SPACE_RE, _respaced_form, _spelling_forms, fold_forget_text

    text = fold_forget_text(hay)
    first, full = _spelling_forms(text)
    respaced = _respaced_form(text)
    invisible = not text.isascii() and _INVISIBLE_BESIDE_SPACE_RE.search(text) is not None
    return {"a": text, "b": first, "c": full, "r": respaced, "s": respaced if invisible else None}


def _needle_forms(needle: str) -> dict[str, str]:
    """The needle forms `forget_needle_in` compares, normalized as `_needle_pattern` normalizes them; "r" when the
    needle holds punctuation (its re-spaced form is always tried), else "s" (tried only beside an invisible
    character)."""
    from core.memory.carriers import _needle_has_punctuation, _respaced_form, _spelling_forms, normalized_forget_text

    first, full = _spelling_forms(needle)
    respaced = "r" if _needle_has_punctuation(needle) else "s"
    return {"a": normalized_forget_text(needle), "b": normalized_forget_text(first),
            "c": normalized_forget_text(full), respaced: normalized_forget_text(_respaced_form(needle))}


# --------------------------------------------------------------------------- word parts


def _word_record(word: str) -> list:
    """[length, fingerprint, less one letter, two neighbours swapped, starts of 3.. letters]."""
    less = sorted({_hw(word[:i] + word[i + 1:]) for i in range(len(word))})
    swapped = sorted({_hw(word[:i] + word[i + 1] + word[i] + word[i + 2:])
                      for i in range(len(word) - 1) if word[i] != word[i + 1]})
    starts = [_hw(word[:k]) for k in range(3, len(word) + 1)]
    return [len(word), _hw(word), less, swapped, starts]


def _kin_record(word: str) -> list:
    """`core.temporal_selection._token_kin`'s view of a word: its length without a final e, and its starts."""
    core = word.rstrip("e")
    return [len(core), [_hw(core[:k]) for k in range(4, len(core) + 1)]]


def _withdrawal_record(needle: str) -> dict | None:
    """What a subject withdrawal by ``needle`` compares, as `_withdrawn_in_chain` reads the control "Forget
    <needle>"; None when that control withdraws nothing."""
    from core import temporal_selection as ts

    control = "Forget " + needle
    if ts.retraction_marker(control, naming_the_saying=False) is None:
        return None
    subject = ts.slot_signature(control) - ts._retraction_act_words(control)
    matches = list(ts._NP_TOKEN_RE.finditer(control))
    walk = []
    for index, match in enumerate(matches):
        token = match.group(0).lower().replace("’", "'")
        owner = ts._possessor_of(token)
        flags = ((1 if token in ts._NP_OPENERS else 0) | (2 if token in ts._FIRST_PERSON_POSSESSIVES else 0)
                 | (4 if (token in ts._STOPWORDS or token in ts._NUMBER_WORDS or any(ch.isdigit() for ch in token)
                          or token in ts._TEMPORAL_WORDS or token in ts._UNIT_WORDS) else 0)
                 | (8 if (token in ts._FIELD_VERSION_WORDS or token in ts._REASSIGNMENT_TOKENS
                          or token in ts._LIGHT_VERB_TOKENS) else 0)
                 | (16 if index + 1 < len(matches)
                    and control[match.end():matches[index + 1].start()].strip() else 0))
        walk.append([_hw(ts._stem(token)), _hw(owner) if owner is not None else "", flags])
    return {
        "s": sorted({_hw(word) for word in subject}),
        "k": sorted(_kin_record(word) for word in subject),
        "v": sorted({_hw(value) for value in ts.value_tokens(control)}),
        "r": 1 if ts._RESTORATION_MARKER_RE.search(control) else 0,
        "g": sorted({_hw(word) for word in ts.slot_signature(control)}),
        "p": walk,
        "m": sorted({_hw(word) for word in ts._mentions(control)}),
    }


# --------------------------------------------------------------------------- making a mark


def forget_mark(token: object, runtime_home=None) -> str:
    """The forget list entry for ``token`` in the forget authority of ``runtime_home`` (the active home by default):
    its fingerprints under that home's key, never its words; "" for no words. A mark is its own mark."""
    raw = str(token or "")
    if is_forget_mark(raw):
        return raw
    from core.memory.carriers import normalized_forget_text

    needle = normalized_forget_text(raw)
    if not needle:
        return ""
    key_id, key = home_key(runtime_home, create=True)
    with _using(key):
        record = _mark_record(needle)
    record["k"] = key_id
    return PREFIX + json.dumps(record, sort_keys=True, separators=(",", ":"))


def _mark_record(needle: str) -> dict:
    from core.chat_housekeeping import query_terms
    from core.fact_extractor import _folded_words
    from core.memory_recall_intent import _recall_subject

    record: dict = {}
    record["n"] = sorted({(kind, len(form), _h(form[:_LEAD]), _h(form), int(any(ch.isdecimal() for ch in form)))
                          for kind, form in _needle_forms(needle).items() if form})
    forms = _text_forms(needle)
    contained: dict[str, list[str]] = {}
    for kind in ("a", "b", "c", "r"):
        form = forms[kind]
        if form and (kind == "a" or form != forms["a"]):
            contained[kind] = sorted({_hc(span) for span in _all_spans(form)})
    record["c"] = contained
    record["i"] = 1 if forms["s"] is not None else 0
    subject = _recall_subject(needle)
    record["u"] = [_hw(" ".join(subject.subject) + "|" + " ".join(subject.relation))] if subject else []
    record["q"] = sorted({_hw(term) for term in query_terms(needle) if term not in {"the", "my", "a", "an"}})
    record["f"] = sorted(_word_record(word) for word in _folded_words(needle) if len(word) >= 4)
    withdrawal = _withdrawal_record(needle)
    if withdrawal is not None:
        record["w"] = withdrawal
    return record


@dataclass(frozen=True)
class _Mark:
    needles: tuple[tuple[str, int, str, str, bool], ...]
    contained: dict
    invisible: bool
    subjects: frozenset[str]
    terms: frozenset[str]
    words: tuple[tuple, ...]
    withdrawal: dict | None
    key_id: str
    key: bytes


@functools.lru_cache(maxsize=4096)
def _read_mark(mark: str) -> _Mark:
    try:
        record = json.loads(mark[len(PREFIX):])
    except ValueError as error:
        raise ForgetMarkKeyError("forget list entry is unreadable") from error
    if not isinstance(record, dict) or not isinstance(record.get("k"), str):
        raise ForgetMarkKeyError("forget list entry is unreadable")
    key = _key_for(record["k"])
    withdrawal = record.get("w")
    if withdrawal is not None:
        withdrawal = {**withdrawal, "s": frozenset(withdrawal["s"]), "v": frozenset(withdrawal["v"]),
                      "g": frozenset(withdrawal["g"]), "m": frozenset(withdrawal["m"])}
    return _Mark(
        needles=tuple((kind, int(length), lead, digest, bool(numeric))
                      for kind, length, lead, digest, numeric in record["n"]),
        contained={kind: frozenset(values) for kind, values in record["c"].items()},
        invisible=bool(record.get("i")),
        subjects=frozenset(record.get("u") or ()),
        terms=frozenset(record.get("q") or ()),
        words=tuple((int(w[0]), w[1], frozenset(w[2]), frozenset(w[3]), tuple(w[4])) for w in record.get("f") or ()),
        withdrawal=withdrawal,
        key_id=record["k"],
        key=key,
    )


# --------------------------------------------------------------------------- reading a mark


def mark_in_text(hay: object, mark: str) -> bool:
    """`forget_needle_in(hay, needle)` for the needle ``mark`` was made from."""
    parsed = _read_mark(mark)
    forms = _text_forms(str(hay) if isinstance(hay, str) else hay)
    key_id = parsed.key_id
    tried: set[tuple[str, int, str]] = set()
    for kind, length, lead, digest, numeric in parsed.needles:
        text = forms.get(kind)
        if not text or (text, length, digest) in tried:
            continue
        tried.add((text, length, digest))
        if _text_index(text, key_id).found(length, lead, digest, numeric):
            return True
    return False


def text_in_mark(mark: str, needle: object) -> bool:
    """`forget_needle_in(words, needle)` for the forgotten words ``mark`` was made from."""
    parsed = _read_mark(mark)
    text = str(needle or "")
    if not text:
        return False
    forms = _needle_forms(text)
    contained = parsed.contained
    for kind in ("a", "b", "c"):
        form = forms[kind]
        if form and _hc(form) in contained.get(kind, contained["a"]):
            return True
    respaced = forms.get("r") or (forms.get("s") if parsed.invisible else None)
    return bool(respaced) and _hc(respaced) in contained.get("r", contained["a"])


def recall_subject_in_mark(mark: str, subject: tuple[str, ...], relation: tuple[str, ...]) -> bool:
    """Whether the recall subject of the forgotten words is ``subject`` (with no relation, or with ``relation``),
    as `revoked_memory_recall_clauses` compares them."""
    subjects = _read_mark(mark).subjects
    name = " ".join(subject)
    return _hw(name + "|") in subjects or _hw(name + "|" + " ".join(relation)) in subjects


def terms_of_mark_in(mark: str, terms: Iterable[str]) -> bool:
    """Whether every content term of the forgotten words (less "the", "my", "a", "an") is among ``terms``."""
    wanted = _read_mark(mark).terms
    return bool(wanted) and wanted <= {_hw(term) for term in terms}


def _same_word(record: tuple, other: str) -> bool:
    """`core.fact_extractor._same_word` of a forgotten word (``record``) and ``other``."""
    length, digest, less, swapped, starts = record
    other_digest = _hw(other)
    if other_digest == digest:
        return True
    size = len(other)
    if min(length, size) >= 4:
        if size == length and other_digest in swapped:
            return True
        if size == length - 1 and other_digest in less:
            return True
        if size == length + 1 and any(_hw(other[:i] + other[i + 1:]) == digest for i in range(size)):
            return True
    shorter = min(length, size)
    if shorter < 5 or abs(length - size) > 3:
        return False
    return _hw(other[:shorter - 2]) == starts[shorter - 2 - 3]


def mark_words_named(mark: str, still_held: set[str], asked: set[str]) -> bool:
    """`_judged_forgotten_named` for one forgotten token: one of its own words (four or more letters, the same word as
    none of ``still_held``) is the same word as one of ``asked``."""
    own = [record for record in _read_mark(mark).words
           if not any(_same_word(record, other) for other in still_held)]
    return any(_same_word(record, other) for record in own for other in asked)


def _kin(word: str, record: list) -> bool:
    length, starts = record
    core = word.rstrip("e")
    shared = min(len(core), length)
    return shared >= 4 and _hw(core[:shared]) == starts[shared - 4]


def _field_np_of_mark(walk: list, shared: frozenset[str]):
    """`core.temporal_selection._field_np` of the withdrawal's control, on its fingerprints."""
    start = next((index for index, (stem, _, _) in enumerate(walk) if stem in shared), None)
    if start is None:
        return None
    modifiers: set[str] = set()
    possessor = ""
    first_person = False
    index = start - 1
    while index >= 0:
        stem, owner, flags = walk[index]
        if flags & 16:
            break  # punctuation ends the phrase
        if owner:
            if not possessor and not modifiers:
                possessor = owner
            else:
                break
            index -= 1
            continue
        if flags & 1:
            first_person = not possessor and bool(flags & 2)
            break
        if flags & 4:
            break
        if stem not in shared and not flags & 8:
            modifiers.add(stem)
        index -= 1
    return frozenset(modifiers), possessor, first_person


def withdrawn_by_mark(body: str, mark: str) -> bool:
    """`_withdrawn_in_chain` of a source ``body`` and the withdrawal control the forgotten words made."""
    from core import temporal_selection as ts

    control = _read_mark(mark).withdrawal
    if control is None:
        return False
    lo_sig = ts.slot_signature(body)
    lo_values = ts.value_tokens(body)
    value_digests = {_hw(value) for value in lo_values}
    if control["r"] and lo_values and value_digests <= control["v"]:
        return False
    tied = (any(_hw(word) in control["s"] for word in lo_sig)
            or any(_kin(word, record) for word in lo_sig for record in control["k"])
            or bool(value_digests & control["v"]))
    if not tied:
        return False
    return not _names_another_field(body, lo_sig, control)


def _names_another_field(body: str, lo_sig: frozenset[str], control: dict) -> bool:
    from core import temporal_selection as ts

    shared = frozenset(
        tok for tok in lo_sig
        if _hw(tok) in control["g"] and tok not in ts._REASSIGNMENT_TOKENS and tok not in ts._LIGHT_VERB_TOKENS
        and tok not in ts._FIELD_VERSION_WORDS and not any(ch.isdigit() for ch in tok))
    if not shared:
        return False
    loser_np = ts._field_np(body, shared)
    winner_np = _field_np_of_mark(control["p"], frozenset(_hw(word) for word in shared))
    if loser_np is None or winner_np is None:
        return False
    lo_mods, lo_owner, lo_mine = loser_np
    wi_mods, wi_owner, wi_mine = winner_np
    loser_words = {_hw(word) for word in ts._mentions(body)}
    if lo_owner and wi_mine and _hw(lo_owner) not in control["m"]:
        return True
    if wi_owner and lo_mine and wi_owner not in loser_words:
        return True
    contrastive = {_hw(word) for word in ts._CONTRASTIVE_FIELD_MODIFIERS}
    return bool({mod for mod in lo_mods & ts._CONTRASTIVE_FIELD_MODIFIERS if _hw(mod) not in control["m"]}
                or (wi_mods & contrastive) - loser_words)


def _keyed(position: int):
    """Run a reader of one mark under the key the mark was made with."""
    def wrap(fn):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            with _using(_read_mark(args[position]).key):
                return fn(*args, **kwargs)
        return run
    return wrap


mark_in_text = _keyed(1)(mark_in_text)
text_in_mark = _keyed(0)(text_in_mark)
recall_subject_in_mark = _keyed(0)(recall_subject_in_mark)
terms_of_mark_in = _keyed(0)(terms_of_mark_in)
mark_words_named = _keyed(0)(mark_words_named)
withdrawn_by_mark = _keyed(1)(withdrawn_by_mark)

__all__ = [
    "PREFIX",
    "ForgetMarkKeyError",
    "forget_mark",
    "is_forget_mark",
    "mark_in_text",
    "mark_words_named",
    "recall_subject_in_mark",
    "terms_of_mark_in",
    "text_in_mark",
    "withdrawn_by_mark",
]
