"""Parent-chat disclosure authority for server-admitted Council seat turns.

The live run registry and model-pin capability mint this process-only value.
Request JSON cannot construct it. Child retrieval still uses its canonical
namespace; this binding owns preview display and privacy invalidation only.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field

from core.disclosure.sources import SourceCatalog


@dataclass(frozen=True)
class CouncilDisclosureScope:
    run_id: str
    chat_id: str
    project_id: str
    workspace: str
    version: str | None
    catalog: SourceCatalog = field(default_factory=SourceCatalog, repr=False)

    stop_requested: threading.Event = field(default_factory=threading.Event, repr=False)

    def __deepcopy__(self, memo):
        return self

    def cancel_run(self) -> None:
        from core.council.api import stop

        with self.catalog.lock:
            if self.stop_requested.is_set():
                return
            self.stop_requested.set()
        # The provider worker must unwind before Council closes its HTTP
        # reader. Closing it synchronously here waits on that same worker.
        threading.Thread(target=stop, args=(self.run_id,),
                         name="council-disclosure-stop", daemon=True).start()

    def clear_sources(self) -> None:
        with self.catalog.lock:
            self.catalog.entries.clear()
            self.catalog.prepared.clear()

    def verify(self) -> None:
        from core.council import pin_lock
        from core.council.containment import run_is_fenced
        from core.disclosure.scope import current_version
        from core.disclosure.wire import DisclosureRefused

        if not self.chat_id or not self.version:
            raise DisclosureRefused("This chat's privacy could not be verified. Nothing was sent.")
        pin = pin_lock.snapshot()
        if (self.stop_requested.is_set() or run_is_fenced(self.run_id) or not self.chat_id or not self.version or pin is None
                or pin.get("run_id") != self.run_id
                or current_version(self.chat_id, self.project_id) != self.version):
            raise DisclosureRefused("This Council's chat changed or closed. Nothing was sent.")


def capture_parent(orchestrator) -> CouncilDisclosureScope:
    from core.chat_session_identity import canonical_chat_session_id
    from core.context_namespace import ensure_chat_namespace, load_chat_namespace
    from core.council.roles import role_brief
    from core.disclosure.scope import current_version
    from core.persistent_memory import load_session_meta
    from core.project_store import project_root

    chat = canonical_chat_session_id(orchestrator.chat_session)
    project = ""
    workspace = orchestrator.workspace_root
    version = None
    if chat:
        try:
            namespace = load_chat_namespace(chat)
            if namespace is None:
                project = str((load_session_meta().get(chat) or {}).get("project_id") or "")
                namespace = ensure_chat_namespace(chat, project_id=project)
            project = namespace.project_id
            if project:
                workspace = project_root(project) or ""
            version = current_version(chat, project)
        except (RuntimeError, ValueError, OSError, sqlite3.Error):
            # Creating a run is not a send grant. Keep it unusable when the
            # canonical privacy authority cannot supply a verified snapshot.
            version = None
    scope = CouncilDisclosureScope(orchestrator.run_id, chat, project, workspace, version)
    # The operator's problem and server-authored role controls are conversation.
    # Exhibits retain their file class; unknown candidates/reports stay local
    # under project rules.
    scope.catalog.add(orchestrator.problem.strip(), "conversation", chat)
    exhibits = str(orchestrator.exhibits or "").strip()
    if exhibits:
        scope.catalog.has_attachments = True
        scope.catalog.add(exhibits, "attachment", "council-exhibits")
        scope.catalog.prepared["council-exhibits"] = {
            "id": "council-exhibits", "filename": "Council evidence",
            "size_bytes": len(exhibits.encode("utf-8")), "text": exhibits,
        }
    for seat in orchestrator.seats:
        for round_no in (1, 2):
            scope.catalog.add(role_brief(seat.role_id, votes=seat.votes, round_no=round_no),
                              "conversation", "council-role")
    return scope
