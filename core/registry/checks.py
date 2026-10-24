"""Automatic Skill Codex checks: every byte of every file, failing closed.

The Mac authority runs these on the exact uploaded bytes before it signs anything. The
server runs the same code first so a creator hears about a problem at once. A finding
names the file and line and says what to change; a secret is never quoted back.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from core.registry.artifact import REVIEW_METADATA, metadata_text, review_files
from core.registry.codec import RegistryError, canonical, digest

SUITE = "skill-codex-checks"
SUITE_MAJOR = 1
# Fixed per major version so rule refinements do not need a new trust root.
CONFIG_SHA256 = digest({"suite": SUITE, "major": SUITE_MAJOR})
PUBLICATION_SERVICE = "skill-codex"
DETERMINISTIC = ("source", "archive", "secrets", "code", "dependencies", "license",
                 "instructions-primary", "native-boundaries", "compatibility", "quality")

TEXT_SUFFIXES = (".md", ".txt")
# Line breaks, tabs, joiners some scripts need, and direction marks; never bidi overrides or zero-width spaces.
_ALLOWED_MARKS = {"\n", "\t", "\r", "‌", "‍", "‎", "‏"}
# Letters that show as nothing, refused like control characters (variation selectors 1-14 too; 15-16 pick emoji style).
_INVISIBLE = {"͏", "ᅟ", "ᅠ", "឴", "឵", "⠀", "ㅤ", "ﾠ",
              *(chr(code) for code in range(0xFE00, 0xFE0E)), *(chr(code) for code in range(0xE0100, 0xE01F0))}
MIN_INSTRUCTIONS = 80
# Said when the checks fail in a way nobody planned for; the upload is refused, never passed.
UNREADABLE = "Our checks couldn't read this skill. Make it plainer, then upload it again."
QUOTE = 120
# The review model reads at most this much, so nothing longer can pass; refusing early also bounds the work.
MAX_TEXT = 120_000

_MIT = r"""MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
# The whole licence is the MIT text; only the copyright lines are the creator's.
_MIT_BODY = " ".join(_MIT.split("\n\n", 2)[2].casefold().split())
_MIT_TITLE = re.compile(r"(?:the )?mit licen[cs]e(?: \(mit\))? ")
# Words that read as the Skill Codex vouching for a skill. Only the team's own skills may use them.
_RESERVED = re.compile(r"\b(?:verified|official|certified|endorsed|tested by|parad0x|paradox labs|vool team|"
                       r"vool-team|safe to use|security checked)\b", re.IGNORECASE)
_ENDORSED = re.compile(r"\b(?:verified|approved|certified|endorsed|tested|checked|reviewed)\s+by\s+(?:vool|the skill codex|"
                       r"parad0x|paradox)\b|\b(?:official|verified)\s+(?:vool|skill codex)\b|\bparad0x\s+labs\b|"
                       r"\bvool[- ]team\b", re.IGNORECASE)
