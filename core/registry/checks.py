"""Automatic Skill Codex checks: every byte of every file, failing closed.

The Mac authority runs these on the exact uploaded bytes before it signs anything. The
server runs the same code first so a creator hears about a problem at once. A finding
names the file and line and says what to change; a secret is never quoted back.
"""
from __future__ import annotations

import re
import unicodedata

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
_ALLOWED_MARKS = {"\n", "\t", "\r", "\u200c", "\u200d", "\u200e", "\u200f"}
MIN_INSTRUCTIONS = 80
QUOTE = 120

_MIT = (
    re.compile(r"permission is hereby granted, free of charge, to any person obtaining a copy of this software"),
    re.compile(r"the above copyright notice and this permission notice shall be included in all copies or "
               r"substantial portions of the software"),
    re.compile(r"the software is provided \"as is\", without warranty of any kind"),
)
# Words that read as the Skill Codex vouching for a skill. Only the team's own skills may use them.
_RESERVED = re.compile(r"\b(?:verified|official|certified|endorsed|tested by|parad0x|paradox labs|vool team|"
                       r"vool-team|safe to use|security checked)\b", re.IGNORECASE)
_ENDORSED = re.compile(r"\b(?:verified|approved|certified|endorsed|tested|checked|reviewed)\s+by\s+(?:vool|the skill codex|"
                       r"parad0x|paradox)\b|\b(?:official|verified)\s+(?:vool|skill codex)\b|\bparad0x\s+labs\b|"
                       r"\bvool[- ]team\b", re.IGNORECASE)
_RULES = (
    ("install", re.compile(
        r"\b(?:curl|wget|iwr|invoke-webrequest|invoke-restmethod)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b|"
        r"\b(?:bash|sh|zsh)\s+-c\b|\b(?:bash|sh)\s+<\(\s*(?:curl|wget)|\biex\b|invoke-expression|"
        r"powershell(?:\.exe)?\s+[^\n]{0,40}-(?:enc|encodedcommand|e)\b|"
        r"\b(?:pip3?|pipx|uv)\s+install\b|\bnpm\s+(?:install|i)\b|\bnpx\s+\S|\byarn\s+(?:global\s+)?add\b|"
        r"\bpnpm\s+(?:add|install)\b|\bbrew\s+(?:install|tap)\b|\bapt(?:-get)?\s+install\b|\bcargo\s+install\b|"
        r"\bgo\s+install\b|\bgem\s+install\b|\bchmod\b|\bxattr\s+-[a-z]*d\b|spctl\s+--master-disable|"
        r"\bsudo\s+\S|\bosascript\b|\blaunchctl\s+load\b|\bcrontab\s+-", re.IGNORECASE),
        "Skills on the Skill Codex can't ask anyone to install or run software. Describe the task in words instead."),
    ("download", re.compile(
        r"https?://\S+?\.(?:exe|msi|dmg|pkg|app|zip|rar|7z|tar|gz|tgz|bz2|xz|sh|bash|command|ps1|bat|cmd|vbs|"
        r"js|py|jar|apk|deb|rpm|bin|scr|iso|img)(?:[?#]\S*)?(?=[\s)\]>\"'`]|$)", re.IGNORECASE),
        "Skills can't link to programs or archives to download. Take the link out."),
    ("address", re.compile(
        r"https?://(?:\d{1,3}\.){3}\d{1,3}\b|https?://\[[0-9a-f:]+\]|"
        r"https?://(?:www\.)?(?:bit\.ly|tinyurl\.com|t\.co|is\.gd|cutt\.ly|rb\.gy|goo\.gl|ow\.ly|"
        r"pastebin\.com|paste\.ee|hastebin\.com|glot\.io|rentry\.co|ghostbin\.\w+)/", re.IGNORECASE),
        "Skills can't use short links, raw addresses or paste sites. Link to the real page, or take the link out."),
    ("secret-ask", re.compile(
        r"\b(?:send|paste|enter|share|provide|type|give|upload|reveal|tell)\b[^\n.]{0,60}\b(?:recovery|seed|secret|"
        r"mnemonic)\s+(?:phrase|words)|\b(?:send|paste|enter|share|provide|type|give|upload|reveal|tell)\b[^\n.]{0,60}"
        r"\b(?:private\s+key|password|passcode|api\s+key|access\s+token|2fa\s+code|one-time\s+code)", re.IGNORECASE),
        "Skills can't ask for recovery phrases, keys, passwords or codes. Take that out."),
    ("wallet", re.compile(r"\b0x[0-9a-fA-F]{40}\b|\b(?:bc1|tb1)[02-9ac-hj-np-z]{11,71}\b|"
                          r"(?<![A-Za-z0-9/])(?=[1-9A-HJ-NP-Za-km-z]*\d)(?=[1-9A-HJ-NP-Za-km-z]*[A-HJ-NP-Z])"
                          r"(?=[1-9A-HJ-NP-Za-km-z]*[a-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9])"),
     "Skills can't contain wallet addresses. Take the address out."),
    ("injection", re.compile(
        r"\b(?:ignore|disregard|forget)\b[^\n.]{0,30}\b(?:all|any|previous|prior|earlier|above|other)\b[^\n.]{0,20}"
        r"\b(?:instructions|rules|messages|prompts?)\b|\bsystem prompt\b|\bdeveloper mode\b|\bjailbreak\b|"
        r"\byou are now\b|\bdo not (?:tell|inform|show|let) (?:the )?(?:user|owner|person)\b|"
        r"\bwithout (?:telling|asking|informing|showing) (?:the )?(?:user|owner|person)\b|"
        r"\b(?:secretly|silently|quietly|covertly)\b[^\n.]{0,40}\b(?:send|upload|forward|copy|transfer|pay|run|delete)\b",
        re.IGNORECASE),
        "This reads like an attempt to change VOOL's instructions or hide what the skill does. Rewrite it plainly."),
    ("hidden", re.compile(r"<!--(?:(?!-->).){3,}?-->|data:[a-z]+/[a-z0-9.+-]+;base64,|[A-Za-z0-9+/]{200,}={0,2}",
                          re.IGNORECASE | re.DOTALL),
     "Skills can't carry hidden comments or encoded blobs. Write everything as plain visible text."),
)


