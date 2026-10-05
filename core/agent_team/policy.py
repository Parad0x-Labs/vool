"""Never-do rules every agent inherits, whatever its brief says.

Always denied to an agent, at every depth:

* pushing with git (every push URL is rewritten to a transport that does not exist, through
  git's own environment config, so it holds for any git binary and every descendant process);
* reading the owner's keys: secret-looking environment variables are not passed down;
* starting agents past the depth ceiling (the depth travels in the environment and is checked
  when an agent asks the team to start another);
* writing outside its lease (the write gate refuses, the overlap watch pauses);
* writing git internals (``.git/hooks``, ``.git/config``: the overlap watch alerts and pauses).

Deploys, external messages and financial actions have no process-level switch; a process agent
that attempts one is outside what this module can stop, so the team never offers those as agent
capabilities and model agents run inside VOOL's own permission matrix (plan mode for readers,
the parent's prohibitions carried into the brief verbatim).

The parent's prohibitions (``never push``, ``no web``, ``don't spend``…) are read with VOOL's own
parser and conserved into every child: parent ∩ child, never wider.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PUSH_DENIED_SCHEME = "vool-agent-push-denied:"
ALWAYS_DENIED = (
    "git push",
    "reading the owner's keys and tokens",
    "starting agents beyond the depth ceiling",
    "writing outside its claimed paths",
    "writing git hooks or git config",
)
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|COOKIE|SESSION|AUTH|PRIVATE|MNEMONIC|SEED)", re.I)
_KEEP = {"VOOL_AGENT_RUN", "VOOL_AGENT_STATUS", "VOOL_AGENT_RESULT", "VOOL_AGENT_PAUSE",
         "VOOL_AGENT_DEPTH", "VOOL_AGENT_TEAM", "VOOL_AGENT_NAME", "VOOL_AGENT_CLAIMS",
         "SSH_AUTH_SOCK_DISABLED", "TERM_SESSION_ID"}


def child_environment(base: Mapping[str, str], *, extra: Mapping[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k in _KEEP or not _SECRET_NAME.search(k)}
    env.pop("SSH_AUTH_SOCK", None)
    # Drop any inherited GIT_CONFIG_COUNT block: it is replaced, never merged, so an inherited
    # config cannot shadow the push denial.
    count = env.pop("GIT_CONFIG_COUNT", None)
    if count is not None:
        for i in range(int(count) if str(count).isdigit() else 0):
            env.pop(f"GIT_CONFIG_KEY_{i}", None)
            env.pop(f"GIT_CONFIG_VALUE_{i}", None)
    env.update({
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{PUSH_DENIED_SCHEME}:.pushInsteadOf",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_TERMINAL_PROMPT": "0",
    })
    env.update({k: str(v) for k, v in extra.items()})
    return env


def parent_prohibitions(chat_text: str) -> Any:
    """The chat's own prohibitions, read with VOOL's parser."""
    from core.turn_prohibitions import prohibitions_from_text

    return prohibitions_from_text(str(chat_text or ""))


def brief_constraints(chat_text: str) -> str:
    """The parent's constraint sentences, quoted into a model agent's brief verbatim so the
    child turn's own prohibition parser freezes the same families (parent ∩ child)."""
    lines = [f"- Never: {item}." for item in ALWAYS_DENIED]
    # Every constraint sentence travels verbatim, whether or not VOOL's family parser names a
    # family for it ("never push" is not a frozen family, and it still binds the child): the
    # child turn re-parses the same words, so its own gates freeze at least what the parent's did.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", str(chat_text or "")) if s.strip()]
    kept = [s for s in sentences if re.search(r"\b(never|don'?t|do not|no|without|avoid|not|only)\b", s, re.I)]
    lines.extend(f"- From the user: {s}" for s in kept[:8])
    return "\n".join(lines)


def child_may_use(chat_text: str, capability: str) -> tuple[bool, str]:
    from core.turn_prohibitions import child_demand_policy

    decision = child_demand_policy(parent_prohibitions(chat_text), capability, "")
    return bool(decision.allowed), str(decision.refusal_text or "")


__all__ = [
    "ALWAYS_DENIED",
    "PUSH_DENIED_SCHEME",
    "brief_constraints",
    "child_environment",
    "child_may_use",
    "parent_prohibitions",
]
