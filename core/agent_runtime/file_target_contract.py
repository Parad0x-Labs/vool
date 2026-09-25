"""What counts as a real file target — one list, so every extractor agrees.

A dotted token is not a path. `workspace.write_file`, `provider.foo_bar`, `blob.startswith` and
`Path.resolve` all have the shape of `name.extension` and none of them is a file, but three separate
extractors in this runtime each decided that question for themselves and two of them got it wrong:

* 2026-08-01: an audit report quoting `blob.startswith` was parsed as a claimed file `blob.starts`,
  no execution record existed for that "file", and the entire report was replaced with "I did not
  actually open `blob.starts`". Fixed by adding an extension allowlist — in one extractor.
* 2026-08-07: "Trace one workspace.write_file request from tool schema through the rollback ledger"
  had `workspace.write` extracted as its audit target, because `audit_target_in` had only a
  five-entry BLOCKlist (`e`, `g`, `i`, `etc`, `vs`) and everything else was a file to it.

The second is the first, one module over, six days later. An allowlist that lives inside one
extractor is a fix that does not travel, so the list lives here and the extractors import it.

Allowlist rather than blocklist, deliberately: the space of dotted code identifiers is unbounded and
the space of file extensions this product actually reads is not. An unknown extension is ambiguity,
and every consumer of this module resolves ambiguity by declining to treat the token as a target.
"""
from __future__ import annotations

import re

# Extensions a file in a real workspace actually has. Kept as one flat set rather than grouped by
# language: every consumer asks the same yes/no question and a grouping would only invite a caller
# to consult one group and miss another.
REAL_FILE_EXTENSIONS = frozenset(
    {
        "py", "pyi", "js", "jsx", "ts", "tsx", "mjs", "cjs", "json", "jsonl", "yaml", "yml",
        "toml", "ini", "cfg", "conf", "env", "md", "rst", "txt", "log", "csv", "tsv", "html",
        "htm", "css", "scss", "xml", "svg", "sh", "bash", "zsh", "ps1", "bat", "cmd", "rs",
        "go", "java", "kt", "c", "h", "cpp", "hpp", "cc", "cs", "rb", "php", "swift", "m",
        "sql", "db", "lock", "pdf", "docx", "xlsx", "pptx", "zip", "tar", "gz", "plist",
        "iss", "vbs", "wasm", "proto", "ipynb",
    }
)


def is_real_file_target(candidate: str) -> bool:
    """Whether ``candidate`` is a path this runtime should treat as naming a file.

    The stem must be non-empty (so a bare ``.py`` is not a target) and the extension must be one a
    file actually carries. Case-insensitive on the extension only — paths are case-sensitive on the
    platforms that matter and this function does not normalize them.
    """
    text = str(candidate or "").strip()
    if not text:
        return False
    stem, dot, extension = text.rpartition(".")
    if not dot or not stem:
        return False
    return extension.lower() in REAL_FILE_EXTENSIONS


# The historical candidate extractor both honesty gates used:
#     [`'\"]?((?:[\w\-.]+/)*[\w\-]+\.[A-Za-z][A-Za-z0-9]{0,5})[`'\"]?
# That pattern backtracks through every suffix of a long dotless run (and every slash count of a
# path-shaped run) at every start position, which is quadratic on adversarial prose. The scanner
# below yields exactly the strings the pattern's capture group yielded, in the same order, in one
# linear pass. Two structural facts make each attempt O(1) after a per-token precompute: a stem
# character is never '.', so the stem never usefully backtracks; and the segment chain is fixed --
# each segment is the run before a slash, an empty segment (a double slash) ends the chain, and the
# greedy star prefers the LARGEST chain slash whose following stem parses before falling back to
# the attempt position itself.
_QUOTE_CHARS = "`'\""
_TOKEN_SPAN_RE = re.compile(r"[\w\-./]+")
_CHAR_TOKEN_RE = re.compile(r"[\w\-.]")
_CHAR_STEM_RE = re.compile(r"[\w\-]")
_CHAR_EXT_RE = re.compile(r"[A-Za-z0-9]")
_CHAR_LETTER_RE = re.compile(r"[A-Za-z]")