def _finding(check: str, path: str, reason: str, *, line: int | None = None, quote: str | None = None) -> dict:
    item = {"check": check, "path": path, "reason": reason}
    if line is not None:
        item["line"] = line
    if quote is not None:
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
    for name, raw in artifact["files"].items():
        if name in pictures:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(_finding("code", name, "This file isn't plain text. Skills hold text, the licence and "
                                                  "their pictures only."))
            continue
        hidden = next((index for index, char in enumerate(text)
                       if unicodedata.category(char) in {"Cc", "Cf", "Co", "Cs"} and char not in _ALLOWED_MARKS and
                       not (index == 0 and char == "\ufeff")), None)
        if hidden is not None:
            findings.append(_finding("instructions-primary", name, "This file has invisible or control characters. "
                                     "Retype the line as plain text.", line=_line(text, hidden)))
        texts[name] = text
    return texts, findings


def _code(artifact: dict) -> list[dict]:
    from core.skill_package import _RUNNABLE_MANIFEST_KEYS, _looks_runnable

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
                                                   "and their pictures. Take this file out."))
        elif artifact["files"][name][:2] == b"#!":
            findings.append(_finding("code", name, "This file starts like a script. Skills can't run code."))
    manifest = artifact["manifest"]
    declared = [key for key in _RUNNABLE_MANIFEST_KEYS if manifest.get(key)]
    if declared:
        findings.append(_finding("code", ".codex-plugin/plugin.json", "The manifest declares " + ", ".join(declared) +
                                 ", which run code. Skills can't run code."))
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


def _secrets(texts: dict[str, str], artifact: dict) -> list[dict]:
    from core.registry.intake import secret_findings
    from core.secret_redaction import _bip39_index, contains_secret

    findings = []
    words = _bip39_index()
    for name, text in texts.items():
        lines = set()
        for number, line in enumerate(text.splitlines(), 1):
            plain = _PLACEHOLDER.sub(r"\1x", line)
            if secret_findings({name: line.encode()}) or contains_secret(plain) or _HEX_KEY.search(line):
                lines.add(number)
        # A recovery phrase may be spread over several lines: 11 list words in any 12, across the file.
        tokens = []
        for number, line in enumerate(text.splitlines(), 1):
            spaced = _LIST_MARKERS.sub(" ", unicodedata.normalize("NFKC", line).casefold())
            tokens += [(token.strip(".,;:!?\"'()[]*`_"), number) for token in spaced.split()]
        listed = [token in words for token, _ in tokens]
        for start in range(max(1, len(tokens) - 11)):
            if sum(listed[start:start + 12]) >= 11:
                # Point at the phrase's first word, not a heading word before it.
                lines.add(tokens[next(index for index in range(start, start + 12) if listed[index])][1])
                break
        # Never quote it back: the finding is stored and shown.
        findings += [_finding("secrets", name, "This line looks like a key, password or recovery phrase. Take it out; "
                                               "nobody needs one to use a skill.", line=number) for number in sorted(lines)]
    return findings