_SHELL = r"(?:/(?:usr/)?(?:local/)?bin/)?(?:env\s+)?(?:ba|z|da|k|fi)?sh"
# Rules run on folded text: NFKC, invisible marks and combining marks taken out, look-alike letters made Latin, and
# every line joined with a space, so nothing hides behind a line break, a continuation or a different alphabet.
_RULES = (
    ("install", re.compile(
        r"\b(?:curl|wget)\s+(?:-|['\"`]?https?://)|\b(?:iwr|irm|invoke-webrequest|invoke-restmethod|certutil|bitsadmin|"
        r"mshta|rundll32|regsvr32|iex|invoke-expression)\b|\|\s*(?:sudo\s+)?" + _SHELL + r"\b|"
        r"\b(?:ba|z|da|k|fi)?sh\s+(?:-[a-z]*c\b|\S{1,200}\.(?:sh|command)\b|<\()|"
        r"(?:^|[\s`'\"])/(?:usr/)?(?:local/)?(?:s?bin)/\S|(?:^|[\s`'\";&|])\./\S|"
        r"\b(?:python[0-9.]*|py|node|nodejs|perl|ruby|php|deno|bun|lua|rscript|osascript)\s+"
        r"(?:-[a-z]*[cer]\b|run\b|\S{1,200}\.(?:py|js|mjs|cjs|ts|pl|rb|php|lua|r|scpt|applescript)\b)|"
        r"\b(?:npx|bunx|uvx|pipx\s+run)\s+\S|powershell(?:\.exe)?\s+-|\bpwsh\s+-|"
        r"\b(?:pip[0-9.]*|pipx|uv|conda|poetry|mamba)\s+(?:install|add)\b|\bnpm\s+(?:install|i|exec|run|x)\b|"
        r"\byarn\s+(?:global\s+)?(?:add|dlx)\b|\bpnpm\s+(?:add|install|i|dlx)\b|\bbrew\s+(?:install|tap|reinstall)\b|"
        r"\b(?:apt|apt-get|dnf|yum|pacman|apk|zypper|snap|flatpak|port|choco|winget|scoop|nix-env)\s+(?:install|add|-S)\b|"
        r"\bcargo\s+install\b|\bgo\s+install\b|\bgem\s+install\b|\bchmod\b|\bxattr\s+-[a-z]*[dc]\b|"
        r"\bspctl\b|\bsudo\s+\S|\bosascript\b|\blaunchctl\s+(?:load|bootstrap|submit|enable)\b|\bcrontab\b|"
        r"\bsource\s+\S{1,200}\.sh\b|\bdocker\s+(?:run|exec|pull)\b|\bgit\s+clone\b", re.IGNORECASE),
        "Skills on the Skill Codex can't ask anyone to install or run software. Describe the task in words instead."),
    ("download", re.compile(
        r"https?://\S{1,500}?\.(?:exe|msi|msix|appx|dmg|pkg|app|appimage|run|zip|rar|7z|tar|gz|tgz|bz2|xz|zst|sh|bash|"
        r"zsh|command|ps1|psm1|bat|cmd|vbs|wsf|hta|lnk|reg|scpt|workflow|js|mjs|py|jar|apk|ipa|deb|rpm|bin|scr|iso|img|"
        r"crx|xpi|vsix|whl|egg|nupkg|snap|torrent)(?:[?#]\S{0,500})?(?=[\s)\]>\"'`]|$)", re.IGNORECASE),
        "Skills can't link to programs or archives to download. Take the link out."),
    ("address", re.compile(
        r"https?://(?:\d{1,3}\.){3}\d{1,3}\b|https?://\[[0-9a-f:]+\]|https?://[^\s/]*@|"
        r"https?://(?:www\.)?(?:bit\.ly|tinyurl\.com|t\.co|is\.gd|cutt\.ly|rb\.gy|goo\.gl|ow\.ly|"
        r"pastebin\.com|paste\.ee|hastebin\.com|glot\.io|rentry\.co|ghostbin\.\w+)/", re.IGNORECASE),
        "Skills can't use short links, raw addresses or paste sites. Link to the real page, or take the link out."),
    ("secret-ask", re.compile(
        r"\b(?:send|paste|enter|share|provide|type(?!\s+of\b)|give|upload|reveal|tell|input|submit|copy|dm)\b[^.!?\n]{0,60}?"
        r"\b(?:(?:recovery|seed|secret|backup|wallet)\s+(?:phrase|words|key)|mnemonic|private\s+keys?|keystore|"
        r"wallet\s+file|password|passcode|passphrase|api\s+keys?|access\s+tokens?|session\s+(?:token|cookie)s?|"
        r"2fa\s+codes?|one-time\s+codes?|verification\s+codes?|security\s+codes?|pin\s+(?:code|number)s?|ssh\s+keys?)",
        re.IGNORECASE),
        "Skills can't ask for recovery phrases, keys, passwords or codes. Take that out."),
    ("wallet", re.compile(r"\b0x[0-9a-fA-F]{40}\b|\b(?:bc1|tb1)[02-9ac-hj-np-z]{11,71}\b|"
                          r"(?<![A-Za-z0-9/])(?=[1-9A-HJ-NP-Za-km-z]*\d)(?=[1-9A-HJ-NP-Za-km-z]*[A-HJ-NP-Z])"
                          r"(?=[1-9A-HJ-NP-Za-km-z]*[a-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])"),
     "Skills can't contain wallet addresses. Take the address out."),
    ("injection", re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b[^.!?\n]{0,30}?\b(?:all|any|previous|prior|earlier|above|"
        r"other|your|system|safety)\b[^.!?\n]{0,20}?\b(?:instructions|rules|messages|prompts?|guidelines|limits|"
        r"approvals?)\b|"
        r"\bsystem prompt\b|\bdeveloper mode\b|\bjailbreak\b|\byou are now\b|"
        r"\b(?:do not|don't) (?:tell|inform|show|let|alert|notify) (?:the )?(?:user|owner|person)\b|"
        r"\bwithout (?:telling|asking|informing|showing|notifying|alerting) (?:the )?(?:user|owner|person)\b|"
        r"\b(?:secretly|silently|quietly|covertly|invisibly)\b[^.!?\n]{0,40}?\b(?:send|upload|forward|copy|transfer|pay|run|"
        r"delete|post|share|email)\b", re.IGNORECASE),
        "This reads like an attempt to change VOOL's instructions or hide what the skill does. Rewrite it plainly."),
    ("hidden", re.compile(r"<!--|\[[^\]]{0,100}\]:\s*(?:#|<>)|data:[a-z]+/[a-z0-9.+-]+;base64,|"
                          r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{200,}={0,2}|(?:[A-Za-z0-9+/]{40,}={0,2}\s+){3,})|"
                          r"\\u[0-9a-f]{4}|\\x[0-9a-f]{2}|"
                          r"&#x?[0-9a-f]{2,6};", re.IGNORECASE),
     "Skills can't carry hidden comments, escape codes or encoded blobs. Write everything as plain visible text."),
    ("letters", re.compile(r"(?<!\w)(?=\w*[A-Za-z])(?=\w*[Ͱ-ϿЀ-ԯ])\w+"),
     "This word mixes letters from different alphabets, which can hide what it says. Retype it in one alphabet."),
)
# Look-alike letters from other alphabets, read as the Latin letter they imitate.
_LOOKALIKE = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y",
    "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ӏ": "l", "ԛ": "q", "ԝ": "w", "ү": "y", "һ": "h",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "І": "I", "Ј": "J", "Ѕ": "S", "Ү": "Y", "α": "a", "ο": "o", "ρ": "p", "ν": "v", "ι": "i", "κ": "k", "τ": "t",
    "υ": "u", "ϲ": "c", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X"})


