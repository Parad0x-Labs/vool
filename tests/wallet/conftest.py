import pytest

from tests.wallet._rig import rpc, wallet_env
from tests.wallet._rig_evm import evm_rig


@pytest.fixture(autouse=True)
def _cold_chain_identity_cache():
    """Chain identity is proven per endpoint and cached in process memory: every wallet test starts cold,
    so no test inherits another test's proof of an endpoint it never probed."""
    from core.wallet import chains

    chains.invalidate_chain_identity()
    yield
    chains.invalidate_chain_identity()
