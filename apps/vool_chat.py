from __future__ import annotations

import argparse

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import sys as _bootstrap_sys

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)


from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)
from apps.vool_agent import VoolAgent
from core.compute_mode import ComputeModeDaemon
from core.onboarding import get_agent_display_name, is_first_boot, run_onboarding_interactive
from core.runtime_backbone import build_runtime_backbone


def _print_prewarm_results(results: tuple[dict[str, object], ...]) -> None:
    if not results:
        return
    print("Provider prewarm:")
    for result in results:
        provider_id = str(result.get("provider_id") or "unknown-provider")
        status = str(result.get("status") or "unknown").strip() or "unknown"
        if result.get("ok") and status == "prewarmed":
            keep_alive = str(result.get("keep_alive") or "").strip() or "unspecified"
            print(f" - {provider_id}: prewarmed (keep_alive={keep_alive})")
            continue
        if result.get("ok") and status == "timed_out":
            keep_alive = str(result.get("keep_alive") or "").strip() or "unspecified"
            reason = str(result.get("reason") or "unspecified").strip() or "unspecified"
            print(
                f" - {provider_id}: prewarm timed out; continuing without background warming "
                f"(keep_alive={keep_alive}, reason={reason})"
            )
            continue
        if result.get("ok"):
            reason = str(result.get("reason") or "unspecified").strip() or "unspecified"
            print(f" - {provider_id}: skipped ({reason})")
            continue
        error = str(result.get("error") or "unknown_error").strip() or "unknown_error"
        print(f" - {provider_id}: failed ({error})")


def _bootstrap_agent(*, persona_id: str, device: str) -> VoolAgent:
    # Honour a persisted GPU-fallback verdict before the backbone builds the provider registry, so a
    # host whose GPU cannot generate runs the Ollama lane on CPU (VOOL_OLLAMA_NUM_GPU=0) instead of
    # shipping brain-dead. Fail-safe: a missing/ok verdict is a no-op.
    try:
        from core.gpu_capability_state import apply_persisted_cpu_fallback
        from core.runtime_paths import active_vool_home

        apply_persisted_cpu_fallback(str(active_vool_home()))
    except Exception:
        pass
    backbone = build_runtime_backbone(
        mode="chat",
        force_policy_reload=True,
        resolve_backend=True,
    )

    if is_first_boot():
        run_onboarding_interactive()

    probe = backbone.local_model_profile.probe
    tier = backbone.local_model_profile.tier
    hw_info = backbone.local_model_profile.summary
    vram_part = f" ({hw_info['vram_gb']}GB VRAM)" if hw_info.get("vram_gb") else ""
    print(
        f"Hardware: {hw_info['accelerator']} | RAM {hw_info['ram_gb']}GB | "
        f"GPU {hw_info['gpu'] or 'none'}{vram_part}"
    )
    print(f"Selected model tier: {tier.tier_name} -> {tier.ollama_tag}")

    compute_daemon = ComputeModeDaemon(has_gpu=probe.accelerator != "cpu")
    compute_daemon.start()
    budget = compute_daemon.budget
    print(
        f"Compute mode: {budget.mode} | CPU threads: {budget.cpu_threads} | "
        f"GPU mem fraction: {budget.gpu_memory_fraction:.0%}"
    )

    provider_warnings = list(backbone.provider_snapshot.warnings)
    if provider_warnings:
        print("Model provider warnings:")
        for warning in provider_warnings:
            print(f" - {warning}")
    _print_prewarm_results(backbone.provider_snapshot.prewarm_results)

    selection = backbone.boot.backend_selection
    if selection is None:
        raise RuntimeError("Chat bootstrap did not resolve a backend selection.")
    if selection.backend_name == "remote_only":
        print("No local model backend found. Starting in remote-first mode.")

    agent = VoolAgent(
        backend_name=selection.backend_name,
        device=device,
        persona_id=persona_id,
    )
    runtime = agent.start()
    display_name = get_agent_display_name()
    print(f"{display_name} is ready.")
    print(f"Backend: {runtime.backend_name} | Device: {runtime.device} | Persona: {runtime.persona_id}")
    print("Type /exit to quit.")
    return agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vool-chat", description="Interactive local chat with VOOL.")
    parser.add_argument("--persona", default="default", help="Persona id")
    parser.add_argument("--device", default="openclaw", help="Session device hint")
    parser.add_argument("--platform", default="openclaw", help="Source platform label")
    return parser