def _finding(check: str, path: str, reason: str, *, line: int | None = None, quote: str | None = None,
             rule: str | None = None) -> dict:
    """One problem. `rule` is a stable code for the reason, so a page can show it in the reader's language."""
    item = {"check": check, "path": path, "reason": reason}
    if rule is not None:
        item["rule"] = rule
    if line is not None:
        item["line"] = line
    if quote is not None and not _looks_secret(quote):  # the finding is stored and shown
        item["quote"] = quote[:QUOTE]
    return item


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _quote(text: str, start: int, end: int) -> str:
    begin = text.rfind("\n", 0, start) + 1
    stop = text.find("\n", end)
    return " ".join(text[begin:len(text) if stop < 0 else stop].split())


def _picture_paths(artifact: dict) -> set[str]:
    """Files the metadata names as pictures; inspect() has already checked their bytes."""
    return {artifact["metadata"][kind]["path"] for kind in artifact["pictures"]}


def _texts(artifact: dict) -> tuple[dict[str, str], list[dict]]:
    """Every file other than the declared pictures must be UTF-8 text without control characters."""
    pictures = _picture_paths(artifact)
    texts, findings = {}, []
    for name, raw in review_files(artifact).items():
        if name in pictures:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(_finding("code", name, "This file isn't plain text. Skills hold text, the licence and "
                                                  "their pictures only."))
            continue
        hidden = next((index for index, char in enumerate(text)
                       if (unicodedata.category(char) in {"Cc", "Cf", "Co", "Cs"} or char in _INVISIBLE) and
                       char not in _ALLOWED_MARKS and not (index == 0 and char == "\ufeff")), None)
        if hidden is not None:
            findings.append(_finding("instructions-primary", name, "This file has invisible or control characters. "
                                     "Retype the line as plain text.", line=_line(text, hidden), rule="invisible"))
        texts[name] = text
    return texts, findings


