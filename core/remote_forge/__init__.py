"""Remote-forge VOCABULARY. The authorities that used to live here are gone.

What remains is typed language about repositories and holds no authority of its own:

  identity.py   provider+owner+repo+url -- which repo, exactly (no fuzzy names)
  state.py      LOCAL_HEAD / REMOTE_BRANCH_HEAD / PR_HEAD bound to SHAs + fetch time
  actions.py    typed proposed mutations, each with an idempotency key
  adapters.py   the in-memory `FakeGitHubAdapter` double used by the sandbox demos

WHAT WAS REMOVED, AND WHERE IT WENT

This package had grown a complete parallel VOOL: its own permission gate with its own role
matrix, its own effect runner and receipt ledger, its own render-boundary truth guard, and its
own raw socket to api.github.com holding a credential read out of the environment. Each was a
second answer to a question VOOL already answers once, and every one of them was reachable only
from a demo script -- which is the only reason none had caused harm yet.

  gate.py     -> deleted. `core.mode_permission_policy.decide_tool_call` is THE permission
                 authority; RepoOps reaches it through the tool contracts' declared permission
                 actions and the one runtime door.
  execute.py  -> deleted. Effects are receipted by `core.effect_gateway`, journaled by
                 `core.blackbox`, reconciled by `core.effect_reconciliation`, and finalized by
                 `core.finalization`. A claim about a remote mutation is backed by
                 `repo.verify_remote` reading the remote, not by a per-module claim guard.
  GitHubAdapter -> deleted. The one real forge client is `core.kas.adapters.github`, behind the
                 single `ForgeAdapter` contract that GitLab also implements.

The production repository vertical is `core.repoops.plane`, offered from the one tool registry
as `repo.*`. See `docs/AUTHORITY_MAP.md`.
"""
