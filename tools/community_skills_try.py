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
import shutil
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["run_try"]

# The throwaway profile is defined by these four: home for any state the runtime keeps,
# the plugins tree the skill installs into, and an empty bundled dir so the packs shipped
# with this checkout take no part in the picking. Saved and restored on every run.
_ISOLATED_ENV = ("HOME", "VOOL_HOME", "VOOL_PLUGINS_DIR", "VOOL_BUNDLED_PLUGINS_DIR")


def run_try(folder: Path, picks: tuple[str, ...], not_picks: tuple[str, ...],
            rank_with: tuple[Path, ...] = ()) -> int:
    """Check a skill folder against picks and not-picks; the process exit code.

    One line per ask, then one summary line. Returns 0 only when every pick was picked
    and every not-pick was left alone. Never raises: every failure, expected or not,
    is said in plain words and returns 1.
    """

    try:
        return _run(Path(folder), tuple(picks or ()), tuple(not_picks or ()), tuple(Path(p) for p in rank_with or ()))
    except Exception as exc:  # an author gets words, never a traceback
        detail = " ".join(str(exc).split())[:200]
        print(f"the check could not finish: {detail or type(exc).__name__}")
        return 1


def _run(folder: Path, picks: tuple[str, ...], not_picks: tuple[str, ...],
         rank_with: tuple[Path, ...] = ()) -> int:
    from core.registry.codec import canonical
    from core.skill_tools import resolve_skill_markdown, validate_skill

    if not picks:
        print(canonical({"status": "refused",
                         "reason": "at least one --pick is required"}).decode("ascii"))
        return 1

    # Validate with the same check `skill.install` runs, BEFORE anything is installed.
    source = resolve_skill_markdown(str(folder))
    from tools.community_skills import STARTER_MARKER, starter_text_left

    try:
        starter_left = source.is_file() and starter_text_left(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        starter_left = False
    if starter_left:
        print("the skill was not installed: SKILL.md still holds the starter text from `new` "
              f"(lines with {STARTER_MARKER}); write your own description, triggers and steps first")
        return 1
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
        code = _run_in_profile(Path(source), skill, picks, not_picks, rank_with)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(root, ignore_errors=True)
    return code


def _other_skill_files(rank_with: tuple[Path, ...], own: Path) -> list[Path]:
    """Every SKILL.md under the --rank-with folders, except the skill being tried."""

    found: list[Path] = []
    for folder in rank_with:
        folder = Path(folder).expanduser()
        candidates = [folder] if folder.name == "SKILL.md" else sorted(folder.rglob("SKILL.md"))
        found.extend(path for path in candidates if path.is_file() and path.resolve() != own.resolve())
    return found


def _install_others(rank_with: tuple[Path, ...], source: Path, ours: str) -> int:
    """Install the --rank-with skills beside the tried one, as the person's own; how many went in."""

    from core.plugin_skills import parse_skill
    from core.skill_tools import install_skill

    count = 0
    for path in _other_skill_files(rank_with, source):
        other = parse_skill(path)
        name = str(getattr(other, "name", "") or "").strip()
        if not name or name == ours:
            continue
        result = install_skill(str(path), plugin_id="local-skills", scope="global")
        if str(result.get("status") or "") == "ok":
            count += 1
        else:
            print(f"warn --rank-with skill {path} was left out: {_finding(result)}")
    return count


def _run_in_profile(source: Path, skill: Any, picks: tuple[str, ...],
                    not_picks: tuple[str, ...], rank_with: tuple[Path, ...] = ()) -> int:
    """Install and judge, already inside the throwaway profile."""

    from core.plugin_catalog import invalidate_storage_listing
    from core.registry.codec import canonical
    from core.skill_tools import install_skill
    from core.tool_offer_assembly import reset_skill_cache, skill_guidance_for

    result = install_skill(str(source), plugin_id="local-skills", scope="global")
    if str(result.get("status") or "") != "ok":
        print(f"the skill could not be installed for the check: {_finding(result)}")
        return 1
    others = _install_others(rank_with, source, str(skill.name or "").strip()) if rank_with else 0
    # The catalog and the guidance seam both cache what they last saw on disk; both must
    # re-read the tree this install just created, or the asks below judge yesterday's list.
    invalidate_storage_listing()
    reset_skill_cache()

    skill_name = str(skill.name or "").strip()
    slug = str(result.get("slug") or "").strip()
    # The loader reports the name the SKILL.md declares; the slug is compared too, so a
    # name the slugger would rewrite is still recognised as this skill.
    ours = {skill_name} | ({slug} if slug else set())

    from tools.community_skills import frontmatter_warnings

    try:
        warnings = frontmatter_warnings(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        warnings = []
    for line in warnings + trigger_and_name_warnings(skill):
        print(line)

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
            chosen = [str(row.get("name") or "") for row in guidance.skills
                      if str(row.get("name") or "") and str(row.get("name") or "") not in ours]
            if kind == "pick" and picked:
                print(f'ok   {"pick":<8} "{text}"')
            elif kind == "pick":
                failures["pick"] += 1
                taken = f" (picked instead: {', '.join(chosen)})" if chosen else ""
                print(f'FAIL {"pick":<8} "{text}" -> not picked: {_miss_reason(text, skill)}{taken}')
            elif not picked:
                print(f'ok   {"not-pick":<8} "{text}"')
            else:
                failures["not-pick"] += 1
                why = "; ".join(_pick_reasons(text, skill, slug))
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
        # How many other skills it was ranked against (--rank-with); 0 means it was ranked alone.
        "ranked_with": others,
    }).decode("ascii"))
    return 0 if passed else 1


def _pick_reasons(ask: str, skill: Any, slug: str) -> list[str]:
    """Why the picker chose this skill for the ask, in plain words.

    The picker's own explanation comes first: core.plugin_skills.match_reasons, when this
    checkout has it, gives the reasons rank_skills itself used. Without it, the fallback below
    mirrors rank_skills's rules on folded words.
    """

    from core import plugin_skills

    # --- the picker's own reasons (match_reasons(skills, user_text) -> {name: [reasons]}) ---
    match_reasons = getattr(plugin_skills, "match_reasons", None)
    if callable(match_reasons):
        try:
            found = match_reasons((skill,), ask) or {}
            for key in (str(skill.name or ""), slug):
                reasons = [str(item).strip() for item in (found.get(key) or ()) if str(item).strip()]
                if reasons:
                    return reasons
        except Exception:
            pass  # fall through to the local mirror of the rules
    # --- fallback: the same rules, read on the same folded words rank_skills reads ---
    return _fallback_reasons(ask, skill, slug)


def _spoken_forms(ask: str) -> dict[str, str]:
    """Each folded content word of the ask, mapped to the way the person wrote it."""

    from core.plugin_skills import _STOPWORDS, _raw_tokens, _stem

    forms: dict[str, str] = {}
    for token in _raw_tokens(ask):
        if len(token) > 2 and token not in _STOPWORDS and not token.isdigit():
            forms.setdefault(_stem(token), token)
    return forms


def _quoted(stems: list[str], forms: dict[str, str]) -> str:
    return ", ".join(f'"{forms.get(stem, stem)}"' for stem in stems)


def _fallback_reasons(ask: str, skill: Any, slug: str) -> list[str]:
    from core.plugin_skills import _KIND_STEMS, _contains_phrase, _content_stems, _raw_tokens

    ask_stems = _content_stems(ask)
    forms = _spoken_forms(ask)
    spoken = " " + " ".join(_raw_tokens(ask)) + " "
    reasons: list[str] = []
    for candidate in (str(skill.name or ""), slug):
        words = " ".join(_raw_tokens(candidate)).strip()
        if len(words) > 2 and f" {words} " in spoken:
            reasons.append(f'the ask names the skill ("{words}")')
            break
    name_stems = set(_content_stems(str(skill.name or "")))
    trigger_words: set[str] = set()
    for phrase in tuple(skill.triggers or ()):
        stems = _content_stems(phrase)
        if len(stems) == 1:
            trigger_words.update(stems)
        if _contains_phrase(ask_stems, stems):
            reasons.append(f'trigger "{str(phrase).strip()}"')
    topic = set(ask_stems) - _KIND_STEMS
    from_name = sorted(stem for stem in topic & name_stems)
    if from_name and not reasons:
        reasons.append("word from the skill's name " + _quoted(from_name, forms))
    corpus = set(_content_stems(str(skill.description or ""))) | trigger_words
    shared = [stem for stem in dict.fromkeys(ask_stems) if stem in corpus and stem not in name_stems]
    if len(set(shared) | set(from_name)) >= 2 and shared:
        reasons.append("shared words " + _quoted(shared, forms))
    return reasons


def _miss_reason(ask: str, skill: Any) -> str:
    """Why an ask the author meant for the skill did not pick it."""

    from core.plugin_skills import _KIND_STEMS, _content_stems

    ask_stems = _content_stems(ask)
    if not ask_stems:
        return "the ask has no content words once function words are dropped"
    corpus = (set(_content_stems(str(skill.name or ""))) | set(_content_stems(str(skill.description or "")))
              | {stem for phrase in tuple(skill.triggers or ()) for stem in _content_stems(phrase)})
    forms = _spoken_forms(ask)
    shared = [stem for stem in dict.fromkeys(ask_stems) if stem in corpus]
    topic = [stem for stem in shared if stem not in _KIND_STEMS]
    if not shared:
        return "no trigger matched; shares no words with the skill"
    if not topic:
        return ("no trigger matched; shares only kind-of-help words " + _quoted(shared, forms)
                + ", which never pick a skill alone")
    if len(shared) == 1:
        return "no trigger matched; shares only " + _quoted(shared, forms)
    return ("no trigger matched; shares " + _quoted(shared, forms)
            + ", but another skill matched better or a word sits only inside a longer trigger")


def trigger_and_name_warnings(skill: Any) -> list[str]:
    """Warnings, never failures: triggers that shrink to one word or none, and name words that
    pick the skill on their own."""

    from core.plugin_skills import _KIND_STEMS, _content_stems, _raw_tokens

    lines: list[str] = []
    for phrase in tuple(skill.triggers or ()):
        text = str(phrase).strip()
        stems = _content_stems(text)
        if not stems:
            lines.append(f'warn trigger "{text}" has no words left once function words are dropped, '
                         "so it can never pick the skill; use the topic words a person would say")
        elif len(stems) == 1 and len(_raw_tokens(text)) > 1:
            lines.append(f'warn trigger "{text}" comes down to the one word "{stems[0]}": '
                         "any ask with that word picks the skill")
    name = str(skill.name or "")
    forms = {}
    for token in _raw_tokens(name):
        for stem in _content_stems(token):
            forms.setdefault(stem, token)
    alone = [token for stem, token in forms.items() if stem not in _KIND_STEMS]
    if alone:
        words = ", ".join(f'"{token}"' for token in alone)
        lines.append(f"warn name words {words} pick this skill by themselves wherever no other "
                     f'installed skill uses them (for example "where is my {alone[0]}?"); '
                     "keep them out of your --not-pick asks or choose a more specific name")
    return lines


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
