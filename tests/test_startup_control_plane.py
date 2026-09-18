from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.meet_and_greet_node import MeetAndGreetNode, MeetAndGreetNodeConfig
from apps.vool_cli import cmd_adaptation_status, cmd_summary, cmd_wallet_status


def test_cli_summary_bootstraps_storage_before_rendering() -> None:
    order = []
    with mock.patch("apps.vool_cli.bootstrap_storage_environment", side_effect=lambda **kw: order.append("bootstrap")) as bootstrap_storage, mock.patch(
        "core.vool_user_summary.build_user_summary", side_effect=lambda: order.append("summary") or {"recent": []},
    ), mock.patch(
        "core.vool_user_summary.render_user_summary", side_effect=lambda data: order.append("render") or "summary",
    ), mock.patch("builtins.print"):
        assert cmd_summary() == 0
    bootstrap_storage.assert_called_once()
    assert "context" in bootstrap_storage.call_args.kwargs
    assert order == ["bootstrap", "summary", "render"]


def test_cli_wallet_status_bootstraps_storage_before_loading_wallet() -> None:
    status = SimpleNamespace(
        hot_wallet_address="hot",
        cold_wallet_address="cold",
        hot_balance_usdc=1.0,
        cold_balance_usdc=2.0,
        hot_auto_spend_enabled=True,
    )
    manager = mock.Mock()
    manager.get_status.return_value = status
    with mock.patch("apps.vool_cli.bootstrap_storage_environment") as bootstrap_storage, mock.patch(
        "apps.vool_cli.DNAWalletManager",
        return_value=manager,
    ), mock.patch("builtins.print"):
        assert cmd_wallet_status() == 0

    bootstrap_storage.assert_called_once()
    assert "context" in bootstrap_storage.call_args.kwargs


def test_cli_adaptation_status_bootstraps_storage_before_status_lookup() -> None:
    payload = {
        "dependency_status": {"ok": True, "device": "cpu", "modules": []},
        "recent_corpora": [],
        "recent_jobs": [],
        "recent_evals": [],
        "worker_running": False,
    }
    with mock.patch("apps.vool_cli.bootstrap_storage_environment") as bootstrap_storage, mock.patch(
        "apps.vool_cli.get_adaptation_autopilot_status",
        return_value=payload,
    ), mock.patch("builtins.print"):
        assert cmd_adaptation_status() == 0

    bootstrap_storage.assert_called_once()
    assert "context" in bootstrap_storage.call_args.kwargs


def test_agent_main_bootstraps_runtime_before_constructing_agent() -> None:
    args = SimpleNamespace(
        backend="test-local",
        device="cpu-test",
        persona="default",
        input="",
        json=False,
    )
    fake_agent = mock.Mock()

    with mock.patch(
        "core.agent_runtime.agent.argparse.ArgumentParser.parse_args",
        return_value=args,
    ), mock.patch(
        "core.runtime_bootstrap.bootstrap_runtime_mode",
        return_value=SimpleNamespace(backend_selection=None),
    ) as bootstrap_runtime, mock.patch(
        "core.agent_runtime.agent.VoolAgent",
        return_value=fake_agent,
    ), mock.patch.object(
        fake_agent,
        "start",
        return_value=None,
    ), mock.patch("builtins.print"):
        from apps.vool_agent import main

        assert main() == 0

    bootstrap_runtime.assert_called_once_with(mode="agent", force_policy_reload=True, resolve_backend=False)


def test_daemon_main_bootstraps_runtime_before_starting_node() -> None:
    args = SimpleNamespace(
        bind_host="127.0.0.1",
        bind_port=49152,
        advertise_host="127.0.0.1",
        capacity="auto",
        health_host="127.0.0.1",
        health_port=0,
        health_token="",
    )
    fake_stop_event = mock.Mock()
    fake_stop_event.wait.return_value = True
    fake_daemon = mock.Mock()

    with mock.patch(
        "core.agent_runtime.daemon.argparse.ArgumentParser.parse_args",
        return_value=args,
    ), mock.patch(
        "core.runtime_bootstrap.bootstrap_runtime_mode"
    ) as bootstrap_runtime, mock.patch(
        "core.agent_runtime.daemon.resolve_local_worker_capacity",
        return_value=(2, 2),
    ), mock.patch(
        "core.agent_runtime.daemon.VoolDaemon",
        return_value=fake_daemon,
    ), mock.patch(
        "core.agent_runtime.daemon.threading.Event",
        return_value=fake_stop_event,
    ), mock.patch(
        "core.agent_runtime.daemon.signal.signal",
    ):
        from apps.vool_daemon import main

        assert main() == 0

    bootstrap_runtime.assert_called_once_with(mode="daemon", force_policy_reload=True)
    fake_daemon.start.assert_called_once_with()
    fake_daemon.stop.assert_called_once_with()


def test_meet_and_greet_node_start_bootstraps_storage_before_binding_server() -> None:
    fake_server = mock.Mock()
    fake_thread = mock.Mock()
    config = MeetAndGreetNodeConfig(
        node_id="seed-eu-1",
        public_base_url="https://voolbook.com",
        auth_token="token",
    )

    with mock.patch("apps.meet_and_greet_node.setup_logging"), mock.patch(
        "apps.meet_and_greet_node.bootstrap_storage_environment"
    ) as bootstrap_storage, mock.patch(
        "apps.meet_and_greet_node.enforce_meet_public_deployment"
    ), mock.patch(
        "apps.meet_and_greet_node.build_server",
        return_value=fake_server,
    ), mock.patch.object(
        MeetAndGreetNode,
        "_register_self",
    ), mock.patch.object(
        MeetAndGreetNode,
        "_register_seeds",
    ), mock.patch(
        "apps.meet_and_greet_node.threading.Thread",
        return_value=fake_thread,
    ):
        node = MeetAndGreetNode(config)
        node.start()

    bootstrap_storage.assert_called_once_with()
    assert node.server is fake_server
