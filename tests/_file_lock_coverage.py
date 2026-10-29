"""More paths for the cross-process file-lock order (`tests/_file_lock_order.py`), REVIEW-1 finding 3.

Each role here drives one path through its real entry point, in a child process with the same
recorder and the same documented order:

- `served_forget`: a forget sent as a served chat turn, `VoolAgent.run_once("Forget ...")`. The
  turn door admits it first (`_preadmit_forget_turn`, every home's ledger flock in sorted order),
  the command runs under `_command_erasure_fence`, and `_seal_semantic_result` retires the
  admission (`_finish_forget_turn_admission`, the same sorted homes again).
- `project_delete`: the web route `/api/projects/delete` (`dispatch_post`): it unbinds the
  project's chats, erases their finalizations (`erase_finalizations_for_session`, the A8
  traversal) and purges what was learned in them (`forget_sessions_memory`, the entry lock).
- `remember_forget_recall`: "Remember that ..." (`add_memory_fact`), "Forget ..."
  (`forget_memory` -> `forget_structured_memory`), then the web runtime's owner-local recall
  (`core.web.api.runtime._memory_recall_response`, which holds `serialized(memory._authority_home)`
  around `revoked_tokens`).

`inverted:<role>` runs the same path inside a test-only wrapper that first takes a lock the
documented order puts after the fence (or, for the served turn, the home that sorts last), so the
path's own fence is entered the wrong way round. Test code only; production is not changed.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from tests import _file_lock_order as flo

ENTRY = "tests._file_lock_coverage"
ROLES = ("served_forget", "project_delete", "remember_forget_recall")

#: Functions each role's trace must show in its recorded frames: the path really ran through them.
PATH_FRAMES = {
    "served_forget": ("_run_once_inner", "_preadmit_forget_turn", "_finish_forget_turn_admission",
                      "maybe_handle_memory_command"),
    "project_delete": ("_dispatch_post_inner", "erase_finalizations_for_session", "forget_sessions_memory"),
    "remember_forget_recall": ("add_memory_fact", "forget_structured_memory", "_memory_recall_response"),
}

#: The test-only inversion each `inverted:<role>` wraps its path in: the lock it takes first.
INVERSION = {
    "served_forget": "later_home",
    "project_delete": "finalization.A8_TRAVERSAL_LOCK",
    "remember_forget_recall": "files._JSONL_LOCK",
}

# A served turn in a plain child: keep it off any local model server and the machine's keychain.
_CHILD_ENV = {
    "OLLAMA_HOST": "http://127.0.0.1:9",
    "VOOL_OLLAMA_URL": "http://127.0.0.1:9",
    "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:9",
    "VOOL_OLLAMA_CHAT_URL": "http://127.0.0.1:9/api/chat",
    "VOOL_OLLAMA_TAGS_URL": "http://127.0.0.1:9/api/tags",
    "VOOL_OLLAMA_PS_URL": "http://127.0.0.1:9/api/ps",
    "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
    "VOOL_SKIP_PROVIDER_PREWARM": "1",
    "VOOL_CREDENTIAL_STORE": "vault",
    "VOOL_KEY_STORAGE_MODE": "file",
    "VOOL_LIQUEFY_LOGS": "0",
}


def frames(rows) -> set[str]:
    """Every function name in the rows' recorded call sites."""
    return {where.rsplit(":", 1)[-1] for row in rows for where in row.get("where") or []}


def _post(path: str, body: dict, work: str) -> tuple[int, dict]:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(path=path, body=body, headers={"content-type": "application/json"},
                             runtime=RuntimeServices(display_name="VOOL"), model_name="vool",
                             workspace_root_provider=lambda: work, client_host="127.0.0.1")
    return response.status, json.loads(response.body.decode("utf-8"))


def _owner_context(chat: str, home: str) -> dict:
    return {"surface": "openclaw", "platform": "api", "_owner_local": True, "chat_id": chat, "runtime_home": home}