def main() -> int:
    # C15: the ONE bounded unattended preflight before bootstrap reaches a credential API.
    from core.unattended_preflight import preflight

    preflight('apps.vool_chat')
    args = build_parser().parse_args()
    try:
        agent = _bootstrap_agent(persona_id=str(args.persona), device=str(args.device))
    except Exception as exc:
        print(f"VOOL chat bootstrap failed: {exc}")
        return 1

    prompt_tag = get_agent_display_name().lower()
    source_context = {"surface": "channel", "platform": str(args.platform)}
    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return 0
        if not user_text:
            continue
        if user_text.lower() in {"/exit", "/quit", "exit", "quit"}:
            print("bye.")
            return 0
        if user_text.lower().startswith("/rename "):
            new_name = user_text[8:].strip()
            if not new_name:
                print("usage: /rename <new-name>")
                continue
            from core.onboarding import force_rename

            force_rename(new_name)
            prompt_tag = new_name.lower()
            print(f"name updated: {new_name}")
            continue
        if user_text.lower() in {"/credits", "/balance"}:
            from core.credit_ledger import reconcile_ledger
            from network.signer import get_local_peer_id

            recon = reconcile_ledger(get_local_peer_id())
            print(f"credits={recon.balance:.2f} entries={recon.entries} mode={recon.mode}")
            continue
        if user_text.lower() in {"/summary", "/status"}:
            # GENERATED_ADAPTER: the command registry owns this action (status.show);
            # the REPL renders from the typed envelope, never a second declaration.
            from core.command_registry.execute import ExecutionContext, execute_command
            from core.vool_user_summary import render_user_summary

            envelope = execute_command("status.show", {}, context=ExecutionContext(projection="chat"))
            if envelope.ok:
                print(render_user_summary(envelope.data))
            else:
                print(envelope.summary)
            continue
        if user_text.lower().startswith("/resolve"):
            from apps.vool_cli import cmd_resolve

            name = user_text[len("/resolve"):].strip()
            if not name:
                print("usage: /resolve <name>.null")
                continue
            cmd_resolve(name)
            continue
        if user_text.lower().startswith("/dial"):
            from apps.vool_cli import cmd_dial

            rest = user_text[len("/dial"):].strip()
            parts = rest.split(None, 1)
            if len(parts) < 2:
                print('usage: /dial <name>.null "<task>"')
                continue
            cmd_dial(parts[0], parts[1])
            continue
        if user_text.lower() in {"/manifest", "/capabilities"}:
            from apps.vool_cli import cmd_manifest

            cmd_manifest()
            continue
        if user_text.lower().startswith("/web"):
            from apps.vool_cli import cmd_web

            query = user_text[len("/web"):].strip()
            if not query:
                print("usage: /web <query>")
                continue
            cmd_web(query=query)
            continue
        if user_text.lower().startswith("/quote"):
            from apps.vool_cli import cmd_sell_quote

            target = user_text[len("/quote"):].strip()
            if not target:
                print("usage: /quote [null://service/path | <name>.null]")
                continue
            cmd_sell_quote(target)
            continue
        try:
            result = agent.run_once(user_text, source_context=source_context)
        except Exception as exc:
            print(f"{prompt_tag}> [error] {exc}")
            continue
        # A7 W5: the CLI prints committed canonical bytes only — never raw
        # result text mutated after finalization (M09). The confidence line is
        # separate terminal framing, not part of the answer body.
        _commit = result.get("vool_response_commit")
        if isinstance(_commit, dict) and _commit.get("type") == "response.commit":
            response = str(_commit.get("canonical_content") or "")
        else:
            response = str(result.get("response") or "")
        confidence = float(result.get("confidence") or 0.0)
        print(f"{prompt_tag}> {response}")
        print(f"[confidence={confidence:.2f}]")


if __name__ == "__main__":
    raise SystemExit(main())
