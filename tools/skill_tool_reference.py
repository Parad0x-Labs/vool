"""Write the tool table in docs/guides/skill-tools-and-limits.md from VOOL's own tool contracts.

    python -m tools.skill_tool_reference           # rewrite the table between the markers
    python -m tools.skill_tool_reference --check   # exit 1 when the page differs from the contracts

The table is generated, never hand-edited: a skill's `allowed-tools` must name tools that exist,
so the list an author reads has to be the list the validator checks. It is read in a FRESH
profile (a temporary home, no VOOL_* settings), so it shows what a new install offers before the
person sets anything up, the same on every machine that runs it.

Per tool: the name, what it does (the contract's own first sentence), its input fields
(`core.runtime_tool_contracts` input_schema; `?` marks an optional field), a scope note where the
name alone misleads, whether it reads or changes something, and what Manual and Auto mode do with
a call (from `core.mode_permission_policy.actions_for_tool` and the mode matrix).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "docs" / "guides" / "skill-tools-and-limits.md"
BEGIN = "<!-- BEGIN GENERATED TOOL TABLE (python -m tools.skill_tool_reference) -->"
END = "<!-- END GENERATED TOOL TABLE -->"

#: Surfaces that are VOOL's own plumbing, not tools a skill would ask for, with the reason shown.
INTERNAL_SURFACES = {
    "runtime": "the plain answer itself; it is always offered, so naming it changes nothing",
    "orchestration": "VOOL's own sub-task runner, used by VOOL's planner rather than by a skill",
    "capability": "VOOL's own way of widening the tool offer mid-turn",
}

#: One line where a tool's name or description alone would mislead an author.
SCOPE_NOTES = {
    "operator.check_availability": "Takes the request as written in `text` (\"am I free Tuesday afternoon?\"). "
                                   "Needs a calendar account in Settings. With no morning/afternoon/evening "
                                   "word it checks the whole day from 00:00.",
    "operator.propose_calendar_event": "Takes the request as written in `text`. Put the title in quotes "
                                       "(propose \"Project review\" on Tuesday at 15:00 for 30 minutes), or it "
                                       "asks \"What should it be called?\". Needs a calendar account in Settings.",
    "operator.update_calendar_event": "Takes the request as written in `text`. Stages a change; it applies only "
                                      "after the person approves. Needs a calendar account in Settings.",
    "operator.save_note": "Takes the request as written in `text` (save a note called \"Tea\" saying buy tea). "
                          "Writes a Markdown file into the chat's project folder, under notes/.",
    "operator.schedule_calendar_event": "Writes one .ics file into VOOL's calendar outbox. It does not add the "
                                        "event to any calendar app.",
    "operator.list_reminders": "Only VOOL's own reminders in this chat, not reminders in other apps.",
    "operator.find_notes": "The notes operator.save_note wrote in the chat's project folder, not other apps' notes.",
    "operator.show_note": "The notes operator.save_note wrote in the chat's project folder, not other apps' notes.",
    "operator.apple_note_list": "Apple Notes on a Mac only.",
    "operator.apple_note_read": "Apple Notes on a Mac only.",
    "operator.show_agenda": "Needs a calendar account in Settings. `window` is the words of the request "
                            "(\"tomorrow\", \"this week\").",
    "operator.search_calendar_event": "Needs a calendar account in Settings.",
    "operator.list_calendars": "Needs a calendar account in Settings.",
    "operator.inspect_calendar_event": "Needs a calendar account in Settings.",
    "web.search": "Off until the person turns on web lookups (Settings > Privacy & Permissions > Web lookups).",
    "web.research": "Off until the person turns on web lookups (Settings > Privacy & Permissions > Web lookups).",
    "machine.write_file": "Only inside ~/Desktop, ~/Downloads and ~/Documents.",
    "machine.ensure_directory": "Only inside ~/Desktop, ~/Downloads and ~/Documents.",
    "machine.move_path": "Refuses protected system locations and asks for a live confirmation from the "
                         "operating system before moving.",
    "machine.read_file": "Only inside ~/Desktop, ~/Downloads and ~/Documents.",
    "sandbox.run_command": "Runs with no network, inside a sandbox; refused where the sandbox is unavailable.",
    "workspace.run_tests": "Runs inside a sandbox with no network; refused where the sandbox is unavailable.",
    "workspace.run_lint": "Runs inside a sandbox with no network; refused where the sandbox is unavailable.",
    "workspace.run_formatter": "Runs inside a sandbox with no network; refused where the sandbox is unavailable.",
    "file.deliver": "Hands the person a file to download; nothing is written to their folders.",
    "profile.remember": "The person's own profile in VOOL (name, language, time zone and so on).",
    "profile.forget": "The person's own profile in VOOL.",
}

_EFFECT_WORDS = {
    "read_only": "reads",
    "workspace_write": "writes files",
    "creative_state": "saves VOOL data",
    "builder_state": "saves a draft",
    "network_send": "sends out",
    "network_publish": "publishes",
    "runtime_capability_change": "changes VOOL's setup",
    "validation_command": "runs a command",
    "sandbox_command": "runs a command",
    "media_generation": "makes media",
    "wallet_spend": "spends money",
    "credit_spend": "spends credits",
    "task_orchestration": "runs sub-tasks",
}
_MODE_WORDS = {"allow": "runs", "require_approval": "asks", "deny": "refused"}


def _fresh_profile() -> None:
    """Read the contracts as a new install sees them: a temporary home and no VOOL_* settings."""
    for name in [key for key in os.environ if key.startswith("VOOL_")]:
        os.environ.pop(name, None)
    home = Path(tempfile.mkdtemp(prefix="skill-tool-reference-"))
    os.environ["HOME"] = str(home)
    os.environ["VOOL_HOME"] = str(home / "vool")
    from core import runtime_paths

    runtime_paths.configure_runtime_home(home / "vool")


def _cell(text: str) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _first_sentence(text: str, limit: int = 170) -> str:
    flat = " ".join(str(text or "").split())
    match = re.match(r"(.+?[.!?])(?:\s|$)", flat)
    sentence = match.group(1) if match else flat
    return sentence if len(sentence) <= limit else sentence[: limit - 3].rstrip() + "..."


def _inputs(schema: dict) -> str:
    if not schema:
        return "none"
    return ", ".join(f"`{key}{'?' if 'optional' in str(kind).lower() else ''}`" for key, kind in schema.items())


def _modes(intent: str) -> tuple[str, str]:
    from core.mode_permission_policy import MODE_PERMISSION_MATRIX, OperatingMode, actions_for_tool

    actions = actions_for_tool(intent, {})
    out = []
    for mode in (OperatingMode.MANUAL, OperatingMode.AUTO):
        effects = {MODE_PERMISSION_MATRIX[mode][action].value for action in actions} or {"allow"}
        worst = "deny" if "deny" in effects else "require_approval" if "require_approval" in effects else "allow"
        out.append(_MODE_WORDS[worst])
    return out[0], out[1]


def render() -> str:
    """The generated section, markers included."""
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    listed: dict[str, list] = defaultdict(list)
    off: dict[str, list[str]] = defaultdict(list)
    hidden: list[str] = []
    internal: dict[str, list[str]] = defaultdict(list)
    for name in sorted(contracts):
        contract = contracts[name]
        if contract.source != "builtin":
            continue
        if not contract.model_visible:
            hidden.append(name)
        elif contract.tool_surface in INTERNAL_SURFACES:
            internal[contract.tool_surface].append(name)
        elif not contract.supported:
            off[" ".join(str(contract.unsupported_reason or "switched off on this runtime").split())].append(name)
        else:
            listed[contract.tool_surface].append(contract)

    lines = [BEGIN, "",
             f"{sum(len(v) for v in listed.values())} tools, read from `core.runtime_tool_contracts` in a fresh "
             "profile. `?` marks an optional input. **Permission ids** are what a frontmatter `permissions` list "
             "must cover when it is declared. **Manual** and **Auto** say what that mode does with a "
             "call: *runs*, *asks* (a **Permission needed** prompt; nothing happens until the person clicks "
             "**Allow once**) or *refused*. A call can still be refused for its arguments (a path outside the "
             "allowed folders, a startup file) or ask where this says *runs* (overwriting an existing file "
             "asks in Auto mode).", ""]
    for surface in sorted(listed):
        lines += [f"### {surface}", "",
                  "| Tool | What it does | Inputs | Reads or changes | Permission ids | Manual | Auto |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for contract in listed[surface]:
            manual, auto = _modes(contract.intent)
            what = _first_sentence(contract.description)
            note = SCOPE_NOTES.get(contract.intent, "")
            if note:
                what = f"{what} *{note}*"
            permissions = ", ".join(f"`{item}`" for item in contract.permission_actions) or "none"
            effect = _EFFECT_WORDS.get(contract.side_effect_class, _cell(contract.side_effect_class))
            inputs = _cell(_inputs(contract.input_schema))
            lines.append(f"| `{contract.intent}` | {_cell(what)} | {inputs} | {effect} | {permissions} | {manual} | {auto} |")
        lines.append("")
    lines += ["### Left out of the table", "",
              "These tools exist, and a skill may name them in `allowed-tools` (the validator accepts every "
              "name below), but on a fresh install they are off until the person sets something up:", ""]
    for reason in sorted(off):
        lines.append("* " + ", ".join(f"`{name}`" for name in off[reason]) + f": {_cell(reason)}")
    lines += ["", "Not for skills:", ""]
    for surface in sorted(internal):
        lines.append("* " + ", ".join(f"`{name}`" for name in internal[surface]) + f": {INTERNAL_SURFACES[surface]}.")
    if hidden:
        lines.append("* " + ", ".join(f"`{name}`" for name in hidden) +
                     ": owner-only; never offered to a model, so naming it in a skill does nothing.")
    lines += ["", END]
    return "\n".join(lines)


def updated(page_text: str, section: str) -> str:
    start, end = page_text.find(BEGIN), page_text.find(END)
    if start < 0 or end < start:
        raise SystemExit(f"{PAGE}: the generated-table markers are missing")
    return page_text[:start] + section + page_text[end + len(END):]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the page is out of date")
    args = parser.parse_args(argv)
    _fresh_profile()
    current = PAGE.read_text(encoding="utf-8")
    wanted = updated(current, render())
    if args.check:
        if wanted != current:
            print(f"{PAGE.relative_to(ROOT)} is out of date with the tool contracts; "
                  "run: python -m tools.skill_tool_reference")
            return 1
        print("up to date")
        return 0
    if wanted != current:
        PAGE.write_text(wanted, encoding="utf-8")
        print(f"rewrote the tool table in {PAGE.relative_to(ROOT)}")
    else:
        print("up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
