"""Publish a skill you made to the Skill Codex: a check on this device, then a link to the website.

VOOL sends nothing. ``publish_handoff`` runs the Skill Codex server's own instant checks for its
written-skill form (``registry_service.store.Store.draft``: ``initialize``, ``pack``, ``preflight``,
then the automatic checks of ``core.registry.checks`` the service runs on a draft sent with the
creator's MIT agreement) in a private temporary directory, with placeholder package details, and
turns their findings into plain sentences. The automatic checks' own reasons are shown as written,
with the line and words that tripped them, exactly as the website shows them. When the check passes it returns a link to the website's publish page
whose fragment carries the skill::

    ORIGIN + "/codex/skills/publish#skill=" + base64url(UTF-8 JSON, no padding)
    JSON: {"v":1,"name":<slug>,"title":<display name>,"summary":<description>,"skill_markdown":<SKILL.md>}

A fragment never reaches a server; the page reads it, and the person signs in, chooses a category and
sends it there. ORIGIN is the fixed constant of ``core.registry.client``.

Only skills the person made qualify, exactly as for ``core.skill_package.export_skill``: first-party
skills, skills inside someone else's package and skills marked ``local-only: true`` are refused. The
website form carries one SKILL.md, so a skill folder holding anything else the person put there is
refused too; the version history VOOL keeps next to SKILL.md (``core.skill_tools``) is not theirs and
is ignored.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

PUBLISH_PATH = "/codex/skills/publish"
#: The written-skill form's limit on the SKILL.md text (``Store.draft``).
MAX_SKILL_MARKDOWN = 32768

# Placeholder package details for the check only; the person chooses the real ones on the website.
_PUBLISHER = "local"
_STAND_IN_NAME = "skill"
_STAND_IN_SUMMARY = "A skill made in VOOL."
_CATEGORY = ("productivity", "notes-summaries")
_MIT = """MIT License

Copyright (c) the author of this skill

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

# Files VOOL itself keeps in a skill folder (core.skill_tools): the version store next to SKILL.md
# (versions/v<N>.md, versions/history.json and its atomic-write .tmp) and the .tmp an install or
# rollback copies through before replacing SKILL.md.
_HISTORY_FILE = re.compile(r"v[1-9][0-9]*\.md|history\.json(?:\.tmp)?")
_ACTIVE_TMP = "SKILL.md.tmp"
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

#: One plain sentence per reason; the add-on page shows its own translation of each code.
REASONS = {
    "not_user_skill": "Only skills you made yourself can go on the Skill Codex.",
    "not_found": "VOOL couldn't find this skill. It may have been turned off or removed.",
    "local_only": "This skill is marked local-only, so it stays on this device.",
    "unreadable": "VOOL couldn't read this skill's text.",
    "extra_files": "This skill has other files with it (listed below). For now the Skill Codex takes only the skill's text.",
    "too_long_text": "The skill's text is longer than 32,768 characters, the most the Skill Codex form takes.",
    "too_long_body": "The skill's instructions are longer than 8,000 characters, the most VOOL reads.",
    "header": "The skill's header, the part between the --- lines at the top, is missing or can't be read.",
    "no_description": "Add a short description of what the skill does.",
    "description": "The skill's description is longer than 500 characters or has characters the Skill Codex can't show.",
    "long_name": "The skill's short name is longer than 64 characters, the most the Skill Codex allows.",
    "title": "The skill's name is longer than 60 characters or uses angle brackets, which the Skill Codex doesn't allow.",
    "reserved_name": "The skill's short name is one Windows keeps for itself, so the Skill Codex can't use it.",
    "secret": "The skill's text looks like it holds a key, password or other secret. Take it out before sharing.",
    "override": (
        "Part of the skill tells VOOL to ignore or get around its own rules, such as spending limits or "
        "approvals. Skills can't change those rules, so reword that part."
    ),
    "no_instructions": "The skill has no instructions yet.",
    "unknown_tools": "The skill names tools VOOL doesn't have: {tools}.",
    "format": "The Skill Codex's check didn't accept this skill as it is.",
    # The automatic checks' own sentence goes in "text"; this is only the fallback.
    "codex_check": "The Skill Codex's checks would refuse this skill as it is.",
    "check_failed": "VOOL couldn't finish checking this skill. Nothing was sent.",
    # The Skill Codex's automatic checks, one per stable rule code (``core.registry.checks``).
    "check_install": "This asks someone to install or run software. Describe the task in words instead.",
    "check_download": "This links to a program or archive to download. Take the link out.",
    "check_address": "This uses a short link, a raw address or a paste site. Link to the real page, or take the link out.",
    "check_secret_ask": "This asks for a recovery phrase, key, password or code. Take that out.",
    "check_wallet": "This contains a wallet address. Take the address out.",
    "check_injection": ("This reads like an attempt to change VOOL's instructions or hide what the skill does. "
                        "Rewrite it plainly."),
    "check_hidden": "This holds a hidden comment, escape code or encoded text. Write everything as plain visible text.",
    "check_letters": ("A word here mixes letters from different alphabets, which can hide what it says. "
                      "Retype it in one alphabet."),
    "check_invisible": "This line has invisible or control characters. Retype it as plain text.",
    "check_escapes": "Write the skill's name and description in plain words, without backslash codes.",
    "check_reserved_name": ("The skill's name uses a word only the Skill Codex may use, such as \"official\" or "
                            "\"verified\". Take it out of the name."),
    "check_reserved_summary": ("The skill's description uses a word only the Skill Codex may use, such as \"official\" or "
                               "\"verified\". Take it out of the description."),
    "check_summary": "The skill's description is too short. Say in one sentence what the skill does.",
    "check_short": "The instructions are too short to be useful. Say what the skill does, step by step.",
    "check_endorsed": ("This says the Skill Codex or VOOL vouches for the skill. Only the Skill Codex can say that, "
                       "so take it out."),
}


