from __future__ import annotations

from core.public_hive import (
    bridge_facade_bootstrap,
    bridge_facade_bootstrap_auth,
    bridge_facade_bootstrap_sync,
    bridge_facade_bootstrap_write,
    bridge_facade_compat,
    bridge_facade_compat_auth,
    bridge_facade_compat_bootstrap,
    bridge_facade_compat_config,
    bridge_support,
    bridge_support_env,
    bridge_support_paths,
    bridge_support_runtime,
)


def test_public_hive_bridge_support_facade_reexports_paths_and_runtime_helpers() -> None:
    assert bridge_support.load_agent_bootstrap is bridge_support_paths.load_agent_bootstrap
    assert bridge_support.agent_bootstrap_paths is bridge_support_paths.agent_bootstrap_paths
    assert bridge_support.discover_local_cluster_bootstrap is bridge_support_runtime.discover_local_cluster_bootstrap
    assert bridge_support.find_public_hive_ssh_key is bridge_support_runtime.find_public_hive_ssh_key
    assert bridge_support.sync_public_hive_auth_from_ssh is bridge_support_runtime.sync_public_hive_auth_from_ssh


def test_public_hive_bridge_support_facade_reexports_env_helpers() -> None:
    assert bridge_support.load_json_file is bridge_support_env.load_json_file
    assert bridge_support.split_csv is bridge_support_env.split_csv
    assert bridge_support.json_env_object is bridge_support_env.json_env_object
    assert bridge_support.json_env_write_grants is bridge_support_env.json_env_write_grants
    assert bridge_support.merge_auth_tokens_by_base_url is bridge_support_env.merge_auth_tokens_by_base_url
    assert bridge_support.merge_write_grants_by_base_url is bridge_support_env.merge_write_grants_by_base_url


def test_public_hive_bridge_bootstrap_facade_reexports_split_helpers() -> None:
    assert bridge_facade_bootstrap.write_public_hive_agent_bootstrap is bridge_facade_bootstrap_write.write_public_hive_agent_bootstrap
    assert bridge_facade_bootstrap.sync_public_hive_auth_from_ssh is bridge_facade_bootstrap_sync.sync_public_hive_auth_from_ssh
    assert bridge_facade_bootstrap.ensure_public_hive_auth is bridge_facade_bootstrap_auth.ensure_public_hive_auth


def test_public_hive_bridge_compat_facade_exposes_impl_lanes() -> None:
    assert bridge_facade_compat.load_public_hive_bridge_config_impl is bridge_facade_compat_config.load_public_hive_bridge_config_impl
    assert bridge_facade_compat.ensure_public_hive_agent_bootstrap_impl is bridge_facade_compat_config.ensure_public_hive_agent_bootstrap_impl
    assert bridge_facade_compat.write_public_hive_agent_bootstrap_impl is bridge_facade_compat_bootstrap.write_public_hive_agent_bootstrap_impl
    assert bridge_facade_compat.sync_public_hive_auth_from_ssh_impl is bridge_facade_compat_bootstrap.sync_public_hive_auth_from_ssh_impl
    assert bridge_facade_compat.discover_local_cluster_bootstrap_impl is bridge_facade_compat_bootstrap.discover_local_cluster_bootstrap_impl
    assert bridge_facade_compat.public_hive_write_enabled_impl is bridge_facade_compat_auth.public_hive_write_enabled_impl
    assert bridge_facade_compat.ensure_public_hive_auth_impl is bridge_facade_compat_auth.ensure_public_hive_auth_impl