def _code(artifact: dict) -> list[dict]:
    from core.skill_package import _RUNNABLE_MANIFEST_KEYS, _looks_runnable

    manifest = artifact["manifest"]
    if artifact["metadata"]["kind"] == "plugin" and isinstance(manifest.get("vool"), dict) and "id" in manifest["vool"]:
        from core.plugin_tools import PluginManifestError, parse_manifest

        try:
            plugin = parse_manifest(manifest, path=Path(".codex-plugin/plugin.json"))
            if not plugin.vool:
                raise ValueError("A store plugin needs the current access declarations.")
        except (PluginManifestError, ValueError, TypeError, KeyError) as exc:
            return [_finding("code", ".codex-plugin/plugin.json", str(exc))]
        # Executable source belongs to a plugin's complete reviewed distribution.
        # _texts still refuses binary/hidden content; all source proceeds through
        # the same secret, instruction, native-boundary and independent reviews.
        return []

    findings = []
    pictures = _picture_paths(artifact)
    allowed = {".codex-plugin/plugin.json", "LICENSE", "registry.json"}
    for entry in artifact["entries"]:
        name = entry["path"]
        if entry["type"] != "file":
            continue
        if entry["executable"]:
            findings.append(_finding("code", name, "This file is marked as a program. Skills can't run code; "
                                                   "take the file out or share it as text."))
        elif _looks_runnable(name) or name.lower().endswith((".pyc", ".pyo", ".wasm", ".class", ".node")):
            findings.append(_finding("code", name, "This is a program or script. Skills can't run code; take it out."))
        elif name not in allowed and name not in pictures and not (
                name.startswith("skills/") and name.lower().endswith(TEXT_SUFFIXES)):
            findings.append(_finding("code", name, "Skills hold Markdown or plain-text files under skills/, the licence "
                                                   "and their pictures. Take this file out.", rule="layout"))
        elif artifact["files"][name][:2] == b"#!":
            findings.append(_finding("code", name, "This file starts like a script. Skills can't run code."))
    manifest = artifact["manifest"]
    declared = [key for key in _RUNNABLE_MANIFEST_KEYS if manifest.get(key)]
    if declared:
        findings.append(_finding("code", ".codex-plugin/plugin.json", "The manifest declares " + ", ".join(declared) +
                                 ", which run code. Skills can't run code."))
    if "vool" in manifest:
        # VOOL writes this block itself to mark a pack as the owner's own; a package claiming it would pass as theirs.
        findings.append(_finding("code", ".codex-plugin/plugin.json", "The manifest has a \"vool\" section, which only "
                                 "VOOL itself writes. Take it out."))
    for field in ("permissions", "credential_bindings"):
        if manifest.get(field):
            findings.append(_finding("code", ".codex-plugin/plugin.json", "The manifest asks for " +
                                     field.replace("_", " ") + ". Skills can't ask for access; take it out."))
    if artifact["metadata"]["kind"] != "skill":
        findings.append(_finding("code", "registry.json", "Only skills can be published to the Skill Codex."))
    return findings