_NEGATABLE = {"install", "download", "secret-ask"}
_NEGATED = re.compile(r"\b(?:never|don't|do not|must not|mustn't|should not|shouldn't|cannot|can't|won't|"
                      r"no one|nobody|not)\b", re.IGNORECASE)
# "never silently send" forbids hiding; the negation must sit right before the word.
_SILENT = re.compile(r"(?i)(?:secretly|silently|quietly|covertly)\b")
_NEGATED_BEFORE = re.compile(r"(?i)\b(?:never|not|don't|do not|must not)\s+$")
MAX_FINDINGS = 50


def _instructions(texts: dict[str, str]) -> list[dict]:
    findings = []
    for name, text in texts.items():
        if not name.startswith("skills/"):
            continue
        for rule, pattern, reason in _RULES:
            for match in pattern.finditer(text):
                if rule in _NEGATABLE:
                    # "Never paste your recovery phrase anywhere" is good advice, not an ask.
                    sentence = re.split(r"[.!?\n]", text[:match.start()])[-1]
                    if _NEGATED.search(sentence):
                        continue
                elif rule == "injection" and _SILENT.match(match.group(0)) and \
                        _NEGATED_BEFORE.search(text[max(0, match.start() - 12):match.start()]):
                    continue
                findings.append(_finding("instructions-primary", name, reason, line=_line(text, match.start()),
                                         quote=_quote(text, match.start(), match.end())))
                if len(findings) >= MAX_FINDINGS:
                    return findings
    return findings


def _license(artifact: dict) -> list[dict]:
    text = " ".join(artifact["files"].get("LICENSE", b"").decode("utf-8", "replace").casefold().split())
    if artifact["metadata"]["license"] != "MIT" or not all(pattern.search(text) for pattern in _MIT):
        return [_finding("license", "LICENSE", "Every Skill Codex skill uses the MIT licence. Put the MIT licence text in LICENSE.")]
    return []


def _quality(artifact: dict, texts: dict[str, str], *, official: bool) -> list[dict]:
    info, findings = artifact["metadata"], []
    if not official:
        for field in ("id", "title", "summary"):
            value = str(info.get(field) or "")
            match = _RESERVED.search(value.replace("-", " ") if field == "id" else value)
            if match:
                findings.append(_finding("quality", "registry.json", f"The {field} says \"{match.group(0)}\". Only the "
                                         "Skill Codex can mark a skill that way; take the word out.", quote=value))
    if len(info["summary"].strip()) < 10:
        findings.append(_finding("quality", "registry.json", "Write a summary that says what the skill does."))
    for name, text in texts.items():
        if name.endswith("/SKILL.md"):
            body = text.split("---", 2)[2] if text.startswith("---") and text.count("---") >= 2 else text
            if len(body.strip()) < MIN_INSTRUCTIONS:
                findings.append(_finding("quality", name, "The instructions are too short to be useful. Say what the "
                                                          "skill does, step by step."))
            if not official:
                match = _ENDORSED.search(text)
                if match:
                    findings.append(_finding("quality", name, f"It says \"{match.group(0)}\". Only the Skill Codex can "
                                             "mark a skill that way; take the word out.",
                                             line=_line(text, match.start()), quote=_quote(text, match.start(), match.end())))
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
    findings["secrets"] += _secrets(texts, artifact)
    findings["instructions-primary"] += _instructions(texts)
    findings["license"] += _license(artifact)
    findings["quality"] += _quality(artifact, texts, official=official)
    if artifact["manifest"].get("requires"):
        findings["dependencies"].append(_finding("dependencies", ".codex-plugin/plugin.json",
                                                 "Skill Codex skills can't depend on other packages yet. Empty the requires list."))
    boundaries = analyze(artifact["files"])
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
