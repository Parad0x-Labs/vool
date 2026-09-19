"""Served coding journeys across repair units, revisions and reviewed bases (coding revision 3).

Real daemon, real ``/api/chat``, the production ``/api/mode`` operator Allow and resume, real
``node`` checks and real bytes on disk. The MODEL IS SCRIPTED -- ``ObservedRepairModel`` below --
and says so. It decides from what the served boundary puts in its prompt: the task id the planner
opened; the newest ``code.task`` observation line, read tolerantly because the renderer clips long
lines (a line that fits keeps sorted keys; a clipped code task line leads with its declared fields
``intent``, ``ok``, ``status``, ``stage``, ``pending_repairs``, ``evidence``, ``reason``, ``next`` and
``verification_failed``, and loses the plan, ``executed`` and ``mode`` first); on a new user turn,
which carries no earlier observations, the stage the previous reply named. A check it issued failed
when the runtime says so (``verification_failed``, or the recovery it names) or, where the cut removed
both, when the step ran (``ok: true``) and its command failed (``status: command_failed``) at
``narrow_test`` or ``cumulative``; a step that was refused or did not complete (``ok: false``) verified
nothing, whatever failure its line still carries. When a line was clipped before its stage it applies
the documented stage order to the step it issued, and logs ``stage-inferred``. A refused proposal whose line names ``differences`` is read as a revision under
a recorded id and previewed again under a new one (logged ``conflict``). A journey that names no owner makes the model find it
from the failure evidence on the newest line (``evidence``: the failure message and the symbol it names): it
searches a named call through the ordinary ``workspace.search_text`` door and takes the file that defines it, or
takes a file the failure message names -- logging ``evidence-visible``, ``search`` and ``owner-from-*``, and giving
up (``owner-not-derived``) when the evidence does not say. Beyond that it uses only its own memory of what it asked for and the repair
text a model would write after reading the files it requested. It never reads the journal, the
workspace or the provider log. Every prompt it received is written under the test's temporary folder
(``model-prompts-<session>``, with its decision log ``model-log.json``) as the record of what it saw.

These journeys replace revision 2's served "atomic two-file unit" test, whose claim the independent
review refuted: an arithmetic fix and a label fix are independent however they are proposed. Here
two independent repairs are validated BETWEEN units, and only a genuinely coupled rename -- proven
to break when either half lands alone -- is validated after all of its changes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import pending_session_approvals
from tests.test_code_task_served_boundary import (
    _boot,
    _journal,
    _reply_text,
    _task_verdict,
    served_factory,
)

pytestmark = [pytest.mark.served]


def _facts(line: str) -> dict[str, Any]:
    """Fields of one (possibly clipped) observation line. A line that fits keeps sorted keys; a clipped code task line
    leads with its declared fields (`intent`, `ok`, `status`, `stage`, `pending_repairs`, `evidence`, `reason`, `next`,
    `verification_failed`) and loses the plan, `executed` and `mode` first; each fact falls back accordingly."""
    def grab(pattern: str) -> str:
        match = re.search(pattern, line)
        return match.group(1) if match else ""

    pending_raw = re.search(r'"pending_repairs": \[([^\]]*)\]', line)
    next_raw = grab(r'"next": \[(.*?)\], "ok"')
    stage = grab(r'"name": "([a-z_]+)", "state": "current"') or grab(r'"stage": "([a-z_]+)"')
    status = grab(r'"status": "([a-z_]+)"')
    ok = {"true": True, "false": False}.get(grab(r'"ok": (true|false)'))
    return {
        "stage": stage,
        "status": status,
        "ok": ok,
        "mode": grab(r'"mode": "([a-z_]+)"'),
        "executed": grab(r'"executed": (true|false)') == "true",
        "pending": re.findall(r'"([^"]+)"', pending_raw.group(1)) if pending_raw else None,
        "next": next_raw,
        "failed_verification": _failed_verification(line, ok=ok, status=status, stage=stage),
        "checkpoint_signal": ("has not been validated" in line
                              or "focused and full checks are recorded" in next_raw
                              or "after it is recorded, the remaining approved repair" in next_raw),
        "stale_refusal": "has changed since you last read it" in line or "changed after this repair was reviewed" in line,
        "conflict": '"differences": [' in line and '"intent": "code.task.propose"' in line,
        "suggested": grab(r'"suggested_proposal_id": "([^"]+)"'),
        "evidence_failure": grab(r'"failure_summary": "((?:[^"\\]|\\.)*)"'),
        "evidence_query": grab(r'"diagnostic_query": "((?:[^"\\]|\\.)*)"'),
        "matches": re.findall(r'\{"line": (\d+), "path": "((?:[^"\\]|\\.)*)", "snippet": "((?:[^"\\]|\\.)*)"\}', line),
    }


def _failed_verification(line: str, *, ok: bool | None, status: str, stage: str) -> bool:
    """Whether the line shows a failed verification. While one stands the runtime says so on every result
    (`verification_failed`, and the recovery it names), and what it visibly says decides. Where the cut removed both, the
    step's own outcome still says it: a code task step that ran (`ok: true`) and whose command failed
    (`status: command_failed`) at `narrow_test` or `cumulative`, where a failed check leaves the task. A step that did not
    run, an unrelated tool and a failing command at any other stage (the reproduction) are not verifications."""
    if '"verification_failed": true' in line or "still fails: re-diagnose" in line:
        return True
    if '"verification_failed": false' in line:
        return False
    return ('"intent": "code.task.step"' in line and ok is True and status == "command_failed"
            and stage in {"narrow_test", "cumulative"})


class ObservedRepairModel:
    """SCRIPTED model. See the module docstring for exactly what it may look at."""

    def __init__(self, *, repro: str, owner: str | None, reads: list[str], repairs: list[dict[str, str]],
                 focused: dict[str, str], full: str, recoveries: list[dict[str, Any]] | None = None,
                 check_mid_unit: bool = False, repairs_by_path: dict[str, dict[str, str]] | None = None) -> None:
        self.repro = repro
        self.owner = owner
        self.reads = list(reads)
        self.plan = [dict(repair) for repair in repairs]
        self.focused = dict(focused)
        self.full = full
        self.recoveries = [dict(recovery) for recovery in (recoveries or [])]
        self.check_mid_unit = check_mid_unit
        self.log: list[str] = []
        self.task_id = ""
        self.n = 0
        self.done: set[str] = set()
        self.proposed: set[str] = set()
        self.approved: set[str] = set()
        self.attempted: set[str] = set()
        self.landed: list[str] = []
        self.stale: set[str] = set()
        self.checked: set[tuple[int, str]] = set()
        self.checkpoint_owed = False
        self.used_recoveries: set[int] = set()
        self.expect: tuple[str, Any] | None = None
        self.last_line = ""
        self.prompt_dir: Path | None = None
        self.replies = 0
        self.last_stage = ""
        self.turn_has_observations = False
        self.last_proposed: list[str] = []
        # The repair text this model would write once it has found and read a file; WHICH file is decided only from
        # evidence (`_derive_owner`).
        self.repairs_by_path = {path: dict(repair) for path, repair in (repairs_by_path or {}).items()}
        self.derived_owner = ""
        self.last_failure = ""
        self.searched: set[str] = set()

    def capture_prompts(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.prompt_dir = folder

    # -- calls ------------------------------------------------------------------

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.n += 1
        return {"id": f"call-{self.n}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}

    def _step(self, prefix: str, intent: str, arguments: dict[str, Any], expect: tuple[str, Any] | None = None):
        self.n += 1
        self.expect = expect
        return self._call("code__task__step", {"task_id": self.task_id, "step_id": f"{prefix}-{self.n}",
                                               "intent": intent, "arguments": arguments})

    def _identify(self, path: str, reason: str):
        self.log.append(f"identify:{path}")
        return self._call("code__task__identify", {"task_id": self.task_id, "path": path, "line": 1, "reason": reason})

    def _unit(self, pid: str) -> str:
        repair = next((r for r in self.plan if r["pid"] == pid), {})
        return str(repair.get("unit") or pid)

    def _land(self, pid: str, how: str) -> None:
        if pid not in self.landed:
            self.landed.append(pid)
            self.log.append(f"landed:{pid}{how}")
        self.attempted.discard(pid)

    # -- reading the boundary ---------------------------------------------------

    def _settle(self, facts: dict[str, Any]) -> str:
        """The newest task observation is the outcome of the one step this model last issued. Returns the
        stage the documented stage machine gives that outcome, used only when the line was clipped before
        its plan (a long turn shares one observation window among many results)."""
        if self.expect is None:
            return ""
        kind, ref, issued_at = self.expect
        self.expect = None
        status = facts["status"]
        pending = facts["pending"]
        if kind == "search":
            self._settle_search(facts, ref)
            return issued_at
        if kind == "mutation":
            if status == "executed" or (facts["executed"] and facts["mode"] == "tool_executed"):
                self._land(ref, "")
                unit = self._unit(ref)
                part_applied = any(self._unit(r["pid"]) == unit and r["pid"] not in self.landed for r in self.plan)
                return "mutate" if part_applied else "narrow_test"
            self.attempted.discard(ref)
            if status == "stale_base" or facts["stale_refusal"]:
                self.stale.add(ref)
                self.log.append(f"refused-stale:{ref}")
            elif status == "checkpoint_required" or (pending is not None and ref in pending and facts["checkpoint_signal"]):
                self.checkpoint_owed = True
                self.log.append(f"refused-checkpoint:{ref}")
            elif pending is not None and ref not in pending:
                # Nothing ran and the repair is no longer approved and waiting: its approval was withdrawn.
                self.stale.add(ref)
                self.log.append(f"refused-withdrawn:{ref}")
            else:
                self.log.append(f"refused-{status or facts['mode'] or 'unreadable'}:{ref}")
            return issued_at
        if facts["ok"] is False or status == "stage_violation" or (facts["mode"] == "tool_failed" and not facts["executed"]):
            # Refused, or never completed: the step verified nothing, whatever failure its line still carries.
            self.log.append(f"check-refused:{status or 'unreadable'}")
            return issued_at
        stage = facts["stage"]
        checked_stage = ref[1]
        if not stage:
            if facts["failed_verification"]:
                stage = checked_stage
            elif checked_stage == "narrow_test":
                stage = "cumulative"
            elif checked_stage == "cumulative":
                waiting = pending if pending is not None else [
                    r["pid"] for r in self.plan if r["pid"] not in self.landed and r["pid"] not in self.stale]
                stage = "mutate" if waiting else "inspect_diff"
        self.log.append(f"check:{checked_stage}->{stage or '?'}{':failed' if facts['failed_verification'] else ''}")
        if stage in {"mutate", "inspect_diff"} or facts["failed_verification"]:
            self.checkpoint_owed = False
        return stage

    def _settle_search(self, facts: dict[str, Any], symbol: str) -> None:
        """The file that DEFINES the searched symbol, from the visible match rows: a check or test file that only uses
        it, an assertion, an import and a prose mention are all passed over."""
        escaped = re.escape(symbol)
        for _line, path, snippet in facts["matches"]:
            if re.search(r"(?:^|/)(?:check|test)s?[^/]*(?:/|$)", path) or re.search(r"\bassert\b|require\(|import ", snippet):
                continue
            if re.search(rf"(?:exports\.|function\s+|def\s+|class\s+|const\s+|let\s+|var\s+){escaped}\b", snippet) \
                    or re.search(rf"\b{escaped}\s*[:=]", snippet):
                self.derived_owner = path
                self.log.append(f"owner-from-search:{path}")
                return
        self.log.append(f"search-found-no-definition:{symbol}")

    def _derive_owner(self, facts: dict[str, Any], prompt: str, purpose: str) -> tuple[str, Any]:
        """The next move toward the owning file, decided only from the failure evidence on the newest line or from a
        search this model already ran: ("owner", path), ("step", call) or ("stuck", purpose)."""
        if self.derived_owner:
            owner, self.derived_owner = self.derived_owner, ""
            return "owner", owner
        failure = facts["evidence_failure"]
        visible = bool(failure) and failure in prompt
        self.log.append(f"evidence-visible:{visible}")
        if visible:
            self.last_failure = failure
        call = re.fullmatch(r"([A-Za-z_$][\w$]*)\(", facts["evidence_query"] or "") if visible else None
        if call and call.group(1) not in self.searched:
            self.searched.add(call.group(1))
            self.log.append(f"search:{call.group(1)}")
            return "step", self._step("search", "workspace.search_text", {"query": call.group(1), "path": "."},
                                      expect=("search", call.group(1), self.last_stage))
        for token in re.findall(r"[\w./-]+\.(?:js|mjs|cjs|ts|py)\b", failure if visible else ""):
            if not re.search(r"(?:^|/)(?:check|test)s?[^/]*$", token):
                self.log.append(f"owner-from-named-file:{token}")
                return "owner", token
        self.log.append(f"owner-not-derived:{purpose}")
        return "stuck", purpose

    def _rename_after_conflict(self, facts: dict[str, Any]) -> None:
        """A refused proposal that names `differences` was a revision under a recorded id: preview it again
        under a new id (the runtime's suggestion when the clipped line still shows it)."""
        old = self.last_proposed[-1] if self.last_proposed else ""
        if not old:
            return
        new = facts["suggested"] or f"{old}-2"
        for entry in reversed(self.plan):
            if entry["pid"] == old:
                entry["pid"] = new
                break
        self.last_proposed = []
        self.log.append(f"conflict:{old}->{new}")

    def _recovery(self, facts: dict[str, Any], prompt: str):
        for index, recovery in enumerate(self.recoveries):
            if index in self.used_recoveries:
                continue
            if recovery["on"] == "verification_failed" and facts["failed_verification"] and recovery.get("derive_owner"):
                kind, value = self._derive_owner(facts, prompt, "the check still fails")
                if kind == "step":
                    return [value]
                self.used_recoveries.add(index)
                if kind == "stuck" or value not in self.repairs_by_path:
                    self.log.append(f"no-repair-for:{value}")
                    return [self._call("respond__direct", {"message": "I could not find the file behind this failure."})]
                self.plan.append(dict(self.repairs_by_path[value]))
                self.log.append(f"recover:{value}")
                return [self._identify(value, self.last_failure or "the check still fails in this file"),
                        self._step("reread", "workspace.read_file", {"path": value})]
            if recovery["on"] == "verification_failed" and facts["failed_verification"]:
                self.used_recoveries.add(index)
                marker = recovery.get("marker")
                if marker:
                    self.log.append(f"marker-visible:{marker in prompt}")
                for repair in recovery["repairs"]:
                    entry = dict(repair)
                    if entry.pop("reuse", False):
                        # This model revises its repair under the id it already used.
                        self.plan = [r for r in self.plan if r["pid"] != entry["pid"]]
                        self.proposed.discard(entry["pid"])
                    self.plan.append(entry)
                self.log.append(f"recover:{recovery['identify']['path']}")
                return [self._identify(recovery["identify"]["path"], recovery["identify"]["reason"]),
                        *(self._step("reread", "workspace.read_file", {"path": p}) for p in recovery.get("reads", []))]
            if recovery["on"] == "stale_base" and recovery["pid"] in self.stale:
                self.used_recoveries.add(index)
                self.plan.extend(dict(r) for r in recovery["repairs"])
                self.log.append(f"rebase:{recovery['pid']}")
                return [self._step("reread", "workspace.read_file", {"path": p}) for p in recovery.get("reads", [])]
        return None

    def reply(self, prompt: str) -> Any:
        self.replies += 1
        if self.prompt_dir is not None:
            (self.prompt_dir / f"{self.replies:03d}.txt").write_text(prompt[-12000:], encoding="utf-8")
        try:
            return self._decide(prompt)
        finally:
            if self.prompt_dir is not None:
                (self.prompt_dir / "model-log.json").write_text(json.dumps(self.log, indent=1), encoding="utf-8")

    def _decide(self, prompt: str) -> Any:
        if not self.task_id:
            opened = re.findall(r"Opened code task (ct-[0-9a-f]{12})", prompt)
            if not opened:
                self.log.append("no-task")
                return self._call("respond__direct", {"message": "No coding task was opened for this request."})
            self.task_id = opened[-1]
        lines = [line.strip() for line in prompt.splitlines()
                 if line.strip().startswith("- {") and '"intent": "code.task.' in line]
        newest = lines[-1] if lines else ""
        facts = _facts(newest)
        stage = facts["stage"]
        if not lines:
            # A new user turn carries none of the previous turn's observations. The stage is still visible in
            # the conversation ("is still at <stage>"), else remembered.
            visible = re.findall(r"ct-[0-9a-f]{12} (?:is still at|at stage) ([a-z_]+)", prompt)
            stage = visible[-1] if visible else self.last_stage
            if self.turn_has_observations:
                self.turn_has_observations = False
                self.checked.clear()
                self.log.append(f"new-turn:{stage or '?'}")
                if self.expect is not None and self.expect[0] == "mutation":
                    pid, issued_at = self.expect[1], self.expect[2]
                    if stage == "narrow_test" and issued_at != "narrow_test":
                        # The previous turn ended right after this write ran: only a landed repair opens a
                        # validation checkpoint from another stage.
                        self._land(pid, "(turn-ended)")
                    else:
                        self.attempted.discard(pid)
                        self.log.append(f"unsettled:{pid}")
                self.expect = None
        else:
            self.turn_has_observations = True
            if newest != self.last_line:
                self.last_line = newest
                inferred = self._settle(facts)
                if facts["conflict"]:
                    self._rename_after_conflict(facts)
                if not stage and inferred:
                    stage = inferred
                    self.log.append(f"stage-inferred:{stage}")
        stage = stage or self.last_stage
        self.last_stage = stage
        revision = len(self.landed)
        if "repro" not in self.done:
            self.done.add("repro")
            self.log.append("repro")
            return self._step("repro", "workspace.run_tests", {"command": self.repro})
        if "identified" not in self.done:
            if self.owner is None:
                kind, value = self._derive_owner(facts, prompt, "the reproduced failure")
                if kind == "step":
                    return value
                if kind == "stuck" or value not in self.repairs_by_path:
                    self.log.append(f"no-repair-for:{value}")
                    return self._call("respond__direct", {"message": "I could not find the file behind this failure."})
                self.owner = value
                self.plan.append(dict(self.repairs_by_path[value]))
                self.reads = [value]
            self.done.add("identified")
            return [self._identify(self.owner, self.last_failure or "the reproduced check fails in this file"),
                    *(self._step("read", "workspace.read_file", {"path": p}) for p in self.reads)]
        recovery = self._recovery(facts, prompt)
        if recovery is not None:
            return recovery
        unproposed = [r for r in self.plan if r["pid"] not in self.proposed]
        if unproposed:
            self.proposed.update(r["pid"] for r in unproposed)
            self.last_proposed = [r["pid"] for r in unproposed]
            self.log.append("propose:" + ",".join(r["pid"] for r in unproposed))
            return [self._call("code__task__propose", {
                "task_id": self.task_id, "proposal_id": r["pid"], "intent": "workspace.write_file",
                "arguments": {"path": r["path"], "content": r["content"]}, "rationale": r["rationale"],
                **({"unit": r["unit"]} if r.get("unit") else {}),
            }) for r in unproposed]
        unapproved = [r for r in self.plan if r["pid"] not in self.approved]
        if unapproved:
            self.approved.update(r["pid"] for r in unapproved)
            self.log.append("approve:" + ",".join(r["pid"] for r in unapproved))
            return [self._call("code__task__approve", {"task_id": self.task_id, "proposal_id": r["pid"]})
                    for r in unapproved]
        part_applied = [u for u in {self._unit(p) for p in self.landed}
                        if any(self._unit(r["pid"]) == u and r["pid"] not in self.landed for r in self.plan)]
        if self.check_mid_unit and part_applied and "mid-unit-check" not in self.done:
            self.done.add("mid-unit-check")
            self.log.append("mid-unit-check")
            return self._step("check", "workspace.run_tests", {"command": self.full}, expect=("check", (revision, "mid-unit"), stage))
        if facts["pending"] is not None:
            # The runtime's own list of approved repairs still waiting is the truth when it is visible.
            remaining = [r for r in self.plan if r["pid"] in facts["pending"] and r["pid"] not in self.stale
                         and r["pid"] not in self.attempted]
        else:
            remaining = [r for r in self.plan if r["pid"] not in self.landed and r["pid"] not in self.attempted
                         and r["pid"] not in self.stale]
        if stage in {"narrow_test", "cumulative"} and (self.checkpoint_owed or not remaining or not lines) \
                and (revision, stage) not in self.checked:
            self.checked.add((revision, stage))
            unit = self._unit(self.landed[-1]) if self.landed else ""
            command = self.focused.get(unit, self.full) if stage == "narrow_test" else self.full
            self.log.append(f"run:{stage}:r{revision}")
            return self._step("check", "workspace.run_tests", {"command": command}, expect=("check", (revision, stage), stage))
        if remaining and stage in {"mutate", "narrow_test", "cumulative"} and not self.checkpoint_owed:
            repair = remaining[0]
            self.attempted.add(repair["pid"])
            self.log.append(f"write:{repair['pid']}")
            return self._step("write", "workspace.write_file", {"path": repair["path"], "content": repair["content"]},
                              expect=("mutation", repair["pid"], stage))
        if stage == "inspect_diff" and "diff" not in self.done:
            # One native batch: the diff advances the task to `report`, and the report is seated at
            # `inspect_diff` already, so both run in this round in provider order.
            self.done.update({"diff", "report"})
            self.log.append("diff+report")
            return [self._step("diff", "workspace.git_diff", {}),
                    self._call("code__task__report", {"task_id": self.task_id})]
        if stage == "report" and "report" not in self.done:
            self.done.add("report")
            self.log.append("report")
            return self._call("code__task__report", {"task_id": self.task_id})
        self.log.append(f"final:{stage or '?'}")
        if '"code_task_verdict": "completed"' in prompt:
            return self._call("respond__direct", {"message": "The coding task completed. The published report is the verification."})
        return self._call("respond__direct", {"message": "The repair is not complete; the task journal says what remains."})


def _post(base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(f"{base_url}{path}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
                      method="POST")
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode())


FOLLOW_UP = "next step of the task"


def _converse(rig: dict[str, Any], session: str, demand: str, model: ObservedRepairModel, *, before_allow=None,
              max_approvals: int = 8, max_follow_ups: int = 3) -> dict[str, Any]:
    """One same-session conversation. The demand; every operator Allow through /api/mode followed by a
    resume carrying that approval's token; and, only while the task is unfinished with nothing pending,
    a short follow-up message -- one user turn holds a bounded number of model rounds, and an approval's
    resume continues that same turn, so a repair with several cycles continues on the next message."""
    model.capture_prompts(Path(rig["store_dir"]).parent / f"model-prompts-{session}")
    daemon = rig["daemon"]
    transcript: list[dict[str, Any]] = []
    approvals = 0
    follow_ups = 0
    reply: dict[str, Any] = {}
    message = demand
    while True:
        reply = daemon.chat(message, session_id=session, mode="auto", timeout=900.0)
        transcript.append({"user": message, "reply": _reply_text(reply)})
        while approvals < max_approvals and _task_verdict(rig["store_dir"]) not in {"completed", "cancelled", "rolled_back"}:
            pending = pending_session_approvals(rig["home"], session)
            if not pending:
                break
            if before_allow is not None:
                before_allow(approvals)
            token = ""
            for entry in pending:
                token = str(entry["approval_id"])
                resolved = _post(daemon.base_url, "/api/mode", {"op": "resolve_approval", "session_id": session,
                                                               "approval_id": token, "decision": "allow"})
                assert resolved.get("ok") is True, resolved
            approvals += 1
            reply = daemon.chat("continue the approved repair", session_id=session, mode="auto",
                                approval_token=token, timeout=900.0)
            transcript.append({"user": "continue the approved repair", "reply": _reply_text(reply), "approval": token})
        if _task_verdict(rig["store_dir"]) in {"completed", "cancelled", "rolled_back"} or follow_ups >= max_follow_ups:
            break
        follow_ups += 1
        message = FOLLOW_UP
    return {"reply": reply, "text": _reply_text(reply), "transcript": transcript, "approvals": approvals,
            "follow_ups": follow_ups}


def _verifications(store_dir: Path) -> list[tuple[str, str, bool, int, bool]]:
    return [(v["stage"], v["unit"], v["success"], v["revision"], v["current"]) for v in _journal(store_dir)["verifications"]]


def _effects(store_dir: Path) -> list[tuple[str, str]]:
    """The ordered writes and checks the task admitted or refused, from the journal."""
    journal = _journal(store_dir)
    rows = []
    for step_id in journal["step_order"]:
        step = journal["steps"][step_id]
        if step["intent"] in {"workspace.write_file", "workspace.run_tests"}:
            target = step["arguments"].get("path") or step["arguments"].get("command")
            rows.append((f"{step['intent'].split('.')[-1]}:{target}", "ok" if step["ok"] else step["status"]))
    return rows


def _assert_completed(outcome: dict[str, Any], rig: dict[str, Any], model: ObservedRepairModel) -> dict[str, Any]:
    store_dir = rig["store_dir"]
    tasks = sorted((Path(store_dir) / "code_tasks").glob("ct-*.json"))
    assert len(tasks) == 1, (tasks, model.log, outcome["transcript"])
    journal = _journal(store_dir)
    assert "completed." in outcome["text"], (outcome["text"][:600], journal["stage"], model.log)
    assert "incomplete" not in outcome["text"].lower(), outcome["text"][:600]
    assert journal["stage"] == "report" and _task_verdict(store_dir) == "completed", (journal["stage"], model.log)
    return journal


# ---------------------------------------------------------------------------
# Two independent repairs known up front: validated between units
# ---------------------------------------------------------------------------

INDEPENDENT_FILES = {
    "package.json": '{\n  "name": "shipping-totals",\n  "private": true,\n  "scripts": {"test": "node suite.js"}\n}\n',
    "pricing.js": "exports.total = (subtotal, tax) => subtotal - tax;\n",
    "labels.js": "exports.slug = (words) => words.join('_');\n",
    "check_pricing.js": ("const assert = require('assert');\nconst { total } = require('./pricing.js');\n"
                         "assert.strictEqual(total(40, 8), 48, 'pricing.js total(40, 8) must be 48');\n"
                         "console.log('pricing ok');\n"),
    "check_labels.js": ("const assert = require('assert');\nconst { slug } = require('./labels.js');\n"
                        "assert.strictEqual(slug(['next', 'day']), 'next-day', 'labels.js slug must join with a hyphen');\n"
                        "console.log('labels ok');\n"),
    "suite.js": "require('./check_pricing.js');\nrequire('./check_labels.js');\nconsole.log('suite ok');\n",
    "OPERATOR_NOTES.md": "operator notes: not part of any repair\n",
}
PRICING_FIXED = "exports.total = (subtotal, tax) => subtotal + tax;\n"
LABELS_FIXED = "exports.slug = (words) => words.join('-');\n"


def test_served_two_known_independent_repairs_are_validated_between_units(served_factory) -> None:
    model = ObservedRepairModel(
        repro="node suite.js", owner="pricing.js", reads=["pricing.js", "labels.js"],
        repairs=[
            {"pid": "pricing", "path": "pricing.js", "content": PRICING_FIXED,
             "rationale": "Owner pricing.js: total subtracts tax; check_pricing requires the sum."},
            {"pid": "labels", "path": "labels.js", "content": LABELS_FIXED,
             "rationale": "Owner labels.js: slug joins with an underscore; check_labels requires a hyphen."},
        ],
        focused={"pricing": "node check_pricing.js", "labels": "node check_labels.js"}, full="node suite.js",
    )
    rig = _boot(served_factory, INDEPENDENT_FILES, model)
    outcome = _converse(rig, "served-independent-units",
                        "Fix the bugs in this project, run its tests after each fix, and explain what you changed.", model)
    journal = _assert_completed(outcome, rig, model)
    workspace: Path = rig["workspace"]
    assert (workspace / "pricing.js").read_text() == PRICING_FIXED
    assert (workspace / "labels.js").read_text() == LABELS_FIXED
    for name in ("OPERATOR_NOTES.md", "check_pricing.js", "check_labels.js", "suite.js"):
        assert (workspace / name).read_text() == INDEPENDENT_FILES[name], name
    # The naive second write was refused until the first unit was validated, then ran through its own approval.
    assert "refused-checkpoint:labels" in model.log, model.log
    assert _verifications(rig["store_dir"]) == [
        ("narrow_test", "pricing", True, 1, True),
        ("cumulative", "pricing", False, 1, True),   # recorded as it is: the second defect is still present
        ("narrow_test", "labels", True, 2, True),
        ("cumulative", "labels", True, 2, True),
    ], model.log
    assert _effects(rig["store_dir"]) == [
        ("run_tests:node suite.js", "ok"),
        ("write_file:pricing.js", "ok"),
        ("write_file:labels.js", "checkpoint_required"),
        ("run_tests:node check_pricing.js", "ok"),
        ("run_tests:node suite.js", "ok"),
        ("write_file:labels.js", "ok"),
        ("run_tests:node check_labels.js", "ok"),
        ("run_tests:node suite.js", "ok"),
    ], model.log
    assert sorted(journal["git_diff_paths"]) == ["labels.js", "pricing.js"]
    assert {p["proposal_id"]: p["unit"] for p in journal["proposals"].values()} == {"pricing": "pricing", "labels": "labels"}
    # The operator was asked about the refused write too: permission is decided before the task law.
    assert outcome["approvals"] == 3, (outcome["approvals"], model.log)


# ---------------------------------------------------------------------------
# A genuinely indivisible change: one declared unit, validated after all of it
# ---------------------------------------------------------------------------

COUPLED_FILES = {
    "package.json": '{\n  "name": "parcel-weights",\n  "private": true,\n  "scripts": {"test": "node check.js"}\n}\n',
    "units.js": "exports.kgToLb = (kg) => kg * 2;\n",
    "shipping.js": "const { kgToLb } = require('./units.js');\nexports.parcelPounds = (kg) => kgToLb(kg);\n",
    "check.js": ("const assert = require('assert');\nconst { kilogramsToPounds } = require('./units.js');\n"
                 "const { parcelPounds } = require('./shipping.js');\n"
                 "assert.strictEqual(kilogramsToPounds(10), 22.05, 'units.js must export kilogramsToPounds');\n"
                 "assert.strictEqual(parcelPounds(3), 6.61, 'shipping.js must use kilogramsToPounds');\n"
                 "console.log('parcel weights ok');\n"),
    "CARRIER_NOTES.md": "carrier notes: operator owned\n",
}
UNITS_FIXED = "exports.kilogramsToPounds = (kg) => Math.round(kg * 2.20462 * 100) / 100;\n"
SHIPPING_FIXED = "const { kilogramsToPounds } = require('./units.js');\nexports.parcelPounds = (kg) => kilogramsToPounds(kg);\n"


def test_served_coupled_fixture_breaks_when_either_half_lands_alone(tmp_path) -> None:
    """Evidence the served unit below is indivisible by construction, not by declaration."""
    node = shutil.which("node")
    assert node, "node is required for the served coding fixtures"
    results = {}
    for label, units, shipping in (("rename-only", UNITS_FIXED, COUPLED_FILES["shipping.js"]),
                                   ("caller-only", COUPLED_FILES["units.js"], SHIPPING_FIXED),
                                   ("both", UNITS_FIXED, SHIPPING_FIXED)):
        folder = tmp_path / label
        folder.mkdir()
        for name, text in COUPLED_FILES.items():
            (folder / name).write_text(text)
        (folder / "units.js").write_text(units)
        (folder / "shipping.js").write_text(shipping)
        run = subprocess.run([node, "check.js"], cwd=folder, capture_output=True, text=True, timeout=60)
        results[label] = run.returncode
    assert results == {"rename-only": 1, "caller-only": 1, "both": 0}, results


def test_served_dependency_coupled_unit_lands_whole_then_validates(served_factory) -> None:
    model = ObservedRepairModel(
        repro="node check.js", owner="units.js", reads=["units.js", "shipping.js"],
        repairs=[
            {"pid": "rename", "path": "units.js", "content": UNITS_FIXED, "unit": "rename-conversion",
             "rationale": "Owner units.js: check.js requires kilogramsToPounds with pound precision."},
            {"pid": "caller", "path": "shipping.js", "content": SHIPPING_FIXED, "unit": "rename-conversion",
             "rationale": "Owner shipping.js: its only import must follow the rename in the same unit."},
        ],
        focused={"rename-conversion": "node check.js"}, full="node check.js", check_mid_unit=True,
    )
    rig = _boot(served_factory, COUPLED_FILES, model)
    outcome = _converse(rig, "served-coupled-unit",
                        "Fix the failing check in this project by renaming the weight conversion and updating its caller.",
                        model)
    journal = _assert_completed(outcome, rig, model)
    workspace: Path = rig["workspace"]
    assert (workspace / "units.js").read_text() == UNITS_FIXED
    assert (workspace / "shipping.js").read_text() == SHIPPING_FIXED
    assert (workspace / "check.js").read_text() == COUPLED_FILES["check.js"]
    assert (workspace / "CARRIER_NOTES.md").read_text() == COUPLED_FILES["CARRIER_NOTES.md"]
    assert "check-refused:stage_violation" in model.log, model.log
    assert _effects(rig["store_dir"]) == [
        ("run_tests:node check.js", "ok"),
        ("write_file:units.js", "ok"),
        ("run_tests:node check.js", "stage_violation"),  # half a unit is never validated
        ("write_file:shipping.js", "ok"),
        ("run_tests:node check.js", "ok"),
        ("run_tests:node check.js", "ok"),
    ], model.log
    assert _verifications(rig["store_dir"]) == [("narrow_test", "rename-conversion", True, 2, True),
                                                ("cumulative", "rename-conversion", True, 2, True)]
    assert journal["checkpoint"]["unit"] == "rename-conversion" and journal["checkpoint"]["revision"] == 2
    assert sorted(journal["git_diff_paths"]) == ["shipping.js", "units.js"]
    assert outcome["approvals"] == 2, (outcome["approvals"], model.log)


# ---------------------------------------------------------------------------
# A wrong first repair recovers in the same task and is verified again, narrow and full
# ---------------------------------------------------------------------------

WRONG_FIRST_FILES = {
    "package.json": '{\n  "name": "thermo",\n  "private": true,\n  "scripts": {"test": "node check_all.js"}\n}\n',
    "temperature.js": "exports.toFahrenheit = (c) => c * 9 / 5 - 32;\n",
    "check_boiling.js": ("const assert = require('assert');\nconst { toFahrenheit } = require('./temperature.js');\n"
                         "assert.strictEqual(toFahrenheit(100), 212, 'boiling point must be 212F');\nconsole.log('boiling ok');\n"),
    "check_all.js": ("const assert = require('assert');\nconst { toFahrenheit } = require('./temperature.js');\n"
                     "assert.strictEqual(toFahrenheit(100), 212);\nassert.strictEqual(toFahrenheit(0), 32);\n"
                     "assert.strictEqual(toFahrenheit(-40), -40);\nconsole.log('all conversions ok');\n"),
}
TEMPERATURE_WRONG = "exports.toFahrenheit = (c) => c * 5 / 9 + 32;\n"
TEMPERATURE_FIXED = "exports.toFahrenheit = (c) => c * 9 / 5 + 32;\n"


def test_served_wrong_first_repair_is_revised_and_verified_narrow_and_full(served_factory) -> None:
    model = ObservedRepairModel(
        repro="node check_all.js", owner="temperature.js", reads=["temperature.js"],
        repairs=[{"pid": "first-attempt", "path": "temperature.js", "content": TEMPERATURE_WRONG,
                  "rationale": "Owner temperature.js: the conversion factor is inverted."}],
        focused={"first-attempt": "node check_boiling.js", "corrected": "node check_boiling.js"}, full="node check_all.js",
        recoveries=[{"on": "verification_failed", "identify": {"path": "temperature.js",
                                                                "reason": "the first repair still fails the boiling check"},
                     "reads": ["temperature.js"],
                     "repairs": [{"pid": "corrected", "path": "temperature.js", "content": TEMPERATURE_FIXED,
                                  "rationale": "Owner temperature.js: multiply by 9/5 and add 32."}]}],
    )
    rig = _boot(served_factory, WRONG_FIRST_FILES, model)
    outcome = _converse(rig, "served-wrong-first-complete", "Fix the temperature bug in this project and explain the change.",
                        model)
    journal = _assert_completed(outcome, rig, model)
    assert (rig["workspace"] / "temperature.js").read_text() == TEMPERATURE_FIXED
    assert (rig["workspace"] / "check_all.js").read_text() == WRONG_FIRST_FILES["check_all.js"]
    assert (rig["workspace"] / "check_boiling.js").read_text() == WRONG_FIRST_FILES["check_boiling.js"]
    assert _verifications(rig["store_dir"]) == [
        ("narrow_test", "first-attempt", False, 1, True),
        ("narrow_test", "corrected", True, 2, True),
        ("cumulative", "corrected", True, 2, True),
    ], model.log
    assert journal["proposals"]["first-attempt"]["consumed_by"] and journal["proposals"]["corrected"]["consumed_by"]
    assert len(journal["diagnoses"]) == 1 and journal["defect"]["reason"].startswith("the first repair")
    assert "recover:temperature.js" in model.log
    assert _effects(rig["store_dir"]) == [
        ("run_tests:node check_all.js", "ok"),
        ("write_file:temperature.js", "ok"),
        ("run_tests:node check_boiling.js", "ok"),
        ("write_file:temperature.js", "ok"),
        ("run_tests:node check_boiling.js", "ok"),
        ("run_tests:node check_all.js", "ok"),
    ], model.log
    assert outcome["approvals"] == 2, (outcome["approvals"], model.log)


# ---------------------------------------------------------------------------
# A second defect nobody knew about is found by the full check after the first repair
# ---------------------------------------------------------------------------

SECOND_DEFECT_FILES = {
    "package.json": '{\n  "name": "stockroom",\n  "private": true,\n  "scripts": {"test": "node suite.js"}\n}\n',
    "inventory.js": "exports.restock = (onHand, delivered) => onHand - delivered;\n",
    "format.js": "exports.formatPrice = (amount) => '$' + amount;\n",
    "check_inventory.js": ("const assert = require('assert');\nconst { restock } = require('./inventory.js');\n"
                           "assert.strictEqual(restock(12, 5), 17, 'inventory.js restock must add deliveries');\n"
                           "console.log('inventory ok');\n"),
    "check_format.js": ("const assert = require('assert');\nconst { formatPrice } = require('./format.js');\n"
                        "assert.strictEqual(formatPrice(5), '$5.00', 'format.js formatPrice(5) must be $5.00');\n"
                        "console.log('format ok');\n"),
    "suite.js": "require('./check_inventory.js');\nrequire('./check_format.js');\nconsole.log('suite ok');\n",
    "STOCK_NOTES.md": "stock notes: keep\n",
}
INVENTORY_FIXED = "exports.restock = (onHand, delivered) => onHand + delivered;\n"
FORMAT_FIXED = "exports.formatPrice = (amount) => '$' + amount.toFixed(2);\n"


def _search_queries(store_dir: Path) -> list[str]:
    journal = _journal(store_dir)
    return [journal["steps"][sid]["arguments"].get("query") for sid in journal["step_order"]
            if journal["steps"][sid]["intent"] == "workspace.search_text" and journal["steps"][sid]["executed"]]


def _assert_owners_came_from_evidence(model: ObservedRepairModel, owner_decisions: list[str], queries: list[str],
                                      store_dir: Path) -> None:
    """Every owner decision was made from failure evidence the model could see, by the route it logged."""
    assert [entry for entry in model.log if entry.startswith("owner-from-")] == owner_decisions, model.log
    assert model.log.count("evidence-visible:True") == len(owner_decisions), model.log
    assert "evidence-visible:False" not in model.log and not any(e.startswith("owner-not-derived") for e in model.log)
    assert _search_queries(store_dir) == queries, _search_queries(store_dir)


def test_served_second_defect_found_by_the_full_check_is_repaired_and_verified(served_factory) -> None:
    model = ObservedRepairModel(
        repro="node check_inventory.js", owner=None, reads=[], repairs=[],
        repairs_by_path={
            "inventory.js": {"pid": "restock", "path": "inventory.js", "content": INVENTORY_FIXED,
                             "rationale": "Owner inventory.js: deliveries must be added to stock."},
            "format.js": {"pid": "price-format", "path": "format.js", "content": FORMAT_FIXED,
                          "rationale": "Owner format.js: prices need two decimals."},
        },
        focused={"restock": "node check_inventory.js", "price-format": "node check_format.js"}, full="node suite.js",
        recoveries=[{"on": "verification_failed", "derive_owner": True}],
    )
    rig = _boot(served_factory, SECOND_DEFECT_FILES, model)
    outcome = _converse(rig, "served-second-defect-complete",
                        "Repair the failing stock behavior in this project and describe the changes.", model)
    journal = _assert_completed(outcome, rig, model)
    workspace: Path = rig["workspace"]
    assert (workspace / "inventory.js").read_text() == INVENTORY_FIXED
    assert (workspace / "format.js").read_text() == FORMAT_FIXED
    for name in ("STOCK_NOTES.md", "suite.js", "check_format.js", "check_inventory.js"):
        assert (workspace / name).read_text() == SECOND_DEFECT_FILES[name], name
    assert _verifications(rig["store_dir"]) == [
        ("narrow_test", "restock", True, 1, True),
        ("cumulative", "restock", False, 1, True),
        ("narrow_test", "price-format", True, 2, True),
        ("cumulative", "price-format", True, 2, True),
    ], model.log
    assert journal["defect"]["path"] == "format.js" and [d["path"] for d in journal["diagnoses"]] == ["inventory.js"]
    assert sorted(journal["git_diff_paths"]) == ["format.js", "inventory.js"]
    # The first failure names its file; the second names only `formatPrice(5)`, which the model searched.
    _assert_owners_came_from_evidence(model, ["owner-from-named-file:inventory.js", "owner-from-search:format.js"],
                                      ["formatPrice"], rig["store_dir"])
    assert outcome["approvals"] == 2, (outcome["approvals"], model.log)


# ---------------------------------------------------------------------------
# A different layout and different data: owners reachable only through the evidence
# ---------------------------------------------------------------------------

LEDGER_FILES = {
    "package.json": '{\n  "name": "ledger",\n  "private": true,\n  "scripts": {"test": "node test/all.js"}\n}\n',
    "lib/stock/restock.js": "exports.restock = (onHand, delivered) => onHand - delivered;\n",
    "lib/money/to_cents.js": "exports.toCents = (dollars) => dollars * 10;\n",
    "lib/money/format_label.js": "exports.formatLabel = (name) => name.trim();\n",
    "docs/money.md": "toCents converts dollars to cents for invoices.\n",
    "test/check_restock.js": ("const assert = require('assert');\nconst { restock } = require('../lib/stock/restock.js');\n"
                              "assert.strictEqual(restock(12, 5), 17, 'restock(12, 5) must be 17');\nconsole.log('restock ok');\n"),
    "test/check_cents.js": ("const assert = require('assert');\nconst { toCents } = require('../lib/money/to_cents.js');\n"
                            "assert.strictEqual(toCents(3), 300, 'toCents(3) must be 300 cents');\nconsole.log('cents ok');\n"),
    "test/all.js": "require('./check_restock.js');\nrequire('./check_cents.js');\nconsole.log('all ok');\n",
}
LEDGER_RESTOCK_FIXED = "exports.restock = (onHand, delivered) => onHand + delivered;\n"
LEDGER_CENTS_FIXED = "exports.toCents = (dollars) => dollars * 100;\n"


def test_served_second_defect_in_a_nested_layout_is_found_from_the_failure_evidence(served_factory) -> None:
    """Failure messages that name only a symbol, owners two directories down, a decoy module and a prose mention:
    the owners are reachable only by reading each failure and searching the call it names."""
    model = ObservedRepairModel(
        repro="node test/all.js", owner=None, reads=[], repairs=[],
        repairs_by_path={
            "lib/stock/restock.js": {"pid": "restock-sum", "path": "lib/stock/restock.js", "content": LEDGER_RESTOCK_FIXED,
                                     "rationale": "Owner lib/stock/restock.js: a delivery adds to stock on hand."},
            "lib/money/to_cents.js": {"pid": "cents", "path": "lib/money/to_cents.js", "content": LEDGER_CENTS_FIXED,
                                      "rationale": "Owner lib/money/to_cents.js: a dollar is one hundred cents."},
        },
        focused={"restock-sum": "node test/check_restock.js", "cents": "node test/check_cents.js"}, full="node test/all.js",
        recoveries=[{"on": "verification_failed", "derive_owner": True}],
    )
    rig = _boot(served_factory, LEDGER_FILES, model)
    outcome = _converse(rig, "served-nested-ledger",
                        "Fix the bugs in this project, run its tests after each fix, and explain what you changed.", model)
    journal = _assert_completed(outcome, rig, model)
    workspace: Path = rig["workspace"]
    assert (workspace / "lib/stock/restock.js").read_text() == LEDGER_RESTOCK_FIXED
    assert (workspace / "lib/money/to_cents.js").read_text() == LEDGER_CENTS_FIXED
    for name in ("lib/money/format_label.js", "docs/money.md", "test/check_restock.js", "test/check_cents.js", "test/all.js"):
        assert (workspace / name).read_text() == LEDGER_FILES[name], name
    assert _verifications(rig["store_dir"]) == [
        ("narrow_test", "restock-sum", True, 1, True),
        ("cumulative", "restock-sum", False, 1, True),
        ("narrow_test", "cents", True, 2, True),
        ("cumulative", "cents", True, 2, True),
    ], model.log
    assert journal["defect"]["path"] == "lib/money/to_cents.js"
    assert [d["path"] for d in journal["diagnoses"]] == ["lib/stock/restock.js"]
    assert sorted(journal["git_diff_paths"]) == ["lib/money/to_cents.js", "lib/stock/restock.js"]
    _assert_owners_came_from_evidence(
        model, ["owner-from-search:lib/stock/restock.js", "owner-from-search:lib/money/to_cents.js"],
        ["restock", "toCents"], rig["store_dir"])
    assert outcome["approvals"] == 2, (outcome["approvals"], model.log)


# ---------------------------------------------------------------------------
# An edit by someone else after approval: refused, re-read, rebased, verified
# ---------------------------------------------------------------------------

STALE_FILES = {
    "package.json": '{\n  "name": "checkout",\n  "private": true,\n  "scripts": {"test": "node check_discount.js"}\n}\n',
    "discount.js": "exports.applyDiscount = (price, percent) => price + price * percent / 100;\n",
    "check_discount.js": ("const assert = require('assert');\nconst { applyDiscount } = require('./discount.js');\n"
                          "assert.strictEqual(applyDiscount(200, 15), 170, 'discount.js must subtract the discount');\n"
                          "console.log('discount ok');\n"),
}
EDITOR_LINE = "// pricing team: rounding rules live in rounding.js\n"
DISCOUNT_FIXED = "exports.applyDiscount = (price, percent) => price - price * percent / 100;\n"


def test_served_stale_concurrent_edit_is_refused_and_the_rebased_repair_keeps_it(served_factory) -> None:
    model = ObservedRepairModel(
        repro="node check_discount.js", owner="discount.js", reads=["discount.js"],
        repairs=[{"pid": "discount", "path": "discount.js", "content": DISCOUNT_FIXED,
                  "rationale": "Owner discount.js: the discount is added instead of subtracted."}],
        focused={"discount": "node check_discount.js", "discount-rebased": "node check_discount.js"},
        full="node check_discount.js",
        recoveries=[{"on": "stale_base", "pid": "discount", "reads": ["discount.js"],
                     "repairs": [{"pid": "discount-rebased", "path": "discount.js", "content": DISCOUNT_FIXED + EDITOR_LINE,
                                  "rationale": "Owner discount.js: subtract the discount, keeping the pricing team's note."}]}],
    )
    rig = _boot(served_factory, STALE_FILES, model)
    target = rig["workspace"] / "discount.js"

    def independent_editor(approvals_so_far: int) -> None:
        # Not the model: someone else edits the file while the operator's first approval is on screen.
        if approvals_so_far == 0:
            target.write_text(STALE_FILES["discount.js"] + EDITOR_LINE, encoding="utf-8")

    outcome = _converse(rig, "served-stale-edit", "Fix the discount bug in this project and explain the change.", model,
                        before_allow=independent_editor)
    journal = _assert_completed(outcome, rig, model)
    assert target.read_text() == DISCOUNT_FIXED + EDITOR_LINE  # the repair landed and the other edit survived
    assert (rig["workspace"] / "check_discount.js").read_text() == STALE_FILES["check_discount.js"]
    assert "refused-stale:discount" in model.log and "rebase:discount" in model.log, model.log
    assert journal["proposals"]["discount"]["invalidated"]["reason"] == "writer_refused_stale_base"
    assert journal["proposals"]["discount"]["consumed_by"] == ""
    assert journal["proposals"]["discount-rebased"]["consumed_by"]
    assert _effects(rig["store_dir"])[:3] == [("run_tests:node check_discount.js", "ok"),
                                              ("write_file:discount.js", "stale_base"),
                                              ("write_file:discount.js", "ok")], model.log
    assert outcome["approvals"] == 2, (outcome["approvals"], model.log)
    assert journal["git_diff_paths"] == ["discount.js"]
