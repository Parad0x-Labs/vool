"""The served coding boundary: a guided model completes the repair the native runs could not.

The native acceptance failures (c354e500/01ce8109 evidence) all share one shape: the task opened,
the model narrated or asked for the catalog, tried a door that either executed silently beside the
task journal (top-level ``workspace.run_tests``) or was refused with guidance that led nowhere
("propose it" for a command), and the bounded correction budget ran out with the journal still at
``reproduce``.

This pack drives the SAME model shape through the real served door -- a real daemon, a real
socket, a scripted provider whose policy narrates first and then tries the top-level door -- and
proves the repaired boundary converts that model into a completed repair:

    narration -> correction -> top-level run_tests -> refused WITH the step path ->
    step run_tests (red) -> identify -> read -> propose -> approve -> mutate -> narrow ->
    cumulative -> git_diff -> report (published as the turn's grounded answer)

On the pinned base the identical policy dead-ends exactly like the native runs: the top-level
call executes with no refusal in sight, the model records an identify the stage machine refuses,
and the turn ends honestly incomplete with the fixture untouched.

The policy knows only what the boundary shows it (markers in the observations and refusals) plus
the fixture facts a model would derive from reading the files; every product seam between the
request and the bytes on disk is production code from this checkout.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"
MISSION_FIXTURES = REPO_ROOT.parent / "fixtures"


def _git(root: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "fx",
        "GIT_AUTHOR_EMAIL": "fx@local",
        "GIT_COMMITTER_NAME": "fx",
        "GIT_COMMITTER_EMAIL": "fx@local",
    }
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"call-{name}", "type": "function", "function": {"name": name, "arguments": arguments}}


class GuidedRepair:
    """A scripted model with the native failure shape that follows boundary guidance.

    Decisions read markers from what the runtime sent back (refusals, stage observations,
    completion feedback) and never inspect the fixtures beyond the injected facts a model would
    learn by reading the files it is shown.
    """

    def __init__(
        self,
        *,
        command: str,
        defects: list[dict[str, str]],
        narration: str,
        attempt_command_between_mutations: bool = False,
        try_top_level_door: bool = True,
        narrate: bool = True,
    ) -> None:
        self.command = command
        self.defects = defects
        self.narration = narration
        self.narrate = narrate
        self.attempt_command_between_mutations = attempt_command_between_mutations
        self.try_top_level_door = try_top_level_door
        self.reached: dict[str, bool] = {}
        self.call_log: list[str] = []
        self.task_id_cache = ""
        #: Repair CYCLES: one proposal + approval + mutation each, in order. Defaults to one
        #: cycle per defect (the atomic multi-defect shape proposes them in one batch). A
        #: recovery journey overrides `cycles` with per-cycle content so the first proposal
        #: can be a competent-but-wrong implementation, and `recovery_diagnoses` with the
        #: re-diagnosis to issue when the boundary reports a failed verification.
        self.cycles: list[dict[str, str]] = []
        self.recovery_diagnoses: list[dict[str, str]] = []

    def _once(self, key: str) -> bool:
        if self.reached.get(key):
            return False
        self.reached[key] = True
        return True

    @staticmethod
    def _at_stage(prompt: str, stage: str) -> bool:
        """The stage is visible three ways: the controller feedback's own sentence
        ("... at stage propose: preview the repair ..."), the observation JSON's stage
        field (which reports the stage AT CALL TIME, so it lags an advance), and the
        plan array every code.task result carries ({"name": "propose", "state":
        "current"}), which is the authoritative current stage."""
        return (
            f"at stage {stage}:" in prompt
            or f'"stage": "{stage}"' in prompt
            or f'"name": "{stage}", "state": "current"' in prompt
        )

    def _cycle(self, index: int) -> dict[str, str]:
        """The proposal/mutation target for repair cycle `index`: the override list first,
        then the per-defect fixed facts (identical behavior when no override is set)."""
        if self.cycles:
            return self.cycles[index]
        defect = self.defects[index]
        return {**defect, "content": defect["fixed"], "hash": defect["expected_hash"],
                "proposal_id": f"p{index + 1}"}

    def _total_cycles(self) -> int:
        return len(self.cycles) or len(self.defects)

    def _task_id(self, prompt: str) -> str:
        if self.task_id_cache:
            return self.task_id_cache
        # The journal-open line is authoritative; a skill body or example may also carry a
        # ct-shaped id, so the loose "last match anywhere" heuristic is not safe.
        opened = re.findall(r"Opened code task (ct-[0-9a-f]{12})", prompt)
        if not opened:
            opened = re.findall(r'"task_id": "(ct-[0-9a-f]{12})"', prompt)
        assert opened, "the model never saw its task id"
        self.task_id_cache = opened[-1]
        return self.task_id_cache

    def reply(self, prompt: str) -> Any:
        if self.task_id_cache:
            task_id = self.task_id_cache
        elif re.search(r"ct-[0-9a-f]{12}", prompt):
            task_id = self._task_id(prompt)
        else:
            task_id = "ct-unknown"
        if self.narrate and not self.reached.get("narrated") and "Opened code task" in prompt:
            self.reached["narrated"] = True
            self.call_log.append("narrate")
            return _call("respond__direct", {"message": self.narration})
        if self.try_top_level_door and self._once("tried_top_level") and "Opened code task" in prompt:
            # The door the base used to advertise beside the task plane.
            self.call_log.append("top-level-run_tests")
            return _call("workspace__run_tests", {"command": self.command})
        if self._once("guided_step") and (
            "re-issue this as `code.task.step`" in prompt
            or "run the failing suite through `code.task.step`" in prompt
            or (not self.narrate and "Opened code task" in prompt)
        ):
            # The repaired boundary names the lawful action -- in the refusal text when the
            # model tried the wrong door, or in the controller feedback after a corrected
            # narration -- and a model that reads either does the same thing: re-issue the
            # command as the task's evidence step.
            self.call_log.append("step-run_tests")
            return _call(
                "code__task__step",
                {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests",
                 "arguments": {"command": self.command}},
            )
        if (
            self._once("misled_identify")
            and not self.reached.get("guided_step")
            and ("Validation test run" in prompt or "Exit code: 1" in prompt)
        ):
            # BASE branch: the top-level door executed silently, so the model believes the
            # failure is reproduced and records its diagnosis -- which the stage machine refuses.
            # (On the repaired boundary the guided step ALSO leaves a failing output behind, so
            # the marker alone is not enough: this branch is only for a model that was never
            # told the step path.)
            self.call_log.append("identify-after-silent-run")
            first = self.defects[0]
            return _call(
                "code__task__identify",
                {"task_id": task_id, "path": first["path"], "line": 1, "reason": first["reason"]},
            )
        if self._once("gave_up") and "stage_violation" in prompt:
            self.call_log.append("honest-give-up")
            return _call(
                "respond__direct",
                {"message": "I reproduced the failure, but the task will not accept my next step, "
                            "so I cannot complete the repair."},
            )
        if "re-diagnose with `code.task.identify`" in prompt and self._once("recovery_cycle"):
            # The boundary's failed-verification feedback names the recovery; a model that
            # reads it re-diagnoses in the SAME durable task and reads the owner again.
            index = min(self.reached.get("recoveries_done", 0), len(self.recovery_diagnoses) - 1)
            diagnosis = self.recovery_diagnoses[index]
            self.reached["recoveries_done"] = self.reached.get("recoveries_done", 0) + 1
            self.reached["cycle_override_active"] = True
            self.call_log.append(f"re-identify+read:{diagnosis['path']}")

            def _recovery_batch(name: str, arguments: dict[str, Any], key: str = "x") -> dict[str, Any]:
                return {"id": f"call-{name}-{key}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}

            return [
                _recovery_batch("code__task__identify",
                                {"task_id": task_id, "path": diagnosis["path"], "line": 1,
                                 "reason": diagnosis["reason"]}, key="redefect"),
                _recovery_batch("code__task__step",
                                {"task_id": task_id,
                                 "step_id": f"reread-{diagnosis['path'].replace('/', '-')}",
                                 "intent": "workspace.read_file",
                                 "arguments": {"path": diagnosis["path"]}}, key="reread"),
            ]
        if self._once("identified") and self._at_stage(prompt, "identify"):
            first = self.defects[0]
            self.reached["reads_done"] = len(self.defects)
            self.call_log.append("identify+read:" + ",".join(d["path"] for d in self.defects))

            def _batch(name: str, arguments: dict[str, Any], key: str = "x") -> dict[str, Any]:
                return {"id": f"call-{name}-{key}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}

            # One native batch: record the defect and read EVERY owning file in the same
            # reply. All envelopes are seated at this stage and read-only class, so they
            # run inline in provider order.
            members = [
                _batch("code__task__identify",
                       {"task_id": task_id, "path": first["path"], "line": 1, "reason": first["reason"]},
                       key="defect")
            ]
            for index, defect in enumerate(self.defects):
                members.append(
                    _batch("code__task__step",
                           {"task_id": task_id, "step_id": f"read-{index + 1}", "intent": "workspace.read_file",
                            "arguments": {"path": defect["path"]}},
                           key=f"r{index + 1}")
                )
            return members
        one_per_cycle = bool(self.recovery_diagnoses) or bool(self.cycles)
        start = self.reached.get("proposals_done", 0)
        prior_verified = start == 0 or f"narrow-{start}" in self.reached
        if self.reached.get("proposals_done", 0) < self._total_cycles() and (
            self._at_stage(prompt, "propose") or "previewed" in prompt
        ) and (not one_per_cycle or prior_verified):
            end = start + 1 if one_per_cycle else self._total_cycles()
            self.reached["proposals_done"] = end
            self.call_log.append("propose:" + ",".join(f"p{i + 1}" for i in range(start, end))
                                 + "".join(f"[{self._cycle(i).get('kind', 'fixed')}]" for i in range(start, end)))

            def _propose_call(index: int) -> dict[str, Any]:
                cycle = self._cycle(index)
                return {"id": f"call-propose-{index + 1}", "type": "function",
                        "function": {"name": "code__task__propose", "arguments": json.dumps(
                            {"task_id": task_id, "proposal_id": f"p{index + 1}", "intent": "workspace.write_file",
                             "arguments": {"path": cycle["path"], "content": cycle["content"],
                                           "expected_hash": cycle["hash"]},
                             "rationale": cycle["rationale"]})}}

            # A second proposal is lawful at the approve stage the first one moved the task
            # to, so an atomic multi-file repair proposes everything in one native batch;
            # a recovery journey proposes one revised repair per cycle instead.
            return [_propose_call(i) for i in range(start, end)]
        if self.reached.get("approvals_done", 0) < self.reached.get("proposals_done", 0) and (
            self._at_stage(prompt, "approve") or "may now execute" in prompt or self._at_stage(prompt, "mutate")
        ):
            start = self.reached.get("approvals_done", 0)
            self.reached["approvals_done"] = (
                start + 1 if one_per_cycle else self.reached["proposals_done"]
            )
            self.call_log.append("approve:" + ",".join(
                f"p{i + 1}" for i in range(start, self.reached["proposals_done"])))
            # A second approval is lawful at the mutate stage the first one moved the task
            # to, so every pending approval rides one native batch.
            return [
                {"id": f"call-approve-{i + 1}", "type": "function",
                 "function": {"name": "code__task__approve",
                              "arguments": json.dumps({"task_id": task_id, "proposal_id": f"p{i + 1}"})}}
                for i in range(start, self.reached["proposals_done"])
            ]
        if (
            self.attempt_command_between_mutations
            and self.reached.get("mutations_done", 0) == 1
            and self._once("mid_mutate_command")
        ):
            # The user asked for tests after each repair; at mutate stage the runtime must
            # refuse the command (commands are evidence acts, never interstitial).
            self.call_log.append("mid-mutate-command-refused")
            return _call(
                "code__task__step",
                {"task_id": task_id, "step_id": "midcheck", "intent": "workspace.run_tests",
                 "arguments": {"command": self.command}},
            )
        if self.reached.get("mutations_done", 0) < self.reached.get("approvals_done", 0) and (
            self._at_stage(prompt, "mutate") or self._at_stage(prompt, "narrow_test")
        ):
            index = self.reached.get("mutations_done", 0)
            cycle = self._cycle(index)
            self.reached["mutations_done"] = index + 1
            self.call_log.append(f"mutate:p{index + 1}[{cycle.get('kind', 'fixed')}]")
            return _call(
                "code__task__step",
                {"task_id": task_id, "step_id": f"mut-{index + 1}", "intent": "workspace.write_file",
                 "arguments": {"path": cycle["path"], "content": cycle["content"],
                               "expected_hash": cycle["hash"]}},
            )
        if (
            self._at_stage(prompt, "narrow_test")
            and self.reached.get("mutations_done", 0) >= self.reached.get("approvals_done", 0)
            and self.reached.get("mutations_done", 0) > 0
            and self._once(f"narrow-{self.reached.get('mutations_done', 0)}")
        ):
            self.call_log.append(f"verify-{self.reached.get('mutations_done', 0)}")
            return _call(
                "code__task__step",
                {"task_id": task_id, "step_id": f"verify-{self.reached.get('mutations_done', 0)}",
                 "intent": "workspace.run_tests",
                 "arguments": {"command": self.command}},
            )
        if self._once("cumulative") and self._at_stage(prompt, "cumulative"):
            self.call_log.append("cumulative")
            return _call(
                "code__task__step",
                {"task_id": task_id, "step_id": "cumulative", "intent": "workspace.run_tests",
                 "arguments": {"command": self.command}},
            )
        if self._once("diff") and self._at_stage(prompt, "inspect_diff"):
            self.call_log.append("git_diff")
            return _call(
                "code__task__step",
                {"task_id": task_id, "step_id": "diff", "intent": "workspace.git_diff", "arguments": {}},
            )
        if self._once("report") and self._at_stage(prompt, "report"):
            self.call_log.append("report")
            return _call("code__task__report", {"task_id": task_id})
        self.call_log.append("final-summary")
        if '"code_task_verdict": "completed"' in prompt:
            # A grounded model reads the report's own verdict and repeats exactly that.
            return _call("respond__direct", {
                "message": "The coding task completed. The published report above is the "
                           "verification: steps executed, tests green, diff inspected."})
        return _call("respond__direct", {"message": "The repair is not complete; the journal above says what remains."})


class GuidedProvider(ScriptedProvider):
    """The rig's provider, answering from the guided policy instead of a table."""

    def __init__(self, policy: GuidedRepair) -> None:
        super().__init__({MODEL: "ok"})
        self.policy = policy

    def reply_for(
        self, model: str, *, has_tool_result: bool, body: dict[str, Any] | None = None
    ) -> Any:
        with self._lock:
            last = self.calls[-1] if self.calls else None
        if last is None or not last.get("tools"):
            # The classifier call: plain text, as the rig's own fixtures answer it.
            return "shell_guidance"
        prompt = (
            f"{last.get('system') or ''}\n{last.get('full_prompt') or ''}"
            f"\n{last.get('prompt_tail') or ''}"
        )
        return self.policy.reply(prompt)


def _stub_models_are_resident(provider: ScriptedProvider) -> None:
    handler = provider._server.RequestHandlerClass
    original = handler.do_GET

    def do_GET(self):
        if self.path.startswith("/api/ps"):
            return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in provider.table]})
        return original(self)

    handler.do_GET = do_GET


@pytest.fixture
def served_factory(tmp_path: Path):
    made: list[tuple[ServedDaemon, ScriptedProvider]] = []

    def make(workspace_files: dict[str, str]) -> dict[str, Any]:
        home = tmp_path / f"home{len(made)}"
        workspace = tmp_path / f"repo{len(made)}"
        store_dir = tmp_path / f"store{len(made)}"
        workspace.mkdir(parents=True)
        for rel, content in workspace_files.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        _git(workspace, "init", "-q")
        _git(workspace, "add", ".")
        _git(workspace, "commit", "-q", "-m", "seed")
        return {"home": home, "workspace": workspace, "store_dir": store_dir, "made": made}

    yield make
    for daemon, provider in made:
        daemon.stop()
        provider.__exit__(None, None, None)


def _boot(served_factory, workspace_files: dict[str, str], policy: GuidedRepair) -> dict[str, Any]:
    rig = served_factory(workspace_files)
    provider = GuidedProvider(policy)
    daemon = ServedDaemon(
        rig["home"],
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(rig["workspace"]),
            "VOOL_BLACKBOX_DIR": str(rig["store_dir"]),
            "VOOL_CODE_TASK_DIR": str(rig["store_dir"] / "code_tasks"),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            # The session fence dead-ends every endpoint var a launcher leaves unset
            # (setdefault), and RAW outranks OLLAMA_HOST -- without these three the
            # daemon's inventory, pull and residency probes all hit a dead port, the
            # stub reads as not-resident, and turns come back tool-less.
            "VOOL_RAW_OLLAMA_API_URL": provider.base_url,
            "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
            "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps",
            "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
        },
    )
    _stub_models_are_resident(provider)
    provider.__enter__()
    try:
        daemon.start(timeout=240)
    except Exception as exc:  # pragma: no cover - environment, not the runtime
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    run_in_home(rig["home"], SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
    # Certify the scripted stub for final-answer authorship the way an operator's probe run
    # would (same authority as the wallet/blackbox rigs): the precall fence refuses an
    # uncertified local author, and this rig's drives must be callable authors.
    run_in_home(
        rig["home"],
        textwrap.dedent(
            f'''
            import sys
            sys.path.insert(0, "{REPO_ROOT}")
            from storage.model_provider_manifest import list_provider_manifests
            from tests._authorship_certification import certify_for_authorship
            for m in list_provider_manifests():
                if m.model_name == "{MODEL}":
                    print("certified", m.model_name, certify_for_authorship(m))
            '''
        ),
    )
    provider.reset()
    rig.update(daemon=daemon, provider=provider)
    rig["made"].append((daemon, provider))
    return rig


def _journal(store_dir: Path) -> dict[str, Any]:
    files = sorted((store_dir / "code_tasks").glob("ct-*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


def _reply_text(reply: dict[str, Any]) -> str:
    message = reply.get("message") if isinstance(reply.get("message"), dict) else {}
    return str(message.get("content") or reply.get("response") or reply.get("content") or "")


ORIGINAL_FILES = {
    "package.json": '{\n  "name": "backend-coding",\n  "private": true,\n  "scripts": {"test": "node test.js"}\n}\n',
    "math.js": "exports.add = (left, right) => left - right;\n",
    "test.js": (
        "const assert = require('assert');\n"
        "const { add } = require('./math.js');\n"
        "assert.strictEqual(add(11, 4), 15, 'add(11, 4) must be 15');\n"
        "assert.strictEqual(add(-8, 3), -5, 'add(-8, 3) must be -5');\n"
        "assert.strictEqual(add(0, 6), 6, 'add(0, 6) must be 6');\n"
        "console.log('all arithmetic checks passed');\n"
    ),
}
ORIGINAL_DEMAND = "Fix the bug in this project, run its tests, and explain what you changed."
ORIGINAL_DEFECT = {
    "path": "math.js",
    "fixed": "exports.add = (left, right) => left + right;\n",
    "reason": "math.js subtracts where the tests require addition",
    "rationale": "Owner math.js: add subtracts its operands while test.js requires the sum for 11+4, -8+3 and 0+6; restore the addition.",
}

NOVEL_FILES = {
    "package.json": '{\n  "name": "native-slug",\n  "private": true,\n  "scripts": {"test": "node checks.js"}\n}\n',
    "lib/normalize.js": "exports.normalize = (text) => text.replace('  ', ' ');\n",
    "checks.js": (
        "const assert = require('assert');\n"
        "const { normalize } = require('./lib/normalize.js');\n"
        "assert.strictEqual(normalize('  galactic   command  '), 'galactic command');\n"
        "assert.strictEqual(normalize('deck\\t\\tseven'), 'deck seven');\n"
        "assert.strictEqual(normalize('\\tcorridor \\t log\\t'), 'corridor log');\n"
        "console.log('all normalization checks passed');\n"
    ),
}
NOVEL_DEMAND = "Please repair normalize.js to satisfy the tests in checks.js. Execute the existing test command and summarize the change."
NOVEL_DEFECT = {
    "path": "lib/normalize.js",
    "fixed": "exports.normalize = (text) => text.trim().replace(/[ \\t]+/g, ' ');\n",
    "reason": "normalize.js replaces only one double space and neither trims nor collapses tabs",
    "rationale": "Owner lib/normalize.js: a single replace('  ', ' ') neither trims nor collapses repeated spaces or tabs, which checks.js requires; normalize with trim plus a whitespace collapse.",
}


def _task_verdict(store_dir: Path) -> str:
    files = sorted((store_dir / "code_tasks").glob("ct-*.json"))
    if not files:
        return "none"
    task = json.loads(files[-1].read_text(encoding="utf-8"))
    if task.get("stage") == "cancelled":
        return "cancelled"
    if (task.get("rollback") or {}).get("restored"):
        return "rolled_back"
    failed = any(s.get("executed") and not s.get("ok") for s in (task.get("steps") or {}).values())
    if (
        task.get("stage") == "report"
        and (task.get("narrow") or {}).get("success")
        and (task.get("cumulative") or {}).get("success")
        and not failed
    ):
        return "completed"
    return "unresolved"


def _resolve_approvals(daemon: ServedDaemon, home: Path, session: str) -> str:
    """The operator's Allow, through the same /api/mode door the native app uses."""
    from urllib.request import Request as _Request
    from urllib.request import urlopen as _urlopen

    from tests._blackbox_served_rig import pending_session_approvals

    token = ""
    for entry in pending_session_approvals(home, session):
        token = str(entry["approval_id"])
        request = _Request(
            f"{daemon.base_url}/api/mode",
            data=json.dumps({
                "op": "resolve_approval", "session_id": session,
                "approval_id": str(entry["approval_id"]), "decision": "allow",
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urlopen(request, timeout=30) as response:
            resolved = json.loads(response.read().decode())
        assert resolved.get("ok") is True, resolved
    return token


def _run_guided_repair(served_factory, files: dict[str, str], demand: str, defects: list[dict[str, str]],
                       *, command: str = "node test.js",
                       attempt_command_between_mutations: bool = False,
                       try_top_level_door: bool = True,
                       narrate: bool = True) -> dict[str, Any]:
    # npm itself is unreadable to the sandbox on this host (EPERM on its lib tree under
    # the operator home), so the assistant runs the package's test entry directly -- the
    # same tests, through the same real command door.
    hashed = [
        {**d, "expected_hash": hashlib.sha256(files[d["path"]].encode()).hexdigest()} for d in defects
    ]
    policy = GuidedRepair(
        command=command,
        defects=hashed,
        narration="I'll inspect the project and repair the failing behaviour. Let me work through it.",
        attempt_command_between_mutations=attempt_command_between_mutations,
        try_top_level_door=try_top_level_door,
        narrate=narrate,
    )
    rig = _boot(served_factory, files, policy)
    session = "served-boundary"
    reply = rig["daemon"].chat(demand, session_id=session, mode="auto", timeout=900.0)
    # The task plane's mutation is an overwrite-class act: the runtime raises the operator's
    # approval and pauses. The operator resolves each approval through the real /api/mode
    # door and resumes with its token -- the same authority the native e496 run exercised
    # when the operator allowed the write. Bounded: a task that cannot finish still ends.
    for _attempt in range(6):
        if _task_verdict(rig["store_dir"]) in {"completed", "cancelled", "rolled_back"}:
            break
        token = _resolve_approvals(rig["daemon"], rig["home"], session)
        if not token:
            break
        reply = rig["daemon"].chat(
            "continue the approved repair", session_id=session, mode="auto",
            approval_token=token, timeout=900.0,
        )
    return {**rig, "reply": reply, "policy": policy, "demand": demand}


def test_served_original_fixture_completes_through_the_guided_boundary(served_factory) -> None:
    outcome = _run_guided_repair(served_factory, ORIGINAL_FILES, ORIGINAL_DEMAND, [ORIGINAL_DEFECT])
    workspace: Path = outcome["workspace"]
    journal = _journal(outcome["store_dir"])
    text = _reply_text(outcome["reply"])

    assert "completed." in text, (text[:600], outcome["policy"].call_log)  # the report verdict, not prose
    assert (workspace / "math.js").read_text(encoding="utf-8") == ORIGINAL_DEFECT["fixed"]
    # The tests are preserved byte-for-byte: the repair never touched them.
    assert (workspace / "test.js").read_text(encoding="utf-8") == ORIGINAL_FILES["test.js"]
    assert journal["stage"] == "report"
    assert journal["reproduced_failure"] and journal["reproduced_failure"]["success"] is False
    assert journal["narrow"]["success"] is True and journal["cumulative"]["success"] is True
    assert journal["git_diff_paths"] == ["math.js"]
    mutation = next(s for s in journal["steps"].values() if s["intent"] == "workspace.write_file")
    assert mutation["bytes_match_approved_patch"] is True
    # The boundary converted the native failure shape: narration corrected, silent top-level
    # door refused WITH the lawful path, and the model followed it.
    log = outcome["policy"].call_log
    assert log[:3] == ["narrate", "top-level-run_tests", "step-run_tests"], log
    assert "identify-after-silent-run" not in log and "honest-give-up" not in log
    assert log[-1] == "report", log


def test_served_novel_fixture_completes_with_a_different_layout(served_factory) -> None:
    outcome = _run_guided_repair(served_factory, NOVEL_FILES, NOVEL_DEMAND, [NOVEL_DEFECT], command="node checks.js")
    workspace: Path = outcome["workspace"]
    journal = _journal(outcome["store_dir"])
    text = _reply_text(outcome["reply"])

    assert "completed." in text, (text[:600], outcome["policy"].call_log)  # the report verdict, not prose
    assert (workspace / "lib" / "normalize.js").read_text(encoding="utf-8") == NOVEL_DEFECT["fixed"]
    assert (workspace / "checks.js").read_text(encoding="utf-8") == NOVEL_FILES["checks.js"]
    assert journal["stage"] == "report"
    assert journal["narrow"]["success"] is True and journal["cumulative"]["success"] is True
    assert journal["git_diff_paths"] == ["lib/normalize.js"]
    assert outcome["policy"].call_log[:3] == ["narrate", "top-level-run_tests", "step-run_tests"]


def test_served_uncooperative_model_fails_honestly_and_changes_no_bytes(served_factory) -> None:
    class Narrator(GuidedRepair):
        def reply(self, prompt: str) -> Any:
            self.call_log.append("narrate")
            return _call("respond__direct", {"message": "I will fix this project soon."})

    policy = Narrator(command="node test.js", defects=[ORIGINAL_DEFECT], narration="x")
    rig = _boot(served_factory, ORIGINAL_FILES, policy)
    before = (rig["workspace"] / "math.js").read_bytes()
    reply = rig["daemon"].chat(ORIGINAL_DEMAND, session_id="served-negative", mode="auto", timeout=900.0)
    text = _reply_text(reply)
    assert "completed." not in text
    assert "incomplete" in text or "unfinished" in text.lower(), text[:400]
    assert (rig["workspace"] / "math.js").read_bytes() == before
    journal = _journal(rig["store_dir"])
    assert journal["stage"] == "reproduce" and not journal["steps"]


WRONG_FIX_FILES = {
    "package.json": '{\n  "name": "wrong-first",\n  "private": true,\n  "scripts": {"test": "node check.js"}\n}\n',
    "math.js": "exports.add = (a, b) => a - b;\n",
    "check.js": (
        "const assert = require('assert');\n"
        "const { add } = require('./math.js');\n"
        "assert.strictEqual(add(9, 5), 14);\n"
        "assert.strictEqual(add(-3, 2), -1);\n"
        "console.log('wrong-first checks passed');\n"
    ),
}
WRONG_FIRST = "exports.add = (a, b) => a * b;\n"  # a plausible, wrong first implementation
WRONG_FIXED = "exports.add = (a, b) => a + b;\n"
WRONG_DEMAND = "Fix the bug in this project and explain the change."


def test_served_wrong_first_fix_recovers_in_the_same_task(served_factory) -> None:
    """A competent-but-wrong first repair, then the boundary-guided recovery: the failed
    verification is journaled, the correction names re-diagnosis, a REVISED proposal gets a
    FRESH approval, the corrected bytes land, and the focused suite goes green -- all in the
    same durable task. The turn then ends inside the designed 12-round bound with the full
    cumulative still pending: an honest bounded incomplete, no limit widened."""
    import hashlib as _hashlib

    wrong_hash = _hashlib.sha256(WRONG_FIX_FILES["math.js"].encode()).hexdigest()
    fixed_hash = _hashlib.sha256(WRONG_FIRST.encode()).hexdigest()
    policy = GuidedRepair(
        command="node check.js",
        defects=[{"path": "math.js", "fixed": WRONG_FIXED, "reason": "math.js subtracts",
                  "rationale": "Owner math.js: restore the sum the checks require.",
                  "expected_hash": wrong_hash}],
        narration="I'll repair the arithmetic and verify it.",
        try_top_level_door=False,
        narrate=False,
    )
    policy.cycles = [
        {"path": "math.js", "content": WRONG_FIRST, "hash": wrong_hash, "kind": "wrong",
         "rationale": "Owner math.js: the checks fail because the operands are combined with the wrong operator."},
        {"path": "math.js", "content": WRONG_FIXED, "hash": fixed_hash, "kind": "fixed",
         "rationale": "Owner math.js: the first fix still failed the checks; the assertions require addition."},
    ]
    policy.recovery_diagnoses = [
        {"path": "math.js", "reason": "The first fix still fails the checks; the assertions require addition"},
    ]
    rig = _boot(served_factory, WRONG_FIX_FILES, policy)
    session = "served-wrong-first"
    reply = rig["daemon"].chat(WRONG_DEMAND, session_id=session, mode="auto", timeout=900.0)
    for _attempt in range(6):
        if _task_verdict(rig["store_dir"]) in {"completed", "cancelled", "rolled_back"}:
            break
        token = _resolve_approvals(rig["daemon"], rig["home"], session)
        if not token:
            break
        reply = rig["daemon"].chat("continue the approved repair", session_id=session, mode="auto",
                                  approval_token=token, timeout=900.0)
    workspace: Path = rig["workspace"]
    journal = _journal(rig["store_dir"])
    text = _reply_text(reply)
    log = policy.call_log

    # The wrong fix landed first, was verified, FAILED, and only then was revised.
    assert "propose:p1[wrong]" in log and "mutate:p1[wrong]" in log, log
    assert "verify-1" in log and "re-identify+read:math.js" in log, log
    assert "propose:p2[fixed]" in log and "mutate:p2[fixed]" in log, log
    # Both failures and the recovery's green verification stay journaled, in order.
    assert [(v["stage"], v["success"]) for v in journal["verifications"]] == [("narrow_test", False), ("narrow_test", True)]
    # The corrected bytes are on disk through the second approval; the wrong fix never returned.
    assert (workspace / "math.js").read_text(encoding="utf-8") == WRONG_FIXED
    assert len(journal["diagnoses"]) == 1 and journal["defect"]["path"] == "math.js"
    # Fresh approval for changed bytes: two distinct approved proposals, the first consumed.
    approved = [p for p in journal["proposals"].values() if p["approved"]]
    assert len(approved) == 2 and all(p["consumed_by"] for p in approved), journal["proposals"]
    # Bounded honesty: no fabricated completion; the task honestly remains unfinished
    # (cumulative pending) inside the designed per-task round bound.
    assert "completed." not in text and journal["stage"] in {"narrow_test", "cumulative", "identify"}, (
        text[:400], journal["stage"], log)


SEQ_FILES = {
    "package.json": '{\n  "name": "sequential",\n  "private": true,\n  "scripts": {"test": "node suite.js"}\n}\n',
    "arithmetic.js": "exports.total = (a, b) => a - b;\n",
    "labels.js": "exports.label = (parts) => parts.join(';');\n",
    "suite.js": (
        "const assert = require('assert');\n"
        "const { total } = require('./arithmetic.js');\n"
        "const { label } = require('./labels.js');\n"
        "assert.strictEqual(total(20, 4), 24);\n"
        "assert.strictEqual(label(['core', 'deck']), 'core,deck');\n"
        "console.log('sequential suite passed');\n"
    ),
    "NOTES.md": "operator notes: do not touch\n",
}


def test_served_second_defect_discovered_by_between_repairs_validation(served_factory) -> None:
    """Sequential discovery: the model knows ONE defect; the between-repairs suite check
    honestly exposes the second; the boundary's failed-verification recovery reopens review;
    the second repair lands through its own approval; the suite then goes green. The full
    cumulative gate remains pending at the designed round bound -- recorded, never faked."""
    import hashlib as _hashlib

    a = "exports.total = (a, b) => a + b;\n"
    b = "exports.label = (parts) => parts.join(',');\n"
    policy = GuidedRepair(
        command="node suite.js",
        defects=[{"path": "arithmetic.js", "fixed": a, "reason": "arithmetic.js subtracts",
                  "rationale": "Owner arithmetic.js: total subtracts; the suite requires the sum.",
                  "expected_hash": _hashlib.sha256(SEQ_FILES["arithmetic.js"].encode()).hexdigest()}],
        narration="I'll repair the failing behavior and validate.",
        try_top_level_door=False,
        narrate=False,
    )
    policy.cycles = [
        {"path": "arithmetic.js", "content": a, "kind": "fixed",
         "hash": _hashlib.sha256(SEQ_FILES["arithmetic.js"].encode()).hexdigest(),
         "rationale": "Owner arithmetic.js: total subtracts; the suite requires the sum."},
        {"path": "labels.js", "content": b, "kind": "fixed",
         "hash": _hashlib.sha256(SEQ_FILES["labels.js"].encode()).hexdigest(),
         "rationale": "Owner labels.js: the suite exposes a second failing check; join with a comma."},
    ]
    policy.recovery_diagnoses = [
        {"path": "labels.js", "reason": "The between-repairs suite run exposes a second failing check in labels.js"},
    ]
    rig = _boot(served_factory, SEQ_FILES, policy)
    session = "served-sequential"
    reply = rig["daemon"].chat(
        "Repair the failing behavior in this project and describe the changes.",
        session_id=session, mode="auto", timeout=900.0)
    for _attempt in range(6):
        if _task_verdict(rig["store_dir"]) in {"completed", "cancelled", "rolled_back"}:
            break
        token = _resolve_approvals(rig["daemon"], rig["home"], session)
        if not token:
            break
        reply = rig["daemon"].chat("continue the approved repair", session_id=session, mode="auto",
                                  approval_token=token, timeout=900.0)
    workspace: Path = rig["workspace"]
    journal = _journal(rig["store_dir"])
    log = policy.call_log
    assert "completed." not in _reply_text(reply), "the cumulative gate was not run; completion must not be claimed"

    # The between-repairs validation ran after the FIRST repair, failed honestly on the
    # second defect, and the recovery diagnosed THE NEWLY EXPOSED owner.
    assert "verify-1" in log and "re-identify+read:labels.js" in log, log
    assert "mutate:p2[fixed]" in log and "verify-2" in log, log
    assert [(v["stage"], v["success"]) for v in journal["verifications"]] == [("narrow_test", False), ("narrow_test", True)]
    assert journal["verifications"][0]["command"] == "node suite.js"
    # Both defects repaired through their own approvals; the sibling untouched.
    assert (workspace / "arithmetic.js").read_text(encoding="utf-8") == a
    assert (workspace / "labels.js").read_text(encoding="utf-8") == b
    assert (workspace / "NOTES.md").read_text(encoding="utf-8") == SEQ_FILES["NOTES.md"]
    # Two diagnoses journaled: the initial one and the recovery's.
    assert len(journal["diagnoses"]) == 1 and journal["defect"]["path"] == "labels.js"
    assert journal["stage"] in {"narrow_test", "cumulative", "identify"}, (journal["stage"], log)
