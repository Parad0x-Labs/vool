"""`vool-community try`: install a skill into a throwaway profile and watch it pick.

The engine behind the `try` command (tools/community_skills.py wires argparse to `run_try`).
An author wrote a skill; the question they actually have is "will VOOL choose it for the
requests I mean it for, and leave it alone everywhere else?". So this checks exactly that,
with VOOL's own validator, installer and picker, inside a profile the caller never sees:
the skill validates first (nothing is installed when it does not), then it is installed
into a temporary home, every ask is run through the same guidance call a real turn makes,
and one line per ask plus a single summary line is all the output there is.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["run_try"]

# The throwaway profile is defined by these four: home for any state the runtime keeps,
# the plugins tree the skill installs into, and an empty bundled dir so the packs shipped
# with this checkout take no part in the picking. Saved and restored on every run.
_ISOLATED_ENV = ("HOME", "VOOL_HOME", "VOOL_PLUGINS_DIR", "VOOL_BUNDLED_PLUGINS_DIR")


def run_try(folder: Path, picks: tuple[str, ...], not_picks: tuple[str, ...]) -> int:
    """Check a skill folder against picks and not-picks; the process exit code.

    One line per ask, then one summary line. Returns 0 only when every pick was picked
    and every not-pick was left alone. Never raises: every failure, expected or not,
    is said in plain words and returns 1.
    """

    try:
        return _run(Path(folder), tuple(picks or ()), tuple(not_picks or ()))
    except Exception as exc:  # an author gets words, never a traceback
        detail = " ".join(str(exc).split())[:200]
        print(f"the check could not finish: {detail or type(exc).__name__}")
        return 1


def _run(folder: Path, picks: tuple[str, ...], not_picks: tuple[str, ...]) -> int:
    from core.registry.codec import canonical
    from core.skill_tools import resolve_skill_markdown, validate_skill

    if not picks:
        print(canonical({"status": "refused",
                         "reason": "at least one --pick is required"}).decode("ascii"))
        return 1

    # Validate with the same check `skill.install` runs, BEFORE anything is installed.
    source = resolve_skill_markdown(str(folder))
    verdict = validate_skill(str(source))
    if str(verdict.get("status") or "") != "ok":
        print(f"the skill was not installed: {_finding(verdict)}")
        return 1

    from core.plugin_skills import parse_skill

    skill = parse_skill(Path(source))
    if skill is None or not str(skill.name or "").strip():
        print("the skill was not installed: its SKILL.md does not name the skill")
        return 1

    saved = {name: os.environ.get(name) for name in _ISOLATED_ENV}
    root = Path(tempfile.mkdtemp(prefix="community-try-"))
    home = root / "home"
    profile = root / "profile"
    bundled = root / "bundled"
    home.mkdir(parents=True)
    (profile / "plugins").mkdir(parents=True)  # the catalog only sees a root that holds plugins/
    bundled.mkdir(parents=True)  # empty: the checkout's own packs stay out of the ranking
    os.environ["HOME"] = str(home)
    os.environ["VOOL_HOME"] = str(home)
    os.environ["VOOL_PLUGINS_DIR"] = str(profile)
    os.environ["VOOL_BUNDLED_PLUGINS_DIR"] = str(bundled)
    code = 1
    try:
        code = _run_in_profile(Path(source), skill, picks, not_picks)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(root, ignore_errors=True)
    return code


def _run_in_profile(source: Path, skill: Any, picks: tuple[str, ...],
                    not_picks: tuple[str, ...]) -> int:
    """Install and judge, already inside the throwaway profile."""

    from core.plugin_catalog import invalidate_storage_listing
    from core.registry.codec import canonical
    from core.skill_tools import install_skill
    from core.tool_offer_assembly import reset_skill_cache, skill_guidance_for

    result = install_skill(str(source), plugin_id="local-skills", scope="global")
    if str(result.get("status") or "") != "ok":
        print(f"the skill could not be installed for the check: {_finding(result)}")
        return 1
    # The catalog and the guidance seam both cache what they last saw on disk; both must
    # re-read the tree this install just created, or the asks below judge yesterday's list.
    invalidate_storage_listing()
    reset_skill_cache()

    skill_name = str(skill.name or "").strip()
    slug = str(result.get("slug") or "").strip()
    # The loader reports the name the SKILL.md declares; the slug is compared too, so a
    # name the slugger would rewrite is still recognised as this skill.
    ours = {skill_name} | ({slug} if slug else set())

    failures = {"pick": 0, "not-pick": 0}
    for kind, asks in (("pick", picks), ("not-pick", not_picks)):
        for ask in asks:
            text = str(ask)
            if not text.strip():
                failures[kind] += 1
                print(f'FAIL {kind:<8} "{text}" -> the ask is empty - '
                      "write the words a person would actually say")
                continue
            guidance = skill_guidance_for(user_text=text)
            picked = any(str(row.get("name") or "") in ours for row in guidance.skills)
            if kind == "pick" and picked:
                print(f'ok   {"pick":<8} "{text}"')
            elif kind == "pick":
                failures["pick"] += 1
                print(f'FAIL {"pick":<8} "{text}" -> not picked')
            elif not picked:
                print(f'ok   {"not-pick":<8} "{text}"')
            else:
                failures["not-pick"] += 1
                why = _picked_reason(text, skill, slug)
                tail = f"picked by {why}" if why else "picked"
                print(f'FAIL {"not-pick":<8} "{text}" -> {tail}')

    passed = failures["pick"] == 0 and failures["not-pick"] == 0
    print(canonical({
        "status": "passed" if passed else "failed",
        "picks": len(picks),
        "pick_failures": failures["pick"],
        "not_picks": len(not_picks),
        "not_pick_failures": failures["not-pick"],
        "skill": skill_name,
    }).decode("ascii"))
    return 0 if passed else 1


def _picked_reason(ask: str, skill: Any, slug: str) -> str:
    """Why the ranker matched, in the order it weighs: name word, trigger, shared words.

    Mirrors core.plugin_skills.rank_skills: the skill named outright is the strongest
    signal, an explicit trigger counts next, and shared description words are the
    weakest way a match can happen.
    """

    from core.plugin_skills import _tokens

    wanted = _tokens(ask)
    spoken = " " + " ".join(re.split(r"[^a-z0-9]+", str(ask or "").lower())) + " "
    for candidate in (str(skill.name or ""), slug):
        words = " ".join(re.split(r"[^a-z0-9]+", candidate.lower())).strip()
        if len(words) > 2 and f" {words} " in spoken:
            return f'name word "{candidate.strip()}"'
    for phrase in tuple(skill.triggers or ()):
        tokens = _tokens(phrase)
        if tokens and tokens <= wanted:
            return f'trigger "{str(phrase).strip()}"'
    shared: list[str] = []
    for token in re.split(r"[^a-z0-9]+", str(skill.description or "").lower()):
        if token in wanted and token not in shared:
            shared.append(token)
        if len(shared) >= 3:
            break
    if shared:
        return "shared words " + ", ".join(f'"{token}"' for token in shared)
    return ""


def _finding(report: dict) -> str:
    """A validator or installer report, said in one line an author can act on."""

    parts = [str(item).strip() for item in (report.get("problems") or []) if str(item).strip()]
    if not parts:
        reason = str(report.get("reason") or "").strip()
        if reason:
            parts.append(reason)
    if not parts:
        parts.append("it did not pass the skill check")
    return "; ".join(parts)
