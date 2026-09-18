from __future__ import annotations

import subprocess

from core.public_hive import bridge_support as _public_hive_bridge_support
from core.public_hive.bridge import PublicHiveBridge
from core.public_hive.bridge_facade_auth import public_hive_has_auth as _public_hive_has_auth
from core.public_hive.bridge_facade_auth import public_hive_write_requires_auth as _public_hive_write_requires_auth
from core.public_hive.bridge_facade_auth import url_requires_auth as _url_requires_auth_impl
from core.public_hive.bridge_facade_compat import (
    discover_local_cluster_bootstrap_impl as _discover_local_cluster_bootstrap_impl,
)
from core.public_hive.bridge_facade_compat import (
    ensure_public_hive_agent_bootstrap_impl as _ensure_public_hive_agent_bootstrap_impl,
)
from core.public_hive.bridge_facade_compat import ensure_public_hive_auth_impl as _ensure_public_hive_auth_impl
from core.public_hive.bridge_facade_compat import (
    load_public_hive_bridge_config_impl as _load_public_hive_bridge_config_impl,
)
from core.public_hive.bridge_facade_compat import public_hive_write_enabled_impl as _public_hive_write_enabled_impl
from core.public_hive.bridge_facade_compat import (
    sync_public_hive_auth_from_ssh_impl as _sync_public_hive_auth_from_ssh_impl,
)
from core.public_hive.bridge_facade_compat import (
    write_public_hive_agent_bootstrap_impl as _write_public_hive_agent_bootstrap_impl,
)
from core.public_hive.config import PublicHiveBridgeConfig

_subprocess = subprocess

__all__ = [
    "PublicHiveBridge",
    "PublicHiveBridgeConfig",
    "ensure_public_hive_auth",
    "load_public_hive_bridge_config",
    "public_hive_write_enabled",
    "sync_public_hive_auth_from_ssh",
    "write_public_hive_agent_bootstrap",
]


load_public_hive_bridge_config = _load_public_hive_bridge_config_impl
ensure_public_hive_agent_bootstrap = _ensure_public_hive_agent_bootstrap_impl


_load_agent_bootstrap = _public_hive_bridge_support.load_agent_bootstrap
_load_json_file = _public_hive_bridge_support.load_json_file
_split_csv = _public_hive_bridge_support.split_csv
_json_env_object = _public_hive_bridge_support.json_env_object
_json_env_write_grants = _public_hive_bridge_support.json_env_write_grants
_merge_auth_tokens_by_base_url = _public_hive_bridge_support.merge_auth_tokens_by_base_url
_merge_write_grants_by_base_url = _public_hive_bridge_support.merge_write_grants_by_base_url
_clean_token = _public_hive_bridge_support.clean_token
_normalize_base_url = _public_hive_bridge_support.normalize_base_url
_resolve_local_tls_ca_file = _public_hive_bridge_support.resolve_local_tls_ca_file
_discover_local_cluster_bootstrap = _public_hive_bridge_support.discover_local_cluster_bootstrap
find_public_hive_ssh_key = _public_hive_bridge_support.find_public_hive_ssh_key


write_public_hive_agent_bootstrap = _write_public_hive_agent_bootstrap_impl
sync_public_hive_auth_from_ssh = _sync_public_hive_auth_from_ssh_impl


public_hive_has_auth = _public_hive_has_auth
public_hive_write_requires_auth = _public_hive_write_requires_auth
public_hive_write_enabled = _public_hive_write_enabled_impl
_discover_local_cluster_bootstrap = _discover_local_cluster_bootstrap_impl
ensure_public_hive_auth = _ensure_public_hive_auth_impl
_url_requires_auth = _url_requires_auth_impl