def iter_claimed_file_candidates(text: str):
    """Yield each candidate path token the historical honesty-gate pattern captured.

    Byte-for-byte the same candidates, order and resume positions as the pattern above, without
    its quadratic rescans: absolute paths keep their leading slash outside the candidate,
    multi-dot names keep their legacy cut (``archive.tar.gz`` yields ``archive.tar``), and a
    long extension is cut after six characters exactly as ``[A-Za-z][A-Za-z0-9]{0,5}`` did.
    """
    raw = str(text or "")
    n = len(raw)
    i = 0
    token_lo = token_hi = -1
    parses: list[bool] = []
    parse_ends: list[int] = []
    latest_parsing_slash: list[int] = []
    chain_end: list[int] = []
    while i < n:
        start = i + 1 if raw[i] in _QUOTE_CHARS else i
        if start >= n or _CHAR_TOKEN_RE.fullmatch(raw[start]) is None:
            i += 1
            continue
        if not token_lo <= start < token_hi:
            token_lo = start
            span = _TOKEN_SPAN_RE.match(raw, start)
            token_hi = span.end() if span else start + 1
            length = token_hi - token_lo
            # Stem-run lengths: a stem is [\w\-]+, so runs stop at '.' and '/' inside the token.
            run: list[int] = [0] * (length + 1)
            for offset in range(length - 1, -1, -1):
                run[offset] = (
                    run[offset + 1] + 1
                    if _CHAR_STEM_RE.fullmatch(raw[token_lo + offset]) is not None
                    else 0
                )
            # parses[offset]: a stem + '.' + letter + up to five alnum starts at this position.
            parses = [False] * length
            parse_ends = [-1] * length
            for offset in range(length):
                if run[offset] < 1:
                    continue
                dot_at = token_lo + offset + run[offset]
                if (
                    dot_at < token_hi
                    and raw[dot_at] == "."
                    and dot_at + 1 < token_hi
                    and _CHAR_LETTER_RE.fullmatch(raw[dot_at + 1]) is not None
                ):
                    end = dot_at + 2
                    limit = min(dot_at + 7, token_hi)
                    while end < limit and _CHAR_EXT_RE.fullmatch(raw[end]) is not None:
                        end += 1
                    parses[offset] = True
                    parse_ends[offset] = end
            # latest_parsing_slash[offset]: the largest slash at or before it whose following
            # position parses (running maximum, left to right).
            latest_parsing_slash = [-1] * length
            for offset in range(length):
                latest_parsing_slash[offset] = (
                    offset
                    if raw[token_lo + offset] == "/" and offset + 1 < length and parses[offset + 1]
                    else (latest_parsing_slash[offset - 1] if offset else -1)
                )
            # chain_end[offset]: the last slash the greedy segment chain may consume from this
            # position -- the first double-slash leader ends the chain (an empty segment stops
            # it), otherwise the token's last slash. -1 when no slash is reachable.
            chain_end = [-1] * length
            last_slash = -1
            for offset in range(length):
                if raw[token_lo + offset] == "/":
                    last_slash = offset
            first_double_ahead = -1
            for offset in range(length - 2, -1, -1):
                is_double = (
                    raw[token_lo + offset] == "/" and raw[token_lo + offset + 1] == "/"
                )
                if is_double:
                    first_double_ahead = offset
                chain_end[offset] = first_double_ahead if first_double_ahead >= 0 else last_slash
        offset = start - token_lo
        winner = -1
        if raw[start] != "/":
            limit = chain_end[offset]
            if limit >= 0 and limit > offset:
                slash = latest_parsing_slash[limit]
                if slash > offset:
                    winner = slash + 1
            if winner < 0 and parses[offset]:
                winner = offset
        if winner < 0:
            i += 1
            continue
        end = parse_ends[winner]
        yield raw[start:end]
        i = end + 1 if end < n and raw[end] in _QUOTE_CHARS else end


__all__ = ["REAL_FILE_EXTENSIONS", "is_real_file_target", "iter_claimed_file_candidates"]