def setup_role(spec: dict) -> dict:
    """Data for every role: forget turns, projects with chats, chats to remember in."""
    from core.context_namespace import ensure_chat_namespace
    from core.memory import entries
    from tests.foundation.test_a8_privacy_pass004 import _finalize

    home, runtime_home, n, tag = spec["home"], spec["runtime_home"], spec["n"], spec.get("tag", "x")
    data: dict = {role: [] for role in ROLES}
    for i in range(n):
        chat, value = f"openclaw:{tag}turn{i:011d}", f"QZTN{i:02d}-{4300 + i}"
        flo._ingest(runtime_home, chat, f"The locker code is {value}.")
        data["served_forget"].append([chat, value])

        root = Path(home) / f"project-{tag}{i}"
        root.mkdir(parents=True, exist_ok=True)
        status, body = _post("/api/projects", {"name": f"P{tag}{i}", "root": str(root)}, home)
        assert status == 200, body
        pid = body["project"]["id"]
        for j in range(2):
            member = f"openclaw:{tag}proj{i}{j:010d}"
            flo._ingest(home, member, f"The vault code {i}{j} is PRJX{i}{j}-{5100 + i}.")
            answer = f"Answer {i}{j} about the vault."
            _finalize(answer, request_id=f"req-{tag}-p{i}{j}", turn_id=f"t-{tag}-p{i}{j}")
            entries._ensure_memory_files()
            with open(entries.conversation_log_path(), "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"session_id": member, "request_id": f"req-{tag}-p{i}{j}",
                                         "user": "Where is the vault?", "assistant": answer}) + "\n")
            status, body = _post("/api/chat/session", {"session_id": member, "project_id": pid}, home)
            assert status == 200, body
        data["project_delete"].append(pid)

        recall = f"openclaw:{tag}recall{i:011d}"
        ensure_chat_namespace(recall, grant_confirmed_profile=True)
        data["remember_forget_recall"].append([recall, f"the harbor gate {i} code is ZX-82{i}{i}",
                                               f"the harbor gate {i} code", f"What is the harbor gate {i} code?"])

    return data


def _run_path(role: str, item, spec: dict):
    home, runtime_home = spec["home"], spec["runtime_home"]
    if role == "served_forget":
        from core.agent_runtime.agent import VoolAgent

        chat, value = item
        agent = VoolAgent(backend_name="test-backend", device="lock-order", persona_id="default")
        result = agent.run_once(f"Forget {value}.", session_id_override=chat,
                                source_context={"chat_id": chat, "runtime_home": runtime_home,
                                                "client_turn_id": f"lock-order-{chat}"})
        return str(result.get("response") or "")
    if role == "project_delete":
        status, body = _post("/api/projects/delete", {"id": item}, home)
        return {"status": status, **body}
    if role == "remember_forget_recall":
        from core.persistent_memory import maybe_handle_memory_command
        from core.web.api.runtime import RuntimeServices, _memory_recall_response

        chat, fact, subject, question = item
        context = _owner_context(chat, home)
        _, remembered = maybe_handle_memory_command(f"Remember that {fact}.", session_id=chat, source_context=context)
        _, forgot = maybe_handle_memory_command(f"Forget {subject}.", session_id=chat, source_context=context)
        recall = _memory_recall_response(RuntimeServices(runtime_home=home), user_text=question,
                                         source_context=context) or {}
        return [remembered, forgot, recall.get("route_reason")]
    raise ValueError(role)


@contextlib.contextmanager
def _inversion(role: str, spec: dict):
    """Test-only: take first a lock the documented order puts after the fence the path will enter."""
    import importlib

    kind = INVERSION[role]
    if kind == "later_home":
        from core.memory import revocation

        later = max(Path(spec["home"]).resolve(), Path(spec["runtime_home"]).resolve())
        with revocation.serialized(later):  # the turn then takes the earlier home under it
            yield
        return
    module, attr = {"finalization.A8_TRAVERSAL_LOCK": ("core.finalization", "A8_TRAVERSAL_LOCK"),
                    "files._JSONL_LOCK": ("core.memory.files", "_JSONL_LOCK")}[kind]
    with getattr(importlib.import_module(module), attr):  # reentrant: the path takes it again inside
        yield


def run_role(role: str, items: list, spec: dict) -> list:
    if role.startswith("inverted:"):
        path = role.split(":", 1)[1]
        out = []
        for item in items:
            with _inversion(path, spec):
                out.append(_run_path(path, item, spec))
        return out
    if role in ROLES:
        return [_run_path(role, item, spec) for item in items]
    return flo.run_role(role, items, spec)


def child_main() -> None:
    for name, value in _CHILD_ENV.items():
        os.environ.setdefault(name, value)
    flo.child_main(run=run_role, setup_fn=setup_role)
