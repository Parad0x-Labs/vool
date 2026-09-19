from __future__ import annotations

from typing import Any

from core import policy_engine
from core.agent_runtime import build_request_intent
from core.agent_runtime.builder import controller as agent_builder_controller
from core.agent_runtime.builder import scaffolds as agent_builder_scaffolds
from core.agent_runtime.builder import support as agent_builder_support
from core.context_scope import ContextAccessPolicy


class BuilderFacadeMixin:
    def _workspace_build_observations(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
        sources: list[dict[str, str]],
    ) -> dict[str, Any]:
        return agent_builder_controller.workspace_build_observations(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
            sources=sources,
        )

    def _workspace_build_degraded_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
    ) -> str:
        return agent_builder_controller.workspace_build_degraded_response(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
        )

    def _builder_support_gap_report(
        self,
        *,
        source_context: dict[str, object] | None,
        reason: str,
    ) -> dict[str, Any]:
        return agent_builder_support.support_gap_report(
            source_context=source_context,
            reason=reason,
            write_enabled=bool(policy_engine.get("filesystem.allow_write_workspace", False)),
        )

    def _builder_controller_profile(
        self,
        *,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
        access_policy: ContextAccessPolicy | None = None,
    ) -> dict[str, Any]:
        return agent_builder_support.controller_profile(
            self,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            access_policy=access_policy,
            plan_tool_workflow_fn=self._plan_tool_workflow,
            looks_like_workspace_bootstrap_request_fn=self._looks_like_workspace_bootstrap_request,
        )

    def _supports_bounded_builder_workflow_request(
        self,
        *,
        effective_input: str,
        task_class: str,
        source_context: dict[str, object] | None = None,
    ) -> bool:
        if self._looks_like_explicit_workspace_file_request(effective_input):
            return True
        if self._looks_like_workspace_bootstrap_request(effective_input):
            return True
        workflow_probe = self._plan_tool_workflow(
            user_text=effective_input,
            task_class=task_class,
            executed_steps=[],
            source_context=dict(source_context or {}),
        )
        workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
        if workflow_probe.handled and workflow_probe.next_payload and workflow_intent in {
            "workspace.read_file",
            "workspace.write_file",
            "workspace.ensure_directory",
            "orchestration.execute_envelope",
            "sandbox.run_command",
            "hive.create_topic",
        }:
            return True
        if not self._explicit_runtime_workflow_request(
            user_input=effective_input,
            task_class=task_class,
        ):
            return False
        lowered = f" {str(effective_input or '').lower()} "
        operation_markers = (
            " run ",
            " rerun ",
            " retry ",
            " inspect ",
            " search ",
            " find ",
            " read ",
            " open ",
            " replace ",
            " patch ",
            " edit ",
            " fix ",
            " debug ",
            " trace ",
            " diagnose ",
            " test ",
            " tests ",
        )
        target_markers = (
            " workspace ",
            " repo ",
            " repository ",
            " code ",
            " file ",
            " files ",
            ".py",
            ".ts",
            ".js",
            ".tsx",
            ".jsx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".md",
            "`",
        )
        return any(marker in lowered for marker in operation_markers) and any(marker in lowered for marker in target_markers)

    def _builder_controller_step_record(
        self,
        *,
        execution: Any,
        tool_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return agent_builder_support.controller_step_record(
            self,
            execution=execution,
            tool_payload=tool_payload,
        )

    def _workspace_build_verification_payload(self, *, target: dict[str, str]) -> dict[str, Any] | None:
        return agent_builder_support.workspace_build_verification_payload(target=target)

    def _builder_initial_payloads(
        self,
        *,
        mode: str,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
        initial_payloads: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return agent_builder_support.initial_payloads(
            self,
            mode=mode,
            target=target,
            user_request=user_request,
            web_notes=web_notes,
            initial_payloads=initial_payloads,
        )

    def _builder_controller_backing_sources(self, executed_steps: list[dict[str, Any]]) -> list[str]:
        return agent_builder_support.controller_backing_sources(executed_steps)

    def _builder_controller_observations(
        self,
        *,
        mode: str,
        target: dict[str, str],
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        sources: list[dict[str, str]],
        final_status: str,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        return agent_builder_support.controller_observations(
            mode=mode,
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            sources=sources,
            final_status=final_status,
            artifacts=artifacts,
        )

    def _builder_retry_history(self, executed_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return agent_builder_support.retry_history(executed_steps)

    def _builder_controller_artifacts(
        self,
        *,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
    ) -> dict[str, Any]:
        return agent_builder_support.controller_artifacts(
            executed_steps=executed_steps,
            stop_reason=stop_reason,
        )

    def _builder_artifact_citation_block(self, artifacts: dict[str, Any]) -> str:
        return agent_builder_support.artifact_citation_block(self, artifacts)

    def _append_builder_artifact_citations(self, text: str, *, artifacts: dict[str, Any]) -> str:
        return agent_builder_support.append_artifact_citations(
            self,
            text,
            artifacts=artifacts,
        )

    def _builder_controller_degraded_response(
        self,
        *,
        target: dict[str, str],
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        failed_execution: Any | None,
        effective_input: str,
        session_id: str,
        artifacts: dict[str, Any],
    ) -> str:
        return agent_builder_support.controller_degraded_response(
            self,
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            failed_execution=failed_execution,
            effective_input=effective_input,
            session_id=session_id,
            workspace_root=str(artifacts.get("workspace_root") or "") if isinstance(artifacts, dict) else "",
        )

    def _builder_controller_direct_response(
        self,
        *,
        effective_input: str,
        executed_steps: list[dict[str, Any]],
    ) -> str | None:
        return agent_builder_support.controller_direct_response(
            self,
            effective_input=effective_input,
            executed_steps=executed_steps,
        )

    def _builder_controller_workflow_summary(
        self,
        *,
        mode: str,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        artifacts: dict[str, Any],
    ) -> str:
        return agent_builder_support.controller_workflow_summary(
            self,
            mode=mode,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            artifacts=artifacts,
        )

    def _run_bounded_builder_loop(
        self,
        *,
        task: Any,
        session_id: str,
        effective_input: str,
        task_class: str,
        source_context: dict[str, object] | None,
        initial_payloads: list[dict[str, Any]],
        trust_initial_payloads: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], str, Any | None]:
        return agent_builder_controller.run_bounded_builder_loop(
            self,
            task=task,
            session_id=session_id,
            effective_input=effective_input,
            task_class=task_class,
            source_context=source_context,
            initial_payloads=initial_payloads,
            plan_tool_workflow_fn=self._plan_tool_workflow,
            execute_tool_intent_fn=self._execute_tool_intent,
            trust_initial_payloads=trust_initial_payloads,
        )

    def _maybe_run_builder_controller(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        web_notes: list[dict[str, Any]],
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_builder_controller.maybe_run_builder_controller(
            self,
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            web_notes=web_notes,
            session_id=session_id,
            source_context=source_context,
            render_capability_truth_response_fn=self._render_capability_truth_response,
            load_active_persona_fn=self._load_active_persona,
        )

    def _should_run_builder_controller(
        self,
        *,
        effective_input: str,
        classification: dict[str, Any],
        source_context: dict[str, object],
    ) -> bool:
        if not policy_engine.get("filesystem.allow_write_workspace", False):
            return False
        if not str(source_context.get("workspace") or source_context.get("workspace_root") or "").strip():
            return False
        # A pinned model must not be silently swapped for a different one — and the way that was
        # enforced here was to refuse the turn outright:
        #
        #     if explicit_model_owns_semantic_turn(source_context):
        #         return False
        #
        # The premise was right and the outcome was not. The refusal did not produce a refusal: the
        # turn fell through to the selected model's tool loop, that model did not call
        # `workspace.write_file`, and "i need <ws>/v5/median.py with a median function" came back
        # "I couldn't map that cleanly to a real action" after 75.6s. Measured on the installed
        # build, pinning a model meant you could not build at all.
        #
        # The substitution is now prevented where it actually happens instead. `_run_model_build`
        # asks `core/agent_runtime/builder/pinned_generation.py` for a generate_fn backed by the
        # SELECTED provider, so a pinned build's files are generated by the operator's own model and
        # the report says so; a pin that cannot be honoured is refused by name, with the reason, and
        # with the local lane offered. Nothing here needs to stand in the way of that.
        task_class = str(classification.get("task_class") or "unknown")
        lowered = str(effective_input or "").lower()
        operating_mode = str(source_context.get("operating_mode") or "").strip().lower()
        # Mode gates the file-writing builder. Plan is strictly read-only. Manual/Review enter the
        # same builder only far enough for the central controller to produce an exact approval/diff;
        # Auto can apply ordinary writes. Legacy Ask remains read-only for installed clients. A
        # "build/create a <bot/agent/service/app/script/...>" request is itself the intent to write
        # files, so the explicit "write the files / workspace" language is not required and the
        # task-class gate is bypassed. An explicit "just plan / no files" still opts out. Requests with
        # no mode set (channel/api) keep the existing explicit-request behavior below.
        _opt_out = build_request_intent.is_opted_out(lowered)
        if operating_mode in {"ask", "plan"}:
            return False
        # The verb/noun pair used to be matched by bare substring, so `app` fired inside "happens",
        # `site` inside "opposite", `api` inside "rapid" and `cli` inside "client" — a build noun the
        # operator never wrote. Worse, it drew no line between an instruction and a question, so
        # "lets discuss whether we should build an api" wrote files. Both are now decided once, in
        # core/agent_runtime/build_request_intent.py, shared by all four detectors.
        if (
            operating_mode in {"manual", "review_edits", "build", "auto"}
            and not _opt_out
            and build_request_intent.is_build_instruction(lowered, scope="project")
        ):
            return True
        # A request that enumerates a small project's whole file set is a build whatever verb it
        # used -- "small python app folder, readme + app only" names its container and its two
        # files and carries no imperative at all, so every gate below misses it and the turn ends
        # up in the model lane with nothing written.
        #
        # No mode or opt-out check is repeated here. Ask/Plan already returned above, and the plan
        # resolver refuses an opted-out or deliberative request itself -- a second copy of either
        # would read as a layered defence while being unreachable, which is exactly the shape this
        # repository keeps removing. See builder/small_project_plan.py.
        from core.agent_runtime.builder import small_project_plan

        if (
            small_project_plan.resolve_small_project_plan(
                effective_input,
                workspace_root=str(
                    source_context.get("workspace") or source_context.get("workspace_root") or ""
                ),
            )
            is not None
        ):
            return True
        explicit_file_request = self._looks_like_explicit_workspace_file_request(effective_input)
        generic_bootstrap_request = self._looks_like_generic_workspace_bootstrap_request(lowered)
        workflow_probe = self._plan_tool_workflow(
            user_text=effective_input,
            task_class=task_class,
            executed_steps=[],
            source_context=dict(source_context or {}),
        )
        workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
        pure_inspection_workflow = workflow_intent in {
            "workspace.search_text",
            "workspace.symbol_search",
            "workspace.git_summary",
            "workspace.git_status",
            "workspace.list_files",
        }
        workflow_supported_request = bool(
            workflow_probe.handled
            and workflow_probe.next_payload
            and workflow_intent
            in {
                "workspace.read_file",
                "workspace.write_file",
                "workspace.ensure_directory",
                "orchestration.execute_envelope",
                "sandbox.run_command",
                "hive.create_topic",
            }
        )
        if pure_inspection_workflow and not explicit_file_request and not generic_bootstrap_request and not self._looks_like_builder_request(lowered):
            return False
        if task_class not in {
            "system_design",
            "integration_orchestration",
            "debugging",
            "dependency_resolution",
            "config",
            "file_inspection",
            "shell_guidance",
            "unknown",
        } and not generic_bootstrap_request and not explicit_file_request and not workflow_supported_request:
            # The class gate may not swallow what the build-intent authority already recognized:
            # "build a web scraper service ... and write the files" classifies outside this
            # allowlist, yet it is an explicit build instruction (verb + thing-to-build +
            # write evidence) — the same authority the mode-gated branch above trusts.
            if not build_request_intent.is_build_instruction(lowered, scope="project"):
                return False
        if not self._looks_like_builder_request(lowered) and not explicit_file_request and not workflow_supported_request:
            return False
        if _opt_out:
            return False
        scaffold_request = (
            any(marker in lowered for marker in ("build", "create", "scaffold", "implement", "generate", "start working"))
            and any(marker in lowered for marker in ("telegram", "discord", "bot", "agent", "service"))
            and any(marker in lowered for marker in ("workspace", "repo", "repository", "write the files", "create the files", "generate the code"))
        )
        return (
            "write the files" in lowered
            or "create the files" in lowered
            or "generate the code" in lowered
            or "build the code" in lowered
            or "building the code" in lowered
            or "start working" in lowered
            or "start building" in lowered
            or "start creating" in lowered
            or "implement it" in lowered
            or "edit the files" in lowered
            or "patch the files" in lowered
            or "launch local" in lowered
            or scaffold_request
            or generic_bootstrap_request
            or explicit_file_request
            or workflow_supported_request
            or self._explicit_runtime_workflow_request(user_input=effective_input, task_class=task_class)
        )

    def _looks_like_write_intent_request(self, effective_input: str) -> bool:
        """True when a request would CREATE or edit workspace files (build an app, write/patch files).

        Used to give an honest read-only refusal in Ask/Plan mode instead of letting a build request
        fall through to a tool loop that reads a not-yet-created file ("File `tasks.json` does not
        exist"). Deliberately conservative: a read-only question that merely names a file, or an
        explicit "read the file" request, is NOT a write intent (reads are allowed in read-only mode).
        """
        lowered = f" {' '.join(str(effective_input or '').split()).lower()} "
        if (
            build_request_intent.is_opted_out(lowered)
            # A question about writing is not a write intent, so it needs no read-only refusal —
            # "should we create the files first or plan?" is answered, not refused. The `write_files`
            # list below still matches by substring, which is exactly how that sentence claimed one.
            or build_request_intent.is_deliberation(lowered)
            or any(
                marker in lowered
                for marker in (
                    "read the file", "read back", "read it back",
                    "list the folder", "list the directory",
                )
            )
        ):
            return False
        # Same shared decision as the other three: a question about building is not a write intent,
        # and a build noun must be a word rather than letters inside one.
        build_app = build_request_intent.is_build_instruction(lowered)
        write_files = any(
            marker in lowered
            for marker in (
                "write the file", "create the file", "create a file", "create a new file", "generate the code",
                "build the code", "edit the file", "patch the file", "overwrite ", "save it to a file", "save to a file",
            )
        )
        return build_app or write_files

    def _workspace_build_target(
        self,
        *,
        query_text: str,
        interpretation: Any,
        access_policy: ContextAccessPolicy | None = None,
        workspace_root: str = "",
    ) -> dict[str, str]:
        return agent_builder_scaffolds.workspace_build_target(
            query_text=query_text,
            interpretation=interpretation,
            access_policy=access_policy,
            extract_requested_builder_root_fn=self._extract_requested_builder_root,
            search_user_heuristics_fn=self._search_user_heuristics,
            workspace_root=workspace_root,
        )

    def _workspace_build_file_map(
        self,
        *,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
    ) -> dict[str, str]:
        return agent_builder_scaffolds.workspace_build_file_map(
            target=target,
            user_request=user_request,
            web_notes=web_notes,
        )

    def _workspace_build_sources(self, web_notes: list[dict[str, Any]]) -> list[dict[str, str]]:
        return agent_builder_scaffolds.workspace_build_sources(web_notes)

    def _workspace_build_verification(
        self,
        *,
        target: dict[str, str],
        source_context: dict[str, object],
    ) -> dict[str, Any] | None:
        return agent_builder_controller.workspace_build_verification(
            target=target,
            source_context=source_context,
            execute_runtime_tool_fn=self._execute_runtime_tool,
        )

    def _workspace_build_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
        sources: list[dict[str, str]],
    ) -> str:
        return agent_builder_controller.workspace_build_response(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
            sources=sources,
        )

    def _sources_section(self, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.sources_section(sources)

    def _generic_workspace_readme(
        self,
        *,
        user_request: str,
        root_dir: str,
        sources: list[dict[str, str]],
        language: str,
    ) -> str:
        return agent_builder_scaffolds.generic_workspace_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
            language=language,
        )

    def _generic_python_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_python_source(
            user_request=user_request,
            sources=sources,
        )

    def _generic_typescript_package_json(self, *, root_dir: str) -> str:
        return agent_builder_scaffolds.generic_typescript_package_json(root_dir=root_dir)

    def _generic_typescript_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_typescript_source(
            user_request=user_request,
            sources=sources,
        )

    def _telegram_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_python_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _telegram_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_python_bot_source(sources=sources)

    def _telegram_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_typescript_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _telegram_typescript_package_json(self) -> str:
        return agent_builder_scaffolds.telegram_typescript_package_json()

    def _telegram_typescript_tsconfig(self) -> str:
        return agent_builder_scaffolds.telegram_typescript_tsconfig()

    def _telegram_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_typescript_bot_source(sources=sources)

    def _discord_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_python_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _discord_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_python_bot_source(sources=sources)

    def _discord_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_typescript_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _discord_typescript_package_json(self) -> str:
        return agent_builder_scaffolds.discord_typescript_package_json()

    def _discord_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_typescript_bot_source(sources=sources)

    def _generic_build_brief(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_build_brief(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )
