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


__all__ = ["REAL_FILE_EXTENSIONS", "is_real_file_target"]