_LIST_MARKERS = re.compile(r"(?<!\w)#?\d{1,2}[.):]?(?=\s)|[-/|;,•·]")
_HEX_KEY = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
# "secret: true" or "password: none" documents a setting; it holds no secret.
_PLACEHOLDER = re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key|credential|passphrase|mnemonic)\w*\s*[:=]\s*)"
                          r"[\"'`]?(?:true|false|none|null|yes|no|empty|required|optional|hidden|masked|redacted)\b[\"'`]?")


def _looks_secret(line: str) -> bool:
    """A key, token or password on this line, as written or as the rules read it."""
    from core.registry.intake import secret_findings
    from core.secret_redaction import contains_secret

    for form in {line, _fold(line)[0]}:  # also with joiners and marks taken out, as the rules read it
        plain = _PLACEHOLDER.sub(r"\1x", form)
        if secret_findings({"line": form.encode()}) or contains_secret(plain) or _HEX_KEY.search(form):
            return True
    return False


def _secret_lines(text: str) -> tuple[list[int], set[int]]:
    """(lines to report, every line holding part of a secret). Neither may ever be quoted back."""
    from core.secret_redaction import _bip39_index

    lines = text.splitlines()
    report = {number for number, line in enumerate(lines, 1) if _looks_secret(line)}
    held = set(report)
    # A recovery phrase may be spread over several lines: 11 list words in any 12, across the file.
    words = _bip39_index()
    tokens = []
    for number, line in enumerate(lines, 1):
        spaced = _LIST_MARKERS.sub(" ", _fold(line)[0].casefold())
        tokens += [(token.strip(".,;:!?\"'()[]*`_"), number) for token in spaced.split()]
    listed = [token in words for token, _ in tokens]
    for start in range(max(1, len(tokens) - 11)):
        if sum(listed[start:start + 12]) >= 11:
            window = [index for index in range(start, min(start + 12, len(tokens))) if listed[index]]
            # Point at the phrase's first word, not a heading word before it.
            report.add(tokens[window[0]][1])
            held.update(tokens[index][1] for index in window)
            break
    return sorted(report), held


def _secrets(texts: dict[str, str]) -> tuple[list[dict], dict[str, set[int]]]:
    """Findings for lines that hold a secret, and every such line per file so no other finding quotes it."""
    findings, held = [], {}
    for name, text in texts.items():
        report, held[name] = _secret_lines(text)
        # Never quote it back: the finding is stored and shown.
        findings += [_finding("secrets", name, "This line looks like a key, password or recovery phrase. Take it out; "
                                               "nobody needs one to use a skill.", line=number, rule="secret")
                     for number in report]
    return findings, held


# "Never paste your recovery phrase" is advice: a negation right before the verb, nowhere else, excuses an ask.
_NEGATED_ASK = re.compile(r"(?i)(?:\bnever|\bnot|n't|\bcannot|\bnobody|\bno one)\s+(?:\w+\s+)?$")
# "never silently send" forbids hiding; the negation must sit right before the word.
_SILENT = re.compile(r"(?i)(?:secretly|silently|quietly|covertly|invisibly)\b")
_NEGATED_BEFORE = re.compile(r"(?i)\b(?:never|not|don't|do not|must not)\s+$")
MAX_FINDINGS = 50