def _reason(code: str, text: str | None = None, **params: Any) -> dict[str, Any]:
    template = REASONS[code]
    return {"code": code, "text": text if text is not None else template.format(**params), **params}


def _refused(reasons: list[dict[str, Any]]) -> dict[str, Any]:
    unique: list[dict[str, Any]] = []
    for item in reasons:
        if item not in unique:
            unique.append(item)
    return {"status": "refused", "reasons": unique}


def _extra_files(skill_dir: Path, *, limit: int = 12) -> list[str]:
    """Files in the skill folder other than SKILL.md and VOOL's own bookkeeping, as relative paths."""
    from core.skill_tools import VERSIONS_DIRNAME

    found: list[str] = []
    for current, folders, files in os.walk(skill_dir, followlinks=False):
        here = Path(current)
        relative = here.relative_to(skill_dir)
        for name in list(folders):
            if (here / name).is_symlink():
                found.append((relative / name).as_posix())
        for name in files:
            path = relative / name
            if path.parts == ("SKILL.md",):
                continue  # the skill itself, read the way export reads it
            if not (here / name).is_symlink() and (
                    path.parts == (_ACTIVE_TMP,)
                    or (relative.parts == (VERSIONS_DIRNAME,) and _HISTORY_FILE.fullmatch(name))):
                continue
            found.append(path.as_posix())
        if len(found) >= limit:
            break
    return sorted(found)[:limit]


def _plain(value: str) -> str:
    return " ".join(str(value or "").split())


def _metadata(package_name: str, title: str, summary: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Package details for the check, validated by the registry's own metadata rules.

    A detail the rules refuse becomes a reason, and a valid stand-in takes its place so the rest of
    the check still runs and every reason is reported at once."""
    from core.registry.artifact import metadata
    from core.registry.codec import RegistryError

    info: dict[str, Any] = {
        "id": f"{_PUBLISHER}/{package_name}", "kind": "skill", "version": "1.0.0", "summary": summary,
        "category": _CATEGORY[0], "subcategory": _CATEGORY[1], "license": "MIT", "languages": ["any"],
        "source": {"upload_id": "0" * 32, "namespace": _PUBLISHER}, "title": title,
    }
    reasons: list[dict[str, Any]] = []
    for _ in range(4):
        try:
            metadata(info)
            return info, reasons
        except RegistryError as exc:
            message = str(exc)
        if message == "invalid summary" and info["summary"] != _STAND_IN_SUMMARY:
            reasons.append(_reason("no_description" if not summary else "description"))
            info["summary"] = _STAND_IN_SUMMARY
        elif message == "invalid title" and "title" in info:
            reasons.append(_reason("title"))
            info.pop("title")
        elif message == "invalid stable package id" and package_name != _STAND_IN_NAME:
            reasons.append(_reason("long_name"))
            package_name = _STAND_IN_NAME
            info["id"] = f"{_PUBLISHER}/{package_name}"
        else:
            break
    reasons.append(_reason("format"))
    return {}, reasons


def _server_check(info: dict[str, Any], skill_markdown: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``Store.draft``'s instant checks, step for step, inside a private temporary directory.

    Returns the preflight report and the automatic checks' findings. As on the service, text that
    looks like it holds a secret stops there, so it is never read any further."""
    from core.plugin_package import execution_directory
    from core.registry.artifact import pack
    from core.registry.checks import run
    from tools.community_skills import initialize, preflight

    with tempfile.TemporaryDirectory(prefix="vool-codex-check-") as outer:
        scratch = Path(outer)
        with execution_directory(scratch) as owned:
            source = owned / "author"
            initialize(source, info, _MIT.encode("utf-8"))
            name = info["id"].split("/")[1]
            # newline="" writes the text byte for byte, as the server's write_text does on its host.
            (source / "skills" / name / "SKILL.md").write_text(skill_markdown, encoding="utf-8", newline="")
            raw = pack(source, info, scratch)
            report = preflight(raw, scratch)
        if any(item.get("check") == "secrets-preflight" for item in report["findings"]):
            return report, []
        # A Skill Codex skill is the person's own, so the team's exception for its own wording never applies.
        outcome = run(raw, scratch, targets=[], official=False)
        return report, [item for items in outcome["findings"].values() for item in items]


def _automatic_reason(finding: dict[str, Any], slug: str) -> dict[str, Any]:
    """One automatic check finding, by its stable rule code so the page can show it in the person's language."""
    check = str(finding.get("check") or "")
    if check == "secrets":
        return _reason("secret")  # never quoted back
    if check == "native-boundaries":
        return _reason("override")
    if check == "compatibility":
        # The same native-format check the preflight runs, so its reasons map the same way.
        return _finding_reason(dict(finding, check="native-format"))
    if check not in {"instructions-primary", "quality"}:
        return _reason("format")  # the parts VOOL fills in itself: licence, manifest, package
    path = str(finding.get("path") or "")
    code = "check_" + str(finding.get("rule") or "").replace("-", "_")
    if code in REASONS:
        reason = _reason(code)
    else:
        # A reason without a code VOOL knows: shown in the checks' own (English) words.
        reason = {"code": "codex_check", "text": _plain(finding.get("reason", "")) or REASONS["codex_check"]}
    if path == f"skills/{slug}/SKILL.md":
        # The line and words in the person's own SKILL.md, written byte for byte. Package details such as
        # the placeholder id are VOOL's own, so they are never quoted.
        if type(finding.get("line")) is int:
            reason["line"] = finding["line"]
        if isinstance(finding.get("quote"), str) and finding["quote"].strip():
            reason["quote"] = finding["quote"]
    return reason


def _finding_reason(finding: dict[str, Any]) -> dict[str, Any]:
    check = str(finding.get("check") or "")
    reason = str(finding.get("reason") or "")
    if check == "secrets-preflight":
        return _reason("secret")
    if check == "native-boundaries" or reason.startswith("Possible instruction to override a native control"):
        return _reason("override")
    if check == "native-format":
        if reason.startswith("body exceeds"):
            return _reason("too_long_body")
        if reason.startswith("description is empty"):
            return _reason("no_description")
        if reason.startswith("body is empty"):
            return _reason("no_instructions")
        if reason.startswith(("no YAML frontmatter", "frontmatter ", "name is empty")):
            return _reason("header")
        prefix = "allowed-tools names tools this runtime does not have: "
        if reason.startswith(prefix):
            return _reason("unknown_tools", tools=reason[len(prefix):])
    return _reason("format")


def handoff_url(slug: str, title: str, summary: str, skill_markdown: str) -> str:
    """The website's publish page with this skill in the fragment (never sent to any server)."""
    from core.registry.client import ORIGIN

    payload = {"v": 1, "name": slug, "title": title, "summary": summary, "skill_markdown": skill_markdown}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ORIGIN + PUBLISH_PATH + "#skill=" + base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def publish_handoff(name: str, *, keys: dict[str, str] | None = None, scope: str = "") -> dict[str, Any]:
    """Check one of the person's skills here and, when it passes, return the website link.

    ``{"status": "ok", "name", "slug", "title", "summary", "text", "chars", "url", "suggested_needs"}``
    (``name`` is the package name the website receives, the skill's slug) or
    ``{"status": "refused", "reasons": [{"code", "text", ...}]}``. Uses no network, and writes
    nothing outside a temporary directory it removes.
    """
    from core import skill_package
    from core.plugin_package import PackageError
    from core.plugin_skills import parse_skill_content
    from core.registry.codec import RegistryError
    from core.registry.requirements import undeclared_needs
    from core.skill_tools import _MAX_BODY_CHARS

    try:
        skill_dir, _scope = skill_package._user_skill_dir(name, keys, scope)
        raw = (skill_dir / "SKILL.md").read_bytes()
    except skill_package.PackageRefused as refused:
        # The export refusal stays as written; the page shows its own sentence for the code.
        code = "not_user_skill" if refused.code == "not_user_skill" else "not_found"
        return _refused([_reason(code, refused.message)])
    except OSError:
        return _refused([_reason("unreadable")])
    if skill_package._is_local_only(raw):
        return _refused([_reason("local_only")])

    reasons: list[dict[str, Any]] = []
    extra = _extra_files(skill_dir)
    if extra:
        reasons.append(_reason("extra_files", files=extra))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _refused([*reasons, _reason("unreadable")])

    slug = skill_dir.name
    match = _FRONTMATTER.match(text)
    if match is None:
        reasons.append(_reason("header"))
    elif len(match.group(2).strip()) > _MAX_BODY_CHARS:
        reasons.append(_reason("too_long_body"))
    skill = parse_skill_content(text, path=skill_dir / "SKILL.md")
    title = _plain(getattr(skill, "name", "")) or slug
    summary = _plain(getattr(skill, "description", ""))

    info, problems = _metadata(slug, title, summary)
    reasons.extend(problems)
    if len(text) > MAX_SKILL_MARKDOWN:
        # The form refuses the text before anything is packaged, so there is nothing more to check.
        reasons.append(_reason("too_long_text"))
    elif info:
        try:
            report, automatic = _server_check(info, text)
        except RegistryError as exc:
            reasons.append(_reason("reserved_name" if str(exc) == "path is ambiguous on supported platforms" else "format"))
        except (PackageError, OSError, ValueError):
            reasons.append(_reason("check_failed"))
        else:
            reasons.extend(_finding_reason(finding) for finding in report["findings"])
            if report["status"] != "preflight_passed" and not report["findings"]:
                reasons.append(_reason("format"))
            reasons.extend(_automatic_reason(finding, info["id"].split("/")[1]) for finding in automatic)
    if reasons:
        return _refused(reasons)
    return {
        "status": "ok",
        "name": slug,
        "slug": slug,
        "title": title,
        "summary": summary,
        "text": text,
        "chars": len(text),
        "url": handoff_url(slug, title, summary, text),
        "suggested_needs": undeclared_needs({"needs": []}, {f"skills/{slug}/SKILL.md": raw}),
    }


def is_own_skill(skill_dir: Path) -> bool:
    """Whether ``publish_handoff`` accepts this installed skill folder by its name.

    The add-on page offers Publish only here: the name must lead to this very folder through the
    same lookup export uses (so a same-named skill in someone else's package never borrows the
    button), and the skill must not be marked local-only."""
    from core import skill_package

    try:
        found, _scope = skill_package._user_skill_dir(skill_dir.name, None)
        if found.resolve() != Path(skill_dir).resolve():
            return False
        return not skill_package._is_local_only((found / "SKILL.md").read_bytes())
    except (skill_package.PackageRefused, OSError):
        return False


__all__ = ["MAX_SKILL_MARKDOWN", "PUBLISH_PATH", "REASONS", "handoff_url", "is_own_skill", "publish_handoff"]