def _strings(value) -> list[str]:
    """Every string a JSON file holds, keys included, as VOOL reads them after decoding."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in [key, *_strings(item)]]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _fold(line: str) -> tuple[str, str]:
    """(plain, folded): plain is NFKC without invisible and combining marks; folded also reads look-alikes as Latin."""
    plain = "".join(char for char in unicodedata.normalize("NFKC", line)
                    if char not in _ALLOWED_MARKS and char not in _INVISIBLE and unicodedata.category(char) != "Mn")
    return plain, plain.translate(_LOOKALIKE)


def _flat(lines: list[str]) -> tuple[str, str, list[int], list[int]]:
    """Lines joined with spaces, so a line break splits nothing; with each line's start offset.

    A line ending in a backslash continues straight into the next one, as a shell reads it, so `pip ins\\` over
    `tall` reads as `pip install`.
    """
    plain, folded, plain_starts, folded_starts = [], [], [], []
    plain_at = folded_at = 0
    for line in lines:
        joined = line.endswith("\\")
        one, two = _fold(line[:-1] if joined else line)
        gap = "" if joined else " "
        plain_starts.append(plain_at)
        folded_starts.append(folded_at)
        plain.append(one + gap)
        folded.append(two + gap)
        plain_at += len(one) + len(gap)
        folded_at += len(two) + len(gap)
    return "".join(plain), "".join(folded), plain_starts, folded_starts


def _instructions(texts: dict[str, str], secret_lines: dict[str, set[int]]) -> list[dict]:
    """Every rule over every text a person or VOOL reads: the skill files, registry.json and the manifest."""
    import bisect

    findings = []
    for name, text in texts.items():
        hidden = secret_lines.get(name, set())
        if name == "LICENSE":
            continue  # checked word for word against the MIT text
        lines = text.splitlines()
        if name.endswith(".json"):
            try:
                # Plain package paths such as "./skills/" are loader structure. A path prefix never excuses
                # the words following it, and outer public metadata has no path exemption.
                lines = [line for value in _strings(json.loads(text)) if name == REVIEW_METADATA or not re.fullmatch(r"\./[A-Za-z0-9._/-]+", value)
                         for line in value.splitlines()]
            except ValueError:
                lines = text.splitlines()
            numbered = False  # decoded strings have no line in the file
        else:
            numbered = True
        if name.endswith("/SKILL.md") and text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0 and "\\" in text[3:end]:
                at = text.index("\\", 3)
                findings.append(_finding("instructions-primary", name, "Write the name and description in plain words, "
                                         "without backslash codes.", line=_line(text, at), quote=_quote(text, at, at + 1),
                                         rule="escapes"))
        plain, folded, plain_starts, folded_starts = _flat(lines)
        seen = set()  # one finding per rule and line, however many ways the line trips it
        for rule, pattern, reason in _RULES:
            subject, starts = (plain, plain_starts) if rule == "letters" else (folded, folded_starts)
            for match in pattern.finditer(subject):
                before = subject[max(0, match.start() - 30):match.start()]
                if rule == "secret-ask" and _NEGATED_ASK.search(before):
                    continue
                if rule == "injection" and _SILENT.match(match.group(0)) and _NEGATED_BEFORE.search(before[-12:]):
                    continue
                index = bisect.bisect_right(starts, match.start()) - 1
                if (rule, index) in seen:
                    continue
                seen.add((rule, index))
                quote = " ".join(lines[index].split()) if lines and not (numbered and index + 1 in hidden) else None
                findings.append(_finding("instructions-primary", name, reason,
                                         line=index + 1 if numbered else None, quote=quote, rule=rule))
                if len(findings) >= MAX_FINDINGS:
                    return findings
    return findings


def _is_mit(text: str) -> bool:
    """An optional title, one or more copyright lines, then the MIT text word for word. One pass, nothing to backtrack."""
    if not text.endswith(" " + _MIT_BODY):
        return False
    head = text[:-len(_MIT_BODY)]
    title = _MIT_TITLE.match(head)
    statements = (" " + head[title.end() if title else 0:]).split(" copyright ")
    if statements[0]:
        return False
    for statement in statements[1:]:
        holder = statement.strip()
        for mark in ("(c) ", "© "):
            if holder.startswith(mark):
                holder = holder[len(mark):]
                break
        if not 1 <= len(holder) <= 200 or "©" in holder:
            return False
    return len(statements) > 1


def _license(artifact: dict) -> list[dict]:
    text = " ".join(artifact["files"].get("LICENSE", b"").decode("utf-8", "replace").casefold().split())
    if artifact["metadata"]["license"] != "MIT" or not _is_mit(text):
        return [_finding("license", "LICENSE", "Every Skill Codex skill uses the MIT licence. Put the MIT licence text "
                                               "in LICENSE, with its copyright lines and nothing else.")]
    return []


def _quality(artifact: dict, texts: dict[str, str], *, official: bool) -> list[dict]:
    info, findings = artifact["metadata"], []
    if not official:
        for field in ("id", "title", "summary"):
            value = str(info.get(field) or "")
            match = _RESERVED.search(value.replace("-", " ") if field == "id" else value)
            if match:
                findings.append(_finding("quality", "registry.json", f"The {field} says \"{match.group(0)}\". Only the "
                                         "Skill Codex can mark a skill that way; take the word out.", quote=value,
                                         rule="reserved-summary" if field == "summary" else "reserved-name"))
    if len(info["summary"].strip()) < 10:
        findings.append(_finding("quality", "registry.json", "Write a summary that says what the skill does.", rule="summary"))
    for name, text in texts.items():
        if name.endswith("/SKILL.md"):
            body = text.split("---", 2)[2] if text.startswith("---") and text.count("---") >= 2 else text
            # Counted as a web page counts it: a Windows line break is one character there.
            if len(body.replace("\r\n", "\n").strip()) < MIN_INSTRUCTIONS:
                findings.append(_finding("quality", name, "The instructions are too short to be useful. Say what the "
                                                          "skill does, step by step.", rule="short"))
        if name.startswith("skills/") or name.endswith(".json"):
            if not official:
                match = _ENDORSED.search(text)
                if match:
                    findings.append(_finding("quality", name, f"It says \"{match.group(0)}\". Only the Skill Codex can "
                                             "mark a skill that way; take the word out.",
                                             line=_line(text, match.start()), quote=_quote(text, match.start(), match.end()),
                                             rule="endorsed"))
    return findings


def run(raw: bytes, scratch, *, targets: list[dict], official: bool = False) -> dict:
    """Run every deterministic check. Returns per-check findings and results; never raises for content."""
    from core.plugin_package import execution_directory
    from core.registry.artifact import inspect, materialize
    from core.registry.rules import analyze
    from core.registry.validation import native_format_findings

    try:
        artifact = inspect(raw)
    except (RegistryError, ValueError, TypeError, KeyError) as exc:
        reason = str(exc) if isinstance(exc, RegistryError) else "the package could not be read"
        return {"artifact": None, "findings": {"archive": [_finding("archive", "package", reason)]}, "results": {}}
    texts, text_findings = _texts(artifact)
    findings = {name: [] for name in DETERMINISTIC}
    for item in text_findings:
        findings[item["check"]].append(item)
    findings["code"] += _code(artifact)
    if sum(len(text) for text in texts.values()) > MAX_TEXT:
        # Nothing this long can be read in full, so nothing else is worth running on it.
        findings["quality"].append(_finding("quality", "package", "This skill is too long to check. Keep all its "
                                            "text under 120,000 characters.", rule="too-long"))
        return {"artifact": artifact, "findings": findings, "results": {}}
    # Scan decoded author text, not JSON escape sequences: escaped quotes must not hide a secret.
    metadata_words = "\n".join(_strings(json.loads(metadata_text(artifact, checksums=False))))
    secrets, secret_lines = _secrets({**texts, REVIEW_METADATA: metadata_words})
    findings["secrets"] += secrets
    findings["instructions-primary"] += _instructions(texts, secret_lines)
    findings["license"] += _license(artifact)
    findings["quality"] += _quality(artifact, texts, official=official)
    if artifact["manifest"].get("requires"):
        findings["dependencies"].append(_finding("dependencies", ".codex-plugin/plugin.json",
                                                 "Skill Codex skills can't depend on other packages yet. Empty the requires list."))
    boundary_text = "\n".join(_strings(json.loads(metadata_text(artifact))))
    boundaries = analyze({**artifact["files"], REVIEW_METADATA: boundary_text.encode("utf-8")})
    findings["native-boundaries"] += [dict(item, reason="This tries to override VOOL's own rules on spending, "
                                           "approvals or permissions. Take it out.") for item in boundaries["conflicts"]]
    if not findings["code"]:
        # Only text-only packages are written to disk for the native format check.
        try:
            with execution_directory(scratch) as owned:
                materialize(raw, owned / "package")
                findings["compatibility"] += [dict(item, check="compatibility")
                                              for item in native_format_findings(owned / "package", artifact)]
        except (RegistryError, ValueError, OSError) as exc:
            findings["compatibility"].append(_finding("compatibility", "package", str(exc) if isinstance(
                exc, RegistryError) else "VOOL could not load this skill"))
    else:
        findings["compatibility"].append(_finding("compatibility", "package", "Not checked until the files above are fixed."))
    if secret_lines.get(REVIEW_METADATA):
        for items in findings.values():
            for item in items:
                if item.get("path") == REVIEW_METADATA:
                    item.pop("quote", None)  # do not expose fragments of a secret split across fields or lines
                    item.pop("line", None)  # decoded metadata has no source-file line number
    publication = {"service": PUBLICATION_SERVICE, "artifact_sha256": artifact["sha256"]}
    results = {
        "source": {"publication": publication, "artifact_sha256": artifact["sha256"]},
        "archive": {"files": len(artifact["files"]), "bytes": artifact["raw_length"]},
        "secrets": {}, "code": {}, "license": {"license": "MIT"}, "quality": {},
        "instructions-primary": {"rules": [rule for rule, _, _ in _RULES]},
        "dependencies": {"pins": {}},
        "native-boundaries": {"obeys_native_rules": True, "conflicts": [],
                              "overlaps": sorted({item["control"] for item in boundaries["native_control_overlaps"]})},
        "compatibility": {"targets": targets, "passed": True},
    }
    return {"artifact": artifact, "findings": findings, "results": results}


def policy(targets: list[dict]) -> dict:
    """The admission policy these checks satisfy; its digest goes into the trust root.

    The model behind the instruction review is recorded in each report, not here, so changing it
    needs no new trust root.
    """
    checks = {name: {"tool": SUITE + "/" + name, "version": str(SUITE_MAJOR), "config_sha256": CONFIG_SHA256}
              for name in DETERMINISTIC}
    checks["instructions-secondary"] = {"tool": SUITE + "/model-review", "version": str(SUITE_MAJOR),
                                        "config_sha256": CONFIG_SHA256}
    return {"schema": "vool.registry.policy.v1", "checks": checks, "targets": targets, "max_age": 86400}


def evidence(outcome: dict, policy_value: dict, review: dict, *, now: int) -> dict:
    """Bind every check's result to the exact bytes. Only call when no check found anything."""
    from core.registry.admission import CHECKS, validate_policy

    artifact = outcome["artifact"]
    publication = outcome["results"]["source"]["publication"]
    value = {"schema": "vool.registry.evidence.v1", "artifact_sha256": artifact["sha256"],
             "native_digest": artifact["native_digest"], "policy_sha256": validate_policy(policy_value),
             "source": artifact["metadata"]["source"], "dependencies": {}, "publication": publication,
             "checks": [], "reports": {}}
    results = dict(outcome["results"], **{"instructions-secondary": review})
    for name in sorted(CHECKS):
        check = {"name": name, "scanner": dict(policy_value["checks"][name]), "completed_at": now,
                 "status": "complete", "findings": [], "coverage": "all"}
        report = {"schema": "vool.registry.scan_report.v1", "artifact_sha256": artifact["sha256"],
                  "native_digest": artifact["native_digest"], "policy_sha256": value["policy_sha256"],
                  "publication": publication, **check, "result": results[name]}
        check["report_sha256"] = digest(report)
        value["checks"].append(check)
        value["reports"][name] = report
    canonical(value)  # refuse anything that cannot be signed
    return value
